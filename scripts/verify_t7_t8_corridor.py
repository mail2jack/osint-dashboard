#!/usr/bin/env python3
"""ADR-0002 T7/T8 corridor re-test — RLS pre-cleanup + FORCE-RLS neighbor filter.

Re-runs the level-A functional-test corridor on the ACME test tenant with the
two checks that were under-verified in the original script (see issue #83):

  T7  Pre-cleanup verification WITHIN the RLS context: after creating the
      test case + number counters, first verify the end state under the
      tenant context itself (``set_tenant_context(db, tenant_id)`` WITHOUT
      bypass), only then check externally with ``bypass_rls``.

  T8  FORCE-RLS filter on a NEIGHBOR tenant: create one row for a second
      tenant and prove it shows 0 results under the ACME context. The
      original check only compared cross-tenant rows within the SAME tenant
      context, so FORCE-RLS filtering of a neighbor tenant went unnoticed.

Lesson learned (applied): ``app.tenant_id``/``app.bypass_rls`` GUCs are
per-connection; after ``commit()`` connection reuse can drop the context, so
each check re-asserts its context and verification queries run through the
model/session. RLS does not apply to superusers — the script refuses to run
as a superuser role (non-superuser, like CI's ``cms_test``, is required).

Writes a markdown artifact into ``reports/`` (same style as rollout reports).

Usage:
    ./scripts/verify_t7_t8_corridor.py --dir /opt/osint-dashboard \
        [--tenant "Acme Corp Rotterdam"] \
        [--artifact-dir /opt/osint-dashboard/reports]

Exit code 0 only if every T7/T8 check passes.
"""

import argparse
import datetime
import os
import subprocess
import sys
import uuid

# The script may be invoked as ``python scripts/verify_t7_t8_corridor.py``
# where only ``scripts/`` is on sys.path — add the project root so the app
# package is importable regardless of ${PWD}.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

try:
    from app import app  # noqa: E402
    from cms.models import (  # noqa: E402,WPS433
        Case,
        CaseNumberCounter,
        Client,
        Investigation,
        InvoiceNumberCounter,
        Tenant,
        db,
    )
    from cms.services.sequence_service import (  # noqa: E402,WPS433
        allocate_case_number,
        allocate_invoice_number,
        create_investigation,
    )
    from cms.tenant_context import set_tenant_context  # noqa: E402,WPS433
    from sqlalchemy import text  # noqa: E402
except ImportError as e:  # pragma: no cover - import guard only
    print(f"[t7-t8-corridor] ERROR cannot import app: {e}")
    sys.exit(2)


