"""
Profile write API (ADR-0001 PR7b).

Inline add/edit/delete endpoints for the tabbed Subject Profile, all gated by
the ``subject_first_investigations`` feature flag (404 when off). Writes go
through ``SubjectService`` — the single write path — including the PR3
fact-layer models (identifiers/facts) and the structured children
(addresses/contacts/social accounts) with provenance. Relations reuse the
canonical single-row storage (PR3).
"""

import json
import logging
import functools
import uuid
from typing import Any

import flask
from flask import request, jsonify, abort
from flask_login import login_required, current_user
from pydantic import BaseModel

from . import cms_bp
from ..models import db, Subject, AuditLog
from ..auth import roles_required, subject_access_required
from ..tier_limits import check_feature
from ..services.subject_service import subject_service
from ..validation import validate, EditSubjectSchema
from .response import api_success, api_error

logger = logging.getLogger(__name__)

_PROFILE_WRITE_ROLES = (
    "admin",
    "owner",
    "senior_investigator",
    "investigator",
    "junior_investigator",
)


def _profile_required(f):
    """404 when the subject-first profile flag is off (feature hidden)."""

    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        if not check_feature("subject_first_investigations", current_user.tenant_id):
            abort(404)
        return f(*args, **kwargs)

    return wrapper


def _get_subject(subject_id: str) -> Subject:
    subject = db.session.get(Subject, subject_id)
    if not subject:
        abort(404)
    return subject


def _audit(entity_type: str, entity_id: str, action: str, description: str, **extra):
    AuditLog.log(
        user_id=current_user.id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        ip_address=request.remote_addr,
        description=description,
        **extra,
    )


class ProfileIdentifierSchema(BaseModel):
    identifier_type: str = ""
    value: str | None = None
    status: str | None = None
    source: str | None = None
    source_url: str | None = None
    reliability: str | None = None
    observed_at: str | None = None


class ProfileFactSchema(BaseModel):
    fact_key: str = ""
    value: str | None = None
    status: str | None = None
    source: str | None = None
    source_url: str | None = None
    reliability: str | None = None
    observed_at: str | None = None


class ProfileAddressSchema(BaseModel):
    street: str | None = None
    number: str | None = None
    zipcode: str | None = None
    town: str | None = None
    country: str | None = None
    is_primary: Any = None
    source: str | None = None
    status: str | None = None
    observed_at: str | None = None


class ProfileContactSchema(BaseModel):
    contact_type: str = ""
    value: str | None = None
    is_primary: Any = None
    source: str | None = None
    status: str | None = None
    observed_at: str | None = None


class ProfileSocialSchema(BaseModel):
    platform: str = ""
    username: str = ""
    url: str | None = None
    account_id: str | None = None
    source: str | None = None
    status: str | None = None
    observed_at: str | None = None


class ProfileRelationSchema(BaseModel):
    related_subject_id: str = ""
    relation_type: str = "other"
    direction: str = "mutual"
    source: str | None = None
    reliability: str | None = None
    status: str | None = None
    case_number: str | None = None


@cms_bp.route("/api/profile/subjects/<subject_id>", methods=["PATCH"])
@login_required
@subject_access_required
@roles_required(*_PROFILE_WRITE_ROLES)
@_profile_required
@validate(EditSubjectSchema)
def profile_update_subject(subject_id: str) -> flask.Response:
    """Update base subject fields (Overview/Identity/Financial tabs)."""
    subject = _get_subject(subject_id)
    try:
        changes = subject_service.edit(
            subject, request.validated_data, actor_id=current_user.id
        )
    except ValueError as e:
        return api_error(str(e), 400)
    _audit(
        "subject",
        subject_id,
        "update",
        f"Updated subject from profile ({subject.subject_type})",
        changes=changes,
    )
    db.session.commit()
    return jsonify({"message": "Subject updated", "changes": list(changes.keys())})


