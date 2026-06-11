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

from types import SimpleNamespace

import numpy as np
import pytest

from promptpotter.application.intelligence.exploration import (
    Observation,
    fit_rasch,
    select_round_subset,
)
from promptpotter.application.mask import (
    MaskCandidate,
    MaskCycle,
    MaskRecord,
    MaskRound,
    find_divergences,
    make_abort_verdict,
    make_scoring_verdict,
)
from promptpotter.application.optimization.pobb.elimination import terminal_ranking
from promptpotter.application.scoring.evaluators import all_evaluators, materialize_round_values
from promptpotter.application.scoring.formula import (
    compile_scorer,
    extract_display_answer,
    extract_item_label,
)
from promptpotter.application.scoring.formula.matchers import (
    _aime_match,
    _exact_match,
    _extract_gsm8k_number,
    _gsm8k_match,
)
from promptpotter.application.scoring.metrics import (
    compute_composite_fitness,
    matched_origin_stats,
)
from promptpotter.domain.pipeline_schema import (
    NodeType,
    ObservationMapping,
    PipelineNode,
    PipelineSchema,
)
from promptpotter.domain.sample import Sample

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
        # _extract_gsm8k_number: comma-stripped / #### preferred / none.
        (_extract_gsm8k_number, ("#### 1,234",), 1234.0),
        (_extract_gsm8k_number, ("I calculated 99 but #### 42",), 42.0),
        (_extract_gsm8k_number, ("no numbers",), None),
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
                        pipeline_key="candidate_ranking", obs_key="candidate_ranking"
                    )
                ],
            ),
            PipelineNode(
                name="ranker",
                node_type=NodeType.RANKER,
                observation_mappings=[
                    ObservationMapping(pipeline_key="final_ranking", obs_key="final_ranking")
                ],
            ),
        ],
    )


def test_registry_scopes_are_valid():
    """Every registered evaluator declares a known scope + data type."""
    names = {ev.name for ev in all_evaluators()}
    assert {"accuracy", "error_rate", "latency_norm", "source_recall"}.issubset(names)
    for ev in all_evaluators():
        assert ev.scope in ("per_sample", "per_round")
        assert ev.data_type in ("NUMERIC", "BOOLEAN")


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
    schema = _single_node_schema()
    results = [_eval_result(score=1.0, total_time=100), _eval_result(score=0.0, total_time=200)]
    scored = compute_composite_fitness(results, schema)
    # Accuracy=0.5, latency_norm=0.985, recall falls back to accuracy.
    # With no opt_sp all four self-heal rates are 0 → (1 - rate) = 1 each.
    # prompt_compactness defaults to 1.0 when no opt_sp passed (vacuous).
    expected = (
        0.55 * 0.5
        + 0.10 * 1.0  # 1 - validation_failure_rate
        + 0.10 * 1.0  # 1 - runtime_failure_rate
        + 0.05 * 1.0  # 1 - l2_guard_breach_rate
        + 0.05 * 1.0  # 1 - l3_guard_breach_rate
        + 0.05 * 0.985
        + 0.05 * 0.5
        + 0.05 * 1.0
    )
    assert scored["composite_fitness"] == pytest.approx(expected, abs=1e-4)


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
    # Origin had ZERO hits on samples 10-17. The faked extrapolation
    # (origin.accuracy * 8 = 0.5 * 8 = 4) would have invented 4 hits.
    assert matched["hits"] == 0
    assert matched["total"] == 8
    assert matched["accuracy"] == 0.0
    # Degenerate case: candidate measured all 20 → matches full-set stats.
    full = matched_origin_stats(origin_results, origin_results, schema)
    assert full["hits"] == 10
    assert full["total"] == 20
    assert full["accuracy"] == pytest.approx(0.5)


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
    fake_opt_sp = SimpleNamespace(
        memory=SimpleNamespace(
            wounds=SimpleNamespace(validation_failures=[object()], runtime_failures=[])
        )
    )
    scored = compute_composite_fitness(
        [_eval_result(score=1.0)], _single_node_schema(), opt_sp=fake_opt_sp
    )
    assert scored["composite_fitness"] == 0.0


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
    # Vacuous (no opt_sp) returns 1.0 so the term never injects a phantom penalty.
    assert compute_prompt_compactness(opt_sp=None) == 1.0


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
                obs.append(Observation(candidate_id=cid, sample_id=sid, hit=bool(rng.random() < p)))
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
    from promptpotter.application.optimization.pobb.elimination import classify_result

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

