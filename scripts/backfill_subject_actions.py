"""Backfill research_actions.subject_id from target snapshots (ADR-0001 rollout).

Research actions created before the subject-first layout can carry a
subject-scoped ``target_snapshot`` but no ``subject_id`` FK. This script only
backfills unambiguous rows:

* ``target_kind == "subject"`` and the snapshot contains a ``subject_id``,
* that subject still exists in the same tenant and is not soft-deleted.

Rows whose snapshot is missing a subject_id, whose subject is gone or soft
deleted, or which point across tenants are skipped and reported. Case-wide
actions (``target_kind != "subject"``) are never touched. Re-running the script
is a no-op for already-linked rows (idempotent).

Usage:
    python3 scripts/backfill_subject_actions.py            # dry-run
    python3 scripts/backfill_subject_actions.py --apply    # write + audit
Run with DATABASE_URL set in .env or environment.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app as flask_app
from cms.models import db, ResearchAction, Subject, AuditLog


def _snapshot_subject_id(action):
    try:
        data = json.loads(action.target_snapshot or "null")
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    sid = data.get("subject_id")
    return sid or None


def backfill(apply: bool = False):
    rows = ResearchAction.query.filter(
        ResearchAction.subject_id.is_(None),
        ResearchAction.target_kind == "subject",
        ResearchAction.target_snapshot.isnot(None),
    ).all()

    stats = {
        "scanned": len(rows),
        "matched": 0,
        "skipped_no_snapshot": 0,
        "skipped_missing_subject": 0,
        "skipped_tenant_mismatch": 0,
        "skipped_already_linked": 0,
    }
    per_tenant_updated = {}
    print(f"Scanned {len(rows)} subject-scoped action(s) without a subject_id.\n")

    for action in rows:
        sid = _snapshot_subject_id(action)
        if not sid:
            stats["skipped_no_snapshot"] += 1
            print(f"  skip (no snapshot subject_id): {action.id}")
            continue

        subject = db.session.get(Subject, sid)
        if subject is None or subject.is_deleted:
            stats["skipped_missing_subject"] += 1
            print(f"  skip (subject missing/deleted): {action.id} -> {sid}")
            continue

        if subject.tenant_id != action.tenant_id:
            stats["skipped_tenant_mismatch"] += 1
            print(f"  skip (tenant mismatch): {action.id} -> {sid}")
            continue

        if action.subject_id == sid:
            stats["skipped_already_linked"] += 1
            continue

        stats["matched"] += 1
        print(
            f"  {'SET' if apply else 'WOULD SET'}: {action.id} -> subject "
            f"{sid} ({subject.name})"
        )
        if apply:
            action.subject_id = sid
            per_tenant_updated[action.tenant_id] = (
                per_tenant_updated.get(action.tenant_id, 0) + 1
            )

    if apply:
        db.session.commit()
        for tenant_id, count in per_tenant_updated.items():
            db.session.add(
                AuditLog(
                    tenant_id=tenant_id,
                    user_id=None,
                    action="backfill",
                    entity_type="research_action",
                    description=(
                        f"Backfilled subject_id on {count} research action(s) "
                        "from target snapshots (ADR-0001 rollout)."
                    ),
                )
            )
        db.session.commit()
        print(
            f"\nCommitted {stats['matched']} update(s); wrote "
            f"{len(per_tenant_updated)} audit entry/entries."
        )

    print("\n" + "\n".join(f"{k}: {v}" for k, v in stats.items()))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=(
            "Backfill research_actions.subject_id from target snapshots "
            "(default: dry-run)."
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write changes and add audit entries (default is dry-run).",
    )
    args = parser.parse_args()
    with flask_app.app_context():
        backfill(apply=args.apply)
