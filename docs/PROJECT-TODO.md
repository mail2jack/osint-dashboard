# Joost / Iveras — centrale projecttodo

Laatste bijgewerkt: 10 oktober 2026
Werkafspraak: per beurt werken we aan één actieve werkstroom. Andere punten blijven geparkeerd totdat ze expliciet worden hervat.

## Actieve focus

**Werkstroom A — Publieke website en klantenportaal**
Doel: een duidelijke publieke website met een veilige klantomgeving waarin klanten aanvragen kunnen indienen en onderzoekers deze gecontroleerd kunnen omzetten naar zaken in Joost.

Volgende concrete stap: klantaccount, portaaltoegang en financiële afhandeling scheiden van een volledige Joost-onderzoekstenant.

### Belangrijke architectuurkeuze

Iedere externe klant krijgt een eigen beveiligde klantomgeving. Dat geldt ook
voor een eenmalige klant: die moet rapporten kunnen lezen, documenten kunnen
ontvangen, facturen kunnen bekijken en financiële zaken kunnen afhandelen.

Daarbij maken we onderscheid tussen:

- **Klantaccount**: portaalidentiteit, rapporten, communicatie, facturen en betalingen.
- **Joost-onderzoekstenant**: geïsoleerde onderzoeksdata en medewerkers die in Joost werken.

Een eenmalige klant krijgt dus wel een klantaccount en portaaltoegang, maar niet
automatisch een volledige onderzoekstenant met eigen onderzoekers, instellingen
en onderzoeksruimte. Een organisatie met een structurele samenwerking krijgt
beide.

## Statuslegenda

- ✅ afgerond
- 🟡 gebouwd, maar nog testen of besluit nodig
- 🔵 gepland
- ⏸️ geparkeerd
- 🔴 probleem of blokkade

---

## Werkstroom A — Publieke website en klantenportaal

### A1. Publieke website

- ✅ Publieke homepage beschikbaar via `/public`.
- ✅ Uitleg over OSINT, werkwijze, doelgroepen, privacy en FAQ.
- ✅ Navigatie naar login en portaal.
- 🟡 Teksten redigeren naar definitieve bedrijfspositionering.
- 🔵 Definitieve huisstijl bepalen: logo, kleuren, typografie en beeldtaal.
- 🔵 Definitieve contact- en conversiepagina toevoegen.
- 🔵 Dienstenpagina maken met duidelijke afbakening van wat wel en niet wordt onderzocht.
- 🔵 Privacy-, cookie- en juridische teksten laten aansluiten op de definitieve dienstverlening.
- 🔵 SEO-basis: paginatitels, beschrijvingen, sitemap en zoekmachine-instellingen.
- 🔵 Publieke site voorbereiden voor productiehosting op de nieuwe productie-VPS.

### A2. Klantregistratie en toegang

- ✅ Authenticated portal dashboard beschikbaar.
- ✅ Tenant-scoping toegepast op portaldata.
- ✅ Gekozen model: registratieaanvraag met beoordeling; optie voor eenmalig onderzoek of eigen organisatie-tenant.
- ✅ Architectuur vastgelegd: iedere klant krijgt een portalaccount; alleen organisaties krijgen daarnaast een volledige onderzoekstenant.
- ✅ Uitnodigingsflow technisch getest met een synthetische portal-only klant.
- ✅ Uitnodigingsmail, uitnodigingslink, klantaccount, 2FA-configuratie en opnieuw inloggen zijn met een synthetische klant lokaal getest; Mailpit-bezorging is bevestigd.
- ✅ Rollenmodel bepaald: `portal_admin`, `portal_member` en `portal_viewer`; een organisatie behoudt daarnaast de interne tenantrollen.
- ✅ Publieke wachtwoordreset toegevoegd en lokaal getest met herstelmail, nieuw wachtwoord, 2FA-login en eenmalige token.
- 🔵 Klant kan alleen eigen aanvragen, zaken, rapporten en documenten zien.
- ✅ Autorisatietests uitgevoerd met twee afzonderlijke tenants; klant kan geen zaak, rapport, document of factuur van een andere tenant openen.

