# SpiderFoot

SpiderFoot is een open-source OSINT automation tool geïntegreerd in het dashboard voor het uitvoeren van verkenningsscans. Het verzamelt informatie over IP-adressen, domeinen, e-mailadressen, gebruikersnamen en meer uit honderden open bronnen.

## Configuratie

Voordat SpiderFoot gebruikt kan worden, moet de verbinding worden ingesteld in **Settings**:

- **SpiderFoot URL** — bijv. `http://127.0.0.1:5001`
- **Gebruikersnaam** — zoals ingesteld in `~/.spiderfoot/passwd`
- **Wachtwoord** — het bijbehorende wachtwoord

SpiderFoot gebruikt HTTP **Digest** authenticatie. De health status wordt elke 60 seconden gecontroleerd; bij een fout verschijnt er een rode banner bovenaan de pagina.

## Scan Aanmaken

1. Ga naar **SpiderFoot** → **New Scan**
2. Vul het **Target** in (domein, IP-adres, e-mailadres, gebruikersnaam etc.)
3. Selecteer een **Scan Type**:
   - **All** — alle modules gebruiken (kan lang duren)
   - **Footprint** — oppervlakkige verkenning
   - **Investigate** — diepgaand onderzoek
   - **Passive** — alleen passieve bronnen (geen directe connecties)
   - **Custom** — kies zelf modules
4. Klik **Start Scan**

## Scan Overzicht

De scan lijst toont:

- **ID** — scan identificatienummer
- **Target** — het onderzochte object
- **Status** — RUNNING / FINISHED / ERROR / FAILED / ABORTED
- **Results** — aantal gevonden resultaten
- **Created** — aanmaakdatum

Klik op een scan om de resultaten te bekijken.

## Scan Resultaten

Resultaten worden gegroepeerd per **type** en **bronmodule**:

| Type | Voorbeelden |
|------|-------------|
| `SOCIAL_MEDIA` | Facebook, LinkedIn, Twitter profielen |
| `IP_ADDRESS` | IPv4/IPv6 adressen |
| `DOMAIN_NAME` | Gekoppelde domeinen |
| `EMAIL_ADDRESS` | Gevonden e-mailadressen |
| `PHONE_NUMBER` | Telefoonnummers |
| `GEO_INFO` | Locatiegegevens |
| `NETBLOCK` | IP ranges |
| `WEB_CONTENT` | Webpagina inhoud |
| `LEAKED_DATA` | Gelekte credentials |

### SFURL Tags

Resultaten kunnen `<SFURL>` tags bevatten in de data. Deze worden automatisch gedetecteerd en klikbaar gemaakt in de weergave.

## Resultaten Koppelen

Vanuit de scan resultaten kun je:

- 🏷️ **Add to Case** — voeg een resultaat toe als **Finding** aan een bestaande case
- 👤 **Create Subject** — maak een nieuw subject aan van ontdekte data

## Scan Types in Detail

### All
Doorloopt alle beschikbare modules. Meest compleet, maar duurt het langst.

### Footprint
Gebruikt ~30 basis modules voor een algemene verkenning van het target.

### Investigate
~50 modules gericht op diepgaand onderzoek van de gevonden data.

### Passive
Alleen modules die geen directe verbinding maken met het target. Gebruikt open bronnen zoals DNS, WHOIS, zoekmachines.

## Probleemoplossing

**Scan blijft hangen op RUNNING**: SpiderFoot service is mogelijk vastgelopen. Herstart de service: `sudo systemctl restart spiderfoot`

**Rode banner "SpiderFoot is unreachable"**:
- Controleer of SpiderFoot draait: `curl http://127.0.0.1:5001`
- Controleer de URL in Settings
- Controleer de inloggegevens in Settings
- Controleer of de Digest auth werkt

**Geen resultaten**: Sommige scans hebben tijd nodig. Grote scans kunnen uren duren.
