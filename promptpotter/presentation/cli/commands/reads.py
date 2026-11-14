"""Every read verb: its ``ReadKind`` row's params off argv, answered as every door answers it, then
rendered by the verb's own renderer; and the small verbs that read the machine and the trail."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from promptpotter.application import health, notices
from promptpotter.application.account import local_stores, tenant_trail, trail_by_email
from promptpotter.application.caller import Caller
from promptpotter.application.command_reading import CommandReading
from promptpotter.application.commands.loop_commands import read_payload_file
from promptpotter.application.cycle_addressing import cycle_target, latest_launch
from promptpotter.application.evidence.read import Evidence
from promptpotter.application.head_to_head_price import ArmPrice, HeadToHeadPrice
from promptpotter.application.jobs.capacity import SLOT_BOUND_WORDS
from promptpotter.application.jobs.machine_status import MachineStatusResponse
from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.application.read_params import (
    PARAMS_FOR_READ,
    CycleExportParams,
    EvidenceParams,
    HeadToHeadPriceParams,
    HierarchyParams,
    MachineStatusParams,
    ReadParams,
)
from promptpotter.application.views.evidence import evidence_lines
from promptpotter.application.views.render.export import ExportFiles
from promptpotter.application.views.trail import trail_notes, trail_rows
from promptpotter.config.logging import setup_logging
from promptpotter.config.paths import default_projects_root
from promptpotter.domain.cycle_paths import Subject
from promptpotter.domain.hierarchy import HierarchyCampaign, HierarchyCourse, HierarchyResponse
from promptpotter.domain.spend import usd_text
from promptpotter.presentation.cli.commands.launch import head_to_head_command
from promptpotter.presentation.cli.main import VERB_PACKAGE, VERBS, CommandResult, said_text
from promptpotter.shared.errors import PayloadInvalidError
from promptpotter.shared.named_rows import imported

if TYPE_CHECKING:
    Render = Callable[[ReadParams, Any, argparse.Namespace, Caller], CommandResult]


_CYCLE = frozenset({"campaign_id", "cycle_id"})
# The flags a per-cycle row's verb addresses its cycle by (`cycle_target`).
_ADDRESSING = frozenset({"campaign", "cycle"})


def _unread_flags() -> list[str]:
    """Every read verb's flag that lands on no field of its row's model, no declared renderer
    arg and no cycle address: a value `cmd_read` would drop unsaid."""
    unread: list[str] = []
    for verb in VERBS:
        if verb.read is None:
            continue
        fields = PARAMS_FOR_READ[verb.read].model_fields.keys()
        taken = {*fields, *verb.render_args, *(_ADDRESSING if fields >= _CYCLE else ())}
        unread += [f"{verb.name} {a.dest}" for a in verb.args if a.dest not in taken]
    return unread


if unread := _unread_flags():
    raise TypeError(f"read verb flags no row field or renderer takes: {', '.join(unread)}")


def command_human(
    _params: ReadParams, read: CommandReading, _args: argparse.Namespace, _caller: Caller
) -> CommandResult:
    where = f" on {read.subject}" if read.subject else ""
    return CommandResult(
        data=read.model_dump(mode="json"),
        human=f"{read.command_id} ({read.kind}{where}): {read.status} as of "
        f"{read.last_updated_at}. {read.said}",
    )


async def cmd_read(args: argparse.Namespace) -> CommandResult:
    setup_logging(style="full" if args.verbose else "cli")
    verb = next(verb for verb in VERBS if verb.name == args.command)
    assert verb.read is not None and verb.render is not None
    model = PARAMS_FOR_READ[verb.read]
    stores = local_stores(tenant=args.tenant)
    # A flag left unset leaves the row's own default; a structured field is named by a YAML file.
    fields: dict[str, Any] = {
        name: read_payload_file(value) if isinstance(value, Path) else value
        for name, value in vars(args).items()
        if name in model.model_fields and value is not None
    }
    if model.model_fields.keys() >= _CYCLE:
        hop = cycle_target(stores, args.campaign, args.cycle)
        fields |= {"campaign_id": hop.campaign_id, "cycle_id": hop.cycle_id}
    params = PayloadInvalidError.validating(model, fields)
    caller = Caller(stores)
    answer = await params.answered(caller)
    render = cast("Render", imported(VERB_PACKAGE, verb.render))
    return render(params, answer, args, caller)


def _line(course: HierarchyCourse, *, active: bool) -> str:
    state = course.state
    return (
        course.address
        + (
            f"  paused by {state.pause.cause.value}"
            + (f": {state.pause.detail}" if state.pause.detail else "")
            if state.pause
            else ""
        )
        + (
            f"  next: {state.admission.offers}"
            if state.admission.offers
            else f"  ({state.admission.refusal})"
        )
        + ("  <- latest launch" if active else "")
    )


def cycles_human(
    params: HierarchyParams,
    listing: HierarchyResponse,
    _args: argparse.Namespace,
    caller: Caller,
) -> CommandResult:
    inside = params.inside
    hop = latest_launch(caller.stores, inside=inside)
    active = None if hop is None else Subject.of((*inside, hop)).address
    lines: list[str] = []
    for campaign in listing.entries:
        if not isinstance(campaign, HierarchyCampaign):
            continue
        for course in campaign.children:
            lines.append(_line(course, active=course.address == active))
            if (climb := course.climb) is not None:
                lines.append(f"    {said_text(climb)}")
            lines.append("    " + " · ".join(said_text(r) for r in course.figures))
            lines.extend(f"    {said_text(r)}" for r in course.lines)
            if (said := course.said) is not None:
                lines.append(f"    {said.tone}: {said_text(said)}")
    # Each arm campaign's `<h2h>:<key>`: the head-to-head ids already taken.
    arms = [
        f"  {tag.value}  {campaign.address}"
        for campaign in listing.entries
        if (tag := campaign.tag("arm")) is not None
    ]
    if arms:
        lines += ["", "head-to-head arms:", *arms]
    return CommandResult(
        data=listing.model_dump(mode="json"),
        human="\n".join(lines) or "No cycles match.",
    )


def evidence_human(
    _params: EvidenceParams, ev: Evidence, args: argparse.Namespace, _caller: Caller
) -> CommandResult:
    return CommandResult(data=ev.model_dump(), human="\n".join(evidence_lines(ev, top=args.top)))


def export_written(
    params: CycleExportParams, files: ExportFiles, args: argparse.Namespace, _caller: Caller
) -> CommandResult:
    ids = {"campaign_id": params.campaign_id, "cycle_id": params.cycle_id, "tier": params.tier}
    single = files.single
    if single is not None and args.out is None:
        return CommandResult(data={**ids, "text": single}, human=single.rstrip("\n"))
    root = (args.out or Path.cwd()) / files.name
    for rel, text in files.files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    written = sorted(files.files)
    return CommandResult(
        data={**ids, "dir": str(root), "files": written},
        human=f"{params.tier}: wrote {len(written)} file(s) under {root}",
    )


def _clock(seconds: float | None) -> str:
    if seconds is None:
        return "--"
    return f"{seconds / 3600:.1f} h" if seconds >= 3600 else f"{seconds / 60:.0f} min"


def _arm_line(arm: ArmPrice) -> str:
    address = f"{arm.head_to_head_id}:{arm.arm_key}"
    refused = "" if arm.refused is None else f"  REFUSED: {arm.refused}"
    return (
        f"  {address:<34} {arm.optimizer:<8} {arm.rounds:>3} rounds"
        f"  {arm.search_cells:>5} + {arm.bench_cells:<4} calls  {usd_text(arm.usd):>8}"
        f"  {_clock(arm.wall_clock_s):>7} ({_clock(arm.serial_s)} serial){refused}"
    )


def _human(price: HeadToHeadPrice) -> str:
    cell = price.cell
    timed = "untimed" if cell.seconds is None else f"{cell.seconds:.1f}s"
    head = (
        f"bank {price.dataset_hash[:12]} · {price.search_rows} search rows, {price.bench_rows} "
        f"held out · a cell ${cell.usd:.5f} and {timed}, over the archive's {cell.cells} newest "
        f"rows · {price.cells_in_flight} cells in flight"
    )
    optimizers = [
        f"  {name:<8} unpriced: no campaign here called it in a round, so its arms leave its "
        "own calls out"
        if held is None
        else f"  {name:<8} {usd_text(held.usd)} and {held.seconds:.0f}s a round, over {held.rounds} "
        f"rounds of {held.campaigns} campaign(s)"
        for name, held in price.optimizers.items()
    ]
    mints = " && \\\n  ".join(head_to_head_command(head) for head in price.heads)
    return "\n".join(
        [
            head,
            "Optimizer calls, as measured here:",
            *optimizers,
            "Arms (calls = search + held-out cells; a point's arms are held equal on search):",
            *map(_arm_line, price.arms),
            f"Total {usd_text(price.usd)} · {_clock(price.wall_clock_s)} "
            f"({_clock(price.serial_s)} serial), every arm minted after the last.",
            "Mint them, one head-to-head per budget point:",
            f"  {mints}",
        ]
    )


def price_human(
    _params: HeadToHeadPriceParams,
    price: HeadToHeadPrice,
    _args: argparse.Namespace,
    _caller: Caller,
) -> CommandResult:
    return CommandResult(data=price.model_dump(mode="json"), human=_human(price))


def machine_status_human(
    _params: MachineStatusParams,
    status: MachineStatusResponse,
    _args: argparse.Namespace,
    _caller: Caller,
) -> CommandResult:
    lines = [
        f"running {status.occupied} of {status.capacity} (ceiling {status.ceiling}), "
        f"{status.queued} queued"
    ]
    said = status.model_dump()
    lines += [SLOT_BOUND_WORDS[b].format(**said) for b in (status.bound, status.account_bound) if b]
    notice = status.notice
    lines.append("a slot is free" if notice is None else said_text(notice))
    lines += [
        f"running: {slot.what} ({slot.user}) since {slot.started_at or 'starting'}"
        for slot in status.running
    ]
    lines += [
        f"queued #{entry.position}: {entry.job_id}  {entry.what}  since {entry.created_at}"
        for entry in status.queue
    ]
    lines += [
        f"refused {refusal.released_at}: {refusal.job_id}  {refusal.what}  {refusal.reason}"
        for refusal in status.refused
    ]
    return CommandResult(
        data=status.model_dump(mode="json"),
        human="\n".join(lines),
    )


async def cmd_close_admission(args: argparse.Namespace) -> CommandResult:
    held = JobRegistry.attach(local_stores(tenant=args.tenant).jobs_dir).close_for_landing(
        args.target
    )
    return CommandResult(
        data={"target": args.target, "held": [job.job_id for job in held]},
        # The first word is the verdict `deploy-linux/update.sh` reads.
        human="\n".join(
            [f"held {job.what} ({job.user_id}) on {job.code_revision[:12]}" for job in held]
            or [f"closed to all but {args.target}"]
        ),
    )


async def cmd_health(_args: argparse.Namespace) -> CommandResult:
    checked = await asyncio.to_thread(notices.check_and_tell)
    return CommandResult(
        data={one.probe.name: one.reading._asdict() for one in checked},
        # The state leads each line: `deploy-linux/update.sh` reads it.
        human="\n".join(f"{one.reading.state} {health.row(one)}" for one in checked),
        exit_code=int(any(one.reading.state == "broken" for one in checked)),
    )


async def cmd_trail(args: argparse.Namespace) -> CommandResult:
    email = args.email.strip()
    trail = (
        trail_by_email(default_projects_root(), email)
        if email
        else tenant_trail(local_stores(tenant=args.tenant))
    )
    nothing = "Nothing recorded yet." if trail.own else f"No account with email {email}."
    return CommandResult(
        data={
            "accounts": sorted(trail.own),
            "lines": [line._asdict() for line in trail.lines],
            "unreadable": trail.unreadable,
        },
        human="\n".join([*(trail_rows(trail) or [nothing]), *trail_notes(trail)]),
    )
