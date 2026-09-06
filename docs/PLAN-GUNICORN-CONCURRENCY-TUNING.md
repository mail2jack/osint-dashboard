# Plan: Gunicorn-concurrency en production performance

**Doel:** onafhankelijke beoordeling door ChatGPT, security specialist en
performance reviewer  
**Status:** afgerond — besluit: productie blijft op **2 sync workers**  
**Systeem:** Iveras OSINT Dashboard

## 0. Conclusie (besluit 2026-09-06)

De twee-worker canary (24 uur, venster 2026-09-04T16:43:30Z → 2026-09-05T16:43:30Z,
commit `8d571d4`) is volledig doorlopen en gesloten met **STATUS=PASS**
(report `canary-close-gunicorn-20260906T141655Z.txt`):

- 288/288 health-samples, geen CSV-gap;
- `NRestarts=0` binnen het venster;
- 0 OSErrors binnen het venster (journald geteld); één uitsluitende
  teardown-burst van de oude worker vlak vóór window-open werd per
  journald-crosscheck geïdentificeerd en als `oserr_excluded_bursts`
  separaat gerapporteerd (border-artefact, geen eigen fout van de 2-worker
  configuratie);
- geen backlogbeperking zichtbaar.

**Besluit:** productie blijft op `--workers 2 --worker-class sync --threads 1`.
2 workers hebben de volledige meetperiode zonder failures, restarts of gaps
gedragen. Opschalen naar 4 workers (fase 2) staat open bij aantoonbare winst:
p95-verslechtering, sample-ratio-daling of een burst in de metingen — dit bewijs
is er op 2026-09-06 niet.

De configuratie is als canoniek vastgelegd in  
`deploy/osint-dashboard-gunicorn2.override.conf` (live drop-in op de VPS) en de
twee-worker-rollout is operationeel (go-live commit `8d571d4`, deploy naar VPS
2026-09-06 14:49:30Z). De health-monitor (elke 5 min) blijft het vangnet.

## 1. Beslispunt

De actieve productie-unit draait momenteel met:

```text
--workers 1 --timeout 120
```

De repository bevat twee verschillende defaults:

```text
install.sh: --workers 4 --threads 2 --timeout 120 --keep-alive 60 --max-requests 1000
start.sh:   --workers 4 --timeout 120
```

Belangrijk: bij Gunicorn schakelt `--threads 2` de workerclass feitelijk over
naar `gthread`. Dit is dus niet hetzelfde als vier sync workers. Vier expliciete
sync workers zijn:

```text
--workers 4 --worker-class sync --threads 1
```

Er is geen gevonden ADR of commit die de afwijking naar één worker formeel
onderbouwt. Eén worker is tijdens de session-store-wedge als oorzaakversterker
vastgesteld: één geblokkeerde request hield de hele webrequest-capaciteit op.

**Beslissing gevraagd (oorspronkelijk):** moet productie gecontroleerd van één
naar twee of vier workers, en moeten daarnaast timeout-, keep-alive- en
recyclingwaarden worden aangepast?  
**Genomen beslissing (2026-09-06):** bij **2 sync workers** blijven, timeout 120s
behouden; fase 2 (4 workers) alleen op basis van dit plan bij aantoonbare winst.

## 2. Huidige uitgangssituatie

### Productie

- Gunicorn sync worker class.
- Actieve unit: één worker, timeout 120 seconden.
- App bindt op `0.0.0.0:5000`; nginx staat ervoor.
- PostgreSQL als productiedatabase met RLS en tenantcontext.
- Filesystem sessions via bounded Cachelib backend.
- Full health refresh draait buiten Gunicorn via één systemd oneshot/timer.
- SpiderFoot draait als afzonderlijke service op localhost.
- Application access-log is eerder leeg bevonden; nginx access-log bestaat wel.
- Productie-venv heeft eerder corrupte package-metadatawaarschuwingen gemeld.

### Bekende gedragspunten

- Externe OSINT- en providercalls kunnen seconden duren.
- Sommige healthchecks doen externe checks of SpiderFoot-pings.
- In-process state en caches zijn niet automatisch gedeeld tussen workers.
- Background tasks gebruiken afhankelijk van configuratie RQ of
  `ThreadPoolExecutor`.
- Filesystem-sessionfiles worden door meerdere processen beschreven zodra meer
  workers actief zijn; de bounded backend en ownership-guard zijn daarom
  randvoorwaarden.

## 3. Opties

### Optie A: één worker behouden

