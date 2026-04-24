"""Hard-sample-sorter artifact — schema + axis-contract + truncation invariants.

Guards three contracts the phase-3 webapp will lean on:
  1. Schema v1 shape + top-level keys (matches the on-disk serialization).
  2. Axis sort contract — desc θ_c / desc δ_s with the spec's tie-breakers.
  3. Truncation honesty — ``truncated``/``total_*`` fields survive the cap,
     and pass ``top_k_*=None`` yields the full matrix.

Renderer output is explicitly NOT tested (tests/CLAUDE.md forbids
display-format assertions). File-on-disk existence is covered by
``tests/test_artifact_parity.py``; Rasch math by ``tests/test_adaptive_prefix.py``.
"""

from __future__ import annotations

from promptpotter.application.campaign.config import HardSampleSorterConfig
from promptpotter.application.intelligence.adaptive_prefix import build_observations
from promptpotter.application.intelligence.hard_sample_sorter import (
    ARTIFACT_SCHEMA_VERSION,
    build_hard_samples_artifact,
)
from promptpotter.application.optimization.results import RoundResult


def _make_round(round_idx: int, candidate_results: dict[str, list[dict]]) -> RoundResult:
    return RoundResult(
        round=round_idx,
        label=f"R{round_idx}",
        accuracy=0.5,
        composite=0.5,
        hits=0,
        total=0,
        improved=False,
        prompt_fields={},
        all_candidate_results=candidate_results,
        candidates_scored=len(candidate_results),
    )


def _fixture_rounds() -> list[RoundResult]:
    """Deterministic 6-candidate × 10-sample hit matrix.

    ``c_strong``/``c_good`` solve most samples; ``c_weak``/``c_dead`` miss most.
    Samples 0–2 are easy (most candidates hit), 7–9 are hard (most miss).
    Stable enough that Rasch recovers the intended θ/δ ordering.
    """
    # Map (cid, sid) → hit. Only materialize the ~50 cells we need.
    hits: dict[tuple[str, int], bool] = {}
    abilities = {
        "c_strong": 0.95,
        "c_good": 0.80,
        "c_mid_a": 0.55,
        "c_mid_b": 0.50,
        "c_weak": 0.25,
        "c_dead": 0.10,
    }
    # Higher index = harder.
    easiness = {sid: 1.0 - (sid / 10.0) for sid in range(10)}

    for cid, base in abilities.items():
        for sid, ease in easiness.items():
            # Deterministic: hit iff ability > (1 - ease) threshold.
            # Gives decreasing hit rate across sample axis, monotonic over candidates.
            hits[(cid, sid)] = base >= (1.0 - ease)

    results: dict[str, list[dict]] = {cid: [] for cid in abilities}
    for (cid, sid), hit in hits.items():
        results[cid].append({"sample_id": sid, "hit": hit})
    return [_make_round(0, results)]


def test_build_hard_samples_artifact_shape_axes_and_truncation() -> None:
    rounds = _fixture_rounds()
    full_obs = build_observations(rounds)

    # 1. Schema contract — capped artifact carries honesty fields.
    art = build_hard_samples_artifact(
        rounds, cycle_id="cycle_test", top_k_candidates=4, top_k_samples=4
    )
    required_keys = {
        "schema_version",
        "cycle_id",
        "generated_at",
        "disabled",
        "truncated",
        "total_candidates",
        "total_samples",
        "n_candidates",
        "n_samples",
        "n_observations",
        "candidate_order",
        "sample_order",
        "rasch",
        "cells",
    }
    assert required_keys <= set(art.keys())
    assert art["schema_version"] == ARTIFACT_SCHEMA_VERSION
    assert art["cycle_id"] == "cycle_test"
    assert art["disabled"] is False

    # Rasch sub-object carries the full posterior shape, not the compact
    # prefix_events summary. delta/theta keys must align with the persisted axes.
    rasch = art["rasch"]
    for key in ("converged", "iterations", "theta", "theta_se", "delta", "delta_se"):
        assert key in rasch
    assert set(rasch["theta"].keys()) == set(art["candidate_order"])
    assert set(rasch["delta"].keys()) == {str(sid) for sid in art["sample_order"]}

    # 2. Axis contract — desc θ_c / desc δ_s. After truncation to top_k=4 the
    # persisted axes hold the strongest 4 candidates and hardest 4 samples.
    assert art["candidate_order"][0] == "c_strong"
    assert "c_dead" not in art["candidate_order"]
    assert art["sample_order"][0] == 9  # hardest sample on the left

    # 3a. Truncation honesty — 6 candidates × 10 samples, top_k=4 each.
    assert art["truncated"] is True
    assert art["total_candidates"] == 6
    assert art["total_samples"] == 10
    assert art["n_candidates"] == 4
    assert art["n_samples"] == 4

    # 3b. Cells restricted to persisted axes; observations outside either axis
    # must not materialize.
    cand_set = set(art["candidate_order"])
    samp_set = set(art["sample_order"])
    for cell in art["cells"]:
        assert cell["c"] in cand_set
        assert cell["s"] in samp_set
    # Sanity: count matches the intersection of observations with both axes.
    expected_cells = sum(
        1 for o in full_obs if o.candidate_id in cand_set and o.sample_id in samp_set
    )
    assert art["n_observations"] == expected_cells
    assert len(art["cells"]) == expected_cells

    # 3c. Uncapped run yields the full matrix, truncated=False, full axis order.
    full = build_hard_samples_artifact(rounds, top_k_candidates=None, top_k_samples=None)
    assert full["truncated"] is False
    assert full["n_candidates"] == full["total_candidates"] == 6
    assert full["n_samples"] == full["total_samples"] == 10
    assert len(full["cells"]) == len(full_obs)
    # Full ordering places the engineered extremes at the ends.
    assert full["candidate_order"][0] == "c_strong"
    assert full["candidate_order"][-1] == "c_dead"
    # Hardest sample is unique (sid=9 missed by all but c_strong); the easy tail
    # is tied — sid=0 and sid=1 both solved by every candidate, so the final
    # tie-breaker (sid asc) puts sid=1 at the very end. Either of the tied
    # easiest pair is acceptable; just confirm the easy tail is not the hard tail.
    assert full["sample_order"][0] == 9
    assert full["sample_order"][-1] in {0, 1}


def test_empty_rounds_produce_valid_stub_artifact() -> None:
    """Zero-observation campaigns produce a parseable, schema-valid stub —
    renderer and webapp can short-circuit via ``n_observations == 0``."""
    art = build_hard_samples_artifact([], cycle_id="cycle_empty")
    assert art["schema_version"] == ARTIFACT_SCHEMA_VERSION
    assert art["cycle_id"] == "cycle_empty"
    assert art["disabled"] is False
    assert art["n_observations"] == 0
    assert art["cells"] == []
    assert art["candidate_order"] == []
    assert art["sample_order"] == []


def test_disabled_config_surfaces_in_config_model() -> None:
    """``HardSampleSorterConfig`` validates with ``extra='forbid'`` and defaults
    to enabled — guards the wire contract with users authoring campaign.json."""
    default = HardSampleSorterConfig()
    assert default.enabled is True
    assert default.top_k_candidates == 40
    assert default.top_k_samples == 40

    explicit = HardSampleSorterConfig(enabled=False)
    assert explicit.enabled is False
