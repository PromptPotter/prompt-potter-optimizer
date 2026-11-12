from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from promptpotter.config.paths import default_projects_root
from promptpotter.infrastructure.identity.blocklist import check_blocklist
from promptpotter.infrastructure.identity.default_claim import is_declared_host
from promptpotter.infrastructure.identity.github import GitHubProviderClient
from promptpotter.infrastructure.identity.google import GoogleProviderClient
from promptpotter.infrastructure.identity.grants import (
    PrincipalGrant,
    read_grant,
    resolve_effective_capabilities,
)
from promptpotter.infrastructure.identity.jwks import JWKSCache
from promptpotter.infrastructure.identity.paths import IdentityPaths
from promptpotter.infrastructure.identity.provider_config import (
    ProviderConfigBundle,
    SignInClient,
    load_provider_config,
)
from promptpotter.infrastructure.identity.session import OIDCSessionStore, SessionData
from promptpotter.infrastructure.store.layout import tenant_workspace
from promptpotter.infrastructure.store.user_store import UserStore
from promptpotter.shared.identity import (
    OWNER_COMMAND_CAPABILITIES,
    AccessState,
    IdentityContext,
    Issuer,
    TenantId,
    UserId,
)


@dataclass
class IdentityBundle:
    paths: IdentityPaths
    config: ProviderConfigBundle
    session_store: OIDCSessionStore
    jwks_cache: JWKSCache
    # One row per CONFIGURED provider, keyed by its `SUPPORTED_PROVIDERS` name.
    clients: Mapping[str, SignInClient]


def build_identity_bundle(paths: IdentityPaths) -> IdentityBundle:
    paths.root.mkdir(parents=True, exist_ok=True)
    paths.sessions_dir.mkdir(parents=True, exist_ok=True)
    config = load_provider_config(paths.provider_config)
    jwks_cache = JWKSCache()
    clients: dict[str, SignInClient] = {}
    if config.google is not None:
        clients["google"] = GoogleProviderClient(config.google, jwks_cache)
    if config.github is not None:
        clients["github"] = GitHubProviderClient(config.github)
    return IdentityBundle(
        paths=paths,
        config=config,
        session_store=OIDCSessionStore(paths.sessions_dir),
        jwks_cache=jwks_cache,
        clients=clients,
    )


def resolve_access_state(email: str | None, bundle: IdentityBundle) -> AccessState:
    return "blocked" if check_blocklist(bundle.paths.blocklist, email).blocked else "active"


def _session_capabilities(access_state: AccessState) -> frozenset[str]:
    if access_state == "blocked":
        return frozenset()
    return OWNER_COMMAND_CAPABILITIES


def _delegated_identity(
    data: SessionData, grant: PrincipalGrant, bundle: IdentityBundle
) -> IdentityContext:
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
    # A delegate stands on its delegator's access: blocking the delegator blocks it (bl4.B7).
    delegator = UserStore(tenant_workspace(default_projects_root(), grant.delegated_by)).load()
    access_state = resolve_access_state(None if delegator is None else delegator.email, bundle)
    return IdentityContext(
        user_id=UserId(grant.delegated_by),
        tenant_id=TenantId(grant.delegated_by),
        issuer=Issuer(data.issuer) if data.issuer else None,
        email=data.email,
        provider=data.provider,
        access_state=access_state,
        claims={
            "subject": data.subject,
            "principal": data.user_id,
            "delegated_by": grant.delegated_by,
        },
        capabilities=resolve_effective_capabilities(grant, _session_capabilities(access_state)),
    )


def identity_of_session(data: SessionData, bundle: IdentityBundle) -> IdentityContext:
    grant = read_grant(bundle.paths.grants, data.user_id)
    if grant is not None:
        return _delegated_identity(data, grant, bundle)
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
        # A delegate (above) is never the host, whoever delegated it.
        host=is_declared_host(data.email),
    )
