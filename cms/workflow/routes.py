import json
import os
import uuid
from datetime import datetime, timezone

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
from flask_login import current_user, login_required

from cms.models import db, UserRole, AuditLog
from cms.auth import ensure_tenant_access
from cms.routes.utils import normalize_phone, normalize_postcode
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
    cancel_action,
    start_action_async,
    get_remaining_credits,
)
from cms.routes.dashboard import _get_cached_health
from cms.services.invoice_service import auto_invoice_case_created
from cms.routes.utils import find_similar_clients


def _investigator_required(f):
    from functools import wraps

    @wraps(f)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated:
            return jsonify({"error": "Forbidden"}), 403
        if current_user.role not in (
            UserRole.INVESTIGATOR.value,
            UserRole.SENIOR_INVESTIGATOR.value,
            UserRole.ADMIN.value,
            UserRole.OWNER.value,
        ):
            return jsonify({"error": "Forbidden"}), 403
        return f(*args, **kwargs)

    return wrapper


def _combine_address(prefix: str) -> str:
    """Combine separate address form fields into a single address string."""
    parts = [
        request.form.get(f"{prefix}_street", ""),
        request.form.get(f"{prefix}_house_number", ""),
        request.form.get(f"{prefix}_house_number_addition", ""),
    ]
    street = " ".join(p for p in parts if p)
    pc = request.form.get(f"{prefix}_postal_code", "")
    city = request.form.get(f"{prefix}_city", "")
    loc = " ".join(p for p in [pc, city] if p)
    return ", ".join(p for p in [street, loc] if p)


def _combine_address_number(number: str, addition: str) -> str:
    """Combine house number and addition into a single field (e.g. '45A')."""
    n = number.strip()
    a = addition.strip()
    if not n and not a:
        return ""
    if not a:
        return n
    return n + a


def _set_address_fields(obj, prefix: str) -> None:
    """Set all address fields (combined + individual) on a subject/client from form data."""
    obj.address = _combine_address(prefix)
    obj.street = request.form.get(f"{prefix}_street", "")
    obj.house_number = request.form.get(f"{prefix}_house_number", "")
    obj.house_number_addition = request.form.get(f"{prefix}_house_number_addition", "")
    obj.postal_code = normalize_postcode(request.form.get(f"{prefix}_postal_code", ""))
    obj.city = request.form.get(f"{prefix}_city", "")


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


def _set_vehicle_fields(obj, prefix: str) -> None:
    """Set vehicle-specific fields from form data."""
    obj.brand = request.form.get(f"{prefix}_brand", "")
    obj.vehicle_type = request.form.get(f"{prefix}_vehicle_type", "")
    obj.license_plate = request.form.get(f"{prefix}_identification", "")
    obj.vin = request.form.get(f"{prefix}_vin", "")
    obj.insurance_company = request.form.get(f"{prefix}_insurance_company", "")
    rdw_data = obj.rdw_data or {}
    for field in _VEHICLE_RDW_FIELDS:
        val = request.form.get(f"{prefix}_{field}", "")
        if val:
            rdw_data[field] = val
    obj.rdw_data = rdw_data if rdw_data else None


