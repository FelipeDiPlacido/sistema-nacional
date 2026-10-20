import hmac
import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from app.core.config import get_settings
from app.core.database import DbSession
from app.core.security import (
    CurrentAccount,
    create_access_token,
    dummy_password_hash,
    password_hasher,
)
from app.models import NGO, Certification, NationalResource, NGOCertification, User
from app.models.national import NGOCertificationStatus, NGOStatus, RiskLevel
from app.schemas import (
    AccountResponse,
    AllocationCreate,
    AllocationResponse,
    AvailabilityConstraint,
    AvailabilityStatus,
    CertificationResponse,
    CompetencyProfileResponse,
    CompetencyResponse,
    EnabledNGOResponse,
    LoginRequest,
    NationalResourceCreate,
    NationalResourceResponse,
    NGORegistration,
    NGORegistrationResponse,
    ResourceAvailabilityRequest,
    ResourceAvailabilityResponse,
    TokenResponse,
)
from app.services.allocation_service import close_allocation, create_allocation

router = APIRouter(prefix="/api/v1", tags=["Sistema Nacional"])
logger = logging.getLogger(__name__)


def resource_response(resource: NationalResource) -> NationalResourceResponse:
    return NationalResourceResponse(
        id=resource.id,
        code=resource.code,
        name=resource.name,
        category=resource.category,
        unit=resource.unit,
        total_quantity=resource.total_quantity,
        committed_quantity=resource.committed_quantity,
        available_quantity=resource.total_quantity - resource.committed_quantity,
        required_certification_code=resource.required_certification.code,
        required_certification_name=resource.required_certification.name,
    )


def require_resource_admin_token(token: str | None) -> None:
    configured_token = get_settings().resource_admin_token
    if configured_token is None:
        raise HTTPException(503, "La administración de inventario no está configurada")
    if token is None or not hmac.compare_digest(token, configured_token.get_secret_value()):
        raise HTTPException(401, "Se requiere una credencial de administración válida")


@router.post(
    "/ngos",
    response_model=NGORegistrationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Registrar una ONG y solicitar su habilitación",
)
def register_ngo(body: NGORegistration, db: DbSession):
    email = str(body.email).strip().lower()
    certifications = db.scalars(
        select(Certification).where(
            Certification.code.in_(set(body.certification_codes)),
            Certification.is_active.is_(True),
        )
    ).all()
    certifications_by_code = {item.code: item for item in certifications}
    if len(certifications_by_code) != len(set(body.certification_codes)):
        raise HTTPException(422, "Una o más certificaciones no existen o no están activas")

    user = User(email=email, password_hash=password_hasher.hash(body.password))
    ngo = NGO(name=body.name, status=NGOStatus.PENDING_REVIEW, owner=user)
    ngo.certifications = [
        NGOCertification(
            certification=certifications_by_code[code],
            status=NGOCertificationStatus.PENDING_REVIEW,
        )
        for code in sorted(set(body.certification_codes))
    ]
    db.add(ngo)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        logger.info("ngo_registration_conflict")
        raise HTTPException(409, "Ya existe una cuenta con ese correo electrónico") from exc
    db.refresh(ngo)
    return NGORegistrationResponse(
        id=ngo.id,
        name=ngo.name,
        email=email,
        status=NGOStatus(ngo.status),
        created_at=ngo.created_at,
    )


@router.post("/auth/login", response_model=TokenResponse, summary="Iniciar sesión con JWT")
def login(body: LoginRequest, db: DbSession):
    email = str(body.email).strip().lower()
    user = db.scalar(select(User).where(User.email == email))
    valid_password = password_hasher.verify(
        body.password, user.password_hash if user is not None else dummy_password_hash
    )
    if not valid_password or user is None or not user.is_active:
        logger.info("login_rejected")
        raise HTTPException(401, "Correo electrónico o contraseña incorrectos")
    settings = get_settings()
    return TokenResponse(
        access_token=create_access_token(user),
        expires_in=settings.access_token_expire_minutes * 60,
    )


