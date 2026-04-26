"""Data-vs-policy separation: rescore-on-load, decision replay, and fork."""

from __future__ import annotations

import json
from pathlib import Path

from promptpotter.application.campaign.decisions import (
    REPLAYERS,
    _fork_at_divergence,
    replay_decisions,
)
from promptpotter.infrastructure.store.stores import build_stores
from promptpotter.shared.scoring import compile_scorer, rescore_results


def test_rescore_results_accumulates_and_projects_active() -> None:
    """Two scorers accumulate side-by-side; top-level score/hit follow the latest call."""
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
    rescore_results([result], compile_scorer(formula_a), scorer_id="a", formula=formula_a)
    rescore_results([result], compile_scorer(formula_a), scorer_id="a", formula=formula_a)
    assert list(result["scored"]) == ["a"]  # idempotent
    assert result["score"] == 1.0 and result["hit"] is True

    formula_b = "1 - exact_match(predicted, ground_truth)"
    rescore_results([result], compile_scorer(formula_b), scorer_id="b", formula=formula_b)
    assert set(result["scored"]) == {"a", "b"}
    assert result["score"] == 0.0 and result["hit"] is False


def _r(score: float) -> dict:
    return {"query": "q", "predicted": "p", "ground_truth": "g", "score": score, "hit": False}


def test_round_winner_replay_uses_rescored_baseline() -> None:
    """Uniform rescaling preserves the recorded winner — the replayer must
    derive its threshold from the rescored baseline, not the stale one."""
    trial = {
        "round": 0,
        "all_candidate_results": {
            "c1": [_r(0.5), _r(0.5)],
            "c2": [_r(0.1), _r(0.1)],
        },
        "decisions": [
            {
                "kind": "round_winner",
                "inputs_ref": {"candidate_ids": ["c1", "c2"], "round_num": 0},
                "outcome": "c1",
                "data": {"current_best_accuracy_at_record": 0.8},  # stale, must be ignored
            }
        ],
    }
    assert replay_decisions(trial, baseline_results=[_r(0.4), _r(0.4)]) is None


def test_elimination_cut_replay_flags_divergence_when_scores_flip() -> None:
    priors = [_r(1.0)] * 10
    current = [_r(1.0)] * 6  # rescored: now ties with priors
    trial = {
        "round": 2,
        "all_candidate_results": {"c0": priors, "c1": priors, "c2": current},
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
    assert div.recorded_outcome is True and div.current_outcome is False


def test_unknown_decision_kind_silently_skipped() -> None:
    """probe_round_commitment is recorded but not divergence-gated — no replayer registered."""
    assert "probe_round_commitment" not in REPLAYERS
    trial = {
        "round": 1,
        "results": [],
        "all_candidate_results": {},
        "decisions": [
            {
                "kind": "probe_round_commitment",
                "inputs_ref": {"round_num": 1},
                "outcome": True,
                "data": {"action": "PROBE"},
            }
        ],
    }
    assert replay_decisions(trial) is None


def _seed_cycle(projects_root: Path, tenant: str, cycle_id: str, n_rounds: int) -> list[dict]:
    """Lay down a minimal cycle dir on disk; return the trial-index list."""
    base = projects_root / tenant / "campaigns" / cycle_id
    (base / "trials").mkdir(parents=True)
    (base / "candidates").mkdir(parents=True)
    trials_index = []
    for r in range(n_rounds):
        t = {"trial_id": f"round_{r}", "round": r, "accuracy": 0.5 + 0.1 * r, "label": f"r{r}"}
        (base / "trials" / f"trial_{r:04d}.json").write_text(json.dumps(t), encoding="utf-8")
        (base / "candidates" / f"round_{r:04d}.json").write_text("[]", encoding="utf-8")
        trials_index.append(t)
    (base / "index.json").write_text(
        json.dumps(
            {
                "campaign_id": cycle_id,
                "trials": trials_index,
                "n_trials": n_rounds,
                "best_trial_id": f"round_{n_rounds - 1}",
                "status": "in_progress",
            }
        ),
        encoding="utf-8",
    )
    return trials_index


def test_fork_at_divergence_drops_round_R_and_sets_parent_pointer(
    tmp_path: Path, monkeypatch
) -> None:
    """Forking inside resume must inherit trials < R, drop R, and retarget the pointer."""
    tenant = "default"
    old_cycle = "cycle_abc123"
    trials = _seed_cycle(tmp_path, tenant, old_cycle, n_rounds=4)
    ptr = tmp_path / ".promptpotter" / "active_session.json"
    monkeypatch.setattr("promptpotter.infrastructure.store.stores._ACTIVE_SESSION_PATH", ptr)

    stores = build_stores(tmp_path, tenant_id=tenant)
    new_cycle = _fork_at_divergence(
        stores.campaigns,
        tenant_id=tenant,
        session_id="s_test",
        old_cycle_id=old_cycle,
        fork_from_round=2,
        surviving_trials=trials[:2],
    )

    new_dir = tmp_path / tenant / "campaigns" / new_cycle
    assert (new_dir / "trials" / "trial_0001.json").exists()
    assert not (new_dir / "trials" / "trial_0002.json").exists()  # R is dropped
    assert not (new_dir / "trials" / "trial_0003.json").exists()  # > R also dropped

    index = json.loads((new_dir / "index.json").read_text(encoding="utf-8"))
    assert index["parent_cycle_id"] == old_cycle
    assert index["forked_from_round"] == 2
    assert index["n_trials"] == 2

    pointer = json.loads(ptr.read_text(encoding="utf-8"))
    assert pointer == {"tenant_id": tenant, "session_id": "s_test", "cycle_id": new_cycle}
