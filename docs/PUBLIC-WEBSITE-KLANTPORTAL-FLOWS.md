# Kernflows publieke website en klantportal

**Status:** eerste UX-/functioneel ontwerp
**Datum:** 2026-10-03

Dit document beschrijft de eerste schermstromen. Het is geen definitieve
visuele vormgeving en bevat geen productiegegevens.

## 1. Publieke homepage

### Doel

In enkele seconden duidelijk maken wat Iveras doet, voor wie Joost bedoeld is
en wat de bezoeker als volgende stap kan doen.

### Schermopbouw

```text
┌────────────────────────────────────────────────────────────┐
│ Iveras                         Diensten  Joost  Contact  EN │
├────────────────────────────────────────────────────────────┤
│ Onderzoek met overzicht, onderbouwing en controle           │
│                                                            │
│ OSINT-onderzoek voor organisaties die betrouwbare           │
│ informatie nodig hebben.                                   │
│                                                            │
│ [Plan een kennismaking]   [Bekijk hoe het werkt]           │
├────────────────────────────────────────────────────────────┤
│  Onderzoeken  │  Verifiëren  │  Rapporteren                │
├────────────────────────────────────────────────────────────┤
│ Werkwijze: aanvraag → onderzoek → verificatie → rapport    │
├────────────────────────────────────────────────────────────┤
│ Beveiliging, privacy en menselijke beoordeling              │
├────────────────────────────────────────────────────────────┤
│ Contact / demo-aanvraag                                    │
└────────────────────────────────────────────────────────────┘
```

### Regels

- geen operationele zoekvelden op de publieke website;
- geen onderzoeksresultaten of persoonsgegevens in marketinganalytics;
- iedere call-to-action leidt naar contact of een portal-login;
- mobiel ontwerp vanaf het begin meenemen.

## 2. Klantportal-dashboard

### Doel

Een klant ziet direct welke opdrachten aandacht nodig hebben en welke rapporten
beschikbaar zijn.

### Schermopbouw

```text
┌────────────────────────────────────────────────────────────┐
│ Iveras Portal       Organisatie ▾       Meldingen  Account  │
├────────────────────────────────────────────────────────────┤
│ Goedemiddag, [naam]                       [+ Nieuwe opdracht]│
│                                                            │
│ ┌────────────┐ ┌────────────┐ ┌────────────┐               │
│ │ In behandeling│ │ Wacht op info│ │ Rapporten │             │
│ │      2       │ │      1       │ │      3     │             │
│ └────────────┘ └────────────┘ └────────────┘               │
│                                                            │
│ Recente opdrachten                                         │
│ Referentie    Status              Laatste wijziging  Actie  │
│ C-2026-001    In onderzoek        03-10-2026         Bekijk │
│ C-2026-002    Rapport beschikbaar  02-10-2026         Open   │
│                                                            │
│ Alleen door Iveras vrijgegeven informatie wordt getoond.   │
└────────────────────────────────────────────────────────────┘
```

### Toegangsregels

- de gebruiker ziet alleen opdrachten van de eigen tenant;
- klantlezers zien geen actieknop voor nieuwe opdrachten;
- een rapport verschijnt pas na expliciete vrijgave;
- downloads zijn tijdelijk gelimiteerd en worden geaudit;
- foutmeldingen geven geen informatie over andere tenants of interne IDs.

## 3. Nieuwe opdracht indienen

### Doel

Een klant kan voldoende context geven voor een eerste intake zonder interne
onderzoeksvelden te hoeven begrijpen.

### Stappen

```text
1. Type opdracht
   - persoon
   - organisatie
   - digitaal spoor / account
   - anders

2. Onderzoeksvraag
   - wat moet worden onderzocht?
   - gewenste uitkomst
   - relevante context

3. Betrokken gegevens
   - naam of organisatie
   - bekende gebruikersnamen
   - e-mail/telefoon, alleen indien noodzakelijk
   - documenten of bijlagen

4. Scope en toestemming
   - doel en bevoegdheid bevestigd
   - gewenste deadline
   - beperkingen of uitgesloten bronnen

5. Controle en verzenden
   - samenvatting
   - privacywaarschuwing
   - [Opdracht indienen]
```

Na verzenden krijgt de klant een portalreferentie. Joost maakt vervolgens een
interne intake of casus aan met de minimale noodzakelijke gegevens. De klant
krijgt niet automatisch toegang tot ruwe findings, zoektermen, interne notities
of AI-prompts.

## 4. Statusmodel voor klanten

De portal gebruikt een beperkt statusmodel, los van interne Joost-statussen:

| Portalstatus | Betekenis voor klant |
|---|---|
| Ontvangen | De aanvraag is ontvangen |
| Intake nodig | Iveras heeft aanvullende informatie nodig |
| In onderzoek | Het onderzoek loopt |
| Interne controle | Resultaten worden gecontroleerd |
| Rapport beschikbaar | Een rapport is vrijgegeven |
| Afgerond | De opdracht is afgerond |
| Gepauzeerd | De opdracht wacht op informatie of besluit |

Interne statussen en foutdetails worden niet rechtstreeks doorgegeven.

## 5. Beslispunten voor de volgende review

1. Is de voorgestelde klanttaal begrijpelijk genoeg voor niet-onderzoekers?
2. Mag een klant meerdere opdrachten tegelijk indienen?
3. Moet de klant bestanden kunnen uploaden in de eerste versie?
4. Wil Iveras klanten tussentijdse statusupdates laten zien?
5. Is rapportvrijgave altijd handmatig door Iveras?

## 6. Aanbevolen bouwvolgorde

1. Statische homepage zonder backendkoppeling;
2. portal-login en lege dashboardstaat met synthetische data;
3. opdrachtformulier dat alleen naar een testendpoint schrijft;
4. autorisatie- en tenanttests;
5. pas daarna de koppeling met Joost-productieachtige API's.
