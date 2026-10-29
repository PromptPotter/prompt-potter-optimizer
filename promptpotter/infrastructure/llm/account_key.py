"""The one key choice ([decisions.md](../../../docs/decisions.md) § 0003): whose key a send goes out on.

The host runs an account on the box's keys up to its coupon; past it the account sends on a key of
its own. What the coupon has left is DERIVED, from the face (`grant.json`, else the box's default)
less what the account's ledgers say the box's keys paid; nothing counts it down. Here, below the
connectors, so a key a backend's cell is handed is chosen where a node's is.
"""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
from typing import Any, NamedTuple

from promptpotter.config.settings import settings
from promptpotter.domain.pipeline_overlay import node_config_items
from promptpotter.domain.spend import Budget, KeySource, PaidSpend, UserSpend
from promptpotter.infrastructure.identity.migration import is_host
from promptpotter.infrastructure.llm.base import LLMClientBase, ProviderKey
from promptpotter.infrastructure.llm.pricing import inline_route
from promptpotter.infrastructure.llm.registry import (
    get_llm_client,
    host_key,
    host_key_attr,
    providers,
)
from promptpotter.infrastructure.llm.send_pacing import set_rate_tenant
from promptpotter.infrastructure.store.account_spend import account_ledgers, sum_user_spend
from promptpotter.infrastructure.store.campaign_store.store import CampaignStore
from promptpotter.infrastructure.store.layout import tenant_workspace
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.infrastructure.store.wallet_store import CouponGrant
from promptpotter.shared.errors import HostAllowanceExhaustedError, NoApiKeyError
from promptpotter.shared.identity import IdentityContext


def face_of(grant: CouponGrant | None, *, host: bool) -> Budget:
    """Per unit, what the box's keys may pay for an account over its life; unset is unbounded."""
    if host:
        return Budget()
    if grant is not None:
        return grant.face
    return Budget(usd=settings.HOST_COUPON.usd, tokens=settings.HOST_COUPON.tokens)


def coupon_face(stores: Stores) -> Budget:
    return face_of(stores.grant.load(), host=is_host(stores.identity))


def account_spend(stores: Stores) -> PaidSpend:
    """The ACCOUNT's ledgers, never a sandbox's: an L4 inner run's bills are carried onto its root."""
    account = CampaignStore(tenant_workspace(stores.shared_root, stores.tenant_id))
    return sum_user_spend(ledgers=account_ledgers(account))


def named_providers(pipeline_params: dict[str, Any] | None) -> frozenset[str | None]:
    """The provider each node of *pipeline_params* sends on; ``None`` for a model routed to no
    provider an account may hold a key for, which its library sends on the box's keys."""
    named: set[str | None] = set()
    for _, cfg in node_config_items(pipeline_params):
        provider, model = cfg.get("provider"), cfg.get("model")
        if not isinstance(provider, str) and isinstance(model, str):
            provider = inline_route(model)[1]
        if isinstance(provider, str) or isinstance(model, str):
            named.add(provider if provider in providers() else None)
    return frozenset(named)


def payers_of(named: frozenset[str | None], stores: Stores) -> frozenset[KeySource]:
    """Whose keys the providers *named* send on: the account's where it holds one, else the box's,
    which is whose a run naming none sends on too."""
    held = stores.api_keys.load()
    return frozenset("user" if p in held else "host" for p in named) or frozenset({"host"})


class Coupon(NamedTuple):
    face: Budget
    # What the box's keys paid for this account; its own keys' bills never draw the coupon.
    spent: UserSpend
    # Retired by `HOST_COUPON.void_on_byo` once the account holds a key of its own.
    void: bool

    @property
    def left(self) -> Budget:
        """Unreported sends counted: what they may have cost is not the coupon's to give again."""
        if self.void:
            return Budget.amount(0.0, 0)
        return Budget(
            usd=None if self.face.usd is None else max(0.0, self.face.usd - self.spent.at_most_usd),
            tokens=None
            if self.face.tokens is None
            else max(0, self.face.tokens - self.spent.at_most_tokens),
        )

    @property
    def exhausted(self) -> bool:
        left = self.left
        return (left.usd is not None and left.usd <= 0.0) or (
            left.tokens is not None and left.tokens <= 0
        )

    def refusal(self, provider: str | None) -> str:
        on = f" on {provider}" if provider else ""
        if self.void:
            return (
                "This account holds a key of its own, so the host's allowance is retired. Add "
                f"your own key{on} under Account → API keys to send on it."
            )
        return (
            f"The host's allowance for this account is used up ({_amounts(self.spent)} of "
            f"{_face(self.face)} on the box's keys), so nothing further is sent{on} on them. Add "
            "your own key under Account → API keys to continue on your own money."
        )


