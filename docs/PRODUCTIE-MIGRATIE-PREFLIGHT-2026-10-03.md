# Preflight productie-overgang — 3 oktober 2026

Dit document is een momentopname voor de latere migratie. Het is geen
migratieopdracht en bevat geen secrets.

## Bron: huidige staging

| Onderdeel | Waarneming |
|---|---:|
| Staging-IP | `136.144.211.112` |
| Compose-services | `app`, `postgres`, `redis`, `worker` |
| Tenants | 1 |
| Users | 1 |
| Clients | 5 |
| Cases | 5 |
| Findings | 6.548 |
| Investigations | 7 |
| Laatste zichtbare dagelijkse backup | `iveras_backup_20261003_003002.tar.gz.gpg` |

De aanwezige cases, clients, findings, investigations en andere records zijn
testdata en mogen niet naar de nieuwe productieomgeving worden gekopieerd.
De aantallen zijn alleen bedoeld als controle dat de uiteindelijke productie
leeg wordt opgebouwd.

## Doel: nieuwe productie

De doelomgeving moet na de migratie aantoonbaar bevatten:

- één lege Default Organization/default company;
- precies één nieuw aangemaakt account voor
  `ivan.versteegh@protonmail.com`;
- nieuw ingestelde credentials en opnieuw geregistreerde 2FA;
- alleen de expliciet goedgekeurde technische configuratie,
  API-instellingen en licentie-integratie.

De doelomgeving mag geen oude tenants, clients, subjects, cases, findings,
investigations, rapporten, sessies, recovery-codes of testdata bevatten.

## Readiness-controles

- `/health?quick=1`: geslaagd, HTTP 200, circa 76 ms;
- `/api/v1/health`: geslaagd, HTTP 200, circa 49 ms;
- volledige `/health`: functioneel, maar niet geschikt als korte liveness-probe
  omdat externe controles soms meer dan 20 seconden duren;
- dagelijkse backupplanning: aanwezig;
- eerdere backup-decryptie, archiefcontrole en tijdelijke database-restore:
  geslaagd.

## Nog niet uitgevoerd

- geen productie-DNS-wijziging;
- geen productiegegevens gewijzigd;
- geen serverrol gewijzigd;
- geen gebruikers of secrets naar productie geschreven;
- geen doelomgeving leeggemaakt of opnieuw opgebouwd.

## Go/no-go

Deze preflight is informatief. De daadwerkelijke migratie vereist nog steeds
een afzonderlijk, expliciet akkoord op het go/no-go-moment.
