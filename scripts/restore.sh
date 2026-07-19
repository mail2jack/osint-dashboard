#!/usr/bin/env bash
#
# Iveras OSINT Dashboard — Restore Script
# ========================================
# RESTORE FROM AN ENCRYPTED BACKUP (created by scripts/backup.sh).
#
# Usage:
#   ./scripts/restore.sh                          # Restore latest backup (prompt)
#   ./scripts/restore.sh --backup /path/to/archive.tar.gz.gpg
#   ./scripts/restore.sh --list                   # List available backups
#   ./scripts/restore.sh --dry-run --backup ...   # Show what would be done
#
# ⚠️  This script can DESTROY current data. Always confirm first.
#

set -euo pipefail

export PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-$SCRIPT_DIR/backups}"
KEY_FILE="${KEY_FILE:-$BACKUP_DIR/backup-key.gpg}"
WORK_DIR="/tmp/iveras_restore_$$"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

DRY_RUN=false
SELECTED_BACKUP=""

_cleanup() {
    rm -rf "$WORK_DIR"
}
trap _cleanup EXIT

# --- Parse args ---
while [[ $# -gt 0 ]]; do
    case "$1" in
        --list|-l)
            echo "Available backups in $BACKUP_DIR:"
            find "$BACKUP_DIR" -maxdepth 1 -name "iveras_backup_*.tar.gz.gpg" -type f 2>/dev/null \
                | sort -r | while read -r f; do
                    name=$(basename "$f" .tar.gz.gpg)
                    size=$(du -h "$f" | cut -f1)
                    date=${name#iveras_backup_}
                    date_fmt="${date:0:4}-${date:4:2}-${date:6:2} ${date:9:2}:${date:11:2}:${date:13:2}"
                    echo "  $f  ($size)  [$date_fmt]"
                done
            exit 0
            ;;
        --backup|-b)
            SELECTED_BACKUP="$2"
            shift 2
            ;;
        --key|-k)
            KEY_FILE="$2"
            shift 2
            ;;
        --dry-run|-n)
            DRY_RUN=true
            shift
            ;;
        --help|-h)
            echo "Usage: $0 [--backup <file>] [--key <file>] [--dry-run] [--list]"
            echo ""
            echo "  --backup, -b <file>   Specific backup archive to restore"
            echo "  --key, -k <file>      GPG key file (default: $BACKUP_DIR/backup-key.gpg)"
            echo "  --dry-run, -n         Show what would be restored, don't touch anything"
            echo "  --list, -l            List available backups and exit"
            exit 0
            ;;
        *)
            echo "❌ Unknown option: $1"
            echo "Usage: $0 --help"
            exit 1
            ;;
    esac
done

# --- Find backup ---
if [ -z "$SELECTED_BACKUP" ]; then
    SELECTED_BACKUP=$(find "$BACKUP_DIR" -maxdepth 1 -name "iveras_backup_*.tar.gz.gpg" -type f 2>/dev/null | sort -r | head -1)
fi

if [ -z "$SELECTED_BACKUP" ] || [ ! -f "$SELECTED_BACKUP" ]; then
    echo -e "${RED}❌ No backup found.${NC}"
    echo "   Use --list to see available backups."
    exit 1
fi

ARCHIVE="$SELECTED_BACKUP"
ARCHIVE_NAME=$(basename "$ARCHIVE" .tar.gz.gpg)

echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║   Iveras Restore                             ║"
echo "╚══════════════════════════════════════════════╝"
echo ""
echo -e "${CYAN}Backup:${NC}  $ARCHIVE"
echo -e "${CYAN}Key:${NC}     $KEY_FILE"
echo ""

if [ ! -f "$KEY_FILE" ]; then
    echo -e "${RED}❌ No key file found at: $KEY_FILE${NC}"
    echo "   The backup is encrypted. Without the key you cannot restore."
    echo "   Specify a different path with: --key /path/to/backup-key.gpg"
    exit 1
fi

