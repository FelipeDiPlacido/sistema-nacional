import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.core.security import password_hasher
from app.models import Certification, NationalResource, User
from app.models.national import UserRole
from app.seed import seed_accounts


def register(client, email=None):
    body = {
        "name": "Asistencia La Plata",
        "email": email or f"ong-{uuid.uuid4()}@example.org",
        "password": "ngo-test-password",
        "certification_codes": ["LOGISTICS"],
    }
    response = client.post("/api/v1/ngos", json=body)
    assert response.status_code == 201, response.text
    token = client.post(
        "/api/v1/auth/login",
        json={
            "email": body["email"],
            "password": body["password"],
        },
    ).json()["access_token"]
    return response.json()["id"], {"Authorization": f"Bearer {token}"}


def approve(client, ngo_id, levels=None):
    response = client.patch(
        f"/api/v1/ngos/{ngo_id}/status", json={"status": "ENABLED"}, headers=client.admin_headers
    )
    assert response.status_code == 200, response.text
    response = client.put(
        f"/api/v1/ngos/{ngo_id}/certifications/LOGISTICS",
        json={
            "status": "VERIFIED",
            "allowed_risk_levels": levels or ["LOW"],
        },
        headers=client.admin_headers,
    )
    assert response.status_code == 200, response.text


def stock(client, ngo_id, code, headers, quantity=10):
    return client.post(
        "/api/v1/resources",
        json={
            "ngo_id": ngo_id,
            "code": code,
            "name": "Agua potable",
            "category": "SUPPLIES",
            "unit": "litros",
            "total_quantity": quantity,
            "required_certification_code": "LOGISTICS",
        },
        headers=headers,
    )


def award(client, ngo_id, code, headers, project="EMERGENCY-200", quantity=6, key=None):
    return client.post(
        "/api/v1/allocations",
        json={
            "project_reference": project,
            "ngo_id": ngo_id,
            "risk_level": "LOW",
            "items": [{"resource_code": code, "quantity": quantity}],
        },
        headers={**headers, "Idempotency-Key": key or str(uuid.uuid4())},
    )


