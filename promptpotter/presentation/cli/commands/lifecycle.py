from __future__ import annotations

import argparse
import logging
import uuid
from dataclasses import dataclass

from promptpotter.application.commands.dispatcher import CommandCall, CommandDispatcher
from promptpotter.application.commands.payloads import (
    ArchiveCampaignPayload,
    CancelQueuedRunPayload,
    ChangeRunLimitsPayload,
    CleanupEmptyCyclesPayload,
    CyclePayload,
    DeleteCampaignPayload,
    DeleteCyclePayload,
    LifecyclePayload,
    OriginGateDecisionPayload,
    PauseCyclePayload,
    ReplaceDatasetPayload,
    SetCampaignLabelPayload,
    SetConcurrentCyclesPayload,
    SkipSearchpointPayload,
    StepCyclePayload,
    UnarchiveCampaignPayload,
)
from promptpotter.domain.launch_limits import RoundsCap
from promptpotter.domain.phases import GateDecision
from promptpotter.presentation.cli.commands.result import CommandResult
from promptpotter.presentation.cli.commands.workspace import (
    cycle_scoped,
    open_stores,
    refused_by,
    resolve_campaign_hint,
)

logger = logging.getLogger("promptpotter.presentation.cli.lifecycle")

__all__ = [
    "cmd_archive",
    "cmd_cancel_queued",
    "cmd_cycle_verb",
    "cmd_delete",
    "cmd_origin_gate",
    "cmd_pause",
    "cmd_rename",
    "cmd_replace_dataset",
    "cmd_set_concurrent_cycles",
    "cmd_set_limits",
    "cmd_step_cycle",
    "cmd_unarchive",
]


async def _dispatch(
    args: argparse.Namespace,
    payload_type: type[LifecyclePayload | SetCampaignLabelPayload],
    **fields: object,
) -> tuple[CommandResult | None, str]:
    stores = open_stores(args)
    campaign_id = resolve_campaign_hint(stores, args.campaign_id)
    refusal = await refused_by(
        CommandDispatcher(stores).dispatch_campaign_command(
            CommandCall(
                payload_type.model_validate({"campaign_id": campaign_id, **fields}),
                uuid.uuid4().hex,
            )
        ),
        {"campaign_id": campaign_id},
    )
    return refusal, campaign_id


def _reason_suffix(args: argparse.Namespace) -> str:
    return f" ({args.reason})" if args.reason else ""


async def cmd_archive(args: argparse.Namespace) -> CommandResult:
    refusal, campaign_id = await _dispatch(args, ArchiveCampaignPayload, reason=args.reason)
    if refusal is not None:
        return refusal
    logger.info("lifecycle: %s -> archived (flagged in place)", campaign_id)
    return CommandResult(
        data={"campaign_id": campaign_id, "lifecycle_status": "archived"},
        human=f"{campaign_id} -> archived (hidden from the default listing){_reason_suffix(args)}",
    )


async def cmd_unarchive(args: argparse.Namespace) -> CommandResult:
    refusal, campaign_id = await _dispatch(args, UnarchiveCampaignPayload)
    if refusal is not None:
        return refusal
    logger.info("lifecycle: %s -> active (flag cleared)", campaign_id)
    return CommandResult(
        data={"campaign_id": campaign_id, "lifecycle_status": "active"},
        human=f"{campaign_id} -> active (restored)",
    )


async def cmd_pause(args: argparse.Namespace) -> CommandResult:
    sent = await cycle_scoped(
        open_stores(args), args, PauseCyclePayload, "pause", reason=args.reason
    )
    if isinstance(sent, CommandResult):
        return sent
    campaign_id, cycle_id = sent.campaign_id, sent.cycle_id
    logger.info("run control: %s/%s -> pause requested", campaign_id, cycle_id)
    # `status`, not `run_phase`: `pausing` is in no `RunPhase`, and the cycle runs to its checkpoint.
    return CommandResult(
        data={"campaign_id": campaign_id, "cycle_id": cycle_id, "status": "pause_requested"},
        human=(
            f"{campaign_id}/{cycle_id} -> pause requested{_reason_suffix(args)}. "
            "The loop exits at its next checkpoint; `resume` picks it up."
        ),
    )


async def cmd_set_limits(args: argparse.Namespace) -> CommandResult:
    # All-absent goes through too: "at least one ceiling" is validated once, by the dispatcher.
    rounds_cap: RoundsCap | None = args.rounds_cap
    payload = await cycle_scoped(
        open_stores(args),
        args,
        ChangeRunLimitsPayload,
        "set limits on",
        ceiling={"usd": args.max_usd, "tokens": args.max_tokens},
        # Passed only when given: an explicit `None` here is the LIFT, not "untouched".
        **({} if rounds_cap is None else {"max_rounds": rounds_cap.max_rounds}),
    )
    if isinstance(payload, CommandResult):
        return payload
    campaign_id, cycle_id = payload.campaign_id, payload.cycle_id
    logger.info("budget: %s/%s -> %s", campaign_id, cycle_id, payload)
    # No requested figure in the human line: the account clamp can write less than was asked.
    return CommandResult(
        data={
            "campaign_id": campaign_id,
            "cycle_id": cycle_id,
            "status": "budget_set",
            **payload.model_dump(mode="json", include={"ceiling", "max_rounds"}),
        },
        human=(
            f"{campaign_id}/{cycle_id} -> ceiling written. It is clamped against your account "
            "allowance, so read the armed value back from the dashboard; `resume` picks it up."
        ),
    )


