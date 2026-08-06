"""OIDC middleware — the Stage-1 sole identity ingress; enforcement is ``resolve_identity``'s job.
Per ADR-0002 no-drift gate #2, no JWT type ever appears past this boundary."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from starlette.types import ASGIApp, Receive, Scope, Send

from promptpotter.domain.identity import Issuer, TenantId, UserId
from promptpotter.infrastructure.identity.bundle import IdentityBundle
from promptpotter.infrastructure.identity.grants import (
    PrincipalGrant,
    read_grant,
    resolve_effective_capabilities,
)
from promptpotter.infrastructure.identity.migration import registered_user_id
from promptpotter.infrastructure.identity.session import SessionData
from promptpotter.shared.identity import (
    ADMIN_CAPABILITIES,
    OWNER_COMMAND_CAPABILITIES,
    IdentityContext,
)

logger = logging.getLogger(__name__)

SESSION_COOKIE_NAME = "promptpotter_session"


def _session_capabilities(user_id: str, bundle: IdentityBundle) -> frozenset[str]:
    """Capabilities for an authenticated web identity — every user owns their tenant, so each holds the
    owner set. Admin is the PINNED marker identity: an env flag here would make every signup an admin."""
    admin_uid = registered_user_id(bundle.paths.default_claim_marker)
    if admin_uid is not None and user_id == admin_uid:
        return OWNER_COMMAND_CAPABILITIES | ADMIN_CAPABILITIES
    return OWNER_COMMAND_CAPABILITIES


def _delegated_identity(data: SessionData, grant: PrincipalGrant) -> IdentityContext:
    """Rebind a sub-principal to act inside its delegator's tenant (ADR-0005 §1); its own identity is kept
    in ``claims["principal"]``. Capabilities are the grant INTERSECTED with the owner set, never admin."""
    if grant.is_denied:
        return IdentityContext(
            user_id=UserId(data.user_id),
            tenant_id=TenantId(data.tenant_id),
            issuer=Issuer(data.issuer) if data.issuer else None,
            claims={"email": data.email, "provider": data.provider, "subject": data.subject},
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
    return IdentityContext(
        user_id=UserId(data.user_id),
        tenant_id=TenantId(data.tenant_id),
        issuer=Issuer(data.issuer) if data.issuer else None,
        claims={
            "email": data.email,
            "provider": data.provider,
            "subject": data.subject,
        },
        capabilities=_session_capabilities(data.user_id, bundle),
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


__all__ = ["SESSION_COOKIE_NAME", "install_oidc_middleware"]
