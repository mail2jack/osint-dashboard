# Workflow-native verdiept brononderzoek

Deze functie start uitsluitend passieve SpiderFoot-scans vanuit een open
onderzoek. De gewone workflowgebruiker kiest geen modules en krijgt geen
SpiderFoot-beheerscherm te zien.

## Veilig standaardgedrag

- Feature flag `workflow_spiderfoot` staat standaard uit.
- De bestaande tenant-entitlement `spiderfoot` blijft daarnaast vereist.
- Een webrequest plaatst alleen een duurzame wachtrijrecord; het start nooit
  zelf een externe scan.
- De worker verwerkt hooguit één start- of statusstap per cyclus en gebruikt
  uitsluitend `use_case=passive`.
- Resultaten worden begrensd tot 250 opgeschoonde voorstellen. Pas na een
  expliciete selectie worden ze als kandidaat-bevindingen toegevoegd.

## Uitrol

1. Volg de normale backup/DR- en `scripts/update.sh`-procedure. De Alembic
   migratie koppelt alleen bestaande scanrecords optioneel aan workflowacties.
2. Controleer na deploy dat de applicatie gezond is; activeer de feature flag
   nog niet.
3. Installeer en activeer de aparte worker bewust:

   ```bash
   sudo /opt/osint-dashboard/scripts/install_source_research_worker.sh --enable
   systemctl is-active osint-source-research-worker
   ```

4. Activeer `workflow_spiderfoot` per pilottenant via de super-admin-UI.
   De bestaande `spiderfoot`-entitlement moet ook actief zijn.
5. Maak één synthetische, passieve testopdracht. Controleer: queued → running
   → completed, resultaten beoordelen en expliciet één voorstel importeren.

## Stoppen en rollback

Zet eerst de tenant-override `workflow_spiderfoot` uit. Nieuwe scans starten
dan niet meer; de worker markeert een nog niet extern gestarte opdracht als
uitgeschakeld. Stop daarna, indien nodig, alleen de worker:

```bash
sudo systemctl disable --now osint-source-research-worker
```

Gebruik nooit `alembic downgrade` of een handmatige databasewijziging als
rollback voor deze functie.
