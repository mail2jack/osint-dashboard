"""P0 guard: app- en timer-starts voeren NOOIT schema-DDL uit.

Achtergrond: vóór deze fix draaide create_cms_module() bij elke app-import
`alembic upgrade head` / `stamp`. Incident 20260909-Voorloper werd getriggerd
door een timer die het app-module importeerde (osint-health-refresh.service),
wat een ongecontroleerde DB-migratie uitvoerde. Sinds deze fix doet boot alleen
een read-only schema sync-check; DDL loopt uitsluitend via de gecontroleerde
deploy-flow (scripts/update.sh / scripts/migrate.sh). Zie:
incident-20260909-adr0005-pr146/FOLLOWUP-P0-automigrate.md
"""

import os
import sqlite3
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from flask import Flask

from cms import ALEMBIC_LOCK_ID, _boot_lock, _run_schema_upgrade_serialized


ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"


def _migrate(db_uri: str) -> None:
    # migrations/env.py reads DATABASE_URL from the environment (not from the
    # alembic.ini sqlalchemy.url), so point it at the isolated test DB.
    old = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = db_uri
    try:
        cfg = Config(str(ALEMBIC_INI))
        command.upgrade(cfg, "head")
    finally:
        if old is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = old


def _fresh_app(db_uri: str) -> Flask:
    app = Flask(__name__)
    app.config.update(
        TESTING=True,
        SQLALCHEMY_DATABASE_URI=db_uri,
        SECRET_KEY="test-secret",
    )
    return app


class TestReadOnlySyncCheck:
    """De read-only check zelf: meldt maar voert nooit DDL uit."""

    def test_synced_db_reports_ok(self, tmp_path, monkeypatch, caplog):
        from cms import _check_schema_sync
        from cms.models import db

        db_uri = f"sqlite:///{tmp_path / 'synced.db'}"
        _migrate(db_uri)
        app = _fresh_app(db_uri)
        db.init_app(app)

        calls = []
        monkeypatch.setattr(
            command, "upgrade", lambda *a, **k: calls.append(a)
        )
        monkeypatch.setattr(command, "stamp", lambda *a, **k: calls.append(a))

        with app.app_context():
            ok = _check_schema_sync(app)

        assert ok is True
        assert calls == [], "sync-check heeft DDL aangeroepen (P0-schending)"

    def test_stale_db_reports_mismatch_without_ddl(
        self, tmp_path, monkeypatch
    ):
        from cms import _check_schema_sync
        from cms.models import db

        db_uri = f"sqlite:///{tmp_path / 'stale.db'}"
        _migrate(db_uri)
        db_path = Path(db_uri[len("sqlite:///") :])
        conn = sqlite3.connect(db_path)
        conn.execute(
            "UPDATE alembic_version SET version_num = ?", ("stale-bevroren-head",)
        )
        conn.commit()
        conn.close()
        app = _fresh_app(db_uri)
        db.init_app(app)

        calls = []
        monkeypatch.setattr(
            command, "upgrade", lambda *a, **k: calls.append(a)
        )
        monkeypatch.setattr(command, "stamp", lambda *a, **k: calls.append(a))

        with app.app_context():
            ok = _check_schema_sync(app)

        assert ok is False
        assert calls == [], "sync-check heeft DDL aangeroepen (P0-schending)"

    def test_empty_db_reports_mismatch_without_ddl(
        self, tmp_path, monkeypatch
    ):
        from cms import _check_schema_sync
        from cms.models import db

        db_uri = f"sqlite:///{tmp_path / 'empty.db'}"
        app = _fresh_app(db_uri)
        db.init_app(app)

        calls = []
        monkeypatch.setattr(
            command, "upgrade", lambda *a, **k: calls.append(a)
        )
        monkeypatch.setattr(command, "stamp", lambda *a, **k: calls.append(a))

        with app.app_context():
            ok = _check_schema_sync(app)

        assert ok is False
        assert calls == [], "sync-check heeft DDL aangeroepen (P0-schending)"


class TestProductionAppImportNeverMigrates:
    """De echte productie-import (`from app import app`, de timer-vector) mag
    op een lege DB nooit tabellen/migraties aanmaken en moet fail-closed zijn."""

    def test_app_import_on_fresh_db_runs_no_ddl(self, tmp_path):
        db_uri = f"sqlite:///{tmp_path / 'appimport.db'}"

        probe = tmp_path / "probe_import.py"
        probe.write_text(
            f"""import os, sqlite3
os.environ['DATABASE_URL'] = {db_uri!r}
os.environ['FLASK_SECRET_KEY'] = 'x' * 32
os.environ['CMS_ENCRYPTION_KEY'] = os.urandom(32).hex()
os.environ['CMS_FINGERPRINT_KEY'] = 'x' * 32
os.environ['LICENSE_ENFORCEMENT'] = 'off'

import alembic.command as alembic_cmd
calls = []
def _boom(*a, **k):
    calls.append(a)
    raise AssertionError('boot voert NOOIT DDL uit (P0)')
alembic_cmd.upgrade = _boom
alembic_cmd.stamp = _boom

error = None
try:
    import app  # triggers create_cms_module()
except Exception as exc:
    error = f'{{type(exc).__name__}}: {{exc}}'

conn = sqlite3.connect({str(tmp_path / 'appimport.db')!r})
tables = [r[0] for r in conn.execute(
    "SELECT name FROM sqlite_master WHERE type='table'"
).fetchall()]
conn.close()
print('CALLS=' + repr(calls))
print('TABLES=' + repr(tables))
print('ERROR=' + repr(error))
"""
        )
        import subprocess

        proc = subprocess.run(
            [sys.executable, str(probe)],
            capture_output=True,
            text=True,
            cwd=os.path.dirname(os.path.dirname(__file__)),
            timeout=120,
        )
        out = proc.stdout + proc.stderr
        assert proc.returncode == 0, f"probe mislukt:\n{out}"
        assert "CALLS=[]" in out, f"app-import heeft DDL aangeroepen (P0): {out}"
        assert "TABLES=[]" in out, f"app-import heeft schema beschreven: {out}"
        assert "ERROR=" in out, f"app-import faalde niet fail-closed:\n{out}"


class TestHelpersStillWork:
    """Lock/serialization helpers blijven beschikbaar voor de gecontroleerde flow."""

    def test_lock_id_exported(self):
        assert isinstance(ALEMBIC_LOCK_ID, int)

    def test_boot_lock_and_serializer_noop(self, app):
        with _boot_lock(app):
            pass
        _run_schema_upgrade_serialized(lambda: None, app)