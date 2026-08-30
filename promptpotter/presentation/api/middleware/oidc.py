"""OIDC middleware — the Stage-1 sole identity ingress; enforcement is ``resolve_identity``'s job.
Per ADR-0002 no-drift gate #2, no JWT type ever appears past this boundary."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from starlette.types import ASGIApp, Receive, Scope, Send

from promptpotter.domain.identity import Issuer, TenantId, UserId
from promptpotter.infrastructure.identity.blocklist import check_blocklist
from promptpotter.infrastructure.identity.bundle import IdentityBundle
from promptpotter.infrastructure.identity.grants import (
    PrincipalGrant,
    read_grant,
    resolve_effective_capabilities,
)
from promptpotter.infrastructure.identity.session import SessionData
from promptpotter.shared.identity import (
    ACCESS_ACTIVE,
    ACCESS_BLOCKED,
    OWNER_COMMAND_CAPABILITIES,
    IdentityContext,
)

logger = logging.getLogger(__name__)

SESSION_COOKIE_NAME = "promptpotter_session"


def resolve_access_state(email: str | None, bundle: IdentityBundle) -> str:
    """Is this account entitled to act? Signing up IS the grant, so this answers ``active`` for everyone the
    operator has not blocked. The ONE derivation — the capability set and the served ``access_state`` both
    read it, so an account cannot be blocked on one surface and active on another."""
    return (
        ACCESS_BLOCKED if check_blocklist(bundle.paths.blocklist, email).blocked else ACCESS_ACTIVE
    )


def _session_capabilities(access_state: str) -> frozenset[str]:
    """Capabilities for an authenticated web identity — every ENTITLED user owns their tenant, so each
    holds the owner set, the box's operator included. A BLOCKED account holds none: the dispatcher's
    `_require_capability_for` refuses every command with the same 404 a stranger already gets, so no
    surface needs its own check."""
    if access_state == ACCESS_BLOCKED:
        return frozenset()
    return OWNER_COMMAND_CAPABILITIES


def _delegated_identity(data: SessionData, grant: PrincipalGrant) -> IdentityContext:
    """Rebind a sub-principal to act inside its delegator's tenant (ADR-0005 §1); its own identity is kept
    in ``claims["principal"]``. Capabilities are the grant INTERSECTED with the owner set, never admin."""
    if grant.is_denied:
        return IdentityContext(
            user_id=UserId(data.user_id),
            tenant_id=TenantId(data.tenant_id),
            issuer=Issuer(data.issuer) if data.issuer else None,
            claims={
                "email": data.email,
                "provider": data.provider,
                "subject": data.subject,
                # A sub-principal's entitlement IS its grant, never the blocklist — it acts inside a
                # delegator's tenant and was provisioned by the admin channel. Revoked reads as blocked
                # for the same reason a blocked signup does: authenticated, holding nothing.
                "access_state": ACCESS_BLOCKED,
            },
            capabilities=frozenset(),
        )
    return IdentityContext(
        user_id=UserId(grant.delegated_by),
        tenant_id=TenantId(grant.delegated_by),
        issuer=Issuer(data.issuer) if data.issuer else None,
        claims={
            "email": data.email,
            "provider": data.provider,
            "subject": data.subject,
            "principal": data.user_id,
            "delegated_by": grant.delegated_by,
            "spend_ceiling_usd": grant.spend_ceiling_usd,
            "access_state": ACCESS_ACTIVE,
        },
        capabilities=resolve_effective_capabilities(grant, OWNER_COMMAND_CAPABILITIES),
    )


def _identity_context_from_session(
    session_id: str, bundle: IdentityBundle
) -> IdentityContext | None:
    """The session's ``IdentityContext``, or ``None`` if expired/unknown. A grant-carrying user resolves
    to a delegated identity in their delegator's tenant; everyone else owns their own."""
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
        claims={
            "email": data.email,
            "provider": data.provider,
            "subject": data.subject,
            "access_state": access_state,
        },
        capabilities=_session_capabilities(access_state),
    )


class OIDCMiddleware:
    """Pure-ASGI identity ingress. ASGI and NOT ``BaseHTTPMiddleware``, which buffers the whole response
    through a memory stream: that breaks a streaming SSE response and hangs graceful shutdown."""

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
