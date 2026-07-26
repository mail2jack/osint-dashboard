import json
import logging
from datetime import datetime, timezone

import flask
from flask import request, jsonify, render_template, redirect, url_for, flash, abort
from flask_login import login_required, current_user

from . import cms_bp
from ..validation import (
    validate,
    CreateSubjectSchema,
    EditSubjectSchema,
    BulkDeleteSchema,
)
from ..models import db, Subject, Case, Address, Contact, AuditLog, case_subjects
from ..auth import (
    roles_required,
    subject_access_required,
    apply_tenant_filter,
    ensure_tenant_access,
)
from ..encryption_utils import encryptor, EncryptionError
from .utils import normalize_phone, find_similar_subjects, check_for_exact_match
from ..rate_limiting import rate_limit, STRICT_RATE_LIMIT
from ..tier_limits import check_resource_limit

from .response import api_success, api_error

logger = logging.getLogger(__name__)


@cms_bp.route("/subjects/create", methods=["GET", "POST"])
@login_required
@roles_required(
    "admin", "owner", "senior_investigator", "investigator", "junior_investigator"
)
@rate_limit(STRICT_RATE_LIMIT, key_prefix="create_subject")
@validate(CreateSubjectSchema)
def create_subject() -> flask.Response:
    """Create a new subject with duplicate detection."""
    if request.method == "POST":
        data = request.validated_data
        if "type_" in data:
            data["type"] = data.pop("type_")

        required = ["subject_type"]
        for field in required:
            if not data.get(field):
                if request.is_json:
                    return api_error(f"{field} is required", 400)
                flash(f"{field} is required.", "danger")
                return render_template("cms/subjects/create.html")

        # Compute name from split fields if not provided
        if not data.get("name"):
            parts = []
            for f in ("voorletters", "voornamen", "tussenvoegsels", "achternaam"):
                if data.get(f):
                    parts.append(data[f].strip())
            data["name"] = " ".join(parts)

        if not data.get("name"):
            if request.is_json:
                return api_error("name is required", 400)
            flash("name is required.", "danger")
            return render_template("cms/subjects/create.html")

        name = data["name"].strip()

        # Auto-prepend @ for online entity names
        if data.get("subject_type") == "online" and name and not name.startswith("@"):
            name = "@" + name
            data["name"] = name

        # Check for duplicates
        exact_match = check_for_exact_match(name, "subject")
        similar = find_similar_subjects(name)

        # Skip duplicate check if already confirmed
        if not data.get("confirm_duplicate"):
            if exact_match:
                if request.is_json:
                    return jsonify(
                        {
                            "error": "exact_match",
                            "message": f"A subject with this name already exists: {exact_match['name']}",
                            "duplicate": exact_match,
                            "similar": similar[:5],
                        }
                    ), 409
                flash(
                    f"Warning: A subject with this name already exists: {exact_match['name']}",
                    "warning",
                )
                case_id = request.args.get("case_id")
                return render_template(
                    "cms/subjects/create.html",
                    case_id=case_id,
                    duplicate_warning=True,
                    exact_match=exact_match,
                    similar_subjects=similar[:5],
                    submitted_name=name,
                    submitted_type=data.get("subject_type"),
                )

            if similar and not request.is_json:
                flash(
                    "Warning: Similar subjects found. Please review before creating.",
                    "warning",
                )
                case_id = request.args.get("case_id")
                return render_template(
                    "cms/subjects/create.html",
                    case_id=case_id,
                    duplicate_warning=True,
                    similar_subjects=similar[:5],
                    submitted_name=name,
                    submitted_type=data.get("subject_type"),
                )

        # Check subject limit before creating
        ok, cur, maximum = check_resource_limit(Subject, "tenant_id", "max_subjects")
        if not ok:
            if request.is_json:
                return api_error(
                    f"Subject limit reached ({cur}/{maximum}). Upgrade your plan to add more subjects.",
                    403,
                )
            flash(
                f"Subject limit reached ({cur}/{maximum}). Upgrade your plan to add more subjects.",
                "danger",
            )
            return render_template("cms/subjects/create.html")

        subject = Subject(
            name=name,
            subject_type=data["subject_type"],
            risk_score=data.get("risk_score", 0),
            risk_factors=data.get("risk_factors"),
            notes=data.get("notes"),
            registration_number=data.get("registration_number"),
            legal_form=data.get("legal_form"),
            asset_type=data.get("asset_type"),
            estimated_value=data.get("estimated_value"),
            currency=data.get("currency", "EUR"),
            license_plate=data.get("license_plate"),
            vin=data.get("vin"),
            insurance_company=data.get("insurance_company"),
            brand=data.get("brand"),
            vehicle_type=data.get("vehicle_type"),
            imo_number=data.get("imo_number"),
            mmsi=data.get("mmsi"),
            eni_number=data.get("eni_number"),
            vessel_nationality=data.get("vessel_nationality"),
            date_of_birth=data.get("date_of_birth"),
            place_of_birth=data.get("place_of_birth"),
            identification_number=data.get("identification_number"),
            bank_account=data.get("bank_account"),
            created_by=current_user.id,
            achternaam=data.get("achternaam"),
            voornamen=data.get("voornamen"),
            voorletters=data.get("voorletters"),
            tussenvoegsels=data.get("tussenvoegsels"),
            geslacht=data.get("geslacht"),
            nationality=data.get("nationality"),
            bsn_number=data.get("bsn_number"),
            reisdocument_type=data.get("reisdocument_type"),
            reisdocument_nummer=data.get("reisdocument_nummer"),
        )

        if data["subject_type"] == "vehicle":
            rdw_data = {}
            rdw_fields = [
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
                "vervaldatum_apk",
                "wam_verzekerd",
                "taxi_indicator",
                "export_indicator",
                "europese_voertuigcategorie",
                "zuinigheidsclassificatie",
                "catalogusprijs",
                "bruto_bpm",
                "datum_eerste_toelating",
                "datum_tenaamstelling",
                "rdw_type",
                "variant",
                "uitvoering",
                "typegoedkeuringsnummer",
                "openstaande_terugroepactie",
            ]
            for field in rdw_fields:
                if data.get(field):
                    rdw_data[field] = data.get(field)

            if rdw_data or data.get("license_plate"):
                rdw_data["kenteken"] = data.get("license_plate", "").upper()
                rdw_data["merk"] = data.get("brand", "")
                rdw_data["inrichting"] = data.get("vehicle_type", "")
                if data.get("eerste_kleur"):
                    rdw_data["kleur"] = data.get("eerste_kleur")
                subject.rdw_data = rdw_data

        if data["subject_type"] == "vessel" and data.get("vessel_data"):
            try:
                subject.vessel_data = json.loads(data["vessel_data"])
            except (json.JSONDecodeError, TypeError):
                subject.vessel_data = data["vessel_data"]

        # Encrypt all identifying fields (person + vehicle + vessel)
        subject.encrypt_identifiers()

        db.session.add(subject)
        db.session.flush()  # Get subject ID before adding addresses

        # Handle structured addresses
        if data.get("addresses_data"):
            try:
                addresses_data = (
                    json.loads(data["addresses_data"])
                    if isinstance(data["addresses_data"], str)
                    else data["addresses_data"]
                )
                primary_addr = None
                for addr_data in addresses_data:
                    if addr_data.get("street") or addr_data.get("zipcode"):
                        address = Address(
                            subject_id=subject.id,
                            street=addr_data.get("street"),
                            number=addr_data.get("number"),
                            zipcode=addr_data.get("zipcode"),
                            town=addr_data.get("town"),
                            country=addr_data.get("country", "Netherlands"),
                            is_primary=addr_data.get("is_primary", False),
                        )
                        address.encrypt_fields()
                        db.session.add(address)
                        if addr_data.get("is_primary") or not primary_addr:
                            primary_addr = addr_data
                # Sync primary address to legacy Subject fields for backward compat
                if primary_addr:
                    parts = []
                    if primary_addr.get("street"):
                        parts.append(primary_addr["street"])
                    if primary_addr.get("number"):
                        parts.append(primary_addr["number"])
                    street_loc = " ".join(parts)
                    pc = primary_addr.get("zipcode", "")
                    town = primary_addr.get("town", "")
                    subject.address = ", ".join(
                        p for p in [street_loc, f"{pc} {town}".strip()] if p
                    )
                    subject.street = (
                        encryptor.encrypt(primary_addr["street"])
                        if primary_addr.get("street")
                        else None
                    )
                    subject.house_number = (
                        encryptor.encrypt(primary_addr["number"])
                        if primary_addr.get("number")
                        else None
                    )
                    subject.house_number_addition = (
                        encryptor.encrypt(primary_addr.get("addition", ""))
                        if primary_addr.get("addition")
                        else None
                    )
                    subject.postal_code = encryptor.encrypt(pc) if pc else None
                    subject.city = encryptor.encrypt(town) if town else None
            except (json.JSONDecodeError, TypeError) as e:
                logger.warning(f"Failed to parse addresses_data: {e}")

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
                            subject_id=subject.id,
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
                            subject.email = (
                                encryptor.encrypt(c_data.get("value"))
                                if c_data.get("value")
                                else None
                            )
                        elif c_data.get("contact_type") == "phone" and c_data.get(
                            "is_primary"
                        ):
                            subject.phone = (
                                encryptor.encrypt(normalize_phone(c_data.get("value")))
                                if c_data.get("value")
                                else None
                            )
            except (json.JSONDecodeError, TypeError) as e:
                logger.warning(f"Failed to parse contacts_data: {e}")

        # Link to case if specified
        if data.get("case_id"):
            case = db.session.get(Case, data["case_id"])
            if case:
                ensure_tenant_access(case)
                case.subjects.append(subject)

        # Create social account for online entities
        if data["subject_type"] == "online" and data.get("online_platform"):
            from ..models import SocialAccount

            platform = data["online_platform"].strip().lower()
            username = name.lstrip("@")
            profile_url = (data.get("online_profile_url") or "").strip()
            sa = SocialAccount(
                tenant_id=current_user.tenant_id,
                subject_id=subject.id,
                platform=platform,
                username=username,
                url=profile_url,
            )
            db.session.add(sa)

        AuditLog.log(
            user_id=current_user.id,
            action="create",
            entity_type="subject",
            entity_id=subject.id,
            new_values={"name": subject.name, "type": subject.subject_type},
            ip_address=request.remote_addr,
            case_id=data.get("case_id"),
            description=f"Created subject ({subject.subject_type})",
        )
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            logger.exception("Failed to create subject")
            if request.is_json:
                return api_error("Failed to create subject", 500)
            flash("Failed to create subject.", "danger")
            return render_template("cms/subjects/create.html")

        try:
            from ..webhooks import dispatch

            dispatch(
                "subject.created",
                {
                    "id": subject.id,
                    "name": subject.name,
                    "subject_type": subject.subject_type,
                },
            )
        except Exception:
            logger.debug("Webhook dispatch failed for subject.created", exc_info=True)

        if request.is_json:
            return jsonify(
                {"message": "Subject created", "subject": subject.to_dict()}
            ), 201

        flash(f"Subject {subject.name} created successfully.", "success")

        # If created from case view, redirect back to case
        if data.get("case_id"):
            return redirect(url_for("cms.view_case", case_id=data["case_id"]))

        return redirect(url_for("cms.view_subject", subject_id=subject.id))

    # Pass case_id from query param if coming from case view
    case_id = request.args.get("case_id")
    cases = []
    if not case_id:
        q = apply_tenant_filter(Case.query, Case)
        cases = (
            q.filter(Case.is_deleted == False).order_by(Case.case_number.desc()).all()
        )
    return render_template("cms/subjects/create.html", case_id=case_id, cases=cases)


