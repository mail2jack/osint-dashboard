# Social Media

The **Social Accounts** system manages social media profiles of subjects and provides extraction features for OSINT purposes.

## Adding Social Accounts

1. Open the subject detail page
2. Click **Add Social Account**
3. Select the platform:
   - Facebook, Twitter/X, LinkedIn, Instagram, YouTube, TikTok, Snapchat, Reddit, Telegram, WhatsApp, Signal, Discord, GitHub, OnlyFans, Patreon, and many more
4. Enter the **username** or **profile URL**
5. Click **Save**

The profile URL is automatically generated based on the platform and username.

## Social Accounts Overview

On the subject detail page, all accounts are shown with:

- Platform icon and name
- Username
- Clickable profile URL
- Date added

## Social Extraction

The **Social Extraction** feature retrieves additional information from social media profiles:

### Current Extraction Methods

- **WhatsApp** — checks whether the phone number is active on WhatsApp via the whatsapp.checkleaked.cc API (or fallback via api.whatsapp.com)
- **Telegram** — checks whether the phone number or username is active on Telegram
- **Other platforms** — via the Brave Search API (if configured)

### WhatsApp Presence

WhatsApp presence check shows:

- Whether the number has a WhatsApp account
- Whether it is a business/enterprise account
- Verified or not
- Banned or not
- Line type (mobile, voip)
- Profile picture (if available, stored as base64)
- Cached status and check date

The results are stored in the `PhoneLookup` table so they do not need to be queried again.

## API Keys

Some social extraction features require API keys, configurable via **Settings**:

| Key | Service | Required for |
|-----|---------|--------------|
| `brave_api_key` | Brave Search | Social media search |
| `whatsapp_checkleaked_key` | whatsapp.checkleaked.cc (RapidAPI) | WhatsApp presence check |
