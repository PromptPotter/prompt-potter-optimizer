from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from promptpotter.application.scoring.evaluators import (
    Evaluator,
    all_evaluators,
    compute_accuracy,
    default_per_round_formula,
    materialize_round_values,
)
from promptpotter.application.scoring.formula import (
    ScoringTermMissingError,
    compile_round_scorer,
    extract_item_label,
)
from promptpotter.domain.pipeline_schema import NodeType
from promptpotter.domain.scoring import RoundScorer
from promptpotter.shared.errors import has_pipeline_warnings, is_error_result

if TYPE_CHECKING:
    from promptpotter.application.intelligence.exploration import RaschPosterior, Ruler
    from promptpotter.domain.opt_search_point import OptSearchPoint
    from promptpotter.domain.pipeline_schema import (
        PipelineNode,
        PipelineSchema,
    )
    from promptpotter.domain.scoring import QueryMeasurement


__all__ = [
    "Evaluator",
    "all_evaluators",
    "compute_composite_fitness",
    "count_degraded_samples",
    "elect_round_winner",
    "elimination_p_best",
    "extract_sample_diagnostics",
    "find_rank",
    "has_pipeline_warnings",
    "matched_origin_stats",
    "paired_fitness",
    "value_with_mask_applied",
]


def find_rank(items: list[Any], ground_truth: str) -> int | None:
    """1-based rank, or ``None`` — diagnostics report the position 0-based."""
    if not items or not ground_truth:
        return None
    for i, c in enumerate(items):
        if extract_item_label(c) == ground_truth:
            return i + 1
    return None


def _compute_accuracy(results: list[QueryMeasurement]) -> dict[str, Any]:
    """``total`` is the EVIDENCE denominator: scoreable rows only. An errored or deprecated row
    carries no verdict, so neither belongs in the denominator a rate is read against."""
    from promptpotter.application.optimization.pobb.classification import is_deprecated

    deprecated = sum(1 for r in results if is_deprecated(r))
    valid = [r for r in results if not is_deprecated(r)]
    errors = sum(1 for r in valid if is_error_result(r))
    scoreable = [r for r in valid if not is_error_result(r)]
    total = len(scoreable)
    # Single source for the mean-fitness-over-scoreable formula.
    accuracy = compute_accuracy(results=results)
    return {
        "total": total,
        "accuracy": accuracy,
        "errors": errors,
        "deprecated": deprecated,
    }


def composite_ci(results: list[QueryMeasurement]) -> tuple[float | None, float | None]:
    """Brackets the SCOREABLE population, since that is the number it is drawn beside — but do
    not push that filter into ``_mean_fitness_by_cell``, which the overlap guard needs un-predicated."""
    # Lazy: scoring → optimization circular.
    from promptpotter.application.optimization.pobb.classification import is_deprecated
    from promptpotter.shared.statistics import mean_ci

    scoreable = [r for r in results if not is_deprecated(r) and not is_error_result(r)]
    per_cell = list(_mean_fitness_by_cell(scoreable).values())
    if not per_cell:
        return (None, None)
    _, ci_lo, ci_hi = mean_ci(per_cell)
    # Clipped to the metric's own support. ``mean_ci`` is a normal-CLT band carrying PoBB's
    # ``1/(4n)`` SE floor, so a candidate whose cells all scored 0.0 came out at ±0.0817 on six
    # samples — an interval claiming negative accuracy, which the webapp then clamped at paint
    # time to keep the whisker inside its own axis. The band stays deliberately optimistic at
    # the boundary (it is the posterior PoBB eliminated on, drawn as the loop believed it), but
    # it may not claim support the quantity does not have.
    return (min(max(ci_lo, 0.0), 1.0), min(max(ci_hi, 0.0), 1.0))


def count_degraded_samples(results: Sequence[Mapping[str, Any]]) -> int:
    return sum(1 for r in results if has_pipeline_warnings(r))


# ---------------------------------------------------------------------------
# Per-sample diagnostics — typed mixed values (bool/int/str/None), keyed off
# ``PipelineNode.node_type``.
# ---------------------------------------------------------------------------