# ---------------------------------------------------------------------------
# Identifiers
# ---------------------------------------------------------------------------


@cms_bp.route("/api/profile/subjects/<subject_id>/identifiers", methods=["POST"])
@login_required
@subject_access_required
@roles_required(*_PROFILE_WRITE_ROLES)
@_profile_required
@validate(ProfileIdentifierSchema)
def profile_add_identifier(subject_id: str) -> flask.Response:
    subject = _get_subject(subject_id)
    try:
        ident = subject_service.save_identifier(
            subject, request.validated_data, actor_id=current_user.id
        )
    except ValueError as e:
        return api_error(str(e), 400)
    db.session.flush()
    _audit(
        "subject_identifier",
        ident.id,
        "create",
        f"Added {ident.identifier_type} identifier to {subject.name}",
    )
    db.session.commit()
    return jsonify({"message": "Identifier added", "item": ident.to_dict()}), 201


@cms_bp.route(
    "/api/profile/subjects/<subject_id>/identifiers/<item_id>", methods=["PUT"]
)
@login_required
@subject_access_required
@roles_required(*_PROFILE_WRITE_ROLES)
@_profile_required
@validate(ProfileIdentifierSchema)
def profile_update_identifier(subject_id: str, item_id: str) -> flask.Response:
    subject = _get_subject(subject_id)
    try:
        ident = subject_service.save_identifier(
            subject,
            request.validated_data,
            actor_id=current_user.id,
            identifier_id=item_id,
        )
    except ValueError as e:
        return api_error(str(e), 400)
    _audit(
        "subject_identifier",
        item_id,
        "update",
        f"Updated {ident.identifier_type} identifier on {subject.name}",
    )
    db.session.commit()
    return jsonify({"message": "Identifier updated", "item": ident.to_dict()})


@cms_bp.route(
    "/api/profile/subjects/<subject_id>/identifiers/<item_id>", methods=["DELETE"]
)
@login_required
@subject_access_required
@roles_required(*_PROFILE_WRITE_ROLES)
@_profile_required
def profile_delete_identifier(subject_id: str, item_id: str) -> flask.Response:
    subject = _get_subject(subject_id)
    try:
        subject_service.delete_identifier(subject, item_id, actor_id=current_user.id)
    except ValueError as e:
        return api_error(str(e), 400)
    _audit(
        "subject_identifier",
        item_id,
        "delete",
        f"Deleted identifier from {subject.name}",
    )
    db.session.commit()
    return api_success({}, "Identifier deleted")


# ---------------------------------------------------------------------------
# Facts
# ---------------------------------------------------------------------------


@cms_bp.route("/api/profile/subjects/<subject_id>/facts", methods=["POST"])
@login_required
@subject_access_required
@roles_required(*_PROFILE_WRITE_ROLES)
@_profile_required
@validate(ProfileFactSchema)
def profile_add_fact(subject_id: str) -> flask.Response:
    subject = _get_subject(subject_id)
    try:
        fact = subject_service.save_fact(
            subject, request.validated_data, actor_id=current_user.id
        )
    except ValueError as e:
        return api_error(str(e), 400)
    db.session.flush()
    _audit(
        "subject_fact",
        fact.id,
        "create",
        f"Added fact '{fact.fact_key}' to {subject.name}",
    )
    db.session.commit()
    return jsonify({"message": "Fact added", "item": fact.to_dict()}), 201


@cms_bp.route("/api/profile/subjects/<subject_id>/facts/<item_id>", methods=["PUT"])
@login_required
@subject_access_required
@roles_required(*_PROFILE_WRITE_ROLES)
@_profile_required
@validate(ProfileFactSchema)
def profile_update_fact(subject_id: str, item_id: str) -> flask.Response:
    subject = _get_subject(subject_id)
    try:
        fact = subject_service.save_fact(
            subject, request.validated_data, actor_id=current_user.id, fact_id=item_id
        )
    except ValueError as e:
        return api_error(str(e), 400)
    _audit(
        "subject_fact",
        item_id,
        "update",
        f"Updated fact '{fact.fact_key}' on {subject.name}",
    )
    db.session.commit()
    return jsonify({"message": "Fact updated", "item": fact.to_dict()})


