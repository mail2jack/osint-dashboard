# Iveras License & Telemetry Server

Fase 1 + 2 van het telemetrie + licensing-systeem. Centrale registry voor alle
OSINT Dashboard installs: registratie bij install + dagelijkse heartbeat met
systeeminfo, plus Ed25519-ondertekende licenties die de app offline kan
verifiëren. Fase 3 voegt Stripe-betalingen toe.

> **Productie**: zet `LICENSE_ENV=production` en een vaste
> `LICENSE_ADMIN_SECRET` in `/opt/license-server/.env`. Zonder die secret weigert
> de server te starten (fail-fast); de random fallback is alleen voor dev/tests
> en maakt sessies/CSRF ongeldig bij elke herstart.

## Endpoints

| Method | Path             | Auth                  | Doel                                  |
|--------|------------------|-----------------------|---------------------------------------|
| POST   | `/api/register`  | `Bearer <token>`      | Registreer een install (idempotent); geeft meteen een trial-licentie |
| POST   | `/api/telemetry` | `Bearer <token>`      | Dagelijkse heartbeat + systeeminfo + actuele licentie |
| GET    | `/api/license`   | `Bearer <token>` + `X-Install-ID` | Opgehaalde ondertekende licentie |
| GET    | `/`              | Basic (dashboard)     | Overzicht alle installaties + licenties |
| GET    | `/api/installs`  | Basic (dashboard)     | Registry als JSON (incl. licentie per install) |
| POST   | `/license/issue` | Basic (dashboard)     | Licentie uitgeven/vervangen (form: install_id, plan, days/expires) |
| POST   | `/license/revoke`| Basic (dashboard)     | Licentie intrekken (form: install_id) |
| GET    | `/health`        | —                      | Health check                          |

De client stuurt `{"install_id": "...", "info": {...}}` met
`Authorization: Bearer <INSTALL_TOKEN>` en `X-Install-ID`. De server slaat
alleen een SHA-256 hash van het token op. Bij een onbekend install_id + geldige
token registreert de server de install opnieuw (idempotent). Verkeerde token →
`403`.

## Licenties (fase 2, Ed25519)

Elke install krijgt automatisch een **trial-licentie** (default 30 dagen, env
`TRIAL_DAYS`) bij registratie. Licenties verstrekken/vervangen/revoken kan op
twee manieren: via de webdashboard-acties op `https://license.iveras.com`
(issue-formulier + per-rij revoke-knop, achter de basic-auth login) of via de
CLI hieronder. De payload is een JSON-document (`install_id`, `license_id`,
`plan`, `issued_at`, `expires_at`) ondertekend met Ed25519; de app verifieert
dit offline met de ingebakken publieke sleutel. Revocatie is online (de app
krijgt `status: "revoked"` mee bij de check-in).

### Keypair genereren (éénmalig, na deploy)

```bash
sudo -u license env HOME=/opt/license-server \
  /opt/license-server/venv/bin/python3 /opt/license-server/cli.py keys:generate
```

Schrijft `keys/private.pem` (mode 600, eigenaar `license`) en print de publieke
sleutel. Die publieke sleutel is al als default ingebakken in
`cms/services/license.py` en via Settings → General (`license_public_key`)
overschrijfbaar — alleen wijzigen als je de sleutels roteert. `keys/`, `data/`
en `.env` staan in `.gitignore`, dus die gaan niet mee met `rsync --delete`.

### Licenties beheren (dashboard)

Op `https://license.iveras.com` (inloggen met de `ADMIN_USER`/`ADMIN_PASSWORD`
uit `/opt/license-server/.env`) staat boven de tabel een **Issue license**
formulier: kies de install, het plan (full/trial), het aantal dagen (default
365) of een vaste vervaldatum. Per rij staat een **Revoke**-knop. Beide roepen
dezelfde logica aan als de CLI (zie `app.py: _issue_license`/`_revoke_license`).

### Licenties beheren (CLI)

```bash
CLI="sudo -u license env HOME=/opt/license-server /opt/license-server/venv/bin/python3 /opt/license-server/cli.py"

$CLI license:list
$CLI license:new --install <install_id> --plan full --days 365   # vervangt trial
$CLI license:new --install <install_id> --plan full --expires 2026-12-31
$CLI license:revoke --install <install_id>
```

`license:new` overschrijft de vorige licentie van de install; de app toont de
nieuwe bij de volgende check-in. `--days` en `--expires` zijn optioneel
(default 365 dagen / respectievelijk eind van de dag).

### App-side (soft trial, per install)

- `cms/services/license.py` — offline verificatie + toestandsmachine
  (present/valid/plan/expires/revoked), gated features.
- Gates: trial blokkeert `ai`, `spiderfoot`, `vessel`, `phone` en beperkt
  tenants tot `trial_tenant_limit` (default 1).
- Uitschakelen: `LICENSE_ENFORCEMENT=off` in `.env` van de app of een geldige
  full-licentie.
- UI: banner in de header (trial/verlopen/revoked/invalid) + licentiestatus in
  Settings → General.

