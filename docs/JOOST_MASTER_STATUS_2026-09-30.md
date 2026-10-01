# Joost — masterstatus en werkafspraken

**Vastgelegd:** 30 september 2026  
**Status:** levend overzicht voor staging en voorbereiding  
**Productie:** beschermd; dit document geeft geen toestemming voor wijzigingen

## 1. Doel van dit document

Dit is het centrale overzicht van de belangrijkste besluiten, de huidige
technische stand, open punten en de volgorde van het vervolgwerk. Het voorkomt
dat belangrijke afspraken alleen in een chat of in het geheugen van een
werkbeurt staan.

De meer gedetailleerde documenten in deze map blijven leidend voor hun eigen
onderwerp. Dit document verwijst naar die documenten en vat de praktische
status samen.

## 2. Omgevingen en grenzen

- **Staging:** `https://staging.joost.iveras.com`
- **Productie:** `https://joost.iveras.com`
- Staging en productie blijven gescheiden.
- Onderzoek, testen, documentatie en wijzigingen vinden eerst op staging plaats.
- Productie wordt niet gemigreerd, aangepast, herstart of gedeployed zonder
  afzonderlijke, expliciete toestemming van de eigenaar.
- Een geslaagde stagingtest, SSH-toegang of voorbereid migratieplan is nooit
  automatisch toestemming voor productie.

## 3. Wat Joost nu inhoudelijk bevat

Joost is een OSINT-case-managementapplicatie met onder meer:

- tenants/organisaties, gebruikers en rollen;
- clients, cases en subjects;
- investigations en research actions;
- candidates, findings en verificatie;
- bronverwijzingen, provenance, content hashes en screenshots;
- rapportage, audit logging en achtergrondtaken;
- tenant-isolatie en PostgreSQL Row Level Security;
- externe OSINT-integraties, SpiderFoot en Tor;
- Redis, workers, Docker, HTTPS, backups en licentiecontrole.

De functionele basis is op staging getest. Dat bewijst niet automatisch dat de
zelfde toestand op productie aanwezig is.

## 4. Bevestigde besluiten

### Tenantmodel

Joost werkt per tenant. Gegevens, gebruikers, API-sleutels, integraties,
exports en achtergrondwerk horen bij de juiste tenant te blijven.

### Default Organization en latere migratie

De latere productiemigratie neemt uitsluitend mee:

- de Default Organization/default company;
- één gebruiker: `ivan.versteegh@protonmail.com`;
- noodzakelijke configuratie, API-configuratie en API-sleutels;
- noodzakelijke licentie- en integratie-instellingen.

Niet meenemen: oude cases, clients, subjects, findings, onderzoeksresultaten,
andere tenantbedrijven en oude testdata.

Voor de migratie komt eerst een apart controleoverzicht met aantallen en een
go/no-go-moment. Zie ook `PRODUCTIE-MIGRATIE-SCOPE.md`.

### AI-onderzoeksworkflow

- De AI maakt eerst een onderzoeksplan/proposals.
- Er wordt niets gestart zonder menselijke goedkeuring.
- Betaalde acties mogen uitsluitend na expliciete toestemming worden gebruikt.
- Gratis acties vormen de veilige basis van een plan.
- Een onderzoeker moet voorstellen vanuit een case of investigation kunnen
  starten.
- Findings blijven de feitelijke onderzoeksresultaten.
- Een AI-verhaal is een interpretatieve samenvatting en nooit zelfstandig
  bewijs.
- Een AI-verhaal moet later duurzaam aan een investigation/report kunnen worden
  gekoppeld; dat is nog een open verbetering.
- De AI-functionaliteit gebruikt OpenRouter wanneer beschikbaar. De AI krijgt
  de voor het verzoek noodzakelijke onderzoeksinformatie; providerbeleid,
  privacy en kosten blijven aandachtspunten.
- Ollama is op staging alleen als configuratie voorbereid, maar niet lokaal
  geïnstalleerd of actief. Met 2 CPU-cores, 4 GB RAM en circa 2 GB beschikbaar
  geheugen is een bruikbare lokale 7B-AI naast Joost niet verantwoord. Lokale
  Ollama wordt bewaard als toekomstoptie op een aparte of zwaardere AI-host.
