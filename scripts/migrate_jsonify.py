#!/usr/bin/env python3
"""Migrate single-line ``return jsonify(...)`` calls to response helpers.

Only handles simple single-line patterns where the entire jsonify call
fits on one line.  Multiline calls are left untouched.

Usage:
  python3 scripts/migrate_jsonify.py --dry-run   # preview
  python3 scripts/migrate_jsonify.py              # apply
"""

import re
import sys
import glob
import os

DRY_RUN = "--dry-run" in sys.argv

ROUTES_DIR = os.path.join(os.path.dirname(__file__), "..", "cms", "routes")
HELPER_IMPORT = "from .response import api_success, api_created, api_deleted, api_error"

# Single-line patterns using .search() (not .match()), so indentation is fine.

# return jsonify({"error": "..."}), 40x
_PAT_ERR = re.compile(
    r'return\s+jsonify\(\s*\{\s*["\']error["\']\s*:\s*"([^"]*)"\s*\}\s*\)\s*,\s*(40[0-9])\s*'
)
# return jsonify({"error": var}), 40x
_PAT_ERR_VAR = re.compile(
    r'return\s+jsonify\(\s*\{\s*["\']error["\']\s*:\s*([a-z_][a-z0-9_]*)\s*\}\s*\)\s*,\s*(40[0-9])\s*'
)
# return jsonify({"error": str(e)}), 40x
_PAT_ERR_STR = re.compile(
    r'return\s+jsonify\(\s*\{\s*["\']error["\']\s*:\s*str\(([^)]+)\)\s*\}\s*\)\s*,\s*(40[0-9])\s*'
)
# return jsonify({"error": f"..."}), 40x
_PAT_ERR_F = re.compile(
    r'return\s+jsonify\(\s*\{\s*["\']error["\']\s*:\s*(f"[^"]*")\s*\}\s*\)\s*,\s*(40[0-9])\s*'
)

# return jsonify({"message": "..."})  (no status)
_PAT_MSG = re.compile(
    r'return\s+jsonify\(\s*\{\s*["\']message["\']\s*:\s*"([^"]*)"\s*\}\s*\)\s*$'
)
# return jsonify({"message": f"..."})
_PAT_MSG_F = re.compile(
    r'return\s+jsonify\(\s*\{\s*["\']message["\']\s*:\s*(f"[^"]*")\s*\}\s*\)\s*$'
)
# return jsonify({"message": "..."}), 201
_PAT_MSG_201 = re.compile(
    r'return\s+jsonify\(\s*\{\s*["\']message["\']\s*:\s*"([^"]*)"\s*\}\s*\)\s*,\s*201\s*'
)
# return jsonify({"message": "..."}), 200
_PAT_MSG_200 = re.compile(
    r'return\s+jsonify\(\s*\{\s*["\']message["\']\s*:\s*"([^"]*)"\s*\}\s*\)\s*,\s*200\s*'
)

# return jsonify(...), 200  (generic: jsonify(some_var), 200)
_PAT_GENERIC_200 = re.compile(r"(return\s+jsonify\()(.+)(\)\s*,\s*200\s*)$")
# return jsonify(...), 201  (generic: jsonify(some_var), 201)
_PAT_GENERIC_201 = re.compile(r"(return\s+jsonify\()(.+)(\)\s*,\s*201\s*)$")


def _replacement(line: str) -> str | None:
    """If *line* (single line) matches a pattern, return the replacement with
    leading whitespace preserved."""
    lead = line[: len(line) - len(line.lstrip())]
    stripped = line.strip()

    m = None
    repl = None

    if m := _PAT_ERR.search(stripped):
        repl = f'return api_error("{m.group(1)}", {m.group(2)})'
    elif m := _PAT_ERR_VAR.search(stripped):
        repl = f"return api_error(str({m.group(1)}), {m.group(2)})"
    elif m := _PAT_ERR_STR.search(stripped):
        repl = f"return api_error(str({m.group(1)}), {m.group(2)})"
    elif m := _PAT_ERR_F.search(stripped):
        repl = f"return api_error({m.group(1)}, {m.group(2)})"
    elif m := _PAT_MSG_201.search(stripped):
        repl = f'return api_created({{}}, "{m.group(1)}")'
    elif m := _PAT_MSG_200.search(stripped):
        repl = f'return api_success({{}}, "{m.group(1)}")'
    elif m := _PAT_MSG.search(stripped):
        repl = f'return api_success({{}}, "{m.group(1)}")'
    elif m := _PAT_MSG_F.search(stripped):
        repl = f"return api_success({{}}, {m.group(1)})"

    if repl:
        return lead + repl
    return None


def _add_import_safe(content: str) -> str:
    """Insert the response helper import at the top module level, after the
    *entire* last multi-line import block.  Never inserts inside a multi-line
    import, function body, or try/except."""
    if "from .response import" in content:
        return content

    lines = content.split("\n")
    n = len(lines)

    # Scan top-of-file lines that are blank, comment, or import lines
    insert_idx = 0
    paren_depth = 0
    for i in range(n):
        stripped = lines[i].strip()

        # Always skip blank lines and standalone comments
        if stripped == "" or stripped.startswith("#"):
            if paren_depth == 0:
                insert_idx = i + 1
            continue

        # A line belongs to a multi-line import if we entered it with
        # paren_depth > 0 (the closing paren on the final line reduces
        # depth, but we still treat that closing-paren line as part of
        # the import block).
        came_in_with_depth = paren_depth > 0

        # Track parenthesis depth for multi-line imports
        for ch in stripped:
            if ch == "(":
                paren_depth += 1
            elif ch == ")":
                paren_depth -= 1

        is_import_line = stripped.startswith("from ") or stripped.startswith("import ")

        if is_import_line or came_in_with_depth:
            if paren_depth == 0:
                insert_idx = i + 1
        else:
            # We hit a non-import, non-blank, non-comment line not
            # inside any multi-line import block — stop scanning.
            break

    lines.insert(insert_idx, HELPER_IMPORT)
    return "\n".join(lines)


def _migrate_file(fpath: str) -> bool:
    with open(fpath) as f:
        content = f.read()

    if "from .response import" in content:
        return False

    lines = content.split("\n")
    changed = False

    for i in range(len(lines)):
        repl = _replacement(lines[i])
        if repl:
            lines[i] = repl
            changed = True

    if not changed:
        return False

    new_content = _add_import_safe("\n".join(lines))

    if DRY_RUN:
        print(f"[DRY-RUN] Would update: {fpath}")
        for old_line, new_line in zip(content.split("\n"), new_content.split("\n")):
            if old_line != new_line and "from .response import" not in new_line:
                print(f"  - {old_line.strip()}")
                print(f"  + {new_line.strip()}")
        return False

    with open(fpath, "w") as f:
        f.write(new_content)
    print(f"  {fpath}")
    return True


def main():
    files = sorted(glob.glob(os.path.join(ROUTES_DIR, "*.py")))
    exclude = {"__init__.py", "response.py", "utils.py", "demo.py"}
    files = [f for f in files if os.path.basename(f) not in exclude]

    count = 0
    for fpath in files:
        if _migrate_file(fpath):
            count += 1

    print(f"\n{'[DRY-RUN] ' if DRY_RUN else ''}Updated {count} files.")


if __name__ == "__main__":
    main()
