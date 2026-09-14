"""FeatureFlag writes are attributed, audited, atomic and tenant-safe."""

import uuid

from cms.models import AuditLog, FeatureFlag, Tenant, User, db
from cms.services.feature_flag_service import (
    set_feature_flag_by_superadmin,
    set_feature_flag_by_system,
)


def _admin() -> User:
    return User.query.filter_by(username="admin").first()


def _tenant(name: str = "Flag Tenant", tier: str = "starter") -> Tenant:
    tenant = Tenant(
        name=name,
        slug=f"flag-{uuid.uuid4().hex[:8]}",
        tier=tier,
        join_code=uuid.uuid4().hex[:12].upper(),
    )
    db.session.add(tenant)
    db.session.commit()
    return tenant


def test_service_create_update_delete_and_noop_are_audited(app):
    actor = _admin()
    tenant = db.session.get(Tenant, actor.tenant_id)

    created = set_feature_flag_by_superadmin(
        tenant=tenant,
        flag_name="investigation_workspace",
        enabled=True,
        actor=actor,
    )
    db.session.commit()
    assert created.operation == "create"
    flag = FeatureFlag.query.one()
    assert flag.created_by_id == actor.id == flag.updated_by_id
    assert AuditLog.query.filter_by(entity_type="feature_flag").count() == 1

    timestamp = flag.updated_at
    noop = set_feature_flag_by_superadmin(
        tenant=tenant,
        flag_name="investigation_workspace",
        enabled=True,
        actor=actor,
    )
    db.session.commit()
    assert noop.operation == "noop"
    assert flag.updated_at == timestamp
    assert AuditLog.query.filter_by(entity_type="feature_flag").count() == 1

    deleted = set_feature_flag_by_superadmin(
        tenant=tenant,
        flag_name="investigation_workspace",
        enabled=False,
        actor=actor,
    )
    db.session.commit()
    assert deleted.operation == "delete"
    assert FeatureFlag.query.count() == 0
    logs = AuditLog.query.filter_by(entity_type="feature_flag").order_by(
        AuditLog.timestamp
    ).all()
    assert [log.action for log in logs] == ["create", "delete"]
    assert logs[-1].old_values["override"] is True
    assert logs[-1].new_values["override"] is None


def test_service_and_audit_are_one_transaction(app, monkeypatch):
    actor = _admin()
    tenant = db.session.get(Tenant, actor.tenant_id)

    def fail_log(**_kwargs):
        raise RuntimeError("audit failed")

    monkeypatch.setattr(AuditLog, "log", fail_log)
    try:
        set_feature_flag_by_superadmin(
            tenant=tenant,
            flag_name="investigation_workspace",
            enabled=True,
            actor=actor,
        )
        db.session.commit()
    except RuntimeError:
        db.session.rollback()
    assert FeatureFlag.query.count() == 0


def test_system_source_is_allowlisted(app):
    tenant = db.session.get(Tenant, _admin().tenant_id)
    try:
        set_feature_flag_by_system(
            tenant=tenant,
            flag_name="investigation_workspace",
            enabled=True,
            source="request_body_value",
        )
    except ValueError as exc:
        assert "system" in str(exc)
    else:
        raise AssertionError("unknown system source accepted")


def test_human_entrypoint_rejects_non_superadmin(app):
    actor = _admin()
    actor.is_super_admin = False
    tenant = db.session.get(Tenant, actor.tenant_id)
    try:
        set_feature_flag_by_superadmin(
            tenant=tenant,
            flag_name="investigation_workspace",
            enabled=True,
            actor=actor,
        )
    except PermissionError:
        pass
    else:
        raise AssertionError("non-superadmin actor accepted")
    assert FeatureFlag.query.count() == 0
    assert AuditLog.query.filter_by(entity_type="feature_flag").count() == 0


def test_switched_superadmin_cannot_toggle_other_tenant(app, auth_client):
    selected = _tenant("Selected")
    other = _tenant("Other")
    with auth_client.session_transaction() as sess:
        sess["switched_tenant_id"] = selected.id

    response = auth_client.post(
        "/cms/admin/feature-flags/toggle",
        data={
            "tenant_id": other.id,
            "flag_name": "investigation_workspace",
            "enabled": "1",
        },
    )
    assert response.status_code == 302
    assert FeatureFlag.query.filter_by(tenant_id=other.id).count() == 0


def test_admin_route_records_actor_and_audit(app, auth_client):
    actor = _admin()
    response = auth_client.post(
        "/cms/admin/feature-flags/toggle",
        data={
            "tenant_id": actor.tenant_id,
            "flag_name": "investigation_workspace",
            "enabled": "1",
        },
    )
    assert response.status_code == 302
    flag = FeatureFlag.query.one()
    assert flag.created_by_id == actor.id == flag.updated_by_id
    log = AuditLog.query.filter_by(entity_type="feature_flag").one()
    assert log.user_id == actor.id
    assert log.tenant_id == actor.tenant_id
