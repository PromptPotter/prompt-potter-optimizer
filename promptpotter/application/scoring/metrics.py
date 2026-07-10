"""Scoring and metric computation for measurement results.

Driven by the evaluator registry in ``application/scoring/evaluators.py``.
The per-round composite_fitness is whatever the dataset's per-round scoring formula
resolves to; when no formula is set, the default formula is plain ``accuracy``
(``default_per_round_formula``).

Pure computation — no I/O, no backend dependencies.
"""

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
    from promptpotter.domain.pipeline_schema import (
        PipelineNode,
        PipelineSchema,
    )
    from promptpotter.domain.scoring import QueryMeasurement


__all__ = [
    "Evaluator",
    "all_evaluators",
    "binom_sf",
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
    """Return 1-based rank of *ground_truth* in *items*, or None."""
    if not items or not ground_truth:
        return None
    for i, c in enumerate(items):
        if extract_item_label(c) == ground_truth:
            return i + 1
    return None


def _compute_accuracy(results: list[QueryMeasurement]) -> dict[str, Any]:
    """Base scalars: hits, total, accuracy, errors, deprecated.

    Deprecated samples (those whose ``classify_result()`` returns any fatal
    code) are not valid measurements and are excluded from ``hits``,
    ``total``, ``errors``, and the accuracy denominator. Their count
    surfaces as ``deprecated`` for operator transparency.

    Kept as a thin function (not part of the registry) because several
    consumers read ``hits`` / ``total`` directly.
    """
    from promptpotter.application.optimization.pobb.elimination import is_deprecated

    deprecated = sum(1 for r in results if is_deprecated(r))
    valid = [r for r in results if not is_deprecated(r)]
    total = len(valid)
    hits = sum(1 for r in valid if r.get("hit"))
    errors = sum(1 for r in valid if is_error_result(r))
    # Single source for the mean-fitness-over-non-deprecated formula.
    accuracy = compute_accuracy(results=results)
    return {
        "hits": hits,
        "total": total,
        "accuracy": accuracy,
        "errors": errors,
        "deprecated": deprecated,
    }


def composite_ci(results: list[QueryMeasurement]) -> tuple[float | None, float | None]:
    """95% normal-CLT CI on the mean per-cell composite ``fitness`` — the always-on
    whisker every scored candidate carries so no composite point estimate stands alone.

    The **single home** for the CI idiom: every stamping site (``l1_score`` for L1
    candidates, ``emit_origin_round`` for C0) routes through here. It reads the per-sample
    ``fitness`` through the SAME ``_mean_fitness_by_cell`` the decision metrics use (θ /
    ``paired_fitness``), so the CI and the decision can never disagree on what a scoreless
    row is worth (both: 0.0) — and replicate draws of one cell collapse to that cell's mean,
    so the CI's independent unit is the sample, not the re-draw (identity at the ``rep_k=0``
    default). ``(None, None)`` when no cell was measured — nothing to bracket.
    """
    from promptpotter.shared.statistics import mean_ci

    per_cell = list(_mean_fitness_by_cell(results).values())
    if not per_cell:
        return (None, None)
    _, ci_lo, ci_hi = mean_ci(per_cell)
    return (ci_lo, ci_hi)


def count_degraded_samples(results: Sequence[Mapping[str, Any]]) -> int:
    """Count samples that have pipeline degradation warnings."""
    return sum(1 for r in results if has_pipeline_warnings(r))


# ---------------------------------------------------------------------------
# Per-sample diagnostics — typed mixed values (bool/int/str/None), keyed off
# ``PipelineNode.node_type``.
# ---------------------------------------------------------------------------


def extract_sample_diagnostics(
    result: Mapping[str, Any],
    pipeline_schema: PipelineSchema,
) -> dict[str, float | bool | int | str | None]:
    """Extract per-sample diagnostic signals; per-sample complement to ``compute_composite_fitness``."""
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
    """Shared shape for candidate_source + ranker diagnostics. Diagnostics report the
    ground-truth position 0-based; ``find_rank`` is the canonical 1-based walk."""
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
    return {
        "gt_in_ranked": pos is not None,
        "n_final_ranking": len(candidates),
        "gt_rank": pos,
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
    """Per-node diagnostic extractor — None for node types without one. Explicit match so
    ``grep _diag_ranker`` lands on the call site (no string-keyed dispatch table)."""
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
    opt_sp: Any = None,
    round_scorer: RoundScorer | str | None = None,
    l1_diversity: float = 1.0,
) -> dict[str, Any]:
    """Compute round-level metrics from the evaluator registry.

    Every per-round evaluator whose ``applies(schema)`` is True is
    materialized into a flat ``evaluators`` dict; names are namespaced
    when multiple nodes of the same type exist. The composite_fitness score is
    the result of evaluating ``round_scorer`` against that namespace.

    - ``round_scorer`` can be a compiled callable (via
      ``domain.scoring.compile_round_scorer``), a formula string, or
      ``None``. ``None`` uses the default formula produced by
      ``default_per_round_formula(schema)`` — plain ``accuracy``
      (no latency/recall/self-heal blend; degradation is gated separately).
    - ``opt_sp`` is an optional ``OptSearchPoint``; when provided, its
      ``memory.validation_failures`` forces composite_fitness to 0.0 (structurally
      invalid candidates), and ``memory.runtime_failures`` feeds the
      ``runtime_failure_rate`` evaluator.
    - ``l1_diversity`` is the round-level fraction of valid (non-no-op,
      non-duplicate) L1 variants; defaults to 1.0 for non-L1 calls.

    The composite_fitness is **recorded, not gating**: the round-winner
    election compares candidates on difficulty-adjusted ability (``theta`` from
    the joint Rasch fit, ``elect_round_winner``), which stays comparable when
    each candidate is scored on a different signal-chased subset. ``accuracy``
    and ``composite_fitness`` are subset-relative display numbers, persisted so
    operators can see whether a win came with hidden costs.
    """
    base = _compute_accuracy(results)
    evaluator_values = materialize_round_values(pipeline_schema, results, opt_sp=opt_sp)
    # L1-generation quality is a batch property, not a per-result derivation —
    # injected after registry materialization so operator formulas can
    # reference ``l1_diversity`` via campaign.json::scoring / scoring_steer.json.
    evaluator_values["l1_diversity"] = float(l1_diversity)

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
) -> dict[str, Any]:
    """Origin's accuracy/hits/composite restricted to the candidate's measured samples.

    When PoBB leader-locks a candidate at q8/20, returns origin's stats on
    only those 8 samples — the apples-to-apples comparison PoBB's matched-pair
    posterior is built on. Degenerates to full origin stats when the candidate
    measured every sample. ``opt_sp=None`` for the origin composite so
    opt_sp-aware evaluators (e.g. ``prompt_compactness``) take their vacuous
    fallback in both numerator and denominator of the delta.
    """
    candidate_sids = {r.get("sample_id") for r in candidate_results}
    matched = [r for r in origin_results if r.get("sample_id") in candidate_sids]
    # `compute_composite_fitness` already spreads `_compute_accuracy(matched)` into its result —
    # calling it again here was a second `is_deprecated` walk over the same rows for the same
    # numbers, and a second place for the two to disagree.
    composite = compute_composite_fitness(
        matched,
        pipeline_schema,
        opt_sp=None,
        round_scorer=round_scorer,
        l1_diversity=1.0,
    )
    return {key: composite[key] for key in ("accuracy", "hits", "total", "composite_fitness")}


