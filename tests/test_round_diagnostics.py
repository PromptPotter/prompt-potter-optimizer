"""``compute_round_diagnostics`` — pure function over scoring data.

Four named invariants:
  1. With no pipeline_schema, candidate-key resolution returns no candidates,
     so all valid samples land in ``not_found`` and ``n_valid`` counts every
     non-error result.
  2. Multi-round trajectories classify against the documented
     ``healthy / oscillating / plateau / regressing / ceiling`` axis.
  3. The function is deterministic — same input → same output.
  4. ``probe_outcome`` is populated only when ``probe_just_completed=True`` —
     the contract that lets L2/L3 read post-probe footprints from diagnostics.
"""

from __future__ import annotations

import pytest

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
    """No schema → no ranked_item_keys → find_rank always None → not_found bucket only."""
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


def test_probe_outcome_gated_by_probe_just_completed_flag():
    """probe_outcome is populated only when probe_just_completed=True."""
    results = [_hit("q1", "a"), _hit("q2", "b"), _miss("q3", "c"), _miss("q4", "d")]
    rr = _round(0, 0.5, results)

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
