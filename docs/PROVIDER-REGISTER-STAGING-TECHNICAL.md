# Joost — technisch providerregister staging

**Datum:** 30 september 2026  
**Status:** technische inventarisatie; geen juridische of contractuele goedkeuring  
**Omgeving:** `https://staging.joost.iveras.com`

Dit register is gebaseerd op de repositorydocumentatie, de geregistreerde
onderzoeksacties en een read-only inventaris van staging. Het beschrijft wat
technisch mogelijk of geconfigureerd is. **Een provider met een ingevulde
API-sleutel is daarmee nog niet goedgekeurd voor gebruik.**

## Beslisregels

- `Open` betekent: geen betaalde provideractie vastgesteld; privacycontrole
  blijft nodig.
- `Betaald` betekent: expliciete toestemming en tenantopt-in vereist.
- `Lokaal` betekent: dienst draait binnen de eigen Joost-infrastructuur, maar
  eventuele externe verbindingen blijven apart beoordelen.
- `Pending` betekent: providerretentie, regio, contract/DPA en grondslag zijn
  niet uit de repository vastgesteld.
- Alleen synthetische testdata gebruiken zolang een externe provider niet is
  goedgekeurd voor de betreffende datacategorie.

## Register

| Joost-actie / functie | Provider of dienst | Gegevens die technisch kunnen worden doorgestuurd | Categorie | Kosten | Technische status staging | Privacy/retentie | Besluit |
|---|---|---|---|---|---|---|---|
| Email check | HIBP, OpenPGP, Brave, SpiderFoot | E-mailadres, domein | Persoonsgegeven / technisch | Niet volledig vastgesteld | HIBP/Brave/SpiderFoot health aanwezig; sleutelstatus afzonderlijk gecontroleerd | Niet vastgesteld | Pending |
| Phone check | Publieke bronnen, WhatsApp/Telegram, 2Chat | Telefoonnummer | Gevoelig persoonsgegeven | Mogelijk betaald | Functie aanwezig; sommige sleutels leeg | Niet vastgesteld | Pending |
| Address research | PDOK/Kadaster, Overheid.io | Adres, postcode, huisnummer | Persoonsgegeven / bedrijfsgegeven | Open/API | Overheid-configuratie aanwezig; overige per sleutel controleren | Niet vastgesteld | Pending |
| Social media scan | Maigret en publieke platformpagina’s | Gebruikersnaam, zoekterm | Persoonsgegeven | Open | Functie aanwezig | Niet vastgesteld | Pending |
| Facebook research | RapidAPI-provider | Profiel-/zoekidentifier | Persoonsgegeven | Betaald | Betaalde kanalen als feature beschikbaar | Niet vastgesteld | Pending; expliciete opt-in |
| Instagram research | RapidAPI-provider | Profiel-/zoekidentifier | Persoonsgegeven | Betaald | Betaalde kanalen als feature beschikbaar | Niet vastgesteld | Pending; expliciete opt-in |
| TikTok research | RapidAPI Scraptik | Gebruikersnaam/profielidentifier | Persoonsgegeven | Betaald | Betaalde kanalen als feature beschikbaar | Niet vastgesteld | Pending; expliciete opt-in |
| LinkedIn research | RapidAPI-provider | Naam, gebruikersnaam, profielidentifier | Persoonsgegeven / bedrijfsgegeven | Betaald | Betaalde kanalen als feature beschikbaar | Niet vastgesteld | Pending; expliciete opt-in |
| Twitter/X research | RapidAPI-provider | Naam, gebruikersnaam, zoekterm | Persoonsgegeven | Betaald | Betaalde kanalen als feature beschikbaar | Niet vastgesteld | Pending; expliciete opt-in |
| KvK research | Overheid.io/openKVK | Bedrijfsnaam, KvK-nummer, domein | Bedrijfsgegeven | Open/API | Overheid-configuratie aanwezig | Niet vastgesteld | Pending |
| Vehicle check | RDW open data | Kenteken | Technisch/voertuiggegeven | Open | Health en actie beschikbaar | Publieke bron; voorwaarden nog vastleggen | Pending |
| Vessel check | VesselFinder, MarinePlan, KVNR, Binnenvaart.eu, Equasis | Scheepsnaam, IMO/MMSI, locatie of andere maritieme identifier | Bedrijfs-/locatiegegeven | Gemengd | Actie aanwezig; niet elke sleutel is ingevuld | Niet vastgesteld | Pending |
| OSINT Deep Search | Brave, DuckDuckGo, SpiderFoot | Zoekterm, domein, identifier | Afhankelijk van query | Gemengd | Brave en SpiderFoot technisch beschikbaar | Niet vastgesteld | Pending |
| Financial research | Openbare bedrijfs-, insolventie- en UBO-bronnen | Bedrijfsnaam, registratienummer, persoon/UBO | Bedrijfs-/persoonsgegeven | Gemengd | Actie aanwezig | Extra juridische controle nodig | Pending |
| Subdomain scan | crt.sh, CertSpotter | Domein | Publiek technisch | Open | Actie aanwezig | Niet vastgesteld | Pending |
| Google Dork Search | Brave, DuckDuckGo en directe links | Zelf samengestelde zoekquery | Afhankelijk van query | Gemengd | Actie aanwezig | Query kan identifiers bevatten | Pending |
| Browser search | Google, Bing, DuckDuckGo | Zoekterm; browser opent expliciet | Afhankelijk van query | Open | Alleen gebruikersgestuurde zoekvoorstel-flow | Providerbeleid onbekend | Pending |
| Photo Analysis | EXIF lokaal; optioneel Picarta/reverse-searchlinks | Afbeelding, publieke afbeeldings-URL of metadata | Potentieel gevoelig persoonsgegeven | Gemengd | Lokale analyse beschikbaar; Picarta-sleutel niet gevuld | Niet vastgesteld | Pending |
| AI-plan en narrative | OpenRouter, optioneel Ollama | Door gebruiker geselecteerde onderzoekscontext, findings of tekst | Mogelijk persoons-/bedrijfsgegeven | OpenRouter mogelijk betaald; Ollama lokaal | OpenRouter technisch ingesteld; Ollama-configuratie aanwezig | Modelretentie/training/locatie nog bevestigen | Pending; minimale data |
| OSINT-engine | SpiderFoot lokaal | Onderzoeksquery en scanresultaten | Afhankelijk van scan | Lokaal met externe modules | Service en health zijn beschikbaar | Externe SpiderFoot-modules apart beoordelen | Pending |
| Tor/OPSEC | Tor lokaal met externe exit | Netwerkverkeer van expliciete actie | Technische metadata | Open | Tor actief wanneer expliciet ingeschakeld | Exit/providerretentie niet beheerst door Joost | Pending |
| Telemetry/licensing | `license.iveras.com`, IP-dienst | Install-ID, hostname, IP, OS/kernel, hardware, versie | Technische telemetry | Eigen dienst | Telemetry/licentie actief voor staging | Serverretentie en rechtsgrond nog bevestigen | Pending |

