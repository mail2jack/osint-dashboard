# OpenRouter-privacybeleid voor Joost

**Status:** operationeel beleid voor staging; productie-instelling vereist nog
een afzonderlijke expliciete goedkeuring  
**Bijgewerkt:** 3 oktober 2026

## Doel

Joost gebruikt OpenRouter uitsluitend na een expliciete gebruikersactie, zoals
het maken van een AI-onderzoeksrapport of het uitvoeren van een andere zichtbare
AI-functie. Het aanmaken van een zaak, subject, finding of gewone
onderzoeksactie start geen AI-verzoek.

## Welke gegevens kunnen worden doorgestuurd?

Dat hangt af van de gekozen AI-functie. Een verzoek kan onder meer bevatten:

- de door de onderzoeker gestelde onderzoeksvraag;
- geselecteerde, al beschikbare findings en bronverwijzingen;
- beperkte subjectcontext die nodig is om de vraag te beantwoorden;
- instructies voor de gewenste rapportvorm.

De applicatie moet geen volledige database, niet-geselecteerde dossiers,
wachtwoorden, API-sleutels, sessies, backup-sleutels of interne credentials naar
OpenRouter sturen. Gevoelige gegevens worden alleen opgenomen wanneer zij
noodzakelijk zijn voor de expliciet gestarte actie en binnen de toegestane
onderzoekscontext vallen.

## Training en bewaartermijn

Er is geen algemene garantie dat iedere OpenRouter-aanvraag buiten training of
retentie blijft. OpenRouter geeft aan dat de uiteindelijke modelprovider eigen
regels kan hebben voor bewaren, evalueren, verbeteren en trainen met inputs en
outputs. De gekozen provider en het gekozen model moeten daarom vóór gebruik
worden gecontroleerd.

Voor Joost geldt als minimale operationele eis:

1. geen willekeurige automatische provider-routing voor persoonsgegevens;
2. alleen een expliciet toegestane model/provider-combinatie;
3. Zero Data Retention (ZDR) afdwingen waar beschikbaar;
4. data collection/training uitschakelen waar de provider dit ondersteunt;
5. geen model gebruiken wanneer de providerretentie of trainingspraktijk niet
   aantoonbaar is;
6. kosten- en rate-limits instellen op de OpenRouter-account en API-key.

OpenRouter ondersteunt volgens de actuele providerinformatie privacyfilters,
provider-/modellijsten en ZDR-guardrails. EU-in-region routing is volgens de
OpenRouter-documentatie een enterprisefunctie; “via OpenRouter” betekent dus
niet automatisch dat verwerking uitsluitend binnen de EU plaatsvindt.

## Huidige Joost-beperking

De huidige integratie leest een API-key, modelnaam en base URL uit de
configuratie en stuurt een chat-completionsverzoek. De standaardmodelwaarde is
`openrouter/auto`. Dat is geschikt voor testen, maar niet voldoende als
productiebeleid voor gevoelige persoonsgegevens, omdat automatische routing de
uiteindelijke providerkeuze kan overlaten aan OpenRouter.

Voor productie moet daarom eerst een vast model/providerbeleid worden gekozen en
gedocumenteerd. Tot die tijd blijft OpenRouter een gecontroleerde staging-
voorziening voor expliciet gestarte rapport- en AI-acties; het is geen vrije
autonome onderzoeksagent.

## Rollen en verantwoordelijkheid

- De onderzoeker bepaalt expliciet wanneer AI wordt aangeroepen.
- Joost toont de vraag, resultaten en onzekerheden gescheiden van menselijke
  validatie.
- AI-output is een voorstel en geen vastgesteld onderzoeksfeit.
- Findings, bronnen en menselijke validaties blijven leidend voor het rapport.
- Beheerders controleren model, provider, budget en privacy-instellingen.
- Een wijziging naar een ander model of provider vereist opnieuw een
  privacy-/retentiecontrole.

## Go/no-go voor productie

Productiegebruik is pas toegestaan nadat de volgende punten zijn vastgelegd:

- gekozen model en concrete provider;
- providerretentie en trainingsstatus;
- ZDR/data-collection-instelling;
- eventuele EU-regio-eis en bewijs daarvan;
- toegestane categorieën persoonsgegevens;
- budget, rate limit en incidentprocedure;
- bewaartermijn van prompts, outputs en auditmetadata in Joost zelf;
- eigenaar die de providerinstellingen periodiek controleert.

## Bronnen

- [OpenRouter Privacy Policy](https://openrouter.ai/privacy)
- [OpenRouter Providers](https://openrouter.ai/providers)
- [OpenRouter Guardrails](https://openrouter.ai/docs/guides/features/guardrails/overview)
- [OpenRouter Sovereign AI and in-region routing](https://openrouter.ai/docs/guides/get-started/sovereign-ai)

Deze bronnen kunnen wijzigen. De datum van de laatste controle staat bovenaan
dit document; vóór productie moet opnieuw worden gecontroleerd.
