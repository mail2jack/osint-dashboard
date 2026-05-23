from functools import wraps
from flask import request, jsonify
from pydantic import BaseModel, Field, field_validator
from typing import Optional


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


def validate(schema_class):
    """Decorator that validates request JSON against a Pydantic schema."""
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            data = request.get_json(silent=True)
            if data is None:
                data = {}
            try:
                validated = schema_class(**data)
                request.validated_data = validated.model_dump(exclude_none=True)
            except Exception as e:
                errors = []
                if hasattr(e, 'errors'):
                    for err in e.errors():
                        field = ' → '.join(str(loc) for loc in err.get('loc', []))
                        msg = err.get('msg', 'Invalid value')
                        errors.append({'field': field, 'message': msg})
                else:
                    errors.append({'message': str(e)})
                return jsonify({'error': 'Validation failed', 'details': errors}), 400
            return f(*args, **kwargs)
        return wrapper
    return decorator
