# Disaster Recovery Verification

The backup verifier restores the latest encrypted archive into a temporary
PostgreSQL database. It never restores into the configured production database
and never modifies production uploads or license-server files.

## Verification

Configure an administrative PostgreSQL URL that can create and drop a temporary
database, but is not used by the application:

```bash
DR_VERIFY_DATABASE_URL=postgresql://dr_verify:<password>@127.0.0.1:5432/postgres
```

Alternatively configure `PGSERVICE` and `PGPASSFILE`; credentials are read from
the environment/libpq service configuration and never passed as command-line
arguments. The URL should be stored in `/etc/default/osint-backup-verify` with
mode `600`.
The verifier writes reports to `reports/dr/` (override with `DR_REPORT_DIR`).
Reports contain only timestamp, backup ID, commit SHA, check statuses, and row
counts. They never contain passwords, encryption keys, SQL, or decrypted data.

Run manually:

```bash
sudo -u osint /opt/osint-dashboard/scripts/verify_backup.sh /opt/osint-dashboard/backups
```

Install the periodic verifier:

```bash
sudo cp deploy/osint-backup-verify.service deploy/osint-backup-verify.timer deploy/osint-backup-verify-alert.service /etc/systemd/system/
sudo cp scripts/backup_verification_alert.sh /opt/osint-dashboard/scripts/
sudo chmod 700 /opt/osint-dashboard/scripts/backup_verification_alert.sh
sudo systemctl daemon-reload
sudo systemctl enable --now osint-backup-verify.timer
```

Set `BACKUP_VERIFY_ALERT_EMAIL` in `/etc/default/osint-backup-verify` when local
mail delivery is configured. Failures always reach the system journal through
the `OnFailure` unit, even without email.

The report checks:

- PostgreSQL restore into an isolated temporary database.
- Alembic migration version at the expected head.
- Counts of tenants, cases, and users.
- Decryption of an existing encrypted settings value when present.
- Presence of files in the uploaded-files archive.
- SQLite integrity and schema of `license.db`.
- Readability and validity of the Ed25519 private key, without logging it.

## RPO/RTO Targets

- **RPO: 6 hours maximum.** Backups run four times daily; a disaster may lose
  at most the interval since the last successful backup.
- **RTO: 4 hours maximum.** The target is to provision the database, restore the
  verified archive, restore uploads, run migrations, and return the application
  to a healthy state within four hours.

These are operational targets, not guarantees. A successful scheduled
verification proves recoverability of the tested archive, not that production
credentials or infrastructure are available during an incident.