def extract_sample_diagnostics(
    result: Mapping[str, Any],
    pipeline_schema: PipelineSchema,
) -> dict[str, float | bool | int | str | None]:
    pd = result.get("pipeline_data") or {}
    gt = result.get("ground_truth", "")
    diag: dict[str, float | bool | int | str | None] = {
        "terminated_at": pd.get("terminated_at"),
        "total_time_ms": pd.get("total_time"),
        "degraded": bool((pd.get("diagnostics") or {}).get("warnings")),
        "error": is_error_result(result),
    }
    if not pd:
        return diag

    # Namespace a node's diagnostics by step name only when ≥2 nodes share its type.
    type_counts = Counter(s.node_type for s in pipeline_schema.nodes if s.node_type)
    for step in pipeline_schema.nodes:
        extracted = _extract_node_diagnostics(step, pd, gt)
        if extracted is None:
            continue
        prefix = f"{step.name}_" if type_counts[step.node_type] > 1 else ""
        for k, v in extracted.items():
            diag[f"{prefix}{k}"] = v
    return diag


def _diag_ranking(
    pd: Mapping[str, Any],
    gt: str,
    *,
    key: str,
    label: str,
) -> dict[str, float | bool | int | str | None]:
    """Diagnostics report the ground-truth position 0-based; :func:`find_rank` is the canonical
    1-based walk."""
    candidates = pd.get(key, [])
    rank = find_rank(candidates, gt)
    pos = rank - 1 if rank is not None else None
    return {
        f"gt_in_{label}": pos is not None,
        f"n_{label}_candidates": len(candidates),
        f"gt_{label}_rank": pos,
    }


def _diag_candidate_source(
    node: PipelineNode, pd: Mapping[str, Any], gt: str
) -> dict[str, float | bool | int | str | None]:
    return _diag_ranking(pd, gt, key="candidate_ranking", label="source")


def _diag_ranker(
    node: PipelineNode, pd: Mapping[str, Any], gt: str
) -> dict[str, float | bool | int | str | None]:
    candidates = pd.get("final_ranking", [])
    rank = find_rank(candidates, gt)
    pos = rank - 1 if rank is not None else None
    top_score_gap: float | None = None
    if len(candidates) >= 2:
        # Two shapes reach here, both across the highway contract: TermNorm emits scored dicts
        # keyed `relevance_score` (its fuzzy arm converts its own `(term, score)` tuples before
        # they leave), and `llm_only` emits bare answer strings, which carry no score. Nothing
        # emits a `similarity` key or a bare tuple. An item without a score contributes none —
        # a gap between a real score and an invented 0.0 is not a gap.
        scores = [
            float(c["relevance_score"])
            for c in candidates[:2]
            if isinstance(c, dict) and isinstance(c.get("relevance_score"), (int, float))
        ]
        if len(scores) == 2:
            top_score_gap = scores[0] - scores[1]
    # A width-1 ranking has no ranking to report, and the panel prints these on MISS lines
    # only — so `gt_in_ranked` was False on every line it could ever appear on. `None` where
    # the fact is absent, which the panel's `is not None` guard already reads.
    ranked = len(candidates) >= 2
    return {
        "gt_in_ranked": (pos is not None) if ranked else None,
        "n_final_ranking": len(candidates),
        "gt_rank": pos if ranked else None,
        "top_score_gap": top_score_gap,
    }


def _diag_enricher(
    node: PipelineNode, pd: Mapping[str, Any], _gt: str
) -> dict[str, float | bool | int | str | None]:
    n = sum(1 for m in node.observation_mappings if pd.get(m.pipeline_key) is not None)
    return {"n_enriched_fields": n}


def _diag_cache(
    node: PipelineNode, pd: Mapping[str, Any], _gt: str
) -> dict[str, float | bool | int | str | None]:
    timings = pd.get("step_timings") or {}
    return {"cache_hit": timings.get(node.name) is not None}