- Betaalde onderzoeksacties zijn in de code standaard geblokkeerd zonder
  tenant-opt-in en worden bij uitvoering opnieuw gecontroleerd. Op staging is
  `paid_channels` voor de Default Organization expliciet geactiveerd op basis
  van eerder eigenaarakkoord; dit is geen productieautorisatie.

### Rapportage en bewijs

- Alleen findings met status **verified** kunnen in officiële rapporten komen.
- `include_in_report` kan een verified finding alsnog uitsluiten.
- Werkresultaten en ruwe exports blijven onderscheiden van officiële
  rapportage.
- Bron, status, timestamp en content hash moeten voor findings behouden
  blijven.

### Telemetry en externe providers

- De huidige telemetryvelden zijn toegestaan en worden bewaard totdat nieuwe
  waarden ze vervangen.
- De eigenaar heeft ingestemd met de technische retentie-uitwerking:
  telemetry wordt na 90 dagen zonder heartbeat als inactief gemarkeerd; na
  365 dagen inactiviteit wordt de installatie verwijderd als er geen actieve
  licentie meer aan gekoppeld is. Een actieve licentie blijft behouden en een
  nieuwe heartbeat maakt de installatie weer actief.
- De korte retenties blijven: IP-afgeleide gegevens 30 dagen, HTTP-metadata
  7 dagen, IP-checks 30 dagen, IP-cache 30 dagen en beheeraudit 365 dagen.
  De purge draait automatisch via de bestaande geplande onderhoudstaak.
- Providergebruik moet per gegevenssoort en privacyrisico worden beoordeeld.
- Publieke technische data is voorlopig toegestaan.
- Gevoelige persoonsgegevens en zakelijke identifiers vereisen passende
  providergoedkeuring en een duidelijke grondslag.

Read-only inventaris van staging op 30 september 2026: technisch ingevuld zijn
onder meer Brave, Google Search, Overheid.io, RapidAPI Username, WhatsApp
CheckLeaked, OpenRouter, Ollama, SpiderFoot, telemetry/licensing en Tor.
Voor deze controle zijn alleen sleutelnaam, categorie, actieve status,
encryptiestatus en waardelengte bekeken; geheime waarden zijn niet uitgelezen
of opgeslagen. Een technische configuratie is nadrukkelijk geen bewijs van
contractuele toestemming, providerretentie, regio of AVG-grondslag.

### WhatsApp

WhatsApp blijft voor productie uitgeschakeld totdat dit expliciet wordt
geactiveerd. Per tenant wordt een afzonderlijk account gebruikt, te beginnen
met handmatig verzenden en zonder automatisch onderzoeksdata in berichten te
plaatsen.

## 5. Beveiliging en continuïteit

Op staging zijn de volgende onderdelen ingericht of gecontroleerd:

- HTTPS voor staging;
- SSH-sleuteltoegang voor Codex/development;
- Fail2ban voor SSH en nginx-auth;
- Tor, alleen wanneer expliciet ingeschakeld;
- licentiecontrole en telemetry richting de bestaande licentieserver;
- PostgreSQL, Redis, app en worker als gezonde stagingservices;
- versleutelde backups met gecontroleerde decryptie en archiefintegriteit;
- dagelijkse backupplanning met dagelijkse, wekelijkse en maandelijkse
  bewaartermijnen;
- tijdelijke database-restoretest in de PostgreSQL-container.

De restoretest heeft aangetoond dat een backup kan worden ontsleuteld en dat
de database-inhoud technisch kan worden teruggezet in een tijdelijke database.
Een ontbrekende lokale PostgreSQL-service op de host was daarbij geen probleem:
staging gebruikt PostgreSQL in Docker.

Backup-sleutels moeten buiten de backupopslag worden bewaard. Geen wachtwoorden,
API-sleutels, tokens of backup-sleutels worden in deze documentatie opgenomen.

## 6. Huidige status per planonderdeel

