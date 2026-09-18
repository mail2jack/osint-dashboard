import ast
from unittest.mock import patch

import pytest

from cms.services.finding_capture_executor import (
    CaptureExecutionError,
    MAX_CAPTURE_PNG_BYTES,
    capture_page_as_png,
)


def test_capture_rejects_target_before_importing_playwright():
    with patch(
        "cms.services.finding_capture_executor.validate_capture_url",
        return_value=(False, "blocked address"),
    ), patch.dict("sys.modules", {"playwright.sync_api": None}):
        with pytest.raises(CaptureExecutionError, match="target rejected"):
            capture_page_as_png("http://127.0.0.1/")


def test_capture_module_has_no_unsafe_chromium_fallback():
    from pathlib import Path

    source = (
        Path(__file__).resolve().parent.parent
        / "cms/services/finding_capture_executor.py"
    ).read_text()
    tree = ast.parse(source)
    launches = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "launch"
    ]
    assert len(launches) == 1
    assert {keyword.arg for keyword in launches[0].keywords} == {"headless", "timeout"}
    assert "install_capture_request_guard(page)" in source


def test_capture_rejects_oversized_png_without_persisting():
    # The execution boundary returns only in-memory bytes.  A future worker
    # therefore has nothing to persist when this safety check fails.
    with patch(
        "cms.services.finding_capture_executor.validate_capture_url",
        return_value=(True, ""),
    ), patch(
        "cms.services.finding_capture_executor.MAX_CAPTURE_PNG_BYTES", 1
    ):
        class Page:
            url = "https://example.test/"

            def goto(self, *_args, **_kwargs):
                return object()

            def screenshot(self, **_kwargs):
                return b"xx"

            def title(self):
                return "Example"

        class Context:
            def new_page(self):
                return Page()

            def close(self):
                pass

        class Browser:
            def new_context(self, **_kwargs):
                return Context()

            def close(self):
                pass

        class Runtime:
            chromium = type(
                "Chromium", (), {"launch": lambda *_a, **_kw: Browser()}
            )()

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        with patch("playwright.sync_api.sync_playwright", return_value=Runtime()), patch(
            "cms.services.finding_capture_executor.install_capture_request_guard"
        ):
            with pytest.raises(CaptureExecutionError, match="size limit"):
                capture_page_as_png("https://example.test/")

    assert MAX_CAPTURE_PNG_BYTES == 8 * 1024 * 1024