def _extract_node_diagnostics(
    node: PipelineNode, pd: Mapping[str, Any], gt: str
) -> dict[str, float | bool | int | str | None] | None:
    """Explicit match rather than a string-keyed table, so grepping a diagnostic's name lands on
    its call site."""
    match node.node_type:
        case NodeType.CANDIDATE_SOURCE:
            return _diag_candidate_source(node, pd, gt)
        case NodeType.RANKER:
            return _diag_ranker(node, pd, gt)
        case NodeType.ENRICHER:
            return _diag_enricher(node, pd, gt)
        case NodeType.CACHE:
            return _diag_cache(node, pd, gt)
        case _:
            return None


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
    # reference ``l1_diversity`` via campaign.json::scoring / scoring_steer.json.
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


def matched_origin_stats(
    origin_results: list[QueryMeasurement],
    candidate_results: list[QueryMeasurement],
    pipeline_schema: PipelineSchema,
    *,
    round_scorer: RoundScorer | str | None = None,
) -> dict[str, Any] | None:
    """``None`` unless the candidate measured EVERY cell the origin did. Pairing does not rescue a truncated prefix — the
    shared cells ARE the incumbent's failures, so both halves are conditioned on what selected the subset."""
    origin_sids = {r.get("sample_id") for r in origin_results}
    if not origin_sids or not origin_sids <= {r.get("sample_id") for r in candidate_results}:
        return None
    # `compute_composite_fitness` already spreads `_compute_accuracy` into its result —
    # calling it again here was a second `is_deprecated` walk over the same rows for the same
    # numbers, and a second place for the two to disagree.
    composite = compute_composite_fitness(
        origin_results,
        pipeline_schema,
        opt_sp=None,
        round_scorer=round_scorer,
        l1_diversity=1.0,
    )
    return {key: composite[key] for key in ("accuracy", "total", "composite_fitness")}


# ---------------------------------------------------------------------------
# Round-winner election — difficulty-adjusted ability (θ) ranking shared by the
# live scorer (``l1_score``) and the resume divergence replayer. ONE rule, two
# callers: a resumed run can never re-elect a different winner under an unchanged
# scorer. ``paired_fitness`` remains the origin-overlap guard + the recorded
# p_value diagnostic in ``l1_score``; the *ranking* is θ, not its mean.
# ---------------------------------------------------------------------------


def _mean_fitness_by_cell(rows: list[QueryMeasurement]) -> dict[Any, float]:
    """Un-predicated ON PURPOSE — this is the origin-overlap population, not the display one, and
    ``elect_round_winner`` relies on an errored row counting as a 0.0 cell. Do not add the filter."""
    acc: dict[Any, list[float]] = {}
    for r in rows:
        sid = r.get("sample_id")
        if sid is not None:
            acc.setdefault(sid, []).append(float(r.get("fitness", 0.0) or 0.0))
    return {sid: sum(v) / len(v) for sid, v in acc.items()}


def _distinct_valid_cells(results: list[QueryMeasurement]) -> int:
    """Counted per CELL, not per row, so replicate rows can never falsely satisfy
    ``coverage_floor``; a cell with one errored and one clean row still counts."""
    from promptpotter.application.optimization.pobb.classification import is_deprecated

    return len(
        {
            r.get("sample_id")
            for r in results
            if not is_deprecated(r) and not is_error_result(r) and r.get("sample_id") is not None
        }
    )


def paired_fitness(
    candidate_results: list[QueryMeasurement],
    origin_results: list[QueryMeasurement],
) -> tuple[list[float], list[float]]:
    """The matched pairs the round-significance test runs on. Sorted by ``sample_id`` so a replay
    is deterministic."""
    cand_by_sid = _mean_fitness_by_cell(candidate_results)
    origin_by_sid = _mean_fitness_by_cell(origin_results)
    cand_fit: list[float] = []
    origin_fit: list[float] = []
    for sid in sorted(cand_by_sid.keys() & origin_by_sid.keys(), key=lambda s: (s is None, s)):
        cand_fit.append(cand_by_sid[sid])
        origin_fit.append(origin_by_sid[sid])
    return cand_fit, origin_fit


