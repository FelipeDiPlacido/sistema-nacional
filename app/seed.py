from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import get_session_factory
from app.core.security import password_hasher
from app.models import Certification
from app.models.national import User, UserRole

DEFAULT_CERTIFICATIONS = (
    (
        "MEDICAL_RESPONSE",
        "Respuesta sanitaria",
        "Atención sanitaria y apoyo médico en emergencias.",
    ),
    (
        "SEARCH_AND_RESCUE",
        "Búsqueda y rescate",
        "Operaciones de búsqueda, rescate y evacuación.",
    ),
    (
        "LOGISTICS",
        "Logística",
        "Transporte, almacenamiento y distribución de recursos.",
    ),
    (
        "SHELTER_AND_FOOD",
        "Alojamiento y alimentación",
        "Gestión de refugios temporales y asistencia alimentaria.",
    ),
)


def seed_accounts(db: Session, settings: Settings) -> None:
    for role in (UserRole.ADMIN, UserRole.COORDINATOR):
        email = getattr(settings, f"bootstrap_{role.value.lower()}_email")
        password = getattr(settings, f"bootstrap_{role.value.lower()}_password")
        if email is None:
            continue
        email = str(email).lower()
        existing = db.scalar(select(User).where(User.email == email))
        if existing is not None:
            if existing.role != role:
                raise ValueError(f"Bootstrap account {email} already has a different role")
            # Restarting the service must not reset passwords or reactivate users.
            continue
        db.add(
            User(
                email=email,
                password_hash=password_hasher.hash(password.get_secret_value()),
                role=role,
                is_active=True,
            )
        )


def seed_catalog() -> None:
    with get_session_factory()() as db:
        existing_codes = set(db.scalars(select(Certification.code)).all())
        for code, name, description in DEFAULT_CERTIFICATIONS:
            if code not in existing_codes:
                db.add(
                    Certification(
                        code=code,
                        name=name,
                        description=description,
                        is_active=True,
                    )
                )
        seed_accounts(db, get_settings())
        db.commit()


if __name__ == "__main__":
    seed_catalog()
