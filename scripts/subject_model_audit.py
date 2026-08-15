#!/usr/bin/env python3
"""
Privacy-safe, read-only production data-model audit for the subject redesign.

Outputs ONLY structural statistics: schema, row counts, per-column null
percentages, and type distributions. It NEVER reads, prints, or decrypts row
data. It uses introspection (information_schema) so it does not import the
Flask application or its models.

Usage:
    DATABASE_URL=postgresql://... python3 scripts/subject_model_audit.py
    PGSERVICE=osint PGPASSFILE=~/.pgpass python3 scripts/subject_model_audit.py
    python3 scripts/subject_model_audit.py --db sqlite:///./test.db

The connection is forced read-only: a PostgreSQL session is opened with
SET TRANSACTION READ ONLY; SQLite uses PRAGMA query_only = ON.
"""

import argparse
import json
import os
import sys

TABLES = [
    "subjects",
    "addresses",
    "contacts",
    "social_accounts",
    "financial_records",
    "findings",
    "research_actions",
    "case_subjects",
    "subject_relations",
]


def _mask_url(url: str) -> str:
    """Mask the password portion of a connection URL for logging only."""
    try:
        from sqlalchemy.engine.url import make_url

        u = make_url(url)
        return u.render_as_string(hide_password=True)
    except Exception:
        return "<database-url>"


def _connect(db_url: str):
    from sqlalchemy import create_engine

    engine = create_engine(
        db_url,
        pool_pre_ping=False,
        connect_args={"options": "-c default_transaction_read_only=on"}
        if db_url.startswith("postgresql")
        else {},
    )
    return engine


def _introspect(engine, tables):
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    available = set(inspector.get_table_names())
    report = {}

    with engine.connect() as conn:
        if engine.dialect.name == "postgresql":
            conn.execute(text("SET TRANSACTION READ ONLY"))
        else:
            conn.execute(text("PRAGMA query_only = ON"))

        for table in tables:
            if table not in available:
                report[table] = {"error": "table not present"}
                continue
            cols = [
                {"name": c["name"], "type": str(c["type"]), "nullable": c["nullable"]}
                for c in inspector.get_columns(table)
            ]
            count = conn.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar()
            null_stats = {}
            for c in cols:
                null_cnt = conn.execute(
                    text(f'SELECT COUNT(*) FROM "{table}" WHERE "{c["name"]}" IS NULL')
                ).scalar()
                null_stats[c["name"]] = {
                    "nulls": null_cnt,
                    "null_pct": round((null_cnt / count) * 100, 2) if count else None,
                }
            report[table] = {"rows": count, "columns": cols, "nulls": null_stats}

        # Distribution queries (GROUP BY only, never row values in output).
        distributions = {}
        if "subjects" in available:
            for col in ("subject_type", "is_deleted"):
                rows = conn.execute(
                    text(
                        f'SELECT "{col}" AS v, COUNT(*) AS n FROM "subjects" GROUP BY "{col}" ORDER BY n DESC'
                    )
                ).fetchall()
                distributions[f"subjects.{col}"] = [
                    {"value": r[0], "count": r[1]} for r in rows
                ]
        if "research_actions" in available:
            for col in ("action_type", "status"):
                rows = conn.execute(
                    text(
                        f'SELECT "{col}" AS v, COUNT(*) AS n FROM "research_actions" GROUP BY "{col}" ORDER BY n DESC'
                    )
                ).fetchall()
                distributions[f"research_actions.{col}"] = [
                    {"value": r[0], "count": r[1]} for r in rows
                ]
        if "findings" in available:
            rows = conn.execute(
                text(
                    'SELECT "verified" AS v, COUNT(*) AS n FROM "findings" GROUP BY "verified" ORDER BY n DESC'
                )
            ).fetchall()
            distributions["findings.verified"] = [
                {"value": r[0], "count": r[1]} for r in rows
            ]

        # Cross-cutting counts that inform the redesign (aggregates only).
        relationships = {}
        if {"case_subjects"} & available:
            relationships["case_subjects.rows"] = conn.execute(
                text('SELECT COUNT(*) FROM "case_subjects"')
            ).scalar()
        if {"subject_relations"} & available:
            relationships["subject_relations.rows"] = conn.execute(
                text('SELECT COUNT(*) FROM "subject_relations"')
            ).scalar()
        if "subjects" in available and "findings" in available:
            relationships["findings.without_subject_id"] = conn.execute(
                text('SELECT COUNT(*) FROM "findings" WHERE "subject_id" IS NULL')
            ).scalar()
        if "subjects" in available:
            relationships["subjects.soft_deleted"] = conn.execute(
                text('SELECT COUNT(*) FROM "subjects" WHERE "is_deleted" = TRUE')
            ).scalar()

    return {
        "tables": report,
        "distributions": distributions,
        "relationships": relationships,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        default=os.environ.get("DATABASE_URL", "postgresql://"),
        help="SQLAlchemy database URL (default: $DATABASE_URL or libpq service env).",
    )
    parser.add_argument(
        "--tables",
        default=",".join(TABLES),
        help="Comma-separated table list to audit (default: all known subject tables).",
    )
    parser.add_argument("--no-redact", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    db_url = args.db or "postgresql://"
    if not db_url.startswith(("postgresql", "sqlite")):
        sys.stderr.write(f"Unsupported URL scheme for {_mask_url(db_url)}\n")
        return 2

    engine = _connect(db_url)
    try:
        report = _introspect(
            engine, [t.strip() for t in args.tables.split(",") if t.strip()]
        )
    finally:
        engine.dispose()

    sys.stdout.write(json.dumps(report, indent=2, default=str))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
