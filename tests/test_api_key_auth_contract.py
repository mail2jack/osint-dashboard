"""Contract tests for API-key authentication and account state.

These tests deliberately exercise Flask-Login's request-loader path, which is
separate from the ``api_key_required`` decorator.  Keeping both paths covered
prevents authentication rules from drifting apart.
"""

import uuid

from flask import request

from cms.auth import load_user_from_request
from cms.api_key_auth import api_key_required
from cms.models import ApiKey, Tenant, User, db


def _api_key_for_user(user: User) -> str:
    raw_key, key_hash = ApiKey.generate_key()
    db.session.add(
        ApiKey(
            tenant_id=user.tenant_id,
            name="contract-test",
            key_hash=key_hash,
            key_prefix=raw_key[:8],
            user_id=user.id,
            scopes=["read"],
            is_active=True,
        )
    )
    db.session.commit()
    return raw_key


def _user_for_auth_test() -> User:
    admin = User.query.filter_by(username="admin").first()
    suffix = uuid.uuid4().hex[:12]
    user = User(
        username=f"api-contract-{suffix}",
        email=f"api-contract-{suffix}@localhost",
        full_name="API Contract User",
        role="investigator",
        tenant_id=admin.tenant_id,
        is_active=True,
    )
    user.set_password("Test1234!")
    db.session.add(user)
    db.session.commit()
    return user


def test_request_loader_accepts_active_api_key(app):
    user = _user_for_auth_test()
    raw_key = _api_key_for_user(user)

    with app.test_request_context("/api/test", headers={"X-API-Key": raw_key}):
        loaded = load_user_from_request(request)

    assert loaded is not None
    assert loaded.id == user.id


def test_request_loader_rejects_unknown_api_key(app):
    with app.test_request_context(
        "/api/test", headers={"X-API-Key": "osint_not-a-real-key"}
    ):
        loaded = load_user_from_request(request)

    assert loaded is None


def test_request_loader_rejects_api_key_for_inactive_user(app):
    user = _user_for_auth_test()
    user.is_active = False
    db.session.commit()
    raw_key = _api_key_for_user(user)

    with app.test_request_context("/api/test", headers={"X-API-Key": raw_key}):
        loaded = load_user_from_request(request)

    # Security contract: disabled accounts must not authenticate through an
    # Security contract: disabled accounts must not authenticate through an
    # API key.
    assert loaded is None


def test_request_loader_rejects_key_from_another_tenant(app):
    user = _user_for_auth_test()
    other_tenant = Tenant(
        name=f"Other tenant {uuid.uuid4().hex[:8]}",
        slug=f"other-{uuid.uuid4().hex[:12]}",
        join_code=uuid.uuid4().hex[:20],
    )
    db.session.add(other_tenant)
    db.session.flush()
    raw_key, key_hash = ApiKey.generate_key()
    db.session.add(
        ApiKey(
            tenant_id=other_tenant.id,
            name="cross-tenant-contract-test",
            key_hash=key_hash,
            key_prefix=raw_key[:8],
            user_id=user.id,
            scopes=["read"],
            is_active=True,
        )
    )
    db.session.commit()

    with app.test_request_context("/api/test", headers={"X-API-Key": raw_key}):
        loaded = load_user_from_request(request)

    assert loaded is None


def test_api_key_decorator_rejects_inactive_user(app):
    user = _user_for_auth_test()
    user.is_active = False
    db.session.commit()
    raw_key = _api_key_for_user(user)

    @api_key_required
    def protected():
        return "ok"

    with app.test_request_context("/api/test", headers={"X-API-Key": raw_key}):
        response = protected()

    assert response[1] == 401


def test_api_key_decorator_rejects_cross_tenant_key(app):
    user = _user_for_auth_test()
    other_tenant = Tenant(
        name=f"Decorator tenant {uuid.uuid4().hex[:8]}",
        slug=f"decorator-{uuid.uuid4().hex[:12]}",
        join_code=uuid.uuid4().hex[:20],
    )
    db.session.add(other_tenant)
    db.session.flush()
    raw_key, key_hash = ApiKey.generate_key()
    db.session.add(
        ApiKey(
            tenant_id=other_tenant.id,
            name="decorator-cross-tenant-test",
            key_hash=key_hash,
            key_prefix=raw_key[:8],
            user_id=user.id,
            scopes=["read"],
            is_active=True,
        )
    )
    db.session.commit()

    @api_key_required
    def protected():
        return "ok"

    with app.test_request_context("/api/test", headers={"X-API-Key": raw_key}):
        response = protected()

    assert response[1] == 401


def test_api_key_generation_rejects_cross_tenant_user(app, auth_client):
    other_tenant = Tenant(
        name=f"Generation tenant {uuid.uuid4().hex[:8]}",
        slug=f"generation-{uuid.uuid4().hex[:12]}",
        join_code=uuid.uuid4().hex[:20],
    )
    db.session.add(other_tenant)
    db.session.flush()
    other_user = User(
        username=f"generation-{uuid.uuid4().hex[:12]}",
        email=f"generation-{uuid.uuid4().hex[:12]}@localhost",
        full_name="Cross Tenant User",
        role="investigator",
        tenant_id=other_tenant.id,
        is_active=True,
    )
    other_user.set_password("Test1234!")
    db.session.add(other_user)
    db.session.commit()

    response = auth_client.post(
        "/cms/api/api-keys/generate",
        json={"name": "cross-tenant-generation", "user_id": other_user.id},
    )

    assert response.status_code == 400
    assert ApiKey.query.filter_by(user_id=other_user.id).count() == 0
