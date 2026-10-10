from __future__ import annotations

import logging
from json import JSONDecodeError
from pathlib import Path
from typing import TYPE_CHECKING

from promptpotter.infrastructure.identity.paths import default_identity_paths
from promptpotter.infrastructure.store.io import read_json, write_json
from promptpotter.infrastructure.store.layout import CampaignLayout
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.identity import default_identity

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
        write_json(
            marker_path,
            {"user_id": user_id, "claimed_at": utcnow_iso(), "renamed": renamed},
        )
        if user_dir.is_dir():
            _rewrite_campaign_ownership(user_dir / "campaigns", user_id)
    return renamed


def registered_or_default_identity(explicit_tenant: str | None = None) -> IdentityContext:
    if explicit_tenant:
        return default_identity(tenant_id=explicit_tenant)
    uid = registered_user_id(default_identity_paths().default_claim_marker)
    return default_identity(tenant_id=uid, user_id=uid) if uid else default_identity()


def registered_user_id(marker_path: Path) -> str | None:
    if not marker_path.is_file():
        return None
    try:
        uid = read_json(marker_path).get("user_id")
    except (OSError, JSONDecodeError):
        return None
    return uid if isinstance(uid, str) and uid and uid != "default" else None


def _rewrite_campaign_ownership(campaigns_root: Path, user_id: str) -> None:
    """Any OTHER owner is left alone: a multi-user install holds campaigns that are someone else's."""
    if not campaigns_root.is_dir():
        return
    rewritten = 0
    for campaign_dir in campaigns_root.iterdir():
        manifest = CampaignLayout(campaign_dir).manifest
        if not manifest.is_file():
            continue
        try:
            data = read_json(manifest)
        except (OSError, JSONDecodeError) as exc:
            logger.warning("Skipping unreadable campaign.json at %s: %s", manifest, exc)
            continue
        current = data.get("owner_user_id")
        if current == user_id or (current not in (None, "", "default")):
            continue
        data["owner_user_id"] = user_id
        write_json(manifest, data)
        rewritten += 1
    if rewritten:
        logger.info("Rebound %d campaigns to user %s", rewritten, user_id)


__all__ = ["maybe_claim_default", "registered_or_default_identity", "registered_user_id"]