| Onderdeel | Status | Toelichting |
|---|---|---|
| Technische basis en architectuur | 🟢 | Flask, PostgreSQL, Redis, workers, Docker en deployment zijn vastgesteld. |
| Tenantmodel en Default Organization | 🟢 | Staging gebruikt tenantisolatie; migratiescope is vastgelegd. |
| Securitybasis | 🟢 | HTTPS, SSH-key, fail2ban, licentiecontrole en Tor-basis zijn ingericht of gecontroleerd. |
| Cases, clients, subjects | 🟢 | Aanmaken, openen en basisworkflow zijn op staging getest. |
| Investigations en research actions | 🟢 | Onderzoeken zijn bereikbaar en acties kunnen worden uitgevoerd. |
| Findings, screenshots en provenance | 🟢 | Findings hebben broninformatie, hashes en verificatiestatus. |
| AI-plan en goedkeuringsstap | 🟢 | Plan/proposals en menselijke goedkeuring zijn aanwezig. |
| Betaalde onderzoeksacties | 🟢 | Tenant-opt-in en uitvoeringscontrole getest; 20 geïsoleerde tests geslaagd. Staging is expliciet geactiveerd, productie niet. |
| AI-start vanuit investigation | 🟢 | Direct starten vanuit de investigation is toegevoegd. |
| AI-narrative | 🟢 | Verhaal wordt bij het onderzoek opgeslagen en herkenbaar in rapportweergave opgenomen; de regressietest is geslaagd in de aparte Docker-testimage met tijdelijke SQLite-database, zonder netwerk en zonder live stagingdata. |
| Officiële rapportage | 🟢 | Alleen verified findings verschijnen; opgeslagen AI-verhalen verschijnen apart met interpretatiewaarschuwing. |
| Backups en restorecontrole | 🟢 | Versleutelde backup, planning en tijdelijke restore zijn gecontroleerd. |
| Telemetryretentie | 🟢 | 90 dagen tot inactief, 365 dagen daarna opruimen zonder actieve licentie; 67 geïsoleerde licentie-servertests geslaagd. Productie-uitrol nog niet uitgevoerd. |
| Achtergrondtaken en tenantisolatie | 🟢 | Tenant wordt opgeslagen bij de taak; RLS beschermt taakstatussen; runner- en PostgreSQL-tests dekken tenant-scheiding en context-reset. |
| Productiemigratie | 🔵 | Plan bestaat, maar mag pas na expliciete go/no-go worden uitgevoerd. |
| Publieke bedrijfswebsite/marketing | 🔵 | Blijft een afzonderlijk spoor naast Joost. |

### Gecontroleerde afrondingsronde — 30 september 2026

De technische afrondingscontrole op staging is uitgevoerd. Alle vier Docker-
services waren gezond (`app`, `postgres`, `redis` en `worker`), `/health` gaf
status `ok`, en de openbare routes `/health` en `/auth/login` gaven HTTP 200.
Fail2ban, Tor en cron waren actief. De geplande backup van 30 september is
aanwezig. De licentie-serverregressiesuite is opnieuw uitgevoerd in een
geïsoleerde container: 67 tests geslaagd.

De schema-controle vond geen nieuwe migraties. De bestaande waarschuwing over
de wederzijdse foreign keys tussen `tenants` en `users` blijft een bekend
onderhoudspunt. Een volledige handmatige gebruikersacceptatietest met een
echte eigenaarlogin blijft een aparte gebruikershandeling; deze controle is
niet stilzwijgend als technisch afgerond aangemerkt.

## 7. Openstaande punten

### Prioriteit 1 — AI en rapportage

1. De volledige AI-plan → goedkeuring → actie → findings → narrative-flow als
   vaste regressietest uitvoeren in een geïsoleerde testomgeving. De test is
   toegevoegd en is geslaagd in `Dockerfile.test` met een tijdelijke SQLite-
   database. De test draait niet tegen de live stagingdatabase.
2. Rapportage verder verfijnen met eventueel handmatige redactie en selectie
   van meerdere opgeslagen onderzoeksverhalen.

### Prioriteit 2 — technische afronding

