import uuid
from datetime import datetime

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
