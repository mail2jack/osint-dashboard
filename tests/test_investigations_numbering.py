"""ADR-0002 PR2: atomic, tenant-scoped number issuance + investigations.

Covers the SQLite-backed behaviour (PostgreSQL additions live in
``test_postgres_integration.py`` / ``test_postgres_migration_rls.py``):

- case numbers are allocated atomically and sequentially per (tenant, year);
- investigation sequence numbers are allocated atomically per (tenant, case);
- counters are isolated per tenant and per case;
- the unique per-case sequence constraint rejects duplicates;
- gaps are allowed and issued numbers are never reused;
- case numbers are immutable, also via the workflow edit route;
- the create-case route ignores any submitted case_number;
- the hard tenant invariant (investigation.tenant_id == case.tenant_id) is
  enforced by the service layer before any row is written.
"""

import threading
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from cms.models import (
    Case,
    CaseNumberCounter,
    Client,
    Investigation,
    InvestigationStatus,
    Tenant,
    db,
)
from cms.services import sequence_service
from cms.services.sequence_service import (
    allocate_case_number,
    allocate_investigation_sequence_no,
    create_investigation,
    preview_case_number,
)

_YEAR = datetime.now(timezone.utc).year


def _raw_session_factory():
    """Session factory over the app engine for real multi-thread concurrency.

    Flask-SQLAlchemy's ``db.session`` is scoped to the request/app context and
    is not safe to reuse across threads; a dedicated ``sessionmaker`` gives
    each worker its own connection while exercising the real allocator code.
    """
    return sessionmaker(bind=db.engine)


