#!/usr/bin/env bash
set -euo pipefail

# Test runner only: never allow the shared staging/production database.
TEST_DATABASE_URL="${TEST_DATABASE_URL:-sqlite:////tmp/joost-isolated-tests.db}"
case "$TEST_DATABASE_URL" in
  sqlite:////tmp/*) ;;
  *) echo "ERROR: TEST_DATABASE_URL must point to /tmp SQLite" >&2; exit 1 ;;
esac

export DATABASE_URL="$TEST_DATABASE_URL"
rm -f /tmp/joost-isolated-tests.db /tmp/joost-isolated-tests.db-*

exec python3 -m pytest -o addopts="" "$@"
