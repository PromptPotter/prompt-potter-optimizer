"""MeasurementArchive facade — sole gateway to the cross-cycle archive.

This file participates in **archive** as the sole access facade.
The archive is "the database core" (per ``docs/architecture.md``):
cross-cycle, cross-session, content-addressed measurements indexed by
sample and by node-config. Every read or write of the archive lives
behind this module. Reaching into ``session.store.archive`` (or
``store.archive``, or ``self.store.archive``) outside this file is
drift; ``tests/test_invariants.py::test_no_direct_archive_access_outside_facade``
enforces the invariant via grep.

Aliasing the archive (``archive = session.store.archive``) is also
drift — the widened grep also catches that.

The facade narrows the surface to the exact reads + writes today's
callers need. It is *not* a re-shaping of the archive API; pure
gateway. Adding a new archive read/write means adding a function here
first, then calling it from the consumer.

Layer placement: facade lives in ``infrastructure/store/`` (next to
the archive itself) rather than ``application/scoring/`` because
``infrastructure/tracing/replay.py`` is one of the consumers and
``infrastructure → application`` is a forbidden hexagonal direction.

Out-of-bounds: no caller may access ``stores.archive`` member methods
directly outside this module. ``record_measurement_run`` is the sole
write; any new write means a new function here, not a sidecar.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

    from promptpotter.domain.sample import Measurement
    from promptpotter.infrastructure.store.stores import Stores

__all__ = [
    "aggregate_per_query",
    "find_by_prefix",
    "list_runs",
    "load_run",
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
) -> list[Measurement]:
    """Every measurement of one training example, across all configs."""
    return stores.archive.measurements_for_sample(backend_id, sample_id, run_ids=run_ids)


def measurements_for_config(
    stores: Stores,
    backend_id: str,
    predicate: dict[str, dict[str, Any]],
    *,
    run_ids: set[str] | list[str] | None = None,
) -> list[Measurement]:
    """Every measurement under configs matching *predicate*, across all samples."""
    return stores.archive.measurements_for_config(backend_id, predicate, run_ids=run_ids)


def load_run(stores: Stores, backend_id: str, run_id: str) -> dict[str, Any] | None:
    """Load one run's full detail file by ``run_id``; ``None`` if absent."""
    return stores.archive.load_by_id(backend_id, run_id)


def list_runs(stores: Stores, backend_id: str) -> list[dict[str, Any]]:
    """All run-summary entries from the archive index."""
    return stores.archive.list_all(backend_id)


def runs_since(
    stores: Stores,
    backend_id: str,
    seen_ids: set[str],
) -> Iterator[tuple[str, dict[str, Any]]]:
    """Yield ``(run_id, detail)`` for runs not in *seen_ids*; missing details skipped."""
    return stores.archive.load_since(backend_id, seen_ids)


def find_by_prefix(
    stores: Stores,
    backend_id: str,
    node_configs: list[tuple[str, dict]],
) -> list[tuple[dict[str, Any], int]]:
    """Index entries sharing a node-config prefix; ``(entry, match_length)``, best first."""
    return stores.archive.find_by_node_configs(backend_id, node_configs)


def reusable_results(
    stores: Stores,
    backend_id: str,
    node_configs: list[tuple[str, dict]],
    is_fatal: Callable[[dict[str, Any]], bool] | None = None,
) -> dict[str, dict[str, Any]]:
    """Per-sample cache reuse from prior runs sharing *node_configs* (full or prefix-trusted)."""
    return stores.archive.load_reusable_results(backend_id, node_configs, is_fatal=is_fatal)


def resolve_aliases(stores: Stores, backend_id: str, rp_hash: str) -> set[str]:
    """All ``rendered_prompt_hash`` values equivalent to *rp_hash* (including itself)."""
    return stores.archive.resolve_aliases(backend_id, rp_hash)


def aggregate_per_query(
    stores: Stores,
    backend_id: str,
) -> dict[str, Any]:
    """Roll up every archived measurement into per-query stats — the "your data accumulates" view.

    Walks the index, loads each detail file, groups items by ``query``,
    and returns ``{n_runs, n_measurements, n_unique_queries, per_query: [...]}``
    where each ``per_query`` row carries ``{query, sample_id, n_measurements,
    n_unique_configs, mean_fitness, hit_rate, last_seen}``. Rows are
    sorted by ``n_measurements`` desc — most-reused queries first.

    Archive is tenant-scoped (``backend_id`` is preserved on the signature
    for facade consistency but ignored for path construction by the
    underlying archive). Output therefore aggregates across every
    campaign on the install.
    """
    by_query: dict[str, dict[str, Any]] = {}
    seen_run_ids: set[str] = set()
    total_measurements = 0
    for entry in stores.archive.list_all(backend_id):
        run_id = entry["run_id"]
        if run_id in seen_run_ids:
            continue
        detail = stores.archive.load_by_id(backend_id, run_id)
        if detail is None:
            continue
        seen_run_ids.add(run_id)
        content_hash = detail.get("content_hash", "")
        created_at = detail.get("created_at", "")
        for item in detail.get("measurements", []):
            query = item.get("query", "")
            if not query:
                continue
            total_measurements += 1
            bucket = by_query.setdefault(
                query,
                {
                    "query": query,
                    "sample_id": item.get("sample_id", -1),
                    "n_measurements": 0,
                    "fitness_sum": 0.0,
                    "fitness_count": 0,
                    "hit_count": 0,
                    "configs": set(),
                    "last_seen": "",
                },
            )
            bucket["n_measurements"] += 1
            fitness = item.get("fitness")
            if isinstance(fitness, int | float):
                bucket["fitness_sum"] += float(fitness)
                bucket["fitness_count"] += 1
            if item.get("hit"):
                bucket["hit_count"] += 1
            if content_hash:
                bucket["configs"].add(content_hash)
            if created_at > bucket["last_seen"]:
                bucket["last_seen"] = created_at

    per_query: list[dict[str, Any]] = []
    for bucket in by_query.values():
        n = bucket["n_measurements"]
        fc = bucket["fitness_count"]
        per_query.append(
            {
                "query": bucket["query"],
                "sample_id": bucket["sample_id"],
                "n_measurements": n,
                "n_unique_configs": len(bucket["configs"]),
                "mean_fitness": (bucket["fitness_sum"] / fc) if fc > 0 else None,
                "hit_rate": (bucket["hit_count"] / n) if n > 0 else 0.0,
                "last_seen": bucket["last_seen"],
            }
        )
    per_query.sort(key=lambda r: r["n_measurements"], reverse=True)
    return {
        "n_runs": len(seen_run_ids),
        "n_measurements": total_measurements,
        "n_unique_queries": len(by_query),
        "per_query": per_query,
    }


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
