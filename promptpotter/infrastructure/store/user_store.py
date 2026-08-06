"""Per-user quota knobs at ``projects/{tenant}/user.json`` — the abuse-limit ceilings the launcher gates against. A
missing file yields defaults via :meth:`get_or_create`."""

from __future__ import annotations

from pathlib import Path

from pydantic import ConfigDict, Field

from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.store.io import (
    read_json_optional,
    write_json,
)
from promptpotter.shared.clock import utcnow_iso


class ConsentRecord(StrictModel):
    """Provable record that a user accepted a specific Terms version. ``accepted_at`` is SERVER-stamped, never client-supplied —
    the record's legal weight depends on a trustworthy clock."""

    model_config = ConfigDict(frozen=True)

    version: str
    accepted_at: str


class User(StrictModel):
    """Per-user quota record at ``user.json`` under the tenant root. Every limit is nullable and overridable on disk, so an
    operator can raise one for a trusted user without a redeploy."""

    model_config = ConfigDict(frozen=True)

    user_id: str
    tenant_id: str
    email: str | None = None
    terms_accepted: ConsentRecord | None = Field(
        default=None,
        description="Provable Terms-acceptance record (version + server-stamped timestamp). None until the consent gate is cleared.",
    )
    spend_budget_usd_daily: float | None = Field(
        default=None,
        description="Per-UTC-day cap composed with the per-cycle cap at mint time.",
    )
    max_concurrent_cycles: int = Field(default=2, ge=1)
    max_campaigns_per_day: int = Field(default=1000, ge=1)
    demo_mode_enabled: bool = Field(
        default=True,
        description="Surface the built-in try-and-learn demo dataset in the collection. On by default for new users; toggled in Account → Preferences.",
    )
    created_at: str


class UserStore:
    def __init__(self, base_dir: Path) -> None:
        self._base_dir = base_dir

    def _path(self) -> Path:
        return self._base_dir / "user.json"

    def load(self) -> User | None:
        raw = read_json_optional(self._path())
        if raw is None:
            return None
        return User.model_validate(raw)

    def save(self, user: User) -> None:
        write_json(self._path(), user.model_dump())

    def get_or_create(self, *, user_id: str, tenant_id: str, email: str | None = None) -> User:
        existing = self.load()
        if existing is not None:
            return existing
        user = User(
            user_id=user_id,
            tenant_id=tenant_id,
            email=email,
            created_at=utcnow_iso(),
        )
        self.save(user)
        return user


__all__ = ["ConsentRecord", "User", "UserStore"]