# ---------------------------------------------------------------------------
# Round-winner election — difficulty-adjusted ability (θ) ranking shared by the
# live scorer (``l1_score``) and the resume divergence replayer. ONE rule, two
# callers: a resumed run can never re-elect a different winner under an unchanged
# scorer. ``paired_fitness`` remains the origin-overlap guard + the recorded
# p_value diagnostic in ``l1_score``; the *ranking* is θ, not its mean.
# ---------------------------------------------------------------------------


def _mean_fitness_by_cell(rows: list[QueryMeasurement]) -> dict[Any, float]:
    """``{sample_id: mean composite fitness}``, collapsing REPLICATE rows (same
    ``sample_id``, multiple measurements under ``replicate_survivors``) to their per-cell
    mean. At the n=1 default this is the identity — one row per cell. A degraded sample
    with no recorded fitness contributes 0 (the score it earned), not a dropped row.
    """
    acc: dict[Any, list[float]] = {}
    for r in rows:
        sid = r.get("sample_id")
        if sid is not None:
            acc.setdefault(sid, []).append(float(r.get("fitness", 0.0) or 0.0))
    return {sid: sum(v) / len(v) for sid, v in acc.items()}


def _distinct_valid_cells(results: list[QueryMeasurement]) -> int:
    """Distinct non-deprecated cells (``sample_id``) a candidate was measured on — the
    coverage notion under ``replicate_survivors``: k replicates of one cell count once, so
    replication can never falsely satisfy ``coverage_floor``. Identity with row count at n=1.
    """
    from promptpotter.application.optimization.pobb.elimination import is_deprecated

    return len(
        {
            r.get("sample_id")
            for r in results
            if not is_deprecated(r) and r.get("sample_id") is not None
        }
    )