@cms_bp.route("/api/profile/subjects/<subject_id>/facts/<item_id>", methods=["DELETE"])
@login_required
@subject_access_required
@roles_required(*_PROFILE_WRITE_ROLES)
@_profile_required
def profile_delete_fact(subject_id: str, item_id: str) -> flask.Response:
    subject = _get_subject(subject_id)
    try:
        subject_service.delete_fact(subject, item_id, actor_id=current_user.id)
    except ValueError as e:
        return api_error(str(e), 400)
    _audit("subject_fact", item_id, "delete", f"Deleted fact from {subject.name}")
    db.session.commit()
    return api_success({}, "Fact deleted")


# ---------------------------------------------------------------------------
# Addresses
# ---------------------------------------------------------------------------


@cms_bp.route("/api/profile/subjects/<subject_id>/addresses", methods=["POST"])
@login_required
@subject_access_required
@roles_required(*_PROFILE_WRITE_ROLES)
@_profile_required
@validate(ProfileAddressSchema)
def profile_add_address(subject_id: str) -> flask.Response:
    subject = _get_subject(subject_id)
    try:
        addr = subject_service.save_address(
            subject, request.validated_data, actor_id=current_user.id
        )
    except ValueError as e:
        return api_error(str(e), 400)
    db.session.flush()
    _audit("address", addr.id, "create", f"Added address to {subject.name}")
    db.session.commit()
    return jsonify(
        {"message": "Address added", "item": addr.to_dict(decrypted=True)}
    ), 201


@cms_bp.route("/api/profile/subjects/<subject_id>/addresses/<item_id>", methods=["PUT"])
@login_required
@subject_access_required
@roles_required(*_PROFILE_WRITE_ROLES)
@_profile_required
@validate(ProfileAddressSchema)
def profile_update_address(subject_id: str, item_id: str) -> flask.Response:
    subject = _get_subject(subject_id)
    try:
        addr = subject_service.save_address(
            subject,
            request.validated_data,
            actor_id=current_user.id,
            address_id=item_id,
        )
    except ValueError as e:
        return api_error(str(e), 400)
    _audit("address", item_id, "update", f"Updated address on {subject.name}")
    db.session.commit()
    return jsonify({"message": "Address updated", "item": addr.to_dict(decrypted=True)})


@cms_bp.route(
    "/api/profile/subjects/<subject_id>/addresses/<item_id>", methods=["DELETE"]
)
@login_required
@subject_access_required
@roles_required(*_PROFILE_WRITE_ROLES)
@_profile_required
def profile_delete_address(subject_id: str, item_id: str) -> flask.Response:
    subject = _get_subject(subject_id)
    try:
        subject_service.delete_address(subject, item_id, actor_id=current_user.id)
    except ValueError as e:
        return api_error(str(e), 400)
    _audit("address", item_id, "delete", f"Deleted address from {subject.name}")
    db.session.commit()
    return api_success({}, "Address deleted")


# ---------------------------------------------------------------------------
# Contacts
# ---------------------------------------------------------------------------


@cms_bp.route("/api/profile/subjects/<subject_id>/contacts", methods=["POST"])
@login_required
@subject_access_required
@roles_required(*_PROFILE_WRITE_ROLES)
@_profile_required
@validate(ProfileContactSchema)
def profile_add_contact(subject_id: str) -> flask.Response:
    subject = _get_subject(subject_id)
    try:
        contact = subject_service.save_contact(
            subject, request.validated_data, actor_id=current_user.id
        )
    except ValueError as e:
        return api_error(str(e), 400)
    db.session.flush()
    _audit(
        "contact",
        contact.id,
        "create",
        f"Added {contact.contact_type} to {subject.name}",
    )
    db.session.commit()
    return jsonify(
        {"message": "Contact added", "item": contact.to_dict(decrypted=True)}
    ), 201


