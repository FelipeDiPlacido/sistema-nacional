"""Bound request frequency before parsing bodies, hashing passwords or accessing the DB."""

import math
import time

from limits import RateLimitItemPerHour, RateLimitItemPerMinute
from limits.storage import MemoryStorage
from limits.strategies import FixedWindowRateLimiter
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.config import Settings

RATE_LIMIT_RESPONSE = {
    "description": "Se alcanzó el límite de solicitudes. Reintentar tras Retry-After segundos.",
    "headers": {
        "Retry-After": {
            "description": "Segundos de espera antes de reintentar",
            "schema": {"type": "integer"},
        }
    },
    "content": {
        "application/json": {
            "example": {"detail": "Demasiadas solicitudes. Intentá nuevamente más tarde."}
        }
    },
}


class RequestLimitsMiddleware:
    def __init__(self, app: ASGIApp, settings: Settings):
        self.app = app
        self.limiter = FixedWindowRateLimiter(MemoryStorage())
        self.policies = {
            "general": (
                RateLimitItemPerMinute(settings.rate_limit_global_requests_per_minute),
                RateLimitItemPerMinute(settings.rate_limit_requests_per_minute),
            ),
            "login": (
                RateLimitItemPerMinute(settings.rate_limit_global_login_per_minute),
                RateLimitItemPerMinute(settings.rate_limit_login_per_minute),
            ),
            "registration": (
                RateLimitItemPerHour(settings.rate_limit_global_registration_per_hour),
                RateLimitItemPerHour(settings.rate_limit_registration_per_hour),
            ),
        }

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope["path"].rstrip("/")
        # Render probes must remain available even after a client exhausts its quota.
        if scope["method"] in {"GET", "HEAD"} and path in {"/health", "/ready"}:
            await self.app(scope, receive, send)
            return

        # Use the ASGI server's address, never client-supplied forwarding headers.
        # A proxy may group clients. Global limits still apply if addresses rotate.
        peer = scope.get("client")
        client = peer[0] if peer else "unknown"
        policies = ["general"]
        if scope["method"] == "POST":
            if path == "/api/v1/auth/login":
                policies.append("login")
            elif path == "/api/v1/ngos":
                policies.append("registration")

        for name in policies:
            global_limit, client_limit = self.policies[name]
            # Check the global bucket first to bound the number of client buckets.
            for item, kind, key in (
                (global_limit, "global", "service"),
                (client_limit, "client", client),
            ):
                identifiers = ("national-api", name, kind, key)
                if not self.limiter.hit(item, *identifiers):
                    window = self.limiter.get_window_stats(item, *identifiers)
                    retry_after = max(1, math.ceil(window.reset_time - time.time()))
                    response = JSONResponse(
                        {"detail": "Demasiadas solicitudes. Intentá nuevamente más tarde."},
                        status_code=429,
                        headers={"Retry-After": str(retry_after), "Cache-Control": "no-store"},
                    )
                    await response(scope, receive, send)
                    return

        await self.app(scope, receive, send)
