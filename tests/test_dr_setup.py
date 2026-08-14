"""Tests for the guided DR account setup wizard."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_dr_setup_script_syntax_and_safety_controls():
    script = ROOT / "scripts/dr_setup.sh"
    result = subprocess.run(["bash", "-n", str(script)], check=False)
    assert result.returncode == 0
    source = script.read_text(encoding="utf-8")
    assert "--dry-run" in source
    assert "--confirm SETUP-DR" in source
    assert "CREATE ROLE $DR_ROLE" in source
    assert (
        "CREATE ROLE $DR_ROLE LOGIN NOSUPERUSER CREATEDB NOCREATEROLE BYPASSRLS"
        in source
    )
    assert "CREATEDB" in source
    assert "PGPASSFILE" in source
    assert "DROP DATABASE" not in source


def test_dr_postgres_helper_grants_only_temporary_schema_access():
    source = (ROOT / "scripts/dr_postgres.py").read_text(encoding="utf-8")
    assert "GRANT CREATE, USAGE ON SCHEMA public" in source