@pytest.fixture
def coordinator(client, db):
    db.add(
        User(
            email="coordination@example.org",
            role=UserRole.COORDINATOR,
            password_hash=password_hasher.hash("coordinator-password"),
        )
    )
    db.commit()
    token = client.post(
        "/api/v1/auth/login",
        json={
            "email": "coordination@example.org",
            "password": "coordinator-password",
        },
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_onboarding_to_project_closure_through_http(client, coordinator):
    first, first_headers = register(client)
    second, second_headers = register(client)
    pending = client.get("/api/v1/ngos?status=PENDING_REVIEW", headers=client.admin_headers)
    assert {item["id"] for item in pending.json()} == {first, second}
    for ngo_id, code, headers in [
        (first, "FIRST_WATER", first_headers),
        (second, "SECOND_WATER", second_headers),
    ]:
        assert stock(client, ngo_id, code, headers).status_code == 201
        blocked = award(client, ngo_id, code, coordinator)
        assert blocked.status_code == 409
        approve(client, ngo_id)
        evaluation = client.post(
            "/api/v1/allocations/validate",
            json={
                "ngo_id": ngo_id,
                "resource_code": code,
                "quantity": 6,
                "risk_level": "LOW",
            },
            headers=coordinator,
        )
        assert evaluation.status_code == 200
        assert evaluation.json()["fulfillable_quantity"] == 6
        assert evaluation.json()["allowed_risk_levels"] == ["LOW"]
        assert client.get(f"/api/v1/resources/{code}").json()["committed_quantity"] == 0
        key = str(uuid.uuid4())
        created = award(client, ngo_id, code, coordinator, key=key)
        assert created.status_code == 201, created.text
        assert award(client, ngo_id, code, coordinator, key=key).status_code == 200
        assert award(client, ngo_id, code, coordinator).status_code == 409
    allocations = client.get(
        "/api/v1/allocations?project_reference=EMERGENCY-200", headers=coordinator
    ).json()
    assert len(allocations) == 2
    own = client.get("/api/v1/allocations", headers=first_headers).json()
    assert len(own) == 1 and own[0]["ngo_id"] == first
    other = next(item for item in allocations if item["ngo_id"] == second)
    other_url = f"/api/v1/allocations/{other['id']}"
    assert client.get(other_url, headers=first_headers).status_code == 404
    other_code = other["items"][0]["resource_code"]
    assert (
        client.post(
            other_url + "/close",
            json={"items": [{"resource_code": other_code, "quantity": 0}]},
            headers=first_headers,
        ).status_code
        == 404
    )
    for item in allocations:
        url = f"/api/v1/allocations/{item['id']}/close"
        headers = first_headers if item["ngo_id"] == first else coordinator
        code = item["items"][0]["resource_code"]
        close_body = {"items": [{"resource_code": code, "quantity": 2}]}
        closed = client.post(url, json=close_body, headers=headers)
        assert closed.status_code == 200, closed.text
        assert closed.json()["status"] == "CLOSED"
        assert closed.json()["items"][0]["consumed_quantity"] == 2
        # SQLite does not preserve timezone metadata; compare the UTC instants.
        repeated = client.post(url, json=close_body, headers=headers).json()
        assert datetime.fromisoformat(repeated["closed_at"]).replace(
            tzinfo=UTC
        ) == datetime.fromisoformat(closed.json()["closed_at"]).replace(tzinfo=UTC)
        inventory = client.get(f"/api/v1/resources/{code}").json()
        assert inventory["total_quantity"] == 8
        assert inventory["committed_quantity"] == 0
        assert inventory["available_quantity"] == 8


@pytest.mark.parametrize(
    ("consumed_quantity", "expected_total"),
    [(0, 10), (3, 7), (6, 4)],
)
def test_close_consumes_declared_quantity_once(
    client, coordinator, consumed_quantity, expected_total
):
    ngo_id, headers = register(client)
    approve(client, ngo_id)
    stock(client, ngo_id, "CLOSE_CONSUMPTION", headers, quantity=10)
    allocation = award(
        client, ngo_id, "CLOSE_CONSUMPTION", coordinator, quantity=6
    ).json()
    url = f"/api/v1/allocations/{allocation['id']}/close"
    body = {
        "items": [
            {"resource_code": "CLOSE_CONSUMPTION", "quantity": consumed_quantity}
        ]
    }

    closed = client.post(url, json=body, headers=headers)
    assert closed.status_code == 200, closed.text
    assert closed.json()["items"][0]["consumed_quantity"] == consumed_quantity
    inventory = client.get("/api/v1/resources/CLOSE_CONSUMPTION").json()
    assert inventory["total_quantity"] == expected_total
    assert inventory["committed_quantity"] == 0
    assert inventory["available_quantity"] == expected_total

    repeated = client.post(url, json=body, headers=headers)
    assert repeated.status_code == 200
    assert client.get("/api/v1/resources/CLOSE_CONSUMPTION").json() == inventory
    conflicting = client.post(
        url,
        json={"items": [{"resource_code": "CLOSE_CONSUMPTION", "quantity": 1}]},
        headers=headers,
    )
    assert conflicting.status_code == 409
    assert client.get("/api/v1/resources/CLOSE_CONSUMPTION").json() == inventory


def test_close_rejects_incomplete_or_excess_consumption_without_mutating_stock(
    client, coordinator
):
    ngo_id, headers = register(client)
    approve(client, ngo_id)
    stock(client, ngo_id, "INVALID_CLOSE", headers, quantity=10)
    allocation = award(client, ngo_id, "INVALID_CLOSE", coordinator, quantity=6).json()
    url = f"/api/v1/allocations/{allocation['id']}/close"
    invalid_bodies = [
        {"items": []},
        {"items": [{"resource_code": "INVALID_CLOSE", "quantity": 7}]},
        {
            "items": [
                {"resource_code": "INVALID_CLOSE", "quantity": 2},
                {"resource_code": "INVALID_CLOSE", "quantity": 1},
            ]
        },
    ]
    for body in invalid_bodies:
        assert client.post(url, json=body, headers=headers).status_code == 422
        assert (
            client.get("/api/v1/resources/INVALID_CLOSE").json()["committed_quantity"] == 6
        )
        assert (
            client.get(f"/api/v1/allocations/{allocation['id']}", headers=headers).json()[
                "status"
            ]
            == "ACTIVE"
        )


def test_roles_cannot_self_elevate_or_use_old_static_admin_token(client, coordinator):
    ngo_id, headers = register(client)
    status_url = f"/api/v1/ngos/{ngo_id}/status"
    for credential, expected in [
        ({}, 401),
        (headers, 403),
        (coordinator, 403),
        ({"X-National-Admin-Token": "old-static-secret"}, 401),
    ]:
        assert (
            client.patch(status_url, json={"status": "ENABLED"}, headers=credential).status_code
            == expected
        )
    assert award(client, ngo_id, "ANY_STOCK", headers).status_code == 403
    assert client.get("/api/v1/auth/me", headers=client.admin_headers).json()["role"] == "ADMIN"
    assert client.get("/api/v1/auth/me", headers=coordinator).json()["ngo_id"] is None
    assert client.get("/api/v1/ngos/me/competencies", headers=coordinator).status_code == 403
    response = client.post(
        "/api/v1/ngos",
        json={
            "name": "Fake admin",
            "email": "fake@example.org",
            "password": "long-test-password",
            "role": "ADMIN",
        },
    )
    assert response.status_code == 422


@pytest.mark.parametrize(
    "status,levels",
    [
        ("VERIFIED", []),
        ("REJECTED", ["LOW"]),
        ("PENDING_REVIEW", ["LOW"]),
        ("VERIFIED", ["LOW", "LOW"]),
        ("VERIFIED", ["UNKNOWN"]),
    ],
)
def test_certification_review_requires_explicit_consistent_risks(client, status, levels):
    ngo_id, _ = register(client)
    response = client.put(
        f"/api/v1/ngos/{ngo_id}/certifications/LOGISTICS",
        json={"status": status, "allowed_risk_levels": levels},
        headers=client.admin_headers,
    )
    assert response.status_code == 422


def test_suspension_and_revocation_block_new_awards_but_allow_release(client, coordinator):
    ngo_id, headers = register(client)
    approve(client, ngo_id)
    stock(client, ngo_id, "SUSPEND_STOCK", headers)
    created = award(client, ngo_id, "SUSPEND_STOCK", coordinator).json()
    client.patch(
        f"/api/v1/ngos/{ngo_id}/status", json={"status": "SUSPENDED"}, headers=client.admin_headers
    )
    assert client.get("/api/v1/ngos/enabled").json() == []
    assert (
        award(client, ngo_id, "SUSPEND_STOCK", coordinator, project="OTHER", quantity=1).status_code
        == 409
    )
    assert (
        client.post(
            f"/api/v1/allocations/{created['id']}/close",
            json={"items": [{"resource_code": "SUSPEND_STOCK", "quantity": 0}]},
            headers=headers,
        ).status_code
        == 200
    )
    client.patch(
        f"/api/v1/ngos/{ngo_id}/status", json={"status": "ENABLED"}, headers=client.admin_headers
    )
    client.put(
        f"/api/v1/ngos/{ngo_id}/certifications/LOGISTICS",
        json={
            "status": "REJECTED",
            "allowed_risk_levels": [],
        },
        headers=client.admin_headers,
    )
    blocked = award(client, ngo_id, "SUSPEND_STOCK", coordinator, project="OTHER")
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["items"][0]["constraints"] == ["CERTIFICATION_NOT_VERIFIED"]


def test_inventory_is_owned_and_cannot_shrink_below_commitments(client, coordinator):
    first, headers = register(client)
    second, other_headers = register(client)
    approve(client, first)
    approve(client, second)
    assert stock(client, first, "OWN_STOCK", other_headers).status_code == 403
    assert stock(client, first, "OWN_STOCK", coordinator).status_code == 403
    assert stock(client, first, "OWN_STOCK", headers).status_code == 201
    wrong_owner = award(client, second, "OWN_STOCK", coordinator)
    assert wrong_owner.status_code == 409
    assert wrong_owner.json()["detail"]["items"][0]["constraints"] == ["RESOURCE_NOT_OWNED"]
    evaluation = client.post(
        "/api/v1/resources/validate",
        headers=other_headers,
        json={
            "resource_code": "OWN_STOCK",
            "quantity": 1,
            "risk_level": "LOW",
        },
    ).json()
    assert evaluation["fulfillable_quantity"] == 0
    assert evaluation["constraints"] == ["RESOURCE_NOT_OWNED"]
    assert award(client, first, "OWN_STOCK", coordinator).status_code == 201
    assert (
        client.put(
            "/api/v1/resources/OWN_STOCK",
            headers=headers,
            json={"total_quantity": 5, "is_active": True},
        ).status_code
        == 409
    )
    assert (
        client.put(
            "/api/v1/resources/OWN_STOCK",
            headers=other_headers,
            json={"total_quantity": 10, "is_active": True},
        ).status_code
        == 403
    )
    updated = client.put(
        "/api/v1/resources/OWN_STOCK",
        headers=headers,
        json={"total_quantity": 12, "is_active": True},
    )
    assert updated.json()["available_quantity"] == 6
    assert client.get(f"/api/v1/resources?ngo_id={second}").json() == []


def test_legacy_stock_requires_explicit_owner_without_guessing(client, db, coordinator):
    ngo_id, _ = register(client)
    approve(client, ngo_id)
    certification = db.scalar(select(Certification).where(Certification.code == "LOGISTICS"))
    db.add(
        NationalResource(
            code="LEGACY_STOCK",
            name="Stock anterior",
            category="SUPPLIES",
            unit="litros",
            total_quantity=10,
            required_certification=certification,
        )
    )
    db.commit()
    assert award(client, ngo_id, "LEGACY_STOCK", coordinator).status_code == 409
    url = "/api/v1/resources/LEGACY_STOCK/owner"
    assigned = client.patch(url, json={"ngo_id": ngo_id}, headers=client.admin_headers)
    assert assigned.status_code == 200
    assert assigned.json()["ngo_id"] == ngo_id
    assert award(client, ngo_id, "LEGACY_STOCK", coordinator).status_code == 201
    assert (
        client.patch(url, json={"ngo_id": ngo_id}, headers=client.admin_headers).status_code == 409
    )


def test_inactive_certification_is_not_reported_as_enabled(client, db):
    ngo_id, headers = register(client)
    approve(client, ngo_id)
    certification = db.scalar(select(Certification).where(Certification.code == "LOGISTICS"))
    certification.is_active = False
    db.commit()
    profile = client.get("/api/v1/ngos/me/competencies", headers=headers).json()
    assert profile["competencies"][0]["enabled"] is False
    assert profile["competencies"][0]["allowed_risk_levels"] == []
    response = client.put(
        f"/api/v1/ngos/{ngo_id}/certifications/LOGISTICS",
        json={
            "status": "VERIFIED",
            "allowed_risk_levels": ["LOW"],
        },
        headers=client.admin_headers,
    )
    assert response.status_code == 409


def test_bootstrap_is_idempotent_and_never_promotes_registered_accounts(client, db):
    settings = Settings(
        database_url="sqlite://",
        jwt_secret="x" * 32,
        bootstrap_coordinator_email="bootstrap@example.org",
        bootstrap_coordinator_password="bootstrap-password",
    )
    seed_accounts(db, settings)
    db.commit()
    user = db.scalar(select(User).where(User.email == "bootstrap@example.org"))
    original_hash = user.password_hash
    user.is_active = False
    db.commit()
    seed_accounts(db, settings)
    db.commit()
    assert user.password_hash == original_hash and user.is_active is False
    register(client, "existing@example.org")
    conflicting = settings.model_copy(
        update={"bootstrap_coordinator_email": "existing@example.org"}
    )
    with pytest.raises(ValueError, match="different role"):
        seed_accounts(db, conflicting)


def test_bootstrap_rejects_incomplete_credentials():
    with pytest.raises(ValidationError):
        Settings(
            database_url="sqlite://", jwt_secret="x" * 32, bootstrap_admin_email="admin@example.org"
        )


def test_expired_and_disabled_account_tokens_are_rejected(client, db):
    user = db.scalar(select(User).where(User.email == "admin@example.org"))
    token = jwt.encode(
        {
            "sub": str(user.id),
            "iat": datetime.now(UTC) - timedelta(hours=2),
            "exp": datetime.now(UTC) - timedelta(hours=1),
        },
        get_settings().jwt_secret.get_secret_value(),
        algorithm="HS256",
    )
    assert (
        client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code
        == 401
    )
    user.is_active = False
    db.commit()
    assert client.get("/api/v1/auth/me", headers=client.admin_headers).status_code == 401


@pytest.mark.parametrize("create_first", [False, True])
def test_cancel_intent_blocks_late_create_and_releases_once(client, coordinator, create_first):
    ngo_id, headers = register(client)
    approve(client, ngo_id)
    assert stock(client, ngo_id, "CANCEL_WATER", headers).status_code == 201
    key = str(uuid.uuid4())
    body = {
        "project_reference": "CANCEL-PROJECT",
        "ngo_id": ngo_id,
        "risk_level": "LOW",
        "items": [{"resource_code": "CANCEL_WATER", "quantity": 6}],
    }
    request_headers = {**coordinator, "Idempotency-Key": key}
    if create_first:
        assert (
            client.post("/api/v1/allocations", json=body, headers=request_headers).status_code
            == 201
        )
    assert (
        client.post(
            "/api/v1/allocations/cancel", json=body, headers={**headers, "Idempotency-Key": key}
        ).status_code
        == 403
    )
    cancelled = client.post("/api/v1/allocations/cancel", json=body, headers=request_headers)
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "CLOSED"
    repeated = client.post("/api/v1/allocations/cancel", json=body, headers=request_headers)
    assert repeated.json()["id"] == cancelled.json()["id"]
    delayed = client.post("/api/v1/allocations", json=body, headers=request_headers)
    assert delayed.status_code == 200
    assert delayed.json()["status"] == "CLOSED"
    assert client.get("/api/v1/resources/CANCEL_WATER").json()["committed_quantity"] == 0
    changed = {**body, "items": [{"resource_code": "CANCEL_WATER", "quantity": 5}]}
    assert (
        client.post("/api/v1/allocations/cancel", json=changed, headers=request_headers).status_code
        == 409
    )
