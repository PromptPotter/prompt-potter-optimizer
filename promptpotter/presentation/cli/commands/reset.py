"""``reset`` — drop campaigns + sessions; preserve ``archive/`` (measurements).

Operator escape hatch for the "obsoleted by code change" case: per-cycle
artifacts under ``campaigns/`` and ``sessions/`` are a function of code +
measurements, so they're cheap to regenerate. The MeasurementArchive
(``archive/measurements/``) and the optimizer-call cache
(``archive/optimizer_calls/``) cost real LLM spend to build and survive
every reset. Anything else at the tenant top level the operator hasn't
named yet defaults to *preserve* — a reset never reaches a path it can't
explain.
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
from pathlib import Path

from promptpotter.infrastructure.store import clear_active_pointer, read_active_pointer
from promptpotter.infrastructure.store.paths import DEFAULT_PROJECTS_ROOT
from promptpotter.presentation.cli.commands._shared import CommandResult

logger = logging.getLogger("promptpotter.presentation.cli.reset")

__all__ = ["cmd_reset"]


# Top-level names under a tenant dir that `reset` removes. Everything else
# (notably `archive/`) is preserved by default — adding a new disposable
# tree means adding its name here. The global `.promptpotter/active_session.json`
# pointer is handled separately (cleared iff it points at a tenant we're
# resetting).
_DROP_NAMES = ("campaigns", "sessions")

# Top-level names `reset` documents as preserved. Listed separately so the
# confirm prompt can name what survives. Adding a new durable tree means
# adding its name here.
_PRESERVE_NAMES = ("archive",)


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
    """Best-effort size summary — entries × bytes for dirs, bytes for files."""
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
    """Pick the tenant dirs to operate on, honoring ``--tenant`` / ``--all-tenants``."""
    if getattr(args, "all_tenants", False):
        if not projects_root.is_dir():
            return []
        return sorted(p for p in projects_root.iterdir() if p.is_dir())
    tenant_id = getattr(args, "tenant", None) or "default"
    return [projects_root / tenant_id]


def _render_summary(tenants: list[tuple[Path, list[Path], list[Path], list[Path]]]) -> str:
    """Format the drop/preserve/surprise breakdown for the confirm prompt + dry-run."""
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
    """Remove *path* — directories recursively, files directly."""
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()


def _active_pointer_in_scope(tenant_dirs: list[Path]) -> bool:
    """True iff the global active-session pointer points at a tenant we're resetting."""
    tenant_id, _, _, _ = read_active_pointer()
    if not tenant_id:
        return False
    return any(td.name == tenant_id for td in tenant_dirs)


async def cmd_reset(args: argparse.Namespace) -> CommandResult:
    """Drop campaigns + sessions across the selected tenant(s); preserve ``archive/``.

    Flags:
      --tenant <name>       single tenant (default: 'default')
      --all-tenants         iterate every tenant under projects_root
      --yes                 skip confirmation prompt
      --dry-run             list what would be removed; touch nothing
    """
    projects_root = DEFAULT_PROJECTS_ROOT
    tenant_dirs = _resolve_tenant_dirs(args, projects_root)
    classified = [(td, *_classify(td)) for td in tenant_dirs]
    pointer_in_scope = _active_pointer_in_scope(tenant_dirs)

    has_anything_to_drop = any(drop for _td, drop, _p, _s in classified) or pointer_in_scope
    summary = _render_summary(classified)
    if pointer_in_scope:
        summary += (
            "\n\nglobal:\n    - .promptpotter/active_session.json (points to a tenant in scope)"
        )

    if getattr(args, "dry_run", False):
        if not has_anything_to_drop:
            return CommandResult(
                data={"tenants": [str(td) for td in tenant_dirs], "dropped": [], "dry_run": True},
                human="reset --dry-run:\n" + summary + "\n\n(nothing to drop)",
            )
        would_drop = [str(p) for _td, drop, _p, _s in classified for p in drop]
        if pointer_in_scope:
            would_drop.append(".promptpotter/active_session.json")
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
    if pointer_in_scope:
        clear_active_pointer()
        dropped.append(".promptpotter/active_session.json")
        logger.info("reset: cleared active-session pointer")

    return CommandResult(
        data={"tenants": [str(td) for td in tenant_dirs], "dropped": dropped},
        human=(
            f"reset: dropped {len(dropped)} path(s).\n"
            "preserved: archive/ (measurements + optimizer_calls + sweeps).\n"
            "next: `python -m promptpotter optimize --config datasets/<name>/campaign.json`."
        ),
    )
