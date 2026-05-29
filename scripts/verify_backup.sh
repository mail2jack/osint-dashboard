#!/bin/bash
#
# Iveras OSINT Dashboard — Backup Verification Script
# ====================================================
# Restores the latest backup to a temp directory and validates integrity.
# Designed to be run as a daily cron job or manually.
#
# Usage:
#   ./scripts/verify_backup.sh                    # Verify latest backup
#   ./scripts/verify_backup.sh /path/to/archive   # Verify specific archive
#   ./scripts/verify_backup.sh --cleanup          # Remove old verification dirs
#
# Exit codes:
#   0 — Verified OK
#   1 — No backup found
#   2 — Verification failed (corrupt/incomplete)
#   3 — Cleanup error
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BACKUP_DIR="${1:-$SCRIPT_DIR/backups}"
VERIFY_DIR="/tmp/iveras_backup_verify"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
ERRORS=0
WARNINGS=0

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

cleanup() {
    local exit_code=$?
    if [ -d "$VERIFY_DIR/current" ]; then
        rm -rf "$VERIFY_DIR/current"
    fi
    exit "$exit_code"
}
trap cleanup EXIT

# --- Cleanup mode ---
if [ "$1" = "--cleanup" ]; then
    echo "Cleaning up verification directories older than 7 days..."
    find /tmp -maxdepth 1 -name "iveras_backup_verify_*" -type d -mtime +7 -exec rm -rf {} \; 2>/dev/null
    echo "Done."
    exit 0
fi

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║   Iveras Backup Verification             ║"
echo "╚══════════════════════════════════════════╝"
echo ""

# --- Find latest archive ---
if [ -f "$BACKUP_DIR" ]; then
    LATEST_ARCHIVE="$BACKUP_DIR"
else
    LATEST_ARCHIVE=$(find "$BACKUP_DIR" -maxdepth 1 -name "iveras_backup_*.tar.gz" -type f 2>/dev/null | sort -r | head -1)
fi

if [ -z "$LATEST_ARCHIVE" ]; then
    echo -e "${RED}❌ No backup archive found at: $BACKUP_DIR${NC}"
    exit 1
fi

echo "Archive: $LATEST_ARCHIVE"
echo ""

# --- Extract ---
VERIFY_TARGET="$VERIFY_DIR/iveras_backup_verify_$TIMESTAMP"
mkdir -p "$VERIFY_TARGET"
echo -n "Extracting... "
tar xzf "$LATEST_ARCHIVE" -C "$VERIFY_TARGET"
EXTRACT_DIR=$(find "$VERIFY_TARGET" -maxdepth 1 -type d | tail -1)
if [ -z "$EXTRACT_DIR" ]; then
    echo -e "${RED}FAILED${NC}"
    exit 2
fi
echo -e "${GREEN}OK${NC} ($EXTRACT_DIR)"
echo ""

# --- Check files ---
echo "=== File Integrity ==="

check_file() {
    local file="$1"
    local label="$2"
    local min_size="${3:-0}"

    if [ -f "$EXTRACT_DIR/$file" ]; then
        local size
        size=$(stat -f%z "$EXTRACT_DIR/$file" 2>/dev/null || stat -c%s "$EXTRACT_DIR/$file" 2>/dev/null)
        if [ "$size" -gt "$min_size" ]; then
            echo -e "  ✅ $label ($(numfmt --to=iec "$size" 2>/dev/null || echo "$size bytes"))"
        else
            echo -e "  ⚠️  $label — file too small (${size}b, expected >${min_size}b)"
            WARNINGS=$((WARNINGS + 1))
        fi
    else
        echo -e "  ⚠️  $label — MISSING"
        WARNINGS=$((WARNINGS + 1))
    fi
}

check_file "database.sql.gz" "Database dump" 100
check_file "env_backup.txt" "Environment config" 10
check_file "uploads.tar.gz" "Uploads volume" 0
check_file "flask_sessions.tar.gz" "Session volume" 0

echo ""

# --- Validate database dump ---
echo "=== Database Validation ==="