## Technisch geconfigureerd op staging

Read-only gecontroleerd op 30 september 2026, uitsluitend op sleutelnaam,
categorie, actieve status, encryptiestatus en waardelengte:

- Brave Search
- Google Search
- Overheid.io
- RapidAPI Username
- WhatsApp CheckLeaked
- OpenRouter
- Ollama
- SpiderFoot
- telemetry/licensing
- Tor

De sleutelwaarden zelf zijn niet uitgelezen of in dit document opgenomen.
Lege of niet-aangetroffen sleutels zijn niet hetzelfde als een goedgekeurde
uitgeschakelde provider.

De feature flag `paid_channels` staat op staging expliciet aan voor de Default
Organization, op basis van eerder eigenaarakkoord om de flags in staging te
activeren. De code controleert dit opnieuw bij het starten en uitvoeren van
een betaalde actie. Deze stagingkeuze is geen productieautorisatie en wordt
niet automatisch meegenomen in de latere productiemigratie.

De geïsoleerde testset `tests/test_paid_channels.py` is op 30 september 2026
uitgevoerd: **20 tests geslaagd**. De test controleert onder meer standaard
uit, tenant-opt-in, zichtbaarheid van betaalde opties, uitvoeringsblokkade en
kostenlabels.

## Nog door eigenaar/adviseur vast te stellen

