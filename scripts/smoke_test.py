"""Exercise onboarding, approval, inventory, allocation and closure through HTTP.

Run only against an environment where creating demonstration records is acceptable.
Credentials are read from the environment, never logged.
"""

import json
import os
import uuid
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def request(base, method, path, body=None, token=None, key=None, expected=200):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if key:
        headers["Idempotency-Key"] = key
    req = Request(
        base + path,
        method=method,
        headers=headers,
        data=json.dumps(body).encode() if body is not None else None,
    )
    try:
        with urlopen(req, timeout=15) as response:
            payload = json.load(response)
            if response.status != expected:
                raise RuntimeError(f"{method} {path}: HTTP {response.status}, expected {expected}")
            return payload
    except HTTPError as exc:
        raise RuntimeError(f"{method} {path}: HTTP {exc.code}: {exc.read().decode()}") from None


def main():
    base = os.getenv("NATIONAL_URL", "http://localhost:8010").rstrip("/")
    admin = request(
        base,
        "POST",
        "/api/v1/auth/login",
        {
            "email": os.environ["NATIONAL_ADMIN_EMAIL"],
            "password": os.environ["NATIONAL_ADMIN_PASSWORD"],
        },
    )["access_token"]
    coordinator = request(
        base,
        "POST",
        "/api/v1/auth/login",
        {
            "email": os.environ["NATIONAL_COORDINATOR_EMAIL"],
            "password": os.environ["NATIONAL_COORDINATOR_PASSWORD"],
        },
    )["access_token"]
    run_id = uuid.uuid4().hex[:12].upper()
    project = f"DEMO-{run_id}"
    results = []
    for index in (1, 2):
        email = f"demo-{run_id.lower()}-{index}@example.org"
        password = uuid.uuid4().hex
        ngo = request(
            base,
            "POST",
            "/api/v1/ngos",
            {
                "name": f"ONG de demostración {index} {run_id}",
                "email": email,
                "password": password,
                "certification_codes": ["LOGISTICS"],
            },
            expected=201,
        )
        ngo_id = ngo["id"]
        ngo_token = request(
            base,
            "POST",
            "/api/v1/auth/login",
            {
                "email": email,
                "password": password,
            },
        )["access_token"]
        request(base, "PATCH", f"/api/v1/ngos/{ngo_id}/status", {"status": "ENABLED"}, admin)
        request(
            base,
            "PUT",
            f"/api/v1/ngos/{ngo_id}/certifications/LOGISTICS",
            {
                "status": "VERIFIED",
                "allowed_risk_levels": ["LOW", "MEDIUM"],
            },
            admin,
        )
        code = f"WATER_{run_id}_{index}"
        request(
            base,
            "POST",
            "/api/v1/resources",
            {
                "ngo_id": ngo_id,
                "code": code,
                "name": "Agua potable",
                "category": "SUPPLIES",
                "unit": "litros",
                "total_quantity": 10,
                "required_certification_code": "LOGISTICS",
            },
            ngo_token,
            expected=201,
        )
        evaluation = request(
            base,
            "POST",
            "/api/v1/allocations/validate",
            {
                "ngo_id": ngo_id,
                "resource_code": code,
                "quantity": 6,
                "risk_level": "LOW",
            },
            coordinator,
        )
        if evaluation["fulfillable_quantity"] != 6:
            raise RuntimeError("La validación no habilitó la cantidad esperada")
        body = {
            "project_reference": project,
            "ngo_id": ngo_id,
            "risk_level": "LOW",
            "items": [{"resource_code": code, "quantity": 6}],
        }
        key = str(uuid.uuid4())
        allocation = request(base, "POST", "/api/v1/allocations", body, coordinator, key, 201)
        replay = request(base, "POST", "/api/v1/allocations", body, coordinator, key)
        inventory = request(base, "GET", f"/api/v1/resources/{code}")
        if replay["id"] != allocation["id"] or inventory["available_quantity"] != 4:
            raise RuntimeError("La adjudicación o su repetición modificó el stock incorrectamente")
        url = f"/api/v1/allocations/{allocation['id']}/close"
        request(base, "POST", url, token=ngo_token)
        closed = request(base, "POST", url, token=coordinator)
        inventory = request(base, "GET", f"/api/v1/resources/{code}")
        if closed["status"] != "CLOSED" or inventory["available_quantity"] != 10:
            raise RuntimeError("El cierre no liberó los recursos correctamente")
        results.append({"ngo_id": ngo_id, "resource_code": code, "allocation_id": allocation["id"]})
    awards = request(
        base, "GET", f"/api/v1/allocations?project_reference={project}", token=coordinator
    )
    if len(awards) != 2:
        raise RuntimeError("El proyecto no conserva ambas adjudicaciones")
    print(
        json.dumps(
            {"result": "ok", "project_reference": project, "closed_allocations": results}, indent=2
        )
    )


if __name__ == "__main__":
    main()
