"""The campaign/cycle control verbs — thin shells over ``CommandDispatcher``, the sole writer of
``CommandRecord``. ``measurements/`` is never touched, so siblings still cache-hit.

Every verb here is the SAME command the browser posts, by the same kind string: the two surfaces
share the server's vocabulary and nothing else, which is why a CLI verb can land without waiting on
any UI arrangement."""

from __future__ import annotations

import argparse
import logging
import uuid
from collections.abc import Awaitable, Callable

from promptpotter.application.jobs.registry import JobRegistry, default_jobs_dir
from promptpotter.config.paths import DEFAULT_PROJECTS_ROOT
from promptpotter.domain.command_kinds import CycleScopedKind, LifecycleKind
from promptpotter.infrastructure.store.session_pointer import read_active_pointer
from promptpotter.infrastructure.store.stores import Stores, build_stores
from promptpotter.presentation.api.middleware.command_dispatcher import (
    ChangeSpendBudgetPayload,
    CleanupEmptyCyclesPayload,
    CommandDispatcher,
    CyclePayload,
    DeleteCyclePayload,
    LifecyclePayload,
    PauseCyclePayload,
    ReplaceDatasetPayload,
    SetAllowedModelsPayload,
    SetCampaignLabelPayload,
    SkipSearchpointPayload,
    StepCyclePayload,
)
from promptpotter.presentation.cli.commands._shared import (
    CommandResult,
    identity_from_args,
    resolve_campaign_hint,
)
from promptpotter.shared.errors import ConflictError, NotFoundError

logger = logging.getLogger("promptpotter.presentation.cli.lifecycle")

__all__ = [
    "cmd_archive",
    "cmd_cleanup_empty_cycles",
    "cmd_delete",
    "cmd_delete_cycle",
    "cmd_pause",
    "cmd_rename",
    "cmd_replace_dataset",
    "cmd_set_allowed_models",
    "cmd_set_budget",
    "cmd_skip_searchpoint",
    "cmd_step_cycle",
    "cmd_unarchive",
]


def _resolve_target(args: argparse.Namespace, store: Stores) -> tuple[str, str]:
    """The ``--campaign``/``--cycle`` pair, falling back to the active pointer. Shared by every
    cycle-scoped verb here so they cannot resolve "which cycle" two different ways."""
    campaign_id: str = getattr(args, "campaign", None) or ""
    cycle_id: str = getattr(args, "cycle", None) or ""
    if not (campaign_id and cycle_id):
        _sid, pointer_cid, pointer_cyid = read_active_pointer(store.base_dir)
        campaign_id = campaign_id or pointer_cid
        cycle_id = cycle_id or pointer_cyid
    # The pointer already holds a full id; a hand-typed `--campaign` gets the same reach here as
    # it does for `verify`, through the one matcher rather than a second rule.
    return (resolve_campaign_hint(store, campaign_id) if campaign_id else ""), cycle_id


async def _refused(awaitable: Awaitable[object], ids: dict[str, str]) -> CommandResult | None:
    """``None`` when the dispatcher accepted; the operator's line when it refused.

    ONE mapping for every verb here, so the same two refusals cannot grow a wording per dispatch
    family. ``not_found`` covers "absent" and "not yours" alike: the existence-leak gate answers
    404 rather than 403, and the terminal must not widen that.
    """
    target = "/".join(ids.values())
    noun = "cycle" if "cycle_id" in ids else "campaign"
    try:
        await awaitable
    except NotFoundError:
        return CommandResult(
            data={**ids, "status": "not_found"}, human=f"{noun} not found: {target}"
        )
    except ConflictError as exc:
        return CommandResult(data={**ids, "status": "conflict"}, human=str(exc))
    return None


async def _dispatch(args: argparse.Namespace, kind: LifecycleKind) -> CommandResult | None:
    """Run *kind* through the dispatcher. ``None`` on success; a result when the campaign is absent or not the caller's
    (existence-leak gate: not_found, never 403), or when the target is the active campaign."""
    stores = build_stores(identity_from_args(args), projects_root=DEFAULT_PROJECTS_ROOT)
    campaign_id = resolve_campaign_hint(stores, args.campaign_id)
    dispatcher = CommandDispatcher(stores)
    return await _refused(
        dispatcher.dispatch_lifecycle(
            kind=kind,
            payload=LifecyclePayload(
                campaign_id=campaign_id,
                reason=getattr(args, "reason", None) or "",
                keep_results=bool(getattr(args, "keep_results", False)),
            ),
            idempotency_key=uuid.uuid4().hex,
        ),
        {"campaign_id": campaign_id},
    )


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


