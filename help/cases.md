# Cases

Een **Case** is de centrale organisatie-eenheid van het systeem. Het groepeert subjects, findings, documenten, financiële gegevens en opmerkingen in één onderzoek.

## Case Velden

| Veld | Omschrijving |
|------|-------------|
| **Title** | Korte beschrijvende naam van het onderzoek |
| **Status** | Open / In Progress / On Hold / Closed / Archived |
| **Priority** | Low / Medium / High / Critical |
| **Client** | De opdrachtgever (verplicht) |
| **Description** | Uitgebreide omschrijving van het onderzoek |
| **Tags** | Labels voor categorisatie |

## Status Transities

De mogelijke statusovergangen:

- **Open** → **In Progress** (onderzoek gestart)
- **In Progress** → **On Hold** (wacht op informatie)
- **In Progress** → **Closed** (afgerond)
- **On Hold** → **In Progress** (onderzoek hervat)
- **Closed** → **Archived** (gearchiveerd)

Gebruik de **State** sectie op de case detailpagina om de status te wijzigen.

## Een Case Aanmaken

1. Klik **Cases** in de header, dan **New Case**
2. Vul de verplichte velden in (Titel, Client)
3. Selecteer Status en Prioriteit
4. Klik **Save**

De case wordt aangemaakt en je wordt doorgestuurd naar de detailpagina.

## Case Detailpagina

De detailpagina toont verschillende secties:

### Subjects
Gelinkte personen, bedrijven of schepen. Klik **Link Subjects** om bestaande subjects te koppelen of nieuwe aan te maken. Eén subject kan aan meerdere cases zijn gekoppeld.

### Findings
OSINT-bevindingen georganiseerd per subject. Elke finding heeft een type, betrouwbaarheidsscore en bron. Findings kunnen worden toegevoegd via handmatige invoer, SpiderFoot-import, of OSINT Search.

### Documenten
Geüploade bestanden en screenshots (max 16 MB per bestand). Ondersteunde formaten: PDF, afbeeldingen, Office-documenten.

### Financiële Gegevens
Banktransacties, facturen en betalingsregels. Elke financial record heeft een type (credit/debet), bedrag, datum en tegenpartij.

### Opmerkingen
Interne teamnotities. Opmerkingen kunnen worden bewerkt en hebben een bewerkingsgeschiedenis.

### Audit Log
Een compleet logboek van wie wat heeft gedaan en wanneer.

## Rapporten

Het **Reports** tabblad biedt:

- **Case Summary** — overzicht gegenereerd uit case data
- **CSV Export** — exporteer findings naar CSV
- **PDF Export** — exporteer case rapport naar PDF (indien geconfigureerd)

## Cases Zoeken

Gebruik de zoekbalk bovenaan de cases lijst om te filteren op titel, status, client of tags.

## Paginering

Findings, documenten en financials worden getoond in pagina's van 20 stuks. Gebruik de **Previous** / **Next** knoppen om te navigeren.
