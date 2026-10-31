"""An account's two wallet files: what the host grants it on the box's keys, and the keys it holds.

Neither holds a balance: what the coupon has left is a fold of the account's ledgers
(`application/jobs/coupon.py`), never a counter beside them.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from pydantic import Field, SecretStr

from promptpotter.domain.spend import Budget
from promptpotter.domain.strict_model import FrozenModel
from promptpotter.infrastructure.store.io import read_json_optional, take_lock, write_json
from promptpotter.shared.clock import utcnow_iso

_KEYS_LOCK_WAIT_S = 5.0


class CouponGrant(FrozenModel):
    """The host's hand-set coupon for one account, replacing the box's default face in both units."""

    face: Budget = Field(
        description="The coupon's face per unit; an axis left unset is unbounded on the box's keys."
    )
    granted_at: str = Field(default_factory=utcnow_iso)


class GrantStore:
    """Absent is the box's default coupon (`settings.HOST_COUPON`); corrupt raises, never reads as it."""

    def __init__(self, account_dir: Path) -> None:
        self.path = account_dir / "grant.json"

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


class TenantApiKeyStore:
    """One key per provider; read on every send's resolution, so a removal binds the next send."""

    def __init__(self, account_dir: Path) -> None:
        self.path = account_dir / "api_keys.json"

    def load(self) -> dict[str, HeldKey]:
        # Optional, not tolerant: a corrupt file read as "no keys" would bill the host's.
        raw = read_json_optional(self.path) or {}
        return {provider: HeldKey.model_validate(held) for provider, held in raw.items()}

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
        lock = take_lock(self.path.with_name(self.path.name + ".lock"), wait_s=_KEYS_LOCK_WAIT_S)
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
