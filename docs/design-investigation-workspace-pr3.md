# PR3 — Investigation Workspace (herzien v3)

## 0. Scope
Uitbreiding van het investigation detail (`/case/<cid>/investigations/<iid>`) met subjects, research actions, findings en activity-timeline. Read-only (geen mutatie-UI behalve PR2). Flag-gated, default OFF. Geen migratie/schema-wijzigingen.

### Leveringsvoorwaarden (Volledig groen voor merge)
- Drie logische commits:
  1. read-model/querylaag;
  2. template, read-only partial en i18n;
  3. tests.
- Draft-PR, volledige SQLite-suite, echte PostgreSQL/RLS-tests (niet alleen opt-in), ruff, mypy, remote CI. **Niet mergen of deployen zonder onafhankelijke review.**

---

## 1. Read-model + querylaag — `cms/services/investigation_workspace.py` (NIEUW)

Zorgt voor alle queries, dedup, groepering, labels, tellingen, diff-computatie en sortering. Template ontvangt alleen platte dicts/DTO's — geen ORM-objecten, geen businesslogica.

### Functies

| Functie | Input | Output |
|---|---|---|
| `load_inv_actions(...)` | `tenant_id`, `case_id`, `inv_id` | `list[ActionDTO]` |
| `load_inv_subjects(...)` | tenant_id, action_subject_ids | `list[SubjectDTO]` |
| `load_inv_findings(...)` | tenant_id, case_id, action_ids | `list[FindingDTO]` |
| `load_inv_timeline(...)` | tenant_id, case_id, inv_id, action_ids, finding_ids, cap=50 | `TimelineResult` |
| `build_inv_workspace(inv, case)` | ORM Investigation + WorkflowCase | `WorkspaceDTO` |

### `WorkspaceDTO` (dataclasses, `frozen=True, slots=True`)

```python
@dataclasses.dataclass(frozen=True, slots=True)
class ActionDTO:
    id: str
    action_type: str
    label: str
    icon: str | None
    status: str
    data_value: str | None
    subject_id: str | None
    target_kind: str | None
    created_at: datetime | None
    completed_at: datetime | None
    archived: bool
    finding_count: int          # unieke findings gekoppeld aan deze action

@dataclasses.dataclass(frozen=True, slots=True)
class SubjectDTO:
    id: str
    name: str | None
    subject_type: str | None
    display_name: str
    decrypted: dict             # alle plain-text velden, geen ciphertext

@dataclasses.dataclass(frozen=True, slots=True)
class FindingDTO:
    id: str
    title: str
    detail: str | None
    source_url: str | None          # rauwe waarde (wordt altijd ge-escaped)
    source_url_is_linkable: bool    # alleen True bij http/https-schema (zie §2.5)
    source_type: str | None
    status: str | None
    verified: bool
    created_at: datetime | None
    screenshots: list[ScreenshotDTO]
    action_ids: list[str]       # GEEN alle gekoppelde actions: alleen de
                                # action-IDs van DIT onderzoek (scoped).
    action_labels: list[str]    # labels van die scoped actions
                                # Een finding die óók aan een zaakbrede action
                                # hangt, krijgt die action NIET in deze lijst.

@dataclasses.dataclass(frozen=True, slots=True)
class TimelineEventDTO:
    id: str
    timestamp: datetime | None
    action_label: str           # "create" → "Created", "link" → "Linked", etc.
    entity_type: str
    entity_id: str
    description: str | None
    user_name: str | None       # uit één user_id → naam-mapping, geen N+1
    diff: dict | None           # {"status": ("open","archived")} of None

@dataclasses.dataclass(frozen=True, slots=True)
class TimelineResult:
    events: list[TimelineEventDTO]   # ≤ cap (50), gesorteerd (timestamp DESC, id DESC)
    total: int                       # aantal unieke events vóór de cap, op basis van
                                     # exacte channel-A-count + aanvullende B-events;
                                     # alleen exact wanneer total_exact True is.
    total_exact: bool                # False zodra b_window_full: dan kunnen oudere
                                     # in-scope link/unlink-events buiten het
                                     # ontdekkingswindow liggen en is total een
                                     # ondergrens.
    truncated: bool                  # True als total > len(events) of b_window_full:
                                     # er bestaan (of kunnen bestaan) oudere events.

@dataclasses.dataclass(frozen=True, slots=True)
class WorkspaceDTO:
    actions: list[ActionDTO]
    subjects: list[SubjectDTO]
    findings: list[FindingDTO]
    finding_actions_map: dict[str, list[str]]  # scoped finding_id → scoped action_ids
    timeline: TimelineResult
    counts: dict                # {"actions": N, "subjects": N, "findings": N}
```

