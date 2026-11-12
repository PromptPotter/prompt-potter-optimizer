from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from promptpotter.config.paths import ACCOUNTS_LOCK
from promptpotter.config.settings import LOCK_TIMEOUT, settings
from promptpotter.domain.cycle_paths import WorkspaceDir
from promptpotter.infrastructure.runtime_flags import producer_attached
from promptpotter.infrastructure.store.campaign_store.store import CampaignStore
from promptpotter.infrastructure.store.io import held_lock
from promptpotter.infrastructure.store.user_store import User, UserStore, read_accounts
from promptpotter.shared.identity import TERMINAL_IDENTITY_ID

if TYPE_CHECKING:
    from promptpotter.shared.identity import IdentityContext

logger = logging.getLogger(__name__)


def claim_default(*, projects_root: Path, user_id: str, email: str | None) -> str | None:
    """The host's account takes the terminal's: ONE act under the accounts lock, so no sign-in
    opens either directory meanwhile. ``None`` once claimed; else why it refused, the terminal's
    tree left in place so the host's next sign-in asks again."""
    with held_lock(projects_root / ACCOUNTS_LOCK, wait_s=LOCK_TIMEOUT):
        user_dir = projects_root / user_id
        default_dir = projects_root / TERMINAL_IDENTITY_ID
        if not default_dir.is_dir():
            return None
        if user_dir.exists():
            return f"both {default_dir} and {user_dir} exist: merge or remove one, then sign in"
        terminal = CampaignStore(WorkspaceDir(default_dir))
        if any(
            producer_attached(cycle_dir)
            for campaign_id in terminal.list_campaign_ids()
            for cycle_dir in terminal.campaign_cycle_dirs(campaign_id)
        ):
            return f"a run holds a cycle under {default_dir}: pause it or let it end, then sign in"
        default_dir.rename(user_dir)
        logger.info("Claimed default tenant: %s → %s", default_dir, user_dir)
        users = UserStore(user_dir)
        # The terminal's record becomes the signer's, so the account reads as the declared one.
        if (held := users.load()) is not None:
            users.save(
                held.model_copy(update={"user_id": user_id, "tenant_id": user_id, "email": email})
            )
    return None


def is_declared_host(email: str | None) -> bool:
    """The account signed in with `HOST_ADMIN_EMAIL`, a VERIFIED email (every provider passes only
    one), so whoever holds that inbox. Unset means nobody is: signing up is the grant, so "first one
    in wins" is unsafe."""
    declared = settings.HOST_ADMIN_EMAIL.strip().lower()
    return bool(declared) and (email or "").strip().lower() == declared


def is_host_account(user: User) -> bool:
    """The terminal's own account, or the one `HOST_ADMIN_EMAIL` names."""
    return user.user_id == TERMINAL_IDENTITY_ID or is_declared_host(user.email)


def host_user_id(projects_root: Path) -> str | None:
    """The declared host's account once its sign-in opened one; ``None`` before."""
    return next(
        (
            account.user.user_id
            for account in read_accounts(projects_root)
            if isinstance(account.user, User)
            and account.user.user_id != TERMINAL_IDENTITY_ID
            and is_host_account(account.user)
        ),
        None,
    )


def is_host(identity: IdentityContext) -> bool:
    """The fact its seam stamped (`IdentityContext.host`)."""
    return identity.host
