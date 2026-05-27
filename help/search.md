# Zoeken

De **Search** pagina biedt full-text zoeken door de gehele database: cases, subjects, clients, findings, documenten en opmerkingen.

## Basis Zoeken

1. Typ een zoekterm in het zoekveld
2. Resultaten worden gegroepeerd per categorie (Cases, Subjects, Clients, Findings)
3. Klik op een resultaat om direct te navigeren

De zoekopdracht doorzoekt de volgende velden:

- **Cases**: titel, omschrijving, tags
- **Subjects**: naam, notities
- **Clients**: naam, contactpersoon
- **Findings**: data, type, bron

## Full-Text Search (FTS)

Als PostgreSQL wordt gebruikt, is **full-text search** beschikbaar voor relevantere resultaten:

- Gebruikt PostgreSQL `tsvector` / `tsquery` met ranking
- Ondersteunt prefix matching
- Resultaten worden gesorteerd op relevantie

Op SQLite wordt teruggevallen op `LIKE`-gebaseerd zoeken (langzamer, minder accuraat).

## OSINT Search

De **OSINT Search** functionaliteit (te vinden in het Search tabblad of via het SpiderFoot menu) biedt:

### Email Search
Zoekt naar een e-mailadres in:
- Have I Been Pwned (datalekken)
- Social media
- Open bronnen

### Username Search
Zoekt een gebruikersnaam op honderden social media platforms via:
- Brave Search API (indien geconfigureerd)
- Open bronnen

### Phone Search
Verrijkt een telefoonnummer met:
- Carrier informatie
- Lijntype
- WhatsApp/Telegram aanwezigheid

## Zoekresultaten Exporteren

Resultaten kunnen worden geëxporteerd naar CSV. Bij meer dan 5000 resultaten krijg je een waarschuwing. De export gebruikt `yield_per(200)` om geheugenproblemen te voorkomen.

## Toetsenbord Navigatie

Op de search resultaten pagina:

- **s** — focus het zoekveld
- **j** — selectie omlaag
- **k** — selectie omhoog
- **Enter** — open geselecteerd item

## Tips

- Zoeken is **hoofdletterongevoelig**
- Gebruik specifieke termen voor betere resultaten
- Voor FTS (PostgreSQL) worden veelvoorkomende woorden (stopwords) genegeerd
- Je kunt zoeken op datum door de datum in het zoekveld te zetten (bijv. "2024-01")
