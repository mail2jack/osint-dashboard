"""Migration up/down cycle tests for the ADR-0001 PR3 data model migration.

Runs Alembic in a subprocess against an isolated SQLite file so the session
database (already migrated to head by conftest) is never downgraded.
"""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PREV_REVISION = "e0f1a2b3c4d5"


def _run_alembic(db_file: Path, *args: str) -> None:
    env = dict(os.environ)
    env["DATABASE_URL"] = f"sqlite:///{db_file}"
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"alembic {' '.join(args)} failed:\n{result.stdout}\n{result.stderr}"
    )


def _seed_legacy_relations(db_file: Path) -> None:
    _run_alembic(db_file, "upgrade", PREV_REVISION)
    conn = sqlite3.connect(db_file)
    conn.execute(
        "INSERT INTO tenants (id, name, slug, is_active, tier, join_code) "
        "VALUES ('t1', 'T', 't', 1, 'enterprise', 'seed')"
    )
    for sid in ("a", "b", "c"):
        conn.execute(
            "INSERT INTO subjects (id, tenant_id, subject_type, name, is_deleted) "
            "VALUES (?, 't1', 'person', ?, 0)",
            (sid, sid),
        )
    conn.executemany(
        "INSERT INTO subject_relations "
        "(subject_id, related_subject_id, relationship_type, created_at) "
        "VALUES (?, ?, ?, datetime('now'))",
        [
            ("a", "b", "family"),
            ("b", "a", "family"),
            ("a", "c", "business_partner"),
            ("c", "a", "business_partner"),
        ],
    )
    conn.commit()
    conn.close()


class TestMigrationCycle:
    def test_upgrade_downgrade_upgrade(self, tmp_path):
        db_file = tmp_path / "migrate.db"
        _run_alembic(db_file, "upgrade", "head")

        conn = sqlite3.connect(db_file)
        revision = conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        conn.close()

        assert revision != PREV_REVISION
        assert {"subject_identifiers", "subject_facts"} <= tables

        _run_alembic(db_file, "downgrade", PREV_REVISION)
        conn = sqlite3.connect(db_file)
        revision = conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        rel_cols = {r[1] for r in conn.execute("PRAGMA table_info(subject_relations)")}
        conn.close()
        assert revision == PREV_REVISION
        assert "subject_identifiers" not in tables
        assert "relation_type" not in rel_cols
        assert "relationship_type" in rel_cols

        _run_alembic(db_file, "upgrade", "head")

    def test_legacy_relations_collapse_and_restore(self, tmp_path):
        db_file = tmp_path / "seed.db"
        _seed_legacy_relations(db_file)

        _run_alembic(db_file, "upgrade", "head")
        conn = sqlite3.connect(db_file)
        rows = conn.execute(
            "SELECT subject_id, related_subject_id, relation_type, direction, status "
            "FROM subject_relations ORDER BY subject_id"
        ).fetchall()
        conn.close()
        assert rows == [
            ("a", "b", "family", "mutual", "candidate"),
            ("a", "c", "business", "mutual", "candidate"),
        ]

        _run_alembic(db_file, "downgrade", PREV_REVISION)
        conn = sqlite3.connect(db_file)
        pairs = sorted(
            conn.execute(
                "SELECT subject_id, related_subject_id FROM subject_relations"
            ).fetchall()
        )
        conn.close()
        assert pairs == [
            ("a", "b"),
            ("a", "c"),
            ("b", "a"),
            ("c", "a"),
        ]
