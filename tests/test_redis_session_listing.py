"""Regression tests for the admin session listing with the Redis backend.

redis-py returns *bytes* keys from ``keys("session:*")`` unless
``decode_responses=True``.  `_all_session_ids` previously called
``k.split(":")`` on those bytes and crashed into its exception handler,
making ``/cms/admin/sessions`` show "No active sessions" even though the
sessions existed in Redis.
"""


class _StubRedis:
    def __init__(self, keys):
        self._keys = keys

    def keys(self, *args, **kwargs):
        return list(self._keys)

    def get(self, sid):
        return None


def _backend(method, app):
    with app.app_context():
        return method()


def test_all_session_ids_decodes_bytes_redis_keys(app, monkeypatch):
    redis_client = _StubRedis(
        [b"session:aaa111", b"session:bbb222"]
    )
    monkeypatch.setattr(
        "cms.routes.system._get_session_backend", lambda: "redis"
    )
    monkeypatch.setattr(
        "cms.routes.system._get_redis_client", lambda: redis_client
    )
    from cms.routes.system import _all_session_ids

    ids = _backend(_all_session_ids, app)
    assert ids == ["aaa111", "bbb222"]
    assert all(isinstance(s, str) for s in ids)


def test_all_session_ids_accepts_str_keys(app, monkeypatch):
    redis_client = _StubRedis(["session:ccc"])
    monkeypatch.setattr(
        "cms.routes.system._get_session_backend", lambda: "redis"
    )
    monkeypatch.setattr(
        "cms.routes.system._get_redis_client", lambda: redis_client
    )
    from cms.routes.system import _all_session_ids

    assert _backend(_all_session_ids, app) == ["ccc"]


def test_all_session_ids_filesystem_fallback(app, monkeypatch, tmp_path):
    session_file = tmp_path / "session_abc"
    session_file.write_text("{}")
    monkeypatch.setattr(
        "cms.routes.system._get_session_backend", lambda: "filesystem"
    )
    monkeypatch.setattr(
        "cms.routes.system._SESSION_DIR", str(tmp_path)
    )
    from cms.routes.system import _all_session_ids

    assert _backend(_all_session_ids, app) == ["abc"]