async def _cycle_scoped(
    args: argparse.Namespace,
    kind: CycleScopedKind,
    payload_for: Callable[[str, str], CyclePayload],
    noun: str,
) -> tuple[CommandResult | None, str, str]:
    """Resolve the target, dispatch, map a refusal — the shape EVERY cycle-scoped verb here shares.

    Returns ``(refusal_or_None, campaign_id, cycle_id)``; the caller owns only its success line.
    One helper rather than a copy per verb, so "which cycle" and "what a refusal reads like"
    cannot come to differ between two verbs that answer to the same dispatcher.
    """
    store = build_stores(identity_from_args(args), projects_root=DEFAULT_PROJECTS_ROOT)
    campaign_id, cycle_id = _resolve_target(args, store)
    if not (campaign_id and cycle_id):
        return (
            CommandResult(
                data={"status": "no_target"},
                human=f"No active cycle to {noun} — name one with --campaign/--cycle.",
            ),
            campaign_id,
            cycle_id,
        )
    refused = await _refused(
        CommandDispatcher(store).dispatch_cycle_command(
            kind=kind,
            payload=payload_for(campaign_id, cycle_id),
            idempotency_key=uuid.uuid4().hex,
            expected_version=None,
        ),
        {"campaign_id": campaign_id, "cycle_id": cycle_id},
    )
    return refused, campaign_id, cycle_id


async def cmd_pause(args: argparse.Namespace) -> CommandResult:
    """Ask a running cycle to stop at its next checkpoint. The SAME ``pause-cycle`` command the webapp fires, so the interrupt
    lands on the ledger naming who asked — writing ``.runtime/pause.flag`` by hand leaves no such record."""
    refused, campaign_id, cycle_id = await _cycle_scoped(
        args,
        "pause-cycle",
        lambda c, cy: PauseCyclePayload(
            campaign_id=c, cycle_id=cy, reason=getattr(args, "reason", None) or ""
        ),
        "pause",
    )
    if refused is not None:
        return refused
    logger.info("run control: %s/%s -> pause requested", campaign_id, cycle_id)
    # `status`, matching this function's other two exits — NOT `run_phase`. The cycle's
    # phase is still whatever `derive_run_phase` says (it runs until its next checkpoint),
    # and `pausing` — the obvious word for it — is in no `RunPhase`. Run-state has
    # one server-owned answer and one vocabulary; a fourth entry point minting a seventh
    # word for it is how the CLI and the browser come to describe one cycle differently.
    return CommandResult(
        data={"campaign_id": campaign_id, "cycle_id": cycle_id, "status": "pause_requested"},
        human=(
            f"{campaign_id}/{cycle_id} -> pause requested{_reason_suffix(args)}. "
            "The loop exits at its next checkpoint; `resume` picks it up."
        ),
    )


