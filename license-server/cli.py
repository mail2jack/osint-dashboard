#!/usr/bin/env python3
"""Iveras license server CLI — key management + license issuance.

Usage:
    python3 cli.py keys:generate [--out PATH]
    python3 cli.py license:new --install <id> [--plan full|trial] [--expires YYYY-MM-DD | --days N]
    python3 cli.py license:revoke --install <id>
    python3 cli.py license:list

Run as the `license` user so the key files stay owned by it:
    sudo -u license env HOME=/opt/license-server /opt/license-server/venv/bin/python3 cli.py ...
"""

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app as lsapp
import licensing


def cmd_keys_generate(args) -> int:
    public_b64 = licensing.generate_keypair(args.out)
    print(f"Private key written to: {args.out}")
    print("Public key — embed in the dashboard app (cms/services/license.py default):")
    print(public_b64)
    return 0


def _expires_at(args) -> str:
    if args.expires:
        return args.expires + "T00:00:00Z"
    days = args.days if args.days is not None else 365
    return (datetime.now(timezone.utc) + timedelta(days=days)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def cmd_license_new(args) -> int:
    if args.plan not in ("full", "trial"):
        print("--plan must be 'full' or 'trial'")
        return 2
    with lsapp._connect() as conn:
        install = conn.execute(
            "SELECT install_id FROM installs WHERE install_id = ?", (args.install,)
        ).fetchone()
        if install is None:
            print(f"Install not registered yet: {args.install}")
            return 1
        try:
            lic = lsapp._issue_license(
                conn, args.install, plan=args.plan, expires_at=_expires_at(args)
            )
        except FileNotFoundError as exc:
            print(exc)
            return 1
    print(
        f"License issued: {lic['license_id']}  install={args.install}  "
        f"plan={lic['plan']}  expires={lic['expires_at']}"
    )
    return 0


def cmd_license_revoke(args) -> int:
    with lsapp._connect() as conn:
        changed = lsapp._revoke_license(conn, args.install)
    print("License revoked" if changed else f"No active license for {args.install}")
    return 0


def cmd_install_delete(args) -> int:
    with lsapp._connect() as conn:
        ins, lic = lsapp._delete_install(conn, args.install)
    if ins == 0 and lic == 0:
        print(f"Install not found: {args.install}")
        return 1
    print(f"Deleted install {args.install} ({ins} install row, {lic} license row)")
    return 0


def cmd_license_list(args) -> int:
    with lsapp._connect() as conn:
        rows = conn.execute(
            "SELECT license_id, install_id, plan, status, expires_at, created_at "
            "FROM licenses ORDER BY created_at DESC"
        ).fetchall()
    if not rows:
        print("No licenses issued yet.")
        return 0
    for r in rows:
        print(
            f"{r['license_id']}  {r['install_id']}  {r['plan']:6s}  {r['status']:7s}  "
            f"expires {r['expires_at']}  (created {r['created_at']})"
        )
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Iveras license server CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_keys = sub.add_parser("keys:generate", help="Generate the Ed25519 keypair")
    p_keys.add_argument("--out", default=licensing.PRIVATE_KEY_PATH)
    p_keys.set_defaults(func=cmd_keys_generate)

    p_new = sub.add_parser("license:new", help="Issue/replace a license for an install")
    p_new.add_argument("--install", required=True)
    p_new.add_argument("--plan", default="full", choices=["full", "trial"])
    p_new.add_argument("--expires", default=None, help="YYYY-MM-DD")
    p_new.add_argument("--days", type=int, default=None)
    p_new.set_defaults(func=cmd_license_new)

    p_rev = sub.add_parser("license:revoke", help="Revoke a license")
    p_rev.add_argument("--install", required=True)
    p_rev.set_defaults(func=cmd_license_revoke)

    p_del = sub.add_parser("install:delete", help="Delete an install and its licenses")
    p_del.add_argument("--install", required=True)
    p_del.set_defaults(func=cmd_install_delete)

    p_list = sub.add_parser("license:list", help="List all licenses")
    p_list.set_defaults(func=cmd_license_list)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
