import os
import uuid
from urllib.parse import urlsplit

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite://")
os.environ.setdefault("JWT_SECRET", "test-secret-that-is-at-least-32-characters")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.security import get_auth_db, password_hasher
from app.main import app
from app.models import Certification, User
from app.models.national import UserRole
from app.seed import DEFAULT_CERTIFICATIONS


@pytest.fixture(
    params=["sqlite"] + (["postgresql"] if os.getenv("NATIONAL_TEST_DATABASE_URL") else [])
)
def db(request):
    if request.param == "postgresql":
        database_url = os.environ["NATIONAL_TEST_DATABASE_URL"]
        if not urlsplit(database_url).path.lstrip("/").endswith("_test"):
            pytest.fail("Database tests require a dedicated database ending in _test")
        engine = create_engine(database_url)
    else:
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
        session.add(
            User(
                email="admin@example.org",
                role=UserRole.ADMIN,
                password_hash=password_hasher.hash("test-admin-password"),
            )
        )
        session.commit()
        yield session
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def client(db):
    def override_get_db():
        try:
            yield db
        finally:
            db.rollback()

    def override_auth_db():
        with Session(db.bind, expire_on_commit=False) as auth_db:
            yield auth_db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_auth_db] = override_auth_db
    with TestClient(app) as test_client:
        token = test_client.post(
            "/api/v1/auth/login",
            json={"email": "admin@example.org", "password": "test-admin-password"},
        ).json()["access_token"]
        test_client.admin_headers = {"Authorization": f"Bearer {token}"}
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
