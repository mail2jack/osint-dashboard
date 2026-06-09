import logging
import re
from functools import wraps
from flask import request, jsonify, flash, redirect
from pydantic import BaseModel, Field, field_validator
from typing import Any

logger = logging.getLogger(__name__)


def validate_password_complexity(password: str) -> str:
    """Validate password meets minimum complexity requirements."""
    if not password or len(password) < 8:
        raise ValueError("Password must be at least 8 characters")
    if not re.search(r"[A-Z]", password):
        raise ValueError("Password must contain at least one uppercase letter")
    if not re.search(r"[a-z]", password):
        raise ValueError("Password must contain at least one lowercase letter")
    if not re.search(r"[0-9]", password):
        raise ValueError("Password must contain at least one digit")
    if not re.search(r"[^a-zA-Z0-9]", password):
        raise ValueError("Password must contain at least one special character")
    return password


# =============================================================================
# Existing schemas
# =============================================================================


class EmailCheckSchema(BaseModel):
    email: str = Field(min_length=1, description="Email address to check")


class KadasterLookupSchema(BaseModel):
    address_id: str | None = None
    query: str | None = None
    street: str | None = None
    number: str | None = None
    zipcode: str | None = None
    town: str | None = None


class PolitiebureauLookupSchema(BaseModel):
    address_id: str | None = None
    lat: float | None = None
    lon: float | None = None
    query: str | None = None


class RDWCheckSchema(BaseModel):
    kenteken: str = Field(min_length=1, description="License plate")
    subject_id: str | None = None


class RDWUpdateSchema(BaseModel):
    kenteken: str = Field(min_length=1, description="License plate")


class VesselLookupSchema(BaseModel):
    name: str | None = None
    imo: str | None = None
    mmsi: str | None = None
    eni: str | None = None
    subject_id: str | None = None


class VesselUpdateSubjectSchema(BaseModel):
    subject_id: str = Field(min_length=1)
    imo_number: str | None = None
    mmsi: str | None = None
    eni_number: str | None = None
    vessel_nationality: str | None = None
    vessel_data: dict | None = None


class VesselFindingSchema(BaseModel):
    case_id: str = Field(min_length=1)
    subject_id: str | None = None
    vessel_data: dict = Field(default_factory=dict)
    source: str = "vessel_lookup"
    source_url: str | None = None


class InterpolFindingSchema(BaseModel):
    case_id: str = Field(min_length=1)
    subject_id: str | None = None
    wanted_persons: list = Field(default_factory=list)
    missing_persons: list = Field(default_factory=list)
    opsporingsberichten: list = Field(default_factory=list)


class PhoneLookupSchema(BaseModel):
    phone: str = Field(min_length=1, description="Phone number to look up")


class CheckPolicieDataSchema(BaseModel):
    subject_id: str | None = None
    name: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    date_of_birth: str | None = None
    country: str = "NL"


class CheckExistingUrlsSchema(BaseModel):
    case_id: str | None = None
    urls: list = Field(default_factory=list)


class AddSocialAccountSchema(BaseModel):
    platform: str = Field(min_length=1, description="Social media platform name")
    username: str = Field(min_length=1, description="Username on the platform")
    url: str = ""
    account_id: str = ""


class SaveFindingAsSocialAccountSchema(BaseModel):
    finding_id: str | None = None
    subject_id: str | None = None
    url: str = ""
    platform: str = ""
    username: str = ""


class SaveUsernameFindingsSchema(BaseModel):
    results: list = Field(
        default_factory=list, description="List of {platform, url, username}"
    )
    case_id: str | None = None


class CreateSubjectFromUsernameSchema(BaseModel):
    username: str = ""
    platform: str = ""
    url: str = ""
    case_id: str | None = None


class ExtractSocialIdSchema(BaseModel):
    url: str = Field(min_length=1, description="URL to extract social IDs from")
    subject_id: str | None = None


# =============================================================================
# app.py OSINT route schemas
# =============================================================================


class AISummarizeSchema(BaseModel):
    query: str = ""
    tool: str = "unknown"
    findings: list = Field(default_factory=list)


