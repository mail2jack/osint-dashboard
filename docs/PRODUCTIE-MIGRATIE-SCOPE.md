# Besluit: scope van de latere productiemigratie

**Vastgelegd:** 29 september 2026

## Expliciete grens

De productiemigratie wordt pas gestart nadat de eigenaar daarvoor expliciet
toestemming heeft gegeven. Een voorbereid plan, technische toegang of een
geslaagde stagingtest geldt niet als toestemming om productie te wijzigen.

## Goedgekeurde veilige basis

Bij de latere migratie wordt uitsluitend meegenomen:

- de **Default Organization / default company**;
- één gebruiker: `ivan.versteegh@protonmail.com`;
- de noodzakelijke configuratie en instellingen;
- de API-configuratie en API-sleutels, veilig overgenomen zoals eerder op
  staging is gedaan;
- de benodigde licentie- en integratie-instellingen.

Niet meenemen:

- oude dossiers/cases;
- oude clients;
- oude subjects;
- oude findings en onderzoeksresultaten;
- andere tenantbedrijven;
- oude testdata.

## Uitvoeringsregel

Voor de daadwerkelijke overgang wordt eerst een apart migratieoverzicht
gemaakt met aantallen en controles. De eigenaar krijgt daarna een concreet
go/no-go-moment. Zonder expliciet akkoord worden geen productiegegevens,
DNS-instellingen, gebruikers of services gewijzigd.