@cms_bp.route("/subjects/<subject_id>/edit", methods=["GET", "POST"])
@login_required
@subject_access_required
@roles_required(
    "admin", "owner", "senior_investigator", "investigator", "junior_investigator"
)
@rate_limit(STRICT_RATE_LIMIT, key_prefix="edit_subject")
@validate(EditSubjectSchema)
def edit_subject(subject_id: str) -> flask.Response:
    """Edit subject details."""
    subject = db.session.get(Subject, subject_id) or abort(404)

    if request.method == "POST":
        data = request.validated_data
        if "type_" in data:
            data["type"] = data.pop("type_")
        changes = {}

        # Update basic fields
        if data.get("name") and data["name"] != subject.name:
            new_name = data["name"].strip()
            # Auto-prepend @ for online entity names
            if (
                subject.subject_type == "online"
                and new_name
                and not new_name.startswith("@")
            ):
                new_name = "@" + new_name
            changes["name"] = {"old": subject.name, "new": new_name}
            subject.name = new_name

        # Update subject_type if provided
        if (
            "subject_type" in data
            and data["subject_type"]
            and data["subject_type"] != subject.subject_type
        ):
            changes["subject_type"] = {
                "old": subject.subject_type,
                "new": data["subject_type"],
            }
            subject.subject_type = data["subject_type"]

        if "risk_score" in data:
            changes["risk_score"] = {
                "old": subject.risk_score,
                "new": data["risk_score"],
            }
            subject.risk_score = int(data["risk_score"])

        if "notes" in data:
            subject.notes = data["notes"]

        # Update non-encrypted person fields
        person_text_fields = [
            "achternaam",
            "voornamen",
            "voorletters",
            "tussenvoegsels",
            "geslacht",
            "reisdocument_type",
        ]
        for field in person_text_fields:
            if field in data:
                setattr(subject, field, data[field])

        # Update encrypted fields for persons
        encrypted_fields = [
            "date_of_birth",
            "place_of_birth",
            "nationality",
            "identification_number",
            "address",
            "phone",
            "email",
            "bank_account",
            "bsn_number",
            "reisdocument_nummer",
        ]
        for field in encrypted_fields:
            if field in data:
                new_value = data[field] if data[field] else None
                old_value = getattr(subject, field)
                # Decrypt old value for comparison
                try:
                    if old_value:
                        old_value = encryptor.decrypt(old_value)
                except EncryptionError:
                    logger.debug(
                        "Could not decrypt %s (may already be plaintext or key changed)",
                        field,
                    )
                if field == "phone" and new_value:
                    new_value = normalize_phone(new_value)
                if new_value != old_value:
                    changes[field] = {
                        "old": "[encrypted]",
                        "new": "[encrypted]",
                    }
                    if new_value:
                        setattr(subject, field, encryptor.encrypt(new_value))
                    else:
                        setattr(subject, field, None)

        # Update vehicle fields
        # Encrypted vehicle fields
        encrypted_vehicle_fields = ["license_plate", "vin", "insurance_company"]
        for field in encrypted_vehicle_fields:
            if field in data:
                new_value = data[field] if data[field] else None
                old_value = getattr(subject, field)
                # Decrypt old value for comparison
                try:
                    if old_value:
                        old_value = encryptor.decrypt(old_value)
                except EncryptionError:
                    logger.debug(
                        "Could not decrypt %s (may already be plaintext or key changed)",
                        field,
                    )
                if new_value != old_value:
                    changes[field] = {
                        "old": "[encrypted]",
                        "new": "[encrypted]",
                    }
                    if new_value:
                        setattr(subject, field, encryptor.encrypt(new_value))
                    else:
                        setattr(subject, field, None)

        # Non-encrypted vehicle fields
        non_encrypted_vehicle_fields = ["brand", "vehicle_type"]
        for field in non_encrypted_vehicle_fields:
            if field in data:
                new_value = data[field] if data[field] else None
                if new_value != getattr(subject, field):
                    changes[field] = {
                        "old": getattr(subject, field) or "[empty]",
                        "new": new_value or "[empty]",
                    }
                    setattr(subject, field, new_value)

        # Encrypted vessel fields
        encrypted_vessel_fields = [
            "imo_number",
            "mmsi",
            "eni_number",
            "vessel_nationality",
        ]
        for field in encrypted_vessel_fields:
            if field in data:
                new_value = data[field] if data[field] else None
                old_value = getattr(subject, field)
                try:
                    if old_value:
                        old_value = encryptor.decrypt(old_value)
                except EncryptionError:
                    logger.debug(
                        "Could not decrypt %s (may already be plaintext or key changed)",
                        field,
                    )
                if new_value != old_value:
                    changes[field] = {
                        "old": old_value or "[empty]",
                        "new": new_value or "[empty]",
                    }
                    if new_value:
                        setattr(subject, field, encryptor.encrypt(new_value))
                    else:
                        setattr(subject, field, None)

        # Handle structured addresses
        if data.get("addresses_data"):
            try:
                addresses_data = (
                    json.loads(data["addresses_data"])
                    if isinstance(data["addresses_data"], str)
                    else data["addresses_data"]
                )
                if addresses_data:  # only replace if non-empty
                    old_addresses = list(subject.addresses)
                    addr_count_before = len(old_addresses)
                    for addr in old_addresses:
                        db.session.delete(addr)
                    primary_addr = None
                    for addr_data in addresses_data:
                        if addr_data.get("street") or addr_data.get("zipcode"):
                            address = Address(
                                subject_id=subject.id,
                                street=addr_data.get("street"),
                                number=addr_data.get("number"),
                                zipcode=addr_data.get("zipcode"),
                                town=addr_data.get("town"),
                                country=addr_data.get("country", "Netherlands"),
                                is_primary=addr_data.get("is_primary", False),
                            )
                            address.encrypt_fields()
                            db.session.add(address)
                            if addr_data.get("is_primary") or not primary_addr:
                                primary_addr = addr_data
                    # Sync primary address to legacy Subject fields for backward compat
                    if primary_addr:
                        parts = []
                        if primary_addr.get("street"):
                            parts.append(primary_addr["street"])
                        if primary_addr.get("number"):
                            parts.append(primary_addr["number"])
                        street_loc = " ".join(parts)
                        pc = primary_addr.get("zipcode", "")
                        town = primary_addr.get("town", "")
                        subject.address = ", ".join(
                            p for p in [street_loc, f"{pc} {town}".strip()] if p
                        )
                        subject.street = (
                            encryptor.encrypt(primary_addr["street"])
                            if primary_addr.get("street")
                            else None
                        )
                        subject.house_number = (
                            encryptor.encrypt(primary_addr["number"])
                            if primary_addr.get("number")
                            else None
                        )
                        subject.house_number_addition = (
                            encryptor.encrypt(primary_addr.get("addition", ""))
                            if primary_addr.get("addition")
                            else None
                        )
                        subject.postal_code = encryptor.encrypt(pc) if pc else None
                        subject.city = encryptor.encrypt(town) if town else None
                    changes["addresses"] = {
                        "old": f"{addr_count_before} address(es)",
                        "new": f"{len(addresses_data)} address(es)",
                    }
            except (json.JSONDecodeError, TypeError) as e:
                logger.warning(f"Failed to parse addresses_data: {e}")

        # Handle structured contacts
        if data.get("contacts_data"):
            try:
                contacts_data = (
                    json.loads(data["contacts_data"])
                    if isinstance(data["contacts_data"], str)
                    else data["contacts_data"]
                )
                if contacts_data:  # only replace if non-empty
                    old_contacts = list(subject.contacts)
                    contact_count_before = len(old_contacts)
                    for c in old_contacts:
                        db.session.delete(c)
                    for c_data in contacts_data:
                        if c_data.get("value"):
                            contact = Contact(
                                subject_id=subject.id,
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
                                try:
                                    current = (
                                        encryptor.decrypt(subject.email)
                                        if subject.email
                                        else None
                                    )
                                except Exception:
                                    current = subject.email  # may already be plaintext
                                if c_data.get("value") != current:
                                    subject.email = (
                                        encryptor.encrypt(c_data.get("value"))
                                        if c_data.get("value")
                                        else None
                                    )
                            elif c_data.get("contact_type") == "phone" and c_data.get(
                                "is_primary"
                            ):
                                try:
                                    current = (
                                        encryptor.decrypt(subject.phone)
                                        if subject.phone
                                        else None
                                    )
                                except Exception:
                                    current = subject.phone  # may already be plaintext
                                new_val = normalize_phone(c_data.get("value"))
                                if new_val != current:
                                    subject.phone = (
                                        encryptor.encrypt(new_val) if new_val else None
                                    )
                    changes["contacts"] = {
                        "old": f"{contact_count_before} contact(s)",
                        "new": f"{len(contacts_data)} contact(s)",
                    }
            except (json.JSONDecodeError, TypeError) as e:
                logger.warning(f"Failed to parse contacts_data: {e}")

        # Update RDW data if provided
        rdw_fields = [
            "handelsbenaming",
            "voertuigsoort",
            "eerste_kleur",
            "tweede_kleur",
            "aantal_deuren",
            "aantal_zitplaatsen",
            "cilinderinhoud",
            "aantal_cilinders",
            "massa_ledig",
            "maximum_massa",
            "vervaldatum_apk",
            "wam_verzekerd",
            "taxi_indicator",
            "export_indicator",
            "europese_voertuigcategorie",
            "zuinigheidsclassificatie",
            "catalogusprijs",
            "datum_eerste_toelating",
            "rdw_type",
            "variant",
            "uitvoering",
            "typegoedkeuringsnummer",
            "wielbasis",
        ]

        rdw_data = {}
        for field in rdw_fields:
            if data.get(field):
                rdw_data[field] = data[field]

        # Also store basic vehicle fields in RDW data
        if data.get("license_plate"):
            rdw_data["kenteken"] = data["license_plate"]
        if data.get("brand"):
            rdw_data["merk"] = data["brand"]
        if data.get("vehicle_type"):
            rdw_data["inrichting"] = data["vehicle_type"]
        if data.get("vin"):
            rdw_data["chassisnummer"] = data["vin"]

        if rdw_data:
            existing_rdw = subject.rdw_data or {}
            existing_rdw.update(rdw_data)
            subject.rdw_data = existing_rdw
            changes["rdw_data"] = {"old": "updated", "new": "RDW fields updated"}

        # Update vessel data if provided
        if data.get("vessel_data"):
            try:
                subject.vessel_data = json.loads(data["vessel_data"])
            except (json.JSONDecodeError, TypeError):
                subject.vessel_data = data["vessel_data"]
            changes["vessel_data"] = {"old": "updated", "new": "Vessel data updated"}

        subject.updated_at = datetime.now(timezone.utc)

        AuditLog.log(
            user_id=current_user.id,
            action="update",
            entity_type="subject",
            entity_id=subject_id,
            changes=changes,
            ip_address=request.remote_addr,
            description=f"Updated subject ({subject.subject_type})",
        )
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            logger.exception("Failed to update subject")
            if request.is_json:
                return api_error("Failed to update subject", 500)
            flash("Failed to update subject.", "danger")
            return redirect(url_for("cms.edit_subject", subject_id=subject_id))

        if request.is_json:
            return jsonify({"message": "Subject updated", "subject": subject.to_dict()})

        flash("Subject updated successfully.", "success")
        return redirect(url_for("cms.view_subject", subject_id=subject.id))

    subject.decrypt_identifiers()
    addresses = list(subject.addresses)
    for addr in addresses:
        addr.decrypt_fields()
    contacts = list(subject.contacts)
    for c in contacts:
        c.decrypt_fields()
    return render_template(
        "cms/subjects/edit.html",
        subject=subject,
        addresses=addresses,
        contacts=contacts,
    )


@cms_bp.route("/api/subjects/bulk-delete", methods=["POST"])
@login_required
@roles_required("admin", "owner", "senior_investigator")
@validate(BulkDeleteSchema)
def bulk_delete_subjects() -> flask.Response:
    """Soft-delete subjects in bulk (consistent with single delete)."""
    data = request.validated_data
    ids = data.get("ids", [])
    if not ids or len(ids) > 100:
        return api_error("Provide a list of up to 100 subject IDs", 400)
    now = datetime.now(timezone.utc)
    count = apply_tenant_filter(
        Subject.query.filter(Subject.id.in_(ids), Subject.is_deleted == False),
        Subject,
    ).update({"is_deleted": True, "deleted_at": now}, synchronize_session=False)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception("Failed to bulk delete subjects")
        return api_error("Failed to delete subjects", 500)
    AuditLog.log(
        user_id=current_user.id,
        action="bulk_delete",
        entity_type="subject",
        ip_address=request.remote_addr,
        description=f"Bulk soft-deleted {count} subjects",
    )
    return jsonify({"deleted": count, "message": f"{count} subjects deleted"})


@cms_bp.route("/subjects/<subject_id>/delete", methods=["POST"])
@login_required
@subject_access_required
@roles_required("admin", "owner", "senior_investigator")
def delete_subject(subject_id: str) -> flask.Response:
    """Soft-delete a subject if not linked to any case."""
    subject = db.session.get(Subject, subject_id) or abort(404)

    # Check if subject is linked to any active case
    linked_case_ids = [
        row.case_id
        for row in db.session.query(case_subjects.c.case_id)
        .filter(case_subjects.c.subject_id == subject_id)
        .all()
    ]
    linked_cases = (
        Case.query.filter(Case.id.in_(linked_case_ids), Case.is_deleted == False).all()
        if linked_case_ids
        else []
    )
    if linked_cases:
        case_list = ", ".join(
            [f"{c.case_number} ({c.title})" for c in linked_cases[:5]]
        )
        extra = f" and {len(linked_cases) - 5} more" if len(linked_cases) > 5 else ""
        return jsonify(
            {
                "error": f"Cannot delete subject: linked to {len(linked_cases)} case(s): {case_list}{extra}"
            }
        ), 400

    subject.soft_delete()

    AuditLog.log(
        user_id=current_user.id,
        action="delete",
        entity_type="subject",
        entity_id=subject_id,
        ip_address=request.remote_addr,
        description=f"Deleted subject ({subject.subject_type})",
    )
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception("Failed to delete subject")
        if request.is_json:
            return api_error("Failed to delete subject", 500)
        flash("Failed to delete subject.", "danger")
        return redirect(url_for("cms.subjects"))

    if request.is_json:
        return api_success({}, "Subject deleted")
    flash(f"Subject {subject.name} deleted.", "info")
    return redirect(url_for("cms.subjects"))
