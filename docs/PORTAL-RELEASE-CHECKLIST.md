# Portal release checklist

Deze checklist is het afrondingspunt voor de lokale portalwerkzaamheden. Een
release naar productie blijft een afzonderlijk, expliciet besluit.

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

## Voor productie-openstelling nog expliciet uitvoeren

- Gebruik uitsluitend synthetische testdata bij de laatste portaalcontrole.
- Lokale Mailpit-bezorging, wachtwoordherstel, eenmalige resetlink en 2FA zijn
  met een synthetisch testaccount gecontroleerd; echte SMTP-bezorging blijft een
  aparte productiecontrole.
- Productie-licentie, telemetry en TLS-bereikbaarheid gecontroleerd; de productie heeft een actieve licentiestatus en recente check-in.
- Controleer de geplande back-up, melding bij falen en herstel van een recente
  back-up inclusief sleutelbeheer.
- Maak een release-commit en pull request; voer productie pas daarna bij met
  expliciet releasebesluit.

## Bekende lokale testdata

De huidige lokale testdata bevat synthetische portalregistraties en berichten.
Deze gegevens horen niet naar GitHub of productie te worden gekopieerd.
