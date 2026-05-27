# Social Media

Het **Social Accounts** systeem beheert sociale media profielen van subjects en biedt extractie functionaliteiten voor OSINT-doeleinden.

## Social Accounts Toevoegen

1. Open de subject detailpagina
2. Klik **Add Social Account**
3. Selecteer het platform:
   - Facebook, Twitter/X, LinkedIn, Instagram, YouTube, TikTok, Snapchat, Reddit, Telegram, WhatsApp, Signal, Discord, GitHub, OnlyFans, Patreon, en vele andere
4. Vul de **username** of **profile URL** in
5. Klik **Save**

De profiel-URL wordt automatisch gegenereerd op basis van het platform en de gebruikersnaam.

## Social Accounts Overzicht

Op de subject detailpagina worden alle accounts getoond met:

- Platform icoon en naam
- Gebruikersnaam
- Klikbare profiel-URL
- Datum van toevoeging

## Social Extraction

De **Social Extraction** functionaliteit haalt extra informatie op uit sociale media profielen:

### Huidige Extractie Methodes

- **WhatsApp** — controleert of het telefoonnummer actief is op WhatsApp via de whatsapp.checkleaked.cc API (of fallback via api.whatsapp.com)
- **Telegram** — controleert of het telefoonnummer of de gebruikersnaam actief is op Telegram
- **Overige platforms** — via de Brave Search API (indien geconfigureerd)

### WhatsApp Presence

WhatsApp presence check toont:

- Of het nummer een WhatsApp-account heeft
- Of het een business/enterprise account is
- Geverifieerd of niet
- Gebanned of niet
- Lijntype (mobile, voip)
- Profielfoto (indien beschikbaar, opgeslagen als base64)
- Cached status en check datum

De resultaten worden opgeslagen in de `PhoneLookup` tabel zodanig dat ze niet opnieuw hoeven worden opgevraagd.

## API Keys

Voor sommige social extractie functies zijn API keys nodig, configureerbaar via **Settings**:

| Key | Service | Noodzakelijk voor |
|-----|---------|-------------------|
| `brave_api_key` | Brave Search | Social media search |
| `whatsapp_checkleaked_key` | whatsapp.checkleaked.cc (RapidAPI) | WhatsApp presence check |
