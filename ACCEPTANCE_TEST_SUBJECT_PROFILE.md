# Acceptatietest Subject Profile — Operationele Pilot

**Datum**: ___________
**Tester**: ___________
**Omgeving**: lokaal / productie
**Feature flag**: `subject_first_investigations`

## Status productie (vóór pilot)

| Setting | Waarde |
|---|---|
| `subject_first_investigations_global` | `1` (open) |
| Default Organization (`3a169c92`) | geen override (OFF) |
| Neonova Nederland (`00d72d7c`) | geen override (OFF) |
| Acme Corp Rotterdam (`1d428db5`) | geen override (OFF) |

---

## Stap 0 — Kill-switch pre-check (vóór activatie)

- [ ] Kill-switch op `"0"` zetten → verifiëren dat alle tenants legacy view krijgen
- [ ] Kill-switch weer op `"1"` zetten → verifiëren dat legacy view blijft (want geen per-tenant override)
- [ ] Kill-switch werkt zoals verwacht: **BEVESTIGD**

---

## Stap 1 — Flag activeren

- [ ] Via admin-UI (`/cms/admin/feature-flags`) toggle `subject_first_investigations` aan voor te testen tenant
- [ ] Verifiëren dat redirect naar `/profile` werkt (niet naar legacy view)
- [ ] Verifiëren dat legacy `/subjects/<id>/edit` redirect naar `/profile`
- [ ] Verifiëren dat standalone `/subjects/create` geblokkeerd wordt (403 of redirect)

---

## Stap 2 — Persoon (type: `person`)

**Testsubject**: ___________

### Aanmaken
- [ ] Via workflow-case een nieuw persoon-subject aanmaken
- [ ] Identiteit: achternaam, voornamen, tussenvoegsels invullen
- [ ] Geslacht, geboortedatum, geboorteplaats invullen
- [ ] Nationaliteit, BSN, ID-nummer invullen (optioneel)
- [ ] Contact: email + telefoon toevoegen
- [ ] Adres: straat + huisnummer + postcode + stad + land toevoegen
- [ ] Opslaan

### Profile — Tabs controleren
- [ ] **Overview**: naam klopt, type-badge toont "person", samenvatting toont juiste aantallen
- [ ] **Identity**: alle ingevulde velden zichtbaar (naam, DOB, BSN, etc.)
- [ ] **Contact**: email + telefoon zichtbaar met provenance chips (source, status)
- [ ] **Address**: adres zichtbaar met provenance
- [ ] **Financial**: leeg (nog niet ingevuld) — correcte empty state
- [ ] **Online**: leeg — correcte empty state
- [ ] **Relations**: leeg — correcte empty state
- [ ] **Investigation**: read-only, "No research actions" message
- [ ] **Facts + Identifiers**: leeg, add-forms aanwezig
- [ ] **Findings**: leeg

### Roundtrip
- [ ] Subject sluiten (naar lijst)
- [ ] Subject heropenen via `/profile`
- [ ] Alle eerder ingevulde velden nog steeds aanwezig
- [ ] **Identity tab**: wijziging doorvoeren (bijv. achternaam aanpassen)
- [ ] Opslaan → heropenen → wijziging zichtbaar
- [ ] **Contact toevoegen**: nieuw emailadres → opslaan → zichtbaar
- [ ] **Contact verwijderen**: verwijderen → opslaan → niet meer zichtbaar
- [ ] **Address bewerken**: wijzig straatnaam → opslaan → heropenen → wijziging zichtbaar

### Provenance
- [ ] Bij elk contact/address: provenance chips tonen (minimaal source + status)
- [ ] Bij nieuw aangemaakt item: `created_by` gekoppeld aan huidige gebruiker

---

## Stap 3 — Voertuig (type: `vehicle`)

**Testsubject**: ___________

### Aanmaken
- [ ] Voertuig aanmaken met kenteken + VIN
- [ ] Merk + type invullen
- [ ] RDW-data: als beschikbaar, tonen in data grid

### Profile — Tabs controleren
- [ ] **Identity**: kenteken, VIN, merk, type zichtbaar
- [ ] **Identity**: RDW-data grid (indien beschikbaar)
- [ ] Overige tabs: juiste empty states

### Roundtrip
- [ ] Heropenen → kenteken + VIN nog steeds zichtbaar
- [ ] Kenteken wijzigen → opslaan → heropenen → wijziging zichtbaar

---

## Stap 4 — Vaartuig (type: `vessel`)

**Testsubject**: ___________

### Aanmaken
- [ ] Vaartuig aanmaken met IMO, MMSI, ENI
- [ ] Nationaliteit (vlagstaat) invullen

