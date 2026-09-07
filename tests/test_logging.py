import logging
import os
import subprocess
import sys

from cms.logging_config import enforce_root_level


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