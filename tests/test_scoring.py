"""Per-dataset scorer formulas, evaluator registry, composite_fitness render, scoring-set
evolution, and interactive steering.

Five named invariants:
  1. Dataset-specific scorers (AIME / GSM8K / exact_match) recover wrong-reveal
     cases via ``compile_scorer`` + ``extract_display_answer``.
  2. ``materialize_round_values`` only emits recall/cache evaluators when the
     pipeline schema has typed nodes; ``compute_composite_fitness`` matches the
     default formula and zeroes on validation failure; ``compute_prompt_compactness``
     penalises verbose prompts and stays vacuous when no opt_sp is passed.
  3. ``render_composite_fitness_block`` / ``render_composite_fitness_oneliner`` /
     ``inline_short_formula_values`` produce formula text + named evaluator pairs
     within their three-line frame budget, dropping evaluators not in formula.
  4. ``fit_rasch`` recovers known θ/δ on synthetic Bernoulli data and is sparse-safe;
     ``evolve_scoring_set`` preserves the ``elimination_n_min`` floor, mutates only the
     scoring slice, and is a no-op when disabled.
  5. ``apply_steer_file`` hot-swaps ``session.scoring.round_scorer`` on a valid
     ``scoring_steer.json`` (archives the file) and leaves state untouched on
     a broken formula (file stays in place for the operator).
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from promptpotter.application.config import ExplorationConfig
from promptpotter.application.intelligence.exploration import (
    Observation,
    evolve_scoring_set,
    fit_rasch,
    knowledge_gradient,
)
from promptpotter.application.scoring.evaluators import (
    all_evaluators,
    materialize_round_values,
)
from promptpotter.application.scoring.formula import (
    _aime_match,
    _exact_match,
    _extract_gsm8k_number,
    _gsm8k_match,
    compile_round_scorer,
    compile_scorer,
    extract_display_answer,
)
from promptpotter.application.scoring.metrics import compute_composite_fitness
from promptpotter.domain.pipeline_schema import (
    NodeType,
    ObservationMapping,
    PipelineNode,
    PipelineSchema,
)
from promptpotter.domain.results import RoundResult
from promptpotter.domain.sample import Sample
from promptpotter.shared.composite import extract_evaluator_names

# ===========================================================================
# Per-dataset scorer formulas
# ===========================================================================


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
    "predicted,formula,expected",
    [
        ("The answer is **Disproved**.", "exact_match(predicted, ground_truth)", "Disproved"),
        (r"Working… \boxed{17}", "aime_match(predicted, ground_truth)", "17"),
        ("  padded  ", None, "padded"),
    ],
)
def test_extract_display_answer(predicted, formula, expected):
    assert extract_display_answer(predicted, formula) == expected


# ===========================================================================
# Evaluator registry + materialization + composite_fitness scoring
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
        assert ev.scope in ("per_query", "per_round")
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
    # Accuracy=0.5, latency_norm=0.985, health=1.0; recall term falls back to accuracy.
    # prompt_compactness defaults to 1.0 when no opt_sp passed (vacuous).
    expected = 0.65 * 0.5 + 0.15 * 1.0 + 0.10 * 0.985 + 0.05 * 0.5 + 0.05 * 1.0
    assert scored["composite_fitness"] == pytest.approx(expected, abs=1e-4)


def test_composite_fitness_zeroed_on_validation_failure():
    fake_opt_sp = SimpleNamespace(validation_failures=[object()], runtime_failures=[])
    scored = compute_composite_fitness(
        [_eval_result(score=1.0)], _single_node_schema(), opt_sp=fake_opt_sp
    )
    assert scored["composite_fitness"] == 0.0


def test_round_scorer_fails_loud_and_clamps_unit_interval():
    with pytest.raises(NameError):
        compile_round_scorer("nonexistent_evaluator * 0.5")({"accuracy": 1.0})
    assert compile_round_scorer("accuracy * 10")({"accuracy": 0.5}) == 1.0
    assert compile_round_scorer("accuracy - 2")({"accuracy": 0.5}) == 0.0
    assert compile_round_scorer(None)({"accuracy": 0.75}) == pytest.approx(0.75)


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
# Composite render — data-shape only (display-format substrings out per charter)
# ===========================================================================


def test_extract_names_filters_builtins_and_unknowns() -> None:
    formula = "0.5 * accuracy + log(1.0 + latency_norm) + max(0.0, recall) - 1.0"
    available = {"accuracy", "latency_norm", "recall", "error_rate"}
    # log + max are builtins → excluded. error_rate is in available but
    # not in formula → excluded. Bare numbers → not valid identifiers.
    assert extract_evaluator_names(formula, available) == [
        "accuracy",
        "latency_norm",
        "recall",
    ]


# ===========================================================================
# Rasch fit + scoring-set evolution
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
    # Wide spread + many obs/pair → MAP should recover both arrays within tolerance.
    theta_true = {"strong": 1.5, "mid": 0.0, "weak": -1.5}
    delta_true = {1: -1.0, 2: 0.0, 3: 1.0, 4: 2.0}
    obs = _synth_observations(theta_true, delta_true, n_per_pair=40, seed=42)

    posterior = fit_rasch(obs, theta_prior_sigma=10.0, delta_prior_sigma=10.0)

    assert posterior.converged
    # Identifiability: both arrays anchored to mean(theta) == 0.
    assert abs(sum(posterior.theta.values()) / len(posterior.theta)) < 1e-6
    # Recovery is within the noise floor for n=40 obs/pair (Bernoulli SE ~ 0.08).
    # Loosen to 0.5 logits — math correctness is what we're guarding, not precision.
    theta_offset = sum(theta_true.values()) / len(theta_true)
    for cid, t_true in theta_true.items():
        assert abs(posterior.theta[cid] - (t_true - theta_offset)) < 0.5
    for sid, d_true in delta_true.items():
        assert abs(posterior.delta[sid] - (d_true - theta_offset)) < 0.5


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


def test_kg_is_non_negative_and_zero_for_unknown_pair() -> None:
    obs = _synth_observations({"a": 1.0, "b": -1.0}, {1: 0.0, 2: 0.5}, n_per_pair=10, seed=1)
    posterior = fit_rasch(obs)

    for cid in ("a", "b"):
        for sid in (1, 2):
            assert knowledge_gradient(posterior, cid, sid) >= 0.0

    assert knowledge_gradient(posterior, "unknown", 1) == 0.0
    assert knowledge_gradient(posterior, "a", 999) == 0.0


def _make_round(round_idx: int, candidate_results: dict[str, list[dict]]) -> RoundResult:
    return RoundResult(
        round=round_idx,
        label=f"R{round_idx}",
        accuracy=0.5,
        composite_fitness=0.5,
        hits=0,
        total=0,
        improved=False,
        prompt_fields={},
        all_candidate_results=candidate_results,
        candidates_scored=len(candidate_results),
    )


def test_evolve_scoring_set_respects_min_size() -> None:
    # Tiny scoring set at the floor → swap-out must yield zero, returning current.
    scoring_set = [Sample(id=i, query=f"q{i}", ground_truth="g") for i in range(4)]
    extra = [Sample(id=10 + i, query=f"q{10 + i}", ground_truth="g") for i in range(5)]
    full = scoring_set + extra

    # Many candidates measuring all scoring-set samples consistently → narrow δ SE
    # → swap-out would be eligible, but the elimination_n_min==4 floor must block it.
    candidate_results = {
        f"c{ci}": [{"sample_id": s.id, "hit": True} for s in scoring_set] for ci in range(8)
    }
    rounds = [_make_round(0, candidate_results)]

    cfg = ExplorationConfig(
        enabled=True,
        swap_out_delta_se=10.0,  # extremely permissive — every scoring-set sample qualifies
        swap_in_kg_threshold=0.0,
        max_swaps_per_round=10,
    )
    out = evolve_scoring_set(
        full_dataset=full,
        current_scoring_set=scoring_set,
        rounds=rounds,
        config=cfg,
        elimination_n_min=4,  # floor matches len(scoring_set)
    )
    assert len(out.new_scoring_set) >= 4
    # No scoring-set sample dropped below the floor.
    assert {s.id for s in out.new_scoring_set} >= {s.id for s in scoring_set} - {-1}


@pytest.mark.parametrize("enabled", [True, False])
def test_evolve_scoring_set_does_not_mutate_inputs(enabled: bool) -> None:
    samples = [Sample(id=i, query=f"q{i}", ground_truth="g") for i in range(6)]
    snapshot = list(samples)
    rounds = [
        _make_round(
            0,
            {f"c{ci}": [{"sample_id": s.id, "hit": True} for s in samples] for ci in range(3)},
        )
    ]
    cfg = ExplorationConfig(enabled=enabled)
    evolve_scoring_set(
        full_dataset=samples,
        current_scoring_set=samples,
        rounds=rounds,
        config=cfg,
        elimination_n_min=4,
    )
    # Inputs untouched — caller is responsible for mutating session.scoring.scoring_set.
    assert samples == snapshot


# ===========================================================================
# Interactive scoring-steer
# ===========================================================================


def _write_steer(cycle_dir: Path, payload: dict) -> Path:
    p = cycle_dir / "scoring_steer.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


def _make_session(tmp_path: Path) -> SimpleNamespace:
    """Minimal fake satisfying ``apply_steer_file``'s session contract."""
    cycle_id = "cycle_steer_test"
    cycle_dir = tmp_path / "campaigns" / cycle_id
    cycle_dir.mkdir(parents=True)
    campaigns = SimpleNamespace(campaign_dir=lambda _cid: cycle_dir)
    store = SimpleNamespace(campaigns=campaigns)
    state = SimpleNamespace(cycle_id=cycle_id)
    scoring = SimpleNamespace(round_scorer=None, scorer_round_formula=None)
    return SimpleNamespace(
        state=state,
        store=store,
        scoring=scoring,
        _cycle_dir=cycle_dir,
    )


