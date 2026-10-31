from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from promptpotter.domain.cycle_paths import WorkspaceDir
from promptpotter.infrastructure.identity.paths import DefaultClaim, default_identity_paths
from promptpotter.infrastructure.store.campaign_store.store import CampaignStore
from promptpotter.infrastructure.store.io import read_json_optional, write_json
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.identity import TERMINAL_IDENTITY_ID

if TYPE_CHECKING:
    from promptpotter.shared.identity import IdentityContext

logger = logging.getLogger(__name__)


def maybe_claim_default(
    *,
    projects_root: Path,
    user_id: str,
    marker_path: Path,
) -> bool:
    user_dir = projects_root / user_id
    default_dir = projects_root / "default"
    renamed = False

    if not marker_path.is_file():
        if default_dir.is_dir() and not user_dir.exists():
            try:
                default_dir.rename(user_dir)
                renamed = True
                logger.info("Claimed default tenant: %s → %s", default_dir, user_dir)
            except OSError as exc:
                logger.warning("Failed to claim default tenant for %s: %s", user_id, exc)
        claim = DefaultClaim(user_id=user_id, claimed_at=utcnow_iso(), renamed=renamed)
        write_json(marker_path, claim.model_dump())
        if user_dir.is_dir():
            _rebind_campaigns(CampaignStore(WorkspaceDir(user_dir)), user_id)
    return renamed


def registered_user_id(marker_path: Path) -> str | None:
    """Optional, not tolerant: a corrupt marker read as unclaimed would silently demote the host."""
    raw = read_json_optional(marker_path)
    if raw is None:
        return None
    uid = DefaultClaim.model_validate(raw).user_id
    return uid if uid != "default" else None


def _is_host(*, terminal: bool, user_id: str) -> bool:
    """Terminal detection is passed IN: merged, an identity omitting an issuer reads as the host."""
    claimed = registered_user_id(default_identity_paths().default_claim_marker)
    return terminal or (claimed is not None and user_id == claimed)


def is_host(identity: IdentityContext) -> bool:
    return _is_host(terminal=identity.issuer is None, user_id=str(identity.user_id))


def is_host_tenant_dir(user_id: str) -> bool:
    """The DIRECTORY-walk reading: no issuer survives on disk."""
    return _is_host(terminal=user_id == TERMINAL_IDENTITY_ID, user_id=user_id)


def _rebind_campaigns(campaigns: CampaignStore, user_id: str) -> None:
    """Any OTHER owner is left alone: a multi-user install holds campaigns that are someone else's."""
    for campaign_id in campaigns.list_campaign_ids():
        owner = campaigns.campaign_owner(campaign_id)
        if owner is not None and owner.owner_user_id == "default":
            campaigns.update_campaign(campaign_id, owner_user_id=user_id)
            logger.info("Rebound campaign %s to user %s", campaign_id, user_id)
