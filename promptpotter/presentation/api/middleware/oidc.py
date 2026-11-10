from __future__ import annotations

from fastapi import FastAPI, Request
from starlette.types import ASGIApp, Receive, Scope, Send

from promptpotter.infrastructure.identity.bundle import IdentityBundle, identity_of_session
from promptpotter.shared.identity import IdentityContext

SESSION_COOKIE_NAME = "promptpotter_session"
# The browser the box's admin signed in from, on the parent domain, so the public site's visit log
# and this app's own count it as the owner rather than a visitor. It grants nothing.
OWNER_DEVICE_COOKIE_NAME = "pp-self"


def _identity_context_from_session(
    session_id: str, bundle: IdentityBundle
) -> IdentityContext | None:
    data = bundle.session_store.read(session_id)
    return None if data is None else identity_of_session(data, bundle)


class OIDCMiddleware:
    """Pure ASGI, never ``BaseHTTPMiddleware``: that buffers the response and breaks SSE."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        bundle: IdentityBundle | None = getattr(request.app.state, "identity_bundle", None)
        identity_ctx: IdentityContext | None = None
        if bundle is not None:
            session_id = request.cookies.get(SESSION_COOKIE_NAME)
            if session_id:
                identity_ctx = _identity_context_from_session(session_id, bundle)
        # request.state IS scope["state"] — set it here so the endpoint's Request reads it.
        scope.setdefault("state", {})["identity_ctx"] = identity_ctx
        await self.app(scope, receive, send)


def install_oidc_middleware(app: FastAPI) -> None:
    app.add_middleware(OIDCMiddleware)
