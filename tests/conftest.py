import os
import uuid

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite://")
os.environ.setdefault("JWT_SECRET", "test-secret-that-is-at-least-32-characters")
os.environ.setdefault("RESOURCE_ADMIN_TOKEN", "test-resource-admin-token-at-least-32")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.main import app
from app.models import Certification
from app.seed import DEFAULT_CERTIFICATIONS


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        for code, name, description in DEFAULT_CERTIFICATIONS:
            session.add(
                Certification(code=code, name=name, description=description, is_active=True)
            )
        session.commit()
        yield session
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def registration_payload():
    return {
        "name": "Fundación de Respuesta",
        "email": f"contacto-{uuid.uuid4()}@example.org",
        "password": "a-secure-test-password",
        "certification_codes": ["LOGISTICS", "MEDICAL_RESPONSE"],
    }
