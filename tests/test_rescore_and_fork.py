"""Data-vs-policy separation: rescore-on-load, decision replay, and fork."""

from __future__ import annotations

import json
from pathlib import Path

from promptpotter.application.optimization.cycle import (
    REPLAYERS,
    _fork_at_divergence,
    _fork_for_diag_sibling,
    replay_decisions,
)
from promptpotter.application.scoring.formula import compile_scorer, rescore_results
from promptpotter.application.scoring.search_point_scorer import (
    merge_with_unprocessed_priors,
)
from promptpotter.infrastructure.store.stores import build_stores, root_cycle_id


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
    monkeypatch.setattr("promptpotter.infrastructure.store.stores._ACTIVE_SESSION_PATH", ptr)

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


def test_merge_preserves_pipeline_data_timings() -> None:
    """Merged-in priors must keep their original raw trace fields — timings,
    pipeline_data — so the archive stays the source of truth. Only the active
    scorer's projection is re-derived."""
    formula = "exact_match(predicted, ground_truth)"
    prior = _prior("q1", predicted="**hit**", gt="hit")
    merged = merge_with_unprocessed_priors(
        [],
        prior_results={"q1": prior},
        dataset_queries={"q1"},
        evicted_priors={},
        scorer=compile_scorer(formula),
        scorer_id="x",
        scorer_formula=formula,
    )
    assert len(merged) == 1
    entry = merged[0]
    assert entry["pipeline_data"]["total_time"] == 1.5  # untouched
    assert entry.get("hit") is True  # rescored under active scorer
    # Mutating the merged copy must not poison the prior_results source.
    assert prior.get("hit") is None or prior["hit"] is False


def test_root_cycle_id_recognizes_diag_separator() -> None:
    """``root_cycle_id`` must split on both ``_fork_`` and ``_diag_`` so diag
    siblings nest under the family root's ``forks/`` dir like fork siblings."""
    assert root_cycle_id("cycle_abc123") == "cycle_abc123"
    assert root_cycle_id("cycle_abc123_fork_deadbeef") == "cycle_abc123"
    assert root_cycle_id("cycle_abc123_diag_001") == "cycle_abc123"


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
    monkeypatch.setattr("promptpotter.infrastructure.store.stores._ACTIVE_SESSION_PATH", ptr)

    stores = build_stores(tmp_path, tenant_id=tenant)
    sib1 = _fork_for_diag_sibling(stores.campaigns, tenant, "s_test", parent)
    sib2 = _fork_for_diag_sibling(stores.campaigns, tenant, "s_test", parent)

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
    monkeypatch.setattr("promptpotter.infrastructure.store.stores._ACTIVE_SESSION_PATH", ptr)

    stores = build_stores(tmp_path, tenant_id=tenant)
    parent_dir = stores.campaigns.campaign_dir(parent)
    new_cycle = _fork_for_diag_sibling(stores.campaigns, tenant, "s_test", parent)

    parent_ledger = RunLedger.open(CycleDir(parent_dir))
    records = list(parent_ledger.iter())
    cut = records[-1]
    assert isinstance(cut, Decision)
    assert cut.kind is DecisionKind.FORK_CUT
    assert cut.outcome == new_cycle
    assert cut.inputs_ref == {"from_round": 0}
    assert (cut.data or {}).get("kind") == "diag_sibling"
