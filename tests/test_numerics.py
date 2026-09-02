"""C2 — Statistical / numerical correctness.

The math the optimizer trusts: per-dataset scorer formulas, the evaluator
registry + composite fitness, the Rasch (IRT) fit + decision-information sample
picker, the Bayesian Posterior-of-Being-Best gate, and the L1/L2/L3 output
validators that score a proposal's conformance. Every assertion is wrong-reveal
— it fails when the formula is wrong, not when a wrapper is renamed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest
import yaml

from promptpotter.application.evidence import SubjectReading, _comparability, _stamp_comparable
from promptpotter.application.intelligence.exploration import (
    Observation,
    extend_ruler,
    fit_rasch,
    fit_rasch_2pl,
    fit_theta_given_delta,
    graduate_ruler_model,
    parent_level_trajectory,
    ruler_expected_accuracy,
    select_round_subset,
)
from promptpotter.application.mask.backprop import accumulate_node_stats, select_rewind_round
from promptpotter.application.mask.divergence import find_divergences
from promptpotter.application.mask.record import (
    MaskCandidate,
    MaskCycle,
    MaskRecord,
    MaskRound,
    SpineCycle,
)
from promptpotter.application.mask.verdicts import make_abort_verdict, make_scoring_verdict
from promptpotter.application.optimization.pobb.classification import terminal_ranking
from promptpotter.application.scoring.evaluators import materialize_round_values
from promptpotter.application.scoring.formula import (
    ScoringFormulaError,
    compile_scorer,
)
from promptpotter.application.scoring.formula.matchers import (
    _aime_match,
    _exact_match,
    _gsm8k_match,
)
from promptpotter.application.scoring.metrics import (
    compute_composite_fitness,
    matched_parent_stats,
    value_with_mask_applied,
)
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.phases import StopReason
from promptpotter.domain.pipeline_schema import (
    NodePromptInfo,
    NodeType,
    ObservationMapping,
    PipelineNode,
    PipelineSchema,
)
from promptpotter.domain.rendering import display_fitness, extract_display_answer
from promptpotter.domain.results import RoundResult, is_floor_pinned
from promptpotter.domain.ruler import (
    AbilityReading,
    DeltaRuler,
    ThetaCaveat,
    anchor_id_of,
    flat_ruler_id,
    is_flat_ruler_id,
    theta_caveat,
)
from promptpotter.domain.sample import Sample
from promptpotter.domain.scoring import extract_item_label, is_answer_collapsed
from promptpotter.domain.search_point import JobSearchPoint
from promptpotter.shared import extract_gsm8k_number
from promptpotter.shared.errors import RulerCoverageError
from promptpotter.shared.statistics import (
    holm_adjusted,
    mean_ci,
    mean_ci_t,
    paired_reading,
    two_way_effect_sds,
)
from tests.factories import (
    cycle_result,
    lost_round,
    measurement,
    measurements,
    round_result,
    scored_candidate,
)

# 1. Scorer matcher formulas — one parametrized family
# The cases that matter are the format edges: last-marker-wins, bad-marker fallback,
# cross-format numeric equivalence, case-insensitivity, no-signal → 0/None.


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
        # A list matcher's answer is the whole slate, parsed the way `_list_rr` parses it.
        (
            extract_display_answer,
            ("1. Alien\n2. **Blade Runner**\n- Solaris\n", "list_rr(predicted, ground_truth)"),
            "alien | blade runner | solaris",
        ),
        # One line is the contract, so it holds on the no-extractor fallback too — these render
        # into a one-line-per-sample readout and a newline would split the row.
        (extract_display_answer, ("first\nsecond", None), "first second"),
        (
            extract_display_answer,
            ("reasoning\nspills", "rr(predicted, ground_truth)"),
            "reasoning spills",
        ),
    ],
)
def test_matcher_formula(fn, args, expected):
    assert fn(*args) == expected


# 2. ``compile_scorer`` — AST allowlist + known-formula compile


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
    # ``compile_scorer`` mints the PAIR now; the per-sample verdict is the ``fitness`` half.
    aime = compile_scorer("aime_match(predicted, ground_truth)")
    assert aime.fitness(_result_min(r"\boxed{42}", "42")) == 1.0
    gsm = compile_scorer("gsm8k_match(predicted, ground_truth)")
    assert gsm.fitness(_result_min("6 * 7 = 42. The answer is 42.", "#### 42")) == 1.0
    # The other production shapes still clear the AST allowlist the tests above probe.
    for f in (
        "exact_match(predicted, ground_truth)",
        "hockeystick(rr(ground_truth_rank), 0.2)",
        "0.7 * hit + 0.3 * (1.0 - input_tokens / 1000)",
    ):
        assert compile_scorer(f) is not None


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
        missing.fitness(_result_min("a", "a"))
    # A formula that evaluates to a non-numeric must also fail loud, not score 0.
    non_numeric = compile_scorer("ground_truth")
    with pytest.raises(ScoringFormulaError):
        non_numeric.fitness(_result_min("a", "not-a-number"))


def test_scorer_rejects_non_finite_instead_of_scoring_it_perfect() -> None:
    # SILENT wrong-score, and it INVERTS. Python's `min` short-circuits on its first argument,
    # so `min(1.0, nan)` is `1.0` and the clamp `max(0.0, min(1.0, nan))` returns 1.0 — a NaN
    # scored a PERFECT sample. An unmeasured proxy or an empty denominator anywhere in a
    # composite formula therefore read as a flawless answer. `inf` clamps to 1.0 the same way.
    assert max(0.0, min(1.0, float("nan"))) == 1.0  # the trap, pinned
    for expr in ("1e400", "-1e400", "1e400 - 1e400"):
        with pytest.raises(ScoringFormulaError, match="non-finite"):
            compile_scorer(expr).fitness(_result_min("a", "a"))

    # The reachable shape: a non-finite value arriving through `pipeline_data` — how an L4
    # proxy would deliver one — not a literal a human would notice in the formula string.
    result = _result_min("a", "a") | {"pipeline_data": {"after_N_rounds_delta": float("nan")}}
    with pytest.raises(ScoringFormulaError, match="non-finite"):
        compile_scorer("after_N_rounds_delta").fitness(result)

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
        compute_degraded_rate,
        compute_error_rate,
    )
    from promptpotter.application.scoring.formula import (
        ScoringTermMissingError,
        compile_round_scorer,
    )
    from promptpotter.domain.scoring import recorded_cost_s

    assert compute_accuracy(results=[]) is None
    assert compute_error_rate(results=[]) is None
    assert compute_degraded_rate(results=[]) is None

    # Latency stopped being an evaluator and became the `latency` CHANNEL
    # (`domain/scoring.py::recorded_cost_s`) — one reading, per row, so a mask and a `per_cell`
    # formula name the same number. It kept both properties, which is why they are pinned here
    # and not left to the deleted evaluator's grave: a CACHED replay stamps `total_time` 0.0 while
    # the work it replays took minutes, so reading that field priced a whole round at "instant"
    # and elected the arm that had doubled the clock. It reads `step_timings`, which survives the
    # stamp. An EMPTY timing map is a row that recorded no time at all — absent, never a 0.0
    # that would read as a free cell and divide into any budget the formula sets.
    def _timed(total: float, steps: dict[str, float]) -> dict[str, object]:
        pipeline_data = {"total_time": total, "step_timings": steps}
        return _result_min("q", "a") | {"pipeline_data": pipeline_data}

    assert recorded_cost_s(_timed(0.0, {"inner": 600.0})) == 600.0
    assert recorded_cost_s(_timed(0.0, {})) is None

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
        compile_round_scorer(None)({"latency": 600.0})
    with pytest.raises(ScoringTermMissingError, match="latency"):
        compile_round_scorer("accuracy * 500.0 / latency")({"accuracy": 0.9})

    # But the GATEWAY over an EMPTY round is defined, not a crash: an operator skip at query
    # 0/N (or an all-excluded round) hands ``compute_composite_fitness`` no rows. It records the
    # 0.0 floor with ``total`` 0 — the no-evidence marker that keeps the candidate out of winner
    # election — rather than run the fail-loud scorer and halt the whole cycle.
    empty = compute_composite_fitness([], _single_node_schema(), opt_sp=None)
    assert empty["composite_fitness"] == 0.0
    assert empty["accuracy"] == 0.0
    assert empty["total"] == 0


# 3. Evaluator registry + composite fitness + matched-parent subset


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
        # Both halves, as ``rescore_results`` stamps them: the composite means ``objective``, and
        # these fixtures declare no ``per_cell``, where it is the same float as ``fitness``.
        "objective": score,
        "error": error,
        "pipeline_data": pd,
    }


def _single_node_schema() -> PipelineSchema:
    """Minimal schema with one generic node and no node_type assignments."""
    return PipelineSchema(
        name="test", nodes=[PipelineNode(name="llm_only", node_type=NodeType.NONE)]
    )


def _prompt_schema(
    node: str = "llm_only", *, template_variables: list[str] | None = None
) -> PipelineSchema:
    """One node carrying a PROMPT surface — what ``parse_population`` validates a candidate's
    overrides against, and what the render strip rebuilds a searchpoint from."""
    return PipelineSchema(
        name="test",
        available_models=["openai/gpt-oss-120b"],
        nodes=[
            PipelineNode(
                name=node,
                prompt_info=NodePromptInfo(template_variables=template_variables or []),
            )
        ],
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


def test_matched_parent_stats_refuses_a_prefix_it_cannot_measure():
    """A wrong number carried forward with no error — this file's own bar.

    ``build_round_order`` stratifies the round on the PARENT's grades: every 4th slot is
    a cell it passed, the rest are cells it missed. So origin's rate on a truncated
    candidate's prefix is ``⌊n/4⌋/n`` — set by where PoBB stopped, not by the data — and
    both halves of the comparison are conditioned on the outcome that chose the subset, so
    a candidate of identical ability outscores origin there by regression to the mean.
    Measured over the 32 truncated rows banked on disk, that prediction held exactly 28
    times; the 19 candidates cut at six samples every one reported 0.1667.

    Nothing raises when it is wrong: the value renders into the scoreboard, the
    ``mutation_memory`` panel L1 reasons from, and the L4 narrative's top-arm pick.
    """
    schema = _single_node_schema()
    # Origin scored 20 samples: 10 hits (samples 0-9 hit, 10-19 miss).
    origin_results = [
        {**_eval_result(hit=i < 10, score=1.0 if i < 10 else 0.0), "sample_id": i}
        for i in range(20)
    ]
    # Candidate stopped after 8 of the *hardest* samples (origin's misses, ids 10..17).
    # Origin reads 0/8 there — but it reads 0/8 for ANY candidate cut at that depth, which
    # is what makes it unusable rather than merely harsh.
    truncated = [
        {**_eval_result(hit=i < 13, score=1.0 if i < 13 else 0.0), "sample_id": i}
        for i in range(10, 18)
    ]
    assert matched_parent_stats(origin_results, truncated, schema) is None
    # Covered the whole panel → a real comparison, on the origin's own measured set.
    full = matched_parent_stats(origin_results, origin_results, schema)
    assert full is not None
    assert full["total"] == 20
    assert full["accuracy"] == pytest.approx(0.5)
    # DISJOINT (per_round_resubset can hand a candidate samples the origin never measured):
    # no shared basis at all, so no comparison. This previously fell back to origin's full
    # rate, publishing a floor measured on cells the candidate never ran.
    disjoint_candidate = [
        {**_eval_result(hit=True, score=1.0), "sample_id": i} for i in range(100, 108)
    ]
    assert matched_parent_stats(origin_results, disjoint_candidate, schema) is None


def test_value_with_mask_applied_reproduces_the_retired_client_whatif_math():
    """The served What-If bar value must equal what the deleted client weighted-sum
    (``correctedFromEvaluators``) produced for the same formula — else retiring the
    TS recompute silently shifts every What-If bar. The webapp's ``formulaFromWeights``
    emits ``w * t`` terms with each "low"-direction evaluator flipped to ``(1 - t)``;
    feeding that exact criterion through the one scoring operation must hand-check.

    Also the self-consistency leg: the realizing criterion reproduces the stored value,
    and a criterion naming an absent evaluator is unscorable (None, not fabricated)."""
    evaluators = {"accuracy": 0.8, "error_rate": 0.2}
    # `formulaFromWeights({accuracy: high, error_rate: low}, {0.7, 0.3})`:
    #   0.7 * accuracy + 0.3 * (1 - error_rate) = 0.56 + 0.24 = 0.80
    assert value_with_mask_applied(
        evaluators, "0.7 * accuracy + 0.3 * (1 - error_rate)"
    ) == pytest.approx(0.80)
    # Realizing criterion (plain accuracy) reproduces the stored accuracy exactly.
    assert value_with_mask_applied(evaluators, "accuracy") == pytest.approx(0.8)
    # A formula naming an evaluator this candidate lacks is unscorable, not faked.
    assert value_with_mask_applied(evaluators, "source_recall") is None


def _mask_cand(cid: str, acc: float, **kw: object) -> MaskCandidate:
    return MaskCandidate(candidate_id=cid, evaluators={"accuracy": acc}, accuracy=acc, **kw)  # type: ignore[arg-type]


def test_mask_scoring_divergence_self_consistency_and_eligibility():
    """The scoring mask's correctness backbone, in one record.

    Realized election (``winner.py``): argmax display_rank_key over {parent}
    ∪ *eligible* candidates. Round 1: the origin C0=0.5 is the parent, A=0.75 (winner),
    B=0.25, and an INELIGIBLE C=1.0 (escalation-aborted-style). Round 2: A carries
    forward as the parent, D=1.0 wins.

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
        parent_evaluators={"accuracy": 0.5},
        parent_accuracy=0.5,
        candidates=[
            _mask_cand("A", 0.75, is_winner=True),
            _mask_cand("B", 0.25),
            _mask_cand("C", 1.0, is_eligible=False),  # higher score, but excluded
        ],
    )
    r2 = MaskRound(
        cycle_id="cyc",
        round=2,
        parent_evaluators={"accuracy": 0.75},  # A carried forward
        parent_accuracy=0.75,
        candidates=[_mask_cand("D", 1.0, is_winner=True)],
    )
    record = MaskRecord(cycles=[MaskCycle(cycle_id="cyc", rounds=[r0, r1, r2])])

    realized = find_divergences(record, make_scoring_verdict("accuracy"))
    assert realized.divergences == []
    assert realized.divergent == []

    swapped = find_divergences(record, make_scoring_verdict("1 - accuracy"))
    assert [(d.cycle_id, d.round, d.alternative_candidate_id) for d in swapped.divergences] == [
        ("cyc", 1, "B")
    ]
    assert swapped.divergent == [("cyc", 2)]


def _spine_cycle(cycle_id: str, rounds: list[tuple[int, float]], **kw: object) -> SpineCycle:
    return SpineCycle(cycle_id=cycle_id, theta_by_round=dict(rounds), **kw)  # type: ignore[arg-type]


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
    root = _spine_cycle("root", [(0, 0.0), (1, 0.5), (2, 0.4), (3, 0.3)])
    # r0/r1 are byte-copies of root's, carried forward by the mint.
    fork = _spine_cycle(
        "fork", [(0, 0.0), (1, 0.5), (2, 2.0), (3, 2.4)], parent_cycle_id="root", fork_from_round=2
    )
    stats = accumulate_node_stats([root, fork])

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

    # ---- and the PICK the fold feeds, on the same tree ---------------------------------
    # UCB re-expands the ancestor whose subtree carries the ability, not the adjacent round
    # that merely happens to be nearest. Read off root's own spine, where θ collapses after
    # r1: stalled at r3, the target is r1.
    collapsed = [_spine_cycle("root", [(0, 0.0), (1, 2.0), (2, 0.1), (3, 0.0)])]
    assert select_rewind_round(collapsed, cycle_id="root", current_round=3) == 1
    # Nothing above round 0 — the caller must NOT fork, or a rewind to nowhere mints a
    # duplicate cycle and burns a whole run.
    assert select_rewind_round(collapsed, cycle_id="root", current_round=0) is None


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
    assert [(d.cycle_id, d.round) for d in no_lockin.divergences] == [("cyc", 1)]
    assert no_lockin.divergent == [("cyc", 2)]

    no_eps = find_divergences(record, make_abort_verdict(frozenset({"epsilon"})))
    assert [(d.cycle_id, d.round) for d in no_eps.divergences] == [("cyc", 2)]

    no_abort = find_divergences(record, make_abort_verdict(frozenset({"epsilon", "lock_in"})))
    assert [(d.cycle_id, d.round) for d in no_abort.divergences] == [("cyc", 1)]
    assert no_abort.divergent == [("cyc", 2)]


def _flip_spine(cycle_id: str, *, n_rounds: int = 3) -> list[MaskRound]:
    """A spine whose round 1 flips leader under ``1 - accuracy``: parent 0.5, A=0.75 wins,
    B=0.25 loses. Round 2 exists only to be claimed as the divergent tail."""
    rounds = [
        MaskRound(cycle_id=cycle_id, round=0, candidates=[_mask_cand("C0", 0.5, is_winner=True)]),
        MaskRound(
            cycle_id=cycle_id,
            round=1,
            parent_evaluators={"accuracy": 0.5},
            parent_accuracy=0.5,
            candidates=[_mask_cand("A", 0.75, is_winner=True), _mask_cand("B", 0.25)],
        ),
        MaskRound(
            cycle_id=cycle_id,
            round=2,
            parent_evaluators={"accuracy": 0.75},
            parent_accuracy=0.75,
            candidates=[_mask_cand("D", 1.0, is_winner=True)],
        ),
    ]
    return rounds[:n_rounds]


def test_mask_fold_claims_a_forks_subtree_only_when_it_is_rooted_at_or_after():
    """Where a fork sits relative to the divergence decides whether it is real or fiction.

    A wrong answer here is the fold's silent class: the tree still renders, every node
    still carries a number, and the operator reads "this change reaches here" off a
    partition nothing contradicts. Rooted BEFORE the divergence, a fork branched off data
    the change never touched — it stays invariant and is analysed for its own divergence.
    Rooted AT or AFTER, its every round descends from a choice the change would have made
    differently, so the whole subtree is counterfactual and is claimed WITHOUT being asked
    — including a grandchild, which is what makes the claim recursive rather than one-deep.

        root  r0 → r1 ✗ → r2                (✗ = where `1 - accuracy` flips the leader)
          ├─ early (cut at 0)  r0 → r1 ✗ → r2     rooted before → analysed, diverges itself
          └─ late  (cut at 2)  r0 → r1            rooted after  → claimed whole, never asked
               └─ grand        r0 → r1            claimed with it

    ``late``'s r1 carries the same flipping shape as the others, so it WOULD report a
    divergence if the fold walked it. That it does not is the assertion.
    """
    root = MaskCycle(cycle_id="root", rounds=_flip_spine("root"))
    early = MaskCycle(
        cycle_id="early", parent_cycle_id="root", fork_from_round=0, rounds=_flip_spine("early")
    )
    late = MaskCycle(
        cycle_id="late",
        parent_cycle_id="root",
        fork_from_round=2,
        rounds=_flip_spine("late", n_rounds=2),
    )
    grand = MaskCycle(
        cycle_id="grand",
        parent_cycle_id="late",
        fork_from_round=1,
        rounds=_flip_spine("grand", n_rounds=2),
    )
    record = MaskRecord(cycles=[root, early, late, grand])

    # Self-consistency holds across the forest, not just one spine: fed the realizing
    # criterion every cycle IS walked — `late` included — and none of them departs.
    realized = find_divergences(record, make_scoring_verdict("accuracy"))
    assert realized.divergences == [] and realized.divergent == []

    swapped = find_divergences(record, make_scoring_verdict("1 - accuracy"))
    assert [(d.cycle_id, d.round, d.alternative_candidate_id) for d in swapped.divergences] == [
        ("root", 1, "B"),
        ("early", 1, "B"),  # rooted before ⇒ still analysed, and departs on its own
    ]
    assert swapped.divergent == [
        ("root", 2),
        ("early", 2),
        ("late", 0),  # claimed wholesale — note r0, which no verdict ever rules on
        ("late", 1),
        ("grand", 0),
        ("grand", 1),
    ]


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


