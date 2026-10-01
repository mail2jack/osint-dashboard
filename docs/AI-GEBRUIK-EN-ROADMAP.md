# AI-gebruik en toekomstige AI-onderzoeksassistent

**Status:** vastgelegd op 29 september 2026  
**Scope:** huidige stagingimplementatie van Joost

## Huidige situatie

AI wordt niet automatisch gestart bij het aanmaken van een case, client,
subject, finding of gewone researchactie. Joost voert ook niet op de
achtergrond zelfstandig een persoonsonderzoek uit.

De zichtbare gebruikersactie waarbij AI nu daadwerkelijk wordt gebruikt is
de vertaalfunctie:

1. Een beheerder markeert vertaalteksten voor correctie.
2. De beheerder klikt op **Auto-fix marked**.
3. Joost stuurt de gemarkeerde tekst naar de geconfigureerde AI-provider.
4. De voorgestelde vertaling wordt opgeslagen als wijziging die nog moet
   worden nagekeken.

Deze actie wijzigt vertaalbestanden en moet daarom bewust door een beheerder
worden gestart en gecontroleerd.

## Voorbereide backendmogelijkheden

De backend bevat daarnaast voorbereidende functies voor:

- het samenvatten van onderzoeksresultaten;
- het analyseren van een vraag in natuurlijke taal;
- het bepalen van passende onderzoekstools;
- het verrijken van gevonden profielinformatie.

Deze functies zijn momenteel geen volwaardige, zichtbare workflow waarmee een
gebruiker een algemene opdracht invoert en Joost vervolgens zelfstandig een
volledig onderzoek uitvoert.

De bestaande AI-endpoints zijn bovendien beveiligd met feature flags,
licentiecontrole, authenticatie en rate limiting.

## Providerkeuze

- OpenRouter is technisch de primaire AI-provider.
- Ollama is bedoeld als lokale terugvaloptie.
- Zonder geconfigureerde provider zijn de AI-functies niet beschikbaar.
- Een providerstatuscontrole is geen inhoudelijke AI-opdracht; deze controleert
  alleen of een provider beschikbaar is.

## Wat Joost nu niet doet

- Niet automatisch AI aanspreken bij ieder nieuw dossier.
- Niet zelfstandig sociale-mediaonderzoek starten.
- Niet zonder gebruikersactie onderzoekstools combineren.
- Niet zelfstandig conclusies aan een dossier toevoegen.
- Niet automatisch gevoelige casegegevens naar OpenRouter sturen.

## Mogelijke toekomstige ontwikkeling

Een toekomstige AI-onderzoeksassistent zou bijvoorbeeld kunnen:

1. een expliciete gebruikersvraag ontvangen;
2. de vraag omzetten in een voorgesteld onderzoeksplan;
3. de gebruiker laten controleren en goedkeuren welke tools worden gebruikt;
4. goedgekeurde acties uitvoeren;
5. resultaten, bronnen en onzekerheden gescheiden tonen;
6. de gebruiker laten beslissen wat als finding wordt opgeslagen.

Voor deze ontwikkeling zijn eerst nodig:

- duidelijke grenzen voor autonome acties;
- gebruikersbevestiging vóór externe zoekopdrachten;
- beleid voor persoonsgegevens en providerretentie;
- logging en provenance van AI-invoer en -uitvoer;
- kosten- en rate-limitbeleid;
- bescherming tegen prompt injection en onbetrouwbare broninformatie;
- een expliciete scheiding tussen AI-suggestie en menselijke vaststelling.

## Besluit

De huidige AI-integratie blijft voorlopig optioneel en beperkt tot expliciete
acties. De toekomstige AI-onderzoeksassistent is een afzonderlijke roadmap-
ontwikkeling en wordt niet geactiveerd zonder een apart ontwerp, privacybeleid
en expliciete productbeslissing.
