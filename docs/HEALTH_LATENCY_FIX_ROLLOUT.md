# Health latency fix: rolloutplan

## Scope

Deze PR verwijdert de synchronische full healthcheck uit
`/cms/api/health-summary`. De DR-verifier/timer, licentieconfiguratie,
database-schema's en session-store-backend vallen buiten scope.

## Gedrag na de fix

- Een full healthcheck draait via precies één systemd-gestuurde oneshot-
  producer; `flock` voorkomt overlap.
- `health-summary` leest uitsluitend de laatste snapshot.
- De response bevat `checked_at`, `age_seconds`, `stale` en `timings_ms`.
- Bij een lege cache geeft de UI een expliciet stale/unknown-resultaat terug en
  blokkeert de webrequest niet.
- De producer heeft `TimeoutStartSec=90` en faalt gecontroleerd zonder de
  Gunicorn-worker te blokkeren.
- Externe subchecks worden begrensd door hun bestaande timeouts en hun
  monotonic duur wordt als milliseconden opgeslagen, zonder secrets of query's.

## Pre-deploy

1. Draai `pytest -n 0` en controleer ruff/mypy.
2. Controleer dat de PR geen DR-timerbestanden of configuratie wijzigt.
3. Leg baseline vast voor `/health?quick=1`, `/cms/api/health-summary` en
   `/api/v1/health`, inclusief p50/p95/max en HTTP-status.
4. Controleer huidige Gunicorn-workerconfiguratie, journal-events, nginx
   499/5xx's en PostgreSQL `pg_stat_activity`.
5. Maak een pre-deploy backup volgens het bestaande RUNBOOK.

## Gecontroleerde rollout

1. Draai de bestaande dry-run.
2. Deploy via `production_rollout.sh --confirm DEPLOY-MASTER`.
3. Installeer de health-producer expliciet met
   `sudo /opt/osint-dashboard/scripts/install_health_refresh.sh`; controleer
   daarna dat de systemd-oneshot één refresh uitvoert zonder
   traceback en dat een tweede gelijktijdige start door de lock veilig overslaat.
4. Controleer dat een lege/stale cache een snelle `health-summary`-response
   geeft.
5. Controleer dat `/health?quick=1` en `/api/v1/health` niet achter een full
   healthcheck blijven hangen.
6. Controleer `timings_ms` op de full-healthsnapshot; geen credentials,
   response bodies of SQL mogen voorkomen.

## Voor/na-bewijs

Vergelijk ten minste:

- latency p50/p95/max van quick health;
- latency p50/p95/max van health-summary;
- latency van `/api/v1/health` tijdens een full refresh;
- aantal requests boven 1 seconde en boven 5 seconden;
- Gunicorn worker-restarts, timeouts en nginx 499/5xx;
- PostgreSQL active/waiting sessions en lock-events;
- OSErrors, foreign session-files en ownership-guardresultaten.

## Monitoringvenster

Start na succesvolle deploy een nieuw volledig monitoringvenster. De collector
moet zonder VPS-herstart blijven draaien. Een CSV-gat door VPS-herstart of
collectoruitval maakt het resultaat **inconclusief**; start vanaf herstel een
nieuw volledig venster.

Het venster is groen wanneer de health-latencycriteria, queue-indicatoren,
worker-status, session-store ownership en OSError-criteria gedurende de hele
ononderbroken periode groen blijven. Geïsoleerde latency-warm-ups worden apart
gerapporteerd; een combinatie van latency met OSError of foreign ownership
wordt nooit als warm-up geclassificeerd.

Pas na een volledig groen venster volgt een afzonderlijke PR voor de periodieke
DR-verifier/timer en failure-alerting.