---

## 2. Queries — expliciete tenant/case-isolatie, begrensd, geen N+1

Alle queries bevatten `tenant_id` + `case_id` expliciet. **Volgorde van werken in `build_inv_workspace`:**

1. **Alle ORM-queries uitvoeren** (actions, junction, findings, audits + eager `user`, subjects-ORM).
2. **Audit-DTO's en de user-id→naam-mapping bouwen** (geen decryptie betrokken). De investigation-creator-naam wordt in dezelfde mapping meegenomen (`db.session.get(User, inv.created_by)`), zodat de detailroute geen extra losse userquery nodig heeft.
3. **Daarna pas subject-decryptie + DTO-serialisatie**, binnen één `no_autoflush`-blok. Na de decryptie vindt geen enkele lazy-load of extra query meer plaats.

### 2.1 Actions

```python
inv_actions = (
    WorkflowResearchAction.query
    .filter(
        WorkflowResearchAction.tenant_id == tenant_id,
        WorkflowResearchAction.case_id == case_id,
        WorkflowResearchAction.investigation_id == inv_id,
    )
    .order_by(WorkflowResearchAction.created_at.asc(), WorkflowResearchAction.id.asc())
    .all()
)
```

### 2.2 Findings + junction

```python
action_ids = [a.id for a in inv_actions]
af_rows = (
    WorkflowActionFinding.query
    .filter(WorkflowActionFinding.action_id.in_(action_ids))
    .all()
) if action_ids else []
finding_id_set = sorted({af.finding_id for af in af_rows})
inv_findings_orm: list[Finding] = []
if finding_id_set:
    inv_findings_orm = (
        WorkflowFinding.query
        .filter(
            WorkflowFinding.tenant_id == tenant_id,
            WorkflowFinding.case_id == case_id,
            WorkflowFinding.is_deleted == False,
            WorkflowFinding.archived_at.is_(None),
            WorkflowFinding.id.in_(finding_id_set),
        )
        .options(joinedload(WorkflowFinding.finding_screenshots))
        .all()
    )
```

`FindingDTO.action_ids` wordt **alleen** gevuld met `[a.id for a in inv_actions]` die via `WorkflowActionFinding` aan de finding hangen. Een finding die óók aan een zaakbrede action hangt, toont die zaakbrede action hier niet (bevestigd en gecoverd door test `test_finding_also_linked_to_case_wide_action`).

### 2.3 Subjects

```python
subject_ids = sorted({a.subject_id for a in inv_actions if a.subject_id})
inv_subjects_orm: list[Subject] = []
if subject_ids:
    inv_subjects_orm = (
        Subject.query
        .filter(
            Subject.tenant_id == tenant_id,
            Subject.is_deleted == False,
            Subject.id.in_(subject_ids),
        )
        .all()
    )
    # decrypt + serialize — LAATSTE stap, na alle queries en audit-DTO's,
    # binnen één no_autoflush-blok. Geen lazy-loads / queries hierna.
    with db.session.no_autoflush:
        for s in inv_subjects_orm:
            s.decrypt_identifiers()
        subject_dtos = [
            SubjectDTO(
                id=s.id,
                name=s.name,
                subject_type=s.subject_type,
                display_name=(s.compute_name() if callable(getattr(s, "compute_name", None)) else (s.name or "")),
                decrypted=s.to_dict(decrypted=True),   # plain fields only
            )
            for s in inv_subjects_orm
        ]
```

### 2.4 Audit timeline — gekoppelde predicaten, begrensd, eager users

