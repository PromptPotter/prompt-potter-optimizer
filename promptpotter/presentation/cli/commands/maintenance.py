from __future__ import annotations

import argparse
import uuid

from promptpotter.application.commands.dispatcher import CommandCall, CommandDispatcher
from promptpotter.application.commands.payloads import CompactArchivePayload
from promptpotter.application.maintenance.archive_maintenance import (
    ArchiveInventory,
    ArchiveReport,
    inventory_measurement_archive,
)
from promptpotter.presentation.cli.commands.result import CommandResult
from promptpotter.presentation.cli.commands.workspace import open_stores

__all__ = ["cmd_compact_archive"]

_MB = 1024 * 1024


def _render(mode: str, report: ArchiveReport, *, dataset: str | None) -> str:
    scope = dataset or "every dataset"
    if report.archive_writers:
        return f"compact-archive[{mode}]: SKIPPED — {' '.join(report.notes)}"
    verb = "did" if report.applied else "would"
    lines = [
        f"compact-archive[{mode}] over {scope}: {verb} touch {report.files_touched} cell file(s), "
        f"{report.rows_moved} answer(s).",
        f"  hot   {report.bytes_before / _MB:8.2f} MB -> {report.bytes_after / _MB:8.2f} MB",
        f"  cold  {report.cold_bytes / _MB:8.2f} MB",
        # `restore` puts fields BACK, so its net is negative by construction.
        f"  {'cost ' if report.bytes_freed < 0 else 'freed'} "
        f"{abs(report.bytes_freed) / _MB:8.2f} MB",
    ]
    lines += [f"  {note}" for note in report.notes]
    for role, n in sorted(report.rows_skipped_by_role.items()):
        lines.append(f"  skipped {n:5d} x {role}")
    if not report.applied and report.files_touched:
        lines.append("\nDry run. Re-run with --apply to write.")
    if mode == "purge-cold" and not report.applied and report.files_touched:
        lines.append("This one is IRREVERSIBLE — the rows cost real money to measure again.")
    return "\n".join(lines)


def _render_inventory(inv: ArchiveInventory, *, dataset: str | None) -> str:
    scope = dataset or "every dataset"
    megabytes = (inv.total.hot_bytes + inv.total.cold_bytes) / _MB
    lines = [
        f"compact-archive[inventory] over {scope}: {inv.total.configurations} configuration(s), "
        f"{inv.total.answers} answer(s), {megabytes:.2f} MB.",
    ]
    for axis, rows in (("dataset", inv.by_dataset), ("role", inv.by_role), ("age", inv.by_age)):
        lines.append("")
        lines.append(f"  {axis:<24}{'configs':>8}{'answers':>9}{'hot MB':>10}{'cold MB':>10}")
        for row in rows:
            lines.append(
                f"  {row.key[:23]:<24}{row.configurations:>8}{row.answers:>9}"
                f"{row.hot_bytes / _MB:>10.2f}{row.cold_bytes / _MB:>10.2f}"
            )
    if inv.orphan_index_rows:
        lines.append(
            f"\n{inv.orphan_index_rows} index row(s) have no answer behind them — a claim rather "
            "than a measurement. `reindex` clears them."
        )
    if inv.archive_writers:
        lines.append(
            f"\n{inv.archive_writers} cycle(s) can still append, so this is a snapshot of a store "
            "that is moving. Nothing was written."
        )
    return "\n".join(lines)


async def cmd_compact_archive(args: argparse.Namespace) -> CommandResult:
    stores = open_stores(args)
    mode = str(args.mode)
    dataset: str | None = args.dataset
    apply = bool(args.apply)
    # A read, so off the command highway: a census filed as a `CommandRecord` bills a no-op.
    if mode == "inventory":
        inventory = inventory_measurement_archive(stores, dataset=dataset)
        return CommandResult(
            data=inventory.model_dump(mode="json"),
            human=_render_inventory(inventory, dataset=dataset),
        )
    payload = CompactArchivePayload.model_validate(
        {"mode": mode, "dataset": dataset, "apply": apply}
    )
    outcome = await CommandDispatcher(stores).dispatch_compact_archive(
        CommandCall(payload, uuid.uuid4().hex)
    )
    report = outcome.result
    return CommandResult(
        data=report.model_dump(mode="json"), human=_render(mode, report, dataset=dataset)
    )
