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

## Drift guard (fail-closed)

Before writing, backing up, reloading, or restarting anything, the installer
compares the live `/etc/systemd/system/osint-dashboard.service.d/override.conf`
against the managed repo source (`deploy/osint-dashboard-gunicorn2.override.conf`)
with whitespace collapsed and the `--no-control-socket` flag stripped.

Only two contents are accepted:

- the **exact repo source**, or
- the **known legacy variant** that differs solely by an absent
  `--no-control-socket` (the pre-fix override).

Any other content — an unexpected extra rule, reordered parameters, or a
different parameter value — is treated as an unplanned operator change. The
installer refuses with an explicit `ERROR` telling you that nothing was
written, backed up, reloaded, or restarted, and asks you to review the
existing override by hand. This is an exact equality check, never a loose
"contains" match.

## Fixed production paths (fail-closed)

The deployed installer always uses the fixed paths `APP_DIR=/opt/osint-dashboard`
and `DST_BASE=/etc/systemd/system`. When run directly (the production
deployment mode), the installer refuses fail-closed if `APP_DIR` or `DST_BASE`
deviate from these values — before the root check and before any access to the
source file, the venv binary, a backup, a write, `systemctl daemon-reload`, or
`systemd-analyze verify`. Environment overrides are honored ONLY when the
installer is `source`d by the test harness, which drives `run_install` against a
sandbox.

## Read-only real-venv flag check (fail-closed)

Before any change, the installer runs the **real installed binary** at the
fixed path `$APP_DIR/venv/bin/gunicorn --help` (read-only — never starting a
service, never creating a control socket, never touching systemd). It fails
closed and aborts before any filesystem change when:

- the installed binary is missing or not executable,
- `gunicorn --help` cannot be run, or
- the help output does not contain `--no-control-socket` (an outdated/broken
  venv build).

The check is read-only against the production venv; it is never skipped in
deployment.

## Verification

The expected effective command retains two sync workers, `threads=1`, bind
`127.0.0.1:5000`, timeout `120`, `app:app`, and includes exactly one
`--no-control-socket`. No application code, database migration, FEAT-1, or
photo-analysis behavior is changed.
