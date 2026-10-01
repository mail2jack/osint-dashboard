# Joost — open eigenaarbesluiten providers en telemetry

**Status:** voorstel voor besluitvorming  
**Datum:** 30 september 2026  
**Productie:** dit document geeft geen toestemming voor productiewijzigingen

Dit document bevat alleen de resterende keuzes waarvoor repository- of
stagingonderzoek geen betrouwbaar antwoord kan geven. De voorgestelde
standaard is bewust behoudend.

| Onderwerp | Voorgestelde standaard | Huidige status |
|---|---|---|
| Ollama | Niet op de huidige 4 GB staging-VPS installeren; later een aparte/zwaardere AI-host gebruiken | Akkoord als technische aanbeveling, nog niet gebouwd |
| OpenRouter | Alleen minimaal noodzakelijke context; geen volledige dossiers, gevoelige identifiers of afbeeldingen zonder aparte goedkeuring | Technisch beschikbaar, beleidsmatig Pending |
| Brave | Query’s met persoonsgegevens minimaliseren; geen permanente opslag van API-resultaten tenzij het abonnement dat expliciet toestaat | Technisch beschikbaar, beleidsmatig Pending |
| CheckLeaked | Niet gebruiken voor echte dossiers totdat retentie, internationale doorgifte, doel en verwijderproces expliciet zijn goedgekeurd | Technisch aanwezig, aanbevolen blokkade voor dossierdata |
| RapidAPI sociale kanalen | Per kanaal afzonderlijk beoordelen; betaald en standaard uit buiten expliciete tenant-opt-in | Codegate getest; staging opt-in staat aan |
| Overheid.io | Alleen doelgebonden tijdelijke caching; voorwaarden en datasetlicentie vóór productie opnieuw bevestigen | Technisch beschikbaar, beleidsmatig Pending |
| Tor | Alleen bij expliciete gebruikersactie; altijd HTTPS-doelen gebruiken; Tor niet presenteren als volledige anonimiteitsgarantie | Technisch beschikbaar |
| Telemetry | De huidige technische velden blijven toegestaan en worden bewaard totdat nieuwe waarden ze vervangen; providerretentie en klantinformatie nog vastleggen | Technisch actief, intern beleid nog te bevestigen |
| WhatsApp | In productie uitgeschakeld; later per tenant, handmatig verzenden, geen automatische onderzoeksdata | Besluit staat vastgelegd |
| Providerregister | Per provider én per datastroom doel, input, regio, retentie, DPA/voorwaarden, grondslag en verwijderprocedure invullen | Technisch register opgesteld |

## Aanbevolen volgorde

1. CheckLeaked voorlopig niet gebruiken met echte dossierdata.
2. OpenRouter alleen gebruiken met geminimaliseerde tekst en een gekozen
   model/provider waarvan de datapraktijken zijn gecontroleerd.
3. Per klant of productiegebruik de providergoedkeuring en grondslag vastleggen.
4. Telemetryretentie op de eigen licentieserver intern vastleggen.
5. Pas daarna eventueel providers formeel als toegestaan markeren.

## Belangrijke grens

Een technische API-key, actieve feature flag of geslaagde health-check is geen
privacygoedkeuring. Bij ontbrekende providerinformatie blijft de status
**Pending** en gebruiken we uitsluitend synthetische testdata.

## Gerelateerde documenten

- `PROVIDER-REGISTER-STAGING-TECHNICAL.md`
- `JOOST_MASTER_STATUS_2026-09-30.md`
- `PLAN-TELEMETRY-PRIVACY-GOVERNANCE.md`
- `EXTERNAL-DATAFLOW-INVENTORY.md`
