"""Safe AI research planning routes.

The first MVP deliberately stops at approved research-action proposals. It
never starts an action automatically.
"""

import json
import re
import uuid
from datetime import datetime, timezone
from urllib.parse import urlparse

from flask import jsonify, render_template, request
from flask_login import current_user, login_required

from cms.auth import ensure_case_access
from cms.models import AuditLog, Investigation, UserRole, db
from cms.services.action_scope import action_scope_label, get_linkable_investigation, log_scope_audit
from cms.services.ai_service import (
    _generate,
    analyze_natural_language,
    check_ai_available,
    get_ai_config,
)
from cms.workflow.actions.registry import (
    ACTION_REGISTRY,
    is_paid_action,
    paid_channels_enabled,
    start_action_async,
)
from cms.workflow.models import (
    WorkflowActionFinding,
    WorkflowCase,
    WorkflowFinding,
    WorkflowResearchAction,
    WorkflowSubject,
)


from . import workflow_bp

_INVESTIGATOR_ROLES = {
    UserRole.INVESTIGATOR.value,
    UserRole.SENIOR_INVESTIGATOR.value,
    UserRole.ADMIN.value,
    UserRole.OWNER.value,
}

# First MVP allowlist: public/local actions only. Paid channels are never
# proposed by the AI planner in this phase.
_PLAN_ACTIONS = {
    "username": ["social", "google_dork", "browser_search"],
    "name": ["social", "google_dork", "browser_search"],
    "email": ["email", "google_dork", "browser_search"],
    "phone": ["phone", "google_dork", "browser_search"],
    "ip": ["osint", "google_dork", "browser_search"],
    "domain": ["osint", "subdomain", "browser_search"],
}

_NARRATIVE_PLACEHOLDER_RE = re.compile(r"\[([A-Z][A-Z0-9_ ]+)\]")


def _clean_narrative_placeholders(narrative, subject_names):
    """Replace common AI placeholders and remove unresolved brackets."""
    primary_name = next((name for name in subject_names if name), "de onderzochte persoon")
    replacements = {
        "PERSON_NAME": primary_name,
        "SUBJECT_NAME": primary_name,
        "NAME": primary_name,
        "ADDRESS": "Adres",
        "EMAIL": "E-mailadres",
        "PHONE": "Telefoonnummer",
        "SOURCE": "Bron",
    }

    def replace(match):
        return replacements.get(match.group(1), match.group(1).replace("_", " ").title())

    cleaned = _NARRATIVE_PLACEHOLDER_RE.sub(replace, narrative or "").strip()
    for name in subject_names:
        canonical = _canonical_person_name(name)
        if name and canonical and name != canonical:
            cleaned = cleaned.replace(name, canonical)
    return cleaned


def _canonical_person_name(name):
    """Return a person's name without a duplicated leading initial."""
    text = str(name or "").strip()
    parts = text.split()
    if len(parts) >= 2 and re.fullmatch(r"[A-Za-zÀ-ÖØ-öø-ÿ]\.", parts[0]):
        if parts[1] and parts[0][0].casefold() == parts[1][0].casefold():
            return " ".join(parts[1:])
    return text


def _candidate_account_context(findings):
    """Build a compact, source-bound account list for the narrative model."""
    candidates = []
    seen = set()
    for finding in findings:
        title = (finding.title or "").strip()
        url = (finding.source_url or "").strip()
        source_type = (finding.source_type or "").strip()
        if not (
            source_type in {"social", "email", "phone"}
            or re.search(r"\b(account|profile|linkedin|whatsapp|telegram|instagram|reddit|spotify|tiktok|youtube)\b", title, re.I)
        ):
            continue
        key = (title.casefold(), url.casefold())
        if key in seen:
            continue
        seen.add(key)
        host = urlparse(url).netloc.lower().removeprefix("www.") if url else ""
        candidates.append({
            "title": title[:220],
            "platform_or_host": host or source_type or "onbekend",
            "url": url[:500],
            "status": finding.status or ("verified" if finding.verified else "candidate"),
            "verified": bool(finding.verified),
            "source_finding_id": finding.id,
        })
    return candidates[:100]


