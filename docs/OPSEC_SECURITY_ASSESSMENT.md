# OPSEC- en security-assessment

**Systeem:** Iveras OSINT Dashboard  
**Beoordelingsdatum:** 2026-08-31  
**Omgeving:** productie-VPS, repository `/opt/osint-dashboard`  
**Doelgroep:** externe securityspecialist en management

## Executive summary

Het dashboard heeft een zinvolle securitybasis: productie dwingt PostgreSQL af, tenantcontext wordt per request gezet, database-RLS en `FORCE RLS` zijn gemigreerd, gevoelige domeinvelden gebruiken Fernet-encryptie, en er zijn RBAC-, 2FA-, CSRF-, SSRF-, audit- en retentievoorzieningen. Ook is er een aparte SpiderFoot-service op localhost, een centrale licentie-/telemetryservice, een health-refresh-producer en een gedocumenteerd encrypted backup/DR-pad.

De belangrijkste risico's zijn operationeel en systeemmatig. De live Gunicorn-unit draait met één worker en zonder systemd-hardening; de lokale app-accesslog is leeg ondanks de ingestelde logfile. De gedocumenteerde backup-verificatietimer is niet geïnstalleerd. De productie-venv heeft corrupte package-metadata en twee dependency-conflicten. Verder is de license-/telemetryketen een externe afhankelijkheid met privacy- en beschikbaarheidsimpact, en is de uitgebreide set CSRF-exempt JSON-routes een blijvend reviewpunt. Deze punten verhogen vooral detectie-, beschikbaarheids-, supply-chain- en herstelrisico; uit deze read-only review volgt geen bewezen cross-tenant datalek.

Prioriteit: herstel de live deployment-baseline en observability eerst, daarna dependency-/deploy-governance en privacy-/external-flow-besluiten. De assessment is geen formele pentest, geen code-audit van iedere route, en geen legal, AVG/GDPR- of privacy opinion.

## Scope, aannames en beperkingen

### Scope
- Broncode, migraties, deployment- en DR-scripts, CI-workflow en securitydocumentatie in de productiecheckout.
- Read-only observatie van actieve services, timers, firewallstatus, unitconfiguratie en filesystemmetadata.
- Architectuur: browser, nginx/TLS, Gunicorn/Flask, PostgreSQL/RLS, filesystem/session-store, SpiderFoot, license/telemetry, externe OSINT-providers, backups/DR en operator/admin-toegang.

### Aannames
- De actieve systemd-unit en de checkout representeren de productieconfiguratie; niet-getrackte rapporten zijn niet als bron van waarheid gebruikt.
- Code- en documentclaims zijn geen bewijs dat iedere instelling of iedere route in productie exact zo staat.
- Er is geen applicatielogin, databasequery, backupdecompressie, secret-inspectie of wijziging uitgevoerd.

### Buiten scope
- Geen exploitatie, portscan, brute force, pentest, fuzzing of actieve securitytest.
- Geen beoordeling van juridische grondslagen, verwerkersovereenkomsten, bewaartermijnen als juridische verplichting of privacy notice.
- Geen onafhankelijke review van cloud-, DNS-, backup-provider- of GitHub-accountinstellingen.
- Geen formele uitspraak over afwezigheid van kwetsbaarheden.

## Architectuur en trust boundaries

