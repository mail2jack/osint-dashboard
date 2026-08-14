#!/bin/bash
#
# Iveras OSINT Dashboard — Full Backup Script
# ============================================
# Creates an encrypted, verified archive of the entire installation.
# Supports both Docker and native (systemd) deployments.
#
# Usage:
#   ./scripts/backup.sh                    # Default: backup to ./backups/
#   ./scripts/backup.sh /path/to/dir       # Custom output directory
#   ./scripts/backup.sh --key-file /path   # Custom GPG key (default: ./backups/backup-key.gpg)
#
# Restore:  ./scripts/restore.sh --list
#           ./scripts/restore.sh --backup <archive.tar.gz.gpg>
#

set -euo pipefail

export PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$SCRIPT_DIR"
ENV_FILE="$SCRIPT_DIR/.env"

# Source .env if it exists and DATABASE_URL is not already set
if [ -f "$ENV_FILE" ] && [ -z "${DATABASE_URL:-}" ]; then
    set -a; source "$ENV_FILE"; set +a
fi

BACKUP_DIR="${1:-$SCRIPT_DIR/backups}"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_PATH="$BACKUP_DIR/iveras_backup_$TIMESTAMP"
ARCHIVE_FILE="$BACKUP_DIR/iveras_backup_$TIMESTAMP.tar.gz"
ENCRYPTED_FILE="$ARCHIVE_FILE.gpg"

KEY_FILE="${KEY_FILE:-$BACKUP_DIR/backup-key.gpg}"
BACKUP_PGSERVICE="${BACKUP_PGSERVICE:-}"
BACKUP_PGPASSFILE="${BACKUP_PGPASSFILE:-}"
ERRORS=0
WARNINGS=0
DB_DUMP_OK=false

mkdir -p "$BACKUP_PATH"

echo "╔══════════════════════════════════════════════╗"
echo "║   Iveras Full Backup                        ║"
echo "║   $TIMESTAMP"
echo "╚══════════════════════════════════════════════╝"
echo ""

# ------------------------------------------------------------------
# Helper: generate GPG key once, reuse for all backups
# ------------------------------------------------------------------
_ensure_gpg_key() {
    mkdir -p "$BACKUP_DIR"
    if [ ! -f "$KEY_FILE" ]; then
        echo "🔑 Generating new backup encryption key..."
        openssl rand -base64 32 > "$KEY_FILE"
        chmod 600 "$KEY_FILE"
        echo "    Key: $KEY_FILE"
        echo "    ⚠️  KEEP THIS FILE SAFE — without it backups cannot be restored!"
    fi
}

_encrypt() {
    local src="$1"
    local dst="$2"
    gpg --symmetric --batch --passphrase-file "$KEY_FILE" \
        --cipher-algo AES256 --output "$dst" "$src" 2>/dev/null
    local rc=$?
    rm -f "$src"
    return $rc
}

_log_error() { echo -e "  ❌ $1"; ERRORS=$((ERRORS + 1)); }
_log_warn()  { echo -e "  ⚠️  $1"; WARNINGS=$((WARNINGS + 1)); }
_log_ok()    { echo -e "  ✅ $1"; }

# ------------------------------------------------------------------
# 1. Database dump
# ------------------------------------------------------------------
echo "--- 1. Database ---"

if docker compose ps -q postgres 2>/dev/null | grep -q .; then
    echo "  Dumping PostgreSQL (Docker)..."
    if docker compose exec -T postgres pg_dump -U cms -d cms_db --clean --if-exists \
        > "$BACKUP_PATH/database.sql" 2>/dev/null; then
        _log_ok "database.sql"
        DB_DUMP_OK=true
    else
        _log_error "pg_dump failed"
        rm -f "$BACKUP_PATH/database.sql"
    fi

