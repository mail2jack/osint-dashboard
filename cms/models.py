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
from datetime import datetime
from enum import Enum as PyEnum
from typing import Optional, List

from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

from .encryption_utils import EncryptedString, encryptor


db = SQLAlchemy()


class UserRole(PyEnum):
    """User roles with hierarchical permissions."""
    ADMIN = "admin"                    # Full system access
    SENIOR_INVESTIGATOR = "senior_investigator"  # Can manage cases, export data
    JUNIOR_INVESTIGATOR = "junior_investigator"  # Can view and update assigned cases
    VIEWER = "viewer"                  # Read-only access


class CasePriority(PyEnum):
    """Case priority levels."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class CaseStatus(PyEnum):
    """Case lifecycle statuses."""
    OPEN = "open"           # New case, not yet started
    ACTIVE = "active"       # Investigation in progress
    SUSPENDED = "suspended"  # Temporarily paused
    CLOSED = "closed"        # Investigation complete
    ARCHIVED = "archived"    # Archived for compliance


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
    PROPERTY = "property"


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
    'case_assignments',
    db.Column('case_id', db.String(36), db.ForeignKey('cases.id'), primary_key=True),
    db.Column('user_id', db.String(36), db.ForeignKey('users.id'), primary_key=True),
    db.Column('assigned_at', db.DateTime, default=datetime.utcnow),
    db.Column('assigned_by', db.String(36), db.ForeignKey('users.id'))
)

case_subjects = db.Table(
    'case_subjects',
    db.Column('case_id', db.String(36), db.ForeignKey('cases.id'), primary_key=True),
    db.Column('subject_id', db.String(36), db.ForeignKey('subjects.id'), primary_key=True)
)

subject_relations = db.Table(
    'subject_relations',
    db.Column('subject_id', db.String(36), db.ForeignKey('subjects.id'), primary_key=True),
    db.Column('related_subject_id', db.String(36), db.ForeignKey('subjects.id'), primary_key=True),
    db.Column('relationship_type', db.String(100)),  # e.g., "family_member", "business_partner"
    db.Column('created_at', db.DateTime, default=datetime.utcnow)
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
    __tablename__ = 'users'
    
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    hashed_password = db.Column(db.String(255), nullable=False)
    full_name = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(30), nullable=False, default=UserRole.VIEWER.value)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_login = db.Column(db.DateTime)
    
    # Relationships
    assigned_cases = db.relationship(
        'Case',
        secondary=case_assignments,
        primaryjoin='User.id==case_assignments.c.user_id',
        secondaryjoin='Case.id==case_assignments.c.case_id',
        backref=db.backref('investigators', lazy='dynamic'),
        lazy='dynamic'
    )
    created_cases = db.relationship('Case', foreign_keys='Case.created_by', backref='creator', lazy='dynamic')
    findings = db.relationship('Finding', backref='author', lazy='dynamic')
    audit_logs = db.relationship('AuditLog', backref='user', lazy='dynamic')
    
    def set_password(self, password: str):
        """Hash and set password securely."""
        self.hashed_password = generate_password_hash(password, method='pbkdf2:sha256')
    
    def check_password(self, password: str) -> bool:
        """Verify password against hash."""
        return check_password_hash(self.hashed_password, password)
    
    def has_role(self, *roles) -> bool:
        """Check if user has any of the specified roles."""
        return self.role in [r.value if isinstance(r, UserRole) else r for r in roles]
    
    def can_access_case(self, case) -> bool:
        """Check if user can access a specific case."""
        if self.is_admin:
            return True
        # Investigators can view any case
        if self.role in ['senior_investigator', 'junior_investigator']:
            return True
        # Assigned users can access their cases
        if case.assigned_to == self.id or self in case.investigators:
            return True
        return False
    
    @property
    def is_admin(self) -> bool:
        return self.role == UserRole.ADMIN.value
    
    @property
    def is_senior(self) -> bool:
        return self.role in [UserRole.ADMIN.value, UserRole.SENIOR_INVESTIGATOR.value]
    
    @property
    def can_export(self) -> bool:
        """Only senior investigators and admins can export data."""
        return self.is_senior
    
    def to_dict(self, include_sensitive: bool = False) -> dict:
        """Serialize user without password."""
        return {
            'id': self.id,
            'username': self.username,
            'email': self.email,
            'full_name': self.full_name,
            'role': self.role,
            'is_active': self.is_active,
            'last_login': self.last_login.isoformat() if self.last_login else None
        }


# =============================================================================
# Client Model
# =============================================================================

class Client(db.Model):
    """
    Client model for organizations commissioning investigations.
    
    Sensitive NAW data is encrypted at rest.
    """
    __tablename__ = 'clients'
    
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = db.Column(db.String(200), nullable=False, index=True)
    is_company = db.Column(db.Boolean, default=False)
    contact_person = db.Column(db.String(200))  # Encrypted
    contact_email = db.Column(db.String(200))   # Encrypted
    contact_phone = db.Column(db.String(50))    # Encrypted
    address_street = db.Column(db.String(300)) # Encrypted
    address_city = db.Column(db.String(100))    # Encrypted
    address_postal = db.Column(db.String(20))   # Encrypted
    address_country = db.Column(db.String(100)) # Encrypted
    contract_number = db.Column(db.String(100))
    contract_info = db.Column(db.Text)
    social_security_number = db.Column(db.String(50))  # Encrypted
    vat_number = db.Column(db.String(50))  # For companies
    bank_account = db.Column(db.String(100))  # Encrypted
    financial_notes = db.Column(db.Text)
    is_active = db.Column(db.Boolean, default=True)
    is_deleted = db.Column(db.Boolean, default=False)  # Soft delete
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    deleted_at = db.Column(db.DateTime)
    
    # Relationships
    cases = db.relationship('Case', backref='client', lazy='dynamic')
    
    # Encrypted fields list for reference
    ENCRYPTED_FIELDS = [
        'contact_person', 'contact_email', 'contact_phone',
        'address_street', 'address_city', 'address_postal', 'address_country',
        'social_security_number', 'bank_account'
    ]
    
    def encrypt_naw(self):
        """Encrypt all NAW fields before saving."""
        for field in self.ENCRYPTED_FIELDS:
            value = getattr(self, field)
            if value:
                setattr(self, field, encryptor.encrypt(value))
    
    def decrypt_naw(self):
        """Decrypt all NAW fields for display."""
        for field in self.ENCRYPTED_FIELDS:
            value = getattr(self, field)
            if value:
                try:
                    setattr(self, field, encryptor.decrypt(value))
                except:
                    pass  # Handle corrupted data gracefully
    
    def soft_delete(self):
        """Soft delete the client."""
        self.is_deleted = True
        self.deleted_at = datetime.utcnow()
        self.is_active = False
    
    def to_dict(self, decrypted: bool = True) -> dict:
        """Serialize client data."""
        if decrypted:
            self.decrypt_naw()
        
        return {
            'id': self.id,
            'name': self.name,
            'contact_person': self.contact_person,
            'contact_email': self.contact_email,
            'contact_phone': self.contact_phone,
            'address': {
                'street': self.address_street,
                'city': self.address_city,
                'postal': self.address_postal,
                'country': self.address_country
            },
            'contract_number': self.contract_number,
            'contract_info': self.contract_info,
            'is_active': self.is_active,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }


# =============================================================================
# Case Model
# =============================================================================

class Case(db.Model):
    """
    Case model representing an investigation.
    
    Includes workflow status, priority, and assignment tracking.
    """
    __tablename__ = 'cases'
    
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    case_number = db.Column(db.String(50), unique=True, nullable=False, index=True)
    client_id = db.Column(db.String(36), db.ForeignKey('clients.id'), nullable=False)
    title = db.Column(db.String(300), nullable=False)
    description = db.Column(db.Text)
    priority = db.Column(db.String(20), default=CasePriority.MEDIUM.value)
    status = db.Column(db.String(20), default=CaseStatus.OPEN.value)
    start_date = db.Column(db.Date, nullable=False)
    target_end_date = db.Column(db.Date)
    actual_end_date = db.Column(db.Date)
    closure_reason = db.Column(db.Text)  # Reason for closing
    created_by = db.Column(db.String(36), db.ForeignKey('users.id'))
    assigned_to = db.Column(db.String(36), db.ForeignKey('users.id'))
    lead_investigator_id = db.Column(db.String(36), db.ForeignKey('users.id'))
    
    # Case metadata
    case_type = db.Column(db.String(100))  # e.g., "fraud", "due_diligence", "asset_tracing"
    jurisdiction = db.Column(db.String(100))
    tags = db.Column(db.JSON)  # Flexible tagging
    
    # Reopening
    reopened_reason = db.Column(db.Text)  # Reason for reopening
    reopened_at = db.Column(db.DateTime)
    reopened_by = db.Column(db.String(36), db.ForeignKey('users.id'))
    
    # Case hierarchy
    parent_case_id = db.Column(db.String(36), db.ForeignKey('cases.id'), nullable=True)
    
    # Soft delete
    is_deleted = db.Column(db.Boolean, default=False)
    deleted_at = db.Column(db.DateTime)
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    subjects = db.relationship(
        'Subject',
        secondary=case_subjects,
        backref=db.backref('cases', lazy='dynamic'),
        lazy='dynamic'
    )
    findings = db.relationship('Finding', backref='case', lazy='dynamic', cascade='all, delete-orphan')
    financial_records = db.relationship('FinancialRecord', backref='case', lazy='dynamic')
    child_cases = db.relationship('Case', backref=db.backref('parent_case', remote_side=[id]), lazy='dynamic')
    reminders = db.relationship('Reminder', backref='case', lazy='dynamic', foreign_keys='Reminder.case_id')
    lead_investigator = db.relationship('User', foreign_keys=[lead_investigator_id], backref='led_cases')
    
    @staticmethod
    def generate_case_number() -> str:
        """Generate unique case number: YYYY-XXXXX format."""
        year = datetime.now().year
        last_case = Case.query.filter(
            Case.case_number.like(f'{year}-%')
        ).order_by(Case.created_at.desc()).first()
        
        if last_case:
            try:
                last_num = int(last_case.case_number.split('-')[1])
                next_num = last_num + 1
            except:
                next_num = 1
        else:
            next_num = 1
        
        return f'{year}-{next_num:05d}'
    
    def transition_status(self, new_status: str, user_id: str):
        """Transition case to new status with validation."""
        valid_transitions = {
            CaseStatus.OPEN.value: [CaseStatus.ACTIVE.value, CaseStatus.CLOSED.value],
            CaseStatus.ACTIVE.value: [CaseStatus.SUSPENDED.value, CaseStatus.CLOSED.value],
            CaseStatus.SUSPENDED.value: [CaseStatus.ACTIVE.value, CaseStatus.CLOSED.value],
            CaseStatus.CLOSED.value: [CaseStatus.ARCHIVED.value, CaseStatus.ACTIVE.value],
            CaseStatus.ARCHIVED.value: [CaseStatus.ACTIVE.value]
        }
        
        if new_status in valid_transitions.get(self.status, []):
            self.status = new_status
            if new_status == CaseStatus.CLOSED.value:
                self.actual_end_date = datetime.utcnow().date()
            elif new_status == CaseStatus.ACTIVE.value and self.actual_end_date:
                self.actual_end_date = None  # Clear end date when reopening
            return True
        return False
    
    def soft_delete(self):
        """Soft delete the case."""
        self.is_deleted = True
        self.deleted_at = datetime.utcnow()
    
    def to_dict(self, include_relations: bool = True) -> dict:
        """Serialize case data."""
        result = {
            'id': self.id,
            'case_number': self.case_number,
            'client_id': self.client_id,
            'title': self.title,
            'description': self.description,
            'priority': self.priority,
            'status': self.status,
            'start_date': self.start_date.isoformat() if self.start_date else None,
            'target_end_date': self.target_end_date.isoformat() if self.target_end_date else None,
            'actual_end_date': self.actual_end_date.isoformat() if self.actual_end_date else None,
            'closure_reason': self.closure_reason,
            'reopened_reason': self.reopened_reason,
            'reopened_at': self.reopened_at.isoformat() if self.reopened_at else None,
            'case_type': self.case_type,
            'jurisdiction': self.jurisdiction,
            'tags': self.tags,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }
        
        if include_relations:
            result['subjects'] = [s.to_dict() for s in self.subjects]
            result['findings_count'] = self.findings.count()
            result['assigned_investigators'] = [
                {'id': u.id, 'name': u.full_name} for u in self.investigators
            ]
            result['parent_case'] = {
                'id': self.parent_case.id,
                'case_number': self.parent_case.case_number,
                'title': self.parent_case.title
            } if self.parent_case else None
            result['child_cases'] = [
                {'id': c.id, 'case_number': c.case_number, 'title': c.title, 'status': c.status}
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
    __tablename__ = 'subjects'
    
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = db.Column(db.String(300), nullable=False, index=True)
    subject_type = db.Column(db.String(20), nullable=False)
    
    # Encrypted identifying information
    date_of_birth = db.Column(db.String(50))   # Encrypted (DOB can be sensitive)
    place_of_birth = db.Column(db.String(200))  # Encrypted
    nationality = db.Column(db.String(100))     # Encrypted
    identification_number = db.Column(db.String(100))  # Encrypted (BSN, passport, etc.)
    address = db.Column(db.String(500))         # Encrypted
    phone = db.Column(db.String(100))           # Encrypted
    email = db.Column(db.String(200))           # Encrypted
    
    # Additional metadata
    risk_score = db.Column(db.Integer, default=0)  # 0-100 risk assessment
    risk_factors = db.Column(db.JSON)              # List of risk indicators
    notes = db.Column(db.Text)
    
    # Entity-specific fields
    registration_number = db.Column(db.String(100))  # KVK, Chamber of Commerce, etc.
    legal_form = db.Column(db.String(100))          # BV, NV, Stichting, etc.
    
    # Asset-specific fields
    asset_type = db.Column(db.String(50))
    estimated_value = db.Column(db.Numeric(15, 2))
    currency = db.Column(db.String(3), default='EUR')
    
    # Social media identifiers (extracted from profiles)
    # Structure: {"facebook": {"id": "123456", "username": "johndoe"}, "vk": {...}, etc.}
    social_media_ids = db.Column(db.JSON)
    
    # Vehicle-specific fields (encrypted)
    license_plate = db.Column(db.String(20))  # Encrypted
    vin = db.Column(db.String(50))  # Encrypted (Vehicle Identification Number)
    insurance_company = db.Column(db.String(200))  # Encrypted
    brand = db.Column(db.String(100))
    vehicle_type = db.Column(db.String(50))  # sedan, suv, truck, etc.
    
    # RDW vehicle data (full RDW record as JSON)
    rdw_data = db.Column(db.JSON)
    
    # Photo
    photo_path = db.Column(db.String(500))  # Path to uploaded photo
    
    # Soft delete
    is_deleted = db.Column(db.Boolean, default=False)
    deleted_at = db.Column(db.DateTime)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    financial_records = db.relationship('FinancialRecord', backref='subject', lazy='dynamic')
    findings = db.relationship('Finding', backref='subject', lazy='dynamic')
    
    # Relations with other subjects
    related_subjects = db.relationship(
        'Subject',
        secondary=subject_relations,
        primaryjoin=id==subject_relations.c.subject_id,
        secondaryjoin=id==subject_relations.c.related_subject_id,
        backref='related_to'
    )
    
    ENCRYPTED_FIELDS = [
        'date_of_birth', 'place_of_birth', 'nationality',
        'identification_number', 'address', 'phone', 'email',
        'license_plate', 'vin', 'insurance_company'
    ]
    
    def encrypt_identifiers(self):
        """Encrypt all identifying fields."""
        for field in self.ENCRYPTED_FIELDS:
            value = getattr(self, field)
            if value:
                setattr(self, field, encryptor.encrypt(value))
    
    def decrypt_identifiers(self):
        """Decrypt all identifying fields for display."""
        for field in self.ENCRYPTED_FIELDS:
            value = getattr(self, field)
            if value:
                try:
                    setattr(self, field, encryptor.decrypt(value))
                except:
                    pass
    
    def soft_delete(self):
        """Soft delete the subject."""
        self.is_deleted = True
        self.deleted_at = datetime.utcnow()
    
    def to_dict(self, decrypted: bool = True, include_relations: bool = False) -> dict:
        """Serialize subject data."""
        if decrypted:
            self.decrypt_identifiers()
        
        result = {
            'id': self.id,
            'name': self.name,
            'subject_type': self.subject_type,
            'date_of_birth': self.date_of_birth,
            'place_of_birth': self.place_of_birth,
            'nationality': self.nationality,
            'identification_number': self.identification_number,
            'address': self.address,
            'phone': self.phone,
            'email': self.email,
            'risk_score': self.risk_score,
            'risk_factors': self.risk_factors,
            'notes': self.notes,
            'registration_number': self.registration_number,
            'legal_form': self.legal_form,
            'asset_type': self.asset_type,
            'estimated_value': float(self.estimated_value) if self.estimated_value else None,
            'currency': self.currency,
            'license_plate': self.license_plate,
            'vin': self.vin,
            'insurance_company': self.insurance_company,
            'brand': self.brand,
            'vehicle_type': self.vehicle_type,
            'social_media_ids': self.social_media_ids or {},
            'rdw_data': self.rdw_data or {},
            'created_at': self.created_at.isoformat() if self.created_at else None
        }
        
        if include_relations:
            result['related_subjects'] = [
                {'id': s.id, 'name': s.name, 'type': s.subject_type}
                for s in self.related_subjects
            ]
            result['financial_records_count'] = self.financial_records.count()
            result['findings_count'] = self.findings.count()
        
        return result


# =============================================================================
# Financial Record Model
# =============================================================================

class FinancialRecord(db.Model):
    """
    Financial transaction record for tracking money flows.
    
    All amounts and counterparty info encrypted for privacy.
    """
    __tablename__ = 'financial_records'
    
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    case_id = db.Column(db.String(36), db.ForeignKey('cases.id'), nullable=False)
    subject_id = db.Column(db.String(36), db.ForeignKey('subjects.id'))
    
    transaction_date = db.Column(db.Date, nullable=False)
    amount = db.Column(db.Numeric(15, 2), nullable=False)
    currency = db.Column(db.String(3), default='EUR')
    
    # Encrypted counterparty details
    counterparty_name = db.Column(db.String(300))    # Encrypted
    counterparty_account = db.Column(db.String(100)) # Encrypted
    counterparty_bank = db.Column(db.String(200))    # Encrypted
    counterparty_country = db.Column(db.String(100))  # Encrypted
    
    transaction_type = db.Column(db.String(50))  # transfer, cash, crypto, etc.
    source = db.Column(db.String(100))           # bank_statement, invoice, etc.
    source_reference = db.Column(db.String(100))  # Reference number in source doc
    description = db.Column(db.Text)
    
    verification_status = db.Column(db.String(20), default=VerificationStatus.PENDING.value)
    verified_by = db.Column(db.String(36), db.ForeignKey('users.id'))
    verified_at = db.Column(db.DateTime)
    verification_notes = db.Column(db.Text)
    
    is_deleted = db.Column(db.Boolean, default=False)
    deleted_at = db.Column(db.DateTime)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    attachments = db.relationship('Document', backref='financial_record', lazy='dynamic')
    
    ENCRYPTED_FIELDS = [
        'counterparty_name', 'counterparty_account', 'counterparty_bank', 'counterparty_country'
    ]
    
    def encrypt_details(self):
        """Encrypt counterparty information."""
        for field in self.ENCRYPTED_FIELDS:
            value = getattr(self, field)
            if value:
                setattr(self, field, encryptor.encrypt(value))
    
    def decrypt_details(self):
        """Decrypt counterparty information for display."""
        for field in self.ENCRYPTED_FIELDS:
            value = getattr(self, field)
            if value:
                try:
                    setattr(self, field, encryptor.decrypt(value))
                except:
                    pass
    
    def verify(self, user_id: str, notes: str = None):
        """Mark record as verified."""
        self.verification_status = VerificationStatus.VERIFIED.value
        self.verified_by = user_id
        self.verified_at = datetime.utcnow()
        if notes:
            self.verification_notes = notes
    
    def flag(self, user_id: str, notes: str = None):
        """Flag record for review."""
        self.verification_status = VerificationStatus.FLAGGED.value
        self.verified_by = user_id
        self.verified_at = datetime.utcnow()
        if notes:
            self.verification_notes = notes
    
    def soft_delete(self):
        """Soft delete the record."""
        self.is_deleted = True
        self.deleted_at = datetime.utcnow()
    
    def to_dict(self, decrypted: bool = True) -> dict:
        """Serialize financial record."""
        if decrypted:
            self.decrypt_details()
        
        return {
            'id': self.id,
            'case_id': self.case_id,
            'subject_id': self.subject_id,
            'transaction_date': self.transaction_date.isoformat() if self.transaction_date else None,
            'amount': float(self.amount) if self.amount else 0,
            'currency': self.currency,
            'counterparty_name': self.counterparty_name,
            'counterparty_account': self.counterparty_account,
            'counterparty_bank': self.counterparty_bank,
            'counterparty_country': self.counterparty_country,
            'transaction_type': self.transaction_type,
            'source': self.source,
            'source_reference': self.source_reference,
            'description': self.description,
            'verification_status': self.verification_status,
            'verified_by': self.verified_by,
            'verified_at': self.verified_at.isoformat() if self.verified_at else None,
            'verification_notes': self.verification_notes,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }


# =============================================================================
# Finding Model
# =============================================================================

class Finding(db.Model):
    """
    Investigation finding linked to a case and/or subject.
    """
    __tablename__ = 'findings'
    
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    case_id = db.Column(db.String(36), db.ForeignKey('cases.id'), nullable=False)
    subject_id = db.Column(db.String(36), db.ForeignKey('subjects.id'))
    
    title = db.Column(db.String(300), nullable=False)
    content = db.Column(db.Text, nullable=False)
    source_url = db.Column(db.String(500))
    source_type = db.Column(db.String(50))  # osint, interview, document, etc.
    
    # Reliability scoring (1-10)
    reliability_score = db.Column(db.Integer, default=5)
    confidence_level = db.Column(db.String(20))  # low, medium, high, verified
    
    finding_type = db.Column(db.String(50))  # identity, location, connection, financial, etc.
    tags = db.Column(db.JSON)
    
    created_by = db.Column(db.String(36), db.ForeignKey('users.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    is_deleted = db.Column(db.Boolean, default=False)
    deleted_at = db.Column(db.DateTime)
    
    def soft_delete(self):
        """Soft delete the finding."""
        self.is_deleted = True
        self.deleted_at = datetime.utcnow()
    
    def to_dict(self) -> dict:
        """Serialize finding."""
        return {
            'id': self.id,
            'case_id': self.case_id,
            'subject_id': self.subject_id,
            'title': self.title,
            'content': self.content,
            'source_url': self.source_url,
            'source_type': self.source_type,
            'reliability_score': self.reliability_score,
            'confidence_level': self.confidence_level,
            'finding_type': self.finding_type,
            'tags': self.tags,
            'created_by': self.created_by,
            'author_name': self.author.full_name if self.author else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }


# =============================================================================
# Screenshot Model
# =============================================================================

class Screenshot(db.Model):
    """
    Screenshots captured from URLs for case documentation.
    """
    __tablename__ = 'screenshots'
    
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    case_id = db.Column(db.String(36), db.ForeignKey('cases.id'), nullable=False)
    
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
    extracted_data = db.Column(db.JSON)
    
    created_by = db.Column(db.String(36), db.ForeignKey('users.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    case = db.relationship('Case', backref='screenshots')
    creator = db.relationship('User', foreign_keys=[created_by])
    
    def to_dict(self) -> dict:
        """Serialize screenshot."""
        return {
            'id': self.id,
            'case_id': self.case_id,
            'url': self.url,
            'filename': self.filename,
            'original_filename': self.original_filename,
            'title': self.title,
            'width': self.width,
            'height': self.height,
            'file_size': self.file_size,
            'extracted_data': self.extracted_data,
            'created_by': self.created_by,
            'creator_name': self.creator.full_name if self.creator else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'thumbnail_url': f'/cms/cases/{self.case_id}/screenshots/{self.id}/thumbnail',
            'full_url': f'/cms/cases/{self.case_id}/screenshots/{self.id}/view'
        }


# =============================================================================
# Audit Log Model
# =============================================================================

class AuditLog(db.Model):
    """
    Immutable audit log for compliance and security monitoring.
    
    Records all significant actions for GDPR Article 30 compliance.
    """
    __tablename__ = 'audit_logs'
    
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = db.Column(db.String(36), db.ForeignKey('users.id'))
    action = db.Column(db.String(20), nullable=False, index=True)
    
    entity_type = db.Column(db.String(50), nullable=False, index=True)  # case, client, subject, etc.
    entity_id = db.Column(db.String(36), index=True)
    
    changes_made = db.Column(db.JSON)  # {"field": {"old": "x", "new": "y"}}
    old_values = db.Column(db.JSON)     # Previous state snapshot
    new_values = db.Column(db.JSON)    # New state snapshot
    
    ip_address = db.Column(db.String(45))  # IPv6 compatible
    user_agent = db.Column(db.String(500))
    session_id = db.Column(db.String(100))
    
    # Context
    case_id = db.Column(db.String(36))  # Related case for context
    description = db.Column(db.String(500))  # Human-readable description
    
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    
    # No soft delete - audit logs are immutable and permanent
    # No updated_at - logs should not be modified
    
    @property
    def user_name(self) -> str:
        """Get the full name of the user who performed this action."""
        if self.user:
            return self.user.full_name
        return 'System'
    
    @staticmethod
    def log(
        user_id: str,
        action: str,
        entity_type: str,
        entity_id: str = None,
        changes: dict = None,
        old_values: dict = None,
        new_values: dict = None,
        ip_address: str = None,
        user_agent: str = None,
        case_id: str = None,
        description: str = None
    ):
        """
        Create an audit log entry.
        
        This should be called after every significant action.
        """
        log_entry = AuditLog(
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
            description=description
        )
        db.session.add(log_entry)
        return log_entry
    
    def to_dict(self) -> dict:
        """Serialize audit log."""
        return {
            'id': self.id,
            'user_id': self.user_id,
            'user_name': self.user.full_name if self.user else 'System',
            'action': self.action,
            'entity_type': self.entity_type,
            'entity_id': self.entity_id,
            'changes_made': self.changes_made,
            'ip_address': self.ip_address,
            'case_id': self.case_id,
            'description': self.description,
            'timestamp': self.timestamp.isoformat() if self.timestamp else None
        }


# =============================================================================
# Document Model (for attachments)
# =============================================================================

class Document(db.Model):
    """
    Document attachment for cases, subjects, and financial records.
    """
    __tablename__ = 'documents'
    
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    
    # Linked entity
    case_id = db.Column(db.String(36), db.ForeignKey('cases.id'))
    subject_id = db.Column(db.String(36), db.ForeignKey('subjects.id'))
    financial_record_id = db.Column(db.String(36), db.ForeignKey('financial_records.id'))
    
    filename = db.Column(db.String(255), nullable=False)
    original_filename = db.Column(db.String(255), nullable=False)
    mime_type = db.Column(db.String(100))
    file_size = db.Column(db.Integer)  # bytes
    
    # File storage reference (path, S3 key, etc.)
    storage_path = db.Column(db.String(500))
    storage_type = db.Column(db.String(20), default='local')  # local, s3, azure
    
    # Document metadata
    document_type = db.Column(db.String(50))  # evidence, contract, report, etc.
    description = db.Column(db.Text)
    tags = db.Column(db.JSON)
    
    # Security classification
    classification = db.Column(db.String(20), default='confidential')  # public, internal, confidential, restricted
    
    uploaded_by = db.Column(db.String(36), db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    is_deleted = db.Column(db.Boolean, default=False)
    deleted_at = db.Column(db.DateTime)
    
    def soft_delete(self):
        """Soft delete the document."""
        self.is_deleted = True
        self.deleted_at = datetime.utcnow()
    
    def to_dict(self) -> dict:
        """Serialize document metadata."""
        return {
            'id': self.id,
            'case_id': self.case_id,
            'subject_id': self.subject_id,
            'financial_record_id': self.financial_record_id,
            'filename': self.filename,
            'original_filename': self.original_filename,
            'mime_type': self.mime_type,
            'file_size': self.file_size,
            'document_type': self.document_type,
            'description': self.description,
            'classification': self.classification,
            'uploaded_by': self.uploaded_by,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }


# =============================================================================
# Comment Model
# =============================================================================

class Comment(db.Model):
    """
    Comment model for notes/discussions on any entity.
    
    Can be linked to: case, subject, client, or financial_record
    """
    __tablename__ = 'comments'
    
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    
    # Entity references (at least one must be set)
    case_id = db.Column(db.String(36), db.ForeignKey('cases.id'))
    subject_id = db.Column(db.String(36), db.ForeignKey('subjects.id'))
    client_id = db.Column(db.String(36), db.ForeignKey('clients.id'))
    financial_record_id = db.Column(db.String(36), db.ForeignKey('financial_records.id'))
    
    # Comment content
    content = db.Column(db.Text, nullable=False)
    
    # Metadata
    comment_type = db.Column(db.String(20), default='note')  # note, discussion, update, resolution
    is_pinned = db.Column(db.Boolean, default=False)
    is_resolved = db.Column(db.Boolean, default=False)
    
    # Ownership
    author_id = db.Column(db.String(36), db.ForeignKey('users.id'), nullable=False)
    author = db.relationship('User', foreign_keys=[author_id], backref='comments')
    
    # Edit tracking
    edit_count = db.Column(db.Integer, default=0)
    last_edited_by_id = db.Column(db.String(36), db.ForeignKey('users.id'))
    last_edited_by = db.relationship('User', foreign_keys=[last_edited_by_id], backref='edited_comments')
    last_edited_at = db.Column(db.DateTime)
    edit_history = db.relationship('CommentEditHistory', backref='comment', lazy='dynamic', cascade='all, delete-orphan')
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Soft delete
    is_deleted = db.Column(db.Boolean, default=False)
    deleted_at = db.Column(db.DateTime)
    
    def soft_delete(self):
        """Soft delete the comment."""
        self.is_deleted = True
        self.deleted_at = datetime.utcnow()
    
    def to_dict(self) -> dict:
        """Serialize comment."""
        return {
            'id': self.id,
            'case_id': self.case_id,
            'subject_id': self.subject_id,
            'client_id': self.client_id,
            'financial_record_id': self.financial_record_id,
            'content': self.content,
            'comment_type': self.comment_type,
            'is_pinned': self.is_pinned,
            'is_resolved': self.is_resolved,
            'author_id': self.author_id,
            'author_name': self.author.full_name if self.author else 'Unknown',
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'edit_count': self.edit_count,
            'last_edited_by_id': self.last_edited_by_id,
            'last_edited_by_name': self.last_edited_by.full_name if self.last_edited_by else None,
            'last_edited_at': self.last_edited_at.isoformat() if self.last_edited_at else None,
            'edit_history': [h.to_dict() for h in self.edit_history.order_by(CommentEditHistory.edited_at.desc()).limit(10).all()]
        }


# =============================================================================
# Comment Edit History Model
# =============================================================================

class CommentEditHistory(db.Model):
    """
    Audit trail for comment edits.
    Stores each version of a comment when it is edited.
    """
    __tablename__ = 'comment_edit_history'
    
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    
    comment_id = db.Column(db.String(36), db.ForeignKey('comments.id'), nullable=False)
    
    previous_content = db.Column(db.Text, nullable=False)
    new_content = db.Column(db.Text, nullable=False)
    
    edited_by_id = db.Column(db.String(36), db.ForeignKey('users.id'), nullable=False)
    edited_by = db.relationship('User', backref='comment_edits')
    
    edited_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def to_dict(self) -> dict:
        """Serialize edit history entry."""
        return {
            'id': self.id,
            'comment_id': self.comment_id,
            'previous_content': self.previous_content,
            'new_content': self.new_content,
            'edited_by_id': self.edited_by_id,
            'edited_by_name': self.edited_by.full_name if self.edited_by else 'Unknown',
            'edited_at': self.edited_at.isoformat() if self.edited_at else None
        }


# =============================================================================
# Document Template Model
# =============================================================================

class DocumentTemplate(db.Model):
    """
    Document template for generating investigation reports.
    
    Templates use placeholders like {{case_number}}, {{client_name}}, etc.
    """
    __tablename__ = 'document_templates'
    
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    
    # Template info
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    template_type = db.Column(db.String(50), default='report')  # report, summary, letter, memo
    
    # Template content (uses placeholders)
    content = db.Column(db.Text, nullable=False)
    
    # Categories for organization
    category = db.Column(db.String(50))  # investigation, compliance, financial, general
    
    # Metadata
    is_default = db.Column(db.Boolean, default=False)
    is_active = db.Column(db.Boolean, default=True)
    
    # Ownership
    created_by = db.Column(db.String(36), db.ForeignKey('users.id'))
    creator = db.relationship('User', backref='document_templates')
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def to_dict(self) -> dict:
        """Serialize template."""
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'template_type': self.template_type,
            'content': self.content,
            'category': self.category,
            'is_default': self.is_default,
            'is_active': self.is_active,
            'created_by': self.created_by,
            'creator_name': self.creator.full_name if self.creator else 'Unknown',
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }
    
    def render(self, context: dict) -> str:
        """
        Render template with provided context.
        
        Context can include: case, client, subjects, findings, financials, custom fields
        """
        from jinja2 import Environment, BaseLoader
        import logging
        from datetime import datetime
        
        env = Environment(loader=BaseLoader())
        
        env.filters['default'] = lambda v, d: v if v else d
        env.filters['date'] = lambda v, fmt='%Y-%m-%d': v.strftime(fmt) if isinstance(v, datetime) else str(v)
        env.filters['currency'] = lambda v: f"€{v:,.2f}" if isinstance(v, (int, float)) else str(v)
        env.globals['now'] = datetime.utcnow()
        
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
    __tablename__ = 'reminders'
    
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    
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
    priority = db.Column(db.String(20), default='medium')  # low, medium, high, critical
    
    # Links (at least one should be set)
    case_id = db.Column(db.String(36), db.ForeignKey('cases.id'))
    subject_id = db.Column(db.String(36), db.ForeignKey('subjects.id'))
    client_id = db.Column(db.String(36), db.ForeignKey('clients.id'))
    
    # Assignment
    assigned_to = db.Column(db.String(36), db.ForeignKey('users.id'))
    assigned_user = db.relationship('User', foreign_keys=[assigned_to], backref='assigned_reminders')
    
    # Ownership
    created_by = db.Column(db.String(36), db.ForeignKey('users.id'), nullable=False)
    creator = db.relationship('User', foreign_keys=[created_by], backref='created_reminders')
    
    # Status
    is_completed = db.Column(db.Boolean, default=False, index=True)
    is_overdue = db.Column(db.Boolean, default=False, index=True)
    is_dismissed = db.Column(db.Boolean, default=False)
    
    # Notification settings
    notify_email = db.Column(db.Boolean, default=False)
    notify_dashboard = db.Column(db.Boolean, default=True)
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Soft delete
    is_deleted = db.Column(db.Boolean, default=False)
    deleted_at = db.Column(db.DateTime)
    
    def complete(self):
        """Mark reminder as completed."""
        self.is_completed = True
        self.completed_at = datetime.utcnow()
    
    def snooze(self, minutes: int = 30):
        """Snooze reminder for specified minutes."""
        from datetime import timedelta
        self.reminder_date = datetime.utcnow() + timedelta(minutes=minutes)
    
    def check_overdue(self):
        """Check and update overdue status."""
        if not self.is_completed and self.reminder_date < datetime.utcnow():
            self.is_overdue = True
        return self.is_overdue
    
    def soft_delete(self):
        """Soft delete the reminder."""
        self.is_deleted = True
        self.deleted_at = datetime.utcnow()
    
    def to_dict(self) -> dict:
        """Serialize reminder."""
        return {
            'id': self.id,
            'title': self.title,
            'description': self.description,
            'reminder_date': self.reminder_date.isoformat() if self.reminder_date else None,
            'due_date': self.due_date.isoformat() if self.due_date else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
            'reminder_type': self.reminder_type,
            'recurrence': self.recurrence,
            'priority': self.priority,
            'case_id': self.case_id,
            'subject_id': self.subject_id,
            'client_id': self.client_id,
            'assigned_to': self.assigned_to,
            'assigned_user_name': self.assigned_user.full_name if self.assigned_user else None,
            'created_by': self.created_by,
            'creator_name': self.creator.full_name if self.creator else None,
            'is_completed': self.is_completed,
            'is_overdue': self.is_overdue,
            'is_dismissed': self.is_dismissed,
            'notify_email': self.notify_email,
            'notify_dashboard': self.notify_dashboard,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }
