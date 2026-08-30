"""Regression tests for the bounded session-store cache backend.

Regression for the P2 session-store wedge: stock ``cachelib.FileSystemCache``
retries ``PermissionError`` on ``open()`` for up to 10s per operation, which —
with gunicorn sync/1 worker — turns a single unreadable session entry
(e.g. a file owned by a different UID) into a full-app stall and failing
``/health`` checks against a 10s timeout.
"""

import os
import time
from pathlib import Path

import pytest

from cms.session_cache import BoundedFileSystemCache

from cachelib.file import FileSystemCache


def _new_test_app(session_dir):
    from flask import Flask, session
    from flask_session import Session

    app = Flask(__name__)
    app.secret_key = "session-store-wedge-test"
    app.config.update(
        SESSION_TYPE="cachelib",
        SESSION_PERMANENT=True,
        SESSION_SERIALIZATION_FORMAT="json",
        SESSION_CACHELIB=BoundedFileSystemCache(
            cache_dir=str(session_dir), threshold=5000, default_timeout=28800
        ),
    )
    Session(app)

    @app.get("/ping")
    def ping():
        session["visits"] = session.get("visits", 0) + 1
        return {"visits": session["visits"]}

    return app


def test_bounded_retry_is_fast_on_permission_error(tmp_path, monkeypatch):
    """An unreadable session entry must degrade to a fast miss, not a 10s stall."""
    cache = BoundedFileSystemCache(
        cache_dir=str(tmp_path), threshold=5000, default_timeout=28800
    )
    cache.set("k", "v")

    real_open = open
    target = cache._get_filename("k")

    def exploding_open(path, mode="rb", *args, **kwargs):
        if Path(path) == Path(target):
            raise PermissionError("simulated EACCES on session file")
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr("builtins.open", exploding_open)

    start = time.monotonic()
    assert cache.get("k") is None
    elapsed = time.monotonic() - start

    assert elapsed < 0.75, f"session read blocked {elapsed:.2f}s (expected <0.75s)"


def test_roundtrip_still_works(tmp_path):
    cache = BoundedFileSystemCache(
        cache_dir=str(tmp_path), threshold=5000, default_timeout=28800
    )
    cache.set("k", {"a": 1})
    assert cache.get("k") == {"a": 1}
    cache.delete("k")
    assert cache.get("k") is None


def test_end_to_end_broken_session_file_keeps_app_healthy(tmp_path):
    """Replaying a session whose store entry became unreadable must not stall."""
    if os.geteuid() == 0:
        pytest.skip("chmod-000 does not restrict root")

    app = _new_test_app(tmp_path)
    client = app.test_client()

    first = client.get("/ping")
    assert first.status_code == 200

    session_file = next(
        p for p in Path(tmp_path).iterdir() if p.name != "__wz_cache_count"
    )
    session_file.chmod(0o000)

    start = time.monotonic()
    second = client.get("/ping")
    elapsed = time.monotonic() - start

    assert second.status_code == 200, second.data
    assert elapsed < 1.5, f"request blocked {elapsed:.2f}s on broken session file"

    session_file.chmod(0o600)


def test_doctor_check_flags_foreign_session_entries(tmp_path, monkeypatch):
    """doctor's content-level scan must detect non-0600 / foreign-owned files."""
    import scripts.doctor as doctor

    sess_dir = tmp_path / "flask_session"
    sess_dir.mkdir()
    (sess_dir / "ok").write_bytes(b"\x00" * 8)
    (sess_dir / "ok").chmod(0o600)
    (sess_dir / "bad_mode").write_bytes(b"\x00" * 8)
    (sess_dir / "bad_mode").chmod(0o644)

    monkeypatch.setattr(doctor, "APP_DIR", tmp_path)
    dummy_pw = type("DummyPw", (), {"pw_uid": os.getuid()})()
    monkeypatch.setattr(doctor.pwd, "getpwnam", lambda _name: dummy_pw)

    assert doctor.check_flask_session_contents(dry=True) is False
    assert doctor.check_flask_session_contents(dry=False) is True
    assert not (sess_dir / "bad_mode").exists()
    assert (sess_dir / "ok").exists()


def test_bounded_cache_is_subclass_of_stock(tmp_path):
    assert issubclass(BoundedFileSystemCache, FileSystemCache)
    assert BoundedFileSystemCache.max_wait_time < 1.0