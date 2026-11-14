from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from promptpotter.config.paths import ACCOUNTS_LOCK, default_projects_root, jobs_dir
from promptpotter.config.settings import LOCK_TIMEOUT, settings
from promptpotter.domain.cycle_paths import WorkspaceDir
from promptpotter.infrastructure.holders import holder_of
from promptpotter.infrastructure.identity.grants import (
    IdentityPaths,
    PrincipalGrant,
    check_blocklist,
    read_grant,
    resolve_effective_capabilities,
)
from promptpotter.infrastructure.identity.oidc import (
    GitHubProviderClient,
    GoogleProviderClient,
    JWKSCache,
    OIDCSessionStore,
    ProviderConfigBundle,
    SessionData,
    SignInClient,
    load_provider_config,
)
from promptpotter.infrastructure.store.campaign_store.store import CampaignStore
from promptpotter.infrastructure.store.io import held_lock
from promptpotter.infrastructure.store.layout import tenant_workspace
from promptpotter.infrastructure.store.user_store import User, UserStore, read_accounts
from promptpotter.shared.hashing import stable_hash
from promptpotter.shared.identity import (
    OWNER_COMMAND_CAPABILITIES,
    TERMINAL_IDENTITY_ID,
    AccessState,
    IdentityContext,
    Issuer,
    TenantId,
    UserId,
    safe_name,
)


def derive_user_id(issuer: str, subject: str) -> UserId:
    return UserId(safe_name(stable_hash([issuer, subject])))


logger = logging.getLogger(__name__)


def claim_default(*, projects_root: Path, user_id: str, email: str | None) -> str | None:
    """The host's account takes the terminal's: ONE act under the accounts lock, so no sign-in
    opens either directory meanwhile, refused while any job or run holds a terminal cycle
    (`holders.py::holder_of`). ``None`` once claimed; else why it refused, the terminal's tree
    left in place so the host's next sign-in asks again."""
    with held_lock(projects_root / ACCOUNTS_LOCK, wait_s=LOCK_TIMEOUT):
        user_dir = projects_root / user_id
        default_dir = projects_root / TERMINAL_IDENTITY_ID
        if not default_dir.is_dir():
            return None
        if user_dir.exists():
            return f"both {default_dir} and {user_dir} exist: merge or remove one, then sign in"
        jobs = jobs_dir(projects_root)
        terminal = CampaignStore(WorkspaceDir(default_dir), jobs)
        if any(
            holder_of(jobs, cycle_dir) is not None
            for campaign_id in terminal.list_campaign_ids()
            for cycle_dir in terminal.campaign_cycle_dirs(campaign_id)
        ):
            return f"a run holds a cycle under {default_dir}: pause it or let it end, then sign in"
        # The terminal's record becomes the signer's before the rename, which commits the claim: a
        # crash between the two leaves the terminal's tree for the next sign-in to claim again.
        users = UserStore(default_dir)
        if users.load() is not None:
            users.update(
                lambda held: held.model_copy(
                    update={"user_id": user_id, "tenant_id": user_id, "email": email}
                )
            )
        default_dir.rename(user_dir)
        logger.info("Claimed default tenant: %s → %s", default_dir, user_dir)
    return None


def is_declared_host(email: str | None) -> bool:
    """The account signed in with `HOST_ADMIN_EMAIL`, a VERIFIED email (every provider passes only
    one), so whoever holds that inbox. Unset means nobody is: signing up is the grant, so "first one
    in wins" is unsafe."""
    declared = settings.HOST_ADMIN_EMAIL.strip().lower()
    return bool(declared) and (email or "").strip().lower() == declared


def is_host_account(user: User) -> bool:
    """The terminal's own account, or the one `HOST_ADMIN_EMAIL` names."""
    return user.user_id == TERMINAL_IDENTITY_ID or is_declared_host(user.email)


def host_user_id(projects_root: Path) -> str | None:
    """The declared host's account once its sign-in opened one; ``None`` before."""
    return next(
        (
            account.user.user_id
            for account in read_accounts(projects_root)
            if isinstance(account.user, User)
            and account.user.user_id != TERMINAL_IDENTITY_ID
            and is_host_account(account.user)
        ),
        None,
    )


def is_host(identity: IdentityContext) -> bool:
    """The fact its seam stamped (`IdentityContext.host`)."""
    return identity.host


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
