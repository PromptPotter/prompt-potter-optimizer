"""An account's life, written and read back.

Each fact is stamped ONCE and written to the account's consent trail before ``user.json``, so no
account exists without its record; a write that fails between the two repeats the record on the
retry, which reads as the same fact twice and never as one missing. A terms acceptance is its
consent line alone (`user_store.py::UserStore.accepted_terms`).
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Literal, NamedTuple

from pydantic import Field, SecretStr

from promptpotter.config.paths import ACCOUNTS_LOCK, default_projects_root, jobs_dir
from promptpotter.config.settings import LOCK_TIMEOUT, TERMS_VERSION, settings
from promptpotter.domain.command_kinds import CommandKind, Door, filable
from promptpotter.domain.records.run_records import AccountEvent, AccountEventRecord, Device
from promptpotter.domain.said import Tone
from promptpotter.domain.spend import Budget
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.identity import accounts, tokens
from promptpotter.infrastructure.identity import grants as grant_files
from promptpotter.infrastructure.identity.grants import (
    PrincipalGrant,
    default_identity_paths,
    grant_principal,
    list_grants,
    revoke_principal,
)
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.llm.registry import providers
from promptpotter.infrastructure.store.account_trail import (
    TrailLine,
    in_time_order,
    workspace_lines,
)
from promptpotter.infrastructure.store.campaign_store.store import CampaignStore
from promptpotter.infrastructure.store.io import held_lock
from promptpotter.infrastructure.store.layout import tenant_workspace
from promptpotter.infrastructure.store.stores import Stores, build_stores, tenant_stores
from promptpotter.infrastructure.store.user_store import (
    AccountDir,
    ConsentEvent,
    ConsentLine,
    Torn,
    User,
    UserStore,
    count_accounts,
    read_accounts,
)
from promptpotter.infrastructure.store.wallet_store import CouponGrant, GrantStore
from promptpotter.shared.clock import epoch_seconds, utcnow_iso
from promptpotter.shared.errors import (
    BadRequestError,
    ConflictError,
    FileUnreadableError,
    NextAct,
    NotFoundError,
    PayloadInvalidError,
    PotterError,
)
from promptpotter.shared.identity import (
    DELEGABLE_CAPABILITIES,
    AccessState,
    IdentityContext,
    acting_principal,
    capabilities_from_names,
    capability_names,
    default_identity,
    display_name,
)
from promptpotter.shared.worded import Worded


def _consent(
    stores: Stores,
    event: ConsentEvent,
    *,
    at: str,
    subject: str = "",
    device: Device | None = None,
) -> None:
    stores.users.record(
        ConsentLine(
            event=event,
            user_id=acting_principal(stores.identity).id,
            subject=subject,
            device=device,
            at=at,
        )
    )


def _record(stores: Stores, event: AccountEvent, *, subject: str) -> None:
    CycleEventLog.open_workspace(stores.base_dir).append(
        AccountEventRecord(
            event=event, user_id=acting_principal(stores.identity).id, subject=subject
        )
    )


class Arrival(NamedTuple):
    user: User
    # This call opened the account: the one moment there is somebody new to announce.
    opened: bool


SignInRefusal = Literal[
    "state_invalid_or_expired",
    "code_exchange_failed",
    "provider_returned_error",
    "callback_missing_params",
    "signin_unavailable",
    "accounts_full",
]


class SignInRefusalInfo(NamedTuple):
    label: str
    tone: Tone


SIGN_IN_REFUSAL_INFO: Worded[SignInRefusal, SignInRefusalInfo] = Worded(
    {
        "state_invalid_or_expired": SignInRefusalInfo(
            "That sign-in took too long and expired. Try Continue with Google again.", "warn"
        ),
        "code_exchange_failed": SignInRefusalInfo(
            "We couldn't verify your sign-in with Google. Try again.", "warn"
        ),
        "provider_returned_error": SignInRefusalInfo(
            "Google sign-in was cancelled. Try again when you're ready.", "attention"
        ),
        "callback_missing_params": SignInRefusalInfo(
            "That sign-in link was incomplete. Try again.", "warn"
        ),
        "signin_unavailable": SignInRefusalInfo(
            "Sign-in is temporarily unavailable. Open a GitHub issue and we'll fix it.", "danger"
        ),
        "accounts_full": SignInRefusalInfo(
            "This server has reached its account limit, so it can't open a new account. "
            "If you already have one, sign in with it.",
            "warn",
        ),
    }
)


class AccountCeilingError(PotterError):
    """The box holds `Settings.MAX_ACCOUNTS` accounts, so no new one opens; an existing one is untouched."""

    http_status = 403
    code = "accounts_full"


def arrive(stores: Stores, *, device: Device | None) -> Arrival:
    """The ONE place an account comes into being, whichever request reaches it first."""
    existing = stores.users.load()
    if existing is not None:
        return Arrival(existing, opened=False)
    # Held from the count to the write, or two first sign-ins at the ceiling both read room for one.
    with held_lock(stores.projects_root / ACCOUNTS_LOCK, wait_s=LOCK_TIMEOUT):
        # A concurrent first request of this same person may have opened it while this one waited.
        existing = stores.users.load()
        if existing is not None:
            return Arrival(existing, opened=False)
        held = count_accounts(stores.projects_root)
        if not stores.identity.host and held >= settings.MAX_ACCOUNTS:
            raise AccountCeilingError(
                f"This box holds its ceiling of {settings.MAX_ACCOUNTS} accounts "
                f"({held} now), so it opens no new one.",
                details={"accounts": held, "ceiling": settings.MAX_ACCOUNTS},
            )
        user = _opening(stores.identity)
        _consent(stores, "account_created", at=user.created_at, device=device)
        stores.users.save(user)
    return Arrival(user, opened=True)


def _opening(identity: IdentityContext) -> User:
    return User(
        user_id=str(identity.user_id),
        tenant_id=str(identity.tenant_id),
        email=identity.email,
        created_at=utcnow_iso(),
    )


def account_of(stores: Stores) -> User:
    """The account an act charges or edits, opened first where none is."""
    return arrive(stores, device=None).user


def account_read(stores: Stores) -> User | None:
    """The account a read answers, writing nothing; ``None`` for one not opened yet."""
    return stores.users.load()


def account_or_opening(stores: Stores) -> User:
    """The limits and preferences the caller's account holds, writing nothing: one not opened yet
    holds what `arrive` would open it with."""
    return account_read(stores) or _opening(stores.identity)


def sign_in(stores: Stores, *, device: Device | None) -> Arrival:
    arrival = arrive(stores, device=device)
    _consent(
        stores,
        "signed_in",
        at=utcnow_iso(),
        subject=stores.identity.provider or "",
        device=device,
    )
    return arrival


class ConnectedAccount(StrictModel):
    """One OIDC provider bound to the active session."""

    provider: str
    email: str | None


class MeResponse(StrictModel):
    """The caller's identity envelope, with the provider and consent state the account and consent gates read."""

    user_id: str
    tenant_id: str
    email: str | None
    name: str | None
    provider: str | None
    connected_accounts: list[ConnectedAccount]
    available_providers: list[str]
    capabilities: list[str]
    filable: list[CommandKind] = Field(
        description="The kinds this account may file (`command_kinds.py::filable`): what an act "
        "on the account (its campaigns' lifecycle, its usage, its keys) is drawn by."
    )
    access_state: AccessState
    terms_version: str
    terms_accepted_version: str | None


