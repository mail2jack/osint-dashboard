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
#
# The helper functions below are source-friendly: tests `source` this file
# (skipping the main flow via the BASH_SOURCE guard) to exercise the helper
# functions without touching a real server.
# =============================================================================

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# ---------- Git pull + self-restart ----------
# Pulls the latest code. If the pull updated `update.sh` itself, the script
# restarts itself with the new version: bash may have buffered the tail of the
# currently running script, which would silently execute stale code (for
# example skipping a freshly added record_deployed_sha call). Git-only logic,
# so it is fully testable via `source`.
# UPDATE_SH_SELF_EXECUTED=1 marks the re-run (skips backup + pull, breaks the
# restart loop).
# $1 (optional): explicit path of the script to re-exec after a pull that
# changed it. Production passes no argument and re-execs $0; tests inject the
# pulled clone/update.sh to prove the new script version actually runs.
pull_latest_and_maybe_self_restart() {
    local script_path="${1:-$0}"
    local project_dir="${PROJECT_DIR:-$(cd "$(dirname "$0")" && pwd)}"
    if [ ! -d "$project_dir/.git" ]; then
        echo -e "  ${RED}Not a git repository — skipping git pull${NC}"
        return 0
    fi
    local branch="${CURRENT_BRANCH:-$(git -C "$project_dir" rev-parse --abbrev-ref HEAD 2>/dev/null || echo master)}"
    # If the remote branch no longer exists, fall back to master
    if ! git ls-remote --heads origin "$branch" 2>/dev/null | grep -q .; then
        echo -e "  ${YELLOW}Branch '$branch' no longer exists on remote — switching to master${NC}"
        branch="master"
        git -C "$project_dir" checkout master
    fi
    local pre_sha post_sha
    pre_sha="$(git -C "$project_dir" rev-parse HEAD 2>/dev/null || echo none)"
    git -C "$project_dir" fetch origin
    git -C "$project_dir" fetch origin
    git -C "$project_dir" checkout "$branch"
    git -C "$project_dir" pull origin "$branch"
    post_sha="$(git -C "$project_dir" rev-parse HEAD 2>/dev/null || echo none)"
    echo -e "  ✅ Git pull complete ($branch)"
    if [ "$pre_sha" != "$post_sha" ] && [ "${UPDATE_SH_SELF_EXECUTED-}" != "1" ]; then
        echo -e "  ${BLUE}Deploy script updated — restarting with the new version${NC}"
        exec env UPDATE_SH_SELF_EXECUTED=1 bash "$script_path"
    fi
}

# ---------- DB migration step ----------
# Fail-closed: importing the app runs the boot-time Alembic upgrade inside
# create_cms_module(). A real failure MUST abort the deploy, so this runs
# without a pipe (a pipe masks the true exit status under `set -e`) and
# returns non-zero instead of echoing success unconditionally.
# PYTHON_BIN can be overridden from the environment (tests inject a stub).
run_migration_step() {
    local project_dir="${PROJECT_DIR:-$(cd "$(dirname "$0")" && pwd)}"
    local venv_dir="${VENV_DIR:-$project_dir/venv}"
    local pybin="${PYTHON_BIN:-$venv_dir/bin/python3}"
    if [ ! -f "$pybin" ]; then
        pybin="python3"
    fi
    echo -e "${YELLOW}[4/6] Running database migrations...${NC}"
    if ! $pybin -c "from app import app; from cms.models import db; from cms import create_cms_module; create_cms_module(app); print('✅ Migrations OK')"; then
        echo -e "  ${RED}Database migration step failed — aborting deploy${NC}" >&2
        return 1
    fi
    echo -e "  ✅ Migrations applied"
}

# Record the successfully deployed commit in .deployed_sha (see RUNBOOK.md).
record_deployed_sha() {
    local project_dir="${PROJECT_DIR:-$(cd "$(dirname "$0")" && pwd)}"
    local sha
    sha="$(git -C "$project_dir" rev-parse HEAD 2>/dev/null || echo "unknown")"
    if [ "$sha" = "unknown" ]; then
        echo -e "  ${YELLOW}Could not resolve git HEAD — .deployed_sha not updated${NC}"
        return 0
    fi
    echo "$sha" > "$project_dir/.deployed_sha"
    echo -e "  ✅ Deployed commit recorded in .deployed_sha ($sha)"
}

# ---------- Main deployment flow ----------
# Skipped when this file is `source`d by tests (see BASH_SOURCE check).
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then

echo -e "${BLUE}╔══════════════════════════════════════╗${NC}"
echo -e "${BLUE}║   Iveras OSINT Dashboard Update      ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════╝${NC}"
echo ""

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

# ---------- Step 1+2: Backup + Git Pull ----------
# Only in the first instantiation. A pull that updates update.sh itself
# self-restarts below (pull_latest_and_maybe_self_restart), which re-enters
# this script with UPDATE_SH_SELF_EXECUTED=1 and skips straight to the
# remaining steps on the new code.
if [ "${UPDATE_SH_SELF_EXECUTED-}" != "1" ]; then
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

    echo -e "${YELLOW}[2/6] Pulling latest code...${NC}"
    CURRENT_BRANCH=$(git -C "$PROJECT_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "master")
    pull_latest_and_maybe_self_restart
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
run_migration_step

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
    record_deployed_sha
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

fi