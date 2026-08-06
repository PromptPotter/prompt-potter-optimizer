"""Sole writer of top-level ``fitness``; idempotent under one ``scorer_id``. It stamps EVERY row, error rows included,
so the key is always present. There is deliberately no ``hit`` beside it — that stores a threshold's output."""

from __future__ import annotations

from typing import Any

from promptpotter.domain.scoring import Scorer


def rescore_results(
    results: list[dict[str, Any]],
    scorer: Scorer,
    scorer_id: str = "none",
    formula: str | None = None,
) -> list[dict[str, Any]]:
    """Apply *scorer* to each result. An error row is stamped ``fitness=0.0`` as a DISPLAY convention — **no estimator may
    read it as a verdict**; every consumer meaning "measurement" filters the typed ``error_category`` instead."""
    from promptpotter.shared.errors import is_error_result

    for r in results:
        fitness = 0.0 if is_error_result(r) else scorer(r)
        scored = r.setdefault("scored", {})
        scored[scorer_id] = {"fitness": fitness, "formula": formula}
        r["fitness"] = fitness
    return results


__all__ = ["rescore_results"]
