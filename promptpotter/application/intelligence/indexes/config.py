"""ConfigIndex — per-config derived view caching ``node_configs → set[run_id]``."""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any


class ConfigIndex:
    """Per-config derived view over the MeasurementArchive.

    Caches the canonical ``node_configs → set[run_id]`` mapping so
    ``MeasurementArchive.measurements_for_config(predicate)`` can skip
    its O(archive_size) full scan: with this index, a config-keyed
    query becomes O(unique_configs) over the candidate-config walk plus
    O(matching_runs) over the targeted load.

    Mirrors :class:`SampleIndex` ergonomics — pure derived view, owns no
    on-disk artifact, ingests via :meth:`ingest_run` and exposes a
    ``_seen_runs`` cursor for incremental refresh.
    """

    def __init__(self) -> None:
        # config_key → set of run_ids stored under that exact node_configs.
        self._configs_to_runs: dict[str, set[str]] = defaultdict(set)
        # config_key → original list[tuple[name, dict]] for predicate matching.
        self._configs_to_node_configs: dict[str, list[tuple[str, dict[str, Any]]]] = {}
        self._seen_runs: set[str] = set()

    @staticmethod
    def _canonical_key(node_configs: list[tuple[str, dict[str, Any]]]) -> str:
        """Stable string key over the node_configs chain — sorted dicts, defaulted on non-JSON."""
        return json.dumps(
            [[n, dict(c)] for n, c in node_configs],
            sort_keys=True,
            default=str,
        )

    def ingest_run(self, run_detail: dict[str, Any]) -> None:
        """Replay a measurement-archive entry into the index. Idempotent on ``run_id``."""
        run_id = run_detail.get("run_id", "")
        if not run_id or run_id in self._seen_runs:
            return
        raw = run_detail.get("node_configs") or []
        node_configs: list[tuple[str, dict[str, Any]]] = [
            (pair[0], dict(pair[1]))
            for pair in raw
            if isinstance(pair, list | tuple) and len(pair) == 2 and isinstance(pair[1], dict)
        ]
        if not node_configs:
            self._seen_runs.add(run_id)
            return
        key = self._canonical_key(node_configs)
        self._configs_to_runs[key].add(run_id)
        self._configs_to_node_configs.setdefault(key, node_configs)
        self._seen_runs.add(run_id)

    def run_ids_matching(self, predicate: dict[str, dict[str, Any]]) -> set[str]:
        """Run ids whose node_configs satisfy *predicate* (subset semantics).

        ``predicate`` shape matches
        :meth:`MeasurementArchive.measurements_for_config`: each
        ``{node_name: required_subdict}`` entry must appear in the
        stored chain with at least the required keys/values. Empty
        predicate returns the empty set (consistent with the archive).
        """
        if not predicate:
            return set()
        out: set[str] = set()
        for key, node_configs in self._configs_to_node_configs.items():
            if _matches_subset_local(node_configs, predicate):
                out |= self._configs_to_runs[key]
        return out


def _matches_subset_local(
    stored: list[tuple[str, dict[str, Any]]],
    predicate: dict[str, dict[str, Any]],
) -> bool:
    """Subset match — every node in *predicate* must appear in *stored*
    with at least the required key/value pairs. Mirrors
    :func:`measurement_archive._matches_subset` but operates on the
    in-memory tuple form rather than archive index entries."""
    by_name: dict[str, dict[str, Any]] = {n: c for n, c in stored if isinstance(c, dict)}
    for node_name, subdict in predicate.items():
        cfg = by_name.get(node_name)
        if cfg is None:
            return False
        if subdict and not (subdict.items() <= cfg.items()):
            return False
    return True
