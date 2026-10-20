from sqlalchemy import select

from app.core.database import get_session_factory
from app.models import Certification

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
        db.commit()


if __name__ == "__main__":
    seed_catalog()
