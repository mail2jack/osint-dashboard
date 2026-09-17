# Gunicorn Control Socket Override

## Purpose

Gunicorn's unused control socket is disabled explicitly with
`--no-control-socket`. This is an operational systemd override, not an
application or database migration.

`scripts/update.sh` and `scripts/sync_units.sh` intentionally do not manage
the dashboard service override. The live file is
`/etc/systemd/system/osint-dashboard.service.d/override.conf`.

## Rollout

1. Make and verify the normal encrypted backup/DR gate.
2. Deploy the merged application release with the normal `update.sh` flow.
3. Run the explicit installer as root:

   ```bash
   sudo /opt/osint-dashboard/scripts/install_dashboard_gunicorn_override.sh
   ```

4. Review the printed backup path and unit verification result.
5. Restart explicitly, in the approved maintenance window:

   ```bash
   sudo systemctl restart osint-dashboard
   ```

6. Verify service health and confirm the Gunicorn control-socket warning is
   absent from the new start.

The installer never starts or restarts the service. It writes the override
atomically, keeps a timestamped backup of an existing override, runs
`systemctl daemon-reload`, and verifies the unit when `systemd-analyze` is
available.

## Rollback

Do not use `git checkout` on production. Restore the timestamped backup that
the installer printed:

```bash
sudo cp -p /etc/systemd/system/osint-dashboard.service.d/override.conf.backup-<UTC> \
  /etc/systemd/system/osint-dashboard.service.d/override.conf
sudo systemctl daemon-reload
sudo systemctl restart osint-dashboard
```

If there was no prior override, remove only the newly installed drop-in,
daemon-reload, and explicitly restart. Verify health after either rollback.

## Verification

The expected effective command retains two sync workers, `threads=1`, bind
`127.0.0.1:5000`, timeout `120`, `app:app`, and includes exactly one
`--no-control-socket`. No application code, database migration, FEAT-1, or
photo-analysis behavior is changed.
