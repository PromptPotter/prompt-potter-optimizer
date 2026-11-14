from __future__ import annotations

import argparse
import sys
from collections import Counter
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from promptpotter.application.account import local_stores, registered_or_default_identity
from promptpotter.application.commands import archive_compaction
from promptpotter.application.commands.dispatcher import CommandCall, CommandDispatcher
from promptpotter.application.commands.loop_commands import read_payload_file
from promptpotter.application.cycle_addressing import cycle_target
from promptpotter.application.datasets.startability import offer_extras
from promptpotter.application.maintenance.archive_maintenance import (
    ArchiveInventory,
    ArchiveReport,
    inventory_measurement_archive,
    reindex_measurement_archive,
)
from promptpotter.application.maintenance.landing import (
    LandedStamp,
    Landing,
    apply_landing,
    holding_landing,
    last_landing,
    plan_landing,
)
from promptpotter.application.maintenance.reset import ResetPlan, apply_reset, plan_reset
from promptpotter.config.paths import default_projects_root
from promptpotter.connectors.harbor import pin_roster
from promptpotter.domain.command_kinds import (
    PAYLOAD_MODEL_FOR_KIND,
    CommandKind,
    CompactArchivePayload,
    CyclePayload,
    Door,
)
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.presentation.cli.main import CommandResult, budget_from_args
from promptpotter.shared.errors import PayloadInvalidError
from promptpotter.shared.extras import Extra, require_extra

_ADDRESS = ("campaign_id", "cycle_id")


async def cmd_command(args: argparse.Namespace) -> CommandResult:
    """Every verb `cli/main.py::command_verb` derives from a payload model runs here."""
    kind = next(kind for kind in CommandKind if kind.cli_verb == args.command)
    model = PAYLOAD_MODEL_FOR_KIND[kind]
    stores = local_stores(tenant=args.tenant)
    fields: dict[str, Any] = {
        name: read_payload_file(value) if isinstance(value, Path) else value
        for name, value in vars(args).items()
        if name in model.model_fields
    }
    if "budget" in model.model_fields:
        fields["budget"] = budget_from_args(args)
    if issubclass(model, CyclePayload):
        # Named prefixes are the dispatcher's to resolve; the pointer and a sole cycle are not.
        hop = (
            CycleHop(campaign_id=args.campaign, cycle_id=args.cycle)
            if args.campaign and args.cycle
            else cycle_target(stores, args.campaign, args.cycle)
        )
        fields["campaign_id"], fields["cycle_id"] = hop.campaign_id, hop.cycle_id
    payload = PayloadInvalidError.validating(model, fields)
    sent = await CommandDispatcher(stores).dispatch(CommandCall(payload, door=Door.TERMINAL))
    # The address acted on, else the one field a workspace command names its subject by.
    acted = sent.payload.model_dump(mode="json")
    named = [name for name in _ADDRESS if name in model.model_fields] or [next(iter(fields))]
    ids = {name: str(acted[name]) for name in named}
    # A `{field}` in the follow-up names a field of what the kind answers, as the route serves it.
    answered = sent.result.model_dump(mode="json") if isinstance(sent.result, BaseModel) else {}
    follow_up = model.follow_up and model.follow_up.format_map(answered)
    return CommandResult(
        data={
            **acted,
            "command_id": sent.accepted.command_id,
            "ack": sent.ack,
        },
        human=" ".join(
            filter(None, (f"{'/'.join(ids.values())} -> {kind} {sent.ack}.", follow_up))
        ),
    )


# A plan that ran and refused, having written nothing; a crash exits 1, an argument error 2.
REFUSED = 3


def _render_summary(plan: ResetPlan) -> str:
    lines = [f"reset under: {', '.join(str(root) for root in plan.roots)}"]
    counts: Counter[str] = Counter()
    sizes: Counter[str] = Counter()
    for drop in plan.drops:
        if drop.kind is not None:
            counts[drop.kind.name] += 1
            sizes[drop.kind.name] += drop.size
    lines += [
        f"  - {counts[name]:,} {name} file(s), {sizes[name] / 1_048_576:.1f} MiB"
        for name in sorted(counts)
    ]
    lines += [f"  ? unknown {drop.path}" for drop in plan.drops if drop.kind is None]
    lines.append(f"  kept: {plan.kept:,} survivor, cache and machine file(s)")
    if plan.refusal is not None:
        lines.append(f"  refused: {plan.refusal}")
    return "\n".join(lines)


