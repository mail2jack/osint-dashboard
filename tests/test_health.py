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


def test_health_summary_cache_miss_does_not_run_full_health(auth_client, monkeypatch):
    from cms.models import Setting

    Setting.set("health_snapshot", "", category="system")

    def fail_if_called(*args, **kwargs):
        raise AssertionError("full health must not run in the request worker")

    monkeypatch.setattr(
        "cms.health_utils.check_external_services", fail_if_called
    )
    response = auth_client.get("/cms/api/health-summary")

    assert response.status_code == 200
    data = response.get_json()
    assert data["stale"] is True
    assert data["checked_at"] is None
    assert data["timings_ms"] == {}


def test_health_summary_returns_snapshot_metadata(auth_client):
    import json
    from cms.models import Setting

    Setting.set("health_snapshot", json.dumps({
        "services": {"database": "ok", "spiderfoot": "ok"},
        "timings_ms": {"database": 1.2, "spiderfoot": 4.5},
        "checked_at": "2026-08-31T12:00:00+00:00",
        "duration_ms": 8.0,
    }), category="system")

    response = auth_client.get("/cms/api/health-summary")

    assert response.status_code == 200
    data = response.get_json()
    assert data["services"]["database"] == "ok"
    assert data["timings_ms"] == {"database": 1.2, "spiderfoot": 4.5}
    assert data["duration_ms"] == 8.0
    assert data["stale"] is True
    assert data["age_seconds"] is not None


def test_quick_health_does_not_call_external_http_checks(app, monkeypatch):
    from cms import health_utils

    calls = []
    monkeypatch.setattr(
        health_utils,
        "jittered_get",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    monkeypatch.setattr(
        "cms.spiderfoot_service.check_spiderfoot_health",
        lambda: (True, "connected"),
    )

    with app.app_context():
        timings = {}
        result = health_utils.check_external_services(quick=True, timings=timings)

    assert result["database"] == "ok"
    assert result["spiderfoot"] == "ok"
    assert calls == []
    assert set(timings) == {"database", "spiderfoot"}


def test_health_refresh_is_a_single_bounded_systemd_producer():
    from pathlib import Path

    script = Path("scripts/health_refresh.py").read_text(encoding="utf-8")
    unit = Path("deploy/osint-health-refresh.service").read_text(encoding="utf-8")
    timer = Path("deploy/osint-health-refresh.timer").read_text(encoding="utf-8")

    assert "LOCK_NB" in script
    assert "TimeoutStartSec=90" in unit
    assert "Type=oneshot" in unit
    assert "OnUnitActiveSec=300s" in timer
    assert "threading.Thread" not in script


def test_migrations_in_sync(app):
    from cms.health_utils import check_migrations

    with app.app_context():
        assert check_migrations() == "ok"
