#!/bin/bash
# =============================================================================
# Iveras OSINT Dashboard — Update Script
# =============================================================================
# Usage: sudo ./update.sh
#
# Steps:
#   1. Backup database and .env
#   2. Pull latest code from git
#   3. Update Python packages
#   4. Apply DB migrations
#   5. Restart services
#   6. Health check
# =============================================================================

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}╔══════════════════════════════════════╗${NC}"
echo -e "${BLUE}║   Iveras OSINT Dashboard Update      ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════╝${NC}"
echo ""

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

# ---------- Step 1: Backup ----------
echo -e "${YELLOW}[1/6] Backing up database and config...${NC}"

BACKUP_DIR="$PROJECT_DIR/backups/$(date +%Y%m%d_%H%M%S)"
mkdir -p "$BACKUP_DIR"

# PostgreSQL backup (pg_dump) or SQLite fallback
if command -v pg_dump &>/dev/null && grep -q "postgresql://" "$PROJECT_DIR/.env" 2>/dev/null; then
    DB_URL=$(grep "^DATABASE_URL=" "$PROJECT_DIR/.env" | cut -d= -f2-)
    if [ -n "$DB_URL" ]; then
        pg_dump "$DB_URL" > "$BACKUP_DIR/db.sql" 2>/dev/null && echo "  ✅ PostgreSQL database backed up"
    fi
elif [ -f "$PROJECT_DIR/cms.db" ]; then
    cp "$PROJECT_DIR/cms.db" "$BACKUP_DIR/cms.db"
    echo "  ✅ Database backed up to $BACKUP_DIR/cms.db"
fi

if [ -f "$PROJECT_DIR/.env" ]; then
    cp "$PROJECT_DIR/.env" "$BACKUP_DIR/.env"
    echo "  ✅ .env backed up"
fi

# ---------- Step 2: Git Pull ----------
echo -e "${YELLOW}[2/6] Pulling latest code...${NC}"
if [ -d "$PROJECT_DIR/.git" ]; then
    CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "master")
    # If the remote branch no longer exists, fall back to master
    if ! git ls-remote --heads origin "$CURRENT_BRANCH" 2>/dev/null | grep -q .; then
        echo -e "  ${YELLOW}Branch '$CURRENT_BRANCH' no longer exists on remote — switching to master${NC}"
        CURRENT_BRANCH="master"
        git checkout master
    fi
    git fetch origin
    git fetch origin
    git checkout "$CURRENT_BRANCH"
    git pull origin "$CURRENT_BRANCH"
    echo -e "  ✅ Git pull complete ($CURRENT_BRANCH)"
else
    echo -e "  ${RED}Not a git repository — skipping git pull${NC}"
fi

# ---------- Step 3: Get current/latest version ----------
CURRENT_VER=$(python3 -c "from version import get_version; print(get_version())" 2>/dev/null || echo "unknown")
if [ -f "$PROJECT_DIR/VERSION" ]; then
    LATEST_VER=$(cat "$PROJECT_DIR/VERSION")
else
    LATEST_VER="unknown"
fi
echo -e "  ${GREEN}Version: $CURRENT_VER → $LATEST_VER${NC}"

# ---------- Step 4: Update Dependencies ----------
echo -e "${YELLOW}[3/6] Updating Python packages...${NC}"
VENV_DIR="$PROJECT_DIR/venv"
VENV_PIP="$VENV_DIR/bin/pip"
if [ ! -f "$VENV_PIP" ]; then
    echo -e "  ${YELLOW}Creating virtual environment...${NC}"
    python3 -m venv "$VENV_DIR"
fi
$VENV_PIP install -r "$PROJECT_DIR/requirements.txt" --upgrade
echo -e "  ✅ Packages updated"

# ---------- Step 5: DB Migrations ----------
echo -e "${YELLOW}[4/6] Running database migrations...${NC}"
PYTHON_BIN="$VENV_DIR/bin/python3"
if [ ! -f "$PYTHON_BIN" ]; then
    PYTHON_BIN="python3"
fi
$PYTHON_BIN -c "from app import app; from cms.models import db; from cms.__init__ import create_cms_module; create_cms_module(app); print('✅ Migrations OK')" 2>&1 | tail -1
echo -e "  ✅ Migrations applied"

# ---------- Step 6: Restart Services ----------
echo -e "${YELLOW}[5/6] Restarting services...${NC}"

if systemctl is-active --quiet osint-dashboard 2>/dev/null; then
    systemctl restart osint-dashboard
    echo -e "  ✅ osint-dashboard restarted"
else
    echo -e "  ${YELLOW}osint-dashboard not running as systemd service${NC}"
fi

if systemctl is-active --quiet spiderfoot 2>/dev/null; then
    systemctl restart spiderfoot
    echo -e "  ✅ spiderfoot restarted"
fi

# ---------- Step 7: Health Check ----------
echo -e "${YELLOW}[6/6] Running health check...${NC}"
sleep 3

HEALTH=$(curl -s http://localhost:5000/health 2>/dev/null || echo '{"status":"error"}')
STATUS=$(echo "$HEALTH" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','error'))" 2>/dev/null || echo "error")

if [ "$STATUS" = "ok" ]; then
    echo -e "  ✅ Health check passed"
    echo ""
    echo -e "${GREEN}╔══════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║   Update complete!                    ║${NC}"
    echo -e "${GREEN}║   Version: $CURRENT_VER → $LATEST_VER        ${NC}"
    echo -e "${GREEN}╚══════════════════════════════════════╝${NC}"
else
    echo -e "  ${RED}❌ Health check failed${NC}"
    echo -e "  ${RED}Response: $HEALTH${NC}"
    echo ""
    echo -e "${RED}Update completed but health check failed. Check server logs.${NC}"
    exit 1
fi