async def cmd_reset(args: argparse.Namespace) -> CommandResult:
    plan = plan_reset(
        default_projects_root(),
        tenant_id=None
        if args.all_tenants
        else registered_or_default_identity(args.tenant).tenant_id,
        staged={},
    )
    roots = [str(root) for root in plan.roots]
    drops = [str(drop.path) for drop in plan.drops]
    summary = _render_summary(plan)

    if plan.refusal is not None:
        return CommandResult(
            data={"roots": roots, "would_drop": drops, "refusal": plan.refusal},
            human=summary,
            exit_code=REFUSED,
        )
    if args.dry_run:
        return CommandResult(
            data={"roots": roots, "would_drop": drops, "kept": plan.kept, "dry_run": True},
            human="reset --dry-run:\n" + summary,
        )
    if not drops:
        return CommandResult(
            data={"roots": roots, "dropped": [], "kept": plan.kept},
            human=summary + "\n\nnothing to drop.",
        )
    if not args.yes:
        sys.stdout.write(summary + "\n\nproceed? [y/N]: ")
        sys.stdout.flush()
        reply = sys.stdin.readline().strip().lower()
        if reply not in ("y", "yes"):
            return CommandResult(
                data={"roots": roots, "dropped": [], "aborted": True},
                human="aborted.",
            )

    apply_reset(plan)
    return CommandResult(
        data={"roots": roots, "dropped": drops, "kept": plan.kept},
        human=(
            f"{summary}\n\nreset: dropped {len(drops):,} file(s).\n"
            "next: `python -m promptpotter new <name>` (from datasets/<name>/)."
        ),
    )


def _render_landing(landing: Landing, *, written: bool, last: LandedStamp | None) -> list[str]:
    lines = [] if last is None else [f"last landed {last.at}", *(f"  {s}" for s in last.said)]
    # A reset's line is among them: `reset --dry-run` breaks it down by kind.
    lines += landing.said
    lines += [f"unknown {path}" for path in landing.unknown]
    lines += [f"unloadable {one.said}" for one in landing.unloadable]
    if landing.refusal is None:
        lines.append(f"{'landed' if written else 'would land'}: proven {landing.proven:,} file(s)")
    else:
        lines.append(f"refused, nothing written: {landing.refusal}")
    return lines


async def cmd_land_data(args: argparse.Namespace) -> CommandResult:
    projects_root = default_projects_root()
    last = last_landing(projects_root)
    # A dry run writes nothing, its hold's lock file included.
    with nullcontext() if args.dry_run else holding_landing(projects_root):
        landing = plan_landing(projects_root, reset=args.reset)
        written = not (landing.refused or args.dry_run)
        if written:
            landing = apply_landing(projects_root, landing)
    return CommandResult(
        data={
            "written": written,
            "refusal": landing.refusal,
            "proven": landing.proven,
            "last_landing": None if last is None else last.model_dump(mode="json"),
            "said": list(landing.said),
            "unknown": [str(path) for path in landing.unknown],
            "unloadable": [one.said for one in landing.unloadable],
        },
        human="\n".join(_render_landing(landing, written=written, last=last)),
        exit_code=REFUSED if landing.refused else 0,
    )


_MB = 1024 * 1024


