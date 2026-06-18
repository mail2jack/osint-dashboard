# Internal Architecture

## Routes Structure
- `cms_bp` defined in `cms/routes/__init__.py` — blueprint for `/cms` prefix.
- `cms/routes/` — 54 modules total (`__init__.py` + 53 route modules):
  - `register_modules()` registers 44 modules via `import` in `cms/routes/__init__.py:14-58`.
  - Separately registered: `app_bp.py` (PDF + phone), `ai_routes.py`, `osint_routes.py`, `history_routes.py`, `auth_routes.py`, `system_app.py` (health, OpenAPI, keep-alive).
  - Other blueprints: `api_v1_bp` in `api_v1.py` (`/cms/api/v1`), `app_routes_bp` in `app_blueprint.py` (shared definition for app_routes).
- Route modules use `request.validated_data` (Pydantic) where extracted; some use `request.get_json()`.
- `cms/search_manager.py` — `SearchManager` class for DB-backed search lifecycle.

---

## Input Validation (`cms/validation.py`)
- Pydantic `@validate(Schema)` decorator for POST routes. Handles both JSON and form data.
- Usage: `@validate(EmailCheckSchema)` after `@login_required`, then `request.validated_data`.
- Returns 400 with `{"error": "Validation failed", "details": [...]}` for JSON requests.
- **Form POST failure**: `@validate` checks `request.is_json` — form POSTs get `flash()` + `redirect(request.path)`.
- Schema `int` fields with defaults (`risk_score: int = 0`) are typed `Any` to accept empty form submits.
- 78 schemas in `cms/validation.py`.

---

## OpenAPI / API Docs (`cms/routes/system_app.py`)
- Auto-generated OpenAPI 3.0.3 spec at `/api/openapi.json` via `_build_openapi_spec()`.
  - Dynamically iterates `current_app.url_map.iter_rules()` — discovers ALL Flask routes.
  - Endpoint `api_docs` renders `templates/cms/api_docs.html` at `/api/docs`.
  - Path parameters extracted via regex, responses are generic (no per-endpoint schemas).
- No Flasgger dependency. No Swagger UI/Redoc.

---

## SafeJSON (SQLite JSON compat)
- `cms/models/__init__.py` — `SafeJSON` inherits `sqlalchemy.types.JSON`, overrides `process_result_value` to `json.loads()` when SQLite returns a raw string.
- All 18 `db.JSON` columns use `SafeJSON`. No manual `isinstance()` guards needed.

---

## Background Task Queue (`cms/background.py`)
- **Dual backend**: RQ (Redis) when `REDIS_URL` is set, `ThreadPoolExecutor(max_workers=8)` fallback otherwise.
- `run_in_background(task_id, func, *args, **kwargs)` — tries RQ first, falls back to executor.
- `get_task_status(task_id)` — `{'status': 'pending'|'running'|'completed'|'failed', 'result': ..., 'error': ...}`.
- `GET /cms/api/background/status/<task_id>` — polling endpoint.
- Tasks persisted in `BackgroundTask` model (DB-backed, not in-memory).
- RQ worker opt-in via `docker compose --profile worker up -d`.
- **Email**: `send_password_reset_background()` — SMTP moved to background.
- **AI/LLM**: `ollama_generate_background()`, `summarize_results_background()`, `analyze_natural_language_background()`.

---

## Error Templates
- `templates/cms/404.html` and `templates/cms/500.html`.
- Error handlers in `cms/routes/system_app.py`: JSON for `/api/` prefix, HTML for rest.
- Error handlers use `cms/404.html` (not root `templates/404.html`).

---

## Startup Settings Initialization (`init_default_settings`)
- `cms/models/__init__.py:2347` — runs at Flask startup + after settings saves.
- Creates missing `Setting` rows from a hardcoded defaults list (~40 items).
- **Since June 18**: Also patches existing settings that are missing `options`, `value_type`, or `display_order` fields. Relevant to any code that reads `setting.options` for select-type rendering.

## Deprecations Fixed
- `datetime.utcnow()` → `datetime.now(timezone.utc)` (Python 3.12 compat).
- `Model.query.get(id)` → `db.session.get(Model, id)` (SQLAlchemy 2.0 compat).
- `legacy_routes.py` removed — `cms_bp` lives in `cms/routes/__init__.py`.
- Type hints added to all route handlers.

---

## CSRF Protection

- Flask-WTF `CSRFProtect` initialized in `cms/__init__.py:25` with `csrf = CSRFProtect()`.
- ~57 route handlers are marked `@csrf.exempt` (49 `@csrf.exempt` decorators + `csrf.exempt(func)` calls) — most are JSON API endpoints consumed by JavaScript (fetch/XHR).
- The frontend's `csrfSafeFetch()` wrapper in `static/js/base.js:1` auto-adds `X-CSRFToken` header from `<meta name="csrf-token">`.
- Overriding `@csrf.exempt` is safe for routes that:
  - Are called exclusively via `csrfSafeFetch()` or fetch with explicit `X-CSRFToken` header.
  - Use API key authentication (`@api_key_required`).
- **Security gap**: Routes with `@csrf.exempt` that rely solely on session cookies are vulnerable to CSRF. If a route is consumed by HTML forms (not JS), it MUST NOT be exempt and must include `{{ csrf_token() }}` in the form.
- **Test note**: Tests set `WTF_CSRF_ENABLED = False`, so they won't catch CSRF issues — manual review needed.
- **Recommendation**: Systematically audit each `@csrf.exempt` route and remove the decorator where the frontend sends `X-CSRFToken`.
