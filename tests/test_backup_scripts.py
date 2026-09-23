"""Regression tests for PostgreSQL backup safety."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_backup_script_prefers_pghost_service_and_never_compresses_failed_dump():
    script = ROOT / "scripts/backup.sh"
    result = subprocess.run(["bash", "-n", str(script)], check=False)
    assert result.returncode == 0
    source = script.read_text(encoding="utf-8")
    assert "BACKUP_PGSERVICE" in source
    assert "BACKUP_PGPASSFILE" in source
    assert "BACKUP_PGSERVICEFILE" in source
    assert 'PGSERVICEFILE="$BACKUP_PGSERVICEFILE"' in source
    assert "--no-owner --no-acl" in source
    assert "DB_DUMP_OK=true" in source
    assert 'rm -f "$BACKUP_PATH/database.sql"' in source
    assert '[ "$DB_DUMP_OK" = true ]' in source


def test_backup_script_loads_pgservice_config_even_with_caller_database_url():
    """A caller DB override must not suppress .env backup service settings."""
    source = (ROOT / "scripts/backup.sh").read_text(encoding="utf-8")

    assert 'CALLER_DATABASE_URL="${DATABASE_URL:-}"' in source
    assert 'if [ -f "$ENV_FILE" ]; then' in source
    assert 'set -a; source "$ENV_FILE"; set +a' in source
    assert 'export DATABASE_URL="$CALLER_DATABASE_URL"' in source