async def cmd_set_budget(args: argparse.Namespace) -> CommandResult:
    """Raise or lower a cycle's spend / token ceiling — the SAME ``change-spend-budget`` command the
    browser fires, so the terminal can clear a budget wall too.

    This is the verb that continues a budget-halted cycle: raise the ceiling, then ``resume``. The
    launch flags (``--spend-budget`` / ``--token-budget``) cannot do it, because they only shape a
    launch — this writes the operator ceiling the next launch composes on top of its config.
    """
    identity = identity_from_args(args)
    store = build_stores(identity, projects_root=DEFAULT_PROJECTS_ROOT)
    campaign_id, cycle_id = _resolve_target(args, store)
    if not (campaign_id and cycle_id):
        return CommandResult(
            data={"status": "no_target"},
            human="No active cycle — name one with --campaign/--cycle.",
        )
    # Both absent is a no-op the dispatcher already rejects; sending them through keeps ONE
    # validation of "at least one ceiling", on the command highway rather than per entry point.
    payload = ChangeSpendBudgetPayload(
        campaign_id=campaign_id,
        cycle_id=cycle_id,
        max_usd=getattr(args, "max_usd", None),
        max_tokens=getattr(args, "max_tokens", None),
    )
    # The ONE verb here that needs the registry: the clamp counts in-flight commitments against
    # the account, and `hold_ceiling` asks whether a live job carries the ceiling too. It is
    # disk-backed over `default_jobs_dir()`, so this reads the server's jobs rather than an empty
    # set — the dispatcher refuses outright without one, which is what left this verb unrunnable.
    # No `on_reap`: this process exits in a second and must never reap the server's live cycle.
    refused = await _refused(
        CommandDispatcher(store, JobRegistry(default_jobs_dir())).dispatch_cycle_command(
            kind="change-spend-budget",
            payload=payload,
            idempotency_key=uuid.uuid4().hex,
            expected_version=None,
        ),
        {"campaign_id": campaign_id, "cycle_id": cycle_id},
    )
    if refused is not None:
        return refused
    logger.info("budget: %s/%s -> %s", campaign_id, cycle_id, payload)
    # The REQUESTED figures are deliberately absent from the human line: the account clamp can
    # write less than was asked (`quota.py::clamp_budget_change`), so quoting the request here
    # would have the terminal report a ceiling that was never armed.
    return CommandResult(
        data={
            "campaign_id": campaign_id,
            "cycle_id": cycle_id,
            "status": "budget_set",
            **payload.model_dump(mode="json", include={"max_usd", "max_tokens"}),
        },
        human=(
            f"{campaign_id}/{cycle_id} -> ceiling written. It is clamped against your account "
            "allowance, so read the armed value back from the dashboard; `resume` picks it up."
        ),
    )


async def cmd_rename(args: argparse.Namespace) -> CommandResult:
    """Set the campaign's operator name — display only, and the one every surface prefers over the
    dataset name. The campaign id is untouched: it addresses the directory, the measurement cache and
    every bookmark. Identity-neutral, so a rename cannot void a banked origin."""
    label: str = str(getattr(args, "label", "") or "").strip()
    stores = build_stores(identity_from_args(args), projects_root=DEFAULT_PROJECTS_ROOT)
    campaign_id = resolve_campaign_hint(stores, args.campaign_id)
    dispatcher = CommandDispatcher(stores)
    refused = await _refused(
        dispatcher.dispatch_campaign_config(
            kind="set-campaign-label",
            payload=SetCampaignLabelPayload(campaign_id=campaign_id, label=label),
            idempotency_key=uuid.uuid4().hex,
        ),
        {"campaign_id": campaign_id},
    )
    if refused is not None:
        return refused
    logger.info("campaign %s -> label %r", campaign_id, label)
    return CommandResult(
        data={"campaign_id": campaign_id, "label": label},
        human=(
            f"{campaign_id} -> named {label!r}"
            if label
            else f"{campaign_id} -> name cleared (shows its dataset name again)"
        ),
    )


async def cmd_skip_searchpoint(args: argparse.Namespace) -> CommandResult:
    """Cut the candidate currently being scored, at the next sample boundary.

    The sharpest of the terminal's missing controls: an operator watching a candidate burn samples
    had no sanctioned way to stop it — the in-run stdin reader (``runner/origin_gate.py``) answers
    the origin gate only. Like ``pause``, it lands on the ledger naming who asked.
    """
    refused, campaign_id, cycle_id = await _cycle_scoped(
        args,
        "skip-searchpoint",
        lambda c, cy: SkipSearchpointPayload(campaign_id=c, cycle_id=cy),
        "skip a searchpoint in",
    )
    if refused is not None:
        return refused
    logger.info("run control: %s/%s -> searchpoint skip requested", campaign_id, cycle_id)
    return CommandResult(
        data={"campaign_id": campaign_id, "cycle_id": cycle_id, "status": "skip_requested"},
        human=(
            f"{campaign_id}/{cycle_id} -> skip requested. The scorer drops the current "
            "candidate at its next sample boundary; the round continues with the rest."
        ),
    )


async def cmd_step_cycle(args: argparse.Namespace) -> CommandResult:
    """Let a paused cycle run a bounded number of rounds, then stop again."""
    rounds = max(1, int(getattr(args, "rounds", 1) or 1))
    refused, campaign_id, cycle_id = await _cycle_scoped(
        args,
        "step-cycle",
        lambda c, cy: StepCyclePayload(campaign_id=c, cycle_id=cy, rounds=rounds),
        "step",
    )
    if refused is not None:
        return refused
    logger.info("run control: %s/%s -> step %d round(s)", campaign_id, cycle_id, rounds)
    return CommandResult(
        data={"campaign_id": campaign_id, "cycle_id": cycle_id, "rounds": rounds},
        human=f"{campaign_id}/{cycle_id} -> stepping {rounds} round(s), then stopping again.",
    )