1. **Browser naar nginx/TLS:** gebruikersbrowser praat via HTTPS met nginx. Nginx proxy't de app naar localhostpoort 5000 en SpiderFoot naar localhostpoort 5001. HTTP wordt naar HTTPS geredirect. TLS 1.2/1.3 is zichtbaar in nginx-config.
2. **nginx naar Gunicorn/Flask:** nginx is de internet-facing reverse proxy; de Flask-app draait als `osint` en bindt live op `0.0.0.0:5000`, hoewel firewallbeleid alleen 22/80/443 toont. Controleer expliciet dat poort 5000 niet extern bereikbaar is buiten UFW/hostfirewall.
3. **Flask naar PostgreSQL:** productieconfig vereist PostgreSQL en forceert minimaal database-SSL `require`. Per request wordt tenantcontext gezet; RLS-policy's gebruiken `app.tenant_id` en een beperkt super-admin/bypass-pad.
4. **Sessies/filesystem:** Redis is de voorkeursbackend; bij onbeschikbaarheid valt de app terug naar `flask_session` via Cachelib. Sessies zijn server-side, JSON-geserialiseerd en hebben een nominale levensduur van acht uur.
5. **Flask naar SpiderFoot:** SpiderFoot draait als `osint`, localhost-only, met afzonderlijke service- en scannerdata. De app stuurt onderzoekstaken naar deze trust boundary.
6. **Flask naar license/telemetry:** de app registreert en checkt periodiek bij `license.iveras.com`. De licentie wordt lokaal met ingebouwde/instelbare Ed25519-public key geverifieerd; revocatie is afhankelijk van een succesvolle check-in.
7. **Flask naar externe OSINT/API's:** zoek-, telefoon-, RDW-, adres-, sociale, interpol-, vessel- en andere providers ontvangen afhankelijk van de actie onderzoeksinput. Niet alle calls gebruiken dezelfde centrale HTTP-wrapper.
8. **Backups/DR:** encrypted archieven bevatten database, uploads, sessies en operationele configuratie; volgens de scripts kunnen ook license registry/key-material en serviceconfig worden meegenomen. DR restore hoort geïsoleerd te zijn.
9. **Admin/operator:** root/systemd, `osint`, database-operators, super-admins en license-server-operators hebben verschillende maar zeer krachtige trustposities. GUI-update heeft volgens documentatie passwordless sudo-rechten voor git/chown/systemctl.

## Assets en classificatie

| Asset | Classificatie | Hoofdimpact |
|---|---|---|
| Databasecredentials, API-keys, SMTP/Stripe/Twilio-credentials | Zeer vertrouwelijk | account-, financieel- en provider-misbruik |
| Flask session secret, CMS encryption/fingerprint keys, license signing key | Kritiek geheim | sessieforging, decryptie of licensefraude |
| Personen-, contact-, adres-, telefoon-, voertuig-, financiële en OSINT-data | Bijzonder gevoelig | privacy-, reputatie- en veiligheidsimpact |
| Uploads, screenshots, PDF/exportbestanden | Zeer vertrouwelijk | directe inhoudelijke disclosure |
| Audit-, login- en OSINT-chain logs | Vertrouwelijk/forensisch | gedrags-, IP-, tijdlijn- en accountability-informatie |
| Telemetry, lokale/publieke IP intelligence en systeemprestatie | Vertrouwelijk | installatieprofilering en privacy-impact |
| License registry, licentieclaims en revocatiestatus | Vertrouwelijk/kritiek voor beschikbaarheid | entitlement- en bedrijfscontinuïteit |
| Backups, DR-rapporten en restore-omgevingen | Zeer vertrouwelijk | geconcentreerde exfiltratie- en herstelimpact |
| GitHub/repository, deployment scripts en operatoraccounts | Hoog | supply-chain en volledige hostcompromis |

## Threat model

- **Internetaanvaller:** credential stuffing, sessiediefstal, CSRF, SSRF, upload-/parsermisbruik, route- en dependency-exploitatie.
- **Gestolen adminsessie:** export, tenant-switching, gebruikersbeheer, settings/API-keys, update- en retentieacties.
- **Gecompromitteerde tenant:** misbruik van OSINT-integraties, poging tot cross-tenant lezen/schrijven, upload van kwaadaardige documenten.
- **Operator/hostbeheerder:** volledige toegang tot process environment, `.env`, keys, sessies, database, backups en logs; modelleer dit als trusted-but-auditable, niet als technische isolatie.
- **Dependency/provider compromise:** Python/npm dependency, SpiderFoot, OSINT-provider, browser/Playwright of externe license/telemetryservice.
- **VPS-, backup- of license-servercompromis:** bulkdata, signing key, registry, token en restoreketen kunnen worden geraakt.
- **RLS-/tenant-contextfout:** één fout in context, bypass of een vergeten tenantfilter kan cross-tenant disclosure veroorzaken; `FORCE RLS` verkleint maar elimineert dit risico niet.

## Bestaande controls en bewijs

### Identity, auth en authorization
- TOTP-2FA is geïmplementeerd volgens `README.md:35` en model-/auth-code rond `cms/models/__init__.py:297`.
- RBAC-decorators en super-admincontrole staan in `cms/auth.py:110-212`; case-/entity-tenantchecks volgen verderop in hetzelfde bestand.
- Sessions zijn HttpOnly, Secure in productie, SameSite Strict in `cms/config.py:78-88`, en acht uur als standaard in `cms/config.py:30-34` en `app.py:610-618`.
- Password-reset gebruikt een eenmalige, gehashte token met TTL volgens `AGENTS_OPERATIONS.md:127-131`.
- Risico: documentatie bevat een historisch/admin-default patroon. Behandel elk bestaand default account/wachtwoord als gecompromitteerd totdat operatoren productiegebruik en rotatie onafhankelijk bevestigen; de waarde is bewust niet opgenomen.

