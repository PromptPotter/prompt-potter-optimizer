"""MeasurementArchive facade — sole gateway to the cross-cycle archive.

The archive is the database core (per ``docs/architecture.md``): cross-cycle,
content-addressed measurements indexed by sample + node-config, and **tenant-global
— never backend-scoped**, so nothing here takes a ``backend_id``. Every archive
read/write lives behind this module; reaching ``stores.archive`` (or aliasing
it) outside this file is drift, enforced by
``test_no_direct_archive_access_outside_facade``.

Placement in ``infrastructure/store/`` (not ``application/scoring/``): the archive
IS a store, so its single-writer facade lives beside the leaf it wraps. The three
writes below (append / compact / reset) are the whole write surface — any new one
means a new function here, not a sidecar."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from typing import TYPE_CHECKING, Any

from promptpotter.shared.errors import is_error_result
from promptpotter.shared.instrument import instrument_mode

if TYPE_CHECKING:
    from pathlib import Path

    from promptpotter.domain.sample import Measurement
    from promptpotter.infrastructure.store.stores import Stores

__all__ = [
    "capture_evidence_epoch",
    "compact_measurement_run",
    "list_runs",
    "load_run",
    "measurement_series_for_samples",
    "measurements_for_config",
    "measurements_for_sample",
    "record_measurement_run",
    "reindex_measurements",
    "reset_measurement_run",
    "reusable_results",
    "runs_since",
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
    """Every run-id banked right now — the epoch an instrument-mode cycle hides.

    Reads the RAW index, deliberately NOT `list_runs`: that one is already filtered by the
    *caller's* epoch, so a nested instrument would capture only what its parent could see
    and would then treat its grandparent's runs as its own evidence. An epoch has to be
    absolute, not relative to whoever is asking."""
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


def measurement_series_for_samples(
    stores: Stores,
    sample_ids: list[int],
    *,
    dataset_name: str,
) -> dict[int, list[dict[str, Any]]]:
    """Per-sample chronological series, one archive walk for the whole set.

    ``{sample_id: [{ord, hit, run_id, created_at}, ...]}`` sorted ascending by
    ``ord`` = ``created_at``/``run_id``/item-index. Errored items dropped
    (matches ``build_archive_observations``'s Rasch-fit filter so the
    dashboard hit-rate column and δ_s estimate see the same observations).
    Powers the ``/datasets/{name}/measurement-series`` endpoint.

    *dataset_name* is REQUIRED, exactly as on :func:`reusable_results`: **a ``sample_id``
    only identifies a sample within one dataset.** It was `str | None = None`, and the one
    caller — the dataset-scope arm of that endpoint, the arm that most needs the scope —
    omitted it, so the walk crossed EVERY dataset's archive and spliced another dataset's
    sample-14 measurements into this dataset's sample-14 series. No default, so the next
    caller cannot forget it either; that optional-with-a-None-default WAS the bug."""
    wanted = set(sample_ids)
    out: dict[int, list[dict[str, Any]]] = {sid: [] for sid in wanted}
    for entry in stores.archive.list_all(dataset_name=dataset_name):
        run_id = entry["run_id"]
        detail = stores.archive.load_by_id(run_id)
        if detail is None:
            continue
        created_at = str(detail.get("created_at", ""))
        for idx, item in enumerate(detail.get("measurements", [])):
            sid = item.get("sample_id")
            if not isinstance(sid, int) or sid not in wanted:
                continue
            if is_error_result(item):
                continue
            out[sid].append(
                {
                    "ord": f"{created_at}/{run_id}/{idx:04d}",
                    "hit": bool(item.get("hit", False)),
                    # Emitted HERE so all three scopes of the measurement-series endpoint
                    # hand back the same `{ord, hit, label}` dot. The router used to
                    # re-map this arm alone to synthesize the label the round-file arms
                    # emit natively — one wire shape with two authors, and the odd one
                    # out lived in a presentation-layer dict comprehension.
                    "label": f"run {run_id[:8]}",
                    "run_id": run_id,
                    "created_at": created_at,
                }
            )
    for bucket in out.values():
        bucket.sort(key=lambda m: m["ord"])
    return out


def measurements_for_config(
    stores: Stores,
    predicate: dict[str, dict[str, Any]],
    *,
    run_ids: set[str] | list[str] | None = None,
    dataset_name: str | None = None,
) -> list[Measurement]:
    """Every measurement under configs matching *predicate*, across all samples."""
    return stores.archive.measurements_for_config(
        predicate,
        run_ids=run_ids,
        dataset_name=dataset_name,
    )


def load_run(stores: Stores, run_id: str) -> dict[str, Any] | None:
    """Load one run's detail file by ``run_id``; ``None`` if absent.
    Dataset-agnostic — run_id encodes its dataset via the index;
    callers needing the stamp read ``detail['dataset_name']`` themselves."""
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
    *,
    dataset_name: str,
) -> dict[int, dict[str, Any]]:
    """Per-sample cache reuse from prior runs sharing *node_configs*, keyed by ``sample_id``.
    *dataset_name* is required — it scopes the slice, and ``sample_id`` only identifies a sample
    within one dataset."""
    return stores.archive.load_reusable_results(
        node_configs,
        is_fatal=is_fatal,
        dataset_name=dataset_name,
    )


# -- writes -------------------------------------------------------------------


def record_measurement_run(
    stores: Stores,
    run_id: str,
    data: dict[str, Any],
    new_measurements: Iterable[dict[str, Any]],
) -> Path:
    """Sole write entry point — append the rows the caller has not persisted yet, a fresh
    header, and the index upsert. *new_measurements* is what is NEW: the detail log is
    append-only, so the rows already on disk are never rewritten."""
    return stores.archive.append_run(run_id, data, new_measurements)


def compact_measurement_run(stores: Stores, run_id: str) -> bool:
    """Drop the run's superseded rows (dead headers, re-measured samples). Self-limiting —
    a no-op on a log that is already tight."""
    return stores.archive.compact_run(run_id)


def reset_measurement_run(stores: Stores, run_id: str) -> None:
    """Discard the run's detail log — a ``force_fresh`` pass REPLACES its rows, and an
    append-only log does not overwrite. See :meth:`MeasurementArchive.reset_run`."""
    stores.archive.reset_run(run_id)


def reindex_measurements(stores: Stores) -> dict[str, int]:
    """Rebuild the append-only measurement index from the detail files and GC orphans.
    A maintenance verb — the index is derived, so this loses nothing; returns counts."""
    return stores.archive.reindex()