class AIAnalyzeQuerySchema(BaseModel):
    query: str = ""


class AIEnrichProfileSchema(BaseModel):
    platform: str = "Unknown"
    username: str = ""
    info: dict = Field(default_factory=dict)


class PersonSearchSchema(BaseModel):
    name: str = ""


class EmailQuerySchema(BaseModel):
    email: str = ""


class IPQuerySchema(BaseModel):
    ip: str = ""


class DomainQuerySchema(BaseModel):
    domain: str = ""


class OpenKVKQuerySchema(BaseModel):
    query: str = ""


class WebcamQuerySchema(BaseModel):
    query: str = ""
    country: str = ""


class HIBPQuerySchema(BaseModel):
    email: str = ""


class UsernameQuerySchema(BaseModel):
    username: str = ""


class EmailStreamSchema(BaseModel):
    email: str = ""
    tags: list = Field(default_factory=lambda: ["all"])


class EmailHoleheSchema(BaseModel):
    email: str = ""


class EmailCombinedSchema(BaseModel):
    email: str = ""


class EmailCrossValidatedSchema(BaseModel):
    email: str = ""


class UsernameRapidAPISchema(BaseModel):
    username: str = ""


class GeneratePDFSchema(BaseModel):
    results: dict = Field(default_factory=dict)
    type: str = "unknown"
    query: str = "unknown"


# =============================================================================
# Auth route schemas
# =============================================================================


class SetPasswordSchema(BaseModel):
    password: str = Field(min_length=8)
    confirm_password: str = ""

    @field_validator("password")
    @classmethod
    def check_password_complexity(cls, v: str) -> str:
        return validate_password_complexity(v)


class CreateUserSchema(BaseModel):
    username: str = ""
    email: str = ""
    full_name: str = ""
    role: str = ""
    password: str | None = None
    generated_password: str | None = None
    send_email: Any = None
    send_sms: Any = None
    tenant_id: str | None = None

    @field_validator("password")
    @classmethod
    def check_password_complexity(cls, v: str | None) -> str | None:
        if v:
            return validate_password_complexity(v)
        return v


class EditUserSchema(BaseModel):
    full_name: str | None = None
    email: str | None = None
    role: str | None = None
    is_active: Any = None
    password: str | None = None
    tenant_id: str | None = None

    @field_validator("password")
    @classmethod
    def check_password_complexity(cls, v: str | None) -> str | None:
        if v:
            return validate_password_complexity(v)
        return v


class ChangePasswordSchema(BaseModel):
    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=8)
    confirm_password: str = Field(min_length=1)

    @field_validator("new_password")
    @classmethod
    def check_password_complexity(cls, v: str) -> str:
        return validate_password_complexity(v)


class LoginSchema(BaseModel):
    username: str = ""
    password: str = ""
    remember: Any = None


class Verify2FASchema(BaseModel):
    code: str = ""
    recovery_code: str = ""


class Setup2FASchema(BaseModel):
    code: str = ""


# =============================================================================
# CMS comment schemas
# =============================================================================


class CreateCommentSchema(BaseModel):
    content: str = ""
    comment_type: str = "note"
    is_pinned: Any = None
    case_id: str | None = None
    subject_id: str | None = None
    client_id: str | None = None
    financial_record_id: str | None = None


# =============================================================================
# Financial schemas
# =============================================================================


class CreateFinancialSchema(BaseModel):
    case_id: str = ""
    transaction_date: str = ""
    amount: Any = ""
    currency: str = "EUR"
    subject_id: str | None = None
    transaction_type: str | None = None
    source: str | None = None
    source_reference: str | None = None
    description: str | None = None
    counterparty_name: str | None = None
    counterparty_account: str | None = None
    counterparty_bank: str | None = None
    counterparty_country: str | None = None


class VerifyFinancialSchema(BaseModel):
    action: str = ""
    notes: str = ""


# =============================================================================
# Finding schemas
# =============================================================================


class CreateFindingSchema(BaseModel):
    case_id: str = ""
    title: str = ""
    content: str = ""
    subject_id: str | None = None
    source_url: str | None = None
    source_type: str | None = None
    reliability_score: Any = 5
    confidence_level: str | None = None
    finding_type: str | None = None
    tags: Any = None


