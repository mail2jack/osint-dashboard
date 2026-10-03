# Selectieve productie-export

Dit document beschrijft de veilige exportselectie voor de latere overgang van
staging naar een lege productieomgeving. Het is een ontwerp- en controlelijst;
het voert zelf geen export, import of verwijdering uit.

## Doelmodel

De nieuwe productie wordt opgebouwd vanuit een lege database en krijgt alleen:

1. een nieuwe Default Organization/default company;
2. één nieuwe gebruiker met e-mailadres `ivan.versteegh@protonmail.com`;
3. de expliciet goedgekeurde platformconfiguratie en API-instellingen;
4. de licentie-integratie en noodzakelijke technische instellingen.

Het wachtwoord en 2FA worden opnieuw ingesteld. Oude credentials, sessies,
recovery-codes en tokens worden dus niet geëxporteerd.

## Selectiebeleid

### Wel selecteren, na allowlistcontrole

- platforminstellingen die nodig zijn voor de applicatie;
- API/provider-instellingen en sleutels die expliciet voor productie zijn
  goedgekeurd;
- configuratie voor licentietelemetrie;
- noodzakelijke feature- en runtime-instellingen;
- eventueel de encryptiesleutel, uitsluitend als de geselecteerde versleutelde
  instellingen anders niet bruikbaar zijn en alleen via een beveiligd
  overdrachtskanaal.

### Niet selecteren

- alle clients, subjects, cases, investigations, research actions, findings,
  screenshots, documenten en rapporten;
- overige tenants en tenantgebruikers;
- user-generated API-tokens uit `api_keys`;
- sessies, loginlogs, notificaties, auditdata, recovery-codes en 2FA-secrets;
- health-cache, tijdelijke taakdata, rate-limitstatus en testdata;
- productie- of staging-backupbestanden als database-inhoud.

## Uitvoeringsontwerp

De uiteindelijke overdracht gebeurt in deze volgorde:

1. actuele bronbackup maken en decryptie/integriteit controleren;
2. een allowlistbestand met alleen geselecteerde configuratiesleutels maken;
3. een nieuwe lege doel-database initialiseren en migraties uitvoeren;
4. Default Organization en het nieuwe account aanmaken;
5. alleen de gecontroleerde configuratie-allowlist importeren;
6. nieuw wachtwoord en 2FA door de gebruiker registreren;
7. API-, licentie-, health- en login-smoketests uitvoeren;
8. aantonen dat alle business-tabellen leeg zijn en alleen de afgesproken
   tenant/user bestaan;
9. pas daarna een go/no-go voor DNS geven.

## Verplichte doelcontroles

Na import moeten minimaal deze controles nul opleveren:

- `clients`, `subjects`, `cases`, `investigations`, `research_actions`;
- `findings`, `documents`, `screenshots`, `reports` en rapportbijlagen;
- tenants anders dan de nieuwe Default Organization;
- users anders dan het nieuwe Ivan-account;
- `api_keys` en oude auth-/sessiedata.

De controle moet vóór DNS-omzetting als machineleesbaar bewijsbestand worden
opgeslagen. Secrets en sleutelwaarden mogen nooit in dat bewijsbestand staan;
alleen sleutelnaam, bron/targetstatus en eventuele hash van een afzonderlijk
beveiligd bestand.

## Openstaand go/no-go

De allowlist met concrete configuratiesleutels en de eventuele keuze om de
encryptiesleutel over te dragen moeten vlak vóór uitvoering nog één keer
worden gecontroleerd. Tot dat moment worden geen productiegegevens, DNS,
gebruikers of services gewijzigd.
