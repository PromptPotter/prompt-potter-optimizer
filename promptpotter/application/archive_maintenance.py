"""Maintenance passes over the CONTENT-ADDRESSED measurement archive, as use cases rather than as
a CLI script.

Every function here takes ``Stores``, RETURNS its counts and prints nothing. That is the whole
reason the module exists: the archive is reachable from five entry points, and a pass that resolves
its own roots from a process global or reports through stdout can serve exactly one of them.

**Compaction is a deletion with a receipt.** A measurement row is paid LLM spend, so nothing here
offers a way to drop a field — only to MOVE one. The payload lands gzipped in
``measurements/cold/{run_id}.jsonl.gz`` and :func:`restore_measurement_archive` puts it back ROW for
row. Not byte for byte, and the distinction is worth stating rather than discovering: a touched line
is re-serialized with compact separators and a restored key lands at the END of its object, so the
file comes back a few percent smaller with its keys in a different order. Every reader parses, so
nothing depends on either — but a round-trip check that compares bytes will fail on a run that lost
nothing. :func:`purge_cold_store` is the one irreversible act, and it is never bundled with the
compaction that produced the payload.
"""

from __future__ import annotations

import json
import pathlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import Field, computed_field

from promptpotter.domain.phases import RunPhase
from promptpotter.domain.scoring import (
    ABANDONED_ROW_KEYS,
    UNREAD_PIPELINE_KEYS,
    UNREAD_ROW_KEYS,
)
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.runtime_flags import derive_run_phase
from promptpotter.infrastructure.store.archive_views import (
    cold_payload_bytes,
    cold_payload_size,
    drop_cold_payload,
    has_cold_payload,
    load_run,
    maintenance_runs,
    measurement_detail_lines,
    read_cold_payload,
    reindex_measurements,
    replace_measurement_detail,
    write_cold_payload,
)
from promptpotter.infrastructure.store.io import read_json_optional
from promptpotter.infrastructure.store.layout import CycleLayout, inner_sandboxes_dir
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.errors import graceful

if TYPE_CHECKING:
    from promptpotter.infrastructure.store.stores import Stores

__all__ = [
    "ArchiveReport",
    "archive_writers",
    "compact_measurement_archive",
    "iter_cycle_ledgers",
    "purge_cold_store",
    "reindex_measurement_archive",
    "restore_measurement_archive",
    "workspace_trees",
]


# -- the live-writer guard ----------------------------------------------------
#
# One live producer ANYWHERE blocks every pass below: the measurement archive is shared across
# every cycle (`build_stores`'s `shared_root`) and appended to per sample, so a rewrite of any run
# file can lose a row another cycle is landing. CHECKIN is pre-loop and writes no measurement.

_ARCHIVE_WRITER_PHASES: frozenset[RunPhase] = frozenset({RunPhase.RUNNING, RunPhase.GATE})


def workspace_trees(root: pathlib.Path) -> list[pathlib.Path]:
    """Inner sandboxes are a SIBLING tree, not a subtree, so a ``*``-per-level glob silently
    misses every one — it reports a plausible smaller number rather than raising.

    *root* is a parameter rather than :data:`DEFAULT_PROJECTS_ROOT` read from inside: a pass that
    resolves its own root can only ever serve the process it was written for, and cannot be pointed
    at a test tree or at one tenant's ``shared_root``."""
    if not root.is_dir():
        return []
    trees = [root]
    inner = inner_sandboxes_dir(root)
    if inner.is_dir():
        trees.extend(p for p in inner.iterdir() if p.is_dir())
    return trees


def iter_cycle_ledgers(root: pathlib.Path) -> list[pathlib.Path]:
    """Every cycle ledger under *root*, inner sandboxes included."""
    return [
        p for tree in workspace_trees(root) for p in sorted(tree.glob("**/.runtime/ledger.jsonl"))
    ]


