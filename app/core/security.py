import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pwdlib import PasswordHash
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.core.config import get_settings
from app.core.database import DbSession, get_session_factory
from app.models import NGO, User
from app.models.national import UserRole

password_hasher = PasswordHash.recommended()
dummy_password_hash = password_hasher.hash("timing-protection-unused-password")
bearer_scheme = HTTPBearer(auto_error=False)


def create_access_token(user: User) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": str(user.id),
            "iat": now,
            "exp": now + timedelta(minutes=settings.access_token_expire_minutes),
        },
        settings.jwt_secret.get_secret_value(),
        algorithm="HS256",
    )


def get_auth_db():
    # Authentication must not open the transaction owned by allocation services.
    with get_session_factory()() as session:
        yield session


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    db: Annotated[Session, Depends(get_auth_db)],
) -> User:
    if credentials is None:
        raise HTTPException(401, "Se requiere un token Bearer válido")
    try:
        payload = jwt.decode(
            credentials.credentials,
            get_settings().jwt_secret.get_secret_value(),
            algorithms=["HS256"],
            options={"require": ["sub", "exp", "iat"]},
        )
        user_id = uuid.UUID(payload["sub"])
    except (jwt.InvalidTokenError, ValueError, TypeError) as exc:
        raise HTTPException(401, "Se requiere un token Bearer válido") from exc

    user = db.scalar(select(User).options(joinedload(User.ngo)).where(User.id == user_id))
    if user is None or not user.is_active:
        raise HTTPException(401, "La cuenta no está habilitada")
    # Release the read connection before the business transaction needs another.
    # Both user and NGO are loaded, so authorization cannot issue lazy queries.
    db.expunge_all()
    db.rollback()
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def get_current_account(user: CurrentUser, db: DbSession) -> tuple[User, NGO]:
    if user.role != UserRole.NGO:
        raise HTTPException(403, "Esta operación requiere una cuenta de ONG")
    ngo = db.scalar(select(NGO).where(NGO.owner_user_id == user.id))
    if ngo is None:
        raise HTTPException(403, "La cuenta no tiene una ONG asociada")
    return user, ngo


def require_admin(user: CurrentUser) -> User:
    if user.role != UserRole.ADMIN:
        raise HTTPException(403, "Se requiere el rol de administración nacional")
    return user


def require_coordinator(user: CurrentUser) -> User:
    if user.role not in (UserRole.ADMIN, UserRole.COORDINATOR):
        raise HTTPException(403, "Se requiere el rol de coordinación o administración nacional")
    return user


AdminUser = Annotated[User, Depends(require_admin)]
CoordinatorUser = Annotated[User, Depends(require_coordinator)]


CurrentAccount = Annotated[tuple[User, NGO], Depends(get_current_account)]
