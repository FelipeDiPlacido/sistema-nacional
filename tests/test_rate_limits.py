from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.responses import JSONResponse

from app.core.config import Settings
from app.core.rate_limit import RequestLimitsMiddleware


def limited_app(**overrides):
    settings = Settings(_env_file=None, **overrides)
    app = FastAPI()
    app.add_middleware(RequestLimitsMiddleware, settings=settings)

    @app.api_route("/{path:path}", methods=["GET", "POST"])
    def endpoint(path: str):
        # A rejected login must consume quota too; handlers must not run after exhaustion.
        if path.rstrip("/") == "api/v1/auth/login":
            return JSONResponse({"detail": "Credenciales incorrectas"}, status_code=401)
        return {"path": path}

    return app


@pytest.mark.parametrize(
    ("path", "setting", "normal_status", "window"),
    [
        ("/api/v1/auth/login", "rate_limit_login_per_minute", 401, 60),
        ("/api/v1/ngos", "rate_limit_registration_per_hour", 200, 3600),
    ],
)
def test_sensitive_endpoint_quota_cannot_be_bypassed_with_trailing_slash(
    path, setting, normal_status, window
):
    with TestClient(limited_app(**{setting: 2})) as client:
        assert client.post(path).status_code == normal_status
        assert client.post(path + "/").status_code == normal_status
        response = client.post(path)
        assert response.status_code == 429
        assert 1 <= int(response.headers["Retry-After"]) <= window
        assert response.headers["Cache-Control"] == "no-store"
        assert "Demasiadas solicitudes" in response.json()["detail"]
        assert client.get("/api/v1/certifications").status_code == 200


def test_client_quota_is_shared_across_routes_and_ignores_spoofed_headers():
    app = limited_app(rate_limit_requests_per_minute=2)
    with TestClient(app, client=("198.51.100.1", 5000)) as client:
        assert client.get("/docs").status_code == 200
        assert client.get("/openapi.json").status_code == 200
        response = client.get(
            "/api/v1/resources",
            headers={
                "X-Forwarded-For": "198.51.100.99",
                "X-Real-IP": "198.51.100.100",
                "Forwarded": "for=198.51.100.101",
                "Authorization": "Bearer changed-token",
            },
        )
        assert response.status_code == 429
    with TestClient(app, client=("198.51.100.2", 5000)) as other:
        assert other.get("/api/v1/resources").status_code == 200


@pytest.mark.parametrize(
    ("method", "path", "setting", "normal_status"),
    [
        ("GET", "/api/v1/certifications", "rate_limit_global_requests_per_minute", 200),
        ("POST", "/api/v1/auth/login", "rate_limit_global_login_per_minute", 401),
        ("POST", "/api/v1/ngos", "rate_limit_global_registration_per_hour", 200),
    ],
)
def test_global_quota_is_shared_across_clients(method, path, setting, normal_status):
    app = limited_app(**{setting: 2})
    for index, expected in enumerate((normal_status, normal_status, 429), 1):
        with TestClient(app, client=(f"198.51.100.{index}", 5000)) as client:
            assert client.request(method, path).status_code == expected


def test_health_checks_do_not_consume_quota_and_survive_exhaustion():
    with TestClient(limited_app(rate_limit_requests_per_minute=1)) as client:
        for _ in range(3):
            assert client.get("/health").status_code == 200
            assert client.get("/ready").status_code == 200
        assert client.get("/api/v1/certifications").status_code == 200
        assert client.get("/api/v1/certifications").status_code == 429
        assert client.get("/health").status_code == 200
        assert client.get("/ready").status_code == 200
        assert client.post("/ready").status_code == 429


def test_expired_window_allows_requests_again(monkeypatch):
    clock = [1_000.0]
    monkeypatch.setattr("time.time", lambda: clock[0])
    with TestClient(limited_app(rate_limit_login_per_minute=1)) as client:
        assert client.post("/api/v1/auth/login").status_code == 401
        response = client.post("/api/v1/auth/login")
        assert response.status_code == 429
        assert response.headers["Retry-After"] == "60"
        clock[0] += 61
        assert client.post("/api/v1/auth/login").status_code == 401


def test_concurrent_requests_cannot_exceed_global_budget():
    with TestClient(limited_app(rate_limit_global_requests_per_minute=5)) as client:
        with ThreadPoolExecutor(max_workers=10) as pool:
            statuses = list(
                pool.map(lambda _: client.get("/api/v1/resources").status_code, range(20))
            )
    assert statuses.count(200) == 5
    assert statuses.count(429) == 15


@pytest.mark.parametrize("value", [0, -1])
def test_invalid_limit_configuration_fails_startup(value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, rate_limit_login_per_minute=value)


def test_real_login_limits_failures_before_more_password_checks(client):
    # The fixture has already performed one successful administrator login.
    for _ in range(9):
        response = client.post(
            "/api/v1/auth/login",
            json={"email": "admin@example.org", "password": "incorrect-password"},
        )
        assert response.status_code == 401
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.org", "password": "test-admin-password"},
    )
    assert response.status_code == 429
    # Other authenticated actions and health checks still work after a login throttle.
    assert client.get("/api/v1/auth/me", headers=client.admin_headers).status_code == 200
    assert client.get("/ready").status_code == 200


def test_invalid_registration_attempts_are_also_limited(client, db):
    from sqlalchemy import func, select

    from app.models import NGO

    for _ in range(10):
        assert client.post("/api/v1/ngos", json={}).status_code == 422
    assert client.post("/api/v1/ngos", json={}).status_code == 429
    assert db.scalar(select(func.count()).select_from(NGO)) == 0


def test_openapi_documents_rate_limit_response(client):
    schema = client.get("/openapi.json").json()
    for path in ("/api/v1/auth/login", "/api/v1/ngos", "/api/v1/allocations"):
        response = schema["paths"][path]["post"]["responses"]["429"]
        assert response["headers"]["Retry-After"]["schema"]["type"] == "integer"