def _linkedin_finding_context(findings):
    """Expose LinkedIn evidence explicitly to the report model."""
    profiles = []
    for finding in findings:
        url = (finding.source_url or "").strip()
        host = urlparse(url).netloc.lower().removeprefix("www.") if url else ""
        source_type = (finding.source_type or "").strip().casefold()
        if source_type != "linkedin" and "linkedin." not in host:
            continue
        profiles.append({
            "title": (finding.title or "")[:220],
            "profile_url": url[:500],
            "content": (finding.content or "")[:1800],
            "detail": (finding.detail or "")[:1000],
            "status": finding.status or ("verified" if finding.verified else "candidate"),
            "verified": bool(finding.verified),
            "source_finding_id": finding.id,
        })
    return profiles[:50]


def _upsert_narrative_in_report(case, investigation, narrative, research_question=None):
    """Insert or replace one AI narrative in the editable Markdown report."""
    marker = f"<!-- ai-investigation-narrative:{investigation.id} -->"
    end_marker = f"<!-- /ai-investigation-narrative:{investigation.id} -->"
    heading = getattr(investigation, "human_number", None) or investigation.title or "onderzoek"
    question_block = ""
    if research_question:
        quoted_question = "\n".join(f"> {line}" for line in research_question.strip().splitlines())
        question_block = f"## Onderzoeksvraag\n\n{quoted_question}\n\n"
    block = (
        f"{marker}\n"
        f"{question_block}"
        f"## AI-onderzoeksrapport — {heading}\n\n"
        f"{narrative.strip()}\n"
        f"{end_marker}"
    )
    body = case.pv_body or ""
    pattern = re.compile(re.escape(marker) + r".*?" + re.escape(end_marker), re.DOTALL)
    if pattern.search(body):
        case.pv_body = pattern.sub(block, body, count=1)
    else:
        separator = "\n\n" if body.strip() else ""
        case.pv_body = body.rstrip() + separator + block + "\n"
    case.pv_updated_at = datetime.now(timezone.utc)


def _identifier(value, identifier_type, subject, source, platform=None):
    value = str(value or "").strip()
    if not value:
        return None
    return {
        "value": value,
        "type": identifier_type,
        "subject_id": subject.id,
        "subject_name": subject.name or "",
        "source": source,
        "platform": platform or "",
    }


def _subject_context(subject):
    """Return safe, decrypted research inputs for one subject.

    These values are planning context only. They are never treated as
    findings or evidence until an explicit research action produces a result.
    """
    subject.decrypt_identifiers()
    items = []
    seen = set()

    def add(value, kind, source, platform=None):
        item = _identifier(value, kind, subject, source, platform)
        if not item:
            return
        key = (kind, item["value"].casefold(), item["platform"].casefold())
        if key not in seen:
            seen.add(key)
            items.append(item)

    add(subject.compute_name(), "name", "subject.name")
    add(subject.email, "email", "subject.email")
    add(subject.phone, "phone", "subject.phone")

    for contact in subject.contacts.all():
        contact.decrypt_fields()
        kind = contact.contact_type or ""
        if kind == "social":
            add(contact.value, "username", "subject.contact", contact.platform)
        elif kind in {"email", "phone"}:
            add(contact.value, kind, "subject.contact", contact.platform)

    for account in subject.social_accounts.all():
        add(account.username, "username", "subject.social_account", account.platform)

    for raw in subject.workflow_social_accounts or []:
        if isinstance(raw, dict):
            add(raw.get("username") or raw.get("value"), "username", "subject.social_account", raw.get("platform"))
        else:
            add(raw, "username", "subject.social_account")

    for identifier in getattr(subject, "identifiers", []) or []:
        add(
            identifier.get_value(),
            "username" if identifier.identifier_type in {"social", "handle"} else identifier.identifier_type,
            "subject.identifier",
        )
    return items