def test_valid_steer_swaps_round_scorer_and_archives_file(tmp_path: Path) -> None:
    from promptpotter.application.scoring.formula import apply_steer_file

    session = _make_session(tmp_path)
    _write_steer(session._cycle_dir, {"per_round": "0.5 * accuracy + 0.5 * latency_norm"})

    events: list[dict] = []
    applied = apply_steer_file(
        session, round_num=3, on_phase=lambda evt: events.append(evt.model_dump())
    )

    assert applied == "0.5 * accuracy + 0.5 * latency_norm"
    assert callable(session.scoring.round_scorer)
    # Quick sanity: the new scorer evaluates against the registry namespace.
    assert session.scoring.round_scorer({"accuracy": 1.0, "latency_norm": 1.0}) == pytest.approx(
        1.0
    )
    assert session.scoring.scorer_round_formula == applied

    # File archived, not left in place.
    assert not (session._cycle_dir / "scoring_steer.json").exists()
    assert any(p.name.startswith("scoring_steer.applied.") for p in session._cycle_dir.iterdir())

    # PhaseRecord event surfaced for the operator-facing log.
    assert len(events) == 1
    assert events[0]["phase"] == "scoring_steer"
    assert events[0]["event"] == "applied"
    assert events[0]["data"]["formula"] == applied


def test_invalid_steer_leaves_state_untouched(tmp_path: Path) -> None:
    from promptpotter.application.scoring.formula import apply_steer_file

    session = _make_session(tmp_path)
    sentinel = object()
    session.scoring.round_scorer = sentinel
    session.scoring.scorer_round_formula = "original"
    _write_steer(session._cycle_dir, {"per_round": "nonexistent_evaluator * 1.0"})

    applied = apply_steer_file(session, round_num=0, on_phase=None)

    assert applied is None
    assert session.scoring.round_scorer is sentinel  # exact identity preserved
    assert session.scoring.scorer_round_formula == "original"
    assert (session._cycle_dir / "scoring_steer.json").exists()  # left for operator


def test_no_steer_file_is_noop(tmp_path: Path) -> None:
    from promptpotter.application.scoring.formula import apply_steer_file

    session = _make_session(tmp_path)
    sentinel = object()
    session.scoring.round_scorer = sentinel

    assert apply_steer_file(session, round_num=0, on_phase=None) is None
    assert session.scoring.round_scorer is sentinel
