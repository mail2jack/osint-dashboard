# Iveras OSINT Dashboard — Agent Guide

## Entrypoint & Run
- `app.py` is the single Flask entrypoint. Dev: `python app.py` (port 5000).
- CMS module initialized via `cms/__init__.py::create_cms_module(app)`.
- Dev with SpiderFoot: `./start.sh start`. Stops: `./start.sh stop`.
- Production: `sudo ./install.sh` (Debian/Ubuntu — sets up Nginx, PostgreSQL, SpiderFoot, systemd, SSL).

## Database
- Default: SQLite at project root `cms.db`. PostgreSQL: set `DATABASE_URL` in `.env`.
- `db.create_all()` runs on first startup — tables + default admin (`admin`/`changeme123`) auto-created.
- Never mutate `created_at` on ORM objects directly (crashes SQLite). Sort with `strftime()` in sort key lambda.

## SpiderFoot Integration (`cms/spiderfoot_service.py`)

### Config source (critical)
- SpiderFoot config is read from the `Setting` model (DB table), NOT from `.env`.
- `get_spiderfoot_config()` in `routes.py:5406` calls `Setting.get('spiderfoot_url')`, `Setting.get('spiderfoot_password')`, etc.
- Setting values via Flask shell: `Setting.set('spiderfoot_url', 'http://...')`.

### Auth
- SpiderFoot v4 uses HTTP Digest auth. Credentials stored in `~/.spiderfoot/passwd` (`admin:<password>`).
- Start with auth: `python3 sf.py -l 127.0.0.1:5001 --passwd ~/.spiderfoot/passwd`.

### API data quirks
- **Scan list format**: `[id, name, target, created, started, completed, status, resultCount, riskSummary]` — status is UPPERCASE (`RUNNING`, `FINISHED`, etc.).
- **Result format**: `[timestamp, data, value, sourceModule, ..., type]`.
- **SFURL tags**: Result `data` contains HTML-escaped `<SFURL>` tags (`&lt;SFURL&gt;url&lt;/SFURL&gt;`). Must `html.unescape()` before regex parsing. Done in `normalize_result()` at line 588.

### Templates
- Live in `templates/cms/spiderfoot/`: `index.html` (dashboard), `view.html` (scan results), `scan.html` (new scan form), `list.html` (all scans), `scan_subject.html`.
- Template filters in `app.py:103-270`: `urlize_target`, `result_link`, `platform_name`, `platform_color`.
- Rich result cards use `.rich-card` with `--card-color` CSS custom property (no separate classes per type).

## Testing
```bash
python tests/test_core.py
```
One test file: `tests/test_core.py` (email, IP, domain validation; phone normalization). Uses pytest.

## Health Check
`curl http://localhost:5000/health` — returns `{"status":"ok","database":"connected","spiderfoot":"connected"}`.

## Git
- Rollback: `git reset --hard <hash>`. Commits are safe to reset.
- Push after production changes: `git push` (remote: `origin/master`).
