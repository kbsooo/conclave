"""API key authentication for the meeting room server.

Uses Bearer token in the Authorization header.
Timing-safe comparison to prevent timing attacks.
"""

from __future__ import annotations

import hmac
import secrets

from aiohttp import web


def generate_api_key() -> str:
    """Generate a cryptographically secure API key."""
    return secrets.token_urlsafe(32)


def auth_middleware(api_keys: list[str]) -> web.middleware:
    """Create an aiohttp middleware that validates API keys.

    If api_keys is empty, all requests are allowed (no auth).
    """

    @web.middleware
    async def middleware(request: web.Request, handler):
        # No auth configured — allow everything
        if not api_keys:
            return await handler(request)

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return web.json_response(
                {"error": "Missing or invalid Authorization header. Use: Bearer <api_key>"},
                status=401,
            )

        token = auth_header[7:]  # strip "Bearer "

        # Timing-safe comparison against all valid keys
        if not any(hmac.compare_digest(token, key) for key in api_keys):
            return web.json_response({"error": "Invalid API key"}, status=401)

        return await handler(request)

    return middleware
