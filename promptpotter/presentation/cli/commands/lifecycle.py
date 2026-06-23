"""``archive`` / ``unarchive`` / ``delete`` — campaign lifecycle verbs.

``archive`` MOVES the campaign tree into the ``archive/`` recycle bin (hidden from
the default sidebar, restorable by ``unarchive``); ``delete`` is destructive — it
removes the tree outright, or with ``--keep-results`` strips to the keepsake tier
(manifest + reports + the shallow langfuse loop trace). The cross-campaign
measurement cache (``measurements/``) is never touched, so siblings still
cache-hit. The active campaign is refused (switch first). Operator-facing shape:
``docs/operations/persistence-and-state.md`` § Beta hosting state.
"""

from __future__ import annotations

import argparse
import logging

from promptpotter.infrastructure.store import Stores, build_stores
from promptpotter.infrastructure.store.paths import DEFAULT_PROJECTS_ROOT
from promptpotter.presentation.cli.commands._shared import CommandResult, identity_from_args
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.errors import ConflictError

logger = logging.getLogger("promptpotter.presentation.cli.lifecycle")

__all__ = ["cmd_archive", "cmd_delete", "cmd_unarchive"]


def _owned_or_missing(args: argparse.Namespace) -> tuple[Stores | None, str, str]:
    """``(store, campaign_id, reason)`` — or ``(None, …)`` when the campaign is absent
    / not owned by the caller (existence-leak gate: not_found, not 403)."""
    identity = identity_from_args(args)
    store = build_stores(identity, projects_root=DEFAULT_PROJECTS_ROOT)
    campaign_id: str = args.campaign_id
    reason: str = getattr(args, "reason", None) or ""
    campaign = store.campaigns.load_campaign(campaign_id)
    if campaign is None or campaign.owner_user_id != str(identity.user_id):
        return None, campaign_id, reason
    return store, campaign_id, reason


def _not_found(campaign_id: str) -> CommandResult:
    return CommandResult(
        data={"campaign_id": campaign_id, "status": "not_found"},
        human=f"campaign not found: {campaign_id}",
    )


async def cmd_archive(args: argparse.Namespace) -> CommandResult:
    """Move a campaign into the ``archive/`` recycle bin — hidden from the default sidebar."""
    store, campaign_id, reason = _owned_or_missing(args)
    if store is None:
        return _not_found(campaign_id)
    try:
        store.campaigns.archive_campaign(campaign_id, changed_at=utcnow_iso(), reason=reason)
    except ConflictError as exc:
        return CommandResult(
            data={"campaign_id": campaign_id, "status": "conflict"}, human=str(exc)
        )
    logger.info("lifecycle: %s -> archived (moved to archive/)", campaign_id)
    return CommandResult(
        data={"campaign_id": campaign_id, "lifecycle_status": "archived"},
        human=f"{campaign_id} -> archived (moved to recycle bin)"
        + (f" ({reason})" if reason else ""),
    )


async def cmd_unarchive(args: argparse.Namespace) -> CommandResult:
    """Restore a campaign from the ``archive/`` recycle bin back to ``active``."""
    store, campaign_id, reason = _owned_or_missing(args)
    if store is None:
        return _not_found(campaign_id)
    store.campaigns.unarchive_campaign(campaign_id, changed_at=utcnow_iso(), reason=reason)
    logger.info("lifecycle: %s -> active (restored from archive/)", campaign_id)
    return CommandResult(
        data={"campaign_id": campaign_id, "lifecycle_status": "active"},
        human=f"{campaign_id} -> active (restored)",
    )


async def cmd_delete(args: argparse.Namespace) -> CommandResult:
    """Destructively remove a campaign. ``--keep-results`` spares the keepsake tier."""
    store, campaign_id, reason = _owned_or_missing(args)
    if store is None:
        return _not_found(campaign_id)
    keep_results: bool = bool(getattr(args, "keep_results", False))
    try:
        store.campaigns.delete_campaign(
            campaign_id, keep_results=keep_results, changed_at=utcnow_iso(), reason=reason
        )
    except ConflictError as exc:
        return CommandResult(
            data={"campaign_id": campaign_id, "status": "conflict"}, human=str(exc)
        )
    mode = "deleted (keepsake kept)" if keep_results else "deleted (removed)"
    logger.info("lifecycle: %s -> %s", campaign_id, mode)
    return CommandResult(
        data={
            "campaign_id": campaign_id,
            "lifecycle_status": "deleted",
            "keep_results": keep_results,
        },
        human=f"{campaign_id} -> {mode}" + (f" ({reason})" if reason else ""),
    )
