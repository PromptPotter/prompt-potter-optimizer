"""The host coupon and the one key choke point ([decisions.md](../../../docs/decisions.md) § 0003).

The host runs an account on the box's keys up to its coupon; past it the account sends on a key of
its own. What the coupon has left is DERIVED, from the face (`grant.json`, else the box's default)
less what the account's charges say the box's keys paid; nothing counts it down.
"""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
from typing import NamedTuple

from pydantic import Field, SecretStr

from promptpotter.config.settings import settings
from promptpotter.domain.spend import Budget, LifetimeSpend, PaidSpend, UserSpend
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.identity.default_claim import is_host
from promptpotter.infrastructure.llm.base import LLMClientBase, ProviderKey
from promptpotter.infrastructure.llm.registry import (
    get_llm_client,
    host_key,
    host_key_attr,
    providers,
)
from promptpotter.infrastructure.llm.send_pacing import set_rate_tenant
from promptpotter.infrastructure.llm.spend_book import bind_charges, reset_charges
from promptpotter.infrastructure.store.account_spend import account_bill
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.infrastructure.store.wallet_store import CouponGrant
from promptpotter.shared.errors import (
    HostAllowanceExhaustedError,
    NoApiKeyError,
    PayloadInvalidError,
)
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
    return account_bill(stores.charges)


def holds_own_key(stores: Stores) -> bool:
    return bool(stores.api_keys.load())


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
        void=settings.HOST_COUPON.void_on_byo and holds_own_key(stores),
    )


def resolve_api_key(identity: IdentityContext, provider: str, stores: Stores) -> ProviderKey:
    """THE choke point: the account's own key for *provider* wins with no coupon asked; else a
    live coupon sends on the box's key; else 422 ``host_allowance_exhausted``."""
    own = stores.api_keys.load().get(provider)
    if own is not None:
        return ProviderKey(own.secret, "user")
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
    charges = bind_charges(stores.charges)

    def unbind() -> None:
        reset_charges(charges)
        tenant.var.reset(tenant)
        _ACCOUNT.reset(account)

    return unbind


def keyed_client(provider: str) -> LLMClientBase:
    """The client a send under the bound account goes out on."""
    try:
        stores = _ACCOUNT.get()
    except LookupError:
        raise RuntimeError(
            "no account is bound, so no key can be chosen for this send. Every entry that sends "
            "calls `bind_account` where it binds its spend book."
        ) from None
    return get_llm_client(provider, resolve_api_key(stores.identity, provider, stores))


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


class CouponStatus(StrictModel):
    """The host's allowance for this account, and the keys it sends on past it."""

    host: bool = Field(
        description="This account is the box's operator: its sends on the box's keys are unmetered."
    )
    face_usd: float | None = Field(
        description="What the box's keys may pay for this account over its life; null is unbounded."
    )
    face_tokens: int | None
    left_usd: float | None = Field(
        description="The face less what the box's keys paid, unreported sends counted; null where "
        "the face is unbounded."
    )
    left_tokens: int | None
    used_share: float | None = Field(
        description="How full the allowance draws in the unit nearest its face, at most 1; null "
        "where no unit is bounded."
    )
    exhausted: bool = Field(
        description="Nothing more is sent on the box's keys; a provider this account holds a key "
        "for still sends on it."
    )
    void: bool = Field(
        description="Retired because this account holds a key of its own "
        "(`HOST_COUPON.void_on_byo`)."
    )
    host_key_spend: LifetimeSpend = Field(description="What the box's keys paid for this account.")
    own_key_spend: LifetimeSpend = Field(description="What this account's own keys paid.")
    keys: list[AccountKey]
    providers: list[str] = Field(description="Every provider a key may be held for.")


def _share(spent: float, face: float | None) -> float | None:
    if face is None:
        return None
    return min(1.0, spent / face) if face > 0 else 1.0


def coupon_status(stores: Stores) -> CouponStatus:
    paid = account_spend(stores)
    coupon = read_coupon(stores, paid)
    left = coupon.left
    shares = [
        s
        for s in (
            _share(coupon.spent.at_most_usd, coupon.face.usd),
            _share(float(coupon.spent.at_most_tokens), _float(coupon.face.tokens)),
        )
        if s is not None
    ]
    return CouponStatus(
        host=is_host(stores.identity),
        face_usd=coupon.face.usd,
        face_tokens=coupon.face.tokens,
        left_usd=left.usd,
        left_tokens=left.tokens,
        used_share=max(shares) if shares else None,
        exhausted=coupon.exhausted,
        void=coupon.void,
        host_key_spend=LifetimeSpend.of(paid.host),
        own_key_spend=LifetimeSpend.of(paid.user),
        keys=account_keys(stores),
        providers=sorted(providers()),
    )


def _float(value: int | None) -> float | None:
    return None if value is None else float(value)


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


def account_keys(stores: Stores) -> list[AccountKey]:
    return [
        AccountKey(provider=provider, last4=held.last4, added_at=held.added_at)
        for provider, held in sorted(stores.api_keys.load().items())
    ]
