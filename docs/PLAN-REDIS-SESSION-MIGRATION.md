# Plan: Session-backend migreren van filesystem naar Redis (ADR-0004 optie C)

**Status:** analyse gereed + besluiten vastgesteld (2026-09-06); Fase A (code) in PR #117, Fase B (Redis-install VPS) gereed; Fase C/D open  
**Doel:** filesystem-sessie-races definitief elimineren en schaalbaarheid naar
meer workers ondersteunen, zonder bestaande functionaliteit te breken  
**Systeem:** Iveras OSINT Dashboard (VPS `joost.iveras.com`, Ubuntu)

## 1. Uitgangssituatie

- Sessie-backend is cachelib `BoundedFileSystemCache`
  (`cms/session_cache.py`, threshold 5000, timeout 8h), gekozen als ADR-0004
  optie A (retry op `PermissionError`/`FileNotFoundError` + één `get()`-retry).
  Deze fix is live en heeft de 2-worker canary (PASS, 2026-09-06) opgeleverd.
- De app heeft **al** een Redis-sessiepad ingebouwd (`app.py:194-208`): als
  `REDIS_URL` is gezet én pingbaar bij boot → `SESSION_TYPE=redis` +
  `SESSION_REDIS`, JSON-serialisatie, permanente (sliding) sessies.
- Redis is **niet** op de VPS geïnstalleerd; `REDIS_URL` staat niet in `.env`;
  de app draait dus op de filesystem-route. Matrix: `redis==8.1.0`,
  `rq==2.10.0`, `Flask-Session==0.8.0`, `cachelib==0.14.0` (requirements-lock).
- Admin-UI ondersteunt Redis al: `cms/routes/system.py` detecteert
  `SESSION_TYPE=redis` en leest/schrijft/verwijdert `session:*`-keys
  (`_all_session_ids`, `_delete_session`, `_read_session_data`).

## 2. Gewenste eindtoestand

1. Sessies draaien op Redis (lock-vrij, multi-worker-veilig).
2. **Behoud huidige gedrag in alles eromheen**: achtergrondtaken blijven op
   `ThreadPoolExecutor` tenzij expliciet anders (RQ niet meeschakelen).
3. Redis-uitval degradeert of alarmeret veilig, nooit een 500-storm zonder
   waarschuwing.
4. Rollback is één regel: `REDIS_URL` halen uit `.env`, app herstart, filesystem
   route actief.

## 3. Bevindingen uit de analyse

### 3.1 VPS-feiten (2026-09-06)

- Geheugen: **1.8 GiB totaal, 668 MiB available, 722 MiB swap reeds in gebruik**
  (ollama + postgres + gunicorn + tor etc.). Redis moet memory-gecapped worden.
- 4725 actieve sessiebestanden (19 MB). Sessies zijn klein; bij migratie naar
  Redis zijn deze **allemaal weg** → alle ingelogde gebruikers loggen uit.
- Health-monitor (`scripts/monitor_health_light.sh`) telt `store_foreign`/
  `store_total` uit `flask_session/`; bij Redis blijven die 0/0 (geen vals alarm)
  en `oserror` blijft 0. Monitor hoeft niet gewijzigd te worden.
- `/api/cache/status` meldt via `cms.redis_cache.get_status()` dan Redis
  ipv filesystem (al ondersteund).

### 3.2 Risico 1 — RQ-interlock (brekend als we zomaar REDIS_URL zetten)

`cms/background.py:16` koppelt achtergrondtaken aan `bool(REDIS_URL)`: zodra
`REDIS_URL` gezet is, enqueuet `run_in_background` jobs naar `Queue("default")`
in Redis via `cms.tasks.run_background_task`. Er is **geen RQ-worker** in
`deploy/` noch op de VPS → jobs zouden voorgoed in de queue blijven hangen.

**Besluit:** `_use_rq` loskoppelen van `REDIS_URL` naar een eigen env-var
`RQ_URL` (leeg = ThreadPoolExecutor, ongewijzigd gedrag). RQ blijft een
expliciete, aparte keuze.

### 3.3 Risico 2 — `flushall()`-footgun in de OSINT-cache

`cms/redis_cache.py:115` roept bij lege `invalidate()` **`flushall()`** aan.
Als de OSINT-cache met dezelfde Redis-instance/DB deelt, wist iedere
cache-invalidatie álle sessies. De functie is momenteel nergens door routes
aangeroepen (alleen geëxporteerd via `cms/cache.py`), dus het is nu latent.

**Besluit:** voordat Redis live gaat: `flushall()` vervangen door keyspace-
scoped verwijdering van `osint:*` via `scan_iter` + `unlink` (of een apart
Redis-DB/prefix voor de OSINT-cache).

### 3.4 Risico 3 — Redis-uitval mid-flight

Bij boot faalt de app netjes terug naar filesystem (blokkerende `ping()`).
**Tijdens** runtime zonder Redis: Flask-Session `RedisSessionInterface`
`get`/`set` geeft `redis.ConnectionError` in de request → 500s.

**Besluit:** accepteer met beperking + bewaking:
- systemd `Restart=on-failure` + korte `RestartSec` op Redis (restart ~1s);
- health-monitor uitbreiden met een Redis-probe (`redis-cli ping`) die bij
  falen een `user.alert`/`dr_alert_email` uitstuurt;
