import json
import os
import uuid
from datetime import datetime

import sqlalchemy as sa
from flask import (
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)
from flask_login import current_user, login_required

from . import ensure_db, get_session, workflow_bp, WORKFLOW_DB_PATH
from .models import (
    WorkflowActionFinding,
    WorkflowCase,
    WorkflowClient,
    WorkflowFinding,
    WorkflowResearchAction,
    WorkflowScreenshot,
    WorkflowSubject,
)
from .research import (
    ACTION_REGISTRY,
    cancel_action,
    start_action_async,
    get_remaining_credits,
)
from cms.routes.dashboard import _get_cached_health


def _superadmin_required(f):
    from functools import wraps

    @wraps(f)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_super_admin:
            return jsonify({"error": "Forbidden"}), 403
        return f(*args, **kwargs)

    return wrapper


@workflow_bp.route("/")
@login_required
@_superadmin_required
def dashboard():
    ensure_db()
    session = get_session()
    cases = session.query(WorkflowCase).order_by(WorkflowCase.created_at.desc()).all()
    action_counts = {}
    for c in cases:
        action_counts[c.id] = {
            "total": len(c.actions),
            "completed": sum(1 for a in c.actions if a.status == "completed"),
            "running": sum(1 for a in c.actions if a.status == "running"),
        }
    session.close()
    return render_template(
        "cms/workflow/workflow_dashboard.html",
        cases=cases,
        action_counts=action_counts,
        action_types=ACTION_REGISTRY,
    )


@workflow_bp.route("/case/new", methods=["GET", "POST"])
@login_required
@_superadmin_required
def case_new():
    ensure_db()
    session = get_session()
    if request.method == "POST":
        client_id = str(uuid.uuid4())
        case_id = str(uuid.uuid4())

        def _json_field(name, default=None):
            raw = request.form.get(name, "")
            if not raw or raw.strip() == "":
                return default if default is not None else []
            try:
                return json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                return []

        def _social_field(name):
            raw = request.form.get(name, "").strip()
            if not raw:
                return []
            return [p.strip() for p in raw.split(",") if p.strip()]

        def _int_field(name, default=0):
            try:
                return int(request.form.get(name, default) or default)
            except (ValueError, TypeError):
                return default

        client = WorkflowClient(
            id=client_id,
            name=request.form.get("client_name", "Onbekend"),
            contact_person=request.form.get("client_contact", ""),
            email=request.form.get("client_email", ""),
            phone=request.form.get("client_phone", ""),
            reference=request.form.get("reference", ""),
            street=request.form.get("client_street", ""),
            house_number=request.form.get("client_house_number", ""),
            house_number_addition=request.form.get("client_house_number_addition", ""),
            postal_code=request.form.get("client_postal_code", ""),
            city=request.form.get("client_city", ""),
            notes=request.form.get("client_notes", ""),
        )

        subjects = []
        idx = 0
        while request.form.get(f"subject_{idx}_name"):
            s = WorkflowSubject(
                id=str(uuid.uuid4()),
                name=request.form.get(f"subject_{idx}_name", "Onbekend"),
                subject_type=request.form.get(f"subject_{idx}_type", "person"),
                identification_number=request.form.get(
                    f"subject_{idx}_identification", ""
                ),
                email=request.form.get(f"subject_{idx}_email", ""),
                phone=request.form.get(f"subject_{idx}_phone", ""),
                street=request.form.get(f"subject_{idx}_street", ""),
                house_number=request.form.get(f"subject_{idx}_house_number", ""),
                house_number_addition=request.form.get(
                    f"subject_{idx}_house_number_addition", ""
                ),
                postal_code=request.form.get(f"subject_{idx}_postal_code", ""),
                city=request.form.get(f"subject_{idx}_city", ""),
                social_accounts=_social_field(f"subject_{idx}_social_accounts"),
                risk_score=_int_field(f"subject_{idx}_risk_score"),
                notes=request.form.get(f"subject_{idx}_notes", ""),
            )
            subjects.append(s)
            idx += 1
        if not subjects:
            subjects.append(
                WorkflowSubject(
                    id=str(uuid.uuid4()),
                    name=request.form.get("subject_name", "Onbekend"),
                    subject_type=request.form.get("subject_type", "person"),
                    identification_number=request.form.get("identification", ""),
                    email=request.form.get("subject_email", ""),
                    phone=request.form.get("subject_phone", ""),
                    street=request.form.get("subject_street", ""),
                    house_number=request.form.get("subject_house_number", ""),
                    house_number_addition=request.form.get(
                        "subject_house_number_addition", ""
                    ),
                    postal_code=request.form.get("subject_postal_code", ""),
                    city=request.form.get("subject_city", ""),
                    social_accounts=_social_field("social_accounts"),
                    risk_score=_int_field("risk_score"),
                    notes=request.form.get("subject_notes", ""),
                )
            )

        case = WorkflowCase(
            id=case_id,
            case_number=request.form.get(
                "case_number", f"W-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
            ),
            title=request.form.get("title", "Nieuw onderzoek"),
            status="open",
            priority=request.form.get("priority", "medium"),
            description=request.form.get("description", ""),
            client_id=client_id,
            lead_investigator=current_user.full_name or current_user.email,
        )
        session.add(client)
        session.add(case)
        for s in subjects:
            session.add(s)
            case.subjects.append(s)
        session.commit()
        session.close()
        return redirect(url_for("workflow.case_detail", case_id=case_id))

    session.close()
    return render_template("cms/workflow/workflow_case_new.html")


