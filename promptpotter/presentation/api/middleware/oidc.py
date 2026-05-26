"""OIDC middleware — Stage-1 sole identity ingress.

Reads the opaque session cookie, looks up the server-side session, and
populates `request.state.identity_ctx`. Does not enforce auth — that's
the `resolve_identity` dep's job. The fork between "authenticated" /
"unauthenticated" is one ContextVar-equivalent read upstream of every
route.

Per ADR-0002 no-drift gate #2: no JWT type ever appears past this
boundary. The middleware emits an `IdentityContext` (the only carrier
the rest of the codebase knows) — never raw tokens, never JWS frames.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response

from promptpotter.domain.identity import Issuer, TenantId, UserId
from promptpotter.infrastructure.identity.bundle import IdentityBundle
from promptpotter.shared.identity import IdentityContext

logger = logging.getLogger(__name__)

SESSION_COOKIE_NAME = "promptpotter_session"


def _identity_context_from_session(
    session_id: str, bundle: IdentityBundle
) -> IdentityContext | None:
    """Look up the session; return an `IdentityContext` or `None` if expired/unknown."""
    data = bundle.session_store.read(session_id)
    if data is None:
        return None
    return IdentityContext(
        user_id=UserId(data.user_id),
        tenant_id=TenantId(data.tenant_id),
        issuer=Issuer(data.issuer) if data.issuer else None,
        claims={
            "email": data.email,
            "provider": data.provider,
            "subject": data.subject,
        },
        capabilities=frozenset(),
    )


def install_oidc_middleware(app: FastAPI) -> None:
    """Register the OIDC HTTP middleware. Called once from `main.py`."""

    @app.middleware("http")
    async def oidc_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        bundle: IdentityBundle | None = getattr(request.app.state, "identity_bundle", None)
        identity_ctx: IdentityContext | None = None
        if bundle is not None:
            session_id = request.cookies.get(SESSION_COOKIE_NAME)
            if session_id:
                identity_ctx = _identity_context_from_session(session_id, bundle)
        request.state.identity_ctx = identity_ctx
        return await call_next(request)


__all__ = ["SESSION_COOKIE_NAME", "install_oidc_middleware"]
