# Testplan — Tenant- en API-key-isolatie

**Status:** concept, nog niet uitgevoerd  
**Scope:** lokale tests en PostgreSQL-integratietests  
**Productie:** niet gebruiken voor deze test

## Doel

Bewijzen dat een gebruiker of API-key uit tenant A nooit gegevens van tenant B
kan lezen, wijzigen of laten opnemen in zoek-, usage-, audit- of background-
resultaten.

## Testopstelling

- Twee synthetische tenants: A en B.
- Minimaal één gewone gebruiker per tenant.
- Minimaal één viewer, investigator en admin waar de route dit relevant maakt.
- Eén actieve API-key per tenant.
- Eén ingetrokken/inactieve API-key.
- PostgreSQL met een niet-superuserrol waarop FORCE RLS actief is.
- Geen productiegegevens, echte personen, echte API-keys of echte externe
  onderzoeksresultaten.

## Te testen toegangsvormen

1. Sessie van gebruiker A zonder API-key.
2. Sessie van gebruiker B zonder API-key.
3. API-key van gebruiker A zonder sessie.
4. API-key van gebruiker B zonder sessie.
5. Ongeldige, verlopen of inactieve API-key.
6. Super-admin zonder tenant switch.
7. Super-admin met tenant switch naar A en daarna naar B.

## Routecategorieën

### Read-only zoek- en lookup-routes

Controleer dat resultaten, caches en usage-records uitsluitend bij de juiste
tenant en gebruiker terechtkomen:

- full-text search;
- phone lookup en stored phone lookup;
- email, username, IP en domain lookups;
- KVK, RDW, Interpol, Kadaster en politiebureau lookups;
- vessel lookup;
- AI-routes die case- of subjectdata verwerken.

### Muterende routes

Hoewel deze routes niet CSRF-exempt horen te zijn, moeten zij ook expliciet
worden getest op tenantisolatie:

- subject bijwerken vanuit een lookup;
- finding maken vanuit een lookup;
- comments, screenshots en documents;
- research actions en investigations;
- rapportselectie en exports.

### Achtergrondtaken

Controleer dat een taak gestart door tenant A:

- alleen tenant-A-data leest;
- alleen tenant-A-resultaten schrijft;
- niet via Redis/RQ of thread-fallback context van een andere tenant erft;
- na voltooiing geen cross-tenant status of resultaat toont.

## Verwachte uitkomsten

- Een correcte tenant krijgt uitsluitend eigen records terug.
- Een andere tenant krijgt 403/404 of een lege, aantoonbaar correcte response
  afhankelijk van de routecontracten; nooit gegevens van de andere tenant.
- Een API-key kan niet worden gebruikt om de tenant-ID van het verzoek zelf te
  bepalen of te overschrijven.
- Een inactieve of ongeldige key geeft 401.
- Een key van tenant A kan geen sessie of gebruiker van tenant B activeren.
- FORCE RLS verbergt records ook wanneer een applicatiefilter ontbreekt.
- Cross-tenant foreign-keycombinaties worden door databaseconstraints geweigerd.
- Auditlogs, usage records en background-taskrecords behouden de juiste tenant.

## Bestaande dekking bij aanvang

De repository bevat al sterke dekking voor:

- PostgreSQL FORCE RLS en tenantcontext;
- cross-tenant case-, subject-, finding- en investigationtoegang;
- workflow/background-context en herassertie van tenantcontext;
- viewer/investigator-mutatierestricties;
- CSRF-allowlist en de vijf bekende state-mutating routes;
- finding capture, screenshots en evidence-isolatie.

De volgende dekking is nog niet als één samenhangende suite aangetroffen:

- geldige API-key van tenant A tegenover data van tenant B;
- inactieve of ongeldige API-key op alle lookupcategorieën;
- API-key-authenticatie gecombineerd met tenant switching;
- de 28 CSRF-exempt lookup/AI/search-routes in een twee-tenant matrix;
- opslag van lookup-cache, usage records en auditregels onder die matrix;
- expliciete API-keytests tegen de Redis- en thread-fallback voor backgroundtaken.

Dit is een dekkingsoverzicht op basis van broninspectie. De bestaande tests zijn
voor deze audit niet uitgevoerd.

## Eerste uitgevoerde contracttest

Toegevoegd in `tests/test_api_key_auth_contract.py`:

- actieve API-key wordt geaccepteerd — **PASS**;
- onbekende API-key wordt geweigerd — **PASS**;
- API-key van een gedeactiveerde gebruiker wordt geweigerd — **PASS**.

De request-loader weigert nu gedeactiveerde gebruikers voordat de key als
gebruikt wordt geregistreerd. Ook wordt gecontroleerd dat de tenant van de key
gelijk is aan de tenant van de gebruiker. De generatie-route koppelt nieuwe keys
alleen aan actieve gebruikers binnen de huidige tenant.

## Bewijs dat moet worden vastgelegd

Per testgroep:

- route en HTTP-methode;
- authenticatievorm;
- tenant van de actor;
- verwachte statuscode;
- werkelijk resultaat, zonder persoonsgegevens;
- relevante databasecontrole met synthetische IDs;
- RLS-context (`app.tenant_id` en `app.bypass_rls` waar relevant);
- eventuele query-count- of background-taskobservatie.

## Uitvoeringsvolgorde

1. Bestaande testfixtures en bestaande RLS-tests inventariseren.
2. Gaten in de testdekking vastleggen.
3. Eerst lokale SQLite-contracttests uitvoeren waar dat veilig kan.
4. Daarna PostgreSQL-integratietests uitvoeren met twee tenants.
5. Alleen bij een reproduceerbare fout code aanpassen.
6. Na wijzigingen de relevante tests opnieuw uitvoeren en de resultaten
   documenteren.

## Grenzen

- Dit plan autoriseert geen productiegebruik.
- Dit plan autoriseert geen echte externe OSINT-calls.
- Dit plan autoriseert geen databasewijzigingen buiten tijdelijke testdata.
- Testdata moet synthetisch zijn en na de test automatisch worden verwijderd.
- Een test is geen bewijs van live productieconfiguratie zolang de productie-
  omgeving niet afzonderlijk en expliciet is gecontroleerd.