@workflow_bp.route("/case/<case_id>")
@login_required
@_superadmin_required
def case_detail(case_id):
    ensure_db()
    session = get_session()
    case = session.query(WorkflowCase).get(case_id)
    if not case:
        session.close()
        from flask import abort

        abort(404)
    actions = (
        session.query(WorkflowResearchAction)
        .filter_by(case_id=case_id)
        .order_by(WorkflowResearchAction.created_at.desc())
        .all()
    )
    findings = (
        session.query(WorkflowFinding)
        .filter_by(case_id=case_id)
        .filter(WorkflowFinding.archived_at.is_(None))
        .options(sa.orm.joinedload(WorkflowFinding.screenshots))
        .order_by(WorkflowFinding.created_at.desc())
        .all()
    )

    finding_ids = [f.id for f in findings]
    links = (
        (
            session.query(WorkflowActionFinding)
            .filter(WorkflowActionFinding.finding_id.in_(finding_ids))
            .all()
        )
        if finding_ids
        else []
    )
    finding_actions = {}
    for link in links:
        finding_actions.setdefault(link.finding_id, []).append(link.action_id)

    client = (
        session.query(WorkflowClient).get(case.client_id) if case.client_id else None
    )
    subjects = list(case.subjects)
    subjects_data = [
        {
            "name": s.name,
            "subject_type": s.subject_type,
            "email": s.email,
            "phone": s.phone,
            "street": s.street,
            "house_number": s.house_number,
            "house_number_addition": s.house_number_addition,
            "postal_code": s.postal_code,
            "city": s.city,
            "social_accounts": s.social_accounts,
            "identification_number": s.identification_number,
        }
        for s in subjects
        if s
    ]
    session.close()
    brave_health = _get_cached_health().get("brave", "no key configured")
    action_credits = {}
    for key in ACTION_REGISTRY:
        action_credits[key] = get_remaining_credits(key)
    return render_template(
        "cms/workflow/workflow_case_detail.html",
        case=case,
        client=client,
        subjects=subjects,
        subjects_data=subjects_data,
        actions=actions,
        findings=findings,
        finding_actions=finding_actions,
        action_types=ACTION_REGISTRY,
        action_credits=action_credits,
        brave_health=brave_health,
    )


