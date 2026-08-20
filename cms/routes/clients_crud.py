import json
import logging
from datetime import datetime, timezone

import flask
from flask import request, jsonify, render_template, redirect, url_for, flash, abort
from flask_login import login_required, current_user
from sqlalchemy.orm import joinedload

from . import cms_bp
from ..validation import validate, CreateClientSchema, EditClientSchema
from ..models import db, Client, Case, AuditLog, Contact, Address
from ..auth import (
    roles_required,
    staff_required,
    admin_required,
    audit_read,
    apply_tenant_filter,
    ensure_tenant_access,
)
from ..encryption_utils import encryptor
from .utils import normalize_phone, find_similar_clients, check_for_exact_match
from ..rate_limiting import rate_limit, STRICT_RATE_LIMIT
from ..tier_limits import check_resource_limit

from .response import api_success, api_error

logger = logging.getLogger(__name__)


@cms_bp.route("/clients")
@login_required
@staff_required
def clients() -> str:
    """List all clients."""
    page = request.args.get("page", 1, type=int)
    per_page = 20
    search = request.args.get("search", "")
    show_archived = request.args.get("show_archived", "").lower() in (
        "1",
        "true",
        "yes",
    )
    sort = request.args.get("sort", "name")
    order = request.args.get("order", "asc")

    query = Client.query.filter_by(is_deleted=False)

    # Tenant isolation (SQLite compat)
    query = apply_tenant_filter(query, Client)

    if not show_archived:
        query = query.filter_by(is_active=True)

    if search:
        query = query.filter(Client.name.ilike(f"%{search}%"))

    sort_columns = {
        "name": Client.name,
        "contact": Client.contact_person,
        "contract": Client.contract_number,
    }

    sort_col = sort_columns.get(sort, Client.name)
    if order == "desc":
        sort_col = sort_col.desc()

    pagination = query.order_by(sort_col).paginate(
        page=page, per_page=per_page, error_out=False
    )

    return render_template(
        "cms/clients/list.html",
        clients=pagination.items,
        pagination=pagination,
        search=search,
        sort=sort,
        order=order,
        show_archived=show_archived,
    )


@cms_bp.route("/clients/<client_id>")
@login_required
@staff_required
@audit_read("client")
def view_client(client_id: str) -> str:
    """View client details with all associated cases."""
    client = db.session.get(Client, client_id) or abort(404)
    ensure_tenant_access(client)
    # Wrap in no_autoflush to prevent before_flush from re-encrypting
    # freshly decrypted values during the queries and template render.
    with db.session.no_autoflush:
        client.decrypt_naw()
        contacts = list(client.contacts)
        for c in contacts:
            c.decrypt_fields()
        addresses = list(client.addresses)
        for addr in addresses:
            addr.decrypt_fields()

        cases_q = Case.query.filter_by(client_id=client_id, is_deleted=False).options(
            joinedload(Case.lead_investigator)
        )
        cases_q = apply_tenant_filter(cases_q, Case)
        cases = cases_q.order_by(Case.created_at.desc()).all()

        active_cases_count_q = Case.query.filter(
            Case.client_id == client_id,
            Case.is_deleted == False,
            Case.status.in_(["open", "active", "suspended"]),
        )
        active_cases_count_q = apply_tenant_filter(active_cases_count_q, Case)
        active_cases_count = active_cases_count_q.count()

        return render_template(
            "cms/clients/view.html",
            client=client,
            cases=cases,
            active_cases_count=active_cases_count,
        )


