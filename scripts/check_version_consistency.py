#!/usr/bin/env python3
"""Ensure every release metadata source resolves to the same application version."""

from __future__ import annotations

import ast
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def version_from_module() -> str:
    tree = ast.parse((ROOT / "version.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "VERSION":
                    value = ast.literal_eval(node.value)
                    if isinstance(value, tuple) and all(
                        isinstance(part, int) for part in value
                    ):
                        return ".".join(str(part) for part in value)
    raise RuntimeError("VERSION tuple ontbreekt in version.py")


def version_from_file(path: Path) -> str:
    value = path.read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+", value):
        raise RuntimeError(f"Ongeldige versie in {path.relative_to(ROOT)}: {value!r}")
    return value


def version_from_pyproject() -> str:
    content = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"(\d+\.\d+\.\d+)"\s*$', content, re.MULTILINE)
    if not match:
        raise RuntimeError("version ontbreekt in pyproject.toml")
    return match.group(1)


def main() -> int:
    versions = {
        "version.py": version_from_module(),
        "VERSION": version_from_file(ROOT / "VERSION"),
        "pyproject.toml": version_from_pyproject(),
    }
    unique_versions = set(versions.values())
    if len(unique_versions) != 1:
        details = ", ".join(f"{source}={value}" for source, value in versions.items())
        raise SystemExit(f"Versies verschillen: {details}")
    print(f"Version consistency check passed: {next(iter(unique_versions))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
