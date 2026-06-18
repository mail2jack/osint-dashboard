# Frontend / UI

## UI Internationalization (i18n) — Flask-Babel
- **Setup**: `pip install Flask-Babel` (v4+). Init in `cms/i18n.py`.
- **Config**: `babel.cfg` at project root — extracts strings from `.py` and `.html`.
- **Locale selector** (`cms/i18n.py:get_locale`): reads `session["lang"]` → browser `Accept-Language` → defaults to `"nl"`.
- **Switching**: `GET /lang/<locale>` sets `session["lang"]` and redirects back.
- **Template usage**: `{{ _('Dashboard') }}` or `{{ gettext('Settings') }}`.
- **Adding a string**: wrap in `_('...')`, then:
  ```
  pybabel extract -F babel.cfg -o translations/messages.pot .
  pybabel update -i translations/messages.pot -d translations
  # edit .po, then:
  pybabel compile -d translations
  ```
- **New language**: `pybabel init -i translations/messages.pot -d translations -l de`, edit `.po`, compile.
- **Current languages**: `nl` (default), `en`. Placeholders for `de`, `fr`.
- **`_()` injected** in `app.py` context processor — available in all templates.
- **NOT for content translation**: UI strings only, not Helsinki-NLP model content.

---

## Event Delegation (Templates)
- Global delegation in `templates/cms/base.html` (just before `</body>`):
  - `click` on `[data-click]` → `window[dataset.click](...)` with `data-arg0`, `data-arg1`, etc.
  - `change` on `[data-change]` → `window[dataset.change](element)`
  - `submit` on `[data-submit]` → `window[dataset.submit](event)`. POST forms: `e.preventDefault()` + checks `e.submitter` — only submit button clicks proceed; Enter key silently ignored.
  - `input` on `[data-input]` → `window[dataset.input](element)`
- Helpers: `removeEntry`, `navigateTo`, `reloadPage`.
- Inline `onclick`/`onchange`/`onsubmit` migrated to data attributes (~240 handlers).
- 5 survivors: 3 in `spiderfoot/list.html` (`event.stopPropagation()` cannot use delegation) + 2 in `templates/cms/2fa/recovery_codes.html` — standalone page (does not extend `base.html`), so `[data-click]` delegation is unavailable.
- Flask template variables in data attributes use `|tojson` filter.

---

## Session Keep-Alive
- **Problem**: 8h session timer would `location.reload()` on expiry, causing form data loss.
- **Fix**: Silent `fetch('/api/keep-alive')` extends session instead of reloading.
- **`@csrf.exempt`** on `/api/keep-alive` — fetch carries no CSRF token.
- **File**: `static/js/base.js:82-100`, `cms/routes/system_app.py:143`.

---

## Context-Sensitive Help System
- **Route**: `cms/routes/help.py` — `/cms/help` (index), `/cms/help/<topic>` (full page), `/cms/api/help/<topic>` (AJAX JSON).
- **Content**: Markdown in `help/` (`dashboard.md`, `cases.md`, etc.), rendered via `markdown` library.
- **Slide-out panel**: `#helpPanel` — fixed right-side panel + overlay. Toggled via `openHelp(topic)` / `closeHelp()`.
- **Activation**: `?` key or ❓ button in header.
- **Context awareness**: `body[data-help-topic]` set via Flask context processor.
- **Styling**: `static/css/help.css`.

---

## Image Upload Validation (`cms/image_validation.py`)
- `validate_image_file(file_storage)` checks first 32 bytes against magic byte signatures (PNG, JPEG, GIF, WebP).
- Used in `cms/routes/screenshots.py` and `cms/routes/subjects_faces.py`.
- File cursor restored to position 0 after reading.

---

---

## Static Asset Bundling (`build.mjs`)

### CSS Bundle

- **Entry**: `npm run build` (prod) or `npm run watch` (dev with file watcher).
- **Tool**: esbuild — minifies each CSS file individually, then concatenates into `static/dist/bundle.min.css`.
- **Bundle contents** (3 files → 2 after June 18):
  - `static/css/base.css` — base CMS styles
  - `static/css/help.css` — help panel styles
  - **Excluded** (loaded separately):
    - `static/style.css` — SpiderFoot standalone theme (loaded by `templates/index.html` only)
    - `static/css/cms-professional.css` — Professional theme override, loaded conditionally based on `theme_style` setting

### CSS Loading Strategy (`templates/cms/base.html`)

| Mode | Bundled | Separate |
|---|---|---|
| `g.use_bundle == True` (production) | `bundle.min.css` (base.css + help.css) | `cms-professional.css` only if `theme_style == 'professional'` |
| `g.use_bundle == False` (dev) | — | `base.css`, `help.css`, and `cms-professional.css` (conditional) |

- `cms-professional.css` is intentionally excluded from the bundle so that the Classic theme (base.css only) can be selected without loading Professional overrides.

### JS Bundle

- `static/js/base.js` → minified → `static/dist/base.min.js`.
- Contains: event delegation (`[data-click]`, etc.), CSRF-safe fetch wrapper, keep-alive, theme toggle.

### Version Cache Busting

- `g.css_version` = content of `static/dist/.css_version` (Unix millisecond timestamp).
- Written by `build.mjs` after each build. Appended as `?v=...` to all CSS/JS links in `base.html`.

---

## "current transaction is aborted" Fix
- **Error**: `psycopg2.errors.InFailedSqlTransaction` — caught exceptions in `inject_globals()` and startup blocks aborted the transaction without `rollback()`.
- **Fix (3 files)**: Added `db.session.rollback()` in `before_request` (safety net), in all `except Exception` blocks in `inject_globals`, and in 4 startup `except` blocks in `cms/__init__.py`.
- **Key principle**: Always `db.session.rollback()` after catching any `Exception` that may originate from a DB query.