**Channel A — events van actions/findings in scope** (gekoppelde predicates, géén kruisproduct):
```python
from sqlalchemy import or_, and_

filter_a = (
    AuditLog.tenant_id == tenant_id,
    AuditLog.case_id == case_id,
    or_(
        and_(AuditLog.entity_type == "research_action",
             AuditLog.entity_id.in_(action_ids)),
        and_(AuditLog.entity_type == "finding",
             AuditLog.entity_id.in_(finding_id_set)),
    ),
)
scope_a_events = (
    AuditLog.query
    .options(joinedload(AuditLog.user))
    .filter(*filter_a)
    .order_by(AuditLog.timestamp.desc(), AuditLog.id.desc())
    .limit(CAP)                     # begrensd
    .all()
)
total_a = (
    AuditLog.query.filter(*filter_a).count()   # exacte COUNT, geen fetch van rijen
)
```

**Channel B — link/unlink scope-change events, UITSLUITEND aanvullend (reviewpunt 1).** Na unlinking staat de action niet meer in `inv_actions`; het unlink-auditrecord moet echter wél zichtbaar blijven. Toch moet een link-event van een momenteel gekoppelde action (die dus al in channel A zit) **niet dubbel tellen of renderen**. Channel B is daarom louter aanvullend: het voegt alleen relevante link/unlink-events toe waarvan `entity_id` **niet** al tot de huidige `action_ids` behoort. Om onbegrensde scans te voorkomen (voorgaand reviewpunt) is kanaal B begrensd tot de meest recente `CAP` link/unlink-audits van de zaak, met Python-filter op `old_values`/`new_values.investigation_id` (SafeJSON decodeert naar dict op zowel PG als SQLite — geen dialect-afhankelijke JSON-SQL):

```python
filter_b = (
    AuditLog.tenant_id == tenant_id,
    AuditLog.case_id == case_id,
    AuditLog.entity_type == "research_action",
    AuditLog.action.in_(["link", "unlink"]),
)
link_unlink_audits = (
    AuditLog.query
    .options(joinedload(AuditLog.user))
    .filter(*filter_b)
    .order_by(AuditLog.timestamp.desc(), AuditLog.id.desc())
    .limit(CAP)                     # begrensd
    .all()
)
total_b_case = AuditLog.query.filter(*filter_b).count()   # exact case-level COUNT
b_window_full = len(link_unlink_audits) >= CAP and total_b_case > CAP

_in_scope = lambda e: bool(
    (e.old_values or {}).get("investigation_id") == inv_id
    or (e.new_values or {}).get("investigation_id") == inv_id
)
action_id_set = set(action_ids)     # huidige scoped actions
scope_b_events = [e for e in link_unlink_audits if _in_scope(e)]
supplementary_b = [e for e in scope_b_events if e.entity_id not in action_id_set]
```

**Merge — dedup op AuditLog-ID vóór `total` (reviewpunt 1):**
```python
# Channel A: alle audits van MOMENTEEL scoped actions/findings.
all_events: dict[str, AuditLog] = {e.id: e for e in scope_a_events}
# Channel B: alleen aanvullende link/unlink-events (entity_id ∉ action_id_set),
# zodat een link-event van een huidige gekoppelde action nooit dubbel telt.
for e in supplementary_b:
    all_events.setdefault(e.id, e)

# Weergave: alles wat renderbaar is (≤ CAP)
sorted_events = sorted(
    all_events.values(),
    key=lambda e: (
        e.timestamp.timestamp() if e.timestamp is not None else _EPOCH_MIN,  # UTC-epochsleutel
        e.id,
    ),
    reverse=True,
)[:CAP]

# total = aantal unieke events vóór de cap, altijd op basis van AuditLog-ID-dedup.
# A-count is exact (COUNT(*)); B is aanvullend en overlapt per constructie niet
# met A (entity_id ∉ action_id_set). Defensieve ID-set-check voor de zekerheid:
a_ids = {e.id for e in scope_a_events}
b_ids = {e.id for e in supplementary_b}
overlap = a_ids & b_ids             # per constructie leeg
total = total_a + len(supplementary_b) - len(overlap)

total_exact = not b_window_full
truncated = total > len(sorted_events) or b_window_full
```