def _vehicle_auto_name(prefix: str) -> str:
    """Build a display name for a vehicle from form fields."""
    brand = request.form.get(f"{prefix}_brand", "").strip()
    model = request.form.get(f"{prefix}_handelsbenaming", "").strip()
    kenteken = request.form.get(f"{prefix}_identification", "").strip()
    parts = [p for p in [brand, model] if p]
    if kenteken:
        parts.append(f"({kenteken})")
    return " ".join(parts) if parts else "Voertuig"


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
            return [
                p.strip() if p.strip().startswith("@") else "@" + p.strip()
                for p in raw.split(",")
                if p.strip()
            ]

        def _int_field(name, default=0):
            try:
                return int(request.form.get(name, default) or default)
            except (ValueError, TypeError):
                return default

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
            subj_type = request.form.get(f"{idx_str}_type", "person")
            raw_name = request.form.get(f"{idx_str}_name", "").strip()
            if not raw_name and subj_type == "vehicle":
                raw_name = _vehicle_auto_name(idx_str)
            # Auto-prepend @ for online entity names
            if subj_type == "online" and raw_name and not raw_name.startswith("@"):
                raw_name = "@" + raw_name
            s = WorkflowSubject(
                id=str(uuid.uuid4()),
                name=raw_name or "Onbekend",
                subject_type=subj_type,
                identification_number=request.form.get(f"{idx_str}_identification", ""),
                email=request.form.get(f"{idx_str}_email", ""),
                phone=normalize_phone(request.form.get(f"{idx_str}_phone", "")),
                date_of_birth=request.form.get(f"{idx_str}_date_of_birth", ""),
                place_of_birth=request.form.get(f"{idx_str}_place_of_birth", ""),
                nationality=request.form.get(f"{idx_str}_nationality", ""),
                bank_account=request.form.get(f"{idx_str}_bank_account", ""),
                workflow_social_accounts=_social_field(f"{idx_str}_social_accounts"),
                risk_score=_int_field(f"{idx_str}_risk_score"),
                notes=request.form.get(f"{idx_str}_notes", ""),
                created_by=current_user.id,
                tenant_id=current_user.tenant_id,
                tussenvoegsels=request.form.get(f"{idx_str}_tussenvoegsels", ""),
                voornamen=request.form.get(f"{idx_str}_voornamen", ""),
                voorletters=request.form.get(f"{idx_str}_voorletters", ""),
                geslacht=request.form.get(f"{idx_str}_geslacht", ""),
                bsn_number=request.form.get(f"{idx_str}_bsn_number", ""),
                reisdocument_type=request.form.get(f"{idx_str}_reisdocument_type", ""),
                reisdocument_nummer=request.form.get(
                    f"{idx_str}_reisdocument_nummer", ""
                ),
            )
            _set_address_fields(s, idx_str)
            if subj_type == "vehicle":
                _set_vehicle_fields(s, idx_str)
            s.encrypt_identifiers()
            return s

        subjects = []
        idx = 0
        while request.form.get(f"subject_{idx}_name") or request.form.get(
            f"subject_{idx}_type"
        ):
            subjects.append(_make_subject(f"subject_{idx}"))
            idx += 1
        if not subjects:
            subjects.append(_make_subject("subject"))

        raw_number = request.form.get("case_number", "").strip()
        if not raw_number:
            raw_number = WorkflowCase.generate_case_number(current_user.tenant_id)
        case = WorkflowCase(
            id=case_id,
            case_number=raw_number,
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
        db.session.commit()

        auto_invoice_case_created(case)

        return redirect(url_for("workflow.case_detail", case_id=case_id))

    return render_template(
        "cms/workflow/workflow_case_new.html",
        generated_case_number=WorkflowCase.generate_case_number(current_user.tenant_id),
    )


@workflow_bp.route("/case/<case_id>")
@login_required
@_investigator_required
def case_detail(case_id):
    case = db.session.get(WorkflowCase, case_id)
    if not case:
        abort(404)
    ensure_tenant_access(case)
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

    client = db.session.get(WorkflowClient, case.client_id) if case.client_id else None
    if client:
        client.decrypt_naw()
    subjects = list(case.subjects)
    for s in subjects:
        s.decrypt_identifiers()
    subjects_data = [
        {
            "name": s.name,
            "subject_type": s.subject_type,
            "email": s.email,
            "phone": s.phone,
            "date_of_birth": s.date_of_birth,
            "place_of_birth": s.place_of_birth,
            "nationality": s.nationality,
            "bank_account": s.bank_account,
            "street": s.street,
            "house_number": s.house_number,
            "house_number_addition": s.house_number_addition,
            "postal_code": s.postal_code,
            "city": s.city,
            "workflow_social_accounts": s.workflow_social_accounts,
            "identification_number": s.identification_number,
            "imo_number": s.imo_number,
            "mmsi": s.mmsi,
            "eni_number": s.eni_number,
            "vessel_nationality": s.vessel_nationality,
        }
        for s in subjects
        if s
    ]
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
        action_types=ACTION_REGISTRY,
        action_credits=action_credits,
        brave_health=brave_health,
        show_archived=show_archived,
        dorks_library=dorks_library,
    )


