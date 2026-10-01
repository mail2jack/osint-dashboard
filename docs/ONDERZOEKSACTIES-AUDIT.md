# Audit onderzoeksacties

**Status:** read-only inventarisatie  
**Datum:** 29 september 2026  
**Omgeving:** Joost staging

## Samenvatting

Joost heeft 18 geregistreerde onderzoeksacties. Een actie wordt door een
onderzoeker gestart vanuit een case/subject-workflow; er is geen mechanisme dat
zonder gebruikersactie zelfstandig alle acties uitvoert.

Acties worden als voorstel gestart en kunnen worden geannuleerd. De uitvoering
loopt via de worker. Resultaten worden als findings aan de case gekoppeld.
Acties en findings zijn tenant-gebonden; de worker weigert acties zonder
`tenant_id` en controleert de tenantcontext opnieuw tijdens uitvoering.

## Actieoverzicht

| Actie | Categorie | Externe bronnen / functie | Opmerking |
|---|---|---|---|
| Email check | open | HIBP, OpenPGP, Brave, SpiderFoot | Kan e-mailadres naar externe bronnen sturen |
| Phone check | open | publieke bronnen, WhatsApp/Telegram, 2Chat | Mogelijke providerkosten en privacygevoelige identifier |
| Address research | open | PDOK/Kadaster, Overheid.io | Adresgegevens naar externe bronnen |
| Social media scan | open | Maigret en publieke platforminformatie | Publieke profielzoeking; geen betaalde kanaalactie |
| Facebook research | betaald | RapidAPI Facebook-providers | Expliciete tenantopt-in; kosten/voorwaarden controleren |
| Instagram research | betaald | RapidAPI Instagram-provider | Expliciete tenantopt-in; kosten/voorwaarden controleren |
| TikTok research | betaald | RapidAPI Scraptik | Expliciete tenantopt-in; credits en kosten |
| LinkedIn research | betaald | RapidAPI LinkedIn-provider | Expliciete tenantopt-in; credits en kosten |
| Twitter/X research | betaald | RapidAPI Twitter-provider | Expliciete tenantopt-in; credits en kosten |
| KvK research | open | Overheid.io/openKVK en KvK-links | Bedrijfs- en registratiegegevens |
| Vehicle check (RDW) | open | RDW open data | Kenteken- en voertuiggegevens |
| Vessel check | open | VesselFinder, MarinePlan, KVNR, Binnenvaart.eu, Equasis | Maritieme identifiers en scheepsgegevens |
| OSINT Deep Search | open | Brave en DuckDuckGo; SpiderFoot | Brede zoekactie met meerdere externe/local bronnen |
| Financial research | open | openbare bedrijfs-, insolventie- en UBO-bronnen | Juridisch/privacybeleid extra belangrijk |
| Subdomain scan | open | crt.sh en CertSpotter | Domein- en infrastructuurinformatie |
| Google Dork Search | open | Brave/DDG en directe zoeklinks | Expliciete zoekopdracht; query kan gevoelige identifiers bevatten |
| Browser search | open | Google, Bing en DuckDuckGo | Alleen zoekvoorstel/open browser; geen stille automatisering |
| Photo Analysis | lokaal/open met fallback | EXIF lokaal; reverse-searchlinks; optioneel Picarta | Picarta wordt alleen als AI-geolocatiefallback gebruikt |

## Belangrijke beveiligings- en workflowcontroles

- Betaalde sociale kanalen staan standaard uit en vereisen expliciete
  tenantconfiguratie.
- Betaalde acties hebben per actie maandelijkse creditlimieten.
- Een betaalde actie wordt ook bij uitvoering opnieuw geblokkeerd wanneer de
  tenantoptie inmiddels is uitgezet.
- Acties worden tenant-scoped uitgevoerd met Row-Level Security-context.
- Findings krijgen een tenant- en case-koppeling.
- Annuleren soft-deletet findings; bewijs en integriteitsinformatie blijven
  behouden voor auditdoeleinden.
- `browser_search` opent geen browser stil op de achtergrond en voert geen
  bulkquery uit.
- `manual_entry` is geen externe onderzoeksactie maar een handmatige finding.

## Wat daadwerkelijk is getest

In staging zijn de workflow, handmatige actie, finding, verificatie,
investigation-koppeling en rapportage getest. Niet iedere externe provideractie
is afzonderlijk uitgevoerd, omdat dit echte externe queries, credits of
providerdata kan gebruiken.

## Open aandachtspunten vóór productie

1. Per actie vastleggen welke persoonsgegevens naar welke provider mogen.
2. Providervoorwaarden, DPA's, regio's, retentie en kosten controleren.
3. Per tenant bepalen welke betaalde kanalen mogen worden geactiveerd.
4. Met synthetische testdata een representatieve actie uit iedere categorie
   testen: open, lokaal, betaald en browservoorstel.
5. Controleren dat externe resultaten, bron-URL's, screenshots en provenance
   correct worden opgeslagen.
6. Expliciete waarschuwingen tonen voordat privacygevoelige of betaalde acties
   worden gestart.
7. Besluiten of financiële, adres-, telefoon- en sociale acties aanvullende
   autorisatie of logging vereisen.

## Migratiebesluit

Deze audit verandert geen acties en activeert geen betaalde providers. Voor de
latere productiemigratie wordt alleen de afgesproken default company met één
gebruiker en de noodzakelijke configuratie/API-instellingen meegenomen. Oude
dossiers, tenants en testdata gaan niet mee.