# --- Decrypt ---
echo -n "1. Decrypting... "
mkdir -p "$WORK_DIR"
DECRYPTED="$WORK_DIR/$ARCHIVE_NAME.tar.gz"
if [ "$DRY_RUN" = true ]; then
    echo -e "${YELLOW}DRY-RUN (would decrypt)${NC}"
else
    gpg --decrypt --batch --passphrase-file "$KEY_FILE" \
        --output "$DECRYPTED" "$ARCHIVE" 2>/dev/null \
        && echo -e "${GREEN}OK${NC}" \
        || { echo -e "${RED}FAILED${NC}"; exit 1; }
fi

# --- Extract ---
echo -n "2. Extracting... "
if [ "$DRY_RUN" = true ]; then
    echo -e "${YELLOW}DRY-RUN (would extract to $WORK_DIR)${NC}"
    EXTRACT_DIR="$WORK_DIR/iveras_backup_*"
else
    tar xzf "$DECRYPTED" -C "$WORK_DIR" 2>/dev/null \
        && echo -e "${GREEN}OK${NC}" \
        || { echo -e "${RED}FAILED${NC}"; exit 1; }
    EXTRACT_DIR=$(find "$WORK_DIR" -maxdepth 2 -type d -name "iveras_backup_*" | head -1)
fi
echo ""

# --- Show backup contents ---
echo "=== Backup contents ==="
if [ "$DRY_RUN" = false ] && [ -f "$EXTRACT_DIR/BACKUP_INFO.txt" ]; then
    cat "$EXTRACT_DIR/BACKUP_INFO.txt"
    echo ""
fi

# Check what's available
HAS_DB=false
HAS_DB_GZ=false
HAS_UPLOADS=false
HAS_SESSIONS=false
HAS_ENV=false
HAS_SF=false

if [ "$DRY_RUN" = false ]; then
    [ -f "$EXTRACT_DIR/database.sql.gz" ] && HAS_DB_GZ=true
    [ -f "$EXTRACT_DIR/cms.db" ] && HAS_DB=true
    [ -f "$EXTRACT_DIR/uploads.tar.gz" ] && HAS_UPLOADS=true
    [ -f "$EXTRACT_DIR/sessions.tar.gz" ] && HAS_SESSIONS=true
    [ -f "$EXTRACT_DIR/env.txt" ] && HAS_ENV=true
    [ -f "$EXTRACT_DIR/spiderfoot-passwd.txt" ] && HAS_SF=true
fi

# --- Confirm ---
echo -e "${RED}⚠️  This will OVERWRITE the current database and files!${NC}"
echo -n "Are you sure you want to restore? (yes/NO): "
read -r CONFIRM
if [ "$CONFIRM" != "yes" ]; then
    echo "❌ Cancelled."
    exit 0
fi

if [ "$DRY_RUN" = true ]; then
    echo -e "${YELLOW}DRY-RUN — nothing was changed.${NC}"
    exit 0
fi

echo ""

# ====================================================================
# 3. Database restore
# ====================================================================
echo "=== 3. Database ==="

# Helper: backup current state before overwriting
_backup_current_db() {
    local dest="$BACKUP_DIR/pre_restore_${ARCHIVE_NAME}_db.sql.gz"
    if [ -f "$dest" ]; then
        echo "  ⚠️  Current DB backup already exists: $dest (skipping)"
        return
    fi
    echo -n "  Backing up current database before restore... "
    if docker compose ps -q postgres 2>/dev/null | grep -q .; then
        docker compose exec -T postgres pg_dump -U cms -d cms_db --clean --if-exists 2>/dev/null \
            | gzip > "$dest" && echo -e "${GREEN}OK${NC}" || echo -e "${YELLOW}FAILED${NC}"
    elif command -v pg_dump &>/dev/null && [ -n "${DATABASE_URL:-}" ]; then
        pg_dump "$DATABASE_URL" --clean --if-exists 2>/dev/null | gzip > "$dest" \
            && echo -e "${GREEN}OK${NC}" || echo -e "${YELLOW}FAILED${NC}"
    elif [ -f "$SCRIPT_DIR/cms.db" ]; then
        cp "$SCRIPT_DIR/cms.db" "${dest%.sql.gz}.db" \
            && echo -e "${GREEN}OK${NC}" || echo -e "${YELLOW}FAILED${NC}"
    else
        echo -e "${YELLOW}no current database found${NC}"
    fi
}

