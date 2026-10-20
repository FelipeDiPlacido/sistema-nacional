import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pwdlib import PasswordHash
from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import DbSession
from app.models import NGO, User

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


def get_current_account(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    db: DbSession,
) -> tuple[User, NGO]:
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

    user = db.get(User, user_id)
    ngo = db.scalar(select(NGO).where(NGO.owner_user_id == user_id))
    if user is None or not user.is_active or ngo is None:
        raise HTTPException(401, "La cuenta no está habilitada")
    return user, ngo


CurrentAccount = Annotated[tuple[User, NGO], Depends(get_current_account)]