### A3. Nieuwe onderzoeksaanvraag

- ✅ `/portal/new-request` aanwezig.
- ✅ Aanvraag wordt tenant-scoped opgeslagen.
- ✅ Velden aanwezig voor type, onderzoeksvraag, context en gewenste termijn.
- ✅ Aanvraag verschijnt in het klantenportaal.
- ✅ Onderzoeker kan aanvraag intern beoordelen.
- ✅ Voor nu behandelt de tenant-`admin` de intake; toekomstige verbetering: aparte administratieve rol voor portal-intake zonder volledige superadminrechten.
- ✅ Eenmalige aanvraag leidt tot een geïsoleerde portal-only klantomgeving, niet tot een volledige onderzoekstenant.
- ✅ Goedkeuring maakt de portal-only klantomgeving en uitnodiging aan; rapport-, document- en factuurweergave zijn lokaal aangesloten en synthetisch getest.
- ✅ Goedgekeurde eenmalige aanvragen worden als clientgebonden portalopdracht vastgelegd en blijven zichtbaar totdat ze naar een zaak zijn omgezet.
- ⏸️ Betaalprovider kiezen en echte betaalafhandeling koppelen — bewust opengehouden voor later; factuurweergave werkt al.
- ✅ Alleen goedgekeurde organisatie-aanvragen krijgen daarnaast een volledige onderzoekstenant.
- ✅ Bevestiging na indienen toont een aanvraagreferentie en de eerstvolgende status.
- ✅ Statussen definitief gemaakt en consistent zichtbaar: ontvangen, in beoordeling, aanvullende informatie, geaccepteerd, afgewezen en omgezet naar zaak.
- ✅ E-mailmelding bij nieuwe aanvraag naar platformbeheerders en bevestiging naar de aanvrager.
- ✅ E-mailmelding bij statuswijziging naar de aanvrager.
- ✅ Flow “Andere vraag” handmatig getest via website en klantregistraties; voor nu akkoord bevonden.
- 🔵 Onderzoeker kan aanvullende vragen terugsturen naar de klant.
- ✅ Klant kan aanvullende informatie en beveiligde documenten aan een aanvraag toevoegen; upload/download is tenant- en klantscope-beveiligd en wordt gelogd.
- ✅ Aanvraag gecontroleerd omzetten naar een zaak.
- ✅ Bij omzetting klant, vraag, context, gewenste termijn, bijlagen en auditinformatie correct overnemen.
- ✅ Dubbele of identieke aanvragen herkennen en markeren zonder automatisch te blokkeren of verwijderen.

### A4. Klantportaal na omzetting naar zaak

- ✅ Portaal toont tenant-scoped zaken.
- ✅ Productie end-to-end gecontroleerd: registratie, goedkeuring, uitnodiging, login, aanvraagweergave, omzetting naar zaak en klantcommunicatie.
- ✅ Bepalen welke zaakgegevens de klant mag zien: klant ziet referentie, onderwerp, klantstatus, datum, vrijgegeven rapporten en documenten.
- ✅ Status van de zaak tonen met begrijpelijke klanttaal.
- ✅ Rapporten alleen tonen wanneer ze door de onderzoeker zijn vrijgegeven.
- 🔵 Bevindingen niet automatisch volledig aan de klant tonen.
- ✅ Download van definitieve rapporten is klant- en tenantgebonden, alleen beschikbaar na vrijgave en wordt geaudit.
- ✅ Klant kan vragen stellen over een lopende zaak via een tenantgebonden berichtenkanaal zonder interne velden te wijzigen; klant en behandelaar krijgen notificaties en gelezen-statussen worden bijgewerkt.
- ✅ Notificatiecentrum voor aanvragen en statuswijzigingen; meldingen zijn gebruikers- en tenantgebonden en kunnen als gelezen worden gemarkeerd.
- ✅ Klantportaal getest met een synthetische klanttenant.
- ✅ Portal-only klant en CMS-blokkade getest; rapport, documentdownload, factuurweergave en cross-tenant afscherming end-to-end getest.

### A5. Portaalveiligheid en oplevering

