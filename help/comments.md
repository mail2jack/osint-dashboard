# Opmerkingen

Het **Comments** systeem biedt interne teamnotities binnen cases en subjects. Opmerkingen zijn alleen zichtbaar voor ingelogde gebruikers.

## Een Opmerking Plaatsen

Opmerkingen kunnen worden toegevoegd vanuit:

- **Case detailpagina** — algemene case opmerkingen
- **Subject detailpagina** — subject-specifieke opmerkingen
- **Finding detail** — bevinding-specifieke notities

Klik **Add Comment** of typ in het tekstveld en klik **Save**.

## Opmerking Types

| Type | Scope |
|------|-------|
| **General** | Algemene notitie |
| **Note** (subject) | Subject notitie (gemigreerd van het oude `notes` veld) |
| **Internal** | Interne team-opmerking |

## Opmerking Bewerken

Opmerkingen kunnen worden bewerkt:

1. Klik op het ✏️ icoon naast de opmerking
2. Pas de tekst aan
3. Klik **Save**

### Bewerkingsgeschiedenis

Elke bewerking wordt opgeslagen in de **Comment Edit History**. Je kunt de originele en vorige versies bekijken via de 📋 knop op de opmerking.

## Opmerking Verwijderen

Alleen de auteur of een admin kan een opmerking verwijderen. Klik op het 🗑️ icoon om te verwijderen.

## Notities vs Opmerkingen

Het oude `notes` veld op Subjects is gemigreerd naar het Comments model. Alleen subjects zonder bestaande opmerkingen met dezelfde inhoud krijgen een comment aangemaakt tijdens de migratie (idempotent).