@workflow_bp.route("/case/<case_id>/edit", methods=["GET", "POST"])
@login_required
@_investigator_required
def case_edit(case_id):
    case = db.session.get(WorkflowCase, case_id)
    if not case:
        abort(404)
    ensure_tenant_access(case)

    client = db.session.get(WorkflowClient, case.client_id) if case.client_id else None
    if client:
        client.decrypt_naw()
    subjects = list(case.subjects)
    for s in subjects:
        s.decrypt_identifiers()

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
            return [
                p.strip() if p.strip().startswith("@") else "@" + p.strip()
                for p in raw.split(",")
                if p.strip()
            ]

        def _int_field(name, default=0):
            try:
                return int(request.form.get(name, default) or default)
            except (ValueError, TypeError):
                return default

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
            subj = db.session.get(WorkflowSubject, sid)
            if not subj:
                continue
            subj.name = request.form.get(f"subj_{sid}_name", subj.name)
            subj.subject_type = request.form.get(f"subj_{sid}_type", "person")
            # Auto-prepend @ for online entity names
            if (
                subj.subject_type == "online"
                and subj.name
                and not subj.name.startswith("@")
            ):
                subj.name = "@" + subj.name
            subj.identification_number = request.form.get(
                f"subj_{sid}_identification", ""
            )
            subj.email = request.form.get(f"subj_{sid}_email", "")
            subj.phone = normalize_phone(request.form.get(f"subj_{sid}_phone", ""))
            subj.date_of_birth = request.form.get(f"subj_{sid}_date_of_birth", "")
            subj.place_of_birth = request.form.get(f"subj_{sid}_place_of_birth", "")
            subj.nationality = request.form.get(f"subj_{sid}_nationality", "")
            subj.bank_account = request.form.get(f"subj_{sid}_bank_account", "")
            subj.tussenvoegsels = request.form.get(f"subj_{sid}_tussenvoegsels", "")
            subj.voornamen = request.form.get(f"subj_{sid}_voornamen", "")
            subj.voorletters = request.form.get(f"subj_{sid}_voorletters", "")
            subj.geslacht = request.form.get(f"subj_{sid}_geslacht", "")
            subj.bsn_number = request.form.get(f"subj_{sid}_bsn_number", "")
            subj.reisdocument_type = request.form.get(
                f"subj_{sid}_reisdocument_type", ""
            )
            subj.reisdocument_nummer = request.form.get(
                f"subj_{sid}_reisdocument_nummer", ""
            )
            _set_address_fields(subj, f"subj_{sid}")
            if subj.subject_type == "vehicle":
                _set_vehicle_fields(subj, f"subj_{sid}")
            subj.workflow_social_accounts = _social_field(f"subj_{sid}_social_accounts")
            subj.risk_score = _int_field(f"subj_{sid}_risk_score")
            subj.notes = request.form.get(f"subj_{sid}_notes", "")
            subj.encrypt_identifiers()

        # process new subjects
        n = 0
        while request.form.get(f"subj_new_{n}_name") or request.form.get(
            f"subj_new_{n}_type"
        ):
            new_subj_type = request.form.get(f"subj_new_{n}_type", "person")
            raw_name = request.form.get(f"subj_new_{n}_name", "").strip()
            if not raw_name and new_subj_type == "vehicle":
                raw_name = _vehicle_auto_name(f"subj_new_{n}")
            # Auto-prepend @ for online entity names
            if new_subj_type == "online" and raw_name and not raw_name.startswith("@"):
                raw_name = "@" + raw_name
            new_subj = WorkflowSubject(
                id=str(uuid.uuid4()),
                name=raw_name or "Onbekend",
                subject_type=new_subj_type,
                identification_number=request.form.get(
                    f"subj_new_{n}_identification", ""
                ),
                email=request.form.get(f"subj_new_{n}_email", ""),
                phone=normalize_phone(request.form.get(f"subj_new_{n}_phone", "")),
                date_of_birth=request.form.get(f"subj_new_{n}_date_of_birth", ""),
                place_of_birth=request.form.get(f"subj_new_{n}_place_of_birth", ""),
                nationality=request.form.get(f"subj_new_{n}_nationality", ""),
                bank_account=request.form.get(f"subj_new_{n}_bank_account", ""),
                workflow_social_accounts=_social_field(f"subj_new_{n}_social_accounts"),
                risk_score=_int_field(f"subj_new_{n}_risk_score"),
                notes=request.form.get(f"subj_new_{n}_notes", ""),
                created_by=current_user.id,
                tussenvoegsels=request.form.get(f"subj_new_{n}_tussenvoegsels", ""),
                voornamen=request.form.get(f"subj_new_{n}_voornamen", ""),
                voorletters=request.form.get(f"subj_new_{n}_voorletters", ""),
                geslacht=request.form.get(f"subj_new_{n}_geslacht", ""),
                bsn_number=request.form.get(f"subj_new_{n}_bsn_number", ""),
                reisdocument_type=request.form.get(
                    f"subj_new_{n}_reisdocument_type", ""
                ),
                reisdocument_nummer=request.form.get(
                    f"subj_new_{n}_reisdocument_nummer", ""
                ),
            )
            _set_address_fields(new_subj, f"subj_new_{n}")
            if new_subj_type == "vehicle":
                _set_vehicle_fields(new_subj, f"subj_new_{n}")
            new_subj.encrypt_identifiers()
            db.session.add(new_subj)
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

    return render_template(
        "cms/workflow/workflow_case_edit.html",
        case=case,
        client=client,
        subjects=subjects,
    )