### Tenant isolation en data protection
- Request tenant context en super-admin switch/bypass: `app.py:123-154`.
- PostgreSQL is productievereiste; SQLite wordt geweigerd in production config omdat RLS daar niet actief is: `cms/config.py:103-128`.
- `FORCE RLS`, `USING` en `WITH CHECK` staan onder meer in migratie `migrations/versions/d2e3f4a5b6c7_re_enable_force_rls.py:56-67`; nieuwe tenant-tabellen moeten hetzelfde patroon volgen.
- App-level fallback tenantfilters zijn beschreven in `cms/auth.py:683-727`.
- Fernet field encryption en key rotation staan in `cms/encryption_utils.py:41-276`; key persistence gebruikt env of `.cms_key`, met chmod 600 volgens `app.py:227-248`. Fingerprints zijn onderscheiden van plaintext: `cms/fingerprint_utils.py:1-10`.

### Request, browser en SSRF
- Globale CSRFProtect: `cms/__init__.py:22-28`; frontend wrapper en bekende beperking zijn gedocumenteerd in `AGENTS_ARCHITECTURE.md:71-81`.
- Er zijn veel `@csrf.exempt` API-routes, onder meer `cms/routes/osint_routes.py`, `phone.py`, `vessel.py`, `rdw.py`, plus Stripe webhook. Elke exempt route die alleen cookies gebruikt moet apart worden bewezen als CSRF-safe.
- SSRF-validatie zit in `cms/services/ssrf_guard.py:1-81`; redirect hops worden opnieuw gevalideerd in `cms/services/http_utils.py:444-467`; Playwright gebruikt eveneens safe URL-validatie.
- Uploadlimiet 16 MB en extensieallowlist: `cms/config.py:48-51`; documentroutes gebruiken `secure_filename`: `cms/routes/documents.py:100-109`.
- Residueel: extensiecontrole is geen content-type-/malware-isolatie en document/PDF/image parsers blijven hoog-risico code.

### Logging, audit en retention
- AuditLog wordt op veel mutatie-, toegang- en geweigerde-actiepaden aangemaakt; voorbeelden `cms/auth.py:147-157` en `cms/routes/documents.py:138-244`.
- Retentie defaults en purgecode: `cms/__init__.py:298-365`, `cms/models/__init__.py:2345-2351` en `cms/data_retention.py:48-156`.
- OSINT chain hashing is optioneel en standaard aan volgens `AGENTS_OPSEC.md:91-117`; valideer production setting en onafhankelijke integriteitsmonitoring.
- Nginx heeft access/error logs; live app-accesslog is echter nul bytes, terwijl de Gunicorn-unit deze logfile configureert. Dit is een bevestigd detectie-/forensisch gat, niet noodzakelijk bewijs dat nginx logging ontbreekt.

### Platform, TLS, firewall en brute-force controls
- Live: nginx, PostgreSQL, SpiderFoot, dashboard en Fail2Ban zijn actief; UFW toont alleen SSH/HTTP/HTTPS als toegestane inkomende poorten.
- Nginx toont TLS 1.2/1.3 en reverse proxies; inhoudelijke cipher-, HSTS- en management-routecontrole moet door de specialist worden herhaald.
- App security headers staan in `AGENTS_OPERATIONS.md:72-84`; configuratieclaim moet via live response worden gevalideerd.
- Fail2Ban is actief, maar jail/filter/ban-effectiviteit is niet read-only vastgesteld.
- De live dashboard-unit heeft `NoNewPrivileges=no`, `ProtectSystem=no`, `ProtectHome=no`, `PrivateTmp=no`; dit contrasteert met de geharde SpiderFoot- en DR-units. Zie Finding F-01.