async def cmd_rename(args: argparse.Namespace) -> CommandResult:
    label: str = args.label.strip()
    refused, campaign_id = await _dispatch(args, SetCampaignLabelPayload, label=label)
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


@dataclass(frozen=True)
class _CycleVerb:
    payload: type[CyclePayload]
    noun: str
    status: str
    outcome: str


_CYCLE_VERBS: dict[str, _CycleVerb] = {
    "skip-searchpoint": _CycleVerb(
        SkipSearchpointPayload,
        "skip a searchpoint in",
        "skip_requested",
        "skip requested. The scorer drops the current candidate at its next sample boundary; "
        "the round continues with the rest.",
    ),
    "delete-cycle": _CycleVerb(DeleteCyclePayload, "delete", "deleted", "removed."),
    "cleanup-empty-cycles": _CycleVerb(
        CleanupEmptyCyclesPayload, "clean up under", "cleaned", "empty sibling cycles removed."
    ),
}


async def cmd_cycle_verb(args: argparse.Namespace) -> CommandResult:
    verb = _CYCLE_VERBS[args.command]
    sent = await cycle_scoped(open_stores(args), args, verb.payload, verb.noun)
    if isinstance(sent, CommandResult):
        return sent
    campaign_id, cycle_id = sent.campaign_id, sent.cycle_id
    logger.info("%s: %s/%s -> %s", args.command, campaign_id, cycle_id, verb.status)
    return CommandResult(
        data={"campaign_id": campaign_id, "cycle_id": cycle_id, "status": verb.status},
        human=f"{campaign_id}/{cycle_id} -> {verb.outcome}",
    )


async def cmd_origin_gate(args: argparse.Namespace) -> CommandResult:
    decision: GateDecision = args.decision
    sent = await cycle_scoped(
        open_stores(args),
        args,
        OriginGateDecisionPayload,
        "answer the origin gate of",
        decision=decision,
    )
    if isinstance(sent, CommandResult):
        return sent
    campaign_id, cycle_id = sent.campaign_id, sent.cycle_id
    logger.info("run control: %s/%s -> origin gate %s", campaign_id, cycle_id, decision)
    return CommandResult(
        data={"campaign_id": campaign_id, "cycle_id": cycle_id, "decision": decision},
        human=(
            f"{campaign_id}/{cycle_id} -> origin gate: {decision}. A cycle holding at the gate "
            "acts on it within a second; one that is not clears it when it next arrives there."
        ),
    )


async def cmd_step_cycle(args: argparse.Namespace) -> CommandResult:
    rounds: int = max(1, args.rounds)
    sent = await cycle_scoped(open_stores(args), args, StepCyclePayload, "step", rounds=rounds)
    if isinstance(sent, CommandResult):
        return sent
    campaign_id, cycle_id = sent.campaign_id, sent.cycle_id
    logger.info("run control: %s/%s -> step %d round(s)", campaign_id, cycle_id, rounds)
    return CommandResult(
        data={"campaign_id": campaign_id, "cycle_id": cycle_id, "rounds": rounds},
        human=f"{campaign_id}/{cycle_id} -> stepping {rounds} round(s), then stopping again.",
    )


async def cmd_replace_dataset(args: argparse.Namespace) -> CommandResult:
    slug: str = args.slug.strip()
    stores = open_stores(args)
    refused = await refused_by(
        CommandDispatcher(stores).dispatch_replace_dataset(
            CommandCall(ReplaceDatasetPayload(slug=slug), uuid.uuid4().hex)
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


async def cmd_cancel_queued(args: argparse.Namespace) -> CommandResult:
    job_id: str = args.job_id.strip()
    stores = open_stores(args)
    refused = await refused_by(
        CommandDispatcher(stores).dispatch_workspace_command(
            CommandCall(CancelQueuedRunPayload(job_id=job_id), uuid.uuid4().hex)
        ),
        {"job_id": job_id},
    )
    if refused is not None:
        return refused
    logger.info("queued launch %s -> cancelled", job_id)
    return CommandResult(
        data={"job_id": job_id, "status": "cancelled"},
        human=f"{job_id} -> left the queue; nothing ran and nothing was spent.",
    )


async def cmd_set_concurrent_cycles(args: argparse.Namespace) -> CommandResult:
    limit: int = args.limit
    stores = open_stores(args)
    await CommandDispatcher(stores).dispatch_workspace_command(
        CommandCall(SetConcurrentCyclesPayload(max_concurrent_cycles=limit), uuid.uuid4().hex)
    )
    logger.info("account %s -> max_concurrent_cycles %d", stores.identity.user_id, limit)
    return CommandResult(
        data={"max_concurrent_cycles": limit, "status": "limit_set"},
        human=f"This account now holds at most {limit} campaign(s) at once, queued included.",
    )


async def cmd_delete(args: argparse.Namespace) -> CommandResult:
    keep_results: bool = args.keep_results
    refusal, campaign_id = await _dispatch(
        args, DeleteCampaignPayload, reason=args.reason, keep_results=keep_results
    )
    if refusal is not None:
        return refusal
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
