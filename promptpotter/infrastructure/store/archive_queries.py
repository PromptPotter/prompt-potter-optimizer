"""MeasurementArchive facade — the sole WRITE gateway, tenant-global and never backend-scoped.
Nothing enforces that mechanically, and claiming a guard that does not exist is worse."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from typing import TYPE_CHECKING, Any

from promptpotter.infrastructure.store.io import append_jsonl, write_jsonl
from promptpotter.infrastructure.store.read_model import iter_jsonl
from promptpotter.shared.instrument import instrument_mode

if TYPE_CHECKING:
    from pathlib import Path

    from promptpotter.domain.sample import Measurement
    from promptpotter.infrastructure.store.measurement_archive import ReplayableRow
    from promptpotter.infrastructure.store.stores import Stores

__all__ = [
    "capture_evidence_epoch",
    "cold_payload_bytes",
    "cold_payload_size",
    "compact_measurement_run",
    "drop_cold_payload",
    "has_cold_payload",
    "list_runs",
    "load_run",
    "maintenance_runs",
    "measurement_detail_lines",
    "measurements_for_config",
    "measurements_for_sample",
    "read_cold_payload",
    "record_measurement_run",
    "reindex_measurements",
    "replace_measurement_detail",
    "reset_measurement_run",
    "reusable_results",
    "run_signatures",
    "runs_since",
    "sample_fold_rows",
    "write_cold_payload",
    "write_sample_fold",
]


# -- CACHE vs MEMORY ----------------------------------------------------------
#
# The archive plays two roles that were never named apart, and conflating them is
# what made an L4 inner cycle unreproducible:
#
#   CACHE  — content-addressed replay of raw grades (`reusable_results`). Keyed by
#            content hash, so a hit IS the same measurement. Must stay tenant-global:
#            it is what lets an inner origin replay instead of being re-paid and
#            re-drawn. NEVER filtered.
#   MEMORY — cross-run evidence: the δ ruler (`build_archive_observations`) and the
#            `AxisIndex` panels (`axis_memory` / `archive_top_runs` / `rare_hit_samples`).
#            Read through `list_runs` / `runs_since`, and invisible behind the caller's
#            evidence epoch (`shared/instrument.py`, which is where the WHY lives).
#
# For a normal campaign no mode is bound, the epoch is empty, and MEMORY over the tenant's
# whole archive is the feature.


def _evidence_epoch() -> frozenset[str]:
    """Runs this task must not see as evidence — empty for a normal campaign."""
    mode = instrument_mode()
    return mode.evidence_epoch if mode is not None else frozenset()


def capture_evidence_epoch(stores: Stores) -> frozenset[str]:
    """Every run-id banked right now — the epoch an instrument-mode cycle hides. Reads the RAW
    index: ``list_runs`` is already epoch-filtered, and an epoch must be absolute."""
    return frozenset(e["run_id"] for e in stores.archive.list_all())


# -- reads --------------------------------------------------------------------


def measurements_for_sample(
    stores: Stores,
    sample_id: int,
    *,
    run_ids: list[str] | None = None,
    dataset_name: str | None = None,
) -> list[Measurement]:
    """Every measurement of one sample, across configs; *dataset_name* scopes the slice."""
    return stores.archive.measurements_for_sample(
        sample_id,
        run_ids=run_ids,
        dataset_name=dataset_name,
    )


def measurements_for_config(
    stores: Stores,
    predicate: dict[str, dict[str, Any]],
    *,
    run_ids: set[str] | list[str] | None = None,
    dataset_name: str | None = None,
) -> list[Measurement]:
    return stores.archive.measurements_for_config(
        predicate,
        run_ids=run_ids,
        dataset_name=dataset_name,
    )


def load_run(stores: Stores, run_id: str) -> dict[str, Any] | None:
    """Load one run's detail file by ``run_id``; ``None`` if absent. Dataset-agnostic — a caller
    needing the stamp reads ``detail['dataset_name']`` itself."""
    return stores.archive.load_by_id(run_id)


def run_signatures(stores: Stores) -> dict[str, tuple[int, int]]:
    """Change-tokens for every run detail, one scan — see `MeasurementArchive.detail_signatures`."""
    return stores.archive.detail_signatures()


def list_runs(
    stores: Stores,
    *,
    dataset_name: str | None = None,
) -> list[dict[str, Any]]:
    """Run-summary entries from the archive index, scoped to ``dataset_name`` — an
    EVIDENCE read, so runs behind the evidence epoch are invisible."""
    epoch = _evidence_epoch()
    entries = stores.archive.list_all(dataset_name=dataset_name)
    if not epoch:
        return entries
    return [e for e in entries if e.get("run_id") not in epoch]


def runs_since(
    stores: Stores,
    seen_ids: set[str],
    *,
    dataset_name: str | None = None,
) -> Iterator[tuple[str, dict[str, Any]]]:
    """Yield ``(run_id, detail)`` for runs not in *seen_ids*; missing details skipped.
    An EVIDENCE read — runs behind the evidence epoch are invisible."""
    return stores.archive.load_since(
        seen_ids | _evidence_epoch(),
        dataset_name=dataset_name,
    )


def reusable_results(
    stores: Stores,
    node_configs: list[tuple[str, dict[str, Any]]],
    is_fatal: Callable[[dict[str, Any]], bool] | None = None,
) -> dict[str, ReplayableRow]:
    """Per-sample cache reuse from prior runs sharing *node_configs*, keyed by ``sample_key``.

    The grade floor is the facade's, not the caller's: this is the seam ADR-0005's "every consumer
    excludes ``C``" is enforced at, and a replayed row is re-archived under the reading run, so a
    caller free to lower it could launder a ``C`` cell into the δ ruler."""
    return stores.archive.load_reusable_results(node_configs, is_fatal=is_fatal)


# -- writes -------------------------------------------------------------------


def record_measurement_run(
    stores: Stores,
    run_id: str,
    data: dict[str, Any],
    new_measurements: Iterable[dict[str, Any]],
) -> Path:
    """Sole write entry point. *new_measurements* is what is NEW — the detail log is append-only,
    so rows already on disk are never rewritten."""
    return stores.archive.append_run(run_id, data, new_measurements)


def compact_measurement_run(stores: Stores, run_id: str) -> bool:
    """Drop the run's superseded rows (dead headers, re-measured samples). Self-limiting —
    a no-op on a log that is already tight."""
    return stores.archive.compact_run(run_id)


def reset_measurement_run(stores: Stores, run_id: str) -> None:
    """Discard the run's detail log — a ``force_fresh`` pass REPLACES its rows, and an
    append-only log does not overwrite. See :meth:`MeasurementArchive.reset_run`."""
    stores.archive.reset_run(run_id)


# -- field compaction ---------------------------------------------------------
#
# The facade half of the archive's cold store. These are deliberately thin: WHICH fields move is
# a policy the application layer owns (`application/maintenance/archive_maintenance.py`), and the
# archive owns the paths, the fold-key ordering invariant and the atomic swap.


def maintenance_runs(stores: Stores, *, dataset_name: str | None = None) -> list[dict[str, Any]]:
    """Every index entry, RAW — never epoch-filtered. ``list_runs`` is an EVIDENCE read and hides
    an instrument's own runs; a maintenance pass that cannot see a run cannot maintain it."""
    return stores.archive.list_all(dataset_name=dataset_name)


