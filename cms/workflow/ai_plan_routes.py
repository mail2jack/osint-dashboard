"""Safe AI research planning routes.

The first MVP deliberately stops at approved research-action proposals. It
never starts an action automatically.
"""

import json
import uuid
from datetime import datetime, timezone

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


def _build_plan(query, parsed):
    search_type = parsed.get("type") if isinstance(parsed, dict) else None
    normalized_type = search_type if search_type in _PLAN_ACTIONS else "name"
    actions = []
    for action_type in _PLAN_ACTIONS[normalized_type]:
        info = ACTION_REGISTRY.get(action_type)
        if not info or is_paid_action(action_type):
            continue
        actions.append(
            {
                "action_type": action_type,
                "label": info["label"],
                "description": info["description"],
                "category": info["category"],
                "cost_label": info.get("cost_label", ""),
            }
        )
    return {
        "id": str(uuid.uuid4()),
        "query": query,
        "interpretation": {
            "type": normalized_type,
            "value": parsed.get("query", query) if isinstance(parsed, dict) else query,
            "confidence": parsed.get("confidence", 0) if isinstance(parsed, dict) else 0,
        },
        "actions": actions,
        "policy": {
            "execution": "approval_required",
            "paid_channels": "not_proposed",
            "automatic_verification": False,
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

    config = get_ai_config()
    if not config.get("available") or not check_ai_available():
        return jsonify({"error": "No AI provider is configured or available"}), 503

    parsed = analyze_natural_language(query, list(_PLAN_ACTIONS))
    plan = _build_plan(query, parsed)
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
    return jsonify({"plan": plan, "provider": config.get("provider"), "model": config.get("model")})


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

    allowed = set(_PLAN_ACTIONS.get((plan.get("interpretation") or {}).get("type"), []))
    selected = [str(item) for item in selected]
    if any(item not in allowed or item not in ACTION_REGISTRY for item in selected):
        return jsonify({"error": "The plan contains an action outside the MVP allowlist"}), 400
    if any(is_paid_action(item) for item in selected) or any(
        not paid_channels_enabled(current_user.tenant_id) and is_paid_action(item)
        for item in selected
    ):
        return jsonify({"error": "Paid actions require separate tenant approval"}), 409

    subject_id = body.get("subject_id") or None
    investigation_id = body.get("investigation_id") or None
    subject = None
    if subject_id:
        subject = db.session.get(WorkflowSubject, subject_id)
        if not subject or not case.subjects.filter_by(id=subject_id).first():
            return jsonify({"error": "Subject is not linked to this case"}), 400
    _, err, err_status = get_linkable_investigation(
        investigation_id, case=case, tenant_id=current_user.tenant_id
    )
    if err:
        return jsonify({"error": err}), err_status

    created = []
    for action_type in selected:
        action = WorkflowResearchAction(
            id=str(uuid.uuid4()),
            case_id=case_id,
            subject_id=subject_id,
            investigation_id=investigation_id,
            target_kind="subject" if subject_id else "case",
            action_type=action_type,
            data_value=query,
            label=ACTION_REGISTRY[action_type]["label"],
            status="proposal",
            tenant_id=current_user.tenant_id,
            created_by=current_user.id,
        )
        action.target_snapshot = json.dumps(action.build_target_snapshot(subject, query))
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
    for finding in findings[:20]:
        evidence.append({
            "title": finding.title,
            "content": (finding.content or "")[:900],
            "detail": (finding.detail or "")[:600],
            "source": finding.source_url or finding.source_type or "onbekende bron",
            "status": finding.status or ("verified" if finding.verified else "candidate"),
            "reliability": finding.reliability_score,
        })
    action_context = [
        {"label": action.label or action.action_type, "query": action.data_value or "", "status": action.status}
        for action in actions if action.status != "proposal"
    ]
    prompt = json.dumps({"investigation": investigation.title, "case": case.case_number, "actions": action_context, "findings": evidence}, ensure_ascii=False)
    narrative = _generate(
        "Schrijf een helder Nederlandstalig onderzoeksverhaal op basis van uitsluitend de onderstaande gegevens. "
        "Gebruik korte alinea's met: kernbeeld, belangrijkste bevindingen, onzekerheden en vervolgstappen. "
        "Verzin niets, presenteer kandidaten niet als feiten, en noem bronnen bij de relevante bevindingen. "
        "Dit is een analytische samenvatting en geen zelfstandig bewijs.\n\nGEGEVENS:\n" + prompt,
        "Je bent een zorgvuldige OSINT-analist. Scheid feiten, bronclaims en interpretaties strikt.",
        timeout=90,
        max_tokens=8192,
    )
    if not narrative:
        return jsonify({"error": "De AI kon geen verhaal genereren"}), 503
    investigation.ai_narrative = narrative
    investigation.ai_narrative_generated_at = datetime.now(timezone.utc)
    investigation.ai_narrative_generated_by = current_user.id
    investigation.ai_narrative_finding_count = len(findings)
    AuditLog.log(
        user_id=current_user.id,
        action="create",
        entity_type="ai_investigation_narrative",
        entity_id=investigation.id,
        ip_address=request.remote_addr,
        case_id=case_id,
        description=f"Generated AI narrative from {len(findings)} investigation findings",
    )
    db.session.commit()
    return jsonify({"ok": True, "narrative": narrative, "finding_count": len(findings), "stored": True})


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
