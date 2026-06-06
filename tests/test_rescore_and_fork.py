"""Rescore-on-load + decision replay + ``_mint_fork`` dispatch + rewind."""

from __future__ import annotations

import json
from pathlib import Path

from promptpotter.application.optimization.resume_and_fork import (
    _mint_fork,
    replay_decisions,
)
from promptpotter.application.origin import resolve_origin_opt_search_point
from promptpotter.application.scoring.formula import compile_scorer, rescore_results
from promptpotter.application.scoring.search_point_scorer import (
    merge_with_unprocessed_priors,
)
from promptpotter.domain.cycle_paths import CycleDir
from promptpotter.domain.run_records import (
    ForkSeed,
    ForkSpec,
    ForkTrigger,
    LimitOverrides,
    ResumeCheckpointKind,
    ResumeCheckpointRecord,
)
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.store import build_stores, walk_cycle_lineage
from promptpotter.shared.identity import default_identity

# Every cycle lives inside a campaign; the tests pin one.
_CAMPAIGN = "testds__20260101-000000"


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
    base = projects_root / tenant / "campaigns" / _CAMPAIGN / "cycles" / cycle_id
    (base / "rounds").mkdir(parents=True)
    (base / ".runtime" / "cache" / "candidates").mkdir(parents=True)
    rounds_index = []
    for r in range(n_rounds):
        t = {"round_id": f"round_{r}", "round": r, "accuracy": 0.5 + 0.1 * r, "label": f"r{r}"}
        (base / "rounds" / f"round_{r:04d}.json").write_text(json.dumps(t), encoding="utf-8")
        (base / ".runtime" / "cache" / "candidates" / f"round_{r:04d}.json").write_text(
            "[]", encoding="utf-8"
        )
        rounds_index.append(t)
    (base / "index.json").write_text(
        json.dumps(
            {
                "rounds": rounds_index,
                "n_rounds": n_rounds,
                "best_round_id": f"round_{n_rounds - 1}",
                "sibling_kind": "root",
                "status": "in_progress",
            }
        ),
        encoding="utf-8",
    )
    return rounds_index


def _patch_pointer(monkeypatch, tmp_path: Path) -> Path:
    """Redirect the per-tenant pointer root into tmp_path.

    The pointer lives at ``{projects_root}/{tenant}/.workspace/active_session.json``;
    tests use tenant ``default`` and pass ``projects_root=tmp_path`` to
    ``build_stores`` already. Monkey-patching the module-level default keeps
    bare ``save_active_pointer`` calls (deep inside fork machinery) inside the
    temp tree too.
    """
    monkeypatch.setattr("promptpotter.infrastructure.store.DEFAULT_PROJECTS_ROOT", tmp_path)
    return tmp_path / "default" / ".workspace" / "active_session.json"


def _div_payload() -> ForkSpec:
    return ForkSpec(
        trigger=ForkTrigger.SCORING_DIVERGENCE, reason="scorer_mismatch", issued_by="system"
    )


def _diag_payload() -> ForkSpec:
    return ForkSpec(trigger=ForkTrigger.OPERATOR_DIAG, reason="bfs", issued_by="default")


