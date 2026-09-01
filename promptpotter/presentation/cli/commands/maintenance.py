"""``compact-archive`` — move the unread fields of candidate measurement rows into a gzip cold
store, put them back, or delete the store.

A thin shell, like every verb here: it picks a mode and renders counts. The passes themselves are
``application/archive_maintenance.py``, which is what lets the same three modes reach the REST API,
the webapp and an embedded host rather than the terminal alone.

Dry-run by default. ``--apply`` writes, and ``purge-cold --apply`` is the one step that destroys —
the rows it drops are paid LLM spend and nothing puts them back."""

from __future__ import annotations

import argparse

from promptpotter.application.archive_maintenance import (
    ArchiveReport,
    compact_measurement_archive,
    purge_cold_store,
    restore_measurement_archive,
)
from promptpotter.config.paths import DEFAULT_PROJECTS_ROOT
from promptpotter.infrastructure.store.stores import build_stores
from promptpotter.presentation.cli.commands._shared import CommandResult, identity_from_args

__all__ = ["cmd_compact_archive"]

_MB = 1024 * 1024

_MODES = {
    "compact": compact_measurement_archive,
    "restore": restore_measurement_archive,
    "purge-cold": purge_cold_store,
}


def _render(mode: str, report: ArchiveReport, *, dataset: str | None) -> str:
    scope = dataset or "every dataset"
    if report.archive_writers:
        return (
            f"compact-archive[{mode}]: SKIPPED — {report.archive_writers} cycle(s) can still "
            "append to the shared archive. Nothing was read or written."
        )
    verb = "did" if report.applied else "would"
    lines = [
        f"compact-archive[{mode}] over {scope}: {verb} touch {report.runs_touched} run(s), "
        f"{report.rows_moved} row(s); {report.runs_skipped} skipped.",
        f"  hot   {report.bytes_before / _MB:8.2f} MB -> {report.bytes_after / _MB:8.2f} MB",
        f"  cold  {report.cold_bytes / _MB:8.2f} MB",
        # `restore` puts fields BACK, so its net is negative by construction — named as the cost
        # it is rather than printed as a negative saving.
        f"  {'cost ' if report.bytes_freed < 0 else 'freed'} "
        f"{abs(report.bytes_freed) / _MB:8.2f} MB",
    ]
    if report.conflicts:
        lines.append(
            f"  {report.conflicts} run(s) REFUSED — the cold payload no longer lines up with the "
            "detail log, so nothing was put back rather than half of it."
        )
    if mode == "restore" and report.purged:
        lines.append(
            f"  {report.purged} run(s) were PURGED — measured, then dropped for storage on "
            "purpose. Nothing to put back; they still fit a ruler and still serve a cache hit."
        )
    for label, n in sorted(report.skipped_by_label.items()):
        lines.append(f"  skipped {n:5d} x {label}")
    if not report.applied and report.runs_touched:
        lines.append("\nDry run. Re-run with --apply to write.")
    if mode == "purge-cold" and not report.applied and report.runs_touched:
        lines.append("This one is IRREVERSIBLE — the rows cost real money to measure again.")
    return "\n".join(lines)


async def cmd_compact_archive(args: argparse.Namespace) -> CommandResult:
    stores = build_stores(identity_from_args(args), projects_root=DEFAULT_PROJECTS_ROOT)
    mode = str(args.mode)
    dataset = getattr(args, "dataset", None)
    report = _MODES[mode](stores, dataset=dataset, apply=bool(args.apply))
    return CommandResult(
        data=report.model_dump(mode="json"), human=_render(mode, report, dataset=dataset)
    )
