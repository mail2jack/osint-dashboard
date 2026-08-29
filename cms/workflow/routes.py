import json
import logging
import os
import uuid
from datetime import UTC, datetime

import bleach
import sqlalchemy as sa
from flask import (
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)
from flask_babel import gettext as _
from flask_login import current_user, login_required

from cms.auth import ensure_case_access, ensure_tenant_access
from cms.models import (
    AuditLog,
    Investigation,
    InvestigationStatus,
    User,
    UserRole,
    db,
    report_include_filter,
)
from cms.routes.dashboard import _get_cached_health
from cms.routes.utils import find_similar_clients, normalize_phone, normalize_postcode
from cms.services.invoice_service import auto_invoice_case_created
from cms.services.sequence_service import (
    create_investigation as sequence_create_investigation,
)
from cms.services.subject_service import subject_service
from cms.workflow.actions.registry import action_category

from . import workflow_bp
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
    SUBJECT_TYPE_PRESETS,
    cancel_action,
    get_remaining_credits,
    is_paid_action,
    paid_channels_enabled,
    start_action_async,
)

logger = logging.getLogger(__name__)

_INVESTIGATOR_ROLES = (
    UserRole.INVESTIGATOR.value,
    UserRole.SENIOR_INVESTIGATOR.value,
    UserRole.ADMIN.value,
    UserRole.OWNER.value,
)


def _current_user_is_investigator() -> bool:
    """Writer check for the investigations section (role-based, matches scope)."""
    return current_user.is_authenticated and current_user.role in _INVESTIGATOR_ROLES


def _load_case_investigations(case_id: str, show_archived: bool):
    """Load investigations for a case (archived filtered unless shown) + creator names."""
    investigations = (
        Investigation.query.filter_by(case_id=case_id)
        .filter(
            Investigation.archived_at.is_(None)
            if not show_archived
            else sa.true()
        )
        .order_by(Investigation.created_at.desc(), Investigation.sequence_no.desc())
        .all()
    )
    creator_ids = {inv.created_by for inv in investigations if inv.created_by}
    created_by_names = {}
    if creator_ids:
        for u in User.query.filter(User.id.in_(creator_ids)).all():
            created_by_names[u.id] = u.username or u.full_name or u.id
    return investigations, created_by_names


def _investigator_required(f):
    from functools import wraps

    @wraps(f)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated:
            return jsonify({"error": "Forbidden"}), 403
        if current_user.role not in _INVESTIGATOR_ROLES:
            return jsonify({"error": "Forbidden"}), 403
        return f(*args, **kwargs)

    return wrapper


def _combine_address_number(number: str, addition: str) -> str:
    """Combine house number and addition into a single field (e.g. '45A')."""
    n = number.strip()
    a = addition.strip()
    if not n and not a:
        return ""
    if not a:
        return n
    return n + a


_VEHICLE_RDW_FIELDS = [
    "handelsbenaming",
    "voertuigsoort",
    "eerste_kleur",
    "tweede_kleur",
    "aantal_deuren",
    "aantal_zitplaatsen",
    "cilinderinhoud",
    "aantal_cilinders",
    "vermogen",
    "massa_ledig",
    "maximum_massa",
    "wielbasis",
    "datum_eerste_toelating",
    "datum_tenaamstelling",
    "vervaldatum_apk",
    "europese_voertuigcategorie",
    "wam_verzekerd",
    "catalogusprijs",
    "bruto_bpm",
    "zuinigheidsclassificatie",
    "typegoedkeuringsnummer",
    "taxi_indicator",
    "export_indicator",
    "openstaande_terugroepactie",
    "rdw_type",
    "variant",
    "uitvoering",
]


def _vehicle_auto_name(prefix: str) -> str:
    """Build a display name for a vehicle from form fields."""
    brand = request.form.get(f"{prefix}_brand", "").strip()
    model = request.form.get(f"{prefix}_handelsbenaming", "").strip()
    kenteken = request.form.get(f"{prefix}_identification", "").strip()
    parts = [p for p in [brand, model] if p]
    if kenteken:
        parts.append(f"({kenteken})")
    return " ".join(parts) if parts else "Voertuig"


def _wf_value(prefix: str, name: str, default: str = "") -> str:
    """Read a workflow form field named ``<prefix>_<name>``."""
    value = request.form.get(f"{prefix}_{name}", default)
    return value if isinstance(value, str) else str(value)


def _wf_json_list(prefix: str, name: str):
    """Parse a JSON list from ``<prefix>_<name>``; ``None`` when absent/empty."""
    raw = request.form.get(f"{prefix}_{name}", "")
    if raw is None or str(raw).strip() == "":
        return None
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    return parsed if isinstance(parsed, list) else None


def _wf_social_list(prefix: str) -> list:
    """Comma-separated social handles from ``<prefix>_social_accounts``."""
    raw = request.form.get(f"{prefix}_social_accounts", "").strip()
    if not raw:
        return []
    return [
        p.strip() if p.strip().startswith("@") else "@" + p.strip()
        for p in raw.split(",")
        if p.strip()
    ]


def _wf_int(prefix: str, name: str, default: int = 0) -> int:
    """Integer value from ``<prefix>_<name>``."""
    try:
        return int(request.form.get(f"{prefix}_{name}", default) or default)
    except (ValueError, TypeError):
        return default


def _wf_addresses(prefix: str):
    """Address rows for a subject: serialized JSON, else the flat fields."""
    serialized = _wf_json_list(prefix, "addresses_data")
    if serialized is not None:
        return serialized
    street = _wf_value(prefix, "street")
    number = _wf_value(prefix, "house_number")
    zipcode = _wf_value(prefix, "postal_code")
    town = _wf_value(prefix, "city")
    if not (street or number or zipcode or town):
        return None
    return [
        {
            "street": street,
            "number": number,
            "addition": _wf_value(prefix, "house_number_addition"),
            "zipcode": zipcode,
            "town": town,
            "country": "Netherlands",
            "is_primary": True,
        }
    ]


def _wf_contacts(prefix: str):
    """Contact rows for a subject: serialized JSON, else the flat fields."""
    serialized = _wf_json_list(prefix, "contacts_data")
    if serialized is not None:
        return serialized
    email = _wf_value(prefix, "email")
    phone = _wf_value(prefix, "phone")
    contacts = []
    if email:
        contacts.append({"contact_type": "email", "value": email, "is_primary": True})
    if phone:
        contacts.append({"contact_type": "phone", "value": phone, "is_primary": False})
    return contacts or None


def _wf_subject_data(prefix: str, fallback_type: str | None = None) -> dict:
    """Build subject data (normalizer) from workflow form fields.

    Mirrors the legacy ``_make_subject`` field handling so the workflow input
    path produces the same records as the standalone subject CRUD routes; all
    persistence goes through ``subject_service``.
    """
    f = request.form
    subject_type = (f.get(f"{prefix}_type") or "").strip() or fallback_type or "person"
    data = {"subject_type": subject_type}

    if subject_type == "person":
        data["achternaam"] = _wf_value(prefix, "name")
        for field in (
            "voornamen",
            "voorletters",
            "tussenvoegsels",
            "geslacht",
            "date_of_birth",
            "place_of_birth",
            "nationality",
            "bsn_number",
            "reisdocument_type",
            "reisdocument_nummer",
        ):
            data[field] = _wf_value(prefix, field)
    else:
        name = _wf_value(prefix, "name")
        if subject_type == "vehicle" and not name:
            name = _vehicle_auto_name(prefix)
        data["name"] = name

    if subject_type == "vehicle":
        data["license_plate"] = _wf_value(prefix, "identification")
        for field in ("brand", "vehicle_type", "vin", "insurance_company"):
            value = _wf_value(prefix, field)
            if value:
                data[field] = value
        for field in _VEHICLE_RDW_FIELDS:
            # Set unconditionally so cleared inputs persist as empty (round-trip).
            data[field] = _wf_value(prefix, field)
    elif subject_type == "vessel":
        for field in ("imo_number", "mmsi", "eni_number", "vessel_nationality"):
            value = _wf_value(prefix, field)
            if value:
                data[field] = value
    elif subject_type == "company":
        for field in ("registration_number", "legal_form", "asset_type"):
            value = _wf_value(prefix, field)
            if value:
                data[field] = value

    socials = _wf_social_list(prefix)
    if socials:
        data["social_accounts"] = socials

    data["bank_account"] = _wf_value(prefix, "bank_account")
    data["notes"] = _wf_value(prefix, "notes")
    data["risk_score"] = _wf_int(prefix, "risk_score")

    addresses = _wf_addresses(prefix)
    if addresses is not None:
        data["addresses_data"] = addresses

    contacts = _wf_contacts(prefix)
    if contacts is not None:
        data["contacts_data"] = contacts

    return data


SCREENSHOT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "instance",
    "finding_screenshots",
)


