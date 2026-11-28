from __future__ import annotations

import argparse
import asyncio
import sys
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
    inventory_measurement_archive,
    reindex_measurement_archive,
)
from promptpotter.application.maintenance.landing import (
    Landing,
    apply_landing,
    holding_landing,
    last_landing,
    plan_landing,
)
from promptpotter.application.maintenance.reset import apply_reset, plan_reset
from promptpotter.application.views.render.maintenance import (
    render_compaction,
    render_inventory,
    render_landing,
    render_reset,
)
from promptpotter.config.paths import default_projects_root
from promptpotter.connectors import CONNECTORS
from promptpotter.domain.command_kinds import (
    PAYLOAD_MODEL_FOR_KIND,
    CommandKind,
    CompactArchivePayload,
    CyclePayload,
    Door,
)
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.presentation.cli.main import budget_from_args
from promptpotter.shared.command_result import CommandResult
from promptpotter.shared.errors import PayloadInvalidError
from promptpotter.shared.extras import require_extra

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
            **({"answer": answered} if answered else {}),
        },
        human=" ".join(
            filter(None, (f"{'/'.join(ids.values())} -> {kind} {sent.ack}.", follow_up))
        ),
    )


# A plan that ran and refused, having written nothing; a crash exits 1, an argument error 2.
REFUSED = 3


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
    summary = render_reset(plan)

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


async def cmd_land_data(args: argparse.Namespace) -> CommandResult:
    projects_root = default_projects_root()
    last = last_landing(projects_root)

    def land() -> tuple[Landing, bool]:
        # A dry run writes nothing, its hold's lock file included.
        with nullcontext() if args.dry_run else holding_landing(projects_root):
            landing = plan_landing(projects_root, reset=args.reset)
            written = not (landing.refused or args.dry_run)
            return (apply_landing(projects_root, landing) if written else landing), written

    # Off the loop: the hold waits on a landing another entry has in flight.
    landing, written = await asyncio.to_thread(land)
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
        human=render_landing(landing, written=written, last=last),
        exit_code=REFUSED if landing.refused else 0,
    )


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
            human=render_inventory(inventory, dataset=dataset),
        )
    payload = PayloadInvalidError.validating(
        CompactArchivePayload, {"mode": mode, "dataset": dataset, "apply": apply}
    )
    sent = await CommandDispatcher(stores).answer(
        archive_compaction.compact_archive, CommandCall(payload, door=Door.TERMINAL)
    )
    report = sent.result
    return CommandResult(
        data=report.model_dump(mode="json"),
        human=render_compaction(payload.mode, report, dataset=dataset),
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
    (connector,) = (c for c in CONNECTORS.table.values() if c.pin_roster is not None)
    assert connector.pin_roster is not None and connector.extra is not None
    require_extra(connector.extra)
    path = connector.pin_roster(args.dataset, args.version)
    return CommandResult(data={"path": str(path)}, human=f"pin-roster: {path.name} written.")
