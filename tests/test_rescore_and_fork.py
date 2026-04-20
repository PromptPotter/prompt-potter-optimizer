"""Data-vs-policy separation: rescore-on-load, decision replay, and fork.

Three stable contracts:

1. ``rescore_results`` accumulates into ``result["scored"][scorer_id]`` and
   projects the active scorer onto top-level ``score`` / ``hit``. Idempotent.
2. ``replay_decisions`` returns the first ``Divergence`` when a registered
   replayer re-derives a different outcome under a changed scorer.
3. ``fork_cycle`` copies index + trials + candidates for rounds 0..R into
   a new cycle directory with ``parent_cycle_id`` + ``forked_from_round``
   set, and retargets the active-session pointer.
"""

from __future__ import annotations

import json
from pathlib import Path

from promptpotter.application.campaign.decisions import (
    REPLAYERS,
    replay_decisions,
)
from promptpotter.application.campaign.fork import fork_cycle
from promptpotter.infrastructure.store.stores import build_stores
from promptpotter.shared.scoring import compile_scorer, rescore_results

# ---------------------------------------------------------------------------
# rescore_results
# ---------------------------------------------------------------------------


def test_rescore_results_accumulates_scored_map_and_is_idempotent() -> None:
    """Two passes under the same scorer_id yield one entry; a second
    scorer_id accumulates a second entry without touching the first."""
    result = {
        "query": "q",
        "predicted": "**42**",
        "ground_truth": "42",
        "hit": False,
        "score": 0.0,
        "error": None,
        "pipeline_data": None,
        "ground_truth_rank": 1,
    }
    formula_a = "exact_match(predicted, ground_truth)"
    scorer_a = compile_scorer(formula_a)

    rescore_results([result], scorer_a, scorer_id="policy_a", formula=formula_a)
    rescore_results([result], scorer_a, scorer_id="policy_a", formula=formula_a)

    assert list(result["scored"].keys()) == ["policy_a"]
    assert result["scored"]["policy_a"] == {"score": 1.0, "hit": True, "formula": formula_a}
    assert result["score"] == 1.0 and result["hit"] is True

    formula_b = "1 - exact_match(predicted, ground_truth)"
    scorer_b = compile_scorer(formula_b)
    rescore_results([result], scorer_b, scorer_id="policy_b", formula=formula_b)

    assert set(result["scored"].keys()) == {"policy_a", "policy_b"}
    assert result["scored"]["policy_a"]["score"] == 1.0
    assert result["scored"]["policy_b"]["score"] == 0.0
    # Active projection follows the most recent call
    assert result["score"] == 0.0 and result["hit"] is False


# ---------------------------------------------------------------------------
# replay_decisions
# ---------------------------------------------------------------------------


def test_replay_decisions_flags_round_winner_divergence() -> None:
    """Winner under the recorded scorer flips under a rescored view."""

    def _r(score: float) -> dict:
        return {
            "query": "q",
            "predicted": "p",
            "ground_truth": "g",
            "score": score,
            "hit": score >= 1.0,
        }

    trial = {
        "round": 3,
        "all_candidate_results": {
            # c1 was the recorded winner (high score under old policy)
            "c1": [_r(1.0), _r(1.0)],
            # Under current scorer, c2 dominates (pre-rescored for the test)
            "c2": [_r(1.0), _r(1.0), _r(1.0)],
        },
        "decisions": [
            {
                "kind": "round_winner",
                "inputs_ref": {"candidate_ids": ["c1", "c2"], "current_best_accuracy": 0.0},
                "outcome": "c1",
            }
        ],
    }
    # Sanity: the round_winner replayer is registered
    assert "round_winner" in REPLAYERS

    # Tie (1.0 vs 1.0 vs mean=1.0 either way) — argmax picks first seen > best.
    # Make c2 strictly dominate by lowering c1's scores.
    trial["all_candidate_results"]["c1"] = [_r(0.5), _r(0.5)]
    div = replay_decisions(trial)
    assert div is not None
    assert div.kind == "round_winner"
    assert div.recorded_outcome == "c1"
    assert div.current_outcome == "c2"
    assert div.round_num == 3


