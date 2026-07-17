"""``UserStore`` — per-user quota knobs persisted at ``projects/{tenant}/user.json``.

One file per tenant directory (tenant=user for Stage-1 single-tenant-per-user
beta). The ``User`` record carries abuse-limit knobs that the launcher gates
against: per-user daily spend cap, concurrent-cycles ceiling,
campaigns-per-day ceiling. Missing file ⇒ defaults via :meth:`get_or_create`.
"""

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
    """Provable record that a user accepted a specific Terms version.

    ``version`` is the accepted ``settings.TERMS_VERSION``; ``accepted_at`` is
    server-stamped (never client-supplied — the record's legal weight depends on
    a trustworthy clock). The consent gate re-prompts when ``version`` no longer
    matches the live ``TERMS_VERSION``. This is the artifact the Terms'
    indemnity / prohibition / security clauses lean on: which user accepted which
    version, when.
    """

    model_config = ConfigDict(frozen=True)

    version: str
    accepted_at: str


class User(StrictModel):
    """Per-user quota record. Persisted as ``user.json`` under the tenant root.

    All limits are nullable / overridable per-install via the on-disk file —
    operator hand-edits raise/lower for trusted users without redeploys.
    """

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
            created_at=utcnow_iso(),
        )
        self.save(user)
        return user


__all__ = ["ConsentRecord", "User", "UserStore"]