Per rij moeten vóór professioneel of publiek gebruik nog worden bevestigd:

1. doel en AVG-grondslag;
2. verantwoordelijke/verwerker-rol;
3. providerregio en eventuele doorgifte buiten de EER;
4. providerretentie en gebruik voor modeltraining;
5. DPA/voorwaarden en subverwerkers;
6. Joost-retentie, verwijdering en klantinformatie;
7. tenant- en rolbevoegdheid;
8. kosten, limieten en expliciete betaalbevestiging.

Tot die bevestiging blijft de status **Pending**. Dit document is geen
juridisch advies en activeert geen provider.

## Eerste officiële broncontrole

Deze controle is uitgevoerd op 30 september 2026. Providerbeleid kan wijzigen;
de links moeten vóór productiegebruik opnieuw worden gecontroleerd.

### Brave Search API

- Brave vermeldt dat zoekqueryrecords maximaal 90 dagen worden bewaard voor
  facturatie en probleemoplossing, behoudens wettelijke verplichtingen.
- Brave vermeldt een zero-data-retentionoptie voor enterprise-klanten.
- Opslag van API-resultaten is volgens Brave alleen toegestaan wanneer het
  gekozen plan expliciet opslagrechten geeft.
- Consequentie voor Joost: query’s met namen, e-mailadressen of andere
  identifiers blijven privacygevoelig; Brave is niet automatisch geschikt voor
  permanente opslag of AI-input.

