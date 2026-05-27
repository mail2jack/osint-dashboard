# Dashboard

Het Dashboard is de centrale hub na het inloggen. Het geeft een overzicht van de actieve onderzoeken, subjects en systeemstatus.

## Statistiek-kaarten

Elke kaart toont een belangrijke metriek:

- **📋 Open Cases** — aantal cases met status `Open` of `In Progress`
- **👥 Subjects** — totaal aantal subjects in de database
- **⏰ Pending Reminders** — herinneringen die binnen 7 dagen vervallen
- **📄 Recent Activity** — laatste 30 audit log entries (nieuwe cases, bewerkingen, etc.)

## OSINT Service Health

Een rij van 6 service-kaarten toont de status van externe OSINT-bronnen:

- **SpiderFoot** — OSINT scan engine
- **Kadaster/PDOK** — Nederlandse BAG adresdata
- **RDW** — Nederlandse voertuigregistratie
- **HIBP** — Have I Been Pwned (datalekken check)
- **Overheid.io** — Nederlandse open data API
- **Brave Search** — web search API

Groen = bereikbaar, Rood = onbereikbaar. De health wordt gecheckt bij het laden van de pagina via `/health?quick=1`.

## Trending Subjects

De "Trending Subjects" sectie toont subjects die in de meeste cases voorkomen. Dit helpt bij het identificeren van veelvoorkomende targets over meerdere onderzoeken heen.

## SpiderFoot Statistieken

Als SpiderFoot geconfigureerd is, verschijnt een extra kaart "🔍 Scans" met:

- **Completed** — aantal voltooide scans
- **Running** — aantal actieve scans
- **Failed** — aantal mislukte scans
- **Last scan** — datum/tijd van de laatste scan

Klik op de kaart voor een detailweergave.

## Recente Cases

De "Recent Cases" tabel toont de 10 meest recent bijgewerkte cases met status, prioriteit en een link naar de case-detailpagina.
