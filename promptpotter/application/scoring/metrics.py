"""``composite_fitness`` — the round-level number every other surface is read against, computed from
the evaluator registry and the campaign's scoring formula. This is the COMPUTER; the single scoring
ingress that reaches it is ``search_point_scorer.py::score_search_point`` (§0.5), and confusing the
gateway for the computer is the classic miss — a change to how the number is DERIVED lands here.

What a row reports is ``row_diagnostics.py``'s, and which candidate wins is ``selection.py``'s."""

from __future__ import annotations

from typing import TYPE_CHECKING, TypedDict

from promptpotter.application.scoring.classification import scoreable_rows
from promptpotter.application.scoring.evaluators import (
    compute_accuracy,
    materialize_round_values,
)
from promptpotter.application.scoring.formula import ScoringTermMissingError, cell_channels_of
from promptpotter.application.scoring.row_diagnostics import count_degraded_samples
from promptpotter.application.scoring.selection import mean_fitness_ci
from promptpotter.domain.results_health import is_deprecated

if TYPE_CHECKING:
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.scoring import QueryMeasurement

__all__ = [
    "INVALID_SCORES",
    "CellFold",
    "ScoreSummary",
    "compute_composite_fitness",
    "fold_cells",
    "matched_parent_stats",
]


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


def _composite_of(scoreable: list[QueryMeasurement]) -> float | None:
    if not scoreable:
        # No measurement — an operator skip at query 0/N, a round whose every sample was excluded,
        # or one that errored throughout — has no fitness, as it has no `accuracy`.
        return None
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


class CellFold(TypedDict):
    """Graded rows folded. ``total`` is the EVIDENCE denominator: the rows carrying a verdict, a
    deprecated one included as a miss. The two means are ``None`` where it is empty, never a 0.0."""

    total: int
    accuracy: float | None
    composite_fitness: float | None
    deprecated: int


class ScoreSummary(CellFold):
    """One candidate's fold as the gateway reports it: the round-scope evaluator values, and the
    band accuracy is drawn with, over the same rows."""

    evaluators: dict[str, float]
    degraded_samples: int
    mean_fitness_ci_lo: float | None
    mean_fitness_ci_hi: float | None


def fold_cells(results: list[QueryMeasurement]) -> CellFold:
    """The schema-free half of :func:`compute_composite_fitness`, for a reader holding graded rows
    and no schema."""
    scoreable = scoreable_rows(results)
    return {
        "total": len(scoreable),
        "accuracy": compute_accuracy(results=results),
        "composite_fitness": _composite_of(scoreable),
        "deprecated": sum(1 for r in results if is_deprecated(r)),
    }


def compute_composite_fitness(
    results: list[QueryMeasurement],
    pipeline_schema: PipelineSchema,
) -> ScoreSummary:
    """The composite is the MEAN of what each cell was worth — ``rescore_results`` already evaluated
    the campaign's formula once per row, so this folds rather than re-scores. That is what puts a
    cost or reliability term on θ: this number and the one every θ is fit on are the same
    per-cell value, read at two scopes instead of two formulas at one scope."""
    evaluator_values = materialize_round_values(pipeline_schema, results)
    # Meaned in under the SAME names the per-cell composite uses. A reading of the round, never a
    # formula input: a lens re-grades the rows (`mask/load.py`), since f(mean) is not mean(f).
    evaluator_values.update(_channel_means(scoreable_rows(results)))
    ci_lo, ci_hi = mean_fitness_ci(results, grade="fitness")
    return {
        **fold_cells(results),
        "evaluators": evaluator_values,
        "degraded_samples": count_degraded_samples(results),
        "mean_fitness_ci_lo": ci_lo,
        "mean_fitness_ci_hi": ci_hi,
    }


# An arm rejected before it cost a sample: nothing was measured, so nothing is scored.
INVALID_SCORES: ScoreSummary = {
    "total": 0,
    "accuracy": None,
    "composite_fitness": None,
    "deprecated": 0,
    "evaluators": {},
    "degraded_samples": 0,
    "mean_fitness_ci_lo": None,
    "mean_fitness_ci_hi": None,
}


def matched_parent_stats(
    parent_results: list[QueryMeasurement],
    candidate_results: list[QueryMeasurement],
) -> CellFold | None:
    """``None`` unless the candidate measured EVERY cell the PARENT did — the origin at round 0 and the prior winner after,
    never the origin throughout. Pairing does not rescue a truncated prefix — the shared cells ARE the parent's
    failures, so both halves are conditioned on what selected the subset."""
    parent_sids = {r.get("sample_id") for r in parent_results}
    if not parent_sids or not parent_sids <= {r.get("sample_id") for r in candidate_results}:
        return None
    return fold_cells(parent_results)
