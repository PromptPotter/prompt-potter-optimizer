"""``composite_fitness`` — the round-level number every other surface is read against, computed from
the evaluator registry and the campaign's scoring formula. This is the COMPUTER; the single scoring
ingress that reaches it is ``search_point_scorer.py::score_search_point`` (§0.5), and confusing the
gateway for the computer is the classic miss — a change to how the number is DERIVED lands here.

What a row reports is ``row_diagnostics.py``'s, and which candidate wins is ``selection.py``'s."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from promptpotter.application.scoring.classification import is_deprecated, scoreable_rows
from promptpotter.application.scoring.evaluators import (
    compute_accuracy,
    materialize_round_values,
)
from promptpotter.application.scoring.formula import ScoringTermMissingError, cell_channels_of
from promptpotter.application.scoring.row_diagnostics import count_degraded_samples

if TYPE_CHECKING:
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.scoring import QueryMeasurement

__all__ = [
    "compute_composite_fitness",
    "fold_cells",
    "matched_parent_stats",
]


def _compute_accuracy(results: list[QueryMeasurement]) -> dict[str, Any]:
    """``total`` is the EVIDENCE denominator: the rows carrying a verdict, a deprecated one included
    as the miss it is. ``errors`` is every row carrying none, and ``deprecated`` a count within
    ``total`` — the sample lifecycle's, never a subtraction from the rate."""
    deprecated = sum(1 for r in results if is_deprecated(r))
    scoreable = scoreable_rows(results)
    total = len(scoreable)
    # Derived from the ONE filter rather than re-walked, so the two counts partition `results`.
    errors = len(results) - total
    # Same filter behind the mean — `compute_accuracy` calls `scoreable_rows` too, so `total` and
    # `accuracy` describe one population.
    accuracy = compute_accuracy(results=results)
    return {
        "total": total,
        "accuracy": accuracy,
        "errors": errors,
        "deprecated": deprecated,
    }


# The per-cell channels a round REPORTS a mean of — the registry's COMPLEMENT, not all of them:
# `fitness`/`errored`/`degraded` are already `accuracy`/`error_rate`/`degraded_rate`, and meaning
# each twice would put one number in the map under two names.
_MEANED_CHANNELS: tuple[str, ...] = ("latency", "cost", "tokens")


def _channel_means(scoreable: list[QueryMeasurement]) -> dict[str, float]:
    """Mean over the rows that ANSWERED each channel, never over all of them — a cell nothing
    priced is unmeasured, and the full count reports the round as cheaper than it was."""
    sums: dict[str, list[float]] = {}
    for row in scoreable:
        answered = cell_channels_of(row)
        for name in _MEANED_CHANNELS:
            if (value := answered.get(name)) is not None:
                sums.setdefault(name, []).append(value)
    return {name: sum(vs) / len(vs) for name, vs in sums.items()}


# ---------------------------------------------------------------------------
# Round-level composite_fitness — driven by the evaluator registry + scoring formula.
# ---------------------------------------------------------------------------


def _composite_of(scoreable: list[QueryMeasurement]) -> float:
    if not scoreable:
        # No measurement — an operator skip at query 0/N, a round whose every sample was excluded,
        # or one that errored throughout — has no fitness. Record the 0.0 floor (``total`` is
        # already 0, the no-evidence marker election reads). The floor is the COMPOSITE's alone:
        # `accuracy` stays whatever `compute_accuracy` answered, which is `None` where nothing was
        # measured, so the elected quantity keeps its floor while the reported rate never claims a
        # 0% nobody read.
        return 0.0
    # The SAME denominator ``accuracy`` is read against, so the two cannot describe different
    # populations. An unstamped row is an absence, never a zero: it halts.
    unstamped = sum(1 for r in scoreable if r.get("objective") is None)
    if unstamped:
        raise ScoringTermMissingError(
            f"{unstamped} of {len(scoreable)} scoreable rows carry no 'objective' — they never "
            "passed through `rescore_results`, so what they were worth was never computed. "
            "That is missing data, not a fitness of zero."
        )
    return sum(float(r["objective"]) for r in scoreable) / len(scoreable)


def fold_cells(results: list[QueryMeasurement]) -> dict[str, Any]:
    """The schema-free half of :func:`compute_composite_fitness` — the evidence counts,
    ``accuracy`` and ``composite_fitness`` — for a reader holding graded rows and no schema."""
    return {
        **_compute_accuracy(results),
        "composite_fitness": _composite_of(scoreable_rows(results)),
    }


def compute_composite_fitness(
    results: list[QueryMeasurement],
    pipeline_schema: PipelineSchema,
) -> dict[str, Any]:
    """The composite is the MEAN of what each cell was worth — ``rescore_results`` already evaluated
    the campaign's formula once per row, so this folds rather than re-scores. That is what puts a
    cost or reliability term on θ: this number and the one every θ is fit on are the same
    per-cell value, read at two scopes instead of two formulas at one scope."""
    base = _compute_accuracy(results)
    scoreable = scoreable_rows(results)
    evaluator_values = materialize_round_values(pipeline_schema, results)
    # Meaned in under the SAME names the per-cell composite uses. A reading of the round, never a
    # formula input: a lens re-grades the rows (`mask/load.py`), since f(mean) is not mean(f).
    evaluator_values.update(_channel_means(scoreable))
    return {
        **base,
        **evaluator_values,
        "evaluators": dict(evaluator_values),
        "composite_fitness": _composite_of(scoreable),
        "degraded_samples": count_degraded_samples(results),
    }


def matched_parent_stats(
    parent_results: list[QueryMeasurement],
    candidate_results: list[QueryMeasurement],
    pipeline_schema: PipelineSchema,
) -> dict[str, Any] | None:
    """``None`` unless the candidate measured EVERY cell the PARENT did — the origin at round 0 and the prior winner after,
    never the origin throughout. Pairing does not rescue a truncated prefix — the shared cells ARE the parent's
    failures, so both halves are conditioned on what selected the subset."""
    parent_sids = {r.get("sample_id") for r in parent_results}
    if not parent_sids or not parent_sids <= {r.get("sample_id") for r in candidate_results}:
        return None
    # `compute_composite_fitness` already spreads `_compute_accuracy` into its result —
    # calling it again here was a second `is_deprecated` walk over the same rows for the same
    # numbers, and a second place for the two to disagree.
    composite = compute_composite_fitness(parent_results, pipeline_schema)
    return {key: composite[key] for key in ("accuracy", "total", "composite_fitness")}
