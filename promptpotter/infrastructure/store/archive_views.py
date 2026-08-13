"""MeasurementArchive facade — the sole WRITE gateway, tenant-global and never backend-scoped.
Nothing enforces that mechanically, and claiming a guard that does not exist is worse."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from typing import TYPE_CHECKING, Any

from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.infrastructure.store.io import append_jsonl, read_json_tolerant, write_jsonl
from promptpotter.infrastructure.store.layout import CycleLayout, cycle_dir_for
from promptpotter.infrastructure.store.read_model import iter_jsonl
from promptpotter.shared.errors import is_error_result
from promptpotter.shared.instrument import instrument_mode

if TYPE_CHECKING:
    from pathlib import Path

    from promptpotter.domain.sample import Measurement
    from promptpotter.infrastructure.store.stores import Stores

__all__ = [
    "campaign_measurement_series",
    "capture_evidence_epoch",
    "compact_measurement_run",
    "cycle_measurement_series",
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
    "sample_fold_rows",
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


def measurement_series_for_samples(
    stores: Stores,
    sample_ids: list[int],
    *,
    dataset_name: str,
) -> dict[int, list[dict[str, Any]]]:
    """Per-sample chronological series, one archive walk for the whole set. *dataset_name* is
    REQUIRED with no default: a ``sample_id`` only identifies a sample within one dataset."""
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
                    "fitness": float(item.get("fitness") or 0.0),
                    # Emitted HERE so all three scopes of the measurement-series endpoint hand
                    # back the same `{ord, fitness, label}` dot — never re-map this arm in the
                    # router, which gives one wire shape two authors.
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
    *,
    dataset_name: str,
) -> dict[int, dict[str, Any]]:
    """Per-sample cache reuse from prior runs sharing *node_configs*. *dataset_name* is required:
    a ``sample_id`` only identifies a sample within one dataset."""
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


# ---------------------------------------------------------------------------
# Per-sample chronological series at CYCLE + CAMPAIGN scope — the two round-file
# scopes of ``/datasets/{name}/measurement-series``, whose third (dataset scope) is
# ``measurement_series_for_samples`` above. All three answer one endpoint and all
# three are store walks, so they live together; the two below sat in
# ``application/intelligence/`` whose charter is "shared by scan and loop", and the
# API was their only reader. They walk ``rounds/round_*.json`` directly rather than the
# archive, so an IN-FLIGHT cycle lands before its measurements are banked.
# Errored items dropped, matching the Rasch / heatmap observation set.
# ---------------------------------------------------------------------------


def cycle_measurement_series(
    stores: Stores,
    hop: CycleHop,
    sample_ids: set[int],
) -> dict[int, list[dict[str, Any]]]:
    """Walk one cycle's ``rounds/round_*.json`` → per-sample series; ord is
    ``{round:04d}/{cand_idx:02d}``, and a candidate absent from the scoreboard sinks to slot 99."""
    cycle_dir = cycle_dir_for(stores.base_dir, hop)
    rounds_dir = CycleLayout(cycle_dir).rounds
    out: dict[int, list[dict[str, Any]]] = {sid: [] for sid in sample_ids}
    if not rounds_dir.is_dir():
        return out
    for round_path in sorted(rounds_dir.glob("round_*.json")):
        doc = read_json_tolerant(round_path)
        if not isinstance(doc, dict):
            continue
        round_no = doc.get("round")
        if not isinstance(round_no, int):
            continue
        idx_of: dict[str, int] = {}
        for i, c in enumerate(doc.get("scoreboard") or []):
            cid = c.get("candidate_id") if isinstance(c, dict) else None
            if isinstance(cid, str):
                idx_of[cid] = i
        acr = doc.get("all_candidate_results") or {}
        if not isinstance(acr, dict):
            continue
        for cand_id, results in acr.items():
            ci = idx_of.get(cand_id, 99)
            if not isinstance(results, list):
                continue
            for item in results:
                if not isinstance(item, dict):
                    continue
                sid = item.get("sample_id")
                fitness = item.get("fitness")
                # An unscored row carries no verdict — skipped, as before. The guard reads
                # ``fitness`` because that is the field a scored row is guaranteed to have.
                if not isinstance(sid, int) or not isinstance(fitness, int | float):
                    continue
                if sid not in sample_ids:
                    continue
                if is_error_result(item):
                    continue
                out[sid].append(
                    {
                        "ord": f"{round_no:04d}/{ci:02d}",
                        "fitness": float(fitness),
                        "label": f"R{round_no} cand {ci}",
                    }
                )
    for bucket in out.values():
        bucket.sort(key=lambda m: m["ord"])
    return out


def campaign_measurement_series(
    stores: Stores,
    campaign_id: str,
    sample_ids: set[int],
) -> dict[int, list[dict[str, Any]]]:
    """Pool every cycle's round-file series across one campaign; ord prefixed by cycle id."""
    out: dict[int, list[dict[str, Any]]] = {sid: [] for sid in sample_ids}
    for entry in stores.campaigns.enumerate_cycles():
        if entry["campaign_id"] != campaign_id:
            continue
        cid = entry["cycle_id"]
        per_cycle = cycle_measurement_series(
            stores, CycleHop(campaign_id=campaign_id, cycle_id=cid), sample_ids
        )
        for sid, dots in per_cycle.items():
            for d in dots:
                out[sid].append(
                    {
                        "ord": f"{cid}/{d['ord']}",
                        "fitness": d["fitness"],
                        "label": d["label"],
                    }
                )
    for bucket in out.values():
        bucket.sort(key=lambda m: m["ord"])
    return out
