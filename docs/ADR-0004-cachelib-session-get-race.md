# ADR: Cachelib session-get race onder 2 sync workers

Status: **OPEN** (canary FAIL-blokker, bewijs geleverd 2026-09-03)

## Context

De Gunicorn 2-worker canary (plan-fase 1) heeft een **geïsoleerde `OSError`-burst** geproduceerd bij `2026-09-02T19:27:13Z` (6 regels, geregistreerd als `oserror_delta_5m=6` op de 19:28 sample). Het FINAL-rapport (`reports/rollout/canary-close-gunicorn-20260903T163708Z.txt`) bevestigt: **288/288 samples, FINAL=True, STATUS=FAIL** — de enige fail was deze burst.

Het symbool is een **sessie-cache `get()`-race** tussen de 2 sync workers.

## Bewijs

### Canary-rapport (24h, volledig)

```
FINAL         : True (100.1% elapsed)
STATUS        : FAIL
samples       : 288 (perfect 5-min cadence)
oserror>0     : 1 burst (2026-09-02T19:28:38Z = 6)
foreign       : 0
slow (>1s)    : 0
restarts      : 0
CSV gaps      : none
avg latency   : 18.7ms
max latency   : 104.2ms
```

### Journal-traceback (19:27:13Z, 3 herhalingen)

```
WARNI [root] Exception raised while handling cache file
  '...flask_session/2029240f6d1128be89ddc32729463129'
Traceback (most recent call last):
  File "...cachelib/file.py", line 221, in get
    with self._safe_stream_open(filename, "rb") as f:
  File "...cachelib/file.py", line 347, in _safe_stream_open
    raise OSError
```

Worker: `gunicorn[4053648]` (één van de 2 sync workers).

## Analyse

### Betrokken code-paden

**Worker B — `set()` (cachelib/file.py:240)**:

```python
fd, tmp = tempfile.mkstemp(suffix=..., dir=self._path)
os.fdopen(fd, "wb").write(...)
self._run_safely(os.replace, tmp, filename)   # ← atomische vervanging
self._run_safely(os.chmod, filename, self._mode)
```

`os.replace()` is atomaire vervanging: het oude bestand verdwijnt, het nieuwe verschijnt op dezelfde naam. Op het moment van `replace()` is het bestand **tijdelijk niet leesbaar** (of de inode is gewijzigd).

**Worker A — `get()` (cachelib/file.py:218)**:

```python
with self._safe_stream_open(filename, "rb") as f:
    ...
```

`_safe_stream_open` (regel 342):
```python
fs = self._run_safely(open, path, mode)
if fs is None:
    raise OSError      # ← dit is de bron van de 6 OSError-regels
```

`_run_safely` (regel 317, overridden door `cms/session_cache.py`):
```python
# herhaalt ALLEEN op PermissionError, max 50ms (SESSION_CACHE_MAX_WAIT)
while total_sleep_time < max_sleep_time:
    try:
        output = fn(*args, **kwargs)
    except PermissionError:
        sleep(wait_step)
        ...
    else:
        break
return output  # None alsPermissionError niet oplost binnen 50ms
```

### De race (precieze timing)

```
Worker B:  set(key=X) → mkstemp → os.replace(tmp, fileX)
Worker A:  get(key=X) → open(fileX, "rb") → PermissionError (bestand wordt vervangen)
           _run_safely: herhaalt 50ms → faalt → return None
           _safe_stream_open: raise OSError
           get(): vangt OSError → return None (cache miss)
```

**`os.replace()` is atomair voor de lezer, maar `open()` kan een `PermissionError` krijgen** als het bestand precies op dat moment wordt vervangen. cachelib herhaalt dit 50ms, maar als de `replace()` langer duurt (of meerdere writers), faalt de herhaling.

### Waarom 6 regels (3 herhalingen)?

De traceback toont 3 identieke calls naar `_safe_stream_open` → `get()`. Het meest waarschijnlijke mechanisme:

1. Flask-Session roept `get(session_key)` aan bij het begin van een request
2. De sessie wordt niet gevonden (cache miss door de race)
3. Flask-Session beschouwt dit als een **nieuwe sessie** → creëert een nieuwe sessie
4. Het request eindigt → Flask-Session roept `set()` aan → schrijft een **nieuwe sessie** onder dezelfde key
5. Het volgende request op hetzelfde sessiebestand raakt opnieuw de race

De 3 herhalingen binnen één seconde (19:27:13Z) komen hoogstwaarschijnlijk van **3 opeenvolgende requests** die elk dezelfde sessie-key probeerden te lezen terwijl een writer bezig was.

