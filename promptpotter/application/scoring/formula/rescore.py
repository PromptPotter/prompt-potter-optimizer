"""``rescore_results`` — sole writer of top-level ``fitness`` on result dicts; idempotent under same ``scorer_id``. Stamps every row, error rows included, so the key is ALWAYS present on a served row (consumers read the value, never reconstruct it).

There is deliberately no ``hit`` beside it: it was only ever ``fitness >= 1.0``, which
``domain/scoring.py::is_hit`` derives at the display sites that still want a per-sample
tag. Persisting it stored a threshold's output next to its input."""

from __future__ import annotations

from typing import Any

from promptpotter.domain.scoring import Scorer


def rescore_results(
    results: list[dict[str, Any]],
    scorer: Scorer,
    scorer_id: str = "none",
    formula: str | None = None,
) -> list[dict[str, Any]]:
    """Apply *scorer* to each result, accumulating the multi-scorer audit map. Error rows can't be
    scored (no prediction) → stamped at the floor (``fitness=0.0``) rather than skipped, matching the
    implicit default ``compute_accuracy`` already reads — so the value is unchanged but now explicit
    and always present."""
    from promptpotter.shared.errors import is_error_result

    for r in results:
        fitness = 0.0 if is_error_result(r) else scorer(r)
        scored = r.setdefault("scored", {})
        scored[scorer_id] = {"fitness": fitness, "formula": formula}
        r["fitness"] = fitness
    return results


__all__ = ["rescore_results"]
