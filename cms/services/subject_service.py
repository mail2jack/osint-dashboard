"""
SubjectService — single read/write path for subject profiles.

PR2 of ADR-0001 (subject-first investigations). Both input paths (the
standalone subject CRUD routes and the workflow case screens) call this
service; neither path writes subject columns directly. This PR also fixes the
round-trip failures documented in ``docs/subject-model-inventory.md`` §5.1 —
no data-model change yet (identifiers/facts arrive in PR3).

Round-trip guarantee (per type): create(input) -> edit shows input ->
save() -> reopen edit -> view shows value.
"""

import json
import logging
from datetime import datetime, timezone

from cms.encryption_utils import EncryptionError, encryptor
from cms.models import Address, Contact, SocialAccount, Subject, db
from cms.routes.utils import normalize_phone

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Field groups (single source of truth for both input paths)
# ---------------------------------------------------------------------------

PERSON_TEXT_FIELDS = [
    "achternaam",
    "voornamen",
    "voorletters",
    "tussenvoegsels",
    "geslacht",
    "reisdocument_type",
]

PERSON_ENCRYPTED_FIELDS = [
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

VEHICLE_ENCRYPTED_FIELDS = ["license_plate", "vin", "insurance_company"]
VEHICLE_PLAIN_FIELDS = ["brand", "vehicle_type"]

VESSEL_ENCRYPTED_FIELDS = [
    "imo_number",
    "mmsi",
    "eni_number",
    "vessel_nationality",
]

# Entity / financial / risk fields: plain text, written on create AND edit.
ORG_PLAIN_FIELDS = [
    "registration_number",
    "legal_form",
    "asset_type",
    "estimated_value",
    "currency",
]

CREATE_RDW_FIELDS = [
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

EDIT_RDW_FIELDS = [
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def parse_json_data(raw):
    """Parse a JSON string or return a list/dict as-is."""
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return raw
    return raw


def compute_display_name(data, current=None) -> str:
    """Compute the display ``name`` server-side from split name fields.

    Split fields win; a provided ``name`` is only used when no split field is
    present (API clients). Falls back to the stored value for round-trips.
    """
    parts = []
    for field in ("voorletters", "voornamen", "tussenvoegsels", "achternaam"):
        value = data.get(field)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
    if parts:
        return " ".join(parts).strip()
    raw = data.get("name")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    if current is not None:
        cur_parts = [
            p
            for p in (
                current.voorletters,
                current.voornamen,
                current.tussenvoegsels,
                current.achternaam,
            )
            if p
        ]
        if cur_parts:
            return " ".join(cur_parts).strip()
        return current.name or ""
    return ""


def _auto_prefix_name(subject_type: str, name: str) -> str:
    """Auto-prepend ``@`` for online/account entities."""
    if subject_type == "online" and name and not name.startswith("@"):
        return "@" + name
    return name


def _coerce_amount(value):
    """Coerce a numeric-ish value for ``estimated_value`` (Numeric column)."""
    if value in (None, ""):
        return None
    try:
        from decimal import Decimal

        return Decimal(str(value))
    except Exception:
        return value


def _coerce_risk_factors(value):
    """Accept a list, a JSON string, or a comma-separated string."""
    if value is None or value == "":
        return None
    if isinstance(value, str):
        value = parse_json_data(value)
        if isinstance(value, str):
            value = [p.strip() for p in value.split(",") if p.strip()]
    if isinstance(value, list) and not value:
        return None
    return value


def extract_rdw_data(data, fields):
    """Pull RDW-specific fields from form data into a dict."""
    rdw_data = {}
    for field in fields:
        if data.get(field):
            rdw_data[field] = data.get(field)
    return rdw_data


def sync_primary_address_to_subject(subject, primary_addr):
    """Write the primary address back to legacy Subject columns."""
    parts = []
    if primary_addr.get("street"):
        parts.append(primary_addr["street"])
    if primary_addr.get("number"):
        parts.append(primary_addr["number"])
    if primary_addr.get("addition"):
        parts.append(primary_addr["addition"])
    street_loc = " ".join(parts)
    pc = primary_addr.get("zipcode", "")
    town = primary_addr.get("town", "")
    subject.address = ", ".join(p for p in [street_loc, f"{pc} {town}".strip()] if p)
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


def save_addresses(subject, addresses_data, replace_existing=False):
    """Create Address records and sync primary to legacy fields.

    When *replace_existing* is ``True`` all prior addresses are deleted first.
    Returns the count of removed addresses (0 when not replacing).
    """
    old_count = 0
    if replace_existing and addresses_data:
        old_addresses = list(subject.addresses)
        old_count = len(old_addresses)
        for addr in old_addresses:
            db.session.delete(addr)

    primary_addr = None
    for addr_data in addresses_data:
        if addr_data.get("street") or addr_data.get("zipcode"):
            number = str(addr_data.get("number") or "")
            addition = str(addr_data.get("addition") or "")
            address = Address(
                subject_id=subject.id,
                street=addr_data.get("street"),
                number=number + addition if addition else number,
                zipcode=addr_data.get("zipcode"),
                town=addr_data.get("town"),
                country=addr_data.get("country", "Netherlands"),
                is_primary=addr_data.get("is_primary", False),
            )
            address.encrypt_fields()
            db.session.add(address)
            if addr_data.get("is_primary") or not primary_addr:
                primary_addr = addr_data

    if primary_addr:
        sync_primary_address_to_subject(subject, primary_addr)

    return old_count


def sync_legacy_contact_fields(subject, c_data):
    """Set the primary email/phone on the Subject for backward compat."""
    if c_data.get("contact_type") == "email" and c_data.get("is_primary"):
        new_val = c_data.get("value")
        try:
            current = encryptor.decrypt(subject.email) if subject.email else None
        except Exception:
            current = subject.email
        if new_val != current:
            subject.email = encryptor.encrypt(new_val) if new_val else None
    elif c_data.get("contact_type") == "phone" and c_data.get("is_primary"):
        new_val = normalize_phone(c_data.get("value"))
        try:
            current = encryptor.decrypt(subject.phone) if subject.phone else None
        except Exception:
            current = subject.phone
        if new_val != current:
            subject.phone = encryptor.encrypt(new_val) if new_val else None


def save_contacts(subject, contacts_data, replace_existing=False):
    """Create Contact records and sync primary to legacy fields.

    When *replace_existing* is ``True`` all prior contacts are deleted first.
    Returns the count of removed contacts (0 when not replacing).
    """
    old_count = 0
    if replace_existing and contacts_data:
        old_contacts = list(subject.contacts)
        old_count = len(old_contacts)
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
            sync_legacy_contact_fields(subject, c_data)

    return old_count


def update_encrypted_fields(subject, data, fields, changes, sensitive=True):
    """Update encrypted fields on *subject*, recording diffs in *changes*.

    When *sensitive* is ``True`` the change log shows ``[encrypted]`` for both
    old and new values.  When ``False`` the (decrypted) values are stored.
    """
    for field in fields:
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
            if field == "phone" and new_value:
                new_value = normalize_phone(new_value)
            if new_value != old_value:
                if sensitive:
                    changes[field] = {"old": "[encrypted]", "new": "[encrypted]"}
                else:
                    changes[field] = {
                        "old": old_value or "[empty]",
                        "new": new_value or "[empty]",
                    }
                if new_value:
                    setattr(subject, field, encryptor.encrypt(new_value))
                else:
                    setattr(subject, field, None)


def update_plain_fields(subject, data, fields, changes=None, coerce=None):
    """Update non-encrypted fields.

    When *changes* is ``None`` the value is set unconditionally (create).
    When a dict is provided only actual changes are recorded (edit).
    ``coerce`` maps field names to callables applied before assignment.
    """
    coerce = coerce or {}
    for field in fields:
        if field in data:
            value = data[field]
            if callable(coerce.get(field)):
                value = coerce[field](value)
            if changes is not None:
                if value != getattr(subject, field):
                    changes[field] = {
                        "old": getattr(subject, field) or "[empty]",
                        "new": value or "[empty]",
                    }
                    setattr(subject, field, value)
            else:
                setattr(subject, field, value)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class SubjectService:
    """Single read/write service for subject profiles (both input paths)."""

    def _build_subject_from_data(self, data, created_by, tenant_id):
        """Construct a Subject from validated form data (create)."""
        subject_type = data.get("subject_type", "person")
        name = compute_display_name(data)
        name = _auto_prefix_name(subject_type, name)
        return Subject(
            name=name or "Onbekend",
            subject_type=subject_type,
            tenant_id=tenant_id,
            risk_score=data.get("risk_score", 0),
            risk_factors=_coerce_risk_factors(data.get("risk_factors")),
            notes=data.get("notes"),
            registration_number=data.get("registration_number"),
            legal_form=data.get("legal_form"),
            asset_type=data.get("asset_type"),
            estimated_value=_coerce_amount(data.get("estimated_value")),
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
            created_by=created_by,
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

    def create(self, data, *, created_by, tenant_id):
        """Create a subject and persist its structured data. Returns Subject."""
        subject = self._build_subject_from_data(data, created_by, tenant_id)

        if subject.subject_type == "vehicle":
            rdw_data = extract_rdw_data(data, CREATE_RDW_FIELDS)
            if rdw_data or data.get("license_plate"):
                rdw_data["kenteken"] = (data.get("license_plate") or "").upper()
                rdw_data["merk"] = data.get("brand", "")
                rdw_data["inrichting"] = data.get("vehicle_type", "")
                if data.get("eerste_kleur"):
                    rdw_data["kleur"] = data.get("eerste_kleur")
                subject.rdw_data = rdw_data

        if subject.subject_type == "vessel" and data.get("vessel_data"):
            subject.vessel_data = parse_json_data(data["vessel_data"])

        subject.encrypt_identifiers()
        db.session.add(subject)
        db.session.flush()  # Subject ID needed before adding addresses/contacts

        addresses = (
            parse_json_data(data.get("addresses_data"))
            if data.get("addresses_data")
            else []
        )
        if addresses:
            save_addresses(subject, addresses)

        contacts = (
            parse_json_data(data.get("contacts_data"))
            if data.get("contacts_data")
            else []
        )
        if contacts:
            save_contacts(subject, contacts)

        if subject.subject_type == "online":
            self.set_social_accounts(
                subject,
                data.get("social_accounts") or [],
                tenant_id=tenant_id,
                platform=data.get("online_platform"),
                profile_url=data.get("online_profile_url"),
            )

        return subject

    def edit(self, subject, data, *, actor_id):
        """Apply an edit with full round-trip coverage. Returns *changes* dict."""
        changes = {}

        # Name is recomputed server-side from split fields (never trusted
        # from the client's hidden field).
        split_present = any(
            key in data
            for key in ("voorletters", "voornamen", "tussenvoegsels", "achternaam")
        )
        if split_present:
            new_name = compute_display_name(data)
        elif "name" in data:
            new_name = (data.get("name") or "").strip()
        else:
            new_name = compute_display_name({}, current=subject)
        new_name = _auto_prefix_name(subject.subject_type, new_name)
        if new_name and new_name != subject.name:
            changes["name"] = {"old": subject.name, "new": new_name}
            subject.name = new_name

        if (
            "subject_type" in data
            and data.get("subject_type")
            and data["subject_type"] != subject.subject_type
        ):
            changes["subject_type"] = {
                "old": subject.subject_type,
                "new": data["subject_type"],
            }
            subject.subject_type = data["subject_type"]

        if "risk_score" in data:
            try:
                new_risk = int(data["risk_score"]) if data["risk_score"] else 0
            except (ValueError, TypeError):
                new_risk = subject.risk_score
            if new_risk != subject.risk_score:
                changes["risk_score"] = {
                    "old": subject.risk_score,
                    "new": new_risk,
                }
                subject.risk_score = new_risk

        if "notes" in data:
            subject.notes = data["notes"] or None

        update_plain_fields(subject, data, PERSON_TEXT_FIELDS, changes)
        update_plain_fields(subject, data, VEHICLE_PLAIN_FIELDS, changes)
        update_plain_fields(
            subject,
            data,
            ORG_PLAIN_FIELDS,
            changes,
            coerce={
                "estimated_value": _coerce_amount,
                "currency": lambda v: str(v).upper() if v else v,
            },
        )
        if "risk_factors" in data:
            new_factors = _coerce_risk_factors(data.get("risk_factors"))
            if new_factors != subject.risk_factors:
                changes["risk_factors"] = {
                    "old": subject.risk_factors or "[empty]",
                    "new": new_factors or "[empty]",
                }
                subject.risk_factors = new_factors

        update_encrypted_fields(subject, data, PERSON_ENCRYPTED_FIELDS, changes)
        update_encrypted_fields(subject, data, VEHICLE_ENCRYPTED_FIELDS, changes)
        update_encrypted_fields(
            subject, data, VESSEL_ENCRYPTED_FIELDS, changes, sensitive=False
        )

        if data.get("addresses_data"):
            addresses_data = parse_json_data(data["addresses_data"])
            if addresses_data:
                old_count = save_addresses(
                    subject, addresses_data, replace_existing=True
                )
                changes["addresses"] = {
                    "old": f"{old_count} address(es)",
                    "new": f"{len(addresses_data)} address(es)",
                }

        if data.get("contacts_data"):
            contacts_data = parse_json_data(data["contacts_data"])
            if contacts_data:
                old_count = save_contacts(subject, contacts_data, replace_existing=True)
                changes["contacts"] = {
                    "old": f"{old_count} contact(s)",
                    "new": f"{len(contacts_data)} contact(s)",
                }

        rdw_data = extract_rdw_data(data, EDIT_RDW_FIELDS)
        if data.get("license_plate"):
            rdw_data["kenteken"] = data["license_plate"]
        if data.get("brand"):
            rdw_data["merk"] = data["brand"]
        if data.get("vehicle_type"):
            rdw_data["inrichting"] = data["vehicle_type"]
        if data.get("vin"):
            rdw_data["chassisnummer"] = data["vin"]
        if rdw_data:
            existing_rdw = dict(subject.rdw_data or {})
            existing_rdw.update(rdw_data)
            subject.rdw_data = existing_rdw
            changes["rdw_data"] = {"old": "updated", "new": "RDW fields updated"}

        if data.get("vessel_data"):
            subject.vessel_data = parse_json_data(data["vessel_data"])
            changes["vessel_data"] = {"old": "updated", "new": "Vessel data updated"}

        if "social_accounts" in data and data.get("social_accounts"):
            self.set_social_accounts(
                subject,
                data["social_accounts"],
                tenant_id=subject.tenant_id,
            )
            changes["social_accounts"] = {"old": "updated", "new": "Updated"}

        subject.updated_at = datetime.now(timezone.utc)
        return changes

    def set_social_accounts(
        self, subject, handles, *, tenant_id, platform=None, profile_url=None
    ):
        """Persist social handles to ``SocialAccount`` rows (canonical).

        The workflow's free-text handles become rows; ``workflow_social_accounts``
        is mirrored for the legacy workflow screens (D2 mirror while the flag
        is off). Rows linked to a finding are never removed.
        """
        normalized = []
        for handle in handles:
            if not handle or not str(handle).strip():
                continue
            raw = str(handle).strip()
            if not raw.startswith("@"):
                raw = "@" + raw
            normalized.append(raw)
        if platform:
            handle = "@" + str(subject.name or "").lstrip("@")
            if handle.strip("@") and handle not in normalized:
                normalized.append(handle)

        desired = set(normalized)

        existing = {sa.username: sa for sa in subject.social_accounts}
        for username, sa in existing.items():
            at_username = username if username.startswith("@") else "@" + username
            if at_username not in desired and sa.finding_id is None:
                db.session.delete(sa)
                existing.pop(username, None)

        for handle in normalized:
            username = handle.lstrip("@")
            if username and username not in existing:
                db.session.add(
                    SocialAccount(
                        tenant_id=tenant_id,
                        subject_id=subject.id,
                        platform=(platform or "unknown").strip().lower(),
                        username=username,
                        url=profile_url,
                    )
                )

        subject.workflow_social_accounts = normalized or None

    def view(self, subject):
        """Return the complete decrypted read-model for *subject*."""
        data = subject.to_dict(decrypted=True)
        data["workflow_social_accounts"] = subject.workflow_social_accounts or []
        data["social_accounts"] = [sa.to_dict() for sa in subject.social_accounts]
        data["updated_at"] = (
            subject.updated_at.isoformat() if subject.updated_at else None
        )
        return data


subject_service = SubjectService()
