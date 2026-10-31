from __future__ import annotations

import argparse
import sys
from collections import Counter

from promptpotter.application.account import registered_or_default_identity
from promptpotter.application.maintenance.landing import (
    Landing,
    Unloadable,
    apply_landing,
    plan_landing,
)
from promptpotter.application.maintenance.reset import ResetPlan, apply_reset, plan_reset
from promptpotter.config.paths import default_projects_root
from promptpotter.presentation.cli.commands.result import CommandResult

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


def _unloadable(one: Unloadable) -> str:
    return f"{one.found.kind.fate.value} {one.found.kind.name} {one.path}: {one.why}"


def _render_landing(landing: Landing, *, written: bool) -> list[str]:
    lines = [f"migrate {one.said}" for one in landing.staged]
    lines += [f"drop {_unloadable(one)}" for one in landing.caches]
    if landing.reset is not None:
        lines.append(_render_summary(landing.reset))
    lines += [f"unknown {path}" for path in landing.unknown]
    lines += [f"unloadable {_unloadable(one)}" for one in landing.unloadable]
    if landing.refusal is None:
        lines.append(f"{'landed' if written else 'would land'}: proven {landing.proven:,} file(s)")
    else:
        lines.append(f"refused, nothing written: {landing.refusal}")
    return lines


async def cmd_land_data(args: argparse.Namespace) -> CommandResult:
    landing = plan_landing(default_projects_root(), reset=args.reset)
    written = not (landing.refused or args.dry_run)
    if written:
        apply_landing(landing)
    return CommandResult(
        data={
            "written": written,
            "refusal": landing.refusal,
            "proven": landing.proven,
            "migrated": [one.said for one in landing.staged],
            "dropped_caches": [str(one.path) for one in landing.caches],
            "reset": None if landing.reset is None else len(landing.reset.drops),
            "unknown": [str(path) for path in landing.unknown],
            "unloadable": [_unloadable(one) for one in landing.unloadable],
        },
        human="\n".join(_render_landing(landing, written=written)),
        exit_code=REFUSED if landing.refused else 0,
    )
