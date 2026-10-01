# Haalbaarheid autonome AI-onderzoeksworkflow

**Status:** technische haalbaarheidsanalyse  
**Datum:** 29 september 2026  
**Scope:** huidige Joost stagingarchitectuur

## Conclusie

Een AI-onderzoeksworkflow is technisch mogelijk, maar bestaat nog niet in de
huidige toepassing. Joost heeft al belangrijke bouwstenen, maar de bestaande
AI-functie is nu vooral een providerlaag en query-analyse; zij bestuurt nog geen
volledige onderzoeksworkflow.

Een volledig autonome workflow die zonder menselijke controle alle acties,
inclusief betaalde sociale kanalen en privacygevoelige bronnen, uitvoert, wordt
niet aanbevolen als eerste implementatie.

## Wat al aanwezig is

- 18 geregistreerde onderzoeksacties met één gedeelde worker-uitvoering.
- Een actie kan direct worden gestart of eerst als voorstel worden opgeslagen.
- Voorstellen kunnen later bewust worden gestart of verwijderd.
- Lopende acties kunnen worden geannuleerd.
- Case-, subject- en investigation-scope bestaat al.
- Tenantcontext en Row-Level Security worden tijdens de uitvoering opnieuw
  gecontroleerd.
- Betaalde kanalen zijn standaard uitgeschakeld per tenant.
- Betaalde acties hebben creditlimieten en kostenlabels.
- Auditrecords worden bij het aanmaken van acties vastgelegd.
- Findings, bron-URL's, screenshots en integriteitsinformatie kunnen aan een
  actie worden gekoppeld.
- OpenRouter/Ollama en drie AI-consumenten bestaan al: samenvatten,
  natuurlijke-taal-analyse en profielverrijking.

## Wat nog ontbreekt

De huidige AI-analyse zet een vraag alleen om naar eenvoudige parameters zoals
type, query en confidence. Er is nog geen:

- AI-planner die een volledig onderzoeksplan opstelt;
- gecontroleerde tool-calling-laag die acties selecteert en uitvoert;
- afhankelijkheids- of volgordebeheer tussen acties;
- centrale budget-, tijd- en maximum-stappenlimiet;
- beleidslaag die per tenant en actiecategorie beslist wat automatisch mag;
- scherm voor planreview, goedkeuring en voortgang;
- expliciete scheiding tussen AI-suggestie, bronresultaat en menselijke finding;
- bescherming tegen prompt injection in externe onderzoeksresultaten;
- autonome-workflow audit trail met prompt, model, plan, acties en beslissingen;
- uitgebreide testset voor mislukte providers, dubbele acties, timeouts en
  gedeeltelijke resultaten.

## Aanbevolen ontwikkelvorm

### Fase A — Plan-only

AI ontvangt een expliciete onderzoeksvraag en maakt alleen een voorstel:

- doel en interpretatie van de vraag;
- voorgestelde subjects en identifiers;
- voorgestelde acties;
- verwachte externe datastromen en kosten;
- onzekerheden en ontbrekende gegevens.

Er wordt niets gestart.

### Fase B — Human approval

De onderzoeker keurt het plan goed of wijzigt het. Joost maakt daarna normale
research-action proposals aan. De bestaande voorstel- en auditlogica kan hiervoor
worden hergebruikt.

### Fase C — Beperkte semi-autonomie

Na goedkeuring mogen alleen veilige acties uit een tenant-allowlist automatisch
worden gestart, bijvoorbeeld open bronnen, lokale analyse en synthetische
tests. Betaalde kanalen, foto-upload naar externe AI en privacygevoelige acties
blijven afzonderlijke bevestiging vragen.

### Fase D — Beperkte autonomie

Pas na bewezen betrouwbaarheid kan een tenant expliciet toestaan dat een
afgebakende allowlist zelfstandig draait. Er blijven harde limieten bestaan:

- maximaal aantal acties;
- maximaal budget en credits;
- maximale looptijd;
- geen nieuwe identifiers zonder bevestiging;
- geen automatische verificatie of officiële finding;
- directe stopmogelijkheid;
- volledige audit trail.

## Risico’s die vooraf moeten worden opgelost

- Persoonsgegevens kunnen via zoekvragen of resultaten naar externe providers
  worden doorgestuurd.
- Externe webinhoud is onbetrouwbare input en mag geen instructies aan de AI
  kunnen geven.
- Een model kan een verkeerde actie of een verkeerd subject voorstellen.
- Betaalde providers kunnen onverwachte kosten veroorzaken.
- Een brede workflow kan te veel resultaten of dubbele findings produceren.
- AI-samenvattingen mogen niet als geverifieerde feiten worden opgeslagen.

## Besluitadvies

De aanbevolen route is **plan-only → menselijke goedkeuring → beperkte
semi-autonomie**. Een volledig autonome uitvoering van alle 18 acties wordt niet
als eerste versie gebouwd. Dat kan later alleen per tenant, per actiecategorie
en met expliciete beleidsinstellingen worden uitgebreid.

Dit document is een ontwerpbesluit en activeert geen AI, OpenRouter of nieuwe
onderzoeksacties.