@workflow_bp.route("/case/<case_id>/edit", methods=["GET", "POST"])
@login_required
@_superadmin_required
def case_edit(case_id):
    ensure_db()
    session = get_session()
    case = session.query(WorkflowCase).get(case_id)
    if not case:
        session.close()
        from flask import abort

        abort(404)

    client = (
        session.query(WorkflowClient).get(case.client_id) if case.client_id else None
    )
    subjects = list(case.subjects)

    if request.method == "POST":

        def _json_field(name, default=None):
            raw = request.form.get(name, "")
            if not raw or raw.strip() == "":
                return default if default is not None else []
            try:
                return json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                return []

        def _social_field(name):
            raw = request.form.get(name, "").strip()
            if not raw:
                return []
            return [p.strip() for p in raw.split(",") if p.strip()]

        def _int_field(name, default=0):
            try:
                return int(request.form.get(name, default) or default)
            except (ValueError, TypeError):
                return default

        # update client
        if client:
            client.name = request.form.get("client_name", client.name)
            client.contact_person = request.form.get("client_contact", "")
            client.email = request.form.get("client_email", "")
            client.phone = request.form.get("client_phone", "")
            client.reference = request.form.get("reference", "")
            client.street = request.form.get("client_street", "")
            client.house_number = request.form.get("client_house_number", "")
            client.house_number_addition = request.form.get(
                "client_house_number_addition", ""
            )
            client.postal_code = request.form.get("client_postal_code", "")
            client.city = request.form.get("client_city", "")
            client.notes = request.form.get("client_notes", "")

        # update case
        case.case_number = request.form.get("case_number", case.case_number)
        case.title = request.form.get("title", case.title)
        case.status = request.form.get("status", case.status)
        case.priority = request.form.get("priority", case.priority)
        case.description = request.form.get("description", "")

        # figure out which subject IDs to keep
        existing_ids_raw = request.form.get("existing_subject_ids", "[]")
        try:
            keep_ids = set(json.loads(existing_ids_raw))
        except (json.JSONDecodeError, TypeError):
            keep_ids = set()

        removed_ids_raw = request.form.get("removed_subject_ids", "[]")
        try:
            removed_ids = set(json.loads(removed_ids_raw))
        except (json.JSONDecodeError, TypeError):
            removed_ids = set()

        # process existing subjects
        for sid in list(keep_ids):
            if not sid:
                continue
            subj = session.query(WorkflowSubject).get(sid)
            if not subj:
                continue
            subj.name = request.form.get(f"subj_{sid}_name", subj.name)
            subj.subject_type = request.form.get(f"subj_{sid}_type", "person")
            subj.identification_number = request.form.get(
                f"subj_{sid}_identification", ""
            )
            subj.email = request.form.get(f"subj_{sid}_email", "")
            subj.phone = request.form.get(f"subj_{sid}_phone", "")
            subj.street = request.form.get(f"subj_{sid}_street", "")
            subj.house_number = request.form.get(f"subj_{sid}_house_number", "")
            subj.house_number_addition = request.form.get(
                f"subj_{sid}_house_number_addition", ""
            )
            subj.postal_code = request.form.get(f"subj_{sid}_postal_code", "")
            subj.city = request.form.get(f"subj_{sid}_city", "")
            subj.social_accounts = _social_field(f"subj_{sid}_social_accounts")
            subj.risk_score = _int_field(f"subj_{sid}_risk_score")
            subj.notes = request.form.get(f"subj_{sid}_notes", "")

        # process new subjects
        n = 0
        while request.form.get(f"subj_new_{n}_name"):
            new_subj = WorkflowSubject(
                id=str(uuid.uuid4()),
                name=request.form.get(f"subj_new_{n}_name", "Onbekend"),
                subject_type=request.form.get(f"subj_new_{n}_type", "person"),
                identification_number=request.form.get(
                    f"subj_new_{n}_identification", ""
                ),
                email=request.form.get(f"subj_new_{n}_email", ""),
                phone=request.form.get(f"subj_new_{n}_phone", ""),
                street=request.form.get(f"subj_new_{n}_street", ""),
                house_number=request.form.get(f"subj_new_{n}_house_number", ""),
                house_number_addition=request.form.get(
                    f"subj_new_{n}_house_number_addition", ""
                ),
                postal_code=request.form.get(f"subj_new_{n}_postal_code", ""),
                city=request.form.get(f"subj_new_{n}_city", ""),
                social_accounts=_social_field(f"subj_new_{n}_social_accounts"),
                risk_score=_int_field(f"subj_new_{n}_risk_score"),
                notes=request.form.get(f"subj_new_{n}_notes", ""),
            )
            session.add(new_subj)
            case.subjects.append(new_subj)
            n += 1

        # remove unlinked subjects
        for sid in list(removed_ids):
            if not sid:
                continue
            subj = session.query(WorkflowSubject).get(sid)
            if subj and subj in case.subjects:
                case.subjects.remove(subj)

        session.commit()
        session.close()
        return redirect(url_for("workflow.case_detail", case_id=case_id))

    session.close()
    return render_template(
        "cms/workflow/workflow_case_edit.html",
        case=case,
        client=client,
        subjects=subjects,
    )


