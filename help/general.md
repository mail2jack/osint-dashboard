# Algemeen

Het OSINT Dashboard is een webapplicatie voor het beheren van onderzoeken (cases), het vastleggen van bevindingen (findings), en het uitvoeren van OSINT-zoekopdrachten.

## Navigatie

De hoofdnavigatiebalk bevat:

- **Dashboard** — centrale hub met statistieken en recente activiteit
- **Cases** — overzicht van alle onderzoeken
- **Clients** — opdrachtgevers gekoppeld aan cases
- **Subjects** — personen, bedrijven of schepen van interesse
- **Search** — full-text zoeken door de hele database
- **Reminders** — herinneringen en notificaties
- **SpiderFoot** — OSINT-scanautomation (alleen voor senior/ admin)
- **Settings** — applicatieconfiguratie (alleen admin)

## Toetsenbord sneltoetsen

| Toets | Actie |
|-------|-------|
| `?` | Open context-sensitive help paneel |
| `s` | Focus de zoekbalk (op search-pagina's) |
| `j` / `k` | Navigeer omlaag/omhoog in lijsten |
| `Enter` | Open geselecteerd item |
| `Escape` | Sluit modals / help paneel |

## Thema

Klik op het 🌙/☀️ icoon rechtsboven om te schakelen tussen dark/light mode. De keuze wordt opgeslagen in localStorage.

## Sessie

De sessie verloopt na 8 uur inactiviteit. Bijgevoegde bestanden zijn beperkt tot 16 MB per upload.

## Rate Limiting

Om misbruik te voorkomen is er een globale limiet van 300 verzoeken per 60 seconden per IP. Voor create/edit acties geldt een strengere limiet van 30 verzoeken per 60 seconden.
