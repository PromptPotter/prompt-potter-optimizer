"""The maintenance verbs' reports: reset, landing and the measurement archive's compaction."""

from __future__ import annotations

from collections import Counter

from promptpotter.application.maintenance.archive_maintenance import ArchiveInventory, ArchiveReport
from promptpotter.application.maintenance.landing import LandedStamp, Landing
from promptpotter.application.maintenance.reset import ResetPlan
from promptpotter.domain.command_kinds import ARCHIVE_MODE_INFO, ArchiveMode

_MB = 1024 * 1024


def render_reset(plan: ResetPlan) -> str:
    lines = [f"reset under: {', '.join(str(root) for root in plan.roots)}"]
    counts: Counter[str] = Counter()
    sizes: Counter[str] = Counter()
    for drop in plan.drops:
        if drop.kind is not None:
            counts[drop.kind.name] += 1
            sizes[drop.kind.name] += drop.size
    lines += [
        f"  - {counts[name]:,} {name} file(s), {sizes[name] / _MB:.1f} MiB"
        for name in sorted(counts)
    ]
    lines += [f"  ? unknown {drop.path}" for drop in plan.drops if drop.kind is None]
    lines.append(f"  kept: {plan.kept:,} survivor, cache and machine file(s)")
    if plan.refusal is not None:
        lines.append(f"  refused: {plan.refusal}")
    return "\n".join(lines)


def render_landing(landing: Landing, *, written: bool, last: LandedStamp | None) -> str:
    lines = [] if last is None else [f"last landed {last.at}", *(f"  {s}" for s in last.said)]
    # A reset's line is among them: `reset --dry-run` breaks it down by kind.
    lines += landing.said
    lines += [f"unknown {path}" for path in landing.unknown]
    lines += [f"unloadable {one.said}" for one in landing.unloadable]
    if landing.tally:
        lines.append(f"in all: {landing.tally}")
    if landing.refusal is None:
        lines.append(f"{'landed' if written else 'would land'}: proven {landing.proven:,} file(s)")
    else:
        lines.append(f"refused, nothing written: {landing.refusal}")
    return "\n".join(lines)


def render_compaction(mode: ArchiveMode, report: ArchiveReport, *, dataset: str | None) -> str:
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
        f"  {'cost ' if report.bytes_freed < 0 else 'freed'} {abs(report.bytes_freed) / _MB:8.2f} MB",
    ]
    lines += [f"  {note}" for note in report.notes]
    for role, n in sorted(report.rows_skipped_by_role.items()):
        lines.append(f"  skipped {n:5d} x {role}")
    if not report.applied and report.files_touched:
        lines.append("\nDry run. Re-run with --apply to write.")
    if ARCHIVE_MODE_INFO[mode].irreversible and not report.applied and report.files_touched:
        lines.append("This one is IRREVERSIBLE — the rows cost real money to measure again.")
    return "\n".join(lines)


def render_inventory(inv: ArchiveInventory, *, dataset: str | None) -> str:
    scope = dataset or "every dataset"
    megabytes = (inv.total.hot_bytes + inv.total.cold_bytes) / _MB
    lines = [
        f"compact-archive[inventory] over {scope}: {inv.total.configurations} configuration(s), "
        f"{inv.total.answers} answer(s), {megabytes:.2f} MB.",
    ]
    for axis, rows in (("dataset", inv.by_dataset), ("role", inv.by_role), ("age", inv.by_age)):
        lines.append("")
        lines.append(f"  {axis:<24}{'configs':>8}{'answers':>9}{'hot MB':>10}{'cold MB':>10}")
        lines += [
            f"  {row.key[:23]:<24}{row.configurations:>8}{row.answers:>9}"
            f"{row.hot_bytes / _MB:>10.2f}{row.cold_bytes / _MB:>10.2f}"
            for row in rows
        ]
    if inv.orphan_index_rows:
        lines.append(
            f"\n{inv.orphan_index_rows} index row(s) have no answer behind them — a claim rather "
            "than a measurement. `reindex --apply` clears them."
        )
    if inv.archive_writers:
        lines.append(
            f"\n{inv.archive_writers} cycle(s) can still append, so this is a snapshot of a store "
            "that is moving. Nothing was written."
        )
    return "\n".join(lines)