- ✅ Basis tenant-isolatie aanwezig; negatieve autorisatietests voor rapporten, documenten en facturen geslaagd.
- ✅ Portal-only gebruikers worden naar het klantenportaal beperkt en niet toegelaten tot de interne CMS-werkomgeving.
- ✅ Rapportweergave, factuurweergave en documentdownloads worden usage-gemeten en geaudit.
- ✅ CSRF en rate limiting zijn op portalregistratie, aanvraag-, bericht-, profiel-, notificatie- en downloadacties aangesloten; geautomatiseerde herhaalde-aanvraagcontrole is toegevoegd.
- ✅ Geen productiegegevens gebruiken in portaaltests; laatste geautomatiseerde tests gebruiken uitsluitend synthetische data.
- ✅ Audit logging gecontroleerd: acties worden vastgelegd zonder volledige berichtinhoud of onderzoekswaarden.
- ✅ Back-updecompressie, archiefintegriteit en tijdelijk databaseherstel voor portaldata getest.
- ✅ Lokale productiechecklist vastgelegd in [PORTAL-RELEASE-CHECKLIST.md](PORTAL-RELEASE-CHECKLIST.md); productie-openstelling blijft apart besluit.
- ✅ Portal-entrypoint `/portal` verwijst naar het echte tenantgebonden dashboard; de oude synthetische demopagina is uit de productieflow gehaald.

---

## Werkstroom B — Prijsmodel en tarieven

Deze werkstroom staat los van de portaalbouw. Alleen hervatten wanneer prijsstrategie of facturatie expliciet aan de orde is.

- ✅ Eerste marktvergelijking en tarieveninventarisatie uitgevoerd.
- ✅ Actie-kostenmatrix opgesteld.
- ✅ Kostenmetadata in het actie-register toegevoegd.
- ✅ Informatieve usage-meting en audit logging lokaal toegevoegd.
- 🟡 Kostenindicatie vóór het starten van acties zichtbaar in de lokale omgeving.
- 🟡 Findings Dashboard- en portaalgebruik wordt lokaal gemeten.
- 🔵 Werkelijke providerkosten gedurende representatieve tests verzamelen.
- 🔵 Gratis, inbegrepen en betaalde acties definitief classificeren.
- 🔵 Pakketten ontwerpen: bijvoorbeeld basis, professioneel en enterprise.
- 🔵 Abonnement versus pay-per-use vergelijken.
- 🔵 Prijs voor menselijke onderzoeksuren bepalen.
- 🔵 Offerte- en facturatieflow ontwerpen.
- 🔵 Credits, reservering en reconciliatie pas invoeren na expliciete prijsbeslissing.
- 🔵 Facturatie nooit activeren op basis van alleen een voorlopige kostenindicatie.

Referentie: [JOOST-ACTION-COST-MATRIX.md](JOOST-ACTION-COST-MATRIX.md)

---

## Werkstroom C — Findings Dashboard

Deze werkstroom is functioneel belangrijk, maar staat nu niet bovenaan zolang het portaal wordt afgerond.

- ✅ Findings Dashboard beschikbaar.
- ✅ Tenant-scoped totalen, statusverdeling, bronnen en kwaliteitssignalen.
- ✅ Gebruik van het dashboard wordt lokaal geregistreerd.
- 🟡 Dashboard vullen met representatieve synthetische data.
- 🔵 Filters toevoegen voor zaak, onderzoek, subject, status, bron en platform.
- 🔵 Klik vanuit statistiek naar de onderliggende findings.
- 🔵 Tijdlijn en instroom duidelijker maken.
- 🔵 Relatiegrafiek vanuit findings kunnen openen.
- 🔵 Export of samenvatting voor onderzoekers ontwerpen.
- 🔵 Performance testen bij honderden of duizenden findings.
- 🔵 Controleren dat gearchiveerde of uitgesloten findings niet onbedoeld meetellen.

---

## Werkstroom D — Onderzoeksworkflow en AI

