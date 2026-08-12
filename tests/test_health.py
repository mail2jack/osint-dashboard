"""Health / readiness endpoint tests."""


def test_api_health_liveness(client):
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "ok"


def test_health_quick(client):
    resp = client.get("/health?quick=1")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "ok"
    assert data["database"] == "connected"


def test_health_full_readiness(client, monkeypatch):
    # Keep the test deterministic: stub outbound external-service checks.
    monkeypatch.setattr(
        "cms.health_utils.check_external_services",
        lambda quick=False: {"database": "ok", "spiderfoot": "ok"},
    )
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "ok"
    assert data["database"] == "connected"
    assert data["migrations"] == "ok"


def test_migrations_in_sync(app):
    from cms.health_utils import check_migrations

    with app.app_context():
        assert check_migrations() == "ok"
