"""Atomic, tenant-scoped number allocation and investigation creation (ADR-0002 PR2).

Three number streams are allocated with database UPSERTs — never ``MAX()+1``:

- case numbers, keyed per ``(tenant_id, year)`` in ``case_number_counters``;
- investigation sequence numbers, keyed per ``(tenant_id, case_id)`` in
  ``investigation_seq_counters``;
- invoice numbers, keyed per ``(tenant_id, year)`` in
  ``invoice_number_counters`` (P1: unique per tenant, not globally).

Both counters live under the tenant's RLS context (FORCE RLS on PostgreSQL);
callers must have the correct tenant context (normal web/worker flow) or an
explicit RLS bypass (trusted worker/CLI paths) set before allocating.

Numbers are never changed or reused after issuance; gaps are allowed.
"""

from datetime import datetime, timezone

from cms.models import (
    Case,
    CaseNumberCounter,
    Investigation,
    InvestigationSeqCounter,
    InvestigationStatus,
    InvoiceNumberCounter,
    db,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _atomic_next(session, table, conflict_cols, values_without_seq) -> int:
    """Atomically allocate the next number via UPSERT + RETURNING.

    Works on both PostgreSQL and SQLite (>= 3.35): a fresh row hands out 1,
    existing rows are incremented in place. Concurrent allocations serialize
    on the keyed row, so the sequence stays unique and gapless-by-issuance.
    """
    from sqlalchemy.dialects import postgresql, sqlite

    if session.get_bind().dialect.name == "postgresql":
        insert = postgresql.insert
    else:
        insert = sqlite.insert

    stmt = insert(table).values(**values_without_seq, next_seq=1)
    stmt = stmt.on_conflict_do_update(
        index_elements=list(conflict_cols),
        set_={"next_seq": table.next_seq + 1, "updated_at": _utc_now()},
    ).returning(table.next_seq)
    return session.execute(stmt).scalar_one()


def _get_case(session, case_id: str) -> Case | None:
    return session.get(Case, case_id)


def allocate_case_number(tenant_id: str, session=None) -> str:
    """Allocate the next case number for a tenant + year (e.g. ``2026-00042``).

    ``session`` is an optional SQLAlchemy ``Session``; it defaults to the
    Flask-SQLAlchemy session (used by callers under a request/worker context).
    """
    session = session if session is not None else db.session
    if not tenant_id:
        raise ValueError("tenant_id is required to allocate a case number")
    year = _utc_now().year
    seq = _atomic_next(
        session,
        CaseNumberCounter,
        ("tenant_id", "year"),
        {"tenant_id": tenant_id, "year": year},
    )
    return f"{year}-{seq:05d}"


def preview_case_number(tenant_id: str, session=None) -> str:
    """Read-only preview of the next case number — never allocates."""
    session = session if session is not None else db.session
    year = _utc_now().year
    row = session.query(CaseNumberCounter).filter_by(tenant_id=tenant_id, year=year).first()
    seq = row.next_seq + 1 if row else 1
    return f"{year}-{seq:05d}"


def allocate_invoice_number(tenant_id: str, session=None) -> str:
    """Allocate the next invoice number for a tenant + year (``FAC-YYYY-NNNNN``).

    Atomic per ``(tenant_id, year)`` — never ``MAX()+1`` (P1). Two tenants may
    legitimately receive the same sequential number; within one tenant numbers
    stay unique and sequential (enforced by ``uq_tenant_invoice_number``).
    """
    session = session if session is not None else db.session
    if not tenant_id:
        raise ValueError("tenant_id is required to allocate an invoice number")
    year = _utc_now().year
    seq = _atomic_next(
        session,
        InvoiceNumberCounter,
        ("tenant_id", "year"),
        {"tenant_id": tenant_id, "year": year},
    )
    return f"FAC-{year}-{seq:05d}"


def preview_invoice_number(tenant_id: str, session=None) -> str:
    """Read-only preview of the next invoice number — never allocates."""
    session = session if session is not None else db.session
    year = _utc_now().year
    row = (
        session.query(InvoiceNumberCounter)
        .filter_by(tenant_id=tenant_id, year=year)
        .first()
    )
    seq = row.next_seq + 1 if row else 1
    return f"FAC-{year}-{seq:05d}"


def allocate_investigation_sequence_no(tenant_id: str, case_id: str, session=None) -> int:
    """Allocate the next investigation sequence number within a case.

    Enforces the hard tenant invariant first (ADR-0002 D8): the case must
    belong to the given tenant.
    """
    session = session if session is not None else db.session
    if not tenant_id or not case_id:
        raise ValueError("tenant_id and case_id are required")
    case = _get_case(session, case_id)
    if case is None:
        raise ValueError("case does not exist")
    if case.tenant_id != tenant_id:
        raise ValueError(
            "investigation.tenant_id must equal case.tenant_id (ADR-0002 D8)"
        )
    return _atomic_next(
        session,
        InvestigationSeqCounter,
        ("tenant_id", "case_id"),
        {"tenant_id": tenant_id, "case_id": case_id},
    )


def create_investigation(
    *,
    tenant_id: str,
    case_id: str,
    title: str,
    instructions: str | None = None,
    notes: str | None = None,
    status: str | None = None,
    created_by: str | None = None,
    session=None,
) -> Investigation:
    """Create an investigation with an atomically issued, per-case sequence number."""
    session = session if session is not None else db.session
    if not tenant_id or not case_id:
        raise ValueError("tenant_id and case_id are required")
    title = (title or "").strip()
    if not title:
        raise ValueError("title is required")
    case = _get_case(session, case_id)
    if case is None:
        raise ValueError("case does not exist")
    if case.tenant_id != tenant_id:
        raise ValueError(
            "investigation.tenant_id must equal case.tenant_id (ADR-0002 D8)"
        )
    sequence_no = _atomic_next(
        session,
        InvestigationSeqCounter,
        ("tenant_id", "case_id"),
        {"tenant_id": tenant_id, "case_id": case_id},
    )
    investigation = Investigation(
        tenant_id=tenant_id,
        case_id=case_id,
        sequence_no=sequence_no,
        title=title,
        instructions=instructions,
        notes=notes,
        status=status or InvestigationStatus.OPEN.value,
        created_by=created_by,
    )
    session.add(investigation)
    return investigation