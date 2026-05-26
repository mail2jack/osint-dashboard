from functools import wraps
from flask import request, jsonify
from pydantic import BaseModel, Field, field_validator
from typing import Optional, Any


# =============================================================================
# Existing schemas
# =============================================================================

class EmailCheckSchema(BaseModel):
    email: str = Field(min_length=1, description="Email address to check")


class KadasterLookupSchema(BaseModel):
    query: Optional[str] = None
    street: Optional[str] = None
    number: Optional[str] = None
    zipcode: Optional[str] = None
    town: Optional[str] = None


class PolitiebureauLookupSchema(BaseModel):
    address_id: Optional[int] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    query: Optional[str] = None


class RDWCheckSchema(BaseModel):
    kenteken: str = Field(min_length=1, description="License plate")
    subject_id: Optional[str] = None


class RDWUpdateSchema(BaseModel):
    kenteken: str = Field(min_length=1, description="License plate")


class VesselLookupSchema(BaseModel):
    name: Optional[str] = None
    imo: Optional[str] = None
    mmsi: Optional[str] = None
    eni: Optional[str] = None
    subject_id: Optional[str] = None


class VesselUpdateSubjectSchema(BaseModel):
    subject_id: str = Field(min_length=1)
    imo_number: Optional[str] = None
    mmsi: Optional[str] = None
    eni_number: Optional[str] = None
    vessel_nationality: Optional[str] = None
    vessel_data: Optional[dict] = None


class VesselFindingSchema(BaseModel):
    case_id: str = Field(min_length=1)
    subject_id: Optional[str] = None
    vessel_data: dict = Field(default_factory=dict)
    source: str = 'vessel_lookup'
    source_url: Optional[str] = None


class InterpolFindingSchema(BaseModel):
    case_id: str = Field(min_length=1)
    subject_id: Optional[str] = None
    wanted_persons: list = Field(default_factory=list)
    missing_persons: list = Field(default_factory=list)
    opsporingsberichten: list = Field(default_factory=list)


class PhoneLookupSchema(BaseModel):
    phone: str = Field(min_length=1, description="Phone number to look up")


class CheckPolicieDataSchema(BaseModel):
    subject_id: Optional[str] = None
    name: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    date_of_birth: Optional[str] = None
    country: str = 'NL'


class CheckExistingUrlsSchema(BaseModel):
    case_id: Optional[str] = None
    urls: list = Field(default_factory=list)


class AddSocialAccountSchema(BaseModel):
    platform: str = Field(min_length=1, description="Social media platform name")
    username: str = Field(min_length=1, description="Username on the platform")
    url: str = ''
    account_id: str = ''


class SaveFindingAsSocialAccountSchema(BaseModel):
    finding_id: Optional[str] = None
    subject_id: Optional[str] = None
    url: str = ''
    platform: str = ''
    username: str = ''


class SaveUsernameFindingsSchema(BaseModel):
    results: list = Field(default_factory=list, description="List of {platform, url, username}")
    case_id: Optional[str] = None


class CreateSubjectFromUsernameSchema(BaseModel):
    username: str = ''
    platform: str = ''
    url: str = ''
    case_id: Optional[str] = None


class ExtractSocialIdSchema(BaseModel):
    url: str = Field(min_length=1, description="URL to extract social IDs from")
    subject_id: Optional[str] = None


# =============================================================================
# app.py OSINT route schemas
# =============================================================================

class AISummarizeSchema(BaseModel):
    query: str = ''
    tool: str = 'unknown'
    findings: list = Field(default_factory=list)


class AIAnalyzeQuerySchema(BaseModel):
    query: str = ''


class AIEnrichProfileSchema(BaseModel):
    platform: str = 'Unknown'
    username: str = ''
    info: dict = Field(default_factory=dict)


class PersonSearchSchema(BaseModel):
    name: str = ''


class EmailQuerySchema(BaseModel):
    email: str = ''


class IPQuerySchema(BaseModel):
    ip: str = ''


class DomainQuerySchema(BaseModel):
    domain: str = ''


class OpenKVKQuerySchema(BaseModel):
    query: str = ''


class WebcamQuerySchema(BaseModel):
    query: str = ''
    country: str = ''


class HIBPQuerySchema(BaseModel):
    email: str = ''


class UsernameQuerySchema(BaseModel):
    username: str = ''


class EmailStreamSchema(BaseModel):
    email: str = ''
    tags: list = Field(default_factory=lambda: ['all'])


