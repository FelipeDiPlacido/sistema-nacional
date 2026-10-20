import logging

from fastapi import APIRouter, HTTPException, status
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
from app.models import NGO, Certification, NGOCertification, User
from app.models.national import NGOCertificationStatus, NGOStatus, RiskLevel
from app.schemas import (
    AccountResponse,
    CertificationResponse,
    CompetencyProfileResponse,
    CompetencyResponse,
    EnabledNGOResponse,
    LoginRequest,
    NGORegistration,
    NGORegistrationResponse,
    TokenResponse,
)

router = APIRouter(prefix="/api/v1", tags=["Sistema Nacional"])
logger = logging.getLogger(__name__)


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