from promptpotter.application.optimization.l1.score.winner import is_leader_eligible  # noqa: E402
from promptpotter.application.optimization.pobb.elimination import (  # noqa: E402
    PoBBCheck,
    PoBBConfig,
)
from promptpotter.application.optimization.validators.l1_strict import (  # noqa: E402
    detect_invariants,
)
from promptpotter.application.optimization.validators.l2_output import (  # noqa: E402
    L2_DUPLICATE_INSERT,
    L2_TASK_CONTEXT_PARAPHRASE_REPEAT,
    L2_TASK_CONTEXT_VERBATIM_REPEAT,
    run_l2_output_validators,
)
from promptpotter.application.optimization.validators.l3_output import (  # noqa: E402
    L3_PLAN_LENGTH_FLOOR,
    L3_PLAN_VERBATIM_REPEAT,
    run_l3_output_validators,
)
from promptpotter.domain.escalation_signals import EscalationTarget  # noqa: E402
from promptpotter.domain.l1_layout import L1Layout, validate_l1_layout  # noqa: E402
from promptpotter.domain.opt_search_point import OptSearchPoint  # noqa: E402
from promptpotter.domain.results import CandidateProposal, ScoredCandidate  # noqa: E402
from promptpotter.domain.search_point import TaskDecomposition  # noqa: E402
from promptpotter.shared.statistics import posterior_best_probabilities  # noqa: E402

# ===========================================================================
# 6. L1 invariant detectors
# ===========================================================================


def _parent() -> OptSearchPoint:
    return OptSearchPoint(persona="Expert", instruction="Rank items.")


def _child(parent: OptSearchPoint, **changes) -> CandidateProposal:
    return CandidateProposal(osp=parent.mutate(**changes))


def test_no_op_clone_attaches_validation_failure():
    parent = _parent()
    proposals = [_child(parent), _child(parent, persona="Expert ranker")]

    stats = detect_invariants(proposals, parent)

    no_op_reasons = [vf.reason for vf in proposals[0].osp.memory.wounds.validation_failures]
    assert "no_op_variant" in no_op_reasons
    assert proposals[1].osp.memory.wounds.validation_failures == []
    assert stats.l1_n_no_op == 1
    assert stats.l1_n_duplicate == 0
    assert stats.l1_yield == 0.5


def test_duplicate_signature_attaches_validation_failure():
    parent = _parent()
    proposals = [
        _child(parent, persona="Specialist"),
        _child(parent, persona="Specialist"),  # same signature → duplicate
        _child(parent, instruction="Rank with care."),
    ]

    stats = detect_invariants(proposals, parent)

    assert proposals[0].osp.memory.wounds.validation_failures == []
    dup_reasons = [vf.reason for vf in proposals[1].osp.memory.wounds.validation_failures]
    assert "duplicate_variant" in dup_reasons
    assert proposals[2].osp.memory.wounds.validation_failures == []
    assert stats.l1_n_duplicate == 1
    assert stats.l1_n_no_op == 0
    assert stats.l1_yield == 2 / 3


def test_parse_population_attaches_forbidden_axis_failure_to_osp():
    from promptpotter.application.optimization.l1.population import parse_population
    from promptpotter.domain.pipeline_schema import NodePromptInfo

    schema = PipelineSchema(
        name="test",
        available_models=["openai/gpt-oss-120b"],
        nodes=[PipelineNode(name="llm_only", prompt_info=NodePromptInfo())],
    )
    parent = _parent()
    proposal = CandidateProposal(
        osp=parent.mutate(persona="Strict"),
        pipeline_params_override={"llm_only": {"model": "anything-at-all"}},
    )

    osp_list, _ = parse_population([proposal], pipeline_params=None, schema=schema)

    failures = osp_list[0].memory.wounds.validation_failures
    assert len(failures) == 1
    assert failures[0].reason == "forbidden_axis"
    assert failures[0].axis == "llm_only.model"


# ===========================================================================
# 7. L2 output validators (fire + quiet)
# ===========================================================================


def _osp(**kwargs) -> OptSearchPoint:
    from promptpotter.domain.opt_search_point import L2L3Memory

    memory_fields = {
        "wounds",
        "l1_layout",
        "l1_overrides",
        "l1_supplemental_rules",
        "l1_situational_examples",
        "task_context",
    }
    if "memory" not in kwargs and (
        mem_kwargs := {k: kwargs.pop(k) for k in list(kwargs) if k in memory_fields}
    ):
        kwargs["memory"] = L2L3Memory(**mem_kwargs)
    return OptSearchPoint(persona="Expert", instruction="Rank items.", **kwargs)


