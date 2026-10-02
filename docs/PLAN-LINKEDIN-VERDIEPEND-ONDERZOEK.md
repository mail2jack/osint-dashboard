# Plan: expliciet gestart LinkedIn-verdiepingsonderzoek

## Doel

Een onderzoeker moet een gevonden LinkedIn-profiel expliciet kunnen selecteren
voor verdere analyse. Joost maakt dan een nieuwe onderzoeksactie aan, bewaart
de gebruikte profiel-URL en legt alle resultaten als afzonderlijke, herleidbare
findings vast.

## Grenzen

- De actie start nooit automatisch vanuit een AI-rapport.
- De onderzoeker bevestigt de profiel-URL en start de actie bewust.
- Alleen publiek en toegestaan beschikbare informatie wordt verwerkt.
- Geen omzeiling van login, afscherming, CAPTCHA, rate limits of technische
  toegangsbeperkingen.
- Elke finding bevat de bron-URL, tijdstip, status en de actie die de finding
  heeft geproduceerd.
- AI mag structureren en samenvatten, maar geen gegevens als feit toevoegen.

## Voorgestelde gebruikersflow

1. Joost herkent een LinkedIn-finding in een onderzoek.
2. De onderzoeker kiest **LinkedIn-profiel verdiepen**.
3. Joost toont de gevonden URL en vraagt bevestiging.
4. De onderzoeker kiest de toegestane uitvoermethode: bestaande finding,
   gecontroleerde browseractie of later een officiële integratie.
5. Joost voert alleen de gekozen actie uit en toont voortgang.
6. Resultaten worden als kandidaat-findings opgeslagen, gegroepeerd als:
   - profielidentiteit;
   - functie en organisatie;
   - opleiding en locatie;
   - zichtbare accounts en links;
   - genoemde personen of organisaties;
   - tijdlijngegevens.
7. De onderzoeker valideert findings en kan daarna een AI-onderzoeksrapport
   laten maken.

## Technische fasering

### Fase 1 — bestaande findings (nu)

LinkedIn-findings worden expliciet aan de rapportgenerator aangeboden. Het
rapport haalt profielgegevens uit de bestaande findingtekst en bewaart de
bronverwijzing.

### Fase 2 — expliciete actie op bestaande URL

Voeg een actieknop toe bij een LinkedIn-finding. Deze maakt één actievoorstel
aan met de profiel-URL, waarna de onderzoeker dit voorstel bewust start.

### Fase 3 — gecontroleerde verdieping

Voeg een beperkte extractor toe voor toegestane, publiek zichtbare gegevens.
De extractor moet blokkades herkennen, stoppen en de reden als findingstatus
opslaan in plaats van opnieuw te proberen.

### Fase 4 — rapport en relaties

De nieuwe findings worden gebruikt voor het AI-onderzoeksrapport en de
relatiegrafiek. Iedere voorgestelde relatie blijft kandidaat totdat de
onderzoeker deze valideert.

## Beslispunt vóór fase 2

Voor fase 2 moet nog worden bepaald welke toegestane uitvoermethode we willen
gebruiken: alleen bestaande findings, een gecontroleerde browseractie, of een
officiële API wanneer die beschikbaar en passend is.
