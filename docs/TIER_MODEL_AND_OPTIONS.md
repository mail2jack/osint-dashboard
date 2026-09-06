# Iveras OSINT Dashboard
# Tier- en Optiemodel

**Doelgroep:** CFO, product owner, technisch reviewer en ChatGPT-verificatie  
**Status:** feitelijke inventarisatie van de huidige code en documentatie  
**Datum:** 2026-08-30

## 1. Managementsamenvatting

Het product gebruikt momenteel drie verschillende concepten die zorgvuldig uit elkaar moeten worden gehouden:

1. **SaaS-plan per tenant:** bepaalt hoeveel gebruikers, zaken, onderwerpen, documenten en opslag een tenant mag gebruiken, plus enkele feature-gates.
2. **Commerciële softwarelicentie:** bepaalt onder welke juridische voorwaarden de software wordt gebruikt: AGPLv3 of een commerciële licentie.
3. **Installatie-licentie:** bepaalt technisch of een installatie in trial staat of een geldige volledige licentie heeft. Deze status wordt offline met Ed25519 geverifieerd.

Deze modellen zijn nog niet één-op-één gekoppeld. De namen lijken op elkaar, maar hebben niet automatisch dezelfde betekenis:

- De applicatie kent de SaaS-tiers `free`, `starter`, `professional` en `enterprise`.
- De commerciële prijstabel noemt `Startup`, `Professional`, `Enterprise` en `OEM / Embedding`.
- De technische licentieserver werkt met een `trial`/`full`-plan en kan daarnaast planstrings opslaan.
- Stripe gebruikt een configureerbare mapping van Stripe Price ID naar SaaS-tier; de daadwerkelijke Price IDs staan niet in de repository.

**CFO-conclusie:** de huidige software bevat een bruikbaar technisch entitlement-model, maar de commerciële productcatalogus en de technische tiernamen moeten nog formeel worden geharmoniseerd voordat prijzen, contracten, facturatie en verkoopmateriaal als definitief kunnen worden beschouwd.

## 2. Begrippen en scope

| Begrip | Niveau | Huidige betekenis | Bron |
|---|---|---|---|
| Tenant | Organisatie/workspace | Afzonderlijk geïsoleerd klantdomein binnen de applicatie | `cms.models.Tenant` |
| SaaS-tier | Tenant | `free`, `starter`, `professional` of `enterprise`; resource- en featurelimieten | `cms/tier_limits.py` |
| Trial | Installatie | Geen geldige volledige installatie-licentie; soft enforcement actief | `cms/services/license.py` |
| Full license | Installatie | Geldige, ondertekende installatie-licentie | `cms/services/license.py` |
| Commercial license | Juridisch | Recht om proprietary/SaaS-gebruik zonder AGPL-source-sharing verplichting te doen | `COMMERCIAL_LICENSE.md` |
| Stripe subscription | Betaling | Abonnement gekoppeld aan een Stripe Price ID en tenant | `cms/routes/stripe_billing.py` |
| Feature flag | Uitzondering | Per-tenant override door een super-admin; geen vervanging van commerciële licentie | `FeatureFlag` |

## 3. GitHub-installatie: standaardgedrag

De publieke installatie-instructie gebruikt standaard de `master`-branch:

```bash
wget https://raw.githubusercontent.com/mail2jack/osint-dashboard/master/install.sh
chmod +x install.sh
sudo ./install.sh
```

De installer clone’t de repository naar `/opt/osint-dashboard`, bouwt de Python-omgeving en frontend, configureert PostgreSQL/Nginx/SpiderFoot/systemd en genereert een unieke `INSTALL_ID` en `INSTALL_TOKEN`. Deze worden in `.env` opgeslagen met mode `0600`.

Daarna probeert de installer de installatie best-effort te registreren bij `https://license.iveras.com/api/register`. Deze registratie is non-blocking: als de licentieserver niet bereikbaar is, gaat de installatie door en kan later opnieuw worden geregistreerd.

### Juridische licentie

De GitHub-repository vermeldt AGPLv3 als standaard open-sourcelicentie. Een installatie vanaf GitHub krijgt dus niet automatisch een betaalde commerciële licentie. Wie proprietary gebruik, SaaS-gebruik zonder AGPL-source-sharing of OEM/embedding nodig heeft, moet daarnaast een commerciële licentieovereenkomst sluiten.

