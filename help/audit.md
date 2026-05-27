# Audit Log

Het **Audit Log** systeem registreert alle belangrijke acties in het dashboard. Dit biedt een volledig traceerbaar overzicht van wie wat heeft gedaan en wanneer.

## Wat wordt gelogd?

| Actie | Voorbeeld |
|-------|-----------|
| **Case aanmaken/bewerken** | Nieuwe case, statuswijziging, prioriteitswijziging |
| **Subject aanmaken/bewerken** | Nieuw subject, gegevens wijzigen |
| **Client aanmaken/bewerken** | Nieuwe client, adreswijziging |
| **Finding toevoegen/bewerken** | Nieuwe OSINT-bevinding |
| **Document uploaden** | Bestand toegevoegd aan case |
| **Commentaar** | Opmerking geplaatst of bewerkt |
| **Inloggen** | Gebruiker login/logout |
| **Export** | Data geëxporteerd |
| **SpiderFoot** | Scan gestart of resultaten gekoppeld |
| **Instellingen wijzigen** | API keys of configuratie aangepast |

## Audit Log Bekijken

1. Ga naar **Cases** → open een case
2. Scroll naar de **Audit Log** sectie onderaan de pagina
3. Of gebruik de speciale audit pagina voor een globaal overzicht

Elke entry toont:

- **Timestamp** — datum en tijd
- **User** — welke gebruiker
- **Action** — wat er gebeurde
- **Details** — extra informatie (bijv. "Status changed from Open to In Progress")
- **IP Address** — van waaruit de actie werd uitgevoerd

## Opschonen

Audit logs worden automatisch opgeruimd volgens de retentieperiode die in de configuratie is ingesteld. Oude logs worden periodiek verwijderd om de database grootte te beheersen.