def elect_round_winner(
    candidate_ids: list[str],
    results_by_id: Mapping[str, list[QueryMeasurement]],
    origin_results: list[QueryMeasurement],
    coverage_floor: int,
    delta_scale: Ruler,
) -> tuple[str, RaschPosterior]:
    """The rank key is the POINT-ESTIMATE lift, with no winner's-curse margin — under-probing is ``coverage_floor``'s job.
    The overlap guard and the θ-lift guard cover different holes: one grades an errored row 0.0, the fit drops it."""
    from promptpotter.application.intelligence.exploration import (
        candidate_abilities,
        theta_lift_over_origin,
    )

    abilities = candidate_abilities(
        {cid: list(results_by_id.get(cid) or []) for cid in candidate_ids},
        origin_results,
        delta_scale,
    )

    best_rank: tuple[float, int] = (0.0, 0)
    winner_id = ""
    for cid in candidate_ids:
        cand_results = list(results_by_id.get(cid) or [])
        n_cells = _distinct_valid_cells(cand_results)
        if n_cells < coverage_floor:
            continue
        cand_fit, _ = paired_fitness(cand_results, origin_results)
        if not cand_fit:
            continue
        # Rank by the difficulty-adjusted ability lift POINT ESTIMATE — a candidate strictly
        # above origin wins, no winner's-curse SE margin required. The prior `- theta_se` shrink
        # discarded genuinely-better candidates whenever the posterior was wide (thin per-round
        # budgets ⇒ theta_se can dwarf a real gain), so the loop never compounded a discovered
        # improvement — the next round re-explored origin instead of building on the better
        # candidate. Under-probing is already guarded independently by `coverage_floor` above
        # (a candidate below it never reaches here), so dropping the SE shrink cannot let a thin
        # fluke win — only fully-probed candidates compete, best point-estimate θ takes it.
        # The floor counts EVIDENCE cells (non-errored, the θ fit's own population), not
        # attempted cells — that is what licenses the no-SE-margin rank.
        # `None` = this candidate or the origin was never fit; there is no lift to rank on.
        lift = theta_lift_over_origin(abilities, cid)
        if lift is None:
            continue
        rank = (lift, n_cells)
        if rank > best_rank:
            best_rank = rank
            winner_id = cid
    return winner_id, abilities


def elimination_p_best(
    candidate_grades: Sequence[float],
    paired_prior_grades: Mapping[str, Sequence[float]],
    candidate_sample_ids: Sequence[int],
    delta_scale: Ruler,
) -> tuple[float, dict[str, float]]:
    """Scores on the SAME θ the round-winner election ranks by, so elimination and election cannot
    disagree on what better means. Closed-form, so the resume replayer re-derives the cut exactly."""
    if not paired_prior_grades:
        return 1.0, {}

    import math

    from scipy.stats import norm

    from promptpotter.application.intelligence.exploration import Observation, fit_theta_given_delta

    sids = [int(s) for s in candidate_sample_ids]
    cand_obs = [
        Observation("__cand__", sid, float(g))
        for sid, g in zip(sids, candidate_grades, strict=True)
    ]
    theta_c, se_c = fit_theta_given_delta(cand_obs, delta_scale).get("__cand__", (0.0, 0.0))

    per_prior: dict[str, float] = {}
    for pid, grades in paired_prior_grades.items():
        prior_obs = [Observation(pid, sid, float(g)) for sid, g in zip(sids, grades, strict=True)]
        theta_p, se_p = fit_theta_given_delta(prior_obs, delta_scale).get(pid, (0.0, 0.0))
        denom = math.sqrt(se_c * se_c + se_p * se_p)
        if denom > 1e-12:
            per_prior[pid] = float(norm.cdf((theta_c - theta_p) / denom))
        else:
            per_prior[pid] = 1.0 if theta_c > theta_p else 0.0
    return min(per_prior.values()), per_prior


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