### Technische licentie

Als registratie lukt, geeft de licentieserver automatisch een ondertekende `trial`-licentie uit, standaard 30 dagen (`TRIAL_DAYS`). De applicatie verifieert deze lokaal met Ed25519. Trial enforcement:

- beperkt standaard het aantal tenants tot één;
- blokkeert AI, SpiderFoot, Vessel en Phone;
- laat de applicatie verder normaal draaien.

Telemetrie en licentie zijn standaard ingeschakeld, maar kunnen via Settings → General of `TELEMETRY_DISABLED=1` worden uitgeschakeld. Uitschakelen verandert de juridische licentie niet en is geen commerciële upgrade.

### Eerste tenant

Bij een lege database seedt de huidige code één `Default Organization` met technisch tier `enterprise`. Dat betekent nadrukkelijk niet dat de installatie een commerciële Enterprise-overeenkomst heeft. Trial enforcement blijft daar bovenop actief. Dit is een productbeslissing die expliciet moet worden bevestigd: als nieuwe GitHub-installaties commercieel met Free of Starter moeten beginnen, moet de seed-default worden aangepast.

## 4. Upgradepaden vanaf GitHub

Een GitHub-installatie kent drie verschillende soorten upgrade.

### A. Tenant-plan

Via **Settings → Plan & Limits** kan een tenant owner kiezen tussen Free, Starter en Professional. Enterprise kan alleen door een super-admin worden ingesteld.

Een tenant-planupgrade verhoogt resource-limieten en feature-defaults, maar wijzigt niet automatisch de installatie-licentie. Trial-gates blijven dus actief zolang de installatie geen geldige volledige licentie heeft.

Met een actieve Stripe-subscription loopt de wijziging via de geconfigureerde `stripe_price_mapping`; eventuele proratie wordt geregistreerd. Zonder actieve subscription wordt het technische tenant-tier direct gewijzigd.

### B. Volledige installatie-licentie

Een volledige licentie wordt niet verkregen door een GitHub-pull of door alleen het tenant-tier te wijzigen. De licentiebeheerder geeft een nieuwe licentie uit via de licentieserver:

```bash
CLI="sudo -u license env HOME=/opt/license-server /opt/license-server/venv/bin/python3 /opt/license-server/cli.py"
$CLI license:new --install <install_id> --plan full --days 365
```

Dit kan ook via het licentieserver-dashboard met **Issue license**. De vorige licentie wordt vervangen. De installatie ontvangt de nieuwe ondertekende licentie bij de volgende dagelijkse check-in, of handmatig:

```bash
sudo -u osint /opt/osint-dashboard/venv/bin/flask telemetry:report
```

Daarna toont Settings → General de nieuwe lokale licentiestatus. Normale requests vereisen geen continue verbinding met de licentieserver; revocatie en wijzigingen worden bij een volgende check-in verwerkt.

### C. Softwareversie

Een GitHub-update is alleen een code-update, geen plan- of licentieupgrade. De updateflow haalt code op, installeert dependencies, bouwt de frontend, voert migraties uit en herstart services. Een nieuwere GitHub-versie geeft geen automatisch recht op een hoger commercieel plan.

### Praktische route voor een klant

1. Installeer de software onder AGPLv3.
2. Doorloop de setup wizard en wijzig het standaard admin-wachtwoord.
3. Ontvang, indien registratie lukt, de 30-daagse trial-licentie.
4. Kies eventueel een tenant-plan.
5. Configureer Stripe wanneer self-service betaling gewenst is.
6. Sluit voor proprietary/SaaS/OEM-gebruik de juiste commerciële overeenkomst.
7. Laat voor de `install_id` een `full` installatie-licentie uitgeven.
8. Voer `telemetry:report` uit of wacht op de dagelijkse check-in.

De formele koppeling tussen commercieel aanbod, installatie-licentie en tenant-tier moet nog in een entitlement-matrix worden vastgelegd.

## 5. SaaS-tiers in de applicatie

De applicatie kent vier tenant-tiers. De limieten worden in code vastgelegd in `cms/tier_limits.py` en worden bij relevante create/upload/scan-acties gecontroleerd.

