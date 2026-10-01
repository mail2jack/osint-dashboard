#!/usr/bin/env bash
# Create an encrypted PostgreSQL-only backup through the Docker Compose database.
# This is suitable for staging and as a reviewed production building block;
# production scheduling and offsite key custody require explicit approval.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BACKUP_DIR="${1:-$ROOT_DIR/backups/database}"
KEY_FILE="${BACKUP_KEY_FILE:-$BACKUP_DIR/backup-key.gpg}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
PLAIN="$BACKUP_DIR/joost_database_$STAMP.sql.gz"
ARCHIVE="$PLAIN.gpg"

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"
if [ ! -f "$KEY_FILE" ]; then
    umask 077
    openssl rand -base64 32 > "$KEY_FILE"
fi
chmod 600 "$KEY_FILE"

cd "$ROOT_DIR"
docker compose exec -T postgres pg_dump -U cms -d cms_db --clean --if-exists --no-owner --no-acl \
    | gzip -c > "$PLAIN"
gpg --batch --yes --symmetric --passphrase-file "$KEY_FILE" \
    --cipher-algo AES256 --output "$ARCHIVE" "$PLAIN"
chmod 600 "$ARCHIVE"
rm -f "$PLAIN"

find "$BACKUP_DIR" -maxdepth 1 -type f -name 'joost_database_*.sql.gz.gpg' \
    -mtime "+$RETENTION_DAYS" -delete

test -s "$ARCHIVE"
printf 'Created encrypted database backup: %s\n' "$ARCHIVE"
