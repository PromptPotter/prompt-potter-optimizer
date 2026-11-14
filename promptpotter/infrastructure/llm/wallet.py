"""The wallet door: the host coupon and the one payer rule ([decisions.md](../../../docs/decisions.md)
§ 0003), below the connectors so a cell's sends go through it too.

A send on a provider goes out on the account's own key for it, else on the box's, which the host's
coupon meters; a send a library makes on the process environment's key is the box's
(`ENV_KEY_PAYER`). What the coupon has left is DERIVED, from the face (`grant.json`, else the box's
default) less what the account's charges say the box's keys paid; nothing counts it down.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import NamedTuple

from promptpotter.config.settings import settings
from promptpotter.domain.said import Tone
from promptpotter.domain.spend import (
    NOTHING_METERED,
    ZERO_SPEND,
    Budget,
    KeySource,
    PaidSpend,
    Remainder,
    UserSpend,
    bill_is_floor,
    draws_coupon,
    money_text,
    usd_text,
)
from promptpotter.infrastructure.identity.accounts import is_host
from promptpotter.infrastructure.llm.base import ProviderKey
from promptpotter.infrastructure.llm.openai_compat import OpenAICompatibleClient
from promptpotter.infrastructure.llm.registry import get_llm_client, host_key, host_key_attr
from promptpotter.infrastructure.llm.spend_book import bound_account, require_spend_book
from promptpotter.infrastructure.store.account_spend import account_bill
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.infrastructure.store.wallet_store import CouponGrant, HeldKey
from promptpotter.shared.errors import (
    HostAllowanceExhaustedError,
    LaunchRefusalReason,
    NoApiKeyError,
)

# A send on the process env's key (litellm under DSPy, an installed agent): the env holds the box's.
ENV_KEY_PAYER: KeySource = "host"


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
    def left(self) -> Remainder:
        return self.face.less(self.drawn)

    @property
    def exhausted(self) -> bool:
        return self.left.run_out


def read_coupon(stores: Stores, paid: PaidSpend | None = None) -> Coupon:
    return Coupon(
        face=face_of(stores.grant.load(), host=is_host(stores.identity)),
        spent=(account_bill(stores.charges) if paid is None else paid).host,
    )


class ProviderSend(NamedTuple):
    """A send this process makes on *provider*, paid per `paying_key`; ``None`` where no key pays."""

    provider: str
    payer: KeySource | None


class BackendCells(NamedTuple):
    """A backend's cells, paid and priced as its connector declares whatever keys the account holds."""

    backend: str
    payer: KeySource
    priced: bool


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
    return ProviderSend(provider, None if key is None else key.paid_by)


def unpaid(provider: str) -> NoApiKeyError:
    """THE refusal of a send on *provider* that no key pays."""
    return NoApiKeyError(provider=provider, key=host_key_attr(provider))


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


def keyed_client(provider: str) -> OpenAICompatibleClient:
    """The client a send under the bound account goes out on: `paying_key`'s, refused where the
    box's key would pay past what the run's reserve has left, or no key exists."""
    what = f"a send on {provider}"
    stores = bound_account(what)
    key = paying_key(stores.api_keys.load(), provider)
    if key is None:
        raise unpaid(provider)
    if (
        draws_coupon(key.paid_by)
        and (left := require_spend_book(what).root().coupon_left()).run_out
    ):
        raise AccountWallet(read_coupon(stores), left.budget).refused(
            ProviderSend(provider, key.paid_by), what="this account"
        )
    return get_llm_client(provider, key)