| Resource/feature | Free | Starter | Professional | Enterprise |
|---|---:|---:|---:|---:|
| Gebruikers | 2 | 5 | 25 | Onbeperkt |
| Zaken/cases | 5 | 50 | 500 | Onbeperkt |
| Onderwerpen/subjects | 10 | 100 | 1.000 | Onbeperkt |
| Cliënten | 5 | 25 | 100 | Onbeperkt |
| Findings | 25 | 500 | 5.000 | Onbeperkt |
| Documenten | 25 | 250 | 2.500 | Onbeperkt |
| Opslag | 50 MB | 500 MB | 5 GB | Onbeperkt |
| Gelijktijdige SpiderFoot-scans | 0 | 0 | 3 | 10 |
| Export | Nee | Ja | Ja | Ja |
| AI | Nee | Nee | Ja | Ja |
| SpiderFoot | Nee | Nee | Ja | Ja |
| API keys | Nee | Nee | Ja | Ja |

### Gedrag van deze limieten

- Een onbekende tier valt in de applicatie terug op `free`-limieten.
- `None` betekent onbeperkt voor resources; Enterprise heeft nog wel een limiet van 10 gelijktijdige SpiderFoot-scans.
- Limieten worden per tenant toegepast, niet globaal per installatie.
- De gebruikerslimiet wordt ook gecontroleerd bij uitnodigen/aanmaken van gebruikers.
- Case-, subject-, client-, finding-, document- en opslaglimieten worden gecontroleerd bij de betreffende mutatie.
- SpiderFoot heeft een aparte limiet voor gelijktijdige scans.
- De usage/aggregation-laag kan waarschuwingen genereren bij 80% en 100% van een resource-limiet.
- Deze limieten zijn technisch afdwingbaar, maar de repository bevat geen aparte catalogus met commerciële prijs, btw, contractduur of SLA per SaaS-tier.

## 6. Feature-opties en uitzonderingen

### Tier-defaults

De tier bepaalt de standaardwaarde voor deze features:

- `export`
- `ai`
- `spiderfoot`
- `api_keys`

Een feature kan daarnaast door een super-admin per tenant worden overschreven via `FeatureFlag`. Dat is een operationele override en geen zelfstandig verkoopproduct.

### Trial-gates op installatieniveau

Zonder geldige volledige installatie-licentie blokkeert trial enforcement standaard:

- AI
- SpiderFoot
- Vessel
- Phone

De trial heeft standaard een limiet van één tenant. Deze waarde is configureerbaar via `trial_tenant_limit`.

Belangrijk: de trial-gates en de tenant-tier-gates zijn twee aparte controles. Een tenant kan bijvoorbeeld technisch `professional` zijn, terwijl de installatie als geheel nog in trial staat; dan kunnen trial-gates bepaalde functies alsnog blokkeren.

`LICENSE_ENFORCEMENT=off` schakelt de trial enforcement uit. Dit is een technische configuratieoptie en moet niet worden verward met het verkrijgen van commerciële gebruiksrechten.

## 7. Technische installatie-licenties

De licentieserver levert ondertekende licenties met onder meer:

- `install_id`
- `license_id`
- `plan`
- `issued_at`
- `expires_at`
- status, waaronder actief of ingetrokken

De applicatie:

- verifieert de Ed25519-handtekening lokaal;
- cachet de laatst geverifieerde licentie;
- blijft draaien als de licentieserver tijdelijk niet beschikbaar is;
- verwerkt online revocatie bij een volgende check-in;
- behandelt een verlopen, ingetrokken of ongeldig ondertekende licentie als niet geldig.

De standaard trialduur is 30 dagen (`TRIAL_DAYS` configureerbaar). De licentieserverdocumentatie gebruikt voor volledige licenties doorgaans `plan=full`; de applicatie-interface herkent ook planwaarden zoals `professional` en `enterprise` voor presentatie/entitlement-doeleinden.

**Verificatiepunt:** de exacte contractuele relatie tussen een installatie-licentie (`full`) en een tenant-tier (`professional`/`enterprise`) is niet volledig als formele invariant in code vastgelegd. Dit moet productmatig worden beslist en getest.

## 8. Commerciële licentiemodellen

`COMMERCIAL_LICENSE.md` beschrijft een dual-license-model:

