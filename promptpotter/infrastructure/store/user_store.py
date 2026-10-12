from __future__ import annotations

from pathlib import Path

from pydantic import ConfigDict, Field

from promptpotter.domain.cycle_paths import WorkspaceDir
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.store.io import (
    read_json_optional,
    write_json,
)
from promptpotter.shared.clock import utcnow_iso


class ConsentRecord(StrictModel):
    """``accepted_at`` is SERVER-stamped, never client-supplied: its legal weight rests on that clock."""

    model_config = ConfigDict(frozen=True)

    version: str
    accepted_at: str


class User(StrictModel):
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
    token_budget_total: int | None = Field(
        default=None,
        description="This account's LIFETIME token ceiling, the model-portable twin of `spend_budget_usd_total` and the one that survives an unpriced model. Same shape: None means the install-wide free-tier ceiling applies, a number is this account's own. Whichever ceiling the account reaches first binds.",
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


def account_dirs(projects_root: Path) -> list[WorkspaceDir]:
    """A CROSS-tenant read, so it takes the projects root: a tenant-scoped `Stores` cannot name another tenant's dir."""
    if not projects_root.is_dir():
        return []
    return sorted(
        WorkspaceDir(child) for child in projects_root.iterdir() if (child / "user.json").is_file()
    )


def count_accounts(projects_root: Path) -> int:
    return len(account_dirs(projects_root))


def accounts_by_email(
    projects_root: Path, email: str
) -> tuple[list[tuple[WorkspaceDir, User]], list[str]]:
    """Several where one address signed in through more than one provider: the id is per issuer.

    The second list names the accounts whose ``user.json`` could not be read, by their dir name.
    """
    wanted = email.strip().lower()
    found: list[tuple[WorkspaceDir, User]] = []
    unreadable: list[str] = []
    for tenant_dir in account_dirs(projects_root):
        try:
            user = UserStore(tenant_dir).load()
        except Exception:
            # One torn file must not hide every OTHER account; it is named, since it may be the one asked for.
            unreadable.append(tenant_dir.name)
            continue
        if user is not None and user.email is not None and user.email.strip().lower() == wanted:
            found.append((tenant_dir, user))
    return found, unreadable


__all__ = [
    "ConsentRecord",
    "User",
    "UserStore",
    "account_dirs",
    "accounts_by_email",
    "count_accounts",
]