@workflow_bp.route("/")
@login_required
@_investigator_required
def dashboard():
    cases = (
        WorkflowCase.query.filter(
            WorkflowCase.archived_at.is_(None),
            WorkflowCase.is_deleted == False,
            WorkflowCase.tenant_id == current_user.tenant_id,
        )
        .order_by(WorkflowCase.created_at.desc())
        .all()
    )
    case_ids = [c.id for c in cases]
    action_counts = {
        cid: {"total": 0, "completed": 0, "running": 0} for cid in case_ids
    }
    if case_ids:
        rows = (
            db.session.query(
                WorkflowResearchAction.case_id,
                WorkflowResearchAction.status,
                db.func.count(WorkflowResearchAction.id),
            )
            .filter(WorkflowResearchAction.case_id.in_(case_ids))
            .group_by(WorkflowResearchAction.case_id, WorkflowResearchAction.status)
            .all()
        )
        for case_id, status, cnt in rows:
            if status == "completed":
                action_counts[case_id]["completed"] = cnt
            elif status == "running":
                action_counts[case_id]["running"] = cnt
            action_counts[case_id]["total"] += cnt
    return render_template(
        "cms/workflow/workflow_dashboard.html",
        cases=cases,
        action_counts=action_counts,
        action_types=ACTION_REGISTRY,
    )


def _findings_accessible_case_ids():
    """Case IDs the current user may read + the matching Case objects.

    Reuses the canonical bulk case-isolation rule (admins/super bypass,
    others via direct assignment / case_assignments / involvement).
    """
    from cms.auth import get_accessible_case_ids

    accessible_ids = set(get_accessible_case_ids(current_user))
    if not accessible_ids:
        return [], []
    cases = (
        WorkflowCase.query.filter(
            WorkflowCase.id.in_(accessible_ids),
            WorkflowCase.archived_at.is_(None),
        )
        .order_by(WorkflowCase.created_at.desc())
        .all()
    )
    return [c.id for c in cases], cases


def _findings_base_query(case_ids=None):
    q = WorkflowFinding.query.filter(WorkflowFinding.is_deleted == False)  # noqa: E712
    if case_ids:
        q = q.filter(WorkflowFinding.case_id.in_(case_ids))
    return q


def _finding_json_with_context(f, case=None, subject=None):
    raw = f.raw_data
    if raw is not None and not isinstance(raw, dict):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = None
    return {
        "id": f.id,
        "case_id": f.case_id,
        "subject_id": f.subject_id,
        "title": f.title,
        "detail": f.detail,
        "source_url": f.source_url,
        "source_type": f.source_type,
        "icon": f.icon,
        "verified": f.verified,
        "status": f.status,
        "verified_by": f.verified_by,
        "verified_at": f.verified_at.isoformat() if f.verified_at else None,
        "verifier_name": f.verifier.full_name if f.verifier else None,
        "content_hash": f.content_hash,
        "integrity_verified": f.verify_integrity() if f.content_hash else None,
        "confidence_level": f.confidence_level,
        "archived_at": f.archived_at.isoformat() if f.archived_at else None,
        "comment": f.comment,
        "include_in_report": f.include_in_report,
        "raw_data": raw,
        "created_at": f.created_at.isoformat() if f.created_at else None,
        "case_number": case.case_number if case else None,
        "case_title": case.title if case else None,
        "subject_name": subject.name if subject else None,
        "screenshots": [
            {
                "url": ss.url,
                "source_url": ss.source_url,
                "captured_at": ss.captured_at.isoformat() if ss.captured_at else None,
                "notes": ss.notes,
            }
            for ss in (f.finding_screenshots or [])
        ],
    }


