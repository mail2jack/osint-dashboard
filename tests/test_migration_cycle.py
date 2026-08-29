"""Migration up/down cycle tests for the ADR-0001 PR3 data model migration.

Runs Alembic in a subprocess against an isolated SQLite file so the session
database (already migrated to head by conftest) is never downgraded.
"""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PREV_REVISION = "e0f1a2b3c4d5"
PREV_RESEARCH_FLOW = "f4e5d6c7b8a9"
PR2_PREV_REVISION = "aa1b2c3d4e5f6"
PR3_PREV_REVISION = "bb1c2d3e4f5a7"
INVOICE_PREV_REVISION = "dd1e2f3a4b5c7"
HEAD_REVISION = "a6b7c8d9e0f1"


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


def _unique_index_columns(conn: sqlite3.Connection, table: str) -> set[tuple[str, ...]]:
    """Column tuples of all UNIQUE indexes on a table (SQLite stores named
    and auto-generated unique constraints as indexes)."""
    found: set[tuple[str, ...]] = set()
    for row in conn.execute(f"PRAGMA index_list('{table}')"):
        if not row[2]:
            continue
        cols = tuple(
            r[2] for r in conn.execute(f'PRAGMA index_info("{row[1]}")')
        )
        found.add(cols)
    return found


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

    def test_investigations_numbering_roundtrip(self, tmp_path):
        """ADR-0002 PR2: investigations + atomic counter tables survive a full
        upgrade/downgrade cycle, and the counters are seeded from existing
        ``YYYY-NNNNN`` case numbers so the first allocation never collides."""
        db_file = tmp_path / "inv.db"
        _run_alembic(db_file, "upgrade", PR2_PREV_REVISION)

        conn = sqlite3.connect(db_file)
        conn.execute(
            "INSERT INTO tenants (id, name, slug, is_active, tier, join_code) "
            "VALUES ('t1', 'T', 't', 1, 'enterprise', 'seed')"
        )
        conn.executemany(
            "INSERT INTO cases "
            "(id, tenant_id, case_number, client_id, title, status, start_date) "
            "VALUES (?, 't1', ?, 'cli1', 'x', 'open', datetime('now'))",
            [
                ("c7", "2026-00007"),
                ("c12", "2026-00012"),
                ("cx", "X-1"),
            ],
        )
        conn.commit()
        conn.close()

        _run_alembic(db_file, "upgrade", "head")
        conn = sqlite3.connect(db_file)
        revision = conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        indexes = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }
        counters = dict(
            conn.execute(
                "SELECT year, next_seq FROM case_number_counters WHERE tenant_id='t1'"
            ).fetchall()
        )
        cases_unique = _unique_index_columns(conn, "cases")
        inv_unique = _unique_index_columns(conn, "investigations")
        conn.close()
        assert revision == HEAD_REVISION
        assert {
            "investigations",
            "case_number_counters",
            "investigation_seq_counters",
        } <= tables
        assert "ix_investigations_case_id" in indexes
        assert ("id", "tenant_id") in cases_unique
        assert ("tenant_id", "case_id", "sequence_no") in inv_unique
        # next_seq stores the highest issued number, so the first allocation
        # after the upgrade returns 13 (2026-00012 was the highest existing).
        assert counters == {2026: 12}

        _run_alembic(db_file, "downgrade", PR2_PREV_REVISION)
        conn = sqlite3.connect(db_file)
        revision = conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        indexes = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }
        cases_unique = _unique_index_columns(conn, "cases")
        numbers = [
            r[0]
            for r in conn.execute("SELECT case_number FROM cases ORDER BY case_number").fetchall()
        ]
        conn.close()
        assert revision == PR2_PREV_REVISION
        assert tables.isdisjoint(
            {"investigations", "case_number_counters", "investigation_seq_counters"}
        )
        assert "ix_investigations_case_id" not in indexes
        assert ("id", "tenant_id") not in cases_unique
        assert numbers == ["2026-00007", "2026-00012", "X-1"]

        _run_alembic(db_file, "upgrade", "head")
        conn = sqlite3.connect(db_file)
        counters = dict(
            conn.execute(
                "SELECT year, next_seq FROM case_number_counters WHERE tenant_id='t1'"
            ).fetchall()
        )
        conn.close()
        assert counters == {2026: 12}

    def test_investigations_identity_immutability_roundtrip(self, tmp_path):
        """ADR-0002 PR3 P1: investigation identity triggers (case_id/tenant_id
        immutable) survive a full upgrade/downgrade cycle, while the earlier
        sequence_no trigger from bb1c2d3e4f5a7 is preserved."""
        db_file = tmp_path / "identity.db"
        _run_alembic(db_file, "upgrade", "head")

        conn = sqlite3.connect(db_file)
        revision = conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        triggers = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='trigger' AND tbl_name='investigations'"
            ).fetchall()
        }
        conn.close()
        assert revision == HEAD_REVISION
        assert "trg_investigations_case_id_immutable" in triggers
        assert "trg_investigations_tenant_id_immutable" in triggers
        assert "trg_investigations_sequence_no_immutable" in triggers

        # Downgrade to the PR2 head: only the new identity triggers go, the
        # sequence_no protection from bb1c2d3e4f5a7 must remain.
        _run_alembic(db_file, "downgrade", PR3_PREV_REVISION)
        conn = sqlite3.connect(db_file)
        revision = conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        triggers = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='trigger' AND tbl_name='investigations'"
            ).fetchall()
        }
        conn.close()
        assert revision == PR3_PREV_REVISION
        assert "trg_investigations_case_id_immutable" not in triggers
        assert "trg_investigations_tenant_id_immutable" not in triggers
        assert "trg_investigations_sequence_no_immutable" in triggers

        # Re-upgrade: identity triggers come back, sequence_no trigger intact.
        _run_alembic(db_file, "upgrade", "head")
        conn = sqlite3.connect(db_file)
        revision = conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        triggers = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='trigger' AND tbl_name='investigations'"
            ).fetchall()
        }
        conn.close()
        assert revision == HEAD_REVISION
        assert "trg_investigations_case_id_immutable" in triggers
        assert "trg_investigations_tenant_id_immutable" in triggers
        assert "trg_investigations_sequence_no_immutable" in triggers

    def test_investigations_identity_triggers_block_orm_updates(self, tmp_path):
        """ADR-0002 PR3 P1: the SQLite identity triggers abort direct ORM
        updates of case_id/tenant_id, while normal field updates still work."""
        db_file = tmp_path / "identity-block.db"
        _run_alembic(db_file, "upgrade", "head")

        conn = sqlite3.connect(db_file)
        conn.execute(
            "INSERT INTO tenants (id, name, slug, is_active, tier, join_code) "
            "VALUES ('t1', 'T', 't', 1, 'enterprise', 'seed')"
        )
        conn.execute(
            "INSERT INTO tenants (id, name, slug, is_active, tier, join_code) "
            "VALUES ('t2', 'T2', 't2', 1, 'enterprise', 'seed2')"
        )
        conn.execute(
            "INSERT INTO users (id, tenant_id, username, email, hashed_password, role, full_name, is_active, totp_enabled, failed_login_attempts) "
            "VALUES ('u1', 't1', 'a', 'a@a.a', 'x', 'investigator', 'A', 1, 0, 0)"
        )
        conn.execute(
            "INSERT INTO cases (id, tenant_id, case_number, client_id, title, status, start_date) "
            "VALUES ('c1', 't1', '2026-00001', 'cli1', 'x', 'open', datetime('now'))"
        )
        conn.execute(
            "INSERT INTO cases (id, tenant_id, case_number, client_id, title, status, start_date) "
            "VALUES ('c2', 't1', '2026-00002', 'cli1', 'x', 'open', datetime('now'))"
        )
        conn.execute(
            "INSERT INTO cases (id, tenant_id, case_number, client_id, title, status, start_date) "
            "VALUES ('c3', 't2', '2026-00003', 'cli1', 'x', 'open', datetime('now'))"
        )
        conn.execute(
            "INSERT INTO investigations (id, tenant_id, case_id, sequence_no, title, status, created_at, updated_at) "
            "VALUES ('i1', 't1', 'c1', 1, 'x', 'open', datetime('now'), datetime('now'))"
        )
        conn.commit()

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE investigations SET case_id='c2' WHERE id='i1'"
            )
        conn.execute("ROLLBACK")

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE investigations SET tenant_id='t2' WHERE id='i1'"
            )
        conn.execute("ROLLBACK")

        # Normal field updates and archive state transitions still work.
        conn.execute(
            "UPDATE investigations SET title='renamed', instructions='instr', "
            "notes='note' WHERE id='i1'"
        )
        conn.execute(
            "UPDATE investigations SET status='archived', archived_at=datetime('now') "
            "WHERE id='i1'"
        )
        row = conn.execute(
            "SELECT title, instructions, notes, status, case_id, tenant_id, sequence_no "
            "FROM investigations WHERE id='i1'"
        ).fetchone()
        conn.close()
        assert row == ("renamed", "instr", "note", "archived", "c1", "t1", 1)

    def test_invoice_numbering_per_tenant_roundtrip(self, tmp_path):
        """P1: the per-tenant invoice counter survives a full upgrade/downgrade
        cycle. Upgrade creates ``invoice_number_counters`` (seeded from
        existing ``FAC-YYYY-NNNNN`` invoices so the first allocation continues
        above the highest issued number) and replaces the global unique index
        with the composite ``(tenant_id, invoice_number)`` constraint."""

        def _invoice_index_cols(conn):
            return _unique_index_columns(conn, "invoices")

        db_file = tmp_path / "invnum.db"
        _run_alembic(db_file, "upgrade", INVOICE_PREV_REVISION)

        conn = sqlite3.connect(db_file)
        conn.execute(
            "INSERT INTO tenants (id, name, slug, is_active, tier, join_code) "
            "VALUES ('t1', 'T', 't', 1, 'enterprise', 'seed')"
        )
        conn.executemany(
            "INSERT INTO invoices "
            "(id, tenant_id, invoice_number, client_id, issue_date, due_date, status) "
            "VALUES (?, 't1', ?, 'cl1', '2026-01-01', '2026-02-01', 'draft')",
            [
                ("i07", "FAC-2026-00007"),
                ("i12", "FAC-2026-00012"),
                ("ix", "FAC-2025-00003"),
                ("in", "NOT-A-NUMBER"),
            ],
        )
        conn.commit()
        conn.close()

        _run_alembic(db_file, "upgrade", "head")
        conn = sqlite3.connect(db_file)
        revision = conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        counters = dict(
            conn.execute(
                "SELECT year, next_seq FROM invoice_number_counters WHERE tenant_id='t1'"
            ).fetchall()
        )
        invoice_unique = _invoice_index_cols(conn)
        conn.close()
        assert revision == HEAD_REVISION
        assert "invoice_number_counters" in tables
        # next_seq stores the highest issued number per (tenant, year): the
        # first allocation after upgrade returns FAC-2026-00013 / FAC-2025-00004.
        assert counters == {2026: 12, 2025: 3}
        assert ("tenant_id", "invoice_number") in invoice_unique
        assert ("invoice_number",) not in invoice_unique

        _run_alembic(db_file, "downgrade", INVOICE_PREV_REVISION)
        conn = sqlite3.connect(db_file)
        revision = conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        invoice_unique = _invoice_index_cols(conn)
        numbers = [
            r[0]
            for r in conn.execute(
                "SELECT invoice_number FROM invoices ORDER BY invoice_number"
            ).fetchall()
        ]
        conn.close()
        assert revision == INVOICE_PREV_REVISION
        assert "invoice_number_counters" not in tables
        assert ("invoice_number",) in invoice_unique
        assert ("tenant_id", "invoice_number") not in invoice_unique
        assert numbers == [
            "FAC-2025-00003",
            "FAC-2026-00007",
            "FAC-2026-00012",
            "NOT-A-NUMBER",
        ]

        _run_alembic(db_file, "upgrade", "head")
        conn = sqlite3.connect(db_file)
        counters = dict(
            conn.execute(
                "SELECT year, next_seq FROM invoice_number_counters WHERE tenant_id='t1'"
            ).fetchall()
        )
        conn.close()
        assert counters == {2026: 12, 2025: 3}
