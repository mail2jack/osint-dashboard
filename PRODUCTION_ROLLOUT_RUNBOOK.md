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

## Bij Problemen

1. Laat de maintenance mode actief.
2. Bewaar het rolloutrapport en de deploylog.
3. Deel de fout met de verantwoordelijke operator.
4. Rol alleen terug volgens `RUNBOOK.md`, na expliciete bevestiging.

## Na De Uitrol

Controleer het rapport en bewaar het samen met commit SHA en tijdstip. Plan daarna
de afzonderlijke DR-rehearsal met twee operators. Deze rollouttest bewijst alleen
dat de release veilig is uitgerold; hij bewijst niet dat backup-herstel werkt.
