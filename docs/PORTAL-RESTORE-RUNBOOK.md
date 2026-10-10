# Portal- en productieherstel

Deze procedure beschrijft hoe een versleutelde productieback-up gecontroleerd
en, alleen na expliciete goedkeuring, hersteld kan worden. De controlefase mag
geen productiegegevens overschrijven.

## Vereisten

- Het versleutelde archief `iveras_backup_YYYYMMDD_HHMMSS.tar.gz.gpg`.
- De afzonderlijk bewaarde `backup-key.gpg`; deze sleutel hoort niet alleen bij
  de serverback-ups te staan.
- Toegang tot de juiste doelomgeving en een recente controle van de hostnaam.
- Een actuele back-up van de huidige toestand vóór iedere echte restore.

## Veilige controle zonder productie te wijzigen

1. Controleer bestand, eigenaar en SHA-256 van archief en sleutel via het
   afgesproken veilige kanaal. Zet de sleutel nooit in GitHub, logs of chat.
2. Decrypt het archief met `gpg --batch --pinentry-mode loopback` en de sleutel
   uit een tijdelijk, afgeschermd bestand.
3. Controleer met `tar -tzf` de archiefintegriteit en controleer of
   `database.sql.gz`, configuratie en eventuele uploads aanwezig zijn.
4. Gebruik `scripts/verify_backup.sh` of `scripts/dr_drill.sh` voor een
   geïsoleerde verificatie. Deze scripts mogen geen productie-database of
   productie-uploads overschrijven.
5. Verwijder tijdelijke gedecrypte bestanden na afloop en controleer dat de
   sleutel niet in shell history of tijdelijke logs is achtergebleven.

## Echte restore

Een echte restore is destructief en vereist daarom eerst expliciete toestemming.

1. Controleer drie keer de hostnaam, omgeving en het geselecteerde archief.
2. Maak met `scripts/backup.sh` een verse back-up van de huidige doelomgeving.
3. Stop de applicatie gecontroleerd; laat database- en sleutelbestanden alleen
   door de bevoegde servicegebruiker lezen.
4. Voer `scripts/restore.sh --dry-run --backup <ARCHIEF>` uit en controleer de
   voorgestelde paden.
5. Voer daarna pas de restore uit met de juiste sleutel en bevestiging.
6. Start de services, voer migratie-/healthchecks uit en controleer login,
   tenant-isolatie, rapportvrijgave en mailmeldingen.
7. Bewaar het pre-restore archief en de restore-uitvoer volgens de retentie- en
   auditregels.

## Sleutelbeheer

De back-up is zonder `backup-key.gpg` onherstelbaar. Bewaar minimaal één
afzonderlijke, versleutelde kopie buiten de VPS. Deel de sleutel niet via
e-mail, GitHub, tickets of een onbeveiligde chat. Een sleutelrotatie is een
apart gepland traject omdat oude archieven dan met de oude sleutel hersteld
moeten kunnen worden.

## Mislukte back-up of verificatie

Een mislukte back-up mag geen succesvolle status rapporteren. De bestaande
completion-mail vermeldt UTC, status, archief, fouten en waarschuwingen.
Een mislukte DR-verificatie activeert daarnaast de alert-service en kan via
`BACKUP_VERIFY_ALERT_EMAIL` een aparte melding verzenden. Controleer na een
melding eerst de journal en maak geen productie-release totdat een nieuwe,
gecontroleerde back-up groen is.