def _amounts(spent: UserSpend) -> str:
    unreported = f", up to ${spent.unreported_usd:.2f} unreported" if spent.sends_unreported else ""
    return f"${spent.sent_usd:.2f} and {spent.used_tokens:,} tokens{unreported}"


def _face(face: Budget) -> str:
    usd = "no USD bound" if face.usd is None else f"${face.usd:.2f}"
    tokens = "no token bound" if face.tokens is None else f"{face.tokens:,} tokens"
    return f"{usd} / {tokens}"


def read_coupon(stores: Stores, spent: PaidSpend | None = None) -> Coupon:
    paid = account_spend(stores) if spent is None else spent
    return Coupon(
        face=coupon_face(stores),
        spent=paid.host,
        void=settings.HOST_COUPON.void_on_byo and bool(stores.api_keys.load()),
    )


def resolve_api_key(identity: IdentityContext, provider: str, stores: Stores) -> ProviderKey:
    """THE choke point: the account's own key for *provider* wins with no coupon asked; else a
    live coupon sends on the box's key; else 422 ``host_allowance_exhausted``."""
    own = stores.api_keys.load().get(provider)
    if own is not None:
        return ProviderKey(own.secret, str(stores.tenant_id))
    key = host_key(provider)
    if key is None:
        raise NoApiKeyError(provider=provider, key=host_key_attr(provider))
    if not is_host(identity):
        coupon = read_coupon(stores)
        if coupon.exhausted:
            raise HostAllowanceExhaustedError(coupon.refusal(provider), provider=provider)
    return key


_ACCOUNT: ContextVar[Stores] = ContextVar("account")


def bind_account(stores: Stores) -> Callable[[], None]:
    """THE binder of whose wallet a task's sends draw: its keys, its coupon and its rate share.

    Answers its unbinding, which an entry leaves uncalled: the task that binds owns its context,
    and an inner cell rebinds the same account."""
    account = _ACCOUNT.set(stores)
    tenant = set_rate_tenant(str(stores.tenant_id))

    def unbind() -> None:
        tenant.var.reset(tenant)
        _ACCOUNT.reset(account)

    return unbind


def _bound() -> Stores:
    try:
        return _ACCOUNT.get()
    except LookupError:
        raise RuntimeError(
            "no account is bound, so no key can be chosen for this send. Every entry that sends "
            "calls `bind_account` where it binds its spend book."
        ) from None


def account_key(provider: str) -> ProviderKey:
    """The key a send on *provider* under the bound account goes out on: a node's client's, or the
    one a connector hands its backend's cell."""
    stores = _bound()
    return resolve_api_key(stores.identity, provider, stores)


def cell_key(provider: str | None) -> ProviderKey | None:
    """The key a connector hands its cell's sends on *provider*; ``None`` where its library sends
    on keys of its own, which are the box's."""
    return account_key(provider) if provider in providers() else None


def payer(key: ProviderKey | None) -> KeySource:
    return "host" if key is None else key.paid_by


def cell_payer(pipeline_params: dict[str, Any] | None) -> KeySource:
    """Whose wallet an in-process cell's reservation holds, under the bound account: one send on
    the box's keys makes the whole of it the coupon's."""
    paying = payers_of(named_providers(pipeline_params), _bound())
    return "host" if "host" in paying else "user"


def keyed_client(provider: str) -> LLMClientBase:
    """The client a send under the bound account goes out on."""
    return get_llm_client(provider, account_key(provider))