def _run(args) -> int:
    project_dir = os.path.abspath(args.dir)
    if project_dir not in sys.path:
        sys.path.insert(0, project_dir)

    os.environ.setdefault("CMS_CONFIG", "DevelopmentConfig")

    reports_dir = os.path.abspath(args.artifact_dir or os.path.join(project_dir, "reports"))
    os.makedirs(reports_dir, exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    artifact_path = os.path.join(reports_dir, f"t7-t8-corridor-{stamp}.md")

    results: list[tuple[str, bool, str]] = []
    notes: list[str] = []

    try:
        with app.app_context():
            if db.engine.dialect.name != "postgresql":
                print("[t7-t8-corridor] SKIP: PostgreSQL required for RLS checks")
                return 0

            _check_non_superuser(db, results)

            # Ensure a clean tenant context (equivalent of an external session)
            set_tenant_context(db, None, bypass_rls=True)
            tenant = Tenant.query.filter_by(name=args.tenant).first()
            if tenant is None:
                results.append(
                    (
                        "T0-corridor",
                        False,
                        f"tenant '{args.tenant}' not found in DB",
                    )
                )
                return _finish_and_report(results, notes, artifact_path,
                                           project_dir, report=False)

            client = case = inv = None
            buur = buur_case = buur_client = None
            acme_case_number = acme_invoice_number = None
            corridor_created = False

            try:
                # --- Corridor (T7) inside one transaction with the ACME context ---
                set_tenant_context(db, tenant.id)

                client = Client(
                    tenant_id=tenant.id,
                    name=f"T7 corridor client {uuid.uuid4().hex[:8]}",
                    is_company=True,
                    is_active=False,
                )
                db.session.add(client)
                db.session.flush()

                acme_case_number = allocate_case_number(tenant.id)
                case = Case(
                    tenant_id=tenant.id,
                    case_number=acme_case_number,
                    client_id=client.id,
                    title=f"T7 corridor case {uuid.uuid4().hex[:8]}",
                    start_date=datetime.date.today(),
                    created_by=None,
                )
                db.session.add(case)
                db.session.flush()

                inv = create_investigation(
                    tenant_id=tenant.id,
                    case_id=case.id,
                    title=f"T7 corridor investigation {uuid.uuid4().hex[:8]}",
                    created_by=None,
                )
                db.session.add(inv)

                acme_invoice_number = allocate_invoice_number(tenant.id)
                db.session.commit()

                notes.append(
                    f"ACME corridor issued case '{acme_case_number}' and "
                    f"invoice '{acme_invoice_number}'"
                )

                # --- T7: verify end state FIRST within RLS context (no bypass) ---
                set_tenant_context(db, tenant.id)
                visible = _count_under_context(db, tenant.id, case)
                ok7_before = (
                    visible["case"] == 1
                    and visible["counter"] == 1
                    and visible["invoice_counter"] == 1
                    and visible["investigation"] == 1
                )
                results.append(
                    (
                        "T7-before-bypass",
                        ok7_before,
                        f"ACME end state under tenant context: case={visible['case']}, "
                        f"counter={visible['counter']}, "
                        f"invoice_counter={visible['invoice_counter']}, "
                        f"investigation={visible['investigation']}",
                    )
                )

                # --- T7: verify externally with bypass ---
                set_tenant_context(db, tenant.id, bypass_rls=True)
                bypassed = _count_under_context(db, tenant.id, case)
                ok_bypass = (
                    bypassed == visible
                    and visible["case"] == 1
                    and visible["counter"] == 1
                    and visible["invoice_counter"] == 1
                    and visible["investigation"] == 1
                )
                results.append(
                    (
                        "T7-bypass",
                        ok_bypass,
                        f"external bypass: case={bypassed['case']}, "
                        f"counter={bypassed['counter']}, "
                        f"invoice_counter={bypassed['invoice_counter']}, "
                        f"investigation={bypassed['investigation']} — equal "
                        f"to pre-bypass ({bypassed == visible})",
                    )
                )

                # --- T8: neighbor tenant is invisible under ACME context ---
                buur = Tenant(
                    name=f"T8 neighbor tenant {uuid.uuid4().hex[:6]}",
                    slug=f"t8-buur-{uuid.uuid4().hex[:10]}",
                    is_active=False,
                    tier="free",
                    subscription_status="incomplete",
                    join_code=uuid.uuid4().hex[:12],
                )
                db.session.add(buur)
                db.session.flush()
                buur_id = buur.id

                # Create the neighbor row with ITS OWN context (not the ACME one)
                set_tenant_context(db, buur_id)
                buur_client = Client(
                    tenant_id=buur_id,
                    name=f"T8 neighbor client {uuid.uuid4().hex[:8]}",
                    is_company=True,
                    is_active=False,
                )
                db.session.add(buur_client)
                db.session.flush()
                buur_case_num = allocate_case_number(buur_id)
                buur_case = Case(
                    tenant_id=buur_id,
                    case_number=buur_case_num,
                    client_id=buur_client.id,
                    title=f"T8 neighbor case {uuid.uuid4().hex[:8]}",
                    start_date=datetime.date.today(),
                    created_by=None,
                )
                db.session.add(buur_case)
                db.session.commit()

                # Back under ACME context (no bypass): neighbor row must be invisible
                set_tenant_context(db, tenant.id)
                neighbor_visible = _count_for_tenant(db, buur_id)
                ok8 = (
                    neighbor_visible["cases"] == 0
                    and neighbor_visible["counters"] == 0
                    and neighbor_visible["investigations"] == 0
                )
                results.append(
                    (
                        "T8-neighbor-filter",
                        ok8,
                        f"neighbor rows under ACME context: "
                        f"{neighbor_visible['cases']} cases, "
                        f"{neighbor_visible['counters']} counters, "
                        f"{neighbor_visible['investigations']} investigations",
                    )
                )

                # Cross-check: with the neighbor's own context the row IS visible
                set_tenant_context(db, buur_id)
                own = _count_for_tenant(db, buur_id)
                ok8b = own["cases"] == 1
                results.append(
                    (
                        "T8-own-context-sees-row",
                        ok8b,
                        f"neighbor tenant under its own context sees "
                        f"{own['cases']} case(s)",
                    )
                )

                # Also verify with bypass (full read) that everything created is there
                set_tenant_context(db, None, bypass_rls=True)
                full = _count_for_tenant(db, buur_id)
                ok8c = full["cases"] == 1
                results.append(
                    (
                        "T8-bypass-sees-row",
                        ok8c,
                        f"bypass read sees {full['cases']} neighbor case(s)",
                    )
                )

                # Track the items that must be reverted in finally
                corridor_created = (
                    case is not None
                    and inv is not None
                    and client is not None
                )

            finally:
                try:
                    # Read the plain IDs while the instances are still bound,
                    # then expunge; cleanup afterwards only touches primitives
                    # so the final commit never trips on a detached instance.
                    ids = {
                        "buur_id": buur.id if buur else None,
                        "buur_case_id": buur_case.id if buur_case else None,
                        "buur_client_id": buur_client.id if buur_client else None,
                        "case_id": case.id if case else None,
                        "inv_id": inv.id if inv else None,
                        "client_id": client.id if client else None,
                    }
                    db.session.expunge_all()
                    _cleanup(db, db.session, **ids)
                    db.session.commit()
                    notes.append(
                        "corridor objects removed; ACME counters kept"
                        if corridor_created
                        else "partial corridor — cleanup ran defensively"
                    )
                except Exception as cleanup_exc:  # pragma: no cover - defensive
                    db.session.rollback()
                    notes.append(f"cleanup incomplete: {cleanup_exc}")

        return _finish_and_report(results, notes, artifact_path, project_dir, report=True)

    except Exception as e:  # pragma: no cover - top-level guard
        print(f"[t7-t8-corridor] ERROR: {e}")
        return 2


def _check_non_superuser(db, results) -> None:
    row = db.session.execute(
        text(
            "SELECT rolsuper FROM pg_roles WHERE rolname = current_user"
        )
    ).scalar()
    results.append(
        (
            "T0-role",
            row is False,
            f"current_user is superuser? {row} (required: not superuser)",
        )
    )


def _count_under_context(db, tenant_id, case):
    year = datetime.datetime.now(datetime.timezone.utc).year
    return {
        "case": db.session.query(Case).filter(
            Case.id == case.id, Case.tenant_id == tenant_id
        ).count(),
        "counter": db.session.query(CaseNumberCounter).filter(
            CaseNumberCounter.tenant_id == tenant_id,
            CaseNumberCounter.year == year,
        ).count(),
        "invoice_counter": db.session.query(InvoiceNumberCounter).filter(
            InvoiceNumberCounter.tenant_id == tenant_id,
            InvoiceNumberCounter.year == year,
        ).count(),
        "investigation": db.session.query(Investigation).join(
            Case, Investigation.case_id == Case.id
        ).filter(
            Investigation.tenant_id == tenant_id,
            Investigation.case_id == case.id,
        ).count(),
    }


def _count_for_tenant(db, tenant_id):
    return {
        "cases": db.session.query(Case).filter(Case.tenant_id == tenant_id).count(),
        "counters": db.session.query(CaseNumberCounter).filter(
            CaseNumberCounter.tenant_id == tenant_id
        ).count(),
        "investigations": db.session.query(Investigation).filter(
            Investigation.tenant_id == tenant_id
        ).count(),
    }


_CASE_CHILD_TABLES = (
    ("investigation_seq_counters", "case_id"),
    ("investigations", "case_id"),
    ("findings", "case_id"),
    ("invoices", "case_id"),
    ("documents", "case_id"),
    ("financial_records", "case_id"),
    ("case_subjects", "case_id"),
)


def _delete_bulk(session, table, column, value) -> None:
    session.execute(
        text(f"DELETE FROM {table} WHERE {column} = :value"),
        {"value": value},
    )


def _cleanup(db, session, *, buur_id, buur_case_id, buur_client_id, case_id, inv_id, client_id) -> None:
    """Delete corridor-created rows under a full-bypass context.

    Works on plain IDs only (no ORM instance attribute access) so it stays
    correct after ``expunge_all()``. Children-first SQL order, no per-query
    try/except that would poison the transaction.
    """
    set_tenant_context(db, None, bypass_rls=True)

    if buur_id:
        if buur_case_id:
            for tbl, col in _CASE_CHILD_TABLES:
                _delete_bulk(session, tbl, col, buur_case_id)
            _delete_bulk(session, "cases", "id", buur_case_id)
        if buur_client_id:
            _delete_bulk(session, "clients", "id", buur_client_id)
        _delete_bulk(session, "investigations", "tenant_id", buur_id)
        _delete_bulk(session, "cases", "tenant_id", buur_id)
        _delete_bulk(session, "case_number_counters", "tenant_id", buur_id)
        _delete_bulk(session, "invoice_number_counters", "tenant_id", buur_id)
        _delete_bulk(session, "tenants", "id", buur_id)

    if case_id:
        for tbl, col in _CASE_CHILD_TABLES:
            _delete_bulk(session, tbl, col, case_id)
        _delete_bulk(session, "cases", "id", case_id)
    if inv_id:
        _delete_bulk(session, "investigations", "id", inv_id)
    if client_id:
        _delete_bulk(session, "clients", "id", client_id)


def _finish_and_report(results, notes, artifact_path, project_dir, *, report) -> int:
    failed = [name for name, ok, _ in results if not ok]
    passed = len(results) - len(failed)

    for name, ok, detail in results:
        mark = "PASS" if ok else "FAIL"
        print(f"[t7-t8-corridor] {mark:5} {name}: {detail}")
    for note in notes:
        print(f"[t7-t8-corridor] NOTE: {note}")

    if not report:
        return 1 if failed else 0

    sha = _git_sha(project_dir)
    with open(artifact_path, "w", encoding="utf-8") as fh:
        fh.write("# ADR-0002 T7/T8 corridor re-test artifact (issue #83)\n")
        fh.write(f"**Datum:** {_now_local()}\n")
        fh.write(f"**Git SHA:** `{sha or 'unknown'}`\n")
        fh.write(f"**Host:** `{os.uname().nodename}`\n")
        fh.write("**Status:** "
                 + ("GESLAAGD" if not failed else f"MISLUKT ({len(failed)})") + "\n")
        fh.write("\n---\n\n## Checkresultaat\n\n")
        fh.write("| Check | Status | Detail |\n|---|---|---|\n")
        for name, ok, detail in results:
            fh.write(f"| {name} | {'PASS' if ok else 'FAIL'} | {detail} |\n")
        fh.write("\n---\n\n## Notities\n\n")
        for note in notes:
            fh.write(f"- {note}\n")
        fh.write(f"\n**Pass** {passed}/{passed + len(failed)}\n")
    print(f"[t7-t8-corridor] artifact -> {artifact_path}")
    return 1 if failed else 0


def _now_local() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _git_sha(project_dir: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", project_dir, "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        return out.stdout.strip()
    except Exception:  # pragma: no cover - best effort
        return None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Re-run ADR-0002 T7/T8 corridor checks (RLS pre-cleanup "
                    "verification + neighbor-tenant FORCE-RLS filter)."
    )
    parser.add_argument("--dir", required=True, help="Project directory")
    parser.add_argument(
        "--tenant",
        default="Acme Corp Rotterdam",
        help="Name of the ACME test tenant (default: 'Acme Corp Rotterdam')",
    )
    parser.add_argument(
        "--artifact-dir",
        help="Directory for the report artifact (default: <dir>/reports)",
    )
    args = parser.parse_args()
    return _run(args)


if __name__ == "__main__":
    raise SystemExit(main())