def test_task_context_verbatim_repeat_fires_when_proposed_merge_is_no_op():
    out = L2_TASK_CONTEXT_VERBATIM_REPEAT.run(
        {"task_context_proposed": {"domain": "biotech"}, "task_context_applied": None},
        opt_sp=_osp(),
    )
    assert out is not None
    assert out.passed is False
    assert out.score == 0.0
    assert out.nurse_target == "l3"
    assert out.evidence["proposed_keys"] == ["domain"]


def test_run_l2_output_validators_aggregates_task_context_repeat():
    outcomes = run_l2_output_validators(
        {"task_context_proposed": {"domain": "x"}, "task_context_applied": None},
        _osp(),
    )
    ids = {o.validator_id for o in outcomes}
    assert "l2_task_context_verbatim_repeat" in ids


def test_duplicate_insert_fires_when_proposed_lines_already_in_prior_framing():
    osp = _osp(
        task_context=TaskDecomposition(
            domain="math",
            key_challenges=(
                "Step 1: read the problem\nStep 2: compute carefully\nStep 3: verify the result"
            ),
        )
    )
    proposed = {
        "key_challenges": (
            "Step 1: read the problem\n"
            "Step 2: compute carefully\n"
            "Step 3: verify the result\n"
            "Step 4: emit the answer"  # genuinely new — merge succeeds
        ),
    }
    merged = osp.memory.task_context.merge(proposed)
    out = L2_DUPLICATE_INSERT.run(
        {"task_context_proposed": proposed, "task_context_applied": merged},
        opt_sp=osp,
    )
    assert out is not None
    assert out.passed is False
    assert out.score == 0.0
    assert out.nurse_target == "l3"
    assert out.evidence["duplicate_lines"] == 3
    assert out.evidence["fields"] == ["key_challenges"]


def test_duplicate_insert_quiet_below_threshold():
    """Below the 3-line floor ⇒ no fire."""
    osp = _osp(task_context=TaskDecomposition(key_challenges="line A\nline B"))
    out = L2_DUPLICATE_INSERT.run(
        {
            "task_context_proposed": {"key_challenges": "line A\nline B\nline C"},
            "task_context_applied": osp.memory.task_context.merge(
                {"key_challenges": "line A\nline B\nline C"}
            ),
        },
        opt_sp=osp,
    )
    assert out is None


def test_paraphrase_repeat_fires_on_word_set_overlap_above_threshold():
    """Paraphrase sharing ≥50% of words with prior framing ⇒ fire."""
    prior = (
        "Problem misreading (2/N): targeting L1 axis 'instruction' to add "
        "a mandatory problem-restate and constraint-check step before solving."
    )
    paraphrase = (
        "Problem misreading (2/N): targeting L1 axis 'instruction' to add "
        "an explicit problem restatement followed by a constraint extraction "
        "and self-check step before any solving begins."
    )
    osp = _osp(task_context=TaskDecomposition(key_challenges=prior))
    out = L2_TASK_CONTEXT_PARAPHRASE_REPEAT.run(
        {
            "task_context_proposed": {"key_challenges": paraphrase},
            "task_context_applied": osp.memory.task_context.merge({"key_challenges": paraphrase}),
        },
        opt_sp=osp,
    )
    assert out is not None
    assert out.passed is False
    assert out.nurse_target == "l3"
    assert out.evidence["field"] == "key_challenges"
    assert out.evidence["jaccard"] >= 0.5


def test_paraphrase_repeat_quiet_on_genuine_refinement():
    prior = "Problem misreading on geometry: failing coordinate setup."
    refinement = (
        "Combinatorial enumeration failing: candidates skip casework on "
        "the 27-cell grid; needs explicit decomposition by row pattern."
    )
    osp = _osp(task_context=TaskDecomposition(key_challenges=prior))
    out = L2_TASK_CONTEXT_PARAPHRASE_REPEAT.run(
        {
            "task_context_proposed": {"key_challenges": refinement},
            "task_context_applied": osp.memory.task_context.merge({"key_challenges": refinement}),
        },
        opt_sp=osp,
    )
    assert out is None


