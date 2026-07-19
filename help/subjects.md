# Subjects

A **Subject** is a person, company, or vessel of interest in an investigation. Subjects can be linked to multiple cases.

## Subject Types

### Person
An individual with personal data. Additional features:

- **Faces** — uploaded face images in a gallery
- **Social Accounts** — social media profiles
- **Phone Check** — phone number enrichment
- **Interpol Check** — search INTERPOL Red/Yellow Notices

### Company
An organization with business data.

- **KvK number** — Dutch Chamber of Commerce registration
- **Address** — registered address with postcode check

### Vessel
A vessel with maritime identification. Additional features:

- **IMO number** — International Maritime Organization ID
- **MMSI** — Maritime Mobile Service Identity
- **ENI number** — European Vessel Identification
- **🚢 Check Vessel** — unified lookup via VesselFinder, MarinePlan, KVNR, Binnenvaart.eu, Equasis

## Subject Fields

| Field | Type | Description |
|-------|------|-------------|
| **Name** | All | Full name |
| **Type** | All | Person / Company / Vessel |
| **Date of Birth** | Person | Date of birth |
| **Phone** | Person | Phone number |
| **License Plate** | Person | License plate (stored encrypted) |
| **ID Number** | Person | ID document number (encrypted) |
| **Notes** | All | Internal notes (migrated to Comments model) |
| **Address** | All | Address data (multiple addresses possible) |
| **Contacts** | All | Contact persons (name, email, phone, role) |

## Creating a Subject

1. Navigate to **Subjects** → **New Subject**
2. Select the **Subject Type**
3. Fill in the relevant fields
4. Click **Save**

## Duplicate Detection

When creating, the system checks for existing subjects with a similar name. If a possible duplicate is found, you will see a warning.

## OSINT Actions

Each subject detail page has action buttons:

- **📞 Check Phone** — enriches phone number with carrier, region, WhatsApp/Telegram
- **🌍 Check Interpol** — searches INTERPOL Red Notices (wanted) + Yellow Notices (missing) + politie.nl missing/wanted
- **🚢 Check Vessel** (Vessel type only) — searches vessel data across multiple maritime databases
- **Social Accounts** — add social media profiles and search them

## Faces (Person type)

Uploaded face images are shown in a gallery. Each face can be used for future biometric matching. Upload via the **Add Face** button on the subject detail page.

## Addresses

Subjects can have multiple addresses. Each address has:

- Street + number + postal code + city
- 🔍 Postcode Check (BAG/PDOK)
- 🚔 Police Station Lookup

## Contacts

Contacts are related persons (family, colleagues, etc.) with name, email, phone, and a description of the relationship.
