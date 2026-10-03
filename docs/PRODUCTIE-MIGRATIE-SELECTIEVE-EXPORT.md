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

### Inventaris staging op 3 oktober 2026

Alleen metadata gelezen; waarden en secrets zijn niet uitgelezen:

| Tabel | Rijen | Beleid |
|---|---:|---|
| `platform_settings` | 14 | allowlist per sleutel, nooit blind alles kopiëren |
| `settings` | 96 | legacy/configuratie eerst normaliseren en daarna allowlisten |
| `tenant_settings` | 0 | niets over te nemen |
| `api_keys` | 1 | niet overnemen; nieuwe productietokens apart aanmaken |

De huidige platform-sleutels omvatten onder meer Brave, Overheid, RapidAPI,
OpenRouter, MarinePlan, Pimeyes, TinEye, TwoChat, WhatsApp CheckLeaked,
Equasis en AI-modelinstellingen. De aanwezigheid van een sleutel betekent
niet automatisch dat die sleutel naar productie mag; de uiteindelijke
allowlist blijft expliciet.

## Voorgestelde concrete allowlist

Deze lijst bevat sleutel-namen, geen waarden. Alleen niet-lege waarden worden
overgenomen en iedere waarde wordt vóór import afzonderlijk gecontroleerd.

### Provider/API-configuratie

Voor opname voorgesteld:

`brave_api_key`, `google_search_api_key`, `overheid_api_key`,
`rapidapi_username_key`, `marineplan_api_key`, `pimeyes_api_key`,
`tineye_api_key`, `twochat_api_key`, `twochat_whatsapp_number`,
`whatsapp_checkleaked_key`, `equasis_email`, `equasis_password`,
`picarta_api_key`, `telegram_rapidapi_key` en de bijbehorende
`telegram_rapidapi_limit`.

OpenRouter wordt op verzoek wél meegenomen:
`openrouter_api_key`, `openrouter_base_url` en `openrouter_model`.
De bestaande key hoeft dus niet opnieuw te worden aangemaakt. De overdracht
gebeurt uitsluitend via het beveiligde migratiepad; de key komt niet in het
manifest, GitHub of een logbestand terecht.

Voor een versleutelde waarde moet de doelomgeving dezelfde compatibele
CMS-encryptiesleutel gebruiken, of de bestaande OpenRouter-key wordt eenmalig
via het beveiligde instellingenformulier in de nieuwe productie ingevoerd.
Dat laatste maakt geen nieuwe OpenRouter-key nodig, maar voorkomt dat
onbruikbare ciphertext wordt geïmporteerd.

### Technische integraties

Alleen na controle en met nieuwe productie-identiteit:

- SpiderFoot-URL, gebruikersnaam en wachtwoord;
- licentie-public key; de licentiepayload en handtekening worden voor de
  nieuwe productie-installatie opnieuw uitgegeven;
- telemetry-URL en installatiegegevens;
- Telegram-, SMTP- en Twilio-instellingen als die voor productie nodig zijn;
- Tor-instellingen en goedgekeurde feature flags.

Niet kopiëren maar opnieuw genereren of doelgericht instellen:

- `install_token`;
- `license_status`, `health_snapshot`, `telemetry_last_check` en
  `telemetry_registered_id`;
- rate-limitgebruik, setup-wizardstatus en andere runtime-/health-cache;
- `organization_name`, `case_number_prefix` en vergelijkbare
  productie-identiteit als die van staging afwijkt.

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