# 4. Rasch fit + decision-information round subset


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


def _ruler(
    delta: dict[int, float],
    *,
    mu: float = 0.0,
    sigma: float = 2.0,
    se: float | dict[int, float] = 0.5,
) -> DeltaRuler:
    """A locked ruler over a bare δ map — the shape most numeric tests care about.

    ``se`` per cell rather than flat is what the acquisition tests bend: the whole question
    there is which of two cells at the SAME difficulty a round buys, and a flat map cannot ask
    it."""
    return DeltaRuler(
        delta=dict(delta),
        delta_se=dict(se) if isinstance(se, dict) else dict.fromkeys(delta, se),
        mu_delta=mu,
        sigma_delta=sigma,
        sigma_theta=1.5,
        calibration_model="1PL",
        anchor_id=anchor_id_of(delta, mu, sigma, "1PL"),
    )


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

    # A sample absent from a WARM ruler RAISES. It used to be graded at δ=0, which is not a
    # neutral value but a position: on a ruler centred near +2.8 it scored an unmeasured cell as
    # easier than anything ever measured, and silently pulled θ down ~2 logits for every round
    # whose subset had walked off the scale. Loud beats plausible.
    with pytest.raises(RulerCoverageError) as caught:
        fit_theta_given_delta([Observation("ghost", 99, True)], ruler)
    assert "99" in str(caught.value)
    # The COLD ruler is the one legitimate flat read: θ is plain logit-accuracy, which depends on
    # no fit and so stays comparable across cycles. A single hit ⇒ θ > 0 under the N(0,σ²) prior.
    cold = fit_theta_given_delta([Observation("ghost", 99, True)], None)
    assert cold["ghost"][0] > 0.0

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
    ruler = _ruler({1: -2.0, 2: -2.0, 3: -2.0, 4: 2.0, 5: 2.0, 6: 2.0})  # easy 1-3, hard 4-6
    full = ruler_expected_accuracy(0.0, ruler)
    assert full is not None
    # Symmetric ruler about δ=0 ⇒ ability 0 projects to EXACTLY 0.5 — regardless of which
    # subset a round scored — whereas raw accuracy on the easy 1-3 subset reads ~0.88.
    assert abs(full - 0.5) < 1e-9
    # Monotone in ability: a genuinely stronger candidate always projects higher.
    lo, hi = ruler_expected_accuracy(-1.5, ruler), ruler_expected_accuracy(1.5, ruler)
    assert lo is not None and hi is not None and hi > full > lo
    # Cold ruler / absent ability → None so the proxy falls back to raw accuracy.
    assert ruler_expected_accuracy(0.0, None) is None
    assert ruler_expected_accuracy(None, ruler) is None


def test_parent_level_trajectory_is_honest_single_scale() -> None:
    # The L4 outer proxy's inner-search signal. Every branch here is a SILENT wrong-number
    # class: a completed inner run reports a plausible number and the outer optimizes on it,
    # so a mis-built level is invisible — the run looks fine and the outer fitness is wrong.
    ruler = _ruler({1: -1.0, 2: 0.0, 3: 1.0})

    def on(theta: float, se: float, *, scale: DeltaRuler | None = ruler) -> AbilityReading:
        """A reading, not a bare pair — a level carries the SCALE it was read on, because that is
        what says whether it may be differenced against the next one."""
        cal = scale.calibration_model if scale is not None else None
        # Read on the whole ruler, so the round's own band IS the ruler's.
        span = scale.delta_span if scale is not None else None
        return AbilityReading(
            # ``scale=None`` is a COLD reading, named for the OBJECTIVE θ was logit-accuracy on
            # rather than sharing one id with every other cold one. Either way it is incomparable
            # with a fitted anchor, which is the whole point of the branches below.
            theta=theta,
            se=se,
            ruler_id=scale.anchor_id if scale is not None else flat_ruler_id("acc"),
            ruler_n=len(scale.delta) if scale is not None else 0,
            ruler_span=span,
            round_span=span,
            calibration_model=cal,
            caveat=theta_caveat(calibration_model=cal, round_span=span, ruler_span=span),
        )

    origin_theta = on(0.0, 0.30)

    def thetas(levels: list[tuple[float, float]]) -> list[float]:
        return [t for t, _ in levels]

    # LOGITS, not expected accuracy. θ and the ruler's δ share one INTERVAL scale — the point of
    # fitting Rasch at all — so an identical Δθ must read as an identical gain wherever the origin
    # sits. Projecting each θ back through the ruler's sigmoid before differencing compressed the
    # gain near the ceiling, so the strong-origin arm scored less for the same ability climb.
    # SILENT: the outer ranks optimizer prompts partly by which seed happened to draw an easy origin.
    low_o, low = parent_level_trajectory(on(-1.0, 0.2), [on(-0.5, 0.2)], ruler)
    high_o, high = parent_level_trajectory(on(1.5, 0.2), [on(2.0, 0.2)], ruler)
    assert low_o is not None and high_o is not None
    assert (
        thetas(low)[0] - low_o[0]
        == pytest.approx(thetas(high)[0] - high_o[0])
        == pytest.approx(0.5)
    )

    # THE PARENT THE ROUND ADOPTED — never a statistic over the round's proposals. A round's
    # value to the search is what it CROWNS; the arms it discards are the price of finding that,
    # and averaging them prices exploration as damage. For any mutation operator with mass below
    # the parent (all of them — that is why selection exists) E[mean θ] < θ_parent, so the mean
    # is negative for an exploring generator and ~0 for an inert one: the gradient inverted.
    # Measured on promptpotter-self__d8b5be before the fix: an inner round that adopted a
    # 28-sample winner at θ +0.27 while PoBB killed a dud at 6 samples (θ -1.84) recorded a level
    # of -0.79 — a 0.88-logit REGRESSION stamped on a round the loop marked improved=True. Over
    # the campaign the seed that gained 30 accuracy points scored 2.9x WORSE than one that gained
    # 4.6. SILENT throughout: every run completed and every number looked plausible.
    o_lvl, levels = parent_level_trajectory(origin_theta, [on(0.2702, 0.21)], ruler)
    assert o_lvl == (origin_theta.theta, origin_theta.se)
    assert levels == [(pytest.approx(0.2702), pytest.approx(0.21))]

    # A peak followed by a collapse must read LOWER than a sustained peak. Under a running max
    # the two are byte-identical, so an optimizer prompt that destroys the inner loop after one good
    # round scored as its best round forever.
    _, spike = parent_level_trajectory(origin_theta, [on(1.2, 0.2), on(-2.0, 0.2)], ruler)
    _, held = parent_level_trajectory(origin_theta, [on(1.2, 0.2), on(1.2, 0.2)], ruler)
    assert thetas(spike)[1] < thetas(spike)[0] and sum(thetas(spike)) < sum(thetas(held))

    # A round whose frontier could not be fit (every row errored) carries the PRIOR level: the
    # parent persists, and nothing says it moved. SILENT otherwise: a phantom negative, or a
    # dropped round that shortens the series the mean divides by.
    #
    # It carries BOTH HALVES of that level, and the SE half is the one that can go wrong quietly:
    # a carried θ paired with a fresh round's SE would report the panel a precision no measurement
    # bought, and an inverse-variance pool then weights the cell that measured nothing the highest.
    o2, lv2 = parent_level_trajectory(origin_theta, [None], ruler)
    assert o2 is not None and lv2 == [o2]
    _, carried = parent_level_trajectory(origin_theta, [on(1.0, 0.11), None], ruler)
    assert carried == [(1.0, 0.11), (1.0, 0.11)]

    # Regression preserved: an parent below origin yields a level BELOW origin (the negative
    # delta the outer steers away from), NOT floored at origin.
    o3, lv3 = parent_level_trajectory(origin_theta, [on(-2.0, 0.2)], ruler)
    assert o3 is not None and thetas(lv3)[0] < o3[0]

    # An origin that was never fit, or a COLD ruler, yields `(None, [])` so the caller EXCLUDES
    # the cycle (`no_evidence_reason`). Cold matters on its own: `fit_theta_given_delta` puts
    # every sample at δ=0 there, so θ collapses to that round's logit-accuracy and stops being
    # subset-invariant — differencing it across rounds compares two different scales.
    # SILENT: a dead inner campaign differenced against an invented floor reads as a huge lift.
    assert parent_level_trajectory(None, [on(1.0, 0.2)], ruler) == (None, [])
    assert parent_level_trajectory(origin_theta, [on(1.0, 0.2)], None) == (None, [])

    # A level read on ANOTHER SCALE is not a level on this one: differencing a flat reading
    # against a warm origin subtracts two estimators, not two abilities. It carries the prior
    # level forward, like a round that crowned nobody. SILENT — both numbers are plausible.
    _, mixed = parent_level_trajectory(origin_theta, [on(9.0, 0.2, scale=None)], ruler)
    assert mixed == [(origin_theta.theta, origin_theta.se)]
    # …and an origin off the cycle's own scale leaves nothing to difference against at all.
    assert parent_level_trajectory(on(0.0, 0.3, scale=None), [on(1.0, 0.2)], ruler) == (None, [])
    # A DIFFERENT warm ruler is just as incomparable as a flat one — the id is the whole test.
    other = _ruler({1: -0.5, 2: 0.25, 3: 2.0})
    _, foreign = parent_level_trajectory(origin_theta, [on(9.0, 0.2, scale=other)], ruler)
    assert foreign == [(origin_theta.theta, origin_theta.se)]


