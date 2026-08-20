# Security Remediation Audit Trail

**Datum**: 2026-08-19
**Operator**: Gast (gast@MacBook-Air-van-Ivan-2, IP 46.29.25.18)
**VPS**: joost.iveras.com (136.144.209.108), TransIP
**Database**: osint_db (PostgreSQL)
**Tenant**: 3a169c92-04a2-48f9-be1b-1fcf930c0f0f

---

## Samenvatting

Volledige security-/stabiliteitsfase afgerond in 4 fases:
1. Smoke tests — 29/29 passed
2. SQL-verificatie — 0 NULLs, 0 orphans
3. Logmonitoring — 0 errors
4. Encryptie-herstel — 211 velden opnieuw versleuteld, 6 onherstelbaar (testdata) verwijderd

---

## Commits op master

| SHA | Beschrijving | Datum |
|---|---|---|
| `ffa2dd6` | Merge PR #52: fix auth-flow RLS tenant context | 2026-08-19 |
| `03ca825` | fix(geo): capture Flask app before spawning background thread | 2026-08-19 |
| `3ac6b03` | fix(auth): set tenant context before RLS-protected writes | 2026-08-19 |
| `5725bb9` | Merge PR #51: serialize Alembic upgrade across gunicorn workers | 2026-08-18 |
| `a49223b` | fix(boot): serialize Alembic upgrade across gunicorn workers | 2026-08-18 |
| `16f9332` | Merge PR #50: P1 subject integrity (6 commits) | 2026-08-18 |

---

## Migraties

| Revisie | Beschrijving | Status op productie |
|---|---|---|
| `d2e3f4a5b6c7` | Re-enable FORCE RLS + WITH CHECK op 24 tabellen | Actief |
| `b1f2e3d4c5a6` | Eerdere FORCE RLS (bron 500-incident) | Overschreven |

Alembic head op productie: `d2e3f4a5b6c7`

---

## Fase 1 — Smoke Tests (lokaal)

**Resultaat**: 29/29 passed
**Datum/tijd**: 2026-08-19
**Gedraaid met**: `pytest -n 0` (serial)

Testgevallen:
- Login (normaal)
- 2FA setup + verify
- Password reset + change
- User CRUD
- Routes smoke (alle GET-endpoints)

Geen ThreadExceptionWarnings meer na PR #52 fix.

---

## Fase 2 — SQL Verificatie (productie)

**Resultaat**: Alles schoon
**Uitgevoerd**: 2026-08-19, via `sudo -u postgres psql osint_db`

| Check | Resultaat |
|---|---|
| audit_logs WHERE tenant_id IS NULL | 0 |
| login_logs WHERE tenant_id IS NULL | 0 |
| audit_logs orphan tenants (LEFT JOIN) | 0 |
| login_logs orphan tenants (LEFT JOIN) | 0 |
| Verdeling audit_logs | 1411 records, allen tenant 3a169c92 |
| Verdeling login_logs | 1 record, tenant 3a169c92 |
| Recente login (24h) | 1 success, 127.0.0.1, juiste tenant |

---

## Fase 3 — Logmonitoring

**Resultaat**: 0 errors
**Uitgevoerd**: 2026-08-19

Gecheckt:
- `journalctl -u osint-dashboard` — alleen Alembic INFO-regels
- `app.log` — 0 errors/warnings
- Health endpoint: `status: ok` (database, migrations, alle externe services)

---

## Fase 4 — Encryptie Herstel

**Resultaat**: 211 velden hersteld, 6 verwijderd
**Uitgevoerd**: 2026-08-19 08:13 UTC

### Herstel
- Script: `scripts/repair_encrypted_subject_fields.py --apply`
- Backup: `/opt/osint-dashboard/backups/manual_20260819.dump` (pg_dump, 486KB)
- Manifest: `repair_manifests/plaintext_repair_20260819_081359.json`
- Aangetroffen: 211 plaintext velden, 12 reeds ciphertext
- Na herstel: 223 ciphertext, 0 plaintext, 0 unrecognized

### Verdeling herstelde velden

| Model | Aantal rows | Voorbeelden velden |
|---|---|---|
| Subject | 16 | date_of_birth, phone, email, license_plate, imo_number, mmsi |
| Client | 5 | contact_person, address_street, bank_account |
| Address | 20 | street, number, zipcode, town, country |
| Contact | 16 | value |
| **Totaal** | **57 rows** | **211 fields** |

### Unrecognized velden (verwijderd)
6 contact.value velden — Fernet-marker (`gAAAA`) aanwezig maar decryptie faalde (sleutel-mismatch, waarschijnlijk oude key). Alle 6 waren testdata. Handmatig op NULL gezet via `bypass_rls=True`.

IDs:
- `3b80d9f9-355a-4288-9a2b-1fe6067e730c`
- `b6749265-6cd7-4e91-9b6f-68a11c20c8c4`
- `7b63022a-40ab-4a95-b635-fb7a98f6e5d9`
- `5ffcfba8-d1c4-49e1-99b9-e6cd9b892fed`
- `1a41b826-8d4e-4995-bacf-42eeacf5b7fc`
- `4743ed38-1695-40fc-b197-c498b47ee5c9`

### Verificatie na herstel
Dry-run na afloop: `fields encrypted: 0`, `unrecognized: 0` — volledig schoon.

---

## Artefacten

| Item | Locatie |
|---|---|
| Database backup | `/opt/osint-dashboard/backups/manual_20260819.dump` |
| Repair manifest | `/opt/osint-dashboard/repair_manifests/plaintext_repair_20260819_081359.json` |
| Lokaal archief | `audit_archive_20260819.tar.gz` (bevat backup + manifest) |

---

## Openstaand

- [ ] UI-test: één hersteld subject openen, bewerken, opslaan, heropenen (handmatig)
- [ ] Eventuele PR voor `tenant_id=tenant_id` in LoginLog constructor (optioneel)