@router.get("/auth/me", response_model=AccountResponse, summary="Consultar la cuenta autenticada")
def me(account: CurrentAccount):
    user, ngo = account
    return AccountResponse(
        id=user.id,
        email=user.email,
        ngo_id=ngo.id,
        ngo_name=ngo.name,
        ngo_status=NGOStatus(ngo.status),
    )


@router.get(
    "/certifications",
    response_model=list[CertificationResponse],
    summary="Consultar el catálogo de certificaciones",
)
def list_certifications(db: DbSession):
    return db.scalars(
        select(Certification).where(Certification.is_active.is_(True)).order_by(Certification.code)
    ).all()


@router.get(
    "/ngos/enabled",
    response_model=list[EnabledNGOResponse],
    summary="Consultar las ONG habilitadas",
)
def list_enabled_ngos(db: DbSession):
    ngos = db.scalars(select(NGO).where(NGO.status == NGOStatus.ENABLED).order_by(NGO.name)).all()
    return [
        EnabledNGOResponse(
            id=ngo.id,
            name=ngo.name,
            status=NGOStatus(ngo.status),
            created_at=ngo.created_at,
        )
        for ngo in ngos
    ]


@router.get(
    "/ngos/me/competencies",
    response_model=CompetencyProfileResponse,
    summary="Consultar el perfil estructurado de competencias de la ONG",
)
def my_competencies(account: CurrentAccount, db: DbSession):
    _, ngo = account
    certifications = db.scalars(
        select(NGOCertification)
        .options(joinedload(NGOCertification.certification))
        .where(NGOCertification.ngo_id == ngo.id)
        .order_by(NGOCertification.certification_id)
    ).all()
    enabled_ngo = ngo.status == NGOStatus.ENABLED
    competencies = [
        CompetencyResponse(
            code=item.certification.code,
            name=item.certification.name,
            status=NGOCertificationStatus(item.status),
            enabled=enabled_ngo and item.status == NGOCertificationStatus.VERIFIED,
            allowed_risk_levels=(
                [RiskLevel(level) for level in item.allowed_risk_levels]
                if enabled_ngo and item.status == NGOCertificationStatus.VERIFIED
                else []
            ),
        )
        for item in certifications
    ]
    return CompetencyProfileResponse(
        ngo_id=ngo.id,
        ngo_name=ngo.name,
        authorization_level=NGOStatus(ngo.status),
        competencies=competencies,
    )


@router.post(
    "/resources",
    response_model=NationalResourceResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Registrar disponibilidad inicial de un recurso",
)
def create_resource(
    body: NationalResourceCreate,
    db: DbSession,
    x_national_admin_token: str | None = Header(default=None),
):
    require_resource_admin_token(x_national_admin_token)
    certification = db.scalar(
        select(Certification).where(
            Certification.code == body.required_certification_code,
            Certification.is_active.is_(True),
        )
    )
    if certification is None:
        raise HTTPException(422, "La certificación requerida no existe o no está activa")

    resource = NationalResource(
        code=body.code,
        name=body.name,
        category=body.category,
        unit=body.unit,
        total_quantity=body.total_quantity,
        committed_quantity=0,
        required_certification=certification,
        is_active=True,
    )
    db.add(resource)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        logger.info("national_resource_registration_conflict")
        raise HTTPException(409, "Ya existe un recurso con ese código") from exc
    resource = db.scalar(
        select(NationalResource)
        .options(joinedload(NationalResource.required_certification))
        .where(NationalResource.id == resource.id)
    )
    return resource_response(resource)


@router.get(
    "/resources",
    response_model=list[NationalResourceResponse],
    summary="Consultar disponibilidad global de recursos",
)
def list_resources(db: DbSession):
    resources = db.scalars(
        select(NationalResource)
        .options(joinedload(NationalResource.required_certification))
        .where(NationalResource.is_active.is_(True))
        .order_by(NationalResource.code)
    ).all()
    return [resource_response(resource) for resource in resources]


@router.get(
    "/resources/{resource_code}",
    response_model=NationalResourceResponse,
    summary="Consultar disponibilidad de un recurso",
)
def get_resource(resource_code: str, db: DbSession):
    resource = db.scalar(
        select(NationalResource)
        .options(joinedload(NationalResource.required_certification))
        .where(
            NationalResource.code == resource_code,
            NationalResource.is_active.is_(True),
        )
    )
    if resource is None:
        raise HTTPException(404, "No se encontró un recurso activo con ese código")
    return resource_response(resource)


