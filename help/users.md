# Gebruikers & Authenticatie

Het **Users** systeem beheert gebruikersaccounts, rollen en toegangsrechten.

## Gebruikersrollen

| Rol | Rechten |
|-----|---------|
| **Admin** | Volledige toegang, inclusief settings en gebruikersbeheer |
| **Senior Investigator** | Alle functies behalve settings en gebruikersbeheer |
| **Investigator** | Standaard onderzoeker: cases, subjects, findings |
| **Viewer** | Alleen lezen: kan data bekijken maar niet bewerken |

## Inloggen

1. Ga naar het inlogscherm op `/auth/login`
2. Voer gebruikersnaam en wachtwoord in
3. Klik **Login**

### Twee-Factor Authenticatie (2FA)

Gebruikers kunnen 2FA inschakelen via een TOTP app (Google Authenticator, Authy, etc.):

1. Ga naar je profielpagina
2. Klik **Enable 2FA**
3. Scan de QR-code met je authenticator app
4. Voer een eenmalige code in om te verifiëren

Een ✓ icoon naast je naam in de header geeft aan dat 2FA actief is.

## Wachtwoord Vergeten

1. Klik op **Forgot Password** op het inlogscherm
2. Voer je e-mailadres in
3. Ontvang een reset link (48 uur geldig)
4. Klik de link en kies een nieuw wachtwoord (minimaal 8 tekens)

Let op: wachtwoorden worden **nooit** per e-mail verstuurd.

## Gebruiker Aanmaken (Admin)

1. Ga naar **Settings** → **Users**
2. Klik **Create User**
3. Vul in: gebruikersnaam, e-mail, volledige naam, rol
4. Optioneel: stuur een "Set Password" e-mail
5. Het systeem genereert een tijdelijk wachtwoord dat alleen op het scherm wordt getoond

## Profiel Bewerken

Klik op je naam in de header om je profiel te bekijken/bewerken:

- Wijzig je volledige naam
- Wijzig je e-mailadres
- Wijzig je wachtwoord
- Schakel 2FA in/uit

## Beveiliging

- **Wachtwoordvereisten**: minimaal 8 tekens
- **Sessie verloopt**: na 8 uur inactiviteit
- **Rate limiting**: 30 create/edit acties per 60 seconden
- **Failed login attempts**: account wordt tijdelijk vergrendeld na meerdere mislukte pogingen
- **HTTP-only cookies**: sessiecookies zijn niet toegankelijk via JavaScript
- **Security headers**: X-Content-Type-Options, X-Frame-Options, X-XSS-Protection, HSTS worden meegestuurd in responses
