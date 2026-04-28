"""Evaluator registry + materialization + compile_round_scorer invariants."""

from __future__ import annotations

import pytest

from promptpotter.application.scoring.evaluators import (
    all_evaluators,
    materialize_round_values,
)
from promptpotter.application.scoring.formula import compile_round_scorer
from promptpotter.application.scoring.metrics import compute_composite_score
from promptpotter.domain.pipeline_schema import (
    NodeType,
    ObservationMapping,
    PipelineNode,
    PipelineSchema,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _result(
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
        "score": score,
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


# ---------------------------------------------------------------------------
# Registry structure
# ---------------------------------------------------------------------------


def test_registry_scopes_are_valid():
    """Every registered evaluator declares a known scope + data type."""
    names = {ev.name for ev in all_evaluators()}
    assert {"accuracy", "error_rate", "latency_norm", "source_recall"}.issubset(names)
    for ev in all_evaluators():
        assert ev.scope in ("per_query", "per_round")
        assert ev.data_type in ("NUMERIC", "BOOLEAN")


def test_materialize_recall_only_emits_for_typed_nodes():
    """Recall evaluators only materialize when the schema has a candidate_source/ranker."""
    single = materialize_round_values(_single_node_schema(), [_result(score=1.0)])
    assert "source_recall" not in single

    schema = _recall_schema()
    values = materialize_round_values(
        schema,
        [
            _result(
                final_ranking=[{"candidate": "gt"}],
                candidate_ranking=[{"candidate": "gt"}],
                step_timings={"cache_lookup": 5.0, "fuzzy": 10.0, "ranker": 20.0},
            ),
            _result(
                final_ranking=[{"candidate": "x"}],
                candidate_ranking=[{"candidate": "gt"}],
                step_timings={"cache_lookup": None, "fuzzy": 10.0, "ranker": 20.0},
            ),
        ],
    )
    assert values["source_recall"] == pytest.approx(1.0)
    assert values["candidate_recall"] == pytest.approx(0.5)
    assert "cache_hit_rate" in values


def test_composite_matches_default_formula():
    schema = _single_node_schema()
    results = [_result(score=1.0, total_time=100), _result(score=0.0, total_time=200)]
    scored = compute_composite_score(results, schema)
    # Accuracy=0.5, latency_norm=0.985, health=1.0; recall term falls back to accuracy.
    # prompt_compactness defaults to 1.0 when no opt_sp passed (vacuous).
    expected = 0.65 * 0.5 + 0.15 * 1.0 + 0.10 * 0.985 + 0.05 * 0.5 + 0.05 * 1.0
    assert scored["composite"] == pytest.approx(expected, abs=1e-4)


def test_composite_zeroed_on_validation_failure():
    from types import SimpleNamespace

    fake_opt_sp = SimpleNamespace(validation_failures=[object()], runtime_failures=[])
    scored = compute_composite_score(
        [_result(score=1.0)], _single_node_schema(), opt_sp=fake_opt_sp
    )
    assert scored["composite"] == 0.0


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
