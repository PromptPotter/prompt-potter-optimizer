"""``archive`` / ``delete`` / ``unarchive`` — soft-mark a campaign's lifecycle.

``archived`` hides from the default sidebar; ``deleted`` is soft — data
stays on disk so measurements still cache-hit for siblings. Nothing is
physically removed; ``try_delete_stub_cycle`` stays the only physical-delete
site. Operator-facing shape: ``docs/operations/persistence-and-state.md``
§ Beta hosting state.
"""

from __future__ import annotations

import argparse
import logging
from datetime import UTC, datetime

from promptpotter.infrastructure.store import build_stores
from promptpotter.infrastructure.store.paths import DEFAULT_PROJECTS_ROOT
from promptpotter.presentation.cli.commands._shared import CommandResult, identity_from_args

logger = logging.getLogger("promptpotter.presentation.cli.lifecycle")

__all__ = ["cmd_archive", "cmd_delete", "cmd_unarchive"]


def _mark(args: argparse.Namespace, *, status: str) -> CommandResult:
    identity = identity_from_args(args)
    store = build_stores(identity, projects_root=DEFAULT_PROJECTS_ROOT)
    campaign_id: str = args.campaign_id
    campaign = store.campaigns.load_campaign(campaign_id)
    if campaign is None or campaign.owner_user_id != str(identity.user_id):
        return CommandResult(
            data={"campaign_id": campaign_id, "status": "not_found"},
            human=f"campaign not found: {campaign_id}",
        )
    if campaign.lifecycle_status == status:
        return CommandResult(
            data={"campaign_id": campaign_id, "lifecycle_status": status, "noop": True},
            human=f"{campaign_id} is already {status}.",
        )
    changed_at = datetime.now(UTC).isoformat()
    reason: str = getattr(args, "reason", None) or ""
    store.campaigns.mark_campaign_lifecycle(
        campaign_id,
        lifecycle_status=status,
        lifecycle_changed_at=changed_at,
        lifecycle_reason=reason,
    )
    logger.info("lifecycle: %s -> %s", campaign_id, status)
    return CommandResult(
        data={
            "campaign_id": campaign_id,
            "lifecycle_status": status,
            "lifecycle_changed_at": changed_at,
            "lifecycle_reason": reason,
        },
        human=f"{campaign_id} -> {status}" + (f" ({reason})" if reason else ""),
    )


async def cmd_archive(args: argparse.Namespace) -> CommandResult:
    """Mark a campaign as ``archived`` — hides from the default sidebar."""
    return _mark(args, status="archived")


async def cmd_delete(args: argparse.Namespace) -> CommandResult:
    """Soft-mark a campaign as ``deleted`` — data stays on disk."""
    return _mark(args, status="deleted")


async def cmd_unarchive(args: argparse.Namespace) -> CommandResult:
    """Restore a campaign to ``active`` — reverses ``archive`` or ``delete``."""
    return _mark(args, status="active")
