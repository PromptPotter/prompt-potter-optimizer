"""C2 — Statistical / numerical correctness.

The math the optimizer trusts: per-dataset scorer formulas, the evaluator
registry + composite fitness, the Rasch (IRT) fit + decision-information sample
picker, the Bayesian Posterior-of-Being-Best gate, and the L1/L2/L3 output
validators that score a proposal's conformance. Every assertion is wrong-reveal
— it fails when the formula is wrong, not when a wrapper is renamed.

Sections (Pass A — scorers, evaluators, IRT):
  1. Scorer matcher formulas (one parametrized family).
  2. ``compile_scorer`` — AST allowlist + known-formula compile.
  3. Evaluator registry + composite fitness + matched-origin subset.
  4. Rasch fit + decision-information round subset.
  5. Measurement rerun short-circuit + refusal classification.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import yaml

from promptpotter.application.intelligence.exploration import (
    Observation,
    adopted_level_trajectory,
    fit_rasch,
    fit_rasch_2pl,
    fit_theta_given_delta,
    graduate_ruler_model,
    ruler_expected_accuracy,
    select_round_subset,
)
from promptpotter.application.mask.backprop import accumulate_node_stats, select_rewind_round
from promptpotter.application.mask.divergence import find_divergences
from promptpotter.application.mask.record import MaskCandidate, MaskCycle, MaskRecord, MaskRound
from promptpotter.application.mask.verdicts import make_abort_verdict, make_scoring_verdict
from promptpotter.application.optimization.pobb.classification import terminal_ranking
from promptpotter.application.scoring.evaluators import all_evaluators, materialize_round_values
from promptpotter.application.scoring.formula import (
    ScoringFormulaError,
    compile_scorer,
    extract_item_label,
)
from promptpotter.application.scoring.formula.matchers import (
    _aime_match,
    _exact_match,
    _gsm8k_match,
)
from promptpotter.application.scoring.metrics import (
    compute_composite_fitness,
    matched_origin_stats,
    value_with_mask_applied,
)
from promptpotter.domain.phases import StopReason
from promptpotter.domain.pipeline_schema import (
    NodeType,
    ObservationMapping,
    PipelineNode,
    PipelineSchema,
)
from promptpotter.domain.rendering import display_fitness, extract_display_answer
from promptpotter.domain.sample import Sample
from promptpotter.domain.search_point import JobSearchPoint
from promptpotter.shared import extract_gsm8k_number
from promptpotter.shared.statistics import mean_ci
from tests.factories import cycle_result, lost_round, round_result

# ===========================================================================
# 1. Scorer matcher formulas — one parametrized family
# ===========================================================================
# Each per-dataset matcher is a small pure function; the cases that matter are
# the format edges (last-marker-wins, bad-marker fallback, cross-format numeric
# equivalence, case-insensitivity, no-signal → 0/None). Collapsed from five
# per-function tests into one table keyed by ``(fn, args, expected)``.


@pytest.mark.parametrize(
    "fn,args,expected",
    [
        # _aime_match: last boxed wins / bad boxed → fallback / no numbers.
        (_aime_match, (r"First: \boxed{10}. Rechecking: \boxed{42}", "42"), 1.0),
        (_aime_match, (r"\boxed{undefined} The answer is 42", "42"), 1.0),
        (_aime_match, ("no numbers", "42"), 0.0),
        # extract_gsm8k_number: comma-stripped / #### preferred / none.
        (extract_gsm8k_number, ("#### 1,234",), 1234.0),
        (extract_gsm8k_number, ("I calculated 99 but #### 42",), 42.0),
        (extract_gsm8k_number, ("no numbers",), None),
        # _gsm8k_match: cross-format numeric equivalence / mismatch.
        (_gsm8k_match, ("42.0", "#### 42"), 1.0),
        (_gsm8k_match, ("#### 99", "#### 42"), 0.0),
        # _exact_match: last bold wins (case-insensitive) / no-marker / mismatch.
        (_exact_match, ("First try **No**. Corrected: **Yes**", "yes"), 1.0),
        (_exact_match, ("plain text answer", "Plain Text Answer"), 1.0),
        (_exact_match, ("foo", "bar"), 0.0),
        # extract_display_answer: bold / boxed / bare-strip (formula-aware).
        (
            extract_display_answer,
            ("The answer is **Disproved**.", "exact_match(predicted, ground_truth)"),
            "Disproved",
        ),
        (
            extract_display_answer,
            (r"Working… \boxed{17}", "aime_match(predicted, ground_truth)"),
            "17",
        ),
        (extract_display_answer, ("  padded  ", None), "padded"),
    ],
)
def test_matcher_formula(fn, args, expected):
    assert fn(*args) == expected


# ===========================================================================
# 2. ``compile_scorer`` — AST allowlist + known-formula compile
# ===========================================================================


def _result_min(predicted: str, ground_truth: str) -> dict:
    return {
        "query": "q",
        "predicted": predicted,
        "ground_truth": ground_truth,
        "hit": False,
        "fitness": 0.0,
        "error": None,
        "pipeline_data": None,
    }


def test_compile_scorer_routes_to_named_function():
    aime = compile_scorer("aime_match(predicted, ground_truth)")
    assert aime(_result_min(r"\boxed{42}", "42")) == 1.0
    gsm = compile_scorer("gsm8k_match(predicted, ground_truth)")
    assert gsm(_result_min("6 * 7 = 42. The answer is 42.", "#### 42")) == 1.0


@pytest.mark.parametrize(
    "formula",
    [
        "().__class__",
        "__import__('os').system('echo pwn')",
        "(lambda: 1)()",
        "[x for x in range(10)][0]",
        "predicted.__class__",
        "exact_match(predicted, ground_truth) := 1",
    ],
)
def test_compile_scorer_rejects_attribute_and_unsafe_syntax(formula: str) -> None:
    """Restricted-eval is bypassable; the AST allowlist is the real boundary."""
    with pytest.raises((ValueError, SyntaxError)):
        compile_scorer(formula)


def test_scorer_raises_loud_on_formula_trace_mismatch() -> None:
    """Silent-harm: a formula referencing a field the trace lacks, or returning a
    non-numeric, MUST halt — the prior swallow-to-0.0 zeroed a whole campaign's
    fitness indistinguishably from genuinely-wrong answers."""
    # ``missing_field`` is not in the per-sample namespace → NameError under eval.
    missing = compile_scorer("missing_field + hit")
    with pytest.raises(ScoringFormulaError):
        missing(_result_min("a", "a"))
    # A formula that evaluates to a non-numeric must also fail loud, not score 0.
    non_numeric = compile_scorer("ground_truth")
    with pytest.raises(ScoringFormulaError):
        non_numeric(_result_min("a", "not-a-number"))


def test_scorer_rejects_non_finite_instead_of_scoring_it_perfect() -> None:
    # SILENT wrong-score, and it INVERTS. Python's `min` short-circuits on its first argument,
    # so `min(1.0, nan)` is `1.0` and the clamp `max(0.0, min(1.0, nan))` returns 1.0 — a NaN
    # scored a PERFECT sample. An unmeasured proxy or an empty denominator anywhere in a
    # composite formula therefore read as a flawless answer. `inf` clamps to 1.0 the same way.
    assert max(0.0, min(1.0, float("nan"))) == 1.0  # the trap, pinned
    for expr in ("1e400", "-1e400", "1e400 - 1e400"):
        with pytest.raises(ScoringFormulaError, match="non-finite"):
            compile_scorer(expr)(_result_min("a", "a"))

    # The reachable shape: a non-finite value arriving through `pipeline_data` — how an L4
    # proxy would deliver one — not a literal a human would notice in the formula string.
    result = _result_min("a", "a") | {"pipeline_data": {"after_N_rounds_delta": float("nan")}}
    with pytest.raises(ScoringFormulaError, match="non-finite"):
        compile_scorer("after_N_rounds_delta")(result)

    # The per-ROUND scorer clamps identically and was the twin hole: the fix to the per-sample
    # clamp above left the composite one wide open, so a NaN evaluator scored a perfect ROUND.
    # Both now pass through one `clamp_unit_score`; this pins that they cannot drift apart again.
    from promptpotter.application.scoring.formula import compile_round_scorer

    with pytest.raises(ScoringFormulaError, match="non-finite"):
        compile_round_scorer("accuracy")({"accuracy": float("nan")})


def test_an_unmeasured_term_is_never_scored_as_zero() -> None:
    # SILENT wrong-score. Every empty-collection aggregate in the evaluator registry used to
    # return a PERFECT value — no rows meant "no errors" (0.0), "instant" (1.0), "maximally
    # compact" (1.0). The registry now omits the key, so a formula that names an unmeasured term
    # halts instead of scoring the round on a number nobody computed. The distinction matters:
    # a round that measured every sample and failed them all IS a 0.0; a round that measured
    # nothing is not.
    from promptpotter.application.scoring.evaluators import (
        compute_accuracy,
        compute_error_rate,
        compute_latency_norm,
        compute_output_compactness,
    )
    from promptpotter.application.scoring.formula import (
        ScoringTermMissingError,
        compile_round_scorer,
    )

    assert compute_accuracy(results=[]) is None
    assert compute_error_rate(results=[]) is None
    assert compute_latency_norm(results=[]) is None
    assert compute_output_compactness(results=[]) is None

    # All samples measured, all fatally deprecated → a verdict of 0.0, not an absence.
    deprecated = _result_min("q", "a") | {"error": "SCHEMA_VALIDATION_FAILED", "fitness": 0.0}
    assert compute_accuracy(results=[deprecated]) == 0.0

    # An errored row is the ABSENCE of a verdict — excluded from the mean, never a silent
    # 0.0 dragging a real score down (an L4 inner campaign dying of a timeout must not
    # halve its optimizer prompt's accuracy)...
    scored = _result_min("q", "a") | {"hit": True, "fitness": 1.0}
    errored = _result_min("ERROR", "a") | {"error": "boom", "error_category": "UNKNOWN"}
    del errored["fitness"], errored["hit"]  # real error rows carry neither
    assert compute_accuracy(results=[scored, errored]) == 1.0
    # ...while it still surfaces on the error channel, counted over ALL rows.
    assert compute_error_rate(results=[scored, errored]) == 0.5

    # The default composite (`accuracy`) refuses a round it cannot score, rather than 0.0.
    with pytest.raises(ScoringTermMissingError, match="accuracy"):
        compile_round_scorer(None)({"latency_norm": 0.9})
    with pytest.raises(ScoringTermMissingError, match="latency_norm"):
        compile_round_scorer("0.5 * accuracy + 0.5 * latency_norm")({"accuracy": 0.9})

    # But the GATEWAY over an EMPTY round is defined, not a crash: an operator skip at query
    # 0/N (or an all-excluded round) hands ``compute_composite_fitness`` no rows. It records the
    # 0.0 floor with ``total`` 0 — the no-evidence marker that keeps the candidate out of winner
    # election — rather than run the fail-loud scorer and halt the whole cycle.
    empty = compute_composite_fitness([], _single_node_schema(), opt_sp=None)
    assert empty["composite_fitness"] == 0.0
    assert empty["accuracy"] == 0.0
    assert empty["total"] == 0


def test_compile_scorer_accepts_known_formulas() -> None:
    """Production formulas across datasets must still compile after AST validation."""
    for f in (
        "exact_match(predicted, ground_truth)",
        "gsm8k_match(predicted, ground_truth)",
        "aime_match(predicted, ground_truth)",
        "hockeystick(rr(ground_truth_rank), 0.2)",
        "0.7 * hit + 0.3 * (1.0 - input_tokens / 1000)",
    ):
        assert compile_scorer(f) is not None


# ===========================================================================
# 3. Evaluator registry + composite fitness + matched-origin subset
# ===========================================================================


def _eval_result(
    *,
    hit: bool = True,
    score: float = 1.0,
    total_time: float = 100.0,
    error: str | None = None,
    final_ranking: list | None = None,
    candidate_ranking: list | None = None,
    step_timings: dict | None = None,
    diagnostics: dict | None = None,
    ground_truth: str = "gt",
    predicted: str = "gt",
) -> dict:
    pd: dict = {"total_time": total_time}
    if final_ranking is not None:
        pd["final_ranking"] = final_ranking
    if candidate_ranking is not None:
        pd["candidate_ranking"] = candidate_ranking
    if step_timings is not None:
        pd["step_timings"] = step_timings
    if diagnostics is not None:
        pd["diagnostics"] = diagnostics
    return {
        "query": "q",
        "predicted": predicted,
        "ground_truth": ground_truth,
        "hit": hit,
        "fitness": score,
        "error": error,
        "pipeline_data": pd,
    }


def _single_node_schema() -> PipelineSchema:
    """Minimal schema with one generic node and no node_type assignments."""
    return PipelineSchema(
        name="test", nodes=[PipelineNode(name="llm_only", node_type=NodeType.NONE)]
    )


def _recall_schema() -> PipelineSchema:
    """Schema with candidate_source + ranker + cache — exercises all recall evaluators."""
    return PipelineSchema(
        name="test_recall",
        nodes=[
            PipelineNode(name="cache_lookup", node_type=NodeType.CACHE),
            PipelineNode(
                name="fuzzy",
                node_type=NodeType.CANDIDATE_SOURCE,
                observation_mappings=[
                    ObservationMapping(
                        pipeline_key="candidate_ranking", output_field="candidate_ranking"
                    )
                ],
            ),
            PipelineNode(
                name="ranker",
                node_type=NodeType.RANKER,
                observation_mappings=[
                    ObservationMapping(pipeline_key="final_ranking", output_field="final_ranking")
                ],
            ),
        ],
    )


def test_registry_scopes_are_valid():
    """Every registered evaluator declares a known scope."""
    names = {ev.name for ev in all_evaluators()}
    assert {"accuracy", "error_rate", "latency_norm", "source_recall"}.issubset(names)
    for ev in all_evaluators():
        assert ev.scope in ("per_sample", "per_round")


def test_materialize_recall_only_emits_for_typed_nodes():
    """Recall evaluators only materialize when the schema has a candidate_source/ranker."""
    single = materialize_round_values(_single_node_schema(), [_eval_result(score=1.0)])
    assert "source_recall" not in single

    schema = _recall_schema()
    values = materialize_round_values(
        schema,
        [
            _eval_result(
                final_ranking=[{"candidate": "gt"}],
                candidate_ranking=[{"candidate": "gt"}],
                step_timings={"cache_lookup": 5.0, "fuzzy": 10.0, "ranker": 20.0},
            ),
            _eval_result(
                final_ranking=[{"candidate": "x"}],
                candidate_ranking=[{"candidate": "gt"}],
                step_timings={"cache_lookup": None, "fuzzy": 10.0, "ranker": 20.0},
            ),
        ],
    )
    assert values["source_recall"] == pytest.approx(1.0)
    assert values["candidate_recall"] == pytest.approx(0.5)
    assert "cache_hit_rate" in values


def test_terminal_ranking_sources_the_prediction():
    """The prediction is the head of the LAST ranker/candidate_source node that emitted
    its key — final_ranking when the ranker ran, else the candidate pool. An empty
    terminal list is a verdict (NO_RESULT), not a fall-through to the earlier pool."""
    schema = (
        _recall_schema()
    )  # order: cache_lookup, fuzzy(candidate_ranking), ranker(final_ranking)

    def r(pd: dict) -> dict:
        return {"pipeline_data": pd}

    # Both keys present → the later ranker's final_ranking wins over the earlier pool.
    assert terminal_ranking(
        r(
            {
                "candidate_ranking": [{"candidate": "pool"}],
                "final_ranking": [{"candidate": "ranked"}],
            }
        ),
        schema,
    ) == [{"candidate": "ranked"}]
    # Token-terminal shape (no final_ranking) → the candidate pool IS the result ranking.
    assert terminal_ranking(r({"candidate_ranking": [("Nylon 6-6", 0.5)]}), schema) == [
        ("Nylon 6-6", 0.5)
    ]
    # Ranker ran but found nothing → [] (NO_RESULT), no fall-through to the pool.
    assert (
        terminal_ranking(
            r({"candidate_ranking": [{"candidate": "pool"}], "final_ranking": []}), schema
        )
        == []
    )
    # No ranking keys / no schema → [].
    assert terminal_ranking(r({"total_time": 1.0}), schema) == []
    assert terminal_ranking(r({"final_ranking": [{"candidate": "x"}]}), None) == []
    # Shape-agnostic head extraction: a (name, score) tuple item yields the name.
    assert extract_item_label(("Nylon 6-6", 0.5)) == "Nylon 6-6"


def test_composite_fitness_matches_default_formula():
    # The default formula is plain ``accuracy`` — composite_fitness must equal accuracy
    # so the decision metric and the headline number agree (no latency/recall/self-heal
    # blend inflating it above the real score). Degradation is gated by the round health
    # block, not folded into fitness.
    schema = _single_node_schema()
    results = [_eval_result(score=1.0, total_time=100), _eval_result(score=0.0, total_time=200)]
    scored = compute_composite_fitness(results, schema, opt_sp=None)
    assert scored["composite_fitness"] == pytest.approx(scored["accuracy"], abs=1e-9)
    assert scored["composite_fitness"] == pytest.approx(0.5, abs=1e-4)


def test_matched_origin_stats_restricts_to_candidate_subset():
    """Guards the round-winner gate: when PoBB leader-locks a candidate on a
    subset of samples, the comparison must use origin's stats on the SAME
    subset — not origin's full-set rate extrapolated onto the subset. The
    bug we're guarding: the AIME C1.4 case where a candidate scored 3/8 on
    the hardest samples (where origin scored 0/8) was being compared to
    origin's full 10/20 and dismissed as "no improvement"."""
    schema = _single_node_schema()
    # Origin scored 20 samples: 10 hits (samples 0-9 hit, 10-19 miss).
    origin_results = [
        {**_eval_result(hit=i < 10, score=1.0 if i < 10 else 0.0), "sample_id": i}
        for i in range(20)
    ]
    # Candidate measured only 8 of the *hardest* samples (origin's misses,
    # sample_ids 10..17) and hit 3 of them.
    candidate_results = [
        {**_eval_result(hit=i < 13, score=1.0 if i < 13 else 0.0), "sample_id": i}
        for i in range(10, 18)
    ]
    matched = matched_origin_stats(origin_results, candidate_results, schema)
    # Origin scored ZERO on samples 10-17. The faked extrapolation
    # (origin.accuracy * 8 = 0.5 * 8) would have invented lift the origin never earned.
    assert matched["total"] == 8
    assert matched["accuracy"] == 0.0
    # Degenerate case: candidate measured all 20 → matches full-set stats.
    full = matched_origin_stats(origin_results, origin_results, schema)
    assert full["total"] == 20
    assert full["accuracy"] == 0.5
    assert full["accuracy"] == pytest.approx(0.5)
    # DISJOINT subset (per_round_resubset can hand a candidate samples the origin never
    # measured): restricting yields [], which scores no `accuracy` term. This must NOT crash
    # the campaign (round scorer refuses an empty set) and must NOT invent a fake 0.0 floor
    # (which would credit improvement over an unmeasured floor). It falls back to origin's
    # FULL measured floor — a real number.
    disjoint_candidate = [
        {**_eval_result(hit=True, score=1.0), "sample_id": i} for i in range(100, 108)
    ]
    disjoint = matched_origin_stats(origin_results, disjoint_candidate, schema)
    assert disjoint["total"] == 20
    assert disjoint["accuracy"] == pytest.approx(0.5)