elif command -v pg_dump &>/dev/null && [ -n "${DATABASE_URL:-}" ] && [ -z "$BACKUP_PGSERVICE" ]; then
    echo "  Dumping PostgreSQL (local)..."
    if pg_dump "$DATABASE_URL" --clean --if-exists > "$BACKUP_PATH/database.sql" 2>/dev/null; then
        _log_ok "database.sql"
        DB_DUMP_OK=true
    else
        _log_error "pg_dump failed"
        rm -f "$BACKUP_PATH/database.sql"
    fi

elif command -v pg_dump &>/dev/null && [ -n "$BACKUP_PGSERVICE" ]; then
    echo "  Dumping PostgreSQL (PGSERVICE: $BACKUP_PGSERVICE)..."
    if PGSERVICE="$BACKUP_PGSERVICE" PGPASSFILE="$BACKUP_PGPASSFILE" \
        pg_dump --clean --if-exists > "$BACKUP_PATH/database.sql" 2>/dev/null; then
        _log_ok "database.sql"
        DB_DUMP_OK=true
    else
        _log_error "pg_dump failed"
        rm -f "$BACKUP_PATH/database.sql"
    fi

elif [ -f "$SCRIPT_DIR/cms.db" ]; then
    echo "  Copying SQLite..."
    cp "$SCRIPT_DIR/cms.db" "$BACKUP_PATH/cms.db" && _log_ok "cms.db" || _log_error "SQLite copy failed"

else
    _log_warn "No database found to back up"
fi

# Compress database dump
if [ -f "$BACKUP_PATH/database.sql" ] && [ "$DB_DUMP_OK" = true ]; then
    gzip "$BACKUP_PATH/database.sql" && _log_ok "  Compressed: database.sql.gz"
fi

# ------------------------------------------------------------------
# 2. Uploaded files (static/uploads, subject faces, screenshots)
# ------------------------------------------------------------------
echo ""
echo "--- 2. Uploaded Files ---"

UPLOAD_DIR="$SCRIPT_DIR/static/uploads"
if [ -d "$UPLOAD_DIR" ] && [ -n "$(ls -A "$UPLOAD_DIR" 2>/dev/null)" ]; then
    tar czf "$BACKUP_PATH/uploads.tar.gz" -C "$SCRIPT_DIR/static" uploads/ 2>/dev/null \
        && _log_ok "uploads.tar.gz ($(du -sh "$BACKUP_PATH/uploads.tar.gz" | cut -f1))" \
        || _log_warn "uploads directory empty or tar failed"
else
    _log_warn "No uploaded files found at $UPLOAD_DIR"
fi

# ------------------------------------------------------------------
# 3. Flask session files
# ------------------------------------------------------------------
echo ""
echo "--- 3. Sessions ---"

SESSION_DIR="$SCRIPT_DIR/flask_session"
if [ -d "$SESSION_DIR" ] && [ -n "$(ls -A "$SESSION_DIR" 2>/dev/null)" ]; then
    tar czf "$BACKUP_PATH/sessions.tar.gz" -C "$SCRIPT_DIR" flask_session/ 2>/dev/null \
        && _log_ok "sessions.tar.gz" || _log_warn "session tar failed"
else
    _log_warn "flask_session directory empty or missing"
fi

# ------------------------------------------------------------------
# 4. Configuration files
# ------------------------------------------------------------------
echo ""
echo "--- 4. Configuration ---"

# .env (contains secrets — encrypted below)
if [ -f "$SCRIPT_DIR/.env" ]; then
    cp "$SCRIPT_DIR/.env" "$BACKUP_PATH/env.txt"
    _log_ok "env.txt (will be encrypted in final archive)"
else
    _log_warn ".env not found"
fi

# Nginx config (if on native install)
if [ -f /etc/nginx/sites-available/default ]; then
    cp /etc/nginx/sites-available/default "$BACKUP_PATH/nginx-default.conf"
    _log_ok "nginx-default.conf"
fi
# Also try nginx/sites-enabled
if [ -f /etc/nginx/sites-enabled/default ]; then
    cp /etc/nginx/sites-enabled/default "$BACKUP_PATH/nginx-enabled.conf"
    _log_ok "nginx-enabled.conf"