def me(stores: Stores, user: User | None, *, configured: Iterable[str]) -> MeResponse:
    """``available_providers`` is the *configured* ones minus the connected one; *user* is
    ``None`` for an account not opened yet, which has accepted no terms. Consent is the acting
    principal's: a delegate accepts for itself, never by its delegator's acceptance."""
    identity = stores.identity
    accepted = None if user is None else stores.users.accepted_terms(acting_principal(identity).id)
    email, provider = identity.email, identity.provider
    return MeResponse(
        user_id=str(identity.user_id),
        tenant_id=str(identity.tenant_id),
        email=email,
        name=display_name(email),
        provider=provider,
        connected_accounts=[ConnectedAccount(provider=provider, email=email)] if provider else [],
        available_providers=sorted(set(configured) - {provider}),
        capabilities=sorted(identity.capabilities),
        filable=list(filable(identity)),
        access_state=identity.access_state,
        terms_version=TERMS_VERSION,
        terms_accepted_version=accepted,
    )


class UserSettings(StrictModel):
    """The caller's per-user preferences."""

    demo_mode_enabled: bool


def user_settings(stores: Stores) -> UserSettings:
    return UserSettings(demo_mode_enabled=account_or_opening(stores).demo_mode_enabled)


def set_user_settings(stores: Stores, settings_sent: UserSettings) -> UserSettings:
    account_of(stores)
    stores.users.update(lambda user: user.model_copy(update=settings_sent.model_dump()))
    return settings_sent


