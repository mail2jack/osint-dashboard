# Clients

**Clients** vertegenwoordigen de organisaties of personen die onderzoeken aanvragen. Elke case is gekoppeld aan exact één client.

## Client Velden

| Veld | Omschrijving |
|------|-------------|
| **Name** | Naam van de organisatie of persoon (verplicht) |
| **Contact Person** | Naam van de contactpersoon |
| **Email** | E-mailadres |
| **Phone** | Telefoonnummer |
| **Address** | Straat + nummer + postcode + woonplaats |

## Client Aanmaken

1. Klik **Clients** → **New Client**
2. Vul de gegevens in
3. Klik **Save**

Je wordt doorgestuurd naar de client detailpagina waar je alle gekoppelde cases kunt zien.

## Client Detailpagina

Toont clientgegevens en een lijst van alle bijbehorende cases. Klik op een case om direct te navigeren.

## Archiveren

Clients kunnen worden gearchiveerd in plaats van verwijderd. Gearchiveerde clients worden verborgen in de hoofdlijst maar blijven toegankelijk via de **Show Archived** toggle.

## Adres Functionaliteiten

Elke adreskaart heeft actieknoppen:

- **🔍 Postcode Check** — roept de Nederlandse BAG (PDOK) API aan om straat + woonplaats in te vullen op basis van postcode + huisnummer
- **🚔 Politiebureau** — vindt het dichtstbijzijnde politiebureau voor dat adres via de Politie NL API

## Telefoonnummer Check

Als er een telefoonnummer is opgeslagen, verschijnt een 📞 knop. Deze verrijkt het nummer met:

- Carrier (provider)
- Lijntype (mobile, voip, landline)
- Regio en tijdzone
- WhatsApp aanwezigheid
- Telegram aanwezigheid

## Duplicaat Detectie

Bij het aanmaken van een client wordt automatisch gecontroleerd op soortgelijke bestaande clients om dubbele registraties te voorkomen.
