"""Tests for mid-cycle rewind via ``CampaignStore.rewind_to_round``.

Covers the primitive behind ``optimize --from <round>``:

- Archiving later trial + candidate files into
  ``archived/resumed_at_<ts>/{trials,candidates}/``
- Rebuilding the in-memory trial index to reflect only surviving trials
- Raising ``LookupError`` for a round that has no trial on disk
"""

from __future__ import annotations

import json

import pytest

from promptpotter.infrastructure.store.campaign_store import CampaignStore


def _make_trial(round_num: int, accuracy: float) -> dict:
    return {
        "trial_id": f"round_{round_num}",
        "round": round_num,
        "label": f"r{round_num}",
        "accuracy": accuracy,
        "hits": int(accuracy * 10),
        "total": 10,
        "improved": accuracy > 0.0,
        "opt_search_point": {"id": f"osp_{round_num}"},
    }


def _seed_cycle(store: CampaignStore, backend_id: str, cycle_id: str, rounds: int) -> None:
    store.create(
        backend_id,
        cycle_id,
        {"type": "optimization_loop", "config": {}, "baseline_accuracy": 0.0},
    )
    for r in range(rounds):
        store.add_trial(backend_id, cycle_id, _make_trial(r, 0.1 * (r + 1)))
        # Simulate round-level candidate checkpoints for the same round.
        store.save_round_candidates(
            backend_id,
            cycle_id,
            r,
            [{"round": r, "id": f"cand_{r}"}],
        )


class TestRewindToRound:
    def test_archives_later_trial_and_candidate_files(self, tmp_path):
        store = CampaignStore(tmp_path)
        _seed_cycle(store, "bid", "cycle_a", rounds=5)

        store.rewind_to_round("bid", "cycle_a", after_round=2)

        cycle_dir = store.campaign_dir("cycle_a")
        trials_dir = cycle_dir / "trials"
        candidates_dir = cycle_dir / "candidates"
        assert (trials_dir / "trial_0000.json").exists()
        assert (trials_dir / "trial_0001.json").exists()
        assert (trials_dir / "trial_0002.json").exists()
        assert not (trials_dir / "trial_0003.json").exists()
        assert not (trials_dir / "trial_0004.json").exists()
        assert not (candidates_dir / "round_0003.json").exists()
        assert not (candidates_dir / "round_0004.json").exists()

        archived_roots = list((cycle_dir / "archived").iterdir())
        assert len(archived_roots) == 1
        archived = archived_roots[0]
        assert (archived / "trials" / "trial_0003.json").exists()
        assert (archived / "trials" / "trial_0004.json").exists()
        assert (archived / "candidates" / "round_0003.json").exists()
        assert (archived / "candidates" / "round_0004.json").exists()

    def test_rebuilds_trial_index_from_survivors(self, tmp_path):
        store = CampaignStore(tmp_path)
        _seed_cycle(store, "bid", "cycle_a", rounds=5)

        # Seed a best-accuracy that lives in an archived trial so the
        # rebuild has to recompute it.
        store.add_trial("bid", "cycle_a", _make_trial(4, 0.99))
        before = json.loads((store._entity_path("bid", "cycle_a")).read_text(encoding="utf-8"))
        assert before["best_accuracy"] == pytest.approx(0.99)

        store.rewind_to_round("bid", "cycle_a", after_round=2)

        after = json.loads((store._entity_path("bid", "cycle_a")).read_text(encoding="utf-8"))
        assert after["n_trials"] == 3
        rounds_in_index = sorted(t["round"] for t in after["trials"])
        assert rounds_in_index == [0, 1, 2]
        # Best trial is round 2 (accuracy 0.3 per seed formula).
        assert after["best_trial_id"] == "round_2"
        assert after["best_accuracy"] == pytest.approx(0.3)

    def test_resume_from_missing_round_raises(self, tmp_path):
        store = CampaignStore(tmp_path)
        _seed_cycle(store, "bid", "cycle_a", rounds=3)

        with pytest.raises(LookupError, match=r"trial_0099\.json not found"):
            store.rewind_to_round("bid", "cycle_a", after_round=99)

    def test_resume_from_missing_cycle_raises(self, tmp_path):
        store = CampaignStore(tmp_path)
        with pytest.raises(LookupError, match="no trials on disk"):
            store.rewind_to_round("bid", "cycle_nonexistent", after_round=0)

    def test_rewind_noop_when_target_is_latest(self, tmp_path):
        store = CampaignStore(tmp_path)
        _seed_cycle(store, "bid", "cycle_a", rounds=3)

        store.rewind_to_round("bid", "cycle_a", after_round=2)

        cycle_dir = store.campaign_dir("cycle_a")
        assert (cycle_dir / "trials" / "trial_0002.json").exists()
        # Nothing newer existed, so no archive dir is created.
        assert not (cycle_dir / "archived").exists()
