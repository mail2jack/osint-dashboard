# DR Setup Wizard

Dit is een eenmalige setup. Het script maakt twee aparte PostgreSQL-accounts:

- `iveras_dr`: mag tijdelijke databases maken en gebruikt `BYPASSRLS` alleen
  voor de geïsoleerde restore; het account heeft geen productie-tabelrechten.
- `iveras_snapshot`: read-only snapshotaccount met `BYPASSRLS`, zodat alle
  tenantdata gelezen kan worden voor de before/after-controle.

Wachtwoorden worden automatisch gegenereerd en alleen opgeslagen in een
`PGPASSFILE` met mode `600`. Ze worden niet getoond of gelogd.

## Dry Run

```bash
sudo /opt/osint-dashboard/scripts/dr_setup.sh --dry-run
```

## Eenmalige Setup

Voer alleen uit na controle van database-host en onderhoudsplan:

```bash
sudo /opt/osint-dashboard/scripts/dr_setup.sh --confirm SETUP-DR
```

De setup schrijft alleen rollen, leesrechten en lokale libpq-configuratie. Hij
herstelt geen backup en wijzigt geen applicatiedata.

## Drillomgeving Laden

Voor ieder vervolgcommando moet de DR-configuratie geladen zijn:

```bash
set -a
. /etc/default/osint-dr
set +a
```

Daarna volgt `DR_DRILL_RUNBOOK.md` voor de before-snapshot, drill,
after-snapshot en onafhankelijke attestatie.
