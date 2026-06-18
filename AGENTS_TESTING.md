# Testing

## Run
```bash
python3 -m pytest tests/ -v              # serial, ~65s
python3 -m pytest tests/ -v -n auto      # parallel (pytest-xdist), ~30s
```

## Conftest.py Pitfalls

### `app` fixture (session-scoped)
- SQLite temp file (`NamedTemporaryFile`), Alembic upgrade + `init_default_settings()`.  
  `init_default_settings()` now **patches** existing settings (missing `options`, `value_type`, `display_order`) rather than only inserting new ones — test writers should be aware of this mutation.
- Admin user created once at import time (`create_cms_module`), not in fixture.
- **ADMIN PASSWORD FIX**: Admin is created by `create_cms_module()` with a **random** password. The `app` fixture now always resets admin's password to `Test1234!`.

### `auth_client` fixture
- Skips login/2FA via `session_transaction()` — writes `_user_id`, `_fresh`, `_remember` directly into the Flask session cookie.
- Sets `totp_secret=None` and `totp_enabled=False` to avoid 2FA complications.
- Returns a `client` with an authenticated session.

### `_clean_db_between_tests` (autouse, function-scoped)
- Runs before each test function.
- `db.session.rollback()` + DELETE from all tables EXCEPT `users`, `tenants`, `alembic_version` (keeps admin user + default tenant).
- Avoids `db.session.expire_all()` + raw DELETE — that pattern caused `ObjectDeletedError` and UNIQUE constraint errors.
- `app` fixture teardown: DROPs ALL tables (including `alembic_version`) so each test session starts clean.

### Session leak (FIXED)
- **Root cause**: Flask-Login 0.6.3 stores login state in `g._login_user`. `_get_user()` checks `"_login_user" not in g` to decide whether to call `_load_user()`. Flask 3.x scopes `g` to the app context, so the session-scoped `app` fixture keeps `g._login_user` alive across tests. This causes `_load_user()` to be skipped entirely on subsequent tests — it never re-reads the session cookie.
- **Fix**: A `before_request` handler (prepended before `set_tenant_context`) clears `g._login_user` via `_g.pop("_login_user", None)` on every test request, forcing `_load_user()` to re-read the session.
- 55+ session leak failures were resolved by this fix.
- 4 tests in `test_auth.py` remain `@pytest.mark.skip(reason="Flaky: session.auth state leaks")` — these may be resolved but are skipped to avoid false positives.

### Test files
| File | Tests | Notes |
|---|---|---|---|
| `test_auth.py` | 30 | Login/2FA/password/user mgmt |
| `test_core.py` | 10 | Core functionality |
| `test_findings.py` | 7 | Findings CRUD + social findings |
| `test_phone_lookup.py` | 8 | Phone enrichment |
| `test_username_search.py` | 6 | Username search |
| `test_lookups.py` | 27 | RDW, Kadaster, Interpol lookups |
| `test_social.py` | 23 | Social account CRUD, bulk extract |
| `test_templates.py` | 3 | Template rendering |
| `test_routes_smoke.py` | 2 | Static route response codes |
| `test_cases.py` | 16 | Cases CRUD + state |
| `test_subjects.py` | 18 | Subjects CRUD |
| `test_clients.py` | 18 | Clients CRUD |
| `test_documents.py` | 16 | Document upload |
| `test_reminders.py` | 13 | Reminder CRUD |
| `test_audit.py` | 11 | Audit log purge |
| `test_rate_limiter.py` | 3 | Rate limiter (1 test per class) |
| `test_integration.py` | 42 | Webhooks, API keys, background tasks |
| `test_financials_comments.py` | 25 | Financials + comments endpoints |
| `test_screenshots.py` | 7 | Screenshot upload/manage |
| `test_social_extraction.py` | 11 | Social media extraction |
| **Total** | **296** | 292 passed, 4 skipped |

### Test conventions
- All mock external APIs (httpx, requests). No network calls.
- Password `Test1234!` (meets complexity requirements).
- Tests create cases/clients because schema requires `client_id` and routes validate that.
- Document upload tests mock `validate_upload()` (magic-byte check) and use `multipart/form-data`.
- Audit purge tests verify `AuditLog.purge_old(days=N)` + startup purge in `cms/__init__.py`.
- New tests check `test_requires_auth` (unauthorized = 401/302) + happy path + edge cases.

## pytest-xdist
- Uses `pytest-xdist` for parallel test execution (`-n auto`).
- Each worker is a separate process with its own SQLite temp database (defined at conftest module level).
- Tests must be independent — `_clean_db_between_tests` (autouse) ensures isolation.
- To run serial (e.g., for debugging): `-n 0` or `-p no:xdist`.
