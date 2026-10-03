# Besluit: scope van de latere productiemigratie

**Vastgelegd:** 3 oktober 2026

## Expliciete grens

De productiemigratie wordt pas gestart nadat de eigenaar daarvoor expliciet
toestemming heeft gegeven. Een voorbereid plan, technische toegang of een
geslaagde stagingtest geldt niet als toestemming om productie te wijzigen.

## Goedgekeurde veilige basis

Bij de latere migratie wordt uitsluitend meegenomen:

- een lege **Default Organization / default company**;
- precies één nieuw aangemaakte gebruiker: `ivan.versteegh@protonmail.com`;
- de noodzakelijke technische configuratie en platforminstellingen;
- de API-configuratie en API-sleutels, veilig overgenomen zoals eerder op
  staging is gedaan;
- de benodigde licentie- en integratie-instellingen;
- een nieuw wachtwoord en opnieuw geregistreerde 2FA voor de gebruiker.

Niet meenemen:

- oude dossiers/cases;
- oude clients;
- oude subjects;
- oude findings en onderzoeksresultaten;
- andere tenantbedrijven;
- oude testdata;
- oude sessies, recovery-codes en authenticatietokens.

## Omgevingen na de overgang

- De huidige staging-VPS wordt `joost.iveras.com` en bevat alleen de hierboven
  genoemde lege basisomgeving.
- De huidige productie-VPS wordt licentie-, backup- en stagingserver.
- Die nieuwe stagingomgeving gebruikt uitsluitend synthetische testdata en
  krijgt geen productiecases, clients, subjects, findings of rapporten.

## Uitvoeringsregel

Voor de daadwerkelijke overgang wordt eerst een apart migratieoverzicht
gemaakt met aantallen en controles. De eigenaar krijgt daarna een concreet
go/no-go-moment. Zonder expliciet akkoord worden geen productiegegevens,
DNS-instellingen, gebruikers of services gewijzigd.
