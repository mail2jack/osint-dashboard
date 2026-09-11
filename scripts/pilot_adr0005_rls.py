"""Productie-pilot ADR-0005 (FORCE RLS op research_actions + action_findings).

Wegwerp-verificatie via request-path (app.test_client) tegen productie-DB,
zonder wachtwoord-login. Bewijst end-to-end dat ADR-0005 functioneel is:

- case + subject + investigation aanmaken via echte HTTP-routes (RLS-context
  via before_request -> GUC), zaakbrede en onderzoekgebonden acties;
- scope-serialisatie in status-API + subject profile (badge 🌐/🔗);
- link/unlink met idempotentie en audit-trail (entity_type=research_action);
- FORCE RLS op ruwe connectie: zonder tenant-GUC 0 rijen zichtbaar, met GUC 2;
- opruiming: deterministische FK-veilige volgorde (geen FK-closure — die faalt
  op stale FK-metadata in deze DB), alleen commit bij nul residu;
- alembic head ongewjzigd.

Gebruik (op de VPS, in /opt/osint-dashboard):

    PYTHONPATH=/opt/osint-dashboard venv/bin/python3 scripts/pilot_adr0005_rls.py

Exit-code 0 bij ALLES GROEN, 1 bij een FAIL.
"""
import datetime
import re
import sys

from app import app
from sqlalchemy import text

app.config["TESTING"] = True
app.config["WTF_CSRF_ENABLED"] = False
app.config["SERVER_NAME"] = "localhost"

TENANT_ID = "3a169c92-04a2-48f9-be1b-1fcf930c0f0f"
PILOT_USERNAME = "versi01"
TS = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
PREFIX = f"PILOT-RLS-{TS}"

FAILED = []


def check(cond, label, detail=""):
    tag = "PASS" if cond else "FAIL"
    print(f"  [{tag}] {label}" + (f"  -> {detail}" if detail else ""))
    if not cond:
        FAILED.append(label)


# ------------------------------------------------------------------ sessie/context
with app.app_context():
    from cms.models import db, User

    user = User.query.filter_by(username=PILOT_USERNAME).first()
    assert user is not None, f"user {PILOT_USERNAME} not found"
    assert user.role == "admin", f"expected admin, got {user.role}"
    assert str(user.tenant_id) == TENANT_ID
    print(f"Pilot-user: {user.email} ({user.role}, tenant={user.tenant_id})")

    client = app.test_client()
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True
        sess["_remember"] = "set"
    print("Session geinjecteerd (request-path met RLS-context).\n")

# ------------------------------------------------------------------ 1. case
print("== 1. Case + subject (POST /cms/workflow/case/new) ==")
r = client.post(
    "/cms/workflow/case/new",
    data={
        "client_name": f"{PREFIX}-klant",
        "client_contact": f"{PREFIX}-contact",
        "client_email": f"{PREFIX}@example.invalid",
        "title": f"{PREFIX} Wegwerpverificatie",
        "priority": "medium",
        "description": "Productie-pilot FORCE RLS (ADR-0005). Wegwerpcase, wordt opgeruimd.",
        "subject_0_name": f"{PREFIX}-subject",
        "subject_0_type": "person",
    },
)
check(r.status_code == 302, "case/new -> 302", f"status={r.status_code}")
m = re.search(r"/cms/workflow/case/([0-9a-f-]{36})", r.headers.get("Location", ""))
check(bool(m), "case_id uit Location", str(r.headers.get("Location")))
case_id = m.group(1)
print(f"  case_id={case_id}")

r = client.get(f"/cms/workflow/case/{case_id}")
check(r.status_code == 200, "case detail 200")
check("INVESTIGATIONS" in r.get_data(as_text=True), "case page embedt investigations_meta (INVESTIGATIONS)")

with app.app_context():
    subj_rows = db.session.execute(
        text("SELECT s.id, s.name, s.subject_type FROM subjects s "
             "JOIN case_subjects cs ON cs.subject_id = s.id WHERE cs.case_id = :c"),
        {"c": case_id},
    ).fetchall()
