#!/usr/bin/env python3
"""Standalone entry point for the Telegram bot polling loop.

Runs as a separate systemd service (osint-bot.service) to avoid
Gunicorn multi-worker conflicts.  Blocking — intended for daemon use.

Usage:
    ./scripts/run_bot.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app
from cms.telegram_bot import run_bot_polling


def main():
    with app.app_context():
        from cms.models import Setting
        from cms.telegram_bot import _check_enabled, _ensure_api_key
        import cms.telegram_bot as tb_mod

        if not _check_enabled():
            print("[run_bot] telegram_enabled != true, exiting")
            return

        token = Setting.get("telegram_bot_token", "")
        if not token:
            print("[run_bot] no telegram_bot_token set, exiting")
            return

        tb_mod._cached_allowed_users = Setting.get("telegram_allowed_users", "") or ""
        _ensure_api_key(app)

    run_bot_polling(token)


if __name__ == "__main__":
    main()
