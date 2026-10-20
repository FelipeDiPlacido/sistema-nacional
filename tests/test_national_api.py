import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models import NGO, NGOCertification
from app.models.national import NGOCertificationStatus, NGOStatus


def test_health_and_openapi_are_available(client: TestClient):
    assert client.get("/health").json() == {"status": "ok"}
    schema = client.get("/openapi.json").json()
    assert schema["info"]["title"] == "Sistema Nacional de Gestión de Recursos y Riesgos"
    assert "/api/v1/ngos" in schema["paths"]


def test_register_login_and_get_structured_competency_profile(client, registration_payload):
    registered = client.post("/api/v1/ngos", json=registration_payload)
    assert registered.status_code == 201
    assert registered.json()["status"] == "PENDING_REVIEW"

    login = client.post(
        "/api/v1/auth/login",
        json={
            "email": registration_payload["email"],
            "password": registration_payload["password"],
        },
    )
    assert login.status_code == 200
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    profile = client.get("/api/v1/ngos/me/competencies", headers=headers)
    assert profile.status_code == 200
    body = profile.json()
    assert body["authorization_level"] == "PENDING_REVIEW"
    assert {entry["code"] for entry in body["competencies"]} == {
        "LOGISTICS",
        "MEDICAL_RESPONSE",
    }
    assert all(entry["enabled"] is False for entry in body["competencies"])
    assert all(entry["allowed_risk_levels"] == [] for entry in body["competencies"])

    account = client.get("/api/v1/auth/me", headers=headers)
    assert account.status_code == 200
    assert account.json()["email"] == registration_payload["email"]


def test_registration_rejects_unknown_certifications(client, registration_payload):
    registration_payload["certification_codes"] = ["UNKNOWN"]
    response = client.post("/api/v1/ngos", json=registration_payload)
    assert response.status_code == 422


def test_registration_rejects_duplicate_email(client, registration_payload):
    assert client.post("/api/v1/ngos", json=registration_payload).status_code == 201
    response = client.post("/api/v1/ngos", json=registration_payload)
    assert response.status_code == 409


def test_login_rejects_wrong_password(client, registration_payload):
    client.post("/api/v1/ngos", json=registration_payload)
    response = client.post(
        "/api/v1/auth/login",
        json={"email": registration_payload["email"], "password": "wrong-password"},
    )
    assert response.status_code == 401


def test_competency_profile_requires_authentication(client):
    assert client.get("/api/v1/ngos/me/competencies").status_code == 401


def test_verified_profile_returns_authorized_risk_levels(client, db, registration_payload):
    registered = client.post("/api/v1/ngos", json=registration_payload).json()
    certification = db.scalar(
        select(NGOCertification).where(NGOCertification.ngo_id == uuid.UUID(registered["id"]))
    )
    ngo = db.get(NGO, uuid.UUID(registered["id"]))
    ngo.status = NGOStatus.ENABLED
    certification.status = NGOCertificationStatus.VERIFIED
    certification.allowed_risk_levels = ["LOW", "MEDIUM"]
    db.commit()

    login = client.post(
        "/api/v1/auth/login",
        json={
            "email": registration_payload["email"],
            "password": registration_payload["password"],
        },
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    profile = client.get("/api/v1/ngos/me/competencies", headers=headers)
    assert profile.status_code == 200
    assert profile.json()["authorization_level"] == "ENABLED"
    enabled_competency = next(item for item in profile.json()["competencies"] if item["enabled"])
    assert enabled_competency["allowed_risk_levels"] == ["LOW", "MEDIUM"]


def test_certification_catalog_and_enabled_ngo_catalog(client):
    certifications = client.get("/api/v1/certifications")
    assert certifications.status_code == 200
    assert len(certifications.json()) == 4
    assert client.get("/api/v1/ngos/enabled").json() == []
