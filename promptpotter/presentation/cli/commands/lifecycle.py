"""``archive`` / ``unarchive`` / ``delete`` / ``pause`` / ``rename`` — thin shells over ``CommandDispatcher``,
the sole writer of ``CommandRecord``. ``measurements/`` is never touched, so siblings still cache-hit."""

from __future__ import annotations

import argparse
import logging
import uuid

from promptpotter.config.paths import DEFAULT_PROJECTS_ROOT
from promptpotter.infrastructure.store.session_pointer import read_active_pointer
from promptpotter.infrastructure.store.stores import build_stores
from promptpotter.presentation.api.middleware.command_dispatcher import (
    CommandDispatcher,
    LifecycleKind,
)
from promptpotter.presentation.cli.commands._shared import CommandResult, identity_from_args
from promptpotter.shared.errors import ConflictError, NotFoundError

logger = logging.getLogger("promptpotter.presentation.cli.lifecycle")

__all__ = ["cmd_archive", "cmd_delete", "cmd_pause", "cmd_rename", "cmd_unarchive"]


async def _dispatch(args: argparse.Namespace, kind: LifecycleKind) -> CommandResult | None:
    """Run *kind* through the dispatcher. ``None`` on success; a result when the campaign is absent or not the caller's
    (existence-leak gate: not_found, never 403), or when the target is the active campaign."""
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
    refusal = await _dispatch(args, "archive-campaign")
    if refusal is not None:
        return refusal
    campaign_id: str = args.campaign_id
    logger.info("lifecycle: %s -> archived (flagged in place)", campaign_id)
    return CommandResult(
        data={"campaign_id": campaign_id, "lifecycle_status": "archived"},
        human=f"{campaign_id} -> archived (hidden from the default listing){_reason_suffix(args)}",
    )


async def cmd_unarchive(args: argparse.Namespace) -> CommandResult:
    refusal = await _dispatch(args, "unarchive-campaign")
    if refusal is not None:
        return refusal
    campaign_id: str = args.campaign_id
    logger.info("lifecycle: %s -> active (flag cleared)", campaign_id)
    return CommandResult(
        data={"campaign_id": campaign_id, "lifecycle_status": "active"},
        human=f"{campaign_id} -> active (restored)",
    )


async def cmd_pause(args: argparse.Namespace) -> CommandResult:
    """Ask a running cycle to stop at its next checkpoint. The SAME ``pause-cycle`` command the webapp fires, so the interrupt
    lands on the ledger naming who asked — writing ``.runtime/pause.flag`` by hand leaves no such record."""
    identity = identity_from_args(args)
    store = build_stores(identity, projects_root=DEFAULT_PROJECTS_ROOT)
    campaign_id: str = getattr(args, "campaign", None) or ""
    cycle_id: str = getattr(args, "cycle", None) or ""
    if not (campaign_id and cycle_id):
        _sid, pointer_cid, pointer_cyid = read_active_pointer(store.base_dir)
        campaign_id = campaign_id or pointer_cid
        cycle_id = cycle_id or pointer_cyid
    if not (campaign_id and cycle_id):
        return CommandResult(
            data={"status": "no_target"},
            human="No active cycle to pause — name one with --campaign/--cycle.",
        )

    try:
        await CommandDispatcher(store).dispatch_cycle_command(
            kind="pause-cycle",
            campaign_id=campaign_id,
            cycle_id=cycle_id,
            payload_extras={"reason": getattr(args, "reason", None) or ""},
            idempotency_key=uuid.uuid4().hex,
            expected_version=None,
        )
    except NotFoundError:
        return CommandResult(
            data={"campaign_id": campaign_id, "cycle_id": cycle_id, "status": "not_found"},
            human=f"cycle not found: {campaign_id}/{cycle_id}",
        )
    except ConflictError as exc:
        return CommandResult(
            data={"campaign_id": campaign_id, "cycle_id": cycle_id, "status": "conflict"},
            human=str(exc),
        )
    logger.info("run control: %s/%s -> pause requested", campaign_id, cycle_id)
    # `status`, matching this function's other two exits — NOT `run_phase`. The cycle's
    # phase is still whatever `derive_run_phase` says (it runs until its next checkpoint),
    # and the word this used to serve there, `pausing`, is in no `RunPhase`. Run-state has
    # one server-owned answer and one vocabulary; a fourth entry point minting a seventh
    # word for it is how the CLI and the browser come to describe one cycle differently.
    return CommandResult(
        data={"campaign_id": campaign_id, "cycle_id": cycle_id, "status": "pause_requested"},
        human=(
            f"{campaign_id}/{cycle_id} -> pause requested{_reason_suffix(args)}. "
            "The loop exits at its next checkpoint; `resume` picks it up."
        ),
    )


async def cmd_rename(args: argparse.Namespace) -> CommandResult:
    """Set the campaign's operator name — display only, and the one every surface prefers over the
    dataset name. The campaign id is untouched: it addresses the directory, the measurement cache and
    every bookmark. Identity-neutral, so a rename cannot void a banked origin."""
    campaign_id: str = args.campaign_id
    label: str = str(getattr(args, "label", "") or "").strip()
    dispatcher = CommandDispatcher(
        build_stores(identity_from_args(args), projects_root=DEFAULT_PROJECTS_ROOT)
    )
    try:
        await dispatcher.dispatch_campaign_config(
            kind="set-campaign-label",
            campaign_id=campaign_id,
            payload={"label": label},
            idempotency_key=uuid.uuid4().hex,
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
    logger.info("campaign %s -> label %r", campaign_id, label)
    return CommandResult(
        data={"campaign_id": campaign_id, "label": label},
        human=(
            f"{campaign_id} -> named {label!r}"
            if label
            else f"{campaign_id} -> name cleared (shows its dataset name again)"
        ),
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