fi

# Systemd service files (if on native install)
for svc in osint-dashboard spiderfoot; do
    if [ -f "/etc/systemd/system/$svc.service" ]; then
        cp "/etc/systemd/system/$svc.service" "$BACKUP_PATH/${svc}.service"
        _log_ok "${svc}.service"
    fi
done

# SpiderFoot passwd
SF_PASSWD="/home/osint/.spiderfoot/passwd"
if [ -f "$SF_PASSWD" ]; then
    cp "$SF_PASSWD" "$BACKUP_PATH/spiderfoot-passwd.txt"
    _log_ok "spiderfoot-passwd.txt (will be encrypted)"
fi

# Alembic migrations (for schema version compatibility)
ALEMBIC_DIR="$SCRIPT_DIR/migrations/versions"
if [ -d "$ALEMBIC_DIR" ]; then
    tar czf "$BACKUP_PATH/migrations.tar.gz" -C "$SCRIPT_DIR" migrations/versions/ 2>/dev/null
    _log_ok "migrations.tar.gz"
fi

# ------------------------------------------------------------------
# 5. License Server registry
# ------------------------------------------------------------------
echo ""
echo "--- 5. License Server ---"

LICENSE_DIR="/opt/license-server"
if [ -d "$LICENSE_DIR/data" ]; then
    if command -v sqlite3 &>/dev/null; then
        sqlite3 "$LICENSE_DIR/data/license.db" ".backup '$BACKUP_PATH/license.db'" 2>/dev/null \
            && _log_ok "license.db (consistent copy)" \
            || _log_warn "license.db backup failed — osint needs read access to $LICENSE_DIR/data (try: sudo usermod -aG license osint && sudo chmod g+rX /opt/license-server/data)"
    else
        cp "$LICENSE_DIR/data/license.db" "$BACKUP_PATH/license.db" 2>/dev/null \
            && _log_ok "license.db" \
            || _log_warn "license.db backup failed — osint needs read access to $LICENSE_DIR/data (try: sudo usermod -aG license osint && sudo chmod g+rX /opt/license-server/data)"
    fi

    if [ -f "$LICENSE_DIR/.env" ]; then
        cp "$LICENSE_DIR/.env" "$BACKUP_PATH/license-env.txt" 2>/dev/null \
            && _log_ok "license-env.txt (will be encrypted)" \
            || _log_warn "license-env.txt backup failed (non-critical — ADMIN_PASSWORD is reset-able)"
    fi

    if [ -f "$LICENSE_DIR/keys/private.pem" ]; then
        cp "$LICENSE_DIR/keys/private.pem" "$BACKUP_PATH/license-private.pem" 2>/dev/null \
            && _log_ok "license-private.pem (Ed25519 signing key — required to issue new licenses)" \
            || _log_warn "license-private.pem backup failed (non-critical — existing licenses stay valid)"
    fi
else
    _log_warn "License server not found at $LICENSE_DIR"
fi

if [ -f /etc/systemd/system/license-server.service ]; then
    cp /etc/systemd/system/license-server.service "$BACKUP_PATH/license-server.service"
    _log_ok "license-server.service"
fi
if [ -f /etc/nginx/sites-available/license ]; then
    cp /etc/nginx/sites-available/license "$BACKUP_PATH/nginx-license.conf"
    _log_ok "nginx-license.conf"
fi

# ------------------------------------------------------------------
# 6. Backup metadata
# ------------------------------------------------------------------
echo ""
echo "--- 6. Metadata ---"

cat > "$BACKUP_PATH/BACKUP_INFO.txt" << EOF
Iveras OSINT Dashboard Backup
=============================
Date:       $(date)
Hostname:   $(hostname)
Python:     $(python3 --version 2>/dev/null || echo "N/A")
Node:       $(node --version 2>/dev/null || echo "N/A")
DB Engine:  $( { docker compose ps -q postgres 2>/dev/null && echo "PostgreSQL (Docker)"; } || \
              { command -v pg_dump &>/dev/null && [ -n "${DATABASE_URL:-}" ] && echo "PostgreSQL (local)"; } || \
              { [ -f "$SCRIPT_DIR/cms.db" ] && echo "SQLite"; } || echo "Unknown")