### Backups, DR en deploy
- `scripts/backup.sh:80-245` maakt database-, upload-, sessie-, configuratie- en eventueel license-artefacten klaar voor encryptie; dit is een zeer geconcentreerde asset.
- DR-verificatie is ontworpen als geïsoleerde restore met aparte rol en rapportage: `DISASTER_RECOVERY.md:7-26,48-68`.
- Gedocumenteerd RPO/RTO-doel is 6 uur/4 uur, geen garantie: `DISASTER_RECOVERY.md:58-68`.
- CI bevat lint, typecheck, tests, blocking `pip-audit` en PostgreSQL-RLS tests: `.github/workflows/ci.yml:13-55,71-123`. Bandit is expliciet informational en non-blocking: `.github/workflows/ci.yml:50-51`.
- Deploy preflight is read-only en hoort dependency scanning te blokkeren: `scripts/preflight.sh:1-18,100-120`; deploy heeft geen automatische rollback: `scripts/deploy.sh:8-10,92-101`.

## Dataflows, externe disclosure en retention

| Flow | Disclosure volgens code | Default/opt-in/onbekend | Retention/controle |
|---|---|---|---|
| License telemetry | hostname, OS/kernel, platform, CPU/RAM/disk, lokale IP's, publieke IP via ipify, app-versie, install-ID; bearer-auth naar license server: `cms/services/telemetry.py:1-5,187-236` | telemetry default `true` in `:36-38`; productie/disabled gates in `AGENTS_OPERATIONS.md:10-14`; actuele production-toggle niet gelezen | dagelijks, minimuminterval 6 uur; serverretentie en verwijdering niet vastgesteld |
| Publieke IP | afzonderlijke HTTPS-call naar `api.ipify.org`: `telemetry.py:171-184`; direct, niet via OSINT-proxy | automatisch bij telemetry | providerretentie onbekend |
| IP/PTR/RDAP/geo | `ip-api.com` en andere geo-/domain lookups, o.a. `cms/geo_utils.py:57-85` en `cms/ip_domain_lookup.py:32-46`; PTR/RDAP-providergebruik is route-/serviceafhankelijk | user-triggered of health/lookup afhankelijk; precieze actuele opt-in onbekend | app-retentie en providerretentie niet volledig vastgesteld |
| Search/OSINT providers | Brave/DuckDuckGo en diverse providercalls; centrale jitter-wrapper is niet universeel, bijvoorbeeld directe RDW-calls in `cms/routes/rdw.py:61-163` | feature-/user-triggered, API-key configuratie onbekend | providerlogs/retentie en DPA-status onbekend |
| SpiderFoot | targets en scanresultaten naar localhost-service; SpiderFoot voert verdere externe OSINT-calls uit | feature/licentie gated; runtime targetscope niet gevalideerd | eigen scanner-DB; retentie niet vastgesteld |
| Sentry | `sentry-sdk` is dependency; DSN en initialization zijn conditioneel in `app.py:79-99` | alleen indien `SENTRY_DSN` productie is gezet; actuele waarde/status niet gelezen | Sentry retention, PII scrubbing en data residency onbekend |
| License registry | install-ID, telemetry-info en license state op de centrale server; signed license wordt lokaal gecached in settings: `license.py:90-118` | check-in automatisch; enforcement kan via omgeving worden uitgezet, actuele status onbekend | lokale license settings en server registry; retention onbekend |

Onderzoeksdata wordt niet als payload uit productie uitgelezen. Management moet per provider vastleggen welke invoer, rechtsgrond, regio, subverwerkers, logging en verwijdering gelden. “Jitter”, proxy/Tor en browser-stealth (`AGENTS_OPSEC.md`) zijn OPSEC-maatregelen, geen privacygarantie of toestemming om providervoorwaarden te omzeilen.

## Bevestigde en niet-bevestigde operationele bevindingen

### Bevestigd tijdens deze read-only controle
- Live dashboard draait met één Gunicorn-worker; health-refresh draait als aparte systemd oneshot/timer.
- Live dashboard-unit mist hardening (`NoNewPrivileges`, `ProtectSystem`, `ProtectHome`, `PrivateTmp` uit).
- `/var/log/osint-dashboard/access.log` is leeg, terwijl Gunicorn deze logfile gebruikt; nginx-accesslog bestaat wel.
- `osint-backup-verify.timer` is niet geïnstalleerd/gevonden, ondanks gedocumenteerd ontwerp; `osint-health-refresh.timer` is wel actief.
- Venv meldt corrupte package-distributiemetadata voor meerdere packages en package-conflicten rond `reportlab` met `xhtml2pdf` en `maigret`.
- License server-service is actief; license data/keydirectories zijn toegankelijk voor de license-account. Niet bewezen is of app-backups die artefacten daadwerkelijk succesvol meenemen.
- Firewall is actief met publiek toegestane SSH/80/443; effectieve externe exposure van localhostpoorten is niet getest.

