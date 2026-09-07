import logging
import os
import subprocess
import sys

from cms.logging_config import enforce_root_level
from cms.logging_utils import safe_url


def _expected():
    return getattr(
        logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO
    )


def test_enforce_root_level_restores_info_after_import():
    enforce_root_level()
    assert logging.getLogger().getEffectiveLevel() == _expected()


def test_request_logging_hook_logger_is_info_enabled_in_clean_import():
    code = (
        "import logging; from app import app;"
        "print(logging.getLogger('cms.routes.system_app').isEnabledFor(logging.INFO),"
        "logging.getLogger('cms.routes.system_app').disabled)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        env={**os.environ, "LICENSE_ENFORCEMENT": "off"},
    )
    assert result.returncode == 0, result.stderr
    out = result.stdout.strip().splitlines()[-1]
    assert out == "True False"


def test_safe_url_strips_credentials():
    assert safe_url("redis://:secret@127.0.0.1:6379/0") == "redis://127.0.0.1:6379/0"
    assert (
        safe_url("postgresql://user:pw@db.example.com:5432/osint_db")
        == "postgresql://db.example.com:5432/osint_db"
    )
    assert (
        safe_url("rediss://user:pw@redis.example.com:6380/2")
        == "rediss://redis.example.com:6380/2"
    )
    assert safe_url("redis://127.0.0.1:6379") == "redis://127.0.0.1:6379"
    assert "pw" not in safe_url("redis://:pw@host:6379/0")


def test_safe_url_handles_empty_and_unparsable():
    assert safe_url("") == ""
    assert safe_url("not a url") == "not a url"
    assert safe_url("redis://]bad[") == "<unparsable-url>"