Contents:
EOF

for f in "$BACKUP_PATH"/*; do
    name=$(basename "$f")
    size=$(du -h "$f" 2>/dev/null | cut -f1)
    echo "  - $name ($size)" >> "$BACKUP_PATH/BACKUP_INFO.txt"
done

_log_ok "BACKUP_INFO.txt"

# ------------------------------------------------------------------
# 7. Create archive + encrypt
# ------------------------------------------------------------------
echo ""
echo "--- 7. Archive & Encrypt ---"

_ensure_gpg_key

echo -n "  Creating tar archive... "
(cd "$BACKUP_DIR" && tar czf "$ARCHIVE_FILE" "iveras_backup_$TIMESTAMP" 2>/dev/null && rm -rf "$BACKUP_PATH") \
    && echo "✅ ($(du -h "$ARCHIVE_FILE" | cut -f1))" || { _log_error "tar failed"; exit 1; }

echo -n "  Encrypting (AES-256)... "
_encrypt "$ARCHIVE_FILE" "$ENCRYPTED_FILE" \
    && echo "✅" || { _log_error "encryption failed"; exit 1; }

chmod 600 "$ENCRYPTED_FILE"
echo "  🔒 Encrypted archive: $ENCRYPTED_FILE"

# ------------------------------------------------------------------
# 8. Verify integrity
# ------------------------------------------------------------------
echo ""
echo "--- 8. Verification ---"

echo -n "  Decrypt & check integrity... "
DECRYPTED="/tmp/iveras_backup_verify_$TIMESTAMP.tar.gz"
gpg --decrypt --batch --passphrase-file "$KEY_FILE" \
    --output "$DECRYPTED" "$ENCRYPTED_FILE" 2>/dev/null && echo "✅" || { _log_error "decryption failed"; exit 1; }

echo -n "  Check tar integrity... "
tar tzf "$DECRYPTED" > /dev/null 2>&1 && echo "✅" || { _log_error "tar corrupt"; exit 1; }
rm -f "$DECRYPTED"

# ------------------------------------------------------------------
# 9. Cleanup old backups (keep last 30 days)
# ------------------------------------------------------------------
echo ""
echo "--- 9. Cleanup ---"

OLD_BACKUPS=$(find "$BACKUP_DIR" -name "iveras_backup_*.tar.gz.gpg" -type f -mtime +30 2>/dev/null | sort || true)
if [ -n "$OLD_BACKUPS" ]; then
    echo "  Removing backups older than 30 days:"
    echo "$OLD_BACKUPS" | while read -r old; do
        if rm -f "$old"; then
            echo "    🗑️  $(basename "$old")"
        else
            _log_warn "oud backup niet verwijderd: $(basename "$old")"
        fi
    done
    _log_ok "Old backups cleaned"
else
    _log_ok "No backups older than 30 days"
fi

# ------------------------------------------------------------------
# Summary
# ------------------------------------------------------------------
echo ""
echo "╔══════════════════════════════════════════════╗"
if [ "$ERRORS" -gt 0 ]; then
    echo "║  ❌ BACKUP COMPLETED WITH ERRORS            ║"
else
    echo "║  ✅ BACKUP COMPLETED SUCCESSFULLY           ║"
fi
echo "╚══════════════════════════════════════════════╝"
echo ""
echo "  Archive: $ENCRYPTED_FILE ($(du -h "$ENCRYPTED_FILE" | cut -f1))"
echo "  Key:     $KEY_FILE"
echo "  Errors:  $ERRORS"
echo "  Warnings: $WARNINGS"
echo ""
echo "  ⚠️  Store backup-key.gpg separately from backups!"
echo "     Without it, ALL backups are unrecoverable."
echo ""

exit $ERRORS
