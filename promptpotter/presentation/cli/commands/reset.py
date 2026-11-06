from __future__ import annotations

import argparse
import sys

from promptpotter.application.account import registered_or_default_identity
from promptpotter.application.maintenance.landing import (
    apply_landing,
    landing_data,
    landing_lines,
    plan_landing,
)
from promptpotter.application.maintenance.reset import apply_reset, plan_reset, reset_lines
from promptpotter.config.paths import default_projects_root
from promptpotter.presentation.cli.commands.result import CommandResult

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
    summary = reset_lines(plan)

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
    landing = plan_landing(default_projects_root(), reset=args.reset)
    written = not (landing.refused or args.dry_run)
    if written:
        apply_landing(landing)
    return CommandResult(
        data=landing_data(landing, written=written),
        human="\n".join(landing_lines(landing, written=written)),
        exit_code=REFUSED if landing.refused else 0,
    )
