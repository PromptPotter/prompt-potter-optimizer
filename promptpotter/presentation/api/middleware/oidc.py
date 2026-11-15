from __future__ import annotations

import asyncio

from fastapi import FastAPI, Request
from starlette.types import ASGIApp, Receive, Scope, Send

from promptpotter.infrastructure.identity.accounts import IdentityBundle, identity_of_carrier
from promptpotter.shared.errors import NextAct, PotterError, UnauthorizedError
from promptpotter.shared.identity import Carrier, IdentityContext

SESSION_COOKIE_NAME = "promptpotter_session"
# The browser the box's admin signed in from, on the parent domain, so the public site's visit log
# and this app's own count it as the owner rather than a visitor. It grants nothing.
OWNER_DEVICE_COOKIE_NAME = "pp-self"
_BEARER_SCHEME = "bearer "

type Resolved = IdentityContext | PotterError | None


def _carrier(request: Request) -> Carrier | None:
    """What the request presented: a session cookie or a bearer, never both."""
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    authorization = request.headers.get("authorization", "")
    if not authorization:
        return Carrier("cookie", session_id) if session_id else None
    if session_id:
        raise UnauthorizedError(
            "A request carries one credential: a session cookie or a bearer token, not both.",
            code="credentials_doubled",
            next_acts=(NextAct(field="Authorization", value="absent where the cookie is sent"),),
        )
    if not authorization.lower().startswith(_BEARER_SCHEME):
        raise UnauthorizedError(
            "Authorization takes a bearer token.",
            code="token_malformed",
            next_acts=(NextAct(field="Authorization", value="Bearer and the token's text"),),
        )
    return Carrier("bearer", authorization[len(_BEARER_SCHEME) :].strip())


def _resolved(request: Request, bundle: IdentityBundle) -> tuple[Resolved, Carrier | None]:
    try:
        carrier = _carrier(request)
        return (None, None) if carrier is None else (identity_of_carrier(carrier, bundle), carrier)
    except PotterError as refused:
        return refused, None


class OIDCMiddleware:
    """Pure ASGI, never ``BaseHTTPMiddleware``: that buffers the response and breaks SSE. A refusal
    rides the scope to `deps.py::resolve_identity`, which raises it where a route asks who called."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        bundle: IdentityBundle | None = getattr(request.app.state, "identity_bundle", None)
        resolved: Resolved = None
        carrier: Carrier | None = None
        if bundle is not None:
            resolved, carrier = await asyncio.to_thread(_resolved, request, bundle)
        # request.state IS scope["state"] — set it here so the endpoint's Request reads it; the
        # carrier rides beside the identity to its `Caller`, never inside it.
        scope.setdefault("state", {}).update(identity_ctx=resolved, carrier=carrier)
        await self.app(scope, receive, send)


def install_oidc_middleware(app: FastAPI) -> None:
    app.add_middleware(OIDCMiddleware)
