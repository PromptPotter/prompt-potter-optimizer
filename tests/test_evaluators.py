"""Evaluator registry + materialization + compile_round_scorer invariants."""

from __future__ import annotations

import pytest

from promptpotter.application.scoring.evaluators import (
    all_evaluators,
    default_per_round_formula,
    materialize_round_values,
)
from promptpotter.application.scoring.metrics import compute_composite_score
from promptpotter.domain.pipeline_schema import (
    NodeType,
    ObservationMapping,
    PipelineNode,
    PipelineSchema,
)
from promptpotter.shared.scoring import compile_round_scorer

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


def test_registry_has_expected_names():
    names = {ev.name for ev in all_evaluators()}
    assert {
        "accuracy",
        "error_rate",
        "degraded_rate",
        "runtime_failure_rate",
        "latency_norm",
        "source_recall",
        "candidate_recall",
        "cache_hit_rate",
        "retrieval_shortfall",
        "mean_retrieval_shortfall",
        "pipeline_compactness",
    }.issubset(names)


def test_registry_scopes_are_valid():
    for ev in all_evaluators():
        assert ev.scope in ("per_query", "per_round")
        assert ev.data_type in ("NUMERIC", "BOOLEAN")


# ---------------------------------------------------------------------------
# Materialization
# ---------------------------------------------------------------------------


def test_materialize_round_values_single_node():
    schema = _single_node_schema()
    results = [_result(score=1.0), _result(score=0.0), _result(score=0.5)]
    values = materialize_round_values(schema, results)

    assert values["accuracy"] == pytest.approx(0.5)
    assert values["error_rate"] == 0.0
    assert values["degraded_rate"] == 0.0
    # No recall evaluators apply — those names should be absent.
    assert "source_recall" not in values
    assert "candidate_recall" not in values
    assert "cache_hit_rate" not in values


def test_materialize_round_values_recall_schema():
    schema = _recall_schema()
    results = [
        _result(
            final_ranking=[{"candidate": "gt"}, {"candidate": "x"}],
            candidate_ranking=[{"candidate": "gt"}],
            step_timings={"cache_lookup": 5.0, "fuzzy": 10.0, "ranker": 20.0},
        ),
        _result(
            final_ranking=[{"candidate": "x"}, {"candidate": "y"}],
            candidate_ranking=[{"candidate": "gt"}, {"candidate": "z"}],
            step_timings={"cache_lookup": None, "fuzzy": 10.0, "ranker": 20.0},
        ),
    ]
    values = materialize_round_values(schema, results)

    assert "source_recall" in values  # single candidate_source node → no namespace
    assert "candidate_recall" in values
    assert "cache_hit_rate" in values
    assert values["source_recall"] == pytest.approx(1.0)  # gt in both candidate_rankings
    assert values["candidate_recall"] == pytest.approx(0.5)  # gt in only first final_ranking


# ---------------------------------------------------------------------------
# Composite via compute_pipeline_metrics — backward-compat lock
# ---------------------------------------------------------------------------


def test_composite_matches_default_formula():
    schema = _single_node_schema()
    results = [_result(score=1.0, total_time=100), _result(score=0.0, total_time=200)]

    scored = compute_composite_score(results, schema)
    # Accuracy = 0.5, latency_norm = 1 - 150/10000 = 0.985, health = 1.0 (no errors/degraded).
    # No recall evaluators → recall term falls back to accuracy.
    expected = 0.7 * 0.5 + 0.15 * 1.0 + 0.10 * 0.985 + 0.05 * 0.5
    assert scored["composite"] == pytest.approx(expected, abs=1e-4)
    assert scored["accuracy"] == pytest.approx(0.5)


def test_composite_zeroed_on_validation_failure():
    from types import SimpleNamespace

    schema = _single_node_schema()
    results = [_result(score=1.0)]

    fake_opt_sp = SimpleNamespace(
        memory=SimpleNamespace(
            validation_failures=[object()],
            runtime_failures=[],
        )
    )
    scored = compute_composite_score(results, schema, opt_sp=fake_opt_sp)
    assert scored["composite"] == 0.0


def test_round_scorer_fails_loud_on_missing_name():
    scorer = compile_round_scorer("nonexistent_evaluator * 0.5")
    with pytest.raises(NameError):
        scorer({"accuracy": 1.0})


def test_round_scorer_default_uses_accuracy():
    scorer = compile_round_scorer(None)
    assert scorer({"accuracy": 0.75}) == pytest.approx(0.75)


def test_round_scorer_clamps_to_unit_interval():
    scorer = compile_round_scorer("accuracy * 10")
    assert scorer({"accuracy": 0.5}) == 1.0
    scorer = compile_round_scorer("accuracy - 2")
    assert scorer({"accuracy": 0.5}) == 0.0


def test_default_per_round_formula_shape():
    schema = _single_node_schema()
    formula = default_per_round_formula(schema)
    assert "accuracy" in formula
    assert "error_rate" in formula
    assert "latency_norm" in formula
    # Compile + execute to prove the formula is evaluable against the
    # registry namespace for this schema.
    values = materialize_round_values(
        schema,
        [_result(score=1.0), _result(score=0.0)],
    )
    scorer = compile_round_scorer(formula)
    out = scorer(values)
    assert 0.0 <= out <= 1.0