def test_l1_config_not_in_runtime_failures_fires_on_reproposal():
    """L1 proposing a (param, value) matching a known runtime failure ⇒ ValidationFailure."""
    from promptpotter.application.optimization.validators.l1_strict import (
        L1_CONFIG_NOT_IN_RUNTIME_FAILURES,
    )
    from promptpotter.domain.escalation_signals import RuntimeFailure

    osp = _osp()
    osp.memory.wounds.runtime_failures = [
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
    out = L1_CONFIG_NOT_IN_RUNTIME_FAILURES.run({"llm_only": {"max_tokens": 1800}}, opt_sp=osp)
    assert out is not None
    assert out.passed is False
    assert out.nurse_target == "l2"
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

    osp = _osp()
    osp.memory.wounds.runtime_failures = [
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
    out = L1_CONFIG_NOT_IN_RUNTIME_FAILURES.run({"llm_only": {"max_tokens": 2400}}, opt_sp=osp)
    assert out is None


# ===========================================================================
# 8. L3 output validators
# ===========================================================================


def test_plan_length_floor_fires_on_short_plan():
    out = L3_PLAN_LENGTH_FLOOR.run({"plan": "do better"}, opt_sp=_osp())
    assert out is not None
    assert out.passed is False
    assert out.nurse_target == "l3"


def test_plan_verbatim_repeat_fires():
    osp = _osp(plan="Maintain current strategy and explore persona axis carefully.")
    out = L3_PLAN_VERBATIM_REPEAT.run(
        {"plan": "Maintain current strategy and explore persona axis carefully."}, opt_sp=osp
    )
    assert out is not None
    assert out.score == 0.0


def test_run_l3_output_validators_aggregates():
    osp = _osp(plan="short")
    outcomes = run_l3_output_validators({"plan": "short"}, osp)
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
    result = validate_l1_layout(layout)
    assert result.is_valid is False
    assert expected_validator_id in {o.validator_id for o in result.outcomes}


# ===========================================================================
# 10. PoBB posterior gate + leader eligibility
# ===========================================================================


def test_posterior_best_probabilities_sums_to_one():
    rng = np.random.default_rng(42)
    histories = {
        "a": [0.6, 0.7, 0.8, 0.6, 0.7],
        "b": [0.5, 0.5, 0.5, 0.5, 0.5],
        "c": [0.4, 0.3, 0.5, 0.3, 0.4],
    }
    probs = posterior_best_probabilities(histories, rng=rng)
    assert set(probs) == {"a", "b", "c"}
    assert abs(sum(probs.values()) - 1.0) < 1e-9


def test_high_signal_collapses_to_clear_winner():
    rng = np.random.default_rng(0)
    histories = {"winner": [1.0] * 8, "loser": [0.0] * 8}
    probs = posterior_best_probabilities(histories, n_samples=2000, rng=rng)
    assert probs["winner"] >= 0.99
    assert probs["loser"] <= 0.01


def _measurements(scores: list[float], sample_ids: list[int] | None = None) -> list[dict]:
    """Minimal QueryMeasurement-shaped dicts for PoBB paired tests."""
    ids = sample_ids if sample_ids is not None else list(range(len(scores)))
    return [{"sample_id": sid, "fitness": float(s)} for sid, s in zip(ids, scores, strict=True)]


_DUMMY_SP = SimpleNamespace()


def test_pobb_check_gates_elimination_on_posterior():
    """PoBB.check() fires when the paired-difference posterior clears the ε gate."""
    check_sep = PoBBCheck(PoBBConfig(n_min=4, epsilon=0.05), n_samples=20)
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


def test_pobb_dominance_aborts_when_catch_up_impossible():
    """Arithmetic abort: cand_max_hits < seed_hits ⇒ eliminate before the posterior runs."""
    check = PoBBCheck(PoBBConfig(n_min=4, epsilon=0.05), n_samples=20)
    seed_scores = [1.0] * 10 + [0.0] * 10
    check.register_completed(
        _measurements(seed_scores, sample_ids=list(range(20))), candidate_id="origin", sp=_DUMMY_SP
    )
    check.set_sample_universe(list(range(20)))
    check.set_current("doomed")
    sig = check.check(
        _measurements([0.0] * 11, sample_ids=list(range(11))),
        candidate_idx=1,
        n_total_candidates=3,
    )
    assert sig is not None
    assert sig.check_name == "elimination"
    cr = sig.check_result
    assert cr["leader_id"] == "origin"
    dom = cr["dominance"]
    assert dom["cand_hits"] == 0
    assert dom["cand_max_hits"] == 9
    assert dom["seed_total_hits"] == 10
    assert dom["budget"] == 20


def test_pobb_locks_in_dominant_leader():
    """Current candidate dominating prior past lock_in_n_min fires LEADER_LOCKED."""
    check = PoBBCheck(
        PoBBConfig(n_min=4, epsilon=0.05, lock_in=0.95, lock_in_n_min=8), n_samples=20
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
    check_no_backfill = PoBBCheck(PoBBConfig(n_min=4, epsilon=0.05), n_samples=20)
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
        truth = {9: 0.0, 12: 0.0, 13: 1.0, 14: 0.0, 8: 0.0}
        return [{"sample_id": s.id, "fitness": truth[s.id]} for s in samples]

    check_paired = PoBBCheck(
        PoBBConfig(n_min=4, epsilon=0.05), n_samples=20, backfill_fn=_stub_backfill
    )
    check_paired.register_completed(
        _measurements([1.0] * 8, sample_ids=leader_samples),
        candidate_id="R1_lucky_winner",
        sp=_DUMMY_SP,
    )
    for sid in candidate_samples:
        await check_paired.backfill_for_sample(SimpleNamespace(id=sid))

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
    )


def test_leader_eligibility_excludes_fatal_degradation_and_keeps_pobb_eliminated():
    """Winner-selection: fatal degradation disqualifies; PoBB elimination does not."""
    fatal = _cs(
        candidate_id="C1.3",
        accuracy=0.8333,
        escalation_aborted=True,
        elimination_stopped=True,
        degradation_context={"fatal": True, "dominant_warning": "llm_only:empty_response"},
    )
    pobb_eliminated = _cs(
        candidate_id="C1.2",
        accuracy=0.40,
        escalation_aborted=True,
        elimination_stopped=True,
        degradation_context={},
    )
    clean_loser = _cs(candidate_id="C1.4", accuracy=0.45)

    assert not is_leader_eligible(fatal)
    assert is_leader_eligible(pobb_eliminated)
    assert is_leader_eligible(clean_loser)


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
        opt_search_point={"task_context": {"key_challenges": "the prior framing"}},
    )

    conformant = run_all_l2_checks(
        _round(
            {
                "rationale": "Axis instruction stalled 3 rounds; sample #14 regressed — refocus.",
                "axis_targeted": "instruction",
                "task_context": {"key_challenges": "a genuinely new framing of negation depth"},
            }
        ),
        ctx,
    )
    assert len(conformant) == 4
    assert all(c.passed for c in conformant)

    stub = run_all_l2_checks(
        _round({"rationale": "ok", "axis_targeted": "", "task_context": {}}),
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
        hits=sum(1 for r in results if r.get("hit")),
        total=len(results),
        improved=False,
        prompt_fields={},
        results=results,
        candidates_scored=1,
    )


def _hit(query: str, gt: str) -> dict:
    return {"query": query, "ground_truth": gt, "hit": True, "predicted": gt}


def _miss(query: str, gt: str) -> dict:
    return {"query": query, "ground_truth": gt, "hit": False, "predicted": "?"}


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


def test_diag_probe_outcome_gated_by_probe_just_completed_flag():
    results = [_hit("q1", "a"), _hit("q2", "b"), _miss("q3", "c"), _miss("q4", "d")]
    rr = _round_result(0, 0.5, results)

    diag = compute_round_diagnostics(rr, [rr], pipeline_schema=None)
    assert diag.probe_outcome is None

    diag_probe = compute_round_diagnostics(
        rr,
        [rr],
        pipeline_schema=None,
        probe_just_completed=True,
        axis_tested="persona",
        prior_full_accuracy=0.7,
    )
    assert diag_probe.probe_outcome is not None
    assert diag_probe.probe_outcome.axis_tested == "persona"
    assert diag_probe.probe_outcome.target_subset_size == 4
    assert diag_probe.probe_outcome.hit_rate == 0.5
    assert diag_probe.probe_outcome.delta_vs_full == pytest.approx(-0.2)


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


def test_pick_score_artifact_ranks_contested_above_settled() -> None:
    """``pick_score.per_sample`` ranks contestable samples above settled-all-HIT ones."""
    from promptpotter.application.intelligence.hard_sample_sorter import (
        ARTIFACT_SCHEMA_VERSION,
        build_hard_samples_artifact_from_observations,
    )

    obs = [
        # Sample 1: settled-easy (all HIT); sample 2: split (contestable).
        *[Observation(candidate_id=c, sample_id=1, hit=True) for c in "abcd"],
        Observation(candidate_id="a", sample_id=2, hit=True),
        Observation(candidate_id="b", sample_id=2, hit=False),
        Observation(candidate_id="c", sample_id=2, hit=True),
        Observation(candidate_id="d", sample_id=2, hit=False),
    ]
    artifact = build_hard_samples_artifact_from_observations(obs)
    assert artifact["schema_version"] == ARTIFACT_SCHEMA_VERSION == 3

    per_sample = artifact["pick_score"]["per_sample"]
    assert per_sample["2"] > per_sample["1"]
    assert artifact["pick_score"]["sample_order"][-1] == 1
