#!/usr/bin/env bash
# Run SpiderFoot tests (excluded from the main `pytest` run).
set -euo pipefail

cd "$(dirname "$0")"

exec python3 -m pytest spiderfoot/test/ "$@"