### Profile — Tabs controleren
- [ ] **Identity**: IMO, MMSI, ENI, nationaliteit zichtbaar
- [ ] **Identity**: Vessel data grid (indien beschikbaar)
- [ ] Overige tabs: juiste empty states

### Roundtrip
- [ ] Heropenen → IMO/MMSI/ENI nog steeds zichtbaar
- [ ] IMO wijzigen → opslaan → heropenen → wijziging zichtbaar

---

## Stap 5 — Organisatie (type: `organization`)

**Testsubject**: ___________

### Aanmaken
- [ ] Organisatie aanmaken met naam, KVK-nummer, rechtsvorm
- [ ] Contactpersoon + contactgegevens toevoegen (via Client of Contact)

### Profile — Tabs controleren
- [ ] **Identity**: naam, KVK, rechtsvorm zichtbaar
- [ ] **Contact**: contactpersoon + gegevens zichtbaar
- [ ] Overige tabs: juiste empty states

### Roundtrip
- [ ] Heropenen → alle velden nog steeds zichtbaar
- [ ] Rechtsvorm wijzigen → opslaan → heropenen → wijziging zichtbaar

---

## Stap 6 — Online/Account (type: `online`)

**Testsubject**: ___________

### Aanmaken
- [ ] Account aanmaken (via workflow-case)
- [ ] Social account: platform + username + profiel-URL toevoegen

### Profile — Tabs controleren
- [ ] **Online**: social account zichtbaar met platform, username, URL, provenance
- [ ] Overige tabs: juiste empty states

### Roundtrip
- [ ] Heropenen → social account nog steeds zichtbaar
- [ ] Username wijzigen → opslaan → heropenen → wijziging zichtbaar
- [ ] Social account verwijderen → opslaan → niet meer zichtbaar

---

## Stap 7 — Cross-functioneel

### Relations
- [ ] Vanuit subject A een relation toevoegen naar subject B (type: "colleague")
- [ ] Subject A heropenen → outgoing relation zichtbaar
- [ ] Subject B heropenen → incoming relation zichtbaar
- [ ] Relation verwijderen → niet meer zichtbaar op beide subjects

### Facts + Identifiers
- [ ] Fact toevoegen (key: "eye_color", value: "blue", source: "observation")
- [ ] Heropenen → fact zichtbaar in Facts-tab
- [ ] Fact bewerken (value: "green") → opslaan → heropenen → wijziging zichtbaar
- [ ] Identifier toevoegen (type: "email", value: "test@example.com")
- [ ] Heropenen → identifier zichtbaar
- [ ] Identifier verwijderen → niet meer zichtbaar

### Delete blocks
- [ ] Proberen een contact te verwijderen dat gekoppeld is aan een finding → moet falen met melding

### Legacy gating
- [ ] `/subjects/<id>` (legacy view) → redirect naar `/profile`
- [ ] `/subjects/<id>/edit` → redirect naar `/profile`
- [ ] `/subjects/create` (standalone) → geblokkeerd (403 of redirect)
- [ ] `/cms/workflow/case/<id>` → nog steeds toegankelijk (niet gated)

---

## Stap 8 — Kill-switch eindtest

- [ ] Kill-switch op `"0"` zetten via admin
- [ ] `/subjects/<id>/profile` → redirect naar legacy view
- [ ] Legacy `/subjects/<id>` → werkt normaal
- [ ] Legacy `/subjects/<id>/edit` → werkt normaal
- [ ] Kill-switch weer op `"1"` zetten
- [ ] `/subjects/<id>/profile` → werkt weer
- [ ] **Kill-switch test geslaagd**

---

## Stap 9 — Gaps / bugs vastleggen

| # | Beschrijving | Severity | Type | Card nr |
|---|---|---|---|---|
| | | | | |
| | | | | |
| | | | | |

**Severity**: P0 (blokkerend), P1 (ernstig), P2 (storend), P3 (cosmetisch)
**Type**: bug / ux / missing / regression

---

## Samenvatting

| Test | Status |
|---|---|
| Kill-switch pre-check | ☐ |
| Flag activeren | ☐ |
| Persoon roundtrip | ☐ |
| Voertuig roundtrip | ☐ |
| Vaartuig roundtrip | ☐ |
| Organisatie roundtrip | ☐ |
| Online/Account roundtrip | ☐ |
| Relations | ☐ |
| Facts + Identifiers | ☐ |
| Delete blocks | ☐ |
| Legacy gating | ☐ |
| Kill-switch eindtest | ☐ |
| **Totaal** | __ / 12 |

**Aanbeveling**: ☐ Pilot geslaagd — ga door met PR9/10/11 | ☐ Issues gevonden — eerst fixen