- optioneel later: app-zijde resilience-wrapper (degradeert tijdelijk naar
  bounded filesystem) — buiten scope van deze stap.

### 3.5 Risico 4 — geheugenbudget

Redis op deze VPS krijgt `maxmemory` + `maxmemory-policy`. Voorstel:
- `maxmemory 256mb`;
- `maxmemory-policy volatile-ttl` (OSINT-cache, TTL 5 min, evictt eerst;
  sessies, sliding 8h+, blijven staan).
- `appendonly yes` + `appendfsync everysec`? TTL-sessies overleven restart dan.
  Afweging: AOF kost disk/IO; sessions 8h TTL hebben geen PV-claim nodig →
  voorstel: **RDB `save ""`-uit** (sessies mogen verlangen op Redis-restart) en
  vertrouw op sliding-sessies + re-login. Keuze nog open.

### 3.6 Risico 5 — migratie = alle sessies weg

Bij de switch loggen alle gebruikers uit (4725 sessies verloren). Roepen in
onderhoudsvenster uitvoeren en communiceren. Niet anders op te lossen dan
sessies omzetten (= extra scope); in-vivo migratie kan in een latere fase als
éénmalig script, mits gewenst.

## 4. Voorgesteld ontwerp (besluiten 2026-09-06)

- **Redis-server lokaal op VPS** via apt (`redis-server` v7.x):
  - `bind 127.0.0.1`, `protected-mode yes`, `requirepass` in
    `/etc/redis/redis.conf` (mode 600) + `REDIS_URL=redis://:pass@127.0.0.1:6379/0`;
  - **persistence**: `appendonly yes` + `appendfsync everysec` (sessies
    overleven Redis-restart; de huidige filesystem-sessies overleven app-
    restarts ook — gedragsbehoud);
  - **memory**: `maxmemory 256mb`, `maxmemory-policy volatile-ttl`
    (OSINT-cache evictt eerst, sessies blijven).
- **`app.py`**: weg via `REDIS_URL` (al ingebouwd). `SESSION_COOKIE`-instellingen
  (HttpOnly/SameSite) blijven zoals in filesystem-route.
- **`background.py`**: `_use_rq` op `RQ_URL` (apart; **uit** bij deze migratie),
  zodat `REDIS_URL` uitsluitend sessies schakelt en achtergrondtaken op
  `ThreadPoolExecutor` blijven. RQ is een eigen latere track met rqworker-unit.
- **`redis_cache.py`**: `flushall` → keyspace-clean van `osint:*`
  (`scan_iter` + `unlink`), zodat cache-invalidatie nooit sessies kan raken.
- **Health-monitor**: Redis-probe + `user.alert`/`dr_alert_email` bij falen.
- **Tests**: `tests/test_session_cache.py` uitbreiden met reducerende tests
  (RQ-decouple, flushall-fix), plus regressie op sessie-admin-endpoints.
- **Docs/ADR**: deze keuzes vastleggen als vervolg op ADR-0004 (optie C).

## 5. Migratiefases

1. **Fase A (code — deze PR)**: RQ-decouple, flushall-fix, tests.
2. **Fase B (VPS-install) — GEREED 2026-09-06**: Redis 8.0.5 (apt) live op de
   VPS met: `bind 127.0.0.1 -::1`, `protected-mode yes`, `requirepass`
   (root-only `/etc/redis/redis-pass`, mode 600; compleet `REDIS_URL` in
   root-only `/etc/redis/redis-url`), `appendonly yes` + `appendfsync everysec`,
   `maxmemory 256mb`, `maxmemory-policy volatile-ttl`; override
   `Restart=on-failure` + `RestartSec=2s`; `vm.overcommit_memory=1`.
   **Verificatie:** `ping` met auth = PONG, zonder auth = NOAUTH, luistert
   alleen op loopback, AOF-actief. **`REDIS_URL` staat NIET in `.env`**;
   osint-dashboard draait onveranderd op filesystem-sessies (health 200).
3. **Fase C (verificatie)**: app herstarten zonder REDIS_URL (geen gedragswijziging);
   health-monitor-probe testen. *(rest nog)*
4. **Fase D (switch, onderhoudsvenster)**: `REDIS_URL` in `.env`,
   app herstart, sessie-login E2E, admin-sessie-UI check, OSINT-cache-check.
5. **Rollback** indien nodig: `REDIS_URL` unsetten, app herstart →
   filesystem route (sessies van de switch zijn weg; re-login).

## 6. Open vragen

Alle vier kernvragen zijn besloten (2026-09-06):

| Vraag | Besluit |
|---|---|
| Persistence | AOF aan (`everysec`) |
| Netwerk | `127.0.0.1:6379` + `requirepass` |
| RQ | uit (alleen `RQ_URL`-decouple; rqworker-unit is eigen track) |
| Sessieverlies bij switch | accepteren (geen migratie-script) |

Resterend als eigen later trajecten: RQ-worker (rqworker-unit + supervisie),
eventueel app-zijde resilience-wrapper voor runtime-Redis-uitval, en een
in-vivo-sessiemigratie-script mocht dat toch gewenst worden.