def _render(mode: str, report: ArchiveReport, *, dataset: str | None) -> str:
    scope = dataset or "every dataset"
    if report.archive_writers:
        return f"compact-archive[{mode}]: SKIPPED — {' '.join(report.notes)}"
    verb = "did" if report.applied else "would"
    lines = [
        f"compact-archive[{mode}] over {scope}: {verb} touch {report.files_touched} cell file(s), "
        f"{report.rows_moved} answer(s).",
        f"  hot   {report.bytes_before / _MB:8.2f} MB -> {report.bytes_after / _MB:8.2f} MB",
        f"  cold  {report.cold_bytes / _MB:8.2f} MB",
        # `restore` puts fields BACK, so its net is negative by construction.
        f"  {'cost ' if report.bytes_freed < 0 else 'freed'} "
        f"{abs(report.bytes_freed) / _MB:8.2f} MB",
    ]
    lines += [f"  {note}" for note in report.notes]
    for role, n in sorted(report.rows_skipped_by_role.items()):
        lines.append(f"  skipped {n:5d} x {role}")
    if not report.applied and report.files_touched:
        lines.append("\nDry run. Re-run with --apply to write.")
    if mode == "purge-cold" and not report.applied and report.files_touched:
        lines.append("This one is IRREVERSIBLE — the rows cost real money to measure again.")
    return "\n".join(lines)


def _render_inventory(inv: ArchiveInventory, *, dataset: str | None) -> str:
    scope = dataset or "every dataset"
    megabytes = (inv.total.hot_bytes + inv.total.cold_bytes) / _MB
    lines = [
        f"compact-archive[inventory] over {scope}: {inv.total.configurations} configuration(s), "
        f"{inv.total.answers} answer(s), {megabytes:.2f} MB.",
    ]
    for axis, rows in (("dataset", inv.by_dataset), ("role", inv.by_role), ("age", inv.by_age)):
        lines.append("")
        lines.append(f"  {axis:<24}{'configs':>8}{'answers':>9}{'hot MB':>10}{'cold MB':>10}")
        for row in rows:
            lines.append(
                f"  {row.key[:23]:<24}{row.configurations:>8}{row.answers:>9}"
                f"{row.hot_bytes / _MB:>10.2f}{row.cold_bytes / _MB:>10.2f}"
            )
    if inv.orphan_index_rows:
        lines.append(
            f"\n{inv.orphan_index_rows} index row(s) have no answer behind them — a claim rather "
            "than a measurement. `reindex --apply` clears them."
        )
    if inv.archive_writers:
        lines.append(
            f"\n{inv.archive_writers} cycle(s) can still append, so this is a snapshot of a store "
            "that is moving. Nothing was written."
        )
    return "\n".join(lines)


async def cmd_compact_archive(args: argparse.Namespace) -> CommandResult:
    stores = local_stores(tenant=args.tenant)
    mode = str(args.mode)
    dataset: str | None = args.dataset
    apply = bool(args.apply)
    # A read, so off the command highway: a census filed as a `CommandRecord` bills a no-op.
    if mode == "inventory":
        inventory = inventory_measurement_archive(stores, dataset=dataset)
        return CommandResult(
            data=inventory.model_dump(mode="json"),
            human=_render_inventory(inventory, dataset=dataset),
        )
    payload = PayloadInvalidError.validating(
        CompactArchivePayload, {"mode": mode, "dataset": dataset, "apply": apply}
    )
    sent = await CommandDispatcher(stores).answer(
        archive_compaction.compact_archive, CommandCall(payload, door=Door.TERMINAL)
    )
    report = sent.result
    return CommandResult(
        data=report.model_dump(mode="json"), human=_render(mode, report, dataset=dataset)
    )


async def cmd_reindex(args: argparse.Namespace) -> CommandResult:
    stores = local_stores(tenant=args.tenant)
    apply = bool(args.apply)
    counts = reindex_measurement_archive(stores, apply=apply)
    human = (
        f"reindex: {'indexed' if apply else 'would index'} {counts['indexed']} run(s), "
        f"{counts['undescribed']} undescribed left out."
    )
    if not apply:
        human += "\nDry run. Re-run with --apply to write."
    return CommandResult(data={**counts, "applied": apply}, human=human)


async def cmd_offer_extras(_args: argparse.Namespace) -> CommandResult:
    extras = sorted(offer_extras())
    return CommandResult(data={"extras": extras}, human="\n".join(extras))


async def cmd_pin_roster(args: argparse.Namespace) -> CommandResult:
    require_extra(Extra.HARBOR)
    path = pin_roster(args.dataset, args.version)
    return CommandResult(data={"path": str(path)}, human=f"pin-roster: {path.name} written.")