@workflow_bp.route("/api/case/<case_id>/run-action", methods=["POST"])
@login_required
@_superadmin_required
def run_action(case_id):
    ensure_db()
    session = get_session()
    case = session.query(WorkflowCase).get(case_id)
    if not case:
        session.close()
        return jsonify({"error": "Case not found"}), 404

    body = request.get_json(silent=True) or {}
    action_type = body.get("action_type", "")
    data_value = body.get("data_value") or ""

    if action_type not in ACTION_REGISTRY:
        session.close()
        return jsonify({"error": f"Unknown action: {action_type}"}), 400

    _STALE_TIMEOUT = 600  # 10 minutes
    existing = (
        session.query(WorkflowResearchAction)
        .filter_by(case_id=case_id, action_type=action_type, status="running")
        .first()
    )
    if existing:
        if (
            existing.started_at
            and (datetime.now() - existing.started_at).total_seconds() > _STALE_TIMEOUT
        ):
            existing.status = "error"
            existing.error = "Stale action auto-reset (timed out)"
            session.commit()
        else:
            session.close()
            return jsonify({"error": "Action already running"}), 409

    action = WorkflowResearchAction(
        id=str(uuid.uuid4()),
        case_id=case_id,
        action_type=action_type,
        data_value=data_value,
        label=ACTION_REGISTRY[action_type]["label"],
        status="pending",
    )
    session.add(action)
    session.commit()
    action_id = action.id
    session.close()

    start_action_async(action_id)
    return jsonify({"id": action_id, "status": "started"})


@workflow_bp.route("/api/case/<case_id>/status")
@login_required
@_superadmin_required
def case_status(case_id):
    ensure_db()
    session = get_session()
    case = session.query(WorkflowCase).get(case_id)
    if not case:
        session.close()
        return jsonify({"error": "Not found"}), 404

    actions = session.query(WorkflowResearchAction).filter_by(case_id=case_id).all()
    findings = (
        session.query(WorkflowFinding)
        .filter_by(case_id=case_id)
        .filter(WorkflowFinding.archived_at.is_(None))
        .options(sa.orm.joinedload(WorkflowFinding.screenshots))
        .order_by(WorkflowFinding.created_at.desc())
        .all()
    )

    finding_ids = [f.id for f in findings]
    links = (
        (
            session.query(WorkflowActionFinding)
            .filter(WorkflowActionFinding.finding_id.in_(finding_ids))
            .all()
        )
        if finding_ids
        else []
    )
    finding_actions = {}
    for link in links:
        finding_actions.setdefault(link.finding_id, []).append(link.action_id)

    session.close()

    def finding_json(f):
        raw = f.raw_data
        if raw is not None and not isinstance(raw, dict):
            try:
                import json as _json

                raw = _json.loads(raw)
            except Exception:
                raw = None
        return {
            "id": f.id,
            "title": f.title,
            "detail": f.detail,
            "source_url": f.source_url,
            "source_type": f.source_type,
            "icon": f.icon,
            "verified": f.verified,
            "comment": f.comment,
            "raw_data": raw,
            "created_at": f.created_at.isoformat() if f.created_at else None,
            "action_ids": finding_actions.get(f.id, []),
            "screenshots": [
                {
                    "url": ss.url,
                    "source_url": ss.source_url,
                    "captured_at": ss.captured_at.isoformat()
                    if ss.captured_at
                    else None,
                    "notes": ss.notes,
                }
                for ss in (f.screenshots or [])
            ],
        }

    return jsonify(
        {
            "actions": [
                {
                    "id": a.id,
                    "action_type": a.action_type,
                    "label": a.label,
                    "status": a.status,
                    "error": a.error,
                    "result_summary": a.result_summary,
                    "created_at": a.created_at.isoformat() if a.created_at else None,
                    "started_at": a.started_at.isoformat() if a.started_at else None,
                    "completed_at": a.completed_at.isoformat()
                    if a.completed_at
                    else None,
                }
                for a in actions
            ],
            "findings": [finding_json(f) for f in findings],
            "finding_count": len(findings),
        }
    )


