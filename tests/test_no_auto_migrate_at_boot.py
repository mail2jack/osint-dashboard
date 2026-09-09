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
import subprocess
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from flask import Flask

from cms import ALEMBIC_LOCK_ID, _boot_lock, _run_schema_upgrade_serialized


ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"
# Subprocess probes run `import app` from a temp script (sys.path[0] = script
# dir, NOT the repo), so the repo root must be made importable explicitly.
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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
            f"""import os, sqlite3, sys
sys.path.insert(0, {REPO!r})
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
        assert (
            "RuntimeError: Schema out of sync" in out
        ), f"app-import faalde niet fail-closed met RuntimeError:\n{out}"


class TestAppImportFailClosedStopsStartup:
    """P0 fail-closed in de echte import (`from app import app`): als de
    read-only sync-check out-of-sync meldt of zelf faalt, moet de start worden
    afgebroken met een RuntimeError — en NOOIT `alembic upgrade`/`stamp`
    aanroepen (geen DDL op boot)."""

    @staticmethod
    def _run_probe(tmp_path, name, setup_lines: str) -> str:
        db_path = tmp_path / f"{name}.db"
        db_uri = f"sqlite:///{db_path}"
        probe = tmp_path / f"probe_{name}.py"
        probe.write_text(
            f"""import os, sqlite3, sys
sys.path.insert(0, {REPO!r})
os.environ['DATABASE_URL'] = {db_uri!r}
os.environ['FLASK_SECRET_KEY'] = 'x' * 32
os.environ['CMS_ENCRYPTION_KEY'] = os.urandom(32).hex()
os.environ['CMS_FINGERPRINT_KEY'] = 'x' * 32
os.environ['LICENSE_ENFORCEMENT'] = 'off'

{setup_lines}

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

try:
    conn = sqlite3.connect({str(db_path)!r})
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()]
    conn.close()
    tables_out = 'TABLES=' + repr(tables)
except Exception as exc:
    tables_out = 'TABLES_ERROR=' + repr(exc)
print('CALLS=' + repr(calls))
print(tables_out)
print('ERROR=' + repr(error))
"""
        )

        proc = subprocess.run(
            [sys.executable, str(probe)],
            capture_output=True,
            text=True,
            cwd=os.path.dirname(os.path.dirname(__file__)),
            timeout=120,
        )
        assert proc.returncode == 0, f"probe mislukt:\n{proc.stdout + proc.stderr}"
        return proc.stdout + proc.stderr

    @staticmethod
    def _assert_stopped_fail_closed(out: str) -> None:
        assert "CALLS=[]" in out, f"start heeft upgrade/stamp aangeroepen (P0): {out}"
        assert "ERROR=" in out, f"start liep door ondanks out-of-sync (P0): {out}"
        assert "RuntimeError: Schema out of sync" in out, (
            f"start stopte niet met de verwachte RuntimeError:\n{out}"
        )
        assert "boot voert NOOIT DDL uit (P0)" not in out, (
            f"DDL werd aangeroepen i.p.v. RuntimeError:\n{out}"
        )

    def test_app_import_stops_on_mismatch(self, tmp_path):
        """DB staat op een stamp-verschillende (stale) revisie → start stopt
        met RuntimeError, zonder upgrade/stamp."""
        db_path = tmp_path / "mismatch.db"
        setup = f"""from alembic.config import Config
from alembic import command
command.upgrade(Config('alembic.ini'), 'head')
conn = sqlite3.connect({str(db_path)!r})
conn.execute('UPDATE alembic_version SET version_num = ?', ('stale-bevroren-head',))
conn.commit(); conn.close()
"""
        out = self._run_probe(tmp_path, "mismatch", setup)
        self._assert_stopped_fail_closed(out)

    def test_app_import_stops_on_inspect_error(self, tmp_path):
        """De sync-check zelf faalt (DB niet leesbaar) → behandeld als
        out-of-sync: start stopt met RuntimeError, zonder upgrade/stamp."""
        # A directory in plaats van een sqlite-bestand → inspect() faalt.
        (tmp_path / "blocked").mkdir()
        blocked_path = tmp_path / "blocked" / "nested.db"
        db_uri = f"sqlite:///{blocked_path}"
        probe = tmp_path / "probe_inspect_error.py"
        probe.write_text(
            f"""import os, sqlite3, sys
sys.path.insert(0, {REPO!r})
os.environ['DATABASE_URL'] = {db_uri!r}
os.environ['FLASK_SECRET_KEY'] = 'x' * 32
os.environ['CMS_ENCRYPTION_KEY'] = os.urandom(32).hex()
os.environ['CMS_FINGERPRINT_KEY'] = 'x' * 32
os.environ['LICENSE_ENFORCEMENT'] = 'off'

os.makedirs({str(blocked_path.parent)!r}, exist_ok=True)
with open({str(blocked_path)!r}, 'w') as fh:
    fh.write('dit is geen sqlite database')

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

print('CALLS=' + repr(calls))
print('ERROR=' + repr(error))
"""
        )

        proc = subprocess.run(
            [sys.executable, str(probe)],
            capture_output=True,
            text=True,
            cwd=os.path.dirname(os.path.dirname(__file__)),
            timeout=120,
        )
        out = proc.stdout + proc.stderr
        assert proc.returncode == 0, f"probe mislukt:\n{out}"
        self._assert_stopped_fail_closed(out)


class TestHelpersStillWork:
    """Lock/serialization helpers blijven beschikbaar voor de gecontroleerde flow."""

    def test_lock_id_exported(self):
        assert isinstance(ALEMBIC_LOCK_ID, int)

    def test_boot_lock_and_serializer_noop(self, app):
        with _boot_lock(app):
            pass
        _run_schema_upgrade_serialized(lambda: None, app)