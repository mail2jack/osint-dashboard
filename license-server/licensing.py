"""Ed25519 license signing for the Iveras license server.

The private key lives at ``keys/private.pem`` (mode 600, owned by the `license`
user). The matching public key is embedded in the dashboard app
(``cms/services/license.py``) so clients can verify licenses offline.
"""

import base64
import json
import os
from datetime import datetime, timezone

from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives import serialization

KEYS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "keys")
PRIVATE_KEY_PATH = os.environ.get(
    "LICENSE_KEY_PATH",
    os.path.join(KEYS_DIR, "private.pem"),
)


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _b64enc(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _b64dec(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def canonical_payload(claims: dict) -> str:
    return json.dumps(claims, separators=(",", ":"), sort_keys=True)


def load_private_key(path: str = PRIVATE_KEY_PATH):
    if not os.path.isfile(path):
        return None
    with open(path, "rb") as f:
        return serialization.load_pem_private_key(f.read(), password=None)


def generate_keypair(path: str = PRIVATE_KEY_PATH) -> str:
    """Generate an Ed25519 keypair, persist the private key, return public b64."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    private_key = ed25519.Ed25519PrivateKey.generate()
    with open(path, "wb") as f:
        f.write(
            private_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
    os.chmod(path, 0o600)
    public_b64 = _b64enc(
        private_key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
    )
    return public_b64


def sign_claims(claims: dict, private_key=None) -> str:
    key = private_key or load_private_key()
    if key is None:
        raise FileNotFoundError(
            f"No license private key at {PRIVATE_KEY_PATH} — run cli.py keys:generate"
        )
    return _b64enc(key.sign(canonical_payload(claims).encode("utf-8")))


def build_license(
    install_id: str,
    plan: str = "trial",
    expires_at: str | None = None,
    license_id: str | None = None,
    private_key=None,
) -> tuple[dict, str]:
    """Build signed license claims + base64url signature."""
    import uuid

    claims = {
        "install_id": install_id,
        "license_id": license_id or str(uuid.uuid4()),
        "plan": plan,
        "issued_at": now_utc(),
        "expires_at": expires_at or "9999-12-31T23:59:59Z",
    }
    signature = sign_claims(claims, private_key)
    return claims, signature
