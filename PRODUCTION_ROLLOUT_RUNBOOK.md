# Production Rollout Runbook

Dit script maakt de normale master-deploy zo veel mogelijk één bevestigingsactie.
Het voert geen automatische productie-restore of rollback uit.

## Voorbereiding

1. Open een onderhoudsvenster.
2. Controleer dat een recente backup bestaat.
3. Zorg dat iemand bereikbaar is voor rollbackbeslissingen.
4. Log in op de VPS als gebruiker met sudo-rechten.
5. Controleer dat de repository op `master` staat.

## Dry Run

Plak dit commando letterlijk:

```bash
sudo /opt/osint-dashboard/scripts/production_rollout.sh --dry-run
```

Bij `DRY RUN PASSED` verandert er niets. Bij een fout stopt het script.

## Echte Uitrol

Start alleen na een groene dry-run:

```bash
sudo /opt/osint-dashboard/scripts/production_rollout.sh --confirm DEPLOY-MASTER
```

Het script gebruikt de bestaande deployflow voor backup, pull, dependencies,
frontend-build, migrations, restart en health. Daarna deployt het de license-server,
controleert beide services en health endpoints, controleert privacy-defaults,
controleert de actieve purge-timer en draait eenmaal `privacy:purge`.

De uitrol stopt bij fouten. Er is geen automatische rollback. Het JSON-rapport
staat in `/opt/osint-dashboard/reports/rollout/` en bevat geen secrets.
Na afloop verstuurt het script een korte mail vanaf `server@iveras.com` naar
`ROLLOUT_ALERT_EMAIL` (standaard `server@iveras.com`). Alleen status, commit SHA
en rapportpad worden gemaild. Zet `ROLLOUT_REQUIRE_EMAIL=true` wanneer een
uitrol ook moet falen als de mail niet kan worden verzonden.

## Bij Problemen

1. Laat de maintenance mode actief.
2. Bewaar het rolloutrapport en de deploylog.
3. Deel de fout met de verantwoordelijke operator.
4. Rol alleen terug volgens `RUNBOOK.md`, na expliciete bevestiging.

## Na De Uitrol

Controleer het rapport en bewaar het samen met commit SHA en tijdstip. Plan daarna
de afzonderlijke DR-rehearsal met twee operators. Deze rollouttest bewijst alleen
dat de release veilig is uitgerold; hij bewijst niet dat backup-herstel werkt.

## Bijzondere situaties

### Database migratie na incident

Bij het uitvoeren van een migratie na een incident (zoals junction-data herstel):

1. Maak altijd eerst een backup: `pg_dump` of `scripts/backup.sh`
2. Voer de migratie uit met pre/post-counts (standaard in `flask db upgrade head`)
3. Verifieer dat counts kloppen na migratie
4. Bij fout: abort + backup restore, NIET downgraden zonder expliciete instructie

### Key rotation (multi-key)

Bij het roteren van `CMS_ENCRYPTION_KEY`:
1. Voeg nieuwe key toe aan `CMS_ENCRYPTION_KEYS` (comma-separated)
2. Update `CMS_ENCRYPTION_KEY` naar nieuwe key
3. Draai `flask rotate-encryption` om data te re-encrypten
4. Verifieer met `flask verify-encryption`
5. Herstart gunicorn
6. Verwijder oude key uit `CMS_ENCRYPTION_KEYS` na verificatie

### TOTP secret rotatie

Bij het roteren van een TOTP secret:
1. Genereer nieuw secret: `python3 -c "import pyotp; print(pyotp.random_base32())"`
2. Update in DB: `UPDATE users SET totp_secret = '<new>' WHERE email = '<user>'`
3. Informeer gebruiker: 2FA opnieuw instellen in authenticator app
4. Test login op productie
