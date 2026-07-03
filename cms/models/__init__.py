"""
Case Management System - Database Models
========================================
PostgreSQL database models for professional investigation management.
All sensitive fields are encrypted at rest for GDPR compliance.

Design Decisions:
- PostgreSQL chosen for: ACID compliance, JSON support, row-level security,
  excellent performance with complex queries, and mature audit features.
- Soft deletes ensure data retention for legal/compliance requirements.
- Encrypted fields use Fernet symmetric encryption with external key management.
- UUID primary keys for security (non-sequential, unpredictable).
"""

import uuid
import json
import hashlib
import re
import secrets
import logging
from datetime import datetime, timedelta, timezone
from enum import Enum as PyEnum
from typing import Any

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.types import JSON as _BaseJSON
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash


class SafeJSON(_BaseJSON):
    """JSON type that always returns Python objects, even on SQLite where
    JSON columns may return raw strings (e.g. after ALTER TABLE migrations)."""

    def process_result_value(self, value, dialect) -> object:
        if value is not None and isinstance(value, str):
            try:
                return json.loads(value)
            except (json.JSONDecodeError, TypeError):
                return value
        return value


from ..encryption_utils import encryptor


db = SQLAlchemy()


# Import extracted model modules — these register their models with SQLAlchemy
# via the `db` import. All classes are available at `cms.models.ClassName`.
from .setting import Setting
from .comments import Comment, CommentEditHistory
from .billing import (
    InvoiceStatus,
    Invoice,
    InvoiceItem,
    Payment,
    CreditNote,
    CreditNoteItem,
)
from .background_task import BackgroundTask
from .platform_setting import PlatformSetting
from .invitation import Invitation
from .usage_record import UsageRecord
from .announcement import Announcement, AnnouncementAck
from .dpa import DpaRecord
from .breach import BreachRecord

# Explicit re-exports — used by other modules importing from cms.models
__all__ = [
    "Setting",
    "Comment",
    "CommentEditHistory",
    "InvoiceStatus",
    "Invoice",
    "InvoiceItem",
    "Payment",
    "BackgroundTask",
    "PlatformSetting",
    "Invitation",
    "UsageRecord",
    "NotificationPreference",
    "NOTIFICATION_CATEGORIES",
    "Announcement",
    "AnnouncementAck",
    "DpaRecord",
    "BreachRecord",
]


class UserRole(PyEnum):
    """User roles with hierarchical permissions."""

    OWNER = "owner"  # Tenant owner — full access within tenant
    ADMIN = "admin"  # Full system access
    SENIOR_INVESTIGATOR = "senior_investigator"  # Can manage cases, export data
    INVESTIGATOR = "investigator"  # Can view and update assigned cases
    JUNIOR_INVESTIGATOR = "junior_investigator"  # Can view and update assigned cases
    VIEWER = "viewer"  # Read-only access