def paired_fitness(
    candidate_results: list[QueryMeasurement],
    origin_results: list[QueryMeasurement],
) -> tuple[list[float], list[float]]:
    """Per-cell mean composite fitness for the candidate and origin on the SAME cells,
    aligned by ``sample_id``. The matched pairs the round-significance test runs on — origin's
    fitness restricted to whatever subset the online picker scored the candidate on. Replicate
    rows per cell are averaged first (``_mean_fitness_by_cell``), so one paired point per shared
    cell regardless of replication depth; sorted by ``sample_id`` for replay determinism.
    """
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
    """Elect the round winner: rank candidates by difficulty-adjusted ability lift over the
    origin, tie-broken toward higher coverage. Every candidate **and** the origin gets θ on
    the cycle's **fixed δ ruler** ``delta_scale`` (``candidate_abilities`` → ``fit_theta_given_delta``,
    flat where the ruler is cold), so all arms share one cross-round-comparable scale; the rank
    key is the **point-estimate** lift ``(θ_cand − θ_origin)`` — a candidate strictly above
    origin wins, NO winner's-curse SE margin. (The prior ``− θ_se`` LCB shrink discarded
    genuinely-better candidates whenever the θ posterior was wide — thin per-round budgets make
    ``θ_se`` dwarf a real gain — so the loop never compounded a discovered improvement. Under-
    probing is guarded independently by ``coverage_floor``, so no SE margin is needed to keep a
    thin fluke out.) Origin is the floor at rank ``(0.0, 0)`` — only a candidate above origin
    (lift > 0) with at least ``coverage_floor`` measured samples can win. Returns
    ``("", abilities)`` when none clears the floor.

    Returns ``(winner_id, abilities)`` — the fixed-ruler ``RaschPosterior`` rides out so the
    caller stamps each candidate's θ onto its display row from the SAME fit the decision was
    made on (no second fit), letting the operator see *why* a lower-accuracy candidate won.

    This is the cross-candidate comparison that drifts under per-round resubset: with each
    candidate on a different signal-chased subset, raw subset accuracy is difficulty-blind, so
    the candidate handed the easier samples wins on paper. θ is subset-invariant and crowns the
    genuinely abler candidate.

    Two guards, and they cover different holes. ``paired_fitness`` is the origin-**overlap**
    guard: a candidate sharing no sample with origin cannot win, else it would beat the floor on
    rows the incumbent never ran. It does *not* guard the origin's ability, because it grades an
    errored row as a 0.0 cell while the θ fit drops that row entirely — so an all-errored origin
    still yields overlap. ``theta_lift_over_origin`` is the guard for that: no fitted origin θ,
    no lift, no winner. Pure + deterministic in its inputs, so the resume replayer re-elects the
    same winner under an unchanged scorer.
    """
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
        # `None` = this candidate or the origin was never fit; there is no lift to rank on.
        lift = theta_lift_over_origin(abilities, cid)
        if lift is None:
            continue
        rank = (lift, n_cells)
        if rank > best_rank:
            best_rank = rank
            winner_id = cid
    return winner_id, abilities


