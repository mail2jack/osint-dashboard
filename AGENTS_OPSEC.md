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

### Domain-Based Impersonation (Fase 2)
- **Per-domain fingerprint**: Same domain always gets the same impersonation profile (consistent fingerprint per site).
- **Different domains, different profiles**: Domain is hashed and mapped deterministically to a profile from the list.
- **Prevents cross-domain correlation**: Tracking services on domain A see a Chrome 124, on domain B a Firefox 123 — cannot link them.
- Setting: `domain_impersonation_enabled` — `"true"` (default) / `"false"`
- Functions:
  - `impersonate_for_domain(url)` — returns consistent profile for the URL's domain
  - `_extract_domain(url)` — extracts hostname from URL
  - `reset_impersonation_state()` — clears domain→profile cache (for testing)
- `jittered_get/post/head` use `impersonate_for_domain()` instead of the global `next_impersonate()`.
- `jittered_session()` still uses `next_impersonate()` (Session is a single browser context).

### Wrappers
- `jittered_get(url)`, `jittered_post(url)`, `jittered_head(url)` — combine jitter + proxy/Tor + profile rotation + `curl_requests` with Playwright fallback on failure.
- `jittered_session()` — returns `curl_requests.Session` with proxy + profile rotation.
- Integrated into ALL sync `curl_requests` calls (51+ call sites across 19 files).
- Async modules (`email_search.py`, `username_search.py`) skip jitter (400+ parallel requests).

---

## Playwright Fallback (`cms/services/playwright_service.py`)
- Fallback fetch using headless Chromium when `curl_cffi` fails (403/429/connection error on JS-heavy sites).
- Setting: `playwright_fallback_enabled` — `"true"` / `"false"`.
- `PlaywrightResponse` wraps response to mimic `curl_cffi.Response` (`.json()`, `.raise_for_status()`, `.ok`, etc.).
- `jittered_get/post/head` try Playwright on `CurlError` or exception.
- Dependency: `playwright` + Chromium (`pip install playwright && playwright install chromium`).
- Graceful degradation: `is_playwright_available()` returns `False` if not installed.

## Playwright Stealth Mode (`cms/services/playwright_stealth.py`) — Fase 3
- **Evades headless browser detection** — social media sites block undetected headless Chrome.
- Applied in **4 Playwright usage points**: `playwright_service.py` (central fallback), `social_extraction.py`, `screenshots.py`, `vessel_service.py`.
- Setting: `playwright_stealth_enabled` — `"true"` (default) / `"false"`.

### Wat het doet
| Techniek | Methode |
|---|---|
| **Chromium CLI args** | `--disable-blink-features=AutomationControlled`, `--disable-automation`, `--no-sandbox`, etc. |
| **User-Agent rotatie** | Per-domain deterministisch: 6 realistische Chrome UAs (Win/Mac/Linux) |
| **Viewport randomisatie** | 6 common resolutions (1920×1080, 1366×768, 1536×864, etc.) |
| **Locale + timezone** | en-US, en-GB, nl-NL, de-DE, fr-FR + matching timezones |
| **`navigator.webdriver`** | Override naar `false` (belangrijkste detectiepunt) |
| **`chrome.runtime`** | Mock object aanwezig |
| **`navigator.plugins`** | Toont 5 plugins (niet 0 zoals headless default) |
| **`navigator.languages`** | Vast op `['en-US', 'en']` |
| **Permissions query** | `notifications` altijd `denied` |
| **SOCKS5 proxy** | Playwright proxy via `new_context(proxy=...)` i.p.v. `launch()`, ondersteunt nu wél SOCKS5 |

### Domain-Based Stealth Profiles
- **Zelfde domein** → altijd dezelfde UA, viewport, locale, timezone (voorkomt verdenking).
- **Verschillende domeinen** → verschillende profielen (voorkomt cross-domain correlatie).
- Deterministische hash (MD5, geen Python `hash()` — consistent over processen heen).

### Code Reference (`cms/services/playwright_stealth.py`)
| Function | Purpose |
|---|---|
| `stealth_for_domain(url)` | Returns deterministic stealth profile dict or `None` if disabled |
| `get_stealth_init_scripts()` | Returns list of JS init scripts for context injection |
| `apply_stealth_to_context(context)` | Injects init scripts into a Playwright BrowserContext |
| `reset_stealth_state()` | Clears cached profiles (for testing) |

---

## OSINT Audit Hash Chain (`cms/services/audit_chain.py`) — Fase 5
- **Cryptografische chain of custody** voor alle externe OSINT HTTP calls.
- Elke call via `jittered_get/post/head` wordt geregistreerd met SHA256-hashes.
- Setting: `audit_chain_enabled` — `"true"` (default) / `"false"`.

### Hoe het werkt
| Stap | Wat |
|---|---|
| **1. Entry hash** | `SHA256(url\|method\|status\|domain\|profile\|error\|timestamp)` |
| **2. Chain hash** | `SHA256(prev_hash + entry_hash)` — cryptografisch gelinkt aan vorige call |
| **3. Persist** | Opgeslagen in `AuditLog`-tabel met `entity_type="osint_chain"` |
| **4. Verificatie** | Chain integrity kan worden gecontroleerd door alle hashes opnieuw te berekenen |

