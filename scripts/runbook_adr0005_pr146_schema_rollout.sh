#!/usr/bin/env bash
#
# Runbook — ADR-0005 PR-A (PR #146): schema-uitrol
# `research_actions.investigation_id` + composite FK op productie.
#
# Fail-closed: stopt bij ELKE afwijking en voert NOOIT een deploy uit zonder de
# expliciete vlag `--confirm`. Zonder die vlag draaien ALLEEN preflight,
# TARGET_SHA-resolve/-bewijs, baseline-snapshot, verse backup + verplichte
# verificatie (fail-closed gate) — daarna STOPT het script (droogloop).
#
# Gebruik (als root, op de VPS):
#   sudo bash scripts/runbook_adr0005_pr146_schema_rollout.sh            # preflight + dry-run
#   sudo bash scripts/runbook_adr0005_pr146_schema_rollout.sh --confirm  # preflight + dry-run + deploy + post-checks
#
# Optionele env-overschrijvingen:
#   DB_BYPASS_URL=<read-only|bypass-url>   # URL voor struct/data-checks
#                                          # (standaard: DATABASE_URL uit .env)
#
# Vóór het venster: dit bestand handmatig naar de VPS kopiëren (bv. scp naar
# /opt/osint-dashboard/scripts/) en daar als root draaien; het staat nog niet
# op de VPS zolang die op PREV_SHA staat.
#
set -euo pipefail

RUNBOOK_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$RUNBOOK_DIR/.." && pwd)"
ENV_FILE="$PROJECT_DIR/.env"
VENV_PYTHON="$PROJECT_DIR/venv/bin/python3"

PREV_REV="e2f3a4b5c6d7"                       # alembic-head vóór migratie
TARGET_REV="f5a6b7c8d9e0"                     # alembic-head ná migratie
PR146_MERGE="1382deb"                         # mergecommit PR #146
PR146_MIGRATION="migrations/versions/f5a6b7c8d9e0_research_actions_investigation_link.py"
HEALTH_URL="http://localhost:5000/api/v1/health"
MIN_DISK_MB=5000

CONFIRM_DEPLOY=0
TS="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_DIR="$PROJECT_DIR/logs"
LOG_FILE="$LOG_DIR/runbook-adr0005-pr146-$TS.log"

for arg in "$@"; do
    case "$arg" in
        --confirm) CONFIRM_DEPLOY=1 ;;
        -h | --help) sed -n '2,28p' "$0"; exit 0 ;;
        *) echo "ERROR: onbekend argument '$arg'" >&2; exit 2 ;;
    esac
done

fail() {
    echo "FAIL: $1"
    echo "==== runbook GEFAALD $(date -u +%Y-%m-%dT%H:%M:%SZ) ===="
    echo "Log: $LOG_FILE"
    exit 1
}
note() { echo "OK: $1"; }

on_err() { echo "FAIL (afgebroken bij afwijking) op runbook-regel $1"; }
trap 'on_err $LINENO' ERR

# ---------------------------------------------------------------------------
# Basisomgeving + logging (fail-closed)
# ---------------------------------------------------------------------------
if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: draai als root (sudo bash scripts/runbook_...sh)" >&2
    exit 1
fi
if [ ! -d "$PROJECT_DIR" ]; then
    echo "ERROR: $PROJECT_DIR bestaat niet — draai dit op de VPS" >&2
    exit 1
fi
# update.sh hardcodeert DIR=/opt/osint-dashboard; hieraan koppelen dus vereist.
[ "$PROJECT_DIR" = "/opt/osint-dashboard" ] || {
    echo "ERROR: runbook moet op /opt/osint-dashboard/scripts/ liggen (update.sh hardcodeert dat pad; nu: $PROJECT_DIR)" >&2
    exit 1
}
if [ ! -f "$ENV_FILE" ] || [ ! -f "$VENV_PYTHON" ]; then
    echo "ERROR: .env of venv ontbreekt in $PROJECT_DIR" >&2
    exit 1
fi
if ! command -v psql >/dev/null 2>&1; then
    echo "ERROR: psql ontbreekt op de VPS" >&2
    exit 1
fi

mkdir -p "$LOG_DIR"
trap '' PIPE
exec > >(tee -a "$LOG_FILE") 2>&1
echo "==== runbook start $TS (mode=$([ "$CONFIRM_DEPLOY" -eq 1 ] && echo CONFIRM || echo DRY-RUN)) ===="

DB_URL=""
[ -f "$ENV_FILE" ] && DB_URL=$(grep -m1 '^DATABASE_URL=' "$ENV_FILE" | cut -d= -f2- || true)
[ -n "$DB_URL" ] || fail "geen DATABASE_URL gevonden in .env"
DB_BYPASS_URL="${DB_BYPASS_URL:-$DB_URL}"