### Hypothesen of niet op productie bewezen
- **Session root-writer:** de app kan fallback-filesessies schrijven; ownership is `osint:osint`, niet root. Een root-writer/ownership-regressie is niet aangetoond, maar de fallback en deploy-/restorepaden verdienen een test.
- **Health queue/producer:** de producer is aangetroffen en gebruikt flock plus 75 seconden timeout (`scripts/health_refresh.py:20-103`). Een queue-backlog of stale snapshot is niet gemeten.
- **Backup warning:** scripts/documentatie beschrijven waarschuwingen wanneer license-backup of DR-config ontbreekt; actuele laatste backupstatus is niet uitgelezen.
- **Admin defaults:** code/documentatie bevat een historisch default-adminpatroon; niet gecontroleerd of het account nog bestaat of geroteerd is.
- **License shared fate:** check-in, revocatie en registry delen een externe trust-/beschikbaarheidsketen; offline signature verification voorkomt directe outage maar niet vertraagde revocatie of providercompromis.
- **Access-log gap:** de lege app-log is bevestigd; oorzaak (config, rotatie, logging naar journald of recente start) is niet vastgesteld.

## Severity-ranked findings

### F-01 High: live Gunicorn-unit onvoldoende gehard
**Evidence:** live `osint-dashboard.service` draait als `osint`, maar `NoNewPrivileges=no`, `ProtectSystem=no`, `ProtectHome=no`, `PrivateTmp=no`; SpiderFoot/DR-units gebruiken wel hardening.  
**Impact:** een app- of dependencycompromis krijgt makkelijker toegang tot hostbestanden, home/configuratie, sessies, keys en tooling; privilege-/lateral-movementimpact is hoog.  
**Likelihood:** medium, gezien groot dependency- en parseroppervlak.  
**Aanbeveling:** maak een systemd-hardeningprofiel met expliciete `ReadWritePaths`, minimale filesystemtoegang, private temp, `NoNewPrivileges`, capability drop en zo mogelijk read-only repository. Test uploads, sessions, Playwright, migrations en backup zonder brede uitzonderingen.  
**Owner:** platform/operations.

### F-02 High: production observability en audit trail incompleet
**Evidence:** app-accesslog is leeg; nginx-log bestaat; de unit verwijst naar de lege logfile.  
**Impact:** gemiste request-, incident- en exfiltratiesignalen, slechtere reconstructie van admin/sessionmisbruik en onzekerheid over rate-limit/routecontrole.  
**Likelihood:** high voor detectieverlies; impact high bij incident.  
**Aanbeveling:** kies één gecontroleerd loggingpad (journald of file), test end-to-end request logging, logrotatie, centrale verzending, timestamp-sync, privacyredactie en alerting. Bewaar geen tokens/querypayloads onnodig.  
**Owner:** platform/SOC.

### F-03 High: backup-verificatie-automation ontbreekt live
**Evidence:** `osint-backup-verify.timer` geeft `not-found`, terwijl `DISASTER_RECOVERY.md:34-46,98-99` en de deploy-units een periodieke verifier beschrijven.  
**Impact:** encrypted backups kunnen stil onbruikbaar worden; RPO/RTO-doelen zijn niet operationeel afgedwongen.  
**Likelihood:** medium; impact high.  
**Aanbeveling:** installeer de timer via gecontroleerde change, valideer aparte DR-rol/key, OnFailure-alerting, rapportage zonder secrets en maandelijkse drill. Laat monitoring alarm slaan op ontbrekende timer én stale last-success.  
**Owner:** operations/DR owner.

### F-04 High: dependency-installatie is niet betrouwbaar reproduceerbaar op productie
**Evidence:** `pip check` meldt package-conflicten; pip meldt meerdere invalid distributions in de Python 3.14 venv, terwijl projectconfig Python 3.12 vereist (`pyproject.toml:10`, `Dockerfile:12`).  
**Impact:** onverwachte runtimefouten, onvoorspelbare securitypatches en supply-chain/deploy-gate omzeiling.  
**Likelihood:** high; impact medium-high.  
**Aanbeveling:** vernietig/herbouw de venv gecontroleerd met de ondersteunde Pythonversie en lockfile; pin/resolve conflicten, genereer SBOM, laat `pip check`, `pip-audit`, tests en import-smoke passeren. Blokkeer deploy bij drift.  
**Owner:** engineering/release.