def archive_writers(root: pathlib.Path) -> int:
    """Cycles under *root* that could append to the archive while a pass runs.

    Callers pass ``stores.shared_root`` — the tree the archive is actually rooted in, invariant
    across sandbox depth — so the count is over the cycles that genuinely share this archive."""
    n = 0
    for ledger_path in iter_cycle_ledgers(root):
        cycle_dir = ledger_path.parent.parent
        manifest = read_json_optional(CycleLayout(cycle_dir).manifest)
        finished = bool(manifest.get("finished_at")) if isinstance(manifest, dict) else False
        if derive_run_phase(cycle_dir, is_terminal=finished) in _ARCHIVE_WRITER_PHASES:
            n += 1
    return n


# -- what moves, and what may never ------------------------------------------

# WHICH keys are unread is `domain/scoring.py`'s to say — it declares the two types the answer is
# about, and asserts each set against them at import. This module owns only the ACT of moving them.
_MOVABLE_ROW_FIELDS: frozenset[str] = UNREAD_ROW_KEYS | ABANDONED_ROW_KEYS

_ELIGIBLE_LABEL_PREFIX = "candidate_"
"""Only a candidate run compacts.

Measured over this workspace: ``round_parent`` replays 84.5% of its cells from the archive and
``origin`` 78.6%, against 4.5% for a candidate — so 82% of all cache value sits in the two labels
this prefix excludes, for a third of the bytes. A run whose label matches nothing is SKIPPED and
counted by label, never compacted on a guess."""


def _protected_pipeline_fields(row: Mapping[str, Any]) -> frozenset[str]:
    """Per-ROW protection, derived from the row itself rather than from a list of dataset names.

    An L4 row carrying ``mean_round_delta`` must keep its ``reasoning_trace``: the
    ``_inner_narrated`` gate in ``optimization/dispatch/injections/panels.py`` requires BOTH, and a
    row holding one without the other silently drops out of ``inner_narratives`` and flips
    ``_miss_is_placeholder``, re-enabling ``sample_transcripts`` on the recursion — where every row
    reads as a miss by construction. Not a crash; a wrong panel."""
    pd = row.get("pipeline_data")
    if isinstance(pd, dict) and "mean_round_delta" in pd:
        return frozenset({"reasoning_trace"})
    return frozenset()


# -- the report ---------------------------------------------------------------


class ArchiveReport(StrictModel):
    """What a pass did, or would do.

    A model rather than a dataclass because it IS the wire shape: the API answers it directly and
    the browser's type is generated from it, so a preview and an apply cannot be described in two
    different vocabularies. ``applied`` is the only field that separates the two."""

    runs_touched: int = 0
    runs_skipped: int = 0
    rows_moved: int = 0
    bytes_before: int = 0
    bytes_after: int = 0
    cold_bytes: int = 0
    archive_writers: int = 0
    conflicts: int = 0
    purged: int = 0
    """Runs whose moved payload is gone for good.

    A COUNTED no-op, never folded into ``runs_skipped``: "this configuration was measured and we
    dropped its payload deliberately" and "this run was never compacted" are different facts, and
    collapsing them is how a purge starts reading as if it never happened."""
    skipped_by_label: Mapping[str, int] = Field(default_factory=dict)
    applied: bool = False

    @computed_field  # type: ignore[prop-decorator]
    @property
    def bytes_freed(self) -> int:
        """Net, and net is the honest number: the payload does not vanish, it moves to the cold
        store, so the hot-side saving is reported against what the cold side cost."""
        return self.bytes_before - self.bytes_after - self.cold_bytes


def _blocked(writers: int) -> ArchiveReport:
    return ArchiveReport(archive_writers=writers)


# -- compaction ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _RunPlan:
    lines: list[str]
    cold: list[dict[str, Any]]
    before: int
    after: int


