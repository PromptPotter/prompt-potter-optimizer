"""``compute_round_diagnostics`` — pure function over scoring data.

Three named invariants:
  1. With no pipeline_schema, candidate-key resolution returns no candidates,
     so all valid samples land in ``not_found`` and ``n_valid`` counts every
     non-error result.
  2. Multi-round trajectories classify against the documented
     ``healthy / oscillating / plateau / regressing / ceiling`` axis.
  3. The function is deterministic — same input → same output.
"""

from __future__ import annotations

from promptpotter.application.optimization.round_diagnostics import (
    compute_round_diagnostics,
)
from promptpotter.domain.results import RoundResult


def _round(round_num: int, accuracy: float, results: list[dict]) -> RoundResult:
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


def test_rank_lookup_no_schema_lands_everything_not_found():
    """No schema → no candidate_keys → find_rank always None → not_found bucket only."""
    results = [_hit("q1", "a"), _miss("q2", "b"), _miss("q3", "c")]
    rr = _round(0, 0.33, results)
    diag = compute_round_diagnostics(rr, [rr], pipeline_schema=None)

    assert diag.n_valid == 3
    assert diag.rank_buckets["1"] == 0
    assert diag.rank_buckets["not_found"] == 3
    assert diag.top_k_accuracy[1] == 0.0
    assert diag.top_k_accuracy[10] == 0.0


def test_trajectory_classification_picks_up_plateau():
    """Three+ rounds with negligible deltas → plateau (or ceiling at best)."""
    rounds = [
        _round(0, 0.50, [_hit("q1", "a")]),
        _round(1, 0.51, [_hit("q1", "a")]),
        _round(2, 0.51, [_hit("q1", "a")]),
        _round(3, 0.51, [_hit("q1", "a")]),
    ]
    diag = compute_round_diagnostics(rounds[-1], rounds, pipeline_schema=None)
    assert diag.trajectory in {"plateau", "ceiling"}


def test_pure_function_deterministic_output():
    """Same input → same output. No hidden state in compute_round_diagnostics."""
    results = [_hit("q1", "a"), _miss("q2", "b")]
    rr = _round(0, 0.5, results)
    a = compute_round_diagnostics(rr, [rr], pipeline_schema=None)
    b = compute_round_diagnostics(rr, [rr], pipeline_schema=None)
    assert a.rank_buckets == b.rank_buckets
    assert a.top_k_accuracy == b.top_k_accuracy
    assert a.trajectory == b.trajectory