def test_replay_round_winner_uses_rescored_baseline_not_stale_threshold() -> None:
    """A uniform rescaling of scores (B = A/2 everywhere) must not flag
    divergence: both baseline and candidates shrink proportionally, so the
    recorded winner stays the winner. The stale ``current_best_accuracy``
    stored in inputs_ref under scorer A would have survived into the
    replayer and fabricated a divergence — this regression test pins the
    fix that derives the beat-threshold from the rescored baseline."""

    def _r(score: float) -> dict:
        return {"query": "q", "predicted": "p", "ground_truth": "g", "score": score, "hit": False}

    trial = {
        "round": 0,
        "all_candidate_results": {
            # Under scorer B (rescaled /2), c1 beats rescored baseline (0.4)
            # cleanly; c2 loses. Recorded winner was c1 under scorer A,
            # still c1 under B.
            "c1": [_r(0.5), _r(0.5)],
            "c2": [_r(0.1), _r(0.1)],
        },
        "decisions": [
            {
                "kind": "round_winner",
                # The stale threshold (0.8, under the OLD scorer A) is now
                # in ``data`` — the replayer must ignore it and derive its
                # own threshold from the rescored baseline.
                "inputs_ref": {"candidate_ids": ["c1", "c2"], "round_num": 0},
                "outcome": "c1",
                "data": {"current_best_accuracy_at_record": 0.8},
            }
        ],
    }
    rescored_baseline = [_r(0.4), _r(0.4)]
    assert replay_decisions(trial, baseline_results=rescored_baseline) is None


def test_replay_decisions_returns_none_when_outcome_matches() -> None:
    def _r(score: float) -> dict:
        return {
            "query": "q",
            "predicted": "p",
            "ground_truth": "g",
            "score": score,
            "hit": score >= 1.0,
        }

    trial = {
        "round": 0,
        "all_candidate_results": {
            "c1": [_r(1.0), _r(1.0)],
            "c2": [_r(0.2)],
        },
        "decisions": [
            {
                "kind": "round_winner",
                "inputs_ref": {"candidate_ids": ["c1", "c2"], "current_best_accuracy": 0.0},
                "outcome": "c1",
            }
        ],
    }
    assert replay_decisions(trial) is None


# ---------------------------------------------------------------------------
# Decision `data` sidecar — archived, never compared for divergence
# ---------------------------------------------------------------------------


def test_decision_data_roundtrips_and_is_ignored_by_replay() -> None:
    """``data`` survives to_dict/from_dict and mutating it never fires divergence."""
    from promptpotter.application.campaign.decisions import Decision

    d = Decision(
        kind="round_winner",
        inputs_ref={"candidate_ids": [], "current_best_accuracy": 0.0},
        outcome="",
        data={"llm_output": "raw critique text", "diag": 42},
    )
    restored = Decision.from_dict(d.to_dict())
    assert restored.data == {"llm_output": "raw critique text", "diag": 42}

    # Mutate data arbitrarily; outcome unchanged → no divergence.
    trial = {
        "round": 0,
        "all_candidate_results": {},
        "decisions": [
            {
                **d.to_dict(),
                "data": {"llm_output": "COMPLETELY DIFFERENT TEXT"},
            }
        ],
    }
    assert replay_decisions(trial) is None


# ---------------------------------------------------------------------------
# elimination_cut replayer
# ---------------------------------------------------------------------------


def _score_row(score: float) -> dict:
    return {"query": "q", "predicted": "p", "ground_truth": "g", "score": score, "hit": False}


def test_replay_elimination_cut_matches_under_rescoring() -> None:
    """Candidate that was eliminated under the recorded scorer stays
    eliminated under a rescored view that preserves the same ordering."""
    # Priors score ≈ 1.0 everywhere; current candidate consistently below.
    priors_c0 = [_score_row(1.0) for _ in range(10)]
    priors_c1 = [_score_row(1.0) for _ in range(10)]
    current = [_score_row(0.0) for _ in range(6)]
    trial = {
        "round": 2,
        "all_candidate_results": {"c0": priors_c0, "c1": priors_c1, "c2": current},
        "decisions": [
            {
                "kind": "elimination_cut",
                "inputs_ref": {
                    "candidate_id": "c2",
                    "prior_candidate_ids": ["c0", "c1"],
                    "queries_evaluated": 6,
                    "alpha": 0.2,
                    "n_min": 4,
                    "round_num": 2,
                },
                "outcome": True,
                "data": {"triggered_p": 0.001},
            }
        ],
    }
    assert replay_decisions(trial) is None


