"""``compact-archive`` — count what the measurement archive holds, move the unread fields of
candidate rows into a gzip cold store, put them back, or delete the store.

A thin shell, like every verb here: it picks a mode and renders counts. The passes themselves are
``application/maintenance/archive_maintenance.py``, which is what lets the three WRITE modes reach
the REST API, the webapp and an embedded host rather than the terminal alone.

``inventory`` is the read, and its absence from that highway is the boundary: `/commands/{kind}`
is the control-plane WRITE path, so recording a census as a `CommandRecord` would bill an act that
changed nothing. Its peers are `reindex` and `restamp`, terminal verbs over a quiescent store.

Dry-run by default. ``--apply`` writes, and ``purge-cold --apply`` is the one step that destroys —
the rows it drops are paid LLM spend and nothing puts them back."""

from __future__ import annotations

import argparse

from promptpotter.application.maintenance.archive_maintenance import (
    ArchiveInventory,
    ArchiveReport,
    compact_measurement_archive,
    inventory_measurement_archive,
    purge_cold_store,
    restore_measurement_archive,
)
from promptpotter.config.paths import DEFAULT_PROJECTS_ROOT
from promptpotter.infrastructure.store.stores import build_stores
from promptpotter.presentation.cli.commands._shared import CommandResult, identity_from_args

__all__ = ["cmd_compact_archive"]

_MB = 1024 * 1024


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


def _render_inventory(inv: ArchiveInventory, *, dataset: str | None) -> str:
    scope = dataset or "every dataset"
    megabytes = (inv.total.hot_bytes + inv.total.cold_bytes) / _MB
    lines = [
        f"compact-archive[inventory] over {scope}: {inv.total.runs} run(s), "
        f"{inv.total.cells} cell(s), {megabytes:.2f} MB.",
    ]
    for axis, rows in (("dataset", inv.by_dataset), ("label", inv.by_label), ("age", inv.by_age)):
        lines.append("")
        lines.append(
            f"  {axis:<24}{'runs':>6}{'cells':>8}{'hot MB':>10}{'cold MB':>10}{'replay':>9}"
        )
        for row in rows:
            replay = "-" if row.replay_rate is None else f"{row.replay_rate:.1%}"
            lines.append(
                f"  {row.key[:23]:<24}{row.runs:>6}{row.cells:>8}"
                f"{row.hot_bytes / _MB:>10.2f}{row.cold_bytes / _MB:>10.2f}{replay:>9}"
            )
    if inv.orphan_index_rows:
        lines.append(
            f"\n{inv.orphan_index_rows} index row(s) carry no detail file — a claim rather than a "
            "measurement, so every count above is an UPPER BOUND until `reindex` runs."
        )
    if inv.archive_writers:
        lines.append(
            f"\n{inv.archive_writers} cycle(s) can still append, so this is a snapshot of a store "
            "that is moving. Nothing was written."
        )
    return "\n".join(lines)


async def cmd_compact_archive(args: argparse.Namespace) -> CommandResult:
    stores = build_stores(identity_from_args(args), projects_root=DEFAULT_PROJECTS_ROOT)
    mode = str(args.mode)
    dataset = getattr(args, "dataset", None)
    apply = bool(args.apply)
    match mode:
        case "inventory":
            inventory = inventory_measurement_archive(stores, dataset=dataset)
            return CommandResult(
                data=inventory.model_dump(mode="json"),
                human=_render_inventory(inventory, dataset=dataset),
            )
        case "compact":
            report = compact_measurement_archive(stores, dataset=dataset, apply=apply)
        case "restore":
            report = restore_measurement_archive(stores, dataset=dataset, apply=apply)
        case "purge-cold":
            report = purge_cold_store(stores, dataset=dataset, apply=apply)
        case _:
            raise ValueError(f"compact-archive: no such mode {mode!r}")
    return CommandResult(
        data=report.model_dump(mode="json"), human=_render(mode, report, dataset=dataset)
    )