class TermsConsent(StrictModel):
    """The consent state after an accept, in the fields ``/me`` reports it."""

    terms_version: str
    terms_accepted_version: str | None


def accept_terms(stores: Stores, version: str) -> TermsConsent:
    """``accepted_at`` is this server's clock, never the client's: its legal weight rests on that.
    A version other than the live ``TERMS_VERSION`` is refused, so the gate re-renders current text."""
    if version != TERMS_VERSION:
        raise ConflictError(
            "Terms version is out of date — reload to accept the current terms.",
            code="terms_version_stale",
            details={"expected": TERMS_VERSION, "received": version},
        )
    account_of(stores)
    # The acceptance IS its consent line (`UserStore.accepted_terms`): no account rewrite loses it.
    _consent(stores, "terms_accepted", at=utcnow_iso(), subject=version)
    return TermsConsent(terms_version=TERMS_VERSION, terms_accepted_version=version)


def record_refusal(stores: Stores, *, subject: str) -> None:
    """A person's attempt the server turned down: without it, a failed first try leaves no trace."""
    _record(stores, "request_refused", subject=subject)


def record_upload(stores: Stores, *, filename: str, kept: Path) -> None:
    """Names the kept bytes beside the refusal that may follow, so the trail leads to the file."""
    _record(stores, "uploaded", subject=f"{filename} -> uploads/{kept.name}")


class Trail(NamedTuple):
    # Whose it is: empty where no readable account carries the email asked for.
    own: frozenset[str]
    lines: list[TrailLine]
    # Workspaces that could not be read, so what they hold is missing from ``lines``.
    unreadable: list[str]


def tenant_trail(stores: Stores) -> Trail:
    return Trail(
        frozenset({acting_principal(stores.identity).id}),
        in_time_order(workspace_lines(stores)),
        [],
    )


def _scanned(
    projects_root: Path, accounts: Iterable[AccountDir], unreadable: list[str]
) -> Iterator[tuple[AccountDir, list[TrailLine]]]:
    """A ledger or file that will not read costs its own workspace and no other: it is named, then
    skipped."""
    for account in accounts:
        try:
            found = list(workspace_lines(tenant_stores(projects_root, account.workspace)))
        except (OSError, FileUnreadableError):
            if (who := _who(account)) not in unreadable:
                unreadable.append(who)
            continue
        yield account, found


def _who(account: AccountDir) -> str:
    """The email, else the id: an account no provider vouched an address for still acted."""
    return (
        account.workspace.name
        if isinstance(account.user, Torn)
        else (account.user.email or account.user.user_id)
    )


def trail_by_email(projects_root: Path, email: str) -> Trail:
    """The operator-admin read (decisions.md § 0004) of what one PERSON did, across every workspace on the box.

    Everything in a workspace they own, whoever did it, and their own lines in anyone else's: a
    delegate acts in its delegator's workspace. Several ids where one address signed in through more
    than one provider, since the id is per issuer.
    """
    wanted = email.strip().lower()
    accounts = read_accounts(projects_root)
    own = frozenset(
        account.user.user_id
        for account in accounts
        if not isinstance(account.user, Torn)
        and (account.user.email or "").strip().lower() == wanted
    )
    unreadable = [account.workspace.name for account in accounts if isinstance(account.user, Torn)]
    lines: list[TrailLine] = []
    for account, found in _scanned(projects_root, accounts if own else (), unreadable):
        if not isinstance(account.user, Torn) and account.user.user_id in own:
            lines += found
        else:
            lines += [
                line._replace(subject=f"{account.workspace.name}:{line.subject}")
                for line in found
                if line.actor in own
            ]
    return Trail(own, in_time_order(lines), unreadable)


def _dataset_of(store: CampaignStore, campaign_id: str) -> str | None:
    """A manifest that will not read names no dataset; its lines still list."""
    try:
        campaign = store.load_campaign(campaign_id)
    except FileUnreadableError:
        return None
    return None if campaign is None else campaign.dataset_name


