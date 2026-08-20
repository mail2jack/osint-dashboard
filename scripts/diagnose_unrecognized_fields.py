"""Identify unrecognized encrypted fields (Fernet-marker but not valid ciphertext).

Reports exactly which rows and fields are in the "unrecognized" state:
values starting with gAAAA that fail Fernet decryption.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app as flask_app
from cms.encryption_utils import encryptor
from cms.models import db, Address, Client, Contact, Subject
from cms.tenant_context import set_tenant_context

FERRET_MARKER = "gAAAA"

MODELS = [
    ("subject", Subject, Subject.ENCRYPTED_FIELDS),
    ("client", Client, Client.ENCRYPTED_FIELDS),
    ("address", Address, Address.ENCRYPTED_FIELDS),
    ("contact", Contact, Contact.ENCRYPTED_FIELDS),
]


def _check_value(value):
    if not value:
        return None
    if not isinstance(value, str):
        return "non-string"
    try:
        encryptor.decrypt(value)
        return None  # valid ciphertext
    except Exception:
        if value.startswith(FERRET_MARKER):
            return "UNRECOGNIZED (Fernet-marker, decrypt fails)"
        return None  # plaintext already handled by main tool


with flask_app.app_context():
    try:
        set_tenant_context(db, None, bypass_rls=True)
    except Exception:
        pass

    results = []
    for model_name, model, fields in MODELS:
        for row in model.query.all():
            for field in fields:
                value = getattr(row, field)
                status = _check_value(value)
                if status:
                    preview = (
                        repr(value[:80]) if isinstance(value, str) else repr(value)
                    )
                    results.append(
                        f"  [{row.tenant_id[:8] if row.tenant_id else 'NO-TENANT'}] "
                        f"{model_name} {row.id} .{field}: {status}\n"
                        f"    preview: {preview}"
                    )

    if results:
        print(f"Found {len(results)} unrecognized field(s):\n")
        for r in results:
            print(r)
    else:
        print(
            "No unrecognized fields found — all encrypted fields are either valid ciphertext or empty."
        )

    try:
        set_tenant_context(db, None)
    except Exception:
        pass
