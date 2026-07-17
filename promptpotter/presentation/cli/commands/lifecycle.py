"""``archive`` / ``unarchive`` / ``delete`` — campaign lifecycle verbs.

``archive`` MOVES the campaign tree into the ``archive/`` recycle bin (hidden from
the default sidebar, restorable by ``unarchive``); ``delete`` is destructive — it
removes the tree outright, or with ``--keep-results`` strips to the keepsake tier
(manifest + reports + the shallow langfuse loop trace). The cross-campaign
measurement cache (``measurements/``) is never touched, so siblings still
cache-hit. The active campaign is refused (switch first). Operator-facing shape:
``docs/operations/persistence-and-state.md`` § Beta hosting state.

The three are a thin shell over ``CommandDispatcher.dispatch_lifecycle`` — the
seam ``POST /commands/{kind}`` uses. They used to call ``CampaignStore`` directly,
which made the CLI a second writer of campaign lifecycle state: it re-spelled the
owner gate, and nothing on disk recorded that a campaign had been archived or
destroyed, or by whom. ``CommandDispatcher`` is the sole writer of
``CommandRecord`` (``docs/architecture.md`` §0) — from the terminal as from the web.
"""

from __future__ import annotations

import argparse
import logging
import uuid

from promptpotter.infrastructure.store.layout import DEFAULT_PROJECTS_ROOT
from promptpotter.infrastructure.store.stores import build_stores
from promptpotter.presentation.api.middleware.command_dispatcher.dispatcher import (
    CommandDispatcher,
    LifecycleKind,
)
from promptpotter.presentation.cli.commands._shared import CommandResult, identity_from_args
from promptpotter.shared.errors import ConflictError, NotFoundError

logger = logging.getLogger("promptpotter.presentation.cli.lifecycle")

__all__ = ["cmd_archive", "cmd_delete", "cmd_unarchive"]


async def _dispatch(args: argparse.Namespace, kind: LifecycleKind) -> CommandResult | None:
    """Run *kind* through the dispatcher. ``None`` on success; a ``CommandResult`` when
    the campaign is absent / not the caller's (existence-leak gate: not_found, never
    403) or the target is the active campaign (conflict)."""
    campaign_id: str = args.campaign_id
    dispatcher = CommandDispatcher(
        build_stores(identity_from_args(args), projects_root=DEFAULT_PROJECTS_ROOT)
    )
    try:
        await dispatcher.dispatch_lifecycle(
            kind=kind,
            campaign_id=campaign_id,
            reason=getattr(args, "reason", None) or "",
            idempotency_key=uuid.uuid4().hex,
            keep_results=bool(getattr(args, "keep_results", False)),
        )
    except NotFoundError:
        return CommandResult(
            data={"campaign_id": campaign_id, "status": "not_found"},
            human=f"campaign not found: {campaign_id}",
        )
    except ConflictError as exc:
        return CommandResult(
            data={"campaign_id": campaign_id, "status": "conflict"}, human=str(exc)
        )
    return None


def _reason_suffix(args: argparse.Namespace) -> str:
    reason: str = getattr(args, "reason", None) or ""
    return f" ({reason})" if reason else ""


async def cmd_archive(args: argparse.Namespace) -> CommandResult:
    """Move a campaign into the ``archive/`` recycle bin — hidden from the default sidebar."""
    refusal = await _dispatch(args, "archive-campaign")
    if refusal is not None:
        return refusal
    campaign_id: str = args.campaign_id
    logger.info("lifecycle: %s -> archived (moved to archive/)", campaign_id)
    return CommandResult(
        data={"campaign_id": campaign_id, "lifecycle_status": "archived"},
        human=f"{campaign_id} -> archived (moved to recycle bin){_reason_suffix(args)}",
    )


async def cmd_unarchive(args: argparse.Namespace) -> CommandResult:
    """Restore a campaign from the ``archive/`` recycle bin back to ``active``."""
    refusal = await _dispatch(args, "unarchive-campaign")
    if refusal is not None:
        return refusal
    campaign_id: str = args.campaign_id
    logger.info("lifecycle: %s -> active (restored from archive/)", campaign_id)
    return CommandResult(
        data={"campaign_id": campaign_id, "lifecycle_status": "active"},
        human=f"{campaign_id} -> active (restored)",
    )


async def cmd_delete(args: argparse.Namespace) -> CommandResult:
    """Destructively remove a campaign. ``--keep-results`` spares the keepsake tier."""
    refusal = await _dispatch(args, "delete-campaign")
    if refusal is not None:
        return refusal
    campaign_id: str = args.campaign_id
    keep_results = bool(getattr(args, "keep_results", False))
    mode = "deleted (keepsake kept)" if keep_results else "deleted (removed)"
    logger.info("lifecycle: %s -> %s", campaign_id, mode)
    return CommandResult(
        data={
            "campaign_id": campaign_id,
            "lifecycle_status": "deleted",
            "keep_results": keep_results,
        },
        human=f"{campaign_id} -> {mode}{_reason_suffix(args)}",
    )