@router.post(
    "/resources/validate",
    response_model=ResourceAvailabilityResponse,
    summary="Evaluar disponibilidad y restricciones para la ONG autenticada",
)
def validate_resource_availability(
    body: ResourceAvailabilityRequest,
    account: CurrentAccount,
    db: DbSession,
):
    _, ngo = account
    resource = db.scalar(
        select(NationalResource)
        .options(joinedload(NationalResource.required_certification))
        .where(
            NationalResource.code == body.resource_code,
            NationalResource.is_active.is_(True),
        )
    )
    if resource is None:
        raise HTTPException(404, "No se encontró un recurso activo con ese código")

    certification = db.get(NGOCertification, (ngo.id, resource.required_certification_id))
    constraints: list[AvailabilityConstraint] = []
    if ngo.status != NGOStatus.ENABLED:
        constraints.append(AvailabilityConstraint.ONG_NOT_ENABLED)
    if certification is None or certification.status != NGOCertificationStatus.VERIFIED:
        constraints.append(AvailabilityConstraint.CERTIFICATION_NOT_VERIFIED)
    elif not certification.certification.is_active:
        constraints.append(AvailabilityConstraint.CERTIFICATION_INACTIVE)
    elif body.risk_level.value not in certification.allowed_risk_levels:
        constraints.append(AvailabilityConstraint.RISK_LEVEL_NOT_ALLOWED)

    available_quantity = resource.total_quantity - resource.committed_quantity
    authorized = not any(
        constraint
        in {
            AvailabilityConstraint.ONG_NOT_ENABLED,
            AvailabilityConstraint.CERTIFICATION_NOT_VERIFIED,
            AvailabilityConstraint.CERTIFICATION_INACTIVE,
            AvailabilityConstraint.RISK_LEVEL_NOT_ALLOWED,
        }
        for constraint in constraints
    )
    fulfillable_quantity = min(body.quantity, available_quantity) if authorized else 0
    if authorized and available_quantity < body.quantity:
        constraints.append(AvailabilityConstraint.INSUFFICIENT_STOCK)

    availability_status = (
        AvailabilityStatus.SUFFICIENT
        if fulfillable_quantity == body.quantity
        else AvailabilityStatus.PARTIAL
        if fulfillable_quantity > 0
        else AvailabilityStatus.NONE
    )
    return ResourceAvailabilityResponse(
        resource_code=resource.code,
        requested_quantity=body.quantity,
        available_quantity=available_quantity,
        fulfillable_quantity=fulfillable_quantity,
        risk_level=body.risk_level,
        authorization_level=NGOStatus(ngo.status),
        certification_status=(
            NGOCertificationStatus(certification.status) if certification is not None else None
        ),
        availability_status=availability_status,
        constraints=constraints,
    )


@router.post(
    "/allocations",
    response_model=AllocationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Comprometer recursos para una adjudicación",
)
def allocate_resources(
    body: AllocationCreate,
    response: Response,
    db: DbSession,
    idempotency_key: Annotated[uuid.UUID | None, Header(alias="Idempotency-Key")] = None,
    x_national_admin_token: str | None = Header(default=None),
):
    require_resource_admin_token(x_national_admin_token)
    if idempotency_key is None:
        raise HTTPException(422, "Falta el encabezado Idempotency-Key")
    allocation, created = create_allocation(db, body, idempotency_key)
    if not created:
        response.status_code = status.HTTP_200_OK
    return allocation


@router.post(
    "/allocations/{allocation_id}/close",
    response_model=AllocationResponse,
    summary="Cerrar el proyecto y liberar sus recursos comprometidos",
)
def close_project_allocation(
    allocation_id: uuid.UUID,
    db: DbSession,
    x_national_admin_token: Annotated[str | None, Header()] = None,
):
    require_resource_admin_token(x_national_admin_token)
    return close_allocation(db, allocation_id)