### F-05 Medium: brede CSRF-exempt surface vereist route-voor-route bewijs
**Evidence:** `cms/__init__.py:71-80` noemt 34 exemptions en waarschuwt zelf voor cookie-only routes; actuele lijst bevat veel OSINT-, lookup- en settingsgerelateerde API's.  
**Impact:** een browser met bestaande sessie kan state-changing acties triggeren als één route geen header/API-key afdwingt.  
**Likelihood:** medium; impact high per gevoelige route.  
**Aanbeveling:** maak een machine-readable route-inventaris: auth, method, CSRF, cookie/API-key, mutatie, tenantcheck. Verwijder exemptions waar `csrfSafeFetch` al de token meestuurt; voeg negatieve tests toe met cross-origin request zonder token.  
**Owner:** application security.

### F-06 Medium: license/telemetry is privacy- en beschikbaarheidsketen met ruime metadata
**Evidence:** `telemetry.py:187-236` verzamelt lokale/publieke IP, hostname, kernel, hardwarecapaciteit en versie; default enabled staat in `:36-38`; `license.py:1-12` beschrijft offline cache en online revocatie.  
**Impact:** installatieprofilering, providercompromis, foutieve licentie-/revocatiestatus en afhankelijkheid van externe operatoren.  
**Likelihood:** medium; impact medium-high.  
**Aanbeveling:** documenteer doel/minimalisatie, opt-in/defaultbesluit, retention, DPA/data residency en Sentrybeleid; beperk payload; pin server certificate/domain policy waar passend; monitor check-in zonder onderzoeksdata.  
**Owner:** product/privacy + platform.

### F-07 Medium: app bindt direct op alle interfaces
**Evidence:** live ExecStart gebruikt `--bind 0.0.0.0:5000`; nginx proxy't naar `127.0.0.1:5000`; UFW-regels zijn aanwezig maar geen externe exposure-test is uitgevoerd.  
**Impact:** firewallmisconfiguratie of container/network policy kan auth/TLS/reverse-proxylaag omzeilen.  
**Likelihood:** low-medium; impact high.  
**Aanbeveling:** bind Gunicorn op `127.0.0.1:5000` tenzij aantoonbaar nodig; voeg een gecontroleerde host-exposure check toe aan deployment.  
**Owner:** platform.  
**Remediatie (2026-09-06):** opgelost. Basis-unit `/etc/systemd/system/osint-dashboard.service` en de 2-worker override binden beide `--bind 127.0.0.1:5000`. Live geverifieerd: `ss -ltnp` toont alleen `127.0.0.1:5000` voor gunicorn (geen `0.0.0.0`), UFW staat alleen 22/80/443 toe, gezondheid OK via `localhost:5000` en nginx:443 (zero-downtime, alleen `daemon-reload`). De controlled host-exposure check maakt deel uit van de checklist hieronder.

### F-08 Medium: backups concentreren secrets en live/signed license-material
**Evidence:** `scripts/backup.sh:159-245` neemt `.env`, sessies, SpiderFoot password en mogelijk license DB, license env en private signing key op; encryptie gebruikt een lokaal key-file.  
**Impact:** één backup- of keycompromis kan sessies, database, providercredentials en license issuance blootleggen.  
**Likelihood:** medium; impact critical.  
**Aanbeveling:** scheid data-backups van secret/key escrow, gebruik externe KMS/rotatie en dual control, minimaliseer restore-inhoud, test key recovery, audit toegang en voorkom kopiëren van signing private key tenzij expliciet noodzakelijk.  
**Owner:** security/operations.

### F-09 Low/Medium: CI security gate is asymmetrisch
**Evidence:** Bandit is informational/non-blocking (`.github/workflows/ci.yml:50-51`), dependency-audit is blocking (`:52-55`), deploy heeft geen automatische rollback (`scripts/deploy.sh:8-10`).  
**Impact:** bekende codepatronen en regressies kunnen landen; readiness failure verlengt incidentduur.  
**Likelihood:** medium; impact medium.  
**Aanbeveling:** triage Bandit findings met expliciete allowlist/expiry, voeg secret scanning, container/IaC scan en signed artifact/provenance toe; maak rollback/maintenance runbook meetbaar.  
**Owner:** engineering/release.

## Positieve controls en residueel risico