class CasePriority(PyEnum):
    """Case priority levels."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class CaseStatus(PyEnum):
    """Case lifecycle statuses."""

    OPEN = "open"  # New case, not yet started
    ACTIVE = "active"  # Investigation in progress
    SUSPENDED = "suspended"  # Temporarily paused
    CLOSED = "closed"  # Investigation complete
    ARCHIVED = "archived"  # Archived for compliance


class CaseType(PyEnum):
    """Case types based on CBS criminaliteitscijfers definitions."""

    HKDEELNAME = "hkdeelname"
    HKBELASTING = "hkbelasting"
    HKWOON = "hkwoon"
    HKBURGERLIJK = "hkburgerlijk"
    HKMIGRATIEACHTER = "hkmigratieachter"
    HKEENPERS = "hkeenpers"
    HKJAARMED = "hkjaarmed"
    HKHERKOMST = "hkherkomst"
    HKETNICITEIT = "hketniciteit"
    HKGESLACHT = "hkgeslacht"
    HKLEEFTIJD = "hkleeftijd"
    HKEENGEZ = "hkeengez"
    HKVERZOPENKINDEREN = "hkverzopenkinderen"
    HKKINDETAL = "hkindetal"
    HKLFTCAT = "hklftcat"
    HKVERMISSING = "hkvermissing"
    HKMOORD = "hkmoord"
    HKDOODMOORD = "hkdoodmoord"
    HKDIEFSTAL = "hkdiefstal"
    HKINBRAAKWONING = "hkinbraakwoning"
    HKOVERVAL = "hkoverval"
    HKMISHANDELING = "hkmishandeling"
    HKBEDREIGING = "hkbedreiging"
    HKSEKSMISBRUIK = "hkseksmisbruik"
    HKVERKRACHTING = "hkverkrachting"
    HKDRUGS = "hkdrugs"
    HKVUURWERK = "hkvuurwerk"
    HKECONOMY = "hkeconomy"
    HKFRAUDE = "hkfraude"
    HKOMZETTINGVPH = "hkomzettingvph"
    HKVERBLIJFSTITEL = "hkverblijfstitel"
    HKJAARMEDINKOMEN = "hkjaarmedinkomen"
    HKADRES = "hkadres"
    HKINDELING = "hkindeling"
    HKINKOMEN = "hkinkomen"
    HKSOORTINKOMEN = "hksoortinkomen"
    HKWOZ = "hkwoz"
    HKSTAPPEN = "hkstappen"
    HKZAAK = "hkzaak"
    HKZAAKTYPE = "hkzaaktype"
    HKDATUM = "hkdatum"
    HKPERIODE = "hkperiode"
    HKKLASSE = "hkklasse"
    HKVERWIJZING = "hkverwijzing"
    HKLOCATIE = "hklocatie"


class SubjectType(PyEnum):
    """Types of subjects that can be investigated."""

    PERSON = "person"
    COMPANY = "company"
    ORGANIZATION = "organization"
    VEHICLE = "vehicle"
    VESSEL = "vessel"


class VerificationStatus(PyEnum):
    """Financial record verification status."""

    PENDING = "pending"
    VERIFIED = "verified"
    FLAGGED = "flagged"
    DISPUTED = "disputed"


class AuditAction(PyEnum):
    """Types of auditable actions."""

    CREATE = "create"
    READ = "read"
    UPDATE = "update"
    DELETE = "delete"
    EXPORT = "export"
    LOGIN = "login"
    LOGOUT = "logout"


# =============================================================================
# Association Tables
# =============================================================================

case_assignments = db.Table(
    "case_assignments",
    db.Column("case_id", db.String(36), db.ForeignKey("cases.id"), primary_key=True),
    db.Column("user_id", db.String(36), db.ForeignKey("users.id"), primary_key=True),
    db.Column("assigned_at", db.DateTime, default=lambda: datetime.now(timezone.utc)),
    db.Column("assigned_by", db.String(36), db.ForeignKey("users.id"), index=True),
)

case_subjects = db.Table(
    "case_subjects",
    db.Column("case_id", db.String(36), db.ForeignKey("cases.id"), primary_key=True),
    db.Column(
        "subject_id", db.String(36), db.ForeignKey("subjects.id"), primary_key=True
    ),
)

subject_relations = db.Table(
    "subject_relations",
    db.Column(
        "subject_id", db.String(36), db.ForeignKey("subjects.id"), primary_key=True
    ),
    db.Column(
        "related_subject_id",
        db.String(36),
        db.ForeignKey("subjects.id"),
        primary_key=True,
    ),
    db.Column(
        "relationship_type", db.String(100)
    ),  # e.g., "family_member", "business_partner"
    db.Column("created_at", db.DateTime, default=lambda: datetime.now(timezone.utc)),
)


# =============================================================================
# User Model
# =============================================================================


class User(UserMixin, db.Model):
    """
    User model with role-based access control.

    Security features:
    - Password hashing using werkzeug's secure method
    - Role-based permissions
    - Account activation/deactivation
    - Last login tracking
    """

    __tablename__ = "users"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    hashed_password = db.Column(db.String(255), nullable=False)
    full_name = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(30), nullable=False, default=UserRole.VIEWER.value)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    last_login = db.Column(db.DateTime)

    # 2FA (TOTP)
    totp_secret = db.Column(db.String(64), nullable=True)
    totp_enabled = db.Column(db.Boolean, default=False, nullable=False)
    backup_codes = db.Column(
        db.Text, nullable=True
    )  # JSON array of hashed backup codes

    # Password reset
    password_reset_token = db.Column(db.String(128), nullable=True)
    password_reset_expires = db.Column(db.DateTime, nullable=True)

    # Multi-tenant
    tenant_id = db.Column(
        db.String(36), db.ForeignKey("tenants.id"), nullable=False, index=True
    )
    is_super_admin = db.Column(db.Boolean, default=False, nullable=False)

    # Account lockout
    failed_login_attempts = db.Column(db.Integer, default=0, nullable=False)
    locked_until = db.Column(db.DateTime, nullable=True)

    # SMS/WhatsApp notifications
    phone_number = db.Column(db.String(30), nullable=True)

    # Relationships
    assigned_cases = db.relationship(
        "Case",
        secondary=case_assignments,
        primaryjoin="User.id==case_assignments.c.user_id",
        secondaryjoin="Case.id==case_assignments.c.case_id",
        backref=db.backref("investigators", lazy="dynamic"),
        lazy="dynamic",
    )
    created_cases = db.relationship(
        "Case", foreign_keys="Case.created_by", backref="creator", lazy="dynamic"
    )
    findings = db.relationship("Finding", backref="author", lazy="dynamic")
    audit_logs = db.relationship("AuditLog", backref="user", lazy="dynamic")

    def set_password(self, password: str) -> None:
        """Hash and set password securely."""
        self.hashed_password = generate_password_hash(password, method="pbkdf2:sha256")

    def check_password(self, password: str) -> bool:
        """Verify password against hash."""
        return check_password_hash(self.hashed_password, password)

    def has_role(self, *roles) -> bool:
        """Check if user has any of the specified roles."""
        return self.role in [r.value if isinstance(r, UserRole) else r for r in roles]

    def can_access_case(self, case) -> bool:
        """Check if user can access a specific case."""
        if self.is_super_admin:
            return True
        if getattr(case, "is_deleted", False):
            return False
        if not case.tenant_id or case.tenant_id != self.tenant_id:
            return False
        if self.is_admin:
            return True
        # Creator always has access
        if case.created_by == self.id:
            return True
        # Lead investigator always has access
        if case.lead_investigator_id == self.id:
            return True
        # Direct assignee has access
        if case.assigned_to == self.id:
            return True
        # Assigned via case_assignments table has access
        return self in case.investigators

    @property
    def is_admin(self) -> bool:
        return self.role in (UserRole.ADMIN.value, UserRole.OWNER.value)

    @property
    def is_senior(self) -> bool:
        return self.role in (
            UserRole.ADMIN.value,
            UserRole.OWNER.value,
            UserRole.SENIOR_INVESTIGATOR.value,
        )

    @property
    def can_export(self) -> bool:
        """Only senior investigators and admins can export data."""
        return self.is_senior

    @property
    def is_tenant_owner(self) -> bool:
        return bool(self.owned_tenant)

    def to_dict(self, include_sensitive: bool = False) -> dict:
        """Serialize user without password."""
        return {
            "id": self.id,
            "username": self.username,
            "email": self.email,
            "full_name": self.full_name,
            "role": self.role,
            "is_active": self.is_active,
            "is_super_admin": self.is_super_admin,
            "tenant_id": self.tenant_id,
            "last_login": self.last_login.isoformat() if self.last_login else None,
        }

    def generate_reset_token(self) -> str:
        token = secrets.token_urlsafe(48)
        self.password_reset_token = hashlib.sha256(token.encode()).hexdigest()
        self.password_reset_expires = datetime.now(timezone.utc) + timedelta(hours=48)
        return token

    def verify_reset_token(self, token: str) -> bool:
        now = datetime.now(timezone.utc)
        if not self.password_reset_expires:
            return False
        expires = self.password_reset_expires
        if expires.tzinfo is None:
            now = now.replace(tzinfo=None)
        if now > expires:
            return False
        expected = hashlib.sha256(token.encode()).hexdigest()
        return secrets.compare_digest(self.password_reset_token, expected)

    def clear_reset_token(self) -> None:
        self.password_reset_token = None
        self.password_reset_expires = None


# =============================================================================
# Client Model
# =============================================================================


class Client(db.Model):
    """
    Client model for organizations commissioning investigations.

    Sensitive NAW data is encrypted at rest.
    """

    __tablename__ = "clients"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = db.Column(
        db.String(36), db.ForeignKey("tenants.id"), nullable=False, index=True
    )
    name = db.Column(db.String(200), nullable=False, index=True)
    is_company = db.Column(db.Boolean, default=False)
    date_of_birth = db.Column(db.String(500))  # Encrypted (for persons)
    place_of_birth = db.Column(db.String(500))  # Encrypted (for persons)
    contact_person = db.Column(db.String(500))  # Encrypted
    contact_email = db.Column(db.String(500))  # Encrypted
    contact_phone = db.Column(db.String(500))  # Encrypted
    address_street = db.Column(db.String(500))  # Encrypted
    address_number = db.Column(db.String(500))  # Encrypted
    address_city = db.Column(db.String(500))  # Encrypted
    address_postal = db.Column(db.String(500))  # Encrypted
    address_country = db.Column(db.String(500))  # Encrypted
    reference = db.Column(db.String(100))  # Client reference from workflow
    contract_number = db.Column(db.String(500))
    contract_info = db.Column(db.Text)
    social_security_number = db.Column(db.String(500))  # Encrypted
    vat_number = db.Column(db.String(500))  # For companies
    bank_account = db.Column(db.String(500))  # Encrypted
    financial_notes = db.Column(db.Text)
    is_active = db.Column(db.Boolean, default=True)
    is_deleted = db.Column(db.Boolean, default=False, index=True)  # Soft delete
    created_by = db.Column(db.String(36), db.ForeignKey("users.id"), index=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    creator = db.relationship("User", foreign_keys=[created_by])
    cases = db.relationship("Case", backref="client", lazy="dynamic")
    invoices = db.relationship("Invoice", backref="client", lazy="dynamic")
    contacts = db.relationship(
        "Contact",
        backref="client",
        lazy="dynamic",
        foreign_keys="Contact.client_id",
        order_by="Contact.is_primary.desc(), Contact.created_at",
    )
    addresses = db.relationship(
        "Address",
        backref="client",
        lazy="dynamic",
        foreign_keys="Address.client_id",
        order_by="Address.is_primary.desc(), Address.created_at",
    )

    # Encrypted fields list for reference
    ENCRYPTED_FIELDS = [
        "contact_person",
        "contact_email",
        "contact_phone",
        "address_street",
        "address_number",
        "address_city",
        "address_postal",
        "address_country",
        "social_security_number",
        "bank_account",
        "date_of_birth",
        "place_of_birth",
    ]

    def encrypt_naw(self) -> None:
        """Encrypt all NAW fields before saving."""
        for field in self.ENCRYPTED_FIELDS:
            value = getattr(self, field)
            if value:
                setattr(self, field, encryptor.encrypt(value))

    def decrypt_naw(self) -> None:
        """Decrypt all NAW fields for display."""
        for field in self.ENCRYPTED_FIELDS:
            value = getattr(self, field)
            if value:
                try:
                    setattr(self, field, encryptor.decrypt(value))
                except Exception:
                    logger.warning(
                        "Decrypt failed for %s.%s (id=%s)",
                        self.__class__.__name__,
                        field,
                        getattr(self, "id", "?"),
                    )

    def soft_delete(self) -> None:
        """Soft delete the client."""
        self.is_deleted = True
        self.deleted_at = datetime.now(timezone.utc)
        self.is_active = False

    def to_dict(self, decrypted: bool = True) -> dict:
        """Serialize client data."""
        if decrypted:
            self.decrypt_naw()

        return {
            "id": self.id,
            "name": self.name,
            "contact_person": self.contact_person,
            "contact_email": self.contact_email,
            "contact_phone": self.contact_phone,
            "address": {
                "street": self.address_street,
                "number": self.address_number,
                "city": self.address_city,
                "postal": self.address_postal,
                "country": self.address_country,
            },
            "contract_number": self.contract_number,
            "contract_info": self.contract_info,
            "contacts": [c.to_dict(decrypted=decrypted) for c in self.contacts],
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# =============================================================================
# Case Model
# =============================================================================


class Case(db.Model):
    """
    Case model representing an investigation.

    Includes workflow status, priority, and assignment tracking.
    """

    __tablename__ = "cases"
    __table_args__ = (
        db.UniqueConstraint("tenant_id", "case_number", name="uq_tenant_case_number"),
    )

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = db.Column(
        db.String(36), db.ForeignKey("tenants.id"), nullable=False, index=True
    )
    case_number = db.Column(db.String(50), nullable=False, index=True)
    client_id = db.Column(
        db.String(36), db.ForeignKey("clients.id"), nullable=False, index=True
    )
    title = db.Column(db.String(300), nullable=False, index=True)
    description = db.Column(db.Text)
    priority = db.Column(db.String(20), default=CasePriority.MEDIUM.value)
    status = db.Column(db.String(20), default=CaseStatus.OPEN.value)
    start_date = db.Column(db.Date, nullable=False)
    target_end_date = db.Column(db.Date)
    actual_end_date = db.Column(db.Date)
    closure_reason = db.Column(db.Text)  # Reason for closing
    created_by = db.Column(db.String(36), db.ForeignKey("users.id"), index=True)
    assigned_to = db.Column(db.String(36), db.ForeignKey("users.id"), index=True)
    lead_investigator_id = db.Column(
        db.String(36), db.ForeignKey("users.id"), index=True
    )

    # Case metadata
    case_type = db.Column(
        db.String(100)
    )  # e.g., "fraud", "due_diligence", "asset_tracing"
    jurisdiction = db.Column(db.String(100))
    tags = db.Column(SafeJSON)  # Flexible tagging

    # Reopening
    reopened_reason = db.Column(db.Text)  # Reason for reopening
    reopened_at = db.Column(db.DateTime)
    reopened_by = db.Column(db.String(36), db.ForeignKey("users.id"), index=True)

    # Case hierarchy
    parent_case_id = db.Column(
        db.String(36), db.ForeignKey("cases.id"), nullable=True, index=True
    )

    # Soft delete
    is_deleted = db.Column(db.Boolean, default=False, index=True)
    deleted_at = db.Column(db.DateTime)

    # Archive (soft hide, distinct from delete)
    archived_at = db.Column(db.DateTime, nullable=True, index=True)

    # Proces verbaal (process report — markdown)
    pv_body = db.Column(db.Text)
    pv_updated_at = db.Column(db.DateTime)

    # Timestamps
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    subjects = db.relationship(
        "Subject",
        secondary=case_subjects,
        backref=db.backref("cases", lazy="dynamic"),
        lazy="dynamic",
    )
    findings = db.relationship(
        "Finding", backref="case", lazy="dynamic", cascade="all, delete-orphan"
    )
    financial_records = db.relationship(
        "FinancialRecord", backref="case", lazy="dynamic"
    )
    invoices = db.relationship("Invoice", backref="case", lazy="dynamic")
    child_cases = db.relationship(
        "Case", backref=db.backref("parent_case", remote_side=[id]), lazy="dynamic"
    )
    reminders = db.relationship(
        "Reminder", backref="case", lazy="dynamic", foreign_keys="Reminder.case_id"
    )
    lead_investigator = db.relationship(
        "User", foreign_keys=[lead_investigator_id], backref="led_cases"
    )

    @staticmethod
    def generate_case_number(tenant_id: str | None = None) -> str:
        """Generate unique case number: YYYY-XXXXX format, sequential per tenant."""
        year = datetime.now().year
        q = Case.query.filter(Case.case_number.like(f"{year}-%"))
        if tenant_id:
            q = q.filter(Case.tenant_id == tenant_id)
        last_case = q.order_by(Case.created_at.desc()).first()

        if last_case:
            try:
                last_num = int(last_case.case_number.split("-")[1])
                next_num = last_num + 1
            except Exception:
                next_num = 1
        else:
            next_num = 1

        return f"{year}-{next_num:05d}"

    def transition_status(self, new_status: str, user_id: str) -> bool:
        valid_transitions = {
            CaseStatus.OPEN.value: [CaseStatus.ACTIVE.value, CaseStatus.CLOSED.value],
            CaseStatus.ACTIVE.value: [
                CaseStatus.SUSPENDED.value,
                CaseStatus.CLOSED.value,
            ],
            CaseStatus.SUSPENDED.value: [
                CaseStatus.ACTIVE.value,
                CaseStatus.CLOSED.value,
            ],
            CaseStatus.CLOSED.value: [
                CaseStatus.ARCHIVED.value,
                CaseStatus.ACTIVE.value,
            ],
            CaseStatus.ARCHIVED.value: [CaseStatus.ACTIVE.value],
        }

        if new_status in valid_transitions.get(self.status, []):
            self.status = new_status
            if new_status == CaseStatus.CLOSED.value:
                self.actual_end_date = datetime.now(timezone.utc).date()
            elif new_status == CaseStatus.ACTIVE.value and self.actual_end_date:
                self.actual_end_date = None  # Clear end date when reopening
            return True
        return False

    def soft_delete(self) -> None:
        """Soft delete the case."""
        self.is_deleted = True

    def soft_archive(self) -> None:
        """Archive the case (soft hide, cascades to actions and findings)."""
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        self.archived_at = now
        for action in self.research_actions:
            if not action.archived_at:
                action.archived_at = now
        for finding in self.findings:
            if not finding.archived_at:
                finding.archived_at = now

    def restore_from_archive(self) -> None:
        """Restore archived case and all its archived children."""
        self.archived_at = None
        for action in self.research_actions:
            action.archived_at = None
        for finding in self.findings:
            finding.archived_at = None

    def to_dict(self, include_relations: bool = True) -> dict:
        """Serialize case data."""
        result = {
            "id": self.id,
            "case_number": self.case_number,
            "client_id": self.client_id,
            "title": self.title,
            "description": self.description,
            "priority": self.priority,
            "status": self.status,
            "start_date": self.start_date.isoformat() if self.start_date else None,
            "target_end_date": self.target_end_date.isoformat()
            if self.target_end_date
            else None,
            "actual_end_date": self.actual_end_date.isoformat()
            if self.actual_end_date
            else None,
            "closure_reason": self.closure_reason,
            "reopened_reason": self.reopened_reason,
            "reopened_at": self.reopened_at.isoformat() if self.reopened_at else None,
            "case_type": self.case_type,
            "jurisdiction": self.jurisdiction,
            "tags": self.tags,
            "archived_at": self.archived_at.isoformat() if self.archived_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

        if include_relations:
            result["subjects"] = [s.to_dict() for s in self.subjects]
            result["findings_count"] = self.findings.count()
            result["assigned_investigators"] = [
                {"id": u.id, "name": u.full_name} for u in self.investigators
            ]
            result["parent_case"] = (
                {
                    "id": self.parent_case.id,
                    "case_number": self.parent_case.case_number,
                    "title": self.parent_case.title,
                }
                if self.parent_case
                else None
            )
            result["child_cases"] = [
                {
                    "id": c.id,
                    "case_number": c.case_number,
                    "title": c.title,
                    "status": c.status,
                }
                for c in self.child_cases.filter_by(is_deleted=False)
            ]

        return result


# =============================================================================
# Subject Model
# =============================================================================


class Subject(db.Model):
    """
    Subject model representing a person, entity, or asset under investigation.

    Identifying features are encrypted for privacy.
    """

    __tablename__ = "subjects"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = db.Column(
        db.String(36), db.ForeignKey("tenants.id"), nullable=False, index=True
    )
    name = db.Column(db.String(300), nullable=False, index=True)
    subject_type = db.Column(db.String(20), nullable=False, index=True)

    # Encrypted identifying information
    date_of_birth = db.Column(db.String(500))  # Encrypted (DOB can be sensitive)
    place_of_birth = db.Column(db.String(500))  # Encrypted
    nationality = db.Column(db.String(500))  # Encrypted
    identification_number = db.Column(db.String(500))  # Encrypted (BSN, passport, etc.)
    address = db.Column(db.String(500))  # Encrypted
    street = db.Column(db.String(500))  # Encrypted
    house_number = db.Column(db.String(500))  # Encrypted
    house_number_addition = db.Column(db.String(500))  # Encrypted
    postal_code = db.Column(db.String(500))  # Encrypted
    city = db.Column(db.String(500))  # Encrypted
    phone = db.Column(db.String(500))  # Encrypted
    email = db.Column(db.String(500))  # Encrypted
    bank_account = db.Column(db.String(500))  # Encrypted

    # Additional metadata
    risk_score = db.Column(db.Integer, default=0)  # 0-100 risk assessment
    risk_factors = db.Column(SafeJSON)  # List of risk indicators
    notes = db.Column(db.Text)

    # Entity-specific fields
    registration_number = db.Column(db.String(100))  # KVK, Chamber of Commerce, etc.
    legal_form = db.Column(db.String(100))  # BV, NV, Stichting, etc.

    # Asset-specific fields
    asset_type = db.Column(db.String(50))
    estimated_value = db.Column(db.Numeric(15, 2))
    currency = db.Column(db.String(3), default="EUR")

    # Social media identifiers (extracted from profiles)
    # Structure: {"facebook": {"id": "123456", "username": "johndoe"}, "vk": {...}, etc.}
    social_media_ids = db.Column(SafeJSON)
    workflow_social_accounts = db.Column(
        SafeJSON
    )  # Simple list from workflow: ["@user", "@user2"]

    # Vehicle-specific fields (encrypted)
    license_plate = db.Column(db.String(500))  # Encrypted
    vin = db.Column(db.String(500))  # Encrypted (Vehicle Identification Number)
    insurance_company = db.Column(db.String(500))  # Encrypted
    brand = db.Column(db.String(100))
    vehicle_type = db.Column(db.String(50))  # sedan, suv, truck, etc.

    # Vessel-specific fields (encrypted)
    imo_number = db.Column(db.String(500))  # Encrypted - IMO ship number
    mmsi = db.Column(db.String(500))  # Encrypted - MMSI number
    eni_number = db.Column(db.String(500))  # Encrypted - ENI inland vessel number
    vessel_nationality = db.Column(db.String(500))  # Encrypted - flag state

    # Vessel data (full lookup result as JSON)
    vessel_data = db.Column(SafeJSON)

    # RDW vehicle data (full RDW record as JSON)
    rdw_data = db.Column(SafeJSON)

    # Photo
    photo_path = db.Column(db.String(500))  # Path to uploaded photo

    # Face recognition encoding (stored as JSON array of 128 floats from face-api.js)
    face_encoding = db.Column(SafeJSON)

    # Soft delete
    is_deleted = db.Column(db.Boolean, default=False, index=True)
    deleted_at = db.Column(db.DateTime)

    created_by = db.Column(db.String(36), db.ForeignKey("users.id"), index=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    creator = db.relationship("User", foreign_keys=[created_by])
    financial_records = db.relationship(
        "FinancialRecord", backref="subject", lazy="dynamic"
    )
    findings = db.relationship("Finding", backref="subject", lazy="dynamic")
    addresses = db.relationship(
        "Address",
        backref="subject",
        lazy="dynamic",
        foreign_keys="Address.subject_id",
        order_by="Address.is_primary.desc(), Address.created_at",
    )
    contacts = db.relationship(
        "Contact",
        backref="subject",
        lazy="dynamic",
        foreign_keys="Contact.subject_id",
        order_by="Contact.is_primary.desc(), Contact.created_at",
    )
    social_accounts = db.relationship(
        "SocialAccount",
        backref="subject",
        lazy="dynamic",
        foreign_keys="SocialAccount.subject_id",
        order_by="SocialAccount.platform, SocialAccount.username",
    )

    # Relations with other subjects
    related_subjects = db.relationship(
        "Subject",
        secondary=subject_relations,
        primaryjoin=id == subject_relations.c.subject_id,
        secondaryjoin=id == subject_relations.c.related_subject_id,
        backref="related_to",
    )

    ENCRYPTED_FIELDS = [
        "date_of_birth",
        "place_of_birth",
        "nationality",
        "identification_number",
        "address",
        "street",
        "house_number",
        "house_number_addition",
        "postal_code",
        "city",
        "phone",
        "email",
        "bank_account",
        "license_plate",
        "vin",
        "insurance_company",
        "imo_number",
        "mmsi",
        "eni_number",
        "vessel_nationality",
    ]

    def encrypt_identifiers(self) -> None:
        for field in self.ENCRYPTED_FIELDS:
            value = getattr(self, field)
            if value:
                setattr(self, field, encryptor.encrypt(value))

    def decrypt_identifiers(self) -> None:
        for field in self.ENCRYPTED_FIELDS:
            value = getattr(self, field)
            if value:
                try:
                    setattr(self, field, encryptor.decrypt(value))
                except Exception:
                    logger.warning(
                        "Decrypt failed for %s.%s (id=%s)",
                        self.__class__.__name__,
                        field,
                        getattr(self, "id", "?"),
                    )

    def soft_delete(self) -> None:
        """Soft delete the subject."""
        self.is_deleted = True
        self.deleted_at = datetime.now(timezone.utc)

    def to_dict(self, decrypted: bool = True, include_relations: bool = False) -> dict:
        """Serialize subject data."""
        if decrypted:
            self.decrypt_identifiers()

        result = {
            "id": self.id,
            "name": self.name,
            "subject_type": self.subject_type,
            "date_of_birth": self.date_of_birth,
            "place_of_birth": self.place_of_birth,
            "nationality": self.nationality,
            "identification_number": self.identification_number,
            "address": self.address,
            "phone": self.phone,
            "email": self.email,
            "bank_account": self.bank_account,
            "risk_score": self.risk_score,
            "risk_factors": self.risk_factors,
            "notes": self.notes,
            "registration_number": self.registration_number,
            "legal_form": self.legal_form,
            "asset_type": self.asset_type,
            "estimated_value": float(self.estimated_value)
            if self.estimated_value
            else None,
            "currency": self.currency,
            "license_plate": self.license_plate,
            "vin": self.vin,
            "insurance_company": self.insurance_company,
            "brand": self.brand,
            "vehicle_type": self.vehicle_type,
            "social_media_ids": self.social_media_ids or {},
            "rdw_data": self.rdw_data or {},
            "imo_number": self.imo_number,
            "mmsi": self.mmsi,
            "eni_number": self.eni_number,
            "vessel_nationality": self.vessel_nationality,
            "vessel_data": self.vessel_data or {},
            "addresses": [a.to_dict(decrypted=decrypted) for a in self.addresses],
            "contacts": [c.to_dict(decrypted=decrypted) for c in self.contacts],
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

        if include_relations:
            result["related_subjects"] = [
                {"id": s.id, "name": s.name, "type": s.subject_type}
                for s in self.related_subjects
            ]
            result["financial_records_count"] = self.financial_records.count()
            result["findings_count"] = self.findings.count()

        return result


# =============================================================================
# Address Model
# =============================================================================


class Address(db.Model):
    """
    Structured address for a subject or client.

    Supports multiple addresses per entity (home, work, etc.)
    with Kadaster verification status.
    """

    __tablename__ = "addresses"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = db.Column(
        db.String(36), db.ForeignKey("tenants.id"), nullable=False, index=True
    )
    subject_id = db.Column(
        db.String(36), db.ForeignKey("subjects.id"), nullable=True, index=True
    )
    client_id = db.Column(
        db.String(36), db.ForeignKey("clients.id"), nullable=True, index=True
    )

    # Structured address fields (all encrypted)
    street = db.Column(db.String(500))  # Encrypted
    number = db.Column(db.String(500))  # Encrypted (huisnummer + toevoeging)
    zipcode = db.Column(db.String(500))  # Encrypted
    town = db.Column(db.String(500))  # Encrypted
    country = db.Column(db.String(500))  # Encrypted

    is_primary = db.Column(db.Boolean, default=False)

    # Kadaster verification
    kadaster_verified = db.Column(db.Boolean, default=False)
    kadaster_data = db.Column(SafeJSON)  # Full BAG response
    kadaster_checked_at = db.Column(db.DateTime)

    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    ENCRYPTED_FIELDS = ["street", "number", "zipcode", "town", "country"]

    def encrypt_fields(self) -> None:
        for field in self.ENCRYPTED_FIELDS:
            value = getattr(self, field)
            if value:
                setattr(self, field, encryptor.encrypt(value))

    def decrypt_fields(self) -> None:
        for field in self.ENCRYPTED_FIELDS:
            value = getattr(self, field)
            if value:
                try:
                    setattr(self, field, encryptor.decrypt(value))
                except Exception:
                    logger.warning(
                        "Decrypt failed for %s.%s (id=%s)",
                        self.__class__.__name__,
                        field,
                        getattr(self, "id", "?"),
                    )

    def to_dict(self, decrypted=True) -> dict:
        if decrypted:
            self.decrypt_fields()
        return {
            "id": self.id,
            "subject_id": self.subject_id,
            "client_id": self.client_id,
            "street": self.street,
            "number": self.number,
            "zipcode": self.zipcode,
            "town": self.town,
            "country": self.country,
            "is_primary": self.is_primary,
            "kadaster_verified": self.kadaster_verified,
            "kadaster_data": self.kadaster_data,
            "kadaster_checked_at": self.kadaster_checked_at.isoformat()
            if self.kadaster_checked_at
            else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def full_address(self) -> str:
        parts = []
        if self.street:
            line = self.street
            if self.number:
                line += f" {self.number}"
            parts.append(line)
        if self.zipcode:
            parts.append(self.zipcode)
        if self.town:
            parts.append(self.town)
        if self.country:
            parts.append(self.country)
        return ", ".join(parts)


# =============================================================================
# Contact Model (Email/Phone)
# =============================================================================


class Contact(db.Model):
    """
    Structured contact entry for subjects and clients.

    Supports multiple emails and phones per entity (home, work, etc.)
    with verification/check status.
    """

    __tablename__ = "contacts"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = db.Column(
        db.String(36), db.ForeignKey("tenants.id"), nullable=False, index=True
    )
    subject_id = db.Column(
        db.String(36), db.ForeignKey("subjects.id"), nullable=True, index=True
    )
    client_id = db.Column(
        db.String(36), db.ForeignKey("clients.id"), nullable=True, index=True
    )

    contact_type = db.Column(db.String(10), nullable=False)  # 'email' or 'phone'
    value = db.Column(db.String(500))  # Encrypted
    is_primary = db.Column(db.Boolean, default=False)

    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    ENCRYPTED_FIELDS = ["value"]

    def encrypt_fields(self) -> None:
        for field in self.ENCRYPTED_FIELDS:
            value = getattr(self, field)
            if value:
                setattr(self, field, encryptor.encrypt(value))

    def decrypt_fields(self) -> None:
        for field in self.ENCRYPTED_FIELDS:
            value = getattr(self, field)
            if value:
                try:
                    setattr(self, field, encryptor.decrypt(value))
                except Exception:
                    logger.warning(
                        "Decrypt failed for %s.%s (id=%s)",
                        self.__class__.__name__,
                        field,
                        getattr(self, "id", "?"),
                    )

    def to_dict(self, decrypted=True) -> dict:
        if decrypted:
            self.decrypt_fields()
        return {
            "id": self.id,
            "subject_id": self.subject_id,
            "client_id": self.client_id,
            "contact_type": self.contact_type,
            "value": self.value,
            "is_primary": self.is_primary,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# =============================================================================
# Financial Record Model
# =============================================================================


class FinancialRecord(db.Model):
    """
    Financial transaction record for tracking money flows.

    All amounts and counterparty info encrypted for privacy.
    """

    __tablename__ = "financial_records"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = db.Column(
        db.String(36), db.ForeignKey("tenants.id"), nullable=False, index=True
    )
    case_id = db.Column(
        db.String(36), db.ForeignKey("cases.id"), nullable=False, index=True
    )
    subject_id = db.Column(db.String(36), db.ForeignKey("subjects.id"), index=True)

    transaction_date = db.Column(db.Date, nullable=False)
    amount = db.Column(db.Numeric(15, 2), nullable=False)
    currency = db.Column(db.String(3), default="EUR")

    # Encrypted counterparty details
    counterparty_name = db.Column(db.String(500))  # Encrypted
    counterparty_account = db.Column(db.String(500))  # Encrypted
    counterparty_bank = db.Column(db.String(500))  # Encrypted
    counterparty_country = db.Column(db.String(500))  # Encrypted

    transaction_type = db.Column(db.String(50))  # transfer, cash, crypto, etc.
    source = db.Column(db.String(100))  # bank_statement, invoice, etc.
    source_reference = db.Column(db.String(100))  # Reference number in source doc
    description = db.Column(db.Text)

    verification_status = db.Column(
        db.String(20), default=VerificationStatus.PENDING.value
    )
    verified_by = db.Column(db.String(36), db.ForeignKey("users.id"), index=True)
    verified_at = db.Column(db.DateTime)
    verification_notes = db.Column(db.Text)

    is_deleted = db.Column(db.Boolean, default=False, index=True)
    deleted_at = db.Column(db.DateTime)

    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    attachments = db.relationship(
        "Document", backref="financial_record", lazy="dynamic"
    )

    ENCRYPTED_FIELDS = [
        "counterparty_name",
        "counterparty_account",
        "counterparty_bank",
        "counterparty_country",
    ]

    def encrypt_details(self) -> None:
        """Encrypt counterparty information."""
        for field in self.ENCRYPTED_FIELDS:
            value = getattr(self, field)
            if value:
                setattr(self, field, encryptor.encrypt(value))

    def decrypt_details(self) -> None:
        """Decrypt counterparty information for display."""
        for field in self.ENCRYPTED_FIELDS:
            value = getattr(self, field)
            if value:
                try:
                    setattr(self, field, encryptor.decrypt(value))
                except Exception:
                    logger.warning(
                        "Decrypt failed for %s.%s (id=%s)",
                        self.__class__.__name__,
                        field,
                        getattr(self, "id", "?"),
                    )

    def verify(self, user_id: str, notes: str = None) -> None:
        """Mark record as verified."""
        self.verification_status = VerificationStatus.VERIFIED.value
        self.verified_by = user_id
        self.verified_at = datetime.now(timezone.utc)
        if notes:
            self.verification_notes = notes

    def flag(self, user_id: str, notes: str = None) -> None:
        """Flag record for review."""
        self.verification_status = VerificationStatus.FLAGGED.value
        self.verified_by = user_id
        self.verified_at = datetime.now(timezone.utc)
        if notes:
            self.verification_notes = notes

    def soft_delete(self) -> None:
        """Soft delete the record."""
        self.is_deleted = True
        self.deleted_at = datetime.now(timezone.utc)

    def to_dict(self, decrypted: bool = True) -> dict:
        """Serialize financial record."""
        if decrypted:
            self.decrypt_details()

        return {
            "id": self.id,
            "case_id": self.case_id,
            "subject_id": self.subject_id,
            "transaction_date": self.transaction_date.isoformat()
            if self.transaction_date
            else None,
            "amount": float(self.amount) if self.amount else 0,
            "currency": self.currency,
            "counterparty_name": self.counterparty_name,
            "counterparty_account": self.counterparty_account,
            "counterparty_bank": self.counterparty_bank,
            "counterparty_country": self.counterparty_country,
            "transaction_type": self.transaction_type,
            "source": self.source,
            "source_reference": self.source_reference,
            "description": self.description,
            "verification_status": self.verification_status,
            "verified_by": self.verified_by,
            "verified_at": self.verified_at.isoformat() if self.verified_at else None,
            "verification_notes": self.verification_notes,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# =============================================================================
# Finding Model
# =============================================================================


class Finding(db.Model):
    """
    Investigation finding linked to a case and/or subject.
    """

    __tablename__ = "findings"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = db.Column(
        db.String(36), db.ForeignKey("tenants.id"), nullable=False, index=True
    )
    case_id = db.Column(
        db.String(36), db.ForeignKey("cases.id"), nullable=False, index=True
    )
    subject_id = db.Column(db.String(36), db.ForeignKey("subjects.id"), index=True)

    title = db.Column(db.String(300), nullable=False)
    content = db.Column(db.Text, nullable=False)
    detail = db.Column(db.Text)  # Optional extended detail from workflow findings
    source_url = db.Column(db.String(500))
    source_type = db.Column(db.String(50))  # osint, interview, document, etc.

    # Reliability scoring (1-10)
    reliability_score = db.Column(db.Integer, default=5)
    confidence_level = db.Column(db.String(20))  # low, medium, high, verified

    finding_type = db.Column(
        db.String(50), index=True
    )  # identity, location, connection, financial, etc.
    tags = db.Column(SafeJSON)

    # Workflow/OSINT-specific columns (merged from WorkflowFinding)
    icon = db.Column(db.String(10), default="📄")
    verified = db.Column(db.Boolean, default=False)
    comment = db.Column(db.Text)
    raw_data = db.Column(SafeJSON)
    archived_at = db.Column(db.DateTime, nullable=True, index=True)

    finding_screenshots = db.relationship(
        "FindingScreenshot",
        back_populates="finding",
        lazy="select",
        cascade="all, delete-orphan",
    )

    created_by = db.Column(
        db.String(36), db.ForeignKey("users.id"), nullable=False, index=True
    )
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    is_deleted = db.Column(db.Boolean, default=False, index=True)
    deleted_at = db.Column(db.DateTime)

    def soft_delete(self) -> None:
        """Soft delete the finding."""
        self.is_deleted = True
        self.deleted_at = datetime.now(timezone.utc)

    def to_dict(self) -> dict:
        """Serialize finding."""
        return {
            "id": self.id,
            "case_id": self.case_id,
            "subject_id": self.subject_id,
            "title": self.title,
            "content": self.content,
            "source_url": self.source_url,
            "source_type": self.source_type,
            "reliability_score": self.reliability_score,
            "confidence_level": self.confidence_level,
            "finding_type": self.finding_type,
            "tags": self.tags,
            "created_by": self.created_by,
            "author_name": self.author.full_name if self.author else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


# =============================================================================
# Screenshot Model
# =============================================================================


class Screenshot(db.Model):
    """
    Screenshots captured from URLs for case documentation.
    """

    __tablename__ = "screenshots"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = db.Column(
        db.String(36), db.ForeignKey("tenants.id"), nullable=False, index=True
    )
    case_id = db.Column(
        db.String(36), db.ForeignKey("cases.id"), nullable=False, index=True
    )

    url = db.Column(db.String(500), nullable=False)  # Source URL
    filename = db.Column(db.String(255), nullable=False)  # Stored filename
    original_filename = db.Column(db.String(255))  # Original name if provided
    title = db.Column(db.String(300))  # Optional title

    # Dimensions
    width = db.Column(db.Integer)
    height = db.Column(db.Integer)

    # File info
    file_size = db.Column(db.Integer)  # Size in bytes

    # Optional extracted data (e.g., social media IDs)
    extracted_data = db.Column(SafeJSON)

    created_by = db.Column(
        db.String(36), db.ForeignKey("users.id"), nullable=False, index=True
    )
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    # Relationships
    case = db.relationship("Case", backref="screenshots")
    creator = db.relationship("User", foreign_keys=[created_by])

    def to_dict(self) -> dict:
        """Serialize screenshot."""
        return {
            "id": self.id,
            "case_id": self.case_id,
            "url": self.url,
            "filename": self.filename,
            "original_filename": self.original_filename,
            "title": self.title,
            "width": self.width,
            "height": self.height,
            "file_size": self.file_size,
            "extracted_data": self.extracted_data,
            "created_by": self.created_by,
            "creator_name": self.creator.full_name if self.creator else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "thumbnail_url": f"/cms/cases/{self.case_id}/screenshots/{self.id}/thumbnail",
            "full_url": f"/cms/cases/{self.case_id}/screenshots/{self.id}/view",
        }


# =============================================================================
# ResearchAction — OSINT research action linked to a case
# =============================================================================


class ResearchAction(db.Model):
    __tablename__ = "research_actions"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = db.Column(
        db.String(36), db.ForeignKey("tenants.id"), nullable=False, index=True
    )
    case_id = db.Column(
        db.String(36), db.ForeignKey("cases.id"), nullable=False, index=True
    )
    action_type = db.Column(db.String(50), nullable=False)
    data_value = db.Column(db.Text)
    label = db.Column(db.String(200))
    status = db.Column(db.String(20), default="pending")
    started_at = db.Column(db.DateTime)
    completed_at = db.Column(db.DateTime)
    error = db.Column(db.Text)
    result_summary = db.Column(db.Text)
    cancel_requested = db.Column(db.Boolean, default=False)
    archived_at = db.Column(db.DateTime, nullable=True, index=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, onupdate=lambda: datetime.now(timezone.utc))
    created_by = db.Column(
        db.String(36), db.ForeignKey("users.id"), nullable=True, index=True
    )

    case = db.relationship("Case", backref="research_actions")
    findings = db.relationship(
        "Finding", secondary="action_findings", backref="research_actions"
    )

    def to_dict(self):
        return {
            "id": self.id,
            "case_id": self.case_id,
            "action_type": self.action_type,
            "data_value": self.data_value,
            "label": self.label,
            "status": self.status,
            "error": self.error,
            "result_summary": self.result_summary,
            "archived_at": self.archived_at.isoformat() if self.archived_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat()
            if self.completed_at
            else None,
        }


class ActionFinding(db.Model):
    __tablename__ = "action_findings"

    action_id = db.Column(
        db.String(36), db.ForeignKey("research_actions.id"), primary_key=True
    )
    finding_id = db.Column(
        db.String(36), db.ForeignKey("findings.id"), primary_key=True
    )


class FindingScreenshot(db.Model):
    __tablename__ = "finding_screenshots"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = db.Column(
        db.String(36), db.ForeignKey("tenants.id"), nullable=False, index=True
    )
    finding_id = db.Column(
        db.String(36), db.ForeignKey("findings.id"), nullable=False, index=True
    )
    url = db.Column(db.String(500))
    source_url = db.Column(db.String(500))
    file_path = db.Column(db.String(500))
    captured_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    created_by = db.Column(
        db.String(36), db.ForeignKey("users.id"), nullable=True, index=True
    )

    finding = db.relationship(
        "Finding", back_populates="finding_screenshots", lazy="select"
    )


class ServiceRate(db.Model):
    __tablename__ = "service_rates"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = db.Column(
        db.String(36), db.ForeignKey("tenants.id"), nullable=False, index=True
    )
    service_type = db.Column(db.String(50), nullable=False, index=True)
    description = db.Column(db.String(300), nullable=False)
    unit_price = db.Column(db.Numeric(15, 2), nullable=False, default=0)
    vat_rate = db.Column(db.Numeric(5, 2), nullable=False, default=21.00)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        return {
            "id": self.id,
            "service_type": self.service_type,
            "description": self.description,
            "unit_price": float(self.unit_price) if self.unit_price else 0,
            "vat_rate": float(self.vat_rate) if self.vat_rate else 0,
            "is_active": self.is_active,
        }


# =============================================================================
# Audit Log Model
# =============================================================================


def _redact_pii(text: str | None) -> str | None:
    """Redact common PII patterns from audit log descriptions."""
    if not text:
        return text
    text = re.sub(r"[\w.+-]+@[\w-]+\.[\w.-]+", "[REDACTED EMAIL]", text)
    text = re.sub(r"\b\d{10,15}\b", "[REDACTED PHONE]", text)
    return text


class AuditLog(db.Model):
    """
    Immutable audit log for compliance and security monitoring.

    Records all significant actions for GDPR Article 30 compliance.
    """

    __tablename__ = "audit_logs"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = db.Column(
        db.String(36), db.ForeignKey("tenants.id"), nullable=False, index=True
    )
    user_id = db.Column(db.String(36), db.ForeignKey("users.id"), index=True)
    action = db.Column(db.String(20), nullable=False, index=True)

    entity_type = db.Column(
        db.String(50), nullable=False, index=True
    )  # case, client, subject, etc.
    entity_id = db.Column(db.String(128), index=True)

    changes_made = db.Column(SafeJSON)  # {"field": {"old": "x", "new": "y"}}
    old_values = db.Column(SafeJSON)  # Previous state snapshot
    new_values = db.Column(SafeJSON)  # New state snapshot

    ip_address = db.Column(db.String(45))  # IPv6 compatible
    user_agent = db.Column(db.String(500))
    session_id = db.Column(db.String(100))

    # Context
    case_id = db.Column(db.String(36))  # Related case for context
    description = db.Column(db.String(500))  # Human-readable description

    timestamp = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc), index=True
    )

    # No soft delete - audit logs are immutable and permanent
    # No updated_at - logs should not be modified

    @property
    def user_name(self) -> str:
        """Get the full name of the user who performed this action."""
        if self.user:
            return self.user.full_name
        return "System"

    @staticmethod
    def purge_old(days: int = None) -> int:
        """Delete audit logs older than `days`. Returns count deleted."""
        if days is None:
            days = int(Setting.get("audit_log_retention_days", "365"))
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        deleted = AuditLog.query.filter(AuditLog.timestamp < cutoff).delete()
        if deleted:
            db.session.commit()
        return deleted

    @staticmethod
    def log(
        user_id: str,
        action: str,
        entity_type: str,
        entity_id: str = None,
        changes: dict = None,
        old_values: dict = None,
        new_values: dict = None,
        ip_address: str | None = None,
        user_agent: str = None,
        case_id: str = None,
        description: str = None,
        tenant_id: str = None,
    ):
        """
        Create an audit log entry.

        This should be called after every significant action.
        """
        if tenant_id is None:
            try:
                from flask import g

                tenant_id = getattr(g, "tenant_id", None)
            except Exception:
                pass
        if description:
            description = _redact_pii(description)
        log_entry = AuditLog(
            tenant_id=tenant_id,
            user_id=user_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            changes_made=changes or new_values,
            old_values=old_values,
            new_values=new_values,
            ip_address=ip_address,
            user_agent=user_agent,
            case_id=case_id,
            description=description,
        )
        db.session.add(log_entry)
        return log_entry

    def to_dict(self) -> dict:
        """Serialize audit log."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "user_name": self.user.full_name if self.user else "System",
            "action": self.action,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "changes_made": self.changes_made,
            "ip_address": self.ip_address,
            "case_id": self.case_id,
            "description": self.description,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
        }