## Deployment (eigen VPS, `license.iveras.com`)

```bash
sudo useradd -r -s /usr/sbin/nologin license || true
sudo mkdir -p /opt/license-server/data
# Let op: GEEN plain `--delete` — dat wist runtime-bestanden (.env, venv/, data/,
# keys/) die niet in git staan. De excludes houden die in stand bij updates.
# Vooral `keys/` is kritiek: zonder de privésleutel kunnen er geen licenties
# meer worden ondertekend (bestaande blijven wel verifieerbaar). Zorg ook dat
# backup.sh de privésleutel meeneemt (doet het automatisch, mits leesbaar).
sudo rsync -a --delete \
    --exclude='.env' --exclude='venv/' --exclude='data/' --exclude='keys/' --exclude='.cache/' \
    ./license-server/ /opt/license-server/
sudo chown -R license:license /opt/license-server

sudo -u license python3 -m venv /opt/license-server/venv
sudo -u license /opt/license-server/venv/bin/pip install -r /opt/license-server/requirements.txt

sudo tee /opt/license-server/.env > /dev/null <<EOF
LICENSE_ENV=production
LICENSE_ADMIN_SECRET=$(openssl rand -hex 32)
ADMIN_USER=admin
ADMIN_PASSWORD=$(openssl rand -hex 24)
EOF
sudo chmod 600 /opt/license-server/.env
sudo cat /opt/license-server/.env   # noteer ADMIN_PASSWORD (of vervang door eigen wachtwoord)

sudo cp license-server/deploy/license-server.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now license-server
```

Na de deploy nog eenmalig `keys:generate` draaien (zie boven) zodat
register/trial-uitgifte kan werken.

### Nginx + TLS (certbot)

```bash
sudo cp license-server/deploy/nginx.conf /etc/nginx/sites-available/license
sudo ln -s /etc/nginx/sites-available/license /etc/nginx/sites-enabled/license
sudo nginx -t
sudo certbot --nginx -d license.iveras.com
sudo systemctl reload nginx
```

Het meegeleverde `nginx.conf` is bewust minimaal (alleen poort 80); certbot
voegt zelf het 443/SSL-block en de 80→301-redirect toe. Zonder geldig certificaat
zou een 443-block `nginx -t` laten falen.

### Zelfde VPS als de productie (aanbevolen optie)

De license server is een klein los proces (localhost:8000, eigen systemd-user
`license`, eigen SQLite) en kan probleemloos op dezelfde VPS draaien als de
OSINT Dashboard. Nginx routeert op `server_name`, dus het bestaande prod-block
(`server_name _` of een domein) en het license-block naast elkaar:

1. Zelfde stappen als hierboven (rsync, venv, systemd).
2. Pas `license-server/deploy/nginx.conf` aan zodat de blocks niet botsen met
   de prod-config: het license-block krijgt `server_name license.iveras.com;`
   op `listen 80` (301 → https) en `listen 443 ssl`. De prod-site blijft
   `default_server` op poort 80 — nginx kiest per request op basis van de
   `Host`-header, specifiek wint van `_`.
3. `sudo certbot --nginx -d license.iveras.com` werkt direct: de HTTP-01
   challenge komt binnen op deze zelfde box en nginx.
4. **Geen firewall-wijziging nodig**: poort 8000 is aan `127.0.0.1` gebonden.

Caveat: als deze VPS uitvalt, ligt ook de license server eruit (shared fate).
In fase 1 (telemetrie) is dat onschuldig — de client faalt stil. In fase 2
(Ed25519) is de licentie offline-verifieerbaar, dus de app blijft draaien;
alleen online revocatie-checks hangen dan.

**Vergeet niet de registry te back-uppen**: neem
`/opt/license-server/data/license.db` op in je backup-script. `scripts/backup.sh`
doet dit automatisch (stap 5). De `osint`-user heeft hiervoor leestoegang nodig:

```bash
sudo usermod -aG license osint
sudo chmod g+rX /opt/license-server/data
```

### Belangrijk

- **`ADMIN_PASSWORD` is verplicht.** Zonder wachtwoord is het dashboard
  onbeveiligd (dev-modus + waarschuwing in logs).
- Back-up van `/opt/license-server/data/license.db` is voldoende om de registry
  te herstellen. **Back-up ook `keys/private.pem`**: zonder de privésleutel kun
  je geen nieuwe licenties uitgeven (bestaande blijven wel verifieerbaar).
- Het token van een client wordt nergens in plaintext bewaard; verlies van het
  `INSTALL_TOKEN` betekent her-registratie met een nieuw token.

## Lokale test

```bash
cd license-server
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
ADMIN_PASSWORD=dev .venv/bin/python app.py   # http://localhost:8000
curl -X POST http://localhost:8000/api/register \
  -H "Authorization: Bearer testtoken" \
  -H "Content-Type: application/json" \
  -d '{"install_id":"demo-1","info":{"hostname":"demo","os_name":"Linux"}}'
```