**Voordelen**

- Kleinste operationele wijziging.
- Geen extra proces- of geheugendruk.
- In-process caches en globale state blijven eenvoudig.
- Minder gelijktijdige database- en filesystemschrijvers.

**Nadelen**

- Eén trage route blokkeert alle webrequests.
- Externe calls hebben direct impact op gebruikers en healthchecks.
- Slechte foutisolatie.
- Slechte schaalbaarheid bij meerdere gebruikers.
- De eerdere session-store-wedge had maximale blast radius.

**Beoordeling:** alleen verdedigbaar voor zeer lage traffic of als alle
langlopende acties gegarandeerd buiten de request-worker plaatsvinden. Dat is
op dit moment niet overal het geval.

### Optie B: twee sync workers

**Voordelen**

- Eén geblokkeerde worker laat de tweede worker requests afhandelen.
- Beperkte memory- en database-impact.
- Kleine, goed omkeerbare productie-stap.
- Goede eerste canary voor concurrency.

**Nadelen**

- Een volledige blokkade kan nog steeds beide workers raken.
- In-process caches bestaan tweemaal en kunnen verschillende waarden hebben.
- Background threads of startuplogica kunnen dubbel worden uitgevoerd.
- Filesystem-session concurrency neemt toe.
- Geen oplossing voor structureel trage endpoints.

**Beoordeling:** aanbevolen eerste productiestap.

### Optie C: vier expliciete sync workers

Configuratie:

```text
--workers 4 --worker-class sync --threads 1
```

**Voordelen**

- Komt overeen met de bestaande installer/startscript-defaults.
- Meer requestisolatie en capaciteit.
- Beter voor gelijktijdige gebruikers en korte requests.

**Nadelen**

- Ongeveer viermaal procesgeheugen voor de appbasis.
- Meer PostgreSQL-connecties en mogelijke lockdruk.
- Meer concurrency-races in filesystem sessions en globale state.
- Verdubbeling van eventuele per-worker startup/backgroundlogica.
- Kan problemen maskeren in plaats van de trage route te repareren.

**Beoordeling:** alleen na een succesvolle twee-worker-canary en expliciete
memory-/DB-meting.

### Optie D: threaded workers (`gthread`)

**Voordelen**

- Meer concurrency binnen minder processen.
- Kan geschikt zijn voor I/O-bound externe calls.
- Lager processgeheugen dan veel sync workers.

**Nadelen**

- Thread-safety van Flask extensions, SQLAlchemygebruik, caches en globale
  serviceobjecten moet aantoonbaar zijn.
- Eén fout in gedeelde state kan meerdere requests beïnvloeden.
- Debugging en incidentanalyse worden complexer.
- Filesystem-sessionrace blijft bestaan.

**Beoordeling:** niet de eerste stap; eerst expliciete sync workers testen.

### Optie E: ASGI/async of aparte web-/jobprocessen

**Voordelen**

- Betere architectuur voor veel I/O-bound werk.
- Langlopende OSINT-acties kunnen volledig uit webrequests verdwijnen.

**Nadelen**

- Grote wijziging met hogere regressiekans.
- Flask-extensions en blocking libraries moeten worden aangepast.
- Niet geschikt als kleine operationele tuning.

**Beoordeling:** langere-termijnarchitectuur, buiten deze wijziging.

## 4. Parameters die apart beoordeeld moeten worden

### `workers`

Startvoorstel: één naar twee. Vier alleen na metingen.

### `worker-class`

Expliciet behouden op `sync` voor de eerste test. Niet tegelijk veranderen naar
`gthread`; anders zijn resultaten niet herleidbaar. Let erop dat
`--threads > 1` Gunicorn naar `gthread` laat overschakelen.

### `timeout`

De huidige waarde is 120 seconden. Dit is geen latencydoel; het bepaalt vooral
wanneer Gunicorn een vastgelopen worker afbreekt.

**Mogelijke waarden:**

- 60s: sneller herstel, maar risico dat legitieme langlopende requests worden
  afgebroken.
- 120s: huidige waarde, ruime fouttolerantie maar lange blokkade.
- 30s: alleen als alle zware acties gegarandeerd async zijn.

Niet tegelijk met worker-count wijzigen zonder afzonderlijke meting.

### `keep-alive`

Bij sync workers is Gunicorn `keep-alive` niet de relevante
concurrencyparameter; Gunicorn negeert deze optie voor sync workers. Laat
`keep-alive` daarom volledig buiten de eerste canary. Nginx keep-alive kan
afzonderlijk worden beoordeeld.

