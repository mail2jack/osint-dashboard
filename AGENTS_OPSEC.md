# OSINT OPSEC — Jitter, Proxies, Playwright, Tor

## Request Timing Jitter + Proxy Rotation + Profile Rotation

Defined in `cms/services/http_utils.py`.

### Jitter
- Random delay between consecutive OSINT HTTP calls to evade rate limiting + fingerprinting.
- Settings (DB, all optional):
  - `jitter_enabled` — `"true"` (default) / `"false"`
  - `jitter_min` — minimum delay (default `0.3` seconds)
  - `jitter_max` — maximum delay (default `2.0` seconds)
- **Per-domain tracking**: Only repeat calls to same domain within the jitter window trigger a sleep.
- Functions:
  - `jitter_sleep(domain_hint=None)` — sleep if same domain called recently
  - `reset_jitter_state()` — clear timestamps (for testing)

### Proxy Rotation
- `proxy_rotation_enabled` — `"true"` / `"false"` (default `"false"`)
- `proxy_list` — comma/newline-separated proxy URLs (e.g., `http://user:pass@ip1:port, socks5://ip2:port`)
- `get_next_proxy()` — returns `{"http": proxy, "https": proxy}` dict from round-robin list, or `None`
- `reset_proxy_state()` — reset rotation counter

### Profile Rotation (Browser Fingerprinting)
- `impersonate_rotation_enabled` — `"true"` (default) / `"false"`
- `impersonate_profiles` — custom comma-separated profile names (chrome124, safari17_2_1, firefox123, etc.)
- `next_impersonate()` — returns next of 9 profiles (chrome124/123/120/116/110, safari17, firefox123/120)

### Wrappers
- `jittered_get(url)`, `jittered_post(url)`, `jittered_head(url)` — combine jitter + proxy + profile rotation + `curl_requests` with Playwright fallback on failure.
- `jittered_session()` — returns `curl_requests.Session` with proxy + profile rotation.
- Integrated into ALL sync `curl_requests` calls (39 call sites across 16 files).
- Async modules (`email_search.py`, `username_search.py`) skip jitter (400+ parallel requests).

---

## Playwright Fallback (`cms/services/playwright_service.py`)
- Fallback fetch using headless Chromium when `curl_cffi` fails (403/429/connection error on JS-heavy sites).
- Setting: `playwright_fallback_enabled` — `"true"` / `"false"`.
- `PlaywrightResponse` wraps response to mimic `curl_cffi.Response` (`.json()`, `.raise_for_status()`, `.ok`, etc.).
- `jittered_get/post/head` try Playwright on `CurlError` or exception.
- Dependency: `playwright` + Chromium (`pip install playwright && playwright install chromium`).
- Graceful degradation: `is_playwright_available()` returns `False` if not installed.

---

## Tor Proxy for OPSEC (`cms/services/search_service.py`)
- Routes OSINT searches (Brave API, DuckDuckGo fallback) through Tor exit nodes.
- Settings:
  - `tor_enabled` — `"true"` / `"false"` (default `"false"`)
  - `tor_proxy` — SOCKS5 URL (default `socks5://127.0.0.1:9050`)
- Enable: `Setting.set('tor_enabled', 'true')` or via Settings GUI.
- Code: `_get_http_client()` returns `httpx.Client` routing through Tor when enabled.
- Config cache: `_refresh_tor_config()` caches for 60s.

### macOS Setup
```bash
brew install tor
brew services start tor
```

### Debian/Ubuntu Setup
```bash
sudo apt install tor
sudo systemctl enable --now tor
```

### Health Check
- `/health` and dashboard show `"tor": "ok"` when enabled + Brave reachable through it.
- `"tor": "unavailable: ..."` when proxy unreachable.
