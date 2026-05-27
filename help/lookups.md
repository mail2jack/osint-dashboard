# Lookups

Het dashboard biedt verschillende **lookup** functionaliteiten om data te verrijken uit externe bronnen. Deze zijn beschikbaar als actieknoppen op subject en client detailpagina's.

## 📞 Phone Lookup

Verrijkt een telefoonnummer met open bronnen.

**Endpoint**: `POST /cms/api/phone-lookup`

**Resultaten**:
- Validatie (is het nummer geldig?)
- Geformatteerd nummer (E164-formaat: `+31634407404`)
- Land en regio
- Carrier (provider)
- Lijntype (mobile, landline, voip)
- Tijdzone
- WhatsApp aanwezigheid
- Telegram aanwezigheid

**Bronnen**: `phonenumbers` library, whatsapp.checkleaked.cc (RapidAPI), Telegram scraping

## 🔍 Postcode Check (Kadaster/PDOK)

Vult automatisch straat + woonplaats in op basis van Nederlandse postcode + huisnummer.

**Endpoint**: `POST /cms/api/kadaster-lookup`

**Werking**: Roept de PDOK BAG API aan voor adresgegevens uit de Basisregistratie Adressen en Gebouwen.

**Gebruik**: Klik op de 🔍 knop naast het postcodeveld op create/edit formulieren.

## 🚔 Politiebureau Lookup

Vindt het dichtstbijzijnde politiebureau voor een adres.

**Endpoint**: `POST /cms/api/politiebureau-lookup`

**Resultaten**:
- Naam en adres van het bureau
- Telefoonnummer
- Openingstijden
- OSM Maps link
- Politie.nl pagina URL

**Bron**: `api.politie.nl/politiebureaus/v1`

## 🌍 Interpol + Politie Check

Zoekt een subject naam in INTERPOL en Nederlandse politie databases.

**Endpoint**: `POST /cms/check-policie-data`

**Bronnen**:
- **INTERPOL Red Notices** — internationaal gezochte personen
- **INTERPOL Yellow Notices** — vermiste personen
- **politie.nl/gezocht** — Nederlandse opsporingsberichten
- **politie.nl/vermist** — Nederlandse vermiste personen

**Rate limiting**: De INTERPOL API (Akamai) kan 403 teruggeven na veel calls. Falls back naar politie.nl scraping.

## 🚢 Vessel Lookup

Zoekt scheepsgegevens in meerdere maritieme databases.

**Endpoint**: `POST /cms/api/vessel-lookup`

**Bronnen** (in volgorde):
1. **VesselFinder** — gratis, zoekt op MMSI of naam
2. **MarinePlan** — AIS data, vereist API key (`marineplan_api_key`)
3. **KVNR Schepenzoeker** — IMO/naam, publiek
4. **Binnenvaart.eu** — ENI/naam, publiek
5. **Equasis** — IMO, vereist gratis account (`equasis_email` + `equasis_password`)

**Resultaten** (samengevoegd uit alle bronnen):
- IMO nummer
- MMSI nummer
- ENI nummer
- Scheepsnaam
- Vlag / nationaliteit
- Type
- Bouwjaar

**Opslaan**: Klik **Update Subject** om IMO/MMSI/ENI/flag op te slaan op het subject.

## 🚗 RDW Lookup

Zoekt Nederlandse voertuiggegevens op basis van kenteken.

**Endpoint**: `POST /cms/api/rdw-lookup`

**Bron**: Nederlandse RDW (Rijksdienst voor het Wegverkeer) open data API.

**Resultaten**: Kenteken, merk, model, bouwjaar, kleur, brandstof, etc.