### `max-requests` en jitter

Worker recycling kan memory leaks beperken, maar veroorzaakt gecontroleerde
restarts en mogelijk korte capaciteitsschommelingen. Gebruik een
`max-requests-jitter` om gelijktijdige workerrestarts te vermijden als Gunicorn
dat ondersteunt.

### `threads`

Niet toevoegen in de eerste canary. `--workers 4 --threads 2` is een andere
concurrencyarchitectuur dan vier sync workers en moet apart worden beoordeeld.

## 5. Security- en correctness-randvoorwaarden

Voor verhoging moeten minimaal deze punten gecontroleerd zijn:

- Elke worker draait als `osint`, niet als root.
- Geen worker krijgt brede filesystemrechten.
- `flask_session` blijft `osint`-owned en mode 0600.
- Bounded Cachelib blijft actief.
- Er is geen per-worker health-refresh-thread.
- De health-refresh producer blijft uitsluitend de systemd oneshot/timer.
- RQ/ThreadPool background jobs zijn idempotent en dubbelstartveilig.
- PostgreSQL RLS blijft actief in iedere worker.
- `app.tenant_id` en `app.bypass_rls` worden per request correct gezet en
  vrijgegeven.
- In-process caches worden niet als autoritatieve securitystate gebruikt.
- Sessie-, login-, CSRF- en rate-limitgedrag blijft correct.
- Geen nieuwe worker-startup voert ongewenste migrations of seedacties uit.
- Nginx blijft de enige publiek bedoelde ingang.

### Verplichte connection-budgetcheck

De SQLAlchemy-app pool bestaat per Gunicorn-proces. Voor de canary moeten de
werkelijke poolinstellingen en PostgreSQL-reserves worden gemeten, niet alleen
de theoretische worker-count.

Leg vast:

- `pool_size`, `max_overflow`, `pool_timeout` en eventuele SQLite-fallback;
- `max_connections`;
- bestaande app-, timer-, RQ-, SpiderFoot- en operatorconnecties;
- piek en gemiddelde `pg_stat_activity` tijdens de baseline.

Gebruik als bovengrens minimaal:

```text
max_app_connections = workers * (pool_size + max_overflow)
```

Tel daar expliciet alle andere processen bij op en houd een operationele
reserve over voor migrations, backups, DR-verificatie en operators. Abort de
canary als de gemeten reserve onvoldoende is of als PostgreSQL tijdens de test
connection exhaustion, lockdruk of wachtrijen laat zien.

### Verplichte taak- en startupinventaris

Inventariseer vóór de canary alle:

- `ThreadPoolExecutor`-instanties;
- RQ queues/workers;
- startup hooks en daemon threads;
- telemetry-, health- en cleanup-taken;
- scheduled systemd services/timers;
- in-process singleton services.

Per item moet vaststaan of het per Gunicorn-worker wordt gestart. Dubbele
uitvoering moet idempotent zijn of buiten Gunicorn worden geplaatst.

### Verplichte same-session-test

Test met twee afzonderlijke processen die dezelfde ingelogde sessie gebruiken:

- gelijktijdige POSTs met geldige CSRF-vernieuwing;
- gelijktijdige session reads/writes;
- controle op verloren updates, onverwachte logout en nieuwe session-files;
- ownership/permissies vóór en na de test;
- geen OSError of file-corruption.

De bestaande ownership-guard bewijst alleen detectie/remediatie en niet dat
multiworker session access race-free is.

### Publieke exposure

Controleer vóór de canary vanaf een externe host dat TCP/5000 niet publiek
bereikbaar is. Nginx moet de enige externe ingang blijven. Het wijzigen naar
`127.0.0.1` of een Unix socket is een afzonderlijk hardening-item en valt niet
in de eerste worker-canary.

## 6. Testplan vóór productie

### Functioneel

- Login/logout en 2FA met meerdere gelijktijdige clients.
- Session-cookie replay en verlopen sessies.
- Twee tenants met positieve en negatieve authorizationtests.
- CRUD voor cases, subjects, findings, documents en clients.
- Concurrente case- en investigation-numbering.
- Uploads, exports, screenshots en PDF-generatie.
- SpiderFoot-start, polling en foutafhandeling.
- Background tasks met en zonder Redis/RQ.
- Health-summary, quick-health en API-v1 health gelijktijdig.

### Security