def measurement_detail_lines(stores: Stores, run_id: str) -> list[str]:
    return stores.archive.detail_lines(run_id)


def replace_measurement_detail(stores: Stores, run_id: str, lines: Iterable[str]) -> int:
    return stores.archive.replace_detail(run_id, lines)


def write_cold_payload(stores: Stores, run_id: str, rows: Iterable[dict[str, Any]]) -> int:
    return stores.archive.write_cold(run_id, rows)


def cold_payload_size(stores: Stores, rows: Iterable[dict[str, Any]]) -> int:
    """What writing *rows* would cost. One compression path serves the dry run and the apply."""
    return stores.archive.cold_size(rows)


def cold_payload_bytes(stores: Stores, run_id: str) -> int:
    return stores.archive.cold_bytes_on_disk(run_id)


def read_cold_payload(stores: Stores, run_id: str) -> list[dict[str, Any]] | None:
    return stores.archive.read_cold(run_id)


def drop_cold_payload(stores: Stores, run_id: str) -> int:
    return stores.archive.drop_cold(run_id)


def has_cold_payload(stores: Stores, run_id: str) -> bool:
    return stores.archive.has_cold(run_id)


def maintain_measurement_index(stores: Stores) -> bool:
    """Compact the index's superseded rows at run start. **Silently skipped inside an instrument**,
    wrapped around the disk touch so a second caller inherits the rule instead of restating it."""
    if instrument_mode() is not None:
        return False
    return stores.archive.maintain_index()


def reindex_measurements(stores: Stores) -> dict[str, int]:
    """Rebuild the append-only measurement index from the detail files and GC orphans.
    A maintenance verb — the index is derived, so this loses nothing; returns counts."""
    return stores.archive.reindex()


def _sample_fold_path(stores: Stores, dataset_name: str) -> Path:
    return stores.archive.derived_dir() / f"sample_fold__{dataset_name}.jsonl"


def sample_fold_rows(stores: Stores, *, dataset_name: str) -> list[dict[str, Any]]:
    """The persisted ``SampleIndex`` derivation, **in append order**: one consumer reads a sample's
    trailing observations as a streak, so the replay sequence is part of the answer."""
    return iter_jsonl(_sample_fold_path(stores, dataset_name))


def write_sample_fold(
    stores: Stores, *, dataset_name: str, rows: Iterable[dict[str, Any]], append: bool
) -> None:
    """Persist per-run derivation *rows* — ``append`` for runs newly folded, else replace. Skipped
    inside an instrument, which shares this archive with the campaign that spawned it."""
    if instrument_mode() is not None:
        return
    path = _sample_fold_path(stores, dataset_name)
    if append:
        for row in rows:
            append_jsonl(path, row)
        return
    write_jsonl(path, rows)