Bronnen: [Brave API privacybeleid](https://api-dashboard.search.brave.com/privacy-policy),
[Brave API-documentatie](https://brave.com/search/api/).

### OpenRouter

- OpenRouter geeft geen algemene garantie dat alle modelproviders dezelfde
  retentie- of trainingsregels hanteren.
- Inputs worden doorgestuurd naar de geselecteerde modelprovider; de regels
  van die modelprovider zijn mede bepalend.
- OpenRouter vermeldt dat opgeslagen Files API-bestanden blijven bestaan tot
  verwijdering of sluiting van het account, behoudens uitzonderingen.
- Consequentie voor Joost: geen volledige dossiers, gevoelige identifiers of
  afbeeldingen naar OpenRouter sturen zonder provider-/modelkeuze,
  verwerkersafspraken en expliciete goedkeuring. De AI-input moet vooraf
  worden geminimaliseerd.

Bronnen: [OpenRouter privacybeleid](https://openrouter.ai/privacy/),
[OpenRouter voorwaarden](https://openrouter.ai/terms/).

Deze broncontrole wijzigt de technische configuratie niet en verandert de
status van beide providers niet van **Pending** naar goedgekeurd.

### Overheid.io

- De gepubliceerde voorwaarden staan tijdelijk bewaren/cachen toe wanneer dit
  noodzakelijk is om onnodige herhaalde aanvragen te voorkomen.
- De API-key moet geheim worden gehouden en gebruikslimieten gelden per
  abonnement.
- Consequentie voor Joost: caching moet doelgebonden en beperkt blijven; de
  exacte actuele voorwaarden en datasetlicentie moeten vóór productiegebruik
  opnieuw worden bevestigd.

Bronnen: [Overheid.io voorwaarden](https://overheid.io/algemene-voorwaarden),
[Overheid.io API-documentatie](https://overheid.io/documentatie).

### WhatsApp CheckLeaked

- CheckLeaked vermeldt dat lookupdata over telefoonnummers, resultaten en
  historische profielinformatie langdurig kan worden bewaard zolang de dienst
  actief is.
- De dienst vermeldt internationale verwerking, onder meer buiten de EER,
  en dat lookupresultaten aan klanten beschikbaar kunnen worden gesteld.
- De voorwaarden leggen de rechtmatigheid van iedere lookup bij de klant en
  vereisen dat verwijder- en bezwaarverzoeken worden opgevolgd.
- Consequentie voor Joost: deze provider is privacy-technisch hoog risico.
  Geen echte dossierdata gebruiken totdat doel, grondslag, klantinformatie,
  doorgifte en verwijderproces expliciet zijn goedgekeurd.

Bronnen: [CheckLeaked privacybeleid](https://whatsapp.checkleaked.com/privacy),
[CheckLeaked voorwaarden](https://whatsapp.checkleaked.com/terms).

### RapidAPI-platform

- RapidAPI vermeldt dat het platform rechten behoudt op API-metadata,
  prestatiegegevens en gebruikslogs.
- Voor iedere API op het platform blijven de voorwaarden en datapraktijken van
  de afzonderlijke API-aanbieder bepalend.
- Consequentie voor Joost: een RapidAPI-sleutel is geen algemene goedkeuring
  voor Facebook, Instagram, TikTok, LinkedIn, X of andere kanalen. Iedere
  afzonderlijke API blijft Pending totdat de aanbieder is beoordeeld.

Bronnen: [RapidAPI voorwaarden](https://rapidapi.com/page/terms).

### Ollama

- Ollama vermeldt dat lokaal draaien prompts, antwoorden en modelinteracties
  niet naar Ollama verstuurt.
- Cloud-hosted modellen vallen onder een ander regime en verwerken prompts en
  antwoorden tijdelijk voor de aanvraag.
- Consequentie voor Joost: de lokale Ollama-route is de voorkeursroute voor
  gevoelige AI-context, mits de lokale server niet publiek bereikbaar is en de
  modellen lokaal worden uitgevoerd.

Bron: [Ollama privacybeleid](https://www.ollama.com/privacy).

### Tor

- Tor verbergt voor de doelwebsite het oorspronkelijke IP-adres door verkeer
  via een exit relay te laten uitgaan.
- Tor is geen vervanging voor HTTPS; verkeer naar een niet-HTTPS-doel kan bij
  de exitkant worden gelezen of gewijzigd.
- Consequentie voor Joost: Tor is een netwerkmaatregel voor expliciete acties,
  geen privacygoedkeuring voor de doorgestuurde onderzoeksdata. De doelwebsite
  en eventuele exit-relay blijven afzonderlijke risico’s.

Bronnen: [Tor-overzicht](https://support.torproject.org/about-tor/introduction/tor-vs-other-proxies/),
[Tor relay types](https://community.torproject.org/relay/types-of-relays/).

### Telemetry en licentie

De technische flow stuurt installatietelemetry naar de eigen licentieserver.
De repository legt de velden en vervangingsretentie vast, maar de externe
serverretentie, toegangsrechten en bewaartermijn moeten nog als interne
beheerbeslissing worden vastgelegd. Telemetry bevat geen case-, subject- of
findinginhoud volgens de huidige technische inventaris.

## Voorlopige risicokleur

| Dienstgroep | Voorlopige status | Betekenis |
|---|---|---|
| Lokale Ollama | 🟢 technisch geschikt | Voorkeursroute voor gevoelige AI-context, mits lokaal afgeschermd |
| Lokale SpiderFoot | 🟡 afhankelijk van modules | Dienst is lokaal, maar scans kunnen externe bronnen aanspreken |
| RDW/PDOK/Overheid.io | 🟡 beleidsmatig Pending | Publieke/open data, maar voorwaarden en caching moeten worden vastgelegd |
| Brave | 🟡 beleidsmatig Pending | Queryretentie en opslagrechten moeten bij het plan passen |
| OpenRouter | 🟡 beleidsmatig Pending | Modelprovider en training/retentie moeten per model worden vastgesteld |
| RapidAPI-kanalen | 🔴 voorlopig blokkeren voor dossiers | Onderliggende aanbieders en retentie verschillen per API |
| CheckLeaked | 🔴 voorlopig niet gebruiken | Langdurige lookupretentie en internationale gegevensverstrekking |
| Tor | 🟡 expliciete actie vereist | Netwerkroute, geen zelfstandige privacywaarborg |
| Telemetry/licentie | 🟡 intern besluit nodig | Eigen dienst, maar serverretentie en toegang moeten worden vastgelegd |

## Bronnen

- `docs/PROVIDER-REGISTER-TEMPLATE.md`
- `docs/EXTERNAL-DATAFLOW-INVENTORY.md`
- `docs/ONDERZOEKSACTIES-AUDIT.md`
- `docs/PLAN-TELEMETRY-PRIVACY-GOVERNANCE.md`
- `docs/AI-GEBRUIK-EN-ROADMAP.md`
