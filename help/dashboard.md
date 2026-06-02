# Dashboard

Het Dashboard is de startpagina na het inloggen. Het is nu **search-centric**: de zoekbalk staat prominent centraal voor snel zoeken in cases, subjects, clients, findings en documenten.

## Zoeken

Typ een zoekterm in de centrale balk en druk op Enter — je wordt doorgestuurd naar de search pagina met resultaten. Of gebruik de quick links eronder om direct naar een sectie te navigeren.

## Quick Links

- 📁 All Cases
- ➕ New Case
- 👤 All Subjects
- ➕ New Subject
- 🏢 All Clients
- ⏰ Reminders
- 📥 Export
- ⚙️ Settings (alleen admin)
- 📊 Statistieken (alleen admin)

## Stat Counters

Een rij van 7 kaarten toont het totaal aantal:

- **Open** — cases met status Open
- **Active** — cases met status Active
- **Suspended** — cases met status Suspended
- **Closed** — cases met status Closed
- **Clients** — actieve clients
- **Subjects** — totaal subjects
- **Findings** — totaal findings

## My Open / Active Cases

Tabel met jouw toegewezen cases (status Open of Active), gesorteerd op laatste update.

## OSINT Service Health

Een rij van 7 service-kaarten toont de status van externe OSINT-bronnen:

- **Database** — PostgreSQL verbinding
- **SpiderFoot** — OSINT scan engine
- **RDW** — Nederlandse voertuigregistratie
- **Kadaster/PDOK** — Nederlandse BAG adresdata
- **HIBP** — Have I Been Pwned (datalekken check)
- **Overheid.io** — Nederlandse open data API (OpenKVK KvK lookup)
- **Brave Search** — web search API

Groen = Online, Oranje = geen key geconfigureerd, Rood = fout. De health wordt gecheckt bij het laden van de pagina.

## Statistieken

Voor alle grafieken en widgets (Cases by Status, Criminal Code, Priority, Lead Investigator Workload, Recent Activity, SpiderFoot stats, Reminders, Subject Types) ga je naar **Settings → Statistieken** (`/cms/settings/statistics`). Deze pagina bevat alle voormalige dashboard-widgets.
