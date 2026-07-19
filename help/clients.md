# Clients

**Clients** represent the organizations or persons that request investigations. Each case is linked to exactly one client.

## Client Fields

| Field | Description |
|-------|-------------|
| **Name** | Name of the organization or person (required) |
| **Contact Person** | Name of the contact person |
| **Email** | Email address |
| **Phone** | Phone number |
| **Address** | Street + number + postal code + city |

## Creating a Client

1. Click **Clients** → **New Client**
2. Fill in the details
3. Click **Save**

You are redirected to the client detail page where you can see all linked cases.

## Client Detail Page

Shows client data and a list of all associated cases. Click on a case to navigate directly.

## Archiving

Clients can be archived instead of deleted. Archived clients are hidden from the main list but remain accessible via the **Show Archived** toggle.

## Address Features

Each address card has action buttons:

- **🔍 Postcode Check** — calls the Dutch BAG (PDOK) API to auto-fill street + city based on postal code + house number
- **🚔 Police Station** — finds the nearest police station for that address via the Politie NL API

## Phone Number Check

If a phone number is stored, a 📞 button appears. It enriches the number with:

- Carrier (provider)
- Line type (mobile, voip, landline)
- Region and timezone
- WhatsApp presence
- Telegram presence

## Duplicate Detection

When creating a client, the system automatically checks for similar existing clients to prevent duplicate registrations.