| Juridische optie | Gebruik | Hoofdconsequentie |
|---|---|---|
| AGPLv3 | Open-sourcegebruik onder AGPL-voorwaarden | Bij netwerkgebruik gelden source-sharing-verplichtingen volgens AGPLv3 |
| Commercial License | Proprietary gebruik, SaaS of vermijden van AGPL-source-sharing | Contractuele rechten, warranty, indemnification en support/SLA-opties |

De huidige commerciële prijstabel is:

| Commercieel product | Scope | Richtprijs |
|---|---|---:|
| Startup | 1 tenant, maximaal 5 gebruikers | EUR 499/jaar |
| Professional | 1 tenant, onbeperkte gebruikers | EUR 1.999/jaar |
| Enterprise | Onbeperkte tenants en gebruikers, custom SLA | EUR 4.999/jaar |
| OEM / Embedding | Integratie in eigen product | Op aanvraag |

Deze prijzen zijn volgens de documentatie exclusief btw, jaarlijks gefactureerd, met een 30-dagen-geld-teruggarantie. De contactpersoon in het document is `gast@example.com`; dit is een placeholder en moet vóór externe publicatie worden vervangen.

### Verschil met SaaS-tiers

De commerciële tabel en de applicatietiers hebben momenteel verschillende namen en grenzen:

- `Startup` bestaat in de commerciële tabel, maar niet als technische SaaS-tier; de dichtstbijzijnde technische tier is `starter`.
- Commercieel `Professional` betekent 1 tenant en onbeperkte gebruikers, terwijl technisch `professional` maximaal 25 gebruikers en 500 cases per tenant toestaat.
- Commercieel `Enterprise` noemt onbeperkte tenants en gebruikers; technisch Enterprise laat resources onbeperkt, maar SpiderFoot blijft begrensd op 10 gelijktijdige scans.
- `OEM / Embedding` bestaat commercieel, maar is geen tenant-tier in `cms/tier_limits.py`.

Dit is geen fout zolang het bewust als twee verschillende catalogi wordt beheerd, maar het moet expliciet worden vastgelegd om verkoop- en productverwachtingen niet te laten botsen.

## 9. Stripe- en abonnementsopties

De Stripe-integratie ondersteunt:

- checkout voor een tenant-admin;
- subscription-mode met één Stripe Price ID;
- upgrades en downgrades via Stripe subscription-items;
- webhookverwerking voor betaal- en subscriptionstatussen;
- proratie-logging in `ProrationLog`;
- dunningconfiguratie met standaard maximaal 3 retries, notificatie na 1 dag en downgrade na 7 dagen;
- customer portal wanneer een Stripe customer bestaat.

De mapping is bewust configureerbaar:

```text
PlatformSetting: stripe_price_mapping
vorm: {"<stripe_price_id>": "<technical_tier>"}
```

De repository bevat geen productie-Price IDs of vastgelegde maandprijzen. De analyticsmodule bevat wel een interne default-prijstabelreferentie, maar de daadwerkelijke commerciële waarheid moet uit de actuele Stripe-configuratie en goedgekeurde pricingbeslissing komen.

**CFO-risico:** een Stripe Price ID zonder correcte mapping kan checkout of webhook-updates laten falen of de verkeerde technische tier toekennen. De mapping is daarom een productieconfiguratie die versioned/exported/documented moet worden zonder geheime Stripe-keys te publiceren.

## 10. Privacy- en integratieopties

De license server heeft afzonderlijke opt-in tiers voor IP-verrijking. Dit zijn geen verkooptiers:

| Optie | Default | Externe bron | Voorbeelddata |
|---|---|---|---|
| HTTP-requestmetadata | Aanwezig in request | Geen externe bron | User-Agent, taal, HTTP-versie, tijd |
| PTR | Uit | Lokale reverse DNS | Hostname |
| RDAP | Uit | `rdap.org` | Netname, ASN-organisatie, land, CIDR |
| Geo/IP API | Uit | `ip-api.com` | Land, stad, coördinaten, ISP, ASN, proxy/hosting/mobile |

Deze opties hebben gevolgen voor privacygrondslag, subprocessors, bewaartermijn en kosten/afhankelijkheid. Ze mogen niet stilzwijgend aan worden gezet.