@workflow_bp.route("/findings")
@login_required
@_investigator_required
def findings_index():
    """Central findings register across all accessible investigations."""
    case_ids, all_cases = _findings_accessible_case_ids()

    f_case_id = request.args.get("case_id", "").strip()
    f_subject_id = request.args.get("subject_id", "").strip()
    f_status = request.args.get("status", "").strip()
    f_report = request.args.get("report", "").strip()  # in|out|all
    f_q = request.args.get("q", "").strip()
    show_archived = request.args.get("show_archived") == "1"
    page = request.args.get("page", 1, type=int)
    per_page = 50

    cases = [c for c in all_cases if c.id in case_ids]
    cid = f_case_id if f_case_id in case_ids else None
    scope_ids = [cid] if cid else case_ids

    q = _findings_base_query(scope_ids)
    if not show_archived:
        q = q.filter(WorkflowFinding.archived_at.is_(None))
    if f_status:
        q = q.filter(WorkflowFinding.status == f_status)
    if f_report == "in":
        q = q.filter(
            sa.or_(
                WorkflowFinding.include_in_report.is_(None),
                WorkflowFinding.include_in_report == True,
            )  # noqa: E712
        )
    elif f_report == "out":
        q = q.filter(WorkflowFinding.include_in_report == False)  # noqa: E712
    if f_q:
        like = f"%{f_q.lower()}%"
        q = q.filter(
            sa.or_(
                db.func.lower(WorkflowFinding.title).like(like),
                db.func.lower(WorkflowFinding.content).like(like),
                db.func.lower(WorkflowFinding.detail).like(like),
            )
        )
    if f_subject_id:
        q = q.filter(WorkflowFinding.subject_id == f_subject_id)

    # Subject dropdown: subjects that carry findings within the current scope
    # (case filter + status/report/search), ignoring pagination so the facet
    # reflects the full result set.
    from cms.models import Subject

    subject_ids = [
        r[0]
        for r in q.with_entities(WorkflowFinding.subject_id)
        .filter(WorkflowFinding.subject_id.isnot(None))
        .distinct()
        .all()
    ]
    subject_options = []
    subjects_by_name = {}
    if subject_ids:
        subj_rows = (
            Subject.query.filter(
                Subject.id.in_(subject_ids),
                Subject.is_deleted.is_(False),
            )
            .order_by(Subject.name)
            .all()
        )
        subjects_by_name = {s.id: s for s in subj_rows}
        subject_options = sorted(subj_rows, key=lambda s: (s.name or "").lower())

    finding_count = q.count()
    findings = (
        q.options(sa.orm.joinedload(WorkflowFinding.finding_screenshots))
        .order_by(WorkflowFinding.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )
    case_map = {c.id: c for c in cases}
    for f in findings:
        f._ctx_case = case_map.get(f.case_id)
        f._ctx_subject = subjects_by_name.get(f.subject_id) if f.subject_id else None

    run_rows = (
        db.session.query(WorkflowResearchAction.case_id)
        .filter(WorkflowResearchAction.case_id.in_(scope_ids))
        .filter(WorkflowResearchAction.status.in_(["running", "pending"]))
        .first()
    )
    has_running = run_rows is not None

    return render_template(
        "cms/workflow/workflow_findings.html",
        findings=findings,
        cases=cases,
        subject_options=subject_options,
        subjects_by_name=subjects_by_name,
        f_case_id=f_case_id,
        f_subject_id=f_subject_id,
        f_status=f_status,
        f_report=f_report,
        f_q=f_q,
        show_archived=show_archived,
        finding_count=finding_count,
        page=page,
        per_page=per_page,
        case_map=case_map,
        has_running=has_running,
        action_types=ACTION_REGISTRY,
    )


@workflow_bp.route("/api/findings")
@login_required
@_investigator_required
def findings_api():
    """Lightweight findings data for the register's conditional polling."""
    case_ids, _ = _findings_accessible_case_ids()

    f_case_id = request.args.get("case_id", "").strip()
    f_subject_id = request.args.get("subject_id", "").strip()
    f_status = request.args.get("status", "").strip()
    f_report = request.args.get("report", "").strip()
    f_q = request.args.get("q", "").strip()
    show_archived = request.args.get("show_archived") == "1"

    cid = f_case_id if f_case_id in case_ids else None
    scope_ids = [cid] if cid else case_ids

    q = _findings_base_query(scope_ids)
    if not show_archived:
        q = q.filter(WorkflowFinding.archived_at.is_(None))
    if f_status:
        q = q.filter(WorkflowFinding.status == f_status)
    if f_report == "in":
        q = q.filter(
            sa.or_(
                WorkflowFinding.include_in_report.is_(None),
                WorkflowFinding.include_in_report == True,
            )  # noqa: E712
        )
    elif f_report == "out":
        q = q.filter(WorkflowFinding.include_in_report == False)  # noqa: E712
    if f_subject_id:
        q = q.filter(WorkflowFinding.subject_id == f_subject_id)
    if f_q:
        like = f"%{f_q.lower()}%"
        q = q.filter(
            sa.or_(
                db.func.lower(WorkflowFinding.title).like(like),
                db.func.lower(WorkflowFinding.content).like(like),
                db.func.lower(WorkflowFinding.detail).like(like),
            )
        )
    page = max(1, request.args.get("page", 1, type=int))
    per_page = max(1, min(request.args.get("per_page", 50, type=int), 200))
    findings = (
        q.options(sa.orm.joinedload(WorkflowFinding.finding_screenshots))
        .order_by(WorkflowFinding.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )
    case_map = {}
    if findings:
        from cms.models import Case as _Case

        row_cases = _Case.query.filter(
            _Case.id.in_({f.case_id for f in findings})
        ).all()
        case_map = {c.id: c for c in row_cases}
    subject_map = {}
    for f in findings:
        if f.subject_id:
            subject_map.setdefault(f.subject_id, None)
    if subject_map:
        from cms.models import Subject as _Subject

        subj_rows = _Subject.query.filter(_Subject.id.in_(list(subject_map))).all()
        subject_map = {s.id: s for s in subj_rows}

    # Running-actions lookup so the client knows when to poll / show activity.
    run_rows = (
        db.session.query(
            WorkflowResearchAction.case_id, WorkflowResearchAction.action_type
        )
        .filter(WorkflowResearchAction.case_id.in_(scope_ids))
        .filter(WorkflowResearchAction.status.in_(["running", "pending"]))
        .all()
    )
    has_running = len(run_rows) > 0

    return jsonify(
        {
            "findings": [
                _finding_json_with_context(
                    f,
                    case_map.get(f.case_id),
                    subject_map.get(f.subject_id) if f.subject_id else None,
                )
                for f in findings
            ],
            "actions": [{"case_id": c, "action_type": t} for c, t in run_rows],
            "has_running": has_running,
            "finding_count": q.count(),
            "page": page,
            "per_page": per_page,
        }
    )


@workflow_bp.route("/api/client-lookup", methods=["GET"])
@login_required
@_investigator_required
def client_lookup():
    """Search existing CMS clients by name or contact person."""
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify({"results": []})

    from cms.models import Client as CmsClient

    similar = find_similar_clients(q)
    ids = [s["id"] for s in similar]

    # Also check for exact match
    from cms.routes.utils import check_for_exact_match

    exact = check_for_exact_match(q, "client")
    if exact and exact["id"] not in ids:
        ids.insert(0, exact["id"])

    results = []
    for cid in ids:
        client = db.session.get(CmsClient, cid)
        if not client:
            continue
        # Build inside no_autoflush: to_dict()'s contacts relationship load
        # would autoflush and re-encrypt the freshly decrypted client, turning
        # the bank_account/financial_notes reads below into ciphertext.
        with db.session.no_autoflush:
            client.decrypt_naw()
            raw = client.to_dict(decrypted=True)
            # Also search by contact person
            contact = raw.get("contact_person", "") or ""
            contact_score = 0
            if contact and q.lower() in contact.lower():
                contact_score = 90
            results.append(
                {
                    "id": client.id,
                    "name": client.name,
                    "contact_person": contact,
                    "contact_email": raw.get("contact_email", ""),
                    "contact_phone": raw.get("contact_phone", ""),
                    "address": raw.get("address", {}),
                    "contract_number": raw.get("contract_number", ""),
                    "contract_info": raw.get("contract_info", ""),
                    "vat_number": client.vat_number or "",
                    "bank_account": client.bank_account or "",
                    "financial_notes": client.financial_notes or "",
                    "score": max(
                        next(
                            (s["similarity"] for s in similar if s["id"] == client.id),
                            100 if exact and exact["id"] == client.id else 0,
                        ),
                        contact_score,
                    ),
                }
            )

    results.sort(key=lambda r: r["score"], reverse=True)
    return jsonify({"results": results[:10]})


@workflow_bp.route("/case/new", methods=["GET", "POST"])
@login_required
@_investigator_required
def case_new():
    if request.method == "POST":
        client_id = str(uuid.uuid4())
        case_id = str(uuid.uuid4())

        existing_client_id = request.form.get("existing_client_id", "").strip()

        if existing_client_id:
            from cms.models import Client as CmsClient

            src = db.session.get(CmsClient, existing_client_id)
            if src and src.tenant_id != current_user.tenant_id:
                src = None
            if src:
                src.decrypt_naw()
                client = WorkflowClient(
                    id=client_id,
                    name=src.name,
                    contact_person=src.contact_person or "",
                    contact_email=src.contact_email or "",
                    contact_phone=src.contact_phone or "",
                    reference=request.form.get("reference", ""),
                    address_street=src.address_street or "",
                    address_number=src.address_number or "",
                    address_city=src.address_city or "",
                    address_postal=src.address_postal or "",
                    address_country=src.address_country or "Nederland",
                    vat_number=src.vat_number or "",
                    bank_account=src.bank_account or "",
                    contract_number=src.contract_number or "",
                    contract_info=src.contract_info or "",
                    financial_notes=src.financial_notes or "",
                    created_by=current_user.id,
                    tenant_id=current_user.tenant_id,
                )
                # Override with any explicitly submitted values
                override = request.form.get("client_name", "").strip()
                if override:
                    client.name = override
                override = request.form.get("client_contact", "").strip()
                if override:
                    client.contact_person = override
                override = request.form.get("client_email", "").strip()
                if override:
                    client.contact_email = override
                override = request.form.get("client_phone", "").strip()
                if override:
                    client.contact_phone = override
                override = request.form.get("client_street", "").strip()
                if override:
                    client.address_street = override
                override = request.form.get("client_house_number", "").strip()
                override_add = request.form.get(
                    "client_house_number_addition", ""
                ).strip()
                if override:
                    client.address_number = _combine_address_number(
                        override, override_add
                    )
                override = request.form.get("client_city", "").strip()
                if override:
                    client.address_city = override
                override = normalize_postcode(
                    request.form.get("client_postal_code", "").strip()
                )
                if override:
                    client.address_postal = override
                override = request.form.get("client_country", "").strip()
                if override:
                    client.address_country = override
                override = request.form.get("client_vat_number", "").strip()
                if override:
                    client.vat_number = override
                override = request.form.get("client_bank_account", "").strip()
                if override:
                    client.bank_account = override
                override = request.form.get("client_contract_number", "").strip()
                if override:
                    client.contract_number = override
                override = request.form.get("client_contract_info", "").strip()
                if override:
                    client.contract_info = override
                override = request.form.get("client_notes", "").strip()
                if override:
                    client.financial_notes = override
                client.encrypt_naw()
                db.session.add(client)
        else:
            client = WorkflowClient(
                id=client_id,
                name=request.form.get("client_name", "Onbekend"),
                contact_person=request.form.get("client_contact", ""),
                contact_email=request.form.get("client_email", ""),
                contact_phone=normalize_phone(request.form.get("client_phone", "")),
                reference=request.form.get("reference", ""),
                address_street=request.form.get("client_street", ""),
                address_number=_combine_address_number(
                    request.form.get("client_house_number", ""),
                    request.form.get("client_house_number_addition", ""),
                ),
                address_city=request.form.get("client_city", ""),
                address_postal=normalize_postcode(
                    request.form.get("client_postal_code", "")
                ),
                address_country=request.form.get("client_country", "Nederland"),
                vat_number=request.form.get("client_vat_number", ""),
                bank_account=request.form.get("client_bank_account", ""),
                contract_number=request.form.get("client_contract_number", ""),
                contract_info=request.form.get("client_contract_info", ""),
                financial_notes=request.form.get("client_notes", ""),
                created_by=current_user.id,
                tenant_id=current_user.tenant_id,
            )
            client.encrypt_naw()
            db.session.add(client)

        def _make_subject(idx_str):
            return subject_service.create(
                _wf_subject_data(idx_str),
                created_by=current_user.id,
                tenant_id=current_user.tenant_id,
            )

        subjects = []
        idx = 0
        while request.form.get(f"subject_{idx}_name") or request.form.get(
            f"subject_{idx}_type"
        ):
            subjects.append(_make_subject(f"subject_{idx}"))
            idx += 1
        if not subjects:
            subjects.append(_make_subject("subject"))

        # Case numbers are allocated atomically and are immutable after
        # issuance (ADR-0002 D2/D4) — manual input can never override the
        # sequential allocation, so the submitted field is ignored.
        case_number = WorkflowCase.generate_case_number(current_user.tenant_id)
        case = WorkflowCase(
            id=case_id,
            case_number=case_number,
            title=request.form.get("title", "Nieuw onderzoek"),
            status="open",
            priority=request.form.get("priority", "medium"),
            description=request.form.get("description", ""),
            client_id=client_id,
            start_date=datetime.now().date(),
            created_by=current_user.id,
            lead_investigator_id=current_user.id,
            tenant_id=current_user.tenant_id,
        )
        db.session.add(client)
        db.session.add(case)
        for s in subjects:
            db.session.add(s)
            case.subjects.append(s)
        AuditLog.log(
            user_id=current_user.id,
            action="create",
            entity_type="case",
            entity_id=case.id,
            ip_address=request.remote_addr,
            description=f"Workflow created case: {case.case_number}",
        )
        # P1: the auto-invoice runs in the SAME transaction as the case —
        # case + invoice commit together, so an invoice failure rolls the
        # whole creation back instead of leaving an orphan case behind.
        auto_invoice_case_created(case)
        db.session.commit()

        return redirect(url_for("workflow.case_detail", case_id=case_id))

    return render_template(
        "cms/workflow/workflow_case_new.html",
        generated_case_number=WorkflowCase.peek_case_number(current_user.tenant_id),
    )


@workflow_bp.route("/case/<case_id>")
@login_required
@_investigator_required
def case_detail(case_id):
    case = db.session.get(WorkflowCase, case_id)
    if not case:
        abort(404)
    ensure_case_access(case)
    show_archived = request.args.get("show_archived") == "1"
    actions = (
        WorkflowResearchAction.query.filter_by(case_id=case_id)
        .filter(
            WorkflowResearchAction.archived_at.is_(None)
            if not show_archived
            else sa.true()
        )
        .order_by(WorkflowResearchAction.created_at.desc())
        .all()
    )
    findings = (
        WorkflowFinding.query.filter_by(case_id=case_id)
        .filter(WorkflowFinding.is_deleted == False)
        .filter(
            WorkflowFinding.archived_at.is_(None) if not show_archived else sa.true()
        )
        .options(sa.orm.joinedload(WorkflowFinding.finding_screenshots))
        .order_by(WorkflowFinding.created_at.desc())
        .all()
    )

    finding_ids = [f.id for f in findings]
    links = (
        WorkflowActionFinding.query.filter(
            WorkflowActionFinding.finding_id.in_(finding_ids)
        ).all()
        if finding_ids
        else []
    )
    finding_actions = {}
    for link in links:
        finding_actions.setdefault(link.finding_id, []).append(link.action_id)

    investigations, created_by_names = _load_case_investigations(case_id, show_archived)

    client = db.session.get(WorkflowClient, case.client_id) if case.client_id else None
    with db.session.no_autoflush:
        if client:
            client.decrypt_naw()
        subjects = list(case.subjects)
        subjects.sort(key=lambda s: (s.name or "").lower())
        for s in subjects:
            s.decrypt_identifiers()
        subjects_data = []
        for s in subjects:
            if not s:
                continue
            decrypted_contacts = []
            for c in s.contacts.all():
                c.decrypt_fields()
                decrypted_contacts.append(
                    {
                        "contact_type": c.contact_type,
                        "value": c.value,
                        "is_primary": c.is_primary,
                    }
                )
            subjects_data.append(
                {
                    "id": s.id,
                    "display_name": s.compute_name()
                    if callable(getattr(s, "compute_name", None))
                    else (s.name or ""),
                    "name": s.name,
                    "subject_type": s.subject_type,
                    "geslacht": s.geslacht,
                    "risk_score": s.risk_score,
                    "email": s.email,
                    "phone": s.phone,
                    "date_of_birth": s.date_of_birth,
                    "place_of_birth": s.place_of_birth,
                    "nationality": s.nationality,
                    "bsn_number": s.bsn_number,
                    "reisdocument_type": s.reisdocument_type,
                    "reisdocument_nummer": s.reisdocument_nummer,
                    "identification_number": s.identification_number,
                    "bank_account": s.bank_account,
                    "street": s.street,
                    "house_number": s.house_number,
                    "house_number_addition": s.house_number_addition,
                    "postal_code": s.postal_code,
                    "city": s.city,
                    "workflow_social_accounts": s.workflow_social_accounts,
                    "registration_number": s.registration_number,
                    "legal_form": s.legal_form,
                    "license_plate": s.license_plate,
                    "rdw_data": s.rdw_data or {},
                    "vin": s.vin,
                    "brand": s.brand,
                    "vehicle_type": s.vehicle_type,
                    "imo_number": s.imo_number,
                    "mmsi": s.mmsi,
                    "eni_number": s.eni_number,
                    "vessel_nationality": s.vessel_nationality,
                    "notes": s.notes,
                    "contacts": decrypted_contacts,
                }
            )
        brave_health = _get_cached_health().get("brave", "no key configured")
        action_credits = {}
        for key in ACTION_REGISTRY:
            action_credits[key] = get_remaining_credits(key)

        dorks_library = {}
        dorks_path = os.path.join(os.path.dirname(__file__), "dorks.json")
        try:
            with open(dorks_path, "r", encoding="utf-8") as _f:
                dorks_library = json.load(_f)
        except Exception:
            pass

        return render_template(
            "cms/workflow/workflow_case_detail.html",
            case=case,
            client=client,
            subjects=subjects,
            subjects_data=subjects_data,
            actions=actions,
            findings=findings,
            finding_actions=finding_actions,
            investigations=investigations,
            created_by_names=created_by_names,
            can_write=_current_user_is_investigator(),
            step_number=4,
            action_types=ACTION_REGISTRY,
            action_credits=action_credits,
            subject_presets=SUBJECT_TYPE_PRESETS,
            brave_health=brave_health,
            show_archived=show_archived,
            dorks_library=dorks_library,
            paid_enabled=paid_channels_enabled(),
        )


@workflow_bp.route("/case/<case_id>/investigations")
@login_required
def investigations_index(case_id):
    """Read-only investigations for a case. Gated by case access only, so
    viewers with case access can read too (writes stay investigator-gated)."""
    case = db.session.get(WorkflowCase, case_id)
    if not case:
        abort(404)
    ensure_case_access(case)
    show_archived = request.args.get("show_archived") == "1"
    investigations, created_by_names = _load_case_investigations(case.id, show_archived)
    return render_template(
        "cms/workflow/workflow_case_investigations.html",
        case=case,
        investigations=investigations,
        created_by_names=created_by_names,
        show_archived=show_archived,
        can_write=_current_user_is_investigator(),
        step_number=4,
    )


@workflow_bp.route("/api/case/<case_id>/investigations", methods=["POST"])
@login_required
@_investigator_required
def create_investigation(case_id):
    case = db.session.get(WorkflowCase, case_id)
    if not case:
        return jsonify({"error": "Case not found"}), 404
    ensure_case_access(case)

    payload = request.get_json(silent=True) if request.is_json else request.form
    payload = payload or {}
    title = (payload.get("title") or "").strip()
    instructions = (payload.get("instructions") or "").strip() or None
    notes = (payload.get("notes") or "").strip() or None

    if not title:
        if request.is_json:
            return jsonify({"error": "Title is required"}), 400
        flash(_("Title is required."), "danger")
        return redirect(url_for("workflow.case_detail", case_id=case_id))

    try:
        investigation = sequence_create_investigation(
            tenant_id=case.tenant_id,
            case_id=case_id,
            title=title,
            instructions=instructions,
            notes=notes,
            created_by=current_user.id,
        )
        db.session.flush()
    except ValueError as exc:
        db.session.rollback()
        if request.is_json:
            return jsonify({"error": str(exc)}), 400
        flash(str(exc), "danger")
        return redirect(url_for("workflow.case_detail", case_id=case_id))

    AuditLog.log(
        user_id=current_user.id,
        action="create",
        entity_type="investigation",
        entity_id=investigation.id,
        ip_address=request.remote_addr,
        case_id=case_id,
        description=f"Workflow created investigation: {investigation.human_number}",
    )
    db.session.commit()

    if request.is_json:
        return jsonify({"ok": True, "investigation": investigation.to_dict()})
    flash(_("Investigation created."), "info")
    return redirect(url_for("workflow.case_detail", case_id=case_id))


@workflow_bp.route("/case/<case_id>/edit", methods=["GET", "POST"])
@login_required
@_investigator_required
def case_edit(case_id):
    case = db.session.get(WorkflowCase, case_id)
    if not case:
        abort(404)
    ensure_case_access(case)

    client = db.session.get(WorkflowClient, case.client_id) if case.client_id else None
    subjects = list(case.subjects)
    subjects.sort(key=lambda s: (s.name or "").lower())
    # Decrypt + render inside no_autoflush: the before_flush guard
    # (_reencrypt_plaintext_at_flush) re-encrypts dirty encrypted objects.
    # Template rendering triggers lazy loads which autoflush, silently
    # re-encrypting decrypted values back to ciphertext mid-render.
    if request.method == "GET":
        with db.session.no_autoflush:
            if client:
                client.decrypt_naw()
            for s in subjects:
                s.decrypt_identifiers()
                for c in s.contacts.all():
                    c.decrypt_fields()
            return render_template(
                "cms/workflow/workflow_case_edit.html",
                case=case,
                client=client,
                subjects=subjects,
            )

    # POST handling below
    # Client NAW fields are safe to decrypt: encrypt_naw() below re-encrypts
    # every field after the form mutations, before the commit.
    if client:
        client.decrypt_naw()
    # update client
    if client:
        client.name = request.form.get("client_name", client.name)
        client.contact_person = request.form.get("client_contact", "")
        client.contact_email = request.form.get("client_email", "")
        client.contact_phone = normalize_phone(request.form.get("client_phone", ""))
        client.reference = request.form.get("reference", "")
        client.address_street = request.form.get("client_street", "")
        client.address_number = _combine_address_number(
            request.form.get("client_house_number", ""),
            request.form.get("client_house_number_addition", ""),
        )
        client.address_city = request.form.get("client_city", "")
        client.address_postal = normalize_postcode(
            request.form.get("client_postal_code", "")
        )
        client.address_country = request.form.get("client_country", "Nederland")
        client.vat_number = request.form.get("client_vat_number", "")
        client.bank_account = request.form.get("client_bank_account", "")
        client.contract_number = request.form.get("client_contract_number", "")
        client.contract_info = request.form.get("client_contract_info", "")
        client.financial_notes = request.form.get("client_notes", "")
        client.encrypt_naw()

    # Case numbers are immutable after issuance (ADR-0002 D4) — an issued
    # case number may never be modified, not even via the edit route.
    # Corrections belong to an auditable alias/correction note (out of scope).
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
    try:
        for sid in list(keep_ids):
            if not sid:
                continue
            subj = db.session.get(WorkflowSubject, sid)
            if not subj:
                continue
            subject_service.edit(
                subj,
                _wf_subject_data(f"subj_{sid}", fallback_type=subj.subject_type),
                actor_id=current_user.id,
            )
    except ValueError as e:
        db.session.rollback()
        flash(str(e), "danger")
        return redirect(url_for("workflow.case_edit", case_id=case_id))

    # process new subjects
    n = 0
    while request.form.get(f"subj_new_{n}_name") or request.form.get(
        f"subj_new_{n}_type"
    ):
        new_subj = subject_service.create(
            _wf_subject_data(f"subj_new_{n}"),
            created_by=current_user.id,
            tenant_id=current_user.tenant_id,
        )
        case.subjects.append(new_subj)
        n += 1

    # remove unlinked subjects
    for sid in list(removed_ids):
        if not sid:
            continue
        subj = db.session.get(WorkflowSubject, sid)
        if subj and subj in case.subjects:
            case.subjects.remove(subj)

    AuditLog.log(
        user_id=current_user.id,
        action="update",
        entity_type="case",
        entity_id=case.id,
        ip_address=request.remote_addr,
        description=f"Workflow edited case: {case.case_number}",
    )
    db.session.commit()
    return redirect(url_for("workflow.case_detail", case_id=case_id))


@workflow_bp.route("/api/case/<case_id>/run-action", methods=["POST"])
@login_required
@_investigator_required
def run_action(case_id):
    case = db.session.get(WorkflowCase, case_id)
    if not case:
        return jsonify({"error": "Case not found"}), 404
    ensure_case_access(case)

    body = request.get_json(silent=True) or {}
    action_type = body.get("action_type", "")
    data_value = body.get("data_value") or ""
    subject_id = body.get("subject_id") or None
    mode = body.get("mode") or "run"

    if mode not in ("run", "proposal"):
        return jsonify({"error": "Unknown mode"}), 400
    if action_type not in ACTION_REGISTRY:
        return jsonify({"error": f"Unknown action: {action_type}"}), 400

    # ADR-0001 D1.6: paid channels are off by default behind explicit tenant
    # config (FeatureFlag "paid_channels"). Block both immediate runs and
    # proposals when the tenant has not opted in.
    if is_paid_action(action_type) and not paid_channels_enabled():
        return (
            jsonify(
                {
                    "error": (
                        "Paid research channels are disabled for this tenant. "
                        "A super-admin must enable them via Feature Flags."
                    )
                }
            ),
            409,
        )

    # Explicit target subject (ADR-0001 PR4): a non-null subject_id must be a
    # subject linked to this case. None means an explicit case-wide scope.
    subject = None
    if subject_id:
        subject = db.session.get(WorkflowSubject, subject_id)
        if not subject:
            return jsonify({"error": "Subject not found"}), 404
        if not case.subjects.filter_by(id=subject_id).first():
            return jsonify({"error": "Subject is not linked to this case"}), 400

    if mode == "proposal":
        action = WorkflowResearchAction(
            id=str(uuid.uuid4()),
            case_id=case_id,
            subject_id=subject_id,
            target_kind="subject" if subject_id else "case",
            action_type=action_type,
            data_value=data_value,
            label=ACTION_REGISTRY[action_type]["label"],
            status="proposal",
            tenant_id=current_user.tenant_id,
        )
        action.target_snapshot = json.dumps(
            action.build_target_snapshot(subject, data_value)
        )
        db.session.add(action)
        db.session.commit()
        return jsonify({"id": action.id, "status": "proposal"})

    _STALE_TIMEOUT = 600  # 10 minutes
    existing_query = WorkflowResearchAction.query.filter_by(
        case_id=case_id, action_type=action_type, status="running"
    )
    if subject_id:
        existing_query = existing_query.filter_by(subject_id=subject_id)
    existing = existing_query.first()
    if existing:
        if (
            existing.started_at
            and (datetime.now() - existing.started_at).total_seconds() > _STALE_TIMEOUT
        ):
            existing.status = "error"
            existing.error = "Stale action auto-reset (timed out)"
            db.session.commit()
        else:
            return jsonify({"error": "Action already running"}), 409

    action = WorkflowResearchAction(
        id=str(uuid.uuid4()),
        case_id=case_id,
        subject_id=subject_id,
        target_kind="subject" if subject_id else "case",
        action_type=action_type,
        data_value=data_value,
        label=ACTION_REGISTRY[action_type]["label"],
        status="pending",
        tenant_id=current_user.tenant_id,
    )
    action.target_snapshot = json.dumps(
        action.build_target_snapshot(subject, data_value)
    )
    db.session.add(action)
    db.session.commit()
    action_id = action.id

    start_action_async(action_id)
    return jsonify({"id": action_id, "status": "started"})


@workflow_bp.route("/api/case/<case_id>/proposals", methods=["POST"])
@login_required
@_investigator_required
def create_proposals(case_id):
    """Create one or more investigation proposals without starting them.

    Body: {"subject_id": optional, "action_types": ["osint", "email", ...]}
    Free/local/open actions are ready as proposals; paid channels are never
    silently proposed (ADR-0001 D1.5/D1.6) and are filtered out here — they
    are only startable via an explicit per-action opt-in by the investigator.
    """
    case = db.session.get(WorkflowCase, case_id)
    if not case:
        return jsonify({"error": "Case not found"}), 404
    ensure_case_access(case)

    body = request.get_json(silent=True) or {}
    subject_id = body.get("subject_id") or None
    action_types = body.get("action_types") or []

    if not action_types:
        return jsonify({"error": "No actions proposed"}), 400
    if not isinstance(action_types, list):
        return jsonify({"error": "action_types must be a list"}), 400
    unknown = [t for t in action_types if t not in ACTION_REGISTRY]
    if unknown:
        return jsonify({"error": f"Unknown action: {unknown[0]}"}), 400

    skipped = [t for t in action_types if is_paid_action(t)]
    action_types = [t for t in action_types if not is_paid_action(t)]
    if not action_types:
        return (
            jsonify(
                {
                    "error": (
                        "Only paid channels were proposed; paid channels are "
                        "never auto-proposed (ADR-0001 D1.6)."
                    ),
                    "skipped": skipped,
                }
            ),
            422,
        )

    subject = None
    if subject_id:
        subject = db.session.get(WorkflowSubject, subject_id)
        if not subject:
            return jsonify({"error": "Subject not found"}), 404
        if not case.subjects.filter_by(id=subject_id).first():
            return jsonify({"error": "Subject is not linked to this case"}), 400

    created = []
    for action_type in action_types:
        action = WorkflowResearchAction(
            id=str(uuid.uuid4()),
            case_id=case_id,
            subject_id=subject_id,
            target_kind="subject" if subject_id else "case",
            action_type=action_type,
            label=ACTION_REGISTRY[action_type]["label"],
            status="proposal",
            tenant_id=current_user.tenant_id,
        )
        action.target_snapshot = json.dumps(action.build_target_snapshot(subject, None))
        db.session.add(action)
        created.append(action.id)
    db.session.commit()

    AuditLog.log(
        user_id=current_user.id,
        action="create",
        entity_type="research_action",
        entity_id=",".join(created),
        ip_address=request.remote_addr,
        case_id=case_id,
        description=f"Proposed {len(created)} investigation action(s) for the case",
    )
    return jsonify({"ok": True, "ids": created, "skipped": skipped})


@workflow_bp.route("/api/case/<case_id>/actions/<action_id>/start", methods=["POST"])
@login_required
@_investigator_required
def start_proposal(case_id, action_id):
    """Start a saved proposal action."""
    case = db.session.get(WorkflowCase, case_id)
    if not case:
        return jsonify({"error": "Case not found"}), 404
    ensure_case_access(case)
    action = db.session.get(WorkflowResearchAction, action_id)
    if not action or action.case_id != case_id:
        return jsonify({"error": "Not found"}), 404
    if action.status != "proposal":
        return jsonify({"error": "Action is not a proposal"}), 409
    if is_paid_action(action.action_type) and not paid_channels_enabled():
        return (
            jsonify(
                {
                    "error": (
                        "Paid research channels are disabled for this tenant. "
                        "A super-admin must enable them via Feature Flags."
                    )
                }
            ),
            409,
        )
    action.status = "pending"
    db.session.commit()
    start_action_async(action_id)
    return jsonify({"ok": True, "id": action_id})


@workflow_bp.route("/api/case/<case_id>/photo-analysis", methods=["POST"])
@login_required
@_investigator_required
def photo_analysis_upload(case_id):
    case = db.session.get(WorkflowCase, case_id)
    if not case:
        return jsonify({"error": "Case not found"}), 404
    ensure_case_access(case)

    photo = request.files.get("photo")
    if not photo:
        return jsonify({"error": "No photo uploaded"}), 400

    allowed = {"image/jpeg", "image/png", "image/heic", "image/webp", "image/gif"}
    if photo.content_type not in allowed:
        return jsonify({"error": f"Unsupported file type: {photo.content_type}"}), 400

    import os
    import uuid as _uuid

    upload_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "static",
        "uploads",
        "photos",
    )
    os.makedirs(upload_dir, exist_ok=True)

    ext = (
        photo.filename.rsplit(".", 1)[-1].lower()
        if photo.filename and "." in photo.filename
        else "jpg"
    )
    filename = f"{_uuid.uuid4().hex[:12]}.{ext}"
    filepath = os.path.join(upload_dir, filename)
    photo.save(filepath)

    data_value = filepath

    subject_id = request.form.get("subject_id") or None
    subject = None
    if subject_id:
        subject = db.session.get(WorkflowSubject, subject_id)
        if not subject:
            return jsonify({"error": "Subject not found"}), 404
        if not case.subjects.filter_by(id=subject_id).first():
            return jsonify({"error": "Subject is not linked to this case"}), 400

    _STALE_TIMEOUT = 600
    existing_query = WorkflowResearchAction.query.filter_by(
        case_id=case_id, action_type="photo_analysis", status="running"
    )
    if subject_id:
        existing_query = existing_query.filter_by(subject_id=subject_id)
    existing = existing_query.first()
    if existing:
        if (
            existing.started_at
            and (datetime.now() - existing.started_at).total_seconds() > _STALE_TIMEOUT
        ):
            existing.status = "error"
            existing.error = "Stale action auto-reset (timed out)"
            db.session.commit()
        else:
            return jsonify({"error": "Action already running"}), 409

    action = WorkflowResearchAction(
        id=str(uuid.uuid4()),
        case_id=case_id,
        subject_id=subject_id,
        target_kind="subject" if subject_id else "case",
        action_type="photo_analysis",
        data_value=data_value,
        label=ACTION_REGISTRY["photo_analysis"]["label"],
        status="pending",
        tenant_id=current_user.tenant_id,
    )
    action.target_snapshot = json.dumps(action.build_target_snapshot(subject, None))
    db.session.add(action)
    db.session.commit()
    action_id = action.id

    start_action_async(action_id)
    return jsonify({"id": action_id, "status": "started"})


@workflow_bp.route("/api/case/<case_id>/manual-finding", methods=["POST"])
@login_required
@_investigator_required
def create_manual_finding(case_id):
    case = db.session.get(WorkflowCase, case_id)
    if not case:
        return jsonify({"error": "Case not found"}), 404
    ensure_case_access(case)

    body = request.get_json(silent=True) or {}
    title = (body.get("title") or "").strip()
    content = (body.get("content") or "").strip()

    if not title:
        return jsonify({"error": "Title is required"}), 400

    from cms.workflow.research import link_finding_to_manual_action

    finding = WorkflowFinding(
        id=str(uuid.uuid4()),
        case_id=case_id,
        title=title,
        content=content or title,
        detail=content,
        source_type="manual",
        created_by=current_user.id,
    )
    db.session.add(finding)
    db.session.flush()

    link_finding_to_manual_action(finding.id, case_id, current_user.id)

    AuditLog.log(
        user_id=current_user.id,
        action="create",
        entity_type="finding",
        entity_id=finding.id,
        ip_address=request.remote_addr,
        case_id=case_id,
        description=f"Manual finding via workflow: {finding.title}",
    )
    db.session.commit()

    return jsonify({"ok": True, "finding_id": finding.id})


@workflow_bp.route("/api/case/<case_id>/status")
@login_required
@_investigator_required
def case_status(case_id):
    case = db.session.get(WorkflowCase, case_id)
    if not case:
        return jsonify({"error": "Not found"}), 404
    ensure_case_access(case)

    actions = WorkflowResearchAction.query.filter_by(case_id=case_id).all()
    findings = (
        WorkflowFinding.query.filter_by(case_id=case_id)
        .filter(WorkflowFinding.is_deleted == False)
        .filter(WorkflowFinding.archived_at.is_(None))
        .options(sa.orm.joinedload(WorkflowFinding.finding_screenshots))
        .order_by(WorkflowFinding.created_at.desc())
        .all()
    )

    finding_ids = [f.id for f in findings]
    links = (
        WorkflowActionFinding.query.filter(
            WorkflowActionFinding.finding_id.in_(finding_ids)
        ).all()
        if finding_ids
        else []
    )
    finding_actions = {}
    for link in links:
        finding_actions.setdefault(link.finding_id, []).append(link.action_id)

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
            "status": f.status,
            "verified_by": f.verified_by,
            "verified_at": f.verified_at.isoformat() if f.verified_at else None,
            "verifier_name": f.verifier.full_name if f.verifier else None,
            "content_hash": f.content_hash,
            "integrity_verified": f.verify_integrity() if f.content_hash else None,
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
                for ss in (f.finding_screenshots or [])
            ],
        }

    return jsonify(
        {
            "actions": [
                {
                    "id": a.id,
                    "action_type": a.action_type,
                    "label": a.label,
                    "icon": ACTION_REGISTRY.get(a.action_type, {}).get("icon"),
                    "category": action_category(a.action_type),
                    "status": a.status,
                    "error": a.error,
                    "data_value": a.data_value,
                    "subject_id": a.subject_id,
                    "target_kind": a.target_kind,
                    "target_snapshot": a.target_snapshot_data,
                    "dork_label": a.dork_label,
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
@_investigator_required
def delete_case(case_id):
    case = db.session.get(WorkflowCase, case_id)
    if not case:
        return jsonify({"error": "Not found"}), 404
    ensure_case_access(case)
    case.soft_delete()
    AuditLog.log(
        user_id=current_user.id,
        action="delete",
        entity_type="case",
        entity_id=case.id,
        ip_address=request.remote_addr,
        description=f"Workflow soft-deleted case: {case.case_number}",
    )
    db.session.commit()
    return jsonify({"ok": True})


@workflow_bp.route("/case/<case_id>/pv")
@login_required
@_investigator_required
def pv_view(case_id):
    case = db.session.get(WorkflowCase, case_id)
    if not case:
        abort(404)
    ensure_case_access(case)
    client = db.session.get(WorkflowClient, case.client_id) if case.client_id else None
    subjects = list(case.subjects)
    findings = (
        case.findings.filter_by(is_deleted=False, archived_at=None)
        .filter(report_include_filter())
        .options(sa.orm.joinedload(WorkflowFinding.finding_screenshots))
        .order_by(WorkflowFinding.created_at)
        .all()
    )

    import markdown as md_lib

    _ALLOWED_TAGS = [
        "p",
        "br",
        "strong",
        "em",
        "a",
        "ul",
        "ol",
        "li",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "code",
        "pre",
        "blockquote",
        "hr",
        "table",
        "thead",
        "tbody",
        "tr",
        "th",
        "td",
        "span",
        "div",
    ]
    _ALLOWED_ATTRS = {
        "a": ["href", "title"],
        "span": ["class"],
        "div": ["class"],
        "th": ["align"],
        "td": ["align"],
    }
    raw_html = md_lib.markdown(case.pv_body or "") if case.pv_body else ""
    body_html = bleach.clean(raw_html, tags=_ALLOWED_TAGS, attributes=_ALLOWED_ATTRS)

    return render_template(
        "cms/workflow/workflow_pv.html",
        case=case,
        client=client,
        subjects=subjects,
        findings=findings,
        body_html=body_html,
    )


@workflow_bp.route("/case/<case_id>/pv/regenerate", methods=["POST"])
@login_required
@_investigator_required
def pv_regenerate(case_id):
    case = db.session.get(WorkflowCase, case_id)
    if not case:
        abort(404)
    ensure_case_access(case)

    findings = (
        case.findings.filter_by(is_deleted=False, archived_at=None)
        .filter(report_include_filter())
        .order_by(WorkflowFinding.created_at)
        .all()
    )

    if findings:
        type_map = {}
        for f in findings:
            st = f.source_type or "onbekend"
            type_map[st] = type_map.get(st, 0) + 1
        summary = ", ".join(
            f"{c}x {t}" for t, c in sorted(type_map.items(), key=lambda x: -x[1])
        )
        summary_lines = [
            f"A total of **{len(findings)} finding(s)** have been recorded "
            f"for this case ({summary}).",
            "",
            "The full list of findings is included in the 'Findings' table below.",
        ]
    else:
        summary_lines = [
            "No findings have been recorded for this case yet.",
        ]

    new_summary = (
        "<!-- pv-summary -->\n" + "\n".join(summary_lines) + "\n<!-- /pv-summary -->"
    )

    if "<!-- pv-summary -->" in (case.pv_body or ""):
        import re

        case.pv_body = re.sub(
            r"<!-- pv-summary -->.*?<!-- /pv-summary -->",
            new_summary,
            case.pv_body,
            count=1,
            flags=re.DOTALL,
        )
    else:
        case.pv_body = (case.pv_body or "").rstrip() + "\n\n" + new_summary + "\n"
    case.pv_updated_at = datetime.now()

    AuditLog.log(
        user_id=current_user.id,
        action="update",
        entity_type="case",
        entity_id=case.id,
        ip_address=request.remote_addr,
        description=f"Workflow regenerated PV from findings for case: {case.case_number}",
    )
    db.session.commit()

    flash("Official report has been regenerated based on current findings.", "success")
    return redirect(url_for("workflow.pv_view", case_id=case_id))


@workflow_bp.route("/case/<case_id>/pv/edit", methods=["GET", "POST"])
@login_required
@_investigator_required
def pv_edit(case_id):
    case = db.session.get(WorkflowCase, case_id)
    if not case:
        abort(404)
    ensure_case_access(case)

    if request.method == "POST":
        was_empty = not case.pv_body
        case.pv_body = request.form.get("pv_body", "")
        case.pv_updated_at = datetime.now()
        AuditLog.log(
            user_id=current_user.id,
            action="update",
            entity_type="case",
            entity_id=case.id,
            ip_address=request.remote_addr,
            description=f"Workflow updated PV body for case: {case.case_number}",
        )
        # P1: auto-invoice commits together with the PV-save transaction, so a
        # invoice failure rolls back both instead of silently losing the line.
        if was_empty and case.pv_body:
            from cms.services.invoice_service import auto_invoice_pv_created

            auto_invoice_pv_created(case)
        db.session.commit()

        return redirect(url_for("workflow.pv_view", case_id=case_id))

    return render_template("cms/workflow/workflow_pv_edit.html", case=case)


@workflow_bp.route("/api/case/<case_id>/findings/<finding_id>/delete", methods=["POST"])
@login_required
@_investigator_required
def delete_finding(case_id, finding_id):
    finding = db.session.get(WorkflowFinding, finding_id)
    if not finding or finding.case_id != case_id:
        return jsonify({"error": "Not found"}), 404
    case = db.session.get(WorkflowCase, case_id)
    if case:
        ensure_case_access(case)
    finding.soft_delete()
    AuditLog.log(
        user_id=current_user.id,
        action="delete",
        entity_type="finding",
        entity_id=finding.id,
        ip_address=request.remote_addr,
        description=f"Workflow soft-deleted finding: {finding.title}",
    )
    db.session.commit()
    return jsonify({"ok": True})


@workflow_bp.route("/api/case/<case_id>/findings/batch-delete", methods=["POST"])
@login_required
@_investigator_required
def batch_delete_findings(case_id):
    case = db.session.get(WorkflowCase, case_id)
    if not case:
        return jsonify({"error": "Case not found"}), 404
    ensure_case_access(case)
    body = request.get_json(silent=True) or {}
    ids = body.get("ids", [])
    if not ids or not isinstance(ids, list):
        return jsonify({"error": "No IDs provided"}), 400
    deleted = 0
    for fid in ids:
        finding = db.session.get(WorkflowFinding, fid)
        if finding and finding.case_id == case_id:
            finding.soft_delete()
            deleted += 1
    AuditLog.log(
        user_id=current_user.id,
        action="bulk_delete",
        entity_type="finding",
        ip_address=request.remote_addr,
        description=f"Workflow batch soft-deleted {deleted} findings in case {case_id}",
    )
    db.session.commit()
    return jsonify({"ok": True, "deleted": deleted})


# Per-worker (in-process) cooldown to prevent a rapid toggle-back of the
# legacy boolean verify button. NOTE: this dict is NOT shared across gunicorn
# workers, so it only guards the common single-worker rapid-toggle case. If a
# stricter cross-worker guarantee is ever needed it must move to the DB/Redis.
_verify_cooldown = {}  # {finding_id: timestamp}


@workflow_bp.route("/api/case/<case_id>/findings/<finding_id>/verify", methods=["POST"])
@login_required
@_investigator_required
def verify_finding(case_id, finding_id):
    finding = db.session.get(WorkflowFinding, finding_id)
    if not finding or finding.case_id != case_id:
        return jsonify({"error": "Not found"}), 404
    case = db.session.get(WorkflowCase, case_id)
    if case:
        ensure_case_access(case)
    body = request.get_json(silent=True) or {}
    logger.debug(
        "verify_finding body=%s finding_id=%s user=%s", body, finding_id, current_user.id
    )
    status = body.get("status")
    if status not in (None, "verified", "rejected", "candidate", "superseded"):
        return jsonify({"error": "Unknown status"}), 400

    import time as _time

    now = _time.time()

    if status:
        target_status = status
    else:
        new_val = body.get("verified", not finding.verified)
        target_status = "verified" if new_val else "candidate"

    # Cooldown only guards the legacy boolean toggle path (verify button that
    # was previously re-used as a revert toggle). Explicit status transitions
    # are always allowed.
    last_change = _verify_cooldown.get(finding_id, 0)
    if not status and target_status == "candidate" and now - last_change < 5:
        logger.debug(
            "verify_finding cooldown block finding_id=%s target=%s elapsed=%.1f",
            finding_id,
            target_status,
            now - last_change,
        )
        return jsonify(
            {
                "ok": True,
                "verified": finding.verified,
                "status": finding.status,
                "cooldown": True,
            }
        )

    if status:
        if status == "verified":
            finding.promote_to_verified(current_user)
        elif status == "rejected":
            finding.reject(current_user)
        else:
            finding.demote_to_candidate()
    else:
        if new_val:
            finding.promote_to_verified(current_user)
        else:
            finding.demote_to_candidate()
    _verify_cooldown[finding_id] = now
    AuditLog.log(
        user_id=current_user.id,
        action="verify",
        entity_type="finding",
        entity_id=finding.id,
        ip_address=request.remote_addr,
        case_id=case_id,
        description=f"Workflow set finding status to {finding.status}: {finding.title}",
    )
    db.session.commit()
    logger.debug(
        "verify_finding result finding_id=%s verified=%s status=%s",
        finding.id,
        finding.verified,
        finding.status,
    )
    return jsonify(
        {
            "ok": True,
            "verified": finding.verified,
            "status": finding.status,
        }
    )


@workflow_bp.route(
    "/api/case/<case_id>/findings/<finding_id>/comment", methods=["POST"]
)
@login_required
@_investigator_required
def save_comment(case_id, finding_id):
    finding = db.session.get(WorkflowFinding, finding_id)
    if not finding or finding.case_id != case_id:
        return jsonify({"error": "Not found"}), 404
    case = db.session.get(WorkflowCase, case_id)
    if case:
        ensure_case_access(case)
    body = request.get_json(silent=True) or {}
    new_comment = body.get("comment", "")
    finding.comment = new_comment
    AuditLog.log(
        user_id=current_user.id,
        action="update",
        entity_type="finding",
        entity_id=finding.id,
        ip_address=request.remote_addr,
        description=f"Workflow updated comment on finding: {finding.title}",
    )
    db.session.commit()
    return jsonify({"ok": True, "comment": new_comment})


@workflow_bp.route(
    "/api/case/<case_id>/findings/<finding_id>/report-flag", methods=["POST"]
)
@login_required
@_investigator_required
def set_report_flag(case_id, finding_id):
    """Toggle generic include-in-report selection (ADR-0001 optie (b)).

    ``include_in_report`` semantics: NULL/True = included in official reports,
    False = excluded. Backward compatible — existing findings are unchanged.
    """
    finding = db.session.get(WorkflowFinding, finding_id)
    if not finding or finding.case_id != case_id:
        return jsonify({"error": "Not found"}), 404
    case = db.session.get(WorkflowCase, case_id)
    if case:
        ensure_case_access(case)
    elif (
        not current_user.is_super_admin and finding.tenant_id != current_user.tenant_id
    ):
        return jsonify({"error": "Forbidden"}), 403
    body = request.get_json(silent=True) or {}
    include = body.get("include_in_report")
    if not isinstance(include, bool):
        return jsonify({"error": "include_in_report must be a boolean"}), 400
    finding.include_in_report = include
    AuditLog.log(
        user_id=current_user.id,
        action="update",
        entity_type="finding",
        entity_id=finding.id,
        ip_address=request.remote_addr,
        case_id=case_id,
        description=f"Workflow set include_in_report={include} on finding: {finding.title}",
    )
    db.session.commit()
    return jsonify({"ok": True, "include_in_report": finding.include_in_report})


@workflow_bp.route("/api/case/<case_id>/actions/<action_id>/cancel", methods=["POST"])
@login_required
@_investigator_required
def cancel_action_api(case_id, action_id):
    case = db.session.get(WorkflowCase, case_id)
    if not case:
        return jsonify({"error": "Not found"}), 404
    ensure_case_access(case)
    action = db.session.get(WorkflowResearchAction, action_id)
    if not action or action.case_id != case_id:
        return jsonify({"error": "Not found"}), 404
    if action.status != "running":
        return jsonify({"error": "Action is not running"}), 400
    cancel_action(action_id)
    AuditLog.log(
        user_id=current_user.id,
        action="cancel",
        entity_type="research_action",
        entity_id=action_id,
        ip_address=request.remote_addr,
        description=f"Workflow cancelled action: {action.label}",
    )
    return jsonify({"ok": True})


@workflow_bp.route("/api/case/<case_id>/actions/<action_id>/delete", methods=["POST"])
@login_required
@_investigator_required
def delete_action_api(case_id, action_id):
    case = db.session.get(WorkflowCase, case_id)
    if not case:
        return jsonify({"error": "Not found"}), 404
    ensure_case_access(case)
    action = db.session.get(WorkflowResearchAction, action_id)
    if not action or action.case_id != case_id:
        return jsonify({"error": "Not found"}), 404
    if action.status != "proposal":
        return jsonify({"error": "Only proposals can be deleted"}), 400
    label = action.label
    db.session.delete(action)
    AuditLog.log(
        user_id=current_user.id,
        action="delete",
        entity_type="research_action",
        entity_id=action_id,
        ip_address=request.remote_addr,
        case_id=case_id,
        description=f"Workflow deleted proposal: {label}",
    )
    db.session.commit()
    return jsonify({"ok": True})


@workflow_bp.route(
    "/api/case/<case_id>/findings/<finding_id>/screenshots", methods=["POST"]
)
@login_required
@_investigator_required
def add_screenshot(case_id, finding_id):
    finding = db.session.get(WorkflowFinding, finding_id)
    if not finding or finding.case_id != case_id:
        return jsonify({"error": "Not found"}), 404
    case = db.session.get(WorkflowCase, case_id)
    if case:
        ensure_case_access(case)

    url = ""
    source_url = ""
    notes = ""
    file_path = ""

    if request.content_type and "multipart/form-data" in request.content_type:
        source_url = request.form.get("source_url", "")
        notes = request.form.get("notes", "")
        file = request.files.get("file")
        if file and file.filename:
            from cms.image_validation import validate_upload

            ext = (os.path.splitext(file.filename)[1] or ".png").lower().lstrip(".")
            if ext not in ("png", "jpg", "jpeg", "gif", "webp"):
                return jsonify({"error": "Only image files are allowed"}), 400
            is_valid, detected = validate_upload(file, ext)
            if not is_valid:
                return jsonify({"error": "File content does not match extension"}), 400
            stored_name = f"{uuid.uuid4()}.{ext}"
            finding_dir = os.path.join(SCREENSHOT_DIR, finding_id)
            os.makedirs(finding_dir, exist_ok=True)
            dest = os.path.join(finding_dir, stored_name)
            file.save(dest)
            file_path = dest
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
        tenant_id=current_user.tenant_id,
    )
    db.session.add(ss)
    AuditLog.log(
        user_id=current_user.id,
        action="create",
        entity_type="finding_screenshot",
        entity_id=ss.id,
        ip_address=request.remote_addr,
        description=f"Workflow added screenshot to finding {finding_id}",
    )
    db.session.commit()
    ss_data = {
        "url": ss.url,
        "source_url": ss.source_url,
        "captured_at": ss.captured_at.isoformat() if ss.captured_at else None,
        "notes": ss.notes,
    }
    return jsonify({"ok": True, "screenshot": ss_data})


@workflow_bp.route("/uploads/<finding_id>/<filename>")
@login_required
def serve_screenshot(finding_id, filename):
    from werkzeug.utils import secure_filename

    finding = db.session.get(WorkflowFinding, finding_id)
    if not finding:
        abort(404)
    case = db.session.get(WorkflowCase, finding.case_id)
    if case:
        ensure_case_access(case)
    else:
        ss = WorkflowScreenshot.query.filter_by(
            finding_id=finding_id, tenant_id=current_user.tenant_id
        ).first()
        if not ss:
            abort(404)
    safe_name = secure_filename(filename)
    if not safe_name:
        abort(404)
    finding_dir = os.path.join(SCREENSHOT_DIR, finding_id)
    return send_from_directory(finding_dir, safe_name)


# ── Archive / Restore ───────────────────────────────────────────────────────


@workflow_bp.route("/api/actions/<action_id>/archive", methods=["POST"])
@login_required
def archive_action(action_id):
    """Archive a single research action and its linked findings."""
    from cms.models import ActionFinding, Finding, ResearchAction, db

    action = db.session.get(ResearchAction, action_id) or abort(404)
    case = db.session.get(WorkflowCase, action.case_id)
    if case:
        ensure_case_access(case)
    else:
        ensure_tenant_access(action)
    now = datetime.now(UTC)
    action.archived_at = now
    AuditLog.log(
        user_id=current_user.id,
        action="archive",
        entity_type="research_action",
        entity_id=action.id,
        ip_address=request.remote_addr,
        description=f"Workflow archived action: {action.label} (case {action.case_id})",
    )
    finding_ids = [
        af.finding_id for af in ActionFinding.query.filter_by(action_id=action.id)
    ]
    if finding_ids:
        Finding.query.filter(
            Finding.id.in_(finding_ids), Finding.archived_at.is_(None)
        ).update({"archived_at": now}, synchronize_session=False)
    db.session.commit()
    if request.is_json:
        return jsonify({"ok": True})
    flash("Action archived.", "info")
    return redirect(
        request.referrer or url_for("workflow.case_detail", case_id=action.case_id)
    )


@workflow_bp.route("/api/actions/<action_id>/restore", methods=["POST"])
@login_required
def restore_action(action_id):
    """Restore a research action and its linked findings."""
    from cms.models import ActionFinding, Finding, ResearchAction, db

    action = db.session.get(ResearchAction, action_id) or abort(404)
    case = db.session.get(WorkflowCase, action.case_id)
    if case:
        ensure_case_access(case)
    else:
        ensure_tenant_access(action)
    AuditLog.log(
        user_id=current_user.id,
        action="restore",
        entity_type="research_action",
        entity_id=action.id,
        ip_address=request.remote_addr,
        description=f"Workflow restored action: {action.label} (case {action.case_id})",
    )
    action.archived_at = None
    finding_ids = [
        af.finding_id for af in ActionFinding.query.filter_by(action_id=action.id)
    ]
    if finding_ids:
        Finding.query.filter(Finding.id.in_(finding_ids)).update(
            {"archived_at": None}, synchronize_session=False
        )
    db.session.commit()
    if request.is_json:
        return jsonify({"ok": True})
    flash("Action restored.", "info")
    return redirect(
        request.referrer or url_for("workflow.case_detail", case_id=action.case_id)
    )


@workflow_bp.route("/api/findings/<finding_id>/archive", methods=["POST"])
@login_required
def archive_finding(finding_id):
    """Archive a single finding."""
    from cms.models import Finding, db

    finding = db.session.get(Finding, finding_id) or abort(404)
    case = db.session.get(WorkflowCase, finding.case_id)
    if case:
        ensure_case_access(case)
    else:
        ensure_tenant_access(finding)
    finding.archived_at = datetime.now(UTC)
    AuditLog.log(
        user_id=current_user.id,
        action="archive",
        entity_type="finding",
        entity_id=finding.id,
        ip_address=request.remote_addr,
        description=f"Workflow archived finding: {finding.title}",
    )
    db.session.commit()
    if request.is_json:
        return jsonify({"ok": True})
    flash("Finding archived.", "info")
    return redirect(
        request.referrer or url_for("workflow.case_detail", case_id=finding.case_id)
    )


@workflow_bp.route("/api/findings/<finding_id>/restore", methods=["POST"])
@login_required
def restore_finding(finding_id):
    """Restore an archived finding."""
    from cms.models import Finding, db

    finding = db.session.get(Finding, finding_id) or abort(404)
    case = db.session.get(WorkflowCase, finding.case_id)
    if case:
        ensure_case_access(case)
    else:
        ensure_tenant_access(finding)
    finding.archived_at = None
    AuditLog.log(
        user_id=current_user.id,
        action="restore",
        entity_type="finding",
        entity_id=finding.id,
        ip_address=request.remote_addr,
        description=f"Workflow restored finding: {finding.title}",
    )
    db.session.commit()
    if request.is_json:
        return jsonify({"ok": True})
    flash("Finding restored.", "info")
    return redirect(
        request.referrer or url_for("workflow.case_detail", case_id=finding.case_id)
    )


def _get_writable_investigation(investigation_id):
    """Fetch an investigation and enforce case access; ``None`` when missing."""
    investigation = db.session.get(Investigation, investigation_id)
    if not investigation:
        return None
    case = (
        db.session.get(WorkflowCase, investigation.case_id)
        if investigation.case_id
        else None
    )
    if not case:
        return None
    ensure_case_access(case)
    return investigation


@workflow_bp.route("/api/investigations/<investigation_id>/archive", methods=["POST"])
@login_required
@_investigator_required
def archive_investigation(investigation_id):
    """Archive an investigation. Never mutates number, case_id or tenant_id."""
    investigation = _get_writable_investigation(investigation_id)
    if not investigation:
        return jsonify({"error": "Not found"}), 404

    if (
        investigation.archived_at is not None
        or investigation.status == InvestigationStatus.ARCHIVED.value
    ):
        if request.is_json:
            return jsonify({"error": "Investigation is already archived"}), 409
        flash(_("Investigation is already archived."), "warning")
        return redirect(
            request.referrer
            or url_for("workflow.case_detail", case_id=investigation.case_id)
        )

    investigation.archived_at = datetime.now(UTC)
    investigation.status = InvestigationStatus.ARCHIVED.value
    AuditLog.log(
        user_id=current_user.id,
        action="archive",
        entity_type="investigation",
        entity_id=investigation.id,
        ip_address=request.remote_addr,
        case_id=investigation.case_id,
        description=f"Workflow archived investigation: {investigation.human_number}",
    )
    db.session.commit()
    if request.is_json:
        return jsonify({"ok": True})
    flash(_("Investigation archived."), "info")
    return redirect(
        request.referrer or url_for("workflow.case_detail", case_id=investigation.case_id)
    )


@workflow_bp.route("/api/investigations/<investigation_id>/restore", methods=["POST"])
@login_required
@_investigator_required
def restore_investigation(investigation_id):
    """Restore an archived investigation."""
    investigation = _get_writable_investigation(investigation_id)
    if not investigation:
        return jsonify({"error": "Not found"}), 404

    if investigation.archived_at is None and (
        investigation.status != InvestigationStatus.ARCHIVED.value
    ):
        if request.is_json:
            return jsonify({"error": "Investigation is not archived"}), 409
        flash(_("Investigation is not archived."), "warning")
        return redirect(
            request.referrer
            or url_for("workflow.case_detail", case_id=investigation.case_id)
        )

    investigation.archived_at = None
    investigation.status = InvestigationStatus.OPEN.value
    AuditLog.log(
        user_id=current_user.id,
        action="restore",
        entity_type="investigation",
        entity_id=investigation.id,
        ip_address=request.remote_addr,
        case_id=investigation.case_id,
        description=f"Workflow restored investigation: {investigation.human_number}",
    )
    db.session.commit()
    if request.is_json:
        return jsonify({"ok": True})
    flash(_("Investigation restored."), "info")
    return redirect(
        request.referrer or url_for("workflow.case_detail", case_id=investigation.case_id)
    )
