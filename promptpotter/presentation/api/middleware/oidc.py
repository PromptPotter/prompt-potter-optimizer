from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from starlette.types import ASGIApp, Receive, Scope, Send

from promptpotter.infrastructure.identity.blocklist import check_blocklist
from promptpotter.infrastructure.identity.bundle import IdentityBundle
from promptpotter.infrastructure.identity.grants import (
    PrincipalGrant,
    read_grant,
    resolve_effective_capabilities,
)
from promptpotter.infrastructure.identity.session import SessionData
from promptpotter.shared.identity import (
    OWNER_COMMAND_CAPABILITIES,
    AccessState,
    IdentityContext,
    Issuer,
    TenantId,
    UserId,
)

logger = logging.getLogger(__name__)

SESSION_COOKIE_NAME = "promptpotter_session"


def resolve_access_state(email: str | None, bundle: IdentityBundle) -> AccessState:
    return "blocked" if check_blocklist(bundle.paths.blocklist, email).blocked else "active"


def _session_capabilities(access_state: AccessState) -> frozenset[str]:
    if access_state == "blocked":
        return frozenset()
    return OWNER_COMMAND_CAPABILITIES


def _delegated_identity(data: SessionData, grant: PrincipalGrant) -> IdentityContext:
    if grant.is_denied:
        return IdentityContext(
            user_id=UserId(data.user_id),
            tenant_id=TenantId(data.tenant_id),
            issuer=Issuer(data.issuer) if data.issuer else None,
            email=data.email,
            provider=data.provider,
            # A sub-principal's entitlement IS its grant, never the blocklist: revoked reads blocked.
            access_state="blocked",
            claims={"subject": data.subject},
            capabilities=frozenset(),
        )
    return IdentityContext(
        user_id=UserId(grant.delegated_by),
        tenant_id=TenantId(grant.delegated_by),
        issuer=Issuer(data.issuer) if data.issuer else None,
        email=data.email,
        provider=data.provider,
        claims={
            "subject": data.subject,
            "principal": data.user_id,
            "delegated_by": grant.delegated_by,
            "spend_ceiling_usd": grant.spend_ceiling_usd,
        },
        capabilities=resolve_effective_capabilities(grant, OWNER_COMMAND_CAPABILITIES),
    )


def _identity_context_from_session(
    session_id: str, bundle: IdentityBundle
) -> IdentityContext | None:
    data = bundle.session_store.read(session_id)
    if data is None:
        return None
    grant = read_grant(bundle.paths.grants, data.user_id)
    if grant is not None:
        return _delegated_identity(data, grant)
    access_state = resolve_access_state(data.email, bundle)
    return IdentityContext(
        user_id=UserId(data.user_id),
        tenant_id=TenantId(data.tenant_id),
        issuer=Issuer(data.issuer) if data.issuer else None,
        email=data.email,
        provider=data.provider,
        access_state=access_state,
        claims={"subject": data.subject},
        capabilities=_session_capabilities(access_state),
    )


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


__all__ = ["SESSION_COOKIE_NAME", "install_oidc_middleware", "resolve_access_state"]