def test_value_with_mask_applied_reproduces_the_retired_client_whatif_math():
    """The served What-If bar value must equal what the deleted client weighted-sum
    (``correctedFromEvaluators``) produced for the same formula — else retiring the
    TS recompute silently shifts every What-If bar. The webapp's ``formulaFromWeights``
    emits ``w * t`` terms with each "low"-direction evaluator flipped to ``(1 - t)``;
    feeding that exact criterion through the one scoring operation must hand-check.

    Also the self-consistency leg: the realizing criterion reproduces the stored value,
    and a criterion naming an absent evaluator is unscorable (None, not fabricated)."""
    evaluators = {"accuracy": 0.8, "latency_norm": 0.2}
    # `formulaFromWeights({accuracy: high, latency_norm: low}, {0.7, 0.3})`:
    #   0.7 * accuracy + 0.3 * (1 - latency_norm) = 0.56 + 0.24 = 0.80
    assert value_with_mask_applied(
        evaluators, "0.7 * accuracy + 0.3 * (1 - latency_norm)"
    ) == pytest.approx(0.80)
    # Realizing criterion (plain accuracy) reproduces the stored accuracy exactly.
    assert value_with_mask_applied(evaluators, "accuracy") == pytest.approx(0.8)
    # A formula naming an evaluator this candidate lacks is unscorable, not faked.
    assert value_with_mask_applied(evaluators, "source_recall") is None


def _mask_cand(cid: str, acc: float, **kw: object) -> MaskCandidate:
    return MaskCandidate(candidate_id=cid, evaluators={"accuracy": acc}, accuracy=acc, **kw)  # type: ignore[arg-type]


def test_mask_scoring_divergence_self_consistency_and_eligibility():
    """The scoring mask's correctness backbone, in one record.

    Realized election (``winner.py``): argmax round_winner_key over {origin-anchor}
    ∪ *eligible* candidates. Round 1: origin C0=0.5, A=0.75 (winner), B=0.25, and an
    INELIGIBLE C=1.0 (escalation-aborted-style). Round 2: A carries to anchor, D=1.0
    wins.

    (a) Self-consistency + eligibility: feeding the *realizing* criterion
    (``accuracy``) reproduces every is_winner → zero divergences. C's 1.0 must NOT
    fabricate one — the verdict carries the recorded eligibility filter, not just the
    formula (the reviewer's load-bearing point).
    (b) Swap: under ``1 - accuracy`` the round-1 leader flips to B → one divergence
    at r1 naming B, and the descendant subtree (r2) is marked divergent.
    """
    r0 = MaskRound(cycle_id="cyc", round=0, candidates=[_mask_cand("C0", 0.5, is_winner=True)])
    r1 = MaskRound(
        cycle_id="cyc",
        round=1,
        anchor_evaluators={"accuracy": 0.5},
        anchor_accuracy=0.5,
        candidates=[
            _mask_cand("A", 0.75, is_winner=True),
            _mask_cand("B", 0.25),
            _mask_cand("C", 1.0, is_eligible=False),  # higher score, but excluded
        ],
    )
    r2 = MaskRound(
        cycle_id="cyc",
        round=2,
        anchor_evaluators={"accuracy": 0.75},  # A carried forward
        anchor_accuracy=0.75,
        candidates=[_mask_cand("D", 1.0, is_winner=True)],
    )
    record = MaskRecord(cycles=[MaskCycle(cycle_id="cyc", rounds=[r0, r1, r2])])

    realized = find_divergences(record, make_scoring_verdict("accuracy"))
    assert realized.divergences == []
    assert realized.divergent == []

    swapped = find_divergences(record, make_scoring_verdict("1 - accuracy"))
    assert [(d.node_key, d.alternative_candidate_id) for d in swapped.divergences] == [
        ("cyc::r1", "B")
    ]
    assert swapped.divergent == ["cyc::r2"]


def _theta_round(cycle_id: str, rnd: int, theta: float) -> MaskRound:
    return MaskRound(cycle_id=cycle_id, round=rnd, cumulative_theta=theta)


def test_mcts_backprop_does_not_double_count_a_fork_inherited_prefix():
    """The silent number: a fork's copied rounds are the SAME logical node as the parent's.

    Forking at round 2 physically copies parent rounds 0..1 into the child's dir. Counted
    naively, root r0 would see those copies as fresh descendants and its visit count would
    inflate — worse the deeper the lineage, and invisibly: the fold still returns a
    plausible number, UCB still picks *a* round, and the run still completes. Only the
    rewind is wrong.

    Tree here (canonical nodes only):
        root r0 → r1 → r2 → r3          (root's own spine)
                     ↘ fork r2 → r3      (child, cut at 2; its r0/r1 are copies)
    So r1 has 5 descendants-plus-self, and r0 has 6 — NOT 8, which is what counting the
    child's inherited r0/r1 copies would give.
    """
    root = MaskCycle(
        cycle_id="root",
        rounds=[_theta_round("root", r, t) for r, t in [(0, 0.0), (1, 0.5), (2, 0.4), (3, 0.3)]],
    )
    fork = MaskCycle(
        cycle_id="fork",
        parent_cycle_id="root",
        fork_from_round=2,
        # r0/r1 are byte-copies of root's, carried forward by the mint.
        rounds=[_theta_round("fork", r, t) for r, t in [(0, 0.0), (1, 0.5), (2, 2.0), (3, 2.4)]],
    )
    stats = accumulate_node_stats(MaskRecord(cycles=[root, fork]))

    # Only the child's OWN rounds are nodes; its inherited prefix is not re-counted.
    assert ("fork", 0) not in stats
    assert ("fork", 1) not in stats
    assert stats[("root", 0)].visits == 6  # itself + r1,r2,r3 + fork r2,r3
    assert stats[("root", 1)].visits == 5
    assert stats[("root", 2)].visits == 2  # itself + root r3 only
    assert stats[("fork", 2)].visits == 2  # itself + fork r3

    # Q is the subtree's mean θ. The fork branch is where the ability actually went.
    assert stats[("fork", 2)].q == pytest.approx((2.0 + 2.4) / 2)
    assert stats[("root", 2)].q == pytest.approx((0.4 + 0.3) / 2)
    # r1's subtree spans BOTH branches — that is what backprop is for: the parent learns
    # from a descendant it never ran itself.
    assert stats[("root", 1)].q == pytest.approx((0.5 + 0.4 + 0.3 + 2.0 + 2.4) / 5)


def test_mcts_ucb_rewinds_to_the_ancestor_whose_subtree_paid_off():
    """UCB picks the ancestor to re-expand; a root's round 0 has nowhere to go.

    root r0 → r1(θ high) → r2 → r3, and the θ collapses after r1. Stalled at r3, the
    rewind target must be r1 — the ancestor whose subtree carries the ability — not the
    adjacent r2 that merely happens to be nearest.
    """
    root = MaskCycle(
        cycle_id="root",
        rounds=[_theta_round("root", r, t) for r, t in [(0, 0.0), (1, 2.0), (2, 0.1), (3, 0.0)]],
    )
    record = MaskRecord(cycles=[root])
    assert select_rewind_round(record, cycle_id="root", current_round=3) == 1

    # Nothing above round 0 — the caller must NOT fork (a rewind to nowhere would mint a
    # duplicate cycle and burn a whole run).
    assert select_rewind_round(record, cycle_id="root", current_round=0) is None


def test_mask_abort_verdict_rides_the_same_fold():
    """The abort verdict — a different verdict (log-read, no value face) on the SAME
    fold. r1 fired lock-in, r2 fired ε-elimination. The first round a *suppressed*
    contributor fired is the divergence; the rest is the dimmed counterfactual
    subtree. Empty suppress = the realized config = zero divergences."""
    r0 = MaskRound(cycle_id="cyc", round=0, candidates=[MaskCandidate(candidate_id="C0")])
    r1 = MaskRound(
        cycle_id="cyc", round=1, candidates=[MaskCandidate(candidate_id="A", abort="lock_in")]
    )
    r2 = MaskRound(
        cycle_id="cyc", round=2, candidates=[MaskCandidate(candidate_id="B", abort="epsilon")]
    )
    record = MaskRecord(cycles=[MaskCycle(cycle_id="cyc", rounds=[r0, r1, r2])])

    realized = find_divergences(record, make_abort_verdict(frozenset()))
    assert realized.divergences == [] and realized.divergent == []

    no_lockin = find_divergences(record, make_abort_verdict(frozenset({"lock_in"})))
    assert [d.node_key for d in no_lockin.divergences] == ["cyc::r1"]
    assert no_lockin.divergent == ["cyc::r2"]

    no_eps = find_divergences(record, make_abort_verdict(frozenset({"epsilon"})))
    assert [d.node_key for d in no_eps.divergences] == ["cyc::r2"]

    no_abort = find_divergences(record, make_abort_verdict(frozenset({"epsilon", "lock_in"})))
    assert [d.node_key for d in no_abort.divergences] == ["cyc::r1"]
    assert no_abort.divergent == ["cyc::r2"]


def test_composite_fitness_zeroed_on_validation_failure():
    # A REAL OptSearchPoint, not a namespace stub. The stub carried a bare `object()`
    # where a `ValidationFailure` goes and had no `render()` — it only ever type-checked
    # because `compute_composite_fitness(opt_sp: Any)` accepted anything, and production
    # code grew a `hasattr(opt_sp, "render")` guard to tolerate it. The type is the contract.
    opt_sp = OptSearchPoint(
        memory=L2L3Memory(
            wounds=WoundChannels(
                validation_failures=[
                    ValidationFailure(
                        axis="llm_only.model", value="bad", allowed=[], reason="forbidden_axis"
                    )
                ]
            )
        )
    )
    scored = compute_composite_fitness(
        [_eval_result(score=1.0)], _single_node_schema(), opt_sp=opt_sp
    )
    assert scored["composite_fitness"] == 0.0


def test_display_fitness_keeps_honest_zero_degrades_only_on_absence():
    """The one composite-or-accuracy resolution: a real 0.0 (validation-failed candidate)
    is an honest score and must survive — degrading it to accuracy would mask the failure on
    the trend/sparkline. Only genuine absence (``None`` → no active formula) falls back."""
    assert display_fitness(0.0, 0.7) == 0.0  # honest 0 kept, NOT masked by accuracy
    assert display_fitness(None, 0.7) == 0.7  # no formula → plain accuracy
    assert display_fitness(0.85, 0.7) == 0.85  # active-formula value used as-is