# =============================================================================
# Settings schemas
# =============================================================================


class SaveSettingsSchema(BaseModel):
    settings: list = Field(default_factory=list)


# =============================================================================
# Case CRUD schemas
# =============================================================================


class CreateCaseSchema(BaseModel):
    title: str = ""
    client_id: str = ""
    description: str | None = None
    priority: str | None = None
    case_type: str | None = None
    jurisdiction: str | None = None
    tags: Any = None
    start_date: Any = None
    target_end_date: Any = None
    lead_investigator_id: Any = None
    assigned_to: Any = None
    subject_ids: list = Field(default_factory=list)


class EditCaseSchema(BaseModel):
    title: str | None = None
    description: str | None = None
    priority: str | None = None
    case_type: str | None = None
    jurisdiction: str | None = None
    tags: Any = None
    target_end_date: Any = None
    lead_investigator_id: Any = None
    status: str | None = None


# =============================================================================
# Case state schemas
# =============================================================================


class SetCaseParentSchema(BaseModel):
    parent_case_id: str | None = None


class TransitionCaseSchema(BaseModel):
    status: str = ""
    closure_reason: str | None = None
    reopened_reason: str | None = None


# =============================================================================
# Case subjects schemas
# =============================================================================


class AddSubjectToCaseSchema(BaseModel):
    subject_id: str = ""


class BulkAddSubjectsSchema(BaseModel):
    subject_ids: Any = None


# =============================================================================
# Client CRUD schemas
# =============================================================================


class CreateClientSchema(BaseModel):
    name: str = ""
    is_company: Any = None
    confirm_duplicate: Any = None
    contact_person: str | None = None
    contact_email: str | None = None
    contact_phone: str | None = None
    social_security_number: str | None = None
    bank_account: str | None = None
    date_of_birth: str | None = None
    place_of_birth: str | None = None
    contacts_data: Any = None
    addresses_data: Any = None
    contract_number: str | None = None
    contract_info: str | None = None
    vat_number: str | None = None
    financial_notes: str | None = None


class EditClientSchema(BaseModel):
    name: str | None = None
    is_company: Any = None
    contact_person: str | None = None
    contact_email: str | None = None
    contact_phone: str | None = None
    social_security_number: str | None = None
    bank_account: str | None = None
    date_of_birth: str | None = None
    place_of_birth: str | None = None
    contacts_data: Any = None
    addresses_data: Any = None
    contract_number: str | None = None
    contract_info: str | None = None
    vat_number: str | None = None
    financial_notes: str | None = None


# =============================================================================
# Subject CRUD schemas
# =============================================================================


class CreateSubjectSchema(BaseModel):
    name: str = ""
    subject_type: str = ""
    risk_score: Any = 0
    risk_factors: str | None = None
    notes: str | None = None
    registration_number: str | None = None
    legal_form: str | None = None
    asset_type: str | None = None
    estimated_value: str | None = None
    currency: str = "EUR"
    license_plate: str | None = None
    vin: str | None = None
    insurance_company: str | None = None
    brand: str | None = None
    vehicle_type: str | None = None
    imo_number: str | None = None
    mmsi: str | None = None
    eni_number: str | None = None
    vessel_nationality: str | None = None
    vessel_data: Any = None
    date_of_birth: str | None = None
    place_of_birth: str | None = None
    identification_number: str | None = None
    bank_account: str | None = None
    case_id: str | None = None
    addresses_data: Any = None
    contacts_data: Any = None
    confirm_duplicate: Any = None
    # RDW fields
    handelsbenaming: str | None = None
    voertuigsoort: str | None = None
    eerste_kleur: str | None = None
    tweede_kleur: str | None = None
    aantal_deuren: str | None = None
    aantal_zitplaatsen: str | None = None
    cilinderinhoud: str | None = None
    aantal_cilinders: str | None = None
    massa_ledig: str | None = None
    maximum_massa: str | None = None
    vervaldatum_apk: str | None = None
    wam_verzekerd: str | None = None
    taxi_indicator: str | None = None
    export_indicator: str | None = None
    europese_voertuigcategorie: str | None = None
    zuinigheidsclassificatie: str | None = None
    catalogusprijs: str | None = None
    datum_eerste_toelating: str | None = None
    type_: str | None = Field(None, alias="type")
    variant: str | None = None
    uitvoering: str | None = None
    typegoedkeuringsnummer: str | None = None
    wielbasis: str | None = None


