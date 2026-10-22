import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from app.core.config import get_settings
from app.core.database import DbSession
from app.core.rate_limit import RATE_LIMIT_RESPONSE
from app.core.security import (
    AdminUser,
    CoordinatorUser,
    CurrentAccount,
    CurrentUser,
    create_access_token,
    dummy_password_hash,
    password_hasher,
)
from app.models import NGO, Certification, NationalResource, NGOCertification, User
from app.models.national import NGOCertificationStatus, NGOStatus, RiskLevel, UserRole
from app.schemas import (
    AccountResponse,
    AllocationCreate,
    AllocationResponse,
    AvailabilityConstraint,
    AvailabilityStatus,
    CertificationResponse,
    CertificationReview,
    CompetencyProfileResponse,
    CompetencyResponse,
    CoordinatorAvailabilityRequest,
    EnabledNGOResponse,
    LoginRequest,
    NationalResourceCreate,
    NationalResourceResponse,
    NationalResourceUpdate,
    NGORegistration,
    NGORegistrationResponse,
    NGOStatusUpdate,
    ResourceAvailabilityRequest,
    ResourceAvailabilityResponse,
    ResourceOwnerUpdate,
    TokenResponse,
)
from app.services.allocation_service import (
    close_allocation,
    create_allocation,
    get_allocation,
    list_allocations,
)

router = APIRouter(
    prefix="/api/v1", tags=["Sistema Nacional"], responses={429: RATE_LIMIT_RESPONSE}
)
logger = logging.getLogger(__name__)


def require_inventory_owner(user: User, ngo_id: uuid.UUID | None) -> None:
    if user.role == UserRole.ADMIN:
        return
    if user.role != UserRole.NGO or user.ngo is None or user.ngo.id != ngo_id:
        raise HTTPException(
            403, "Solo la ONG propietaria o administración puede editar el inventario"
        )


def allocation_scope(user: User) -> uuid.UUID | None:
    if user.role in (UserRole.ADMIN, UserRole.COORDINATOR):
        return None
    if user.role == UserRole.NGO and user.ngo is not None:
        return user.ngo.id
    raise HTTPException(403, "La cuenta no tiene permisos sobre adjudicaciones")


