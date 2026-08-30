"""Sole writer of top-level ``fitness`` and ``objective``, and idempotent. It stamps EVERY row, error rows included, so
both keys are always present. There is deliberately no ``hit`` beside them — that stores a threshold's output."""

from __future__ import annotations

from typing import Any

from promptpotter.domain.scoring import CellScorer


def rescore_results(results: list[dict[str, Any]], scorer: CellScorer) -> list[dict[str, Any]]:
    """Apply *scorer* to each result. An error row is stamped ``0.0`` as a DISPLAY convention — **no estimator may
    read it as a verdict**; every consumer meaning "measurement" filters the typed ``error_category`` instead.

    ``fitness`` first, then ``objective``: the composite reads the correctness it is composed OF
    (``compiler.py::objective_namespace`` binds ``fitness``), so the order is the dependency."""
    from promptpotter.shared.errors import is_error_result

    for r in results:
        if is_error_result(r):
            r["fitness"] = r["objective"] = 0.0
            continue
        r["fitness"] = scorer.fitness(r)
        r["objective"] = scorer.objective(r)
    return results


__all__ = ["rescore_results"]