**Documentatie van `total`, `total_exact` en `truncated` (reviewpunt 2):**
- `total` = aantal unieke timeline-events vóór de cap, op basis van exacte channel-A-count plus aanvullende (niet-overlappende) B-events. Dit is een exact totaal zolang `total_exact` True is.
- `total_exact` = `not b_window_full`. Zodra de zaak `> CAP` link/unlink-audits heeft, kunnen oudere in-scope unlink/link-events buiten het ontdekkingswindow liggen en is `total` een **ondergrens**, géén exacte telling.
- `truncated` = `total > len(events)` (er bestaan oudere events) **of** `b_window_full` (er kunnen oudere in-scope events buiten het window bestaan).
- UI-regels:
  - `total_exact and truncated`: `Most recent %(count)s of %(total)s`;
  - `not total_exact` (truncated is dan altijd True): `Most recent %(count)s activities; older scope changes may exist` — géén "of N", want N is slechts een ondergrens.
- Er is een bewuste ontwerpkeuze dat kanaal B ontdekkings-begrensd is (i.p.v. dialect-specifieke JSON-SQL); mocht exacte B-telling ooit nodig zijn, dan achter dezelfde service-interface als dialect-branche (PG `->>`, SQLite `json_extract`) — nu niet nodig.

### 2.5 `source_url` — validatie van URL-schema (reviewpunt 3)

Het read-model valideert `source_url` vóór het DTO:
```python
from urllib.parse import urlparse

def _is_linkable_url(raw: str | None) -> bool:
    if not raw:
        return False
    try:
        return urlparse(raw).scheme.lower() in ("http", "https")
    except ValueError:
        return False
```
- `source_url` blijft de rauwe (ge-escaped gerenderde) waarde.
- `source_url_is_linkable=True` alleen bij `http`/`https`. Alleen dán rendert het template een klikbare `href`.
- Overige/ongeldige schema's (`javascript:`, `data:`, enz.) worden als **gewone tekst** getoond. HTML-escaping beschermt tegen markup; deze schemacheck beschermt de `href`-geschiedenis tegen `javascript:`-achtige waarden.

**Gebruikers zonder N+1 (reviewpunt 3):** beide channels laden `user` eager via `joinedload(AuditLog.user)`. Uit de events wordt één `user_id → naam`-mapping gebouwd; `TimelineEventDTO.user_name` en de investigation-creator-naam komen uit die mapping. Er vindt geen per-event user-query plaats.

---

## 3. Sortering — deterministisch

| Sectie | Sortering |
|---|---|
| Actions | `created_at ASC, id ASC` |
| Subjects | `name ASC (case-insensitive), id ASC` |
| Findings | `created_at DESC, id DESC` |
| Timeline | `timestamp DESC, id DESC` |

Timeline-timestamps zijn timezone-aware (`datetime.now(timezone.utc)`). De Python-sorteersleutel gebruikt daarom een consistente UTC-epochsleutel (`dt.timestamp()`, met een constant minimum voor `None`) — géén `datetime.min`-vergelijking tegen timezone-aware waarden.

---

## 4. Finding-dedup en tellingen

- Iedere finding wordt **één keer** getoond in de findings-sectie.
- `FindingDTO.action_ids`/`action_labels` bevatten alleen de **scoped** action-IDs van dit onderzoek (zie §2.2).
- Elke action DTO heeft een eigen `finding_count` = aantal unieke findings gekoppeld aan die actie (berekend in Python).
- In de findings-sectie worden de gekoppelde scoped actions getoond als badges/links naast de finding-titel.

---

## 5. Template — `workflow_investigation_detail.html`

### Nieuwe read-only partial
`templates/cms/workflow/_finding_item_readonly.html`: kleine specifieke partial. Bevat:
- Finding-titel + badges (source_type, status, verified)
- `detail` (gerendered met `urlize_target | safe`)
- `source_url`: alleen als klikbare link wanneer `source_url_is_linkable=True` (http/https); anders weergegeven als gewone ge-escaped tekst
- Screenshots (alleen view, geen upload)
- Scoped action-badges: `<span class="badge">label</span>`
- Geen comment-textarea, geen checkbox, geen verify/reject/report-flag/archive/delete/screenshot-upload, geen JS-afhankelijkheden

