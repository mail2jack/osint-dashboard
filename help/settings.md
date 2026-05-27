# Instellingen

De **Settings** pagina beheert de applicatieconfiguratie. Alleen admin-gebruikers hebben toegang.

## Algemene Instellingen

| Setting | Omschrijving |
|---------|-------------|
| **Application Name** | Pas de titel van het dashboard aan |
| **Theme Style** | Klassiek of professioneel thema |

## API Sleutels

Bewaar API-sleutels voor externe services. Deze worden in de database opgeslagen (niet in `.env`).

### OSINT Services

| Setting | Service | Verkrijg via |
|---------|---------|-------------|
| `spiderfoot_url` | SpiderFoot URL | Eigen installatie |
| `spiderfoot_username` | SpiderFoot gebruikersnaam | `~/.spiderfoot/passwd` |
| `spiderfoot_password` | SpiderFoot wachtwoord | `~/.spiderfoot/passwd` |
| `overheid_api_key` | Overheid.io API | overheid.io |
| `brave_api_key` | Brave Search API | brave.com/search/api |
| `twoc` | TwoChat WhatsApp | twochat.nl |

### Vessel/Schepen Lookups

| Setting | Service | Verkrijg via |
|---------|---------|-------------|
| `marineplan_api_key` | MarinePlan | marineplan.com |
| `equasis_email` | Equasis login | equasis.org (gratis registratie) |
| `equasis_password` | Equasis wachtwoord | equasis.org |

### WhatsApp Presence

| Setting | Service | Verkrijg via |
|---------|---------|-------------|
| `whatsapp_checkleaked_key` | whatsapp.checkleaked.cc | RapidAPI (50 req/maand gratis) |

## Update Instellingen

- **Update Check Repo** — GitHub repository om op updates te checken (formaat: `owner/repo`)
- Het dashboard checkt bij elke pagina laad of er nieuwe versies of commits beschikbaar zijn
- Bij een update verschijnt er een blauwe banner bovenaan de pagina
- Klik **Update Now** om de update uit te voeren (vereist sudo-rechten voor git/chown/systemctl)

## Encryptie

Gevoelige velden (ID-nummers, kentekens, IMO/MMSI/ENI) worden versleuteld opgeslagen met **Fernet** encryptie.

De encryptiesleutel wordt:
1. Uit de `CMS_ENCRYPTION_KEY` omgevingsvariabele gelezen
2. Of uit het `.cms_key` bestand in de projectroot
3. Of automatisch gegenereerd bij de eerste start

## Gebruikersbeheer

Vanuit Settings kunnen admins:

- Alle gebruikers bekijken
- Nieuwe gebruikers aanmaken (stuurt een "Set Password" e-mail)
- Gebruikersrollen wijzigen:
  - **Admin** — volledige toegang
  - **Senior Investigator** — alle functies behalve settings
  - **Investigator** — standaard onderzoeker
  - **Viewer** — alleen lezen
- Gebruikers uitschakelen of verwijderen
- Wachtwoord reset sturen

## Wachtwoord Reset Flow

1. Admin maakt nieuwe gebruiker aan of klikt "Reset Password"
2. Systeem genereert een token (48 uur geldig)
3. Gebruiker ontvangt e-mail met reset link
4. Gebruiker kiest een nieuw wachtwoord (minimaal 8 tekens)
5. Token wordt eenmalig gebruikt en direct verwijderd

Let op: wachtwoorden worden **nooit** in e-mails meegestuurd.
