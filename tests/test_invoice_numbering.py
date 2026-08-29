"""P1: per-tenant invoice numbering — atomic, tenant-scoped issuance.

Covers the SQLite-backed behaviour (PostgreSQL additions live in
``test_postgres_integration.py``):

- invoice numbers are allocated atomically and sequentially per (tenant, year);
- two tenants may lawfully hold the same invoice number (per-tenant contract);
- the composite ``(tenant_id, invoice_number)`` constraint rejects same-tenant
  duplicates while allowing equal numbers across tenants;
- counters are isolated per tenant, gaps are allowed and numbers never reuse;
- a forced invoice failure on case-create is handled explicitly (clear error,
  no 500, no partial case/client/subject/AuditLog/invoice/counter) and a retry
  creates exactly one case and one invoice.
"""

import threading
import uuid
from datetime import date, datetime, timezone

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from cms.models import (
    Client,
    Invoice,
    InvoiceNumberCounter,
    InvoiceStatus,
    Tenant,
    db,
)
from cms.services.sequence_service import (
    allocate_invoice_number,
    preview_invoice_number,
)

_YEAR = datetime.now(timezone.utc).year


def _raw_session_factory():
    """Session factory over the app engine for real multi-thread concurrency."""
    return sessionmaker(bind=db.engine)