- Cross-tenant requests met twee gelijktijdige workers.
- RLS-tests met gewone tenantrol en bypass-context.
- CSRF-, SSRF- en uploadtests.
- Controle dat geen credentials in logs verschijnen.
- Controle van filesystem ownership en permissies.
- Controle van publiek bereikbare poorten.

### Load/concurrency

Test minimaal drie configuraties onder dezelfde workload:

1. huidige baseline: 1 sync worker;
2. canary: 2 expliciete sync workers (`--worker-class sync --threads 1`);
3. optioneel, later: 4 expliciete sync workers (`--worker-class sync --threads 1`).

Gebruik synthetische data en vaste workloadprofielen:

- korte liveness requests;
- quick-health requests;
- health-summary tijdens full refresh;
- normale dashboardrequests;
- één opzettelijk trage externe dependency;
- gelijktijdige CRUD- en background requests.

## 7. Meetcriteria

Per configuratie vastleggen:

- request p50/p95/p99/max;
- time-to-first-response en queue-wachttijd indien beschikbaar;
- percentage requests boven 250 ms, 1s, 5s en 10s;
- HTTP 4xx/5xx/499;
- Gunicorn worker timeouts/restarts;
- CPU, RSS, swap en load average;
- PostgreSQL actieve connecties, locks, waits en queryduur;
- connection-budgetreserve versus `max_connections`;
- Redis/RQ queue depth en jobduur indien actief;
- session-store file count, ownership en permission violations;
- OSError/PermissionError in journald;
- full health duration en per-subcheck timings;
- health snapshot age/stale/failure status;
- nginx upstream connect/read timeouts.

### Voorstel voor voorlopige SLO’s

Deze waarden zijn bespreekpunten, geen vastgestelde normen:

De definitieve SLO’s mogen pas worden vastgesteld nadat de huidige gezonde
baseline opnieuw is gemeten. Een p95-grens van 100 ms voor liveness is niet
automatisch gerechtvaardigd; leg per endpoint baseline, workload, p95/p99 en
toegestane afwijking vast.

| Endpoint/type | Waarschuwing | Hard failure |
|---|---:|---:|
| `/api/v1/health` | p95 >100 ms | timeout of p99 >1s |
| `/health?quick=1` | p95 >1s | timeout/5s-samples |
| `/cms/api/health-summary` | stale >10 min | geen refresh >30 min |
| Normale korte requests | p95 >1s | p99 >5s |
| Worker | restart/timeout | elke onverwachte timeout |
| Session-store | foreign file | OSError-storm of wedge |

## 8. Gefaseerde rollout

### Fase 0: documentatie en baseline

1. Leg de actieve unit en repo-default naast elkaar vast.
2. Kies één workerconfiguratie voor de eerste canary.
3. Maak backup en rollback-SHA.
4. Leg baseline A/B/C vast voor latency, resources, DB en logs.
5. Verifieer dat de health-refresh timer en snapshotproducer correct werken.

### Fase 1: twee-worker canary — VOLTOOID (2026-09-04 → 2026-09-05)

1. Open onderhoudsvenster en merge-freeze.
2. Draai preflight/dry-run.
3. Wijzig uitsluitend naar `--workers 2 --worker-class sync --threads 1`.
4. Herstart gecontroleerd.
5. Controleer readiness, login, sessions, RLS, health en background jobs.
6. Observeer minimaal **24 uur ononderbroken**.
7. Een VPS-herstart, collectoruitval of CSV-gat maakt de uitkomst
   inconclusief; start vanaf herstel een nieuw volledig 24-uursvenster.

**Uitkomst (2026-09-06):** PASS — zie de conclusie in §0. Geen rolback nodig;
productie blijft op deze configuratie.

**Canary-close/check-timers (runbook).** Het venster wordt formeel afgesloten
door een eenmalige systemd OnCalendar-timer (deploy/osint-canary-close.timer,
draait scripts/canary_close.py en schrijft het FINAL-rapport), gevolgd door
deploy/osint-canary-check.timer die het rapport verifieert en `osint-canary-check`
logt. Voor elk nieuw venster:

1. Zet WINDOW_OPEN_ISO in scripts/canary_close.py op het venster-startmoment
   (ActiveEnterTimestamp van de gunicorn-restart);
2. Zet OnCalendar in deploy/osint-canary-close.timer op window-close + ~3 min
   en osint-canary-check.timer op + ~3 min daarna;
3. Draai scripts/install_canary_close.sh als root.