def binom_sf(n: int, k: int, p: float) -> float:
    """``P(X >= k)`` for ``X ~ Binomial(n, p)`` — exact survival, ``n`` small (≤ the
    per-round sample budget). Shared by the live paired-margin gate
    (``PoBBCheck._margin_stats``) and its resume replayer so both re-derive the
    futility cut bit-for-bit (the same live/replay-determinism contract that keeps
    ``elimination_p_best`` closed-form).
    """
    if k <= 0:
        return 1.0
    if k > n:
        return 0.0
    from math import comb

    q = 1.0 - p
    return sum(comb(n, j) * p**j * q ** (n - j) for j in range(k, n + 1))


def elimination_p_best(
    candidate_grades: Sequence[float],
    paired_prior_grades: Mapping[str, Sequence[float]],
    candidate_sample_ids: Sequence[int],
    delta_scale: Ruler,
) -> tuple[float, dict[str, float]]:
    """``P(candidate is the round's best)`` for PoBB mid-round elimination, on
    difficulty-adjusted ability θ — the SAME quality metric the round-winner election ranks by,
    so elimination and election never disagree on what "better" means (the boundary collapse).

    Inputs are GRADED per-sample responses (fitness clamped to [0,1], ``graded_response``) —
    the logistic MAP is valid for any y ∈ [0,1], so binary datasets are bit-identical to the
    old hit vectors while graded backends (L4 outer proxies, reciprocal-rank matching) keep
    their gradient instead of collapsing to an all-miss θ.

    Candidate and each prior get θ **independently on the cycle's fixed δ ruler**
    (``fit_theta_given_delta`` keyed by the candidate's real ``sample_id``s — the priors are
    already aligned to exactly those samples by the caller), flat (δ=0) where the ruler is cold.
    The ruler is the one the round-winner election reads, so elimination and election share a
    single scale instead of PoBB's old per-call joint fit. Per prior the closed-form
    ``P(θ_cand > θ_prior) = Φ(Δθ / √(se_c² + se_p²))``; ``p_best = min`` over priors — the same
    "bounded above by the hardest prior" criterion the paired-fitness rule used.

    Returns ``(p_best, {prior_id: P(cand > prior)})``; empty priors → ``(1.0, {})`` (no prior to
    lose to). Deterministic + closed-form (no Monte Carlo) — so the resume divergence replayer
    re-derives the elimination cut bit-for-bit.
    """
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
    """A candidate's round value under an alternative scoring criterion, recomputed
    from its **stored, already-materialized evaluator namespace** — no schema, no
    re-run. ``None`` when the criterion names an evaluator absent from this record's
    namespace (unscorable under this mask — *not* a fabricated score).

    The single re-evaluation seam the scoring **mask** verdict routes through — the
    mask layer owns no scoring math of its own, it asks here. The round score is a
    formula over the per-round evaluator values (``accuracy``, ``latency_norm``,
    ``*_recall``, ``prompt_compactness``, the self-heal rates, …); a *scoring swap* is
    that same evaluator namespace under a different formula. Because the realized
    ``composite_fitness`` was itself ``realized_formula(evaluators)``, feeding the
    realizing criterion here reproduces it **exactly** — the mask's self-consistency
    gate holds by construction, and the read path needs no ``PipelineSchema`` (which
    is never persisted). ``criterion`` is a formula string (e.g. ``"accuracy"``), a
    compiled ``RoundScorer``, or ``None`` (the round-scorer default = accuracy-only).

    Missing-name resolution lives **here, once** — the only place the mask scores a
    record under a criterion the record may not satisfy. A schema-bound evaluator
    (``*_recall`` on a pipeline with no such node) genuinely doesn't apply to that
    record; ``ScoringTermMissingError`` becomes ``None`` (the caller treats it like a
    missing candidate, claims no divergence). Every OTHER scoring error still propagates,
    and the live round scorer stays fail-loud — there the namespace is materialized fresh,
    so a missing name is a broken formula.
    Row-derivable evaluators are recomputed into every record's namespace upstream
    (``load._candidates``), so this path only fires for genuinely-absent schema-bound
    names, never for a stale record missing a newer row-derivable evaluator.
    """
    scorer = criterion if callable(criterion) else compile_round_scorer(criterion)
    try:
        return float(scorer(dict(evaluators)))
    except ScoringTermMissingError:
        return None