def test_compute_proxies_is_one_exact_mean_over_the_parent_levels() -> None:
    # SILENT wrong-score: the outer signal is ONE number, so any error in it is the whole
    # ranking. It must be the mean of the adopted levels minus the origin, over the cycle's
    # ROUND BUDGET. A divisor of any OTHER shape reappearing here (a declared target, or the
    # room `max(origin, 1-origin)`) fails loudly on the pins below, and so does a regression to
    # reading the series' last element: the two differ on every trajectory that is not flat.
    from promptpotter.domain.l4.proxies import (
        OUTER_PROXY_KEYS,
        compute_outer_proxies,
        mean_parent_level_se,
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
    # THE PADDING MUST NOT REACH THE PRECISION. `parent_level_series` stretches a short series by
    # repeating its last value; those slots carry no measurement. If they entered the cell's SE
    # as if they did, a cell that quit after 2 of 4 rounds would report itself SHARPER than one
    # that ran the budget out, and an inverse-variance pool would weight the cell that measured
    # LEAST the most. SILENT: the panel would report a tighter CI it never earned, and buy its
    # confidence from the arms that did the least work.
    se_kw = {"origin_level_se": 0.20, "round_parent_level_ses": [0.30, 0.40]}
    padded = cycle_result(
        [0.30, 0.60], 0.30, [round_result(i) for i in (1, 2)], round_budget=4, **se_kw
    )
    unpadded = cycle_result(
        [0.30, 0.60], 0.30, [round_result(i) for i in (1, 2)], round_budget=2, **se_kw
    )
    assert mean_parent_level_se(padded) == pytest.approx(mean_parent_level_se(unpadded))
    # mean(0.30, 0.40) and NOTHING ELSE — never sigma/sqrt(n), which would divide correlated,
    # NESTED frontier fits as if they were independent draws.
    assert mean_parent_level_se(padded) == pytest.approx(0.35)
    assert mean_parent_level_se(padded) > 0.35 / 2

    # SILENT wrong-number: the origin's own SE must NOT be in here. Every arm on a cell replays
    # the same round-0 rows, so `origin_level` is ONE measurement shared by both sides of
    # `variant - origin` and cancels exactly. Folding it in counted it twice, and on the banked
    # corpus that made the claimed noise 2.4x the total spread it is a component of — a ratio
    # that is impossible rather than merely large, and it read out as "100% noise" after clamping.
    assert mean_parent_level_se(padded) != pytest.approx((0.35**2 + 0.20**2) ** 0.5)
    # ...so the cell's own SE cannot depend on whether the origin was ever fit.
    no_origin_se = cycle_result(
        [0.30, 0.60],
        0.30,
        [round_result(i) for i in (1, 2)],
        round_budget=4,
        round_parent_level_ses=[0.30, 0.40],
    )
    assert mean_parent_level_se(no_origin_se) == pytest.approx(0.35)

    # No SE at all yields None — the same answer as "this cell was never fit". A fabricated 0.0
    # reads as an infinitely sharp cell and would dominate every weighting it entered.
    assert mean_parent_level_se(cycle_result([0.30], 0.30, [round_result(1)])) is None

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
    with pytest.raises(InnerCycleUnscoreableError, match="no parent levels"):
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
    from promptpotter.infrastructure.store.layout import CycleLayout, cycle_dir_for

    cycle_dir = CycleDir(cycle_dir_for(tmp_path, CycleHop(campaign_id="c1", cycle_id="cyc1")))
    view = LiveDashboardView(
        cycle_dir=cycle_dir,
        state_path=CycleLayout(Path(cycle_dir)).dashboard,
        hop=CycleHop(campaign_id="c1", cycle_id="cyc1"),
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
    score = compile_scorer(cfg["campaign_config"]["scoring"]["per_sample"])

    def s(mean_round_delta: float) -> float:
        return score.fitness({"pipeline_data": {"mean_round_delta": mean_round_delta}})

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
    warm = _ruler(dict.fromkeys(range(6), 0.0))
    picked_ids = {s.id for s in select_round_subset(bank, obs, 3, ruler=warm)}
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
    from promptpotter.application.scoring.selection import elect_round_winner

    # Fixed δ ruler: easy {0..19} low difficulty, hard {20..39} high — the bank the election reads.
    ruler = _ruler({i: (-1.5 if i < 20 else 1.5) for i in range(40)})
    # Origin spans all 40: easy {0..19} hit, hard {20..39} missed.
    origin = [measurement(i, float(i < 20)) for i in range(40)]
    # Easy candidate: 16/20 on easy samples the origin also hits → accuracy 0.80, modest lift.
    weak_on_easy = [measurement(i, float(i < 16)) for i in range(20)]
    # Able candidate: 14/20 on HARD samples the origin always misses → accuracy 0.70, bigger lift.
    able_on_hard = [measurement(i, float(i < 34)) for i in range(20, 40)]
    results_by_id = {"weak_on_easy": weak_on_easy, "able_on_hard": able_on_hard}

    # Raw subset accuracy crowns the easy candidate (0.80 > 0.70)...
    assert sum(r["hit"] for r in weak_on_easy) / 20 > sum(r["hit"] for r in able_on_hard) / 20
    # ...but the θ-gated election crowns the abler one — it cleared HARD items (high δ), stronger
    # evidence of ability than more wins on easy items (low δ) everyone already passes.
    winner_id, abilities = elect_round_winner(
        ["weak_on_easy", "able_on_hard"], results_by_id, origin, 4, ruler, parent_bias=0.0
    )
    assert winner_id == "able_on_hard"
    # The fit rides out so the caller stamps θ from the same election fit (no second fit):
    # the abler candidate's θ outranks the easy one's despite the lower raw accuracy.
    assert abilities.theta["able_on_hard"] > abilities.theta["weak_on_easy"]


def test_an_origin_that_never_scored_is_no_floor_to_beat() -> None:
    """An origin whose every sample errored must not be scored as a coin-flip.

    ``candidate_abilities`` drops errored rows, and ``fit_theta_given_delta`` omits an arm with
    no observation — so such a parent carries no ``PARENT_ABILITY_ID`` entry (at round 0 the
    parent IS the origin). Reading it as ``.get(..., 0.0)`` invented θ=0, i.e. a floor able on
    ~50% of the ruler, and both the winner election and the ``delta_ok`` promotion gate ranked
    against that phantom.

    ``paired_fitness`` does not catch it: it grades an errored row as a 0.0 cell, so the
    origin-overlap guard passes on the very rows the θ fit discarded.

    Silent harm: a backend outage during the origin's round makes every later round elect and
    promote a winner against a floor that was never measured. No error, no symptom — the lineage
    is built on a number nobody observed.
    """
    from promptpotter.application.intelligence.exploration import (
        PARENT_ABILITY_ID,
        candidate_abilities,
        theta_lift_over_parent,
    )
    from promptpotter.application.scoring.selection import elect_round_winner, paired_fitness

    origin = [measurement(i, 0.0, error_category="transport") for i in range(6)]
    candidate = measurements([0.55] * 6)
    abilities = candidate_abilities({"c1": candidate}, origin, None)

    # The origin was never fit — nothing to floor against.
    assert PARENT_ABILITY_ID not in abilities.theta
    # ...yet the overlap guard sees six shared cells, so it cannot be the thing that stops us.
    assert len(paired_fitness(candidate, origin)[0]) == 6

    assert theta_lift_over_parent(abilities, "c1") is None
    winner_id, _ = elect_round_winner(["c1"], {"c1": candidate}, origin, 1, None, parent_bias=0.0)
    assert winner_id == ""

    # A measured origin still elects normally — the guard costs nothing when there IS a floor.
    scored_origin = measurements([0.1] * 6)
    scored_abilities = candidate_abilities({"c1": candidate}, scored_origin, None)
    lift = theta_lift_over_parent(scored_abilities, "c1")
    assert lift is not None and lift > 0.0
    assert (
        elect_round_winner(["c1"], {"c1": candidate}, scored_origin, 1, None, parent_bias=0.0)[0]
        == "c1"
    )


def test_errored_cells_never_satisfy_coverage_floor() -> None:
    """``coverage_floor`` must count the same cells the θ fit consumes.

    The floor catches an arm thin for a reason OTHER than elimination — an operator skip, a
    run that errored out. (It is not an under-probing guard: it equals PoBB's own ``n_min``,
    which no cut goes below. Ranking on P is what covers that hole.) Errored rows
    (CONNECTION/UNKNOWN — e.g. an L4 inner campaign dying of a timeout) are not fatal-
    classified, so a deprecation-only coverage count includes them while the θ fit drops
    them: a candidate whose cells mostly died as tooling errors passed the floor on
    "coverage" the fit never saw and won the round on its few lucky survivors.

    Silent harm: provider noise laundered into a round winner — the thin fluke becomes
    the next generation's parent with no error and a plausible-looking dashboard.
    """
    from promptpotter.application.scoring.selection import (
        distinct_valid_cells,
        elect_round_winner,
    )

    # ``fitness=None`` IS the error shape: a real error row (`_error_result`) carries no
    # hit/fitness, only the category, and that absence is the whole subject here.
    def errored(sid: int) -> dict:
        return measurement(sid, None, error_category="unknown")

    origin = measurements([0.0] * 7)
    # 7 cells attempted, 5 died as tooling errors — only 2 carry evidence.
    thin = measurements([1.0, 1.0]) + [errored(i) for i in range(2, 7)]

    # The lift is real (floor 2 elects it) — only coverage may stop it...
    assert (
        elect_round_winner(["thin"], {"thin": thin}, origin, 2, None, parent_bias=0.0)[0] == "thin"
    )
    # ...and at floor 6 it must: 2 evidence cells, not 7 attempted ones.
    assert elect_round_winner(["thin"], {"thin": thin}, origin, 6, None, parent_bias=0.0)[0] == ""

    # Repeated rows for one cell (what `verify` leaves behind): an errored row adds no
    # coverage, a clean one carries the cell, and the pair counts once.
    assert distinct_valid_cells([*measurements([1.0, 1.0]), errored(0), errored(1)]) == 2


def test_a_thin_arm_cannot_win_on_a_margin_inside_its_own_noise() -> None:
    """`coverage_floor` IS PoBB's `n_min`, so every cut arm clears it and reaches the election.
    Ranked on a bare point estimate, a thin arm out-points a full panel on a margin smaller than
    its own SE.

    Silent harm: a winner is crowned, every number renders, and the lineage descends from a margin
    the round could not measure."""
    from promptpotter.application.intelligence.exploration import (
        PARENT_ABILITY_ID,
        candidate_abilities,
    )
    from promptpotter.application.scoring.selection import elect_round_winner
    from promptpotter.shared.statistics import p_exceeds

    ruler = _ruler(dict.fromkeys(range(28), 0.0))
    origin = measurements([1.0] * 14 + [0.0] * 14)
    deep = measurements([1.0] * 21 + [0.0] * 7)  # full panel, a well-measured gain
    shallow = measurements([1.0] * 5 + [0.0])  # cut at n_min, higher RATE on 1/6 the evidence

    ab = candidate_abilities({"deep": deep, "shallow": shallow}, origin, ruler)
    theta_p, se_p = ab.theta[PARENT_ABILITY_ID], ab.theta_se[PARENT_ABILITY_ID]
    p = {c: p_exceeds(ab.theta[c], ab.theta_se[c], theta_p, se_p) for c in ("deep", "shallow")}

    # The thin arm's POINT lift is larger, on nearly twice the SE — so it demonstrated less.
    assert ab.theta["shallow"] > ab.theta["deep"]
    assert ab.theta_se["shallow"] > 1.8 * ab.theta_se["deep"]
    assert p["deep"] > p["shallow"]
    args = (["deep", "shallow"], {"deep": deep, "shallow": shallow}, origin, 6, ruler)
    assert elect_round_winner(*args, parent_bias=0.0)[0] == "deep"

    # Ranking on P must never DISQUALIFY — that is what separates it from the `- theta_se` shrink
    # it replaced, which turned a wide-posterior gain negative and crowned nobody.
    assert (
        elect_round_winner(["shallow"], {"shallow": shallow}, origin, 6, ruler, parent_bias=0.0)[0]
        == "shallow"
    )


def test_the_bar_is_what_the_parent_can_do_not_the_draw_that_crowned_it() -> None:
    """A winner is the MAXIMUM over its round's electable arms, so its θ carries that round's
    largest noise draw and not just its ability. Nothing washes it out — ``rescore_parent``
    replays the winner's own cached rows, so the same inflated estimate is re-fit bit-for-bit
    every round after. ``parent_selection_bias`` subtracts E[max of k standard normals] × the
    winner's OWN SE so the bar stops ratcheting past what any arm can clear.

    Both ways of getting it wrong are silent, and they fail in opposite directions.
    UNDER-correct (drop the term) and a genuinely better arm is refused for the rest of the
    cycle: ``improved: false`` on every round with no reason recorded anywhere.
    OVER-correct — the paired ``√2`` SE, which charges the parent's noise to a maximum that
    never selected on it — and the bar sinks below the parent's real ability: on round 2 of
    `screen-taste-v0__0cb4d4` that turned a raw lift of −0.337 into +0.060 and crowned an arm
    below the parent on θ AND composite. A correction that over-corrects buys back exactly the
    noise-crowning it exists to stop, so the ~41% gap between the two is the whole subject.
    """
    import math

    from promptpotter.application.intelligence.exploration import (
        candidate_abilities,
        theta_lift_over_parent,
    )
    from promptpotter.application.scoring.selection import (
        elect_round_winner,
        parent_selection_bias,
    )

    def won(*, se: float | None, electable: int) -> RoundResult:
        """A round that crowned ``w`` over ``electable`` arms. ``winner_id`` is read off
        ``prompt_fields['lineage']``, which is where the live election stamps it."""
        return round_result(
            1,
            electable_count=electable,
            prompt_fields={"lineage": {"id": "w"}},
            candidate_scores=[scored_candidate("w", theta=0.5, theta_se=se)],
        )

    def held() -> RoundResult:
        return round_result(1, prompt_fields={})

    # ---- the term itself -------------------------------------------------------------
    # k=2 has a closed form — E[max of two standard normals] is 1/√π — so the table's entries
    # are checkable against arithmetic rather than against themselves.
    assert parent_selection_bias([won(se=1.0, electable=2)]) == pytest.approx(
        1 / math.sqrt(math.pi), abs=5e-5
    )
    # Linear in the winner's own SE: a sharply-measured winner carries almost no curse.
    assert parent_selection_bias([won(se=0.25, electable=2)]) == pytest.approx(
        0.25 / math.sqrt(math.pi), abs=2e-5
    )
    # A LONE electable arm was selected against nothing, so there is no maximum and no curse —
    # and the default ``electable_count`` of 0 clamps into that same safe end, never inventing
    # a correction for a round that never recorded how many arms it had.
    assert parent_selection_bias([won(se=0.9, electable=1)]) == 0.0
    assert parent_selection_bias([round_result(1, prompt_fields={"lineage": {"id": "w"}})]) == 0.0
    # Monotone in k, and CLAMPED past the table's end rather than extrapolated or IndexError-ing
    # on a round this loop does not produce.
    by_k = [parent_selection_bias([won(se=1.0, electable=k)]) for k in range(1, 7)]
    assert by_k == sorted(by_k) and by_k[0] == 0.0
    assert parent_selection_bias([won(se=1.0, electable=99)]) == pytest.approx(by_k[-1])

    # The STANDING parent is what the next round must beat, so the term is the one that crowned
    # it — the most recent round with a winner. A HELD round crowned nobody and must be walked
    # PAST, not read as "no curse": reading the newest round unconditionally zeroes the term for
    # every round after the first hold, which is most of a long cycle.
    assert parent_selection_bias(
        [won(se=0.40, electable=6), held(), won(se=0.10, electable=2), held()]
    ) == pytest.approx(0.10 / math.sqrt(math.pi), abs=2e-5)
    # No crowned round at all, and a winner with no θ fit (a cold ruler stamps none): 0.0, the
    # safe end again — an absent SE may not be defaulted into a correction nobody measured.
    assert parent_selection_bias([held(), held()]) == 0.0
    assert parent_selection_bias([]) == 0.0
    assert parent_selection_bias([won(se=None, electable=4)]) == 0.0

    # ---- and what it does to an election ---------------------------------------------
    ruler = _ruler(dict.fromkeys(range(28), 0.0))
    parent = measurements([1.0] * 15 + [0.0] * 13)
    challenger = measurements([1.0] * 13 + [0.0] * 15)  # two cells behind on the same panel
    deficit = -(theta_lift_over_parent(candidate_abilities({"c": challenger}, parent, ruler), "c"))
    assert deficit > 0.0, "the challenger must LOOK worse, or there is no bar to lower"

    def elect(bias: float) -> str:
        return elect_round_winner(["c"], {"c": challenger}, parent, 6, ruler, parent_bias=bias)[0]

    # Sized against the term itself, not a hardcoded z: a round whose winner SE puts the curse
    # just OVER the apparent deficit, and one that puts it just under.
    z = parent_selection_bias([won(se=1.0, electable=3)])
    covers = [won(se=deficit * 1.10 / z, electable=3)]
    tight = [won(se=deficit * 0.90 / z, electable=3)]

    # Uncorrected, the arm is refused — which is right only if the parent's θ were its ability.
    assert elect(0.0) == ""
    # The curse covers the deficit ⇒ the arm is crowned. This is the whole point of the term.
    assert elect(parent_selection_bias(covers)) == "c"
    # It does not ⇒ still refused. A term that admitted here would admit anything.
    assert elect(parent_selection_bias(tight)) == ""
    # …and the ~41% the paired √2 SE adds is exactly enough to crown it anyway. THE regression.
    assert elect(parent_selection_bias(tight) * math.sqrt(2.0)) == "c"


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
            {"predicted": predicted, "pipeline_data": {"terminal_node": "llm_only"}}
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
        {"predicted": real_reasoning, "pipeline_data": {"terminal_node": "llm_only"}}
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


def test_content_empty_on_a_result_that_answered_is_not_an_empty_response() -> None:
    """``content_empty`` describes ONE ATTEMPT; the backend retries it and the retry can
    answer. Reading it as a verdict on the RESULT stamps ``empty_response`` — a fatal code
    PoBB fast-cuts off a single sighting — onto a candidate that recovered and answered
    correctly. Measured on three archived ``gpt-oss-20b:nitro`` rows carrying
    ``content_empty`` + ``llm_retry`` (both stamped transient); two scored 1.0.

    The empty verdict still fires when the result really is empty, including the scorer's
    ``NO_RESULT`` sentinel — otherwise this guard would trade one silent misread for another.

    Second axis: a result that emitted NOTHING but did REASON is the route's fault, not the
    candidate's, whichever way the call ended. ``reasoning_tokens > 0`` proves the model
    worked, so ``stop`` and ``length`` are the same fault at two budgets and both route to
    infra, where they deprecate the sample without fast-eliminating the arm.
    """
    from promptpotter.config.settings import NO_RESULT
    from promptpotter.domain.rendering import classify_result

    def result(predicted: str, *, reasoning: int = 0, finish: str = "stop") -> dict[str, object]:
        return {
            "predicted": predicted,
            "pipeline_data": {
                "terminal_node": "llm_only",
                "step_tokens": {"llm_only": {"finish_reason": finish, "reasoning": reasoning}},
                "diagnostics": {
                    "warnings": [
                        {"step": "llm_only", "code": "content_empty", "kind": "transient"},
                        {"step": "llm_only", "code": "llm_retry", "kind": "transient"},
                    ]
                },
            },
        }

    recovered = classify_result(result("TRUE", reasoning=16))
    assert not recovered.fatal_codes, "a row that answered is not an empty response"
    assert "llm_only:content_empty" in recovered.advisory_codes

    for empty in ("", NO_RESULT):
        assert "llm_only:empty_response" in classify_result(result(empty)).fatal_codes

    for finish, code in (
        ("stop", "reasoning_only_response"),
        ("length", "reasoning_budget_exhausted"),
    ):
        thought = classify_result(result("", reasoning=5352, finish=finish))
        assert not thought.fatal_codes, (
            f"{finish} after reasoning is route shape, not candidate fault"
        )
        assert f"llm_only:{code}" in thought.infra_codes


# Pass B — proposal validators, PoBB posterior, round diagnostics, queue math

scipy = pytest.importorskip("scipy")  # transitively required by the PoBB math

from promptpotter.application.optimization.pobb.checks import (  # noqa: E402
    PoBBCheck,
    PoBBConfig,
)
from promptpotter.application.optimization.validators.l1_invariants import (  # noqa: E402
    detect_invariants,
)
from promptpotter.application.optimization.validators.l3_output import (  # noqa: E402
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
    OverlapMember,
    OverlapReading,
    ScoredCandidate,
    best_round_on_shared_cells,
    is_leader_eligible,
)
from promptpotter.domain.search_point import (  # noqa: E402
    FRAMING_FIELDS,
    FRAMING_VALUE_BUDGET,
    TaskDecomposition,
)

# 6. L1 invariant detectors


def _parent() -> OptSearchPoint:
    return OptSearchPoint(persona="Expert", instruction="Rank items.")


def _child(parent: OptSearchPoint, **changes) -> CandidateProposal:
    return CandidateProposal(opt_sp=parent.mutate(**changes))


def test_the_two_collapse_kinds_are_counted_apart_in_one_population():
    """A clone of the parent and a clone of a SIBLING are different failures with the same cost —
    a scoring pass on a searchpoint already measured — and the round reports them separately
    because they ask for different next moves (widen the mutation vs. de-duplicate the batch).

    One population carrying both is the case that was untested: with a test per kind, a detector
    that stamped whichever reason it checked LAST onto every collapse passed both.
    """
    parent = _parent()
    clone = _child(parent)  # echoes the parent
    first = _child(parent, persona="Specialist")
    twin = _child(parent, persona="Specialist")  # echoes its sibling
    novel = _child(parent, instruction="Rank with care.")

    stats = detect_invariants([clone, first, twin, novel], parent, {})

    def reasons(p) -> list[str]:
        return [vf.reason for vf in p.opt_sp.memory.wounds.validation_failures]

    assert reasons(clone) == ["no_op_variant"]
    assert reasons(twin) == ["duplicate_variant"]
    # The first of a duplicate PAIR is the survivor — convicting both empties the round.
    assert reasons(first) == [] and reasons(novel) == []
    assert (stats.l1_n_no_op, stats.l1_n_duplicate) == (1, 1)
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

    # THE THREE QUIET ARMS, beside the fire case because that is what stops one of them going
    # vacuous alone: each is a way the gate could do more harm than the disease, and each is
    # the SAME rewrite against a different history.
    def repeats_caught(history, *proposals) -> int:
        stats = detect_invariants(list(proposals), parent, {}, history)
        assert not any(p.opt_sp.memory.wounds.validation_failures for p in proposals)
        return stats.l1_n_repeat

    # (1) EVERY proposal repeats. Rejecting them all hands PoBB an empty population and the
    # round is forfeit — strictly worse than re-testing a dead idea, which the ALREADY TRIED
    # panel still marks. A repeat may cost a candidate, never the whole round.
    assert (
        repeats_caught(
            prior,
            _child(parent, thinking_style=_DEAD_IDEA_REPHRASED),
            _child(parent, answer_format=_DEAD_IDEA),
        )
        == 0
    )
    # (2) The same idea, never measured: a prior that scored zero samples reads
    # ``accuracy == 0.0`` only because the field is a non-optional float, and 0/0 is absence
    # of evidence rather than a measured defeat.
    assert (
        repeats_caught(
            [lost_round(1, "instruction", _DEAD_IDEA, total=0, acc=0.0)],
            _child(parent, thinking_style=_DEAD_IDEA_REPHRASED),
            _child(parent, persona="A terse logician who commits to a label."),
        )
        == 0
    )
    # (3) The same idea, but it BEAT its matched parent (0.9 against 0.5). Refining a winner is
    # the search working; only measured LOSSES close a direction off.
    assert (
        repeats_caught(
            [lost_round(1, "instruction", _DEAD_IDEA, acc=0.9)],
            _child(parent, thinking_style=_DEAD_IDEA_REPHRASED),
            _child(parent, persona="A terse logician who commits to a label."),
        )
        == 0
    )


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

    parent = _parent()
    proposal = CandidateProposal(
        opt_sp=parent.mutate(persona="Strict"),
        pipeline_params_override={"llm_only": {"model": "anything-at-all"}},
    )

    opt_sp_list, _ = parse_population([proposal], pipeline_params=None, schema=_prompt_schema())

    failures = opt_sp_list[0].memory.wounds.validation_failures
    assert len(failures) == 1
    assert failures[0].reason == "forbidden_axis"
    assert failures[0].axis == "llm_only.model"


def test_parse_population_flags_dropped_mandatory_placeholder():
    """A candidate whose evolved prompt drops {{combined_text}} is invalid (synthetic-0), never a
    real winner — the gap that let an evidence-free program out-score the evidence-fed origin.
    The intact sibling stays clean. Guards a wrong score: the drop is silent without this."""
    from promptpotter.application.optimization.l1.population import parse_population

    schema = _prompt_schema("entity_profiling", template_variables=["combined_text"])
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
    # A 21-char replacement for a 1.5k field is ALSO a gutting, so this candidate now carries two
    # reasons. Selected rather than asserted whole: the subject here is the severed port.
    (port,) = [vf for vf in dropped_failures if vf.reason == "dropped_mandatory_placeholder"]
    assert port.axis == "l1_generate.prompt"
    assert "citable_fields" in port.value
    assert opt_sp_list[1].memory.wounds.validation_failures == []
    assert [vf.reason for vf in inherited_list[0].memory.wounds.validation_failures] == [
        "dropped_mandatory_placeholder"
    ]


# 7. L2 output validators (fire + quiet)


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


def test_a_config_a_runtime_failure_convicted_is_refused_and_a_novel_one_is_not():
    """Re-proposing a (param, value) a RUNTIME failure already convicted is rejected; a novel
    value for the same param is the retune the loop is asking for and must pass.

    One fixture, both arms — as two tests the quiet one drifted onto its own narrower wound and
    stopped saying anything about the fixture the fire case used. Convicted per PARAM: the wound
    records the WHOLE observed config, so a node-level verdict would blacklist every knob that
    happened to be set when the node broke."""
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

    def axes(**params: object) -> list[str]:
        out = L1_CONFIG_NOT_IN_RUNTIME_FAILURES.run({"llm_only": params}, opt_sp=opt_sp)
        if out is None:
            return []
        failures = out.evidence["failures"]
        assert {f.reason for f in failures} == {"reproposes_known_failing_config"}
        return [f.axis for f in failures]

    assert axes(max_tokens=1800) == ["llm_only.max_tokens"]
    # Both halves of the observed config, re-proposed together → both convicted, named apart.
    assert axes(max_tokens=1800, temperature=0.7) == [
        "llm_only.max_tokens",
        "llm_only.temperature",
    ]
    # A novel value on a convicted param, and a novel pair: quiet.
    assert axes(max_tokens=2400) == []
    assert axes(max_tokens=2400, temperature=0.3) == []
    # No wound at all ⇒ nothing to convict against, whatever is proposed.
    assert (
        L1_CONFIG_NOT_IN_RUNTIME_FAILURES.run({"llm_only": {"max_tokens": 1800}}, opt_sp=_opt_sp())
        is None
    )


# 8. L3 output validators


def test_l3_output_validators_fire_on_a_stalled_plan_and_stay_quiet_on_a_real_one() -> None:
    """Both L3 soft signals, through the aggregate the loop actually calls.

    They append to ``opt_sp.l3_guard_breaches`` and come back as self-healing evidence at L3's
    next fire, so one stuck ON spends the replan on a breach that never happened, and one stuck
    OFF leaves a planner repeating itself unremarked. The QUIET arm is what was missing: with
    fire cases only, a check returning an outcome unconditionally passed every one of them.
    """
    prior = "Maintain the current strategy and explore the persona axis, one edit at a time."

    def fired(plan: object, *, standing: str = prior) -> set[str]:
        opt_sp = _opt_sp(plan=standing) if standing else _opt_sp()
        return {o.validator_id for o in run_l3_output_validators({"plan": plan}, opt_sp)}

    # Below the floor, and not a repeat of the standing plan → the length check alone.
    assert fired("do better") == {"l3_plan_length_floor"}
    # Long enough, but the planner restated its own standing plan — the stall this exists for.
    assert fired(prior) == {"l3_plan_verbatim_repeat"}
    # A substantive new plan trips neither.
    assert fired("Target answer_format: the last three rounds all hedged to Uncertain.") == set()
    # Round 1 has no standing plan to repeat, and a non-string payload is not a short plan —
    # both stay quiet rather than convicting on an absence.
    assert fired(prior, standing="") == set()
    assert fired(None) == set()


def test_a_theta_stall_verdict_must_clear_its_own_error() -> None:
    """The escalation ladder advances on "did the cycle improve", and a θ rise inside its own
    standard error is not an improvement. A bare ``>`` counts one, resets the stall counter, and
    holds the ladder at L2 forever — with no error anywhere: every round completes, L2 fires, and
    L3 simply never arrives.

    Measured on `justlogic-d234__082126`, whose numbers this replays. Round 3's θ rose +0.012 on
    se 0.198 — six hundredths of one standard error — which reset ``_l2_stall_count`` to zero and
    cost exactly one round. L3 would then have fired at round 5, but a round's escalation runs
    AFTER it closes and round 5 closed on `max_rounds`, so L3 never fired at all; rounds 3-5 spent
    49% of the run's budget re-testing candidates against a panel that had stopped moving.

    Silent harm: nothing distinguishes "L2 keeps firing because it is working" from "L2 keeps
    firing because noise keeps clearing its stall counter"."""
    from promptpotter.application.optimization.escalation.state import EscalationFSM, NextAction

    # (composite, θ, θ_se) per round, from the live run: composite frozen from round 2 on, θ
    # advancing once for real (+0.467) and then only by noise (+0.012, then flat).
    live = [
        (0.4853, -0.1296, 0.216),
        (0.6248, 0.3376, 0.202),
        (0.6248, 0.3498, 0.198),
        (0.6248, 0.3498, 0.198),
    ]

    def ladder(*, with_se: bool) -> list[str]:
        fsm, actions = EscalationFSM(), []
        for comp, theta, se in live:
            event = fsm.observe_l2_escalation(
                current_composite_fitness=comp,
                current_theta=theta,
                current_theta_se=se if with_se else None,
                l2_patience=2,
                l3_patience=1,
            )
            actions.append(str(event.next_action))
            if event.next_action != NextAction.FIRE_L2:
                break
            fsm.record_l2_fired(best_composite_fitness=comp, best_theta=theta)
        return actions

    # The real +0.467 move at round 2 still counts — the bar rejects noise, not signal.
    assert ladder(with_se=True) == ["fire_l2", "fire_l2", "fire_l2", "fire_l3"]
    # Without it the noise move buys another L2 round and L3 is pushed out of the run.
    assert NextAction.FIRE_L3 not in ladder(with_se=False)

    # The bar is the reading's own SE, applied on the θ scale only: a composite-scale verdict has
    # no error term to clear and must be untouched by it.
    assert EscalationFSM._improved(0.0, 0.0, 0.35, 0.34, 0.20)[0] is False
    assert EscalationFSM._improved(0.0, 0.0, 0.60, 0.34, 0.20)[0] is True
    assert EscalationFSM._improved(0.7, 0.6, None, None, 0.20) == (True, "composite")


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
            elim_check=PoBBCheck(PoBBConfig(), n_samples=2, ruler=None),
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


# 9. L1 layout validators — one parametrized hard-failure family


@pytest.mark.parametrize(
    "layout,expected_validator_id",
    [
        # Missing a mandatory placeholder.
        (
            L1Layout(task_intent=["task_context"], problem_description=["rendered_prompt"]),
            "l1_layout_missing_mandatory",
        ),
        # The COLLAPSE DETECTOR alone, with every other mandatory name present. Nothing else in
        # the prompt can say a pipeline has stopped reasoning and is emitting one label, so a
        # layout edit that drops it leaves the run unable to see the state it is in.
        (
            L1Layout(
                task_intent=["task_context"],
                problem_description=[
                    "rendered_prompt",
                    "pipeline_param_catalogue",
                    "plan",
                    "critique",
                ],
            ),
            "l1_layout_missing_mandatory",
        ),
        # Unknown placeholder in a slot.
        (
            L1Layout(
                task_intent=["task_context", "made_up_signal"],
                problem_description=[
                    "rendered_prompt",
                    "pipeline_param_catalogue",
                    "plan",
                    "critique",
                    "answer_distribution",
                ],
            ),
            "l1_layout_unknown_placeholder",
        ),
    ],
)
def test_layout_hard_failures(layout, expected_validator_id):
    result = validate_l1_layout(layout, spec=NODE_LAYOUTS["l1_generate"])
    assert result.is_valid is False
    assert expected_validator_id in {o.validator_id for o in result.outcomes}


def test_a_move_cannot_place_one_panel_twice():
    """The edit that used to fail: L2 names `thinking_style` and omits `problem_description`, which
    keeps what it has. Addressed as `{panel: slot}` the panel LEAVES its old slot, so the duplicate
    has no shape to arrive in and there is no validator left to reject it.
    """
    from promptpotter.domain.l1_layout import (
        L1_LAYOUT_SLOTS,
        coerce_l1_layout,
        default_l1_layout,
    )

    base = default_l1_layout()
    assert "critique" in base.problem_description, "vacuous — the panel is not where it moves from"

    moved = coerce_l1_layout({"critique": "thinking_style"}, base=base)
    assert moved is not None
    assert moved.thinking_style == ["critique"]
    assert "critique" not in moved.problem_description
    assert validate_l1_layout(moved, spec=NODE_LAYOUTS["l1_generate"]).is_valid

    # Every panel, moved to every slot, one at a time: none can reach two places.
    for panel in sorted(NODE_LAYOUTS["l1_generate"].possible):
        for slot in L1_LAYOUT_SLOTS:
            out = coerce_l1_layout({panel: slot}, base=base)
            assert out is not None
            placed = out.all_placeholders()
            assert len(placed) == len(set(placed)), f"{panel} -> {slot} placed a panel twice"

    # An unmoved panel keeps its POSITION, not merely its slot — the floor's order is authored.
    assert moved.problem_description == [n for n in base.problem_description if n != "critique"]


def test_persisted_params_drop_the_render_and_lose_nothing():
    """The round document records CONFIG; the prompt is a render of `prompt_fields` that nothing
    re-derives at the write, so persisting it is how a round comes to name a prompt its winner
    never ran. Stripping is safe only because the render rebuilds identically from the fields —
    that equality is what this asserts, not the strip itself."""
    from promptpotter.domain.search_point import strip_rendered_prompt

    schema = _prompt_schema()
    osp = _opt_sp()
    # A stale render beside a real config axis — the shape `current_sp` hands the round writer.
    base = {"llm_only": {"temperature": 0.3, "prompt": "AN OLDER ROUND'S PROMPT"}}

    stored = strip_rendered_prompt(base)
    assert "prompt" not in stored["llm_only"]
    assert stored["llm_only"]["temperature"] == 0.3
    assert base["llm_only"]["prompt"] == "AN OLDER ROUND'S PROMPT"  # never mutated in place

    # What every reader does with the stored params: the render comes back from the FIELDS, so
    # the point built off the stripped record is the point built off the full one.
    assert osp.to_job_search_point(base_pipeline_params=stored, schema=schema) == (
        osp.to_job_search_point(base_pipeline_params=base, schema=schema)
    )

    # `promptpotter-self` is not an exception: an optimizer node's evolved content rides the
    # PROMPT_STRING_FIELDS beside the render key, and the strip must not reach them.
    inner = strip_rendered_prompt({"l1_generate": {"persona": "A", "prompt": "rendered"}})
    assert inner["l1_generate"] == {"persona": "A"}


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

        # Valid edit (`diagnostics` stays placed, every name in `possible`) → merges.
        set_optimizer_prompt_overrides(
            {"l1_critique": {"layout": {"axis_memory": "thinking_style"}}}
        )
        applied = resolve_node_layout("l1_critique")
        assert applied.thinking_style == ["axis_memory"]
        assert "diagnostics" in applied.problem_description
        assert applied != floor

        # Guard rail: a name outside `possible` rolls the whole edit back to the floor.
        set_optimizer_prompt_overrides(
            {"l1_critique": {"layout": {"made_up_signal": "thinking_style"}}}
        )
        assert resolve_node_layout("l1_critique") == floor
    finally:
        set_optimizer_prompt_overrides(None)


# 10. PoBB posterior gate + leader eligibility


_DUMMY_SP = JobSearchPoint()


def test_pobb_check_gates_elimination_on_posterior():
    """PoBB.check() fires when the paired-difference posterior clears the ε gate."""
    check_sep = PoBBCheck(PoBBConfig(n_min=4, epsilon=0.05), n_samples=20, ruler=None)
    check_sep.register_completed(measurements([1.0] * 20), candidate_id="winner", sp=_DUMMY_SP)
    check_sep.set_current("loser")
    sig = check_sep.check(measurements([0.0] * 5), candidate_idx=1, n_total_candidates=2)
    assert sig is not None
    assert sig.check_name == "elimination"
    cr = sig.check_result
    assert cr["leader_id"] == "winner"
    assert cr["p_best"] < 0.05
    assert cr["epsilon"] == pytest.approx(0.05)
    # The per-prior numbers ride ``paired_breakdown``, under a name that says what they are.
    # They must NOT also appear as bare cid→float entries beside ``p_best``: that shape put
    # "this candidate's P(best)" and "P(this candidate beats prior X)" under one roof, and
    # every round-wide reader ranked the mixture as if each key were a candidate's standing.
    assert set(cr["paired_breakdown"]) == {"winner"}
    assert cr["paired_breakdown"]["winner"]["p_better"] == pytest.approx(cr["p_best"])
    assert not any(isinstance(v, float) and k.startswith("winner") for k, v in cr.items())


def test_pobb_epsilon_is_graded_by_depth_not_scalar():
    """A raised ε must not spend its aggression at ``n_min``, where ONE discordant sample already
    drives ``p_best`` to ~0.2, NOR carry it into the tail, where cutting saves almost nothing. The
    bar is ``epsilon_floor`` at both ends and the full ``epsilon`` in the middle, ramping over
    ``n_min`` cells each side. Equal floor and ε — the default — leaves the bar flat.

    The ramp-OUT is the half that was missing: the bar sat at its maximum from ``2 * n_min`` until
    the tail guard switched cutting off, so an arm deep in its budget faced the same bar as one
    with the whole panel still to save. It lands on the floor exactly where the guard begins, so
    the two meet instead of cliffing.

    The ramp itself is what this pins. The DEPTHS below are a consequence of the dispersion rule
    and moved by one sample when φ stopped being floored at a constant (`fit_theta_given_delta`):
    an honest posterior is wider, so the near-tie dies at 9 rather than 8. Re-derive them, do not
    restore them, if that rule changes again."""
    cfg = PoBBConfig(n_min=6, epsilon=0.30, epsilon_floor=0.15)
    graded = PoBBCheck(cfg, n_samples=28, ruler=None)
    assert graded.epsilon_at(6) == pytest.approx(0.15)
    assert graded.epsilon_at(9) == pytest.approx(0.225)
    assert graded.epsilon_at(12) == pytest.approx(0.30)
    # Clamped, never extrapolated: the ramp must not carry the bar ABOVE ε at any depth.
    assert graded.epsilon_at(15) == pytest.approx(0.30)
    assert max(graded.epsilon_at(n) for n in range(cfg.n_min, 29)) == pytest.approx(0.30)
    # …and back down to the floor as the remaining budget — all cutting can still save — runs out.
    assert graded.epsilon_at(20) == pytest.approx(0.20)
    # The last cuttable depth (`n_samples - n_min`) is where the guard takes over, at the floor.
    assert graded.epsilon_at(22) == pytest.approx(0.15)

    flat = PoBBCheck(
        PoBBConfig(n_min=6, epsilon=0.30, epsilon_floor=0.30), n_samples=28, ruler=None
    )
    assert flat.epsilon_at(6) == pytest.approx(0.30)
    # Shipped defaults sit floor and ε on the same constant, so an untouched config never grades.
    shipped = PoBBCheck(PoBBConfig(), n_samples=28, ruler=None)
    assert shipped.epsilon_at(shipped.n_min) == pytest.approx(shipped.epsilon)

    def arm_behind_perfect_prior(n: int, misses: int):
        check = PoBBCheck(cfg, n_samples=28, ruler=None)
        check.register_completed(measurements([1.0] * 28), candidate_id="winner", sp=_DUMMY_SP)
        check.set_current("arm")
        return check.check(
            measurements([0.0] * misses + [1.0] * (n - misses)),
            candidate_idx=1,
            n_total_candidates=2,
        )

    # One discordant loss survives the floor that used to cut it, and dies a few samples later.
    assert arm_behind_perfect_prior(6, 1) is None
    assert arm_behind_perfect_prior(8, 1) is None
    cut = arm_behind_perfect_prior(9, 1)
    assert cut is not None
    # The bar that FIRED is what the decision archives — a reader must see the ramped 0.225 at
    # n=9, never the configured 0.30, or the record cannot explain its own cut.
    assert cut.check_result["epsilon"] == pytest.approx(0.225)
    # Two behind is still cut at the floor: the reprieve is for a near-tie, not for a loser.
    assert arm_behind_perfect_prior(6, 2) is not None


def test_pobb_locks_in_dominant_leader():
    """Current candidate dominating prior past lock_in_n_min fires LEADER_LOCKED."""
    check = PoBBCheck(
        PoBBConfig(n_min=4, epsilon=0.05, lock_in=0.95, lock_in_n_min=8, leader_lock_in=True),
        n_samples=20,
        ruler=None,
    )
    check.register_completed(measurements([0.0] * 20), candidate_id="weak_prior", sp=_DUMMY_SP)
    check.set_current("strong_current")
    sig = check.check(measurements([1.0] * 8), candidate_idx=1, n_total_candidates=3)
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
    check_no_backfill = PoBBCheck(PoBBConfig(n_min=4, epsilon=0.05), n_samples=20, ruler=None)
    check_no_backfill.register_completed(
        measurements([1.0] * 8, sample_ids=leader_samples),
        candidate_id="R1_lucky_winner",
        sp=_DUMMY_SP,
    )
    check_no_backfill.set_current("R2_challenger")
    sig = check_no_backfill.check(
        measurements([0.0] * 5, sample_ids=candidate_samples),
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

    async def _stub_backfill(_sp, samples, prior_id):
        backfill_calls.append(tuple(s.id for s in samples))
        assert prior_id == "R1_lucky_winner", (
            "the backfill must be told WHOSE catch-up it is running; inheriting the "
            "foreground candidate's identity is what mis-filed C1.1's rows as C1.2's"
        )
        # Leader misses 4/5 hard samples; only #13 is hit. Mirrors origin's
        # behavior on the same samples in the real cycle.
        truth = {9: False, 12: False, 13: True, 14: False, 8: False}
        return [
            {"sample_id": s.id, "fitness": (f := 1.0 if truth[s.id] else 0.0), "objective": f}
            for s in samples
        ]

    check_paired = PoBBCheck(
        PoBBConfig(n_min=4, epsilon=0.05), n_samples=20, ruler=None, backfill_fn=_stub_backfill
    )
    check_paired.register_completed(
        measurements([1.0] * 8, sample_ids=leader_samples),
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
        measurements([0.0] * 5, sample_ids=candidate_samples),
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
        elimination_context={"p_best": 0.048, "epsilon": 0.05, "gate": "epsilon"},
    )
    leader_locked = _cs(
        candidate_id="C1.1",
        accuracy=0.55,
        elimination_stopped=True,
        elimination_context={"p_best": 0.96, "epsilon": 0.05, "gate": "lock_in"},
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


def _health_row(
    statuses: dict[str, str], *warnings: dict[str, str], hit: bool = False, **extra: object
) -> dict:
    """One sample as the round verdict reads it — per-node step statuses plus whatever the
    BACKEND stamped about them. Every case below is a shape of this one row, so building it in
    one place is what stops them disagreeing about where a warning lives."""
    return {
        "hit": hit,
        "pipeline_data": {"diagnostics": {"step_statuses": statuses, "warnings": list(warnings)}},
        **extra,
    }


_STRUCTURAL_BREAK = {
    "step": "entity_profiling",
    "code": "schema_invalid",
    "message": "max completion tokens reached",
    "kind": "structural",
}
_BRAVE_QUOTA = {
    "step": "web_search",
    "code": "no_results",
    "message": "No web evidence — Brave rate limit exceeded",
    "kind": "transient",
}


def _transient_round() -> list[dict]:
    """A round degrading on transient web_search noise — five of twenty, above the rate floor."""
    return [_health_row({"web_search": "degraded"}) for _ in range(5)] + [
        _health_row({}) for _ in range(15)
    ]


def test_compute_round_health_names_the_structural_cause_not_collateral():
    """End-to-end on the lca-bom-termnorm shape: web_search is silently ``failed``
    (collateral, no warning) while entity_profiling carries the structural warning.
    The verdict must name entity_profiling as ``dominant_node`` — the prior
    tally-by-failed-status picked web_search by 12-12 tie + insertion order."""
    from promptpotter.domain.results_health import compute_round_health

    results = [
        _health_row({"web_search": "failed", "entity_profiling": "failed"}, _STRUCTURAL_BREAK)
        for _ in range(12)
    ] + [_health_row({"entity_profiling": "success"}, hit=True) for _ in range(8)]
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
    belongs to the intelligent tiers, not this backend-coupled signal."""
    from promptpotter.application.runner.termination import backend_unreachable_tripped
    from promptpotter.domain.results_health import compute_round_health

    results = [
        _health_row({"web_search": "degraded", "entity_profiling": "degraded"}, _BRAVE_QUOTA)
        for _ in range(10)
    ] + [_health_row({"web_search": "success"}, hit=True) for _ in range(10)]
    h = compute_round_health(results=results, prior_healths=[])
    assert h is not None
    assert h.grade == "critical"
    assert h.cause == "evidence_starved"
    assert h.dominant_node == "web_search"
    assert h.node_failure_rates.get("web_search") == 0.5
    assert h.suggested_action and "web_search" in h.suggested_action
    # The deterministic tripwire must NOT consume this signal — no auto-halt.
    assert backend_unreachable_tripped(h) is None


def test_a_near_constant_answerer_is_reported_and_still_grades_healthy():
    """A model hedging almost every row to one label is the failure the loop CORRECTS, so
    the round reports the share and grades on nothing else.

    Two halves, and the second is the operator's decision made testable. (1) The share is
    visible at all: ``is_answer_collapsed`` needs a LITERAL single label, so a 19/20 hedger
    passed every check in the package and health carried no number for it — the shipped
    JustLogic worker sat at 95% against a 0.400 floor and graded ``healthy`` unremarked.
    (2) It must STILL grade healthy: a collapse is addressable in the first rounds, so a
    grade that halted on it would block the optimization that fixes it, and eliminating a
    candidate that moved its parent 1.00 → 0.95 would delete the gradient.
    """
    from promptpotter.domain.results_health import compute_round_health
    from promptpotter.domain.scoring import is_answer_collapsed, modal_answer_share

    rows = [
        {
            "predicted": "Uncertain" if i < 19 else "TRUE",
            "ground_truth": "TRUE" if i % 2 else "FALSE",
        }
        for i in range(20)
    ]
    assert modal_answer_share(rows) == 0.95
    assert not is_answer_collapsed(rows), "0.95 is not literal collapse — elimination is unchanged"

    h = compute_round_health(results=rows, prior_healths=[])
    assert h is not None
    assert h.answer_modal_share == 0.95
    assert h.grade == "healthy"
    assert h.cause is None, "the share reports; it must never become a grading arm"

    # Identity-keyed answers (every truth distinct — an L4 outer round's per-seed tokens)
    # make collapse a meaningless question, and the report must abstain rather than read 1.0.
    assert (
        modal_answer_share([{"predicted": "x", "ground_truth": f"seed{i}"} for i in range(5)])
        is None
    )


def test_a_round_that_measured_nothing_usable_names_which_way_it_broke():
    """Three ways a round produces no usable measurement. Each must NAME itself, because the
    next move differs completely between them — and every one of the three used to grade
    ``healthy`` or abstain.

    ``unscoreable`` (JustLogic): the pipeline RUNS — every ``step_status`` success, no warning —
    and emits no extractable label. The backend calls that a success, so the structural/transient
    channel is blind, and the loop spent rounds optimizing a prompt whose every output was
    unreadable. ``origin_unmeasured`` (L4): round-0 scoring produced ZERO rows, and graded
    healthy or abstained, candidates are then elected against NO baseline — the irreversible
    one. ``backend_unreachable``: every row errored, and since ``total`` counts only evidence
    rows, a round that ATTEMPTED work must not slip out as "nothing measured".

    Only the ORIGIN halts, and only on a real break: a non-origin round that measured nothing
    abstains rather than fabricating a verdict, and a wrong-but-extractable round IS a
    measurement — real labels, just wrong — which must stay gradable on accuracy."""
    from promptpotter.application.runner.termination import origin_gate_tripped
    from promptpotter.domain.phases import StopReason
    from promptpotter.domain.results_health import compute_round_health

    def answered(predicted: str) -> list[dict]:
        return [_health_row({"llm_only": "success"}, predicted=predicted) for _ in range(20)]

    unscoreable = compute_round_health(results=answered("NO_RESULT"), prior_healths=[])
    assert unscoreable is not None
    assert (unscoreable.grade, unscoreable.cause) == ("critical", "unscoreable")
    assert unscoreable.no_result_count == 20
    assert unscoreable.suggested_action and "answer_format" in unscoreable.suggested_action

    unmeasured = compute_round_health(results=[], prior_healths=[], is_origin=True)
    assert unmeasured is not None
    assert (unmeasured.grade, unmeasured.cause) == ("critical", "origin_unmeasured")

    dead = compute_round_health(
        results=[
            {"error": "connect timeout", "error_category": "CONNECTION", "pipeline_data": None}
            for _ in range(10)
        ],
        prior_healths=[],
    )
    assert dead is not None
    assert (dead.grade, dead.cause) == ("critical", "backend_unreachable")
    assert dead.samples == 10 and dead.suggested_action is not None

    # Every one halts even in the LEAST-strict armed mode — that is the guarantee that a broken
    # origin never silently enters L1.
    for broken in (unscoreable, unmeasured, dead):
        assert origin_gate_tripped(broken, "critical_only") == StopReason.ORIGIN_GATE

    # A hard task emitting REAL labels is a measurement, not a broken floor.
    wrong = compute_round_health(results=answered("FALSE"), prior_healths=[])
    assert wrong is not None
    assert wrong.no_result_count == 0 and wrong.cause != "unscoreable"
    assert origin_gate_tripped(wrong, "critical_only") is None
    # …and a NON-origin round that measured nothing abstains, never a fabricated ``healthy``.
    assert compute_round_health(results=[], prior_healths=[]) is None


@pytest.mark.parametrize(
    ("attempted", "structural", "transient", "prior_clean", "consec", "grade", "cause"),
    [
        # lca-bom-termnorm origin: 60% structural failures, untested → critical/structural.
        (20, 12, 0, 0, 1, "critical", "structural"),
        # First-sight structural at an untested config, even at low rate → critical.
        (20, 1, 0, 0, 1, "critical", "structural_untested"),
        # The SAME isolated structural failure deep in a proven campaign → NOT critical.
        (20, 1, 0, 5, 1, "healthy", None),
        # Sustained: 3 consecutive degraded rounds escalates on persistence alone.
        (20, 0, 5, 5, 3, "critical", "persistent"),
        # Transient noise on a proven pipeline above the rate floor → degraded, quiet.
        (20, 0, 5, 5, 1, "degraded", "degraded"),
        # A clean round at an UNTESTED config is healthy. It used to grade ``degraded``
        # purely because a 20-sample Wilson interval is wide — precision is not health,
        # and that verdict was what kept ``prior_clean_rounds`` pinned at 0 forever.
        (20, 0, 0, 0, 0, "healthy", None),
        # Zero samples is NO VERDICT, never a fabricated healthy one — the row that keeps
        # "nothing was measured" from reading as "nothing was wrong".
        (0, 0, 0, 0, 0, None, None),
    ],
)
def test_degradation_health_is_context_aware(
    attempted, structural, transient, prior_clean, consec, grade, cause
):
    """The verdict grades the SAME degradation differently by track record, and every GRADED
    round carries its own operator-facing sentence (never auto-stops). ``degraded`` earns one
    too: it fires on structural and transient together, so a reader left to compose the
    sentence from the fields it happens to hold states the split backwards."""
    from promptpotter.domain.results_health import compute_degradation_health

    h = compute_degradation_health(
        attempted=attempted,
        structural_count=structural,
        transient_count=transient,
        prior_clean_rounds=prior_clean,
        consecutive_degraded_rounds=consec,
        dominant_node="entity_profiling",
    )
    if grade is None:
        assert h is None
        return
    assert h is not None
    assert h.grade == grade
    assert h.cause == cause
    assert (h.suggested_action is not None) is (grade != "healthy")


def test_origin_verdict_is_first_in_the_l1_track_record():
    """The origin (round 0) is the floor every L1 round improves on, so its verdict
    must lead the track record. ``Cycle.rounds`` omits the origin (1-indexed L1
    trajectory), but on resume ``replay_priors`` leaves a round-0 entry in it — the
    assembly must take the origin from ``origin_health`` and drop that round-0 entry
    (no double-count) plus the round being closed. End-to-end: an origin that graded
    ``critical`` makes a degrading L1 round see ≥3 consecutive → ``persistent``."""
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
    h = compute_round_health(results=_transient_round(), prior_healths=fresh)
    assert h is not None and h.grade == "critical" and h.cause == "persistent"


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
    h = compute_round_health(results=_transient_round(), prior_healths=[degraded, None, degraded])
    assert h is not None and h.grade == "critical" and h.cause == "persistent"


# 11. L2 behaviour conformance


def test_l2_behavior_checks_score_conformant_vs_stub_fires():
    """Conformant L2 fire passes every behaviour check; stub fails them; no-fire ⇒ no results."""
    from promptpotter.application.optimization.validators.behavior_base import ValidatorContext
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


# 12. compute_round_diagnostics — pure analysis over scoring data

from promptpotter.application.optimization.round_analysis import (  # noqa: E402
    compute_round_diagnostics,
)


def _round_result(round_num: int, accuracy: float, results: list[dict]) -> RoundResult:
    return RoundResult(
        round=round_num,
        label=f"round_{round_num}",
        accuracy=accuracy,
        composite_fitness=accuracy,
        total=len(results),
        improved=False,
        prompt_fields={},
        results=results,
        candidates_scored=1,
    )


def test_round_diagnostics_read_rank_and_trend_off_the_rounds_it_is_given():
    """With no schema there is no ranker to read a rank OFF, so every cell must land in
    ``not_found`` — a fabricated rank-1 bucket would report perfect retrieval on a pipeline that
    has none, and top-k accuracy is drawn straight off those buckets."""

    def rows(*graded: bool) -> list[dict]:
        return [
            {
                "query": f"q{i}",
                "ground_truth": chr(ord("a") + i),
                "fitness": float(g),
                "objective": float(g),
                "predicted": chr(ord("a") + i) if g else "?",
            }
            for i, g in enumerate(graded)
        ]

    rr = _round_result(0, 0.33, rows(True, False, False))
    diag = compute_round_diagnostics(rr, [rr], pipeline_schema=None)
    assert diag.n_valid == 3
    assert diag.rank_buckets["1"] == 0
    assert diag.rank_buckets["not_found"] == 3
    assert diag.top_k_accuracy[1] == 0.0 and diag.top_k_accuracy[10] == 0.0

    def trend(*series: tuple[int, float]) -> tuple[str, str]:
        rounds = [_round_result(n, acc, rows(True)) for n, acc in series]
        diag = compute_round_diagnostics(rounds[-1], rounds, pipeline_schema=None)
        # The DESCRIPTION as well as the class: "healthy" is also the mixed-progress fallback,
        # so on the class alone a classifier that recognised nothing would pass the climbing arm.
        return diag.trend, diag.trend_description.split(" —")[0]

    # A run that moved 0.01 once and then stopped. The escalation ladder reads this class, so a
    # "healthy" verdict here spends L1's whole patience on a search that has already finished.
    assert trend((0, 0.50), (1, 0.51), (2, 0.51), (3, 0.51)) == ("plateau", "Plateau")
    # …and one still climbing must be recognised AS climbing, or the class is a constant
    # dressed as a verdict. This arm is what the plateau case alone could never say.
    assert trend((0, 0.20), (1, 0.45), (2, 0.70)) == ("healthy", "Improving")


# 13. Adaptive-queue Bayesian math (θ posterior, decision-info, pick-score)


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
    """The shared round order is the elimination gate's evidence pipeline: parent-miss
    (win-opportunity) samples front-loaded ascending-δ, a parent-hit regression probe at
    every 4th slot descending-δ, cells the parent NEVER ANSWERED in their own stratum
    ordered by discrimination, deterministic tie-breaks. Silent harm: a wrong order
    re-creates the tie-prefix blindness — the round completes, no error, and dead
    candidates ride their full budget again.

    A known miss is a measured opportunity and an unknown is a gamble, so the knowns drain
    first. Filing unknowns as misses conflated the two: `is_hit` returns False for `None`
    and for a miss alike, and under `per_round_resubset` round 1's panel shares no cell
    with the parent, so EVERY cell became a win-opportunity sorted ascending and the panel
    led with its easiest. Live on `justlogic-d234__8f6499` r1 that put three cells nothing
    has missed in 8-10 archived measurements in the first six, and the ε-gate cut an arm on
    one discordant cell. The all-unknown arm below is that case; the mixed arm above it is
    why unknowns still may not simply be dropped to the tail of a round that has real misses."""
    from promptpotter.application.intelligence.adaptive_queue_mechanism import build_round_order

    ids = list(range(24))
    # Parent hits 0-8; misses 9-22; sample 23 never answered by the parent (unclassified).
    parent_grades = dict.fromkeys(range(9), 1.0) | dict.fromkeys(range(9, 23), 0.0)
    ruler = _ruler({sid: float(sid % 7) for sid in range(20)}, mu=2.85)

    order = build_round_order(parent_grades, ruler, ids)
    assert sorted(order) == ids
    # Deterministic: same inputs, same order.
    assert build_round_order(parent_grades, ruler, ids) == order

    hit_set = set(range(9))
    # Positions 4, 8, 12, 16 (1-indexed) carry parent-HIT probes while both strata remain.
    for pos in (4, 8, 12, 16):
        assert order[pos - 1] in hit_set, f"position {pos} should be a parent-hit probe"
    # All other early positions are win opportunities, never regression probes.
    non_probe_head = [order[i] for i in range(16) if (i + 1) % 4 != 0]
    assert all(sid not in hit_set for sid in non_probe_head)
    # Computed, not spelled: a hardcoded default here would pass while disagreeing with the ruler.
    unmeasured = ruler.mu_delta
    assert min(ruler.delta.values()) < unmeasured < max(ruler.delta.values())
    # MISS stratum walks ascending δ (a miss the ruler has no δ for still sits at the centre),
    # and every one is reached before the unknown GRADE: an opportunity the parent actually
    # failed outranks one nobody has tried.
    miss_positions = [sid for sid in order if sid not in hit_set and sid != 23]
    miss_keys = [(ruler.delta.get(sid, unmeasured), sid) for sid in miss_positions]
    assert miss_keys == sorted(miss_keys)
    assert order.index(23) > max(order.index(sid) for sid in miss_positions)
    # HIT stratum walks descending δ (likeliest regression points first).
    hit_positions = [sid for sid in order if sid in hit_set]
    hit_keys = [(-ruler.delta.get(sid, unmeasured), sid) for sid in hit_positions]
    assert hit_keys == sorted(hit_keys)

    # The round-1 case, and the one the two-state predicate got wrong: the parent has answered
    # NOTHING on this panel, so there is no miss stratum to front-load and no hit stratum to
    # probe from. The order must then lead with the cells that discriminate most — δ nearest the
    # scale's centre — not with the panel's easiest, which separate no arm from any other.
    all_unknown = build_round_order({}, ruler, ids)
    assert sorted(all_unknown) == ids
    spread = [abs(ruler.delta.get(sid, unmeasured) - unmeasured) for sid in all_unknown]
    assert spread == sorted(spread), (
        "an all-unknown panel must open on its most discriminating cells"
    )
    easiest = min(ruler.delta, key=lambda sid: (ruler.delta[sid], sid))
    assert all_unknown.index(easiest) >= 6, "the panel's easiest cell must not lead the round"


def test_elimination_p_best_discriminates_on_graded_backend() -> None:
    """The PoBB ε-gate must read GRADED responses, not binarized hits.

    Silent harm: on a graded backend (L4 outer, reciprocal-rank) every ``hit`` is
    False, so a binarized gate fits identical all-0 θ for every arm and pins
    ``p_best = 0.5`` forever — elimination never discriminates, with no error.
    Graded inputs must separate a plainly-better candidate; binary inputs must be
    bit-identical to the historical hit-vector behavior.
    """
    from promptpotter.application.scoring.selection import elimination_p_best

    sids = list(range(12))
    ruler = None  # cold ruler — flat δ, the common early-cycle case

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


def test_the_two_mean_intervals_differ_where_it_matters_and_neither_invents_width() -> None:
    """Two readings of one mean, and they are NOT interchangeable at the sizes this loop runs.

    ``mean_ci`` is the always-on per-candidate composite band — a normal-CLT interval carrying
    PoBB's own SE floor, drawn as the loop believed it. ``mean_ci_t`` is the Compare read over a
    handful of cells, where t is 2.571 against z's 1.96 at n=6. A wrong bound in either mislabels
    a real difference as noise or the reverse, with nothing to see.

    They part on the degenerate n as well as on the width, which is the half worth pinning:
    ``mean_ci`` answers for one reading because the posterior it draws IS defined there, and
    ``mean_ci_t`` refuses, because a distribution-free bracket from a single value is a fiction.
    """
    assert mean_ci([]) == (0.0, 0.0, 0.0)
    assert mean_ci_t([]) is None
    mean, lo, hi = mean_ci([0.7])
    assert mean == 0.7 and lo < mean < hi
    assert mean_ci_t([0.5]) is None

    values = [0.40, 0.50, 0.45, 0.52, 0.38, 0.49]
    exact = sum(values) / len(values)
    _, lo_z, hi_z = mean_ci(values)
    reading = mean_ci_t(values)
    assert reading is not None
    mean_t, lo_t, hi_t, n = reading
    assert n == len(values)
    assert abs(mean_t - exact) < 1e-9
    assert lo_z < exact < hi_z
    assert lo_t < mean_t < hi_t
    # t is the WIDER of the two on identical data — a swap back to ``norm.ppf`` fails here.
    assert (hi_t - lo_t) > (hi_z - lo_z)


def test_paired_reading_matches_ttest_rel_and_brackets_the_same_evidence_it_tests() -> None:
    """Checked against an INDEPENDENT oracle, so a sidedness flip or a df off-by-one goes red
    rather than agreeing with itself. The interval and the p come from ONE posterior, so the
    silent failure this pins is the two disagreeing about zero — a bracket excluding it beside a
    p that does not, or the reverse. The floor case pins the documented deviation instead of
    hiding it: ``_normal_posterior`` clips the SE at ``1/(4n)``, so a near-constant difference
    reads far LESS significant here than a textbook paired t-test."""
    from scipy.stats import ttest_rel

    # Spread wide enough that the 1/(4n) floor does not bind, so the two must agree exactly.
    cand = [0.90, 0.10, 0.85, 0.20, 0.75, 0.30, 0.95, 0.05]
    prior = [0.10, 0.85, 0.15, 0.80, 0.20, 0.70, 0.05, 0.90]
    reference = float(ttest_rel(cand, prior).pvalue)

    mean_d, lo, hi, p_two, n = paired_reading(cand, prior)
    assert n == len(cand)
    assert abs(mean_d - sum(c - p for c, p in zip(cand, prior, strict=True)) / n) < 1e-12
    assert p_two is not None and abs(p_two - reference) < 1e-12

    # One posterior, one verdict: a p above 0.05 and a bracket clearing zero cannot coexist.
    assert lo is not None and hi is not None and lo < mean_d < hi
    assert (p_two < 0.05) == (lo > 0.0 or hi < 0.0)

    p_greater = paired_reading(cand, prior, tail="greater")[3]
    assert p_greater is not None and abs(p_greater - reference / 2.0) < 1e-12

    # Reading a pair in either order must give one number — the whole point of the two-sided test.
    assert paired_reading(prior, cand)[3] == p_two

    # The floor binds: a difference this tight is "significant" to a textbook test and must not be
    # to this one.
    tight_cand = [0.5000001 * i for i in range(1, 7)]
    tight_prior = [0.5 * i for i in range(1, 7)]
    tight_p = paired_reading(tight_cand, tight_prior)[3]
    assert tight_p is not None and tight_p > float(ttest_rel(tight_cand, tight_prior).pvalue)

    # One pair tests nothing and brackets nothing — absent, not a p of 1.0 nor a zero-width bar.
    assert paired_reading([0.5], [0.1])[1:4] == (None, None, None)


def test_an_exact_paired_reading_refuses_the_resolution_its_width_lacks() -> None:
    """The silent harm this replaces: a t on six cells returns a p below what ANY exact test on six
    pairs can reach, so the extra resolution came from the assumed tail rather than from the cells —
    and a roster read it as a result. Nothing raises; the table just reads significant."""
    from scipy.stats import ttest_rel

    from promptpotter.shared.statistics import (
        cells_for_exact_verdict,
        exact_p_floor,
        exact_paired_reading,
    )

    # The floor is the whole point: two of the 2**n sign draws put every difference on one side.
    assert exact_p_floor(6) == pytest.approx(2 / 64)
    assert exact_p_floor(0) == 1.0

    # A CLEAN SWEEP is the most six pairs can say, and it says exactly the floor — never less.
    won = [0.600, 0.610, 0.600, 0.605, 0.600, 0.602]
    lost = [0.5] * 6
    sweep = exact_paired_reading(won, lost)
    assert sweep[3] == pytest.approx(exact_p_floor(6))
    assert float(ttest_rel(won, lost).pvalue) < exact_p_floor(6)

    # Hodges-Lehmann over the mean: one wild cell drags a mean across zero and moves a median of
    # Walsh averages by nothing. Five cells up 0.1, one down 5.0 — the arm still reads +0.1.
    outlier = exact_paired_reading([0.1] * 5 + [-5.0], [0.0] * 6)
    assert outlier[0] == pytest.approx(0.1)
    assert sum([0.1] * 5 + [-5.0]) / 6 < 0.0

    # Below six pairs no 95% distribution-free bracket EXISTS, so none is served; at six the
    # bracket is served and contains the estimate it was drawn with.
    assert exact_paired_reading([0.6] * 5, [0.5] * 5)[1] is None
    lo, hi = sweep[1], sweep[2]
    assert lo is not None and hi is not None and lo <= sweep[0] <= hi

    # What to BUY: Holm's tightest step over m tests, solved for width. These are the numbers a
    # panel is sized against, and they do not move with effect size.
    assert cells_for_exact_verdict(1) == 6
    assert cells_for_exact_verdict(3) == 7
    assert cells_for_exact_verdict(21) == 10


def test_holm_adjusted_enforces_monotonicity_and_preserves_input_order() -> None:
    """A plain Bonferroni pass satisfies the first case and fails the second — which is the point
    of pinning the second. Holm is a STEP-DOWN: an adjusted p may never fall below the one before
    it in rank order, so the running maximum is load-bearing, not defensive."""
    assert holm_adjusted([]) == []
    assert holm_adjusted([0.037]) == [0.037]  # m=1 is the identity

    # sorted 0.01,0.03,0.04 -> x3, x2, x1 = 0.03, 0.06, 0.04; the last is dragged up to 0.06.
    assert holm_adjusted([0.01, 0.04, 0.03]) == pytest.approx([0.03, 0.06, 0.06])

    # Bonferroni would give [0.06, 0.063, 1.0]; the step-down gives the middle term the max.
    assert holm_adjusted([0.02, 0.021, 0.9]) == pytest.approx([0.06, 0.06, 0.9])

    # Order is the CALLER's, not rank order — a table renders rows where it put them.
    assert holm_adjusted([0.9, 0.01, 0.04]) == pytest.approx([0.9, 0.03, 0.08])
    assert all(0.0 <= p <= 1.0 for p in holm_adjusted([0.4, 0.6, 0.8]))


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
    docstrings on both sides asserted the union WAS "the parent rescored over every
    sample probed so far", a rescore that never happened.

    Because the round subset is the CONTESTED one, the carried rows are the easy tail the
    previous config scored ~100% on, so the bias has a direction: each configuration
    inherited its predecessor's perfect easy-tail score and a regression there was invisible.
    """
    # Shaped like `index.json::rounds[]`: each round's own panel measurement, its winner scored
    # on the line's SHARED cells, and the pooled number the old basis would have published.
    rounds = [
        {"round": 0, "accuracy": 0.575, "overlap_accuracy": None, "pooled": 0.575},
        {"round": 6, "accuracy": 0.750, "overlap_accuracy": 0.600, "pooled": 0.750},
        {"round": 7, "accuracy": 0.679, "overlap_accuracy": 0.640, "pooled": 0.775},
    ]
    best, best_round = best_round_on_shared_cells(rounds)

    shared = {r["overlap_accuracy"] for r in rounds if r["overlap_accuracy"] is not None}
    assert best in shared, "headline must be a value some round actually scored"
    assert best < max(r["pooled"] for r in rounds), "the pooled basis outran every measurement"

    # The panel-difficulty trap, and the reason the basis moved off `accuracy`: round 6 has the
    # higher own-panel number and round 7 the higher score on the cells BOTH answered. Electing on
    # `accuracy` crowns whichever round bought the easiest subset — live on
    # `justlogic-d234__960ea6`, round 1 held the cycle's best accuracy and its worst shared-cell
    # score, because the acquisition does not hold the panel still between rounds.
    assert best == 0.640 and best_round == 7

    # A cycle whose line shares no measurable cell has run only its origin, and answers with it.
    assert best_round_on_shared_cells([{"round": 0, "accuracy": 0.5}]) == (0.5, 0)
    # A round with no number at all doesn't back the headline (vs `or 0.0`, which could crown it).
    assert best_round_on_shared_cells([{"round": 1, "accuracy": None}]) == (0.0, None)
    assert best_round_on_shared_cells([]) == (0.0, None)


def test_a_stale_cycle_index_is_re_derived_from_its_rounds_rather_than_believed(
    tmp_path: Path,
) -> None:
    """The index is a DERIVED read model, so a projection that GAINS a field leaves every index
    written before it stale against its own round documents — silently, because each derivation
    reads the row it was handed rather than the document behind it. Measured 2026-08-30: 22 of 24
    cycle indexes on disk carried no ``overlap_accuracy`` on any row while 70 round documents held
    the reading, so the headline either kept a pre-change argmax or fell through to the origin.
    Neither is a number the cycle measured, and no gate could see the difference.
    """
    import json

    from promptpotter.infrastructure.store.campaign_store.store import reproject_round_index

    def overlap(rnd: int, origin_rate: float, own_rate: float) -> OverlapReading:
        member = lambda r, rate: OverlapMember(  # noqa: E731
            round=r, candidate_id=f"c{r}", label=f"C{r}", accuracy=rate, total=28
        )
        return OverlapReading(
            sample_ids=list(range(28)), members=[member(0, origin_rate), member(rnd, own_rate)]
        )

    # Round 3 bought the EASIEST panel: the cycle's best own-panel accuracy and its worst score on
    # the cells the whole line answered. Shaped on `justlogic-d234__3d3e63`, where round 3 read
    # 0.821 against an archive that passes that panel 89% of the time.
    cycle = tmp_path / "cycles" / "cyc1"
    (cycle / "rounds").mkdir(parents=True)
    docs = []
    for rnd, acc, own in ((0, 0.50, None), (3, 0.82, 0.11), (4, 0.61, 0.14)):
        rr = round_result(
            rnd, accuracy=acc, overlap=overlap(rnd, 0.07, own) if own is not None else None
        )
        doc = cycle / "rounds" / f"round_{rnd:04d}.json"
        doc.write_text(json.dumps(rr.model_dump(mode="json")), encoding="utf-8")
        docs.append(doc)

    # The stale shape: rows projected before the field existed, and a headline from an older basis.
    stale = {"round": 3, "accuracy": 0.82}
    index = cycle / "index.json"
    index.write_text(
        json.dumps(
            {
                "rounds": [{"round": 0, "accuracy": 0.50}, stale, {"round": 4, "accuracy": 0.61}],
                "best_accuracy": 0.82,
                "best_round": 3,
            }
        ),
        encoding="utf-8",
    )
    # Two wrong answers, and the row cannot tell them apart: the number ON DISK crowns the easiest
    # panel, while re-deriving from the same stale rows collapses a five-round cycle onto round 0.
    on_disk = json.loads(index.read_text(encoding="utf-8"))
    assert (on_disk["best_accuracy"], on_disk["best_round"]) == (0.82, 3)
    assert best_round_on_shared_cells(on_disk["rounds"]) == (0.50, 0)

    assert reproject_round_index(index, docs) is True
    fresh = json.loads(index.read_text(encoding="utf-8"))
    # The shared-cell reading the documents carried all along: 0.07 → 0.11 → 0.14, so round 4 is
    # the best the line ever managed on one exam — and 0.14, not 0.82, is what it managed.
    assert (fresh["best_accuracy"], fresh["best_round"]) == (0.14, 4)
    # Round 0 has no reading of its own; the derivation lifts C0's rate out of the NEWEST round's
    # members, or the origin never reaches the election it is the baseline for.
    assert [r["overlap_accuracy"] for r in fresh["rounds"]] == [0.07, 0.11, 0.14]

    # Idempotent, so the maintenance walk's count is a real backlog rather than its own footprint.
    assert reproject_round_index(index, docs) is False


def test_every_carrier_of_a_round_row_projects_the_overlap_pair() -> None:
    """The headline is elected on a flattened pair that no ``RoundResult`` FIELD holds, and two
    carriers build the rows — the cycle index and the resume rebuild. Handed a bare ``model_dump()``
    the election finds no shared cell on any round and collapses the whole trajectory onto round 0:
    silent, plausible, and on every resume and every rewind. So the trap is pinned here, not just
    the agreement — a third carrier that forgets the projection reads a different fact."""
    from promptpotter.domain.results import overlap_row
    from promptpotter.infrastructure.store.campaign_store.store import _index_round

    def overlap(rnd: int, origin_rate: float, own_rate: float) -> OverlapReading:
        member = lambda r, rate: OverlapMember(  # noqa: E731
            round=r, candidate_id=f"c{r}", label=f"C{r}", accuracy=rate, total=28
        )
        return OverlapReading(
            sample_ids=list(range(28)), members=[member(0, origin_rate), member(rnd, own_rate)]
        )

    # Round 2 wins its OWN panel by a mile; round 1 wins the cells the whole line answered. The
    # two carriers must therefore crown round 1, and any row shape that loses the pair crowns C0.
    rounds = [
        round_result(0, accuracy=0.50),
        round_result(1, accuracy=0.60, overlap=overlap(1, 0.50, 0.72)),
        round_result(2, accuracy=0.90, overlap=overlap(2, 0.50, 0.55)),
    ]

    indexed = best_round_on_shared_cells([_index_round(rr) for rr in rounds])
    resumed = best_round_on_shared_cells(
        [{**rr.model_dump(), **overlap_row(rr.overlap)} for rr in rounds]
    )
    assert indexed == resumed == (0.72, 1)

    # The trap itself. A row holding the reading WHOLE but not the flattened pair is the one shape
    # the derivation cannot survive quietly, so it REFUSES rather than crowning C0 at 0.50.
    with pytest.raises(ValueError, match="overlap_row"):
        best_round_on_shared_cells([rr.model_dump() for rr in rounds])

    # Rows older than the projection carry neither key and stay readable — unmeasured, not broken.
    assert best_round_on_shared_cells([{"round": 0, "accuracy": 0.50}]) == (0.50, 0)


def test_delta_ruler_stays_flat_until_a_second_arm_exists() -> None:
    # SILENT wrong-scale: with ONE ability the likelihood cannot separate "this item is hard" from
    # "this arm missed it", so δ collapses into a two-valued restatement of that arm's own hit
    # pattern — and every later θ in the cycle becomes a restatement of whether round 0 happened
    # to get the sample right. It is not an error; it is a plausible ruler that reads backwards.
    # It cost two campaigns: rounds carrying +14.3pp at p<0.05 were stamped `improved=False`.
    # The warm attempt now also runs BEFORE the round's election (`warm_ruler_if_cold`), which
    # relaxes the TIMING only — this is what pins the rule itself as untouched.
    from promptpotter.application.intelligence.exploration import Observation
    from promptpotter.application.optimization.cycle import _calibrate_delta_ruler

    n_min = 4
    arm_a = [Observation("a", sid, 1.0 if sid % 3 else 0.0) for sid in range(8)]

    # Eight distinct samples clears any sample floor on its own — the arm count is the binding
    # condition, and a fresh campaign hands this function exactly this shape.
    flat, _ = _calibrate_delta_ruler(None, n_min, enable_2pl=False, archive_obs=arm_a)
    assert flat is None

    arm_b = [Observation("b", sid, 1.0 if sid < 5 else 0.0) for sid in range(8)]
    warm, _ = _calibrate_delta_ruler(None, n_min, enable_2pl=False, archive_obs=arm_a + arm_b)
    assert warm is not None and warm.calibration_model == "1PL"


def test_panel_precision_names_the_lever_the_panel_needs() -> None:
    # A candidate's `matched_parent_lift` interval is computed from the spread of the per-cell
    # diffs and nothing else, so it cannot distinguish two opposite problems: cells that each
    # measured themselves badly, versus cells that genuinely disagree. They take opposite levers —
    # sharpen the instrument, or widen the panel/candidates — and the operator picks from these two
    # numbers. SILENT: every wrong value here is a plausible-looking bar that sends the next spend
    # at the wrong problem.
    from promptpotter.domain.l4.proxies import PARENT_LEVEL_SE_KEY, panel_precision

    # The on-disk key, pinned as a literal: it is a wire contract between the emit site, the
    # infra-key allow-list and the reader, and a rename that moved only the constant would
    # silently stop the panel finding any SE at all — which reads as "no cells", not as an error.
    assert PARENT_LEVEL_SE_KEY == "mean_parent_level_se"

    def rows(cells: dict[str, tuple[float, float]]) -> list[dict]:
        return [
            {
                "query": c,
                "fitness": (v + 1.0) / 3.0,
                "pipeline_data": {"mean_round_delta": v, "mean_parent_level_se": se},
            }
            for c, (v, se) in cells.items()
        ]

    flat_origin = rows(dict.fromkeys("abc", (0.0, 0.02)))

    # Cells that AGREE (diffs .50/.52/.48) but each measured itself poorly: the panel is reading
    # its own noise, and buying more cells like these buys nothing.
    noisy = panel_precision(
        rows({"a": (0.50, 0.40), "b": (0.52, 0.40), "c": (0.48, 0.40)}), flat_origin
    )
    assert noisy is not None
    # Claimed noise EXCEEDS the total spread it is a component of — impossible, and that
    # impossibility IS the finding. The retired `estimation_share` divided these and clamped with
    # `min(1.0, …)`, rendering a raw 5.55 as a tidy "100% measurement noise". Serving both bars
    # unreduced is what makes the contradiction visible instead of plausible.
    assert noisy.estimation_sd > noisy.observed_sd

    # Cells that genuinely DIFFER (.10/.90/.50) while each is measured sharply: the spread is
    # signal about where this optimizer prompt works, not instrument noise.
    real = panel_precision(
        rows({"a": (0.10, 0.02), "b": (0.90, 0.02), "c": (0.50, 0.02)}), flat_origin
    )
    assert real is not None and real.estimation_sd < real.observed_sd / 10
    assert real.observed_sd == pytest.approx(0.4)  # SAMPLE sd (n-1), not the population one

    # BOTH ARMS' errors enter, in quadrature. The variant and the origin are separate inner
    # campaigns on the same cell, so counting only the variant's understates the diff's error by
    # sqrt(2) — which makes a noise-dominated panel read as half signal.
    assert real.estimation_sd == pytest.approx((2 * 0.02**2) ** 0.5)
    assert real.n_cells == 3

    # One shared cell has no spread to decompose: None, never a fabricated 0.0 (which reads as
    # "all signal" and would argue for spending on more cells at the exact moment it cannot know).
    assert panel_precision(rows({"a": (0.5, 0.02)}), rows({"a": (0.0, 0.02)})) is None
    # A cell whose rows carry no SE is dropped rather than guessed at.
    assert (
        panel_precision(
            [{"query": "a", "fitness": 0.5, "pipeline_data": {"mean_round_delta": 0.5}}],
            [{"query": "a", "fitness": 0.3, "pipeline_data": {"mean_round_delta": 0.0}}],
        )
        is None
    )


def test_a_round_that_resolves_nothing_says_so(monkeypatch) -> None:
    # The one degradation with NO other channel: the round measured cleanly, crowned a winner, and
    # every number on every surface reads exactly as it would on a decisive round. Nothing failed,
    # so no rail fires; the operator reads a margin that the panel cannot support. SILENT by
    # construction, which is why the warning is the whole mechanism.
    from promptpotter.application.optimization.l1.score import winner as winner_mod

    fired: list[dict] = []
    monkeypatch.setattr(winner_mod, "emit_round_warning", lambda **kw: fired.append(kw))

    def arm(cid: str, lo: float, hi: float):
        return scored_candidate(cid, accuracy=0.5).model_copy(
            update={
                "matched_parent_lift": (lo + hi) / 2,
                "matched_parent_lift_ci_lo": lo,
                "matched_parent_lift_ci_hi": hi,
            }
        )

    # Every arm straddles 0 — nothing here separates from the origin.
    assert winner_mod._separability(2, [arm("a", -0.05, 0.21), arm("b", -0.30, 0.10)]) is False
    assert len(fired) == 1
    assert fired[0]["kind"] == "round_not_separable"
    # The message must carry the BEST case, not the count alone: "two arms were inconclusive" and
    # "the best of them could not clear +0.21" ask for different next moves.
    assert fired[0]["detail"] == {"arms": 2, "best_ci_hi": 0.21}

    # ONE arm clearing 0 is enough — the round resolved something, and warning anyway would train
    # the operator to ignore the kind.
    fired.clear()
    assert winner_mod._separability(2, [arm("a", -0.05, 0.21), arm("b", 0.04, 0.30)]) is True
    assert not fired

    # No arm carries an interval (below two shared cells — a one-cell panel, every round). There
    # is nothing to be inconclusive ABOUT, and firing here would be noise on every single round.
    # `None`, never `False`: the escalation gate treats "resolved nothing" as a stall, and an
    # unreadable round is not evidence of one.
    assert winner_mod._separability(2, [scored_candidate("a", accuracy=0.5)]) is None
    assert not fired


def test_inner_narratives_never_rank_a_cell_noise_put_first() -> None:
    # This panel's ORDER decides which inner campaigns spend the 4x narrative cap in the outer
    # `l1_generate` prompt, and the prompt tells the model to ground its next candidate in one of
    # them. Ranked on the point estimate alone that order is a coin flip — the measured panel held
    # `0.000 ±0.336` beside `+0.257 ±0.393`, gaps narrower than either bar — and the prompt asserts
    # it is real. SILENT in every direction: the prompt renders, the round scores, the campaign
    # completes, and the only symptom is an optimizer that learns nothing over many paid rounds.
    from promptpotter.application.optimization.dispatch.bundle import (
        CycleSlice,
        InjectionBundle,
        RoundDiagnostics,
        RoundDigest,
    )
    from promptpotter.application.optimization.dispatch.injections.panels import (
        _r_inner_narratives,
    )
    from promptpotter.domain.opt_search_point import OptSearchPoint

    # Long enough that the summary cap (160) truncates and the full cap (1150) does not — the
    # difference between the two IS the budget this panel spends, so it must be visible here.
    trace = "\n".join(
        f"round {i}: the inner loop tried a thing and it did not land" for i in range(12)
    )

    def cell(sid: int, delta: float, se: float | None) -> dict:
        pd: dict = {"mean_round_delta": delta, "reasoning_trace": trace}
        if se is not None:
            pd["mean_parent_level_se"] = se
        return {"sample_id": sid, "query": f"seed-{sid}", "pipeline_data": pd}

    from promptpotter.application.optimization.dispatch.compose import SECTION_SEP

    def render(rows: list[dict], origin: list[dict], *, at_origin: bool = False) -> str:
        return SECTION_SEP.join(
            item.text
            for item in _r_inner_narratives(
                InjectionBundle(
                    is_origin_round=at_origin,
                    opt_sp=OptSearchPoint(),
                    pipeline_schema=None,
                    cycle_slice=CycleSlice(
                        round_num=1,
                        current_accuracy=0.5,
                        best_accuracy=0.5,
                        best_round=0,
                        l1_stall_count=0,
                        l2_round=0,
                        l2_stall_count=0,
                        l3_round=0,
                        l3_stall_count=0,
                        exploration_budget="tight",
                    ),
                    digest=RoundDigest(
                        diagnostics=RoundDiagnostics(n_valid=0, samples=[]), critique=None
                    ),
                    axes=None,
                    origin_per_sample=origin,
                    trajectory_results=rows,
                    # The recursion DECLARES its noun; the panel never sniffs one off a row.
                    measured_unit="cell",
                )
            )
        )

    origin = [cell(1, 0.0, 0.02), cell(2, 0.0, 0.02), cell(3, 0.0, 0.02)]

    # Seed 1 is the worst POINT (-0.90) and knows nothing (±0.80); seed 2 is milder (-0.30) and
    # measured sharply. Only seed 2 is confidently below the origin, so it must lead — and the
    # panel must not hand seed 1 the full narrative on the strength of a number it cannot support.
    out = render([cell(1, -0.90, 0.80), cell(2, -0.30, 0.05), cell(3, 0.10, 0.05)], origin)
    assert out.index("seed-2") < out.index("seed-1"), "a wide cell must not lead a tight one"
    assert "The first 1 are worse than the origin" in out
    # Exactly the ONE separated cell buys the full narrative; the rest are summarised.
    assert out.count("[…truncated]") == 2

    # The subtraction is SERVED, not asked for: the prompt used to print the candidate's lift
    # beside the origin's and explain how to difference them, which is arithmetic the model had to
    # get right before the evidence meant anything.
    # ±0.054 = sqrt(0.05² + 0.02²) — BOTH arms' halves in quadrature, not the candidate's alone.
    assert "-0.300 ±0.054" in out and "+0.100 ±0.054" in out

    # Nothing separates ⇒ the order carries no information, and the panel says so instead of
    # ranking anyway.
    flat = render([cell(1, 0.00, 0.34), cell(2, 0.26, 0.39), cell(3, 0.10, 0.35)], origin)
    assert "NOT ONE cell separates" in flat
    assert "worse than the origin" not in flat
    # The cells still NARRATE. This assertion used to run the other way — no cell bought the full
    # cap, on the argument that spending it here amplifies whichever cell noise ranked first. That
    # argument was against amplifying ONE exemplar, and withholding every trace was the wrong
    # answer to it: on `promptpotter-self__b40e8b` the zero branch collapsed all six cells to their
    # stat lines from round 2 on, and the generator — whose instruction orders it to ground each
    # candidate in a specific narrative observation — had nothing left but the critique. It cited
    # the critique while declaring the field `inner_narratives` on 4 of 9 candidates, and neither
    # round resolved anything, which suppressed the next round's traces in turn. Equal depth across
    # the leading cells answers the original concern without starving the node: no single cell is
    # held up, and the header above already says the rank means nothing.
    assert flat.count("[…truncated]") == 0
    assert flat.count("round 11: the inner loop tried a thing") == 3, "every cell kept its story"

    # A FLOORED cell adopts no levels, so it carries no SE — and it is the one cell whose badness
    # is not in question. It ranks on its value alone and leads, rather than being dropped for
    # lacking a bar.
    floored = render([cell(1, -1.00, None), cell(2, 0.10, 0.05)], origin)
    assert floored.index("seed-1") < floored.index("seed-2")
    assert "-1.000 (unpriced)" in floored

    # AT THE ORIGIN both arms are the same rows, so the paired difference is every cell against
    # ITSELF. Measured on `promptpotter-self__c355c2`: six seeds that had each gained (+0.405 to
    # +0.734) rendered `+0.000` six times, and the C0 critique read that as "all 6 seeds show zero
    # net lift ... not exploring beyond baseline space" — then spent the round's whole candidate
    # budget escaping a baseline nothing had shown it was stuck on. SILENT: the header took its
    # honest no-signal branch and the numbers under it won anyway. The seed's OWN lift is the
    # measurement at C0, carrying its own SE rather than a difference's.
    c0_rows = [cell(1, 0.405, 0.27), cell(2, 0.734, 0.28)]
    at_c0 = render(c0_rows, c0_rows, at_origin=True)
    assert "+0.000" not in at_c0
    assert "+0.405 ±0.270" in at_c0 and "+0.734 ±0.280" in at_c0
    assert "ORIGIN round" in at_c0 and "no edit to compare against yet" in at_c0
    # Weakest first, and every cell earns the full trace cap: at C0 there is no "worse than the
    # origin" subset to spend it on, and the weakest seeds are what the first edit must attack.
    assert at_c0.index("seed-1") < at_c0.index("seed-2")


def test_matched_parent_lift_drops_the_cell_that_measured_nothing() -> None:
    # An ERRORED cell measured nothing, and here "nothing" is not a low score: outer fitness
    # transforms `mean_round_delta`, so a floored 0.0 asserts the optimizer prompt drove the inner
    # loop maximally DOWN. The ELECTION grades it 0.0 on purpose (`_mean_fitness_by_cell` is
    # un-predicated so the overlap guard works); a published interval must not, because it is drawn
    # beside a point estimate and has to bracket that estimate's own population — the rule
    # `mean_fitness_ci` already follows on the same row. SILENT: the row looks like any other, the
    # interval still prints, and the arm is convicted on a cell that never ran.
    from promptpotter.application.scoring.selection import matched_parent_lift

    clean = [{"query": q, "sample_id": i, "fitness": 0.9} for i, q in enumerate("abc")]
    origin = [{"query": q, "sample_id": i, "fitness": 0.5} for i, q in enumerate("abc")]
    errored = [
        *clean,
        {
            "query": "d",
            "sample_id": 3,
            "fitness": 0.0,
            "error_category": "UNKNOWN",
            "predicted": "ERROR",
        },
    ]

    lift = matched_parent_lift(errored, [*origin, {"query": "d", "sample_id": 3, "fitness": 0.5}])
    assert lift is not None
    assert lift[0] == pytest.approx(0.4)  # not dragged toward the floor by cell d
    assert lift[1] < lift[0] < lift[2]

    # Below two shared cells there is no spread, so no interval — reported as absence rather than
    # as the `_normal_posterior` n=1 fallback, which invents an SE of 0.5 out of one reading.
    assert matched_parent_lift(clean[:1], origin[:1]) is None


def test_panel_precision_subject_is_an_arm_the_round_could_read() -> None:
    # The precision read is taken off ONE arm — the winner, else the best-scoring one — and
    # `max(fitness)` reaches for exactly the arms that FAKE a score: a degradation abort scoring a
    # partial subset, and a constant answerer scoring the drawn subset's label mix. The election
    # already refuses both (`electable`); the projection must refuse the same set or the round's
    # instrument reading comes from a cell set the round would not crown on. SILENT: the served
    # number renders identically whichever arm supplied it.
    from promptpotter.domain.results import is_electable
    from promptpotter.infrastructure.projections.live_dashboard.round_summary import (
        build_round_summary,
    )

    truths = (("a", "T"), ("b", "F"), ("c", "T"), ("d", "F"))

    def cells(fitness: float, *, constant: bool, se: float) -> list[dict[str, object]]:
        # Truth VARIES, and there are more cells than labels — `is_answer_collapsed`
        # self-normalizes below that, since one row per label cannot tell a constant answer
        # from a correct one.
        return [
            {
                "query": q,
                "sample_id": i,
                "fitness": fitness,
                "ground_truth": gt,
                "predicted": "T" if constant else gt,
                "pipeline_data": {"mean_round_delta": fitness, "mean_parent_level_se": se},
            }
            for i, (q, gt) in enumerate(truths)
        ]

    origin = [
        {
            "query": q,
            "sample_id": i,
            "fitness": 0.4,
            "pipeline_data": {"mean_round_delta": 0.4, "mean_parent_level_se": 0.01},
        }
        for i, (q, _) in enumerate(truths)
    ]
    # The arms are separated by their PRECISION, not their score, so the assertion below can only
    # pass if the projection read the honest arm's cells.
    measured = {
        "hot": cells(0.9, constant=True, se=0.30),
        "cool": cells(0.5, constant=False, se=0.05),
    }
    hot = scored_candidate("hot", accuracy=0.9)
    cool = scored_candidate("cool", accuracy=0.5)

    # The predicate: leader-eligible, measured, and not a constant answerer — all three clauses.
    assert is_leader_eligible(hot)  # nothing about its RUN was invalid
    assert not is_electable(hot, measured["hot"])  # it still said one label to everything
    assert not is_electable(hot, [])  # an arm with no rows has nothing to read
    assert is_electable(cool, measured["cool"])

    # And through the projection: the collapsed arm outscores the honest one, so an unfiltered
    # `max` names "hot". The round could only read "cool".
    def summarize(*scores):
        rr = round_result(1, candidates_scored=0).model_copy(
            update={
                "candidates_scored": len(scores),
                "candidate_scores": list(scores),
                "all_candidate_results": {s.candidate_id: measured[s.candidate_id] for s in scores},
            }
        )
        return build_round_summary(rr, origin)

    precision = summarize(hot, cool).panel_precision
    assert precision is not None
    assert precision.estimation_sd == pytest.approx((0.05**2 + 0.01**2) ** 0.5)

    # Every arm refused — the round measured nothing it can attribute, so there is no instrument
    # reading. Reported as absence, never off the least-bad arm.
    assert summarize(hot).panel_precision is None


def test_an_arm_at_zero_on_every_cell_is_floor_pinned_and_a_scale_verdict_is_not() -> None:
    """The FOURTH state θ can be in, and the only per-ARM one.

    An all-zero response vector carries no information about ability, so the fit falls back on the
    prior and θ lands wherever the δ vector and n put it — a constant of the CELLS. Every lift
    taken against it then reads `0.000` whatever the arm did, which is the damage: the level looks
    merely bad, the comparison looks settled.
    """
    zeros = [{"sample_id": i, "objective": 0.0} for i in range(4)]
    assert is_floor_pinned(zeros)
    # ONE non-zero cell is a measurement, not a floor — the vector has variance to fit.
    assert not is_floor_pinned([*zeros[:3], {"sample_id": 3, "objective": 0.25}])

    # Absence is not a zero, in both directions. An arm that answered nothing is UNMEASURED, and
    # an errored cell is absence too — counting either would pin an arm that never scored.
    assert not is_floor_pinned([])
    assert not is_floor_pinned([{"sample_id": 1, "error": "boom"}])
    assert not is_floor_pinned([{"sample_id": 1}])
    # …and an errored cell beside real zeros does not rescue the arm from the verdict either.
    assert is_floor_pinned([*zeros, {"sample_id": 9, "error": "boom"}])

    # Distinct from answer-collapse, which is a PoBB CUT: that one is about the arm saying one
    # thing. An arm can be wrong on every cell while answering differently every time, and that is
    # a real measurement — electable, scoreable, and still not a θ.
    varied = [
        {"sample_id": 0, "objective": 0.0, "predicted": "a", "ground_truth": "x"},
        {"sample_id": 1, "objective": 0.0, "predicted": "b", "ground_truth": "y"},
    ]
    assert is_floor_pinned(varied)
    assert not is_answer_collapsed(varied)

    # The two SCOPES stay apart: `theta_caveat` reads the round's scale and cannot see an arm's
    # responses, so a perfectly sound ruler still returns None while an arm on it is pinned.
    assert theta_caveat(calibration_model="1PL", round_span=4.0, ruler_span=5.0) is None
    assert ThetaCaveat.FLOOR_PINNED not in {
        theta_caveat(calibration_model=m, round_span=r, ruler_span=s)
        for m, r, s in [(None, 4.0, 5.0), ("1PL", 0.1, 5.0), ("1PL", 0.5, 0.5)]
    }


def test_ruler_id_names_the_scale_a_theta_was_read_on() -> None:
    """Comparability is a machine-checkable fact or it is nothing. The cold ruler shares ONE id
    everywhere — flat δ makes θ plain logit-accuracy, which depends on no fit — while any refit
    gets its own, because δ estimates move with the archive the fit saw."""
    # A cold cycle has no fit to name, so its id is minted from the OBJECTIVE instead — there is
    # no one flat id any more, because two objectives on a flat ruler are two scales.
    assert is_flat_ruler_id(flat_ruler_id("acc"))
    assert flat_ruler_id("acc") != flat_ruler_id("acc_minus_latency")

    fitted = _ruler({1: 0.5, 2: -0.25, 3: 0.0})
    # Key order is not part of the scale.
    assert fitted.anchor_id == anchor_id_of({3: 0.0, 2: -0.25, 1: 0.5}, 0.0, 2.0, "1PL")
    # …and a genuinely different δ is a different scale, however small the move.
    assert fitted.anchor_id != anchor_id_of({1: 0.5, 2: -0.2500001, 3: 0.0}, 0.0, 2.0, "1PL")
    assert not is_flat_ruler_id(fitted.anchor_id)

    # THE ANCHOR, NOT THE MEMBERSHIP. Anchored extension adds cells without moving the ones
    # already there, so a θ read before and after are on ONE scale and must share an id. Hashing
    # the membership would churn it every round, read a cycle as incomparable with ITSELF, and —
    # since `evidence.py` reads round 0's id into `Comparability` — poison cross-campaign
    # comparison too.
    grown = extend_ruler(fitted, [Observation("arm", 1, 1.0), Observation("arm", 9, 0.0)])
    assert set(grown.delta) == {1, 2, 3, 9}
    assert grown.anchor_id == fitted.anchor_id
    assert all(grown.delta[sid] == fitted.delta[sid] for sid in fitted.delta)


def test_an_instrument_reads_on_the_scale_its_spawner_fixed(tmp_path: Path) -> None:
    """The dominant error in the L4 loop was the ESTIMATOR, not the measurement: an inner cell's
    evidence epoch hides everything banked before it started, so the only arms its δ fit could see
    were its OWN and the scale came out of the treatment under test. Byte-identical origin rows
    returned θ spread wider than a winning margin.

    Drop the instrument branch and nothing raises — every cell silently self-fits again and the
    loop keeps electing winners on a scale each of them invented."""
    import contextvars
    import types

    from promptpotter.application.optimization.cycle import _given_ruler
    from promptpotter.shared.instrument import enter_instrument_mode

    given = _ruler({1: 0.5, 2: -0.25, 3: 0.0})

    session: Any = types.SimpleNamespace(
        dataset_name="inner-bench", state=types.SimpleNamespace(cycle_id="")
    )
    # No mode bound: an ordinary campaign fits its own, exactly as before.
    assert _given_ruler(session) is None

    def _inside_instrument() -> Any:
        enter_instrument_mode(evidence_epoch=frozenset(), optimizer_clamp=None, ruler=given)
        return _given_ruler(session)

    # Its own context, as a real spawn binds it — so the scale cannot leak back to the spawner.
    assert contextvars.copy_context().run(_inside_instrument) is given


def test_one_ledger_carries_a_scale_per_dataset_and_a_fork_lifts_them_all(tmp_path: Path) -> None:
    """An L4 outer cycle owns two scales at once: its own cells, and the shared inner one its
    cells read. Keyed by nothing, the second write silently supersedes the first and half the
    campaign reads on the wrong scale with every number still rendering."""
    from promptpotter.domain.cycle_paths import CycleHop, WorkspaceDir
    from promptpotter.infrastructure.store.campaign_store.store import CampaignStore

    store = CampaignStore(WorkspaceDir(tmp_path / "tenant"))
    hop = CycleHop(campaign_id="c", cycle_id="cyc")
    outer, inner = _ruler({1: 0.5, 2: -0.25}), _ruler({7: 1.25, 8: -1.0, 9: 0.0})
    store.write_ruler(hop, outer, dataset_name="promptpotter-self", round_num=0)
    store.write_ruler(hop, inner, dataset_name="justlogic-d234", round_num=0)

    assert store.read_ruler(hop, dataset_name="promptpotter-self") == outer
    assert store.read_ruler(hop, dataset_name="justlogic-d234") == inner
    assert store.read_ruler(hop, dataset_name="never-run") is None

    # A fork that lifted only its own scale would re-fit the inner one from an archive that has
    # grown since the lock — the resume half of this bug, one level down.
    store.copy_rulers(hop, "cyc_fork_a1", round_num=1)
    fork = CycleHop(campaign_id="c", cycle_id="cyc_fork_a1")
    assert store.read_ruler(fork, dataset_name="promptpotter-self") == outer
    assert store.read_ruler(fork, dataset_name="justlogic-d234") == inner


def test_two_way_decomposition_reads_only_the_cells_every_arm_measured() -> None:
    """An arm scored on an easier subset would otherwise carry that subset's difficulty as its own
    effect, and the number would be wrong with nothing raising."""
    shared = {
        "a": {"c1": 0.0, "c2": 1.0},
        "b": {"c1": 0.5, "c2": 1.5},
    }
    cell_sd, arm_sd, residual = two_way_effect_sds(shared)
    # A pure additive table: arms differ by exactly 0.5, cells by exactly 1.0, nothing left over.
    assert arm_sd == pytest.approx(0.5 / 2**0.5)
    assert cell_sd == pytest.approx(1.0 / 2**0.5)
    assert residual == pytest.approx(0.0, abs=1e-12)

    # `b` also measured a third, much easier cell. It is EXCLUDED, so the reading is unchanged —
    # were it pooled in, `b`'s arm effect would inherit that cell's difficulty.
    widened = {"a": dict(shared["a"]), "b": {**shared["b"], "c3": 9.0}}
    assert two_way_effect_sds(widened) == (cell_sd, arm_sd, residual)

    # Below two arms or two shared cells there is nothing to separate — absence, never a 0.0
    # that would read as a perfectly precise instrument.
    assert two_way_effect_sds({"a": {"c1": 1.0, "c2": 2.0}}) is None
    assert two_way_effect_sds({"a": {"c1": 1.0}, "b": {"c1": 2.0}}) is None


def test_unstamped_ruler_reads_as_unknown_never_as_comparable() -> None:
    """The one reading that must not degrade quietly: a `None` here says "we cannot tell", and
    collapsing it to `len(ids) == 1` would answer YES for a roster where nothing is stamped."""

    def origin(ruler: str | None, dataset: str = "d") -> SubjectReading:
        return SubjectReading(
            key="campaign:c",
            kind="campaign",
            inside=[],
            campaign_id="c",
            cycle_id="cycle_0",
            candidate_id="origin",
            label="c",
            dataset_name=dataset,
            created_at="",
            comparable=None,
            comparable_note="",
            mask=None,
            scenario=None,
            winner_chain=None,
            config=None,
            arm_id="a",
            instrument_id=None,
            ability=AbilityReading(
                theta=0.0,
                se=None,
                ruler_id=ruler,
                ruler_n=1,
                ruler_span=None,
                round_span=None,
                calibration_model=None,
                caveat=ThetaCaveat.COLD_RULER,
            ),
            round=0,
            cycle_spend_usd=None,
            cycle_rounds_scored=0,
            spend_to_round={},
            values={"q1": 0.0},
            value=0.0,
            ci_lo=None,
            ci_hi=None,
            n_cells=1,
            unscorable_cells=[],
        )

    def verdict(*rulers: str | None) -> tuple[bool | None, str]:
        c = _comparability([origin(r) for r in rulers])
        return c.verdict, c.reason

    assert verdict("r1", "r1") == (True, "one_ruler")
    assert verdict("r1", "r2") == (False, "rulers_differ")
    # Unstamped, and mixed-with-stamped: neither is a yes.
    assert verdict(None, None) == (None, "ruler_unstamped")
    assert verdict("r1", None) == (None, "ruler_unstamped")

    # The PER-SUBJECT twin, and the same rule: a surface strikes one channel through on it, so an
    # unknown that degraded to `True` would render the odd row as a peer of the rest. The minority
    # is what gets marked — the majority defines the scale everything else is judged against.
    def marks(*rows: SubjectReading) -> list[bool | None]:
        return [r.comparable for r in _stamp_comparable(list(rows))]

    assert marks(origin("r1"), origin("r1"), origin("r2")) == [True, True, False]
    assert marks(origin("r1"), origin("r1"), origin(None)) == [True, True, None]
    assert marks(origin(None), origin(None)) == [None, None]
    # A different dataset is not a ruler question at all — nothing about the scale rescues it.
    assert marks(origin("r1"), origin("r1"), origin("r1", "other")) == [True, True, False]


def test_a_scenario_chain_stops_where_the_record_parts() -> None:
    """The mask's silent failure: walking PAST the round the two readings part. Every step after
    it is judged against a parent the run never carried — no candidate was measured against it, and
    L1 would have generated a different population from it — yet those steps render exactly like
    the prefix that is real, so a chart of "what would have happened" plots rounds that could not
    have. The round the walk stops on is also the round a fork applying this criterion is minted at
    (`resume_and_fork/resume.py`), so a chain that runs long misreports where that fork goes.

    Also pins the self-consistency floor: fed the criterion the run actually realized, the chain
    reproduces the recorded winners and never parts. One that cannot reproduce the record under its
    own formula is measuring the fold, not the formula.
    """
    from promptpotter.application.mask.record import MaskCandidate, MaskCycle, MaskRound
    from promptpotter.application.mask.scenario import scenario_spine

    def cand(cid: str, acc: float, latency: float, *, winner: bool = False) -> MaskCandidate:
        return MaskCandidate(
            candidate_id=cid,
            evaluators={"accuracy": acc, "mean_latency_s": latency},
            accuracy=acc,
            n_scored=4,
            is_winner=winner,
        )

    origin = cand("c0", 0.50, 1.0)
    # Round 1: `slow` is the most accurate and is crowned. Round 2 exists only so that "the walk
    # stopped" is a claim with something to stop BEFORE — with no round after the parting, a chain
    # that ran on and one that halted are the same list.
    slow = cand("slow", 0.80, 9.0, winner=True)
    fast = cand("fast", 0.60, 1.0)
    steady = cand("steady", 0.85, 8.0, winner=True)
    cycle = MaskCycle(
        cycle_id="cycle_0",
        rounds=[
            MaskRound(cycle_id="cycle_0", round=0, candidates=[origin]),
            MaskRound(
                cycle_id="cycle_0",
                round=1,
                candidates=[slow, fast],
                parent_evaluators=dict(origin.evaluators),
                parent_accuracy=origin.accuracy,
            ),
            MaskRound(
                cycle_id="cycle_0",
                round=2,
                candidates=[steady],
                parent_evaluators=dict(slow.evaluators),
                parent_accuracy=slow.accuracy,
            ),
        ],
    )

    # Realized criterion ⇒ the record, reproduced, the two readings agreeing on every round.
    realized = scenario_spine(cycle, "accuracy")
    assert [(s.round, s.candidate_id, s.recorded_id) for s in realized] == [
        (0, "c0", "c0"),
        (1, "slow", "slow"),
        (2, "steady", "steady"),
    ]

    # Flip to a criterion that punishes latency: round 1 elects `fast` (0.55) over the origin
    # (0.45), where `slow` scores 0.35 — so it parts from the record's `slow` right there, and the
    # walk ends. Round 2 is NOT on the chain: `steady` was measured against `slow`, and what it
    # would have scored against `fast` is not a thing the record knows.
    flipped = scenario_spine(cycle, "accuracy - 0.05 * mean_latency_s")
    assert [(s.round, s.candidate_id, s.recorded_id) for s in flipped] == [
        (0, "c0", "c0"),
        (1, "fast", "slow"),
    ]


def test_overlap_set_is_one_every_member_actually_answered() -> None:
    """The 1-to-1 bars rest on one property: every member of the parent line has answered every
    cell of the set its rate is read over. Break it and each bar still renders — over a smaller
    denominator, at a rate nothing measured, side by side as if comparable.

    Also pins the three rules that keep the set affordable and honest: a HELD round's parent
    re-score widens the parent's coverage; the choice prefers cells the new member already
    holds; and a member is a CONFIGURATION, not a lineage id — an L2/L3 transition re-mints the
    parent's OSP from the same prompt fields, which put one configuration on the chart twice,
    at the same rate by construction, under a label naming no candidate.
    """
    from promptpotter.domain.results import (
        ScoredCandidate,
        choose_overlap_set,
        measured_cells,
        parent_line,
    )

    def rows(*ids: int) -> list[dict[str, object]]:
        return [{"sample_id": i, "fitness": 1.0} for i in ids]

    def scored(cid: str, label: str) -> ScoredCandidate:
        return ScoredCandidate(
            candidate_id=cid, label=label, accuracy=0.5, composite_fitness=0.5, total=1
        )

    def rnd(n: int, cid: str, label: str, instruction: str, *ids: int) -> RoundResult:
        return RoundResult(
            round=n,
            label=label,
            accuracy=0.5,
            total=len(ids),
            improved=n > 0,
            # `instruction` IS the configuration here — two rounds sharing it are one member
            # however their lineage ids differ.
            prompt_fields={"instruction": instruction, "lineage": {"id": cid}},
            results=rows(*ids),
            candidates_scored=1,
            candidate_scores=[scored(cid, label)],
        )

    # C0 on 1..6; round 1 HELD (C0 re-scored on 7,8); round 2 crowned C2.1 on 5,6,7,9; round 3
    # HELD but an L2 transition re-minted the parent — same instruction, new id, no label.
    history = [
        rnd(0, "c0", "C0", "base", 1, 2, 3, 4, 5, 6),
        rnd(1, "c0", "C0", "base", 7, 8),
        rnd(2, "w2", "C2.1", "edited", 5, 6, 7, 9),
        rnd(3, "l2-remint", "C3.1", "edited", 5, 6),
    ]
    line = parent_line(history)
    # TWO members, not three: the L2 re-mint is the same configuration as C2.1, so it folds in
    # and keeps C2.1's label rather than appearing beside it as an unnamed twin.
    assert [(s.candidate_id, s.label) for s in line] == [("c0", "C0"), ("w2", "C2.1")]
    # The held round WIDENED the parent rather than replacing it — without that, 7 and 8 are
    # lost and cell 7 could never join the set below.
    assert measured_cells(line[0].rows) == {1, 2, 3, 4, 5, 6, 7, 8}

    c0, w2 = measured_cells(line[0].rows), measured_cells(line[1].rows)
    chosen = choose_overlap_set(c0, already_measured=w2, previous=(), size=4)
    # 9 is w2's but C0 never answered it, so it cannot be a shared basis at any price.
    assert 9 not in chosen
    # THE invariant: C0 has answered every chosen cell, so both bars are read on the same exam.
    assert set(chosen) <= c0
    # Free-first: the three cells w2 already holds are taken before any that must be bought.
    assert {5, 6, 7} <= set(chosen)
    assert len([s for s in chosen if s not in w2]) == 1

    # Stickiness is the tiebreak, never an override — a cheaper cell still wins the slot.
    assert choose_overlap_set(c0, already_measured=w2, previous=[1, 2, 3], size=3) == [5, 6, 7]


def test_a_collapse_cut_is_never_reported_as_an_epsilon_cut() -> None:
    """`elimination_stopped` covers two gates and only one measured anything: a collapse cut
    returns before the posterior, so `p_best`/`epsilon`/`n_priors`/`leader_id` are placeholders.
    Read through the ε fields it renders `0.0% < 15% vs ? (of 0 priors)` — four invented numbers —
    and tells the generator the strongest verdict the loop has was "NOT a verdict", leaving the
    collapsed idea free to be re-proposed.

    Silent harm: nothing errors, every field renders, and the number diagnosed from was never
    measured."""
    from promptpotter.application.optimization.dispatch.injections.panels import _candidate_fate
    from promptpotter.application.optimization.pobb.checks import PoBBCheck, PoBBConfig
    from promptpotter.domain.results import EliminationGate

    rows = [
        {
            "sample_id": i,
            "hit": i % 2 == 0,
            "fitness": 1.0 if i % 2 == 0 else 0.0,
            "predicted": "Uncertain",
            "ground_truth": "TRUE" if i % 2 == 0 else "FALSE",
        }
        for i in range(6)
    ]
    signal = PoBBCheck(PoBBConfig(), n_samples=28, ruler=None).check(rows, 0, 1)
    assert signal is not None, "a constant answerer must be cut at n_min"
    cr = signal.check_result
    assert cr["gate"] == EliminationGate.COLLAPSED
    # The ε fields were never computed, so nothing downstream may present one as measured.
    assert not {"p_best", "epsilon", "n_priors"} & cr.keys()

    def cand(cid: str, **ctx: object):
        return scored_candidate(
            cid,
            total=6,
            elimination_stopped=True,
            scored_samples=6,
            expected_samples=28,
            elimination_context={"queries_scored": 6, "total_queries": 28, **ctx},
        )

    collapsed = _candidate_fate(cand("collapsed", gate=EliminationGate.COLLAPSED), "sample")
    epsilon_cut = _candidate_fate(
        cand("eps", p_best=0.03, epsilon=0.15, gate=EliminationGate.EPSILON), "sample"
    )

    # What the GENERATOR is handed must invert between the two, and quote no ε number it lacks.
    assert "NOT a verdict" in epsilon_cut
    assert "NOT a verdict" not in collapsed and "VERDICT" in collapsed
    assert "ε" not in collapsed and "P(best)" not in collapsed


def test_only_an_epsilon_cut_banks_an_idea_as_measured_and_lost() -> None:
    """`lost_ideas` feeds the cross-round repeat gate, whose rejection is a synthetic-0 that never
    reaches an LLM. Only ε weighed the arm against its priors — LOCK_IN is the opposite verdict,
    COLLAPSED is the ABSENCE of a measurement, and a degradation cut names no gate because the node
    broke rather than the idea losing.

    Silent harm: banking any of the three blacklists an idea no round ever judged, and every later
    re-proposal is rejected as `repeat_variant` for the rest of the cycle with nothing raised."""
    from promptpotter.application.optimization.validators.l1_invariants import lost_ideas
    from promptpotter.domain.results import EliminationGate

    def banked(ctx: dict | None) -> bool:
        rnd = lost_round(1, "instruction", "count the premises first", elimination_context=ctx)
        return bool(lost_ideas([rnd]))

    assert banked({"gate": EliminationGate.EPSILON}), "an ε cut IS the measurement, and it lost"
    assert banked(None), "a plain accuracy loss is still a measured loss"
    assert not banked({"gate": EliminationGate.COLLAPSED})
    assert not banked({"gate": EliminationGate.LOCK_IN})
    assert not banked({}), "a degradation cut carries no gate — the node broke, not the idea"


def test_an_arm_that_beats_a_far_off_ruler_is_fit_not_oscillated_past() -> None:
    # `screen-taste-v0` cycle_df3de1e40b64: a graded objective puts every cell's δ near +5.55, and
    # the fit seeds at θ=0 — five logits away, where p saturates and the observed information is
    # the prior term alone. Undamped, `grad / info` is then a jump of ~14 logits into the OPPOSITE
    # saturation, and the iteration limit-cycles (14.06 → -19.46 → 16.09 → -19.68 → …) until
    # max_iter stops it wherever it stands. The three best arms of that run were each recorded
    # near θ = -25 with an SE in the hundreds, so every round refused to crown one.
    #
    # It is the arms sitting FURTHEST from the seed that overshoot, which on a hard ruler are the
    # arms that scored HIGHEST — so the failure is not noise, it reads improvement as collapse.
    delta = dict.fromkeys(range(20), 5.55)
    parent = [Observation("parent", sid, 0.20) for sid in delta]
    better = [Observation("better", sid, 0.45) for sid in delta]

    fit = fit_theta_given_delta(parent + better, delta, sigma_theta=1.34)
    parent_theta, parent_se = fit["parent"]
    better_theta, better_se = fit["better"]

    # Both land near the ruler they were measured on, not tens of logits away from it.
    assert 2.0 < parent_theta < 5.55, parent_theta
    assert parent_theta < better_theta < 7.0, better_theta
    # An SE in the hundreds is the tell that the iteration never converged at all.
    assert parent_se < 1.0 and better_se < 1.0, (parent_se, better_se)


def test_an_arm_in_the_last_n_min_cells_is_finished_not_discarded() -> None:
    """`n_min` at BOTH ends: an arm may not be judged on fewer than that many cells, and may not
    be discarded with fewer than that many left. Cutting in the tail saves almost nothing and
    costs the comparison outright — `matched_parent_stats` needs EVERY cell the parent measured,
    so an arm stopped one cell short is unrankable against the parent for the rest of the round.

    Live on `justlogic-d234__8ada8e` r1: an arm was cut at q27 of 28 having paid 96% of its cost,
    and the round then reported one readable arm and resolved nothing.

    Silent harm: the arm's rows are still banked, so the round LOOKS measured — it simply has
    nothing it can rank, and says so only in the `resolved nothing` line."""
    cfg = PoBBConfig(n_min=6, epsilon=0.30)

    def cut_at(n: int) -> object | None:
        check = PoBBCheck(cfg, n_samples=28, ruler=None)
        check.register_completed(measurements([1.0] * 28), candidate_id="winner", sp=_DUMMY_SP)
        check.set_current("arm")
        return check.check(measurements([0.0] * n), candidate_idx=1, n_total_candidates=2)

    # Hopeless arms in the BODY of the panel still die — the guard is a tail rule, not a reprieve.
    assert cut_at(9) is not None
    assert cut_at(22) is not None, "the last cut-eligible depth is n_samples - n_min"
    # …and in the tail they are finished instead, however far behind they are.
    assert cut_at(23) is None
    assert cut_at(27) is None, "one cell short of the panel is the case this exists for"


def test_an_archive_row_is_graded_by_the_reading_scorer_not_its_stamp(monkeypatch) -> None:
    """The δ ruler grades archive rows with the CAMPAIGN's scorer, never a stamp on the row.

    ``objective`` is written by ``rescore_results`` under whatever formula was active when the row
    was banked, so reading it back pools one scale out of several — and a row banked before the
    field existed carries none at all. Read with ``.get("objective", 0.0)`` that absence is not a
    hole, it is a WRONG ANSWER: measured on `justlogic-d234`, 49,221 of 49,501 observations graded
    0.0, the fit collapsed δ to sd 0.243 across 600 cells, θ became logit-accuracy plus a constant,
    and the acquisition — which ranks on δ ≈ θ — bought progressively EASIER panels while the
    headline climbed 50.0% → 85.7% and `overlap` stayed flat.

    Silent harm: every number renders, the ruler reports 600 cells and a warm id, and the campaign
    reads as a clean climb.
    """
    import types

    from promptpotter.application.intelligence.hard_sample_archive import (
        build_archive_observations,
    )
    from promptpotter.domain.scoring import CellScorer
    from promptpotter.infrastructure.store import archive_views

    # Two cells the arm got RIGHT, banked before ``objective`` existed — and one stamped by a
    # formula that is not the reading campaign's, which must lose to the scorer just the same.
    rows = [
        {"sample_id": 1, "fitness": 1.0},
        {"sample_id": 2, "fitness": 1.0, "objective": 0.0},
    ]
    monkeypatch.setattr(
        archive_views,
        "list_runs",
        lambda *_a, **_k: [
            {"run_id": "r1", "prompt_fields_id": "cand-a", "provenance": {"grade": "A"}}
        ],
    )
    monkeypatch.setattr(archive_views, "run_signatures", lambda *_a, **_k: {"r1": (1, 1)})
    monkeypatch.setattr(archive_views, "load_run", lambda *_a, **_k: {"measurements": rows})

    stores = types.SimpleNamespace(
        archive=types.SimpleNamespace(base_dir="/nowhere-unique-to-this-test")
    )
    obs = build_archive_observations(
        stores,
        dataset_name="d",
        scorer=CellScorer(fitness=lambda r: r["fitness"], objective=lambda r: r["fitness"]),
        scorer_id="under-test",
    )

    assert [o.response for o in obs] == [1.0, 1.0], (
        "an archive row was graded from its stamp, not from the reading campaign's scorer"
    )


def test_ruler_learning_cannot_take_the_whole_round_panel() -> None:
    """The panel is bought to SEPARATE THE ARMS; refining δ gets a bounded reservation, not a vote.

    `pick_value` summed `decision_information_gain + delta_learning_gain`, and the second rises with
    δ's SE while the first is capped by an entropy — so wherever a difficulty exists in both a
    well-measured and a barely-measured cell, the sum takes the barely-measured one every time and
    the panel becomes a ruler-refinement queue. Live on `justlogic-d234` that bought 28 cells the
    archive passes 1.6% of the time; once those were measured and their SE fell, the next round
    bought cells it passes ~100% of. Both scored every arm identically and separated nothing.

    Dropping the term is not the fix either: a pure-decision panel converges on one difficulty
    (0.93 logits on the live ruler, under `BAND_COLLAPSE_LOGITS`), which is the state where θ is
    logit-accuracy plus a constant and ranking on it ranks on accuracy.

    Silent harm: every arm scores ~0 or ~1, `improved` reads false or trivially true, and the
    headline moves with panel composition while nothing reports that the panel moved.
    """
    from promptpotter.application.intelligence.exploration import (
        Observation,
        select_round_subset,
    )
    from promptpotter.domain.sample import Sample

    # A bank shaped like a real one: a DENSE mid-difficulty cluster the arms have hammered, in
    # two measurement states, plus a SPARSE tail nobody has answered often. That shape is what
    # separates the three rankings — an SE tied smoothly to |δ| never exercises it, because there
    # the coarse cells sit where the decision term is already near zero and it wins on its own.
    delta: dict[int, float] = {}
    delta_se: dict[int, float] = {}
    n = 0
    for i in range(100):
        d = -0.6 + 1.2 * i / 99
        delta[n], delta_se[n] = d, 0.20
        n += 1
        delta[n], delta_se[n] = d, 0.90
        n += 1
    for i in range(50):
        delta[n], delta_se[n] = -4.33 + 8.66 * i / 49, 1.50
        n += 1
    ruler = _ruler(delta, se=delta_se)
    bank = [Sample(id=str(sid), query="q", ground_truth="g") for sid in sorted(delta)]
    # One arm in the race, sitting mid-scale: it passes the easy half and misses the hard half.
    own = [Observation("leader", sid, 1.0 if delta[sid] < 0 else 0.0) for sid in delta]
    budget = 28

    panel = [
        int(s.id)
        for s in select_round_subset(bank, own, budget, ruler=ruler, leader_ids={"leader"})
    ]

    assert len(set(panel)) == budget, "the panel duplicated or dropped cells"

    # A MINORITY, not the reservation's exact size: a coarse cell sitting near the leader can
    # legitimately win a decision slot on its own merits. What may never happen is the panel
    # being made of them, which is what the summed acquisition did — 28 of 28.
    coarse = {sid for sid, se in delta_se.items() if se > 1.0}
    from_coarse = len(coarse & set(panel))
    assert from_coarse <= budget // 3, (
        f"{from_coarse} of {budget} panel cells are the ones δ is least sure of — "
        "ruler learning outvoted the decision the panel exists to make"
    )

    span = max(delta[s] for s in panel) - min(delta[s] for s in panel)
    assert span > 1.0, f"panel spans {span:.2f} logits — a collapsed band is not a reading"


def test_the_panel_is_calibrated_to_the_arm_in_the_race_not_the_archive_best() -> None:
    """The round buys cells that separate THIS cycle's arms; the archive only lends the δ scale.

    ``select_round_subset`` is handed ``[*archive_observations, *cycle.rounds]`` because a θ fit
    wants every arm it can get, and it took ``max`` over all of them — the best searchpoint ever
    run on the dataset, which is not in this race. Live on `justlogic-d234` that was θ 1.79 against
    a parent at −0.01, so the panel came out two logits too hard: 28 cells the archive passes 1.6%
    of the time, every arm scored ~0, and the round resolved nothing.

    It was harmless while a flat ruler made every θ equal, which is why it surfaced only once δ
    was real — repairing an instrument is what lets the next defect downstream of it bite.

    Silent harm: the panel is legitimately hard, every number renders, and `improved: false` reads
    as "no candidate was better" rather than "nobody could have been measured".
    """
    from promptpotter.application.intelligence.exploration import (
        Observation,
        select_round_subset,
    )
    from promptpotter.domain.sample import Sample

    delta = {i: -4.33 + 8.66 * i / 99 for i in range(100)}
    # Uniform SE, so ruler-learning cannot confound the read.
    ruler = _ruler(delta, se=0.50)
    bank = [Sample(id=str(sid), query="q", ground_truth="g") for sid in sorted(delta)]
    # The arm under test sits mid-scale; a far stronger arm rides in from the archive.
    leader = [Observation("leader", sid, 1.0 if delta[sid] < 0 else 0.0) for sid in delta]
    archive_star = [Observation("star", sid, 1.0 if delta[sid] < 2.5 else 0.0) for sid in delta]

    panel = [
        int(s.id)
        for s in select_round_subset(
            bank, [*archive_star, *leader], 28, ruler=ruler, leader_ids={"leader"}
        )
    ]
    mean_delta = sum(delta[s] for s in panel) / len(panel)

    # Near the arm in the race (θ ≈ 0), not near the archive's best (θ ≈ +2.6).
    assert abs(mean_delta) < 1.0, (
        f"panel centred at δ {mean_delta:+.2f} — calibrated to an arm that is not in this round"
    )
