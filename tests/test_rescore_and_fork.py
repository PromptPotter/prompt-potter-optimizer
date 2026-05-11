"""Rescore-on-load + decision replay + ``_mint_fork`` dispatch + rewind."""

from __future__ import annotations

import json
from pathlib import Path

from promptpotter.application.optimization.resume_and_fork import (
    _mint_fork,
    replay_decisions,
)
from promptpotter.application.scoring.formula import compile_scorer, rescore_results
from promptpotter.application.scoring.search_point_scorer import (
    merge_with_unprocessed_priors,
)
from promptpotter.domain.cycle_paths import CycleDir
from promptpotter.domain.run_records import (
    ForkPayload,
    ForkTrigger,
    ResumeCheckpointKind,
    ResumeCheckpointRecord,
)
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.store import build_stores, walk_cycle_lineage


def test_rescore_results_accumulates_and_projects_active() -> None:
    """Two scorers accumulate side-by-side; top-level score/hit follow the latest call."""
    result = {
        "query": "q",
        "predicted": "**42**",
        "ground_truth": "42",
        "hit": False,
        "fitness": 0.0,
        "error": None,
        "pipeline_data": None,
        "ground_truth_rank": 1,
    }
    formula_a = "exact_match(predicted, ground_truth)"
    rescore_results([result], compile_scorer(formula_a), scorer_id="a", formula=formula_a)
    rescore_results([result], compile_scorer(formula_a), scorer_id="a", formula=formula_a)
    assert list(result["scored"]) == ["a"]  # idempotent
    assert result["fitness"] == 1.0 and result["hit"] is True

    formula_b = "1 - exact_match(predicted, ground_truth)"
    rescore_results([result], compile_scorer(formula_b), scorer_id="b", formula=formula_b)
    assert set(result["scored"]) == {"a", "b"}
    assert result["fitness"] == 0.0 and result["hit"] is False


def _r(score: float) -> dict:
    return {"query": "q", "predicted": "p", "ground_truth": "g", "fitness": score, "hit": False}


