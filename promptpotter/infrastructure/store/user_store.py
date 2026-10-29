from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

from pydantic import Field

from promptpotter.domain.cycle_paths import WorkspaceDir
from promptpotter.domain.strict_model import FrozenModel
from promptpotter.infrastructure.store.io import (
    read_json_optional,
    write_json,
)


class ConsentRecord(FrozenModel):
    """``accepted_at`` is SERVER-stamped, never client-supplied: its legal weight rests on that clock."""

    version: str
    accepted_at: str


class User(FrozenModel):
    user_id: str
    tenant_id: str
    email: str | None = None
    terms_accepted: ConsentRecord | None = Field(
        default=None,
        description="Provable Terms-acceptance record (version + server-stamped timestamp). None until the consent gate is cleared.",
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


def account_dirs(projects_root: Path) -> list[WorkspaceDir]:
    """A CROSS-tenant read, so it takes the projects root: a tenant-scoped `Stores` cannot name another tenant's dir."""
    if not projects_root.is_dir():
        return []
    return sorted(
        WorkspaceDir(child) for child in projects_root.iterdir() if (child / "user.json").is_file()
    )


def count_accounts(projects_root: Path) -> int:
    return len(account_dirs(projects_root))


class Torn(NamedTuple):
    """A ``user.json`` that would not load."""

    why: str


class AccountDir(NamedTuple):
    workspace: WorkspaceDir
    user: User | Torn


def read_accounts(projects_root: Path) -> list[AccountDir]:
    """Every cross-tenant read of WHO starts here, so one file that will not load costs its own row and no other."""
    accounts: list[AccountDir] = []
    for tenant_dir in account_dirs(projects_root):
        try:
            user = UserStore(tenant_dir).load()
        except (OSError, ValueError) as exc:
            accounts.append(AccountDir(tenant_dir, Torn(f"{type(exc).__name__}: {exc}")))
            continue
        # Gone between the listing and the read: an account that is being deleted is not one.
        if user is not None:
            accounts.append(AccountDir(tenant_dir, user))
    return accounts