class EmailHoleheSchema(BaseModel):
    email: str = ''


class EmailCombinedSchema(BaseModel):
    email: str = ''


class EmailCrossValidatedSchema(BaseModel):
    email: str = ''


class UsernameRapidAPISchema(BaseModel):
    username: str = ''


class GeneratePDFSchema(BaseModel):
    results: dict = Field(default_factory=dict)
    type: str = 'unknown'
    query: str = 'unknown'


# =============================================================================
# Auth route schemas
# =============================================================================

class SetPasswordSchema(BaseModel):
    password: str = ''
    confirm_password: str = ''


class CreateUserSchema(BaseModel):
    username: str = ''
    email: str = ''
    full_name: str = ''
    role: str = ''
    password: Optional[str] = None
    generated_password: Optional[str] = None
    send_email: Any = None
    send_sms: Any = None


class EditUserSchema(BaseModel):
    full_name: Optional[str] = None
    email: Optional[str] = None
    role: Optional[str] = None
    is_active: Any = None
    password: Optional[str] = None


class ChangePasswordSchema(BaseModel):
    current_password: str = ''
    new_password: str = ''
    confirm_password: str = ''


class LoginSchema(BaseModel):
    username: str = ''
    password: str = ''
    remember: Any = None


class Verify2FASchema(BaseModel):
    code: str = ''
    recovery_code: str = ''


class Setup2FASchema(BaseModel):
    code: str = ''


# =============================================================================
# CMS comment schemas
# =============================================================================

class CreateCommentSchema(BaseModel):
    content: str = ''
    comment_type: str = 'note'
    is_pinned: Any = None
    case_id: Optional[str] = None
    subject_id: Optional[str] = None
    client_id: Optional[str] = None
    financial_record_id: Optional[str] = None


# =============================================================================
# Financial schemas
# =============================================================================

class CreateFinancialSchema(BaseModel):
    case_id: str = ''
    transaction_date: str = ''
    amount: Any = ''
    currency: str = 'EUR'
    subject_id: Optional[str] = None
    transaction_type: Optional[str] = None
    source: Optional[str] = None
    source_reference: Optional[str] = None
    description: Optional[str] = None
    counterparty_name: Optional[str] = None
    counterparty_account: Optional[str] = None
    counterparty_bank: Optional[str] = None
    counterparty_country: Optional[str] = None


class VerifyFinancialSchema(BaseModel):
    action: str = ''
    notes: str = ''


# =============================================================================
# Finding schemas
# =============================================================================

class CreateFindingSchema(BaseModel):
    case_id: str = ''
    title: str = ''
    content: str = ''
    subject_id: Optional[str] = None
    source_url: Optional[str] = None
    source_type: Optional[str] = None
    reliability_score: int = 5
    confidence_level: Optional[str] = None
    finding_type: Optional[str] = None
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
    title: str = ''
    client_id: str = ''
    description: Optional[str] = None
    priority: Optional[str] = None
    case_type: Optional[str] = None
    jurisdiction: Optional[str] = None
    tags: Any = None
    start_date: Any = None
    target_end_date: Any = None
    lead_investigator_id: Any = None
    assigned_to: Any = None
    subject_ids: list = Field(default_factory=list)


