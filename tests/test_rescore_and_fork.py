"""Data-vs-policy separation: rescore-on-load, decision replay, fork, and rewind.

Three named invariants:
  1. ``rescore_results`` accumulates per-scorer projections side-by-side and
     projects ``score`` / ``hit`` to the active scorer; ``replay_decisions``
     uses rescored baselines (never stale recorded ones); ``elimination_cut``
     replay flags divergence when scores flip; unknown decision kinds skip.
  2. ``_fork_at_divergence`` / ``fork_for_diag_sibling`` /
     ``fork_for_sweep_sibling`` mint counted ids, retarget the active pointer,
     drop trials beyond the fork point (or all trials for diag/sweep),
     and append a FORK_CUT decision to the parent ledger that downstream
     readers can consume via the freshly re-opened parent.
  3. ``CampaignStore.rewind_to_round`` archives later trial + candidate files
     under ``.runtime/archived/resumed_at_<ts>/``, rebuilds the in-memory
     trial index from survivors, and raises ``LookupError`` for missing
     rounds / missing cycles.
"""

from __future__ import annotations

import json
from pathlib import Path

from promptpotter.application.optimization.cycle import (
    _fork_at_divergence,
    fork_for_diag_sibling,
    fork_for_sweep_sibling,
    replay_decisions,
)
from promptpotter.application.scoring.formula import compile_scorer, rescore_results
from promptpotter.application.scoring.search_point_scorer import (
    merge_with_unprocessed_priors,
)
from promptpotter.domain.run_records import SweepPayload
from promptpotter.infrastructure.store import build_stores


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
    div = replay_decisions(trial)
    assert div is not None
    assert div.kind == "elimination_cut"
    assert div.recorded_outcome is True and div.current_outcome is False


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
    monkeypatch.setattr(
        "promptpotter.infrastructure.store.active_pointer._ACTIVE_SESSION_PATH", ptr
    )

    stores = build_stores(tmp_path, tenant_id=tenant)
    new_cycle = _fork_at_divergence(
        stores.campaigns,
        tenant_id=tenant,
        session_id="s_test",
        old_cycle_id=old_cycle,
        fork_from_round=2,
        surviving_trials=trials[:2],
    )

    new_dir = stores.campaigns.campaign_dir(new_cycle)
    assert new_dir.parent.name == "forks", "fork dirs nest under their family root's forks/"
    assert (new_dir / "trials" / "trial_0001.json").exists()
    assert not (new_dir / "trials" / "trial_0002.json").exists()  # R is dropped
    assert not (new_dir / "trials" / "trial_0003.json").exists()  # > R also dropped

    index = json.loads((new_dir / "index.json").read_text(encoding="utf-8"))
    assert index["parent_cycle_id"] == old_cycle
    assert index["forked_from_round"] == 2
    assert index["n_trials"] == 2

    pointer = json.loads(ptr.read_text(encoding="utf-8"))
    assert pointer == {"tenant_id": tenant, "session_id": "s_test", "cycle_id": new_cycle}