## 11. Wat is technisch al geregeld?

- Tenant-tierlimieten zijn centraal in code gedefinieerd.
- Resource create/upload/scan-paden controleren relevante limieten.
- Feature-gates geven een expliciete fout of redirect bij onvoldoende entitlement.
- Trial enforcement werkt los van en bovenop tenant-tierdefaults.
- Installatielicenties zijn cryptografisch ondertekend en lokaal verifieerbaar.
- Stripe-mapping en dunning zijn configureerbaar.
- Proration events worden opgeslagen.
- Super-admins kunnen per tenant feature overrides instellen.
- Licentie- en telemetriegegevens hebben een afzonderlijk privacy- en retentieontwerp.

## 12. Wat is nog niet formeel vastgelegd?

De volgende punten zijn product- of governancebeslissingen, geen aannames:

1. Is `Startup` formeel hetzelfde als technische `starter`?
2. Moet commercieel Professional de technische limiet van 25 gebruikers behouden, of moet de technische limiet worden verhoogd naar onbeperkt?
3. Hoe wordt de commerciële Enterprise-belofte van onbeperkte gelijktijdige activiteit verenigd met de technische SpiderFoot-limiet van 10?
4. Is OEM/Embedding uitsluitend een juridische licentie, of moet hiervoor een afzonderlijk technisch entitlement bestaan?
5. Welke installatie-licentie (`full`, `professional`, `enterprise` of anders) geeft recht op welke tenant-tiers?
6. Wat gebeurt er bij downgrade wanneer de tenant al boven de nieuwe limiet zit: blokkeren van nieuwe objecten, read-only, archiveren of contractuele grace period?
7. Welke prijzen en valuta zijn de actuele commerciële waarheid: de statische documentatie, Stripe of een nog goed te keuren prijslijst?
8. Zijn maandabonnementen, jaarabonnementen, seat-based pricing en usage-based pricing allemaal ondersteund, of alleen jaar-/subscription-prijzen?
9. Welke SLA-, support-, dataretentie- en onboardingrechten horen bij elk commercieel product?
10. Wie mag de configureerbare `FeatureFlag`-override gebruiken en hoe wordt die commercieel/auditmatig verantwoord?

## 13. Aanbevolen canonical model

Voor verificatie en toekomstige verkoopdocumentatie is deze scheiding aan te bevelen:

```text
CommercialOffer
  - juridische licentie: AGPLv3 | Commercial | OEM
  - prijs, valuta, facturatieperiode, SLA en support

InstallationEntitlement
  - trial | full
  - geldig vanaf/tot, revocatiestatus

TenantPlan
  - free | starter | professional | enterprise
  - resource limits en feature defaults

OperationalOverride
  - expliciete, geauditte uitzonderingen per tenant
```

De koppeling tussen deze objecten moet vervolgens expliciet worden vastgelegd in een entitlement-matrix. Zolang die matrix ontbreekt, moet de technische implementatie niet worden gepresenteerd alsof de vier SaaS-tiers automatisch de vier commerciële contracten zijn.

## 14. Verificatiechecklist voor ChatGPT en CFO

- Controleer dat elke genoemde limiet exact overeenkomt met `cms/tier_limits.py`.
- Controleer dat `None` als onbeperkt wordt uitgelegd, behalve waar een aparte concurrency-limiet bestaat.
- Controleer het onderscheid tussen tenant-tier, installatie-trial/full en commerciële AGPL/commercial/OEM-rechten.
- Controleer dat de commerciële prijzen niet als actuele Stripe-prijzen worden gepresenteerd zonder actuele Stripe-configuratie.
- Controleer dat feature flags geen contractuele rechten vervangen.
- Controleer dat `LICENSE_ENFORCEMENT=off` geen commerciële licentie creëert.
- Controleer bij elke upgrade/downgrade de gevolgen voor bestaande data boven de nieuwe limiet.
- Controleer de privacy- en subprocessorimpact van PTR, RDAP en geo/IP-verrijking.
- Publiceer geen echte Stripe keys, license secrets, install tokens of private keys in aanvullend materiaal.
- Laat de open beslispunten in sectie 10 formeel goedkeuren voordat pricing, salesmateriaal of contracttemplates definitief worden verklaard.