if [ "$HAS_DB_GZ" = true ]; then
    _backup_current_db

    echo -n "  Decompressing... "
    gunzip -c "$EXTRACT_DIR/database.sql.gz" > "$WORK_DIR/database.sql" 2>/dev/null \
        && echo -e "${GREEN}OK${NC}" || { echo -e "${RED}FAILED${NC}"; exit 1; }

    # Detect DB type: Docker PostgreSQL, local PostgreSQL, or SQLite
    if docker compose ps -q postgres 2>/dev/null | grep -q .; then
        echo -n "  Restoring to PostgreSQL (Docker)... "
        docker compose exec -T postgres psql -U cms -d cms_db -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;" 2>/dev/null
        docker compose exec -T postgres psql -U cms -d cms_db < "$WORK_DIR/database.sql" 2>/dev/null \
            && echo -e "${GREEN}OK${NC}" || { echo -e "${RED}FAILED${NC}"; exit 1; }
        echo "  ✅ Database (PostgreSQL Docker) restored"

    elif command -v psql &>/dev/null && [ -n "${DATABASE_URL:-}" ]; then
        echo -n "  Restoring to PostgreSQL (local)... "
        psql "$DATABASE_URL" -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;" 2>/dev/null
        psql "$DATABASE_URL" < "$WORK_DIR/database.sql" 2>/dev/null \
            && echo -e "${GREEN}OK${NC}" || { echo -e "${RED}FAILED${NC}"; exit 1; }
        echo "  ✅ Database (PostgreSQL local) restored"

    else
        echo -e "${YELLOW}  ⚠️  No PostgreSQL connection — SQLite backup available?${NC}"
    fi

elif [ "$HAS_DB" = true ]; then
    _backup_current_db
    echo -n "  Restoring to SQLite... "
    cp "$EXTRACT_DIR/cms.db" "$SCRIPT_DIR/cms.db" \
        && echo -e "${GREEN}OK${NC}" || { echo -e "${RED}FAILED${NC}"; exit 1; }
    echo "  ✅ Database (SQLite) restored"

else
    echo -e "${YELLOW}  ⚠️  No database found in backup${NC}"
fi

# ====================================================================
# 4. Uploads restore
# ====================================================================
echo ""
echo "=== 4. Uploads ==="
if [ "$HAS_UPLOADS" = true ]; then
    echo -n "  Backing up current uploads... "
    UPLOAD_BAK="$BACKUP_DIR/pre_restore_${ARCHIVE_NAME}_uploads.tar.gz"
    if [ -d "$SCRIPT_DIR/static/uploads" ] && [ -n "$(ls -A "$SCRIPT_DIR/static/uploads" 2>/dev/null)" ]; then
        tar czf "$UPLOAD_BAK" -C "$SCRIPT_DIR/static" uploads/ 2>/dev/null \
            && echo -e "${GREEN}OK (${UPLOAD_BAK})${NC}" || echo -e "${YELLOW}FAILED${NC}"
    else
        echo -e "${YELLOW}no current uploads${NC}"
    fi

    echo -n "  Restoring uploads... "
    mkdir -p "$SCRIPT_DIR/static/uploads"
    tar xzf "$EXTRACT_DIR/uploads.tar.gz" -C "$SCRIPT_DIR/static" 2>/dev/null \
        && echo -e "${GREEN}OK${NC}" || { echo -e "${RED}FAILED${NC}"; exit 1; }
    echo "  ✅ Uploads restored"
else
    echo -e "${YELLOW}  ⚠️  No uploads in backup${NC}"
