# Finding-capture browser sandbox

The screenshot-capture worker remains disabled by default. This runbook exists
because Ubuntu's AppArmor policy blocks unprivileged Chromium user namespaces
on the production host. It does not weaken the global host setting and it does
not permit Chromium's `--no-sandbox` option.

## What the installer changes

Run the installer only as root on the production host after a reviewed deploy:

```bash
sudo /opt/osint-dashboard/scripts/install_finding_capture_apparmor_profile.sh
```

It installs the pinned Playwright Chromium revision as root directly under
`/opt/osint-capture/<revision>`; it never copies the browser from the app
user's writable cache. It rejects a group/other-writable bundle, then loads an
AppArmor profile attached to exactly that immutable browser binary and records its path in
`/etc/default/osint-finding-capture`.

The installer only reloads systemd metadata. It never enables, starts or
restarts the capture worker. It refuses changed installation paths, an absent
browser, a non-root-owned destination, or an unavailable AppArmor parser.

## Verification before enabling a capture job

With the worker still disabled, run the existing safe `about:blank` probe
inside the worker's systemd sandbox. It must complete successfully without
network access and without sandbox-bypass flags. If it fails, stop: do not add
`--no-sandbox`, do not disable AppArmor globally, and do not enable the worker.

Only after a separate approval may an operator temporarily enable the worker
for a single QA capture and then disable it again. The QA test must use the
approved synthetic tenant and fixture, and must verify evidence thumbnail,
original file, source URL and cleanup.

## Rollback

The installer writes timestamped backups alongside any existing AppArmor
profile and environment file. Restore the recorded backups, reload the profile
with `apparmor_parser -r`, run `systemctl daemon-reload`, and keep the worker
disabled. Do not use a database downgrade or alter the global AppArmor userns
kernel setting.
