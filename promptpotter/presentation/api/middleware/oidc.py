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
    """Capabilities for an authenticated web identity.

    Every authenticated user owns their own tenant, so each holds the full
    :data:`OWNER_COMMAND_CAPABILITIES` command set — bounded to their workspace by
    tenant-isolation, enforced per-verb at the dispatcher gate.

    WHO is host-admin, Stage 1 (web / OIDC): the one identity pinned in the
    default-claim marker — the registered developer, the web analogue of ADR-0004's
    chat-id lock. A first-time signup never matches the marker, and a fresh box with
    no marker has no admin at all (secure-by-default). This is the deliberate twin of
    Stage 0's `_admin_caps_from_env`; **never merge them** — a process-wide env flag
    here would make every signup an admin.

    A delegated sub-principal (ADR-0005) resolves an ATTENUATED subset from the sealed
    grant store instead of the blanket owner set — that is the seam the sub-user model
    plugs into, without touching the dispatcher.
    """
    admin_uid = registered_user_id(bundle.paths.default_claim_marker)
    if admin_uid is not None and user_id == admin_uid:
        return OWNER_COMMAND_CAPABILITIES | ADMIN_CAPABILITIES
    return OWNER_COMMAND_CAPABILITIES


def _delegated_identity(data: SessionData, grant: PrincipalGrant) -> IdentityContext:
    """Rebind a sub-principal to act inside its delegator's tenant (ADR-0005 §1).

    The delegate authenticates as itself but acts *as* the delegator for ownership
    — `user_id`/`tenant_id` become the delegator's, so every owner-gated read and
    command reaches the delegator's workspace, reusing that machinery unchanged.
    Its own identity is preserved in `claims["principal"]` for the audit trail.
    Capabilities are the grant INTERSECTED with the owner set (never admin) — a
    fail-secure/over-broad grant collapses to no command caps in its own tenant.
    """
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
    """Look up the session; return an `IdentityContext` or `None` if expired/unknown.

    A user carrying a grant (ADR-0005) resolves to a delegated identity acting in
    their delegator's tenant; everyone else is a first-class owner of their own.
    """
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
    """Pure-ASGI identity ingress — reads the session cookie, resolves an
    ``IdentityContext``, stamps it on ``scope["state"]`` for the ``resolve_identity`` dep.

    ASGI, not ``BaseHTTPMiddleware``, on purpose: ``BaseHTTPMiddleware`` buffers the
    whole response through an anyio memory stream, which breaks a streaming
    ``EventSourceResponse`` on client-disconnect / server-shutdown — a lingering SSE
    subscription then hangs graceful shutdown and raises ``RuntimeError: No response
    returned``. A pass-through ASGI middleware forwards ``send`` untouched, so the SSE
    highway keeps its own disconnect/shutdown teardown.
    """

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
    """Register the OIDC ASGI middleware. Called once from `main.py`."""
    app.add_middleware(OIDCMiddleware)


__all__ = ["SESSION_COOKIE_NAME", "install_oidc_middleware"]
