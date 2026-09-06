# Productiediagnose trage quick-health

**Datum:** 2026-08-31  
**Scope:** read-only diagnose op `root@joost.iveras.com`  
**Code:** `a3704918edce5bc00eb65b2c933f111207e19059`  
**Wijzigingen uitgevoerd:** geen configuratie-, code-, service- of datamutaties

## Samenvatting

De session-store wedge is in het onderzochte venster niet teruggekeerd:

- 0 OSErrors;
- 0 foreign session-files;
- 0 worker-restarts;
- geen CSV-gat in het monitoringvenster.

De applicatie is desondanks niet stabiel genoeg verklaard. De trage
`/health?quick=1`-metingen worden primair veroorzaakt door request-queueing in
de enige Gunicorn sync-worker. De quick-health-route zelf heeft geen structureel
trage DB-, filesystem- of lokale SpiderFoot-basiskosten, maar wacht wanneer een
andere request dezelfde worker bezet houdt.

## Bewijs

### Monitoringvenster

Periode `2026-08-30T08:44:54Z` tot `2026-08-31T07:00:00Z`:

- 260 health-samples;
- geen CSV-gaten;
- 169 samples boven 1 seconde;
- 27 samples rond de 5-seconden-timeout;
- health p50 `1.575s`, p95 `5.002s`, maximum `5.003s`;
- OSError-delta totaal `0`;
- maximale foreign ownership `0`;
- worker-restarts `0`.

### Afzonderlijke subchecks

Twaalf read-only metingen in de applicatiecontext gaven ongeveer:

| Subcheck | Mediaan | Maximum |
|---|---:|---:|
| PostgreSQL `SELECT 1` | 1.6 ms | 2.1 ms |
| `pg_stat_activity` query | 2.1 ms | 104 ms |
| Directe SpiderFoot-ping | 68 ms | 118 ms |
| Cache-status | 0.02 ms | 7.2 ms |
| Session-directory `stat` | 0.03 ms | 0.05 ms |
| Session-directory `listdir` | 3.7 ms | 4.7 ms |
| Disk/filesystem | 0.06 ms | 0.1 ms |
| Worker-process snapshot | 31 ms | 51 ms |
| Established worker sockets | 8 ms | 10 ms |

Er was geen langdurige PostgreSQL-wait of lock zichtbaar. Querytekst en
credentials zijn niet opgeslagen.

### Request-queue

Gelijktijdige probes met één sync-worker lieten zien:

- snelle requests worden nog afgehandeld;
- andere requests wachten en vallen na 10 seconden uit;
- zelfs `/api/v1/health`, dat zelf ongeveer 7-10 ms kost, time-out wanneer het
  achter een trage request in de worker-queue staat.

Dit is request-queue-latency, geen directe `/api/v1/health`-latency.

## Wat `quick=1` werkelijk overslaat

In `cms/health_utils.py` staat alle volgende logica onder `if not quick:`:

- RDW;
- Kadaster;
- HIBP;
- Overheid.io;
- Brave-key-check;
- Tor-check.

`quick=1` slaat echter **niet** over:

- PostgreSQL `SELECT 1`;
- `check_spiderfoot_health()` en dus een lokale SpiderFoot-ping;
- `Setting`-reads en de SpiderFoot-statusschrijfacties/commit van die functie;
- cache-status;
- disk- en memory-metrics;
- een Redis-ping als `REDIS_URL` is geconfigureerd.

Daarnaast gebruikt `/cms/api/health-summary` niet de quick-route. Bij een cache
miss roept `_get_cached_health()` `check_external_services()` aan zonder
`quick=True`. De cache-TTL is 300 seconden. De nginx-log laat bijvoorbeeld
health-summary-requests zien op `14:18:47`, `14:23:47`, `14:28:50` en
`14:33:43` UTC, dus vrijwel exact op de cache-TTL.

Daarmee kan een dashboard-poll periodiek de volledige externe healthcheck in de
enige worker uitvoeren. Een gelijktijdige quick-health-probe wacht daarachter.

## Waarschijnlijke oorzaak

De meest waarschijnlijke keten is:

1. Het dashboard vraagt ongeveer iedere vijf minuten `/cms/api/health-summary`.
2. Na het verlopen van de 300-seconden-cache wordt een volledige healthcheck
   uitgevoerd.
3. Die check bevat jitter en externe calls met timeouts van maximaal vijf
   seconden per call.