@workflow_bp.route("/api/case/<case_id>/delete", methods=["POST"])
@login_required
@_superadmin_required
def delete_case(case_id):
    ensure_db()
    session = get_session()
    case = session.query(WorkflowCase).get(case_id)
    if not case:
        session.close()
        return jsonify({"error": "Not found"}), 404
    session.delete(case)
    session.commit()
    session.close()
    return jsonify({"ok": True})


@workflow_bp.route("/case/<case_id>/pv")
@login_required
@_superadmin_required
def pv_view(case_id):
    ensure_db()
    session = get_session()
    case = session.query(WorkflowCase).get(case_id)
    if not case:
        session.close()
        abort(404)
    client = (
        session.query(WorkflowClient).get(case.client_id) if case.client_id else None
    )
    subjects = list(case.subjects)
    findings = list(case.findings)
    session.close()

    import markdown as md_lib

    body_html = md_lib.markdown(case.pv_body or "") if case.pv_body else ""

    return render_template(
        "cms/workflow/workflow_pv.html",
        case=case,
        client=client,
        subjects=subjects,
        findings=findings,
        body_html=body_html,
    )


@workflow_bp.route("/case/<case_id>/pv/edit", methods=["GET", "POST"])
@login_required
@_superadmin_required
def pv_edit(case_id):
    ensure_db()
    session = get_session()
    case = session.query(WorkflowCase).get(case_id)
    if not case:
        session.close()
        abort(404)

    if request.method == "POST":
        case.pv_body = request.form.get("pv_body", "")
        session.commit()
        session.close()
        return redirect(url_for("workflow.pv_view", case_id=case_id))

    session.close()
    return render_template("cms/workflow/workflow_pv_edit.html", case=case)


@workflow_bp.route("/api/case/<case_id>/findings/<finding_id>/delete", methods=["POST"])
@login_required
@_superadmin_required
def delete_finding(case_id, finding_id):
    ensure_db()
    session = get_session()
    finding = session.query(WorkflowFinding).get(finding_id)
    if not finding or finding.case_id != case_id:
        session.close()
        return jsonify({"error": "Not found"}), 404
    session.delete(finding)
    session.commit()
    session.close()
    return jsonify({"ok": True})


@workflow_bp.route("/api/case/<case_id>/findings/batch-delete", methods=["POST"])
@login_required
@_superadmin_required
def batch_delete_findings(case_id):
    ensure_db()
    session = get_session()
    body = request.get_json(silent=True) or {}
    ids = body.get("ids", [])
    if not ids or not isinstance(ids, list):
        session.close()
        return jsonify({"error": "No IDs provided"}), 400
    deleted = 0
    for fid in ids:
        finding = session.query(WorkflowFinding).get(fid)
        if finding and finding.case_id == case_id:
            session.delete(finding)
            deleted += 1
    session.commit()
    session.close()
    return jsonify({"ok": True, "deleted": deleted})


