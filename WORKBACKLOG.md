# WORKBACKLOG — werkvoorraad osint-dashboard (concept)

**Status:** concept — NOOIT comitten/mergen/deployen zonder afzonderlijk akkoord.
**Referentie-waarheid:** `origin/master` (`3a33fd8` = merge PR #164). Lokale `master`
staat op `4ff8000` en is **9 commits achter** → lokale master is géén waarheid meer.
**Gemaakt op:** aparte branch `chore/workbacklog-draft` (alleen ref + dit bestand,
niet gecommit, niet gepusht). Werkboom was schoon vóór dit bestand.
**Waardering per cel:** `[X]` = geverifieerd tegen origin/master in deze run
(command vermeld); `[?]` = niet betrouwbaar geverifieerd (tool-output vermengd) →
niet invullen vóór verse hercheck.

---

## 1. Werkstukinventaris (alleen vóóropenstaande takken + open PR's)

> Selectie: lokaal/remote vóór-op-`origin/master` of open PR. Exact-gelijke
> branches (ahead=0/behind=0) zijn identiek aan master → niet apart werkstuk.

| # | Onderwerp | Lokale branch | Remote branch | Open/gesloten/gemergde PR | Ahead/behind | Laatste commit | Al in master? | Deploy (àltijd alleen met bewijs) | Actie | Bewijskommando / resultaat |
|---|---|---|---|---|---|---|---|---|---|---|
| W1 | Health-latency P1: bounded quick-path | — | `origin/master` (fix staat al in master) | gemergd in master (zie §2) | — | — | ja | **argument: health_refresh is bounded producer + snapshot-lezer; géén herbouw** | **geen herbouw; alleen operationele herverificatie** | `git grep origin/master` → `scripts/health_refresh.py`: `flock`, `LOCK_PATH`, `REFRESH_TIMEOUT_SECONDS=75`, `signal/SIGALRM`; `cms/health_utils.py:42-75`: `check_external_services(quick=True)` skipt kadaster/rdw/hibp; `cms/routes/dashboard.py:151` health-summary leest alleen snapshot |
| W2 | P0-schema automigrate uit boot | — | `origin/master` (fix in master) | gemergd in master | — | — | ja | bewezen in master (`cms/__init__.py:147-171` fail-closed schema-sync; boot migreert nooit) | **gesloten; alleen nazien als draft** | `git grep origin/master` → `cms/__init__.py` "boot voert GEEN migraties uit (P0)"; `update.sh` stap 6/8 = `alembic upgrade head` |
| W3 | „add action column to findings” | lokale `feature/subject-profile-findings-review`? `[?]` | `origin/fix/add-action-column-to-findings` (ahead=4) `[X]` | `[?]` (PR-koppeling niet betrouwbaar geverifieerd) | ahead=4 `[X]` | `[?]` | `[?]` — kan niet hard gemaakt worden in deze run | geen bewijs → niet invullen | **review** (onderzoek of het in master zit) | `git rev-list --count origin/master..origin/fix/add-action-column-to-findings` → 4 |
| W4 | dependabot dependency-PR's | — | 11 `origin/dependabot/*` (ahead=1 elk) `[X]` | 11 open PR's `[X]` | ahead=1/behind varieert `[X]` | `[?]` | nee (nog niet gemerged) | geen | **review per groep, niet blind mergen** (zie §4) | `git for-each-ref` / `gh pr list` |
| W5 | docs: pending independent DR operator | — | `origin/docs/pending-second-operator` | **PR #18 open** `[X]` | `[?]` | `[?]` | nee | n.v.t. (docs) | **beoordelen of claim nog waar is; NIET mergen; sluiten alleen na akkoord + bewijs** | `gh pr view 18` → state=OPEN |
| W6 | overige remote feature-branches (ADR-0002/onderzoeken, feature-flags, health-monitor, subject-profile-workspace, etc.) | enkele lokale `[?]` | diverse `origin/feat/*`/`origin/fix/*` (behind) | onbekend `[?]` | `[?]` — vermengd | `[?]` | `[?]` | geen bewijs | **bewaren; geen verwijderen zonder akkoord; hercheck nodig** | (geen betrouwbare cel) |

> **Let op:** in deze run was meerdere tool-output vermengd (zie `[?]`-cellen).
> Vóór W3/W5/W6 een *actie* definitief maken: eerst elk werkstuk éénmaal,
> geïsoleerd herlezen/herchecken tegen `origin/master` — géén wijzigingen.

---

## 2. Health-latency — vaste conclusie (géén herbouw)

De fix staat **al in `origin/master`** (niet alleen lokaal). Geverifieerd in deze run:

- `scripts/health_refresh.py` is een **bounded producer**: `flock`-slot
  (`LOCK_PATH`), budget via `signal.setitimer`/`SIGALRM`
  (`REFRESH_TIMEOUT_SECONDS = 75`), schrijft een **persistente snapshot**
  (`_store_snapshot` → `cms_setting`/`health_snapshot`).
- `cms/health_utils.py:42-75` — `check_external_services(quick=True)` **skipt
  kadaster/rdw/hibp**; `/health?quick=1` doet geen volledige externe check.
- `cms/routes/dashboard.py:151` `/cms/api/health-summary` leest uitsluitend de
  snapshot (geen full-check in request-worker).

**Volgende stap = ALLEEN operationele herverificatie, géén code:**
1. meet quick-health / health-summary / normale applicatielatency;
2. controleer snapshot-leeftijd en timerstatus;
3. bewijs dat request-workers geen volledige externe check uitvoeren;
4. rapporteer p50/p95/max + gaten; wijzig niets.

---

## 3. Prioriteitsvolgorde

- **P0.** Definitieve werkvoorraad op basis van actuele `origin/master` (dit bestand).
- **P1.** Operationeel bewijs dat de bestaande health-fix stabiel is (§2 — meten).
- **P2.** FeatureFlag-auditability (actorvelden, AuditLog, FORCE-RLS, centraal
  write-pad, tests) — volgens definitief gecorrigeerd plan, nader uit te werken en
  te reviewen. **Niet starten vóór akkoord.**
- **P3.** Tier-/licentiemodel eerst als ADR/beslisdocument; nog géén code.
- **P4.** Dependabot gecontroleerd afhandelen (§4).
- **P5.** Oude branches pas verwijderen nadat bewezen is dat hun commits in
  master zitten of bewust vervallen zijn.

---

## 4. Dependabot-beleid

- Niet alle PR's blind tegelijk mergen.
- Eerst sluiten/vervangen wat door nieuwere lockfile-versies al achterhaald is.
- GitHub Actions-updates apart behandelen.
- Python-updates in kleine compatibele groepen: netwerk/DNS, rendering/documenten,
  overige utilities.
- Per groep: nieuwe branch vanaf actuele master, lockfile regenereren,
  `pip check`, `pip-audit`, volledige SQLite-suite, PostgreSQL/RLS-tests, lint,
  typecheck, CodeQL.
- Geen database- of RLS-wijzigingen combineren met dependency-upgrades.
- Na merge: één gecontroleerde deploy per bewezen compatibele batch.

---

## 5. PR #18 (docs/pending-second-operator)

- Beoordeel of "pending independent DR operator" nog waar is.
- Als onafhankelijke DR-attestatie inmiddels aantoonbaar is afgerond: **PR
  sluiten** of documentatie actualiseren.
- **Niet mergen**; sluiten alleen mét bewijs en na afzonderlijk akkoord.

---

## 6. Leeswijzer / veiligheidsregels

- **Alles in dit bestand is concept.** Niets wordt zonder apart akkoord
  gecommitt, gepusht, gemerged, gesloten, verwijderd of gedeployed.
- Lokale `master` is verouderd (`4ff8000`, 9 achter) → werk uitsluitend tegen
  `origin/master`.
- Bij `[?]`-cellen: eerst één geïsoleerde hercheck; vul niets in op vermengde
  tool-output.