@cms_bp.route("/clients/create", methods=["GET", "POST"])
@login_required
@roles_required("admin", "owner", "senior_investigator", "investigator")
@rate_limit(STRICT_RATE_LIMIT, key_prefix="create_client")
@validate(CreateClientSchema)
def create_client() -> flask.Response:
    """Create a new client with duplicate detection."""
    if request.method == "POST":
        data = request.validated_data

        required = ["name"]
        for field in required:
            if not data.get(field):
                if request.is_json:
                    return api_error(f"{field} is required", 400)
                flash(f"{field} is required.", "danger")
                return render_template("cms/clients/create.html")

        name = data["name"].strip()

        # Check for duplicates
        exact_match = check_for_exact_match(name, "client")
        similar = find_similar_clients(name)

        # Skip duplicate check if already confirmed
        if not data.get("confirm_duplicate"):
            if exact_match:
                if request.is_json:
                    return jsonify(
                        {
                            "error": "exact_match",
                            "message": f"A client with this name already exists: {exact_match['name']}",
                            "duplicate": exact_match,
                            "similar": similar[:5],
                        }
                    ), 409
                flash(
                    f"Warning: A client with this name already exists: {exact_match['name']}",
                    "warning",
                )
                return render_template(
                    "cms/clients/create.html",
                    duplicate_warning=True,
                    exact_match=exact_match,
                    similar_clients=similar[:5],
                    submitted_name=name,
                    submitted_is_company=bool(data.get("is_company")),
                )

            if similar and not request.is_json:
                flash(
                    "Warning: Similar clients found. Please review before creating.",
                    "warning",
                )
                return render_template(
                    "cms/clients/create.html",
                    duplicate_warning=True,
                    similar_clients=similar[:5],
                    submitted_name=name,
                    submitted_is_company=bool(data.get("is_company")),
                )

        # Check client limit before creating
        ok, cur, maximum = check_resource_limit(Client, "tenant_id", "max_clients")
        if not ok:
            if request.is_json:
                return api_error(
                    f"Client limit reached ({cur}/{maximum}). Upgrade your plan to add more clients.",
                    403,
                )
            flash(
                f"Client limit reached ({cur}/{maximum}). Upgrade your plan to add more clients.",
                "danger",
            )
            return render_template("cms/clients/create.html")

        client = Client(name=name)
        client.is_company = bool(data.get("is_company"))

        # Set encrypted fields
        encrypted_fields = [
            "contact_person",
            "contact_email",
            "contact_phone",
            "social_security_number",
            "bank_account",
            "date_of_birth",
            "place_of_birth",
        ]
        for field in encrypted_fields:
            if data.get(field):
                setattr(client, field, encryptor.encrypt(data[field]))

        # Handle structured contacts
        if data.get("contacts_data"):
            try:
                contacts_data = (
                    json.loads(data["contacts_data"])
                    if isinstance(data["contacts_data"], str)
                    else data["contacts_data"]
                )
                for c_data in contacts_data:
                    if c_data.get("value"):
                        contact = Contact(
                            client_id=client.id,
                            contact_type=c_data.get("contact_type", "email"),
                            value=c_data.get("value"),
                            is_primary=c_data.get("is_primary", False),
                        )
                        contact.encrypt_fields()
                        db.session.add(contact)
                        # Also set legacy fields for backward compat
                        if c_data.get("contact_type") == "email" and c_data.get(
                            "is_primary"
                        ):
                            client.contact_email = (
                                encryptor.encrypt(c_data.get("value"))
                                if c_data.get("value")
                                else None
                            )
                        elif c_data.get("contact_type") == "phone" and c_data.get(
                            "is_primary"
                        ):
                            client.contact_phone = (
                                encryptor.encrypt(normalize_phone(c_data.get("value")))
                                if c_data.get("value")
                                else None
                            )
            except (json.JSONDecodeError, TypeError) as e:
                logger.warning(f"Failed to parse contacts_data: {e}")

        # Handle structured addresses
        if data.get("addresses_data"):
            try:
                addresses_data = (
                    json.loads(data["addresses_data"])
                    if isinstance(data["addresses_data"], str)
                    else data["addresses_data"]
                )
                for addr_data in addresses_data:
                    if addr_data.get("street") or addr_data.get("zipcode"):
                        address = Address(
                            client_id=client.id,
                            street=addr_data.get("street"),
                            number=addr_data.get("number"),
                            zipcode=addr_data.get("zipcode"),
                            town=addr_data.get("town"),
                            country=addr_data.get("country", "Netherlands"),
                            is_primary=addr_data.get("is_primary", False),
                        )
                        address.encrypt_fields()
                        db.session.add(address)
            except (json.JSONDecodeError, TypeError) as e:
                logger.warning(f"Failed to parse addresses_data: {e}")

        # Set other fields
        client.contract_number = data.get("contract_number")
        client.contract_info = data.get("contract_info")
        client.vat_number = data.get("vat_number")
        client.financial_notes = data.get("financial_notes")

        db.session.add(client)

        AuditLog.log(
            user_id=current_user.id,
            action="create",
            entity_type="client",
            entity_id=client.id,
            new_values={"name": client.name},
            ip_address=request.remote_addr,
            description="Created client",
        )
        db.session.commit()

        try:
            from ..webhooks import dispatch

            dispatch("client.created", {"id": client.id, "name": client.name})
        except Exception:
            pass

        if request.is_json:
            return jsonify(
                {"message": "Client created", "client": client.to_dict()}
            ), 201

        flash(f"Client {client.name} created successfully.", "success")
        return redirect(url_for("cms.view_client", client_id=client.id))

    return render_template("cms/clients/create.html")


