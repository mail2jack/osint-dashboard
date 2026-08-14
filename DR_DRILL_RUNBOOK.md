# Controlled VPS Restore Drill

This drill is intentionally separate from the automatic backup verifier. It
adds human authorization and audit controls. It does not restore to production.

## Human Preparation

1. Choose a low-traffic maintenance window and identify the primary operator.
2. Assign a different person as the independent production-state checker. This
   is a procedural control: the script records two names, but cannot technically
   prove that two humans entered them.
3. Create a dedicated PostgreSQL DR account. Record its roles, database
   privileges, schema privileges, and `CREATEDB` status in the evidence package.
4. Verify that the DR account cannot write to the production database. Use a
   read-only privilege inspection; do not test this by writing production data.
5. Make the encrypted backup and backup key available through `PGPASSFILE`, a
   `PGSERVICE` definition, and a protected key file. Never put credentials or
   keys in command arguments or shell history.
6. Confirm that `DR_VERIFY_DATABASE_URL` is not the production `DATABASE_URL`.
7. Configure the read-only production snapshot connection separately with
   `DR_PRODUCTION_DATABASE_URL` or `DR_PRODUCTION_PGSERVICE`. Do not reuse the
   DR account for this connection unless it has only read access to production.

## Dry Run

Run configuration validation first:

```bash
sudo -u osint /opt/osint-dashboard/scripts/dr_drill.sh \
  --dry-run \
  --operator Alice \
  --second-operator Bob \
  --backup /opt/osint-dashboard/backups
```

The dry run does not decrypt, create databases, or modify files.

## Real Drill

The second operator independently checks production before and after the drill.
Create the read-only before snapshot first:

```bash
sudo -u osint /opt/osint-dashboard/scripts/dr_production_gate.sh before \
  --operator Alice \
  --evidence-dir /opt/osint-dashboard/reports/dr-drill
```

Put the application and worker in the agreed maintenance/read-only state. The
second operator confirms that production is healthy and unchanged before the
restore starts. Only then run:

```bash
sudo -u osint /opt/osint-dashboard/scripts/dr_drill.sh \
  --confirm \
  --operator Alice \
  --second-operator Bob \
  --production-unchanged PRODUCTION-UNCHANGED \
  --backup /opt/osint-dashboard/backups \
  --evidence-dir /opt/osint-dashboard/reports/dr-drill
```

Return production to its normal state, then create the after snapshot:

```bash
sudo -u osint /opt/osint-dashboard/scripts/dr_production_gate.sh after \
  --operator Alice \
  --evidence-dir /opt/osint-dashboard/reports/dr-drill
```

The independent checker compares both snapshots and signs the result:

```bash
sudo -u osint /opt/osint-dashboard/scripts/dr_production_gate.sh attest \
  --second-operator Bob \
  --confirm PRODUCTION-UNCHANGED \
  --evidence-dir /opt/osint-dashboard/reports/dr-drill
```

The gate checks database identity, schema fingerprint, tenant/case/user counts,
upload fingerprint, service recovery, and absence of temporary DR databases.

The script performs two tests:

- A deliberately wrong backup key must fail and produce a failed verifier run.
- The real encrypted backup must restore to a new temporary PostgreSQL database.

The real verifier checks migrations, row counts, encrypted fields, uploads,
license database integrity, and Ed25519 key validity. The temporary database and
temporary files are removed after completion, including after failures.

## Evidence and Sign-Off

The evidence package must contain:

- The machine-readable drill JSON report and referenced verifier report.
- Backup ID, commit SHA, start/end timestamps, and measured duration.
- DR account name and exact privileges, without its password.
- The wrong-key failure result and alert/journal evidence.
- Primary operator sign-off.
- Independent second-operator confirmation that production was unchanged.

Copy the evidence package to an approved location outside the VPS. Do not copy
the backup key, credentials, database dump, or decrypted data into the package.

## Risk Controls

- The script requires `--confirm` and two distinct operator identities.
- It rejects DR credentials equal to `DATABASE_URL`.
- It uses a temporary database with a generated `iveras_dr_` prefix.
- It never writes production database or upload paths.
- Wrong-key testing uses a generated temporary key and cannot overwrite the real
  backup key.
- Restore logs are suppressed; reports contain metadata and pass/fail only.

If the drill fails, preserve the report and alert evidence, remove only the
temporary DR resources, and do not retry against production until the cause has
been reviewed by both operators.
