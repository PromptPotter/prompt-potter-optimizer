"""Scorer formulas, evaluator registry, composite_fitness render, Rasch + subset, interactive steer."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from promptpotter.application.intelligence.exploration import (
    Observation,
    fit_rasch,
    select_round_subset,
)
from promptpotter.application.scoring.evaluators import (
    all_evaluators,
    materialize_round_values,
)
from promptpotter.application.scoring.formula import (
    compile_scorer,
    extract_display_answer,
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


@pytest.mark.parametrize(
    "predicted,gt,expected",
    [
        (r"First: \boxed{10}. Rechecking: \boxed{42}", "42", 1.0),  # last boxed wins
        (r"\boxed{undefined} The answer is 42", "42", 1.0),  # bad boxed → fallback
        ("no numbers", "42", 0.0),
    ],
)
def test_aime_match(predicted, gt, expected):
    assert _aime_match(predicted, gt) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("#### 1,234", 1234.0),  # comma-stripped
        ("I calculated 99 but #### 42", 42.0),  # #### preferred over fallback
        ("no numbers", None),
    ],
)
def test_extract_gsm8k_number(text, expected):
    assert _extract_gsm8k_number(text) == expected


@pytest.mark.parametrize(
    "predicted,gt,expected",
    [
        ("42.0", "#### 42", 1.0),  # cross-format numeric equivalence
        ("#### 99", "#### 42", 0.0),
    ],
)
def test_gsm8k_match(predicted, gt, expected):
    assert _gsm8k_match(predicted, gt) == expected


@pytest.mark.parametrize(
    "predicted,gt,expected",
    [
        ("First try **No**. Corrected: **Yes**", "yes", 1.0),  # last bold wins, case-insensitive
        ("plain text answer", "Plain Text Answer", 1.0),  # no markers + case-insensitive
        ("foo", "bar", 0.0),
    ],
)
def test_exact_match_bold_aware(predicted, gt, expected):
    assert _exact_match(predicted, gt) == expected


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


@pytest.mark.parametrize(
    "predicted,formula,expected",
    [
        ("The answer is **Disproved**.", "exact_match(predicted, ground_truth)", "Disproved"),
        (r"Working… \boxed{17}", "aime_match(predicted, ground_truth)", "17"),
        ("  padded  ", None, "padded"),
    ],
)
def test_extract_display_answer(predicted, formula, expected):
    assert extract_display_answer(predicted, formula) == expected


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
    pd: dict = {
        "total_time": total_time,
    }
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
        name="test",
        nodes=[
            PipelineNode(name="llm_only", node_type=NodeType.NONE),
        ],
    )


def _recall_schema() -> PipelineSchema:
    """Schema with candidate_source + ranker + cache — exercises all recall evaluators."""
    return PipelineSchema(
        name="test_recall",
        nodes=[
            PipelineNode(
                name="cache_lookup",
                node_type=NodeType.CACHE,
            ),
            PipelineNode(
                name="fuzzy",
                node_type=NodeType.CANDIDATE_SOURCE,
                observation_mappings=[
                    ObservationMapping(
                        pipeline_key="candidate_ranking",
                        obs_key="candidate_ranking",
                    )
                ],
            ),
            PipelineNode(
                name="ranker",
                node_type=NodeType.RANKER,
                observation_mappings=[
                    ObservationMapping(
                        pipeline_key="final_ranking",
                        obs_key="final_ranking",
                    )
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
    # Candidate measured only 8 of the *hardest* samples (the misses for
    # origin — sample_ids 10..17) and hit 3 of them.
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


def test_composite_fitness_zeroed_on_validation_failure():
    fake_opt_sp = SimpleNamespace(
        wounds=SimpleNamespace(validation_failures=[object()], runtime_failures=[])
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
            "diagnostics": {
                "warnings": [{"step": "llm_only", "code": "content_empty"}],
            },
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


# Interactive scoring-steer.
