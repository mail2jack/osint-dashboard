#!/usr/bin/env python3
"""PostgreSQL helper for DR verification; credentials never come from argv."""

from __future__ import annotations

import argparse
import configparser
import json
import os
import subprocess

import psycopg2
from psycopg2 import sql
from sqlalchemy.engine import make_url


def _service_values(service: str) -> dict[str, str]:
    path = os.environ.get("PGSERVICEFILE", os.path.expanduser("~/.pg_service.conf"))
    parser = configparser.ConfigParser()
    if not parser.read(path) or not parser.has_section(service):
        raise RuntimeError(f"PGSERVICE section not found: {service}")
    values = dict(parser.items(service))
    return {
        key: values[key] for key in ("host", "port", "user", "sslmode") if key in values
    }


def _connect(database: str):
    admin_url = os.environ.get("DR_VERIFY_DATABASE_URL")
    if admin_url:
        url = make_url(admin_url).set(database=database)
        return psycopg2.connect(url.render_as_string(hide_password=False))
    service = os.environ.get("PGSERVICE")
    if service or os.environ.get("PGHOST"):
        connection_args = {"dbname": database}
        if service:
            connection_args.update(_service_values(service))
        if os.environ.get("PGPASSFILE"):
            connection_args["passfile"] = os.environ["PGPASSFILE"]
        return psycopg2.connect(**connection_args)
    raise RuntimeError(
        "DR_VERIFY_DATABASE_URL or libpq connection settings are required"
    )


def _libpq_environment(database: str) -> dict[str, str]:
    """Build libpq environment without putting credentials in argv."""
    environment = os.environ.copy()
    admin_url = os.environ.get("DR_VERIFY_DATABASE_URL")
    if admin_url:
        url = make_url(admin_url)
        if url.host:
            environment["PGHOST"] = url.host
        if url.port:
            environment["PGPORT"] = str(url.port)
        if url.username:
            environment["PGUSER"] = url.username
        if url.password:
            environment["PGPASSWORD"] = url.password
        if url.query.get("sslmode"):
            environment["PGSSLMODE"] = url.query["sslmode"]
        environment.pop("PGSERVICE", None)
    else:
        service = os.environ.get("PGSERVICE")
        if not service and os.environ.get("PGSERVICEFILE"):
            service = "iveras-dr"
        if service:
            environment.update(_service_values(service))
        environment.pop("PGSERVICE", None)
    environment["PGDATABASE"] = database
    return environment


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
        subprocess.run(
            ["psql", "-v", "ON_ERROR_STOP=1", "-q", "-f", args.sql_file],
            check=True,
            env=_libpq_environment(args.database),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
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
        if args.action == "create":
            target = _connect(database)
            target.autocommit = True
            try:
                with target.cursor() as cursor:
                    cursor.execute("SELECT current_user")
                    role = cursor.fetchone()[0]
                    cursor.execute(
                        sql.SQL("GRANT CREATE, USAGE ON SCHEMA public TO {}").format(
                            sql.Identifier(role)
                        )
                    )
            finally:
                target.close()
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
