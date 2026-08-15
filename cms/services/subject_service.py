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
from cms.models import (
    Address,
    Contact,
    Finding,
    ResearchAction,
    SocialAccount,
    Subject,
    SubjectFact,
    SubjectIdentifier,
    User,
    case_subjects,
    db,
    subject_relations,
)
from cms.routes.utils import normalize_phone

logger = logging.getLogger(__name__)


def _parse_dt(value):
    """Parse an ISO-8601 date/datetime string (or passthrough a datetime)."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _sync_verification(obj, status, actor_id):
    """Set/clear verified_by/verified_at from the status transition (D7)."""
    if status == "verified":
        if not obj.verified_by:
            obj.verified_by = actor_id
        if not obj.verified_at:
            obj.verified_at = datetime.now(timezone.utc)
    else:
        obj.verified_by = None
        obj.verified_at = None


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

    # -------------------------------------------------------------------
    # PR7a: profile read-model (single view surface for the tabbed profile)
    # -------------------------------------------------------------------

    def _usernames(self, user_ids):
        """Resolve a set of user ids to {id: username}."""
        ids = {uid for uid in user_ids if uid}
        if not ids:
            return {}
        rows = User.query.filter(User.id.in_(ids)).with_entities(User.id, User.username)
        return {r.id: r.username for r in rows}

    def profile_view(self, subject):
        """Build the full read-model for the tabbed Subject Profile (PR7a).

        Renders exactly what the database stores: the base subject plus every
        provenance-carrying child (identifiers, facts, relations, addresses,
        contacts, social accounts) and the subject's investigation surface
        (linked cases, research actions, findings). Nothing is taken from
        browser state.
        """
        base = subject.to_dict(decrypted=True)
        base["photo_path"] = subject.photo_path
        base["face_encoding_present"] = bool(subject.face_encoding)
        base["created_at"] = (
            subject.created_at.isoformat() if subject.created_at else None
        )
        base["updated_at"] = (
            subject.updated_at.isoformat() if subject.updated_at else None
        )

        # to_dict(decrypted=True) decrypts addresses/contacts in place; read the
        # already-decrypted values without re-decrypting (avoids decrypt noise).
        addresses = [a.to_dict(decrypted=False) for a in subject.addresses]
        contacts = [c.to_dict(decrypted=False) for c in subject.contacts]
        social_accounts = [sa.to_dict() for sa in subject.social_accounts]
        identifiers = [i.to_dict() for i in subject.identifiers]
        facts = [f.to_dict() for f in subject.facts]

        # Relations: single canonical row per pair, resolve both directions.
        relation_rows = db.session.execute(
            subject_relations.select().where(
                db.or_(
                    subject_relations.c.subject_id == subject.id,
                    subject_relations.c.related_subject_id == subject.id,
                )
            )
        ).fetchall()
        related_ids = {
            row.related_subject_id if row.subject_id == subject.id else row.subject_id
            for row in relation_rows
        }
        related_map = {}
        if related_ids:
            related_map = {
                s.id: {"id": s.id, "name": s.name, "subject_type": s.subject_type}
                for s in Subject.query.filter(Subject.id.in_(related_ids)).all()
            }
        outgoing, incoming = [], []
        for row in relation_rows:
            other_id = (
                row.related_subject_id
                if row.subject_id == subject.id
                else row.subject_id
            )
            entry = {
                "relation_type": row.relation_type,
                "direction": row.direction,
                "source": row.source,
                "reliability": row.reliability,
                "status": row.status,
                "observed_at": row.observed_at.isoformat() if row.observed_at else None,
                "case_number": row.case_number,
                "created_by": row.created_by,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "related_subject": related_map.get(other_id),
            }
            (outgoing if row.subject_id == subject.id else incoming).append(entry)

        # Linked cases with role/status/note from the association row.
        case_rows = db.session.execute(
            db.select(
                case_subjects.c.case_id,
                case_subjects.c.role_in_case,
                case_subjects.c.status,
                case_subjects.c.note,
            ).where(case_subjects.c.subject_id == subject.id)
        ).fetchall()
        case_ids = [r.case_id for r in case_rows]
        case_map = {}
        if case_ids:
            from cms.models import Case

            case_map = {
                c.id: c
                for c in Case.query.filter(
                    Case.id.in_(case_ids), Case.is_deleted.is_(False)
                ).all()
            }
        cases = []
        for r in case_rows:
            case = case_map.get(r.case_id)
            if not case:
                continue
            cases.append(
                {
                    "id": case.id,
                    "case_number": case.case_number,
                    "title": case.title,
                    "role_in_case": r.role_in_case,
                    "status": r.status,
                    "note": r.note,
                }
            )
        cases.sort(key=lambda c: c["case_number"])

        # Research actions explicitly scoped to this subject (PR4).
        actions = (
            ResearchAction.query.filter_by(
                subject_id=subject.id, tenant_id=subject.tenant_id
            )
            .filter(ResearchAction.archived_at.is_(None))
            .order_by(ResearchAction.created_at.desc())
            .limit(200)
            .all()
        )
        action_rows = []
        for a in actions:
            snap = a.target_snapshot_data or {}
            action_rows.append(
                {
                    "id": a.id,
                    "case_id": a.case_id,
                    "case_number": a.case.case_number if a.case else "",
                    "action_type": a.action_type,
                    "label": a.label or a.action_type,
                    "status": a.status,
                    "error": a.error,
                    "result_summary": a.result_summary,
                    "target_snapshot": snap,
                    "findings_count": len(a.findings),
                    "created_at": a.created_at.isoformat() if a.created_at else None,
                    "started_at": a.started_at.isoformat() if a.started_at else None,
                    "completed_at": (
                        a.completed_at.isoformat() if a.completed_at else None
                    ),
                }
            )

        # Findings linked to the subject (non-deleted), with verification state.
        findings = (
            subject.findings.filter_by(is_deleted=False)
            .order_by(Finding.created_at.desc())
            .limit(200)
            .all()
        )
        finding_rows = []
        for f in findings:
            first_action = f.research_actions[0] if f.research_actions else None
            finding_rows.append(
                {
                    "id": f.id,
                    "title": f.title,
                    "content": f.content,
                    "detail": f.detail,
                    "source_url": f.source_url,
                    "source_type": f.source_type,
                    "status": f.status or ("verified" if f.verified else "candidate"),
                    "verified": f.verified,
                    "verified_by": f.verified_by,
                    "verified_at": f.verified_at.isoformat() if f.verified_at else None,
                    "integrity_ok": f.verify_integrity(),
                    "created_at": f.created_at.isoformat() if f.created_at else None,
                    "action_id": first_action.id if first_action else None,
                    "action_label": (
                        first_action.label or first_action.action_type
                        if first_action
                        else None
                    ),
                }
            )

        user_ids = set()
        for rows in (
            addresses,
            contacts,
            social_accounts,
            identifiers,
            facts,
            outgoing,
            incoming,
        ):
            for r in rows:
                user_ids.update(
                    v
                    for k, v in r.items()
                    if k in ("created_by", "updated_by", "verified_by")
                )
        usernames = self._usernames(user_ids)

        def _names(entry):
            entry["created_by_name"] = usernames.get(entry.get("created_by")) or "—"
            if "verified_by" in entry:
                entry["verified_by_name"] = (
                    usernames.get(entry.get("verified_by")) or "—"
                )
            if "updated_by" in entry:
                entry["updated_by_name"] = usernames.get(entry.get("updated_by")) or "—"

        for rows in (addresses, contacts, social_accounts, identifiers, facts):
            for r in rows:
                _names(r)
        for rows in (outgoing, incoming):
            for r in rows:
                _names(r)
        for r in finding_rows:
            r["verified_by_name"] = usernames.get(r.get("verified_by")) or "—"

        return {
            "subject": base,
            "addresses": addresses,
            "contacts": contacts,
            "social_accounts": social_accounts,
            "identifiers": identifiers,
            "facts": facts,
            "relations": {"outgoing": outgoing, "incoming": incoming},
            "cases": cases,
            "actions": action_rows,
            "findings": finding_rows,
        }

    # -------------------------------------------------------------------
    # PR7b: fact-layer + structured writes (inline profile editing)
    # -------------------------------------------------------------------

    @staticmethod
    def _apply_encrypted(instance, field, value):
        """Encrypt a plaintext value into an encrypted column (empty -> None)."""
        plain = (value or "").strip()
        setattr(instance, field, encryptor.encrypt(plain) if plain else None)

    def save_identifier(self, subject, data, *, actor_id, identifier_id=None):
        """Create or update a ``SubjectIdentifier`` (fact-layer, D2)."""
        identifier_type = (data.get("identifier_type") or "").strip()
        if not identifier_type:
            raise ValueError("identifier_type is required")

        if identifier_id:
            ident = db.session.get(SubjectIdentifier, identifier_id)
            if not ident or str(ident.subject_id) != str(subject.id):
                raise ValueError("Identifier not found")
            ident.identifier_type = identifier_type
            if data.get("value") is not None:
                ident.set_value(data["value"])
            ident.status = data.get("status") or ident.status
            ident.source = data.get("source") or ident.source
            ident.source_url = data.get("source_url") or ident.source_url
            ident.reliability = data.get("reliability") or ident.reliability
            if data.get("observed_at"):
                ident.observed_at = _parse_dt(data["observed_at"])
            return ident

        if not data.get("value"):
            raise ValueError("value is required")
        ident = SubjectIdentifier(
            subject_id=subject.id,
            tenant_id=subject.tenant_id,
            identifier_type=identifier_type,
            status=data.get("status") or "candidate",
            source=data.get("source"),
            source_url=data.get("source_url"),
            reliability=data.get("reliability"),
            created_by=actor_id,
        )
        if data.get("observed_at"):
            ident.observed_at = _parse_dt(data["observed_at"])
        ident.set_value(data["value"])
        db.session.add(ident)
        return ident

    def delete_identifier(self, subject, identifier_id, *, actor_id):
        ident = db.session.get(SubjectIdentifier, identifier_id)
        if not ident or str(ident.subject_id) != str(subject.id):
            raise ValueError("Identifier not found")
        if ident.finding_id:
            raise ValueError("Cannot delete identifier linked to a finding")
        db.session.delete(ident)

    def save_fact(self, subject, data, *, actor_id, fact_id=None):
        """Create or update a ``SubjectFact`` with verification transitions (D7)."""
        fact_key = (data.get("fact_key") or "").strip()
        if not fact_key:
            raise ValueError("fact_key is required")
        status = data.get("status") or "candidate"

        if fact_id:
            fact = db.session.get(SubjectFact, fact_id)
            if not fact or str(fact.subject_id) != str(subject.id):
                raise ValueError("Fact not found")
            fact.fact_key = fact_key
            if data.get("value") is not None:
                fact.set_value(data["value"])
            fact.status = status
            fact.source = data.get("source") or fact.source
            fact.source_url = data.get("source_url") or fact.source_url
            fact.reliability = data.get("reliability") or fact.reliability
            if data.get("observed_at"):
                fact.observed_at = _parse_dt(data["observed_at"])
            _sync_verification(fact, status, actor_id)
            return fact

        if not data.get("value"):
            raise ValueError("value is required")
        fact = SubjectFact(
            subject_id=subject.id,
            tenant_id=subject.tenant_id,
            fact_key=fact_key,
            status=status,
            source=data.get("source"),
            source_url=data.get("source_url"),
            reliability=data.get("reliability"),
            created_by=actor_id,
        )
        if data.get("observed_at"):
            fact.observed_at = _parse_dt(data["observed_at"])
        fact.set_value(data["value"])
        _sync_verification(fact, status, actor_id)
        db.session.add(fact)
        return fact

    def delete_fact(self, subject, fact_id, *, actor_id):
        fact = db.session.get(SubjectFact, fact_id)
        if not fact or str(fact.subject_id) != str(subject.id):
            raise ValueError("Fact not found")
        if fact.finding_id:
            raise ValueError("Cannot delete fact linked to a finding")
        db.session.delete(fact)

    def _sync_primary_address(self, subject, primary, data):
        """Unset other primaries and mirror the primary to legacy columns."""
        for addr in subject.addresses:
            if addr.id != primary.id:
                addr.is_primary = False
        sync_primary_address_to_subject(
            subject,
            {
                "street": data.get("street"),
                "number": data.get("number"),
                "addition": "",
                "zipcode": data.get("zipcode"),
                "town": data.get("town"),
                "country": data.get("country"),
            },
        )

    def save_address(self, subject, data, *, actor_id, address_id=None):
        """Create or update an ``Address``; primary mirrors to legacy columns."""
        if address_id:
            addr = db.session.get(Address, address_id)
            if not addr or str(addr.subject_id) != str(subject.id):
                raise ValueError("Address not found")
            for field in Address.ENCRYPTED_FIELDS:
                if field in data:
                    self._apply_encrypted(addr, field, data.get(field))
            if "is_primary" in data:
                addr.is_primary = bool(data.get("is_primary"))
            addr.source = data.get("source") or addr.source
            addr.status = data.get("status") or addr.status
            if data.get("observed_at"):
                addr.observed_at = _parse_dt(data["observed_at"])
            addr.updated_by = actor_id
            if addr.is_primary:
                self._sync_primary_address(subject, addr, data)
            return addr

        if not any(data.get(f) for f in ("street", "zipcode")):
            raise ValueError("street or zipcode is required")
        addr = Address(
            subject_id=subject.id,
            tenant_id=subject.tenant_id,
            is_primary=bool(data.get("is_primary")),
            source=data.get("source"),
            status=data.get("status") or "candidate",
        )
        for field in Address.ENCRYPTED_FIELDS:
            self._apply_encrypted(addr, field, data.get(field))
        if data.get("observed_at"):
            addr.observed_at = _parse_dt(data["observed_at"])
        db.session.add(addr)
        if addr.is_primary:
            self._sync_primary_address(subject, addr, data)
        return addr

    def delete_address(self, subject, address_id, *, actor_id):
        addr = db.session.get(Address, address_id)
        if not addr or str(addr.subject_id) != str(subject.id):
            raise ValueError("Address not found")
        if addr.finding_id:
            raise ValueError("Cannot delete address linked to a finding")
        db.session.delete(addr)

    def save_contact(self, subject, data, *, actor_id, contact_id=None):
        """Create or update a ``Contact``; primary mirrors to legacy fields."""
        contact_type = (data.get("contact_type") or "").strip()
        if contact_type not in ("email", "phone"):
            raise ValueError("contact_type must be email or phone")

        if contact_id:
            contact = db.session.get(Contact, contact_id)
            if not contact or str(contact.subject_id) != str(subject.id):
                raise ValueError("Contact not found")
            contact.contact_type = contact_type
            if data.get("value") is not None:
                self._apply_encrypted(contact, "value", data.get("value"))
            if "is_primary" in data:
                contact.is_primary = bool(data.get("is_primary"))
            contact.source = data.get("source") or contact.source
            contact.status = data.get("status") or contact.status
            if data.get("observed_at"):
                contact.observed_at = _parse_dt(data["observed_at"])
            contact.updated_by = actor_id
        else:
            if not data.get("value"):
                raise ValueError("value is required")
            contact = Contact(
                subject_id=subject.id,
                tenant_id=subject.tenant_id,
                contact_type=contact_type,
                is_primary=bool(data.get("is_primary")),
                source=data.get("source"),
                status=data.get("status") or "candidate",
            )
            self._apply_encrypted(contact, "value", data.get("value"))
            if data.get("observed_at"):
                contact.observed_at = _parse_dt(data["observed_at"])
            db.session.add(contact)

        if contact.is_primary:
            for other in subject.contacts:
                if other.id != contact.id:
                    other.is_primary = False
            if data.get("value") is not None:
                plain = (data["value"] or "").strip()
            else:
                try:
                    plain = encryptor.decrypt(contact.value) if contact.value else ""
                except Exception:
                    plain = contact.value or ""
            sync_legacy_contact_fields(
                subject,
                {
                    "contact_type": contact_type,
                    "value": plain,
                    "is_primary": True,
                },
            )
        return contact

    def delete_contact(self, subject, contact_id, *, actor_id):
        contact = db.session.get(Contact, contact_id)
        if not contact or str(contact.subject_id) != str(subject.id):
            raise ValueError("Contact not found")
        if contact.finding_id:
            raise ValueError("Cannot delete contact linked to a finding")
        db.session.delete(contact)

    def save_social_account(self, subject, data, *, actor_id, account_id=None):
        """Create or update a ``SocialAccount`` with provenance."""
        platform = (data.get("platform") or "").strip().lower()
        username = (data.get("username") or "").strip()
        if not platform or not username:
            raise ValueError("platform and username are required")

        if account_id:
            account = db.session.get(SocialAccount, account_id)
            if not account or str(account.subject_id) != str(subject.id):
                raise ValueError("Social account not found")
            account.platform = platform
            account.username = username
            account.url = data.get("url") or account.url
            account.account_id = data.get("account_id") or account.account_id
            account.source = data.get("source") or account.source
            account.status = data.get("status") or account.status
            if data.get("observed_at"):
                account.observed_at = _parse_dt(data["observed_at"])
            account.updated_by = actor_id
            return account

        account = SocialAccount(
            tenant_id=subject.tenant_id,
            subject_id=subject.id,
            platform=platform,
            username=username,
            url=data.get("url"),
            account_id=data.get("account_id"),
            source=data.get("source"),
            status=data.get("status") or "candidate",
            updated_by=actor_id,
        )
        if data.get("observed_at"):
            account.observed_at = _parse_dt(data["observed_at"])
        db.session.add(account)
        return account

    def delete_social_account(self, subject, account_id, *, actor_id):
        account = db.session.get(SocialAccount, account_id)
        if not account or str(account.subject_id) != str(subject.id):
            raise ValueError("Social account not found")
        if account.finding_id:
            raise ValueError("Cannot delete social account linked to a finding")
        db.session.delete(account)

    def save_relation(self, subject, data, *, actor_id):
        """Create or update a canonical relation row (ADR-0001 PR3)."""
        related_id = data.get("related_subject_id")
        if not related_id:
            raise ValueError("related_subject_id is required")
        if str(related_id) == str(subject.id):
            raise ValueError("Cannot relate a subject to itself")
        related = db.session.get(Subject, related_id)
        if not related or related.tenant_id != subject.tenant_id or related.is_deleted:
            raise ValueError("Related subject not found")

        relation_type = data.get("relation_type") or "other"
        if relation_type not in ("family", "business", "other"):
            relation_type = "other"
        direction = data.get("direction") or "mutual"
        if direction not in ("outgoing", "incoming", "mutual"):
            direction = "mutual"

        a, b = sorted([str(subject.id), str(related_id)])
        values = {
            "relation_type": relation_type,
            "direction": direction,
            "source": data.get("source"),
            "reliability": data.get("reliability"),
            "status": data.get("status") or "candidate",
            "case_number": data.get("case_number"),
        }
        existing = db.session.execute(
            subject_relations.select().where(
                (subject_relations.c.subject_id == a)
                & (subject_relations.c.related_subject_id == b)
            )
        ).first()
        if existing:
            db.session.execute(
                subject_relations.update()
                .where(
                    (subject_relations.c.subject_id == a)
                    & (subject_relations.c.related_subject_id == b)
                )
                .values(**values)
            )
        else:
            db.session.execute(
                subject_relations.insert().values(
                    subject_id=a, related_subject_id=b, created_by=actor_id, **values
                )
            )
        return {"subject_id": a, "related_subject_id": b}

    def delete_relation(self, subject, related_subject_id, *, actor_id):
        a, b = sorted([str(subject.id), str(related_subject_id)])
        result = db.session.execute(
            subject_relations.delete().where(
                (subject_relations.c.subject_id == a)
                & (subject_relations.c.related_subject_id == b)
            )
        )
        if result.rowcount == 0:
            raise ValueError("Relationship not found")


subject_service = SubjectService()
