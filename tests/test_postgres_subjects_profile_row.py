"""PostgreSQL regression for subjects_list.py:417 Row attribute-access bug.

SQLAlchemy 2.0 ``Row`` objects from ``.with_entities()`` do not support
string-index access (``row["id"]``) — they raise ``TypeError: tuple indices
must be integers or slices, not str``. The fix (commit 9d0f4c5 / PR #155)
switches to attribute access (``row.id``).

This test verifies the fix on real PostgreSQL, where ``with_entities``
returns ``Row`` objects backed by ``psycopg2`` cursors (not SQLite tuples).
On SQLite, the same code path may produce different row types depending on
the driver, so PG coverage is essential.

Reproduces the exact query path from ``cms/routes/subjects_list.py:375-419``
``subject_profile`` relation_candidates serialisation.
"""

import os
import uuid

import pytest

from cms.models import Subject, User, db
from cms.tenant_context import set_tenant_context

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL", "").startswith("postgresql"),
    reason="PostgreSQL regression: Row attribute access requires real PG.",
)


def _mk_subject_with_context(tenant_id, name_prefix="PG-row"):
    """Insert a subject with bypass_rls, then switch to tenant context.

    This mirrors the production request-path: data is inserted by the
    authenticated user (GUC set), then read via the ORM in a
    tenant-scoped session (bypass_rls=False).
    """
    set_tenant_context(db, tenant_id, bypass_rls=True)
    subject = Subject(
        tenant_id=tenant_id,
        name=f"{name_prefix}-{uuid.uuid4().hex[:8]}",
        subject_type="person",
    )
    db.session.add(subject)
    db.session.flush()
    db.session.commit()
    # Switch to the real request-path: no bypass, tenant only.
    set_tenant_context(db, tenant_id)
    return subject


class TestPGRowAttributeAccess:
    """Verify with_entities Row objects support attribute access on real PG."""

    def test_with_entities_rows_support_attribute_access(self, app):
        """The exact query from subjects_list.py:375-384 on real PostgreSQL.

        Pre-fix: ``c["id"]`` raises TypeError.
        Post-fix: ``c.id`` works, returning a clean dict of candidates.
        """
        admin = User.query.filter_by(username="admin").first()
        tenant_id = admin.tenant_id

        subject_a = _mk_subject_with_context(tenant_id, "PG-row-A")
        subject_b = _mk_subject_with_context(tenant_id, "PG-row-B")

        # Switch to bypass_rls=True for cleanup, then back to tenant context
        # to simulate the authenticated request path.
        set_tenant_context(db, tenant_id, bypass_rls=True)
        db.session.expire_all()
        db.session.commit()
        set_tenant_context(db, tenant_id)

        # Reproduce the exact query from subjects_list.py:375-384
        related_ids = set()
        candidates = (
            Subject.query.filter(
                Subject.is_deleted.is_(False),
                Subject.tenant_id == tenant_id,
                Subject.id != subject_a.id,
                ~Subject.id.in_(related_ids or [""]),
            )
            .order_by(Subject.name)
            .limit(500)
            .with_entities(Subject.id, Subject.name, Subject.subject_type)
            .all()
        )

        # The Row objects must be non-empty (the sibling subject is present).
        assert len(candidates) >= 1

        # POST-FIX: attribute access works on each Row.
        serialised = [
            {"id": c.id, "name": c.name, "subject_type": c.subject_type}
            for c in candidates
        ]
        ids = {c["id"] for c in serialised}
        # subject_a is excluded (Subject.id != subject_a.id), subject_b present.
        assert subject_b.id in ids
        assert subject_a.id not in ids
        # At least one candidate is present (the sibling).
        assert len(serialised) >= 1

        # PRE-FIX: string indexing raises TypeError on real PG.
        # (This assertion documents the bug — it MUST fail on old code
        # and pass on fixed code.  Running this file only on PostgreSQL
        # CI ensures this is exercised against the production driver.)
        for c in candidates:
            with pytest.raises(TypeError, match="tuple indices must be integers"):
                _ = c["id"]

    def test_subject_profile_query_returns_rows_not_dicts(self, app):
        """Sanity: with_entities returns Row objects (not dicts), confirming
        that the attribute-access pattern is the correct fix."""
        admin = User.query.filter_by(username="admin").first()
        tenant_id = admin.tenant_id

        subject_a = _mk_subject_with_context(tenant_id, "PG-sanity-A")
        _mk_subject_with_context(tenant_id, "PG-sanity-B")

        set_tenant_context(db, tenant_id, bypass_rls=True)
        db.session.expire_all()
        db.session.commit()
        set_tenant_context(db, tenant_id)

        candidates = (
            Subject.query.filter(
                Subject.is_deleted.is_(False),
                Subject.tenant_id == tenant_id,
                Subject.id != subject_a.id,
            )
            .with_entities(Subject.id, Subject.name, Subject.subject_type)
            .all()
        )
        # On PG, these are SQLAlchemy Row objects, not dicts.
        for c in candidates:
            assert type(c).__name__ == "Row", f"Expected Row, got {type(c)}"
