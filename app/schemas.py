import uuid
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.national import NGOCertificationStatus, NGOStatus, RiskLevel


class NGORegistration(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=3, max_length=160)
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)
    certification_codes: list[str] = Field(default_factory=list, max_length=30)


class NGORegistrationResponse(BaseModel):
    id: uuid.UUID
    name: str
    email: EmailStr
    status: NGOStatus
    created_at: datetime


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class AccountResponse(BaseModel):
    id: uuid.UUID
    email: EmailStr
    ngo_id: uuid.UUID
    ngo_name: str
    ngo_status: NGOStatus


class CertificationResponse(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    description: str

    model_config = ConfigDict(from_attributes=True)


class EnabledNGOResponse(BaseModel):
    id: uuid.UUID
    name: str
    status: NGOStatus
    created_at: datetime


class CompetencyResponse(BaseModel):
    code: str
    name: str
    status: NGOCertificationStatus
    enabled: bool
    allowed_risk_levels: list[RiskLevel]


class CompetencyProfileResponse(BaseModel):
    ngo_id: uuid.UUID
    ngo_name: str
    authorization_level: NGOStatus
    competencies: list[CompetencyResponse]


class NationalResourceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    code: str = Field(pattern=r"^[A-Z0-9_]{2,64}$")
    name: str = Field(min_length=2, max_length=160)
    category: str = Field(min_length=2, max_length=64)
    unit: str = Field(min_length=1, max_length=80)
    total_quantity: int = Field(strict=True, ge=0, le=2_147_483_647)
    required_certification_code: str = Field(pattern=r"^[A-Z0-9_]{2,64}$")


class NationalResourceResponse(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    category: str
    unit: str
    total_quantity: int
    committed_quantity: int
    available_quantity: int
    required_certification_code: str
    required_certification_name: str


class AvailabilityConstraint(StrEnum):
    ONG_NOT_ENABLED = "ONG_NOT_ENABLED"
    CERTIFICATION_NOT_VERIFIED = "CERTIFICATION_NOT_VERIFIED"
    CERTIFICATION_INACTIVE = "CERTIFICATION_INACTIVE"
    RISK_LEVEL_NOT_ALLOWED = "RISK_LEVEL_NOT_ALLOWED"
    INSUFFICIENT_STOCK = "INSUFFICIENT_STOCK"


class AvailabilityStatus(StrEnum):
    SUFFICIENT = "SUFFICIENT"
    PARTIAL = "PARTIAL"
    NONE = "NONE"


class ResourceAvailabilityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    resource_code: str = Field(pattern=r"^[A-Z0-9_]{2,64}$")
    quantity: int = Field(strict=True, gt=0, le=2_147_483_647)
    risk_level: RiskLevel


class ResourceAvailabilityResponse(BaseModel):
    resource_code: str
    requested_quantity: int
    available_quantity: int
    fulfillable_quantity: int
    risk_level: RiskLevel
    authorization_level: NGOStatus
    certification_status: NGOCertificationStatus | None
    availability_status: AvailabilityStatus
    constraints: list[AvailabilityConstraint]


class AllocationItemCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    resource_code: str = Field(pattern=r"^[A-Z0-9_]{2,64}$")
    quantity: int = Field(strict=True, gt=0, le=2_147_483_647)


class AllocationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    project_reference: str = Field(min_length=1, max_length=160)
    ngo_id: uuid.UUID
    risk_level: RiskLevel
    items: list[AllocationItemCreate] = Field(min_length=1, max_length=100)


class AllocationItemResponse(BaseModel):
    resource_code: str
    resource_name: str
    unit: str
    quantity: int


class AllocationResponse(BaseModel):
    id: uuid.UUID
    request_key: uuid.UUID
    project_reference: str
    ngo_id: uuid.UUID
    risk_level: RiskLevel
    status: str
    created_at: datetime
    closed_at: datetime | None
    items: list[AllocationItemResponse]


class AllocationConstraintDetail(BaseModel):
    resource_code: str
    requested_quantity: int
    available_quantity: int
    constraints: list[AvailabilityConstraint]
