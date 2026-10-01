# Telemetry and licensing — privacy decision record

**Status:** owner default recorded; no telemetry setting changed  
**Checked:** 2026-09-26

## What the code currently does

The telemetry client can send the following to the configured license server:

- install identifier and bearer-authenticated install token;
- hostname;
- operating system, kernel and platform/container type;
- CPU model and count;
- RAM and disk capacity;
- local IPv4 addresses;
- public IP obtained through `api.ipify.org`;
- application version.

The service checks in daily, with a minimum six-hour interval. The telemetry
service default is enabled unless a setting or production gate disables it.
Install identity may be stored in encrypted settings and, when a local `.env`
file exists, appended there with restrictive file permissions.

No case, subject or finding payload was observed in the telemetry payload
construction reviewed here. Licensing responses can update the locally cached
license state.

## Owner decision recorded

The current telemetry fields are allowed for now. Telemetry may be retained
until newer values replace them. This is a product default, not evidence that
the external service's actual server-side retention or legal basis has been
verified.

## Decisions still required

1. Is telemetry required, opt-in, or opt-out for a Joost installation?
2. Which fields are genuinely necessary for licensing/support? In particular,
   decide whether hostname, local IPs, public IP, CPU model and disk capacity
   are necessary or should be removed/aggregated.
3. Confirm that the license service can support the approved replacement-based
   retention rule and document its deletion process.
4. Where is the data stored, and which subprocessors can access it?
5. What is the privacy/legal basis and what customer-facing notice is needed?
6. What is the behavior when the license service is unavailable or telemetry is
   disabled?
7. Is the install token treated as a credential requiring rotation/revocation?

## Recommended baseline for a professional product

- Prefer data minimisation: send an install ID, app version and coarse
  platform information only unless a documented support need justifies more.
- Do not send local IP addresses by default.
- Make the effective telemetry state visible in the Joost settings and in an
  operator diagnostic report.
- Define server-side retention and deletion before enabling customer installs.
- Keep telemetry separate from research data and never include case, subject,
  finding, query or provider results in the heartbeat.
- Add a contract test that asserts the exact allowed payload keys.

## Explicitly not done

- No telemetry setting was changed.
- No check-in was sent.
- No license server or production environment was contacted.
