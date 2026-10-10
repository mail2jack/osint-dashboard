# Publieke website en klantportal — plan

**Status:** ontwerp, fase 1
**Datum:** 2026-10-03
**Scope:** publieke Iveras-website en beveiligde klantomgeving naast Joost

## 1. Doel

Iveras krijgt een publieke website die helder uitlegt wat het bedrijf en Joost
bieden. Daarnaast komt er een klantportal waarin klanten veilig opdrachten,
voortgang en rapporten kunnen beheren. De portal communiceert uitsluitend via
een gecontroleerde API met Joost; de browser en portal krijgen geen directe
toegang tot de Joost-database.

## 2. Voorgestelde domeinen

| Onderdeel | Domein | Doel |
|---|---|---|
| Publieke website | `iveras.com` | Marketing, diensten, contact en juridische informatie |
| Klantportal | `app.iveras.com` | Klantlogin, opdrachten, status en rapporten |
| Interne applicatie | `joost.iveras.com` | Onderzoekers en interne administratie |
| Licentieserver | `license.iveras.com` | Licentie-uitgifte en beperkte telemetry |
| Backupomgeving | `backup.iveras.com` | Alleen backuptransport en beheer, geen publieke app |

De publieke website en klantportal worden technisch en operationeel gescheiden
van de interne Joost-applicatie. De klantportal mag later eventueel naar een
eigen host verhuizen zonder de interne applicatie te wijzigen.

## 3. Rollen en toegangsmodel

### Publiek

- anonieme bezoeker;
- contact- of demo-aanvrager.

### Klantportal

- **Klantbeheerder:** beheert de eigen organisatie en gebruikers;
- **Klantmedewerker:** maakt opdrachten aan en bekijkt toegestane resultaten;
- **Klantlezer:** alleen lezen en rapporten downloaden.

### Interne Joost-applicatie

- interne onderzoeker;
- senior onderzoeker;
- tenantbeheerder;
- platformbeheerder.

Elke portalrequest krijgt een expliciete tenantcontext. Object-ID's alleen zijn
nooit voldoende voor autorisatie. Alle muterende acties worden geaudit.

## 4. Eerste versie van de publieke website

De eerste versie bevat alleen noodzakelijke pagina's:

1. Home — probleem, oplossing en duidelijke call-to-action;
2. Diensten — onderzoek, rapportage en ondersteuning;
3. Joost — uitleg van het platform zonder operationele details;
4. Voor organisaties — klantgerichte werkwijze en beveiliging;
5. Over Iveras;
6. Contact/demo-aanvraag;
7. Privacyverklaring, cookiebeleid en algemene voorwaarden;
8. Nederlands en Engels.

De website slaat geen onderzoeksdata op. Een contactformulier schrijft alleen
de noodzakelijke aanvraaggegevens weg en krijgt eigen retentie- en
spam-beveiligingsregels.

## 5. Eerste versie van de klantportal

De portal wordt bewust klein gestart:

1. uitnodiging en registratie;
2. login met MFA;
3. organisatie- en gebruikerscontext;
4. dashboard met eigen opdrachten;
5. nieuwe opdracht/casus aanvragen;
6. status van een opdracht bekijken;
7. beschikbare rapporten veilig downloaden;
8. auditvriendelijke notificaties en foutmeldingen.

Onderzoeksacties, ruwe findings en interne notities blijven in Joost en worden
niet automatisch zichtbaar voor klanten. Alleen expliciet vrijgegeven
rapporten of statusinformatie komt naar de portal.

## 6. Integratie met Joost

De portal gebruikt een aparte, versioned API-laag met:

- korte-lived authenticatiesessies en MFA;
- tenant- en gebruikersautorisatie per endpoint;
- idempotency keys voor het aanmaken van opdrachten;
- statuswaarden met een expliciet contract;
- rapport-release als afzonderlijke handeling;
- audit logging van aanvragen, vrijgave en downloads;
- rate limiting, CSRF-bescherming en veilige foutmeldingen;
- geen API-key of databasecredential in de browser.

Voorgestelde eerste API-oppervlakte:

```text
POST /portal/v1/requests
GET  /portal/v1/requests
GET  /portal/v1/requests/{id}
GET  /portal/v1/requests/{id}/reports
GET  /portal/v1/reports/{id}/download
```

Deze endpoints vormen een adapter bovenop Joost. De interne datamodellen worden
niet als publiek contract hergebruikt; daardoor kunnen Joost en de portal
onafhankelijk evolueren.

## 7. Beveiliging en privacy

- productie- en portaldata blijven tenantgescheiden;
- de portal krijgt geen toegang tot niet-vrijgegeven findings;
- rapportdownloads zijn tijdelijk en worden geaudit;
- persoonsgegevens worden niet in marketinganalytics opgenomen;
- contactaanvragen krijgen een vaste bewaartermijn;
- externe AI- en OSINT-acties blijven interne, expliciet geautoriseerde acties;
- staging gebruikt uitsluitend testdata;
- publieke formulieren krijgen spam- en abuse-bescherming;
- secrets blijven in server-side configuratie en de bestaande settings-/secret
  infrastructuur.

## 8. Fases

### Fase 1 — fundament (nu)

- domeinen, rollen, sitemap en API-grens vastleggen;
- privacy- en gegevensstromen documenteren;
- portal-MVP afbakenen;
- één klantreis uitschrijven: uitnodiging → opdracht → vrijgegeven rapport.

### Fase 2 — publieke website

- contentstructuur en visuele stijl;
- responsive pagina's;
- contact/demoformulier;
- juridische pagina's en analyticsbeleid;
- lokale test en review.

### Fase 3 — portal-MVP

- login, MFA en tenantcontext;
- klantdashboard;
- opdracht indienen en status bekijken;
- testtenant met synthetische data.

### Fase 4 — Joost-integratie

- API-adapter;
- vrijgeven van rapporten;
- download/audit/notificaties;
- negatieve tenant- en autorisatietests.

### Fase 5 — pilot en livegang

- security review;
- privacycontrole;
- pilot met één klanttenant;
- rollback- en supportprocedure;
- daarna pas publieke lancering.

## 9. Besluiten die later nodig zijn

Deze keuzes blokkeren fase 1 niet, maar zijn nodig vóór bouw van het portal:

1. Wordt `app.iveras.com` het definitieve portaldomein?
2. Welke klantinformatie mag zichtbaar worden vóór rapportvrijgave?
3. Wordt een klant een bestaande Joost-tenant of komt er een aparte portaltenant
   met koppeling?
4. Welke notificaties gaan per e-mail en welke alleen in de portal?
5. Welke betaal- of abonnementsfuncties horen bij de eerste commerciële versie?

## 10. Eerstvolgende uitvoerbare stap

Maak een klikbaar scherm- en gebruikersstroomontwerp voor drie kernschermen:

1. publieke homepage;
2. portal-dashboard;
3. nieuwe opdracht indienen.

Daarna kunnen de API-contracten en de eerste portalroutes worden ontworpen
zonder al productiegegevens of productiecode te wijzigen.
