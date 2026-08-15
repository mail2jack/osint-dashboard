"""Cross-process serialization of the boot-time Alembic upgrade.

gunicorn forks N workers that all run the schema upgrade at import time;
without a lock they deadlock on DDL and corrupt alembic_version. These tests
verify that _run_schema_upgrade_serialized blocks until the lock is released,
both for the SQLite file-lock path and the PostgreSQL advisory-lock path.
"""

import os
import threading
from pathlib import Path

import pytest

from cms import ALEMBIC_LOCK_ID, _run_schema_upgrade_serialized
from cms.models import db


class TestFileLockSerialization:
    def test_blocks_until_released(self, app):
        import fcntl

        lock_path = Path(app.instance_path) / "alembic.lock"
        with open(lock_path, "w") as lock_file:
            fcntl.flock(lock_file, fcntl.LOCK_EX)

            events = []
            done = threading.Event()

            def run():
                with app.app_context():
                    _run_schema_upgrade_serialized(lambda: events.append("ran"), app)
                done.set()

            t = threading.Thread(target=run)
            t.start()
            assert not done.wait(timeout=0.5), "should block while file lock is held"

            fcntl.flock(lock_file, fcntl.LOCK_UN)
            assert done.wait(timeout=5), "should complete after lock is released"
            t.join()
            assert events == ["ran"]


@pytest.mark.skipif(
    not os.environ.get("DATABASE_URL", "").startswith("postgresql"),
    reason="PostgreSQL integration tests require DATABASE_URL=postgresql://...",
)
class TestPostgresAdvisoryLock:
    def test_blocks_until_released(self, app):
        from sqlalchemy import text

        conn = db.engine.connect()
        try:
            conn.execute(
                text("SELECT pg_advisory_lock(:lock_id)"),
                {"lock_id": ALEMBIC_LOCK_ID},
            )
            done = threading.Event()

            def run():
                with app.app_context():
                    _run_schema_upgrade_serialized(lambda: None, app)
                done.set()

            t = threading.Thread(target=run)
            t.start()
            assert not done.wait(timeout=0.5), "should block while lock is held"

            conn.execute(
                text("SELECT pg_advisory_unlock(:lock_id)"),
                {"lock_id": ALEMBIC_LOCK_ID},
            )
            assert done.wait(timeout=10), "should complete after lock is released"
            t.join()
        finally:
            conn.close()
