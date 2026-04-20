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