class EditSubjectSchema(BaseModel):
    name: str | None = None
    subject_type: str | None = None
    risk_score: Any = None
    notes: str | None = None
    registration_number: str | None = None
    legal_form: str | None = None
    asset_type: str | None = None
    estimated_value: str | None = None
    currency: str | None = None
    license_plate: str | None = None
    vin: str | None = None
    insurance_company: str | None = None
    brand: str | None = None
    vehicle_type: str | None = None
    imo_number: str | None = None
    mmsi: str | None = None
    eni_number: str | None = None
    vessel_nationality: str | None = None
    vessel_data: Any = None
    date_of_birth: str | None = None
    place_of_birth: str | None = None
    identification_number: str | None = None
    bank_account: str | None = None
    addresses_data: Any = None
    contacts_data: Any = None
    # RDW fields
    handelsbenaming: str | None = None
    voertuigsoort: str | None = None
    eerste_kleur: str | None = None
    tweede_kleur: str | None = None
    aantal_deuren: str | None = None
    aantal_zitplaatsen: str | None = None
    cilinderinhoud: str | None = None
    aantal_cilinders: str | None = None
    massa_ledig: str | None = None
    maximum_massa: str | None = None
    vervaldatum_apk: str | None = None
    wam_verzekerd: str | None = None
    taxi_indicator: str | None = None
    export_indicator: str | None = None
    europese_voertuigcategorie: str | None = None
    zuinigheidsclassificatie: str | None = None
    catalogusprijs: str | None = None
    datum_eerste_toelating: str | None = None
    type_: str | None = Field(None, alias="type")
    variant: str | None = None
    uitvoering: str | None = None
    typegoedkeuringsnummer: str | None = None
    wielbasis: str | None = None


# =============================================================================
# Bulk delete schemas
# =============================================================================


class BulkDeleteSchema(BaseModel):
    ids: list[str] = Field(default_factory=list)


# =============================================================================
# Social IDs schema
# =============================================================================


class UpdateSocialIdsSchema(BaseModel):
    social_media_ids: dict = Field(default_factory=dict)
    model_config = {"extra": "forbid"}


# =============================================================================
# Comment update schema
# =============================================================================


class UpdateCommentSchema(BaseModel):
    content: str | None = None
    is_pinned: Any = None
    is_resolved: Any = None


# =============================================================================
# Subject relationship schemas
# =============================================================================


class AddRelationSchema(BaseModel):
    related_subject_id: str = ""
    relationship_type: str = "related"


class RemoveRelationSchema(BaseModel):
    related_subject_id: str = ""


# =============================================================================
# Subject face schemas
# =============================================================================


class SaveFaceEncodingSchema(BaseModel):
    encoding: list = Field(default_factory=list)


class CompareFacesSchema(BaseModel):
    encoding: list = Field(default_factory=list)
    threshold: float = 0.6
    limit: int = 20


# =============================================================================
# Screenshot schemas
# =============================================================================


class CaptureScreenshotSchema(BaseModel):
    url: str = ""
    title: str = ""


# =============================================================================
# OSINT search schemas
# =============================================================================


class FTSSearchSchema(BaseModel):
    query: str = Field(..., min_length=1, max_length=200)
    scope: str = Field(default="all")
    limit: int = Field(default=20, ge=1, le=100)


class StartOSINTSearchSchema(BaseModel):
    name: str = ""
    subject_id: str | None = None


class AddOSINTFindingsSchema(BaseModel):
    results: list = Field(default_factory=list)
    subject_id: str | None = None


# =============================================================================
# SpiderFoot schemas
# =============================================================================


class SpiderFootScanSchema(BaseModel):
    target: str = ""
    target_type: str = "DOMAIN_NAME"
    scan_name: str | None = None
    case_id: str | None = None
    subject_id: str | None = None
    profile: str | None = None
    use_case: str = "passive"


