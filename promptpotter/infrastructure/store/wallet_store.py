"""An account's three wallet files: what the host grants it on the box's keys, the keys it holds,
and what its sends were charged.

None holds a balance: what the coupon has left is a fold of the charges
(`store/account_spend.py::account_bill`), never a counter beside them.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, TypeAdapter

from promptpotter.domain.spend import Budget, KeySource, TokenUsageKind
from promptpotter.domain.strict_model import FrozenModel
from promptpotter.infrastructure.store.io import (
    append_line,
    read_bytes_optional,
    read_json_optional,
    take_lock,
    write_json,
    write_lock_name,
)
from promptpotter.shared.clock import utcnow_iso

_KEYS_LOCK_WAIT_S = 5.0

GRANT_FILE = "grant.json"
API_KEYS_FILE = "api_keys.json"
CHARGES_FILE = "charges.jsonl"


class CouponGrant(FrozenModel):
    """The host's hand-set coupon for one account, replacing the box's default face in both units."""

    face: Budget = Field(
        description="The coupon's face per unit; an axis left unset is unbounded on the box's keys."
    )
    granted_at: str = Field(default_factory=utcnow_iso)


class GrantStore:
    """Absent is the box's default coupon (`settings.HOST_COUPON`); corrupt raises, never reads as it."""

    def __init__(self, account_dir: Path) -> None:
        self.path = account_dir / GRANT_FILE

    def load(self) -> CouponGrant | None:
        raw = read_json_optional(self.path)
        return None if raw is None else CouponGrant.model_validate(raw)

    def save(self, grant: CouponGrant) -> None:
        write_json(self.path, grant.model_dump(mode="json"))


class HeldKey(FrozenModel):
    secret: SecretStr
    added_at: str = Field(default_factory=utcnow_iso)

    @property
    def last4(self) -> str:
        return self.secret.get_secret_value()[-4:]


HELD_KEYS: TypeAdapter[dict[str, HeldKey]] = TypeAdapter(dict[str, HeldKey])


class TenantApiKeyStore:
    """One key per provider; read on every send's resolution, so a removal binds the next send."""

    def __init__(self, account_dir: Path) -> None:
        self.path = account_dir / API_KEYS_FILE

    def load(self) -> dict[str, HeldKey]:
        # Optional, not tolerant: a corrupt file read as "no keys" would bill the host's.
        raw = read_bytes_optional(self.path)
        return {} if raw is None else HELD_KEYS.validate_json(raw)

    def put(self, provider: str, secret: SecretStr) -> HeldKey:
        held = HeldKey(secret=secret)
        self._rewrite(lambda keys: {**keys, provider: held})
        return held

    def remove(self, provider: str) -> bool:
        removed = False

        def _without(keys: dict[str, HeldKey]) -> dict[str, HeldKey]:
            nonlocal removed
            removed = provider in keys
            return {p: k for p, k in keys.items() if p != provider}

        self._rewrite(_without)
        return removed

    def _rewrite(self, change: Callable[[dict[str, HeldKey]], dict[str, HeldKey]]) -> None:
        lock = take_lock(
            self.path.with_name(write_lock_name(self.path.name)), wait_s=_KEYS_LOCK_WAIT_S
        )
        if lock is None:
            raise TimeoutError(f"{self.path} is held by another write")
        try:
            keys = change(self.load())
            write_json(
                self.path,
                {
                    p: {"secret": k.secret.get_secret_value(), "added_at": k.added_at}
                    for p, k in keys.items()
                },
            )
        finally:
            lock.release()


class HeldCharge(FrozenModel):
    """A send's hold, booked as it is placed: unclosed, it is what the send may have cost."""

    record_type: Literal["held"] = "held"
    hold_id: str
    key_source: KeySource
    input_tokens: int
    # ``None`` where nothing capped the reply: such a send holds no tokens.
    output_tokens: int | None
    cost_usd: float | None
    # The ledger the hold landed on, relative to the account: while its producer is attached the
    # run's reservation already holds it.
    ledger: str
    timestamp: str = Field(default_factory=utcnow_iso)


class BilledCharge(FrozenModel):
    """One answered part of a send, booked as it lands; both figures ``None`` is unpriced."""

    record_type: Literal["billed"] = "billed"
    hold_id: str
    key_source: KeySource
    kind: TokenUsageKind
    # ``None`` where neither the send nor its reply named one, as on a backend cell.
    model: str | None
    provider: str | None
    cost_usd: float | None
    rate_priced_usd: float | None
    tokens: int
    timestamp: str = Field(default_factory=utcnow_iso)


ChargeRecord = HeldCharge | BilledCharge


class ChargeBook:
    """Written at the send by `spend_book.py::Admission`, so no delete and no reshape of run data reads a ledger to keep it."""

    def __init__(self, account_dir: Path) -> None:
        self.account_dir = account_dir
        self.path = account_dir / CHARGES_FILE

    def append(self, charge: ChargeRecord) -> None:
        self.account_dir.mkdir(parents=True, exist_ok=True)
        append_line(self.path, charge.model_dump_json().encode("utf-8"))

    def held_on(self, ledger: Path) -> str:
        return Path(os.path.relpath(ledger, self.account_dir)).as_posix()

    def ledger_of(self, hold: HeldCharge) -> Path:
        return self.account_dir / hold.ledger
