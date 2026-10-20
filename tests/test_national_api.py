import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models import NGO, Certification, NGOCertification
from app.models.national import NGOCertificationStatus, NGOStatus


def test_health_and_openapi_are_available(client: TestClient):
    assert client.get("/health").json() == {"status": "ok"}
    schema = client.get("/openapi.json").json()
    assert schema["info"]["title"] == "Sistema Nacional de Gestión de Recursos y Riesgos"
    assert "/api/v1/ngos" in schema["paths"]
    assert "/api/v1/resources" in schema["paths"]
    assert "/api/v1/resources/validate" in schema["paths"]
    assert "/api/v1/allocations" in schema["paths"]
    assert "/api/v1/allocations/{allocation_id}/close" in schema["paths"]


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


def test_resource_admin_creates_inventory_and_public_catalog_reports_availability(client):
    body = {
        "code": "POTABLE_WATER",
        "name": "Agua potable",
        "category": "SUPPLIES",
        "unit": "litros",
        "total_quantity": 100,
        "required_certification_code": "LOGISTICS",
    }
    unauthorized = client.post("/api/v1/resources", json=body)
    assert unauthorized.status_code == 401

    created = client.post(
        "/api/v1/resources",
        json=body,
        headers={"X-National-Admin-Token": "test-resource-admin-token-at-least-32"},
    )
    assert created.status_code == 201
    assert created.json()["available_quantity"] == 100
    assert created.json()["committed_quantity"] == 0

    listing = client.get("/api/v1/resources")
    assert listing.status_code == 200
    assert [resource["code"] for resource in listing.json()] == ["POTABLE_WATER"]
    assert client.get("/api/v1/resources/POTABLE_WATER").json()["unit"] == "litros"


