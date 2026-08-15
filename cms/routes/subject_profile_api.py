"""
Profile write API (ADR-0001 PR7b).

Inline add/edit/delete endpoints for the tabbed Subject Profile, all gated by
the ``subject_first_investigations`` feature flag (404 when off). Writes go
through ``SubjectService`` — the single write path — including the PR3
fact-layer models (identifiers/facts) and the structured children
(addresses/contacts/social accounts) with provenance. Relations reuse the
canonical single-row storage (PR3).
"""

import logging
import functools
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
