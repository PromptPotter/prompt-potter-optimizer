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
    spend_budget_usd_total: float | None = Field(
        default=None,
        description="This account's LIFETIME USD ceiling, summed over its whole ledger and composed with the per-cycle cap at mint time. None means no personal override, so the install-wide free-tier ceiling applies; a number is this account's own ceiling. Not a per-day allowance — spending it is spending it.",
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


def count_accounts(projects_root: Path) -> int:
    """How many accounts exist on this install. A CROSS-tenant read, so it takes the projects root rather
    than a ``Stores`` — a tenant-scoped store cannot name another tenant's directory, which is the isolation
    working, not a gap to route around. ``user.json`` is what makes a tenant dir an account; a directory
    without one is a workspace nobody has signed into."""
    if not projects_root.is_dir():
        return 0
    return sum(1 for child in projects_root.iterdir() if (child / "user.json").is_file())


__all__ = ["ConsentRecord", "User", "UserStore", "count_accounts"]