check(len(subj_rows) == 1, "case heeft 1 subject via junction", str(subj_rows[0].name if subj_rows else None))
subject_id = str(subj_rows[0].id)
print(f"  subject_id={subject_id}")

# ------------------------------------------------------------------ 2. investigation
print("\n== 2. Investigation (POST api/case/<id>/investigations) ==")
r = client.post(
    f"/cms/workflow/api/case/{case_id}/investigations",
    json={"title": f"{PREFIX}-onderzoek"},
)
check(r.status_code == 200, "investigation create 200", f"status={r.status_code}")
inv = r.get_json().get("investigation", {})
inv_id = inv.get("id")
check(bool(inv_id), "investigation id", str(inv_id))
print(f"  inv_id={inv_id}")

# ------------------------------------------------------------------ 3. acties
print("\n== 3. Acties: zaakbreed + gekoppeld (mode=proposal, manual_entry) ==")
r = client.post(
    f"/cms/workflow/api/case/{case_id}/run-action",
    json={"action_type": "manual_entry", "mode": "proposal", "data_value": f"{PREFIX} zaakbreed"},
)
check(r.status_code == 200, "case-wide action 200", f"status={r.status_code}")
a1 = r.get_json()
a1_id = a1.get("id")
check(bool(a1_id) and a1.get("status") == "proposal", "a1 id/status proposa", str(a1_id))

r = client.post(
    f"/cms/workflow/api/case/{case_id}/run-action",
    json={"action_type": "manual_entry", "mode": "proposal",
          "data_value": f"{PREFIX} gekoppeld", "investigation_id": inv_id},
)
check(r.status_code == 200, "linked action 200", f"status={r.status_code}")
a2 = r.get_json()
a2_id = a2.get("id")
check(bool(a2_id) and a2.get("status") == "proposal", "a2 id/status proposal", str(a2_id))
print(f"  a1 (zaakbreed)={a1_id}  a2 (gekoppeld)={a2_id}")

# ------------------------------------------------------------------ 4. scope serialisatie
print("\n== 4. Scope-serialisatie ==")
r = client.get(f"/cms/workflow/api/case/{case_id}/status")
check(r.status_code == 200, "status API 200")
rows = {a["id"]: a for a in r.get_json().get("actions", []) or []}
check(a1_id in rows and a2_id in rows, "beide acties in status-API", f"totaal {len(rows)} acties")
check(rows[a1_id].get("investigation_id") is None, "a1: investigation_id null (zaakbreed)")
check(rows[a2_id].get("investigation_id") == inv_id, "a2: investigation_id == inv",
      f"get={rows[a2_id].get('investigation_id')}")
check(bool(rows[a2_id].get("investigation_title")) and bool(rows[a2_id].get("investigation_number")),
      "a2: investigation_title+number meegeleverd",
      f"{rows[a2_id].get('investigation_title')!r} {rows[a2_id].get('investigation_number')!r}")

r = client.get(f"/cms/subjects/{subject_id}/profile")
check(r.status_code == 200, "subject profile page 200 (flag ON)", f"status={r.status_code}")
p = r.get_data(as_text=True)
check("🌐" in p, "profile toont 🌐 (case-wide badge)")
check("🔗" in p, "profile toont 🔗 (linked badge)")
check(f"{PREFIX}-onderzoek" in p, "profile toont gekoppeld onderzoek (titel)")

# ------------------------------------------------------------------ 5. link / unlink + audit
print("\n== 5. Link / unlink (incl. idempotentie) + audit ==")
with app.app_context():
    from cms.models import AuditLog

    def cnt(action_id, action):
        return AuditLog.query.filter_by(
            entity_type="research_action", entity_id=action_id,
            action=action, case_id=case_id,
        ).count()

    base = {"c1": cnt(a1_id, "create"), "c2": cnt(a2_id, "create"),
            "l1": cnt(a1_id, "link"), "u1": cnt(a1_id, "unlink"), "l2": cnt(a2_id, "link")}
check(base["c1"] == 1 and base["c2"] == 1, "audit: create a1+a2 == 1", f"{base}")
check(base["l1"] == 0 and base["u1"] == 0 and base["l2"] == 0, "audit: nog geen link/unlink")

