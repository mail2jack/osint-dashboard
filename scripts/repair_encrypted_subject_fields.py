"""Repair plaintext-at-rest encrypted fields (security remediation, P0).

The workflow case-edit POST and background action runs used to decrypt ORM
subjects in-place and then commit, persisting *plaintext* in columns that are
supposed to hold Fernet ciphertext. This script re-encrypts those fields.

Safety properties:
* Dry-run by default; ``--apply`` writes + audits.
* ``--apply`` requires ``--backup-path`` pointing at an existing backup: the
  operator must have a restorable snapshot before any write happens. Without a
  verified backup path the script refuses to run.
* Idempotent — already-valid ciphertext is never touched, re-running is a no-op.
* Only values that are *recognizable plaintext* are encrypted (a value that
  fails decryption but still looks like a Fernet token is left alone and
  reported as ``unrecognized`` instead of being mangled).
* No values are logged — only row ids, field names and counts.
* RLS-aware: the tenant inventory runs under a temporary bypass (a tenantless
  query with FORCE RLS would otherwise return nothing and look like a no-op),
  then each tenant is processed under its own explicit RLS context. The
  context is always reset in ``finally`` so no connection is left scoped.
* A durable manifest (ids only) is written *before* the first write and
  rewritten as rows change, so a crash mid-run still leaves a restorable trace.
  Per tenant, the affected rows are fsynced to the manifest (via atomic
  replace) **before** that tenant's encrypt + commit — an empty manifest with
  changed data is impossible.

Usage:
    python3 scripts/repair_encrypted_subject_fields.py                    # dry-run
    python3 scripts/repair_encrypted_subject_fields.py --apply --backup-path /backups/cms_$(date +%F)   # write + audit
Run with DATABASE_URL set in .env or environment.
"""

import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app as flask_app  # noqa: E402
from cms.encryption_utils import encryptor  # noqa: E402
from cms.models import db, Address, AuditLog, Client, Contact, Subject  # noqa: E402
from cms.tenant_context import set_tenant_context  # noqa: E402

MODELS = [
    ("subject", Subject, Subject.ENCRYPTED_FIELDS),
    ("client", Client, Client.ENCRYPTED_FIELDS),
    ("address", Address, Address.ENCRYPTED_FIELDS),
    ("contact", Contact, Contact.ENCRYPTED_FIELDS),
]

FERRET_MARKER = "gAAAA"


def _field_state(value):
    """Classify a stored value: empty / ciphertext / plaintext / unrecognized."""
    if not value:
        return "empty"
    try:
        encryptor.decrypt(value)
        return "ciphertext"
    except Exception:
        if isinstance(value, str) and value.startswith(FERRET_MARKER):
            return "unrecognized"
        return "plaintext"


def _iter_tenant_ids():
    tenant_ids = {
        r[0]
        for _, model, _ in MODELS
        for r in db.session.query(model.tenant_id).distinct()
    }
    return sorted(t for t in tenant_ids if t)


def _write_manifest(manifest_path, manifest):
    """Write the manifest atomically and durably: write to a temp file, fsync,
    then os.replace() so a reader/crash never sees a half-written manifest."""
    tmp_path = f"{manifest_path}.tmp"
    with open(tmp_path, "w") as fh:
        json.dump(manifest, fh, indent=2, default=str)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp_path, manifest_path)


