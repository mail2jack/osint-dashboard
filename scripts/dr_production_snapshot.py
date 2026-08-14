#!/usr/bin/env python3
"""Create a read-only production state snapshot for the DR second-operator check."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import psycopg2
from sqlalchemy.engine import make_url


def _connect():
    url_value = os.environ.get("DR_PRODUCTION_DATABASE_URL")
    if url_value:
        return psycopg2.connect(
            make_url(url_value).render_as_string(hide_password=False)
        )
    service = os.environ.get("DR_PRODUCTION_PGSERVICE")
    database = os.environ.get("DR_PRODUCTION_DATABASE", "postgres")
    if service:
        return psycopg2.connect(service=service, dbname=database)
    raise RuntimeError(
        "DR_PRODUCTION_DATABASE_URL or DR_PRODUCTION_PGSERVICE is required"
    )


def _uploads_snapshot(root: Path) -> dict:
    digest = hashlib.sha256()
    count = 0
    total_bytes = 0
    if root.is_dir():
        for path in sorted(p for p in root.rglob("*") if p.is_file()):
            relative = path.relative_to(root).as_posix()
            file_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            digest.update(f"{relative}\0{file_hash}\n".encode())
            count += 1
            total_bytes += path.stat().st_size
    return {
        "file_count": count,
        "total_bytes": total_bytes,
        "digest": digest.hexdigest(),
    }


def _service_status() -> dict[str, str]:
    services = os.environ.get(
        "DR_PRODUCTION_SERVICES", "osint-dashboard license-server"
    ).split()
    result = {}
    for service in services:
        completed = subprocess.run(
            ["systemctl", "is-active", service],
            capture_output=True,
            text=True,
            check=False,
        )
        result[service] = completed.stdout.strip() or "unknown"
    return result


def create_snapshot(output: Path, phase: str) -> dict:
    connection = _connect()
    try:
        with connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT current_database(), current_user, "
                "md5(string_agg(table_name || ':' || column_name || ':' || data_type, ',' "
                "ORDER BY table_name, column_name)) "
                "FROM information_schema.columns WHERE table_schema = 'public'"
            )
            database, _user, schema_digest = cursor.fetchone()
            cursor.execute("SELECT count(*) FROM tenants")
            tenants = cursor.fetchone()[0]
            cursor.execute("SELECT count(*) FROM cases")
            cases = cursor.fetchone()[0]
            cursor.execute("SELECT count(*) FROM users")
            users = cursor.fetchone()[0]
            cursor.execute(
                "SELECT datname FROM pg_database "
                "WHERE datname LIKE 'iveras_dr_%' ORDER BY datname"
            )
            temporary_databases = [row[0] for row in cursor.fetchall()]
    finally:
        connection.close()

    report = {
        "schema_version": 1,
        "phase": phase,
        "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "database": {
            "name": database,
            "schema_digest": schema_digest or "empty",
            "tenants": tenants,
            "cases": cases,
            "users": users,
            "temporary_databases": temporary_databases,
        },
        "uploads": _uploads_snapshot(
            Path(
                os.environ.get(
                    "DR_PRODUCTION_UPLOAD_DIR",
                    str(Path(__file__).resolve().parents[1] / "static/uploads"),
                )
            )
        ),
        "services": _service_status(),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("before", "after"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    create_snapshot(args.output, args.phase)
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