### Secties

| Sectie | msgid (EN) | Empty state (msgid EN) |
|---|---|---|
| Info (bestaand) | — | — |
| Subjects | `Subjects referenced by research actions` | `No subjects referenced by research actions yet. Subjects appear here once a research action targets them.` |
| Research Actions | `Research actions` | `No research actions have been run in this investigation yet.` |
| Findings | `Findings` | `No findings generated yet. Run research actions to produce findings.` |
| Timeline | `Activity` / capped badge zie §2.4 | `No activity recorded yet.` |

Timeline-badge (twee varianten, gebaseerd op `total_exact`):
- `total_exact` → `Most recent %(count)s of %(total)s`;
- `not total_exact` → `Most recent %(count)s activities; older scope changes may exist`.

Alle labels zijn uitsluitend **Engelse msgids** (zie §9). Nederlandse tekst is alleen `msgstr` in de NL-catalogus.

---

## 6. Route — `cms/workflow/routes.py:1143`

```python
from cms.services.investigation_workspace import build_inv_workspace

ws = build_inv_workspace(inv, case)   # ORM Subject-objecten gaan NOOIT naar het template
return render_template(
    "cms/workflow/workflow_investigation_detail.html",
    inv=inv,
    case=case,
    ws=ws,
    can_write=_current_user_is_investigator(),
    created_by_name=creator_name,     # afkomstig uit dezelfde user-mapping
)
```

Route-isolatie blijft volledig via `ensure_investigation_access` (403/404-wrong-tenant/wrong-case). De service-querylaag is nooit afzonderlijk van de route bereikbaar voor tenantoverschrijdingen.

---

## 7. Bestandslijst

| Bestand | Actie |
|---|---|
| `cms/services/investigation_workspace.py` | **NIEUW** — read-model, queries, DTO's, dedup, sortering, begrenzing |
| `cms/workflow/routes.py:1143` | Uitbreiden: `build_inv_workspace` aanroep |
| `templates/cms/workflow/workflow_investigation_detail.html` | Uitbreiden met 4 secties + readonly-partial |
| `templates/cms/workflow/_finding_item_readonly.html` | **NIEUW** — read-only finding partial |
| `tests/test_investigation_workspace.py` | **NIEUW** — workspace content + regressie |
| `tests/test_postgres_investigation_workspace_rls.py` | **NIEUW** — echte PostgreSQL/RLS-isolatie voor de workspace (tenant-/case-kolommen, gekoppeld aan de nieuwe service/querylaag) |
| `translations/en/LC_MESSAGES/messages.po` | Engelse msgids toevoegen |
| `translations/nl/LC_MESSAGES/messages.po` | Engelse msgids + NL `msgstr` |
| `translations/en/LC_MESSAGES/messages.mo` | Compileer `.po` → `.mo` |
| `translations/nl/LC_MESSAGES/messages.mo` | Compileer `.po` → `.mo` |

---

## 8. Tests — `tests/test_investigation_workspace.py` (NIEUW)

Fixtures volgen het patroon van `test_investigation_detail_access.py` (`_make_user`, `_enable_workspace`).

### 8.1 Route-isolatie én query-isolatie zijn twee aparte testgroepen (reviewpunt 5)

**Route-isolatie** (HTTP): een andere-tenant-gebruiker mag **nooit** een 200 met lege lijsten krijgen:
| Test | Assertie |
|---|---|
| `test_route_other_tenant_never_200` | GET als andere-tenant-gebruiker → **403 of 404**, nooit 200. Géén succesvolle pagina met lege lijsten. |
| `test_route_wrong_case_404` | Investigation hoort niet bij opgevraagde case → 404 |
| `test_route_flag_off_404_even_for_admin` | Admin zonder flag → 404 |
| `test_route_other_tenant_user_without_access_403` | Andere-tenant-gebruiker zonder access → 403/404 |