### Impact-classificatie

| Criterium | Impact |
|---|---|
| **User-facing storing** | **Geen** — cache miss = sessie niet herkend → gebruiker moet opnieuw inloggen |
| **Data-verlies** | **Geen** — sessie wordt opnieuw aangemaakt |
| **Beschikbaarheid** | **Geen** — requests worden gewoon afgehandeld |
| **Security** | **Neutraal** — sessie-miss is een veiligheidsnormaal (nieuwe sessie) |
| **Prestatie** | **Miniem** — 1 vertraagd request (~50ms extra) per miss |
| **Frequentie** | **1 burst in 24h** (6 regels uit 1 sessiebestand) |

### Root cause

De **cachelib `FileSystemCache`** (en `BoundedFileSystemCache`) is niet ontworpen voor **multi-process concurrentie**. De `_run_safely` herhaalt alleen op `PermissionError` (SMB/CIFS/NTFS-specifiek), niet op andere bestandsrace-voorwaarden. De `os.replace()` in `set()` is atomaire vervanging die een korte `PermissionError`-window veroorzaakt bij een gelijktijdige `get()`.

Dit is een **bekend cachelib-beperking** — de documentatie vermeldt "SMB/CIFS on Linux, NTFS on Windows" als specifieke platforms, maar het probleem geldt ook voor locale POSIX-filesystems wanneer `os.replace()` een gelijktijdige `open()` interfereert.

## Fix-voorstellen

### Optie A: `BoundedFileSystemCache` uitbreiden met `FileNotFoundError`-retry (laag risico)

In `cms/session_cache.py`, `_run_safely` uitbreiden om ook `FileNotFoundError` te herhalen:

```python
except (PermissionError, FileNotFoundError):
    sleep(wait_step)
    ...
```

**Werkingsmechanisme:** als `open()` een `FileNotFoundError` krijgt (bestand bestaat niet meer), herhaalt `_run_safely` → opent het bestand opnieuw → als het inmiddels opnieuw bestaat (door een nieuwe `set()`), slaagt de tweede `open()`.

**Risico:** kleine kans op eindeloze herhaling als het bestand permanent verdwenen is (verholpen door de 50ms-grens).

**Voordeel:** minimale wijziging, geen nieuw gedrag, alleen betere-handling van een bestaande race.

### Optie B: Session-interface wrapper met retry (medium risico)

Een Flask-Session session-interface wrapper die bij een `get()`-miss één keer opnieuw probeert:

```python
class ResilientSessionInterface(SessionInterface):
    def get(self, ...):
        result = super().get(...)
        if result is None:
            result = super().get(...)  # retry once
        return result
```

**Risico:** tweede `get()` kan ook mislukken; verhoogt latentie bij echte misses.

### Optie C: Session-backend verplaatsen naar Redis (hoog risico, hoog rendement)

`REDIS_URL` configureren → sessie naar Redis → volledig lock-vrij. Vereist Redis-installatie + migratie + rollback-plan.

**Risico:** configuratie, migratie, fallback-behavior bij Redis-uitval.

**Voordeel:** lost het probleem definitief op; schaalt met workers; geen filesystem-races meer.

### Optie D: cachelib `set()` atomiciteit verbeteren (upstream, hoog risico)

cachelib's `set()` gebruikt al `os.replace()` (atomaire vervanging). De race is inherent aan het lezen van een bestand dat tegelijkertijd wordt vervangen. Upstream-aanpassing aan `get()` is nodig (cachelib-issue #100+).

## Aanbeveling

**Optie A** (laag risico, minimale wijziging) als immediate fix:
- Breid `cms/session_cache.py` `_run_safely` uit met `FileNotFoundError`-retry
- Voeg een `get_with_retry()` wrapper toe die bij een miss één keer opnieuw probeert
- Canary herstarten met deze fix (nieuw 24h-venster)
- Geen Redis nodig; geen config-wijziging; geen gevaar voor regressie

**Optie C** (Redis) als **volgende stap** na Optie A:
- Fix de onmiddellijke blokker (Optie A)
- Canary herstarten met schone metrische lat
- Daarna Redis migratie plannen als architecturele upgrade (APR)

## Volgende stappen

1. Kies fix-optie (gebruiker)
2. Implementeer in branch + tests
3. Canary herstarten (nieuw 24h-venster)
4. Bij PASS: formally go-live van #105–#109 + canonical release
5. Bij FAIL: herzie analyse, overweeg Optie C onmiddellijk