class AccountTrail(NamedTuple):
    who: str  # `_who`
    trail: Trail
    # Campaign id -> the dataset it optimizes, for each campaign a line names that still exists.
    datasets: dict[str, str]


def trails_since(projects_root: Path, since: float) -> tuple[list[AccountTrail], list[str]]:
    """The operator-admin read (decisions.md § 0004) of what EVERY account did from ``since`` (epoch) on.

    One trail per workspace that has a line, whoever acted in it, then the workspaces that could
    not be read.
    """
    accounts = read_accounts(projects_root)
    unreadable = [account.workspace.name for account in accounts if isinstance(account.user, Torn)]
    trails: list[AccountTrail] = []
    for account, found in _scanned(projects_root, accounts, unreadable):
        recent = [line for line in found if (epoch_seconds(line.at) or 0.0) >= since]
        if recent and not isinstance(account.user, Torn):
            store = CampaignStore(account.workspace, jobs_dir(projects_root))
            trails.append(
                AccountTrail(
                    _who(account),
                    Trail(frozenset({account.user.user_id}), in_time_order(recent), []),
                    {
                        campaign_id: dataset
                        for campaign_id in {line.campaign_id for line in recent if line.campaign_id}
                        if (dataset := _dataset_of(store, campaign_id)) is not None
                    },
                )
            )
    return trails, unreadable


def host_user_id() -> str | None:
    return accounts.host_user_id(default_projects_root())


def registered_or_default_identity(explicit_tenant: str | None = None) -> IdentityContext:
    if explicit_tenant:
        return default_identity(tenant_id=explicit_tenant)
    uid = host_user_id()
    return default_identity(tenant_id=uid) if uid else default_identity()


def local_stores(*, tenant: str | None) -> Stores:
    return build_stores(
        registered_or_default_identity(tenant), projects_root=default_projects_root()
    )


def require_host(stores: Stores, door: Door) -> None:
    """The host's administration of the box (decisions § 0004): never an inbound route, so the API
    door refuses it; every capability-holder reaches the kind, only the host's terminal or bot acts."""
    if door is Door.API or not stores.identity.host:
        raise PayloadInvalidError(
            "Only whoever runs this box administers it, from its terminal or its bot.",
            code="administered_by_host",
        )


def blocked_emails() -> list[str]:
    return grant_files.list_blocked(default_identity_paths().blocklist)


def block_email(email: str, *, actor: str) -> list[str]:
    paths = default_identity_paths()
    return grant_files.block_email(
        paths.blocklist, email, actor=actor, audit_path=paths.blocklist_audit
    )


def unblock_email(email: str, *, actor: str) -> list[str]:
    paths = default_identity_paths()
    return grant_files.unblock_email(
        paths.blocklist, email, actor=actor, audit_path=paths.blocklist_audit
    )


def grants() -> dict[str, PrincipalGrant]:
    return list_grants(default_identity_paths().grants)


def revoke_grant(sub_user_id: str, *, actor: str) -> bool:
    paths = default_identity_paths()
    return revoke_principal(
        paths.grants, sub_principal_user_id=sub_user_id, actor=actor, audit_path=paths.grants_audit
    )


def grant(sub_user_id: str, *, delegator: str, capabilities: frozenset[str], actor: str) -> None:
    paths = default_identity_paths()
    grant_principal(
        paths.grants,
        sub_principal_user_id=sub_user_id,
        delegated_by_user_id=delegator,
        capabilities=capabilities,
        note=f"via {actor}",
        actor=actor,
        audit_path=paths.grants_audit,
    )


def set_coupon(projects_root: Path, user_id: str, face: Budget, *, afresh: bool) -> CouponGrant:
    """The host-admin coupon (decisions.md § 0004): *face* per unit, drawing from the window the
    account's grant already draws from; *afresh* starts that window now. The bill stays whole:
    nothing it holds moves, and `/auth/coupon` serves what the coupon has left of the new face."""
    workspace = tenant_workspace(projects_root, user_id)
    user = UserStore(workspace).load()
    if user is None:
        raise NotFoundError(f"No account {user_id!r} on this install.")
    return GrantStore(workspace).set(face, afresh=afresh, opened_at=user.created_at)


