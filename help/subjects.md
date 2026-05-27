# Subjects

Een **Subject** is een persoon, bedrijf of schip van interesse in een onderzoek. Subjects kunnen aan meerdere cases worden gekoppeld.

## Subject Types

### Person
Een individu met persoonsgegevens. Extra functionaliteiten:

- **Faces** — geüploade gezichtsafbeeldingen in een gallery
- **Social Accounts** — sociale media profielen
- **Phone Check** — telefoonnummer verrijking
- **Interpol Check** — zoeken in INTERPOL Red/Yellow Notices

### Company
Een organisatie met bedrijfsgegevens.

- **KvK nummer** — Nederlandse Kamer van Koophandel registratie
- **Address** — vestigingsadres met postcode check

### Vessel
Een schip met maritieme identificatie. Extra functionaliteiten:

- **IMO nummer** — International Maritime Organization ID
- **MMSI** — Maritime Mobile Service Identity
- **ENI nummer** — European Vessel Identification
- **🚢 Check Vessel** — unified lookup via VesselFinder, MarinePlan, KVNR, Binnenvaart.eu, Equasis

## Subject Velden

| Veld | Type | Omschrijving |
|------|------|-------------|
| **Name** | Alle | Volledige naam |
| **Type** | Alle | Person / Company / Vessel |
| **Date of Birth** | Person | Geboortedatum |
| **Phone** | Person | Telefoonnummer |
| **License Plate** | Person | Kenteken (versleuteld opgeslagen) |
| **ID Number** | Person | Identiteitsbewijs nummer (versleuteld) |
| **Notes** | Alle | Interne notities (gemixt naar Comments model) |
| **Address** | Alle | Adresgegevens (meerdere adressen mogelijk) |
| **Contacts** | Alle | Contactpersonen (naam, email, telefoon, rol) |

## Subject Aanmaken

1. Navigeer naar **Subjects** → **New Subject**
2. Selecteer het **Subject Type**
3. Vul de relevante velden in
4. Klik **Save**

## Duplicaat Detectie

Bij het aanmaken wordt gecontroleerd op bestaande subjects met een vergelijkbare naam. Bij een mogelijke duplicate krijg je een waarschuwing te zien.

## OSINT Acties

Elke subject detailpagina heeft actieknoppen:

- **📞 Check Phone** — verrijkt telefoonnummer met carrier, regio, WhatsApp/Telegram
- **🌍 Check Interpol** — zoekt INTERPOL Red Notices (gezocht) + Yellow Notices (vermist) + politie.nl vermist/gezocht
- **🚢 Check Vessel** (alleen Vessel type) — zoekt scheepsgegevens in meerdere maritieme databases
- **Social Accounts** — voeg sociale media profielen toe en doorzoek ze

## Faces (Person type)

Geüploade gezichtsafbeeldingen worden getoond in een gallery. Elke face kan worden gebruikt voor toekomstige biometrische matching. Upload via de **Add Face** knop op de subject detailpagina.

## Adressen

Subjects kunnen meerdere adressen hebben. Elk adres heeft:

- Straat + nummer + postcode + woonplaats
- 🔍 Postcode Check (BAG/PDOK)
- 🚔 Politiebureau Lookup

## Contacts

Contacts zijn gerelateerde personen (familie, collega's, etc.) met naam, email, telefoon en een omschrijving van de relatie.