def _fk_enforced_engine():
    """A SQLite engine with ``PRAGMA foreign_keys=ON`` per connection.

    The app deliberately keeps FK enforcement off on SQLite (service-layer
    checks are authoritative), so this engine exists to prove the schema-level
    foreign key is present and really refuses a row for an unknown parent.
    """
    from sqlalchemy import create_engine, event

    engine = create_engine(db.engine.url)

    @event.listens_for(engine, "connect")
    def _enable_fk(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    return engine


def _make_tenant() -> Tenant:
    tenant = Tenant(
        name=f"Inv Tenant {uuid.uuid4().hex[:8]}",
        slug=f"inv-{uuid.uuid4().hex[:10]}",
        is_active=True,
        tier="enterprise",
        subscription_status="active",
        join_code=uuid.uuid4().hex[:12],
    )
    db.session.add(tenant)
    db.session.flush()
    return tenant


def _make_case(tenant_id=None) -> Case:
    client = Client(name="Inv Test Client", is_active=True)
    db.session.add(client)
    db.session.flush()
    case = Case(
        case_number=f"TEST-{uuid.uuid4().hex[:10]}",
        client_id=client.id,
        title=f"Why test {uuid.uuid4().hex[:6]}",
        status="open",
        priority="medium",
        start_date=datetime.now(timezone.utc).date(),
    )
    if tenant_id:
        case.tenant_id = tenant_id
    db.session.add(case)
    db.session.flush()
    return case


class TestCaseNumberAllocation:
    def test_sequential_with_preview(self, db_session):
        for _ in range(3):
            db_session.commit()
        assert preview_case_number("t-a") == f"{_YEAR}-00001"
        nums = [allocate_case_number("t-a") for _ in range(3)]
        db_session.commit()
        assert nums == [f"{_YEAR}-00001", f"{_YEAR}-00002", f"{_YEAR}-00003"]
        assert preview_case_number("t-a") == f"{_YEAR}-00004"

    def test_preview_never_allocates(self, db_session):
        db_session.commit()
        preview_case_number("t-p")
        preview_case_number("t-p")
        assert CaseNumberCounter.query.filter_by(tenant_id="t-p").count() == 0

    def test_is_per_tenant(self, db_session):
        other = _make_tenant()
        db_session.commit()
        assert allocate_case_number("t-1") == f"{_YEAR}-00001"
        assert allocate_case_number(other.id) == f"{_YEAR}-00001"
        assert allocate_case_number("t-1") == f"{_YEAR}-00002"

    def test_is_per_year(self, db_session, monkeypatch):
        monkeypatch.setattr(
            sequence_service,
            "_utc_now",
            lambda: datetime(2023, 6, 1, tzinfo=timezone.utc),
        )
        db_session.commit()
        assert allocate_case_number("t-y") == "2023-00001"
        monkeypatch.setattr(sequence_service, "_utc_now", lambda: datetime(2024, 6, 1))
        assert allocate_case_number("t-y") == "2024-00001"
        assert allocate_case_number("t-y") == "2024-00002"

    def test_empty_tenant_rejected(self):
        with pytest.raises(ValueError):
            allocate_case_number("")

    def test_parallel_allocation_unique_and_sequential(self, app):
        worker_count = 6
        barrier = threading.Barrier(worker_count)
        results: list[list[str]] = [[] for _ in range(worker_count)]
        errors: list[BaseException] = []
        session_factory = _raw_session_factory()

        def worker(index: int):
            session = session_factory()
            try:
                session.connection().exec_driver_sql("PRAGMA busy_timeout = 15000")
                barrier.wait(timeout=30)
                results[index].append(
                    allocate_case_number("t-par", session=session)
                )
                session.commit()
            except BaseException as exc:  # pragma: no cover
                errors.append(exc)
            finally:
                session.close()

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(worker_count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)

        assert not errors, errors
        numbers = [int(r[0].split("-")[1]) for r in results if r]
        assert sorted(numbers) == list(range(1, worker_count + 1))


class TestInvestigationSequenceAllocation:
    def test_sequential_within_case(self, db_session):
        case = _make_case()
        db_session.commit()
        create_investigation(tenant_id=case.tenant_id, case_id=case.id, title="Een")
        create_investigation(tenant_id=case.tenant_id, case_id=case.id, title="Twee")
        db_session.commit()
        seqs = sorted(i.sequence_no for i in Investigation.query.filter_by(case_id=case.id).all())
        assert seqs == [1, 2]

    def test_isolated_between_cases_and_tenants(self, db_session):
        case_a = _make_case()
        other_tenant = _make_tenant()
        case_b = _make_case(tenant_id=other_tenant.id)
        db_session.commit()
        assert allocate_investigation_sequence_no(case_a.tenant_id, case_a.id) == 1
        assert allocate_investigation_sequence_no(case_a.tenant_id, case_a.id) == 2
        assert allocate_investigation_sequence_no(other_tenant.id, case_b.id) == 1
        assert allocate_investigation_sequence_no(case_a.tenant_id, case_a.id) == 3
        db_session.commit()

    def test_unique_sequence_per_case_constraint(self, db_session):
        case = _make_case()
        db_session.commit()
        created = create_investigation(
            tenant_id=case.tenant_id, case_id=case.id, title="Eerste"
        )
        db_session.commit()
        assert created.sequence_no == 1
        duplicate = Investigation(
            tenant_id=case.tenant_id,
            case_id=case.id,
            sequence_no=1,
            title="Tweede",
        )
        db_session.add(duplicate)
        with pytest.raises(IntegrityError):
            db_session.flush()
        db_session.rollback()

    def test_counter_gap_never_reused(self, db_session):
        db_session.commit()
        first_three = [allocate_case_number("t-gap") for _ in range(3)]
        fourth = allocate_case_number("t-gap")
        db_session.commit()
        assert first_three == [
            f"{_YEAR}-00001", f"{_YEAR}-00002", f"{_YEAR}-00003"
        ]
        assert fourth == f"{_YEAR}-00004"
        counter = CaseNumberCounter.query.filter_by(tenant_id="t-gap", year=_YEAR).one()
        assert counter.next_seq == 4

    def test_parallel_allocation_within_one_case(self, app, db_session):
        case = _make_case()
        db_session.commit()
        case_id = case.id
        tenant_id = case.tenant_id
        worker_count = 6
        barrier = threading.Barrier(worker_count)
        errors: list[BaseException] = []
        session_factory = _raw_session_factory()

        def worker(index: int):
            session = session_factory()
            try:
                session.connection().exec_driver_sql("PRAGMA busy_timeout = 15000")
                barrier.wait(timeout=30)
                create_investigation(
                    tenant_id=tenant_id,
                    case_id=case_id,
                    title=f"Parallel {index}",
                    session=session,
                )
                session.commit()
            except BaseException as exc:  # pragma: no cover
                errors.append(exc)
            finally:
                session.close()

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(worker_count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)

        assert not errors, errors
        seqs = sorted(
            i.sequence_no for i in Investigation.query.filter_by(case_id=case.id).all()
        )
        assert seqs == list(range(1, worker_count + 1))


class TestInvestigationModel:
    def test_human_number_format_and_defaults(self, db_session):
        case = _make_case()
        db_session.commit()
        created = create_investigation(
            tenant_id=case.tenant_id,
            case_id=case.id,
            title="Formaat controle",
            notes="opmerking",
        )
        db_session.commit()
        assert created.status == InvestigationStatus.OPEN.value
        assert created.human_number == f"{case.case_number}-01"
        tenth = None
        for _ in range(9):
            tenth = create_investigation(
                tenant_id=case.tenant_id, case_id=case.id, title="Meer"
            )
            db_session.commit()
        assert tenth is not None
        assert tenth.sequence_no == 10
        assert tenth.human_number == f"{case.case_number}-10"
        assert Investigation.query.filter_by(case_id=case.id).count() == 10

    def test_to_dict(self, db_session):
        case = _make_case()
        db_session.commit()
        inv = create_investigation(
            tenant_id=case.tenant_id, case_id=case.id, title="Dict"
        )
        db_session.commit()
        data = inv.to_dict()
        assert data["human_number"] == f"{case.case_number}-01"
        assert data["title"] == "Dict"
        assert data["case_id"] == case.id

    def test_requires_title_and_existing_case(self, db_session):
        db_session.commit()
        with pytest.raises(ValueError, match="title"):
            create_investigation(tenant_id="t", case_id="c", title="  ")
        with pytest.raises(ValueError, match="tenant_id and case_id"):
            create_investigation(tenant_id="", case_id="c", title="x")
        with pytest.raises(ValueError, match="does not exist"):
            create_investigation(tenant_id="t", case_id="missing", title="x")


class TestTenantInvariant:
    def test_sequence_allocation_rejects_tenant_mismatch(self, db_session):
        case_a = _make_case()
        other_tenant = _make_tenant()
        db_session.commit()
        with pytest.raises(ValueError, match="case.tenant_id"):
            allocate_investigation_sequence_no(other_tenant.id, case_a.id)
        with pytest.raises(ValueError, match="case.tenant_id"):
            create_investigation(
                tenant_id=other_tenant.id, case_id=case_a.id, title="X"
            )
        assert Investigation.query.count() == 0


class TestDatabaseLevelImmutability:
    """ADR-0002 D2/D4 enforced in the database, not only in the routes:

    ``cases.case_number`` and ``investigations.sequence_no`` cannot be changed
    after INSERT — direct ORM writes fail with an IntegrityError from the
    trigger even when the app layer would never touch them.
    """

    def test_orm_update_of_case_number_fails(self, db_session):
        case = _make_case()
        db_session.commit()
        original = case.case_number
        case.case_number = f"{_YEAR}-00099"
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()
        assert db_session.get(Case, case.id).case_number == original

    def test_orm_update_of_sequence_no_fails(self, db_session):
        case = _make_case()
        db_session.commit()
        created = create_investigation(
            tenant_id=case.tenant_id, case_id=case.id, title="Onwijzigbaar"
        )
        db_session.commit()
        inv_id = created.id
        created.sequence_no = 99
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()
        assert db_session.get(Investigation, inv_id).sequence_no == 1

    def test_normal_create_and_unrelated_updates_still_work(self, db_session):
        case = _make_case()
        db_session.commit()
        number = allocate_case_number(case.tenant_id)
        created = create_investigation(
            tenant_id=case.tenant_id, case_id=case.id, title="Normaal"
        )
        db_session.commit()
        assert number == f"{_YEAR}-00001"
        assert created.sequence_no == 1
        # Non-protected columns stay editable under the triggers.
        case.title = "Gewijzigde titel"
        created.notes = "aantekening"
        db_session.commit()
        db_session.refresh(case)
        db_session.refresh(created)
        assert case.title == "Gewijzigde titel"
        assert created.notes == "aantekening"


class TestCounterTenantReference:
    """The case-number counter may only exist for a real tenant (FK), and the
    allocator keeps working for existing tenants."""

    def test_counter_for_unknown_tenant_refused_by_db(self, db_session):
        engine = _fk_enforced_engine()
        session = sessionmaker(bind=engine)()
        try:
            bad = CaseNumberCounter(
                tenant_id=str(uuid.uuid4()), year=_YEAR, next_seq=1
            )
            session.add(bad)
            with pytest.raises(IntegrityError, match="case_number_counters"):
                session.flush()
        finally:
            session.close()
            engine.dispose()

    def test_allocator_works_for_existing_tenant(self, db_session):
        tenant = _make_tenant()
        db_session.commit()
        number = allocate_case_number(tenant.id)
        db_session.commit()
        assert number == f"{_YEAR}-00001"
        counter = CaseNumberCounter.query.filter_by(
            tenant_id=tenant.id, year=_YEAR
        ).one()
        assert counter.next_seq == 1


class TestCaseAccessContract:
    def test_case_number_immutable_via_workflow_edit_route(self, auth_client, db_session):
        case = _make_case()
        db_session.commit()
        original = case.case_number
        resp = auth_client.post(
            f"/cms/workflow/case/{case.id}/edit",
            data={
                "case_number": "2099-99999",
                "title": "Naam gewijzigd",
                "status": "active",
                "priority": "high",
                "client_name": "Klant-edit",
                "existing_subject_ids": "[]",
                "removed_subject_ids": "[]",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302, resp.status_code
        refreshed = db.session.get(Case, case.id)
        assert refreshed.case_number == original

    def test_case_new_ignores_submitted_number(self, auth_client, db_session):
        db_session.commit()
        resp = auth_client.post(
            "/cms/workflow/case/new",
            data={
                "case_number": "2099-99999",
                "client_name": "Client seq",
                "title": "Sequence Case",
                "subject_0_name": "Seq Subject",
                "subject_0_type": "person",
                "priority": "medium",
            },
        )
        assert resp.status_code in (200, 302), resp.status_code
        case = Case.query.filter_by(title="Sequence Case").first()
        assert case is not None
        assert case.case_number == f"{_YEAR}-00001"
        assert case.case_number != "2099-99999"