@workflow_bp.route("/api/case/<case_id>/run-action", methods=["POST"])
@login_required
@_investigator_required
def run_action(case_id):
    case = db.session.get(WorkflowCase, case_id)
    if not case:
        return jsonify({"error": "Case not found"}), 404
    ensure_tenant_access(case)

    body = request.get_json(silent=True) or {}
    action_type = body.get("action_type", "")
    data_value = body.get("data_value") or ""

    if action_type not in ACTION_REGISTRY:
        return jsonify({"error": f"Unknown action: {action_type}"}), 400

    _STALE_TIMEOUT = 600  # 10 minutes
    existing = WorkflowResearchAction.query.filter_by(
        case_id=case_id, action_type=action_type, status="running"
    ).first()
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
        action_type=action_type,
        data_value=data_value,
        label=ACTION_REGISTRY[action_type]["label"],
        status="pending",
        tenant_id=current_user.tenant_id,
    )
    db.session.add(action)
    db.session.commit()
    action_id = action.id

    start_action_async(action_id)
    return jsonify({"id": action_id, "status": "started"})


@workflow_bp.route("/api/case/<case_id>/photo-analysis", methods=["POST"])
@login_required
@_investigator_required
def photo_analysis_upload(case_id):
    case = db.session.get(WorkflowCase, case_id)
    if not case:
        return jsonify({"error": "Case not found"}), 404
    ensure_tenant_access(case)

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

    _STALE_TIMEOUT = 600
    existing = WorkflowResearchAction.query.filter_by(
        case_id=case_id, action_type="photo_analysis", status="running"
    ).first()
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
        action_type="photo_analysis",
        data_value=data_value,
        label=ACTION_REGISTRY["photo_analysis"]["label"],
        status="pending",
        tenant_id=current_user.tenant_id,
    )
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
    ensure_tenant_access(case)

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
    ensure_tenant_access(case)

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
                    "status": a.status,
                    "error": a.error,
                    "data_value": a.data_value,
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
    ensure_tenant_access(case)
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
    ensure_tenant_access(case)
    client = db.session.get(WorkflowClient, case.client_id) if case.client_id else None
    subjects = list(case.subjects)
    findings = (
        case.findings.filter_by(is_deleted=False, archived_at=None)
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
    ensure_tenant_access(case)

    findings = (
        case.findings.filter_by(is_deleted=False, archived_at=None)
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
    ensure_tenant_access(case)

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
        db.session.commit()

        if was_empty and case.pv_body:
            from cms.services.invoice_service import auto_invoice_pv_created

            auto_invoice_pv_created(case)

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
        ensure_tenant_access(case)
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
    ensure_tenant_access(case)
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


@workflow_bp.route("/api/case/<case_id>/findings/<finding_id>/verify", methods=["POST"])
@login_required
@_investigator_required
def verify_finding(case_id, finding_id):
    finding = db.session.get(WorkflowFinding, finding_id)
    if not finding or finding.case_id != case_id:
        return jsonify({"error": "Not found"}), 404
    case = db.session.get(WorkflowCase, case_id)
    if case:
        ensure_tenant_access(case)
    body = request.get_json(silent=True) or {}
    new_val = body.get("verified", not finding.verified)
    finding.verified = new_val
    AuditLog.log(
        user_id=current_user.id,
        action="verify",
        entity_type="finding",
        entity_id=finding.id,
        ip_address=request.remote_addr,
        description=f"Workflow {'verified' if new_val else 'unverified'} finding: {finding.title}",
    )
    db.session.commit()
    return jsonify({"ok": True, "verified": new_val})


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
        ensure_tenant_access(case)
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


@workflow_bp.route("/api/case/<case_id>/actions/<action_id>/cancel", methods=["POST"])
@login_required
@_investigator_required
def cancel_action_api(case_id, action_id):
    case = db.session.get(WorkflowCase, case_id)
    if not case:
        return jsonify({"error": "Not found"}), 404
    ensure_tenant_access(case)
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
        ensure_tenant_access(case)

    url = ""
    source_url = ""
    notes = ""
    file_path = ""

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
    finding_dir = os.path.join(SCREENSHOT_DIR, finding_id)
    return send_from_directory(finding_dir, filename)


# ── Archive / Restore ───────────────────────────────────────────────────────


@workflow_bp.route("/api/actions/<action_id>/archive", methods=["POST"])
@login_required
def archive_action(action_id):
    """Archive a single research action and its linked findings."""
    from cms.models import ResearchAction, Finding, ActionFinding, db

    action = db.session.get(ResearchAction, action_id) or abort(404)
    ensure_tenant_access(action)
    now = datetime.now(timezone.utc)
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
    from cms.models import ResearchAction, Finding, ActionFinding, db

    action = db.session.get(ResearchAction, action_id) or abort(404)
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
    ensure_tenant_access(finding)
    finding.archived_at = datetime.now(timezone.utc)
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
