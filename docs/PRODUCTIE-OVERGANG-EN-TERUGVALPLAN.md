# Productie-overgang en terugvalplan voor Joost

**Status:** concept voor besluitvorming  
**Opgesteld:** 29 september 2026  
**Productie:** `joost.iveras.com`  
**Nieuwe productie kandidaat:** `staging.joost.iveras.com`

## Doelarchitectuur

De huidige stagingserver wordt na goedkeuring de nieuwe productieserver. De
huidige productieserver blijft voorlopig bestaan en krijgt daarna een andere
rol:

- licentieserver;
- tijdelijke tweede omgeving voor gecontroleerde tests;
- later eventueel opslaglocatie voor versleutelde back-ups.

De applicatie, database en onderzoeksdata van productie blijven logisch
gescheiden van de licentieserver en back-upopslag.

## Voorwaarden vóór de overgang

De overgang mag pas plaatsvinden wanneer:

- staging een actuele, herstelbare back-up heeft;
- de back-up-sleutel buiten de server beschikbaar is;
- login, 2FA en gebruikersrollen zijn gecontroleerd;
- case-aanmaak en de volledige workflow zijn getest;
- SpiderFoot, Tor, API-koppelingen en licentiecontrole werken;
- de definitieve productiegegevens en configuratie zijn vastgesteld;
- DNS en certificaatstrategie zijn voorbereid;
- duidelijk is welke productiegegevens wel en niet worden overgenomen;
- de terugvalbeslissing en verantwoordelijke persoon zijn vastgelegd.

## Aanbevolen overgang in fasen

### Fase 1 — Bevriezen en vastleggen

1. Geen nieuwe functionele wijzigingen meer in staging.
2. Versie, database-head, configuratie en actieve integraties vastleggen.
3. Een laatste volledige back-up maken en proefherstel uitvoeren.
4. De huidige productieomgeving niet wijzigen.

### Fase 2 — Productievoorbereiding

1. Productiedomein en HTTPS-doel bepalen.
2. Productie-specifieke secrets en API-sleutels controleren.
3. Licentietelemetrie laten verwijzen naar de bestaande licentieserver.
4. Productiegebruikers en tenantgegevens expliciet selecteren.
5. Back-upretentie en externe opslag pas na de rolwijziging koppelen.

### Fase 3 — Gecontroleerde omschakeling

1. Korte onderhoudsperiode aankondigen.
2. Laatste back-up van de bronomgeving maken.
3. Productiedata en goedgekeurde configuratie overzetten.
4. DNS naar de nieuwe productieserver laten wijzen.
5. HTTPS, login, 2FA, case-aanmaak en health-check testen.
6. Externe integraties en licentiecontrole testen.

### Fase 4 — Nazorg

1. Logs, foutmeldingen, resourcegebruik en back-ups volgen.
2. Gebruikers een beperkte acceptatietest laten uitvoeren.
3. De oude productieserver nog niet verwijderen of overschrijven.
4. Pas na een afgesproken observatieperiode de oude server herinrichten.

## Terugvalprocedure

Terugval wordt gestart wanneer bijvoorbeeld login, database, licentiecontrole,
onderzoeksworkflow of een kritieke integratie niet betrouwbaar werkt.

1. Nieuwe productie tijdelijk in onderhoudsmodus zetten.
2. DNS terugwijzen naar de oude productieomgeving.
3. Alleen noodzakelijke gegevens uit de laatste gecontroleerde back-up
   herstellen.
4. Gebruikers informeren dat de oude omgeving weer actief is.
5. Oorzaak onderzoeken zonder opnieuw direct om te schakelen.
6. Een nieuwe overgang plannen nadat het probleem aantoonbaar is opgelost.

## Belangrijke grenzen

- De huidige productieomgeving mag niet worden overschreven voordat de nieuwe
  productie aantoonbaar werkt.
- De licentiesleutel en back-upsleutel mogen niet in dezelfde publieke of
  applicatieomgeving worden opgeslagen.
- Testdata mag niet ongemerkt naar productie worden meegenomen.
- Een DNS-wijziging is pas definitief na een succesvolle smoke test.
- Een geslaagde health-check alleen is onvoldoende; de gebruikersworkflow moet
  ook worden getest.

## Beslispunten voor de eigenaar

Voor de daadwerkelijke overgang moet de eigenaar nog expliciet bevestigen:

- welke tenant(s), gebruikers en dossiers meegaan;
- wanneer de onderhoudsperiode mag plaatsvinden;
- welke API-sleutels productie mag gebruiken;
- hoe lang de oude productieomgeving als terugval beschikbaar blijft;
- wanneer de oude server mag worden omgebouwd tot licentie-/back-upserver.

## Huidige conclusie

Staging is technisch geschikt als kandidaat voor productie, maar de overgang
is nog niet uitgevoerd. De huidige productieomgeving blijft voorlopig de
veilige terugval en wordt pas daarna heringericht.
