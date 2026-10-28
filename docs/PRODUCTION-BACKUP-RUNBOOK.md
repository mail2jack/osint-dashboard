# Productieback-up en herstel

Dit is de actuele procedure voor de productieomgeving van Joost.

## Planning

De productieomgeving maakt twee automatische back-ups per dag volgens de
Nederlandse lokale tijd:

- 01:00
- 17:00

De server gebruikt UTC, maar de cronconfiguratie gebruikt expliciet
`Europe/Amsterdam`, zodat zomer- en wintertijd correct worden gevolgd.

## Wat gebeurt er per run?

1. Er wordt een lock geplaatst zodat geen twee back-ups tegelijk lopen.
2. PostgreSQL, configuratie, migraties en aanwezige bestanden worden verzameld.
3. Het archief wordt met AES-256 versleuteld.
4. De decryptie en tar-integriteit worden direct gecontroleerd.
5. De ingestelde backupnotificatie wordt verzonden.
6. Alleen bij een geslaagde lokale back-up wordt het versleutelde archief via
   de beperkte gebruiker `backup-receiver` naar `backup.iveras.com` gekopieerd.
7. Op de backup-VPS worden maximaal 28 off-site archieven bewaard.

De encryptiesleutel staat niet in GitHub en wordt niet per e-mail verstuurd.
Een afzonderlijke kopie wordt buiten de productiebackuparchieven bewaard.

## Opslag

Lokale productiearchieven staan in:

`/home/development/joost-production/backups/`

Off-site archieven staan in de home-directory van de beperkte gebruiker
`backup-receiver` op `backup.iveras.com`.

Lokale retentie:

- 14 dagelijkse archieven
- 8 wekelijkse archieven
- 12 maandelijkse archieven

Off-site retentie: maximaal 28 archieven. Oudere off-site archieven worden
door de synchronisatietaak verwijderd nadat een nieuwe kopie is gecontroleerd.

## Handmatige controle

```bash
cd /home/development/joost-production
sudo /usr/local/sbin/joost-production-backup-run
```

Controleer daarna de laatste regels van:

`/home/development/joost-production/backups/production-backup.log`

Een geslaagde run vermeldt `Errors: 0`, een versleuteld archief en een
off-site upload.

## Herstel

Een echte productie-restore is destructief en vereist vooraf expliciete
goedkeuring. Gebruik eerst altijd een geïsoleerde verificatie of de
restoreprocedure uit [PORTAL-RESTORE-RUNBOOK.md](PORTAL-RESTORE-RUNBOOK.md).

Benodigd:

- het geselecteerde `.tar.gz.gpg`-archief;
- de afzonderlijk bewaarde `backup-key.gpg`;
- een verse veiligheidsback-up van de huidige productieomgeving;
- een vastgelegd herstel- en rollbackplan.

Na herstel moeten minimaal database, migratiestatus, login, tenantisolatie,
rapportvrijgave en externe mailmeldingen worden gecontroleerd.

## Incidenten en meldingen

Een fout in database-export, encryptie, integriteitscontrole of off-site upload
maakt de run niet succesvol. Onderzoek dan eerst de backup-log en maak geen
release totdat een nieuwe geslaagde back-up beschikbaar is.

Voer minimaal maandelijks en na iedere wijziging aan backup, restore, schema of
infrastructuur een geïsoleerde hersteltest uit.