def _plan_compaction(lines: Iterable[str], *, stamped_at: str) -> _RunPlan | None:
    """Pure. ``None`` when the run carries nothing movable, so an already-tight run is not rewritten.

    Every line is kept, in order, including ones that do not parse: the file folds last-wins on
    ``k``, so dropping or reordering one changes what the archive reads back."""
    out: list[str] = []
    cold: list[dict[str, Any]] = []
    before = after = 0
    header_idx: int | None = None

    for idx, line in enumerate(lines):
        before += len(line.encode("utf-8"))
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            out.append(line)
            after += len(line.encode("utf-8"))
            continue
        if not isinstance(row, dict):
            out.append(line)
            after += len(line.encode("utf-8"))
            continue

        key = row.get("k")
        if key == "run":
            header_idx = len(out)
            out.append(line)
            after += len(line.encode("utf-8"))
            continue

        protected = _protected_pipeline_fields(row)
        moved_row = {f: row.pop(f) for f in sorted(_MOVABLE_ROW_FIELDS) if f in row}
        moved_pd: dict[str, Any] = {}
        pd = row.get("pipeline_data")
        if isinstance(pd, dict):
            for f in sorted(UNREAD_PIPELINE_KEYS - protected):
                if f in pd:
                    moved_pd[f] = pd.pop(f)

        if not moved_row and not moved_pd:
            out.append(line)
            after += len(line.encode("utf-8"))
            continue

        entry: dict[str, Any] = {"i": idx, "k": key}
        if moved_row:
            entry["row"] = moved_row
        if moved_pd:
            entry["pd"] = moved_pd
        cold.append(entry)
        new_line = json.dumps(row, separators=(",", ":"), default=str) + "\n"
        out.append(new_line)
        after += len(new_line.encode("utf-8"))

    if not cold:
        return None

    # The stamp is what makes the compacted state LEGIBLE. Without it a row that lost its trace is
    # indistinguishable from one that never had a trace, and every consumer would have to guess.
    if header_idx is not None:
        header = json.loads(out[header_idx])
        header["compaction"] = {
            "at": stamped_at,
            "rows": len(cold),
            "moved_row": sorted(_MOVABLE_ROW_FIELDS),
            "moved_pipeline_data": sorted(UNREAD_PIPELINE_KEYS),
        }
        stamped = json.dumps(header, separators=(",", ":"), default=str) + "\n"
        after += len(stamped.encode("utf-8")) - len(out[header_idx].encode("utf-8"))
        out[header_idx] = stamped

    return _RunPlan(lines=out, cold=cold, before=before, after=after)


def compact_measurement_archive(
    stores: Stores,
    *,
    dataset: str | None = None,
    apply: bool = False,
) -> ArchiveReport:
    """Move the unread fields of candidate runs into the gzip cold store.

    *dataset* scopes the pass to one dataset — ``None`` is every dataset, which is deliberately not
    the default at any entry point. Refuses outright while any cycle can still append."""
    writers = archive_writers(stores.shared_root)
    if writers:
        return _blocked(writers)

    stamped_at = utcnow_iso()
    touched = skipped = rows = before = after = cold_bytes = 0
    by_label: dict[str, int] = {}

    for entry in maintenance_runs(stores, dataset_name=dataset):
        run_id = str(entry.get("run_id") or "")
        label = str(entry.get("name") or "")
        if not run_id:
            continue
        if not label.startswith(_ELIGIBLE_LABEL_PREFIX):
            skipped += 1
            by_label[label or "<unlabelled>"] = by_label.get(label or "<unlabelled>", 0) + 1
            continue
        if has_cold_payload(stores, run_id):
            # A second generation under one name would make the cold payload un-restorable.
            skipped += 1
            by_label["<already compacted>"] = by_label.get("<already compacted>", 0) + 1
            continue

        with graceful(f"compact {run_id}"):
            plan = _plan_compaction(measurement_detail_lines(stores, run_id), stamped_at=stamped_at)
            if plan is None:
                continue
            before += plan.before
            after += plan.after
            rows += len(plan.cold)
            touched += 1
            if apply:
                # Cold first: a crash between the two leaves a recoverable payload beside an
                # untouched detail log, which reads as a no-op. The reverse order loses the rows.
                cold_bytes += write_cold_payload(stores, run_id, plan.cold)
                replace_measurement_detail(stores, run_id, plan.lines)
            else:
                cold_bytes += cold_payload_size(stores, plan.cold)

    return ArchiveReport(
        runs_touched=touched,
        runs_skipped=skipped,
        rows_moved=rows,
        bytes_before=before,
        bytes_after=after,
        cold_bytes=cold_bytes,
        skipped_by_label=by_label,
        applied=apply,
    )