def test_fork_at_divergence_appends_fork_cut_to_parent_ledger(tmp_path: Path, monkeypatch) -> None:
    """The parent's events.jsonl must end with a FORK_CUT decision naming the new cycle.

    A reader tailing the parent's ledger sees the cutover inline — no
    cross-file polling needed to discover that a fork was minted.
    """
    from promptpotter.domain.cycle_paths import CycleDir
    from promptpotter.domain.run_records import Decision, DecisionKind
    from promptpotter.infrastructure.ledger import RunLedger

    tenant = "default"
    old_cycle = "cycle_fork_cut_test"
    trials = _seed_cycle(tmp_path, tenant, old_cycle, n_rounds=3)
    ptr = tmp_path / ".promptpotter" / "active_session.json"
    monkeypatch.setattr(
        "promptpotter.infrastructure.store.active_pointer._ACTIVE_SESSION_PATH", ptr
    )

    stores = build_stores(tmp_path, tenant_id=tenant)
    parent_dir = stores.campaigns.campaign_dir(old_cycle)
    new_cycle = _fork_at_divergence(
        stores.campaigns,
        tenant_id=tenant,
        session_id="s_test",
        old_cycle_id=old_cycle,
        fork_from_round=1,
        surviving_trials=trials[:1],
    )

    parent_ledger = RunLedger.open(CycleDir(parent_dir))
    records = list(parent_ledger.iter())
    assert records, "parent ledger must contain the FORK_CUT record"
    cut = records[-1]
    assert isinstance(cut, Decision)
    assert cut.kind is DecisionKind.FORK_CUT
    assert cut.outcome == new_cycle
    assert cut.inputs_ref == {"from_round": 1}

    # The fork's ledger inheriting from the freshly-reopened parent must
    # see the FORK_CUT marker — i.e. the runner's "re-open parent before
    # inherit" pattern is what downstream readers depend on.
    fork_dir = stores.campaigns.campaign_dir(new_cycle)
    fork_ledger = RunLedger.open(CycleDir(fork_dir))
    fresh_parent = RunLedger.open(CycleDir(parent_dir))
    fork_ledger.inherit_from(fresh_parent, fresh_parent.next_offset)
    fork_history = list(fork_ledger.iter())
    assert any(
        isinstance(r, Decision) and r.kind is DecisionKind.FORK_CUT and r.outcome == new_cycle
        for r in fork_history
    ), "fork inheriting from re-opened parent must see the FORK_CUT marker"


def _prior(query: str, predicted: str = "p", gt: str = "g") -> dict:
    return {
        "query": query,
        "predicted": predicted,
        "ground_truth": gt,
        "error": None,
        "pipeline_data": {"total_time": 1.5},
    }