@cms_bp.route("/api/profile/subjects/<subject_id>/contacts/<item_id>", methods=["PUT"])
@login_required
@subject_access_required
@roles_required(*_PROFILE_WRITE_ROLES)
@_profile_required
@validate(ProfileContactSchema)
def profile_update_contact(subject_id: str, item_id: str) -> flask.Response:
    subject = _get_subject(subject_id)
    try:
        contact = subject_service.save_contact(
            subject,
            request.validated_data,
            actor_id=current_user.id,
            contact_id=item_id,
        )
    except ValueError as e:
        return api_error(str(e), 400)
    _audit("contact", item_id, "update", f"Updated contact on {subject.name}")
    db.session.commit()
    return jsonify(
        {"message": "Contact updated", "item": contact.to_dict(decrypted=True)}
    )


@cms_bp.route(
    "/api/profile/subjects/<subject_id>/contacts/<item_id>", methods=["DELETE"]
)
@login_required
@subject_access_required
@roles_required(*_PROFILE_WRITE_ROLES)
@_profile_required
def profile_delete_contact(subject_id: str, item_id: str) -> flask.Response:
    subject = _get_subject(subject_id)
    try:
        subject_service.delete_contact(subject, item_id, actor_id=current_user.id)
    except ValueError as e:
        return api_error(str(e), 400)
    _audit("contact", item_id, "delete", f"Deleted contact from {subject.name}")
    db.session.commit()
    return api_success({}, "Contact deleted")


# ---------------------------------------------------------------------------
# Social accounts
# ---------------------------------------------------------------------------


@cms_bp.route("/api/profile/subjects/<subject_id>/social-accounts", methods=["POST"])
@login_required
@subject_access_required
@roles_required(*_PROFILE_WRITE_ROLES)
@_profile_required
@validate(ProfileSocialSchema)
def profile_add_social_account(subject_id: str) -> flask.Response:
    subject = _get_subject(subject_id)
    try:
        account = subject_service.save_social_account(
            subject, request.validated_data, actor_id=current_user.id
        )
    except ValueError as e:
        return api_error(str(e), 400)
    db.session.flush()
    _audit(
        "social_account",
        account.id,
        "create",
        f"Added {account.platform} account {account.username} to {subject.name}",
    )
    db.session.commit()
    return jsonify({"message": "Social account added", "item": account.to_dict()}), 201


@cms_bp.route(
    "/api/profile/subjects/<subject_id>/social-accounts/<item_id>", methods=["PUT"]
)
@login_required
@subject_access_required
@roles_required(*_PROFILE_WRITE_ROLES)
@_profile_required
@validate(ProfileSocialSchema)
def profile_update_social_account(subject_id: str, item_id: str) -> flask.Response:
    subject = _get_subject(subject_id)
    try:
        account = subject_service.save_social_account(
            subject,
            request.validated_data,
            actor_id=current_user.id,
            account_id=item_id,
        )
    except ValueError as e:
        return api_error(str(e), 400)
    _audit(
        "social_account", item_id, "update", f"Updated social account on {subject.name}"
    )
    db.session.commit()
    return jsonify({"message": "Social account updated", "item": account.to_dict()})


@cms_bp.route(
    "/api/profile/subjects/<subject_id>/social-accounts/<item_id>", methods=["DELETE"]
)
@login_required
@subject_access_required
@roles_required(*_PROFILE_WRITE_ROLES)
@_profile_required
def profile_delete_social_account(subject_id: str, item_id: str) -> flask.Response:
    subject = _get_subject(subject_id)
    try:
        subject_service.delete_social_account(
            subject, item_id, actor_id=current_user.id
        )
    except ValueError as e:
        return api_error(str(e), 400)
    _audit(
        "social_account",
        item_id,
        "delete",
        f"Deleted social account from {subject.name}",
    )
    db.session.commit()
    return api_success({}, "Social account deleted")