# -- restore ------------------------------------------------------------------


def _plan_restore(lines: list[str], cold: list[dict[str, Any]]) -> tuple[list[str], int] | None:
    """``(lines, restored)`` or ``None`` when the cold payload no longer lines up with the log.

    The payload is keyed by LINE INDEX because a detail log may hold several rows under one ``k``
    (it folds last-wins), so a key alone would restore the same value onto all of them. The ``k``
    travels beside the index purely to verify the alignment still holds — if a row-level
    ``compact_run`` shifted the lines since, the whole run is refused rather than half-restored."""
    out = list(lines)
    restored = 0
    for entry in cold:
        idx = entry.get("i")
        if not isinstance(idx, int) or not (0 <= idx < len(out)):
            return None
        try:
            row = json.loads(out[idx])
        except json.JSONDecodeError:
            return None
        if not isinstance(row, dict) or row.get("k") != entry.get("k"):
            return None
        moved_row = entry.get("row")
        if isinstance(moved_row, dict):
            row.update(moved_row)
        moved_pd = entry.get("pd")
        if isinstance(moved_pd, dict):
            pd = row.get("pipeline_data")
            if not isinstance(pd, dict):
                pd = {}
                row["pipeline_data"] = pd
            pd.update(moved_pd)
        out[idx] = json.dumps(row, separators=(",", ":"), default=str) + "\n"
        restored += 1
    return out, restored


def _unstamp_header(lines: list[str]) -> None:
    """Drop the compaction stamp in place — it describes a state about to stop being true.

    EVERY header row, not the first: the scoring walk re-saves per sample, so a detail log holds
    one header per save and the fold reads the LAST. Stopping at the first left the stamp standing
    on the row that actually wins, and a restored run still read as compacted."""
    for i, line in enumerate(lines):
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict) or row.get("k") != "run":
            continue
        if row.pop("compaction", None) is not None:
            lines[i] = json.dumps(row, separators=(",", ":"), default=str) + "\n"


def _stamp_purged(lines: list[str], *, at: str) -> int:
    """Record on the run itself that its moved payload was dropped, and return the rows it held.

    The compaction stamp says the fields LEFT; without this the operator cannot tell a run whose
    payload is still sitting in the cold store from one whose payload is gone for good — the two
    read identically, and the second is the one that cost money. Every header row, for the same
    reason :func:`_unstamp_header` clears every one."""
    rows = 0
    for i, line in enumerate(lines):
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict) or row.get("k") != "run":
            continue
        stamp = row.get("compaction")
        if isinstance(stamp, dict):
            rows = max(rows, int(stamp.get("rows") or 0))
        # A SEPARATE key, not a flag inside `compaction`: a later re-compaction of newly appended
        # rows rewrites that block, and this fact has to outlive it.
        row["purged"] = {
            "at": at,
            "rows": rows,
            "why": "storage — the payload was dropped deliberately, not lost",
        }
        lines[i] = json.dumps(row, separators=(",", ":"), default=str) + "\n"
    return rows


def _is_purged(detail: Mapping[str, Any] | None) -> bool:
    return isinstance(detail, dict) and isinstance(detail.get("purged"), dict)