def repair(
    apply: bool = False,
    manifest_dir: str | None = None,
    backup_path: str | None = None,
):
    manifest_path = None
    per_tenant = {}
    totals = {"ciphertext": 0, "plaintext": 0, "unrecognized": 0, "encrypted": 0}

    if apply:
        if not backup_path or not os.path.exists(backup_path):
            raise SystemExit(
                "--apply requires --backup-path pointing at an existing backup; "
                "refusing to write without a verified restore point."
            )
        manifest_dir = manifest_dir or os.path.join(os.getcwd(), "repair_manifests")
        os.makedirs(manifest_dir, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        manifest_path = os.path.join(manifest_dir, f"plaintext_repair_{stamp}.json")
        manifest = {
            "created_at": stamp,
            "backup_path": backup_path,
            "affected": [],
        }
        # Durable manifest BEFORE the first write: if the process dies mid-run
        # there is still a restorable record. It is rewritten as rows change.
        _write_manifest(manifest_path, manifest)
    else:
        manifest = {"dry_run": True, "affected": []}

    # Tenant inventory: with FORCE RLS a tenantless query returns nothing,
    # which would look like a successful no-op. Bypass RLS for the inventory
    # only, read ids, then drop the bypass before any real work.
    try:
        set_tenant_context(db, None, bypass_rls=True)
        tenant_ids = _iter_tenant_ids()
    finally:
        set_tenant_context(db, None)

    for tenant_id in tenant_ids:
        # Explicit tenant context so Postgres RLS permits reads/writes per tenant.
        set_tenant_context(db, tenant_id)
        try:
            tenant_hits = []
            for model_name, model, fields in MODELS:
                for row in model.query.filter(model.tenant_id == tenant_id).all():
                    row_hits = []
                    for field in fields:
                        value = getattr(row, field)
                        state = _field_state(value)
                        totals[state] = totals.get(state, 0) + 1
                        if state == "plaintext":
                            row_hits.append(field)
                    if row_hits:
                        tenant_hits.append((model_name, row, row_hits))
            if tenant_hits:
                per_tenant[tenant_id] = [(m, row.id, f) for m, row, f in tenant_hits]
                totals["encrypted"] += sum(len(h[2]) for h in tenant_hits)

                # 1) Record every affected row in the manifest FIRST and persist
                #    it durably — before any DB write. A crash between commit
                #    and manifest-append would otherwise leave an empty manifest
                #    while data was already changed.
                for model_name, row, fields in tenant_hits:
                    manifest["affected"].append(
                        {
                            "tenant_id": tenant_id,
                            "model": model_name,
                            "id": row.id,
                            "fields": fields,
                        }
                    )
                    print(
                        f"  [{tenant_id[:8]}] {model_name} {row.id}: "
                        f"{'RE-ENCRYPT' if apply else 'WOULD RE-ENCRYPT'} {', '.join(fields)}"
                    )
                if apply and manifest_path:
                    _write_manifest(manifest_path, manifest)

                # 2) Only then encrypt and persist this tenant's rows.
                if apply:
                    for model_name, row, fields in tenant_hits:
                        for field in fields:
                            setattr(row, field, encryptor.encrypt(getattr(row, field)))
                    db.session.flush()
                    db.session.add(
                        AuditLog(
                            tenant_id=tenant_id,
                            user_id=None,
                            action="security_remediation",
                            entity_type="encrypted_fields",
                            description=(
                                "Re-encrypted plaintext-at-rest values in "
                                f"{len(tenant_hits)} row(s) "
                                f"({sum(len(h[2]) for h in tenant_hits)} field(s)) "
                                "after the workflow/action decryption bug."
                            ),
                        )
                    )
                    db.session.commit()
        finally:
            set_tenant_context(db, None)

    if apply and manifest_path:
        _write_manifest(manifest_path, manifest)
        print(f"\nManifest written: {manifest_path}")

    print("\n=== Summary ===")
    print(f"tenants with affected rows: {len(per_tenant)}")
    print(f"fields already ciphertext (untouched): {totals['ciphertext']}")
    print(f"fields encrypted: {totals['encrypted']}")
    print(f"fields unrecognized (left alone): {totals['unrecognized']}")
    if not apply:
        print("\nDry-run — no changes written. Re-run with --apply to write.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=(
            "Re-encrypt plaintext-at-rest values in encrypted columns "
            "(default: dry-run)."
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write changes and add audit entries (default is dry-run).",
    )
    parser.add_argument(
        "--backup-path",
        metavar="PATH",
        default=None,
        help=(
            "Required with --apply: an existing backup directory/file that "
            "holds a restorable snapshot. --apply refuses to run without it."
        ),
    )
    args = parser.parse_args()
    with flask_app.app_context():
        repair(apply=args.apply, backup_path=args.backup_path)
