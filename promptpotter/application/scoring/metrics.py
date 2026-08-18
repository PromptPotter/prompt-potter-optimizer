"""``composite_fitness`` — the round-level number every other surface is read against, computed from
the evaluator registry and the campaign's scoring formula. This is the COMPUTER; the single scoring
ingress that reaches it is ``search_point_scorer.py::score_search_point`` (§0.5), and confusing the
gateway for the computer is the classic miss — a change to how the number is DERIVED lands here.

Its two neighbours were split out of the same file: ``diagnostics.py`` (what a row reports) and
``selection.py`` (which candidate wins)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from promptpotter.application.scoring.diagnostics import count_degraded_samples
from promptpotter.application.scoring.evaluators import compute_accuracy as compute_accuracy
from promptpotter.application.scoring.evaluators import (
    default_per_round_formula,
    materialize_round_values,
)
from promptpotter.application.scoring.formula import (
    ScoringTermMissingError,
    compile_round_scorer,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from promptpotter.domain.opt_search_point import OptSearchPoint
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.scoring import QueryMeasurement, RoundScorer

__all__ = [
    "compute_composite_fitness",
    "matched_parent_stats",
    "value_with_mask_applied",
]


def _compute_accuracy(results: list[QueryMeasurement]) -> dict[str, Any]:
    """``total`` is the EVIDENCE denominator: scoreable rows only. An errored or deprecated row
    carries no verdict, so neither belongs in the denominator a rate is read against."""
    from promptpotter.application.optimization.pobb.classification import (
        is_deprecated,
        scoreable_rows,
    )

    deprecated = sum(1 for r in results if is_deprecated(r))
    scoreable = scoreable_rows(results)
    total = len(scoreable)
    # Derived from the ONE filter rather than re-walked: the three counts must partition `results`,
    # and a second `is_error_result` pass is a second place for them to stop doing so.
    errors = len(results) - deprecated - total
    # Same filter behind the mean — `compute_accuracy` calls `scoreable_rows` too, so `total` and
    # `accuracy` can no longer describe different populations.
    accuracy = compute_accuracy(results=results)
    return {
        "total": total,
        "accuracy": accuracy,
        "errors": errors,
        "deprecated": deprecated,
    }


# ---------------------------------------------------------------------------
# Round-level composite_fitness — driven by the evaluator registry + scoring formula.
# ---------------------------------------------------------------------------


def compute_composite_fitness(
    results: list[QueryMeasurement],
    pipeline_schema: PipelineSchema,
    *,
    opt_sp: OptSearchPoint | None,
    round_scorer: RoundScorer | str | None = None,
    l1_diversity: float = 1.0,
) -> dict[str, Any]:
    """``opt_sp=None`` puts every searchpoint-aware evaluator on its vacuous fallback, and ``l1_diversity`` defaults to 1.0
    for the same reason: 0.0 would score the two halves of one delta on different bases."""
    base = _compute_accuracy(results)
    evaluator_values = materialize_round_values(pipeline_schema, results, opt_sp=opt_sp)
    # L1-generation quality is a batch property, not a per-result derivation —
    # injected after registry materialization so operator formulas can
    # reference ``l1_diversity`` via campaign.json::scoring.
    evaluator_values["l1_diversity"] = float(l1_diversity)

    if not results:
        # No measurement — an operator skip at query 0/N, or a round whose every sample was
        # excluded — has no fitness. Record the 0.0 floor (``total`` is already 0, the
        # no-evidence marker election reads) rather than run the default ``accuracy`` scorer,
        # which halts on the absent term and would crash the cycle. A round that DID measure
        # rows but names an absent term is a formula bug and still raises below.
        base = {**base, "accuracy": 0.0}
        composite_fitness = 0.0
    else:
        if callable(round_scorer):
            scorer = round_scorer
        elif isinstance(round_scorer, str):
            scorer = compile_round_scorer(round_scorer)
        else:
            scorer = compile_round_scorer(default_per_round_formula(pipeline_schema))
        composite_fitness = scorer(evaluator_values)

    # OptSP-layer counts for display and the validation-failure short-circuit.
    runtime_failure_count = 0
    validation_failure_count = 0
    if opt_sp is not None:
        runtime_failure_count = len(opt_sp.memory.wounds.runtime_failures)
        validation_failure_count = len(opt_sp.memory.wounds.validation_failures)

    if validation_failure_count > 0:
        composite_fitness = 0.0

    degraded = count_degraded_samples(results)

    return {
        **base,
        **evaluator_values,
        "evaluators": dict(evaluator_values),
        "composite_fitness": composite_fitness,
        "degraded_samples": degraded,
        "validation_failure_count": validation_failure_count,
        "runtime_failure_count": runtime_failure_count,
    }


def matched_parent_stats(
    parent_results: list[QueryMeasurement],
    candidate_results: list[QueryMeasurement],
    pipeline_schema: PipelineSchema,
    *,
    round_scorer: RoundScorer | str | None = None,
) -> dict[str, Any] | None:
    """``None`` unless the candidate measured EVERY cell the PARENT did — the origin at round 0 and the prior winner after,
    never the origin throughout. Pairing does not rescue a truncated prefix — the shared cells ARE the incumbent's
    failures, so both halves are conditioned on what selected the subset."""
    parent_sids = {r.get("sample_id") for r in parent_results}
    if not parent_sids or not parent_sids <= {r.get("sample_id") for r in candidate_results}:
        return None
    # `compute_composite_fitness` already spreads `_compute_accuracy` into its result —
    # calling it again here was a second `is_deprecated` walk over the same rows for the same
    # numbers, and a second place for the two to disagree.
    composite = compute_composite_fitness(
        parent_results,
        pipeline_schema,
        opt_sp=None,
        round_scorer=round_scorer,
        l1_diversity=1.0,
    )
    return {key: composite[key] for key in ("accuracy", "total", "composite_fitness")}


def value_with_mask_applied(
    evaluators: Mapping[str, float],
    criterion: RoundScorer | str | None,
) -> float | None:
    """``None`` when the criterion names an evaluator absent from this record's namespace —
    unscorable under this mask, never a fabricated score. Every OTHER scoring error still propagates."""
    scorer = criterion if callable(criterion) else compile_round_scorer(criterion)
    try:
        return float(scorer(dict(evaluators)))
    except ScoringTermMissingError:
        return None