**Query/service-isolatie** (service-level, expliciete `tenant_id`-filters):
| Test | Assertie |
|---|---|
| `test_query_tenant_isolation_actions` | `load_inv_actions(tenant_b, ...)` → `[]` |
| `test_query_tenant_isolation_subjects` | `load_inv_subjects(tenant_b, ...)` → `[]` |
| `test_query_tenant_isolation_findings` | `load_inv_findings(tenant_b, ...)` → `[]` |
| `test_query_tenant_isolation_timeline` | `load_inv_timeline(tenant_b, ...)` → `TimelineResult(total=0, events=[])` |

De service/test-publieke signatures nemen `tenant_id` als expliciet argument (géén impliciete `g.tenant_id`), zodat deze tests zonder **request-context** draaien. Let op: Flask-SQLAlchemy vereist nog steeds een **app-context** (`with app.app_context():`) voor engine/sessie-gebruik — die wordt daartoe in de tests/fixtures meegegeven.

### 8.2 Content/regressie
| Test | Assertie |
|---|---|
| `test_soft_deleted_subject_excluded` | `is_deleted=True` → niet in subjects |
| `test_deleted_finding_excluded` | `is_deleted=True` → niet in findings |
| `test_archived_finding_excluded` | `archived_at != None` → niet in findings |
| `test_archived_action_shown` | `archived_at != None` → wél in actions, `archived=True` |
| `test_finding_linked_to_multiple_scoped_actions` | 1 finding, 2 action_ids; beide actions `finding_count=1` |
| `test_finding_also_linked_to_case_wide_action` | scoped action A + zaakbrede Y → `action_ids == [A]`; Y niet aanwezig |
| `test_action_ids_only_scoped` | finding ook aan Y gekoppeld → `FindingDTO.action_ids` bevat niet Y |
| `test_cross_product_prevention` | AuditLog-combinaties van entity_type/ID die niet gekoppeld zijn → 0 extra events |
| `test_timeline_link_event_not_double_counted` | Huidig gekoppelde action met link-event dat via A én B zou matchen → **1 event, `total == 1`**; B is uitsluitend aanvullend |
| `test_unlink_timeline_event_persists` | Na unlinking → timeline bevat "unlink"-event (B-aanvullend) |
| `test_timeline_capped_at_50` | >50 events → `len(events) == 50`, `total > 50`, `truncated is True` |
| `test_timeline_total_exact_when_b_window_not_full` | B-window niet vol → `total_exact is True` en `total` klopt exact |
| `test_timeline_total_undercount_when_b_window_full` | `> CAP` link/unlink-audits in zaak → `total_exact is False`, `b_window_full` effect zichtbaar |
| `test_no_ciphertext_in_html` | Respons-HTML bevat geen `gAAAA` |
| `test_ciphertext_unchanged_in_db` | Na page load blijft subject-veld encrypted (`gAAAA`-prefix) |
| `test_viewer_can_read` | Viewer met case-access → 200 |
| `test_user_without_case_access_403` | Geen case-assignment → 403 |

### 8.3 N+1/query-count — concreet met SQLAlchemy `before_cursor_execute`

Gebruik een event-listener op de engine (géén vrijblijvende constructie):
```python
import sqlalchemy as sa

@pytest.fixture
def query_counter(app_with_context):
    counts = {"n": 0}

    def _count(conn, cursor, statement, parameters, context, executemany):
        counts["n"] += 1

    sa.event.listen(db.engine, "before_cursor_execute", _count)
    yield counts
    sa.event.remove(db.engine, "before_cursor_execute", _count)
```

| Test | Assertie |
|---|---|
| `test_query_count_bounded` | Mèt ≥5 actions, ≥5 findings, ≥5 junction-rijen en ≥5 audit-events: `build_inv_workspace` overschrijdt een gedefinieerde bovengrens `K` niet. `K` = constanten (actions, junction, findings+joinedload, audit A, audit B, users-eager, subjects-ORM) + vaste overhead, bepaald en gedocumenteerd in de test |
| `test_no_per_event_user_query` | Geïsoleerd binnen `test_query_count_bounded`: 20 audit-events brengen géén user-query per event (eager-loaded + `user_id → naam`-mapping) — querycount blijft verre van lineair in het aantal events |