def restore_measurement_archive(
    stores: Stores,
    *,
    dataset: str | None = None,
    apply: bool = False,
) -> ArchiveReport:
    """Put every compacted field back and drop the cold payload it came from.

    This is what makes compaction non-destructive, so it is held to the stricter bar: a run whose
    payload no longer aligns with its log is refused whole and counted, never partially restored,
    and one whose payload was PURGED is reported as such rather than skipped in silence."""
    writers = archive_writers(stores.shared_root)
    if writers:
        return _blocked(writers)

    touched = skipped = rows = before = after = conflicts = purged = 0

    for entry in maintenance_runs(stores, dataset_name=dataset):
        run_id = str(entry.get("run_id") or "")
        if not run_id:
            continue
        cold = read_cold_payload(stores, run_id)
        if cold is None:
            # Only a candidate run can carry a purge stamp, so only those are worth opening.
            if str(entry.get("name") or "").startswith(_ELIGIBLE_LABEL_PREFIX) and _is_purged(
                load_run(stores, run_id)
            ):
                purged += 1
            else:
                skipped += 1
            continue

        with graceful(f"restore {run_id}"):
            lines = measurement_detail_lines(stores, run_id)
            before += sum(len(x.encode("utf-8")) for x in lines)
            planned = _plan_restore(lines, cold)
            if planned is None:
                conflicts += 1
                continue
            new_lines, restored = planned
            _unstamp_header(new_lines)
            after += sum(len(x.encode("utf-8")) for x in new_lines)
            rows += restored
            touched += 1
            if apply:
                replace_measurement_detail(stores, run_id, new_lines)
                drop_cold_payload(stores, run_id)

    return ArchiveReport(
        runs_touched=touched,
        runs_skipped=skipped,
        rows_moved=rows,
        bytes_before=before,
        bytes_after=after,
        conflicts=conflicts,
        purged=purged,
        applied=apply,
    )


# -- purge: the one irreversible act -----------------------------------------


def purge_cold_store(
    stores: Stores,
    *,
    dataset: str | None = None,
    apply: bool = False,
) -> ArchiveReport:
    """Delete compacted payloads for good, and RECORD on each run that they are gone.

    Separate from :func:`compact_measurement_archive` on purpose, and never called by it: until this
    runs, every compaction is undoable, and after it the rows cost money and hours to reproduce.

    What survives is the point. Only the moved payload is deleted — the hot rows keep every field
    the δ ruler re-grades from and every field the replay cache needs, so a purged run still fits a
    ruler and still serves a cache hit. What is gone is the reasoning traces and the fields nothing
    reads, and the run says so on its own header rather than going quiet."""
    writers = archive_writers(stores.shared_root)
    if writers:
        return _blocked(writers)

    stamped_at = utcnow_iso()
    touched = freed = rows = 0
    for entry in maintenance_runs(stores, dataset_name=dataset):
        run_id = str(entry.get("run_id") or "")
        if not run_id or not has_cold_payload(stores, run_id):
            continue
        touched += 1
        freed += cold_payload_bytes(stores, run_id)
        with graceful(f"purge {run_id}"):
            lines = measurement_detail_lines(stores, run_id)
            rows += _stamp_purged(lines, at=stamped_at)
            if apply:
                # Stamp BEFORE dropping: a crash between the two leaves a run that claims its
                # payload is gone while it is still there, which `restore` corrects on its own.
                # The reverse order leaves a purged run indistinguishable from a never-compacted one.
                replace_measurement_detail(stores, run_id, lines)
                drop_cold_payload(stores, run_id)

    # The cold store shrinks from `freed` to nothing — reported as the before/after it is, so
    # `bytes_freed` reads positive without a negative cold figure standing in for it.
    return ArchiveReport(
        runs_touched=touched, bytes_before=freed, rows_moved=rows, purged=touched, applied=apply
    )


# -- reindex ------------------------------------------------------------------


def reindex_measurement_archive(stores: Stores) -> dict[str, int]:
    """Rebuild the measurement index from the detail files and GC orphans.

    Lives here rather than in the CLI shell that used to call the store directly: an adapter that
    reaches past this layer is one no other adapter can follow."""
    return reindex_measurements(stores)