**Re-arm gotcha (geverifieerd 2026-09-04).** Een eenmalige OnCalendar-timer die
al een keer gevuurd heeft, herberekent de next-elapse NIET bij `systemctl
enable --now` of `systemctl start`: de timer blijft ActiveState=active maar
NextElapseUSecRealtime= blijft leeg en vuurt nooit meer. Alleen `systemctl
restart <timer>` forceert herberekening. install_canary_close.sh gebruikt daarom
`restart` en verifieert hard dat NextElapseUSecRealtime != leeg (fails met
herstel-instructie als de timer niet armed is). Controleer na wijziging altijd
dat beide timers een realistische NextElapse tonen.


### Fase 2: vier workers, alleen indien gerechtvaardigd

1. Vergelijk fase 1 met baseline en controleer dat het 24-uursvenster volledig
   en zonder CSV-gat is.
2. Controleer geheugen- en PostgreSQL-reserves.
3. Voer dezelfde tests en rollout uit met vier sync workers.
4. Behoud vier alleen als p95, foutpercentages en resourcegebruik verbeteren
   zonder security-/correctnessregressies.

### Fase 3: aanvullende parameters

Pas daarna afzonderlijk beoordelen:

- timeout;
- keep-alive;
- max-requests;
- threads/gthread;
- job queuecapaciteit.

Wijzig maximaal één parameterfamilie per venster.

## 9. Rollback

Rollback naar één worker als één van deze situaties optreedt:

- worker timeouts of 499’s nemen toe;
- p95/p99 verslechtert substantieel;
- memory pressure of swap ontstaat;
- PostgreSQL locks/connecties lopen op;
- cross-tenant/RLS-test faalt;
- session ownership/OSErrors terugkomen;
- background jobs dubbel of niet-idempotent blijken;
- health-refresh of snapshots falen.

Rollback bestaat uit terugzetten van de vorige systemd-unit/configuratie,
daemon-reload, service restart en dezelfde post-checks. Geen Alembic-downgrade.

## 10. Vragen voor ChatGPT/security specialist

1. Is twee workers de juiste eerste canary voor deze Flask-app?
2. Is vier workers verantwoord op de huidige VPS qua RSS, swap en PostgreSQL?
3. Zijn er bekende globale caches, singletons of background threads die per
   worker onveilig dupliceren?
4. Is filesystem-based Flask-Session met meerdere workers voldoende veilig met
   de bounded backend en ownership-guard?
5. Moet de session-store eerst naar Redis voordat workers worden verhoogd?
6. Welke Gunicorn-timeout past bij de langste legitieme request?
7. Welke health-latencygrens is realistisch zonder queueproblemen te maskeren?
8. Moet `/health?quick=1` SpiderFoot nog controleren, of alleen liveness/DB?
9. Welke PostgreSQL pool size en `max_connections` zijn passend per worker?
10. Hoe meten we echte request-queue-wachttijd in Gunicorn/nginx?
11. Is `max-requests` nodig, en welke recyclewaarden zijn veilig?
12. Moet de dashboard-unit tegelijk systemd-hardening krijgen?
13. Moet Gunicorn op `127.0.0.1` binden in plaats van `0.0.0.0`?
14. Is de workerkeuze een operationele standaard of een formele product-SLO?
15. Welke testduur is voldoende om worker-count als stabiel te verklaren?

## 11. Voorlopig advies

- Verhoog niet tegelijk workers, worker-class, timeout en keep-alive.
- Begin met **2 expliciete sync workers** als gecontroleerde canary:
  `--workers 2 --worker-class sync --threads 1`.
- Behoud timeout 120s in de eerste canary voor vergelijkbaarheid.
- Laat `keep-alive` buiten de sync-worker-canary; Gunicorn gebruikt die optie
  niet voor sync workers.
- Voeg geen threads toe in dezelfde wijziging.
- Meet queue-, memory-, PostgreSQL-, RLS-, session- en backgroundgedrag.
- Ga alleen naar 4 workers bij aantoonbare winst en voldoende reserves.
- Behandel workerverhoging als aparte availability/performance-PR.
- Installeer of activeer de DR-verifier-timer niet als onderdeel van dit plan.
- Start na elke productievariant een nieuw volledig monitoringvenster; een gat
  maakt de uitkomst inconclusief.

**Actie (2026-09-06):** dit advies is uitgevoerd en de canary is gehald met het
besluit om op 2 workers te blijven — zie de conclusie in §0.
