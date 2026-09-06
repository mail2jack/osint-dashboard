"""Regression tests for the Redis-session migration groundwork (PLAN-REDIS).

Two invariants must hold before REDIS_URL can be flipped for sessions:

1. Background tasks must NOT silently switch to RQ just because REDIS_URL is
   set — RQ is an explicit opt-in via its own RQ_URL variable.
2. OSINT-cache invalidation must stay scoped to the ``osint:*`` keyspace and
   never flush the shared Redis DB, where Flask-Session keeps ``session:*``.
"""

import fnmatch
import hashlib

from cms import background
from cms import redis_cache


class FakeRedis(dict):
    """Minimal in-memory client recording calls; close enough for hashing."""

    def __init__(self, initial=None):
        super().__init__(initial or {})
        self.calls = []

    def ping(self):
        return True

    def delete(self, *keys):
        self.calls.append(("delete", keys))
        removed = 0
        for key in keys:
            if key in self:
                del self[key]
                removed += 1
        return removed

    def scan_iter(self, match=None, count=None):
        self.calls.append(("scan_iter", match))
        for key in sorted(self):
            if match is None or fnmatch.fnmatch(key, match):
                yield key

    def unlink(self, *keys):
        self.calls.append(("unlink", keys))
        removed = 0
        for key in keys:
            if key in self:
                del self[key]
                removed += 1
        return removed

    def flushall(self):
        self.calls.append(("flushall", None))
        self.clear()
        return True


def _osint_key(tool, query):
    return f"osint:{hashlib.sha256(f'{tool}:{query.strip().lower()}'.encode()).hexdigest()}"


# ---------------------------------------------------------------------------
# RQ opt-in decoupled from REDIS_URL
# ---------------------------------------------------------------------------


def test_rq_is_disabled_when_only_redist_url_is_set(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6379/0")
    monkeypatch.delenv("RQ_URL", raising=False)
    assert background._rq_enabled() is False


def test_rq_is_disabled_when_nothing_is_set(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("RQ_URL", raising=False)
    assert background._rq_enabled() is False


def test_rq_is_enabled_only_via_rq_url(monkeypatch):
    monkeypatch.setenv("RQ_URL", "redis://127.0.0.1:6379/1")
    assert background._rq_enabled() is True


# ---------------------------------------------------------------------------
# OSINT cache invalidation must never flush the shared Redis DB
# ---------------------------------------------------------------------------


def test_invalidate_single_key_does_not_scan_or_flush(monkeypatch):
    client = FakeRedis(
        {
            _osint_key("sfscan", "example.com"): "v",
            "session:alive": {"user_id": 1},
        }
    )
    monkeypatch.setattr(redis_cache, "_redis_client", client)

    redis_cache.invalidate("sfscan", "example.com")

    assert ("delete", (_osint_key("sfscan", "example.com"),)) in client.calls
    assert not any(c[0] == "scan_iter" for c in client.calls), "delete path should not scan"
    assert not any(c[0] == "flushall" for c in client.calls)
    assert "session:alive" in client, "session keys must survive single-key invalidation"


def test_invalidate_full_purge_scopes_to_osint_keyspace(monkeypatch):
    osint_keys = {_osint_key("tool", f"q{i}") for i in range(3)}
    client = FakeRedis(
        {k: "v" for k in osint_keys}
        | {"session:alive": {"user_id": 1}, "rq:queue": "x"}
    )
    monkeypatch.setattr(redis_cache, "_redis_client", client)

    redis_cache.invalidate()

    assert not any(c[0] == "flushall" for c in client.calls), "flushall is banned"
    assert not osint_keys & client.keys(), "osint:* keys must be purged"
    assert "session:alive" in client, "session keys must survive a full OSINT purge"
    assert "rq:queue" in client, "non-osint keys must survive a full OSINT purge"


def test_invalidate_full_purge_batches_unlink(monkeypatch):
    osint_keys = {_osint_key("tool", f"q{i}") for i in range(250)}
    client = FakeRedis({k: "v" for k in osint_keys})
    monkeypatch.setattr(redis_cache, "_redis_client", client)

    redis_cache.invalidate()

    unlinks = [c for c in client.calls if c[0] == "unlink"]
    assert unlinks, "unlink must be used for the full purge"
    assert all(len(keys) <= 100 for _, keys in unlinks), "batches must stay <= 100"
    assert not client, "all osint:* keys must be gone"