4. Alembic/schema-drift in kleine, beoordeelde stappen reconciliëren; de
   read-only controle op 30 september 2026 vond geen nieuwe upgrade-operaties.
   Er blijft een bekende waarschuwing over de wederzijdse foreign keys tussen
   `tenants.owner_id` en `users.tenant_id`; dit is een onderhoudspunt, geen
   reden om nu een migratie uit te voeren.
5. CSRF-exempties en resterende security-routes opnieuw beoordelen.
   De read-only controle op 30 september 2026 vond de catalogus in sync met
   de actuele `@csrf.exempt`-decorators. Muterende browserroutes staan niet in
   de uitzonderingslijst; de uitzonderingen zijn beperkt tot lookups, AI,
   streams, CSP-rapportage en de signature-beveiligde Stripe-webhook.
6. Background-task tenant-context en PostgreSQL/RLS zijn read-only gecontroleerd.
   De thread- en RQ-route slaan de tenant op en herstellen die voor de
   taakuitvoering; bestaande tests controleren tenant-scheiding en het resetten
   van de context. De generieke runner gebruikt bewust tijdelijk bypass-RLS om
   de taakstatus te lezen/bij te werken. Nieuwe taaktypen moeten daarom altijd
   expliciet hun eigen tenant-context instellen; dit blijft een onderhoudsregel
   vóór productiegebruik.
7. Providerregister, privacygrondslag en retention per externe provider
   formaliseren. De technische staginginventaris is uitgevoerd en staat in
   `PROVIDER-REGISTER-STAGING-TECHNICAL.md`; providercontracten, regio,
   retentie en juridische grondslag blijven eigenaar-/juridisch besluit en
   moeten vóór productiegebruik worden ingevuld.
   Een eerste officiële broncontrole voor Brave en OpenRouter is toegevoegd;
   beide blijven beleidsmatig Pending. Een tweede controle markeerde
   CheckLeaked als hoog-risico vanwege langdurige lookupretentie en
   internationale verwerking; deze koppeling blijft technisch ongemoeid maar
   mag beleidsmatig niet als goedgekeurd worden beschouwd.

### Prioriteit 3 — productievoorbereiding

8. Productie alleen read-only inventariseren wanneer dat nodig is.
9. Selectieve migratie voorbereiden met aantallen, hashes, tenantcontrole,
   backup en rollbackplan.
10. Pas daarna expliciet go/no-go vragen voor de productiemigratie.
11. De huidige productieomgeving later omvormen tot licentie-/backupserver,
   volgens een apart plan en met behoud van terugvalmogelijkheid.

## 8. Wat de eigenaar nu wel en niet hoeft te doen

Voor deze documentatietaak is geen actie nodig.

De eigenaar is pas nodig wanneer een keuze gevolgen heeft voor privacy,
externe datadeling, betaalde providers, gebruikersdata, productie, DNS,
migratie of klantgedrag. Dan wordt eerst een begrijpelijke uitleg en een
concreet akkoordmoment gegeven.

## 9. Werkwijze voor volgende taken

Elke volgende technische taak vermeldt vooraf:

1. het exacte doel;
2. read-only of wijziging;
3. staging of productie;
4. wat buiten scope blijft;
5. hoe het resultaat wordt gecontroleerd;
6. of toestemming van de eigenaar nodig is.

De volgorde blijft: onderzoeken → uitleggen → plan → akkoord → uitvoeren →
verifiëren → documenteren.

## 10. Gerelateerde documenten

- `JOOST_PROJECT_CONTEXT.md`
- `JOOST-DECISIONS.md`
- `JOOST-NEXT-ACTIONS.md`
- `PRODUCTIE-MIGRATIE-SCOPE.md`
- `AI-AUTONOME-ONDERZOEKSWORKFLOW-FEASIBILITY.md`
- `AI-GEBRUIK-EN-ROADMAP.md`
- `PLAN-REPORT-FINDING-POLICY.md`
- `PROVIDER-REGISTER-STAGING-TECHNICAL.md`
- `OWNER-DECISIONS-PROVIDER-TELEMETRY.md`
- `STAGING_RELEASE_CHECKLIST.md`
- `DISASTER_RECOVERY.md`
