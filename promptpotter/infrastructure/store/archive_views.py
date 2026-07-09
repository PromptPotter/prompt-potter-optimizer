"""MeasurementArchive facade — sole gateway to the cross-cycle archive.

The archive is the database core (per ``docs/architecture.md``): cross-cycle,
content-addressed measurements indexed by sample + node-config. Every archive
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
    "resolve_aliases",
    "reusable_results",
    "runs_since",
]


# -- reads --------------------------------------------------------------------


def measurements_for_sample(
    stores: Stores,
    backend_id: str,
    sample_id: int,
    *,
    run_ids: list[str] | None = None,
    dataset_name: str | None = None,
    include_unknown: bool = False,
) -> list[Measurement]:
    """Every measurement of one sample, across configs; *dataset_name* scopes the slice."""
    return stores.archive.measurements_for_sample(
        backend_id,
        sample_id,
        run_ids=run_ids,
        dataset_name=dataset_name,
        include_unknown=include_unknown,
    )


def measurement_series_for_samples(
    stores: Stores,
    backend_id: str,
    sample_ids: list[int],
    *,
    dataset_name: str | None = None,
    include_unknown: bool = False,
) -> dict[int, list[dict[str, Any]]]:
    """Per-sample chronological series, one archive walk for the whole set.

    ``{sample_id: [{ord, hit, run_id, created_at}, ...]}`` sorted ascending by
    ``ord`` = ``created_at``/``run_id``/item-index. Errored items dropped
    (matches ``build_archive_observations``'s Rasch-fit filter so the
    dashboard hit-rate column and δ_s estimate see the same observations).
    Powers the ``/datasets/{name}/measurement-series`` endpoint."""
    wanted = set(sample_ids)
    out: dict[int, list[dict[str, Any]]] = {sid: [] for sid in wanted}
    for entry in stores.archive.list_all(
        backend_id, dataset_name=dataset_name, include_unknown=include_unknown
    ):
        run_id = entry["run_id"]
        detail = stores.archive.load_by_id(backend_id, run_id)
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
    backend_id: str,
    predicate: dict[str, dict[str, Any]],
    *,
    run_ids: set[str] | list[str] | None = None,
    dataset_name: str | None = None,
    include_unknown: bool = False,
) -> list[Measurement]:
    """Every measurement under configs matching *predicate*, across all samples."""
    return stores.archive.measurements_for_config(
        backend_id,
        predicate,
        run_ids=run_ids,
        dataset_name=dataset_name,
        include_unknown=include_unknown,
    )


def load_run(stores: Stores, backend_id: str, run_id: str) -> dict[str, Any] | None:
    """Load one run's detail file by ``run_id``; ``None`` if absent.
    Dataset-agnostic — run_id encodes its dataset via the index;
    callers needing the stamp read ``detail['dataset_name']`` themselves."""
    return stores.archive.load_by_id(backend_id, run_id)


def list_runs(
    stores: Stores,
    backend_id: str,
    *,
    dataset_name: str | None = None,
    include_unknown: bool = False,
) -> list[dict[str, Any]]:
    """All run-summary entries from the archive index, scoped to ``dataset_name``."""
    return stores.archive.list_all(
        backend_id, dataset_name=dataset_name, include_unknown=include_unknown
    )


def runs_since(
    stores: Stores,
    backend_id: str,
    seen_ids: set[str],
    *,
    dataset_name: str | None = None,
    include_unknown: bool = False,
) -> Iterator[tuple[str, dict[str, Any]]]:
    """Yield ``(run_id, detail)`` for runs not in *seen_ids*; missing details skipped."""
    return stores.archive.load_since(
        backend_id, seen_ids, dataset_name=dataset_name, include_unknown=include_unknown
    )


def reusable_results(
    stores: Stores,
    backend_id: str,
    node_configs: list[tuple[str, dict[str, Any]]],
    is_fatal: Callable[[dict[str, Any]], bool] | None = None,
    *,
    dataset_name: str | None = None,
    include_unknown: bool = False,
    min_grade: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Per-sample cache reuse from prior runs sharing *node_configs*; *dataset_name* scopes the slice.
    *min_grade* drops runs below that provenance grade (clean-substrate reads); default keeps all."""
    return stores.archive.load_reusable_results(
        backend_id,
        node_configs,
        is_fatal=is_fatal,
        dataset_name=dataset_name,
        include_unknown=include_unknown,
        min_grade=min_grade,
    )


def resolve_aliases(stores: Stores, backend_id: str, rp_hash: str) -> set[str]:
    """All ``rendered_prompt_hash`` values equivalent to *rp_hash* (including itself)."""
    return stores.archive.resolve_aliases(backend_id, rp_hash)


# -- writes -------------------------------------------------------------------


def record_measurement_run(
    stores: Stores,
    backend_id: str,
    run_id: str,
    data: dict[str, Any],
) -> Path:
    """Sole write entry point — persist one measurement-batch detail + index upsert."""
    return stores.archive.save(backend_id, run_id, data)


def register_prompt_alias(
    stores: Stores,
    backend_id: str,
    raw_text: str,
    canonical_text: str,
) -> None:
    """Alias a raw prompt string to its canonical form (no-op if either is empty / equal)."""
    stores.archive.register_prompt_alias(backend_id, raw_text, canonical_text)