4. De route draait in de enige Gunicorn sync-worker.
5. `/health?quick=1` en andere requests wachten achter deze full-health-request.
6. De gemeten latency verschijnt daardoor als 1-5 seconden of als een
   10-seconden client-timeout.

De oorspronkelijke session-wedge is niet nodig om dit gedrag te verklaren.
Tijdens de diagnose waren de directe DB/filesystem/SpiderFoot-subchecks snel en
bleef de session-store ownership schoon. De OSErrors die rond de diagnose
werden gelogd kwamen van de bekende intermitterende sessiefile, maar de
bounded backend maakte die accesses snel en ze verklaren niet de brede
health-latencyverdeling.

## Minimale fixstrategie

De voorkeursrichting is klein en gescheiden:

1. Definieer een echte cheap/readiness healthcheck voor probes. Die doet alleen
   begrensde lokale checks, zonder externe HTTP-calls, zonder SpiderFoot-ping
   en zonder database-writes.
2. Laat `/health?quick=1` uitsluitend deze cheap-check gebruiken.
3. Houd volledige externe healthdiagnose buiten de request path, bijvoorbeeld
   via een achtergrondtaak of een afzonderlijke expliciete healthdiagnose-route
   met eigen timeout/budget.
4. Voorkom dat `/cms/api/health-summary` een volledige externe check in de
   request-worker uitvoert. Gebruik een vooraf bijgewerkte cache of laat de
   UI expliciet een non-blocking/diagnostische status ophalen.
5. Laat de session-store bounded backend en ownership-guard ongewijzigd.

Een nog kleinere tussenstap is `health_summary()` laten werken met
`quick=True`, maar dat maakt de bestaande quick-route nog steeds afhankelijk
van SpiderFoot en de status-commit. Dit is daarom alleen een tijdelijke
mitigatie, niet de gewenste eindarchitectuur.

## Regressietests

De fix moet minimaal aantonen:

- `check_external_services(quick=True)` voert geen RDW/Kadaster/HIBP/
  Overheid/Brave/Tor-call uit;
- quick-health voert geen write/commit uit;
- quick-health blijft onder een afgesproken lokale latencygrens bij een
  trage of onbereikbare externe service;
- `/cms/api/health-summary` kan geen single-worker request blokkeren met
  externe timeouts;
- `/api/v1/health` blijft snel wanneer full-healthdiagnose actief is;
- de bestaande session-wedge regressietests blijven groen;
- health-response en HTTP-status blijven backwards-compatible waar dat nodig
  is.

Tests moeten externe calls mocken en expliciet controleren welke subchecks wel
en niet worden aangeroepen. Een aparte integratietest moet een vertraagde
SpiderFoot/externe service simuleren en de request-queue-impact meten.

## Rollout- en monitoringplan

1. Maak een kleine aparte PR, zonder DB-migratie en zonder relatie met
   factuurnummering of de DR-timer.
2. Draai de tests serieel met `pytest -n 0`, plus ruff en mypy.
3. Doe een review op de health-contracten en de gewijzigde probe-semantieken.
4. Deploy via de bestaande gecontroleerde rollout: backup, dry-run,
   `--confirm DEPLOY-MASTER`, readiness en rollback volgens RUNBOOK.
5. Controleer na deploy afzonderlijk:
   - quick-health p50/p95 en maximum;
   - `/api/v1/health` onder gelijktijdige belasting;
   - `/cms/api/health-summary` en externe-call-budgetten;
   - Gunicorn worker-queue/499’s;
   - `pg_stat_activity` en lock/wait-events;
   - OSErrors, foreign session-files en worker-restarts.
6. Start daarna een nieuw volledig monitoringvenster. Een CSV-gat maakt de
   uitkomst inconclusief; dan start het venster opnieuw vanaf herstel.
7. Alleen bij een volledig groen venster volgt de aparte PR voor de periodieke
   DR-verifier/timer en failure-alerting.

## Status en besluit

**Status:** oorzaak waarschijnlijk request-queueing door periodieke full-health
via `/cms/api/health-summary`; de precieze individuele externe timeout moet in
de fix-PR nog met instrumentatie worden bevestigd.

**Besluit:** geen timer-PR en geen productieconfiguratie-aanpassing. Eerst de
healtharchitectuur minimaal scheiden, testen, gecontroleerd uitrollen en een
nieuw volledig groen monitoringvenster behalen.

**Evidence:**

- `reports/health-diagnosis/health-diagnosis-20260831T143131Z.json`
- `reports/monitoring/p2-live.csv`
- `reports/monitoring/p2-final-20260831T070000Z.json`
