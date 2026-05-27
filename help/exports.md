# Exports

Het **Export** systeem maakt het mogelijk om data uit het dashboard te exporteren voor verdere verwerking of rapportage.

## CSV Export

Findings kunnen worden geëxporteerd naar CSV-formaat. Dit is beschikbaar vanuit:

- De **case detailpagina** (Reports tab → Export CSV)
- De **search resultaten pagina**

### CSV Velden

Het geëxporteerde CSV-bestand bevat:

- Type (email, IP, domein, etc.)
- Data (de gevonden waarde)
- Bron (welke module/service)
- Betrouwbaarheidsscore (0-100)
- Datum
- Case informatie
- Subject informatie

### Beperkingen

- Bij meer dan 5000 records verschijnt een waarschuwing
- Export gebruikt `yield_per(200)` om geheugen te beperken
- Alleen gebruikers met export-rechten kunnen exporteren

## Case Reports

Het **Reports** tabblad op de case detailpagina biedt:

- **Case Summary** — automatisch gegenereerd overzicht van de case met alle gekoppelde subjects, findings en documenten
- **PDF Export** — indien geconfigureerd, exporteer het volledige case rapport als PDF

## Audit Log Export

Audit logs kunnen worden bekeken en gefilterd op de **Audit** pagina maar worden niet direct geëxporteerd (raadpleeg de database voor bulk exports).
