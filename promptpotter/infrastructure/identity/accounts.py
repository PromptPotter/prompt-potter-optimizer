from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path

from promptpotter.config.paths import ACCOUNTS_LOCK
from promptpotter.config.settings import LOCK_TIMEOUT, settings
from promptpotter.infrastructure.identity import bulletin_board
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
from promptpotter.infrastructure.identity.tokens import TOKEN_CHARS, TOKEN_PREFIX, token_of
from promptpotter.infrastructure.store.io import held_lock
from promptpotter.infrastructure.store.layout import tenant_workspace
from promptpotter.infrastructure.store.user_store import User, UserStore, read_accounts
from promptpotter.shared.errors import NextAct, UnauthorizedError
from promptpotter.shared.hashing import stable_hash
from promptpotter.shared.identity import (
    OWNER_COMMAND_CAPABILITIES,
    TERMINAL_IDENTITY_ID,
    AccessState,
    Carrier,
    IdentityContext,
    TenantId,
    UserId,
    safe_name,
)


def derive_user_id(issuer: str, subject: str) -> UserId:
    return UserId(safe_name(stable_hash([issuer, subject])))


logger = logging.getLogger(__name__)


def claim_default(*, projects_root: Path, user_id: str, email: str | None) -> str | None:
    """The host's account takes the terminal's: ONE act under the accounts lock, so no sign-in
    opens either directory meanwhile; its caller holds the admission gate over the terminal's
    cycles (`application/browser_sign_in.py::claim_box`). ``None`` once claimed; else why it refused, the
    terminal's tree left in place so the host's next sign-in asks again."""
    with held_lock(projects_root / ACCOUNTS_LOCK, wait_s=LOCK_TIMEOUT):
        user_dir = projects_root / user_id
        default_dir = projects_root / TERMINAL_IDENTITY_ID
        if not default_dir.is_dir():
            return None
        if user_dir.exists():
            return f"both {default_dir} and {user_dir} exist: merge or remove one, then sign in"
        # The terminal's record becomes the signer's before the rename, which commits the claim: a
        # crash between the two leaves the terminal's tree for the next sign-in to claim again.
        users = UserStore(default_dir)
        if users.load() is not None:
            users.update(
                lambda held: held.model_copy(
                    update={"user_id": user_id, "tenant_id": user_id, "email": email}
                )
            )
        bulletin_board.rekey(
            IdentityPaths(projects_root).bulletins, old_id=TERMINAL_IDENTITY_ID, new_id=user_id
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


def is_host_account(user_id: str, email: str | None) -> bool:
    """The terminal's own account, or the one `HOST_ADMIN_EMAIL` names: a live identity's `host`
    stamp (`account_identity`) and an account's on a directory walk alike."""
    return user_id == TERMINAL_IDENTITY_ID or is_declared_host(email)


def host_user_id(projects_root: Path) -> str | None:
    """The declared host's account once its sign-in opened one; ``None`` before."""
    return next(
        (
            account.user.user_id
            for account in read_accounts(projects_root)
            if isinstance(account.user, User)
            and account.user.user_id != TERMINAL_IDENTITY_ID
            and is_host_account(account.user.user_id, account.user.email)
        ),
        None,
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
    presented: IdentityContext, grant: PrincipalGrant, bundle: IdentityBundle
) -> IdentityContext:
    delegator = (
        None
        if grant.is_denied
        else UserStore(tenant_workspace(bundle.paths.projects_root, grant.delegated_by)).load()
    )
    if delegator is None:
        # A sub-principal's entitlement IS its grant, never the blocklist: revoked reads blocked,
        # and so does a grant onto no standing account, whose first act would open it.
        return replace(presented, access_state="blocked", capabilities=frozenset())
    # A delegate stands on its delegator's access: blocking the delegator blocks it (bl4.B7).
    access_state = resolve_access_state(delegator.email, bundle)
    return replace(
        presented,
        user_id=UserId(grant.delegated_by),
        tenant_id=TenantId(grant.delegated_by),
        access_state=access_state,
        claims={
            **presented.claims,
            "principal": presented.user_id,
            "delegated_by": grant.delegated_by,
        },
        capabilities=resolve_effective_capabilities(grant, _session_capabilities(access_state)),
    )


def account_identity(presented: IdentityContext, bundle: IdentityBundle) -> IdentityContext:
    """THE builder of who an account is, both doors': *presented* names the account as its door
    read it, and its grant, its delegated path, its access state and its host are read here."""
    grant = read_grant(bundle.paths.grants, presented.user_id)
    if grant is not None:
        # A delegate is never the host, whoever delegated it.
        return _delegated_identity(presented, grant, bundle)
    access_state = resolve_access_state(presented.email, bundle)
    return replace(
        presented,
        access_state=access_state,
        capabilities=_session_capabilities(access_state),
        host=is_host_account(presented.user_id, presented.email),
    )


def identity_of_session(data: SessionData, bundle: IdentityBundle) -> IdentityContext:
    return account_identity(
        IdentityContext(
            user_id=UserId(data.user_id),
            tenant_id=TenantId(data.tenant_id),
            email=data.email,
            provider=data.provider,
            claims={"subject": data.subject},
        ),
        bundle,
    )


_LIVE_TOKEN = NextAct(
    field="Authorization",
    value="Bearer and a live token of your account, minted in its API keys tab",
)


def _token_identity(carrier: Carrier, bundle: IdentityBundle) -> IdentityContext:
    """The token's account as a session of it reads, narrowed to the token's scopes; never the
    host's, whose exemption from every bucket never rides a bearer."""
    if not carrier.key.startswith(TOKEN_PREFIX) or len(carrier.key) != TOKEN_CHARS:
        raise UnauthorizedError(
            "That bearer is no PromptPotter token.",
            code="token_malformed",
            next_acts=(_LIVE_TOKEN,),
        )
    token = token_of(bundle.paths.tokens, carrier.key)
    user = (
        None
        if token is None
        else UserStore(tenant_workspace(bundle.paths.projects_root, token.user_id)).load()
    )
    if token is None or user is None:
        raise UnauthorizedError(
            "That token names no account here.", code="token_unknown", next_acts=(_LIVE_TOKEN,)
        )
    if token.revoked_at is not None:
        raise UnauthorizedError(
            f"That token was revoked at {token.revoked_at}.",
            code="token_revoked",
            next_acts=(_LIVE_TOKEN,),
        )
    account = account_identity(
        IdentityContext(
            user_id=UserId(user.user_id), tenant_id=TenantId(user.tenant_id), email=user.email
        ),
        bundle,
    )
    if account.host:
        raise UnauthorizedError(
            "The host acts through its own browser or terminal, never a bearer.",
            code="host_bearer_refused",
            next_acts=(NextAct(field="Cookie", value="the host's signed-in session"),),
        )
    return replace(account, capabilities=account.capabilities & token.scopes, token_id=token.id)


def identity_of_carrier(carrier: Carrier, bundle: IdentityBundle) -> IdentityContext | None:
    """THE caller door: a cookie's session or a bearer's token, as every request and every tick of
    its streams resolves it; ``None`` for a session that is gone, a refusal for a bearer that is."""
    if carrier.kind == "bearer":
        identity: IdentityContext | None = _token_identity(carrier, bundle)
    else:
        data = bundle.session_store.read(carrier.key)
        identity = None if data is None else identity_of_session(data, bundle)
    return None if identity is None else replace(identity, carried_by=carrier.kind)


def still_carried(opened: IdentityContext, carrier: Carrier, bundle: IdentityBundle) -> bool:
    """Whether *carrier* still resolves to *opened*: a revoke, a logout, an expiry, a block or a
    narrowed grant ends what it opened."""
    try:
        return identity_of_carrier(carrier, bundle) == opened
    except UnauthorizedError:
        return False