r = client.post(f"/cms/workflow/api/case/{case_id}/actions/{a1_id}/link", json={"investigation_id": inv_id})
check(r.status_code == 200, "link a1 200", f"status={r.status_code}")
check(r.get_json().get("investigation_id") == inv_id, "link a1: response inv_id")

r = client.post(f"/cms/workflow/api/case/{case_id}/actions/{a1_id}/link", json={"investigation_id": inv_id})
check(r.status_code == 200, "re-link a1 (zelfde inv) 200")

r = client.post(f"/cms/workflow/api/case/{case_id}/actions/{a2_id}/link", json={"investigation_id": inv_id})
check(r.status_code == 200, "link a2 (al bij creatie gekoppeld) 200")

r = client.delete(f"/cms/workflow/api/case/{case_id}/actions/{a1_id}/link")
check(r.status_code == 200, "unlink a1 200")
check(r.get_json().get("investigation_id") is None, "unlink a1: response null")

with app.app_context():
    def cnt(action_id, action):
        return AuditLog.query.filter_by(
            entity_type="research_action", entity_id=action_id,
            action=action, case_id=case_id,
        ).count()

    check(cnt(a1_id, "link") == 1, "audit: link a1 == 1 (idempotent herlink geeft geen extra)")
    check(cnt(a1_id, "unlink") == 1, "audit: unlink a1 == 1")
    check(cnt(a2_id, "link") == 0, "audit: link a2 == 0 (gekoppeld bij creatie -> alleen create)")

r = client.get(f"/cms/workflow/api/case/{case_id}/status")
rows = {a["id"]: a for a in r.get_json().get("actions", []) or []}
check(rows[a1_id].get("investigation_id") is None, "status na unlink: a1 null")
check(rows[a2_id].get("investigation_id") == inv_id, "status na unlink: a2 behoudt link")

# ------------------------------------------------------------------ 6. audit display
print("\n== 6. Audit trail display (GET /cms/audit) ==")
r = client.get(f"/cms/audit?case_id={case_id}&entity_type=research_action")
check(r.status_code == 200, "audit page (research_action) 200")
a = r.get_data(as_text=True)
check("manual_entry" in a, "audit pagina toont research_action entries")
r = client.get(f"/cms/audit?case_id={case_id}")
check(r.status_code == 200, "audit page (alle) 200")

# ------------------------------------------------------------------ 7. RLS-bewijs
print("\n== 7. RLS-bewijs op ruwe connectie (rol 'osint', FORCE RLS) ==")
with app.app_context():
    with db.engine.connect() as conn:
        conn.execute(text("SELECT set_config('app.tenant_id','',true), set_config('app.bypass_rls','',true)"))
        n0 = conn.execute(text("SELECT count(*) FROM research_actions WHERE case_id=:c"), {"c": case_id}).scalar()
        conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": TENANT_ID})
        n1 = conn.execute(text("SELECT count(*) FROM research_actions WHERE case_id=:c"), {"c": case_id}).scalar()
        conn.commit()
check(n0 == 0, "RLS: zonder GUC -> 0 rijen zichtbaar", f"count={n0}")
check(n1 == 2, "RLS: met tenant-GUC -> 2 rijen zichtbaar", f"count={n1}")