def test_resource_validation_reports_pending_ngo_constraints(client, registration_payload):
    registered = client.post("/api/v1/ngos", json=registration_payload).json()
    token = client.post(
        "/api/v1/auth/login",
        json={
            "email": registration_payload["email"],
            "password": registration_payload["password"],
        },
    ).json()["access_token"]
    client.post(
        "/api/v1/resources",
        json={
            "code": "WATER_PENDING",
            "name": "Agua de prueba",
            "category": "SUPPLIES",
            "unit": "litros",
            "total_quantity": 3,
            "required_certification_code": "LOGISTICS",
        },
        headers={"X-National-Admin-Token": "test-resource-admin-token-at-least-32"},
    )

    response = client.post(
        "/api/v1/resources/validate",
        json={"resource_code": "WATER_PENDING", "quantity": 5, "risk_level": "LOW"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    result = response.json()
    assert result["authorization_level"] == "PENDING_REVIEW"
    assert result["available_quantity"] == 3
    assert result["fulfillable_quantity"] == 0
    assert result["constraints"] == ["ONG_NOT_ENABLED", "CERTIFICATION_NOT_VERIFIED"]
    assert registered["status"] == "PENDING_REVIEW"


def test_resource_validation_returns_partial_quantity_and_risk_constraints(
    client, db, registration_payload
):
    registered = client.post("/api/v1/ngos", json=registration_payload).json()
    ngo_id = uuid.UUID(registered["id"])
    ngo = db.get(NGO, ngo_id)
    certification_record = db.scalar(
        select(NGOCertification)
        .join(NGOCertification.certification)
        .where(
            NGOCertification.ngo_id == ngo_id,
            Certification.code == "LOGISTICS",
        )
    )
    ngo.status = NGOStatus.ENABLED
    certification_record.status = NGOCertificationStatus.VERIFIED
    certification_record.allowed_risk_levels = ["LOW"]
    db.commit()

    token = client.post(
        "/api/v1/auth/login",
        json={
            "email": registration_payload["email"],
            "password": registration_payload["password"],
        },
    ).json()["access_token"]
    client.post(
        "/api/v1/resources",
        json={
            "code": "WATER_LIMITED",
            "name": "Agua limitada",
            "category": "SUPPLIES",
            "unit": "litros",
            "total_quantity": 4,
            "required_certification_code": "LOGISTICS",
        },
        headers={"X-National-Admin-Token": "test-resource-admin-token-at-least-32"},
    )
    headers = {"Authorization": f"Bearer {token}"}

    partial = client.post(
        "/api/v1/resources/validate",
        json={"resource_code": "WATER_LIMITED", "quantity": 7, "risk_level": "LOW"},
        headers=headers,
    )
    assert partial.status_code == 200
    assert partial.json()["availability_status"] == "PARTIAL"
    assert partial.json()["fulfillable_quantity"] == 4
    assert partial.json()["constraints"] == ["INSUFFICIENT_STOCK"]

    risk_mismatch = client.post(
        "/api/v1/resources/validate",
        json={"resource_code": "WATER_LIMITED", "quantity": 2, "risk_level": "HIGH"},
        headers=headers,
    )
    assert risk_mismatch.status_code == 200
    assert risk_mismatch.json()["availability_status"] == "NONE"
    assert risk_mismatch.json()["fulfillable_quantity"] == 0
    assert risk_mismatch.json()["constraints"] == ["RISK_LEVEL_NOT_ALLOWED"]


def test_resource_registration_rejects_unknown_certification_and_duplicate_code(client):
    headers = {"X-National-Admin-Token": "test-resource-admin-token-at-least-32"}
    body = {
        "code": "WATER_STOCK",
        "name": "Reserva de agua",
        "category": "SUPPLIES",
        "unit": "litros",
        "total_quantity": 10,
        "required_certification_code": "UNKNOWN",
    }
    assert client.post("/api/v1/resources", json=body, headers=headers).status_code == 422
    body["required_certification_code"] = "LOGISTICS"
    assert client.post("/api/v1/resources", json=body, headers=headers).status_code == 201
    assert client.post("/api/v1/resources", json=body, headers=headers).status_code == 409


def enable_ngo_certification(db, ngo_id: uuid.UUID, certification_code: str = "LOGISTICS"):
    ngo = db.get(NGO, ngo_id)
    certification = db.scalar(
        select(NGOCertification)
        .join(NGOCertification.certification)
        .where(
            NGOCertification.ngo_id == ngo_id,
            Certification.code == certification_code,
        )
    )
    ngo.status = NGOStatus.ENABLED
    certification.status = NGOCertificationStatus.VERIFIED
    certification.allowed_risk_levels = ["LOW", "MEDIUM"]
    db.commit()


def create_test_resource(client, code: str, quantity: int):
    return client.post(
        "/api/v1/resources",
        json={
            "code": code,
            "name": f"Recurso {code}",
            "category": "SUPPLIES",
            "unit": "unidades",
            "total_quantity": quantity,
            "required_certification_code": "LOGISTICS",
        },
        headers={"X-National-Admin-Token": "test-resource-admin-token-at-least-32"},
    )


def test_allocation_commits_resources_and_is_idempotent(client, db, registration_payload):
    registered = client.post("/api/v1/ngos", json=registration_payload).json()
    ngo_id = uuid.UUID(registered["id"])
    enable_ngo_certification(db, ngo_id)
    assert create_test_resource(client, "RESCUE_KITS", 10).status_code == 201
    payload = {
        "project_reference": "EMERGENCY-100",
        "ngo_id": str(ngo_id),
        "risk_level": "LOW",
        "items": [{"resource_code": "RESCUE_KITS", "quantity": 6}],
    }
    headers = {
        "X-National-Admin-Token": "test-resource-admin-token-at-least-32",
        "Idempotency-Key": str(uuid.uuid4()),
    }

    created = client.post("/api/v1/allocations", json=payload, headers=headers)
    assert created.status_code == 201
    assert created.json()["status"] == "ACTIVE"
    assert created.json()["items"][0]["quantity"] == 6
    assert client.get("/api/v1/resources/RESCUE_KITS").json()["available_quantity"] == 4

    repeated = client.post("/api/v1/allocations", json=payload, headers=headers)
    assert repeated.status_code == 200
    assert repeated.json()["id"] == created.json()["id"]
    assert client.get("/api/v1/resources/RESCUE_KITS").json()["committed_quantity"] == 6

    changed = {**payload, "items": [{"resource_code": "RESCUE_KITS", "quantity": 5}]}
    conflict = client.post("/api/v1/allocations", json=changed, headers=headers)
    assert conflict.status_code == 409
    assert client.get("/api/v1/resources/RESCUE_KITS").json()["committed_quantity"] == 6


def test_allocation_is_atomic_when_one_resource_is_unavailable(client, db, registration_payload):
    registered = client.post("/api/v1/ngos", json=registration_payload).json()
    ngo_id = uuid.UUID(registered["id"])
    enable_ngo_certification(db, ngo_id)
    create_test_resource(client, "AVAILABLE_PACKS", 10)
    create_test_resource(client, "LIMITED_PACKS", 2)
    payload = {
        "project_reference": "EMERGENCY-ATOMIC",
        "ngo_id": str(ngo_id),
        "risk_level": "LOW",
        "items": [
            {"resource_code": "AVAILABLE_PACKS", "quantity": 4},
            {"resource_code": "LIMITED_PACKS", "quantity": 3},
        ],
    }
    response = client.post(
        "/api/v1/allocations",
        json=payload,
        headers={
            "X-National-Admin-Token": "test-resource-admin-token-at-least-32",
            "Idempotency-Key": str(uuid.uuid4()),
        },
    )
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "ALLOCATION_CONSTRAINTS"
    assert detail["items"][0]["resource_code"] == "LIMITED_PACKS"
    assert detail["items"][0]["constraints"] == ["INSUFFICIENT_STOCK"]
    assert client.get("/api/v1/resources/AVAILABLE_PACKS").json()["committed_quantity"] == 0
    assert client.get("/api/v1/resources/LIMITED_PACKS").json()["committed_quantity"] == 0


def test_closing_allocation_releases_resources_once(client, db, registration_payload):
    registered = client.post("/api/v1/ngos", json=registration_payload).json()
    ngo_id = uuid.UUID(registered["id"])
    enable_ngo_certification(db, ngo_id)
    create_test_resource(client, "CLOSABLE_STOCK", 8)
    allocation = client.post(
        "/api/v1/allocations",
        json={
            "project_reference": "EMERGENCY-CLOSE",
            "ngo_id": str(ngo_id),
            "risk_level": "LOW",
            "items": [{"resource_code": "CLOSABLE_STOCK", "quantity": 5}],
        },
        headers={
            "X-National-Admin-Token": "test-resource-admin-token-at-least-32",
            "Idempotency-Key": str(uuid.uuid4()),
        },
    ).json()
    close_url = f"/api/v1/allocations/{allocation['id']}/close"
    admin_headers = {"X-National-Admin-Token": "test-resource-admin-token-at-least-32"}

    unauthorized = client.post(close_url)
    assert unauthorized.status_code == 401

    closed = client.post(close_url, headers=admin_headers)
    assert closed.status_code == 200
    assert closed.json()["status"] == "CLOSED"
    assert closed.json()["closed_at"] is not None
    assert client.get("/api/v1/resources/CLOSABLE_STOCK").json()["available_quantity"] == 8

    repeated_close = client.post(close_url, headers=admin_headers)
    assert repeated_close.status_code == 200
    assert repeated_close.json()["status"] == "CLOSED"
    assert client.get("/api/v1/resources/CLOSABLE_STOCK").json()["committed_quantity"] == 0


def test_allocation_requires_admin_and_idempotency_key(client):
    payload = {
        "project_reference": "EMERGENCY-NO-AUTH",
        "ngo_id": str(uuid.uuid4()),
        "risk_level": "LOW",
        "items": [{"resource_code": "MISSING_STOCK", "quantity": 1}],
    }
    assert client.post("/api/v1/allocations", json=payload).status_code == 401
    response = client.post(
        "/api/v1/allocations",
        json=payload,
        headers={"X-National-Admin-Token": "test-resource-admin-token-at-least-32"},
    )
    assert response.status_code == 422