# ---------------------------------------------------------------------------
# Relations
# ---------------------------------------------------------------------------


@cms_bp.route("/api/profile/subjects/<subject_id>/relations", methods=["POST"])
@login_required
@subject_access_required
@roles_required(*_PROFILE_WRITE_ROLES)
@_profile_required
@validate(ProfileRelationSchema)
def profile_add_relation(subject_id: str) -> flask.Response:
    subject = _get_subject(subject_id)
    try:
        pair = subject_service.save_relation(
            subject, request.validated_data, actor_id=current_user.id
        )
    except ValueError as e:
        return api_error(str(e), 400)
    _audit(
        "subject_relation",
        f"{pair['subject_id']}-{pair['related_subject_id']}",
        "create",
        f"Added relationship on {subject.name}",
    )
    db.session.commit()
    return jsonify({"message": "Relationship saved", "pair": pair}), 201


@cms_bp.route(
    "/api/profile/subjects/<subject_id>/relations/<related_subject_id>",
    methods=["DELETE"],
)
@login_required
@subject_access_required
@roles_required(*_PROFILE_WRITE_ROLES)
@_profile_required
def profile_delete_relation(subject_id: str, related_subject_id: str) -> flask.Response:
    subject = _get_subject(subject_id)
    try:
        subject_service.delete_relation(
            subject, related_subject_id, actor_id=current_user.id
        )
    except ValueError as e:
        return api_error(str(e), 400)
    _audit(
        "subject_relation",
        f"{subject.id}-{related_subject_id}",
        "delete",
        f"Removed relationship on {subject.name}",
    )
    db.session.commit()
    return api_success({}, "Relationship removed")


# ---------------------------------------------------------------------------
# Relation candidates (searchable picker)
# ---------------------------------------------------------------------------


@cms_bp.route(
    "/api/profile/subjects/<subject_id>/relation-candidates",
    methods=["GET"],
)
@login_required
@subject_access_required
@_profile_required
def profile_relation_candidates(subject_id: str) -> flask.Response:
    """Searchable subject picker for the Add Relation form.

    Returns subjects from the same tenant, optionally filtered by ``q``.
    Subjects in the same case(s) as the current subject are ranked first.
    """
    from ..models import case_subjects, Case

    subject = _get_subject(subject_id)
    q_param = request.args.get("q", "").strip()
    limit = min(request.args.get("limit", 30, type=int), 100)

    # Case IDs this subject belongs to
    _cs_rows = db.session.execute(
        case_subjects.select().where(
            case_subjects.c.subject_id == subject.id
        )
    ).fetchall()
    import logging as _lg
    _lg.getLogger(__name__).warning(
        "DEBUG relation-candidates: subject=%s cs_rows=%d raw=%s",
        subject.id, len(_cs_rows), [(r.case_id,) for r in _cs_rows],
    )
    same_case_ids = {r.case_id for r in _cs_rows}

    # Already-related subject IDs (exclude from candidates)
    from ..models import subject_relations

    related_ids = {
        row.related_subject_id if row.subject_id == subject.id else row.subject_id
        for row in db.session.execute(
            subject_relations.select().where(
                db.or_(
                    subject_relations.c.subject_id == subject.id,
                    subject_relations.c.related_subject_id == subject.id,
                )
            )
        ).fetchall()
    }

    query = Subject.query.filter(
        Subject.is_deleted.is_(False),
        Subject.tenant_id == current_user.tenant_id,
        Subject.id != subject.id,
    )

    if related_ids:
        query = query.filter(~Subject.id.in_(related_ids))

    if q_param:
        pattern = f"%{q_param}%"
        query = query.filter(
            db.or_(
                Subject.name.ilike(pattern),
                Subject.achternaam.ilike(pattern),
                Subject.voornamen.ilike(pattern),
            )
        )

    from sqlalchemy import case as sa_case

    # Subjects in the same case first, then alphabetical
    same_case_subq = sa_case(
        (Subject.id.in_(same_case_ids), 0),
        else_=1,
    )
    results = (
        query.order_by(same_case_subq, Subject.name)
        .limit(limit)
        .with_entities(Subject.id, Subject.name, Subject.subject_type)
        .all()
    )

    candidates = [
        {"id": r.id, "name": r.name, "subject_type": r.subject_type}
        for r in results
    ]

    # Mark same-case subjects
    candidate_ids = {c["id"] for c in candidates}
    return jsonify(
        {
            "candidates": candidates,
            "same_case_ids": list(same_case_ids & candidate_ids),
            "total": len(candidates),
        }
    )


