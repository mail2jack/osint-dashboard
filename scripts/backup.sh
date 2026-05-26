#!/bin/bash
#
# Iveras OSINT Dashboard — Backup Script
# ========================================
# Backs up PostgreSQL database + Docker volumes to a timestamped archive.
#
# Usage:
#   ./scripts/backup.sh                    # Default backup to ./backups/
#   ./scripts/backup.sh /path/to/backups   # Custom output directory
#
# Restore:
#   cat backup_20260526.sql | docker exec -i $(docker ps -q -f name=postgres) psql -U cms -d cms_db
#   docker run --rm -v uploads_restored:/target -v $(pwd)/uploads_backup:/source alpine cp -a /source/. /target/
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BACKUP_DIR="${1:-$SCRIPT_DIR/backups}"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_PATH="$BACKUP_DIR/iveras_backup_$TIMESTAMP"

mkdir -p "$BACKUP_PATH"

echo "=== Iveras Backup: $TIMESTAMP ==="
echo "Output: $BACKUP_PATH"

# --- 1. Database dump ---
echo ""
echo "--- Database ---"
if docker compose ps -q postgres 2>/dev/null | grep -q .; then
    echo "Dumping PostgreSQL (Docker)..."
    docker compose exec -T postgres pg_dump -U cms -d cms_db --clean --if-exists > "$BACKUP_PATH/database.sql"
    gzip "$BACKUP_PATH/database.sql"
    echo "  -> database.sql.gz"
elif command -v pg_dump &>/dev/null && [ -n "${DATABASE_URL:-}" ]; then
    echo "Dumping PostgreSQL (local)..."
    pg_dump "${DATABASE_URL#postgresql://}" --clean --if-exists > "$BACKUP_PATH/database.sql" 2>/dev/null || \
    pg_dump "$DATABASE_URL" --clean --if-exists > "$BACKUP_PATH/database.sql"
    gzip "$BACKUP_PATH/database.sql"
    echo "  -> database.sql.gz"
elif [ -f "$SCRIPT_DIR/cms.db" ]; then
    echo "Copying SQLite..."
    cp "$SCRIPT_DIR/cms.db" "$BACKUP_PATH/cms.db"
    echo "  -> cms.db"
else
    echo "  WARNING: No database found to back up."
fi

# --- 2. Docker volumes ---
echo ""
echo "--- Docker Volumes ---"
for vol in uploads flask_sessions; do
    if docker volume ls -q -f name="$vol" 2>/dev/null | grep -q .; then
        echo "Backing up volume: $vol"
        docker run --rm \
            -v "${vol}:/source:ro" \
            -v "${BACKUP_PATH}:/target" \
            alpine tar czf "/target/${vol}.tar.gz" -C /source . 2>/dev/null
        echo "  -> ${vol}.tar.gz"
    fi
done

# --- 3. .env (obfuscated) ---
echo ""
echo "--- Configuration ---"
if [ -f "$SCRIPT_DIR/.env" ]; then
    cp "$SCRIPT_DIR/.env" "$BACKUP_PATH/env_backup.txt"
    echo "  -> env_backup.txt"
fi

# --- 4. Summary ---
echo ""
echo "=== Backup Complete ==="
echo "Size: $(du -sh "$BACKUP_PATH" | cut -f1)"
echo "Path: $BACKUP_PATH"

# Archive everything
(cd "$BACKUP_DIR" && tar czf "iveras_backup_$TIMESTAMP.tar.gz" "iveras_backup_$TIMESTAMP" && rm -rf "iveras_backup_$TIMESTAMP")
echo "Archive: $BACKUP_DIR/iveras_backup_$TIMESTAMP.tar.gz"
echo ""
echo "Restore commands:"
echo "  # Database: gunzip -c $BACKUP_DIR/iveras_backup_$TIMESTAMP.tar.gz/database.sql.gz | docker compose exec -T postgres psql -U cms -d cms_db"
echo "  # Volumes:  tar xzf $BACKUP_DIR/iveras_backup_$TIMESTAMP.tar.gz && for vol in uploads flask_sessions; do docker run --rm -v \${vol}:/target -v $BACKUP_DIR/iveras_backup_$TIMESTAMP:/source alpine tar xzf /source/\${vol}.tar.gz -C /target; done"