Sterk zijn de PostgreSQL-only productiegate, tenantcontext plus `FORCE RLS`, encrypted sensitive fields, TOTP/RBAC, Secure/HttpOnly/SameSite sessiecookies, CSRF framework, redirect-hop SSRF-validatie, input validation, audit logging, encrypted backupontwerp en blocking dependency audit in CI. De code bevat bovendien expliciete waarschuwingen voor bekende zwakke paden, waaronder CSRF exemptions en SQLite.

Residueel blijven: correctness van ieder tenantfilter/bypasspad, admin/super-admin trust, host-root en database-superuser, parser/dependency supply chain, providerlogging, sleutelbeheer, upload malware en operationele monitoring. Geen van deze controls vervangt onafhankelijke tests met twee tenants en een niet-superuser database-rol.

## Remediation roadmap

### Direct, 0-7 dagen
1. Herstel logging en verifieer een gecontroleerde request/audit-event-keten zonder gevoelige payloads.
2. Bevestig backupfrequentie, laatste succesvolle encrypted archive en DR-key escrow; installeer of verklaar de ontbrekende verifier-timer.
3. Bevries deploys op de huidige venv totdat ondersteunde Python/lockfile, `pip check` en audit groen zijn.
4. Roteer/bevestig alle historische defaults, sessie-/API-/telemetrycredentials en operatoraccounts zonder waarden te delen.
5. Beperk Gunicorn tot localhost en maak een tijdelijke exposure-check.