def _sweep_payload() -> ForkSpec:
    return ForkSpec(
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

    stores = build_stores(default_identity(tenant_id=tenant), projects_root=tmp_path)
    parent_dir = stores.campaigns.cycle_dir(_CAMPAIGN, parent)
    new_cycle = _mint_fork(
        stores.campaigns,
        _CAMPAIGN,
        tenant,
        "s_test",
        parent,
        2,
        _div_payload(),
        surviving_rounds=rounds[:2],
    )

    new_dir = stores.campaigns.cycle_dir(_CAMPAIGN, new_cycle)
    assert new_dir.parent.name == "cycles"
    assert (new_dir / "rounds" / "round_0001.json").exists()
    assert not (new_dir / "rounds" / "round_0002.json").exists()

    index = json.loads((new_dir / "index.json").read_text(encoding="utf-8"))
    assert index["parent_cycle_id"] == parent
    assert index["forked_from_round"] == 2
    assert index["sibling_kind"] == "fork"
    # The lineage-read fork block is serialized from the typed ForkSpec (seed-excluded).
    assert index["fork"] == {
        "trigger": "scoring_divergence",
        "reason": "scorer_mismatch",
        "issued_by": "system",
        "from_round": None,
        "from_candidate_id": None,
        "l1_layout": None,
    }

    assert json.loads(ptr.read_text(encoding="utf-8"))["cycle_id"] == new_cycle
    assert json.loads(ptr.read_text(encoding="utf-8"))["campaign_id"] == _CAMPAIGN

    cut = list(CycleEventLog.open(CycleDir(parent_dir)).iter())[-1]
    assert isinstance(cut, ResumeCheckpointRecord)
    assert cut.kind is ResumeCheckpointKind.FORK_CUT
    assert cut.outcome == new_cycle
    assert cut.inputs_ref == {"from_round": 2}
    assert cut.data["fork"] == {
        "trigger": "scoring_divergence",
        "reason": "scorer_mismatch",
        "issued_by": "system",
        "from_round": None,
        "from_candidate_id": None,
        "l1_layout": None,
        "seed": None,
    }


def test_mint_fork_operator_diag_counted_id_clean_slate(tmp_path: Path, monkeypatch) -> None:
    """OPERATOR_DIAG: counted ``_diag_NNN`` id, no inheritance, typed FORK_CUT."""
    tenant = "default"
    parent = "cyclediagparent"
    _seed_cycle(tmp_path, tenant, parent, n_rounds=2)
    _patch_pointer(monkeypatch, tmp_path)
    stores = build_stores(default_identity(tenant_id=tenant), projects_root=tmp_path)

    sib1 = _mint_fork(stores.campaigns, _CAMPAIGN, tenant, "s_test", parent, 0, _diag_payload())
    sib2 = _mint_fork(stores.campaigns, _CAMPAIGN, tenant, "s_test", parent, 0, _diag_payload())

    assert sib1 == f"{parent}_diag_001"
    assert sib2 == f"{parent}_diag_002"

    sib1_dir = stores.campaigns.cycle_dir(_CAMPAIGN, sib1)
    assert sib1_dir.parent.name == "cycles"
    sib1_index = json.loads((sib1_dir / "index.json").read_text(encoding="utf-8"))
    assert sib1_index["rounds"] == []
    assert sib1_index["sibling_kind"] == "diag"
    assert sib1_index["fork"] == {
        "trigger": "operator_diag",
        "reason": "bfs",
        "issued_by": "default",
        "from_round": None,
        "from_candidate_id": None,
        "l1_layout": None,
    }


def test_mint_fork_operator_sweep_no_inherit_and_dedup_fields(tmp_path: Path, monkeypatch) -> None:
    """OPERATOR_SWEEP: clean-slate, dedup fields at FORK_CUT data top level, typed payload."""
    tenant = "default"
    parent = "cyclesweepparent"
    _seed_cycle(tmp_path, tenant, parent, n_rounds=1)
    _patch_pointer(monkeypatch, tmp_path)
    stores = build_stores(default_identity(tenant_id=tenant), projects_root=tmp_path)
    parent_dir = stores.campaigns.cycle_dir(_CAMPAIGN, parent)

    new_cycle = _mint_fork(
        stores.campaigns,
        _CAMPAIGN,
        tenant,
        "s_test",
        parent,
        0,
        _sweep_payload(),
        sweep_batch_id="b1abc",
        sweep_source_file="01_persona.json",
    )

    new_dir = stores.campaigns.cycle_dir(_CAMPAIGN, new_cycle)
    assert new_dir.parent.name == "cycles"
    assert not (new_dir / ".runtime" / "cache" / "candidates" / "round_0000.json").exists()

    index = json.loads((new_dir / "index.json").read_text(encoding="utf-8"))
    assert index["sweep_batch_id"] == "b1abc"
    assert index["sibling_kind"] == "sweep"
    assert index["fork"] == {
        "trigger": "operator_sweep",
        "reason": "probe persona",
        "issued_by": "default",
        "from_round": None,
        "from_candidate_id": None,
        "l1_layout": {"task_intent": ["task_context"]},
    }

    # source_file + sweep_batch_id stay at data top level so
    # existing_fork_source_files dedup keeps working without parsing data.fork.
    cut = list(CycleEventLog.open(CycleDir(parent_dir)).iter())[-1]
    assert isinstance(cut, ResumeCheckpointRecord)
    assert cut.data["source_file"] == "01_persona.json"
    assert cut.data["sweep_batch_id"] == "b1abc"
    assert cut.data["fork"]["trigger"] == "operator_sweep"
    assert cut.data["fork"]["l1_layout"] == {"task_intent": ["task_context"]}


def test_mint_fork_operator_steered_writes_seed_and_typed_fork_block(
    tmp_path: Path, monkeypatch
) -> None:
    """OPERATOR_STEERED: clean ``_fork_`` offshoot, edited seed → ``.overrides/seed.json``,
    typed fork block carries from_candidate_id but NOT the heavy seed."""
    tenant = "default"
    parent = "cyclesteerparent"
    _seed_cycle(tmp_path, tenant, parent, n_rounds=3)
    _patch_pointer(monkeypatch, tmp_path)
    stores = build_stores(default_identity(tenant_id=tenant), projects_root=tmp_path)

    spec = ForkSpec(
        trigger=ForkTrigger.OPERATOR_STEERED,
        reason="operator steered",
        issued_by="nieena",
        from_round=2,
        from_candidate_id="cand_x",
        seed=ForkSeed(
            origin_prompt_fields={"instruction": "edited"},
            pipeline_overlay={"llm_only": {"reasoning_effort": "high"}},
            limit_overrides=LimitOverrides(max_rounds=2),
        ),
    )
    new_cycle = _mint_fork(stores.campaigns, _CAMPAIGN, tenant, "s_test", parent, 0, spec)

    assert "_fork_" in new_cycle
    new_dir = stores.campaigns.cycle_dir(_CAMPAIGN, new_cycle)
    index = json.loads((new_dir / "index.json").read_text(encoding="utf-8"))
    assert index["sibling_kind"] == "fork"
    assert index["rounds"] == []  # clean offshoot — no parent-round copy
    assert index["fork"]["trigger"] == "operator_steered"
    assert index["fork"]["from_candidate_id"] == "cand_x"
    # The steering operator's identity round-trips to disk — this is the value
    # the lineage read surfaces (suppressing only the UNATTRIBUTED_OPERATOR
    # default) as the "edited by {name}" badge.
    assert index["fork"]["issued_by"] == "nieena"
    assert "seed" not in index["fork"]  # the seed has its own read-once home

    # The seed rides ``.overrides/seed.json`` (bootstrap-read), round-trips typed.
    read_back = stores.campaigns.read_fork_seed(_CAMPAIGN, new_cycle)
    assert read_back is not None
    assert read_back.origin_prompt_fields == {"instruction": "edited"}
    assert read_back.limit_overrides.max_rounds == 2


def test_resolve_origin_fork_seed_wins_over_experiment_prompt() -> None:
    """A fork seed's ``origin_prompt_fields`` becomes the origin OSP — beating the
    experiment/dataset sources — and stamps ``source='fork_seed'`` lineage.
    No seed → falls through to the experiment-registry prompt (``source='origin'``)."""
    experiment_extract = {"dependencies": {"prompts": {"llm_only": {"template": "dataset origin"}}}}

    steered = resolve_origin_opt_search_point(
        experiment_extract,
        prompt_node_names=["llm_only"],
        fork_seed=ForkSeed(origin_prompt_fields={"instruction": "edited by operator"}),
    )
    assert steered.instruction == "edited by operator"
    assert steered.lineage.source == "fork_seed"

    plain = resolve_origin_opt_search_point(
        experiment_extract, prompt_node_names=["llm_only"], fork_seed=None
    )
    assert plain.instruction == "dataset origin"
    assert plain.lineage.source == "origin"


def test_inherit_fork_origin_unmodified_inherits_else_rescores(tmp_path: Path) -> None:
    """A no-modification operator fork inherits its branch-point candidate's RECORDED
    accuracy as C0 (no re-score under a nondeterministic backend); an edited prompt
    renders differently and falls back to ``None`` → the caller re-scores."""
    from types import SimpleNamespace

    from promptpotter.application.origin import (
        resolve_origin_opt_search_point,
        try_inherit_fork_origin,
    )
    from promptpotter.domain.opt_search_point import OptSearchPoint

    tenant = "default"
    stores = build_stores(default_identity(tenant_id=tenant), projects_root=tmp_path)
    parent = "cycle_inherit_parent"
    fork = "cycle_inherit_parent_fork_abc123"
    prompt = {"instruction": "do the thing", "persona": "you are precise"}

    stores.campaigns.create(_CAMPAIGN, parent, {"sibling_kind": "root"})
    stores.campaigns.save_round_file(
        _CAMPAIGN,
        parent,
        {
            "round_id": "round_1",
            "round": 1,
            "accuracy": 0.4,
            "candidate_scores": [
                {"candidate_id": "c1", "prompt_fields": prompt, "accuracy": 0.2},
            ],
        },
    )
    stores.campaigns.create(
        _CAMPAIGN,
        fork,
        {
            "sibling_kind": "fork",
            "parent_cycle_id": parent,
            "fork": {
                "trigger": "operator_steered",
                "from_round": 1,
                "from_candidate_id": "c1",
            },
        },
    )

    session = SimpleNamespace(
        store=stores,
        campaign_id=_CAMPAIGN,
        state=SimpleNamespace(cycle_id=fork),
        experiment_extract={},
        dataset_config_dir=None,
    )

    # Resolve the origin OSP exactly as ``establish_campaign_origin`` does (fork-seed wins).
    unmodified_seed = ForkSeed(origin_prompt_fields=dict(prompt))
    unmodified_osp = resolve_origin_opt_search_point({}, fork_seed=unmodified_seed)
    inherited = try_inherit_fork_origin(
        session,  # type: ignore[arg-type]
        unmodified_seed,
        origin_osp=unmodified_osp,
    )
    assert inherited is not None
    assert inherited.origin_acc == 0.2  # the branch point, NOT a re-rolled number
    # C0 carries the OSP object, so the inherited origin keeps its fork_seed lineage.
    assert isinstance(inherited.origin_ps, OptSearchPoint)
    assert inherited.origin_ps.lineage.source == "fork_seed"

    edited_seed = ForkSeed(origin_prompt_fields={**prompt, "instruction": "do it differently"})
    edited = try_inherit_fork_origin(
        session,  # type: ignore[arg-type]
        edited_seed,
        origin_osp=resolve_origin_opt_search_point({}, fork_seed=edited_seed),
    )
    assert edited is None


def test_apply_limit_overrides_snapshots_fork_limits_leaving_parent_intact() -> None:
    """A fork's reconciled ``LimitOverrides`` land on a fresh config snapshot —
    absolute values, an absent knob inherits the parent — and the seed's
    ``spend_budget_usd`` becomes the effective cap. The parent config is untouched."""
    from promptpotter.application.config import CampaignConfig
    from promptpotter.application.runner.entry import _apply_limit_overrides

    parent = CampaignConfig(
        optimization={
            "max_rounds": 6,
            "l1_patience": 3,
            "improvement_threshold": 0.01,
            "degradation_threshold": 0.4,
        }
    )
    forked, spend = _apply_limit_overrides(
        parent,
        spend_budget_usd=10.0,
        limits=LimitOverrides(max_rounds=2, spend_budget_usd=4.0),
    )
    assert forked.optimization.max_rounds == 2  # override applied
    assert forked.optimization.l1_patience == 3  # absent knob inherited
    assert spend == 4.0  # seed spend reconciled as the effective cap
    assert parent.optimization.max_rounds == 6  # parent snapshot untouched

    # Empty overrides → identity passthrough (parent config + original spend kept).
    same, spend2 = _apply_limit_overrides(parent, 7.0, LimitOverrides())
    assert same is parent
    assert spend2 == 7.0


def test_walk_cycle_lineage_walks_parent_chain(tmp_path: Path, monkeypatch) -> None:
    """Lineage walker returns ``[root, ..., leaf]`` via parent_cycle_id chain."""
    tenant = "default"
    parent = "cycle_lineage_root"
    _seed_cycle(tmp_path, tenant, parent, n_rounds=2)
    _patch_pointer(monkeypatch, tmp_path)
    stores = build_stores(default_identity(tenant_id=tenant), projects_root=tmp_path)

    fork = _mint_fork(
        stores.campaigns,
        _CAMPAIGN,
        tenant,
        "s_test",
        parent,
        1,
        _div_payload(),
        surviving_rounds=[{"round_id": "round_0", "round": 0, "accuracy": 0.5, "label": "r0"}],
    )
    sweep = _mint_fork(
        stores.campaigns,
        _CAMPAIGN,
        tenant,
        "s_test",
        fork,
        0,
        _sweep_payload(),
        sweep_batch_id="b1abc",
        sweep_source_file="x.json",
    )

    tenant_root = tmp_path / tenant
    assert walk_cycle_lineage(tenant_root, _CAMPAIGN, parent) == [parent]
    assert walk_cycle_lineage(tenant_root, _CAMPAIGN, fork) == [parent, fork]
    assert walk_cycle_lineage(tenant_root, _CAMPAIGN, sweep) == [parent, fork, sweep]


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


def _seed_rewind_cycle(store, campaign_id: str, cycle_id: str, rounds: int) -> None:
    store.create(campaign_id, cycle_id, {"type": "optimization_loop", "origin_accuracy": 0.0})
    for r in range(rounds):
        store.save_round_file(campaign_id, cycle_id, _make_round_data(r, 0.1 * (r + 1)))
        # Simulate round-level candidate checkpoints for the same round.
        store.save_round_candidates(campaign_id, cycle_id, r, [{"round": r, "id": f"cand_{r}"}])
    # Mint a minimal ledger so the ledger-aware ``rewind_to_round`` admissibility
    # check sees each seeded round as completed.
    cycle_dir = store.cycle_dir(campaign_id, cycle_id)
    runtime_dir = cycle_dir / ".runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    ledger_path = runtime_dir / "ledger.jsonl"
    lines: list[str] = []
    for r in range(rounds):
        if r == 0:
            lines.append(
                json.dumps({"record_type": "phase", "phase": "origin", "event": "exit", "round": 0})
            )
        else:
            lines.append(
                json.dumps(
                    {"record_type": "phase", "phase": "round", "event": "complete", "round": r}
                )
            )
    ledger_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


class TestRewindToRound:
    def test_archives_later_round_and_candidate_files(self, tmp_path):
        from promptpotter.infrastructure.store import CampaignStore

        store = CampaignStore(tmp_path)
        _seed_rewind_cycle(store, _CAMPAIGN, "cycle_a", rounds=5)

        store.rewind_to_round(_CAMPAIGN, "cycle_a", after_round=2)

        cycle_dir = store.cycle_dir(_CAMPAIGN, "cycle_a")
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
        _seed_rewind_cycle(store, _CAMPAIGN, "cycle_a", rounds=5)

        # Seed a best-accuracy that lives in an archived round_data so the
        # rebuild has to recompute it.
        store.save_round_file(_CAMPAIGN, "cycle_a", _make_round_data(4, 0.99))
        before = json.loads(store._index_path(_CAMPAIGN, "cycle_a").read_text(encoding="utf-8"))
        assert before["best_accuracy"] == _pytest.approx(0.99)

        store.rewind_to_round(_CAMPAIGN, "cycle_a", after_round=2)

        after = json.loads(store._index_path(_CAMPAIGN, "cycle_a").read_text(encoding="utf-8"))
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
        _seed_rewind_cycle(store, _CAMPAIGN, "cycle_a", rounds=3)

        # ``rewind_to_round`` consults the ledger first for admissibility.
        with _pytest.raises(LookupError, match=r"ledger only has completed rounds 0\.\.2"):
            store.rewind_to_round(_CAMPAIGN, "cycle_a", after_round=99)

    def test_resume_from_missing_cycle_raises(self, tmp_path):
        import pytest as _pytest

        from promptpotter.infrastructure.store import CampaignStore

        store = CampaignStore(tmp_path)
        # A nonexistent cycle has no ledger on disk — the ledger pre-check
        # raises before the public ``rounds/`` tree is consulted.
        with _pytest.raises(LookupError, match="no ledger on disk"):
            store.rewind_to_round(_CAMPAIGN, "cycle_nonexistent", after_round=0)
