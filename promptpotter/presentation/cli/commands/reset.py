from __future__ import annotations

import argparse
import sys
from collections import Counter

from promptpotter.application.account import registered_or_default_identity
from promptpotter.application.maintenance.reset import ResetPlan, apply_reset, plan_reset
from promptpotter.config.paths import default_projects_root
from promptpotter.presentation.cli.commands.result import CommandResult


def _render_summary(plan: ResetPlan) -> str:
    lines = [f"reset under: {', '.join(str(root) for root in plan.roots)}"]
    counts: Counter[str] = Counter()
    sizes: Counter[str] = Counter()
    for drop in plan.drops:
        if drop.kind is not None and drop.unloadable is None:
            counts[drop.kind.name] += 1
            sizes[drop.kind.name] += drop.size
    lines += [
        f"  - {counts[name]:,} {name} file(s), {sizes[name] / 1_048_576:.1f} MiB"
        for name in sorted(counts)
    ]
    lines += [f"  ? unknown {drop.path}" for drop in plan.drops if drop.kind is None]
    lines += [
        f"  ! unloadable {drop.kind.fate.value} {drop.kind.name} {drop.path}: {drop.unloadable}"
        for drop in plan.drops
        if drop.kind is not None and drop.unloadable is not None
    ]
    lines.append(f"  kept: {plan.kept:,} survivor, loading cache and machine file(s)")
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
