from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from promptpotter.config.paths import ACCOUNTS_LOCK
from promptpotter.config.settings import LOCK_TIMEOUT
from promptpotter.domain.cycle_paths import WorkspaceDir
from promptpotter.infrastructure.identity.paths import DefaultClaim, default_identity_paths
from promptpotter.infrastructure.runtime_flags import producer_attached
from promptpotter.infrastructure.store.campaign_store.store import CampaignStore
from promptpotter.infrastructure.store.io import held_lock, write_json
from promptpotter.infrastructure.store.read_model import read_file
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.errors import FileKind
from promptpotter.shared.identity import TERMINAL_IDENTITY_ID

if TYPE_CHECKING:
    from promptpotter.shared.identity import IdentityContext

logger = logging.getLogger(__name__)


def claim_default(*, projects_root: Path, user_id: str, marker_path: Path) -> str | None:
    """The host's account takes the terminal's: ONE act under the accounts lock, so no sign-in
    opens either directory meanwhile. ``None`` once claimed; else why it refused, the marker
    unwritten so the host's next sign-in asks again."""
    with held_lock(projects_root / ACCOUNTS_LOCK, wait_s=LOCK_TIMEOUT):
        if marker_path.is_file():
            return None
        user_dir = projects_root / user_id
        default_dir = projects_root / TERMINAL_IDENTITY_ID
        if default_dir.is_dir():
            if user_dir.exists():
                return f"both {default_dir} and {user_dir} exist: merge or remove one, then sign in"
            terminal = CampaignStore(WorkspaceDir(default_dir))
            if any(
                producer_attached(cycle_dir)
                for campaign_id in terminal.list_campaign_ids()
                for cycle_dir in terminal.campaign_cycle_dirs(campaign_id)
            ):
                return (
                    f"a run holds a cycle under {default_dir}: pause it or let it end, then sign in"
                )
            default_dir.rename(user_dir)
            logger.info("Claimed default tenant: %s → %s", default_dir, user_dir)
        write_json(marker_path, DefaultClaim(user_id=user_id, claimed_at=utcnow_iso()).model_dump())
    return None


def registered_user_id(marker_path: Path) -> str | None:
    """Optional, not tolerant: a corrupt marker read as unclaimed would silently demote the host."""
    claim = read_file(marker_path, DefaultClaim.model_validate_json, FileKind.STORED_CONFIG)
    if claim is None:
        return None
    return claim.user_id if claim.user_id != "default" else None


def is_host(identity: IdentityContext) -> bool:
    """The fact its seam stamped (`IdentityContext.host`)."""
    return identity.host


def is_host_tenant_dir(user_id: str) -> bool:
    """Whether *user_id*'s own account is the host's: the terminal's, or the one that claimed it.
    A signed-in principal's seam stamps it; a directory walk reads it."""
    claimed = registered_user_id(default_identity_paths().default_claim_marker)
    return user_id == TERMINAL_IDENTITY_ID or (claimed is not None and user_id == claimed)
