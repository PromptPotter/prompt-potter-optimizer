"""MeasurementArchive facade — sole gateway to the cross-cycle archive.

The archive is the database core (per ``docs/architecture.md``): cross-cycle,
content-addressed measurements indexed by sample + node-config, and **tenant-global
— never backend-scoped**, so nothing here takes a ``backend_id``. Every archive
read/write lives behind this module; reaching ``stores.archive`` (or aliasing
it) outside this file is drift, enforced by
``test_no_direct_archive_access_outside_facade``.

Placement in ``infrastructure/store/`` (not ``application/scoring/``) because
``tracing/replay.py`` is a consumer and ``infrastructure → application`` is
forbidden. ``record_measurement_run`` is the sole write — any new write means
a new function here, not a sidecar."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING, Any

from promptpotter.shared.errors import is_error_result

if TYPE_CHECKING:
    from pathlib import Path

    from promptpotter.domain.sample import Measurement
    from promptpotter.infrastructure.store.stores import Stores

__all__ = [
    "list_runs",
    "load_run",
    "measurement_series_for_samples",
    "measurements_for_config",
    "measurements_for_sample",
    "record_measurement_run",
    "register_prompt_alias",
    "reindex_measurements",
    "resolve_aliases",
    "reusable_results",
    "runs_since",
]


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
    dataset_name: str | None = None,
) -> dict[int, list[dict[str, Any]]]:
    """Per-sample chronological series, one archive walk for the whole set.

    ``{sample_id: [{ord, hit, run_id, created_at}, ...]}`` sorted ascending by
    ``ord`` = ``created_at``/``run_id``/item-index. Errored items dropped
    (matches ``build_archive_observations``'s Rasch-fit filter so the
    dashboard hit-rate column and δ_s estimate see the same observations).
    Powers the ``/datasets/{name}/measurement-series`` endpoint."""
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


def list_runs(
    stores: Stores,
    *,
    dataset_name: str | None = None,
) -> list[dict[str, Any]]:
    """All run-summary entries from the archive index, scoped to ``dataset_name``."""
    return stores.archive.list_all(dataset_name=dataset_name)


def runs_since(
    stores: Stores,
    seen_ids: set[str],
    *,
    dataset_name: str | None = None,
) -> Iterator[tuple[str, dict[str, Any]]]:
    """Yield ``(run_id, detail)`` for runs not in *seen_ids*; missing details skipped."""
    return stores.archive.load_since(seen_ids, dataset_name=dataset_name)


def reusable_results(
    stores: Stores,
    node_configs: list[tuple[str, dict[str, Any]]],
    is_fatal: Callable[[dict[str, Any]], bool] | None = None,
    *,
    dataset_name: str | None = None,
    min_grade: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Per-sample cache reuse from prior runs sharing *node_configs*; *dataset_name* scopes the slice.
    *min_grade* drops runs below that provenance grade (clean-substrate reads); default keeps all."""
    return stores.archive.load_reusable_results(
        node_configs,
        is_fatal=is_fatal,
        dataset_name=dataset_name,
        min_grade=min_grade,
    )


def resolve_aliases(stores: Stores, rp_hash: str) -> set[str]:
    """All ``rendered_prompt_hash`` values equivalent to *rp_hash* (including itself)."""
    return stores.archive.resolve_aliases(rp_hash)


# -- writes -------------------------------------------------------------------


def record_measurement_run(
    stores: Stores,
    run_id: str,
    data: dict[str, Any],
) -> Path:
    """Sole write entry point — persist one measurement-batch detail + index upsert."""
    return stores.archive.save(run_id, data)


def reindex_measurements(stores: Stores) -> dict[str, int]:
    """Rebuild the append-only measurement index from the detail files and GC orphans.
    A maintenance verb — the index is derived, so this loses nothing; returns counts."""
    return stores.archive.reindex()


def register_prompt_alias(
    stores: Stores,
    raw_text: str,
    canonical_text: str,
) -> None:
    """Alias a raw prompt string to its canonical form (no-op if either is empty / equal)."""
    stores.archive.register_prompt_alias(raw_text, canonical_text)