# An account's own provider keys; who pays a send is `infrastructure/llm/wallet.py::paying_key`'s.
def check_provider(provider: str) -> str:
    if provider not in providers():
        raise PayloadInvalidError(
            f"No provider is named {provider!r}. Known: {', '.join(sorted(providers()))}.",
            code="unknown_provider",
        )
    return provider


class AccountKey(StrictModel):
    provider: str
    last4: str = Field(description="The key's last four characters: all that is ever served back.")
    added_at: str


# Far above any provider's key, far below what a form field should carry.
_KEY_CHARS = 512


def put_account_key(stores: Stores, provider: str, secret: SecretStr) -> AccountKey:
    """Stored as given: the provider is the one that says whether it is good, at the first send."""
    raw = secret.get_secret_value()
    if not raw or raw != raw.strip() or any(c.isspace() for c in raw) or len(raw) > _KEY_CHARS:
        raise PayloadInvalidError(
            f"A {provider} key is one unbroken string of at most {_KEY_CHARS} characters.",
            code="api_key_malformed",
        )
    held = stores.api_keys.put(check_provider(provider), secret)
    return AccountKey(provider=provider, last4=held.last4, added_at=held.added_at)


def remove_account_key(stores: Stores, provider: str) -> bool:
    return stores.api_keys.remove(check_provider(provider))


class TokenEntry(StrictModel):
    """One of the account's API tokens, as every read after its mint serves it: never its text."""

    id: str
    name: str
    scopes: list[str] = Field(description="The `/grant` names of what it may command.")
    created_at: str
    revoked_at: str | None


class AccountTokens(StrictModel):
    tokens: list[TokenEntry] = Field(description="Oldest first, the revoked kept beside the live.")
    grantable: list[str] = Field(description="The scopes a token of this account may carry.")


class MintedToken(StrictModel):
    token: TokenEntry
    secret: str = Field(description="The bearer's text, served by this answer alone: copy it now.")


def _entry(token: tokens.ApiToken) -> TokenEntry:
    return TokenEntry(
        id=token.id,
        name=token.name,
        scopes=capability_names(token.scopes),
        created_at=token.created_at,
        revoked_at=token.revoked_at,
    )


def mint_token(stores: Stores, name: str, scope_names: Iterable[str]) -> MintedToken:
    """Its scopes are what the account may delegate and holds, so a bearer never outranks it; the
    host mints none, as every request would refuse it (`accounts.py::account_identity`)."""
    if stores.identity.host:
        raise BadRequestError(
            "The host acts through its own browser or terminal, never a bearer.",
            code="host_bearer_refused",
            next_acts=(NextAct(field="Cookie", value="the host's signed-in session"),),
        )
    try:
        scopes = capabilities_from_names(scope_names)
    except ValueError as exc:
        raise PayloadInvalidError(
            str(exc),
            code="token_scope_unknown",
            next_acts=(NextAct(field="scopes", value="names from the list it gave"),),
        ) from exc
    if unheld := scopes - stores.identity.capabilities:
        raise PayloadInvalidError(
            f"Your account holds no {', '.join(capability_names(unheld))}, so no token of it may.",
            code="token_scope_unheld",
            next_acts=(NextAct(field="scopes", value="only what your account holds"),),
        )
    secret, token = tokens.mint_token(
        default_identity_paths().tokens,
        # Its first act opens the account, so the bearer names one.
        user_id=account_of(stores).user_id,
        name=name,
        scopes=scopes,
    )
    return MintedToken(token=_entry(token), secret=secret)


def account_tokens(stores: Stores) -> AccountTokens:
    held = tokens.account_tokens(default_identity_paths().tokens, str(stores.identity.user_id))
    return AccountTokens(
        tokens=[_entry(token) for token in held],
        # The host mints none (`mint_token`), so it is offered no scope to mint with.
        grantable=[]
        if stores.identity.host
        else capability_names(stores.identity.capabilities & DELEGABLE_CAPABILITIES),
    )


def revoke_token(stores: Stores, token_id: str) -> None:
    if not tokens.revoke_token(
        default_identity_paths().tokens, user_id=str(stores.identity.user_id), token_id=token_id
    ):
        raise NotFoundError(
            f"No live token {token_id!r} on your account.",
            code="token_not_found",
            next_acts=(NextAct(field="token_id", value="the id of a live token of yours"),),
        )
