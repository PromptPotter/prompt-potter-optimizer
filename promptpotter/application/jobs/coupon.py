"""The host coupon and the one payer rule ([decisions.md](../../../docs/decisions.md) § 0003).

A send on a provider goes out on the account's own key for it, else on the box's, which the host's
coupon meters; a backend's cells go out on whatever its connector declares (`Connector.cell_payer`).
What the coupon has left is DERIVED, from the face (`grant.json`, else the box's default) less what
the account's charges say the box's keys paid; nothing counts it down.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextvars import ContextVar
from enum import StrEnum
from typing import NamedTuple

from pydantic import Field, SecretStr

from promptpotter.config.settings import settings
from promptpotter.domain.said import Tone
from promptpotter.domain.spend import (
    NOTHING_METERED,
    ZERO_SPEND,
    Budget,
    KeySource,
    PaidSpend,
    UserSpend,
    bill_is_floor,
    draws_coupon,
    money_text,
    usd_text,
)
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
from promptpotter.infrastructure.store.wallet_store import CouponGrant, HeldKey
from promptpotter.shared.errors import (
    HostAllowanceExhaustedError,
    LaunchRefusalReason,
    NoApiKeyError,
    PayloadInvalidError,
)


def face_of(grant: CouponGrant | None, *, host: bool) -> Budget:
    """Per unit, what the box's keys may pay for an account over its life; unset is unbounded."""
    if host:
        return Budget()
    if grant is not None:
        return grant.face
    return Budget(usd=settings.HOST_COUPON.usd, tokens=settings.HOST_COUPON.tokens)


class Coupon(NamedTuple):
    face: Budget
    # What the box's keys paid for this account; its own keys' bills never draw the coupon.
    spent: UserSpend

    @property
    def unmetered(self) -> bool:
        return self.face.usd is None and self.face.tokens is None

    @property
    def drawn(self) -> Budget:
        """What the face is compared against: unreported sends counted, since what they may have
        cost is not the coupon's to give again."""
        return Budget.amount(self.spent.at_most_usd, self.spent.at_most_tokens)

    @property
    def left(self) -> Budget:
        drawn = self.drawn
        return Budget(
            usd=None if self.face.usd is None else max(0.0, self.face.usd - drawn.usd_amount),
            tokens=None
            if self.face.tokens is None
            else max(0, self.face.tokens - drawn.token_amount),
        )

    @property
    def exhausted(self) -> bool:
        left = self.left
        return (left.usd is not None and left.usd <= 0.0) or (
            left.tokens is not None and left.tokens <= 0
        )


def read_coupon(stores: Stores, paid: PaidSpend | None = None) -> Coupon:
    return Coupon(
        face=face_of(stores.grant.load(), host=is_host(stores.identity)),
        spent=(account_bill(stores.charges) if paid is None else paid).host,
    )


class ProviderSend(NamedTuple):
    """A send this process makes on *provider*, paid per `paying_key`."""

    provider: str
    payer: KeySource


class BackendCells(NamedTuple):
    """A backend's cells, paid by what its connector declares whatever keys the account holds."""

    backend: str
    payer: KeySource


type Send = ProviderSend | BackendCells


def paying_key(keys: Mapping[str, HeldKey], provider: str) -> ProviderKey | None:
    """THE payer rule: the account's own key for *provider*, else the box's; ``None`` where
    neither holds one. Every send, admission, the free tier and `/auth/coupon` ask it."""
    own = keys.get(provider)
    if own is not None:
        return ProviderKey(own.secret, "user")
    return host_key(provider)


def provider_send(keys: Mapping[str, HeldKey], provider: str) -> ProviderSend:
    key = paying_key(keys, provider)
    if key is None:
        raise NoApiKeyError(provider=provider, key=host_key_attr(provider))
    return ProviderSend(provider, key.paid_by)


class AllowanceState(StrEnum):
    """What the host's allowance does for this account's next launch: one closed set, each surface
    reading its words off `ALLOWANCE_STATE_INFO`."""

    # The box's operator, or a face bounding no unit: nothing meters the box's keys for it.
    UNMETERED = "unmetered"
    # What the box's keys drew reached the face on some unit.
    USED_UP = "used_up"
    # Drawn is under the face, but the account's running campaigns hold the rest.
    HELD = "held"
    OPEN = "open"


class AllowanceStateInfo(NamedTuple):
    label: str
    # Stands where the amount drawn would, where no unit is bounded.
    left_word: str
    lede: str
    # `warn` where nothing is left of it for a new launch.
    tone: Tone