fi

# ====================================================================
# 5. Sessions restore
# ====================================================================
echo ""
echo "=== 5. Sessions ==="
if [ "$HAS_SESSIONS" = true ]; then
    echo -n "  Restoring sessions... "
    mkdir -p "$SCRIPT_DIR/flask_session"
    tar xzf "$EXTRACT_DIR/sessions.tar.gz" -C "$SCRIPT_DIR" 2>/dev/null \
        && echo -e "${GREEN}OK${NC}" || echo -e "${YELLOW}FAILED${NC}"
    echo "  ✅ Sessions restored"
else
    echo -e "${YELLOW}  ⚠️  No sessions in backup${NC}"
fi

# ====================================================================
# 6. Config restore (env, nginx, systemd, spiderfoot)
# ====================================================================
echo ""
echo "=== 6. Configuration ==="

restore_with_backup() {
    local src="$1"
    local dst="$2"
    local label="$3"

    if [ ! -f "$src" ]; then
        echo -e "  ${YELLOW}⚠️  $label not in backup${NC}"
        return
    fi

    if [ -f "$dst" ]; then
        local bak="${dst}.pre_restore_${ARCHIVE_NAME}"
        cp "$dst" "$bak" 2>/dev/null && echo -e "  💾 Current $label backed up: $bak"
    fi

    cp "$src" "$dst" 2>/dev/null \
        && echo -e "  ✅ $label restored" \
        || echo -e "  ${RED}❌ $label restore failed${NC}"
}

if [ "$HAS_ENV" = true ]; then
    echo -e "${YELLOW}  ⚠️  .env contains secrets! Verify this is correct.${NC}"
    echo -n "    Restore .env? (yes/NO): "
    read -r ENV_OK
    if [ "$ENV_OK" = "yes" ]; then
        restore_with_backup "$EXTRACT_DIR/env.txt" "$SCRIPT_DIR/.env" ".env"
    else
        echo "    .env skipped"
    fi
fi

restore_with_backup "$EXTRACT_DIR/spiderfoot-passwd.txt" "/home/osint/.spiderfoot/passwd" "SpiderFoot passwd"
restore_with_backup "$EXTRACT_DIR/nginx-default.conf" "/etc/nginx/sites-available/default" "nginx config"

for svc in osint-dashboard spiderfoot; do
    restore_with_backup "$EXTRACT_DIR/${svc}.service" "/etc/systemd/system/${svc}.service" "${svc}.service"
done

if [ -f "$EXTRACT_DIR/migrations.tar.gz" ]; then
    echo -n "  Restoring migrations... "
    mkdir -p "$SCRIPT_DIR/migrations/versions"
    MIG_BAK="$BACKUP_DIR/pre_restore_${ARCHIVE_NAME}_migrations.tar.gz"
    if [ -d "$SCRIPT_DIR/migrations/versions" ] && [ -n "$(ls -A "$SCRIPT_DIR/migrations/versions" 2>/dev/null)" ]; then
        tar czf "$MIG_BAK" -C "$SCRIPT_DIR/migrations" versions/ 2>/dev/null || true
    fi
    tar xzf "$EXTRACT_DIR/migrations.tar.gz" -C "$SCRIPT_DIR/migrations" 2>/dev/null \
        && echo -e "${GREEN}OK${NC}" || echo -e "${RED}FAILED${NC}"
fi

# ====================================================================
# Done
# ====================================================================
echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║   ✅ RESTORE COMPLETE                        ║"
echo "╚══════════════════════════════════════════════╝"
echo ""
echo "What now?"
echo "  1. Start the service:  sudo systemctl restart osint-dashboard"
echo "  2. Check logs:         sudo journalctl -u osint-dashboard -n 50 --no-pager"
echo "  3. Or via Docker:      docker compose restart web"
echo ""
echo "Restored from: $ARCHIVE"
echo "Pre-restore backups: $BACKUP_DIR/pre_restore_${ARCHIVE_NAME}_*"
