# Portal release checklist

Deze checklist beschrijft de gevalideerde portalrelease van versie 3.8.0.
Productie-uitrol blijft per release een afzonderlijk, expliciet besluit.

## Afgerond en lokaal gecontroleerd

- Portal- en CMS-scope zijn tenantgebonden; portalgebruikers krijgen geen
  toegang tot de interne CMS-werkomgeving.
- Aanvragen voor eenmalig onderzoek, organisatie-tenant en “Andere vraag”
  hebben ieder een herkenbare afhandeling.
- Portalberichten kunnen door klant en behandelaar worden verzonden; citeren
  werkt aan beide kanten.
- Nieuwe berichten maken een interne/portalnotificatie aan en de klant krijgt
  een e-mailmelding via de ingestelde mailtransportlaag.
- Rapporten worden pas zichtbaar na vrijgave; documenten en facturen blijven
  tenant- en gebruikersgebonden.
- Aanvragen, berichten, profielacties, notificaties en downloads hebben
  rate-limiting, CSRF-bescherming waar van toepassing en auditregistratie.
- Auditregels bevatten actie en context, maar geen volledige berichtinhoud of
  onderzoekswaarden.
- Back-updecryptie, archiefintegriteit en een tijdelijke databasehersteltest
  zijn uitgevoerd.
- De lokale Docker-omgeving start gezond en de health-check geeft HTTP 200.

## Productiecontrole 3.8.0

- Synthetische portaldata is gebruikt voor de laatste portaalcontrole; deze data
  is niet naar GitHub of productie gekopieerd.
- Lokale Mailpit-bezorging, wachtwoordherstel, eenmalige resetlink en 2FA zijn
  gecontroleerd. Echte SMTP-bezorging blijft een aparte productiecontrole.
- Productie-licentie, telemetry, TLS en de publieke health-check zijn
  gecontroleerd; de productie heeft een actieve licentiestatus en recente
  check-in.
- Een versleutelde back-up is vóór de uitrol gemaakt en decryptie,
  archiefintegriteit en tijdelijk databaseherstel zijn gecontroleerd.
- Releaseproces: lokale test → GitHub PR → groene verplichte controles →
  gecontroleerde productie-update.

De formele release naar `3.8.0` vereist nog een groene PR, merge en een
afzonderlijk expliciet besluit voor de productie-uitrol van die versie.

## Bekende lokale testdata

De huidige lokale testdata bevat synthetische portalregistraties en berichten.
Deze gegevens horen niet naar GitHub of productie te worden gekopieerd.
