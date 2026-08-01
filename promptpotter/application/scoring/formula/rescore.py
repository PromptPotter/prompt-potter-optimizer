"""``rescore_results`` — sole writer of top-level ``hit``/``fitness`` on result dicts; idempotent under same ``scorer_id``. Stamps every row, error rows included, so both keys are ALWAYS present on a served row (consumers read the value, never reconstruct it)."""

from __future__ import annotations

from typing import Any

from promptpotter.domain.scoring import Scorer, is_hit


def rescore_results(
    results: list[dict[str, Any]],
    scorer: Scorer,
    scorer_id: str = "none",
    formula: str | None = None,
) -> list[dict[str, Any]]:
    """Apply *scorer* to each result, accumulating the multi-scorer audit map. Error rows can't be
    scored (no prediction) → stamped at the floor (``fitness=0.0``, ``hit=False``) rather than skipped,
    matching the implicit default ``compute_accuracy`` already reads — so the value is unchanged but
    now explicit and always present."""
    from promptpotter.shared.errors import is_error_result

    for r in results:
        if is_error_result(r):
            fitness, hit = 0.0, False
        else:
            fitness = scorer(r)
            hit = is_hit(fitness)
        scored = r.setdefault("scored", {})
        scored[scorer_id] = {"fitness": fitness, "hit": hit, "formula": formula}
        r["fitness"] = fitness
        r["hit"] = hit
    return results


__all__ = ["rescore_results"]
