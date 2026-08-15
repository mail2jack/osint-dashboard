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
PREV_RESEARCH_FLOW = "f4e5d6c7b8a9"


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

    def test_research_actions_subject_columns_roundtrip(self, tmp_path):
        db_file = tmp_path / "ra.db"
        _run_alembic(db_file, "upgrade", "head")
        conn = sqlite3.connect(db_file)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(research_actions)")}
        conn.close()
        assert {"subject_id", "target_kind", "target_snapshot"} <= cols

        _run_alembic(db_file, "downgrade", "f0a1b2c3d4e5")
        conn = sqlite3.connect(db_file)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(research_actions)")}
        conn.close()
        assert "subject_id" not in cols
        assert "target_kind" not in cols
        assert "target_snapshot" not in cols

        _run_alembic(db_file, "upgrade", "head")

    def test_finding_status_backfill_and_roundtrip(self, tmp_path):
        """ADR-0001 D1.5: findings get status/verified_by/verified_at columns.

        Legacy `verified` findings are backfilled to 'verified' with a
        verified_at timestamp; unverified ones become 'candidate'. Downgrade
        removes the new columns again.
        """
        db_file = tmp_path / "f.db"
        _run_alembic(db_file, "upgrade", PREV_RESEARCH_FLOW)

        conn = sqlite3.connect(db_file)
        conn.execute(
            "INSERT INTO tenants (id, name, slug, is_active, tier, join_code) "
            "VALUES ('t1', 'T', 't', 1, 'enterprise', 'seed')"
        )
        conn.execute(
            "INSERT INTO users (id, tenant_id, username, email, hashed_password, role, full_name, is_active, totp_enabled, failed_login_attempts) "
            "VALUES ('u1', 't1', 'a', 'a@a.a', 'x', 'investigator', 'A', 1, 0, 0)"
        )
        conn.execute(
            "INSERT INTO cases (id, tenant_id, case_number, client_id, title, status, start_date) "
            "VALUES ('c1', 't1', 'C-1', 'client1', 'x', 'open', datetime('now'))"
        )
        conn.executemany(
            "INSERT INTO findings "
            "(id, case_id, tenant_id, created_by, title, content, detail, verified, confidence_level, created_at, updated_at) "
            "VALUES (?, ?, 't1', 'u1', ?, 'c', 'd', ?, 'medium', datetime('now'), datetime('now'))",
            [
                ("f1", "c1", "Verified finding", 1),
                ("f2", "c1", "Draft finding", 0),
            ],
        )
        conn.commit()

        _run_alembic(db_file, "upgrade", "head")
        conn = sqlite3.connect(db_file)
        rows = {
            r[0]: r[1]
            for r in conn.execute("SELECT id, status FROM findings").fetchall()
        }
        verified_at = {
            r[0]: r[1]
            for r in conn.execute("SELECT id, verified_at FROM findings").fetchall()
        }
        conn.close()
        assert rows == {"f1": "verified", "f2": "candidate"}
        assert verified_at["f1"] is not None
        assert verified_at["f2"] is None

        _run_alembic(db_file, "downgrade", PREV_RESEARCH_FLOW)
        conn = sqlite3.connect(db_file)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(findings)")}
        conn.close()
        assert "status" not in cols
        assert "verified_by" not in cols
        assert "verified_at" not in cols

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