### Integratie
- `_record_audit(url, method, status, kwargs)` wordt aangeroepen in `jittered_get`, `jittered_post`, `jittered_head`:
  - **Success**: na `curl_requests.*()` met response status_code
  - **Playwright fallback**: na `_try_playwright_fallback()` met fallback status_code
  - **Tor blocked**: bij `TorNotAvailableError`
  - **Error**: bij `CurlError` of andere exceptie
- **Graceful degradation**: als AuditLog niet beschikbaar is (geen app context), wordt alleen de in-memory chain bijgewerkt.

### Code Reference (`cms/services/audit_chain.py`)
| Function | Purpose |
|---|---|
| `record_osint_call(url, method, status_code, ...)` | Record een OSINT call in de chain + persist naar AuditLog |
| `get_chain_status()` | Returns huidige chain state (enabled, length, last_hash) |
| `reset_chain()` | Reset in-memory chain (voor testing) |

---

## Identity Isolation (`cms/services/identity_isolation.py`) — Fase 4
- **Per-onderzoek apart Tor circuit**: elk onderzoek (case_id) krijgt een unieke SOCKS5-username.
- Gebruikt Tor's `IsolateSOCKSAuth`-mechanisme: Tor ziet elke unieke auth als een aparte client en gebruikt een apart circuit.
- Setting: `identity_isolation_enabled` — `"false"` (default, vereist torrc aanpassing).

### Hoe het werkt
1. **`set_identity_for_case(case_id)`** — derived een identity uit case_id (SHA256 van case_id, eerste 16 chars)
2. **`identity_for_proxy(proxy_url, identity)`** — voegt identity toe als username in de SOCKS5 URL
3. **`get_next_proxy(identity=identity)`** — retourneert `socks5h://case_<hash>@127.0.0.1:9050`
4. **Tor** met `IsolateSOCKSAuth 1` behandelt elke unieke auth als een apart circuit
5. **Automatisch per request**: `@cms_bp.before_request` detecteert `case_id` in URL params en roept `set_identity_for_case()` aan

### Torrc configuratie
```ini
SOCKSPort 9050 IsolateSOCKSAuth
ControlPort 9051
```

### Code Reference (`cms/services/identity_isolation.py`)
| Function | Purpose |
|---|---|
| `set_identity_for_case(case_id)` | Stel identity in obv case_id (SHA256 hash) |
| `get_current_identity()` | Lees huidige identity (ContextVar) |
| `reset_identity()` | Wis identity |
| `is_identity_isolation_enabled()` | Check of feature aan staat |
| `identity_for_proxy(proxy_url, identity)` | Voegt identity als SOCKS5 username toe |

---

## Tor Proxy for OPSEC (centralized in `cms/services/http_utils.py`)

### Architecture
- Tor routing is centralized in `get_next_proxy()` in `http_utils.py` — ALL OSINT HTTP calls go through it.
- When `tor_enabled=true`, `get_next_proxy()` returns the Tor SOCKS5 proxy (instead of round-robin proxies).
- `jittered_get/post/head` automatically use the Tor proxy via `get_next_proxy()` — no per-file changes needed.
- **51+ call sites across 19 files** now route through Tor when enabled.

### Settings (DB, seed on startup in `cms/__init__.py`)
| Key | Default | Description |
|---|---|---|
| `tor_enabled` | `"false"` | Master toggle — routes ALL OSINT traffic through Tor |
| `tor_proxy` | `"socks5h://127.0.0.1:9050"` | SOCKS5 proxy URL (use `socks5h://` for remote DNS) |
| `tor_strict_mode` | `"false"` | Fail-closed: raise `TorNotAvailableError` if Tor unreachable |
| `tor_control_port` | `"9051"` | Tor control port (for circuit management) |
| `tor_password` | `""` | Tor control password (hashed) |

### Fail-Closed Behavior
- `tor_strict_mode=true` + Tor not reachable → `TorNotAvailableError` is raised, blocking the request.
- `tor_strict_mode=false` (default) → falls back to direct connection when Tor is unreachable (fail-open).
- `TorNotAvailableError` propagates cleanly through `jittered_get/post/head` — no partial state.

### Code Reference (`cms/services/http_utils.py`)
| Function | Purpose |
|---|---|
| `_refresh_tor_config()` | Reads settings + env, caches for 60s |
| `get_next_proxy()` | Returns Tor proxy when enabled, else round-robin |
| `is_tor_enabled()` | Returns `_TOR_ENABLED` bool |
| `get_tor_proxy()` | Returns proxy URL or `None` |
| `reset_tor_state()` | Clears cached Tor config (for testing) |

### Health Check (`cms/health_utils.py`)
- `/health` endpoint performs an **active Tor connection test** via `https://check.torproject.org`.
- Checks for `"Congratulations"` in response text to confirm traffic is routed through Tor.
- Returns `"tor": "ok"` / `"not_using_tor"` / `"unavailable: ..."` / `"disabled"`.

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