def test_prompt_compactness_penalizes_verbose_prompt():
    """Long rendered prompts drive compactness toward zero; short prompts stay near 1."""
    from promptpotter.application.scoring.evaluators import (
        PROMPT_BUDGET_CHARS,
        compute_prompt_compactness,
    )
    from promptpotter.domain.opt_search_point import OptSearchPoint

    short = OptSearchPoint(instruction="Answer correctly.")
    long_text = "x " * (PROMPT_BUDGET_CHARS // 2)  # ≈ 2× budget
    verbose = OptSearchPoint(instruction=long_text)

    assert compute_prompt_compactness(opt_sp=short) > 0.99
    assert compute_prompt_compactness(opt_sp=verbose) == 0.0
    # No prompt to measure → unmeasured (None), NOT a maximally compact 1.0. The old vacuous
    # 1.0 was a free full-marks award on the term for a candidate carrying no prompt at all.
    assert compute_prompt_compactness(opt_sp=None) is None


# ===========================================================================
# 4. Rasch fit + decision-information round subset
# ===========================================================================


def _synth_observations(
    theta_true: dict[str, float],
    delta_true: dict[int, float],
    n_per_pair: int = 8,
    seed: int = 0,
) -> list[Observation]:
    rng = np.random.default_rng(seed)
    obs = []
    for cid, t in theta_true.items():
        for sid, d in delta_true.items():
            p = 1.0 / (1.0 + np.exp(-(t - d)))
            for _ in range(n_per_pair):
                obs.append(
                    Observation(candidate_id=cid, sample_id=sid, response=float(rng.random() < p))
                )
    return obs


def test_rasch_recovers_known_parameters() -> None:
    # Wide spread + many obs/pair → empirical-Bayes MAP recovers both the
    # latent arrays and the population hyperparameters within tolerance.
    theta_true = {"strong": 1.5, "mid": 0.0, "weak": -1.5}
    delta_true = {1: -1.0, 2: 0.0, 3: 1.0, 4: 2.0}
    obs = _synth_observations(theta_true, delta_true, n_per_pair=80, seed=42)

    posterior = fit_rasch(obs)

    assert posterior.converged
    # Identifiability: both arrays anchored to mean(theta) == 0.
    assert abs(sum(posterior.theta.values()) / len(posterior.theta)) < 1e-6
    # Recovery is within the noise floor at n=80 obs/pair.
    # Loosen to 0.5 logits — math correctness is what we're guarding, not precision.
    theta_offset = sum(theta_true.values()) / len(theta_true)
    for cid, t_true in theta_true.items():
        assert abs(posterior.theta[cid] - (t_true - theta_offset)) < 0.5
    for sid, d_true in delta_true.items():
        assert abs(posterior.delta[sid] - (d_true - theta_offset)) < 0.5

    # Empirical Bayes estimates the priors instead of hardcoding them: the
    # hyperparameters track the true population spread (θ std ≈ 1.22, δ std
    # ≈ 1.12, mean δ ≈ 0.5), not the old fixed 1.5 / 2.0 sigmas.
    assert abs(posterior.sigma_theta - 1.22) < 0.6
    assert abs(posterior.sigma_delta - 1.12) < 0.6
    assert abs(posterior.mu_delta - 0.5) < 0.4


def test_rasch_handles_sparse_matrix() -> None:
    # Each candidate touches a different subset — Rasch must not crash on this.
    obs = [
        Observation("a", 1, True),
        Observation("a", 2, False),
        Observation("b", 2, True),
        Observation("b", 3, True),
        Observation("c", 1, False),
        Observation("c", 3, False),
    ]
    posterior = fit_rasch(obs)
    assert set(posterior.theta) == {"a", "b", "c"}
    assert set(posterior.delta) == {1, 2, 3}
    assert all(se > 0 for se in posterior.theta_se.values())
    assert all(se > 0 for se in posterior.delta_se.values())


def test_fit_theta_given_delta_is_subset_invariant_unlike_accuracy() -> None:
    # The cross-round comparability guard (slice 2). A FIXED difficulty ruler δ;
    # two candidates measured on DISJOINT subsets whose raw accuracies INVERT their
    # true ability — the able one saw only HARD samples (low accuracy), the weak one
    # only EASY samples (high accuracy). θ-given-δ must recover the true ordering
    # (able > weak) where accuracy gets it backwards, and be ~subset-invariant.
    rng = np.random.default_rng(7)
    ruler = {1: -2.0, 2: -2.0, 3: -2.0, 4: 2.0, 5: 2.0, 6: 2.0}  # easy 1-3, hard 4-6
    easy, hard = [1, 2, 3], [4, 5, 6]

    def measure(cid: str, theta: float, sids: list[int], n: int = 200) -> list[Observation]:
        obs: list[Observation] = []
        for sid in sids:
            p = 1.0 / (1.0 + np.exp(-(theta - ruler[sid])))
            obs.extend(Observation(cid, sid, float(rng.random() < p)) for _ in range(n))
        return obs

    able = measure("able", 1.5, hard)  # high ability, hard subset → low accuracy
    weak = measure("weak", -0.5, easy)  # low ability, easy subset → high accuracy
    able_acc = sum(o.response for o in able) / len(able)
    weak_acc = sum(o.response for o in weak) / len(weak)
    assert weak_acc > able_acc  # raw accuracy INVERTS the true ability

    fit = fit_theta_given_delta(able + weak, ruler)
    assert fit["able"][0] > fit["weak"][0]  # θ recovers the true ordering
    assert abs(fit["able"][0] - 1.5) < 0.5  # within the noise floor at n=200/pair
    assert abs(fit["weak"][0] - (-0.5)) < 0.5
    assert all(se > 0 for _, se in fit.values())

    # Subset-invariance: the SAME θ measured on easy vs hard → ~same estimate (the
    # property accuracy lacks — easy accuracy ≫ hard accuracy for the same ability).
    same_easy = fit_theta_given_delta(measure("x", 0.8, easy), ruler)["x"][0]
    same_hard = fit_theta_given_delta(measure("x", 0.8, hard), ruler)["x"][0]
    assert abs(same_easy - same_hard) < 0.5

    # A sample absent from the ruler is placed at δ=0 (a FLAT ruler where it is cold), so the
    # candidate is scored on the flat ruler — never omitted (one ruler, θ always; cold ⇒ θ is
    # plain logit-accuracy). A single hit on flat δ ⇒ θ > 0 under the N(0,σ²) prior.
    ghost = fit_theta_given_delta([Observation("ghost", 99, True)], ruler)
    assert "ghost" in ghost and ghost["ghost"][0] > 0.0

    # θ_se carries the quasi-likelihood dispersion correction. `Observation.response` is a GRADED
    # fitness, not a coin flip — a ranked-table answer at position 5 of 20, or the L4 outer
    # composite — and a graded response varies far less about its mean than Bernoulli assumes.
    # Measured against the true sampling spread of θ̂ at n=28: ×1.02 on binary, ×1.51 on
    # reciprocal-rank, ×4.66 on the L4 outer. That inflation is what left the outer election
    # unable to crown and PoBB unable to eliminate. SILENT: every gate reads a real signal as
    # noise, the run completes, and the loop reports "no candidate separated".
    graded = [Observation("g", sid, 0.5 + 0.02 * i) for i, sid in enumerate(easy * 6)]
    spread = [Observation("b", sid, float(i % 2)) for i, sid in enumerate(easy * 6)]
    assert (
        fit_theta_given_delta(graded, ruler)["g"][1] < fit_theta_given_delta(spread, ruler)["b"][1]
    )

    # ...and it must NOT move binary data: φ ≈ 1 there, so a dichotomous dataset is unchanged.
    # A correction that quietly re-scaled every existing campaign's SE would be the same class
    # of silent harm in the other direction.
    binary = measure("bin", 0.8, easy, n=60)
    p_hat = 1.0 / (1.0 + np.exp(-(fit_theta_given_delta(binary, ruler)["bin"][0] - (-2.0))))
    bern_se = 1.0 / np.sqrt(len(binary) * p_hat * (1 - p_hat) + 1 / 1.5**2)
    assert abs(fit_theta_given_delta(binary, ruler)["bin"][1] - bern_se) < 0.15 * bern_se

    # A response with NO residual spread carries no evidence about its own dispersion; the φ
    # floor stops that silence from being read as infinite confidence (SE → 0 ⇒ every gate fires).
    flat = [Observation("f", sid, 0.5) for sid in easy * 6]
    assert fit_theta_given_delta(flat, ruler)["f"][1] > 0.1


def test_ruler_expected_accuracy_refuses_subset_inflation() -> None:
    # RP-2 (L4 proxy honesty). The outer outer fitness reads inner improvement as
    # θ-implied accuracy on the cycle's FIXED ruler, not the winner's raw hit-rate, so a
    # lucky thin resubset (`per_round_resubset`) cannot inflate the signal an outer cycle
    # optimizes against. The silent harm without this: a 5/6 easy-slice reads 0.83 and
    # feeds the outer a phantom +0.33 lift while the honest full-panel ability is 0.5.
    ruler = {1: -2.0, 2: -2.0, 3: -2.0, 4: 2.0, 5: 2.0, 6: 2.0}  # easy 1-3, hard 4-6
    full = ruler_expected_accuracy(0.0, ruler)
    assert full is not None
    # Symmetric ruler about δ=0 ⇒ ability 0 projects to EXACTLY 0.5 — regardless of which
    # subset a round scored — whereas raw accuracy on the easy 1-3 subset reads ~0.88.
    assert abs(full - 0.5) < 1e-9
    # Monotone in ability: a genuinely stronger candidate always projects higher.
    lo, hi = ruler_expected_accuracy(-1.5, ruler), ruler_expected_accuracy(1.5, ruler)
    assert lo is not None and hi is not None and hi > full > lo
    # Cold ruler / absent ability → None so the proxy falls back to raw accuracy.
    assert ruler_expected_accuracy(0.0, {}) is None
    assert ruler_expected_accuracy(None, ruler) is None


def test_adopted_level_trajectory_is_honest_single_scale() -> None:
    # The L4 outer proxy's inner-search signal. Every branch here is a SILENT wrong-number
    # class: a completed inner run reports a plausible number and the outer optimizes on it,
    # so a mis-built level is invisible — the run looks fine and the outer fitness is wrong.
    ruler = {1: -1.0, 2: 0.0, 3: 1.0}
    origin_theta = (0.0, 0.30)

    def thetas(levels: list[tuple[float, float]]) -> list[float]:
        return [t for t, _ in levels]

    # LOGITS, not expected accuracy. θ and the ruler's δ share one INTERVAL scale — the point of
    # fitting Rasch at all — so an identical Δθ must read as an identical gain wherever the origin
    # sits. Projecting each θ back through the ruler's sigmoid before differencing compressed the
    # gain near the ceiling, so the strong-origin arm scored less for the same ability climb.
    # SILENT: the outer ranks optimizer prompts partly by which seed happened to draw an easy origin.
    low_o, low = adopted_level_trajectory((-1.0, 0.2), [(-0.5, 0.2)], ruler)
    high_o, high = adopted_level_trajectory((1.5, 0.2), [(2.0, 0.2)], ruler)
    assert low_o is not None and high_o is not None
    assert (
        thetas(low)[0] - low_o[0]
        == pytest.approx(thetas(high)[0] - high_o[0])
        == pytest.approx(0.5)
    )

    # THE INCUMBENT THE ROUND ADOPTED — never a statistic over the round's proposals. A round's
    # value to the search is what it CROWNS; the arms it discards are the price of finding that,
    # and averaging them prices exploration as damage. For any mutation operator with mass below
    # the parent (all of them — that is why selection exists) E[mean θ] < θ_parent, so the mean
    # is negative for an exploring generator and ~0 for an inert one: the gradient inverted.
    # Measured on promptpotter-self__d8b5be before the fix: an inner round that adopted a
    # 28-sample winner at θ +0.27 while PoBB killed a dud at 6 samples (θ -1.84) recorded a level
    # of -0.79 — a 0.88-logit REGRESSION stamped on a round the loop marked improved=True. Over
    # the campaign the seed that gained 30 accuracy points scored 2.9x WORSE than one that gained
    # 4.6. SILENT throughout: every run completed and every number looked plausible.
    o_lvl, levels = adopted_level_trajectory(origin_theta, [(0.2702, 0.21)], ruler)
    assert o_lvl == origin_theta
    assert levels == [(pytest.approx(0.2702), pytest.approx(0.21))]

    # A peak followed by a collapse must read LOWER than a sustained peak. Under a running max
    # the two are byte-identical, so an optimizer prompt that destroys the inner loop after one good
    # round scored as its best round forever.
    _, spike = adopted_level_trajectory(origin_theta, [(1.2, 0.2), (-2.0, 0.2)], ruler)
    _, held = adopted_level_trajectory(origin_theta, [(1.2, 0.2), (1.2, 0.2)], ruler)
    assert thetas(spike)[1] < thetas(spike)[0] and sum(thetas(spike)) < sum(thetas(held))

    # A round whose frontier could not be fit (every row errored) carries the PRIOR level: the
    # incumbent persists, and nothing says it moved. SILENT otherwise: a phantom negative, or a
    # dropped round that shortens the series the mean divides by.
    #
    # It carries BOTH HALVES of that level, and the SE half is the one that can go wrong quietly:
    # a carried θ paired with a fresh round's SE would report the panel a precision no measurement
    # bought, and an inverse-variance pool then weights the cell that measured nothing the highest.
    o2, lv2 = adopted_level_trajectory(origin_theta, [None], ruler)
    assert o2 is not None and lv2 == [o2]
    _, carried = adopted_level_trajectory(origin_theta, [(1.0, 0.11), None], ruler)
    assert carried == [(1.0, 0.11), (1.0, 0.11)]

    # Regression preserved: an incumbent below origin yields a level BELOW origin (the negative
    # delta the outer steers away from), NOT floored at origin.
    o3, lv3 = adopted_level_trajectory(origin_theta, [(-2.0, 0.2)], ruler)
    assert o3 is not None and thetas(lv3)[0] < o3[0]

    # An origin that was never fit, or a COLD ruler, yields `(None, [])` so the caller EXCLUDES
    # the cycle (`no_evidence_reason`). Cold matters on its own: `fit_theta_given_delta` puts
    # every sample at δ=0 there, so θ collapses to that round's logit-accuracy and stops being
    # subset-invariant — differencing it across rounds compares two different scales.
    # SILENT: a dead inner campaign differenced against an invented floor reads as a huge lift.
    assert adopted_level_trajectory(None, [(1.0, 0.2)], ruler) == (None, [])
    assert adopted_level_trajectory(origin_theta, [(1.0, 0.2)], {}) == (None, [])


def test_compute_proxies_is_one_exact_mean_over_the_adopted_incumbents() -> None:
    # SILENT wrong-score: the outer signal is ONE number, so any error in it is the whole
    # ranking. It must be the mean of the adopted levels minus the origin, over the cycle's
    # ROUND BUDGET. A divisor of any OTHER shape reappearing here (a declared target, or the
    # room `max(origin, 1-origin)`) fails loudly on the pins below, and so does a regression to
    # reading the series' last element: the two differ on every trajectory that is not flat.
    from promptpotter.domain.l4.proxies import (
        OUTER_PROXY_KEYS,
        compute_outer_proxies,
        mean_round_delta_se,
    )

    px = compute_outer_proxies(
        cycle_result([0.40, 0.55], 0.30, [round_result(1), round_result(2)])
    ).model_dump()
    assert px == {"mean_round_delta": pytest.approx(0.175)}  # endpoint would read 0.25
    # The emitted key set IS the dataset's declared `observation_mappings`; a field added here
    # and not declared there is silently dropped before the formula ever sees it.
    assert OUTER_PROXY_KEYS == ("mean_round_delta",)

    # A campaign that ends where it started scores exactly zero lift — not a small positive one.
    flat = compute_outer_proxies(cycle_result([0.30], 0.30, [round_result(1)]))
    assert flat.mean_round_delta == pytest.approx(0.0)

    # WHEN the lift lands is part of the score, and that is the point of the mean. These two
    # trajectories END in the same place; the one that climbed in round 1 and gave some back
    # scores well above the one that crawled. The endpoint read cannot separate them at all
    # (both 0.05), and on the banked 39-cell panel the mean measured a 26% smaller residual
    # while ranking the same arms — so this is a precision gain, not a change of subject.
    early = compute_outer_proxies(
        cycle_result([0.90, 0.35], 0.30, [round_result(1), round_result(2)])
    )
    late = compute_outer_proxies(
        cycle_result([0.05, 0.35], 0.30, [round_result(1), round_result(2)])
    )
    assert early.mean_round_delta == pytest.approx(0.325)
    assert late.mean_round_delta == pytest.approx(-0.10)
    assert early.mean_round_delta > late.mean_round_delta

    # THE DENOMINATOR IS THE BUDGET, NOT THE SERIES LENGTH — and getting this wrong is silent
    # in the worst direction. `lives` stops a STALLING cycle, so dividing by the rounds that ran
    # pays a cell for quitting: this trajectory lifted in round 2 and stopped, and over its own
    # 3 rounds it reads +0.30 — better than the identical search that sat through a 4th flat
    # round. Held forward to the declared budget both read +0.225, which is the same cell twice.
    quit_early = cycle_result(
        [0.30, 0.60, 0.60], 0.30, [round_result(i) for i in (1, 2, 3)], round_budget=4
    )
    ran_out = cycle_result(
        [0.30, 0.60, 0.60, 0.60], 0.30, [round_result(i) for i in (1, 2, 3, 4)], round_budget=4
    )
    # THE PADDING MUST NOT REACH THE PRECISION. `held_levels` stretches a short series by
    # repeating its last value; those slots carry no measurement. If they entered the cell's SE
    # as if they did, a cell that quit after 2 of 4 rounds would report itself SHARPER than one
    # that ran the budget out, and an inverse-variance pool would weight the cell that measured
    # LEAST the most. SILENT: the panel would report a tighter CI it never earned, and buy its
    # confidence from the arms that did the least work.
    se_kw = {"origin_level_se": 0.20, "round_adopted_level_ses": [0.30, 0.40]}
    padded = cycle_result(
        [0.30, 0.60], 0.30, [round_result(i) for i in (1, 2)], round_budget=4, **se_kw
    )
    unpadded = cycle_result(
        [0.30, 0.60], 0.30, [round_result(i) for i in (1, 2)], round_budget=2, **se_kw
    )
    assert mean_round_delta_se(padded) == pytest.approx(mean_round_delta_se(unpadded))
    # mean(0.30, 0.40) against the origin's own 0.20, in quadrature — never sigma/sqrt(n), which
    # would divide correlated, NESTED frontier fits as if they were independent draws.
    assert mean_round_delta_se(padded) == pytest.approx((0.35**2 + 0.20**2) ** 0.5)
    assert mean_round_delta_se(padded) > 0.35 / 2

    # No SE at all yields None — the same answer as "this cell was never fit". A fabricated 0.0
    # reads as an infinitely sharp cell and would dominate every weighting it entered.
    assert mean_round_delta_se(cycle_result([0.30], 0.30, [round_result(1)])) is None

    assert compute_outer_proxies(quit_early).mean_round_delta == pytest.approx(0.225)
    assert compute_outer_proxies(ran_out).mean_round_delta == pytest.approx(0.225)
    # An undeclared budget falls back to the series length rather than dividing by zero.
    assert compute_outer_proxies(
        cycle_result([0.30, 0.60, 0.60], 0.30, [round_result(i) for i in (1, 2, 3)])
    ).mean_round_delta == pytest.approx(0.20)


def test_compute_proxies_excludes_cycles_that_produced_no_evidence() -> None:
    # SILENT wrong-score. Every aggregate here is TOTAL on an empty input (`_mean([])` is 0.0),
    # so a cycle that never ran an L1 round scores `cleanliness = diversity_health = 1.0` — an
    # unexercised optimizer prompt reported as flawless, and a *high* outer fitness. Nothing errors.
    # The exclusion predicate must ask "produced evidence?", not "failed?" — the two answers
    # differ on every row below.
    from promptpotter.domain.l4.proxies import InnerCycleUnscoreableError, compute_outer_proxies

    # Zero L1 rounds, on a cycle that DID end on its own terms.
    empty = cycle_result([], 0.30, [], stop_reason=StopReason.TARGET_HIT)
    with pytest.raises(InnerCycleUnscoreableError, match="no L1 rounds"):
        compute_outer_proxies(empty)

    # ONLY A SUCCESS OUTCOME IS A MEASUREMENT. The dangerous rows are the ones with rounds on
    # the board: a rail-truncated cycle looks exactly like a completed one, so every aggregate
    # below computes happily and reports a TRUNCATED trajectory as the optimizer prompt's verdict —
    # "it stopped improving" is indistinguishable from "we cut it off". That let provider mood
    # (a slow backend, a spend cap tripping on jittery reasoning-token counts, an operator's
    # Ctrl+C) masquerade as optimizer prompt quality. Measured before the fix: 3 of 36 inner cycles
    # on disk tripped `token_budget`, two truncating at rounds 4-5 of a 7-round budget, and
    # every one was scored. Read the verdict off the typed StopOutcome table, never a
    # hand-written reason set.
    for stop in (
        StopReason.SPEND_BUDGET,
        StopReason.TOKEN_BUDGET,
        StopReason.BACKEND_UNREACHABLE,
        StopReason.ABORT,
        StopReason.PAUSED,
        StopReason.OPTIMIZER_TIMEOUT,
        StopReason.CRASHED,
    ):
        truncated = cycle_result(
            [0.40, 0.55],
            0.30,
            [round_result(1), round_result(2)],
            stop_reason=stop,
        )
        with pytest.raises(InnerCycleUnscoreableError, match="did not end on its own terms"):
            compute_outer_proxies(truncated)

    # ...and it is EXCLUDED, never floored: the floor is `after_N_rounds_delta = -1`, which zeroes
    # cell (the lift core is multiplicative) — punishing the optimizer prompt for a slow provider,
    # which is the dead-cell bug in a new costume. An all-tooling-rounds cycle that would
    # otherwise floor (see the test above) is excluded once a rail truncated it.
    railed_and_empty = cycle_result(
        [0.40],
        0.30,
        [round_result(1, parse_failure="l1_provider_empty_response")],
        stop_reason=StopReason.TOKEN_BUDGET,
    )
    with pytest.raises(InnerCycleUnscoreableError, match="did not end on its own terms"):
        compute_outer_proxies(railed_and_empty)

    # Rounds ran, but the trajectory is empty → nothing to difference against origin. Without
    # the guard `first`/`after_N_rounds_delta` would both read a flat 0.0: "no lift" is a
    # plausible-looking number for "no measurement", which is what makes it dangerous.
    levelless = cycle_result([], 0.30, [round_result(1)])
    with pytest.raises(InnerCycleUnscoreableError, match="no levels"):
        compute_outer_proxies(levelless)

    # Rounds AND levels, but the origin was never scored. Every delta here is measured against
    # that floor, so substituting 0.0 (the old `origin_acc` stand-in, itself 0.0 when nothing
    # was scored) reports the whole trajectory as an enormous lift over nothing — and it does so
    # for the CHEAPEST rows, since a crash at round 0 is what leaves the origin unscored.
    floorless = cycle_result([0.40, 0.55], None, [round_result(1), round_result(2)])
    with pytest.raises(InnerCycleUnscoreableError, match="origin was never scored"):
        compute_outer_proxies(floorless)

    # ...and a cycle that DID produce evidence still scores, on the same predicate.
    ok = cycle_result([0.40, 0.55], 0.30, [round_result(1), round_result(2)])
    ok_px = compute_outer_proxies(ok)
    assert ok_px.mean_round_delta == pytest.approx(0.175)


def test_cached_calls_are_metered_but_not_billed(tmp_path: Path) -> None:
    # The upstream half of the bug above, and it is what actually failed: a cache hit emitted NO
    # token-usage record at all, so a replayed inner cycle reported zero cost and the L4 divisor
    # had nothing to divide by. Both halves are silent — the run completes and the dashboard reads
    # a plausible $0.00.
    #
    # The invariant, in one place: EVERY call the search makes lands in `incurred`; only a call
    # that reached the wire lands in the BILL. Collapse them either way and something breaks —
    # bill the cache hits and `spend_budget_usd` halts a run that cost nothing; meter only the
    # misses and the L4 origin arm reads as infinitely efficient.
    from promptpotter.domain.cycle_paths import CycleDir
    from promptpotter.domain.run_records import TokenUsageRecord
    from promptpotter.infrastructure.projections.live_dashboard.view import LiveDashboardView
    from promptpotter.infrastructure.store.layout import cycle_dir_for, session_dir_for

    view = LiveDashboardView(
        cycle_dir=CycleDir(cycle_dir_for(tmp_path, "c1", "cyc1")),
        session_dir=session_dir_for(tmp_path, "s1"),
        campaign_id="c1",
        cycle_id="cyc1",
        session_id="s1",
        l1_patience=2,
        n_variants=2,
        sp_budget_ttest=5,
        headline_metric="accuracy",
    )

    def usage(*, cached: bool) -> TokenUsageRecord:
        return TokenUsageRecord(
            kind="optimizer",
            node="l1_generate",
            model="openai/gpt-oss-120b",
            input_tokens=1000,
            output_tokens=500,
            cost_usd=0.02,
            cached=cached,
        )

    view._handle_token_usage(usage(cached=False))
    view._handle_token_usage(usage(cached=True))

    spend = view.state.spend
    # Two identical calls; one paid, one replayed. The bill counts one, the search made two.
    assert spend.total_used_usd == pytest.approx(0.02)
    assert spend.total_incurred_usd == pytest.approx(0.04)
    # The budget gate reads the bill, so a replay can never halt a run it cost nothing to make.
    assert view.spend_total_used_usd == pytest.approx(0.02)
    assert spend.loop.input_tokens == 1000  # billed tokens: the wire call only


def test_pp_self_scoring_is_monotone_and_never_clips_on_real_data() -> None:
    # SILENT wrong-score: the formula is now a single re-anchored term, so the only ways it can
    # be wrong are (a) losing monotonicity in that term, or (b) CLIPPING — and clipping is the
    # dangerous one, because a clamped cell keeps scoring while carrying no gradient at all. The
    # previous `(x + 0.5) / 1.5` window clipped the best cell ever measured (+1.405 logits) and
    # held zero cells in its lower third; both ends are pinned here.
    from pathlib import Path

    cfg = yaml.safe_load(
        Path("datasets/promptpotter-self/campaign.yaml").read_text(encoding="utf-8")
    )
    score = compile_scorer(cfg["campaign_config"]["scoring"])

    def s(mean_round_delta: float) -> float:
        return score({"pipeline_data": {"mean_round_delta": mean_round_delta}})

    assert s(0.9) > s(0.5) > s(0.0) > s(-0.5)  # monotone across the measured range
    # LINEAR, which is what makes the paired estimator's effect readable as a logit lift rather
    # than merely orderable: equal movements in the delta buy equal movements in the fitness.
    assert (s(0.9) - s(0.5)) == pytest.approx(s(0.5) - s(0.1))
    # The FLOOR lands exactly at 0.0, which is its contract: `_floor_proxies` assigns -1.0 to a
    # optimizer prompt that broke its own measurement, and that is the one route to a zeroed cell.
    assert s(-1.0) == 0.0
    # No MEASURED cell clips. The banked 39-cell panel spans [-0.189, +1.405]; both ends must sit
    # strictly inside (0, 1) or the term stops carrying a gradient exactly where it matters.
    assert s(-0.189) > 0.0 and s(1.405) < 1.0
    # ...and the clamp is still there for a runaway fit beyond the plausibility rail.
    assert s(-99.0) == 0.0 and s(99.0) == 1.0


def _synth_2pl(
    theta: np.ndarray,
    delta: np.ndarray,
    disc: np.ndarray,
    *,
    n_per_pair: int,
    seed: int,
) -> list[Observation]:
    """Responses from a true 2PL model: p = σ(aₛ·(θ_c − δₛ))."""
    rng = np.random.default_rng(seed)
    obs: list[Observation] = []
    for i, th in enumerate(theta):
        for s, (d, a) in enumerate(zip(delta, disc, strict=True)):
            p = 1.0 / (1.0 + np.exp(-a * (th - d)))
            obs.extend(Observation(f"c{i}", s, bool(rng.random() < p)) for _ in range(n_per_pair))
    return obs


def test_2pl_recovers_discrimination_and_seam_is_invisible_when_flat() -> None:
    """The 2PL estimator + the one-ruler seam. Silent harm: if ``fit_theta_given_delta``
    read a ``(δ, a)`` ruler differently from a bare-δ ruler when a≡1, every θ would shift
    the moment a dataset graduated — a wrong winner with no error. And if 2PL couldn't
    recover discrimination, signal-chasing/weighting would key on noise."""
    # Seam invariance: a (δ, 1.0) ruler must give bit-identical θ to a bare-δ ruler.
    obs = [Observation("c1", s, float(s % 2 == 0)) for s in range(8)] + [
        Observation("c2", s, float(s % 3 == 0)) for s in range(8)
    ]
    bare = {s: 0.2 * s - 1.0 for s in range(8)}
    tupled = {s: (0.2 * s - 1.0, 1.0) for s in range(8)}
    fb, ft = fit_theta_given_delta(obs, bare), fit_theta_given_delta(obs, tupled)
    assert all(abs(fb[c][0] - ft[c][0]) < 1e-9 for c in fb)

    # 2PL recovers the discrimination STRUCTURE: alternating high (a=2.5) / low (a=0.4)
    # signal samples → the fit separates them (high aₛ ≫ low aₛ), the rank that matters.
    theta = np.linspace(-2.5, 2.5, 20)
    delta = np.linspace(-2.0, 2.0, 12)
    disc = np.array([2.5 if s % 2 == 0 else 0.4 for s in range(12)])
    post = fit_rasch_2pl(_synth_2pl(theta, delta, disc, n_per_pair=8, seed=1))
    high = float(np.median([post.discrimination[s] for s in range(0, 12, 2)]))
    low = float(np.median([post.discrimination[s] for s in range(1, 12, 2)]))
    assert high > low * 2.0  # high-signal samples discriminate far harder than noisy ones
    assert all(se > 0 for se in post.discrimination_se.values())


def test_graduation_gate_stays_1pl_until_2pl_wins_holdout() -> None:
    """The per-dataset graduation gate. Silent harm: graduating a dataset whose samples
    don't actually discriminate would fit aₛ to noise → an overfit ruler → wrong θ → wrong
    winner, with no error. The held-out CV gate must refuse 2PL unless it provably wins."""
    theta = np.linspace(-2.5, 2.5, 20)
    delta = np.linspace(-2.0, 2.0, 12)

    # (a) Genuinely discriminating data → graduates to 2PL.
    disc_varied = np.array([2.5 if s % 2 == 0 else 0.4 for s in range(12)])
    data_2pl = _synth_2pl(theta, delta, disc_varied, n_per_pair=8, seed=2)
    model_g, post_g = graduate_ruler_model(data_2pl, enable=True)
    assert model_g == "2PL"
    assert post_g.discrimination  # the chosen ruler carries discrimination

    # (b) Flat-discrimination data (true a≡1) → stays 1PL: 2PL can't win held-out.
    data_1pl = _synth_2pl(theta, delta, np.ones(12), n_per_pair=8, seed=3)
    model_flat, post_flat = graduate_ruler_model(data_1pl, enable=True)
    assert model_flat == "1PL"
    assert not post_flat.discrimination

    # (c) The operator switch forces 1PL even on discriminating data (hysteresis floor=off).
    model_off, _ = graduate_ruler_model(data_2pl, enable=False)
    assert model_off == "1PL"

    # (d) Too-sparse data can never graduate (no held-out evidence).
    sparse = [Observation("a", 1, True), Observation("a", 2, False), Observation("b", 1, False)]
    assert graduate_ruler_model(sparse, enable=True)[0] == "1PL"


def test_select_round_subset_cold_starts_to_prefix_and_warms_to_informative() -> None:
    bank = [Sample(id=i, query=f"q{i}", ground_truth="g") for i in range(6)]

    # Cold start (no observations) → deterministic bank-order prefix.
    assert select_round_subset(bank, [], 3) == bank[:3]
    # Budget at/above bank size → the whole bank; non-positive budget → empty.
    assert select_round_subset(bank, [], 99) == bank
    assert select_round_subset(bank, [], 0) == []

    # Warm: samples 0-2 split ~50/50 across candidates (mid difficulty — the
    # contested band where a mutation can still flip the verdict); samples
    # 3-5 always HIT (easy, settled — a fresh candidate's outcome there is
    # predictable, no decision signal). Both groups are measured equally
    # often, so se_δ can't decide it; the decision objective surfaces the
    # contested band.
    obs: list[Observation] = []
    for sid in (0, 1, 2):
        for cid in ("a", "b", "c"):
            obs.append(Observation(cid, sid, True))
            obs.append(Observation(cid, sid, False))
    for sid in (3, 4, 5):
        for cid in ("a", "b", "c"):
            obs.extend(Observation(cid, sid, True) for _ in range(2))
    picked_ids = {s.id for s in select_round_subset(bank, obs, 3)}
    assert picked_ids == {0, 1, 2}


def test_round_winner_elects_by_ability_not_subset_accuracy() -> None:
    """Per-round-resubset drift guard. Two candidates measured on DIFFERENT subsets:
    the high-accuracy one only saw easy samples; the lower-accuracy one cleared HARD
    samples the origin always misses. Raw subset accuracy would crown the easy candidate;
    difficulty-adjusted ability (θ) — the gating metric — must crown the abler one.

    Silent harm: under per-round resubset the wrong winner is promoted with no error —
    the run completes, the dashboard looks fine, the lineage decays toward whoever drew
    the gentlest samples. The election ranks θ on the cycle's fixed δ ruler, so it does not.
    """
    from promptpotter.application.scoring.metrics import elect_round_winner

    def res(hit: bool, sid: int) -> dict:
        return {"sample_id": sid, "hit": hit, "fitness": 1.0 if hit else 0.0}

    # Fixed δ ruler: easy {0..19} low difficulty, hard {20..39} high — the bank the election reads.
    ruler = {i: (-1.5 if i < 20 else 1.5) for i in range(40)}
    # Origin spans all 40: easy {0..19} hit, hard {20..39} missed.
    origin = [res(i < 20, i) for i in range(40)]
    # Easy candidate: 16/20 on easy samples the origin also hits → accuracy 0.80, modest lift.
    weak_on_easy = [res(i < 16, i) for i in range(20)]
    # Able candidate: 14/20 on HARD samples the origin always misses → accuracy 0.70, bigger lift.
    able_on_hard = [res(i < 34, i) for i in range(20, 40)]
    results_by_id = {"weak_on_easy": weak_on_easy, "able_on_hard": able_on_hard}

    # Raw subset accuracy crowns the easy candidate (0.80 > 0.70)...
    assert sum(r["hit"] for r in weak_on_easy) / 20 > sum(r["hit"] for r in able_on_hard) / 20
    # ...but the θ-gated election crowns the abler one — it cleared HARD items (high δ), stronger
    # evidence of ability than more wins on easy items (low δ) everyone already passes.
    winner_id, abilities = elect_round_winner(
        ["weak_on_easy", "able_on_hard"], results_by_id, origin, 4, ruler
    )
    assert winner_id == "able_on_hard"
    # The fit rides out so the caller stamps θ from the same election fit (no second fit):
    # the abler candidate's θ outranks the easy one's despite the lower raw accuracy.
    assert abilities.theta["able_on_hard"] > abilities.theta["weak_on_easy"]


def test_an_origin_that_never_scored_is_no_floor_to_beat() -> None:
    """An origin whose every sample errored must not be scored as a coin-flip.

    ``candidate_abilities`` drops errored rows, and ``fit_theta_given_delta`` omits an arm with
    no observation — so such an origin carries no ``ORIGIN_ABILITY_ID`` entry. Reading it as
    ``.get(ORIGIN_ABILITY_ID, 0.0)`` invented θ=0, i.e. an origin able on ~50% of the ruler, and
    both the winner election and the ``delta_ok`` promotion gate ranked against that phantom.

    ``paired_fitness`` does not catch it: it grades an errored row as a 0.0 cell, so the
    origin-overlap guard passes on the very rows the θ fit discarded.

    Silent harm: a backend outage during the origin's round makes every later round elect and
    promote a winner against a floor that was never measured. No error, no symptom — the lineage
    is built on a number nobody observed.
    """
    from promptpotter.application.intelligence.exploration import (
        ORIGIN_ABILITY_ID,
        candidate_abilities,
        theta_lift_over_origin,
    )
    from promptpotter.application.scoring.metrics import elect_round_winner, paired_fitness

    def res(sid: int, fitness: float, errored: bool = False) -> dict:
        row = {"sample_id": sid, "hit": fitness > 0.5, "fitness": fitness}
        if errored:
            row["error_category"] = "transport"
        return row

    origin = [res(i, 0.0, errored=True) for i in range(6)]
    candidate = [res(i, 0.55) for i in range(6)]
    abilities = candidate_abilities({"c1": candidate}, origin, {})

    # The origin was never fit — nothing to floor against.
    assert ORIGIN_ABILITY_ID not in abilities.theta
    # ...yet the overlap guard sees six shared cells, so it cannot be the thing that stops us.
    assert len(paired_fitness(candidate, origin)[0]) == 6

    assert theta_lift_over_origin(abilities, "c1") is None
    winner_id, _ = elect_round_winner(["c1"], {"c1": candidate}, origin, 1, {})
    assert winner_id == ""

    # A measured origin still elects normally — the guard costs nothing when there IS a floor.
    scored_origin = [res(i, 0.1) for i in range(6)]
    scored_abilities = candidate_abilities({"c1": candidate}, scored_origin, {})
    lift = theta_lift_over_origin(scored_abilities, "c1")
    assert lift is not None and lift > 0.0
    assert elect_round_winner(["c1"], {"c1": candidate}, scored_origin, 1, {})[0] == "c1"


def test_errored_cells_never_satisfy_coverage_floor() -> None:
    """``coverage_floor`` must count the same cells the θ fit consumes.

    The floor is the SOLE under-probing guard — the election ranks by θ point estimate
    with no SE margin *because* only fully-probed candidates compete. Errored rows
    (CONNECTION/UNKNOWN — e.g. an L4 inner campaign dying of a timeout) are not fatal-
    classified, so a deprecation-only coverage count includes them while the θ fit drops
    them: a candidate whose cells mostly died as tooling errors passed the floor on
    "coverage" the fit never saw and won the round on its few lucky survivors.

    Silent harm: provider noise laundered into a round winner — the thin fluke becomes
    the next generation's parent with no error and a plausible-looking dashboard.
    """
    from promptpotter.application.scoring.metrics import (
        _distinct_valid_cells,
        elect_round_winner,
    )

    def clean(sid: int, fitness: float) -> dict:
        return {"sample_id": sid, "hit": fitness > 0.5, "fitness": fitness}

    def errored(sid: int) -> dict:
        # Real error rows (`_error_result`) carry no hit/fitness — only the category.
        return {"sample_id": sid, "error_category": "unknown"}

    origin = [clean(i, 0.0) for i in range(7)]
    # 7 cells attempted, 5 died as tooling errors — only 2 carry evidence.
    thin = [clean(0, 1.0), clean(1, 1.0)] + [errored(i) for i in range(2, 7)]

    # The lift is real (floor 2 elects it) — only coverage may stop it...
    assert elect_round_winner(["thin"], {"thin": thin}, origin, 2, {})[0] == "thin"
    # ...and at floor 6 it must: 2 evidence cells, not 7 attempted ones.
    assert elect_round_winner(["thin"], {"thin": thin}, origin, 6, {})[0] == ""

    # Replicates: an errored replicate adds no coverage, a clean one carries the cell.
    replicated = [clean(0, 1.0), clean(1, 1.0), errored(0), errored(1)]
    assert _distinct_valid_cells(replicated) == 2


# ===========================================================================
# 5. Measurement rerun short-circuit + refusal classification
# ===========================================================================


def test_rerun_short_circuits_when_max_tokens_le_cached_completion() -> None:
    """When the cached failure is a token-budget exhaustion AND the rerun's
    ``max_tokens`` would be no larger than the cached run's actual output,
    the rerun branch must short-circuit — running it would burn an LLM call
    against the same (or tighter) budget for an identical DEPR result.
    """
    from promptpotter.application.scoring.sample_measurement import (
        _rerun_would_repeat_token_budget_failure,
    )

    cached = {
        "pipeline_data": {
            "diagnostics": {"warnings": [{"step": "llm_only", "code": "content_empty"}]},
            "step_tokens": {
                "llm_only": {
                    "input": 212,
                    "output": 3072,  # actual emitted == max_tokens cap
                    "finish_reason": "length",
                    "reasoning": 12226,  # reasoning chars > completion → budget exhausted
                },
            },
        },
    }
    # rerun at tighter budget: short-circuit
    assert _rerun_would_repeat_token_budget_failure(cached, {"llm_only": {"max_tokens": 1536}})
    # rerun at equal budget: short-circuit
    assert _rerun_would_repeat_token_budget_failure(cached, {"llm_only": {"max_tokens": 3072}})
    # rerun at larger budget: let it try
    assert not _rerun_would_repeat_token_budget_failure(cached, {"llm_only": {"max_tokens": 8192}})
    # non-budget failure: regular rerun logic applies
    non_budget = {"pipeline_data": {"diagnostics": {"warnings": []}, "step_tokens": {}}}
    assert not _rerun_would_repeat_token_budget_failure(
        non_budget, {"llm_only": {"max_tokens": 1536}}
    )


def test_classify_result_routes_refusal_to_infra() -> None:
    """LLM refusal patterns (``I'm sorry, but I cannot...``) must surface as a
    distinct ``llm_only:model_refusal`` advisory in ``infra_codes`` so L2 sees
    them as a runtime-failure category — not plain MISS. Without this signal
    the optimizer can't propose mitigations (different model, rephrase) for
    queries where the model literally refuses to engage.
    """
    from promptpotter.domain.rendering import classify_result

    cases_route_to_infra = [
        "I'm sorry, but I cannot solve this problem.",
        "I'm sorry, but I'm unable to provide a definitive answer.",
        "I apologize, but I can't help with that.",
        "I cannot provide a solution to this question.",
    ]
    for predicted in cases_route_to_infra:
        cls = classify_result(
            {"predicted": predicted, "pipeline_data": {"terminated_at": "llm_only"}}
        )
        assert "llm_only:model_refusal" in cls.infra_codes, (
            f"expected refusal classification for {predicted!r}, got {sorted(cls.all_codes)}"
        )
        assert "llm_only:model_refusal" not in cls.fatal_codes  # intermittent, not one-strike-fatal

    # Negative: a mid-text apology inside genuine reasoning must NOT trigger.
    real_reasoning = (
        "Let me work through this. First I'll consider the symmetry. "
        "Sorry, I made an arithmetic slip earlier — recomputing: 7 * 9 = 63. "
        "Therefore the answer is \\boxed{63}."
    )
    cls = classify_result(
        {"predicted": real_reasoning, "pipeline_data": {"terminated_at": "llm_only"}}
    )
    assert "llm_only:model_refusal" not in cls.infra_codes


def test_classify_result_routes_structural_warning_to_fatal() -> None:
    """One source of truth, two consumers: a warning the backend **source-stamped**
    ``kind=structural`` is a deterministic-for-config failure, so PoBB elimination must
    fast-cut it (``fatal_codes`` → ``dominant_fatal``) exactly as the degradation verdict
    grades it structural-critical off the same stamped field. A transient-stamped code
    stays advisory-only — NOT deprecated, since the measurement is still valid. An
    unstamped warning is NOT routed fatal (no guessing)."""
    from promptpotter.domain.rendering import classify_result

    structural = classify_result(
        {
            "pipeline_data": {
                "diagnostics": {
                    "warnings": [
                        {
                            "step": "entity_profiling",
                            "code": "json_validate_failed",
                            "kind": "structural",
                        }
                    ]
                }
            }
        }
    )
    assert "entity_profiling:json_validate_failed" in structural.fatal_codes
    assert structural.dominant_fatal == "entity_profiling:json_validate_failed"

    transient = classify_result(
        {
            "pipeline_data": {
                "diagnostics": {
                    "warnings": [
                        {"step": "web_search", "code": "low_document_count", "kind": "transient"}
                    ]
                }
            }
        }
    )
    assert "web_search:low_document_count" in transient.advisory_codes
    assert not transient.fatal_codes and not transient.infra_codes

    # No ``kind`` stamped → NOT fatal (the source-stamp is the only structural signal;
    # an unstamped warning under-counts rather than over-eliminating).
    unstamped = classify_result(
        {
            "pipeline_data": {
                "diagnostics": {
                    "warnings": [{"step": "entity_profiling", "code": "json_validate_failed"}]
                }
            }
        }
    )
    assert not unstamped.fatal_codes


# ===========================================================================
# Pass B — proposal validators, PoBB posterior, round diagnostics, queue math
# ===========================================================================
# Sections:
#   6. L1 invariant detectors (no-op / duplicate / forbidden-axis).
#   7. L2 output validators (fire + quiet).
#   8. L3 output validators.
#   9. L1 layout validators (one parametrized hard-failure family).
#  10. PoBB posterior gate + leader eligibility.
#  11. L2 behaviour conformance.
#  12. compute_round_diagnostics — pure analysis over scoring data.
#  13. Adaptive-queue Bayesian math (θ posterior, decision-info, pick-score).

scipy = pytest.importorskip("scipy")  # transitively required by the PoBB math

from promptpotter.application.optimization.pobb.checks import (  # noqa: E402
    PoBBCheck,
    PoBBConfig,
)
from promptpotter.application.optimization.validators.l1_strict import (  # noqa: E402
    detect_invariants,
)
from promptpotter.application.optimization.validators.l3_output import (  # noqa: E402
    L3_PLAN_LENGTH_FLOOR,
    L3_PLAN_VERBATIM_REPEAT,
    run_l3_output_validators,
)
from promptpotter.domain.escalation_signals import (  # noqa: E402
    EscalationTarget,
    NurseOwner,
    ValidationFailure,
)
from promptpotter.domain.l1_layout import (  # noqa: E402
    NODE_LAYOUTS,
    L1Layout,
    validate_l1_layout,
)
from promptpotter.domain.opt_search_point import (  # noqa: E402
    L2L3Memory,
    OptSearchPoint,
    WoundChannels,
)
from promptpotter.domain.results import (  # noqa: E402
    CandidateProposal,
    ScoredCandidate,
    best_round_by_measured_accuracy,
    is_leader_eligible,
)
from promptpotter.domain.search_point import (  # noqa: E402
    FRAMING_FIELDS,
    FRAMING_VALUE_BUDGET,
    TaskDecomposition,
)

# ===========================================================================
# 6. L1 invariant detectors
# ===========================================================================


def _parent() -> OptSearchPoint:
    return OptSearchPoint(persona="Expert", instruction="Rank items.")


def _child(parent: OptSearchPoint, **changes) -> CandidateProposal:
    return CandidateProposal(opt_sp=parent.mutate(**changes))


def test_no_op_clone_attaches_validation_failure():
    parent = _parent()
    proposals = [_child(parent), _child(parent, persona="Expert ranker")]

    stats = detect_invariants(proposals, parent, {})

    no_op_reasons = [vf.reason for vf in proposals[0].opt_sp.memory.wounds.validation_failures]
    assert "no_op_variant" in no_op_reasons
    assert proposals[1].opt_sp.memory.wounds.validation_failures == []
    assert stats.l1_n_no_op == 1
    assert stats.l1_n_duplicate == 0
    assert stats.l1_yield == 0.5


# The two strings differ in wording and, in the tests below, in the FIELD they are written
# into — which is the invariant under test. They are close paraphrases on purpose: the gate is
# a lexical overlap test (`IDEA_MATCH_REJECT`), so a pair that shares little vocabulary would
# assert nothing about field-independence, only about the threshold.
_DEAD_IDEA = (
    "Derive new facts using modus ponens, modus tollens, disjunctive syllogism and chaining. "
    "Exhaust every derivation branch before concluding Uncertain."
)
_DEAD_IDEA_REPHRASED = (
    "Using modus ponens, modus tollens, disjunctive syllogism and chaining, derive new facts "
    "and exhaust each derivation branch before concluding Uncertain."
)


def test_a_reproposed_idea_is_rejected_even_when_rewritten_into_another_field():
    """The re-proposal that burned 8 rounds on `justlogic-d234` — same idea, new field.

    Every exact-signature gate in the loop saw eight distinct mutations, because the generator
    rewrites the idea into `instruction`, then `thinking_style`, then a schema description. The
    round is charged for it either way, so a gate that only matches strings is a gate that never
    fires on the failure it was built for.
    """
    parent = _parent()
    prior = [lost_round(1, "instruction", _DEAD_IDEA)]
    proposals = [
        _child(parent, thinking_style=_DEAD_IDEA_REPHRASED),  # same idea, different field
        _child(parent, persona="A terse logician who commits to a label."),  # unrelated
    ]

    stats = detect_invariants(proposals, parent, {}, prior)

    reasons = [vf.reason for vf in proposals[0].opt_sp.memory.wounds.validation_failures]
    assert "repeat_variant" in reasons, "a rewritten re-proposal must still be caught"
    assert "round 1" in proposals[0].opt_sp.memory.wounds.validation_failures[0].value
    assert proposals[1].opt_sp.memory.wounds.validation_failures == [], "unrelated idea survives"
    assert stats.l1_n_repeat == 1


def test_a_repeat_never_empties_the_round_and_unmeasured_history_never_convicts():
    """Two ways the gate could do more harm than the disease, both silent.

    (1) If EVERY proposal repeats, rejecting them all hands PoBB an empty population and the
    loop forfeits the round — strictly worse than re-testing a dead idea, which the ALREADY
    TRIED panel still marks. (2) A prior candidate that scored zero samples carries
    ``accuracy == 0.0`` only because the field is a non-optional float; convicting a live
    proposal on that is the loop punishing an idea nobody ever ran.
    """
    parent = _parent()
    prior = [lost_round(1, "instruction", _DEAD_IDEA)]

    # (1) every proposal is a repeat → none rejected.
    all_repeats = [
        _child(parent, thinking_style=_DEAD_IDEA_REPHRASED),
        _child(parent, answer_format=_DEAD_IDEA),
    ]
    stats = detect_invariants(all_repeats, parent, {}, prior)
    assert stats.l1_n_repeat == 0, "a repeat may cost a candidate, never the whole round"
    assert all(not p.opt_sp.memory.wounds.validation_failures for p in all_repeats)

    # (2) the same history, but never measured → no conviction even with a live alternative.
    unmeasured = [lost_round(1, "instruction", _DEAD_IDEA, total=0, acc=0.0)]
    proposals = [
        _child(parent, thinking_style=_DEAD_IDEA_REPHRASED),
        _child(parent, persona="A terse logician who commits to a label."),
    ]
    stats = detect_invariants(proposals, parent, {}, unmeasured)
    assert stats.l1_n_repeat == 0, "0/0 is absence of evidence, not a measured defeat"


def test_an_idea_that_beat_its_origin_is_not_grounds_for_rejection():
    """Refining a winner is the search working. Only measured LOSSES close a direction off."""
    parent = _parent()
    won = lost_round(1, "instruction", _DEAD_IDEA, acc=0.9)  # 0.9 > matched origin 0.5
    proposals = [
        _child(parent, thinking_style=_DEAD_IDEA_REPHRASED),
        _child(parent, persona="A terse logician who commits to a label."),
    ]

    stats = detect_invariants(proposals, parent, {}, [won])

    assert stats.l1_n_repeat == 0
    assert proposals[0].opt_sp.memory.wounds.validation_failures == []


def test_duplicate_signature_attaches_validation_failure():
    parent = _parent()
    proposals = [
        _child(parent, persona="Specialist"),
        _child(parent, persona="Specialist"),  # same signature → duplicate
        _child(parent, instruction="Rank with care."),
    ]

    stats = detect_invariants(proposals, parent, {})

    assert proposals[0].opt_sp.memory.wounds.validation_failures == []
    dup_reasons = [vf.reason for vf in proposals[1].opt_sp.memory.wounds.validation_failures]
    assert "duplicate_variant" in dup_reasons
    assert proposals[2].opt_sp.memory.wounds.validation_failures == []
    assert stats.l1_n_duplicate == 1
    assert stats.l1_n_no_op == 0
    assert stats.l1_yield == 2 / 3


def test_param_override_echoing_parent_value_is_a_no_op():
    """A variant that restates what the parent already holds must not score as a mutation.

    Silent harm: it clears the no-op gate, burns a full scoring pass on a searchpoint
    identical to its parent, and books the resulting noise as an axis effect. Both shapes
    are covered — a scalar echo (`temperature`) and a virtual-param echo
    (`output_schema_descriptions`, whose current prose lives folded inside `output_schema`,
    so the parent never carries the key the override names).
    """
    parent = _parent()
    parent_pp = {
        "llm_only": {
            "temperature": 0.0,
            "output_schema": {
                "properties": {"answer": {"description": "Commit to one label."}},
            },
        }
    }
    proposals = [
        CandidateProposal(
            opt_sp=parent.mutate(), pipeline_params_override={"llm_only": {"temperature": 0.0}}
        ),
        CandidateProposal(
            opt_sp=parent.mutate(),
            pipeline_params_override={
                "llm_only": {"output_schema_descriptions": {"answer": "Commit to one label."}}
            },
        ),
        CandidateProposal(
            opt_sp=parent.mutate(),
            pipeline_params_override={
                "llm_only": {"output_schema_descriptions": {"answer": "Never hedge."}}
            },
        ),
    ]

    stats = detect_invariants(proposals, parent, parent_pp)

    assert "no_op_variant" in [
        vf.reason for vf in proposals[0].opt_sp.memory.wounds.validation_failures
    ]
    assert "no_op_variant" in [
        vf.reason for vf in proposals[1].opt_sp.memory.wounds.validation_failures
    ]
    assert proposals[2].opt_sp.memory.wounds.validation_failures == []
    assert stats.l1_n_no_op == 2


def test_parse_population_attaches_forbidden_axis_failure_to_opt_sp():
    from promptpotter.application.optimization.l1.population import parse_population
    from promptpotter.domain.pipeline_schema import NodePromptInfo

    schema = PipelineSchema(
        name="test",
        available_models=["openai/gpt-oss-120b"],
        nodes=[PipelineNode(name="llm_only", prompt_info=NodePromptInfo())],
    )
    parent = _parent()
    proposal = CandidateProposal(
        opt_sp=parent.mutate(persona="Strict"),
        pipeline_params_override={"llm_only": {"model": "anything-at-all"}},
    )

    opt_sp_list, _ = parse_population([proposal], pipeline_params=None, schema=schema)

    failures = opt_sp_list[0].memory.wounds.validation_failures
    assert len(failures) == 1
    assert failures[0].reason == "forbidden_axis"
    assert failures[0].axis == "llm_only.model"


def test_parse_population_flags_dropped_mandatory_placeholder():
    """A candidate whose evolved prompt drops {{combined_text}} is invalid (synthetic-0), never a
    real winner — the gap that let an evidence-free program out-score the evidence-fed origin.
    The intact sibling stays clean. Guards a wrong score: the drop is silent without this."""
    from promptpotter.application.optimization.l1.population import parse_population
    from promptpotter.domain.pipeline_schema import NodePromptInfo

    schema = PipelineSchema(
        name="test",
        available_models=["openai/gpt-oss-120b"],
        nodes=[
            PipelineNode(
                name="entity_profiling",
                prompt_info=NodePromptInfo(template_variables=["combined_text"]),
            )
        ],
    )
    parent = _parent()
    dropped = CandidateProposal(opt_sp=parent.mutate(problem_description="Profile the entity."))
    intact = CandidateProposal(
        opt_sp=parent.mutate(problem_description="Profile using {{combined_text}}.")
    )

    opt_sp_list, _ = parse_population([dropped, intact], pipeline_params=None, schema=schema)

    dropped_failures = opt_sp_list[0].memory.wounds.validation_failures
    assert len(dropped_failures) == 1
    assert dropped_failures[0].reason == "dropped_mandatory_placeholder"
    assert dropped_failures[0].axis == "entity_profiling.prompt"
    assert opt_sp_list[1].memory.wounds.validation_failures == []


def test_parse_population_flags_dropped_optimizer_prompt_port():
    """An L4 candidate whose merged `l1_generate` prose drops an INLINE port
    (`{{citable_fields}}` in `answer_format`, `{{n_variants}}` in `task_intent`+`instruction`)
    is invalid (synthetic-0) — a severed channel once ran 4 inner campaigns as normal
    measurements, silently. The capability directives now ride the layout (guarded by
    `validate_l1_layout`'s mandatory set); the inline format ports guarded here can never
    move there, so this is the guard's permanent scope. Checked on MERGED params, so a
    child inheriting the broken prose from its parent (no override of its own) flags too."""
    from promptpotter.application.optimization.dispatch.llm_call.prompts import (
        base_optimizer_template,
    )
    from promptpotter.application.optimization.l1.population import parse_population

    schema = PipelineSchema(
        name="promptpotter-self",
        nodes=[PipelineNode(name="l1_generate", param_keys={"answer_format"})],
    )
    parent = _parent()
    base_answer_format = base_optimizer_template("l1_generate").answer_format
    dropped = CandidateProposal(
        opt_sp=parent.mutate(),
        pipeline_params_override={"l1_generate": {"answer_format": "Return JSON variants."}},
    )
    intact = CandidateProposal(
        opt_sp=parent.mutate(),
        pipeline_params_override={
            "l1_generate": {"answer_format": base_answer_format + " Be terse."}
        },
    )
    inherits_broken = CandidateProposal(opt_sp=parent.mutate(persona="Strict"))

    opt_sp_list, _ = parse_population(
        [dropped, intact],
        pipeline_params=None,
        schema=schema,
    )
    inherited_list, _ = parse_population(
        [inherits_broken],
        pipeline_params={"l1_generate": {"answer_format": "Answer without ports."}},
        schema=schema,
    )

    dropped_failures = opt_sp_list[0].memory.wounds.validation_failures
    assert [vf.reason for vf in dropped_failures] == ["dropped_mandatory_placeholder"]
    assert dropped_failures[0].axis == "l1_generate.prompt"
    assert "citable_fields" in dropped_failures[0].value
    assert opt_sp_list[1].memory.wounds.validation_failures == []
    assert [vf.reason for vf in inherited_list[0].memory.wounds.validation_failures] == [
        "dropped_mandatory_placeholder"
    ]


# ===========================================================================
# 7. L2 output validators (fire + quiet)
# ===========================================================================


def _opt_sp(**kwargs) -> OptSearchPoint:
    from promptpotter.domain.opt_search_point import L2L3Memory

    memory_fields = {
        "wounds",
        "l1_layout",
        "l1_overrides",
        "task_context",
    }
    if "memory" not in kwargs and (
        mem_kwargs := {k: kwargs.pop(k) for k in list(kwargs) if k in memory_fields}
    ):
        kwargs["memory"] = L2L3Memory(**mem_kwargs)
    return OptSearchPoint(persona="Expert", instruction="Rank items.", **kwargs)


def test_l1_config_not_in_runtime_failures_fires_on_reproposal():
    """L1 proposing a (param, value) matching a known runtime failure ⇒ ValidationFailure."""
    from promptpotter.application.optimization.validators.l1_strict import (
        L1_CONFIG_NOT_IN_RUNTIME_FAILURES,
    )
    from promptpotter.domain.escalation_signals import RuntimeFailure

    opt_sp = _opt_sp()
    opt_sp.memory.wounds.runtime_failures = [
        RuntimeFailure(
            source="degradation_check",
            dominant_warning="llm_only:empty_response",
            warning_types={"llm_only:empty_response": 6},
            degraded_rate=1.0,
            degraded_count=6,
            total_scored=6,
            observed_config={"max_tokens": 1800, "temperature": 0.7},
            first_seen_round=1,
        )
    ]
    out = L1_CONFIG_NOT_IN_RUNTIME_FAILURES.run({"llm_only": {"max_tokens": 1800}}, opt_sp=opt_sp)
    assert out is not None
    failures = out.evidence["failures"]
    assert len(failures) == 1
    assert failures[0].axis == "llm_only.max_tokens"
    assert failures[0].reason == "reproposes_known_failing_config"


def test_l1_config_not_in_runtime_failures_quiet_on_novel_config():
    """Novel (param, value) ⇒ no fire."""
    from promptpotter.application.optimization.validators.l1_strict import (
        L1_CONFIG_NOT_IN_RUNTIME_FAILURES,
    )
    from promptpotter.domain.escalation_signals import RuntimeFailure

    opt_sp = _opt_sp()
    opt_sp.memory.wounds.runtime_failures = [
        RuntimeFailure(
            source="degradation_check",
            dominant_warning="llm_only:empty_response",
            warning_types={"llm_only:empty_response": 3},
            degraded_rate=1.0,
            degraded_count=3,
            total_scored=3,
            observed_config={"max_tokens": 1800},
            first_seen_round=1,
        )
    ]
    out = L1_CONFIG_NOT_IN_RUNTIME_FAILURES.run({"llm_only": {"max_tokens": 2400}}, opt_sp=opt_sp)
    assert out is None


# ===========================================================================
# 8. L3 output validators
# ===========================================================================


def test_plan_length_floor_fires_on_short_plan():
    out = L3_PLAN_LENGTH_FLOOR.run({"plan": "do better"}, opt_sp=_opt_sp())
    assert out is not None


def test_plan_verbatim_repeat_fires():
    opt_sp = _opt_sp(plan="Maintain current strategy and explore persona axis carefully.")
    out = L3_PLAN_VERBATIM_REPEAT.run(
        {"plan": "Maintain current strategy and explore persona axis carefully."}, opt_sp=opt_sp
    )
    assert out is not None


def test_fatal_degradation_runtime_failure_escalates_to_operator():
    """A deterministic-for-config break (DegradationCheck fatal fast-path) is not L1's
    to retune — its ``owner`` is stamped OPERATOR (the token-blowout case), so the L1
    render flags it instead of telling L1 to 'raise max_tokens'. A rate-based degradation
    stays L1-owned (retune-able partial noise). ``owner`` is the only wound field that
    varies — the other two wounds' owners are structural (record type + guard stream).
    """
    from promptpotter.application.optimization.l1.score.signal_effect import decode_signal_effect
    from promptpotter.application.optimization.pobb.checks import PoBBCheck, PoBBConfig
    from promptpotter.domain.escalation_signals import EscalationSignal

    def _decode(check_result):
        sig = EscalationSignal(
            check_name="degradation",
            target=EscalationTarget.ELIMINATE_CANDIDATE,
            check_result=check_result,
            candidate_idx=0,
            candidates_scored=1,
            candidates_skipped=0,
        )
        return decode_signal_effect(
            sig,
            results=[{"sample_id": "0"}],
            dataset=[{}, {}],
            effective_pipeline_params={"entity_profiling": {"max_tokens": None}},
            round_num=1,
            elim_check=PoBBCheck(PoBBConfig(), n_samples=2, delta_scale={}),
            candidate_id="C1",
            candidate_label="C1",
            priors_at_test=[],
        ).runtime_failure

    fatal = _decode(
        {"fatal": True, "dominant_warning": "entity_profiling:client_error", "degraded_rate": 1.0}
    )
    assert fatal is not None
    assert fatal.owner is NurseOwner.OPERATOR

    rate_based = _decode(
        {"dominant_warning": "web_search:low_document_count", "degraded_rate": 0.5}
    )
    assert rate_based is not None
    assert rate_based.owner is NurseOwner.L1


def test_run_l3_output_validators_aggregates():
    opt_sp = _opt_sp(plan="short")
    outcomes = run_l3_output_validators({"plan": "short"}, opt_sp)
    ids = {o.validator_id for o in outcomes}
    assert "l3_plan_length_floor" in ids
    assert "l3_plan_verbatim_repeat" in ids


# ===========================================================================
# 9. L1 layout validators — one parametrized hard-failure family
# ===========================================================================


@pytest.mark.parametrize(
    "layout,expected_validator_id",
    [
        # Missing a mandatory placeholder.
        (
            L1Layout(task_intent=["task_context"], problem_description=["rendered_prompt"]),
            "l1_layout_missing_mandatory",
        ),
        # Unknown placeholder in a slot.
        (
            L1Layout(
                task_intent=["task_context", "made_up_signal"],
                problem_description=["rendered_prompt", "pipeline_param_catalogue", "plan"],
            ),
            "l1_layout_unknown_placeholder",
        ),
        # Duplicate placeholder within one slot.
        (
            L1Layout(
                task_intent=["task_context", "task_context"],
                problem_description=["rendered_prompt", "pipeline_param_catalogue", "plan"],
            ),
            "l1_layout_dups_within_slot",
        ),
    ],
)
def test_layout_hard_failures(layout, expected_validator_id):
    result = validate_l1_layout(layout, spec=NODE_LAYOUTS["l1_generate"])
    assert result.is_valid is False
    assert expected_validator_id in {o.validator_id for o in result.outcomes}


def test_resolve_node_layout_l4_edit_and_guard_rail():
    """Slice 6 Arc 3: the outer L4 layout edit rides the per-node override channel —
    a valid edit merges onto the floor; a mandatory-dropping edit rolls back (guard rail)."""
    from promptpotter.application.optimization.dispatch.llm_call.prompts import (
        resolve_node_layout,
        set_optimizer_prompt_overrides,
    )

    floor = NODE_LAYOUTS["l1_critique"].floor
    try:
        # No override bound → the floor, untouched.
        set_optimizer_prompt_overrides(None)
        assert resolve_node_layout("l1_critique") == floor

        # Valid edit (keeps mandatory `diagnostics`, all names in `possible`) → merges.
        set_optimizer_prompt_overrides(
            {"l1_critique": {"layout": {"problem_description": ["diagnostics", "axis_memory"]}}}
        )
        applied = resolve_node_layout("l1_critique")
        assert applied.problem_description == ["diagnostics", "axis_memory"]
        assert applied != floor

        # Guard rail: dropping the mandatory `diagnostics` rolls back to the floor.
        set_optimizer_prompt_overrides(
            {"l1_critique": {"layout": {"problem_description": ["axis_memory"]}}}
        )
        assert resolve_node_layout("l1_critique") == floor
    finally:
        set_optimizer_prompt_overrides(None)


# ===========================================================================
# 10. PoBB posterior gate + leader eligibility
# ===========================================================================


def _measurements(scores: list[float], sample_ids: list[int] | None = None) -> list[dict]:
    """Minimal QueryMeasurement-shaped dicts for PoBB tests. ``hit`` (what the θ
    elimination fit reads) is derived from the per-sample fitness (>0.5 == hit)."""
    ids = sample_ids if sample_ids is not None else list(range(len(scores)))
    return [
        {"sample_id": sid, "fitness": float(s), "hit": s > 0.5}
        for sid, s in zip(ids, scores, strict=True)
    ]


_DUMMY_SP = JobSearchPoint()


def test_pobb_check_gates_elimination_on_posterior():
    """PoBB.check() fires when the paired-difference posterior clears the ε gate."""
    check_sep = PoBBCheck(PoBBConfig(n_min=4, epsilon=0.05), n_samples=20, delta_scale={})
    check_sep.register_completed(_measurements([1.0] * 20), candidate_id="winner", sp=_DUMMY_SP)
    check_sep.set_current("loser")
    sig = check_sep.check(_measurements([0.0] * 5), candidate_idx=1, n_total_candidates=2)
    assert sig is not None
    assert sig.check_name == "elimination"
    cr = sig.check_result
    assert cr["leader_id"] == "winner"
    assert cr["p_best"] < 0.05
    assert "p_best_snapshot" in cr
    assert cr["epsilon"] == pytest.approx(0.05)


def test_pobb_locks_in_dominant_leader():
    """Current candidate dominating prior past lock_in_n_min fires LEADER_LOCKED."""
    check = PoBBCheck(
        PoBBConfig(n_min=4, epsilon=0.05, lock_in=0.95, lock_in_n_min=8, leader_lock_in=True),
        n_samples=20,
        delta_scale={},
    )
    check.register_completed(_measurements([0.0] * 20), candidate_id="weak_prior", sp=_DUMMY_SP)
    check.set_current("strong_current")
    sig = check.check(_measurements([1.0] * 8), candidate_idx=1, n_total_candidates=3)
    assert sig is not None
    assert sig.target == EscalationTarget.LEADER_LOCKED
    cr = sig.check_result
    assert cr["leader_id"] == "weak_prior"
    assert cr["p_best"] >= 0.95
    assert cr["queries_scored"] == 8
    # Two candidates remain unscored (idx=1 of 3).
    assert sig.candidates_skipped == 1


async def test_paired_pobb_breaks_lucky_prefix_leader_trap():
    """Lucky-prefix 100% leader must NOT auto-eliminate a candidate measured on hard samples.

    Round 1 leader-locks at 8/8 on the easy prefix {0..7}. Round 2 candidate
    sees the sorter's HARD subset {9,12,13,14,8} — samples the leader was never
    scored on. Unpaired PoBB would flatten the candidate to p_best ≈ 0 and
    unfairly eliminate it. Paired PoBB fixes this by (a) excluding priors that
    don't cover the candidate's set when no backfill is wired, and (b)
    backfilling the leader on the missing samples when a backfill_fn IS wired.
    """
    leader_samples = [0, 1, 2, 3, 4, 5, 6, 7]
    candidate_samples = [9, 12, 13, 14, 8]  # disjoint from leader; AIME's hard sorter order.

    # --- Branch (a): no backfill_fn ⇒ incomplete prior is excluded, no elimination.
    check_no_backfill = PoBBCheck(PoBBConfig(n_min=4, epsilon=0.05), n_samples=20, delta_scale={})
    check_no_backfill.register_completed(
        _measurements([1.0] * 8, sample_ids=leader_samples),
        candidate_id="R1_lucky_winner",
        sp=_DUMMY_SP,
    )
    check_no_backfill.set_current("R2_challenger")
    sig = check_no_backfill.check(
        _measurements([0.0] * 5, sample_ids=candidate_samples),
        candidate_idx=0,
        n_total_candidates=6,
    )
    assert sig is None, (
        "lucky-prefix leader incomplete on candidate's hard samples must NOT cause "
        "elimination — old unpaired code would have unfairly fired here"
    )

    # --- Branch (b): backfill_fn returns honest leader scores on the hard set ⇒ paired
    # comparison shows the leader is not actually 100% (it misses 4/5), and the
    # candidate (0/5) is no longer overwhelmingly dominated — no elimination.
    backfill_calls: list[tuple[int, ...]] = []

    async def _stub_backfill(_sp, samples):
        backfill_calls.append(tuple(s.id for s in samples))
        # Leader misses 4/5 hard samples; only #13 is hit. Mirrors origin's
        # behavior on the same samples in the real cycle.
        truth = {9: False, 12: False, 13: True, 14: False, 8: False}
        return [{"sample_id": s.id, "fitness": 1.0 if truth[s.id] else 0.0} for s in samples]

    check_paired = PoBBCheck(
        PoBBConfig(n_min=4, epsilon=0.05), n_samples=20, delta_scale={}, backfill_fn=_stub_backfill
    )
    check_paired.register_completed(
        _measurements([1.0] * 8, sample_ids=leader_samples),
        candidate_id="R1_lucky_winner",
        sp=_DUMMY_SP,
    )
    for sid in candidate_samples:
        await check_paired.backfill_for_sample(Sample(id=sid, query="q", ground_truth="t"))

    assert backfill_calls == [(9,), (12,), (13,), (14,), (8,)]
    leader_paired = check_paired.priors_by_sample["R1_lucky_winner"]
    assert all(str(sid) in leader_paired for sid in candidate_samples)

    check_paired.set_current("R2_challenger")
    sig = check_paired.check(
        _measurements([0.0] * 5, sample_ids=candidate_samples),
        candidate_idx=0,
        n_total_candidates=6,
    )
    # Leader honest mean 1/5; candidate 0/5 — gap small ⇒ p_best must stay ≥ ε=0.05.
    if sig is not None:
        assert sig.check_result["p_best"] >= 0.05


def _cs(
    *,
    candidate_id: str,
    accuracy: float,
    escalation_aborted: bool = False,
    elimination_stopped: bool = False,
    degradation_context: dict | None = None,
    elimination_context: dict | None = None,
) -> ScoredCandidate:
    return ScoredCandidate(
        candidate_id=candidate_id,
        label=candidate_id,
        changes_description="",
        accuracy=accuracy,
        composite_fitness=accuracy,
        hits=int(accuracy * 20),
        total=20,
        evaluators={},
        escalation_aborted=escalation_aborted,
        elimination_stopped=elimination_stopped,
        degradation_context=degradation_context or {},
        elimination_context=elimination_context or {},
    )


def test_leader_eligibility_bars_invalid_measurement_not_stops():
    """Winner-selection eligibility: fatal degradation disqualifies; a true PoBB *loss*
    (p_best < epsilon, lead not locked) disqualifies — the eliminator's own verdict that the
    candidate isn't the best; a LEADER_LOCKED stop stays eligible; a clean loser stays eligible.
    """
    fatal = _cs(
        candidate_id="C1.3",
        accuracy=0.8333,
        escalation_aborted=True,
        elimination_stopped=True,
        degradation_context={"fatal": True, "dominant_warning": "llm_only:empty_response"},
    )
    # A STOP IS NOT A VERDICT. A PoBB-stopped candidate stays electable: the stop said
    # "more samples will not change the answer", which is a budget fact, not a ranking one.
    # Reading it as a loss cost a real round — a candidate cut at 19/28 carried a genuine
    # +0.099 theta lift over origin and was the best thing measured, but its stop recorded
    # `p_best: 0.0` (a placeholder the futility gate never computed) and eligibility read
    # that as "PoBB says it lost". Every candidate in that round stopped the same way, so
    # the round crowned nobody. SILENT: `improved=False`, no winner, no reason recorded, and
    # the loop reports a flat cycle rather than a discarded improvement.
    pobb_stopped = _cs(
        candidate_id="C1.2",
        accuracy=0.40,
        elimination_stopped=True,
        elimination_context={"p_best": 0.048, "epsilon": 0.05, "leader_locked": False},
    )
    leader_locked = _cs(
        candidate_id="C1.1",
        accuracy=0.55,
        elimination_stopped=True,
        elimination_context={"p_best": 0.96, "epsilon": 0.05, "leader_locked": True},
    )
    clean_loser = _cs(candidate_id="C1.4", accuracy=0.45)

    # Only INVALID measurement disqualifies; ranking is the election's job alone.
    assert not is_leader_eligible(fatal)
    assert is_leader_eligible(pobb_stopped)
    assert is_leader_eligible(leader_locked)
    assert is_leader_eligible(clean_loser)


@pytest.mark.parametrize(
    ("statuses", "warnings", "kind", "node"),
    [
        # The lca-bom-termnorm shape: entity_profiling hard-broke (source-stamped
        # ``kind=structural``), web_search is silent collateral (failed, NO warning) —
        # attribution must follow the warning-bearing node, never the cascade-failed one.
        (
            {"web_search": "failed", "entity_profiling": "failed"},
            [
                {
                    "step": "entity_profiling",
                    "code": "schema_invalid",
                    "message": "bad schema",
                    "kind": "structural",
                }
            ],
            "structural",
            "entity_profiling",
        ),
        # A 429 rate-limit, backend-stamped ``kind=transient`` → transient.
        (
            {"entity_profiling": "failed"},
            [
                {
                    "step": "entity_profiling",
                    "code": "rate_limited",
                    "message": "429",
                    "kind": "transient",
                }
            ],
            "transient",
            "entity_profiling",
        ),
        # TermNorm 5xx → ``server_error`` stamped transient, not a structural break.
        (
            {"entity_profiling": "failed"},
            [
                {
                    "step": "entity_profiling",
                    "code": "server_error",
                    "message": "503",
                    "kind": "transient",
                }
            ],
            "transient",
            "entity_profiling",
        ),
        # The exact false-alarm the source-stamp fixes: a websearch ``scrape_failed``
        # the old frozenset omitted. Stamped transient → transient noise, NOT critical.
        (
            {"web_search": "degraded"},
            [
                {
                    "step": "web_search",
                    "code": "scrape_failed",
                    "message": "0 usable",
                    "kind": "transient",
                }
            ],
            "transient",
            "web_search",
        ),
        # Unknown/missing ``kind`` → SKIPPED, never defaulted to structural (the shadow-
        # taxonomy bug). The warned node is still "explained", and with a clean status
        # there is no silent-failed fallback → no verdict at all.
        (
            {"entity_profiling": "success"},
            [{"step": "entity_profiling", "code": "some_new_code", "message": "?"}],
            None,
            None,
        ),
        # Hard-failed node with no explanatory warning → structural, best-effort attribution.
        ({"web_search": "failed"}, [], "structural", "web_search"),
        # Fully clean → no degradation, no cause.
        ({"fuzzy_matching": "success", "token_matching": "skipped"}, [], None, None),
    ],
)
def test_classify_sample_failure_attributes_the_warning_bearing_node(
    statuses, warnings, kind, node
):
    """Structural-vs-transient reads the backend's **source-stamped** ``WarningDict.kind``
    (no PromptPotter code taxonomy; unknown/missing kind is SKIPPED, never structural),
    and the causing node follows the WARNING, so silent-collateral failed nodes can't
    outvote the node that actually broke."""
    from promptpotter.domain.results_health import classify_sample_failure

    assert classify_sample_failure(statuses, warnings) == (kind, node)


def test_compute_round_health_names_the_structural_cause_not_collateral():
    """End-to-end on the lca-bom-termnorm shape: web_search is silently ``failed``
    (collateral, no warning) while entity_profiling carries the structural warning.
    The verdict must name entity_profiling as ``dominant_node`` — the prior
    tally-by-failed-status picked web_search by 12-12 tie + insertion order."""
    from promptpotter.domain.results_health import compute_round_health

    def _struct_fail() -> dict:
        return {
            "hit": False,
            "pipeline_data": {
                "diagnostics": {
                    "step_statuses": {"web_search": "failed", "entity_profiling": "failed"},
                    "warnings": [
                        {
                            "step": "entity_profiling",
                            "code": "schema_invalid",
                            "message": "max completion tokens reached",
                            "kind": "structural",
                        }
                    ],
                }
            },
        }

    def _clean_hit() -> dict:
        return {
            "hit": True,
            "pipeline_data": {"diagnostics": {"step_statuses": {"entity_profiling": "success"}}},
        }

    results = [_struct_fail() for _ in range(12)] + [_clean_hit() for _ in range(8)]
    h = compute_round_health(results=results, prior_healths=[])
    assert h is not None
    assert h.grade == "critical"
    assert h.dominant_node == "entity_profiling"
    assert h.suggested_action and "entity_profiling" in h.suggested_action
    assert "web_search" not in (h.suggested_action or "")


def test_evidence_starved_round_grades_critical_without_auto_halting():
    """The lca-bom-termnorm failure mode: web_search dies mid-run (Brave quota), so
    half the round's samples carry a per-sample ``transient`` web_search warning —
    each defensible alone (a rate-limit IS recoverable) but a flood of the SAME
    enricher's failures is systemic at the round level. The OLD verdict graded this
    ``degraded`` (transient noise) and the loop ground on; the round-level per-node
    rate must lift it to ``critical``/``evidence_starved`` and name web_search.

    AND it must NEVER auto-halt: the deterministic health→StopReason tripwire
    (the only one) must stay blind to ``evidence_starved`` — the stop authority
    belongs to the intelligent tiers, not this backend-coupled signal (R-48)."""
    from promptpotter.application.runner.termination import backend_unreachable_tripped
    from promptpotter.domain.results_health import compute_round_health

    def _web_starved() -> dict:
        return {
            "hit": False,
            "pipeline_data": {
                "diagnostics": {
                    "step_statuses": {"web_search": "degraded", "entity_profiling": "degraded"},
                    "warnings": [
                        {
                            "step": "web_search",
                            "code": "no_results",
                            "message": "No web evidence — Brave rate limit exceeded",
                            "kind": "transient",
                        }
                    ],
                }
            },
        }

    def _clean_hit() -> dict:
        return {
            "hit": True,
            "pipeline_data": {"diagnostics": {"step_statuses": {"web_search": "success"}}},
        }

    results = [_web_starved() for _ in range(10)] + [_clean_hit() for _ in range(10)]
    h = compute_round_health(results=results, prior_healths=[])
    assert h is not None
    assert h.grade == "critical"
    assert h.reasons == ["evidence_starved"]
    assert h.dominant_node == "web_search"
    assert h.node_failure_rates.get("web_search") == 0.5
    assert h.suggested_action and "web_search" in h.suggested_action
    # The deterministic tripwire must NOT consume this signal — no auto-halt (R-48).
    assert backend_unreachable_tripped(h) is None


def test_unscoreable_origin_grades_critical_and_trips_the_origin_gate():
    """The JustLogic failure mode: the pipeline RUNS (``step_statuses`` success, no
    warning) but emits no extractable label → ``predicted == NO_RESULT`` on every
    sample. The backend calls this a success, so the structural/transient channel is
    blind to it — the OLD verdict graded the origin ``healthy`` and the loop wasted
    rounds optimizing a prompt whose every output was unscoreable. The PP-owned
    no-result rate must lift it to ``critical``/``unscoreable`` and trip the origin
    gate, while a wrong-but-extractable round (real labels, just wrong) must NOT."""
    from promptpotter.application.runner.termination import origin_gate_tripped
    from promptpotter.domain.phases import StopReason
    from promptpotter.domain.results_health import compute_round_health

    def _row(predicted: str, *, hit: bool) -> dict:
        return {
            "predicted": predicted,
            "hit": hit,
            "pipeline_data": {"diagnostics": {"step_statuses": {"llm_only": "success"}}},
        }

    # All 20 succeeded but produced no parseable label → unscoreable floor.
    no_result_rows = [_row("NO_RESULT", hit=False) for _ in range(20)]
    h = compute_round_health(results=no_result_rows, prior_healths=[])
    assert h is not None
    assert h.grade == "critical"
    assert h.reasons == ["unscoreable"]
    assert h.no_result_count == 20
    assert h.suggested_action and "answer_format" in h.suggested_action
    # Critical → halts even in the least-strict armed mode; this is the guarantee
    # that the broken origin never silently enters L1.
    assert origin_gate_tripped(h, "critical_only") == StopReason.ORIGIN_GATE

    # A hard task that emits REAL labels (just wrong) is a measurement, not a broken
    # floor — it must stay gradable on accuracy, never flagged ``unscoreable``.
    wrong_rows = [_row("FALSE", hit=False) for _ in range(20)]
    hw = compute_round_health(results=wrong_rows, prior_healths=[])
    assert hw is not None
    assert hw.no_result_count == 0
    assert "unscoreable" not in hw.reasons


def test_unmeasured_origin_grades_critical_but_a_non_origin_round_abstains():
    """The L4 failure mode: round-0 scoring produced ZERO rows (``total==0``) — a crash
    or empty connector return, not a wrong answer. The OLD ``has_program`` sniff silently
    skipped scoring the L4 outer origin (empty prose, empty node configs), landing
    ``total=0``; if that graded ``healthy``/abstained, candidates would be elected against
    NO baseline — the silent, irreversible harm. Only the ORIGIN's ``total=0`` is critical
    (halt-and-decide); a non-origin round that measured nothing genuinely abstains (``None``),
    never a fabricated clean verdict."""
    from promptpotter.application.runner.termination import origin_gate_tripped
    from promptpotter.domain.phases import StopReason
    from promptpotter.domain.results_health import compute_round_health

    ho = compute_round_health(results=[], prior_healths=[], is_origin=True)
    assert ho is not None
    assert ho.grade == "critical"
    assert ho.reasons == ["origin_unmeasured"]
    # Halts even in the least-strict armed mode — the baseline-less election never begins.
    assert origin_gate_tripped(ho, "critical_only") == StopReason.ORIGIN_GATE

    # A non-origin round that measured nothing abstains — not a fabricated ``healthy``.
    assert compute_round_health(results=[], prior_healths=[]) is None


@pytest.mark.parametrize(
    ("attempted", "structural", "transient", "prior_clean", "consec", "grade", "reasons"),
    [
        # lca-bom-termnorm origin: 60% structural failures, untested → critical/structural.
        (20, 12, 0, 0, 1, "critical", ["structural"]),
        # First-sight structural at an untested config, even at low rate → critical.
        (20, 1, 0, 0, 1, "critical", ["structural_untested"]),
        # The SAME isolated structural failure deep in a proven campaign → NOT critical.
        (20, 1, 0, 5, 1, "healthy", []),
        # Sustained: 3 consecutive degraded rounds escalates on persistence alone.
        (20, 0, 5, 5, 3, "critical", ["persistent"]),
        # Transient noise on a proven pipeline above the rate floor → degraded, quiet.
        (20, 0, 5, 5, 1, "degraded", ["degraded"]),
        # A clean round at an UNTESTED config is healthy. It used to grade ``degraded``
        # purely because a 20-sample Wilson interval is wide — precision is not health,
        # and that verdict was what kept ``prior_clean_rounds`` pinned at 0 forever.
        (20, 0, 0, 0, 0, "healthy", []),
    ],
)
def test_degradation_health_is_context_aware(
    attempted, structural, transient, prior_clean, consec, grade, reasons
):
    """The verdict grades the SAME degradation differently by track record, and only a
    ``critical`` grade carries an operator-facing suggested action (never auto-stops)."""
    from promptpotter.domain.results_health import compute_degradation_health

    h = compute_degradation_health(
        attempted=attempted,
        structural_count=structural,
        transient_count=transient,
        prior_clean_rounds=prior_clean,
        consecutive_degraded_rounds=consec,
        dominant_node="entity_profiling",
    )
    assert h is not None
    assert h.grade == grade
    assert h.reasons == reasons
    assert (h.suggested_action is not None) is (grade == "critical")


def test_origin_verdict_is_first_in_the_l1_track_record():
    """The origin (round 0) is the floor every L1 round improves on, so its verdict
    must lead the track record. ``Cycle.rounds`` omits the origin (1-indexed L1
    trajectory), but on resume ``replay_priors`` leaves a round-0 entry in it — the
    assembly must take the origin from ``origin_health`` and drop that round-0 entry
    (no double-count) plus the round being closed. End-to-end: an origin that graded
    ``critical`` makes a degrading L1 round see ≥3 consecutive → ``persistent``."""
    from promptpotter.domain.results import RoundResult
    from promptpotter.domain.results_health import (
        assemble_prior_healths,
        compute_degradation_health,
        compute_round_health,
    )

    def _health(grade):
        return compute_degradation_health(
            attempted=20,
            structural_count=(0 if grade == "healthy" else 5),
            transient_count=0,
            prior_clean_rounds=(5 if grade != "critical" else 0),
            consecutive_degraded_rounds=1,
        )

    def _round(round_num: int, grade: str | None) -> RoundResult:
        return RoundResult(
            round=round_num,
            label=f"C{round_num}",
            accuracy=0.75,
            composite_fitness=0.75,
            hits=15,
            total=20,
            improved=False,
            prompt_fields={},
            candidates_scored=1,
            health=(_health(grade) if grade else None),
        )

    # Round 0 IS a round: its verdict is read off the trajectory, never a sidecar,
    # and it is first because it is oldest. Fresh and resumed hold the same list.
    rounds = [_round(0, "critical"), _round(1, "degraded"), _round(2, "degraded")]
    fresh = assemble_prior_healths(rounds, 3)
    assert fresh[0] is rounds[0].health and len(fresh) == 3
    # The round being CLOSED is the only one dropped — closing R1 still counts the origin.
    assert assemble_prior_healths(rounds, 1) == [rounds[0].health, rounds[2].health]

    # Degrading R3 on top of (critical origin, degraded R1, degraded R2) → 3 consecutive.
    transient_rows = [
        {"pipeline_data": {"diagnostics": {"step_statuses": {"web_search": "degraded"}}}}
        for _ in range(5)
    ] + [{"pipeline_data": {"diagnostics": {}}} for _ in range(15)]
    h = compute_round_health(results=transient_rows, prior_healths=fresh)
    assert h is not None and h.grade == "critical" and "persistent" in h.reasons


def test_ungraded_prior_round_is_transparent_to_the_track_record():
    """An ungraded prior verdict (``None`` — a probe round, or a round that measured
    zero samples) must be TRANSPARENT to the track record: it neither counts as a
    clean round nor breaks the consecutive-degraded chain. A probe interleaved in a
    ``degraded → probe → degraded`` run must still reach the ``persistent`` critical;
    the probe's ``None`` must not fake a clean prior that suppresses ``untested``."""
    from promptpotter.domain.results_health import (
        compute_degradation_health,
        compute_round_health,
    )

    degraded = compute_degradation_health(
        attempted=20,
        structural_count=0,
        transient_count=5,
        prior_clean_rounds=5,
        consecutive_degraded_rounds=1,
    )
    assert degraded is not None and degraded.grade == "degraded"

    # Priors oldest→newest: degraded, probe(None), degraded. The current round is also
    # degrading → 3 consecutive once the probe is skipped (not counted, not a break).
    transient_rows = [
        {"pipeline_data": {"diagnostics": {"step_statuses": {"web_search": "degraded"}}}}
        for _ in range(5)
    ] + [{"pipeline_data": {"diagnostics": {}}} for _ in range(15)]
    h = compute_round_health(results=transient_rows, prior_healths=[degraded, None, degraded])
    assert h is not None and h.grade == "critical" and "persistent" in h.reasons


def test_all_errored_round_is_critical_not_unmeasured() -> None:
    """An all-errored round ATTEMPTED work — it must grade critical/backend_unreachable,
    not slip through as "nothing measured" (None) now that ``total`` counts only
    evidence rows. Silent harm: a dead backend would produce rounds with no health
    banner and no suggested_action — the loop burns rounds against an outage nobody
    is told about."""
    from promptpotter.domain.results_health import compute_round_health

    rows = [
        {"error": "connect timeout", "error_category": "CONNECTION", "pipeline_data": None}
        for _ in range(10)
    ]
    h = compute_round_health(results=rows, prior_healths=[])
    assert h is not None and h.grade == "critical" and "backend_unreachable" in h.reasons
    assert h.samples == 10 and h.suggested_action is not None


def test_degradation_health_none_when_unmeasured():
    """Zero samples ⇒ no verdict (None), not a fabricated healthy grade."""
    from promptpotter.domain.results_health import compute_degradation_health

    assert (
        compute_degradation_health(
            attempted=0,
            structural_count=0,
            transient_count=0,
            prior_clean_rounds=0,
            consecutive_degraded_rounds=0,
        )
        is None
    )


# ===========================================================================
# 11. L2 behaviour conformance
# ===========================================================================


def test_l2_behavior_checks_score_conformant_vs_stub_fires():
    """Conformant L2 fire passes every behaviour check; stub fails them; no-fire ⇒ no results."""
    from promptpotter.application.optimization.validators.l1_behavior import ValidatorContext
    from promptpotter.application.optimization.validators.l2_behavior import run_all_l2_checks

    def _round(response: dict) -> dict:
        return {"nodes": {"l2_context": {"output": {"response": response}}}}

    ctx = ValidatorContext(
        round_num=3,
        opt_sp={"task_context": {"key_challenges": "the prior framing"}},
    )

    conformant = run_all_l2_checks(
        _round(
            {
                "rationale": "Axis instruction stalled 3 rounds; sample #14 regressed — refocus.",
                "axis_targeted": "instruction",
                # A real L1 surface — the framing is frozen, so prose no longer counts as a
                # touch (which is exactly what made this check vacuous before).
                "l1_overrides": {"n_variants": 3},
            }
        ),
        ctx,
    )
    assert len(conformant) == 3
    assert all(c.passed for c in conformant)

    stub = run_all_l2_checks(
        _round({"rationale": "ok", "axis_targeted": "", "l1_overrides": {}}),
        ctx,
    )
    failed = {c.check_id for c in stub if not c.passed}
    assert {"l2_rationale_substantive", "l2_evidence_anchored"} <= failed

    assert run_all_l2_checks({"nodes": {"l1_generate": {}}}, ctx) == []


# ===========================================================================
# 12. compute_round_diagnostics — pure analysis over scoring data
# ===========================================================================

from promptpotter.application.optimization.round_analysis import (  # noqa: E402
    compute_round_diagnostics,
)
from promptpotter.domain.results import RoundResult  # noqa: E402


def _round_result(round_num: int, accuracy: float, results: list[dict]) -> RoundResult:
    return RoundResult(
        round=round_num,
        label=f"round_{round_num}",
        accuracy=accuracy,
        composite_fitness=accuracy,
        hits=sum(1 for r in results if r.get("fitness") == 1.0),
        total=len(results),
        improved=False,
        prompt_fields={},
        results=results,
        candidates_scored=1,
    )


def _hit(query: str, gt: str) -> dict:
    return {"query": query, "ground_truth": gt, "fitness": 1.0, "predicted": gt}


def _miss(query: str, gt: str) -> dict:
    return {"query": query, "ground_truth": gt, "fitness": 0.0, "predicted": "?"}


def test_diag_rank_lookup_no_schema_lands_everything_not_found():
    results = [_hit("q1", "a"), _miss("q2", "b"), _miss("q3", "c")]
    rr = _round_result(0, 0.33, results)
    diag = compute_round_diagnostics(rr, [rr], pipeline_schema=None)

    assert diag.n_valid == 3
    assert diag.rank_buckets["1"] == 0
    assert diag.rank_buckets["not_found"] == 3
    assert diag.top_k_accuracy[1] == 0.0
    assert diag.top_k_accuracy[10] == 0.0


def test_diag_trajectory_classification_picks_up_plateau():
    rounds = [
        _round_result(0, 0.50, [_hit("q1", "a")]),
        _round_result(1, 0.51, [_hit("q1", "a")]),
        _round_result(2, 0.51, [_hit("q1", "a")]),
        _round_result(3, 0.51, [_hit("q1", "a")]),
    ]
    diag = compute_round_diagnostics(rounds[-1], rounds, pipeline_schema=None)
    assert diag.trajectory in {"plateau", "ceiling"}


# ===========================================================================
# 13. Adaptive-queue Bayesian math (θ posterior, decision-info, pick-score)
# ===========================================================================


def test_update_theta_posterior_hits_raise_mean_misses_lower_it() -> None:
    """HIT raises μ, MISS lowers it; large se_δ damps the update."""
    from promptpotter.application.intelligence.adaptive_queue_mechanism import (
        update_theta_posterior,
    )

    mu0, var0 = 0.0, 1.0
    mu_hit, var_hit = update_theta_posterior(mu0, var0, 0.0, 0.0, hit=True)
    mu_miss, var_miss = update_theta_posterior(mu0, var0, 0.0, 0.0, hit=False)

    assert mu_hit > mu0
    assert mu_miss < mu0
    assert mu_hit == pytest.approx(-mu_miss, abs=1e-9)
    assert var_hit < var0 and var_miss < var0

    mu_hit_uncertain, _ = update_theta_posterior(mu0, var0, 0.0, 2.0, hit=True)
    assert 0.0 < mu_hit_uncertain < mu_hit


def test_decision_information_gain_peaks_at_candidate_ability() -> None:
    """The most decision-informative sample sits near the candidate's own ability — not the flanks."""
    from promptpotter.application.intelligence.adaptive_queue_mechanism import (
        decision_information_gain,
    )

    # Candidate μ_c=0, seed μ_s=1. δ near μ_c vs flanks.
    near = decision_information_gain(0.0, 1.0, mu_s=1.0, var_s=1.0, delta_s=0.0, se_delta_s=0.1)
    far_easy = decision_information_gain(
        0.0, 1.0, mu_s=1.0, var_s=1.0, delta_s=-4.0, se_delta_s=0.1
    )
    far_hard = decision_information_gain(0.0, 1.0, mu_s=1.0, var_s=1.0, delta_s=4.0, se_delta_s=0.1)
    assert near > far_easy
    assert near > far_hard


def test_pick_value_explores_undermeasured_headroom_at_seed_centred_pick() -> None:
    """At the first pick the candidate prior is seed-centred (μ_c=μ_s ⇒ p₀=0.5 everywhere), so the
    verdict-only decision-IG degenerates to 'prefer lowest se_δ' and *starves* the unmeasured
    headroom. The δ-learning term in ``pick_value`` must flip that: an under-measured sample with a
    genuinely uncertain outcome out-ranks an equally-positioned well-measured one — WITHOUT
    re-promoting a settled always-easy sample (p→1)."""
    from promptpotter.application.intelligence.adaptive_queue_mechanism import (
        decision_information_gain,
        pick_value,
    )

    seed = 0.5
    # Same δ≈μ_c (uncertain outcome) — differ only in measurement precision.
    headroom = {"delta_s": 0.5, "se_delta_s": 1.2}  # under-measured (wide δ): the real headroom
    well_measured = {"delta_s": 0.5, "se_delta_s": 0.1}  # already pinned
    settled_easy = {"delta_s": -4.0, "se_delta_s": 0.1}  # origin aces it (p→1): no headroom

    def dig(s: dict[str, float]) -> float:
        return decision_information_gain(seed, 1.0, seed, 1.0, s["delta_s"], s["se_delta_s"])

    def pv(s: dict[str, float]) -> float:
        return pick_value(seed, 1.0, seed, 1.0, s["delta_s"], s["se_delta_s"])

    # The bug, revealed: verdict-only IG ranks the well-measured sample ABOVE the headroom.
    assert dig(well_measured) > dig(headroom)
    # The fix: total acquisition flips it — explore the under-measured headroom first.
    assert pv(headroom) > pv(well_measured)
    # Anti-pathology: a settled always-easy sample is NOT promoted over the headroom.
    assert pv(headroom) > pv(settled_easy)


def test_pick_score_artifact_ranks_contested_above_settled() -> None:
    """``pick_score.per_sample`` ranks contestable samples above settled-all-HIT ones."""
    from promptpotter.application.intelligence.hard_sample_sorter import (
        ARTIFACT_SCHEMA_VERSION,
        build_hard_samples_artifact_from_observations,
    )

    obs = [
        # Sample 1: settled-easy (all HIT); sample 2: split (contestable).
        *[Observation(candidate_id=c, sample_id=1, response=1.0) for c in "abcd"],
        Observation(candidate_id="a", sample_id=2, response=1.0),
        Observation(candidate_id="b", sample_id=2, response=0.0),
        Observation(candidate_id="c", sample_id=2, response=1.0),
        Observation(candidate_id="d", sample_id=2, response=0.0),
    ]
    artifact = build_hard_samples_artifact_from_observations(obs)
    assert artifact["schema_version"] == ARTIFACT_SCHEMA_VERSION == 5

    per_sample = artifact["pick_score"]["per_sample"]
    assert per_sample["2"] > per_sample["1"]
    # sample_order = the next round's executed order (build_round_order seeded by
    # the best candidate, who hits both samples → hit stratum desc δ: harder 2 first).
    assert artifact["pick_score"]["sample_order"] == [2, 1]


def test_build_round_order_fronts_win_opportunities_with_hit_probes() -> None:
    """The shared round order is the elimination gate's evidence pipeline: seed-miss
    (win-opportunity) samples front-loaded ascending-δ, a seed-hit regression probe at
    every 4th slot descending-δ, unknowns treated as opportunities, deterministic
    tie-breaks. Silent harm: a wrong order re-creates the tie-prefix blindness — the
    round completes, no error, and dead candidates ride their full budget again."""
    from promptpotter.application.intelligence.adaptive_queue_mechanism import build_round_order

    ids = list(range(24))
    # Seed hits 0-8; misses 9-22; sample 23 unmeasured by the seed (unclassified).
    seed_grades = dict.fromkeys(range(9), 1.0) | dict.fromkeys(range(9, 23), 0.0)
    ruler: dict[int, float | tuple[float, float]] = {sid: float(sid % 7) for sid in range(20)}

    order = build_round_order(seed_grades, ruler, ids)
    assert sorted(order) == ids
    # Deterministic: same inputs, same order.
    assert build_round_order(seed_grades, ruler, ids) == order

    hit_set = set(range(9))
    # Positions 4, 8, 12, 16 (1-indexed) carry seed-HIT probes while both strata remain.
    for pos in (4, 8, 12, 16):
        assert order[pos - 1] in hit_set, f"position {pos} should be a seed-hit probe"
    # All other early positions are win opportunities (seed-miss or unclassified —
    # the unmeasured sample 23 must ride the miss stratum, not the tail).
    non_probe_head = [order[i] for i in range(16) if (i + 1) % 4 != 0]
    assert all(sid not in hit_set for sid in non_probe_head)
    # MISS stratum walks ascending δ (cold/unknown entries at δ=0 first, ties by sid).
    miss_positions = [sid for sid in order if sid not in hit_set]
    miss_keys = [(float(ruler.get(sid, 0.0)), sid) for sid in miss_positions]  # type: ignore[arg-type]
    assert miss_keys == sorted(miss_keys)
    # HIT stratum walks descending δ (likeliest regression points first).
    hit_positions = [sid for sid in order if sid in hit_set]
    hit_keys = [(-float(ruler.get(sid, 0.0)), sid) for sid in hit_positions]  # type: ignore[arg-type]
    assert hit_keys == sorted(hit_keys)


def test_elimination_p_best_discriminates_on_graded_backend() -> None:
    """The PoBB ε-gate must read GRADED responses, not binarized hits.

    Silent harm: on a graded backend (L4 outer, reciprocal-rank) every ``hit`` is
    False, so a binarized gate fits identical all-0 θ for every arm and pins
    ``p_best = 0.5`` forever — elimination never discriminates, with no error.
    Graded inputs must separate a plainly-better candidate; binary inputs must be
    bit-identical to the historical hit-vector behavior.
    """
    from promptpotter.application.scoring.metrics import elimination_p_best

    sids = list(range(12))
    ruler: dict[int, float] = {}  # cold ruler — flat δ, the common early-cycle case

    # Graded regime: candidate consistently outscores the prior; hit would be all-0.
    strong = [0.66] * 12
    weak = [0.30] * 12
    p_best_strong, _ = elimination_p_best(strong, {"prior": weak}, sids, ruler)
    assert p_best_strong > 0.9, f"graded gate failed to discriminate: {p_best_strong}"
    p_best_weak, _ = elimination_p_best(weak, {"prior": strong}, sids, ruler)
    assert p_best_weak < 0.1
    # Identical grades ⇒ genuinely undecided.
    p_best_tie, _ = elimination_p_best(weak, {"prior": list(weak)}, sids, ruler)
    assert abs(p_best_tie - 0.5) < 1e-9

    # Binary regime: floats {0,1} are the same values the old bool vectors carried.
    cand_bits = [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 0.0, 0.0, 0.0, 0.0]
    prior_bits = [1.0, 1.0, 1.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    p_best_bin, _ = elimination_p_best(cand_bits, {"prior": prior_bits}, sids, ruler)
    p_best_bool, _ = elimination_p_best(
        [bool(b) for b in cand_bits],  # type: ignore[list-item]
        {"prior": [bool(b) for b in prior_bits]},  # type: ignore[dict-item]
        sids,
        ruler,
    )
    assert p_best_bin == p_best_bool  # bit-identical for binary datasets
    assert p_best_bin > 0.5


def test_mean_ci_bounds_and_degenerate_n() -> None:
    """``mean_ci`` is the always-on per-candidate composite CI (L4 variance fix) — a
    wrong bound would silently mislabel a real difference as noise or vice versa, with
    no visible symptom. Pin the degenerate n=0/n=1 cases and that the interval always
    brackets the mean."""
    assert mean_ci([]) == (0.0, 0.0, 0.0)

    mean, lo, hi = mean_ci([0.7])
    assert mean == 0.7
    assert lo < mean < hi  # n=1 still yields a real (non-degenerate) interval

    values = [0.6, 0.7, 0.65, 0.72, 0.68]
    mean, lo, hi = mean_ci(values)
    assert abs(mean - sum(values) / len(values)) < 1e-9
    assert lo < mean < hi
    assert lo < hi


def test_theta_accuracy_ci_warm_vs_cold_ruler() -> None:
    """The θ-implied whisker (the difficulty-adjusted band the candidate panel draws where the
    ruler is warm). A wrong band is silent — it mislabels a real ability gap as noise on the
    surface the operator reads to decide adoption. Pin that it brackets the θ point, stays in
    [0,1], and that a COLD/empty ruler returns None so the caller keeps the raw composite CI
    rather than dressing a difficulty-blind round as difficulty-adjusted."""
    from promptpotter.application.intelligence.exploration import (
        ruler_expected_accuracy,
        theta_accuracy_ci,
    )

    ruler = {1: -1.0, 2: 0.0, 3: 1.0, 4: 0.5, 5: -0.5, 6: 0.2}
    band = theta_accuracy_ci(0.3, 0.4, ruler)
    assert band is not None
    lo, hi = band
    mid = ruler_expected_accuracy(0.3, ruler)
    assert mid is not None
    assert 0.0 <= lo <= mid <= hi <= 1.0
    # A smaller SE gives a tighter band (the whole point — more evidence, narrower whisker).
    tight = theta_accuracy_ci(0.3, 0.1, ruler)
    assert tight is not None
    assert (tight[1] - tight[0]) < (hi - lo)
    # Cold ruler / missing θ or SE → None: the caller keeps the raw composite CI.
    assert theta_accuracy_ci(0.3, 0.4, {}) is None
    assert theta_accuracy_ci(None, 0.4, ruler) is None
    assert theta_accuracy_ci(0.3, None, ruler) is None


# --- task_context framing is frozen ------------------------------------------------------
# The framing fields are operator-authored evidence about the task. They used to be L2's
# rewrite surface AND were head-clipped at render, which silently amputated the operator's
# own conclusions on ~95% of renders. Both halves of that are now unrepresentable.


def test_merge_refuses_every_framing_field():
    """Each framing key is refused by name, with the mutable set named in the error."""
    tc = TaskDecomposition(key_challenges="the operator's measured finding")
    for field_name in FRAMING_FIELDS:
        with pytest.raises(ValueError, match="framing is frozen"):
            tc.merge({field_name: "a paraphrase from one round's critique"})
    # and the refusal never mutates the receiver
    assert tc.key_challenges == "the operator's measured finding"


def test_merge_still_accepts_the_spliced_fields_l1_owns():
    """`upstream_context` / `downstream_context` are spliced into the TARGET prompt, so a
    candidate carrying them is measured — that is L1's legitimate override surface."""
    tc = TaskDecomposition(domain="logic", upstream_context="before")
    out = tc.merge({"upstream_context": "after", "downstream_context": "then"})
    assert (out.upstream_context, out.downstream_context) == ("after", "then")
    assert out.domain == "logic"  # untouched framing survives the merge


def test_check_budget_names_the_field_its_length_and_the_file():
    over = TaskDecomposition(key_challenges="x" * (FRAMING_VALUE_BUDGET + 1))
    with pytest.raises(ValueError) as err:
        over.check_budget(source="datasets/foo/task_context.yaml")
    msg = str(err.value)
    assert "key_challenges" in msg and str(FRAMING_VALUE_BUDGET + 1) in msg
    assert "datasets/foo/task_context.yaml" in msg


def test_check_budget_ignores_non_framing_fields():
    """`raw_description` is the verbatim operator document — deliberately unbudgeted, since
    it is preserved on disk rather than rendered into a prompt."""
    TaskDecomposition(
        key_challenges="x" * FRAMING_VALUE_BUDGET,
        raw_description="y" * (FRAMING_VALUE_BUDGET * 10),
    ).check_budget(source="t")


def test_shipped_datasets_are_within_the_framing_budget():
    """The guard is only credible if what we ship passes it."""
    for path in Path("datasets").glob("*/task_context.yaml"):
        TaskDecomposition.from_dict(yaml.safe_load(path.read_text(encoding="utf-8"))).check_budget(
            source=str(path)
        )


def test_headline_best_is_always_a_number_something_measured():
    """The published headline may never exceed what the cycle actually measured.

    The silent harm, measured live on `justlogic-d234__f3af53`: the headline argmaxed
    `cumulative_accuracy`, a sample-keyed union of rows scored by DIFFERENT configurations.
    Round 7's 0.775 was 12 rows from round 6's config glued to 28 from round 7's, while the
    best candidate the run ever measured scored 0.679. The sidebar read `57%→78%`, the
    candidate chart read 0.679, every gate was green, and nothing raised for months — the
    docstrings on both sides asserted the union WAS "the incumbent rescored over every
    sample probed so far", a rescore that never happened.

    Because the round subset is the CONTESTED one, the carried rows are the easy tail the
    previous config scored ~100% on, so the bias has a direction: each configuration
    inherited its predecessor's perfect easy-tail score and a regression there was invisible.
    """
    # Shaped like `index.json::rounds[]`: each round's own measurement, plus the pooled
    # number the old basis would have published (never `max`-ed by anything now).
    rounds = [
        {"round": 0, "accuracy": 0.575, "pooled": 0.575},
        {"round": 6, "accuracy": 0.750, "pooled": 0.750},
        {"round": 7, "accuracy": 0.679, "pooled": 0.775},  # pooled > anything measured
    ]
    best, best_round = best_round_by_measured_accuracy(rounds)

    measured = {r["accuracy"] for r in rounds}
    assert best in measured, "headline must be a value some round actually scored"
    assert best == 0.750 and best_round == 6
    assert best < max(r["pooled"] for r in rounds), "the pooled basis outran every measurement"

    # A round with no accuracy doesn't back the headline (vs `or 0.0`, which could crown it).
    assert best_round_by_measured_accuracy([{"round": 1, "accuracy": None}]) == (0.0, None)
    assert best_round_by_measured_accuracy([]) == (0.0, None)


def test_outer_variance_split_names_the_lever_the_panel_needs() -> None:
    # The panel's `se` is computed from the spread of the per-cell diffs and nothing else, so it
    # cannot distinguish two opposite problems: cells that each measured themselves badly, versus
    # cells that genuinely disagree. They take opposite levers — sharpen the instrument, or widen
    # the panel/candidates — and the operator picks from this number. SILENT: every wrong value
    # here is a plausible-looking fraction that sends the next spend at the wrong problem.
    from promptpotter.domain.l4.verdict import CandidateInfo, compute_outer_verdict

    def rows(cells: dict[str, tuple[float, float]]) -> list[dict]:
        return [
            {
                "query": c,
                "fitness": (v + 1.0) / 3.0,
                "pipeline_data": {"mean_round_delta": v, "mean_round_delta_se": se},
            }
            for c, (v, se) in cells.items()
        ]

    cand = [
        CandidateInfo(
            candidate_id="v1",
            label="C1.1",
            changes_description="x",
            composite_fitness=0.5,
            is_winner=True,
        )
    ]

    def split(variant: dict, origin: dict):
        v = compute_outer_verdict(
            {"v1": rows(variant)}, cand, rows(origin), measurand_key="mean_round_delta"
        )
        assert v is not None
        return v.variance

    flat_origin = dict.fromkeys("abc", (0.0, 0.02))

    # Cells that AGREE (diffs .50/.52/.48) but each measured itself poorly: the panel is reading
    # its own noise, and buying more cells like these buys nothing. Share saturates at 1.
    noisy = split({"a": (0.50, 0.40), "b": (0.52, 0.40), "c": (0.48, 0.40)}, flat_origin)
    assert noisy is not None and noisy.estimation_share == 1.0
    assert noisy.estimation_sd > noisy.observed_sd

    # Cells that genuinely DIFFER (.10/.90/.50) while each is measured sharply: the spread is
    # signal about where this optimizer prompt works, not instrument noise.
    real = split({"a": (0.10, 0.02), "b": (0.90, 0.02), "c": (0.50, 0.02)}, flat_origin)
    assert real is not None and real.estimation_share < 0.05
    assert real.observed_sd == pytest.approx(0.4)  # SAMPLE sd (n-1), not the population one

    # BOTH ARMS' errors enter, in quadrature. The variant and the origin are separate inner
    # campaigns on the same cell, so counting only the variant's understates the diff's error by
    # sqrt(2) — which makes a noise-dominated panel read as half signal.
    assert real.estimation_sd == pytest.approx((2 * 0.02**2) ** 0.5)
    assert real.n_cells == 3

    # One shared cell has no spread to decompose: None, never a fabricated 0.0 (which reads as
    # "all signal" and would argue for spending on more cells at the exact moment it cannot know).
    assert split({"a": (0.5, 0.02)}, {"a": (0.0, 0.02)}) is None
    # A cell whose rows carry no SE is dropped rather than guessed at.
    bare = compute_outer_verdict(
        {"v1": [{"query": "a", "fitness": 0.5, "pipeline_data": {"mean_round_delta": 0.5}}]},
        cand,
        [{"query": "a", "fitness": 0.3, "pipeline_data": {"mean_round_delta": 0.0}}],
        measurand_key="mean_round_delta",
    )
    assert bare is not None and bare.variance is None


def test_outer_snr_pools_noise_and_refuses_to_guess_it() -> None:
    # Turns the hand-computed "the panel needs ~N cells" into a number derived from disk. It
    # reads UNKNOWN on today's corpus (no state has ever been measured twice on one cell), so a
    # wrong formula here stays invisible until the first repeat lands and then quietly sets the
    # spend. SILENT both ways: too small an N says a coin-flip panel can crown a winner; too
    # large says a working panel cannot.
    from promptpotter.application.optimizer_prompt_ranking import (
        RankedOptimizerPrompt,
        _Accum,
        _outer_snr,
    )

    def state(cells: dict[str, list[float]]) -> _Accum:
        a = _Accum({}, "s")
        a.cand_by_cell = dict(cells)
        return a

    def ranked(effect: float) -> RankedOptimizerPrompt:
        return RankedOptimizerPrompt(
            state_hash=f"h{effect}",
            label="C1.1",
            prompt_state={},
            provenance=[],
            per_cell_effects=[],
            anchor_effect=effect,
            ci_lo=effect,
            ci_hi=effect,
            n_cells=1,
            n_measurements=1,
        )

    # Two groups with the SAME spread pool to that spread — not to something between it and 0.
    snr = _outer_snr({"a": state({"c1": [0.0, 2.0], "c2": [1.0, 3.0]})}, [ranked(0.0), ranked(2.0)])
    assert snr.within_n_groups == 2 and snr.within_sd == pytest.approx(2**0.5)
    assert snr.between_sd == pytest.approx(2**0.5) and snr.between_n_states == 2
    # Equal noise and contrast ⇒ (2.8·σ/d)² ≈ 7.8 cells, rounded UP: a fractional cell cannot be
    # run, and rounding down would report a panel as sufficient one cell before it is.
    assert snr.n_cells_to_verdict == 8

    # A bigger group must carry more weight than a small one — pooling is on (n−1) df, never a
    # mean over per-group SDs, which would let one noisy pair outvote nine clean readings.
    lopsided = _outer_snr(
        {"a": state({"c1": [0.0, 0.0, 0.0, 0.0, 0.0], "c2": [0.0, 4.0]})},
        [ranked(0.0), ranked(1.0)],
    )
    assert lopsided.within_sd is not None
    assert lopsided.within_sd == pytest.approx((8.0 / 5) ** 0.5)  # ss=8, df=4+1

    # NEVER guessed. One reading of one cell has no spread; one state has no contrast. A 0.0 in
    # either slot reads as a perfectly sharp instrument and would argue for crowning a winner.
    lone = _outer_snr({"a": state({"c1": [0.5]})}, [ranked(0.0)])
    assert lone.within_sd is None and lone.between_sd is None
    assert lone.n_cells_to_verdict is None and lone.within_n_groups == 0