### Binnen 30 dagen
1. Hardening van de dashboard systemd-unit met staged canary en rollback.
2. CSRF-exempt routecatalogus en negatieve cross-origin tests.
3. Twee-tenant RLS testmatrix inclusief super-admin switch, background jobs, exports, uploads, reports en direct niet-superuser DB-pad.
4. Providerregister met payloadcategorie, default/opt-in, retention, DPA/data residency en Sentry-redactie.
5. Backup/restore-oefening met gescheiden secret escrow en alert op stale/missing timer. (2026-09-06: stale-gedeelte live — DR-verifier heeft nu een `freshness`-check, archief ouder dan `DR_MAX_ARCHIVE_AGE` 24h → fail + OnFailure-alert; zie PR #120.)

### Binnen 90 dagen
1. Externe authenticated application review en beperkte pentest na staging parity.
2. SBOM, signed builds, dependency/container scanning, secret scanning en release provenance.
3. Centralized immutable logging/SIEM, alert runbooks en tabletop voor sessie-, RLS-, backup- en license-serverincidenten.
4. KMS/HSM- of vergelijkbaar sleutelbeheer, periodieke key rotation en gecontroleerde restore.
5. Formele privacy/data-governance review door bevoegde specialist.

## Validatietests en veilige rollout

- Gebruik staging met twee tenants, synthetische records en een niet-superuser PostgreSQL-rol; geen productie-PII.
- Test browser zonder CSRF-header, met verkeerde tenant-ID, gestolen/expired sessie, direct app-poortpad, redirect naar private IP, kwaadaardige uploadnaam/type en export buiten toegestane rol.
- Test process isolation via tijdelijke canary-unit: read-only repository, expliciete write paths, session create/read, Playwright, SpiderFoot, health refresh en graceful restart.
- Test backup: encrypt/decrypt met gescheiden key, isolated DB restore, uploads, migrations, license registry zonder productie te overschrijven, en bewijs van cleanup.
- Meet p50/p95/max health-latency, worker restarts, nginx 4xx/5xx/499, log-ingest, timer last-success, PostgreSQL locks en audit-chain status.
- Rolloutvolgorde: snapshot/backup, maintenancevenster, canary of parallel staging, health/readiness, securitytests, observability, expliciete go/no-go, daarna pas production. Bij failure geen ad-hoc downgrade; gebruik pinned SHA en gedocumenteerde restore.

## Specialist checklist en beslissingen

- [ ] Bevestig alle publiek bereikbare poorten vanaf een externe meetlocatie. (2026-09-06: gedeeltelijk — UFW actief met alleen 22/80/443 en gunicorn-luistersocket loopback-only geverifieerd; de externe meetlocatie-check is nog open.)
- [ ] Review nginx headers, TLS ciphers, host allowlist, rate limiting en admin-route exposure.
- [ ] Controleer effectieve Fail2Ban-jails, bans en logbron.
- [ ] Controleer PostgreSQL role attributes, `BYPASSRLS`, grants, SSL-certificaatvalidatie en backup-DB-isolatie.
- [ ] Voer twee-tenant authenticated RLS- en authorizationtests uit.
- [ ] Inventariseer alle `csrf.exempt`, directe HTTP-calls en upload-/documentparsers.
- [ ] Beoordeel providercontracten, OSINT-inputdisclosure, retention, lawful basis en data residency.
- [ ] Beoordeel telemetry/Sentry minimization en production toggle met operators.
- [x] Bevestig default-accountrotatie, 2FA-enforcement voor privileged accounts en sessie-invalidatie. (2026-09-06: `testu00` is een privileged testaccount zonder TOTP en is gedeactiveerd; `inves00` was inactief maar had een live wachtwoord — wachtwoord gereset en TOTP gewist; beide via de app-omgeving archived in `audit_logs`.)
- [ ] Beoordeel backup key custody, license private-key custody, dual control en restoretoegang.
- [ ] Beslis of telemetry standaard aan mag blijven en welke velden noodzakelijk zijn.
- [ ] Beslis of license server en dashboard een gedeelde beschikbaarheids-/incidentprocedure krijgen.
- [x] Beslis of Gunicorn één worker bewust is of naar meerdere workers/queue moet, met load- en locktest. (Besluit 2026-09-06: blijven op 2 sync workers, 1 thread — 2-worker canary geslaagd; zie `PLAN-GUNICORN-CONCURRENCY-TUNING.md` §0.)
- [ ] Beslis welke security scans blocking moeten zijn en wie uitzonderingen expireert.

## Appendix A: relevante bestanden en commando's

### Relevante bestanden
- `app.py:107-221,227-321` — request-ID, tenantcontext, HTTPS, sessions en secrets-loading.
- `cms/config.py:25-145` — productieflags, cookies, DB-SSL, uploads en required settings.
- `cms/auth.py:110-212,239-727` — RBAC en tenant/entity authorization.
- `cms/services/ssrf_guard.py` en `cms/services/http_utils.py:444-567` — SSRF en redirectvalidatie.
- `cms/encryption_utils.py` en `cms/fingerprint_utils.py` — encryptie/search fingerprints.
- `cms/services/telemetry.py` en `cms/services/license.py` — externe metadataflow en licentiecache.
- `migrations/versions/d2e3f4a5b6c7_re_enable_force_rls.py` — RLS/`WITH CHECK`.
- `scripts/backup.sh`, `scripts/verify_backup.sh`, `scripts/health_refresh.py` — backup, DR en health-producer.
- `deploy/*.service`, `deploy/*.timer`, live `osint-dashboard.service` — service-isolatie en scheduling.
- `.github/workflows/ci.yml`, `scripts/preflight.sh`, `scripts/deploy.sh` — CI en deploygates.
- `DISASTER_RECOVERY.md`, `AGENTS_OPSEC.md`, `AGENTS_OPERATIONS.md` — operationele claims en beperkingen.

### Read-only evidence commands

De volgende commando's zijn gebruikt of zijn reproduceerbare, secret-veilige voorbeelden. Ze lezen geen `.env`, database-records, logs met payloads, private keys of license payloads:

```bash
ssh -o ConnectTimeout=15 root@joost.iveras.com \
  'cd /opt/osint-dashboard && git remote -v && git status --short && git log -1 --oneline'
ssh -o ConnectTimeout=15 root@joost.iveras.com \
  'systemctl list-units --type=service --state=running --no-legend'
ssh -o ConnectTimeout=15 root@joost.iveras.com \
  'systemctl show osint-dashboard.service -p User -p Group -p ExecStart -p ProtectSystem -p ProtectHome -p NoNewPrivileges -p PrivateTmp'
ssh -o ConnectTimeout=15 root@joost.iveras.com \
  'systemctl list-timers --all --no-legend'
ssh -o ConnectTimeout=15 root@joost.iveras.com \
  'ufw status'
ssh -o ConnectTimeout=15 root@joost.iveras.com \
  'cd /opt/osint-dashboard && /opt/osint-dashboard/venv/bin/python -m pip check'
ssh -o ConnectTimeout=15 root@joost.iveras.com \
  'cd /opt/osint-dashboard && git grep -n -E "csrf.exempt|FORCE ROW LEVEL SECURITY|validate_url" -- app.py cms migrations'
```

Waarden uit `.env`, process environment, database, logs, license registry en keyfiles zijn bewust niet gelezen of opgenomen. Dit assessment bevat geen secrets of volledige gevoelige productiegegevens.