@cms_bp.route("/clients/<client_id>/edit", methods=["GET", "POST"])
@login_required
@roles_required("admin", "owner", "senior_investigator", "investigator")
@rate_limit(STRICT_RATE_LIMIT, key_prefix="edit_client")
@validate(EditClientSchema)
def edit_client(client_id: str) -> flask.Response:
    """Edit client details."""
    client = db.session.get(Client, client_id) or abort(404)
    ensure_tenant_access(client)

    if request.method == "POST":
        data = request.validated_data
        changes = {}

        # Update name
        if data.get("name") and data["name"] != client.name:
            changes["name"] = {"old": client.name, "new": data["name"]}
            client.name = data["name"]

        # Update is_company
        new_is_company = bool(data.get("is_company"))
        if new_is_company != client.is_company:
            changes["is_company"] = {"old": client.is_company, "new": new_is_company}
            client.is_company = new_is_company

        # Update encrypted fields
        encrypted_fields = [
            "contact_person",
            "contact_email",
            "contact_phone",
            "social_security_number",
            "bank_account",
            "date_of_birth",
            "place_of_birth",
        ]
        for field in encrypted_fields:
            if field in data:
                new_value = data[field] if data[field] else None
                old_value = getattr(client, field)
                if new_value != old_value:
                    changes[field] = {"old": "[encrypted]", "new": "[encrypted]"}
                    if new_value:
                        setattr(client, field, encryptor.encrypt(new_value))
                    else:
                        setattr(client, field, None)

        # Handle structured contacts
        if data.get("contacts_data"):
            try:
                contacts_data = (
                    json.loads(data["contacts_data"])
                    if isinstance(data["contacts_data"], str)
                    else data["contacts_data"]
                )
                old_contacts = list(client.contacts)
                for c in old_contacts:
                    db.session.delete(c)
                for c_data in contacts_data:
                    if c_data.get("value"):
                        contact = Contact(
                            client_id=client.id,
                            contact_type=c_data.get("contact_type", "email"),
                            value=c_data.get("value"),
                            is_primary=c_data.get("is_primary", False),
                        )
                        contact.encrypt_fields()
                        db.session.add(contact)
                        # Update legacy fields for backward compat
                        if c_data.get("contact_type") == "email" and c_data.get(
                            "is_primary"
                        ):
                            client.contact_email = (
                                encryptor.encrypt(c_data.get("value"))
                                if c_data.get("value")
                                else None
                            )
                        elif c_data.get("contact_type") == "phone" and c_data.get(
                            "is_primary"
                        ):
                            client.contact_phone = (
                                encryptor.encrypt(normalize_phone(c_data.get("value")))
                                if c_data.get("value")
                                else None
                            )
                changes["contacts"] = {
                    "old": f"{len(old_contacts)} contact(s)",
                    "new": f"{len(contacts_data)} contact(s)",
                }
            except (json.JSONDecodeError, TypeError) as e:
                logger.warning(f"Failed to parse contacts_data: {e}")

        # Handle structured addresses
        if data.get("addresses_data"):
            try:
                addresses_data = (
                    json.loads(data["addresses_data"])
                    if isinstance(data["addresses_data"], str)
                    else data["addresses_data"]
                )
                old_addresses = list(client.addresses)
                for addr in old_addresses:
                    db.session.delete(addr)
                for addr_data in addresses_data:
                    if addr_data.get("street") or addr_data.get("zipcode"):
                        address = Address(
                            client_id=client.id,
                            street=addr_data.get("street"),
                            number=addr_data.get("number"),
                            zipcode=addr_data.get("zipcode"),
                            town=addr_data.get("town"),
                            country=addr_data.get("country", "Netherlands"),
                            is_primary=addr_data.get("is_primary", False),
                        )
                        address.encrypt_fields()
                        db.session.add(address)
                changes["addresses"] = {
                    "old": f"{len(old_addresses)} address(es)",
                    "new": f"{len(addresses_data)} address(es)",
                }
            except (json.JSONDecodeError, TypeError) as e:
                logger.warning(f"Failed to parse addresses_data: {e}")

        # Update contract info
        if data.get("contract_number") != client.contract_number:
            changes["contract_number"] = {
                "old": client.contract_number,
                "new": data.get("contract_number"),
            }
            client.contract_number = data.get("contract_number")

        # Update financial fields
        if data.get("vat_number") != client.vat_number:
            client.vat_number = data.get("vat_number")
        if data.get("financial_notes") != client.financial_notes:
            client.financial_notes = data.get("financial_notes")

        client.updated_at = datetime.now(timezone.utc)

        AuditLog.log(
            user_id=current_user.id,
            action="update",
            entity_type="client",
            entity_id=client_id,
            changes=changes,
            ip_address=request.remote_addr,
            description="Updated client",
        )
        db.session.commit()

        if request.is_json:
            return jsonify({"message": "Client updated", "client": client.to_dict()})

        flash("Client updated successfully.", "success")
        return redirect(url_for("cms.view_client", client_id=client.id))

    # Render with autoflush off: the template re-queries client.addresses/
    # contacts; an autoflush would re-encrypt the freshly decrypted values
    # mid-render and show ciphertext. The view's commit still flushes, so the
    # before_flush guard re-encrypts before anything persists.
    with db.session.no_autoflush:
        client.decrypt_naw()
        for c in client.contacts:
            c.decrypt_fields()
        for addr in client.addresses:
            addr.decrypt_fields()
        return render_template("cms/clients/edit.html", client=client)


@cms_bp.route("/clients/<client_id>/delete", methods=["POST"])
@login_required
@admin_required
def delete_client(client_id: str) -> flask.Response:
    """Soft delete a client."""
    client = db.session.get(Client, client_id) or abort(404)
    ensure_tenant_access(client)

    client.soft_delete()

    AuditLog.log(
        user_id=current_user.id,
        action="delete",
        entity_type="client",
        entity_id=client_id,
        ip_address=request.remote_addr,
        description="Deleted client",
    )
    db.session.commit()

    flash(f"Client {client.name} has been archived.", "info")

    if request.is_json:
        return api_success({}, "Client archived")
    return redirect(url_for("cms.clients"))