def test_replay_elimination_cut_flags_divergence_when_rescored_scores_flip() -> None:
    """Under a rescored view where the current candidate now matches the
    priors, the t-test no longer rejects → recorded ``True`` diverges."""
    priors_c0 = [_score_row(1.0) for _ in range(10)]
    priors_c1 = [_score_row(1.0) for _ in range(10)]
    # Rescored scores: current candidate is tied with priors.
    current = [_score_row(1.0) for _ in range(6)]
    trial = {
        "round": 2,
        "all_candidate_results": {"c0": priors_c0, "c1": priors_c1, "c2": current},
        "decisions": [
            {
                "kind": "elimination_cut",
                "inputs_ref": {
                    "candidate_id": "c2",
                    "prior_candidate_ids": ["c0", "c1"],
                    "queries_evaluated": 6,
                    "alpha": 0.2,
                    "n_min": 4,
                    "round_num": 2,
                },
                "outcome": True,
                "data": {},
            }
        ],
    }
    div = replay_decisions(trial)
    assert div is not None
    assert div.kind == "elimination_cut"
    assert div.recorded_outcome is True
    assert div.current_outcome is False


# ---------------------------------------------------------------------------
# l2_escalation_trigger replayer
# ---------------------------------------------------------------------------


def _round_trial(round_num: int, winner_score: float) -> dict:
    """Minimal trial with a rescorable winner composite."""
    return {
        "round": round_num,
        "results": [_score_row(winner_score), _score_row(winner_score)],
        "all_candidate_results": {},
        "decisions": [],
    }


def test_replay_l2_trigger_fires_when_not_stalled() -> None:
    """L2 never fired yet → stall=0 → fires (outcome=True), no divergence."""
    trial = {
        "round": 3,
        "results": [_score_row(0.5)],
        "all_candidate_results": {},
        "decisions": [
            {
                "kind": "l2_escalation_trigger",
                "inputs_ref": {
                    "round_num": 3,
                    "l2_patience": 2,
                    "from_degradation": False,
                    "entry_round": -1,
                },
                "outcome": True,
                "data": {},
            }
        ],
    }
    assert replay_decisions(trial, prior_trials=[_round_trial(0, 0.5)]) is None


def test_replay_l2_trigger_flags_divergence_when_rescored_improvement_resets_stall() -> None:
    """L2 entered at round 1 with baseline=0.5. Rescored rounds 2 and 3 both
    beat baseline, so stall=0 under rescoring. Recorded outcome was
    ``False`` (patience exhausted); replayer returns ``True`` → divergence."""
    prior = [
        _round_trial(0, 0.5),
        _round_trial(1, 0.5),  # L2 entered here; baseline = 0.5
        _round_trial(2, 0.9),  # beats baseline → improvement
        _round_trial(3, 0.9),
    ]
    trial = {
        "round": 4,
        "results": [_score_row(0.9)],
        "all_candidate_results": {},
        "decisions": [
            {
                "kind": "l2_escalation_trigger",
                "inputs_ref": {
                    "round_num": 4,
                    "l2_patience": 2,
                    "from_degradation": False,
                    "entry_round": 1,
                },
                "outcome": False,  # recorded: patience exhausted
                "data": {},
            }
        ],
    }
    div = replay_decisions(trial, prior_trials=prior)
    assert div is not None
    assert div.kind == "l2_escalation_trigger"
    assert div.recorded_outcome is False
    assert div.current_outcome is True


def test_replay_l2_trigger_from_degradation_is_non_divergent() -> None:
    """Degradation-triggered L2 isn't replayable (would need re-running
    the degradation detector). Replayer raises → non-divergence."""
    trial = {
        "round": 3,
        "results": [_score_row(0.5)],
        "all_candidate_results": {},
        "decisions": [
            {
                "kind": "l2_escalation_trigger",
                "inputs_ref": {
                    "round_num": 3,
                    "l2_patience": 2,
                    "from_degradation": True,
                    "entry_round": -1,
                },
                "outcome": True,
                "data": {},
            }
        ],
    }
    assert replay_decisions(trial) is None