# ---------------------------------------------------------------------------
# 1/8 Basis-gate (preflight.sh) — read-only, fail-closed
# ---------------------------------------------------------------------------
echo "=== 1/8 Preflight ("$([ "$CONFIRM_DEPLOY" -eq 1 ] && echo CONFIRM || echo dry)" — read-only) ==="
[ -x "$PROJECT_DIR/scripts/preflight.sh" ] || fail "preflight.sh ontbreekt"
bash "$PROJECT_DIR/scripts/preflight.sh" || fail "preflight niet groen"
note "preflight.sh"

echo "=== 1b/8 Dienststatus + disk ==="
systemctl -q is-active osint-dashboard || fail "osint-dashboard niet actief"
systemctl -q is-active license-server || fail "license-server niet actief"
note "services actief (osint-dashboard, license-server)"

DISK_MB_AVAIL="$(df -Pm "$PROJECT_DIR" | awk 'NR==2{print $4}')"
[ "${DISK_MB_AVAIL:-0}" -ge "$MIN_DISK_MB" ] || fail "te weinig schijfruimte (${DISK_MB_AVAIL}MB < ${MIN_DISK_MB}MB)"
note "disk vrij >= ${MIN_DISK_MB}MB (${DISK_MB_AVAIL}MB)"

# ---------------------------------------------------------------------------
# 2/8 TARGET_SHA-resolve + merge-freeze + PR-#146-bewijs
# ---------------------------------------------------------------------------
echo "=== 2/8 TARGET_SHA-resolve en PR-#146-bewijs ==="
sudo -u osint git -C "$PROJECT_DIR" fetch origin || fail "git fetch origin mislukt (network/clone)"
TARGET_SHA="$(sudo -u osint git -C "$PROJECT_DIR" rev-parse origin/master)"
[ -n "$TARGET_SHA" ] || fail "geen origin/master geresolveerd"
PREV_SHA="$(cat "$PROJECT_DIR/.deployed_sha" 2>/dev/null || true)"
[ -n "$PREV_SHA" ] || fail ".deployed_sha niet gevonden op de VPS"
echo "PREV_SHA:   $PREV_SHA (uit .deployed_sha)"
echo "TARGET_SHA: $TARGET_SHA (= origin/master na fetch)"

[ "$TARGET_SHA" != "$PREV_SHA" ] || fail "TARGET_SHA == PREV_SHA (geen forward deploy)"
sudo -u osint git -C "$PROJECT_DIR" merge-base --is-ancestor "$PREV_SHA" "$TARGET_SHA" \
    || fail "PREV_SHA is geen ancestor van TARGET_SHA (geen forward deploy)"
# Echte PR-#146-check: 1382deb (mergecommit) in target, of de migratie zelf.
sudo -u osint git -C "$PROJECT_DIR" merge-base --is-ancestor "$PR146_MERGE" "$TARGET_SHA" \
    || fail "proof: PR #146 (1382deb) is geen ancestor van TARGET_SHA"
sudo -u osint git -C "$PROJECT_DIR" cat-file -e "$TARGET_SHA:$PR146_MIGRATION" \
    || fail "proof: migratie f5a6b7c8d9e0 niet aanwezig in TARGET_SHA"
note "TARGET_SHA bevat PR #146 (1382deb-ancestor + migratie f5a6b7c8d9e0 aanwezig)"
echo "Merge-freeze: géén master-mutatie tot na het venster (re-check bij deploy)."

# ---------------------------------------------------------------------------
# 3/8 Alembic pre-head check (read-only)
# ---------------------------------------------------------------------------
echo "=== 3/8 Alembic pre-head (moet $PREV_REV zijn, single head) ==="
ALEMBIC_CUR="$(sudo -u osint env DATABASE_URL="$DB_URL" "$VENV_PYTHON" -m alembic current 2>&1 || true)"
echo "alembic current: $ALEMBIC_CUR"
echo "$ALEMBIC_CUR" | grep -q "$PREV_REV" || fail "alembic current != $PREV_REV (pre)"
echo "$ALEMBIC_CUR" | grep -q "$TARGET_REV" && fail "migratie f5a6b7c8d9e0 al toegepast — niet mid-run"
note "alembic-head pre = $PREV_REV"

