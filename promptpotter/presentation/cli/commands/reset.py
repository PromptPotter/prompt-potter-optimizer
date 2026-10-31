from __future__ import annotations

import argparse
import sys

from promptpotter.application.account import registered_or_default_identity
from promptpotter.application.maintenance.reset import (
    ResetPlan,
    apply_reset,
    plan_reset,
    size_tag,
)
from promptpotter.config.paths import SHARED_CACHE_DIRS, default_projects_root
from promptpotter.presentation.cli.commands.result import CommandResult


def _render_summary(plan: ResetPlan) -> str:
    lines: list[str] = []
    for tenant in plan.tenants:
        lines.append(f"tenant: {tenant.workspace}")
        if not tenant.workspace.is_dir():
            lines.append("  (tenant dir does not exist — nothing to reset)")
            lines.append("")
            continue
        if tenant.drop:
            lines.append("  drop:")
            lines.extend(f"    - {p.name}{size_tag(p)}" for p in tenant.drop)
        else:
            lines.append("  drop: (nothing — already clean)")
        if tenant.preserve:
            lines.append("  preserve:")
            lines.extend(f"    + {p.name}{size_tag(p)}" for p in tenant.preserve)
        if tenant.unrecognized:
            lines.append(
                "  unrecognized (preserved by default — name them in maintenance/reset.py::_DROP_NAMES if you want them gone):"
            )
            lines.extend(f"    ? {p.name}" for p in tenant.unrecognized)
        lines.append("")
    summary = "\n".join(lines).rstrip()
    sandbox_lines: list[str] = []
    if plan.sandboxes:
        sandbox_lines.append("inner sandboxes (off-tree, dropped with their owner's campaigns):")
        sandbox_lines.extend(
            f"    - {target.sandbox.name}  [{target.owner.campaign_id}]{size_tag(target.sandbox)}"
            for target in plan.sandboxes
        )
    if plan.unattributable_sandboxes:
        sandbox_lines.append(
            "inner sandboxes KEPT (no usable owner record — cannot name the tenant they belong to):"
        )
        sandbox_lines.extend(f"    ? {sandbox.name}" for sandbox in plan.unattributable_sandboxes)
    if sandbox_lines:
        summary += "\n\n" + "\n".join(sandbox_lines)
    return summary


async def cmd_reset(args: argparse.Namespace) -> CommandResult:
    plan = plan_reset(
        default_projects_root(),
        tenant_id=None
        if args.all_tenants
        else registered_or_default_identity(args.tenant).tenant_id,
    )
    tenants = [str(tenant.workspace) for tenant in plan.tenants]
    drops = plan.drops
    summary = _render_summary(plan)

    if args.dry_run:
        if not drops:
            return CommandResult(
                data={"tenants": tenants, "dropped": [], "dry_run": True},
                human="reset --dry-run:\n" + summary + "\n\n(nothing to drop)",
            )
        return CommandResult(
            data={"tenants": tenants, "would_drop": drops, "dry_run": True},
            human="reset --dry-run:\n" + summary,
        )

    if not drops:
        return CommandResult(
            data={"tenants": tenants, "dropped": []},
            human=summary + "\n\nnothing to drop.",
        )

    if not args.yes:
        sys.stdout.write(summary + "\n\nproceed? [y/N]: ")
        sys.stdout.flush()
        reply = sys.stdin.readline().strip().lower()
        if reply not in ("y", "yes"):
            return CommandResult(
                data={"tenants": tenants, "dropped": [], "aborted": True},
                human="aborted.",
            )

    apply_reset(plan)

    kept = plan.unattributable_sandboxes
    kept_note = (
        f"kept: {len(kept)} inner sandbox(es) with no usable owner record.\n" if kept else ""
    )
    return CommandResult(
        data={
            "tenants": tenants,
            "dropped": drops,
            "kept_sandboxes": [str(p) for p in kept],
        },
        human=(
            f"reset: dropped {len(drops)} path(s), including {len(plan.sandboxes)} inner "
            f"sandbox(es).\n"
            f"{kept_note}"
            f"preserved: the archive home's paid caches "
            f"({', '.join(d + '/' for d in SHARED_CACHE_DIRS)}).\n"
            "kept: each account's charges, written at every send — a reset drops the data, never "
            "the money.\n"
            "next: `python -m promptpotter new <name>` (from datasets/<name>/)."
        ),
    )