class EditCaseSchema(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    priority: Optional[str] = None
    case_type: Optional[str] = None
    jurisdiction: Optional[str] = None
    tags: Any = None
    target_end_date: Any = None
    lead_investigator_id: Any = None
    status: Optional[str] = None


# =============================================================================
# Case state schemas
# =============================================================================

class SetCaseParentSchema(BaseModel):
    parent_case_id: Optional[str] = None


class TransitionCaseSchema(BaseModel):
    status: str = ''
    closure_reason: Optional[str] = None
    reopened_reason: Optional[str] = None


# =============================================================================
# Case subjects schemas
# =============================================================================

class AddSubjectToCaseSchema(BaseModel):
    subject_id: str = ''


class BulkAddSubjectsSchema(BaseModel):
    subject_ids: Any = None


# =============================================================================
# Client CRUD schemas
# =============================================================================

class CreateClientSchema(BaseModel):
    name: str = ''
    is_company: Any = None
    confirm_duplicate: Any = None
    contact_person: Optional[str] = None
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    social_security_number: Optional[str] = None
    bank_account: Optional[str] = None
    date_of_birth: Optional[str] = None
    place_of_birth: Optional[str] = None
    contacts_data: Any = None
    addresses_data: Any = None
    contract_number: Optional[str] = None
    contract_info: Optional[str] = None
    vat_number: Optional[str] = None
    financial_notes: Optional[str] = None


class EditClientSchema(BaseModel):
    name: Optional[str] = None
    is_company: Any = None
    contact_person: Optional[str] = None
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    social_security_number: Optional[str] = None
    bank_account: Optional[str] = None
    date_of_birth: Optional[str] = None
    place_of_birth: Optional[str] = None
    contacts_data: Any = None
    addresses_data: Any = None
    contract_number: Optional[str] = None
    contract_info: Optional[str] = None
    vat_number: Optional[str] = None
    financial_notes: Optional[str] = None


# =============================================================================
# Subject CRUD schemas
# =============================================================================

class CreateSubjectSchema(BaseModel):
    name: str = ''
    subject_type: str = ''
    risk_score: int = 0
    risk_factors: Optional[str] = None
    notes: Optional[str] = None
    registration_number: Optional[str] = None
    legal_form: Optional[str] = None
    asset_type: Optional[str] = None
    estimated_value: Optional[str] = None
    currency: str = 'EUR'
    license_plate: Optional[str] = None
    vin: Optional[str] = None
    insurance_company: Optional[str] = None
    brand: Optional[str] = None
    vehicle_type: Optional[str] = None
    imo_number: Optional[str] = None
    mmsi: Optional[str] = None
    eni_number: Optional[str] = None
    vessel_nationality: Optional[str] = None
    vessel_data: Any = None
    date_of_birth: Optional[str] = None
    place_of_birth: Optional[str] = None
    identification_number: Optional[str] = None
    case_id: Optional[str] = None
    addresses_data: Any = None
    contacts_data: Any = None
    confirm_duplicate: Any = None
    # RDW fields
    handelsbenaming: Optional[str] = None
    voertuigsoort: Optional[str] = None
    eerste_kleur: Optional[str] = None
    tweede_kleur: Optional[str] = None
    aantal_deuren: Optional[str] = None
    aantal_zitplaatsen: Optional[str] = None
    cilinderinhoud: Optional[str] = None
    aantal_cilinders: Optional[str] = None
    massa_ledig: Optional[str] = None
    maximum_massa: Optional[str] = None
    vervaldatum_apk: Optional[str] = None
    wam_verzekerd: Optional[str] = None
    taxi_indicator: Optional[str] = None
    export_indicator: Optional[str] = None
    europese_voertuigcategorie: Optional[str] = None
    zuinigheidsclassificatie: Optional[str] = None
    catalogusprijs: Optional[str] = None
    datum_eerste_toelating: Optional[str] = None
    type_: Optional[str] = Field(None, alias='type')
    variant: Optional[str] = None
    uitvoering: Optional[str] = None
    typegoedkeuringsnummer: Optional[str] = None
    wielbasis: Optional[str] = None


class EditSubjectSchema(BaseModel):
    name: Optional[str] = None
    subject_type: Optional[str] = None
    risk_score: Any = None
    notes: Optional[str] = None
    registration_number: Optional[str] = None
    legal_form: Optional[str] = None
    asset_type: Optional[str] = None
    estimated_value: Optional[str] = None
    currency: Optional[str] = None
    license_plate: Optional[str] = None
    vin: Optional[str] = None
    insurance_company: Optional[str] = None
    brand: Optional[str] = None
    vehicle_type: Optional[str] = None
    imo_number: Optional[str] = None
    mmsi: Optional[str] = None
    eni_number: Optional[str] = None
    vessel_nationality: Optional[str] = None
    vessel_data: Any = None
    date_of_birth: Optional[str] = None
    place_of_birth: Optional[str] = None
    identification_number: Optional[str] = None
    addresses_data: Any = None
    contacts_data: Any = None
    # RDW fields
    handelsbenaming: Optional[str] = None
    voertuigsoort: Optional[str] = None
    eerste_kleur: Optional[str] = None
    tweede_kleur: Optional[str] = None
    aantal_deuren: Optional[str] = None
    aantal_zitplaatsen: Optional[str] = None
    cilinderinhoud: Optional[str] = None
    aantal_cilinders: Optional[str] = None
    massa_ledig: Optional[str] = None
    maximum_massa: Optional[str] = None
    vervaldatum_apk: Optional[str] = None
    wam_verzekerd: Optional[str] = None
    taxi_indicator: Optional[str] = None
    export_indicator: Optional[str] = None
    europese_voertuigcategorie: Optional[str] = None
    zuinigheidsclassificatie: Optional[str] = None
    catalogusprijs: Optional[str] = None
    datum_eerste_toelating: Optional[str] = None
    type_: Optional[str] = Field(None, alias='type')
    variant: Optional[str] = None
    uitvoering: Optional[str] = None
    typegoedkeuringsnummer: Optional[str] = None
    wielbasis: Optional[str] = None


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
    content: Optional[str] = None
    is_pinned: Any = None
    is_resolved: Any = None


# =============================================================================
# Subject relationship schemas
# =============================================================================

class AddRelationSchema(BaseModel):
    related_subject_id: str = ''
    relationship_type: str = 'related'


class RemoveRelationSchema(BaseModel):
    related_subject_id: str = ''


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
    url: str = ''
    title: str = ''


# =============================================================================
# OSINT search schemas
# =============================================================================

class FTSSearchSchema(BaseModel):
    query: str = Field(..., min_length=1, max_length=200)
    scope: str = Field(default='all')
    limit: int = Field(default=20, ge=1, le=100)


class StartOSINTSearchSchema(BaseModel):
    name: str = ''


class AddOSINTFindingsSchema(BaseModel):
    results: list = Field(default_factory=list)
    subject_id: Optional[str] = None


# =============================================================================
# SpiderFoot schemas
# =============================================================================

class SpiderFootScanSchema(BaseModel):
    target: str = ''
    target_type: str = 'DOMAIN_NAME'
    scan_name: Optional[str] = None
    case_id: Optional[str] = None
    subject_id: Optional[str] = None
    profile: Optional[str] = None
    use_case: str = 'passive'


class SpiderFootImportSchema(BaseModel):
    element_types: list = Field(default_factory=list)
    min_length: int = 3
    limit: int = 1000


class SpiderFootSettingsSchema(BaseModel):
    url: str = 'http://localhost:5001'
    username: str = 'admin'
    password: str = ''


class SpiderFootTestSchema(BaseModel):
    url: str = 'http://localhost:5001'
    username: str = 'admin'
    password: str = ''


class SpiderFootScanSubjectSchema(BaseModel):
    profile: str = 'basic'
    use_case: str = 'passive'
    case_id: Optional[str] = None


# =============================================================================
# Template schemas
# =============================================================================

class CreateTemplateSchema(BaseModel):
    name: str = ''
    description: Optional[str] = None
    template_type: str = 'report'
    content: str = ''
    category: Optional[str] = None
    is_default: Any = None


class EditTemplateSchema(BaseModel):
    name: str = ''
    description: Optional[str] = None
    template_type: str = 'report'
    content: str = ''
    category: Optional[str] = None
    is_default: Any = None


class RenderPreviewSchema(BaseModel):
    template_id: str = ''
    case_id: Optional[str] = None
    conclusion: str = ''
    recommendation: str = ''
    classification: str = 'Confidential'


class GenerateReportSchema(BaseModel):
    template_id: str = ''
    conclusion: str = ''
    recommendation: str = ''
    classification: str = 'Confidential'


# =============================================================================
# Reminder schemas
# =============================================================================

class CreateReminderSchema(BaseModel):
    title: str = ''
    description: str = ''
    reminder_date: Optional[str] = None
    due_date: Optional[str] = None
    reminder_type: str = 'manual'
    recurrence: str = 'none'
    priority: str = 'medium'
    case_id: Optional[str] = None
    subject_id: Optional[str] = None
    client_id: Optional[str] = None
    assigned_to: Any = None
    notify_email: Any = None
    notify_dashboard: Any = None


class EditReminderSchema(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    reminder_date: Optional[str] = None
    due_date: Optional[str] = None
    reminder_type: Optional[str] = None
    recurrence: Optional[str] = None
    priority: Optional[str] = None
    assigned_to: Any = None
    notify_email: Any = None
    notify_dashboard: Any = None


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
                if hasattr(e, 'errors'):
                    for err in e.errors():
                        field = ' \u2192 '.join(str(loc) for loc in err.get('loc', []))
                        msg = err.get('msg', 'Invalid value')
                        errors.append({'field': field, 'message': msg})
                else:
                    errors.append({'message': str(e)})
                return jsonify({'error': 'Validation failed', 'details': errors}), 400
            return f(*args, **kwargs)
        return wrapper
    return decorator