def test_round_winner_replay_uses_rescored_origin() -> None:
    """Uniform rescaling preserves the recorded winner — the replayer must
    derive its threshold from the rescored origin, not the stale one."""
    round_data = {
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
    assert replay_decisions(round_data, origin_results=[_r(0.4), _r(0.4)]) is None


def test_elimination_cut_replay_flags_divergence_when_scores_flip() -> None:
    priors = [_r(1.0)] * 10
    current = [_r(1.0)] * 6  # rescored: now ties with priors
    round_data = {
        "round": 2,
        "all_candidate_results": {"c0": priors, "c1": priors, "c2": current},
        "decisions": [
            {
                "kind": "elimination_cut",
                "inputs_ref": {
                    "candidate_id": "c2",
                    "prior_candidate_ids": ["c0", "c1"],
                    "queries_scored": 6,
                    "epsilon": 0.05,
                    "n_min": 4,
                    "round_num": 2,
                },
                "outcome": True,
                "data": {},
            }
        ],
    }
    div = replay_decisions(round_data)
    assert div is not None
    assert div.kind == "elimination_cut"
    assert div.recorded_outcome is True and div.current_outcome is False


def _seed_cycle(projects_root: Path, tenant: str, cycle_id: str, n_rounds: int) -> list[dict]:
    """Lay down a minimal cycle dir on disk; return the round_data-index list."""
    base = projects_root / tenant / "campaigns" / cycle_id
    (base / "rounds").mkdir(parents=True)
    (base / "candidates").mkdir(parents=True)
    rounds_index = []
    for r in range(n_rounds):
        t = {"round_id": f"round_{r}", "round": r, "accuracy": 0.5 + 0.1 * r, "label": f"r{r}"}
        (base / "rounds" / f"round_{r:04d}.json").write_text(json.dumps(t), encoding="utf-8")
        (base / "candidates" / f"round_{r:04d}.json").write_text("[]", encoding="utf-8")
        rounds_index.append(t)
    (base / "index.json").write_text(
        json.dumps(
            {
                "campaign_id": cycle_id,
                "rounds": rounds_index,
                "n_rounds": n_rounds,
                "best_round_id": f"round_{n_rounds - 1}",
                "status": "in_progress",
            }
        ),
        encoding="utf-8",
    )
    return rounds_index


def _patch_pointer(monkeypatch, tmp_path: Path) -> Path:
    ptr = tmp_path / ".promptpotter" / "active_session.json"
    monkeypatch.setattr("promptpotter.infrastructure.store._ACTIVE_SESSION_PATH", ptr)
    return ptr


def _div_payload() -> ForkPayload:
    return ForkPayload(
        trigger=ForkTrigger.SCORING_DIVERGENCE, reason="scorer_mismatch", issued_by="system"
    )


def _diag_payload() -> ForkPayload:
    return ForkPayload(trigger=ForkTrigger.OPERATOR_DIAG, reason="bfs", issued_by="default")


def _sweep_payload() -> ForkPayload:
    return ForkPayload(
        trigger=ForkTrigger.OPERATOR_SWEEP,
        reason="probe persona",
        issued_by="default",
        l1_layout={"task_intent": ["task_context"]},
    )


def test_mint_fork_scoring_divergence_inherits_and_appends_fork_cut(
    tmp_path: Path, monkeypatch
) -> None:
    """SCORING_DIVERGENCE: inherit rounds < R, retarget pointer, FORK_CUT carries typed payload."""
    tenant = "default"
    parent = "cycle_div_parent"
    rounds = _seed_cycle(tmp_path, tenant, parent, n_rounds=4)
    ptr = _patch_pointer(monkeypatch, tmp_path)

    stores = build_stores(tmp_path, tenant_id=tenant)
    parent_dir = stores.campaigns.campaign_dir(parent)
    new_cycle = _mint_fork(
        stores.campaigns,
        tenant,
        "s_test",
        parent,
        2,
        _div_payload(),
        surviving_rounds=rounds[:2],
    )

    new_dir = stores.campaigns.campaign_dir(new_cycle)
    assert new_dir.parent.name == "forks"
    assert (new_dir / "rounds" / "round_0001.json").exists()
    assert not (new_dir / "rounds" / "round_0002.json").exists()

    index = json.loads((new_dir / "index.json").read_text(encoding="utf-8"))
    assert index["parent_cycle_id"] == parent
    assert index["forked_from_round"] == 2
    assert index["fork"] == {"trigger": "scoring_divergence"}

    assert json.loads(ptr.read_text(encoding="utf-8"))["cycle_id"] == new_cycle

    cut = list(CycleEventLog.open(CycleDir(parent_dir)).iter())[-1]
    assert isinstance(cut, ResumeCheckpointRecord)
    assert cut.kind is ResumeCheckpointKind.FORK_CUT
    assert cut.outcome == new_cycle
    assert cut.inputs_ref == {"from_round": 2}
    assert cut.data["fork"] == {
        "trigger": "scoring_divergence",
        "reason": "scorer_mismatch",
        "issued_by": "system",
        "l1_layout": None,
    }


def test_mint_fork_operator_diag_counted_id_clean_slate(tmp_path: Path, monkeypatch) -> None:
    """OPERATOR_DIAG: counted ``_diag_NNN`` id, no inheritance, typed FORK_CUT."""
    tenant = "default"
    parent = "cyclediagparent"
    _seed_cycle(tmp_path, tenant, parent, n_rounds=2)
    _patch_pointer(monkeypatch, tmp_path)
    stores = build_stores(tmp_path, tenant_id=tenant)

    sib1 = _mint_fork(stores.campaigns, tenant, "s_test", parent, 0, _diag_payload())
    sib2 = _mint_fork(stores.campaigns, tenant, "s_test", parent, 0, _diag_payload())

    assert sib1 == f"{parent}_diag_001"
    assert sib2 == f"{parent}_diag_002"

    sib1_dir = stores.campaigns.campaign_dir(sib1)
    assert sib1_dir.parent.name == "diag"
    sib1_index = json.loads((sib1_dir / "index.json").read_text(encoding="utf-8"))
    assert sib1_index["rounds"] == []
    assert sib1_index["fork"] == {"trigger": "operator_diag"}


def test_mint_fork_operator_sweep_no_inherit_and_dedup_fields(tmp_path: Path, monkeypatch) -> None:
    """OPERATOR_SWEEP: clean-slate, dedup fields at FORK_CUT data top level, typed payload."""
    tenant = "default"
    parent = "cyclesweepparent"
    _seed_cycle(tmp_path, tenant, parent, n_rounds=1)
    cands = tmp_path / tenant / "campaigns" / parent / ".runtime" / "cache" / "candidates"
    cands.mkdir(parents=True)
    (cands / "round_0000.json").write_text("[]", encoding="utf-8")
    _patch_pointer(monkeypatch, tmp_path)
    stores = build_stores(tmp_path, tenant_id=tenant)
    parent_dir = stores.campaigns.campaign_dir(parent)

    new_cycle = _mint_fork(
        stores.campaigns,
        tenant,
        "s_test",
        parent,
        0,
        _sweep_payload(),
        sweep_batch_id="b1abc",
        sweep_source_file="01_persona.json",
    )

    new_dir = stores.campaigns.campaign_dir(new_cycle)
    assert new_dir.parent.parent.parent.name == "sweeps"
    assert not (new_dir / ".runtime" / "cache" / "candidates" / "round_0000.json").exists()

    index = json.loads((new_dir / "index.json").read_text(encoding="utf-8"))
    assert index["sweep_batch_id"] == "b1abc"
    assert index["fork"] == {"trigger": "operator_sweep"}

    # source_file + sweep_batch_id stay at data top level so
    # existing_fork_source_files dedup keeps working without parsing data.fork.
    cut = list(CycleEventLog.open(CycleDir(parent_dir)).iter())[-1]
    assert isinstance(cut, ResumeCheckpointRecord)
    assert cut.data["source_file"] == "01_persona.json"
    assert cut.data["sweep_batch_id"] == "b1abc"
    assert cut.data["fork"]["trigger"] == "operator_sweep"
    assert cut.data["fork"]["l1_layout"] == {"task_intent": ["task_context"]}


def test_walk_cycle_lineage_walks_parent_chain(tmp_path: Path, monkeypatch) -> None:
    """Lineage walker returns ``[root, ..., leaf]`` via parent_cycle_id chain."""
    tenant = "default"
    parent = "cycle_lineage_root"
    _seed_cycle(tmp_path, tenant, parent, n_rounds=2)
    _patch_pointer(monkeypatch, tmp_path)
    stores = build_stores(tmp_path, tenant_id=tenant)

    fork = _mint_fork(
        stores.campaigns,
        tenant,
        "s_test",
        parent,
        1,
        _div_payload(),
        surviving_rounds=[{"round_id": "round_0", "round": 0, "accuracy": 0.5, "label": "r0"}],
    )
    sweep = _mint_fork(
        stores.campaigns,
        tenant,
        "s_test",
        fork,
        0,
        _sweep_payload(),
        sweep_batch_id="b1abc",
        sweep_source_file="x.json",
    )

    tenant_root = tmp_path / tenant
    assert walk_cycle_lineage(tenant_root, parent) == [parent]
    assert walk_cycle_lineage(tenant_root, fork) == [parent, fork]
    assert walk_cycle_lineage(tenant_root, sweep) == [parent, fork, sweep]


def _prior(query: str, predicted: str = "p", gt: str = "g") -> dict:
    return {
        "query": query,
        "predicted": predicted,
        "ground_truth": gt,
        "error": None,
        "pipeline_data": {"total_time": 1.5},
    }


def test_merge_with_unprocessed_priors_preserves_full_archive_on_partial_run() -> None:
    """The load-bearing invariant: a partial state.results merged with cached_sample_results
    yields back every dataset query the archive already covered.

    Aborted runs must not shrink an already-fuller archive — without this the
    overwrite-on-save ``_persist_fresh`` would grind down the cache file each
    Ctrl+C.
    """
    queries = [f"q{i}" for i in range(20)]
    dataset_queries = set(queries)
    cached_sample_results = {q: _prior(q) for q in queries}
    # Simulate a partial run: 6 cache hits + 1 fresh measurement.
    state_results = [_prior(q) for q in queries[:7]]
    formula = "exact_match(predicted, ground_truth)"
    merged = merge_with_unprocessed_priors(
        state_results,
        cached_sample_results=cached_sample_results,
        dataset_queries=dataset_queries,
        deprecated_samples={},
        scorer=compile_scorer(formula),
        scorer_id="x",
        scorer_formula=formula,
    )
    assert len(merged) == 20
    assert {r["query"] for r in merged} == dataset_queries


def test_merge_with_unprocessed_priors_filters_off_dataset_and_evicted() -> None:
    """Only dataset queries get merged; evicted (deprecated) priors are excluded
    so they re-measure on the next encounter."""
    dataset_queries = {"q1", "q2"}
    cached_sample_results = {
        "q1": _prior("q1"),
        "q2": _prior("q2"),
        "q_off": _prior("q_off"),  # not in current dataset
    }
    deprecated = {"q2": _prior("q2")}  # q2 deprecated → must remeasure
    formula = "exact_match(predicted, ground_truth)"
    merged = merge_with_unprocessed_priors(
        [],
        cached_sample_results=cached_sample_results,
        dataset_queries=dataset_queries,
        deprecated_samples=deprecated,
        scorer=compile_scorer(formula),
        scorer_id="x",
        scorer_formula=formula,
    )
    assert {r["query"] for r in merged} == {"q1"}


# ===========================================================================
# CampaignStore.rewind_to_round — mid-cycle rewind primitive behind --from
# ===========================================================================


def _make_round_data(round_num: int, accuracy: float) -> dict:
    return {
        "round_id": f"round_{round_num}",
        "round": round_num,
        "label": f"r{round_num}",
        "accuracy": accuracy,
        "hits": int(accuracy * 10),
        "total": 10,
        "improved": accuracy > 0.0,
        "opt_search_point": {"id": f"osp_{round_num}"},
    }


def _seed_rewind_cycle(store, backend_id: str, cycle_id: str, rounds: int) -> None:
    store.create(
        backend_id,
        cycle_id,
        {"type": "optimization_loop", "config": {}, "origin_accuracy": 0.0},
    )
    for r in range(rounds):
        store.save_round_file(backend_id, cycle_id, _make_round_data(r, 0.1 * (r + 1)))
        # Simulate round-level candidate checkpoints for the same round.
        store.save_round_candidates(
            backend_id,
            cycle_id,
            r,
            [{"round": r, "id": f"cand_{r}"}],
        )


class TestRewindToRound:
    def test_archives_later_round_and_candidate_files(self, tmp_path):
        from promptpotter.infrastructure.store import CampaignStore

        store = CampaignStore(tmp_path)
        _seed_rewind_cycle(store, "bid", "cycle_a", rounds=5)

        store.rewind_to_round("bid", "cycle_a", after_round=2)

        cycle_dir = store.campaign_dir("cycle_a")
        rounds_dir = cycle_dir / "rounds"
        candidates_dir = cycle_dir / ".runtime" / "cache" / "candidates"
        assert (rounds_dir / "round_0000.json").exists()
        assert (rounds_dir / "round_0001.json").exists()
        assert (rounds_dir / "round_0002.json").exists()
        assert not (rounds_dir / "round_0003.json").exists()
        assert not (rounds_dir / "round_0004.json").exists()
        assert not (candidates_dir / "round_0003.json").exists()
        assert not (candidates_dir / "round_0004.json").exists()

        archived_roots = list((cycle_dir / ".runtime" / "archived").iterdir())
        assert len(archived_roots) == 1
        archived = archived_roots[0]
        assert (archived / "rounds" / "round_0003.json").exists()
        assert (archived / "rounds" / "round_0004.json").exists()
        assert (archived / "candidates" / "round_0003.json").exists()
        assert (archived / "candidates" / "round_0004.json").exists()

    def test_rebuilds_round_index_from_survivors(self, tmp_path):
        import pytest as _pytest

        from promptpotter.infrastructure.store import CampaignStore

        store = CampaignStore(tmp_path)
        _seed_rewind_cycle(store, "bid", "cycle_a", rounds=5)

        # Seed a best-accuracy that lives in an archived round_data so the
        # rebuild has to recompute it.
        store.save_round_file("bid", "cycle_a", _make_round_data(4, 0.99))
        before = json.loads((store._entity_path("bid", "cycle_a")).read_text(encoding="utf-8"))
        assert before["best_accuracy"] == _pytest.approx(0.99)

        store.rewind_to_round("bid", "cycle_a", after_round=2)

        after = json.loads((store._entity_path("bid", "cycle_a")).read_text(encoding="utf-8"))
        assert after["n_rounds"] == 3
        rounds_in_index = sorted(t["round"] for t in after["rounds"])
        assert rounds_in_index == [0, 1, 2]
        # Best round_data is round 2 (accuracy 0.3 per seed formula).
        assert after["best_round_id"] == "round_2"
        assert after["best_accuracy"] == _pytest.approx(0.3)

    def test_resume_from_missing_round_raises(self, tmp_path):
        import pytest as _pytest

        from promptpotter.infrastructure.store import CampaignStore

        store = CampaignStore(tmp_path)
        _seed_rewind_cycle(store, "bid", "cycle_a", rounds=3)

        with _pytest.raises(LookupError, match=r"round_0099\.json not found"):
            store.rewind_to_round("bid", "cycle_a", after_round=99)

    def test_resume_from_missing_cycle_raises(self, tmp_path):
        import pytest as _pytest

        from promptpotter.infrastructure.store import CampaignStore

        store = CampaignStore(tmp_path)
        with _pytest.raises(LookupError, match="no rounds on disk"):
            store.rewind_to_round("bid", "cycle_nonexistent", after_round=0)
