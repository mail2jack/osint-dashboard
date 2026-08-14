#!/usr/bin/env python3
"""PostgreSQL helper for DR verification; credentials never come from argv."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import psycopg2
from sqlalchemy.engine import make_url


def _connect(database: str):
    admin_url = os.environ.get("DR_VERIFY_DATABASE_URL")
    if admin_url:
        url = make_url(admin_url).set(database=database)
        return psycopg2.connect(url.render_as_string(hide_password=False))
    service = os.environ.get("PGSERVICE")
    if service or os.environ.get("PGHOST"):
        connection_args = {"dbname": database}
        if service:
            connection_args["service"] = service
        return psycopg2.connect(**connection_args)
    raise RuntimeError(
        "DR_VERIFY_DATABASE_URL or libpq connection settings are required"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action", choices=("create", "drop", "restore", "query", "encrypted-check")
    )
    parser.add_argument("--database", required=True)
    parser.add_argument("--sql-file")
    parser.add_argument("--sql")
    args = parser.parse_args()

    if args.action == "restore":
        if not args.sql_file:
            raise SystemExit("--sql-file is required for restore")
        connection = _connect(args.database)
        try:
            with connection, connection.cursor() as cursor:
                cursor.execute(Path(args.sql_file).read_text(encoding="utf-8"))
        finally:
            connection.close()
        return 0

    if args.action == "query":
        if not args.sql:
            raise SystemExit("--sql is required for query")
        connection = _connect(args.database)
        try:
            with connection, connection.cursor() as cursor:
                cursor.execute(args.sql)
                for row in cursor.fetchall():
                    print(
                        "\t".join("" if value is None else str(value) for value in row)
                    )
        finally:
            connection.close()
        return 0

    if args.action == "encrypted-check":
        from cryptography.fernet import Fernet

        connection = _connect(args.database)
        try:
            with connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT value FROM settings "
                    "WHERE is_encrypted = true AND value IS NOT NULL LIMIT 1"
                )
                row = cursor.fetchone()
        finally:
            connection.close()
        key = os.environ.get("CMS_ENCRYPTION_KEY")
        if row is None:
            print(
                json.dumps({"status": "pass", "detail": "no encrypted values present"})
            )
        elif not key:
            raise SystemExit(
                "CMS_ENCRYPTION_KEY is required to decrypt restored fields"
            )
        else:
            Fernet(key.encode()).decrypt(row[0].encode())
            print(
                json.dumps(
                    {
                        "status": "pass",
                        "detail": "decrypted one restored encrypted field",
                    }
                )
            )
        return 0

    connection = _connect("postgres")
    connection.autocommit = True
    database = args.database.replace('"', "")
    try:
        with connection.cursor() as cursor:
            if args.action == "create":
                cursor.execute(f'CREATE DATABASE "{database}"')
            else:
                cursor.execute(f'DROP DATABASE IF EXISTS "{database}"')
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