# =============================================================================
# Document Model (for attachments)
# =============================================================================


class Document(db.Model):
    """
    Document attachment for cases, subjects, and financial records.
    """

    __tablename__ = "documents"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = db.Column(
        db.String(36), db.ForeignKey("tenants.id"), nullable=False, index=True
    )

    # Linked entity
    case_id = db.Column(db.String(36), db.ForeignKey("cases.id"), index=True)
    subject_id = db.Column(db.String(36), db.ForeignKey("subjects.id"), index=True)
    financial_record_id = db.Column(
        db.String(36), db.ForeignKey("financial_records.id"), index=True
    )

    filename = db.Column(db.String(255), nullable=False)
    original_filename = db.Column(db.String(255), nullable=False)
    mime_type = db.Column(db.String(100))
    file_size = db.Column(db.Integer)  # bytes

    # File storage reference (path, S3 key, etc.)
    storage_path = db.Column(db.String(500))
    storage_type = db.Column(db.String(20), default="local")  # local, s3, azure

    # Document metadata
    document_type = db.Column(db.String(50))  # evidence, contract, report, etc.
    description = db.Column(db.Text)
    tags = db.Column(SafeJSON)

    # Security classification
    classification = db.Column(
        db.String(20), default="confidential"
    )  # public, internal, confidential, restricted

    uploaded_by = db.Column(db.String(36), db.ForeignKey("users.id"), index=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    is_deleted = db.Column(db.Boolean, default=False, index=True)
    deleted_at = db.Column(db.DateTime)

    def soft_delete(self) -> None:
        """Soft delete the document."""
        self.is_deleted = True
        self.deleted_at = datetime.now(timezone.utc)

    def to_dict(self) -> dict:
        """Serialize document metadata."""
        return {
            "id": self.id,
            "case_id": self.case_id,
            "subject_id": self.subject_id,
            "financial_record_id": self.financial_record_id,
            "filename": self.filename,
            "original_filename": self.original_filename,
            "mime_type": self.mime_type,
            "file_size": self.file_size,
            "document_type": self.document_type,
            "description": self.description,
            "classification": self.classification,
            "uploaded_by": self.uploaded_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# =============================================================================
# Comment Model
# =============================================================================


class DocumentTemplate(db.Model):
    """
    Document template for generating investigation reports.

    Templates use placeholders like {{case_number}}, {{client_name}}, etc.
    """

    __tablename__ = "document_templates"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = db.Column(
        db.String(36), db.ForeignKey("tenants.id"), nullable=False, index=True
    )

    # Template info
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    template_type = db.Column(
        db.String(50), default="report"
    )  # report, summary, letter, memo

    # Template content (uses placeholders)
    content = db.Column(db.Text, nullable=False)

    # Categories for organization
    category = db.Column(db.String(50))  # investigation, compliance, financial, general

    # Metadata
    is_default = db.Column(db.Boolean, default=False)
    is_active = db.Column(db.Boolean, default=True)

    # Ownership
    created_by = db.Column(db.String(36), db.ForeignKey("users.id"), index=True)
    creator = db.relationship("User", backref="document_templates")

    # Timestamps
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    def to_dict(self) -> dict:
        """Serialize template."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "template_type": self.template_type,
            "content": self.content,
            "category": self.category,
            "is_default": self.is_default,
            "is_active": self.is_active,
            "created_by": self.created_by,
            "creator_name": self.creator.full_name if self.creator else "Unknown",
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def render(self, context: dict) -> str:
        """
        Render template with provided context.

        Context can include: case, client, subjects, findings, financials, custom fields
        """
        from jinja2 import BaseLoader
        from jinja2.sandbox import SandboxedEnvironment
        import logging
        from datetime import datetime, timezone

        env = SandboxedEnvironment(loader=BaseLoader())

        env.filters["default"] = lambda v, d: v if v else d
        env.filters["date"] = lambda v, fmt="%Y-%m-%d": (
            v.strftime(fmt) if isinstance(v, datetime) else str(v)
        )
        env.filters["currency"] = lambda v: (
            f"€{v:,.2f}" if isinstance(v, (int, float)) else str(v)
        )
        env.globals["now"] = datetime.now(timezone.utc)

        try:
            template = env.from_string(self.content)
            return template.render(**context)
        except Exception as e:
            logging.error(f"Template render error: {e}")
            return self.content  # Return raw content on error


# =============================================================================
# Reminder Model
# =============================================================================


class ReminderType(PyEnum):
    """Types of reminders."""

    MANUAL = "manual"
    RECURRING = "recurring"
    SYSTEM = "system"


class ReminderRecurrence(PyEnum):
    """Recurrence patterns."""

    NONE = "none"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"


class Reminder(db.Model):
    """
    Reminder model for task/alert management.

    Supports manual reminders and system-generated alerts.
    Can be linked to cases, subjects, or be standalone.
    """

    __tablename__ = "reminders"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = db.Column(
        db.String(36), db.ForeignKey("tenants.id"), nullable=False, index=True
    )

    # Content
    title = db.Column(db.String(300), nullable=False)
    description = db.Column(db.Text)

    # Timing
    reminder_date = db.Column(db.DateTime, nullable=False, index=True)
    due_date = db.Column(db.DateTime)
    completed_at = db.Column(db.DateTime)

    # Type and recurrence
    reminder_type = db.Column(db.String(20), default=ReminderType.MANUAL.value)
    recurrence = db.Column(db.String(20), default=ReminderRecurrence.NONE.value)

    # Priority
    priority = db.Column(db.String(20), default="medium")  # low, medium, high, critical

    # Links (at least one should be set)
    case_id = db.Column(db.String(36), db.ForeignKey("cases.id"), index=True)
    subject_id = db.Column(db.String(36), db.ForeignKey("subjects.id"), index=True)
    client_id = db.Column(db.String(36), db.ForeignKey("clients.id"), index=True)

    # Assignment
    assigned_to = db.Column(db.String(36), db.ForeignKey("users.id"), index=True)
    assigned_user = db.relationship(
        "User", foreign_keys=[assigned_to], backref="assigned_reminders"
    )

    # Ownership
    created_by = db.Column(
        db.String(36), db.ForeignKey("users.id"), nullable=False, index=True
    )
    creator = db.relationship(
        "User", foreign_keys=[created_by], backref="created_reminders"
    )

    # Status
    is_completed = db.Column(db.Boolean, default=False, index=True)
    is_overdue = db.Column(db.Boolean, default=False, index=True)
    is_dismissed = db.Column(db.Boolean, default=False)

    # Notification settings
    notify_email = db.Column(db.Boolean, default=False)
    notify_dashboard = db.Column(db.Boolean, default=True)

    # Timestamps
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Soft delete
    is_deleted = db.Column(db.Boolean, default=False, index=True)
    deleted_at = db.Column(db.DateTime)

    def complete(self) -> None:
        """Mark reminder as completed."""
        self.is_completed = True
        self.completed_at = datetime.now(timezone.utc)

    def snooze(self, minutes: int = 30) -> None:
        """Snooze reminder for specified minutes."""
        from datetime import timedelta

        self.reminder_date = datetime.now(timezone.utc) + timedelta(minutes=minutes)

    def check_overdue(self) -> bool:
        """Check and update overdue status."""
        if not self.is_completed and self.reminder_date < datetime.now(timezone.utc):
            self.is_overdue = True
        return self.is_overdue

    def soft_delete(self) -> None:
        """Soft delete the reminder."""
        self.is_deleted = True
        self.deleted_at = datetime.now(timezone.utc)

    def to_dict(self) -> dict:
        """Serialize reminder."""
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "reminder_date": self.reminder_date.isoformat()
            if self.reminder_date
            else None,
            "due_date": self.due_date.isoformat() if self.due_date else None,
            "completed_at": self.completed_at.isoformat()
            if self.completed_at
            else None,
            "reminder_type": self.reminder_type,
            "recurrence": self.recurrence,
            "priority": self.priority,
            "case_id": self.case_id,
            "subject_id": self.subject_id,
            "client_id": self.client_id,
            "assigned_to": self.assigned_to,
            "assigned_user_name": self.assigned_user.full_name
            if self.assigned_user
            else None,
            "created_by": self.created_by,
            "creator_name": self.creator.full_name if self.creator else None,
            "is_completed": self.is_completed,
            "is_overdue": self.is_overdue,
            "is_dismissed": self.is_dismissed,
            "notify_email": self.notify_email,
            "notify_dashboard": self.notify_dashboard,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


# =============================================================================
# Settings Model
# =============================================================================


class SocialAccount(db.Model):
    __tablename__ = "social_accounts"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = db.Column(
        db.String(36), db.ForeignKey("tenants.id"), nullable=False, index=True
    )
    subject_id = db.Column(
        db.String(36), db.ForeignKey("subjects.id"), nullable=True, index=True
    )

    platform = db.Column(db.String(50), nullable=False, index=True)
    username = db.Column(db.String(200), nullable=False, index=True)
    url = db.Column(db.String(500))
    account_id = db.Column(db.String(200))
    finding_id = db.Column(
        db.String(36), db.ForeignKey("findings.id"), nullable=True, index=True
    )

    finding = db.relationship("Finding", foreign_keys=[finding_id])

    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    def __str__(self) -> str:
        return f"{self.platform}: {self.username}"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "subject_id": self.subject_id,
            "platform": self.platform,
            "username": self.username,
            "url": self.url,
            "account_id": self.account_id,
            "finding_id": self.finding_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


def get_setting(key: str, default=None) -> Any:
    setting = Setting.query.filter_by(key=key, is_active=True).first()
    if not setting:
        return default
    return setting.value


def set_setting(
    key: str,
    value: str,
    category: str = "general",
    description: str = None,
    value_type: str = "text",
    is_sensitive: bool = False,
) -> object:
    setting = Setting.query.filter_by(key=key).first()
    if setting:
        setting.value = value
        setting.updated_at = datetime.now(timezone.utc)
    else:
        setting = Setting(
            key=key,
            value=value,
            category=category,
            description=description,
            value_type=value_type,
            is_sensitive=is_sensitive,
            is_encrypted=is_sensitive,
        )
        db.session.add(setting)
    db.session.commit()
    return setting


def init_default_settings() -> None:
    defaults = [
        {
            "key": "brave_api_key",
            "category": "api_keys",
            "description": "Brave Search API → web searches via Brave. 📍 https://api.search.brave.com/app/dashboard (gratis: 2.000 queries/maand)",
            "value_type": "password",
            "is_sensitive": True,
            "display_order": 1,
        },
        {
            "key": "pimeyes_api_key",
            "category": "api_keys",
            "description": "PimEyes API → gezichtsherkenning op internet. 📍 https://pimeyes.com/en/api",
            "value_type": "password",
            "is_sensitive": True,
            "display_order": 2,
        },
        {
            "key": "tineye_api_key",
            "category": "api_keys",
            "description": "TinEye API → reverse image search. 📍 https://services.tineye.com/developers",
            "value_type": "password",
            "is_sensitive": True,
            "display_order": 3,
        },
        {
            "key": "spiderfoot_url",
            "category": "spiderfoot",
            "description": "SpiderFoot server URL",
            "value_type": "text",
            "display_order": 4,
        },
        {
            "key": "spiderfoot_username",
            "category": "spiderfoot",
            "description": "SpiderFoot login username",
            "value_type": "text",
            "display_order": 5,
        },
        {
            "key": "spiderfoot_password",
            "category": "spiderfoot",
            "description": "SpiderFoot login password",
            "value_type": "password",
            "is_sensitive": True,
            "display_order": 6,
        },
        {
            "key": "default_search_engine",
            "category": "search",
            "description": "Default search engine",
            "value_type": "select",
            "options": {
                "options": [
                    {"value": "brave", "label": "Brave Search"},
                    {"value": "ddg", "label": "DuckDuckGo"},
                ]
            },
            "display_order": 10,
        },
        {
            "key": "search_result_limit",
            "category": "search",
            "description": "Max search results",
            "value_type": "number",
            "display_order": 11,
        },
        {
            "key": "enable_osint_dorks",
            "category": "search",
            "description": "Enable OSINT dorks",
            "value_type": "boolean",
            "display_order": 12,
        },
        {
            "key": "case_number_prefix",
            "category": "general",
            "description": "Case number prefix",
            "value_type": "text",
            "display_order": 20,
        },
        {
            "key": "default_risk_score",
            "category": "general",
            "description": "Default risk score",
            "value_type": "number",
            "display_order": 21,
        },
        {
            "key": "organization_name",
            "category": "general",
            "description": "Organization name",
            "value_type": "text",
            "display_order": 22,
        },
        {
            "key": "session_timeout_minutes",
            "category": "security",
            "description": "Session timeout (minutes)",
            "value_type": "number",
            "display_order": 30,
        },
        {
            "key": "require_password_change",
            "category": "security",
            "description": "Password change (days)",
            "value_type": "number",
            "display_order": 31,
        },
        {
            "key": "smtp_server",
            "category": "email",
            "description": "SMTP server",
            "value_type": "text",
            "display_order": 40,
        },
        {
            "key": "smtp_port",
            "category": "email",
            "description": "SMTP port",
            "value_type": "number",
            "display_order": 41,
        },
        {
            "key": "smtp_username",
            "category": "email",
            "description": "SMTP username",
            "value_type": "text",
            "display_order": 42,
        },
        {
            "key": "smtp_password",
            "category": "email",
            "description": "SMTP password",
            "value_type": "password",
            "is_sensitive": True,
            "display_order": 43,
        },
        {
            "key": "smtp_from_email",
            "category": "email",
            "description": "From email",
            "value_type": "text",
            "display_order": 44,
        },
        {
            "key": "smtp_from_name",
            "category": "email",
            "description": "From name",
            "value_type": "text",
            "display_order": 45,
        },
        {
            "key": "update_check_repo",
            "category": "general",
            "description": "GitHub repo for update checks (e.g. user/repo). Leave empty to disable.",
            "value_type": "text",
            "display_order": 50,
        },
        {
            "key": "theme_style",
            "category": "appearance",
            "value": "classic",
            "description": "Visual style and layout",
            "value_type": "select",
            "options": {
                "options": [
                    {"value": "classic", "label": "Classic"},
                    {"value": "professional", "label": "Professional"},
                ]
            },
            "display_order": 1,
        },
        {
            "key": "app_logo",
            "category": "appearance",
            "value": "",
            "description": "Logo bestandsnaam in static/uploads/logo/",
            "value_type": "text",
            "display_order": 2,
        },
        {
            "key": "marineplan_api_key",
            "category": "api_keys",
            "description": "MarinePlan OpenShipData API Key (free: https://marineplan.com)",
            "value_type": "password",
            "is_sensitive": True,
            "display_order": 4,
        },
        {
            "key": "equasis_email",
            "category": "api_keys",
            "description": "Equasis login email (free: https://equasis.org)",
            "value_type": "text",
            "display_order": 5,
        },
        {
            "key": "equasis_password",
            "category": "api_keys",
            "description": "Equasis login password",
            "value_type": "password",
            "is_sensitive": True,
            "display_order": 6,
        },
        {
            "key": "overheid_api_key",
            "category": "api_keys",
            "description": "Overheid.io API Key (gratis: https://overheid.io)",
            "value_type": "password",
            "is_sensitive": True,
            "display_order": 7,
        },
        {
            "key": "twochat_api_key",
            "category": "api_keys",
            "description": "2Chat API Key (https://app.2chat.io/api)",
            "value_type": "password",
            "is_sensitive": True,
            "display_order": 8,
        },
        {
            "key": "twochat_whatsapp_number",
            "category": "api_keys",
            "description": "2Chat WhatsApp nummer (E164, bv +31612345678)",
            "value_type": "text",
            "display_order": 9,
        },
        {
            "key": "rapidapi_username_key",
            "category": "api_keys",
            "description": "RapidAPI Key voor Username Check. 📍 https://rapidapi.com/ (zoek naar 'osint-username-availability-brand-checker-api')",
            "value_type": "password",
            "is_sensitive": True,
            "display_order": 10,
        },
        {
            "key": "openrouter_api_key",
            "category": "api_keys",
            "description": "OpenRouter API Key (300+ models via unified API: https://openrouter.ai/keys)",
            "value_type": "password",
            "is_sensitive": True,
            "display_order": 11,
        },
        {
            "key": "openrouter_model",
            "category": "ai",
            "value": "openrouter/auto",
            "description": "OpenRouter model slug (e.g. openrouter/auto, deepseek/deepseek-v4-pro, anthropic/claude-opus-4-5-20251101)",
            "value_type": "text",
            "display_order": 1,
        },
        {
            "key": "openrouter_base_url",
            "category": "ai",
            "value": "https://openrouter.ai/api/v1",
            "description": "OpenRouter API base URL (change for self-hosted or alternative endpoints)",
            "value_type": "text",
            "display_order": 2,
        },
        {
            "key": "ollama_url",
            "category": "ai",
            "value": "http://localhost:11434/api/generate",
            "description": "Ollama server URL (fallback when OpenRouter is unavailable)",
            "value_type": "text",
            "display_order": 3,
        },
        {
            "key": "ollama_model",
            "category": "ai",
            "value": "llama3.2",
            "description": "Ollama model name (fallback when OpenRouter is unavailable)",
            "value_type": "text",
            "display_order": 4,
        },
        {
            "key": "audit_log_retention_days",
            "category": "security",
            "value": "365",
            "description": "Audit log retention (days, 0=keep forever)",
            "value_type": "number",
            "display_order": 32,
        },
        {
            "key": "phone_lookup_retention_days",
            "category": "security",
            "value": "90",
            "description": "Phone lookup retention (days, 0=keep forever)",
            "value_type": "number",
            "display_order": 33,
        },
        {
            "key": "webhook_url",
            "category": "general",
            "value": "",
            "description": "Webhook URL for system notifications (POST JSON). Leave empty to disable.",
            "value_type": "text",
            "display_order": 51,
        },
        {
            "key": "feature_email",
            "category": "feature_flags",
            "value": "1",
            "description": "Email search (Sherlock)",
            "value_type": "boolean",
            "display_order": 1,
        },
        {
            "key": "feature_email_holehe",
            "category": "feature_flags",
            "value": "1",
            "description": "Email breach check (Holehe)",
            "value_type": "boolean",
            "display_order": 2,
        },
        {
            "key": "feature_ip",
            "category": "feature_flags",
            "value": "1",
            "description": "IP address lookup",
            "value_type": "boolean",
            "display_order": 3,
        },
        {
            "key": "feature_domain",
            "category": "feature_flags",
            "value": "1",
            "description": "Domain WHOIS/DNS lookup",
            "value_type": "boolean",
            "display_order": 4,
        },
        {
            "key": "feature_username",
            "category": "feature_flags",
            "value": "1",
            "description": "Username search (Sherlock/Maigret)",
            "value_type": "boolean",
            "display_order": 5,
        },
        {
            "key": "feature_phone",
            "category": "feature_flags",
            "value": "1",
            "description": "Phone number lookup",
            "value_type": "boolean",
            "display_order": 6,
        },
        {
            "key": "feature_hibp",
            "category": "feature_flags",
            "value": "1",
            "description": "Have I Been Pwned breach check",
            "value_type": "boolean",
            "display_order": 7,
        },
        {
            "key": "feature_openkvk",
            "category": "feature_flags",
            "value": "1",
            "description": "Dutch business registry (Overheid.io)",
            "value_type": "boolean",
            "display_order": 8,
        },
        {
            "key": "feature_ai",
            "category": "feature_flags",
            "value": "1",
            "description": "AI summarization (OpenRouter / Ollama)",
            "value_type": "boolean",
            "display_order": 9,
        },
        {
            "key": "feature_kadaster",
            "category": "feature_flags",
            "value": "1",
            "description": "Dutch address lookup (PDOK/BAG)",
            "value_type": "boolean",
            "display_order": 10,
        },
        {
            "key": "feature_rdw",
            "category": "feature_flags",
            "value": "1",
            "description": "Dutch vehicle lookup (RDW)",
            "value_type": "boolean",
            "display_order": 11,
        },
        {
            "key": "feature_vessel",
            "category": "feature_flags",
            "value": "1",
            "description": "Vessel/ship lookup",
            "value_type": "boolean",
            "display_order": 12,
        },
        {
            "key": "feature_interpol",
            "category": "feature_flags",
            "value": "1",
            "description": "Interpol wanted/missing check",
            "value_type": "boolean",
            "display_order": 13,
        },
        {
            "key": "feature_webcam",
            "category": "feature_flags",
            "value": "1",
            "description": "Webcam search",
            "value_type": "boolean",
            "display_order": 14,
        },
        # WhatsApp / Telegram API keys
        {
            "key": "whatsapp_checkleaked_key",
            "category": "api_keys",
            "description": "whatsapp.checkleaked.cc API Key (RapidAPI: https://rapidapi.com/...). 📍 https://whatsapp.checkleaked.cc/pricing",
            "value_type": "password",
            "is_sensitive": True,
            "display_order": 12,
        },
        {
            "key": "telegram_rapidapi_key",
            "category": "api_keys",
            "description": "Telegram155 / TG Gateway API Key (RapidAPI: https://rapidapi.com/starnikovoleg/api/telegram155). 📍 https://rapidapi.com/starnikovoleg/api/telegram155",
            "value_type": "password",
            "is_sensitive": True,
            "display_order": 13,
        },
        {
            "key": "telegram_rapidapi_limit",
            "category": "api_keys",
            "value": "30",
            "description": "Telegram155 API maandlimiet (aantal checks per maand, reset op de 1e van elke maand)",
            "value_type": "number",
            "display_order": 14,
        },
        # Telegram bot settings
        {
            "key": "telegram_enabled",
            "category": "telegram",
            "value": "false",
            "description": "Enable Telegram bot (true/false)",
            "value_type": "boolean",
            "display_order": 1,
        },
        {
            "key": "telegram_bot_token",
            "category": "telegram",
            "value": "",
            "description": "Bot token from @BotFather",
            "value_type": "password",
            "is_sensitive": True,
            "display_order": 2,
        },
        {
            "key": "telegram_allowed_users",
            "category": "telegram",
            "value": "",
            "description": "Comma-separated Telegram user IDs allowed to use the bot",
            "value_type": "text",
            "display_order": 3,
        },
        # Twilio SMS/WhatsApp settings
        {
            "key": "twilio_account_sid",
            "category": "general",
            "value": "",
            "description": "Twilio Account SID",
            "value_type": "text",
            "display_order": 60,
        },
        {
            "key": "twilio_auth_token",
            "category": "general",
            "value": "",
            "description": "Twilio Auth Token",
            "value_type": "password",
            "is_sensitive": True,
            "display_order": 61,
        },
        {
            "key": "twilio_phone_number",
            "category": "general",
            "value": "",
            "description": "Twilio SMS sender number (e.g. +12025551234)",
            "value_type": "text",
            "display_order": 62,
        },
        {
            "key": "twilio_whatsapp_number",
            "category": "general",
            "value": "",
            "description": "Twilio WhatsApp sender number (e.g. +14155238886)",
            "value_type": "text",
            "display_order": 63,
        },
        # Rate limit tier defaults
        {
            "key": "rate_limit_tier_defaults",
            "category": "general",
            "value": '{"free": 30, "starter": 60, "professional": 120, "enterprise": 300}',
            "description": "Per-tier API rate limits (requests/min) as JSON object",
            "value_type": "text",
            "display_order": 70,
        },
        {
            "key": "rate_limit_overrides",
            "category": "general",
            "value": "{}",
            "description": "Per-tenant rate limit overrides (tenant_id: requests/min) as JSON object",
            "value_type": "text",
            "display_order": 71,
        },
    ]
    for default in defaults:
        existing = Setting.query.filter_by(key=default["key"]).first()
        if not existing:
            setting = Setting(**default)
            db.session.add(setting)
        else:
            # Patch existing settings that are missing fields like options
            patched = False
            if default.get("options") and not existing.options:
                existing.options = default["options"]
                patched = True
            if (
                default.get("value_type")
                and existing.value_type != default["value_type"]
            ):
                existing.value_type = default["value_type"]
                patched = True
            if (
                default.get("display_order")
                and existing.display_order != default["display_order"]
            ):
                existing.display_order = default["display_order"]
                patched = True
            if patched:
                existing.updated_at = datetime.now(timezone.utc)
    db.session.commit()


# =============================================================================
# SpiderFoot Scan Model
# =============================================================================


class SpiderFootScan(db.Model):
    __tablename__ = "spiderfoot_scans"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = db.Column(
        db.String(36), db.ForeignKey("tenants.id"), nullable=False, index=True
    )
    scan_id = db.Column(db.String(100), nullable=False, index=True)
    scan_name = db.Column(db.String(300))
    target_value = db.Column(db.String(500), nullable=False)
    target_type = db.Column(db.String(50))
    case_id = db.Column(db.String(36), db.ForeignKey("cases.id"), index=True)
    subject_id = db.Column(db.String(36), db.ForeignKey("subjects.id"), index=True)
    use_case = db.Column(db.String(50), default="passive")
    profile = db.Column(db.String(50))
    module_ids = db.Column(SafeJSON)
    status = db.Column(db.String(50), default="pending", index=True)
    progress = db.Column(db.Integer, default=0)
    result_count = db.Column(db.Integer, default=0)
    result_summary = db.Column(SafeJSON)
    started_at = db.Column(db.DateTime)
    finished_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    created_by = db.Column(db.String(36), db.ForeignKey("users.id"), index=True)
    is_deleted = db.Column(db.Boolean, default=False, index=True)
    deleted_at = db.Column(db.DateTime)

    creator = db.relationship(
        "User", foreign_keys=[created_by], backref="spiderfoot_scans"
    )
    case = db.relationship("Case", backref="spiderfoot_scans", foreign_keys=[case_id])
    subject = db.relationship(
        "Subject", backref="spiderfoot_scans", foreign_keys=[subject_id]
    )

    def update_status(self, status: str, progress: int = None) -> None:
        if progress is not None:
            self.progress = progress
        if status == "running" and not self.started_at:
            self.started_at = datetime.now(timezone.utc)
        elif status in ["completed", "failed", "cancelled"]:
            self.finished_at = datetime.now(timezone.utc)
        self.updated_at = datetime.now(timezone.utc)

    def soft_delete(self) -> None:
        self.is_deleted = True
        self.deleted_at = datetime.now(timezone.utc)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "scan_id": self.scan_id,
            "scan_name": self.scan_name,
            "target_value": self.target_value,
            "target_type": self.target_type,
            "case_id": self.case_id,
            "subject_id": self.subject_id,
            "use_case": self.use_case,
            "profile": self.profile,
            "module_ids": self.module_ids,
            "status": self.status,
            "progress": self.progress,
            "result_count": self.result_count,
            "result_summary": self.result_summary,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "created_by": self.created_by,
            "creator_name": self.creator.full_name if self.creator else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


logger = logging.getLogger(__name__)


class OsintSearch(db.Model):
    """DB-backed OSINT search state — survives gunicorn worker restarts and multi-worker setups."""

    __tablename__ = "osint_searches"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = db.Column(
        db.String(36), db.ForeignKey("tenants.id"), nullable=False, index=True
    )
    search_id = db.Column(db.String(36), unique=True, nullable=False, index=True)
    case_id = db.Column(
        db.String(36),
        db.ForeignKey("cases.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    subject_id = db.Column(
        db.String(36),
        db.ForeignKey("subjects.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    search_query = db.Column("query", db.String(500), nullable=False)
    status = db.Column(
        db.String(20), default="running", index=True
    )  # running, completed, cancelled, failed
    results = db.Column(SafeJSON, nullable=True)
    error = db.Column(db.Text, nullable=True)
    spiderfoot_scan_id = db.Column(db.String(36), nullable=True, index=True)
    sf_status = db.Column(
        db.String(20), nullable=True
    )  # pending, running, completed, failed
    started_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    completed_at = db.Column(db.DateTime, nullable=True)
    cancelled_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    started_by = db.Column(
        db.String(36), db.ForeignKey("users.id"), nullable=True, index=True
    )

    def to_dict(self) -> dict:
        return {
            "search_id": self.search_id,
            "case_id": self.case_id,
            "subject_id": self.subject_id,
            "query": self.search_query,
            "status": self.status,
            "results": self.results,
            "error": self.error,
            "spiderfoot_scan_id": self.spiderfoot_scan_id,
            "sf_status": self.sf_status,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat()
            if self.completed_at
            else None,
            "cancelled_at": self.cancelled_at.isoformat()
            if self.cancelled_at
            else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def get_status_dict(self) -> dict:
        return {
            "status": self.status,
            "results": self.results,
            "error": self.error,
            "spiderfoot_scan_id": self.spiderfoot_scan_id,
            "sf_status": self.sf_status,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat()
            if self.completed_at
            else None,
            "cancelled_at": self.cancelled_at.isoformat()
            if self.cancelled_at
            else None,
        }


class ApiKey(db.Model):
    """API key for programmatic access to OSINT endpoints."""

    __tablename__ = "api_keys"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = db.Column(
        db.String(36), db.ForeignKey("tenants.id"), nullable=False, index=True
    )
    name = db.Column(db.String(100), nullable=False)
    key_hash = db.Column(db.String(255), nullable=False)
    key_prefix = db.Column(db.String(8), nullable=False)
    user_id = db.Column(
        db.String(36), db.ForeignKey("users.id"), nullable=False, index=True
    )
    scopes = db.Column(SafeJSON, default=lambda: ["read"])
    is_active = db.Column(db.Boolean, default=True, nullable=False, index=True)
    last_used_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    creator = db.relationship("User", backref="api_keys", foreign_keys=[user_id])

    @staticmethod
    def generate_key() -> tuple[str, str]:
        """Generate a new API key. Returns (raw_key, key_hash)."""
        import secrets

        raw = f"osint_{secrets.token_urlsafe(32)}"
        from werkzeug.security import generate_password_hash

        return raw, generate_password_hash(raw, method="pbkdf2:sha256")

    def verify_key(self, raw_key: str) -> bool:
        from werkzeug.security import check_password_hash

        return check_password_hash(self.key_hash, raw_key)


class Notification(db.Model):
    """In-app notification for users (alerts, restricted search matches, etc.)."""

    __tablename__ = "notifications"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = db.Column(
        db.String(36), db.ForeignKey("tenants.id"), nullable=False, index=True
    )
    user_id = db.Column(
        db.String(36), db.ForeignKey("users.id"), nullable=False, index=True
    )
    category = db.Column(db.String(50), default="general", index=True)
    title = db.Column(db.String(200), default="")
    message = db.Column(db.String(500), nullable=False)
    link = db.Column(db.String(500))
    is_read = db.Column(db.Boolean, default=False, index=True)
    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc), index=True
    )

    user = db.relationship("User", backref=db.backref("notifications", lazy="dynamic"))

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "category": self.category,
            "title": self.title,
            "message": self.message,
            "link": self.link,
            "is_read": self.is_read,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


NOTIFICATION_CATEGORIES = [
    ("usage_alerts", "Usage Alerts"),
    ("search_restricted", "Search Restrictions"),
    ("case_updates", "Case Updates"),
    ("system", "System Notifications"),
    ("general", "General"),
]


class NotificationPreference(db.Model):
    """Per-user notification preference for each category."""

    __tablename__ = "notification_preferences"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = db.Column(
        db.String(36), db.ForeignKey("users.id"), nullable=False, index=True
    )
    category = db.Column(db.String(50), nullable=False)
    web_enabled = db.Column(db.Boolean, default=True)
    email_enabled = db.Column(db.Boolean, default=False)
    sms_enabled = db.Column(db.Boolean, default=False)
    whatsapp_enabled = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, onupdate=lambda: datetime.now(timezone.utc))

    user = db.relationship(
        "User", backref=db.backref("notification_preferences", lazy="dynamic")
    )

    __table_args__ = (
        db.UniqueConstraint(
            "user_id", "category", name="uq_user_notification_category"
        ),
    )

    @classmethod
    def get_pref(cls, user_id: str, category: str) -> "NotificationPreference":
        pref = cls.query.filter_by(user_id=user_id, category=category).first()
        if not pref:
            pref = cls(user_id=user_id, category=category)
            db.session.add(pref)
            db.session.commit()
        return pref

    @classmethod
    def wants_web(cls, user_id: str, category: str) -> bool:
        return cls.get_pref(user_id, category).web_enabled

    @classmethod
    def wants_email(cls, user_id: str, category: str) -> bool:
        return cls.get_pref(user_id, category).email_enabled

    @classmethod
    def wants_sms(cls, user_id: str, category: str) -> bool:
        return cls.get_pref(user_id, category).sms_enabled

    @classmethod
    def wants_whatsapp(cls, user_id: str, category: str) -> bool:
        return cls.get_pref(user_id, category).whatsapp_enabled

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "category": self.category,
            "web_enabled": self.web_enabled,
            "email_enabled": self.email_enabled,
            "sms_enabled": self.sms_enabled,
            "whatsapp_enabled": self.whatsapp_enabled,
        }


class LoginLog(db.Model):
    """Record of login attempts with geolocation and anomaly flags."""

    __tablename__ = "login_logs"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = db.Column(
        db.String(36), db.ForeignKey("tenants.id"), nullable=False, index=True
    )
    user_id = db.Column(
        db.String(36), db.ForeignKey("users.id"), nullable=False, index=True
    )
    ip_address = db.Column(db.String(45), nullable=False)
    user_agent = db.Column(db.String(500))
    country = db.Column(db.String(100))
    region = db.Column(db.String(100))
    city = db.Column(db.String(100))
    isp = db.Column(db.String(200))
    lat = db.Column(db.Float)
    lon = db.Column(db.Float)
    is_success = db.Column(db.Boolean, default=True)
    is_anomaly = db.Column(db.Boolean, default=False)
    anomaly_reason = db.Column(db.String(200))
    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc), index=True
    )

    user = db.relationship("User", backref=db.backref("login_logs", lazy="dynamic"))

    __table_args__ = (db.Index("ix_login_logs_user_created", "user_id", "created_at"),)

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "ip_address": self.ip_address,
            "user_agent": self.user_agent,
            "country": self.country,
            "region": self.region,
            "city": self.city,
            "isp": self.isp,
            "lat": self.lat,
            "lon": self.lon,
            "is_success": self.is_success,
            "is_anomaly": self.is_anomaly,
            "anomaly_reason": self.anomaly_reason,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "username": self.user.username if self.user else None,
        }


class PhoneLookup(db.Model):
    __tablename__ = "phone_lookups"

    ENCRYPTED_FIELDS = ["raw_response", "profile_picture"]

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = db.Column(
        db.String(36), db.ForeignKey("tenants.id"), nullable=False, index=True
    )
    phone = db.Column(
        db.String(50), nullable=False, index=True
    )  # plaintext for queryability
    raw_response = db.Column(SafeJSON, nullable=False)
    profile_picture = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    created_by = db.Column(db.String(36), db.ForeignKey("users.id"), index=True)

    creator = db.relationship(
        "User", backref="phone_lookups", foreign_keys=[created_by]
    )

    def _get_encryptor(self):
        from ..encryption_utils import encryptor

        return encryptor

    def _encrypt_field(self, value):
        f = self._get_encryptor()
        if value is not None:
            try:
                return f.encrypt(str(value))
            except Exception:
                return value
        return value

    def _decrypt_field(self, value):
        if value is None:
            return None
        f = self._get_encryptor()
        if f:
            try:
                return f.decrypt(value)
            except Exception:
                return value
        return value

    @property
    def decrypted_raw_response(self):
        if self.raw_response and isinstance(self.raw_response, str):
            try:
                import json

                raw = self._decrypt_field(self.raw_response)
                return json.loads(raw) if raw else None
            except (json.JSONDecodeError, TypeError):
                return self.raw_response
        return self.raw_response

    @decrypted_raw_response.setter
    def decrypted_raw_response(self, value):
        self.raw_response = self._encrypt_field(value)

    @property
    def decrypted_profile_picture(self):
        return self._decrypt_field(self.profile_picture)

    @decrypted_profile_picture.setter
    def decrypted_profile_picture(self, value):
        self.profile_picture = self._encrypt_field(value)

    @staticmethod
    def purge_old(days: int = None) -> int:
        if days is None:
            days = int(Setting.get("phone_lookup_retention_days", "90"))
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        deleted = PhoneLookup.query.filter(PhoneLookup.created_at < cutoff).delete()
        if deleted:
            db.session.commit()
        return deleted


# =============================================================================
# Invoice / Billing Models
# =============================================================================


class Tenant(db.Model):
    """
    Multi-tenant organization.
    Each tenant is an isolated workspace with its own users and data.
    """

    __tablename__ = "tenants"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = db.Column(db.String(200), nullable=False)
    slug = db.Column(db.String(100), unique=True, nullable=False, index=True)
    domain = db.Column(db.String(255), nullable=True, unique=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    tier = db.Column(db.String(20), default="free", nullable=False)
    stripe_customer_id = db.Column(db.String(255), nullable=True, index=True)
    stripe_subscription_id = db.Column(db.String(255), nullable=True)
    subscription_status = db.Column(db.String(50), default="incomplete", nullable=False)
    current_period_end = db.Column(db.DateTime, nullable=True)
    trial_ends_at = db.Column(db.DateTime, nullable=True)
    dunning_retries = db.Column(db.Integer, default=0)
    canceled_at = db.Column(db.DateTime, nullable=True)
    scheduled_deletion_at = db.Column(db.DateTime, nullable=True)
    join_code = db.Column(db.String(20), unique=True, nullable=False, index=True)
    owner_id = db.Column(
        db.String(36), db.ForeignKey("users.id"), nullable=True, index=True
    )
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    owner = db.relationship(
        "User",
        foreign_keys=[owner_id],
        backref=db.backref("owned_tenant", uselist=False),
        primaryjoin="Tenant.owner_id==User.id",
    )
    users = db.relationship(
        "User", foreign_keys="User.tenant_id", backref="tenant", lazy="dynamic"
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "slug": self.slug,
            "join_code": self.join_code,
            "domain": self.domain,
            "is_active": self.is_active,
            "tier": self.tier,
            "stripe_customer_id": self.stripe_customer_id,
            "stripe_subscription_id": self.stripe_subscription_id,
            "subscription_status": self.subscription_status,
            "current_period_end": self.current_period_end.isoformat()
            if self.current_period_end
            else None,
            "trial_ends_at": self.trial_ends_at.isoformat()
            if self.trial_ends_at
            else None,
            "dunning_retries": self.dunning_retries,
            "canceled_at": self.canceled_at.isoformat() if self.canceled_at else None,
            "scheduled_deletion_at": self.scheduled_deletion_at.isoformat()
            if self.scheduled_deletion_at
            else None,
            "owner_id": self.owner_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class FeatureFlag(db.Model):
    """Super-admin overrides for tier-based feature flags per tenant."""

    __tablename__ = "feature_flags"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = db.Column(
        db.String(36), db.ForeignKey("tenants.id"), nullable=False, index=True
    )
    flag_name = db.Column(db.String(50), nullable=False)
    enabled = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        db.UniqueConstraint(
            "tenant_id", "flag_name", name="uq_feature_flag_tenant_flag"
        ),
    )

    tenant = db.relationship(
        "Tenant", backref=db.backref("feature_flags", lazy="dynamic")
    )


class ProrationLog(db.Model):
    """Record of prorated tier changes for billing transparency."""

    __tablename__ = "proration_logs"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = db.Column(
        db.String(36), db.ForeignKey("tenants.id"), nullable=False, index=True
    )
    from_tier = db.Column(db.String(20), nullable=False)
    to_tier = db.Column(db.String(20), nullable=False)
    stripe_invoice_id = db.Column(db.String(255), nullable=True)
    amount_cents = db.Column(db.Integer, nullable=False, default=0)
    currency = db.Column(db.String(3), default="eur")
    description = db.Column(db.String(500), default="")
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    tenant = db.relationship(
        "Tenant", backref=db.backref("proration_logs", lazy="dynamic")
    )

    def to_dict(self):
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "from_tier": self.from_tier,
            "to_tier": self.to_tier,
            "stripe_invoice_id": self.stripe_invoice_id,
            "amount_cents": self.amount_cents,
            "currency": self.currency,
            "description": self.description,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# =============================================================================
# Tenant Setting Model (per-tenant configuration)
# =============================================================================


class TenantSetting(db.Model):
    """Per-tenant configuration settings (SF URL, API keys, etc.)."""

    __tablename__ = "tenant_settings"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = db.Column(
        db.String(36), db.ForeignKey("tenants.id"), nullable=False, index=True
    )
    key = db.Column(db.String(100), nullable=False, index=True)
    value = db.Column(db.Text, nullable=True)
    category = db.Column(db.String(50), default="general")
    description = db.Column(db.String(500))
    is_encrypted = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        db.UniqueConstraint("tenant_id", "key", name="uq_tenant_settings_key"),
    )

    tenant = db.relationship("Tenant", backref=db.backref("settings", lazy="dynamic"))

    @classmethod
    def get(
        cls, key: str, default: str | None = None, tenant_id: str | None = None
    ) -> str | None:
        """Get a setting value for a tenant."""
        from flask import g

        tid = tenant_id or getattr(g, "tenant_id", None)
        if not tid:
            return default
        row = cls.query.filter_by(tenant_id=tid, key=key).first()
        if not row:
            return default
        if row.is_encrypted and row.value:
            from ..encryption_utils import encryptor

            try:
                return encryptor.decrypt(row.value)
            except Exception:
                return default
        return row.value

    @classmethod
    def set(
        cls,
        key: str,
        value: str,
        tenant_id: str | None = None,
        category: str = "general",
        description: str = "",
        encrypt: bool = False,
    ) -> "TenantSetting":
        """Set a setting value for a tenant."""
        from flask import g

        tid = tenant_id or getattr(g, "tenant_id", None)
        if not tid:
            raise ValueError("No tenant_id provided or available in context")
        from ..encryption_utils import encryptor

        row = cls.query.filter_by(tenant_id=tid, key=key).first()
        if not row:
            row = cls(tenant_id=tid, key=key)
        row.value = encryptor.encrypt(value) if encrypt else value
        row.category = category
        row.description = description
        row.is_encrypted = encrypt
        db.session.add(row)
        db.session.commit()
        return row


# =============================================================================
# Auto-fill tenant_id on insert
# =============================================================================

_TENANT_MODELS = [
    Client,
    Case,
    Subject,
    Address,
    Contact,
    FinancialRecord,
    Finding,
    Screenshot,
    AuditLog,
    Document,
    Comment,
    CommentEditHistory,
    DocumentTemplate,
    Reminder,
    SocialAccount,
    OsintSearch,
    ApiKey,
    Notification,
    LoginLog,
    PhoneLookup,
    Invoice,
    InvoiceItem,
    Payment,
    CreditNote,
    CreditNoteItem,
    ProrationLog,
    TenantSetting,
    SpiderFootScan,
    User,
    UsageRecord,
    ResearchAction,
    FindingScreenshot,
    ServiceRate,
]


def _fill_tenant_id(mapper, connection, target):
    """Auto-fill tenant_id from flask.g or the owning user when a new row is inserted."""
    if hasattr(target, "tenant_id") and target.tenant_id is None:
        from flask import g as _g

        tid = getattr(_g, "tenant_id", None)
        if not tid and hasattr(target, "user_id") and target.user_id:
            try:
                _user = db.session.get(User, target.user_id)
                if _user:
                    tid = _user.tenant_id
            except Exception:
                pass
        if not tid:
            try:
                _admin = User.query.filter_by(role="admin").first()
                if _admin and _admin.tenant_id:
                    tid = _admin.tenant_id
            except Exception:
                pass
        if tid:
            target.tenant_id = tid


for _model in _TENANT_MODELS:
    from sqlalchemy import event as _event

    if hasattr(_model, "tenant_id"):
        _event.listen(_model, "before_insert", _fill_tenant_id)
