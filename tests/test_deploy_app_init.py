"""
Regression tests for the boot/deploy app initialization route.

update.sh's migration step imports the app (which already runs
create_cms_module() at module import time via app.py) and then calls
create_cms_module(app) again explicitly. Before the idempotency guard this
second call re-registered the shared Flask-SQLAlchemy / Flask-Migrate
instances and raised::

    RuntimeError: A 'SQLAlchemy' instance has already been registered on
    this Flask app. Import and use that instance instead.

Under `set -e` and with `... | tail -1` masking the real exit status, the
deploy continued anyway — which is why this module also covers the exact
one-liner that update.sh runs.
"""

import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_create_cms_module_is_idempotent(app):
    """Calling create_cms_module twice must be a no-op, not a re-registration."""
    from cms import create_cms_module

    create_cms_module(app)

    assert app.extensions["cms_module_initialized"] is True
    assert "sqlalchemy" in app.extensions
    assert "migrate" in app.extensions


def test_update_sh_migration_one_liner_exits_cleanly(tmp_path):
    """Exact update.sh route: eerst `alembic upgrade head` via de gecontroleerde
    deploy-flow, DÁN pas app-import/init. Boot mag nooit zelf migreren (P0).
    Moet exit 0 geven en de marker printen."""
    db_path = tmp_path / "migration_step.db"
    db_uri = f"sqlite:///{db_path}"
    env = {
        **os.environ,
        "DATABASE_URL": db_uri,
    }
    one_liner = (
        "import os; "
        "from alembic.config import Config; "
        "from alembic import command; "
        f"cfg = Config('{PROJECT_ROOT / 'alembic.ini'}'); "
        "command.upgrade(cfg, 'head'); "
        "from app import app; "
        "from cms.models import db; "
        "from cms import create_cms_module; "
        "create_cms_module(app); "
        "print('MIGRATIONS_OK')"
    )
    result = subprocess.run(
        [sys.executable, "-c", one_liner],
        cwd=str(PROJECT_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=900,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "MIGRATIONS_OK" in result.stdout