def _deterministic_query_parse(query):
    """Small offline fallback used when no AI provider is configured."""
    text = query.strip()
    lowered = text.casefold()
    if re.search(r"\b(?:e[- ]?mail|mailadres|email)", lowered):
        kind = "email"
    elif re.search(r"\b(?:telefoon|phone|telefoonnummer|nummer)", lowered):
        kind = "phone"
    elif re.search(r"\b(?:username|gebruikersnaam|handle|account)", lowered):
        kind = "username"
    elif re.search(r"\b(?:ip|ipv4|ipv6)", lowered):
        kind = "ip"
    elif re.search(r"\b(?:domein|domain|website)", lowered):
        kind = "domain"
    else:
        kind = "name"
    return {"type": kind, "query": text, "confidence": 0.35, "source": "offline-rules"}


def _investigator_required():
    if not current_user.is_authenticated or current_user.role not in _INVESTIGATOR_ROLES:
        return jsonify({"error": "Investigator access required"}), 403
    return None


def _load_case(case_id):
    case = db.session.get(WorkflowCase, case_id)
    if not case:
        return None, (jsonify({"error": "Case not found"}), 404)
    ensure_case_access(case)
    return case, None


def _build_plan(query, parsed, subjects=None):
    search_type = parsed.get("type") if isinstance(parsed, dict) else None
    normalized_type = search_type if search_type in _PLAN_ACTIONS else "name"
    actions = []
    context = []
    for subject in subjects or []:
        context.extend(_subject_context(subject))
    if not context:
        context = [{
            "value": parsed.get("query", query) if isinstance(parsed, dict) else query,
            "type": normalized_type,
            "subject_id": None,
            "subject_name": "",
            "source": "research question",
            "platform": "",
        }]

    for index, target in enumerate(context[:40]):
        target_type = target["type"] if target["type"] in _PLAN_ACTIONS else normalized_type
        for action_type in _PLAN_ACTIONS[target_type]:
            info = ACTION_REGISTRY.get(action_type)
            if not info or is_paid_action(action_type):
                continue
            actions.append(
                {
                    "proposal_key": f"{index}:{action_type}",
                    "action_type": action_type,
                    "label": info["label"],
                    "description": info["description"],
                    "category": info["category"],
                    "cost_label": info.get("cost_label", ""),
                    "data_value": target["value"],
                    "target_type": target_type,
                    "target_subject_id": target["subject_id"],
                    "target_subject_name": target["subject_name"],
                    "target_source": target["source"],
                    "platform": target["platform"],
                    "rationale": (
                        "Bestaande identifier uit het dossier; zoek dit exacte gegeven."
                    ),
                }
            )
    related = []
    for subject in subjects or []:
        for related_subject in subject.related_subjects:
            related.append({"id": related_subject.id, "name": related_subject.name, "type": related_subject.subject_type})
    return {
        "id": str(uuid.uuid4()),
        "query": query,
        "interpretation": {
            "type": normalized_type,
            "value": parsed.get("query", query) if isinstance(parsed, dict) else query,
            "confidence": parsed.get("confidence", 0) if isinstance(parsed, dict) else 0,
            "source": parsed.get("source", "ai") if isinstance(parsed, dict) else "ai",
        },
        "actions": actions,
        "known_identifiers": context,
        "relationship_expansion": {
            "status": "suggested",
            "candidates": related,
            "rule": "Explore relationships only when supported by a concrete public source; do not treat suggestions as facts.",
        },
        "policy": {
            "execution": "approval_required",
            "paid_channels": "not_proposed",
            "automatic_verification": False,
            "exclude_instruction_pages": True,
            "relations_require_evidence": True,
        },
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def _render_ai_research_page(case_id, investigation_id=None):
    denied = _investigator_required()
    if denied:
        return denied
    case, error = _load_case(case_id)
    if error:
        return error
    investigation = None
    if investigation_id:
        investigation = Investigation.query.filter_by(
            id=investigation_id, case_id=case_id, tenant_id=current_user.tenant_id
        ).first()
        if not investigation:
            return jsonify({"error": "Investigation not found"}), 404
    subjects = case.subjects.all()
    ai_config = get_ai_config()
    return render_template(
        "cms/workflow/ai_research_plan.html",
        case=case,
        subjects=subjects,
        ai_config=ai_config,
        investigation=investigation,
    )


@workflow_bp.route("/case/<case_id>/ai-research", methods=["GET"])
@login_required
def ai_research_page(case_id):
    return _render_ai_research_page(case_id)


@workflow_bp.route("/case/<case_id>/investigations/<investigation_id>/ai-research", methods=["GET"])
@login_required
def investigation_ai_research_page(case_id, investigation_id):
    return _render_ai_research_page(case_id, investigation_id)


@workflow_bp.route("/api/case/<case_id>/ai-research/plan", methods=["POST"])
@login_required
def create_ai_plan(case_id):
    denied = _investigator_required()
    if denied:
        return denied
    case, error = _load_case(case_id)
    if error:
        return error
    body = request.get_json(silent=True) or {}
    query = str(body.get("query") or "").strip()
    if not query:
        return jsonify({"error": "A research question is required"}), 400
    if len(query) > 2000:
        return jsonify({"error": "Research question is too long"}), 400

    subjects = case.subjects.all()
    config = get_ai_config()
    if config.get("available") and check_ai_available():
        parsed = analyze_natural_language(query, list(_PLAN_ACTIONS))
    else:
        parsed = _deterministic_query_parse(query)
    selected_subject_id = body.get("subject_id") or None
    selected_subjects = [s for s in subjects if not selected_subject_id or s.id == str(selected_subject_id)]
    plan = _build_plan(query, parsed, selected_subjects)
    AuditLog.log(
        user_id=current_user.id,
        action="create",
        entity_type="ai_research_plan",
        entity_id=plan["id"],
        ip_address=request.remote_addr,
        case_id=case_id,
        description=(
            f"Created AI research plan for case {case.case_number}; "
            "no research action was started"
        ),
    )
    db.session.commit()
    return jsonify({
        "plan": plan,
        "provider": config.get("provider") if config.get("available") else "offline-rules",
        "model": config.get("model") if config.get("available") else None,
    })


@workflow_bp.route("/api/case/<case_id>/ai-research/approve", methods=["POST"])
@login_required
def approve_ai_plan(case_id):
    denied = _investigator_required()
    if denied:
        return denied
    case, error = _load_case(case_id)
    if error:
        return error
    body = request.get_json(silent=True) or {}
    plan = body.get("plan") or {}
    selected = body.get("selected_actions") or []
    query = str(plan.get("query") or "").strip()
    if not query or not isinstance(selected, list) or not selected:
        return jsonify({"error": "A plan and at least one selected action are required"}), 400

    selected = [str(item) for item in selected]
    plan_actions = plan.get("actions") if isinstance(plan.get("actions"), list) else []
    specs_by_key = {
        str(item.get("proposal_key")): item
        for item in plan_actions
        if isinstance(item, dict) and item.get("action_type") in ACTION_REGISTRY
    }
    specs = []
    for item in selected:
        if item in specs_by_key:
            specs.append(specs_by_key[item])
        elif item in ACTION_REGISTRY:
            # Backward compatibility for older plans and API clients.
            spec = next((a for a in plan_actions if a.get("action_type") == item), None)
            specs.append(spec or {"action_type": item, "data_value": query, "target_subject_id": None})
    if not specs or len(specs) != len(selected):
        return jsonify({"error": "The plan contains an action outside the MVP allowlist"}), 400
    if any(is_paid_action(spec["action_type"]) for spec in specs) or any(
        not paid_channels_enabled(current_user.tenant_id) and is_paid_action(spec["action_type"])
        for spec in specs
    ):
        return jsonify({"error": "Paid actions require separate tenant approval"}), 409

    subject_id = body.get("subject_id") or None
    investigation_id = body.get("investigation_id") or None
    subject = None
    _, err, err_status = get_linkable_investigation(
        investigation_id, case=case, tenant_id=current_user.tenant_id
    )
    if err:
        return jsonify({"error": err}), err_status
    investigation = db.session.get(Investigation, investigation_id)
    if investigation and query:
        investigation.ai_research_question = query

    created = []
    for spec in specs:
        action_type = spec["action_type"]
        target_subject_id = spec.get("target_subject_id") or subject_id
        subject = None
        if target_subject_id:
            subject = db.session.get(WorkflowSubject, target_subject_id)
            if not subject or not case.subjects.filter_by(id=target_subject_id).first():
                return jsonify({"error": "Subject is not linked to this case"}), 400
        data_value = str(spec.get("data_value") or query).strip()
        action = WorkflowResearchAction(
            id=str(uuid.uuid4()),
            case_id=case_id,
            subject_id=target_subject_id,
            investigation_id=investigation_id,
            target_kind="subject" if target_subject_id else "case",
            action_type=action_type,
            data_value=data_value,
            label=ACTION_REGISTRY[action_type]["label"],
            status="proposal",
            tenant_id=current_user.tenant_id,
            created_by=current_user.id,
        )
        action.target_snapshot = json.dumps(action.build_target_snapshot(subject, data_value))
        db.session.add(action)
        db.session.flush()
        log_scope_audit(
            action=action,
            audit_action="create",
            user_id=current_user.id,
            ip_address=request.remote_addr,
            case_id=case_id,
            description=f"Approved AI plan and proposed {action_type} ({action_scope_label(action)})",
            new_investigation_id=investigation_id,
        )
        created.append(action.id)
    AuditLog.log(
        user_id=current_user.id,
        action="approve",
        entity_type="ai_research_plan",
        entity_id=plan.get("id") or str(uuid.uuid4()),
        ip_address=request.remote_addr,
        case_id=case_id,
        description=f"Approved AI research plan and created {len(created)} action proposals",
    )
    db.session.commit()
    return jsonify({"ok": True, "proposal_ids": created, "started": False})


@workflow_bp.route("/api/case/<case_id>/investigations/<investigation_id>/ai-research/start-proposals", methods=["POST"])
@login_required
def start_investigation_ai_proposals(case_id, investigation_id):
    """Explicitly start AI proposals belonging to one investigation."""
    denied = _investigator_required()
    if denied:
        return denied
    case, error = _load_case(case_id)
    if error:
        return error
    investigation, err, err_status = get_linkable_investigation(
        investigation_id, case=case, tenant_id=current_user.tenant_id
    )
    if err:
        return jsonify({"error": err}), err_status
    body = request.get_json(silent=True) or {}
    requested_ids = body.get("action_ids")
    query = WorkflowResearchAction.query.filter_by(
        case_id=case_id,
        tenant_id=current_user.tenant_id,
        investigation_id=investigation.id,
        status="proposal",
    )
    if isinstance(requested_ids, list) and requested_ids:
        query = query.filter(WorkflowResearchAction.id.in_({str(item) for item in requested_ids}))
    proposals = query.all()
    if not proposals:
        return jsonify({"error": "No startable proposals found in this investigation"}), 409
    if any(is_paid_action(action.action_type) for action in proposals) and not paid_channels_enabled():
        return jsonify({"error": "Paid research channels require separate tenant approval"}), 409
    started_ids = []
    for action in proposals:
        action.status = "pending"
        AuditLog.log(
            user_id=current_user.id,
            action="start",
            entity_type="research_action",
            entity_id=action.id,
            ip_address=request.remote_addr,
            case_id=case_id,
            description="Explicitly started AI research proposal from investigation",
        )
        started_ids.append(action.id)
    db.session.commit()
    for action_id in started_ids:
        start_action_async(action_id)
    return jsonify({"ok": True, "started_ids": started_ids, "started": len(started_ids)})


@workflow_bp.route(
    "/api/case/<case_id>/investigations/<investigation_id>/linkedin-deepen",
    methods=["POST"],
)
@login_required
def create_linkedin_deepening_proposal(case_id, investigation_id):
    """Create, but never start, an explicit LinkedIn deepening proposal."""
    denied = _investigator_required()
    if denied:
        return denied
    case, error = _load_case(case_id)
    if error:
        return error
    investigation, err, err_status = get_linkable_investigation(
        investigation_id, case=case, tenant_id=current_user.tenant_id
    )
    if err:
        return jsonify({"error": err}), err_status

    body = request.get_json(silent=True) or {}
    finding_id = str(body.get("finding_id") or "").strip()
    source_url = str(body.get("source_url") or "").strip()[:2000]
    parsed = urlparse(source_url)
    if not finding_id or parsed.scheme not in {"http", "https"} or "linkedin." not in parsed.netloc.lower():
        return jsonify({"error": "Een geldige LinkedIn-profiel-URL en finding zijn vereist"}), 400

    finding = WorkflowFinding.query.filter_by(
        id=finding_id,
        case_id=case_id,
        tenant_id=current_user.tenant_id,
        is_deleted=False,
    ).first()
    if not finding:
        return jsonify({"error": "LinkedIn-finding niet gevonden"}), 404
    scoped_action = (
        WorkflowResearchAction.query
        .join(WorkflowActionFinding, WorkflowActionFinding.action_id == WorkflowResearchAction.id)
        .filter(
            WorkflowResearchAction.case_id == case_id,
            WorkflowResearchAction.tenant_id == current_user.tenant_id,
            WorkflowResearchAction.investigation_id == investigation.id,
            WorkflowActionFinding.finding_id == finding.id,
        )
        .order_by(WorkflowResearchAction.created_at.asc())
        .first()
    )
    subject_id = scoped_action.subject_id if scoped_action else None
    subject = db.session.get(WorkflowSubject, subject_id) if subject_id else None
    if subject_id and (not subject or not case.subjects.filter_by(id=subject_id).first()):
        return jsonify({"error": "Het subject van deze finding is niet aan de zaak gekoppeld"}), 400

    existing = WorkflowResearchAction.query.filter_by(
        case_id=case_id,
        tenant_id=current_user.tenant_id,
        investigation_id=investigation.id,
        action_type="linkedin",
        data_value=source_url,
        status="proposal",
    ).first()
    if existing:
        return jsonify({"ok": True, "id": existing.id, "status": "proposal", "existing": True})

    action = WorkflowResearchAction(
        id=str(uuid.uuid4()),
        case_id=case_id,
        subject_id=subject_id,
        investigation_id=investigation.id,
        target_kind="subject" if subject_id else "case",
        action_type="linkedin",
        data_value=source_url,
        label="LinkedIn-profiel verdiepen",
        status="proposal",
        tenant_id=current_user.tenant_id,
    )
    action.target_snapshot = json.dumps(action.build_target_snapshot(subject, source_url))
    db.session.add(action)
    db.session.flush()
    log_scope_audit(
        action=action,
        audit_action="create",
        user_id=current_user.id,
        ip_address=request.remote_addr,
        case_id=case_id,
        description=f"Proposed LinkedIn deepening from finding {finding.id}; not started",
        new_investigation_id=investigation.id,
    )
    db.session.commit()
    return jsonify({"ok": True, "id": action.id, "status": "proposal", "started": False})


@workflow_bp.route("/api/case/<case_id>/investigations/<investigation_id>/ai-research/narrative", methods=["POST"])
@login_required
def investigation_ai_narrative(case_id, investigation_id):
    """Create an on-demand, source-bound narrative from investigation findings."""
    denied = _investigator_required()
    if denied:
        return denied
    case, error = _load_case(case_id)
    if error:
        return error
    investigation, err, err_status = get_linkable_investigation(
        investigation_id, case=case, tenant_id=current_user.tenant_id
    )
    if err:
        return jsonify({"error": err}), err_status
    actions = WorkflowResearchAction.query.filter_by(
        case_id=case_id, tenant_id=current_user.tenant_id, investigation_id=investigation.id
    ).order_by(WorkflowResearchAction.created_at.asc()).all()
    action_ids = [action.id for action in actions]
    links = WorkflowActionFinding.query.filter(WorkflowActionFinding.action_id.in_(action_ids)).all() if action_ids else []
    finding_ids = sorted({link.finding_id for link in links})
    findings = WorkflowFinding.query.filter(
        WorkflowFinding.case_id == case_id,
        WorkflowFinding.tenant_id == current_user.tenant_id,
        WorkflowFinding.id.in_(finding_ids),
        WorkflowFinding.is_deleted.is_(False),
        WorkflowFinding.archived_at.is_(None),
    ).order_by(WorkflowFinding.created_at.asc()).all() if finding_ids else []
    if not findings:
        return jsonify({"error": "Er zijn nog geen findings om samen te vatten"}), 409
    evidence = []
    for finding in findings[:100]:
        evidence.append({
            "title": finding.title,
            "content": (finding.content or "")[:900],
            "detail": (finding.detail or "")[:600],
            "source": finding.source_url or finding.source_type or "onbekende bron",
            "status": finding.status or ("verified" if finding.verified else "candidate"),
            "reliability": finding.reliability_score,
            "finding_id": finding.id,
        })
    action_context = [
        {"label": action.label or action.action_type, "query": action.data_value or "", "status": action.status}
        for action in actions if action.status != "proposal"
    ]
    subject_names = []
    for action in actions:
        if action.subject and action.subject.name and action.subject.name not in subject_names:
            subject_names.append(action.subject.name)
    if not subject_names:
        subject_names = [subject.name for subject in case.subjects if subject.name]
    canonical_subject_names = [_canonical_person_name(name) for name in subject_names]
    research_question = (investigation.ai_research_question or "").strip()
    prompt = json.dumps({
        "investigation": investigation.title,
        "case": case.case_number,
        "research_question": research_question or "Niet opgeslagen",
        "subject_names": canonical_subject_names,
        "actions": action_context,
        "findings": evidence,
        "possible_accounts_extracted_from_findings": _candidate_account_context(findings),
        "linkedin_profiles_extracted_from_findings": _linkedin_finding_context(findings),
    }, ensure_ascii=False)
    narrative = _generate(
        "Schrijf een helder Nederlandstalig onderzoeksrapport op basis van uitsluitend de onderstaande gegevens. "
        "Begin het informatieproduct met de exacte onderzoeksvraag onder de kop 'Onderzoeksvraag'. "
        "Maak daarna een uitgebreid maar nuchter informatieproduct met deze vaste onderdelen: kernbeeld, "
        "identiteits- en naamvarianten, mogelijke online accounts (gegroepeerd per platform met URL en status), "
        "belangrijkste inhoudelijke bevindingen, mogelijke relaties of associaties, onzekerheden en vervolgstappen. "
        "Als er LinkedIn-findings zijn, analyseer dan ook de daarin vastgelegde profielgegevens en neem concrete "
        "functies, organisaties, opleidingen, locaties, datums, profiel-URL's en genoemde relaties afzonderlijk op. "
        "Leid geen nieuwe gegevens af buiten de vastgelegde LinkedIn-finding; markeer ontbrekende of onzekere gegevens. "
        "Neem ieder account alleen op als kandidaat of geverifieerd volgens de bronstatus. "
        "Verbind iedere belangrijke bewering aan de bijbehorende bron-URL of finding. "
        "Herhaal de onderzoeksvraag niet in je antwoord; die wordt al apart door het systeem geplaatst. "
        "Gebruik de genormaliseerde subjectnaam zonder een losse dubbele voorletter. "
        "Behandel browser_search-items met 'manual review' uitsluitend als nog handmatig uit te voeren zoekopdrachten, "
        "niet als onderzoeksresultaten. "
        "Verzin niets, presenteer kandidaten niet als feiten, en noem bronnen bij de relevante bevindingen. "
        "Gebruik nooit tekst tussen vierkante haken als placeholder. Vervang bekende namen door hun echte naam "
        "en schrijf voor onbekende gegevens dat ze niet zijn vastgesteld. "
        "Dit is een analytische samenvatting en geen zelfstandig bewijs.\n\nGEGEVENS:\n" + prompt,
        "Je bent een zorgvuldige OSINT-analist. Scheid feiten, bronclaims en interpretaties strikt.",
        timeout=90,
        max_tokens=8192,
    )
    if not narrative:
        return jsonify({"error": "De AI kon geen rapport genereren"}), 503
    narrative = _clean_narrative_placeholders(narrative, subject_names)
    investigation.ai_narrative = narrative
    investigation.ai_narrative_generated_at = datetime.now(timezone.utc)
    investigation.ai_narrative_generated_by = current_user.id
    investigation.ai_narrative_finding_count = len(findings)
    _upsert_narrative_in_report(case, investigation, narrative, research_question)
    AuditLog.log(
        user_id=current_user.id,
        action="create",
        entity_type="ai_investigation_narrative",
        entity_id=investigation.id,
        ip_address=request.remote_addr,
        case_id=case_id,
        description=f"Generated AI narrative from {len(findings)} investigation findings and inserted it into the editable report",
    )
    db.session.commit()
    return jsonify({
        "ok": True,
        "narrative": narrative,
        "finding_count": len(findings),
        "stored": True,
        "report_updated": True,
    })


@workflow_bp.route("/api/case/<case_id>/ai-research/start-proposals", methods=["POST"])
@login_required
def start_ai_proposals(case_id):
    """Explicitly start all or selected AI-created proposals for a case."""
    denied = _investigator_required()
    if denied:
        return denied
    case, error = _load_case(case_id)
    if error:
        return error
    body = request.get_json(silent=True) or {}
    requested_ids = body.get("action_ids")
    query = WorkflowResearchAction.query.filter_by(
        case_id=case_id,
        tenant_id=current_user.tenant_id,
        status="proposal",
    )
    if isinstance(requested_ids, list) and requested_ids:
        ids = {str(item) for item in requested_ids}
        proposals = query.filter(WorkflowResearchAction.id.in_(ids)).all()
    else:
        proposals = query.all()
    if not proposals:
        return jsonify({"error": "No startable proposals found for this case"}), 409
    if any(is_paid_action(action.action_type) for action in proposals) and not paid_channels_enabled():
        return jsonify({"error": "Paid research channels require separate tenant approval"}), 409

    started_ids = []
    for action in proposals:
        action.status = "pending"
        AuditLog.log(
            user_id=current_user.id,
            action="start",
            entity_type="research_action",
            entity_id=action.id,
            ip_address=request.remote_addr,
            case_id=case_id,
            description="Explicitly started AI research proposal",
        )
        started_ids.append(action.id)
    db.session.commit()
    for action_id in started_ids:
        start_action_async(action_id)
    return jsonify({"ok": True, "started_ids": started_ids, "started": len(started_ids)})