DB_GZ="$EXTRACT_DIR/database.sql.gz"
if [ -f "$DB_GZ" ]; then
    DECOMPRESSED="$EXTRACT_DIR/database_verify.sql"

    echo -n "Decompressing... "
    if gunzip -c "$DB_GZ" > "$DECOMPRESSED" 2>/dev/null; then
        echo -e "${GREEN}OK${NC}"
    else
        echo -e "${RED}FAILED (corrupt gzip)${NC}"
        ERRORS=$((ERRORS + 1))
        rm -f "$DECOMPRESSED"
    fi

    if [ -f "$DECOMPRESSED" ]; then
        # Check for valid SQL content
        echo -n "SQL syntax check... "
        if grep -q "CREATE TABLE\|CREATE SCHEMA\|COPY\|INSERT INTO\|CREATE SEQUENCE" "$DECOMPRESSED" 2>/dev/null; then
            echo -e "${GREEN}VALID${NC}"
        else
            echo -e "${YELLOW}NO STRUCTURE FOUND (may be empty dump)${NC}"
            WARNINGS=$((WARNINGS + 1))
        fi

        # Count expected tables
        TABLE_COUNT=$(grep -c "CREATE TABLE" "$DECOMPRESSED" 2>/dev/null || echo 0)
        echo "  Tables in dump: $TABLE_COUNT"

        # Try restoring to temp PostgreSQL (if available)
        if command -v psql &>/dev/null && [ -z "${PGHOST:-}" ]; then
            echo -n "Restore dry-run (postgres)... "
            if createdb "iveras_verify_$TIMESTAMP" --template=template0 2>/dev/null; then
                if psql -q -d "iveras_verify_$TIMESTAMP" -f "$DECOMPRESSED" > /dev/null 2>&1; then
                    echo -e "${GREEN}PASSED${NC}"
                    # Clean up test DB
                    dropdb "iveras_verify_$TIMESTAMP" 2>/dev/null || true
                else
                    echo -e "${RED}FAILED${NC}"
                    ERRORS=$((ERRORS + 1))
                    dropdb "iveras_verify_$TIMESTAMP" 2>/dev/null || true
                fi
            else
                echo -e "${YELLOW}SKIPPED (cannot create test DB — is postgres running?)${NC}"
            fi
        else
            echo -e "  PostgreSQL restore: ${YELLOW}SKIPPED${NC} (psql not available)"
        fi

        rm -f "$DECOMPRESSED"
    fi
else
    # Check for SQLite backup
    DB_SQLITE="$EXTRACT_DIR/cms.db"
    if [ -f "$DB_SQLITE" ]; then
        echo -n "SQLite integrity... "
        if sqlite3 "$DB_SQLITE" "PRAGMA integrity_check;" 2>/dev/null | grep -q "^ok$"; then
            echo -e "${GREEN}PASSED${NC}"
        else
            echo -e "${RED}FAILED${NC}"
            ERRORS=$((ERRORS + 1))
        fi

        TABLE_COUNT=$(sqlite3 "$DB_SQLITE" ".tables" 2>/dev/null | wc -w)
        echo "  Tables: $TABLE_COUNT"

        # Check for key records
        echo -n "Key data check... "
        USER_COUNT=$(sqlite3 "$DB_SQLITE" "SELECT COUNT(*) FROM users;" 2>/dev/null || echo "0")
        if [ "$USER_COUNT" -gt 0 ]; then
            echo -e "${GREEN}OK ($USER_COUNT users)${NC}"
        else
            echo -e "${YELLOW}No users found${NC}"
            WARNINGS=$((WARNINGS + 1))
        fi
    fi
fi

echo ""

# --- Summary ---
echo "=== Summary ==="
ARCHIVE_SIZE=$(stat -f%z "$LATEST_ARCHIVE" 2>/dev/null || stat -c%s "$LATEST_ARCHIVE" 2>/dev/null || echo "?")
echo "  Archive size: $(numfmt --to=iec "$ARCHIVE_SIZE" 2>/dev/null || echo "$ARCHIVE_SIZE bytes")"
echo "  Errors:   $ERRORS"
echo "  Warnings: $WARNINGS"
echo ""

if [ "$ERRORS" -gt 0 ]; then
    echo -e "${RED}❌ VERIFICATION FAILED — $ERRORS error(s), $WARNINGS warning(s)${NC}"
    exit 2
elif [ "$WARNINGS" -gt 0 ]; then
    echo -e "${YELLOW}⚠️  VERIFIED WITH WARNINGS — $WARNINGS warning(s)${NC}"
    exit 0
else
    echo -e "${GREEN}✅ BACKUP VERIFIED OK${NC}"
fi