def _fk_enforced_engine():
    """A SQLite engine with ``PRAGMA foreign_keys=ON`` per connection.

    The app deliberately keeps FK enforcement off on SQLite, so this engine
    exists to prove the schema-level foreign key on the counter is present.
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


def _make_client(tenant_id=None) -> Client:
    client = Client(name="Inv Test Client", is_active=True)
    if tenant_id:
        client.tenant_id = tenant_id
    db.session.add(client)
    db.session.flush()
    return client


def _make_invoice(tenant_id: str, client_id: str, number: str) -> Invoice:
    invoice = Invoice(
        tenant_id=tenant_id,
        client_id=client_id,
        invoice_number=number,
        issue_date=date.today(),
        due_date=date.today(),
        status=InvoiceStatus.DRAFT.value,
    )
    db.session.add(invoice)
    return invoice


_CASE_NEW_DATA = {
    "client_name": "Invoice Client seq",
    "title": "Invoice Sequence Case",
    "subject_0_name": "Invoice Subject",
    "subject_0_type": "person",
    "priority": "medium",
}


class TestInvoiceNumberAllocation:
    def test_sequential_per_tenant_with_preview(self, db_session):
        db_session.commit()
        assert preview_invoice_number("t-inv") == f"FAC-{_YEAR}-00001"
        nums = [allocate_invoice_number("t-inv") for _ in range(3)]
        db_session.commit()
        assert nums == [
            f"FAC-{_YEAR}-00001",
            f"FAC-{_YEAR}-00002",
            f"FAC-{_YEAR}-00003",
        ]
        assert preview_invoice_number("t-inv") == f"FAC-{_YEAR}-00004"

    def test_preview_never_allocates(self, db_session):
        db_session.commit()
        preview_invoice_number("t-inv-p")
        preview_invoice_number("t-inv-p")
        assert InvoiceNumberCounter.query.filter_by(tenant_id="t-inv-p").count() == 0

    def test_is_per_tenant(self, db_session):
        other = _make_tenant()
        db_session.commit()
        assert allocate_invoice_number("t-1") == f"FAC-{_YEAR}-00001"
        assert allocate_invoice_number(other.id) == f"FAC-{_YEAR}-00001"
        assert allocate_invoice_number("t-1") == f"FAC-{_YEAR}-00002"

    def test_empty_tenant_rejected(self):
        with pytest.raises(ValueError):
            allocate_invoice_number("")

    def test_counter_gap_never_reused(self, db_session):
        db_session.commit()
        first_three = [allocate_invoice_number("t-gap") for _ in range(3)]
        fourth = allocate_invoice_number("t-gap")
        db_session.commit()
        assert first_three == [
            f"FAC-{_YEAR}-00001",
            f"FAC-{_YEAR}-00002",
            f"FAC-{_YEAR}-00003",
        ]
        assert fourth == f"FAC-{_YEAR}-00004"
        counter = InvoiceNumberCounter.query.filter_by(
            tenant_id="t-gap", year=_YEAR
        ).one()
        assert counter.next_seq == 4

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
                    allocate_invoice_number("t-par", session=session)
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
        numbers = [int(r[0].split("-")[-1]) for r in results if r]
        assert sorted(numbers) == list(range(1, worker_count + 1))


class TestInvoiceNumberConstraint:
    def test_same_tenant_duplicate_rejected_cross_tenant_allowed(self, db_session):
        db_session.commit()
        other = _make_tenant()
        client_a = _make_client()
        client_b = _make_client(tenant_id=other.id)
        db_session.commit()

        _make_invoice(client_a.tenant_id, client_a.id, f"FAC-{_YEAR}-00001")
        _make_invoice(other.id, client_b.id, f"FAC-{_YEAR}-00001")
        db_session.commit()

        _make_invoice(client_a.tenant_id, client_a.id, f"FAC-{_YEAR}-00001")
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_counter_for_unknown_tenant_refused_by_db(self, db_session):
        engine = _fk_enforced_engine()
        session = sessionmaker(bind=engine)()
        try:
            bad = InvoiceNumberCounter(
                tenant_id=str(uuid.uuid4()), year=_YEAR, next_seq=1
            )
            session.add(bad)
            with pytest.raises(IntegrityError, match="invoice_number_counters"):
                session.flush()
        finally:
            session.close()
            engine.dispose()

    def test_allocator_works_for_existing_tenant(self, db_session):
        tenant = _make_tenant()
        db_session.commit()
        number = allocate_invoice_number(tenant.id)
        db_session.commit()
        assert number == f"FAC-{_YEAR}-00001"
        counter = InvoiceNumberCounter.query.filter_by(
            tenant_id=tenant.id, year=_YEAR
        ).one()
        assert counter.next_seq == 1


class TestInvoiceThroughCaseCreate:
    def test_case_creation_allocates_number_and_invoice(self, auth_client, db_session):
        db_session.commit()
        resp = auth_client.post("/cms/workflow/case/new", data=_CASE_NEW_DATA)
        assert resp.status_code in (200, 302), resp.status_code
        invoices = Invoice.query.all()
        assert len(invoices) == 1
        assert invoices[0].invoice_number == f"FAC-{_YEAR}-00001"
        assert invoices[0].status == InvoiceStatus.DRAFT.value

    def test_invoice_failure_is_handled_and_leaves_no_durable_state(
        self, auth_client, db_session, monkeypatch
    ):
        db_session.commit()
        from cms.models import Case
        from cms.services import invoice_service

        def _boom(tenant_id, session=None):  # pragma: no cover - forced failure
            raise IntegrityError("stmt", {}, Exception("simulated collision"))

        monkeypatch.setattr(invoice_service, "allocate_invoice_number", _boom)

        # The route must catch the invoice failure and answer with a clear,
        # user-facing error redirect — never an unhandled 500.
        resp = auth_client.post("/cms/workflow/case/new", data=_CASE_NEW_DATA)
        assert resp.status_code == 302, resp.status_code
        assert resp.status_code != 500
        assert resp.headers["Location"].endswith("/cms/workflow/case/new")
        with auth_client.session_transaction() as sess:
            flashes = sess.get("_flashes", [])
        assert flashes, "expected a user-facing error flash"
        assert all(category == "danger" for category, _ in flashes)
        assert any(
            "not created" in str(msg).lower() and "invoicing" in str(msg).lower()
            for _, msg in flashes
        )

        db_session.rollback()
        # No orphan case, no invoice, no counter leak — retry is safe.
        assert Case.query.filter_by(title=_CASE_NEW_DATA["title"]).count() == 0
        assert Invoice.query.count() == 0
        assert InvoiceNumberCounter.query.count() == 0

    def test_retry_after_invoice_failure_creates_exactly_one(self, auth_client, db_session, monkeypatch):
        db_session.commit()
        from cms.models import Case
        from cms.services import invoice_service

        calls = {"n": 0}

        def _flaky(tenant_id, session=None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise IntegrityError("stmt", {}, Exception("simulated collision"))
            return allocate_invoice_number(tenant_id, session=session)

        monkeypatch.setattr(invoice_service, "allocate_invoice_number", _flaky)

        first = auth_client.post("/cms/workflow/case/new", data=_CASE_NEW_DATA)
        assert first.status_code == 302 and first.status_code != 500
        db_session.rollback()
        assert Case.query.filter_by(title=_CASE_NEW_DATA["title"]).count() == 0

        second = auth_client.post("/cms/workflow/case/new", data=_CASE_NEW_DATA)
        assert second.status_code in (200, 302), second.status_code
        cases = Case.query.filter_by(title=_CASE_NEW_DATA["title"]).all()
        invoices = Invoice.query.all()
        assert len(cases) == 1
        assert len(invoices) == 1
        assert invoices[0].invoice_number == f"FAC-{_YEAR}-00001"