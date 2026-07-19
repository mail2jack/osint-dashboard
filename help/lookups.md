# Lookups

The dashboard provides various **lookup** features to enrich data from external sources. These are available as action buttons on subject and client detail pages.

## 📞 Phone Lookup

Enriches a phone number with open sources.

**Endpoint**: `POST /cms/api/phone-lookup`

**Results**:
- Validation (is the number valid?)
- Formatted number (E164 format: `+31634407404`)
- Country and region
- Carrier (provider)
- Line type (mobile, landline, voip)
- Timezone
- WhatsApp presence
- Telegram presence

**Sources**: `phonenumbers` library, whatsapp.checkleaked.cc (RapidAPI), Telegram scraping

## 🔍 Postcode Check (Kadaster/PDOK)

Auto-fills street + city based on Dutch postal code + house number.

**Endpoint**: `POST /cms/api/kadaster-lookup`

**How it works**: Calls the PDOK BAG API for address data from the Basisregistratie Adressen en Gebouwen.

**Usage**: Click the 🔍 button next to the postal code field on create/edit forms.

## 🚔 Police Station Lookup

Finds the nearest police station for an address.

**Endpoint**: `POST /cms/api/politiebureau-lookup`

**Results**:
- Name and address of the station
- Phone number
- Opening hours
- OSM Maps link
- Politie.nl page URL

**Source**: `api.politie.nl/politiebureaus/v1`

## 🌍 Interpol + Police Check

Searches a subject name in INTERPOL and Dutch police databases.

**Endpoint**: `POST /cms/check-policie-data`

**Sources**:
- **INTERPOL Red Notices** — internationally wanted persons
- **INTERPOL Yellow Notices** — missing persons
- **politie.nl/gezocht** — Dutch wanted persons
- **politie.nl/vermist** — Dutch missing persons

**Rate limiting**: The INTERPOL API (Akamai) may return 403 after many calls. Falls back to politie.nl scraping.

## 🚢 Vessel Lookup

Searches vessel data across multiple maritime databases.

**Endpoint**: `POST /cms/api/vessel-lookup`

**Sources** (in order):
1. **VesselFinder** — free, searches by MMSI or name
2. **MarinePlan** — AIS data, requires API key (`marineplan_api_key`)
3. **KVNR Schepenzoeker** — IMO/name, public
4. **Binnenvaart.eu** — ENI/name, public
5. **Equasis** — IMO, requires free account (`equasis_email` + `equasis_password`)

**Results** (merged from all sources):
- IMO number
- MMSI number
- ENI number
- Vessel name
- Flag / nationality
- Type
- Year built

**Saving**: Click **Update Subject** to save IMO/MMSI/ENI/flag on the subject.

## 🚗 RDW Lookup

Searches Dutch vehicle data by license plate.

**Endpoint**: `POST /cms/api/rdw-lookup`

**Source**: Dutch RDW (Rijksdienst voor het Wegverkeer) open data API.

**Results**: License plate, make, model, year, color, fuel type, etc.