def test_merge_with_unprocessed_priors_preserves_full_archive_on_partial_run() -> None:
    """The load-bearing invariant: a partial state.results merged with prior_results
    yields back every dataset query the archive already covered.

    Aborted runs must not shrink an already-fuller archive — without this the
    overwrite-on-save ``_persist_fresh`` would grind down the cache file each
    Ctrl+C.
    """
    queries = [f"q{i}" for i in range(20)]
    dataset_queries = set(queries)
    prior_results = {q: _prior(q) for q in queries}
    # Simulate a partial run: 6 cache hits + 1 fresh measurement.
    state_results = [_prior(q) for q in queries[:7]]
    formula = "exact_match(predicted, ground_truth)"
    merged = merge_with_unprocessed_priors(
        state_results,
        prior_results=prior_results,
        dataset_queries=dataset_queries,
        evicted_priors={},
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
    prior_results = {
        "q1": _prior("q1"),
        "q2": _prior("q2"),
        "q_off": _prior("q_off"),  # not in current dataset
    }
    evicted = {"q2": _prior("q2")}  # q2 deprecated → must remeasure
    formula = "exact_match(predicted, ground_truth)"
    merged = merge_with_unprocessed_priors(
        [],
        prior_results=prior_results,
        dataset_queries=dataset_queries,
        evicted_priors=evicted,
        scorer=compile_scorer(formula),
        scorer_id="x",
        scorer_formula=formula,
    )
    assert {r["query"] for r in merged} == {"q1"}


def test_fork_for_diag_sibling_mints_counted_id_and_clears_trials(
    tmp_path: Path, monkeypatch
) -> None:
    """Diag-BFS siblings get human-counted ids (``_diag_001``, ``_002``…),
    inherit ``parent_cycle_id`` + baseline_accuracy, and start with empty trials
    so each probe is a fresh diagnostic from the parent's baseline."""
    tenant = "default"
    parent = "cycle_diagparent"
    _seed_cycle(tmp_path, tenant, parent, n_rounds=2)
    parent_index_path = tmp_path / tenant / "campaigns" / parent / "index.json"
    parent_index = json.loads(parent_index_path.read_text(encoding="utf-8"))
    parent_index["baseline_accuracy"] = 0.42
    parent_index["final"] = {"mode": "diag"}
    parent_index_path.write_text(json.dumps(parent_index), encoding="utf-8")

    ptr = tmp_path / ".promptpotter" / "active_session.json"
    monkeypatch.setattr(
        "promptpotter.infrastructure.store.active_pointer._ACTIVE_SESSION_PATH", ptr
    )

    stores = build_stores(tmp_path, tenant_id=tenant)
    sib1 = fork_for_diag_sibling(stores.campaigns, tenant, "s_test", parent)
    sib2 = fork_for_diag_sibling(stores.campaigns, tenant, "s_test", parent)

    assert sib1 == f"{parent}_diag_001"
    assert sib2 == f"{parent}_diag_002"

    sib1_dir = stores.campaigns.campaign_dir(sib1)
    assert sib1_dir.parent.name == "diag", (
        "diag siblings nest under diag/ (separate from operator forks/)"
    )

    sib1_index = json.loads((sib1_dir / "index.json").read_text(encoding="utf-8"))
    assert sib1_index["parent_cycle_id"] == parent
    assert sib1_index["forked_from_round"] == 0
    assert sib1_index["fork_kind"] == "diag_sibling"
    assert sib1_index["trials"] == []
    assert sib1_index["baseline_accuracy"] == 0.42  # inherited

    pointer = json.loads(ptr.read_text(encoding="utf-8"))
    assert pointer["cycle_id"] == sib2  # second mint retargets pointer


def test_fork_for_diag_sibling_appends_fork_cut_to_parent_ledger(
    tmp_path: Path, monkeypatch
) -> None:
    """Parent's events.jsonl must end with a FORK_CUT decision naming the new
    sibling and ``from_round=0``, with ``data.kind="diag_sibling"`` so a tail
    of the parent distinguishes diag-BFS branches from divergence forks."""
    from promptpotter.domain.cycle_paths import CycleDir
    from promptpotter.domain.run_records import Decision, DecisionKind
    from promptpotter.infrastructure.ledger import RunLedger

    tenant = "default"
    parent = "cycle_diagparent2"
    _seed_cycle(tmp_path, tenant, parent, n_rounds=1)
    ptr = tmp_path / ".promptpotter" / "active_session.json"
    monkeypatch.setattr(
        "promptpotter.infrastructure.store.active_pointer._ACTIVE_SESSION_PATH", ptr
    )

    stores = build_stores(tmp_path, tenant_id=tenant)
    parent_dir = stores.campaigns.campaign_dir(parent)
    new_cycle = fork_for_diag_sibling(stores.campaigns, tenant, "s_test", parent)

    parent_ledger = RunLedger.open(CycleDir(parent_dir))
    records = list(parent_ledger.iter())
    cut = records[-1]
    assert isinstance(cut, Decision)
    assert cut.kind is DecisionKind.FORK_CUT
    assert cut.outcome == new_cycle
    assert cut.inputs_ref == {"from_round": 0}
    assert (cut.data or {}).get("kind") == "diag_sibling"


def test_fork_for_sweep_sibling_does_not_inherit_round_candidates(
    tmp_path: Path, monkeypatch
) -> None:
    """Sweep forks must start with no candidate-cache files copied from the
    parent — round 0 has to regenerate via L1 to actually exercise the
    payload's directive. ``_fork_at_divergence`` inherits prior rounds for
    resume semantics; ``fork_for_sweep_sibling`` deliberately doesn't, and
    a regression here silently makes every fork in a batch score the
    parent's pre-existing population.
    """
    tenant = "default"
    parent = "cycle_sweepparent"
    _seed_cycle(tmp_path, tenant, parent, n_rounds=1)
    # Lay down the candidate-cache file at the runtime path (the seed helper
    # writes to a legacy ``candidates/`` subdir that the production code
    # doesn't read; sweep inheritance hits ``.runtime/cache/candidates/``).
    parent_runtime_cands = (
        tmp_path / tenant / "campaigns" / parent / ".runtime" / "cache" / "candidates"
    )
    parent_runtime_cands.mkdir(parents=True)
    (parent_runtime_cands / "round_0000.json").write_text(
        '[{"osp": {"persona": "stale parent population"}}]', encoding="utf-8"
    )
    ptr = tmp_path / ".promptpotter" / "active_session.json"
    monkeypatch.setattr(
        "promptpotter.infrastructure.store.active_pointer._ACTIVE_SESSION_PATH", ptr
    )

    stores = build_stores(tmp_path, tenant_id=tenant)
    payload = SweepPayload(reason="probe", directive="explore persona axis")
    new_cycle = fork_for_sweep_sibling(
        stores.campaigns,
        tenant_id=tenant,
        session_id="s_test",
        parent_cycle_id=parent,
        sweep_batch_id="b1abc",
        payload_source_file="01_persona_axis.json",
        payload=payload,
    )

    new_dir = stores.campaigns.campaign_dir(new_cycle)
    # Path: campaigns/{root}/sweeps/{batch}/forks/{cycle_id}
    assert new_dir.parent.name == "forks"
    assert new_dir.parent.parent.name == "b1abc"
    assert new_dir.parent.parent.parent.name == "sweeps", (
        "sweep forks nest under sweeps/{batch}/forks/, not the operator forks/ dir"
    )
    fork_runtime_cands = new_dir / ".runtime" / "cache" / "candidates" / "round_0000.json"
    assert not fork_runtime_cands.exists(), (
        "sweep fork must NOT inherit the parent's round-0 candidate cache; "
        "L1 generation would short-circuit and ignore the sweep payload's directive"
    )
    assert not (new_dir / "trials" / "trial_0000.json").exists(), (
        "sweep fork must NOT inherit any parent trials; sweep starts fresh from baseline"
    )

    index = json.loads((new_dir / "index.json").read_text(encoding="utf-8"))
    assert index["parent_cycle_id"] == parent
    assert index["fork_kind"] == "sweep_fork"
    assert index["sweep_batch_id"] == "b1abc"
    assert index["forked_from_round"] == 0
    assert index["trials"] == []


def test_fork_for_sweep_sibling_archives_payload_in_fork_cut(tmp_path: Path, monkeypatch) -> None:
    """The parent's FORK_CUT must carry sweep_payload + source_file +
    sweep_batch_id so ``_existing_fork_source_files`` can dedupe re-runs and
    downstream review can attribute the fork to the operator's hypothesis.
    """
    from promptpotter.domain.cycle_paths import CycleDir
    from promptpotter.domain.run_records import Decision, DecisionKind
    from promptpotter.infrastructure.ledger import RunLedger

    tenant = "default"
    parent = "cycle_sweepparent2"
    _seed_cycle(tmp_path, tenant, parent, n_rounds=1)
    ptr = tmp_path / ".promptpotter" / "active_session.json"
    monkeypatch.setattr(
        "promptpotter.infrastructure.store.active_pointer._ACTIVE_SESSION_PATH", ptr
    )

    stores = build_stores(tmp_path, tenant_id=tenant)
    parent_dir = stores.campaigns.campaign_dir(parent)
    payload = SweepPayload(reason="probe", directive="explore persona axis")
    new_cycle = fork_for_sweep_sibling(
        stores.campaigns,
        tenant_id=tenant,
        session_id="s_test",
        parent_cycle_id=parent,
        sweep_batch_id="b2def",
        payload_source_file="02_persona_axis.json",
        payload=payload,
    )

    parent_ledger = RunLedger.open(CycleDir(parent_dir))
    cut = list(parent_ledger.iter())[-1]
    assert isinstance(cut, Decision)
    assert cut.kind is DecisionKind.FORK_CUT
    assert cut.outcome == new_cycle
    data = cut.data or {}
    assert data.get("kind") == "sweep_fork"
    assert data.get("source_file") == "02_persona_axis.json"
    assert data.get("sweep_batch_id") == "b2def"
    assert (data.get("sweep_payload") or {}).get("directive") == "explore persona axis"


# ===========================================================================
# CampaignStore.rewind_to_round — mid-cycle rewind primitive behind --from
# ===========================================================================


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


def _seed_rewind_cycle(store, backend_id: str, cycle_id: str, rounds: int) -> None:
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
        from promptpotter.infrastructure.store import CampaignStore

        store = CampaignStore(tmp_path)
        _seed_rewind_cycle(store, "bid", "cycle_a", rounds=5)

        store.rewind_to_round("bid", "cycle_a", after_round=2)

        cycle_dir = store.campaign_dir("cycle_a")
        trials_dir = cycle_dir / "trials"
        candidates_dir = cycle_dir / ".runtime" / "cache" / "candidates"
        assert (trials_dir / "trial_0000.json").exists()
        assert (trials_dir / "trial_0001.json").exists()
        assert (trials_dir / "trial_0002.json").exists()
        assert not (trials_dir / "trial_0003.json").exists()
        assert not (trials_dir / "trial_0004.json").exists()
        assert not (candidates_dir / "round_0003.json").exists()
        assert not (candidates_dir / "round_0004.json").exists()

        archived_roots = list((cycle_dir / ".runtime" / "archived").iterdir())
        assert len(archived_roots) == 1
        archived = archived_roots[0]
        assert (archived / "trials" / "trial_0003.json").exists()
        assert (archived / "trials" / "trial_0004.json").exists()
        assert (archived / "candidates" / "round_0003.json").exists()
        assert (archived / "candidates" / "round_0004.json").exists()

    def test_rebuilds_trial_index_from_survivors(self, tmp_path):
        import pytest as _pytest

        from promptpotter.infrastructure.store import CampaignStore

        store = CampaignStore(tmp_path)
        _seed_rewind_cycle(store, "bid", "cycle_a", rounds=5)

        # Seed a best-accuracy that lives in an archived trial so the
        # rebuild has to recompute it.
        store.add_trial("bid", "cycle_a", _make_trial(4, 0.99))
        before = json.loads((store._entity_path("bid", "cycle_a")).read_text(encoding="utf-8"))
        assert before["best_accuracy"] == _pytest.approx(0.99)

        store.rewind_to_round("bid", "cycle_a", after_round=2)

        after = json.loads((store._entity_path("bid", "cycle_a")).read_text(encoding="utf-8"))
        assert after["n_trials"] == 3
        rounds_in_index = sorted(t["round"] for t in after["trials"])
        assert rounds_in_index == [0, 1, 2]
        # Best trial is round 2 (accuracy 0.3 per seed formula).
        assert after["best_trial_id"] == "round_2"
        assert after["best_accuracy"] == _pytest.approx(0.3)

    def test_resume_from_missing_round_raises(self, tmp_path):
        import pytest as _pytest

        from promptpotter.infrastructure.store import CampaignStore

        store = CampaignStore(tmp_path)
        _seed_rewind_cycle(store, "bid", "cycle_a", rounds=3)

        with _pytest.raises(LookupError, match=r"trial_0099\.json not found"):
            store.rewind_to_round("bid", "cycle_a", after_round=99)

    def test_resume_from_missing_cycle_raises(self, tmp_path):
        import pytest as _pytest

        from promptpotter.infrastructure.store import CampaignStore

        store = CampaignStore(tmp_path)
        with _pytest.raises(LookupError, match="no trials on disk"):
            store.rewind_to_round("bid", "cycle_nonexistent", after_round=0)