# ── Run Action from Subject Profile ─────────────────────────────────────────


@cms_bp.route(
    "/api/profile/subjects/<subject_id>/run-action",
    methods=["POST"],
)
@login_required
@subject_access_required
@roles_required(*_PROFILE_WRITE_ROLES)
@_profile_required
def profile_run_action(subject_id: str) -> flask.Response:
    """Start an OSINT research action directly from the subject profile.

    Body JSON: {"action_type": "email", "data_value": "...", "case_id": "..."}
    The subject must be linked to the specified case.
    """
    from cms.workflow.actions.registry import ACTION_REGISTRY
    from cms.workflow.research import (
        is_paid_action,
        paid_channels_enabled,
        start_action_async,
    )
    from cms.workflow.models import WorkflowCase, WorkflowResearchAction
    from cms.auth import ensure_case_access

    subject = _get_subject(subject_id)

    body = request.get_json(silent=True) or {}
    action_type = body.get("action_type", "")
    data_value = body.get("data_value") or ""
    case_id = body.get("case_id") or ""

    if not action_type:
        return api_error("action_type is required", 400)
    if action_type not in ACTION_REGISTRY:
        return api_error(f"Unknown action: {action_type}", 400)
    if not case_id:
        return api_error("case_id is required", 400)

    case = db.session.get(WorkflowCase, case_id)
    if not case:
        return api_error("Case not found", 404)
    ensure_case_access(case)

    # Verify subject is linked to this case
    from cms.workflow.models import WorkflowSubject as WSubject

    ws = db.session.get(WSubject, subject_id)
    if not ws:
        return api_error("WorkflowSubject not found", 404)
    if not case.subjects.filter_by(id=subject_id).first():
        return api_error("Subject is not linked to this case", 400)

    if is_paid_action(action_type) and not paid_channels_enabled():
        return api_error("Paid research channels are disabled for this tenant.", 409)

    # Check for stale running action
    from datetime import datetime

    _STALE_TIMEOUT = 600
    existing = WorkflowResearchAction.query.filter_by(
        case_id=case_id,
        action_type=action_type,
        subject_id=subject_id,
        status="running",
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
            return api_error("Action already running", 409)

    action = WorkflowResearchAction(
        id=str(uuid.uuid4()),
        case_id=case_id,
        subject_id=subject_id,
        target_kind="subject",
        action_type=action_type,
        data_value=data_value,
        label=ACTION_REGISTRY[action_type]["label"],
        status="pending",
        tenant_id=current_user.tenant_id,
    )
    action.target_snapshot = json.dumps(action.build_target_snapshot(ws, data_value))
    db.session.add(action)
    db.session.commit()

    AuditLog.log(
        user_id=current_user.id,
        action="create",
        entity_type="research_action",
        entity_id=action.id,
        ip_address=request.remote_addr,
        description=f"Started {action_type} action from subject profile on {subject.name}",
    )
    db.session.commit()

    start_action_async(action.id)
    return jsonify({"id": action.id, "status": "started"}), 201


# ── Finding review from Subject Profile ──────────────────────────────────────


class _FindingMutationOk:
    """Sentinel return type for _validate_finding_mutation success."""

    __slots__ = ("finding", "case")

    def __init__(self, finding, case):
        self.finding = finding
        self.case = case


def _validate_finding_mutation(subject_id: str, finding_id: str):
    """Shared validation for all finding mutations (review/archive/restore).

    Returns _FindingMutationOk on success or a Flask Response (error) on failure.
    Checks: finding exists, not deleted, belongs to subject, case exists,
    user has case access, subject is linked to the case.
    """
    from cms.models import Finding, Case
    from cms.auth import ensure_case_access

    finding = db.session.get(Finding, finding_id)
    if not finding or finding.is_deleted:
        return api_error("Finding not found", 404)

    if finding.subject_id != subject_id:
        return api_error("Finding does not belong to this subject", 403)

    case = db.session.get(Case, finding.case_id)
    if not case:
        return api_error("Case not found", 404)
    ensure_case_access(case)

    from cms.models import case_subjects

    link = db.session.execute(
        case_subjects.select().where(
            case_subjects.c.case_id == case.id,
            case_subjects.c.subject_id == subject_id,
        )
    ).first()
    if not link:
        return api_error("Subject is not linked to this case", 403)

    return _FindingMutationOk(finding, case)


@cms_bp.route(
    "/api/profile/subjects/<subject_id>/findings/<finding_id>/review",
    methods=["POST"],
)
@login_required
@subject_access_required
@roles_required(*_PROFILE_WRITE_ROLES)
@_profile_required
def profile_review_finding(subject_id: str, finding_id: str) -> flask.Response:
    """Review (verify/reject/restore) a finding from the Subject Profile.

    Body JSON: {"status": "verified"|"rejected"|"candidate"}
    """
    result = _validate_finding_mutation(subject_id, finding_id)
    if not isinstance(result, _FindingMutationOk):
        return result
    finding, case = result.finding, result.case

    body = request.get_json(silent=True) or {}
    new_status = (body.get("status") or "").strip()
    if new_status not in ("verified", "rejected", "candidate", "superseded"):
        return api_error("Invalid status", 400)

    old_status = finding.status or ("verified" if finding.verified else "candidate")

    if new_status == "verified":
        finding.promote_to_verified(current_user)
    elif new_status == "rejected":
        finding.reject(current_user)
    elif new_status == "superseded":
        finding.status = "superseded"
        finding.verified = False
    else:
        finding.demote_to_candidate()

    _audit(
        "finding",
        finding.id,
        "review",
        f"Finding status {old_status} → {finding.status}: {finding.title}",
        case_id=case.id,
        changes={"status": {"old": old_status, "new": finding.status}},
    )
    db.session.commit()
    return jsonify({"ok": True, "status": finding.status})


@cms_bp.route(
    "/api/profile/subjects/<subject_id>/findings/<finding_id>/archive",
    methods=["POST"],
)
@login_required
@subject_access_required
@roles_required(*_PROFILE_WRITE_ROLES)
@_profile_required
def profile_archive_finding(subject_id: str, finding_id: str) -> flask.Response:
    """Archive a finding from the Subject Profile."""
    result = _validate_finding_mutation(subject_id, finding_id)
    if not isinstance(result, _FindingMutationOk):
        return result
    finding, case = result.finding, result.case

    from datetime import datetime, timezone

    finding.archived_at = datetime.now(timezone.utc)
    _audit(
        "finding",
        finding.id,
        "archive",
        f"Archived finding from subject profile: {finding.title}",
        case_id=case.id,
    )
    db.session.commit()
    return jsonify({"ok": True})


@cms_bp.route(
    "/api/profile/subjects/<subject_id>/findings/<finding_id>/restore",
    methods=["POST"],
)
@login_required
@subject_access_required
@roles_required(*_PROFILE_WRITE_ROLES)
@_profile_required
def profile_restore_finding(subject_id: str, finding_id: str) -> flask.Response:
    """Restore an archived finding from the Subject Profile."""
    result = _validate_finding_mutation(subject_id, finding_id)
    if not isinstance(result, _FindingMutationOk):
        return result
    finding, case = result.finding, result.case

    finding.archived_at = None
    _audit(
        "finding",
        finding.id,
        "restore",
        f"Restored finding from subject profile: {finding.title}",
        case_id=case.id,
    )
    db.session.commit()
    return jsonify({"ok": True})


# ── Read-only Activity / Audit Trail ─────────────────────────────────────────


@cms_bp.route(
    "/api/profile/subjects/<subject_id>/activity",
    methods=["GET"],
)
@login_required
@subject_access_required
@_profile_required
def profile_activity_trail(subject_id: str) -> flask.Response:
    """Return a unified activity timeline for a subject.

    Merges AuditLog entries (profile writes, review actions, case links)
    and ResearchAction entries (OSINT probes) into a single chronologically
    ordered list.  Read-only — no feature-flag gate beyond the profile
    decorator (the trail is visible whenever the profile itself is).
    """
    from ..models import ResearchAction, User

    subject = _get_subject(subject_id)

    # --- AuditLog entries referencing this subject or its children -----------
    entity_types = (
        "subject",
        "subject_identifier",
        "subject_fact",
        "address",
        "contact",
        "social_account",
        "subject_relation",
        "finding",
        "research_action",
    )
    audit_entries = (
        AuditLog.query.filter(
            AuditLog.tenant_id == subject.tenant_id,
            AuditLog.entity_type.in_(entity_types),
            AuditLog.entity_id.isnot(None),
        )
        .filter(
            db.or_(
                # Direct match on entity_id (most profile writes)
                AuditLog.entity_id == subject_id,
                # Entries whose description mentions the subject name
                AuditLog.description.ilike(f"%{subject.name}%"),
            )
        )
        .order_by(AuditLog.timestamp.desc())
        .limit(200)
        .all()
    )

    # --- ResearchActions targeting this subject ------------------------------
    action_entries = (
        ResearchAction.query.filter(
            ResearchAction.tenant_id == subject.tenant_id,
            ResearchAction.subject_id == subject_id,
        )
        .order_by(ResearchAction.created_at.desc())
        .limit(200)
        .all()
    )

    # --- Merge into a unified timeline --------------------------------------
    timeline: list[dict] = []
    seen_ids: set[str] = set()

    for a in audit_entries:
        if a.id in seen_ids:
            continue
        seen_ids.add(a.id)
        user_name = "System"
        if a.user_id:
            user = db.session.get(User, a.user_id)
            if user:
                user_name = user.full_name or user.username
        timeline.append(
            {
                "id": a.id,
                "source": "audit",
                "timestamp": a.timestamp.isoformat() if a.timestamp else None,
                "action": a.action,
                "entity_type": a.entity_type,
                "entity_id": a.entity_id,
                "description": a.description or "",
                "user_name": user_name,
                "case_id": a.case_id,
                "changes": a.changes_made,
            }
        )

    for ra in action_entries:
        dedup_key = f"ra:{ra.id}"
        if dedup_key in seen_ids:
            continue
        seen_ids.add(dedup_key)
        user_name = "System"
        if ra.created_by:
            user = db.session.get(User, ra.created_by)
            if user:
                user_name = user.full_name or user.username
        timeline.append(
            {
                "id": ra.id,
                "source": "action",
                "timestamp": ra.created_at.isoformat() if ra.created_at else None,
                "action": ra.status or "created",
                "entity_type": "research_action",
                "entity_id": ra.id,
                "description": f"{ra.label or ra.action_type} — {ra.status}",
                "user_name": user_name,
                "case_id": ra.case_id,
                "changes": None,
            }
        )

    timeline.sort(key=lambda e: e.get("timestamp") or "", reverse=True)

    return jsonify({"items": timeline[:200], "total": len(timeline)})