# ---------------------------------------------------------------------------
# 4/8 Baseline-data-snapshot (read-only; voor NULL-populatie-compare)
# ---------------------------------------------------------------------------
echo "=== 4/8 Baseline data-snapshot ==="
psql_ro() { psql -v ON_ERROR_STOP=1 -Atc "$1" "$DB_BYPASS_URL"; }
RA_TOTAL_PRE="$(psql_ro 'SELECT count(*) FROM research_actions;')"
RA_LINKED_PRE="$(psql_ro 'SELECT count(investigation_id) FROM research_actions;')"
echo "research_actions totaal (pre): $RA_TOTAL_PRE"
echo "research_actions gekoppeld (pre): $RA_LINKED_PRE"

# ---------------------------------------------------------------------------
# 5/8 Verse backup + verplichte verificatie (fail-closed gate)
# ---------------------------------------------------------------------------
echo "=== 5/8 Verse backup + verify_backup (fail-closed) ==="
sudo -u osint bash "$PROJECT_DIR/scripts/backup.sh" "$PROJECT_DIR/backups" \
    || fail "backup.sh mislukt — venster stopt"
ARCHIVE="$(ls -t "$PROJECT_DIR"/backups/iveras_backup_*.tar.gz.gpg 2>/dev/null | head -1 || true)"
[ -n "$ARCHIVE" ] || fail "geen verse backup-archief gevonden"
echo "vers backup-archief: $ARCHIVE"
sudo -u osint bash "$PROJECT_DIR/scripts/verify_backup.sh" "$ARCHIVE" \
    || fail "verify_backup niet groen — venster stopt (geen update.sh)"
note "backup + verificatie groen (fail-closed gate gepasseerd)"

# ---------------------------------------------------------------------------
# 6/8 Deploy-gate
# ---------------------------------------------------------------------------
echo ""
echo "===== DEPLOY-GATE ====="
if [ "$CONFIRM_DEPLOY" -eq 0 ]; then
    echo "DRY-RUN: alle preflight-, resolutie-, baseline- en backup-gates groen."
    echo "Deploy NIET uitgevoerd — herstart met --confirm voor de daadwerkelijke uitrol."
    echo "==== runbook DRY-RUN GROEN $(date -u +%Y-%m-%dT%H:%M:%SZ) ===="
    echo "Log: $LOG_FILE"
    exit 0
fi

# Re-check merge-freeze direct vóór deploy: TARGET_SHA mag niet zijn opgeschoven.
CUR_SHA="$(sudo -u osint git -C "$PROJECT_DIR" rev-parse origin/master)"
[ "$CUR_SHA" = "$TARGET_SHA" ] || fail "origin/master veranderd tijdens venster (was $TARGET_SHA, nu $CUR_SHA) — merge-freeze geschonden"
note "merge-freeze gehouden (origin/master nog steeds == TARGET_SHA)"

# ---------------------------------------------------------------------------
# 7/8 Deploy via bestaand update.sh (patroon PR #145)
# ---------------------------------------------------------------------------
echo "=== 7/8 update.sh (deploy) ==="
(cd "$PROJECT_DIR" && sudo ./scripts/update.sh) || fail "update.sh faalde — géén automatische rollback, zie update.sh-log"
note "update.sh geslaagd"

# ---------------------------------------------------------------------------
# 8/8 Post-deploychecks (fail-closed, in volgorde)
# ---------------------------------------------------------------------------
echo "=== 8/8a Alembic-head post (moet $TARGET_REV zijn, single head) ==="
ALEMBIC_CUR2="$(sudo -u osint env DATABASE_URL="$DB_URL" "$VENV_PYTHON" -m alembic current 2>&1 || true)"
echo "alembic current: $ALEMBIC_CUR2"
echo "$ALEMBIC_CUR2" | grep -q "$TARGET_REV" || fail "alembic current != $TARGET_REV (post)"
echo "$ALEMBIC_CUR2" | grep -q "$PREV_REV" && fail "PREV_REV $PREV_REV nog head (post) — downgrade-fout"
note "alembic-head post = $TARGET_REV"

echo "=== 8/8b Deployed SHA ==="
DEP_SHA="$(cat "$PROJECT_DIR/.deployed_sha" 2>/dev/null || true)"
[ "$DEP_SHA" = "$TARGET_SHA" ] || fail ".deployed_sha ($DEP_SHA) != TARGET_SHA"
HEAD_SHA="$(sudo -u osint git -C "$PROJECT_DIR" rev-parse HEAD)"
[ "$HEAD_SHA" = "$TARGET_SHA" ] || fail "HEAD ($HEAD_SHA) != TARGET_SHA"
note ".deployed_sha == HEAD == TARGET_SHA"

echo "=== 8/8c Healthchecks ==="
curl -fsS "$HEALTH_URL" >/dev/null || fail "health-endpoint niet bereikbaar"
systemctl -q is-active osint-dashboard || fail "osint-dashboard inactief na deploy"
systemctl -q is-active license-server || fail "license-server inactief na deploy"
note "health + services groen"

