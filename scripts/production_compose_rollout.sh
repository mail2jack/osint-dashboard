#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/home/development/joost-production}"
RELEASE_SHA="${RELEASE_SHA:-}"
DRY_RUN=false
CONFIRM=false

usage() {
    echo 'Usage: production_compose_rollout.sh [--dry-run | --confirm DEPLOY-MASTER]'
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --dry-run) DRY_RUN=true; shift ;;
        --confirm)
            [ "${2:-}" = "DEPLOY-MASTER" ] || { usage >&2; exit 2; }
            CONFIRM=true; shift 2 ;;
        --help|-h) usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
done

[ "$(id -u)" -eq 0 ] || { echo 'Run dit script als root (sudo).' >&2; exit 2; }

for required in "$APP_DIR/.env" "$APP_DIR/docker-compose.yml" \
    "$APP_DIR/docker-compose.production.yml" "$APP_DIR/scripts/backup.sh"; do
    [ -f "$required" ] || { echo "Ontbrekend bestand: $required" >&2; exit 1; }
done

cd "$APP_DIR"
COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.production.yml)
"${COMPOSE[@]}" config --quiet

if [ "$DRY_RUN" = true ]; then
    echo 'DRY RUN PASSED: Compose-configuratie is geldig; er wordt niets gewijzigd.'
    exit 0
fi
[ "$CONFIRM" = true ] || { usage >&2; exit 2; }

echo '=== 1/4 Versleutelde productieback-up ==='
bash "$APP_DIR/scripts/backup.sh" "$APP_DIR/backups"
echo '=== 2/4 Applicatie-images bouwen ==='
"${COMPOSE[@]}" build app worker health-refresh
echo '=== 3/4 Applicatieservices starten ==='
"${COMPOSE[@]}" up -d app worker health-refresh
echo '=== 4/4 Healthcheck ==='

attempt=1
while [ "$attempt" -le 10 ]; do
    status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}no-healthcheck{{end}}' joost-production-app-1 2>/dev/null || true)"
    if [ "$status" = healthy ] && "${COMPOSE[@]}" exec -T app curl -fsS 'http://localhost:5000/health?quick=1' >/dev/null; then
        echo 'OK: applicatie healthy'
        [ -z "$RELEASE_SHA" ] || printf '%s\n' "$RELEASE_SHA" > "$APP_DIR/.deployed_sha"
        echo 'Rollout geslaagd.'
        exit 0
    fi
    echo "health nog niet groen (poging $attempt/10): ${status:-unknown}"
    sleep 6
    attempt=$((attempt + 1))
done

echo 'FAIL: applicatie werd niet healthy; controleer Docker-logs.' >&2
exit 1
