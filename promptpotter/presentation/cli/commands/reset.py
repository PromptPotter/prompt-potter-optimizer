"""The ``reset`` verb's shell: resolve which tenants, show the plan, confirm, apply. What a reset
drops and what it preserves is ``application/maintenance/reset.py``'s."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from promptpotter.application.maintenance.reset import ResetPlan, apply_reset, plan_reset
from promptpotter.config.paths import DEFAULT_PROJECTS_ROOT
from promptpotter.infrastructure.store.io import iter_files
from promptpotter.infrastructure.store.layout import SHARED_CACHE_DIRS
from promptpotter.presentation.cli.commands._shared import CommandResult, identity_from_args

__all__ = ["cmd_reset"]


def _size_tag(path: Path) -> str:
    try:
        if path.is_file():
            return f"  ({path.stat().st_size:,} B)"
        if path.is_dir():
            sizes = [st.st_size for _p, st in iter_files(path)]
            return f"  ({len(sizes):,} files / {sum(sizes) / 1_048_576:.1f} MiB)"
    except OSError:
        pass
    return ""


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
            lines.extend(f"    - {p.name}{_size_tag(p)}" for p in tenant.drop)
        else:
            lines.append("  drop: (nothing — already clean)")
        if tenant.preserve:
            lines.append("  preserve:")
            lines.extend(f"    + {p.name}{_size_tag(p)}" for p in tenant.preserve)
        if tenant.unrecognized:
            lines.append(
                "  unrecognized (preserved by default — name them in maintenance/reset.py::_DROP_NAMES if you want them gone):"
            )
            lines.extend(f"    ? {p.name}" for p in tenant.unrecognized)
        lines.append("")
    summary = "\n".join(lines).rstrip()
    if pointers := [t.pointer_label for t in plan.tenants if t.holds_pointer]:
        summary += "\n\npointers:" + "".join(f"\n    - {label}" for label in pointers)
    sandbox_lines: list[str] = []
    if plan.sandboxes:
        sandbox_lines.append("inner sandboxes (off-tree, dropped with their spend banked):")
        sandbox_lines.extend(
            f"    - {target.sandbox.name}  [{target.owner.campaign_id}]{_size_tag(target.sandbox)}"
            for target in plan.sandboxes
        )
    if plan.unattributable_sandboxes:
        sandbox_lines.append(
            "inner sandboxes KEPT (no usable owner record — cannot name a ledger to bank into):"
        )
        sandbox_lines.extend(f"    ? {sandbox.name}" for sandbox in plan.unattributable_sandboxes)
    if sandbox_lines:
        summary += "\n\n" + "\n".join(sandbox_lines)
    return summary


async def cmd_reset(args: argparse.Namespace) -> CommandResult:
    """Drop campaigns + sessions across the selected tenant(s); preserve the paid caches."""
    plan = plan_reset(
        DEFAULT_PROJECTS_ROOT,
        tenant_id=None
        if getattr(args, "all_tenants", False)
        else identity_from_args(args).tenant_id,
    )
    tenants = [str(tenant.workspace) for tenant in plan.tenants]
    drops = plan.drops
    summary = _render_summary(plan)

    if getattr(args, "dry_run", False):
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

    if not getattr(args, "yes", False):
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
            # Derived, never counted in prose: "the two paid caches" was already a number that
            # drifts the moment a third one lands.
            f"preserved: {', '.join(d + '/' for d in SHARED_CACHE_DIRS)} — the paid caches.\n"
            "banked: each campaign's spend plus every sandbox's unforwarded residue, onto the "
            "workspace ledger — a reset drops the data, never the money.\n"
            "next: `python -m promptpotter new <name>` (from datasets/<name>/)."
        ),
    )