### 8.4 XSS-regressie + URL-schema (aanvullend punt)
`urlize_target | safe` is veilig omdat de filter eerst `html.escape()` uitvoert; dat moet als regressietest vastgelegd worden. Injecteer `<script>alert(1)</script>` in:
| Test | Assertie |
|---|---|
| `test_xss_finding_detail` | `detail` escaped, geen `<script>` in respons |
| `test_xss_finding_title` | `title` escaped |
| `test_xss_finding_source_url` | `source_url` escaped |
| `test_xss_action_label` | action-label escaped |
| `test_xss_subject_name` | subjectnaam escaped |

**URL-schema-tests (reviewpunt 3):**
| Test | Assertie |
|---|---|
| `test_source_url_javascript_scheme` | `javascript:alert(1)` → `source_url_is_linkable is False`; HTML toont gewone tekst, géén `href` met `javascript:` |
| `test_source_url_data_scheme` | `data:text/html,...` → `source_url_is_linkable is False`; gewone tekst |
| `test_source_url_https_linkable` | `https://example.com/x` → `source_url_is_linkable is True`; klikbare `<a href="https://example.com/x">` |

### 8.5 Locale-tests (reviewpunt 4)
Er is maar één NL/EN msgid-set; de NL-test assert de **NL `msgstr`**:
| Test | Assertie |
|---|---|
| `test_nl_locale_strings` | NL `msgstr`: "Subjects **betrokken** via onderzoeksacties", "Activiteiten" | 
| `test_en_locale_strings` | EN msgid: "Subjects referenced by research actions", "Activity" |

De foute assertie `tes bijgehouden subjects` is vervallen.

---

## 9. i18n — uitsluitend Engelse msgids

Gebruik **alleen Engelse msgids** in templates en Python (conform project-conventie):
- `Subjects referenced by research actions`
- `No subjects referenced by research actions yet. Subjects appear here once a research action targets them.`
- `Research actions`
- `No research actions have been run in this investigation yet.`
- `Findings`
- `No findings generated yet. Run research actions to produce findings.`
- `Activity`
- `No activity recorded yet.`
- `Most recent %(count)s of %(total)s`
- `Most recent %(count)s activities; older scope changes may exist`
- `Linked actions`

NL-teksten uitsluitend als `msgstr` in de NL-catalogus:
- `Subjects referenced by research actions` → `Subjects betrokken via onderzoeksacties`
- `Research actions` → `Onderzoeksacties`
- `Findings` → `Bevindingen`
- `Activity` → `Activiteiten`
- `Most recent %(count)s of %(total)s` → `Meest recente %(count)s van %(total)s`
- `Most recent %(count)s activities; older scope changes may exist` → `Meest recente %(count)s activiteiten; er kunnen oudere scope-wijzigingen bestaan`
- `Linked actions` → `Gekoppelde acties`

Na wijzigingen: `pybabel extract`, `pybabel update`, `pybabel compile` voor beide locales (EN-catalogus houdt `msgstr` gelijk aan `msgid`).

---

## 10. Scope-change-events — definitieve aanpak

Zoals §2.4: kanaal B is **uitsluitend aanvullend** en begrensd op de meest recente `CAP` link/unlink-audits van de zaak. Alleen link/unlink-events waarvan `entity_id` niet tot de huidige `action_ids` behoort worden toegevoegd; dedup op AuditLog-ID gebeurt vóór `total`. Python-filter op `old_values`/`new_values.investigation_id` (SafeJSON → dict, DB-agnostisch). Dialect-specifieke JSON-SQL wordt bewust niet gebruikt; dit is de gedocumenteerde afbakening (boundedness vs. exacte B-telling, `total_exact`).

---

## 11. Verificatie/acceptatie voor merge

1. `python3 -m pytest tests/ -n 0` (volledige SQLite-suite) groen.
2. Echte PostgreSQL/RLS-tests groen (`tests/test_postgres_investigation_workspace_rls.py` — nieuwe PG-module, tenant/case-kolommen onder RLS — of uitbreiding van bestaande PG-detail-RLS-module).
3. `python3 -m ruff` en `python3 -m mypy` zero findings.
4. Remote CI groen (alleen de draft-PR).
5. Geen merge/deploy zonder onafhankelijke review.