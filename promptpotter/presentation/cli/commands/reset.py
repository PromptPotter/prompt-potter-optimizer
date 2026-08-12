"""Drop campaigns + sessions; PRESERVE ``measurements/`` (the DB core) and ``archive/``. Anything unnamed at the tenant top
level defaults to preserve — a reset never reaches a path it cannot explain."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from promptpotter.config.paths import DEFAULT_PROJECTS_ROOT
from promptpotter.domain.cycle_paths import WorkspaceDir
from promptpotter.infrastructure.store.io import rmtree_robust, unlink_robust
from promptpotter.infrastructure.store.session_pointer import (
    active_pointer_exists,
    clear_active_pointer,
)
from promptpotter.presentation.cli.commands._shared import CommandResult, identity_from_args

logger = logging.getLogger("promptpotter.presentation.cli.reset")

__all__ = ["cmd_reset"]


# Top-level names ``reset`` removes; everything else is preserved by default.
# Each tenant's active-session pointer lives at ``{tenant}/.workspace/active_session.json``
# and is cleared in lockstep with the campaigns/ + sessions/ trees it points into.
_DROP_NAMES = ("campaigns", "sessions")

# Preserved names — listed separately so the confirm prompt can name what survives.
# ``measurements/`` is the expensive DB core (real LLM spend); ``archive/`` is the
# recycle bin of archived campaigns plus the optimizer-call / sweep caches.
_PRESERVE_NAMES = ("measurements", "archive")


def _classify(tenant_dir: Path) -> tuple[list[Path], list[Path], list[Path]]:
    """Return (drop_paths, preserve_paths, surprise_paths) for *tenant_dir*."""
    drop: list[Path] = []
    preserve: list[Path] = []
    surprise: list[Path] = []
    if not tenant_dir.is_dir():
        return drop, preserve, surprise
    for entry in sorted(tenant_dir.iterdir()):
        if entry.name in _DROP_NAMES:
            if entry.exists():
                drop.append(entry)
        elif entry.name in _PRESERVE_NAMES:
            preserve.append(entry)
        else:
            surprise.append(entry)
    return drop, preserve, surprise


def _human_size(path: Path) -> str:
    try:
        if path.is_file():
            return f"{path.stat().st_size:,} B"
        if path.is_dir():
            total = sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
            n = sum(1 for _ in path.rglob("*") if _.is_file())
            return f"{n:,} files / {total / 1_048_576:.1f} MiB"
    except OSError:
        pass
    return ""


def _resolve_tenant_dirs(args: argparse.Namespace, projects_root: Path) -> list[Path]:
    """Tenant dirs to operate on, from ``identity_from_args`` — never ``args.tenant or "default"``. The first web sign-in RENAMES
    ``projects/default/``, so a hardcoded default makes a destructive verb silently do nothing and call it success."""
    if getattr(args, "all_tenants", False):
        if not projects_root.is_dir():
            return []
        return sorted(p for p in projects_root.iterdir() if p.is_dir())
    return [projects_root / identity_from_args(args).tenant_id]


def _render_summary(tenants: list[tuple[Path, list[Path], list[Path], list[Path]]]) -> str:
    lines: list[str] = []
    for tenant_dir, drop, preserve, surprise in tenants:
        lines.append(f"tenant: {tenant_dir}")
        if not tenant_dir.is_dir():
            lines.append("  (tenant dir does not exist — nothing to reset)")
            lines.append("")
            continue
        if drop:
            lines.append("  drop:")
            for p in drop:
                size = _human_size(p)
                tag = f"  ({size})" if size else ""
                lines.append(f"    - {p.name}{tag}")
        else:
            lines.append("  drop: (nothing — already clean)")
        if preserve:
            lines.append("  preserve:")
            for p in preserve:
                size = _human_size(p)
                tag = f"  ({size})" if size else ""
                lines.append(f"    + {p.name}{tag}")
        if surprise:
            lines.append(
                "  unrecognized (preserved by default — name them in _DROP_NAMES if you want them gone):"
            )
            for p in surprise:
                lines.append(f"    ? {p.name}")
        lines.append("")
    return "\n".join(lines).rstrip()


def _remove(path: Path) -> None:
    """Delete a tenant subtree through the package's two ROBUST deleters. This is the verb that removes whole tenant trees,
    and therefore the ``.inner/`` sandboxes a plain ``rmtree`` cannot remove at all."""
    if path.is_dir() and not path.is_symlink():
        rmtree_robust(path)
    elif path.exists() or path.is_symlink():
        unlink_robust(path)


def _tenants_with_pointers(tenant_dirs: list[Path]) -> list[WorkspaceDir]:
    """The in-scope tenant workspaces currently holding an active-session pointer. Returns the ROOTS, not the slugs —
    re-deriving ``projects_root / slug`` on the way back is how the two halves of that relation drift apart."""
    return [ws for td in tenant_dirs if active_pointer_exists(ws := WorkspaceDir(td))]


async def cmd_reset(args: argparse.Namespace) -> CommandResult:
    """Drop campaigns + sessions across the selected tenant(s); preserve ``archive/``."""
    projects_root = DEFAULT_PROJECTS_ROOT
    tenant_dirs = _resolve_tenant_dirs(args, projects_root)
    classified = [(td, *_classify(td)) for td in tenant_dirs]
    tenants_with_pointers = _tenants_with_pointers(tenant_dirs)

    has_anything_to_drop = any(drop for _td, drop, _p, _s in classified) or bool(
        tenants_with_pointers
    )
    summary = _render_summary(classified)
    if tenants_with_pointers:
        summary += "\n\npointers:"
        for ws in tenants_with_pointers:
            summary += f"\n    - projects/{ws.name}/.workspace/active_session.json"

    if getattr(args, "dry_run", False):
        if not has_anything_to_drop:
            return CommandResult(
                data={"tenants": [str(td) for td in tenant_dirs], "dropped": [], "dry_run": True},
                human="reset --dry-run:\n" + summary + "\n\n(nothing to drop)",
            )
        would_drop = [str(p) for _td, drop, _p, _s in classified for p in drop]
        for ws in tenants_with_pointers:
            would_drop.append(f"projects/{ws.name}/.workspace/active_session.json")
        return CommandResult(
            data={
                "tenants": [str(td) for td in tenant_dirs],
                "would_drop": would_drop,
                "dry_run": True,
            },
            human="reset --dry-run:\n" + summary,
        )

    if not has_anything_to_drop:
        return CommandResult(
            data={"tenants": [str(td) for td in tenant_dirs], "dropped": []},
            human=summary + "\n\nnothing to drop.",
        )

    if not getattr(args, "yes", False):
        prompt = summary + "\n\nproceed? [y/N]: "
        sys.stdout.write(prompt)
        sys.stdout.flush()
        reply = sys.stdin.readline().strip().lower()
        if reply not in ("y", "yes"):
            return CommandResult(
                data={"tenants": [str(td) for td in tenant_dirs], "dropped": [], "aborted": True},
                human="aborted.",
            )

    dropped: list[str] = []
    for _td, drop, _preserve, _surprise in classified:
        for p in drop:
            _remove(p)
            dropped.append(str(p))
            logger.info("reset: removed %s", p)
    for ws in tenants_with_pointers:
        clear_active_pointer(ws)
        dropped.append(f"projects/{ws.name}/.workspace/active_session.json")
        logger.info("reset: cleared active-session pointer for %s", ws.name)

    return CommandResult(
        data={"tenants": [str(td) for td in tenant_dirs], "dropped": dropped},
        human=(
            f"reset: dropped {len(dropped)} path(s).\n"
            "preserved: measurements/ (DB core) + archive/ (recycle bin + optimizer_calls + sweeps).\n"
            "next: `python -m promptpotter new <name>` (from datasets/<name>/)."
        ),
    )
