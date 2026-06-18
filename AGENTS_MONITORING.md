# Monitoring — Health, Sentry, Grafana, Updates

## Health Check
`curl http://localhost:5000/health` — returns `{"status":"ok","database":"connected","spiderfoot":"connected"}`.
- `/health?quick=1` skips external services (kadaster, rdw, hibp) — for Docker + template banner.
- SF health uses `SpiderFootClient.ping()` with HTTP Digest auth.
- Health status cached in Settings (`spiderfoot_health`, `spiderfoot_last_ok`).
- Banner in `base.html` checks every 60s.

---

## Sentry Error Tracking (`app.py`)
- **Opt-in**: `SENTRY_DSN` env var (primary) or `sentry_dsn` Setting (GUI fallback).
- **Integrations**: `FlaskIntegration` + `SqlalchemyIntegration`.
- **Config**: `traces_sample_rate=0.1`, `environment` from `FLASK_ENV`.
- **`send_default_pii=False`** — never send user PII to Sentry.
- Initialize at import time (env var) and after `create_cms_module(app)` (Settings check).
- Set via shell: `Setting.set('sentry_dsn', 'https://...@ingest.sentry.io/...')` (category=`system`, `encrypt=True`).

---

## Grafana Dashboard (`grafana/dashboard.json`)
- **7 panels**: Request Rate, Active Requests, HTTP Status (2xx/4xx/5xx), Latency p50/p95/p99, Top Routes, Duration Distribution, Requests by Method.
- **Prometheus data source**: expects `http://localhost:9090`.
- **Metrics at** `/metrics` by `cms/metrics.py`.
- **Import**: Grafana → Dashboards → Import → paste JSON → select Prometheus datasource.

---

## Update Notifications
- `check_update()` checks both VERSION file AND latest commit SHA from GitHub.
- `last_update_commit` Setting stores local HEAD SHA after each successful `do_update()`.
- If remote SHA ≠ stored SHA, "New commits available" banner appears.
- **CRITICAL**: `Setting.set('update_check_repo', 'mail2jack/osint-dashboard')` must be set — without it, `check_enabled: False` and banner NEVER shows. `install.sh` sets this automatically.
- **Auto-detect**: If `last_update_commit` is empty, `check_update()` runs `git rev-parse HEAD` and stores it. Visiting AFTER a `git pull` silently stores the new HEAD (no diff shown).
- **Diagnostic**: `sudo -u osint /opt/osint-dashboard/venv/bin/python -c "from app import app; from cms.models import Setting; app.app_context().push(); print('repo:', Setting.get('update_check_repo')); print('last_sha:', Setting.get('last_update_commit','(empty)'))"`
