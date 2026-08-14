# License Server Privacy and EULA Notice

This notice describes the license-server telemetry and optional IP intelligence.
It supplements the commercial EULA and must be reviewed by the controller
before production use. It is not legal advice.

## Purpose and Legal Basis

Telemetry is processed to authenticate installations, issue and verify licenses,
provide support, and protect the licensing service against abuse. The controller
should document the applicable GDPR Article 6 basis for its customer context,
typically legitimate interest for security/support or contract necessity for
license operation. Optional IP intelligence requires a separate documented
assessment and must not be enabled by default.

## Personal-Data Categories

The server may process installation identifiers, public and observed IP
addresses, hostnames, operating-system information, user-agent, language
preference, protocol metadata, client-reported public IP, and optional PTR/RDAP
or geolocation/ISP results. IP intelligence can reveal approximate location,
network provider, hosting/proxy indicators, and reverse-DNS names.

## Privacy Defaults and Subprocessors

External enrichment is disabled by default. PTR, RDAP, and ip-api are separate
opt-ins. Enabling RDAP or ip-api sends an IP address to the selected external
service. ip-api is an explicit opt-in subprocessor and may provide location and
ISP data. The controller must review the provider terms, transfer mechanism,
data-processing agreement, and customer notice before enabling it.

## Retention

Default retention is 30 days for `ip_intel`, 7 days for `last_http`, 30 days for
`ip_check`, and 30 days for the IP-intelligence cache. The automatic purge runs
periodically during requests. Operators may shorten these periods through the
documented environment variables; they should not lengthen them without a
privacy review.

## Access and Audit

IP intelligence is available only through the license-server administrator
dashboard and authenticated installs export API. Dashboard views and exports
are recorded in the local admin audit table. Administrators must restrict Basic
Auth credentials, protect the database, and treat exports as personal data.

Customers may request access, correction, deletion, or restriction according to
the controller's applicable privacy process. The controller is responsible for
the complete record of processing, data-subject notices, international-transfer
assessment, retention overrides, and deletion requests.