- ✅ AI-onderzoeksplan maakt eerst voorstellen.
- ✅ Start van acties blijft expliciet.
- ✅ AI-rapport kan findings samenvatten.
- 🟡 Vraag van onderzoeker letterlijk en herkenbaar opnemen in het rapport.
- 🟡 Placeholdervelden zoals `[PERSON_NAME]` en `[ADDRESS]` voorkomen of vervangen.
- 🟡 Rapportopmaak verbeteren naar leesbare secties, tabellen en bronverwijzingen.
- 🔵 Accounts, telefoonnummers en relaties uit findings expliciet vervolgen.
- 🔵 Relatiegrafiek koppelen aan subjects en findings.
- 🔵 AI-resultaat altijd begrenzen tot de scope van de onderzoeksvraag.
- 🔵 Fals-positive signalen duidelijk labelen.
- 🔵 Gratis acties standaard; betaalde of exotische acties alleen na expliciete toestemming.
- 🔵 Foutafhandeling verbeteren wanneer een AI-provider HTML of ongeldige JSON teruggeeft.
- 🔵 OpenRouter en eventueel Ollama als afzonderlijke providerkeuze documenteren.

---

## Werkstroom E — Infrastructuur, productie en herstel

- ✅ Lokale Docker-ontwikkelomgeving beschikbaar.
- ✅ Nieuwe VPS voorbereid als productieomgeving.
- ✅ Oude VPS aangewezen voor backup- en licentietaken.
- ✅ TLS en domeinstructuur voorbereid.
- ✅ Fail2ban actief op de server.
- ✅ SpiderFoot en Tor gecontroleerd op staging.
- ✅ Back-upscript en geplande back-ups aanwezig.
- ✅ Back-updecryptie en tijdelijke databasehersteltest uitgevoerd.
- ✅ Nieuwe productieomgeving nog één keer gecontroleerd vóór ingebruikname; health en containers zijn gezond.
- ✅ Productie bijgewerkt via de gevalideerde master-merge; publieke health-check en kritieke smoke tests zijn geslaagd.
- 🟡 Alleen default company met één gebruiker overnemen; geen zaken, subjects, dossiers of rapporten.
- 🟡 API-configuratie gecontroleerd; OpenRouter bewust later invullen indien nodig.
- ✅ Licentie-uitgifte en telemetry tussen productie en license server gecontroleerd; productie heeft een actieve licentiestatus en recente check-in.
- ✅ Restoreprocedure documenteren inclusief sleutelbeheer in [PORTAL-RESTORE-RUNBOOK.md](PORTAL-RESTORE-RUNBOOK.md).
- 🟡 Monitoring en e-mailmeldingen voor mislukte back-ups geautomatiseerd gecontroleerd; een echte SMTP-failure-drill op productie blijft nog open.
- ✅ Productie-releaseproces vastgelegd: lokaal → GitHub → groene verplichte controles → gecontroleerde productie-update.
- 🔵 Geen directe wijzigingen op productie zonder expliciet akkoord.

---

## Werkstroom F — GitHub en ontwikkelproces

- 🟡 Lokale wijzigingen bestaan; nog niet automatisch gelijk aan GitHub.
- 🔵 Wijzigingen per logisch onderdeel committen.
- 🔵 Pre-commitcontrole uitvoeren: syntax, tests, migrations en diff-check.
- 🔵 Verplichte `integration-postgres`-controle correct in de ontwikkelstraat opnemen.
- 🔵 Pull request gebruiken voor wijzigingen die naar productie gaan.
- 🔵 Lokale Docker-test uitvoeren vóór merge.
- 🔵 Productie pas bijwerken na expliciet releasebesluit.
- 🔵 Rollbackprocedure per release documenteren.

---

## Werkafspraak voor volgende sessies

Bij ieder vervolg bepalen we eerst:

1. Welke werkstroom is actief?
2. Wat is het eerstvolgende concrete punt?
3. Is er een gebruikersbeslissing nodig?
4. Is de wijziging alleen lokaal, naar GitHub, of ook productie?
5. Welke controle bewijst dat het punt klaar is?

**Huidige focus:** Werkstroom A — Publieke website en klantenportaal.
**Niet automatisch oppakken:** prijsbeslissingen, productie-uitrol en migratie zonder expliciet akkoord.