class SpiderFootImportSchema(BaseModel):
    element_types: list = Field(default_factory=list)
    min_length: int = 3
    limit: int = 1000


class SpiderFootSettingsSchema(BaseModel):
    url: str = "http://localhost:5001"
    username: str = "admin"
    password: str = ""


class SpiderFootTestSchema(BaseModel):
    url: str = "http://localhost:5001"
    username: str = "admin"
    password: str = ""


class SpiderFootScanSubjectSchema(BaseModel):
    profile: str = "basic"
    use_case: str = "passive"
    case_id: str | None = None


# =============================================================================
# Template schemas
# =============================================================================


class CreateTemplateSchema(BaseModel):
    name: str = ""
    description: str | None = None
    template_type: str = "report"
    content: str = ""
    category: str | None = None
    is_default: Any = None


class EditTemplateSchema(BaseModel):
    name: str = ""
    description: str | None = None
    template_type: str = "report"
    content: str = ""
    category: str | None = None
    is_default: Any = None


class RenderPreviewSchema(BaseModel):
    template_id: str = ""
    case_id: str | None = None
    conclusion: str = ""
    recommendation: str = ""
    classification: str = "Confidential"


# =============================================================================
# Reminder schemas
# =============================================================================


class CreateReminderSchema(BaseModel):
    title: str = ""
    description: str = ""
    reminder_date: str | None = None
    due_date: str | None = None
    reminder_type: str = "manual"
    recurrence: str = "none"
    priority: str = "medium"
    case_id: str | None = None
    subject_id: str | None = None
    client_id: str | None = None
    assigned_to: Any = None
    notify_email: Any = None
    notify_dashboard: Any = None


class EditReminderSchema(BaseModel):
    title: str | None = None
    description: str | None = None
    reminder_date: str | None = None
    due_date: str | None = None
    reminder_type: str | None = None
    recurrence: str | None = None
    priority: str | None = None
    assigned_to: Any = None
    notify_email: Any = None
    notify_dashboard: Any = None


# =============================================================================
# Phone service schemas
# =============================================================================


class PhoneNumberSchema(BaseModel):
    phone: str = Field(default="", min_length=1)


class PhoneLookupAllSchema(BaseModel):
    phone: str = Field(default="", min_length=1)
    services: list[str] | None = None


# =============================================================================
# Document upload schemas
# =============================================================================


class DocumentUploadSchema(BaseModel):
    document_type: str = Field(default="evidence", max_length=50)
    description: str = Field(default="", max_length=2000)
    classification: str = Field(default="confidential", max_length=50)


class ScreenshotUploadSchema(BaseModel):
    url: str = Field(default="", max_length=2000)


class GenerateReportSchema(BaseModel):
    template_id: str | None = None
    conclusion: str = Field(default="", max_length=10000)
    recommendation: str = Field(default="", max_length=10000)
    classification: str = Field(default="Confidential", max_length=50)


# =============================================================================
# Validate decorator
# =============================================================================


def validate(schema_class):
    """Decorator that validates request JSON (or form data) against a Pydantic schema."""

    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            data = request.get_json(silent=True)
            if data is None:
                data = request.form.to_dict() if request.form else {}
            try:
                validated = schema_class(**data)
                request.validated_data = validated.model_dump(exclude_none=True)
            except Exception as e:
                errors = []
                messages = []
                if hasattr(e, "errors"):
                    for err in e.errors():
                        field = " \u2192 ".join(str(loc) for loc in err.get("loc", []))
                        msg = err.get("msg", "Invalid value")
                        errors.append({"field": field, "message": msg})
                        messages.append(f"{field}: {msg}" if field else msg)
                else:
                    logger.exception("Unexpected validation error")
                    errors.append({"message": "Validation error"})
                    messages.append("Validation error")
                if request.is_json:
                    return jsonify(
                        {"error": "Validation failed", "details": errors}
                    ), 400
                flash(" | ".join(messages), "danger")
                return redirect(request.path)
            return f(*args, **kwargs)

        return wrapper

    return decorator
