"""``rescore_results`` — sole writer of top-level ``hit``/``fitness`` on result dicts; idempotent under same ``scorer_id``."""

from __future__ import annotations

from typing import Any

from promptpotter.domain.scoring import Scorer


def rescore_results(
    results: list[dict[str, Any]],
    scorer: Scorer,
    scorer_id: str = "none",
    formula: str | None = None,
) -> list[dict[str, Any]]:
    """Apply *scorer* to each result, accumulating the multi-scorer audit map; skips error rows."""
    from promptpotter.shared.errors import is_error_result

    for r in results:
        if is_error_result(r):
            continue
        fitness = scorer(r)
        hit = fitness >= 1.0
        scored = r.setdefault("scored", {})
        scored[scorer_id] = {"fitness": fitness, "hit": hit, "formula": formula}
        r["fitness"] = fitness
        r["hit"] = hit
    return results


__all__ = ["rescore_results"]