def resource_response(resource: NationalResource) -> NationalResourceResponse:
    return NationalResourceResponse(
        id=resource.id,
        ngo_id=resource.ngo_id,
        is_active=resource.is_active,
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
def me(user: CurrentUser):
    ngo = user.ngo
    return AccountResponse(
        id=user.id,
        email=user.email,
        role=UserRole(user.role),
        ngo_id=ngo.id if ngo else None,
        ngo_name=ngo.name if ngo else None,
        ngo_status=NGOStatus(ngo.status) if ngo else None,
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
    ngos = db.scalars(
        select(NGO)
        .join(NGO.owner)
        .where(NGO.status == NGOStatus.ENABLED, User.is_active.is_(True))
        .order_by(NGO.name)
    ).all()
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
    return competency_profile(ngo, db)


def competency_profile(ngo: NGO, db) -> CompetencyProfileResponse:
    certifications = db.scalars(
        select(NGOCertification)
        .options(joinedload(NGOCertification.certification))
        .where(NGOCertification.ngo_id == ngo.id)
        .order_by(NGOCertification.certification_id)
    ).all()
    enabled_ngo = ngo.status == NGOStatus.ENABLED and ngo.owner.is_active
    competencies = [
        CompetencyResponse(
            code=item.certification.code,
            name=item.certification.name,
            status=NGOCertificationStatus(item.status),
            enabled=enabled_ngo
            and item.status == NGOCertificationStatus.VERIFIED
            and item.certification.is_active,
            allowed_risk_levels=(
                [RiskLevel(level) for level in item.allowed_risk_levels]
                if enabled_ngo
                and item.status == NGOCertificationStatus.VERIFIED
                and item.certification.is_active
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
    user: CurrentUser,
    db: DbSession,
):
    require_inventory_owner(user, body.ngo_id)
    if db.get(NGO, body.ngo_id) is None:
        raise HTTPException(404, "No se encontró la ONG")
    certification = db.scalar(
        select(Certification).where(
            Certification.code == body.required_certification_code,
            Certification.is_active.is_(True),
        )
    )
    if certification is None:
        raise HTTPException(422, "La certificación requerida no existe o no está activa")

    resource = NationalResource(
        ngo_id=body.ngo_id,
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
def list_resources(db: DbSession, ngo_id: uuid.UUID | None = None):
    resources = db.scalars(
        select(NationalResource)
        .options(joinedload(NationalResource.required_certification))
        .where(NationalResource.is_active.is_(True))
        .where(NationalResource.ngo_id == ngo_id if ngo_id else True)
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
    return evaluate_availability(body, ngo, db)


def evaluate_availability(body, ngo: NGO, db) -> ResourceAvailabilityResponse:
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
    if resource.ngo_id != ngo.id:
        constraints.append(AvailabilityConstraint.RESOURCE_NOT_OWNED)
    if ngo.status != NGOStatus.ENABLED or not ngo.owner.is_active:
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
            AvailabilityConstraint.RESOURCE_NOT_OWNED,
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
        allowed_risk_levels=(
            [RiskLevel(level) for level in certification.allowed_risk_levels]
            if certification
            and certification.status == NGOCertificationStatus.VERIFIED
            and resource.required_certification.is_active
            and ngo.status == NGOStatus.ENABLED
            and ngo.owner.is_active
            else []
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
    user: CoordinatorUser,
    response: Response,
    db: DbSession,
    idempotency_key: Annotated[uuid.UUID | None, Header(alias="Idempotency-Key")] = None,
):
    if idempotency_key is None:
        raise HTTPException(422, "Falta el encabezado Idempotency-Key")
    allocation, created = create_allocation(db, body, idempotency_key)
    if not created:
        response.status_code = status.HTTP_200_OK
    return allocation


@router.post(
    "/allocations/{allocation_id}/close",
    response_model=AllocationResponse,
    summary="Finalizar la participación de una ONG y liberar los recursos adjudicados",
)
def close_project_allocation(
    allocation_id: uuid.UUID,
    db: DbSession,
    user: CurrentUser,
):
    return close_allocation(db, allocation_id, allocation_scope(user))


@router.get("/ngos", response_model=list[EnabledNGOResponse], summary="Listar ONG para revisión")
def list_ngos(
    user: AdminUser,
    db: DbSession,
    status: NGOStatus | None = None,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=100),
):
    query = select(NGO).order_by(NGO.created_at, NGO.id).offset(offset).limit(limit)
    if status is not None:
        query = query.where(NGO.status == status)
    return [
        EnabledNGOResponse(
            id=ngo.id, name=ngo.name, status=NGOStatus(ngo.status), created_at=ngo.created_at
        )
        for ngo in db.scalars(query)
    ]


@router.get(
    "/ngos/{ngo_id}/competencies",
    response_model=CompetencyProfileResponse,
    summary="Consultar competencias de una ONG desde coordinación",
)
def ngo_competencies(ngo_id: uuid.UUID, user: CoordinatorUser, db: DbSession):
    ngo = db.get(NGO, ngo_id)
    if ngo is None:
        raise HTTPException(404, "No se encontró la ONG")
    return competency_profile(ngo, db)


@router.patch(
    "/ngos/{ngo_id}/status",
    response_model=CompetencyProfileResponse,
    summary="Habilitar, suspender o devolver una ONG a revisión",
)
def update_ngo_status(ngo_id: uuid.UUID, body: NGOStatusUpdate, user: AdminUser, db: DbSession):
    ngo = db.scalar(select(NGO).where(NGO.id == ngo_id).with_for_update())
    if ngo is None:
        raise HTTPException(404, "No se encontró la ONG")
    ngo.status = body.status
    db.commit()
    logger.info("ngo_status_updated", extra={"ngo_id": str(ngo.id), "actor_id": str(user.id)})
    return competency_profile(ngo, db)


@router.put(
    "/ngos/{ngo_id}/certifications/{certification_code}",
    response_model=CompetencyProfileResponse,
    summary="Verificar una certificación y establecer los riesgos permitidos",
)
def review_certification(
    ngo_id: uuid.UUID,
    certification_code: str,
    body: CertificationReview,
    user: AdminUser,
    db: DbSession,
):
    # Sharing this NGO lock with allocation serializes reviews and new commitments.
    ngo = db.scalar(select(NGO).where(NGO.id == ngo_id).with_for_update())
    if ngo is None:
        raise HTTPException(404, "No se encontró la ONG")
    certification = db.scalar(select(Certification).where(Certification.code == certification_code))
    if certification is None:
        raise HTTPException(404, "No se encontró la certificación")
    if body.status == NGOCertificationStatus.VERIFIED and not certification.is_active:
        raise HTTPException(409, "No se puede verificar una certificación inactiva")
    record = db.get(NGOCertification, (ngo_id, certification.id))
    if record is None:
        record = NGOCertification(ngo=ngo, certification=certification)
        db.add(record)
    record.status = body.status
    record.allowed_risk_levels = [level.value for level in body.allowed_risk_levels]
    db.commit()
    logger.info(
        "ngo_certification_reviewed", extra={"ngo_id": str(ngo.id), "actor_id": str(user.id)}
    )
    return competency_profile(ngo, db)


@router.put(
    "/resources/{resource_code}",
    response_model=NationalResourceResponse,
    summary="Actualizar cantidad total y estado del inventario propio",
)
def update_resource(
    resource_code: str,
    body: NationalResourceUpdate,
    user: CurrentUser,
    db: DbSession,
):
    resource = db.scalar(
        select(NationalResource).where(NationalResource.code == resource_code).with_for_update()
    )
    if resource is None:
        raise HTTPException(404, "No se encontró el recurso")
    require_inventory_owner(user, resource.ngo_id)
    if body.total_quantity < resource.committed_quantity:
        raise HTTPException(409, "El total no puede ser menor que la cantidad comprometida")
    resource.total_quantity = body.total_quantity
    resource.is_active = body.is_active
    db.commit()
    return resource_response(resource)


@router.patch(
    "/resources/{resource_code}/owner",
    response_model=NationalResourceResponse,
    summary="Asignar propietario al inventario anterior a la migración",
)
def assign_legacy_owner(
    resource_code: str,
    body: ResourceOwnerUpdate,
    user: AdminUser,
    db: DbSession,
):
    if db.get(NGO, body.ngo_id) is None:
        raise HTTPException(404, "No se encontró la ONG")
    resource = db.scalar(
        select(NationalResource).where(NationalResource.code == resource_code).with_for_update()
    )
    if resource is None:
        raise HTTPException(404, "No se encontró el recurso")
    if resource.ngo_id is not None or resource.committed_quantity != 0:
        raise HTTPException(409, "Solo se puede asignar inventario sin propietario ni compromisos")
    resource.ngo_id = body.ngo_id
    db.commit()
    return resource_response(resource)


@router.post(
    "/allocations/validate",
    response_model=ResourceAvailabilityResponse,
    summary="Evaluar recursos de una ONG desde coordinación sin reservarlos",
)
def coordinator_validate(
    body: CoordinatorAvailabilityRequest, user: CoordinatorUser, db: DbSession
):
    ngo = db.get(NGO, body.ngo_id)
    if ngo is None:
        raise HTTPException(404, "No se encontró la ONG")
    return evaluate_availability(body, ngo, db)


@router.get(
    "/allocations",
    response_model=list[AllocationResponse],
    summary="Consultar adjudicaciones por proyecto; las ONG solo ven las propias",
)
def allocations(
    user: CurrentUser,
    db: DbSession,
    project_reference: str | None = None,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=100),
):
    return list_allocations(db, allocation_scope(user), project_reference, offset, limit)


@router.get(
    "/allocations/{allocation_id}",
    response_model=AllocationResponse,
    summary="Consultar una adjudicación",
)
def allocation(allocation_id: uuid.UUID, user: CurrentUser, db: DbSession):
    return get_allocation(db, allocation_id, allocation_scope(user))
