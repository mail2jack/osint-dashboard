"""
PR59: Encryption Integrity Diagnostic Tool
==========================================
Usage:
    python -m cms.encryption_diagnostic [--subject ID] [--all] [--json]

Inspects encrypted fields across all subjects and reports metadata.
NEVER outputs plaintext or encryption keys.
"""

import argparse
import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app import app
from cms.models import db, Subject
from cms.encryption_utils import encryptor
from sqlalchemy import text

FERNET_PREFIX = "gAAAAAB"


def classify_ciphertext(value):
    """Classify ciphertext without decrypting."""
    if not value:
        return {"status": "empty", "length": 0}
    s = str(value)
    if not s.startswith(FERNET_PREFIX):
        return {"status": "not_fernet", "length": len(s), "prefix": s[:10]}
    return {"status": "fernet_valid_format", "length": len(s), "prefix": s[:20]}


def run_diagnostic(subject_id=None, output_json=False):
    """Run encryption diagnostic on all (or one) subjects."""
    import hashlib

    key_bytes = encryptor._get_key()
    key_fingerprint = hashlib.sha256(key_bytes).hexdigest()[:16]

    if subject_id:
        rows = db.session.execute(
            text("SELECT id, name FROM subjects WHERE id = :id"),
            {"id": subject_id},
        ).fetchall()
    else:
        rows = db.session.execute(
            text("SELECT id, name FROM subjects WHERE is_deleted = false")
        ).fetchall()

    results = {
        "key_fingerprint": f"sha256:{key_fingerprint}",
        "total_subjects": len(rows),
        "total_fields_inspected": 0,
        "decrypt_ok": 0,
        "decrypt_fail": 0,
        "empty_fields": 0,
        "subjects": [],
        "issues": [],
    }

    for row in rows:
        sid, sname = row[0], row[1]
        subject_result = {
            "id": sid,
            "name": sname,
            "fields": [],
        }

        for field in Subject.ENCRYPTED_FIELDS:
            raw_value = db.session.execute(
                text(f"SELECT {field} FROM subjects WHERE id = :id"),
                {"id": sid},
            ).scalar()

            if not raw_value:
                results["empty_fields"] += 1
                continue

            results["total_fields_inspected"] += 1
            classification = classify_ciphertext(raw_value)

            try:
                encryptor.decrypt(raw_value)
                results["decrypt_ok"] += 1
                dec_status = "ok"
            except Exception as exc:
                results["decrypt_fail"] += 1
                dec_status = f"fail:{type(exc).__name__}"
                issue = {
                    "subject_id": sid,
                    "subject_name": sname,
                    "field": field,
                    "error": type(exc).__name__,
                    "ciphertext_prefix": str(raw_value)[:30],
                    "ciphertext_length": len(str(raw_value)),
                }
                results["issues"].append(issue)

            subject_result["fields"].append(
                {
                    "field": field,
                    "format": classification["status"],
                    "length": classification["length"],
                    "decrypt": dec_status,
                }
            )

        results["subjects"].append(subject_result)

    if output_json:
        print(json.dumps(results, indent=2))
    else:
        print("Encryption Integrity Diagnostic")
        print(f"{'=' * 60}")
        print(f"Key fingerprint:       {results['key_fingerprint']}")
        print(f"Subjects inspected:    {results['total_subjects']}")
        print(f"Fields with data:      {results['total_fields_inspected']}")
        print(f"Empty fields:          {results['empty_fields']}")
        print(f"Decrypt OK:            {results['decrypt_ok']}")
        print(f"Decrypt FAILED:        {results['decrypt_fail']}")
        print()

        for s in results["subjects"]:
            failures = [f for f in s["fields"] if f["decrypt"].startswith("fail:")]
            if failures:
                print(f"  FAIL: {s['name']} (id={s['id'][:12]}...)")
                for f in failures:
                    print(
                        f"    {f['field']:30s} {f['decrypt']}  prefix={f.get('format', '')[:20]}"
                    )
            elif s["fields"]:
                ok = len([f for f in s["fields"] if f["decrypt"] == "ok"])
                print(f"  OK:   {s['name']} ({ok} fields)")

        if results["issues"]:
            print(f"\n{'=' * 60}")
            print(f"ISSUES ({len(results['issues'])}):")
            for issue in results["issues"]:
                print(
                    f"  {issue['subject_name'][:25]:25s} / {issue['field']:25s} {issue['error']}"
                )
                print(f"    prefix: {issue['ciphertext_prefix']}")
        else:
            print("\nNo decryption issues found.")

    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Encryption integrity diagnostic")
    parser.add_argument("--subject", help="Inspect a specific subject by ID")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    with app.app_context():
        db.session.execute(text("SET app.bypass_rls = 'true'"))
        run_diagnostic(subject_id=args.subject, output_json=args.json)