@workflow_bp.route("/api/case/<case_id>/findings/<finding_id>/verify", methods=["POST"])
@login_required
@_superadmin_required
def verify_finding(case_id, finding_id):
    ensure_db()
    session = get_session()
    finding = session.query(WorkflowFinding).get(finding_id)
    if not finding or finding.case_id != case_id:
        session.close()
        return jsonify({"error": "Not found"}), 404
    body = request.get_json(silent=True) or {}
    new_val = body.get("verified", not finding.verified)
    finding.verified = new_val
    session.commit()
    session.close()
    return jsonify({"ok": True, "verified": new_val})


@workflow_bp.route(
    "/api/case/<case_id>/findings/<finding_id>/comment", methods=["POST"]
)
@login_required
@_superadmin_required
def save_comment(case_id, finding_id):
    ensure_db()
    session = get_session()
    finding = session.query(WorkflowFinding).get(finding_id)
    if not finding or finding.case_id != case_id:
        session.close()
        return jsonify({"error": "Not found"}), 404
    body = request.get_json(silent=True) or {}
    new_comment = body.get("comment", "")
    finding.comment = new_comment
    session.commit()
    session.close()
    return jsonify({"ok": True, "comment": new_comment})


@workflow_bp.route("/api/case/<case_id>/actions/<action_id>/cancel", methods=["POST"])
@login_required
@_superadmin_required
def cancel_action_api(case_id, action_id):
    ensure_db()
    session = get_session()
    action = session.query(WorkflowResearchAction).get(action_id)
    if not action or action.case_id != case_id:
        session.close()
        return jsonify({"error": "Not found"}), 404
    if action.status != "running":
        session.close()
        return jsonify({"error": "Action is not running"}), 400
    cancel_action(action_id)
    session.close()
    return jsonify({"ok": True})


SCREENSHOT_DIR = os.path.join(os.path.dirname(WORKFLOW_DB_PATH), "screenshots")


@workflow_bp.route(
    "/api/case/<case_id>/findings/<finding_id>/screenshots", methods=["POST"]
)
@login_required
@_superadmin_required
def add_screenshot(case_id, finding_id):
    ensure_db()
    session = get_session()
    finding = session.query(WorkflowFinding).get(finding_id)
    if not finding or finding.case_id != case_id:
        session.close()
        return jsonify({"error": "Not found"}), 404

    url = ""
    source_url = ""
    notes = ""
    file_path = ""
    uploaded_filename = ""

    if request.content_type and "multipart/form-data" in request.content_type:
        source_url = request.form.get("source_url", "")
        notes = request.form.get("notes", "")
        file = request.files.get("file")
        if file and file.filename:
            ext = os.path.splitext(file.filename)[1] or ".png"
            stored_name = f"{uuid.uuid4()}{ext}"
            finding_dir = os.path.join(SCREENSHOT_DIR, finding_id)
            os.makedirs(finding_dir, exist_ok=True)
            dest = os.path.join(finding_dir, stored_name)
            file.save(dest)
            file_path = dest
            uploaded_filename = stored_name
            url = url_for(
                "workflow.serve_screenshot",
                finding_id=finding_id,
                filename=stored_name,
                _external=False,
            )
    else:
        body = request.get_json(silent=True) or {}
        url = body.get("url", "")
        source_url = body.get("source_url", "")
        notes = body.get("notes", "")

    ss = WorkflowScreenshot(
        id=str(uuid.uuid4()),
        finding_id=finding_id,
        url=url,
        source_url=source_url,
        file_path=file_path,
        notes=notes,
        captured_at=datetime.now(),
    )
    session.add(ss)
    session.commit()
    ss_data = {
        "url": ss.url,
        "source_url": ss.source_url,
        "captured_at": ss.captured_at.isoformat() if ss.captured_at else None,
        "notes": ss.notes,
    }
    session.close()
    return jsonify({"ok": True, "screenshot": ss_data})


@workflow_bp.route("/uploads/<finding_id>/<filename>")
def serve_screenshot(finding_id, filename):
    finding_dir = os.path.join(SCREENSHOT_DIR, finding_id)
    return send_from_directory(finding_dir, filename)
