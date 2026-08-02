# Iveras License & Telemetry Server

Fase 1 van het telemetrie + licensing-systeem. Centrale registry voor alle
OSINT Dashboard installs: registratie bij install + dagelijkse heartbeat met
systeeminfo. Fase 2 voegt Ed25519-licenties toe, fase 3 Stripe-betalingen.

## Endpoints

| Method | Path             | Auth                  | Doel                                  |
|--------|------------------|-----------------------|---------------------------------------|
| POST   | `/api/register`  | `Bearer <token>`      | Registreer een install (idempotent)   |
| POST   | `/api/telemetry` | `Bearer <token>`      | Dagelijkse heartbeat + systeeminfo    |
| GET    | `/`              | Basic (dashboard)     | Overzicht alle installaties           |
| GET    | `/api/installs`  | Basic (dashboard)     | Registry als JSON                     |
| GET    | `/health`        | —                      | Health check                          |

De client stuurt `{"install_id": "...", "info": {...}}` met
`Authorization: Bearer <INSTALL_TOKEN>` en `X-Install-ID`. De server slaat
alleen een SHA-256 hash van het token op. Bij een onbekend install_id + geldige
token registreert de server de install opnieuw (idempotent). Verkeerde token →
`403`.

## Deployment (eigen VPS, `license.iveras.com`)

```bash
sudo useradd -r -s /usr/sbin/nologin license || true
sudo mkdir -p /opt/license-server/data
# Let op: GEEN plain `--delete` — dat wist runtime-bestanden (.env, venv/, data/)
# die niet in git staan. De excludes houden die in stand bij updates.
sudo rsync -a --delete \
    --exclude='.env' --exclude='venv/' --exclude='data/' --exclude='.cache/' \
    ./license-server/ /opt/license-server/
sudo chown -R license:license /opt/license-server

sudo -u license python3 -m venv /opt/license-server/venv
sudo -u license /opt/license-server/venv/bin/pip install -r /opt/license-server/requirements.txt

sudo tee /opt/license-server/.env > /dev/null <<EOF
ADMIN_USER=admin
ADMIN_PASSWORD=$(openssl rand -hex 24)
EOF
sudo chmod 600 /opt/license-server/.env
sudo cat /opt/license-server/.env   # noteer ADMIN_PASSWORD (of vervang door eigen wachtwoord)

sudo cp license-server/deploy/license-server.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now license-server
```

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
  te herstellen.
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