async def cmd_delete_cycle(args: argparse.Namespace) -> CommandResult:
    """Remove ONE named stub cycle. The singular of ``cleanup-empty-cycles``, which reaps every
    empty sibling under a campaign — so this is the verb for a stub you can name and the other for
    a mess you cannot. Both refuse a cycle that holds rounds, and both refuse a live producer."""
    refused, campaign_id, cycle_id = await _cycle_scoped(
        args,
        "delete-cycle",
        lambda c, cy: DeleteCyclePayload(campaign_id=c, cycle_id=cy),
        "delete",
    )
    if refused is not None:
        return refused
    logger.info("lifecycle: %s/%s -> cycle deleted", campaign_id, cycle_id)
    return CommandResult(
        data={"campaign_id": campaign_id, "cycle_id": cycle_id, "status": "deleted"},
        human=f"{campaign_id}/{cycle_id} -> removed.",
    )


async def cmd_cleanup_empty_cycles(args: argparse.Namespace) -> CommandResult:
    """Reap the stub cycles a mint left behind when it never reached round 0."""
    refused, campaign_id, cycle_id = await _cycle_scoped(
        args,
        "cleanup-empty-cycles",
        lambda c, cy: CleanupEmptyCyclesPayload(campaign_id=c, cycle_id=cy),
        "clean up under",
    )
    if refused is not None:
        return refused
    logger.info("lifecycle: %s/%s -> empty cycles cleaned", campaign_id, cycle_id)
    return CommandResult(
        data={"campaign_id": campaign_id, "cycle_id": cycle_id, "status": "cleaned"},
        human=f"{campaign_id}/{cycle_id} -> empty sibling cycles removed.",
    )


async def cmd_set_allowed_models(args: argparse.Namespace) -> CommandResult:
    """Set the models a steered fork may pick from.

    Its absence had teeth: ``resume --steer-model`` refuses against exactly this list
    (``fork_siblings.py::steer_is_babysit``), so a terminal-only operator could hit the refusal
    with no terminal way to widen it. Empty clears the list.
    """
    raw: str = str(getattr(args, "models", "") or "")
    models = [m.strip() for m in raw.split(",") if m.strip()]
    stores = build_stores(identity_from_args(args), projects_root=DEFAULT_PROJECTS_ROOT)
    campaign_id = resolve_campaign_hint(stores, args.campaign_id)
    refused = await _refused(
        CommandDispatcher(stores).dispatch_campaign_config(
            kind="set-allowed-models",
            payload=SetAllowedModelsPayload(campaign_id=campaign_id, allowed_models=models),
            idempotency_key=uuid.uuid4().hex,
        ),
        {"campaign_id": campaign_id},
    )
    if refused is not None:
        return refused
    logger.info("campaign %s -> allowed models %s", campaign_id, models)
    return CommandResult(
        data={"campaign_id": campaign_id, "allowed_models": models},
        human=(
            f"{campaign_id} -> steerable to {', '.join(models)}"
            if models
            else f"{campaign_id} -> allowed-model list cleared"
        ),
    )


async def cmd_replace_dataset(args: argparse.Namespace) -> CommandResult:
    """Version a dataset slug and repoint what referenced it.

    The one verb here the browser answers differently: on a slug collision the CLI's ingest path
    tells the operator to pick another name (``new.py::SlugTakenError``), where the browser offers
    version-and-repoint. This is that offer, in the terminal.
    """
    slug: str = str(getattr(args, "slug", "") or "").strip()
    stores = build_stores(identity_from_args(args), projects_root=DEFAULT_PROJECTS_ROOT)
    refused = await _refused(
        CommandDispatcher(stores).dispatch_workspace_command(
            kind="replace-dataset",
            payload=ReplaceDatasetPayload(slug=slug),
            idempotency_key=uuid.uuid4().hex,
        ),
        {"slug": slug},
    )
    if refused is not None:
        return refused
    logger.info("dataset %s -> replaced (versioned + repointed)", slug)
    return CommandResult(
        data={"slug": slug, "status": "replaced"},
        human=f"{slug} -> replaced; the prior cut is versioned and references repointed.",
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