echo "=== 8/8d Structuur-verificatie (constraints, FK-kolommen, nullable, index) ==="
CONSTRAINTS="$(psql_ro "SELECT conname || ':' || contype FROM pg_constraint WHERE conname IN ('uq_investigations_id_case_tenant','fk_research_actions_investigation_case_tenant') ORDER BY conname;")"
echo "$CONSTRAINTS"
echo "$CONSTRAINTS" | grep -q 'uq_investigations_id_case_tenant:u' || fail "parent-key uq_investigations_id_case_tenant ontbreekt"
echo "$CONSTRAINTS" | grep -q 'fk_research_actions_investigation_case_tenant:f' || fail "composite FK ontbreekt"

FKCOLS="$(psql_ro "SELECT a.attname FROM pg_constraint c JOIN unnest(c.conkey) WITH ORDINALITY k(attnum, ord) ON true JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum WHERE c.conname = 'fk_research_actions_investigation_case_tenant' ORDER BY k.ord;")"
echo "FK-kolommen (volgorde): $FKCOLS"
[ "$(echo "$FKCOLS" | sed -n '1p')" = "investigation_id" ] || fail "FK-kolom 1 != investigation_id"
[ "$(echo "$FKCOLS" | sed -n '2p')" = "case_id" ] || fail "FK-kolom 2 != case_id"
[ "$(echo "$FKCOLS" | sed -n '3p')" = "tenant_id" ] || fail "FK-kolom 3 != tenant_id"

NUL_COL="$(psql_ro "SELECT is_nullable FROM information_schema.columns WHERE table_schema='public' AND table_name='research_actions' AND column_name='investigation_id';")"
[ "$NUL_COL" = "YES" ] || fail "investigation_id is niet nullable"
echo "investigation_id nullable: $NUL_COL"

IDX="$(psql_ro "SELECT indexname FROM pg_indexes WHERE indexname='ix_research_actions_investigation_id';")"
[ "$IDX" = "ix_research_actions_investigation_id" ] || fail "index ix_research_actions_investigation_id ontbreekt"
note "structuur-correct (parent-key, composite FK, nullable, index)"

echo "=== 8/8e Data-intact-verificatie (mismatch=0, NULL-populatie, pk-duplicaten) ==="
MISMATCH="$(psql_ro "SELECT count(*) FROM research_actions ra JOIN investigations iv ON iv.id = ra.investigation_id WHERE ra.investigation_id IS NOT NULL AND (ra.case_id <> iv.case_id OR ra.tenant_id <> iv.tenant_id);")"
[ "$MISMATCH" = "0" ] || fail "mismatch-rijen != 0 (waarde: $MISMATCH)"
note "mismatch-rijen = $MISMATCH"

RA_TOTAL_POST="$(psql_ro 'SELECT count(*) FROM research_actions;')"
RA_LINKED_POST="$(psql_ro 'SELECT count(investigation_id) FROM research_actions;')"
[ "$RA_TOTAL_POST" = "$RA_TOTAL_PRE" ] || fail "research_actions-totaal veranderd ($RA_TOTAL_PRE -> $RA_TOTAL_POST)"
[ "$RA_LINKED_POST" = "$RA_LINKED_PRE" ] || fail "gekoppeld-aantal veranderd ($RA_LINKED_PRE -> $RA_LINKED_POST)"
note "NULL-populatie onveranderd: totaal $RA_TOTAL_POST, gekoppeld $RA_LINKED_POST"

DUPE="$(psql_ro "SELECT count(*) - count(DISTINCT (id, case_id, tenant_id)) FROM investigations;")"
[ "$DUPE" = "0" ] || fail "parent-key-duplicaten != 0 (waarde: $DUPE)"
note "parent-key-uniek houdbaar (geen pk-duplicaten = $DUPE)"

# ---------------------------------------------------------------------------
# Einde
# ---------------------------------------------------------------------------
echo ""
echo "==== runbook ADR-0005 PR-A VOLTOOID $(date -u +%Y-%m-%dT%H:%M:%SZ) ===="
echo "TARGET_SHA:     $TARGET_SHA"
echo "PREV_SHA:       $PREV_SHA"
echo "Alembic-head:   $TARGET_REV"
echo "Backup-archief: $ARCHIVE"
echo "Mismatch-rijen: $MISMATCH"
echo "Log:            $LOG_FILE"
echo ""
echo "Rolloutrapport hierboven archiveren. Formeel einde venster."
echo "PR #147 pas mergen ná deze groene post-deploychecks (zie plan sectie 8)."