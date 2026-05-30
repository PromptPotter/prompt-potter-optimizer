"""``UserStore`` — per-user quota knobs persisted at ``projects/{tenant}/user.json``.

One file per tenant directory (tenant=user for Stage-1 single-tenant-per-user
beta). The ``User`` record carries abuse-limit knobs that the launcher gates
against: per-user daily spend cap, concurrent-cycles ceiling,
campaigns-per-day ceiling. Missing file ⇒ defaults via :meth:`get_or_create`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from promptpotter.infrastructure.store.base import (
    read_json_optional,
    write_json,
)


class User(BaseModel):
    """Per-user quota record. Persisted as ``user.json`` under the tenant root.

    All limits are nullable / overridable per-install via the on-disk file —
    operator hand-edits raise/lower for trusted users without redeploys.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: str
    tenant_id: str
    email: str | None = None
    spend_budget_usd_daily: float | None = Field(
        default=None,
        description="Per-UTC-day cap composed with the per-cycle cap at mint time.",
    )
    max_concurrent_cycles: int = Field(default=2, ge=1)
    max_campaigns_per_day: int = Field(default=10, ge=1)
    demo_mode_enabled: bool = Field(
        default=True,
        description="Surface the built-in try-and-learn demo dataset in the collection. On by default for new users; toggled in Account → Preferences.",
    )
    created_at: str


class UserStore:
    """File-backed user-quota store; one ``user.json`` per tenant directory."""

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
        """Return the persisted ``User`` or mint one with defaults."""
        existing = self.load()
        if existing is not None:
            return existing
        user = User(
            user_id=user_id,
            tenant_id=tenant_id,
            email=email,
            created_at=datetime.now(UTC).isoformat(),
        )
        self.save(user)
        return user


__all__ = ["User", "UserStore"]
