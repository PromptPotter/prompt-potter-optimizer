"""The host coupon as an account sees it, and the keys it holds ([decisions.md](../../../docs/decisions.md) § 0003).

Which key a send goes out on is `infrastructure/llm/account_key.py`'s; this serves what that choice
reads and moves the keys it chooses from.
"""

from __future__ import annotations

from pydantic import Field, SecretStr

from promptpotter.domain.spend import LifetimeSpend
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.identity.migration import is_host
from promptpotter.infrastructure.llm.account_key import account_spend, read_coupon
from promptpotter.infrastructure.llm.registry import drop_account_client, providers
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.errors import PayloadInvalidError


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


# Below every provider's key format, so `last4` never serves a key whole; far below what a form
# field should carry above.
_KEY_CHARS = (16, 512)


def put_account_key(stores: Stores, provider: str, secret: SecretStr) -> AccountKey:
    """Stored as given: the provider is the one that says whether it is good, at the first send."""
    raw = secret.get_secret_value()
    shortest, longest = _KEY_CHARS
    if raw != raw.strip() or any(c.isspace() for c in raw) or not shortest <= len(raw) <= longest:
        raise PayloadInvalidError(
            f"A {provider} key is one unbroken string of {shortest} to {longest} characters.",
            code="api_key_malformed",
        )
    held = stores.api_keys.put(check_provider(provider), secret)
    drop_account_client(str(stores.tenant_id), provider)
    return AccountKey(provider=provider, last4=held.last4, added_at=held.added_at)


def remove_account_key(stores: Stores, provider: str) -> bool:
    removed = stores.api_keys.remove(check_provider(provider))
    drop_account_client(str(stores.tenant_id), provider)
    return removed


def account_keys(stores: Stores) -> list[AccountKey]:
    return [
        AccountKey(provider=provider, last4=held.last4, added_at=held.added_at)
        for provider, held in sorted(stores.api_keys.load().items())
    ]