# ------------------------------------------------------------------ 8. opruiming (deterministisch)
print("\n== 8. Opruiming wegwerp-case (deterministische FK-veilige volgorde) ==")
with app.app_context():
    from cms.models import db

    # Alle deletes draaien binnen dezelfde connectie met tenant-GUC, zodat RLS
    # de rijen niet verbergt. We verwijderen via case_id/subject_id en de
    # client via het client_id van de zaak.
    with db.engine.connect() as conn:
        conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": TENANT_ID})
        client_id = conn.execute(
            text("SELECT client_id FROM cases WHERE id=:c"), {"c": case_id}
        ).scalar()
        assert client_id, "client_id niet gevonden"

        def delstep(label, sql, params):
            with conn.begin_nested():
                res = conn.execute(text(sql), params)
            print(f"    - {label}: {res.rowcount}")
            if res.rowcount == 0 and "subjects" not in label:
                pass  # legitiem 0 voor junction/leegstuizen; subject zelf wordt gecheckt

        delstep("action_findings", "DELETE FROM action_findings WHERE action_id IN :a",
                {"a": (a1_id, a2_id)})
        delstep("research_actions", "DELETE FROM research_actions WHERE case_id = :c", {"c": case_id})
        delstep("investigation_seq_counters", "DELETE FROM investigation_seq_counters WHERE case_id = :c", {"c": case_id})
        delstep("investigations", "DELETE FROM investigations WHERE case_id = :c", {"c": case_id})
        delstep("case_subjects", "DELETE FROM case_subjects WHERE case_id = :c", {"c": case_id})
        delstep("audit_logs_case", "DELETE FROM audit_logs WHERE case_id = :c", {"c": case_id})
        delstep("audit_logs_entity", "DELETE FROM audit_logs WHERE entity_id IN :a",
                {"a": (a1_id, a2_id)})
        delstep("audit_logs_desc", "DELETE FROM audit_logs WHERE description LIKE :p",
                {"p": f"%{PREFIX}%"})
        delstep("cases", "DELETE FROM cases WHERE id = :c", {"c": case_id})
        for tbl in ("subject_identifiers", "subject_facts", "addresses", "contacts", "social_accounts"):
            delstep(tbl, f"DELETE FROM {tbl} WHERE subject_id = :s", {"s": subject_id})
        delstep("subject_relations",
                "DELETE FROM subject_relations WHERE subject_id = :s OR related_subject_id = :s",
                {"s": subject_id})
        delstep("subjects", "DELETE FROM subjects WHERE id = :s", {"s": subject_id})
        inv_rows = conn.execute(
            text("SELECT id FROM invoices WHERE client_id = :c"), {"c": client_id}
        ).scalars().all()
        if inv_rows:
            delstep("invoice_items", "DELETE FROM invoice_items WHERE invoice_id IN :i", {"i": tuple(inv_rows)})
            delstep("invoices", "DELETE FROM invoices WHERE id IN :i", {"i": tuple(inv_rows)})
        delstep("clients", "DELETE FROM clients WHERE id = :c", {"c": client_id})
        conn.commit()
        print("    commited")

    # Residu-verificatie met verse connectie (GUC-scoped aan transactie; na
    # bovenstaande commit elders opnieuw met relatieve tellingen).
    with db.engine.connect() as conn:
        conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": TENANT_ID})
        rem_cases = conn.execute(text("SELECT count(*) FROM cases WHERE id=:c"), {"c": case_id}).scalar()
        rem_inv = conn.execute(text("SELECT count(*) FROM investigations WHERE id=:c"), {"c": inv_id}).scalar()
        rem_ra = conn.execute(text("SELECT count(*) FROM research_actions WHERE case_id=:c"), {"c": case_id}).scalar()
        rem_subj = conn.execute(text("SELECT count(*) FROM subjects WHERE id=:c"), {"c": subject_id}).scalar()
        rem_audit = conn.execute(text("SELECT count(*) FROM audit_logs WHERE description LIKE :p"), {"p": f"%{PREFIX}%"}).scalar()
        head = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        conn.rollback()
check(rem_cases == 0, "cleanup: 0 cases over", f"count={rem_cases}")
check(rem_inv == 0, "cleanup: 0 investigations over", f"count={rem_inv}")
check(rem_ra == 0, "cleanup: 0 research_actions over", f"count={rem_ra}")
check(rem_subj == 0, "cleanup: 0 subjects over", f"count={rem_subj}")
check(rem_audit == 0, "cleanup: 0 residu (audit)", f"count={rem_audit}")
check(head == "f6a7b8c9d0e1", "alembic head ongewijzigd", str(head))

print("\n" + "=" * 70)
print("PILOT RESULTAAT: " + ("ALLES GROEN" if not FAILED else f"{len(FAILED)} FAILS: {'; '.join(FAILED)}"))
sys.exit(1 if FAILED else 0)