# ---------------------------------------------------------------------------
# probe_round_commitment — recorded but not divergence-gated
# ---------------------------------------------------------------------------


def test_replay_probe_commitment_is_silently_skipped() -> None:
    """Probe is an LLM-output projection; no replayer registered. Unknown
    kinds are silently skipped by ``replay_decisions`` — which is exactly
    the ``recorded, not divergence-gated`` semantics we want."""
    assert "probe_round_commitment" not in REPLAYERS
    trial = {
        "round": 1,
        "results": [],
        "all_candidate_results": {},
        "decisions": [
            {
                "kind": "probe_round_commitment",
                "inputs_ref": {"round_num": 1, "l2_round": 1},
                "outcome": True,
                "data": {"action": "TransitionAction.PROBE", "l2_directive_preview": "…"},
            }
        ],
    }
    # Even if the outcome is nonsensical, replay never returns a Divergence.
    assert replay_decisions(trial) is None


# ---------------------------------------------------------------------------
# fork_cycle
# ---------------------------------------------------------------------------


def _seed_cycle(projects_root: Path, tenant: str, cycle_id: str, n_rounds: int) -> None:
    """Write minimal index + trial files so fork has something to copy."""
    base = projects_root / tenant
    camp = base / "campaigns" / cycle_id
    (camp / "trials").mkdir(parents=True)
    (camp / "candidates").mkdir(parents=True)
    trials_index = []
    for r in range(n_rounds):
        t = {
            "trial_id": f"round_{r}",
            "round": r,
            "accuracy": 0.5 + 0.1 * r,
            "hits": r,
            "total": 10,
            "improved": r > 0,
            "label": f"r{r}",
            "created_at": "",
        }
        (camp / "trials" / f"trial_{r:04d}.json").write_text(json.dumps(t), encoding="utf-8")
        (camp / "candidates" / f"round_{r:04d}.json").write_text("[]", encoding="utf-8")
        trials_index.append(t)
    (camp / "index.json").write_text(
        json.dumps(
            {
                "campaign_id": cycle_id,
                "trials": trials_index,
                "n_trials": n_rounds,
                "best_accuracy": max((t["accuracy"] for t in trials_index), default=0.0),
                "best_trial_id": f"round_{n_rounds - 1}",
                "status": "in_progress",
            }
        ),
        encoding="utf-8",
    )


def test_fork_cycle_copies_trials_and_sets_parent_pointer(tmp_path: Path, monkeypatch) -> None:
    tenant = "default"
    old_cycle = "cycle_abc123"
    _seed_cycle(tmp_path, tenant, old_cycle, n_rounds=4)

    # Redirect the active-session pointer to a test-local path so the test
    # doesn't touch the user's real .promptpotter/ dir.
    ptr = tmp_path / ".promptpotter" / "active_session.json"
    monkeypatch.setattr("promptpotter.infrastructure.store.stores._ACTIVE_SESSION_PATH", ptr)

    stores = build_stores(tmp_path, tenant_id=tenant)
    new_cycle = fork_cycle(
        stores,
        tenant_id=tenant,
        session_id="s_test",
        backend_id="backend",
        old_cycle_id=old_cycle,
        fork_from_round=2,
    )

    assert new_cycle.startswith(old_cycle + "_fork_")
    new_dir = tmp_path / tenant / "campaigns" / new_cycle
    assert (new_dir / "trials" / "trial_0000.json").exists()
    assert (new_dir / "trials" / "trial_0002.json").exists()
    assert not (new_dir / "trials" / "trial_0003.json").exists(), "rounds > R must not be copied"
    assert (new_dir / "candidates" / "round_0002.json").exists()

    index = json.loads((new_dir / "index.json").read_text(encoding="utf-8"))
    assert index["campaign_id"] == new_cycle
    assert index["parent_cycle_id"] == old_cycle
    assert index["forked_from_round"] == 2
    assert index["n_trials"] == 3  # rounds 0, 1, 2

    pointer = json.loads(ptr.read_text(encoding="utf-8"))
    assert pointer == {"tenant_id": tenant, "session_id": "s_test", "cycle_id": new_cycle}