ALLOWANCE_STATE_INFO: dict[AllowanceState, AllowanceStateInfo] = {
    AllowanceState.UNMETERED: AllowanceStateInfo(
        "Spend",
        "unmetered",
        "Nothing meters this account's sends on the host's keys: it runs the box, or its "
        "allowance bounds no unit.",
        tone="quiet",
    ),
    AllowanceState.USED_UP: AllowanceStateInfo(
        "Allowance used up",
        "used up",
        "Used up: the host's keys send nothing more for this account. A provider your own key "
        "pays still sends, and every result stays readable.",
        tone="warn",
    ),
    AllowanceState.HELD: AllowanceStateInfo(
        "Allowance held",
        "held by your running campaigns",
        "Held: your running campaigns hold what is left, so a new one drawing it starts once one "
        "ends.",
        tone="warn",
    ),
    AllowanceState.OPEN: AllowanceStateInfo(
        "Allowance",
        "left",
        "What the host's provider keys may pay for this account over its life. A key of your own "
        "pays every send on its provider instead, billed to you, and never draws it.",
        tone="quiet",
    ),
}
assert ALLOWANCE_STATE_INFO.keys() == set(AllowanceState)


class AccountWallet(NamedTuple):
    """One read of an account's coupon, and what a launch may still hold of it once in-flight
    reservations are held back."""

    coupon: Coupon
    headroom: Budget
    # Headroom is quoted at zero, not twice: a contended account has money and needs a retry.
    contended: bool = False
    # The excluded campaign's own spend, which `headroom` makes room for: its ceiling counts it.
    history: UserSpend = ZERO_SPEND
    # What the account's other live runs reserve of the coupon: `headroom` is net of it.
    held: Budget = NOTHING_METERED

    @property
    def launch_room_gone(self) -> bool:
        """A contended wallet quotes zero and has room: a launch still being admitted holds it."""
        return not self.contended and (
            (self.headroom.usd is not None and self.headroom.usd <= self.history.at_most_usd)
            or (
                self.headroom.tokens is not None
                and self.headroom.tokens <= self.history.at_most_tokens
            )
        )

    @property
    def state(self) -> AllowanceState:
        if self.coupon.unmetered:
            return AllowanceState.UNMETERED
        if self.coupon.exhausted:
            return AllowanceState.USED_UP
        if self.contended or self.launch_room_gone:
            return AllowanceState.HELD
        return AllowanceState.OPEN

    def refused(
        self, drawing: Send, *, what: str, declares: Budget | None = None
    ) -> HostAllowanceExhaustedError:
        """THE allowance's refusal, in the terms ``headroom`` is computed from, so the sentence
        cannot disagree with the arithmetic: what *what* declares against the face, what is drawn
        and held, the unpriced grace, what is left, and the send that draws it."""
        face, spent = self.coupon.face, self.coupon.spent
        terms = [f"{money_text(self.coupon.drawn, on=face)} drawn of {money_text(face)}"]
        if self.held != NOTHING_METERED:
            terms.append(f"{money_text(self.held, on=face)} held by your running campaigns")
        if bill_is_floor(spent.unpriced_tokens) and face.usd is not None:
            terms.append(
                f"{spent.unpriced_tokens:,} tokens no rate prices, so at most "
                f"{usd_text(settings.UNPRICED_GRACE_USD)} more is lent"
            )
        named: str | None = None
        match drawing:
            case ProviderSend(provider=provider):
                sends, named = f"{what}'s sends on {provider}", provider
            case BackendCells(backend=backend):
                sends = (
                    f"{what}'s {backend} cells, which go out on the box's keys whatever keys "
                    "this account holds"
                )
        asks = (
            f"nothing is left for {sends}"
            if declares is None
            else f"{what} declares {money_text(declares, on=face)}, past the "
            f"{money_text(self.headroom, on=face)} left for {sends}"
        )
        return HostAllowanceExhaustedError(
            f"The host's allowance: {'; '.join(terms)}, so {asks}.",
            reason=LaunchRefusalReason.OVER_DECLARED
            if declares is not None
            else LaunchRefusalReason.USED_UP
            if self.coupon.exhausted
            else LaunchRefusalReason.HELD,
            provider=named,
        )


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
    """The client a send under the bound account goes out on: `paying_key`'s, refused where the
    box's key would pay past a used-up coupon, or no key exists."""
    try:
        stores = _ACCOUNT.get()
    except LookupError:
        raise RuntimeError(
            "no account is bound, so no key can be chosen for this send. Every entry that sends "
            "calls `bind_account` where it binds its spend book."
        ) from None
    key = paying_key(stores.api_keys.load(), provider)
    if key is None:
        raise NoApiKeyError(provider=provider, key=host_key_attr(provider))
    if draws_coupon(key.paid_by) and (coupon := read_coupon(stores)).exhausted:
        raise AccountWallet(coupon, coupon.left).refused(
            ProviderSend(provider, key.paid_by), what="this account"
        )
    return get_llm_client(provider, key)


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
