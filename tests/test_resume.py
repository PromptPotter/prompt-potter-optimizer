"""C4 — Resume / mutation safety.

The guarantees that make a killed-and-restarted run, a rewind, or an operator
fork safe: rescore-on-load accumulates without corrupting prior fitness,
decision-replay flags divergence when a rescore flips a recorded outcome,
``_mint_fork`` dispatches each trigger to the right lineage shape, the
unprocessed-prior merge never shrinks an already-fuller archive, and
``rewind_to_round`` archives-not-deletes the truncated tail. ``DiffScope``
closes the loop: a config change either resumes in place (policy-only) or forks
(data-affecting), and an unclassified knob must default to forking.

Sections:
  1. Rescore-on-load + decision replay.
  2. ``_mint_fork`` — one lineage shape per trigger.
  3. Origin inheritance + limit-override reconciliation + lineage walk.
  4. Unprocessed-prior merge — partial-run archive preservation.
  5. ``rewind_to_round`` — archive-not-delete the truncated tail.
  6. ``DiffScope`` — fork-vs-resume classifier.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from _factories import make_round, seed_campaign_cycle

from promptpotter.application.config import CampaignConfig, DiffScope
from promptpotter.application.optimization.resume_and_fork import _mint_fork, replay_decisions
from promptpotter.application.origin import resolve_origin_opt_search_point
from promptpotter.application.scoring.formula import compile_scorer, rescore_results
from promptpotter.application.scoring.search_point_scorer import merge_with_unprocessed_priors
from promptpotter.domain.cycle_paths import CycleDir
from promptpotter.domain.run_records import (
    CycleSeed,
    ForkSpec,
    ForkTrigger,
    LimitOverrides,
    ResumeCheckpointKind,
    ResumeCheckpointRecord,
)
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.store import Stores, walk_cycle_lineage
from promptpotter.shared.errors import BadRequestError, NotFoundError

# Every cycle lives inside a campaign; the foundation factory's default id.
_CAMPAIGN = "testds__20260101-000000"


# ===========================================================================
# 1. Rescore-on-load + decision replay
# ===========================================================================


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


def test_next_resume_round_keys_on_round_number_not_count() -> None:
    """Resume's next-round is highest persisted round NUMBER + 1, never ``len()``.
    Origin is round 0 in ``index.json::rounds`` now, so counting entries over-counts
    by one and the loop would skip a round (round_0000, round_0002, no round_0001)."""
    from promptpotter.application.bootstrap.scoring_context import next_resume_round

    assert next_resume_round([]) == 1  # nothing persisted → fresh
    assert next_resume_round([make_round(0)]) == 1  # origin only → first L1 round
    assert next_resume_round([make_round(0), make_round(1)]) == 2  # NOT 3
    assert next_resume_round([make_round(0), make_round(1), make_round(2)]) == 3


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


# ===========================================================================
# 2. ``_mint_fork`` — one lineage shape per trigger
# ===========================================================================


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
    patch_pointer_root: tuple[Stores, object],
) -> None:
    """SCORING_DIVERGENCE: inherit rounds < R, retarget pointer, FORK_CUT carries typed payload."""
    stores, ptr = patch_pointer_root
    tenant = stores.tenant_id
    parent = "cycle_div_parent"
    seed_campaign_cycle(
        stores.base_dir,
        cycle_id=parent,
        n_rounds=4,
        with_candidates=True,
        index={"best_round_id": "round_3", "status": "in_progress"},
    )
    parent_dir = stores.campaigns.cycle_dir(_CAMPAIGN, parent)
    new_cycle = _mint_fork(
        stores.campaigns,
        _CAMPAIGN,
        tenant,
        "s_test",
        parent,
        2,
        _div_payload(),
        surviving_rounds=[make_round(0), make_round(1)],
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


def test_mint_fork_operator_diag_counted_id_clean_slate(
    patch_pointer_root: tuple[Stores, object],
) -> None:
    """OPERATOR_DIAG: counted ``_diag_NNN`` id, no inheritance, typed FORK_CUT."""
    stores, _ = patch_pointer_root
    tenant = stores.tenant_id
    parent = "cyclediagparent"
    seed_campaign_cycle(stores.base_dir, cycle_id=parent, n_rounds=2, with_candidates=True)

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


def test_mint_fork_operator_sweep_no_inherit_and_dedup_fields(
    patch_pointer_root: tuple[Stores, object],
) -> None:
    """OPERATOR_SWEEP: clean-slate, dedup fields at FORK_CUT data top level, typed payload."""
    stores, _ = patch_pointer_root
    tenant = stores.tenant_id
    parent = "cyclesweepparent"
    seed_campaign_cycle(stores.base_dir, cycle_id=parent, n_rounds=1, with_candidates=True)
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
    patch_pointer_root: tuple[Stores, object],
) -> None:
    """OPERATOR_STEERED: clean ``_fork_`` offshoot, edited seed → ``.overrides/seed.json``,
    typed fork block carries from_candidate_id but NOT the heavy seed."""
    stores, _ = patch_pointer_root
    tenant = stores.tenant_id
    parent = "cyclesteerparent"
    seed_campaign_cycle(stores.base_dir, cycle_id=parent, n_rounds=3, with_candidates=True)

    spec = ForkSpec(
        trigger=ForkTrigger.OPERATOR_STEERED,
        reason="operator steered",
        issued_by="nieena",
        from_round=2,
        from_candidate_id="cand_x",
        seed=CycleSeed(
            origin_prompt_fields={"instruction": "edited"},
            pipeline_overlay={"llm_only": {"reasoning_effort": "high"}},
            limit_overrides=LimitOverrides(max_rounds=2),
            origin_source="fork_seed",
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
    read_back = stores.campaigns.read_cycle_seed(_CAMPAIGN, new_cycle)
    assert read_back is not None
    assert read_back.origin_prompt_fields == {"instruction": "edited"}
    assert read_back.limit_overrides.max_rounds == 2


# ===========================================================================
# 3. Origin inheritance + limit reconciliation + lineage walk
# ===========================================================================


def test_resolve_origin_seed_wins_over_experiment_prompt() -> None:
    """A seed's ``origin_prompt_fields`` becomes the origin OSP — beating the
    experiment/dataset sources — and stamps the C0 lineage from its
    ``origin_source`` (data-driven, not a hardcoded ``'fork_seed'``): an
    operator-steered fork stamps ``fork_seed``, a campaign-from-origin mint stamps
    ``campaign_origin``. No seed → falls through to the experiment-registry prompt
    (``source='origin'``)."""
    experiment_extract = {"dependencies": {"prompts": {"llm_only": {"template": "dataset origin"}}}}

    steered = resolve_origin_opt_search_point(
        experiment_extract,
        prompt_node_names=["llm_only"],
        seed=CycleSeed(
            origin_prompt_fields={"instruction": "edited by operator"},
            origin_source="fork_seed",
        ),
    )
    assert steered.instruction == "edited by operator"
    assert steered.lineage.source == "fork_seed"

    from_origin = resolve_origin_opt_search_point(
        experiment_extract,
        prompt_node_names=["llm_only"],
        seed=CycleSeed(
            origin_prompt_fields={"instruction": "a chosen prior origin"},
            origin_source="campaign_origin",
        ),
    )
    assert from_origin.instruction == "a chosen prior origin"
    assert from_origin.lineage.source == "campaign_origin"

    plain = resolve_origin_opt_search_point(
        experiment_extract, prompt_node_names=["llm_only"], seed=None
    )
    assert plain.instruction == "dataset origin"
    assert plain.lineage.source == "origin"


def test_inherit_fork_origin_unmodified_inherits_else_rescores(built_stores: Stores) -> None:
    """A no-modification operator fork inherits its branch-point candidate's RECORDED
    accuracy as C0 (no re-score under a nondeterministic backend); an edited prompt
    renders differently and falls back to ``None`` → the caller re-scores."""
    from types import SimpleNamespace

    from promptpotter.application.origin import (
        resolve_origin_opt_search_point,
        try_inherit_fork_origin,
    )
    from promptpotter.domain.opt_search_point import OptSearchPoint

    stores = built_stores
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
    unmodified_seed = CycleSeed(origin_prompt_fields=dict(prompt), origin_source="fork_seed")
    unmodified_osp = resolve_origin_opt_search_point({}, seed=unmodified_seed)
    inherited = try_inherit_fork_origin(
        session,  # type: ignore[arg-type]
        unmodified_seed,
        resolved_origin=unmodified_osp,
    )
    assert inherited is not None
    assert inherited.origin_acc == 0.2  # the branch point, NOT a re-rolled number
    # C0 carries the OSP object, so the inherited origin keeps its fork_seed lineage.
    assert isinstance(inherited.resolved_origin, OptSearchPoint)
    assert inherited.resolved_origin.lineage.source == "fork_seed"

    edited_seed = CycleSeed(
        origin_prompt_fields={**prompt, "instruction": "do it differently"},
        origin_source="fork_seed",
    )
    edited = try_inherit_fork_origin(
        session,  # type: ignore[arg-type]
        edited_seed,
        resolved_origin=resolve_origin_opt_search_point({}, seed=edited_seed),
    )
    assert edited is None


def test_apply_limit_overrides_snapshots_fork_limits_leaving_parent_intact() -> None:
    """A fork's reconciled ``LimitOverrides`` land on a fresh config snapshot —
    absolute values, an absent knob inherits the parent — and the seed's
    ``spend_budget_usd`` becomes the effective cap. The parent config is untouched."""
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


def test_walk_cycle_lineage_walks_parent_chain(patch_pointer_root: tuple[Stores, object]) -> None:
    """Lineage walker returns ``[root, ..., leaf]`` via parent_cycle_id chain."""
    stores, _ = patch_pointer_root
    tenant = stores.tenant_id
    parent = "cycle_lineage_root"
    seed_campaign_cycle(stores.base_dir, cycle_id=parent, n_rounds=2, with_candidates=True)

    fork = _mint_fork(
        stores.campaigns,
        _CAMPAIGN,
        tenant,
        "s_test",
        parent,
        1,
        _div_payload(),
        surviving_rounds=[make_round(0)],
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

    tenant_root = stores.base_dir
    assert walk_cycle_lineage(tenant_root, _CAMPAIGN, parent) == [parent]
    assert walk_cycle_lineage(tenant_root, _CAMPAIGN, fork) == [parent, fork]
    assert walk_cycle_lineage(tenant_root, _CAMPAIGN, sweep) == [parent, fork, sweep]


# ===========================================================================
# 4. Unprocessed-prior merge — partial-run archive preservation
# ===========================================================================


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
    overwrite-on-save ``_persist_fresh`` would grind down the cache file each Ctrl+C.
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
# 5. ``rewind_to_round`` — archive-not-delete the truncated tail
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
    """Seed via the store API + a minimal ledger whose phase records mark each
    round completed (the rewind admissibility pre-check reads the ledger)."""
    store.create(campaign_id, cycle_id, {"type": "optimization_loop", "origin_accuracy": 0.0})
    for r in range(rounds):
        store.save_round_file(campaign_id, cycle_id, _make_round_data(r, 0.1 * (r + 1)))
        store.save_round_candidates(campaign_id, cycle_id, r, [{"round": r, "id": f"cand_{r}"}])
    cycle_dir = store.cycle_dir(campaign_id, cycle_id)
    runtime_dir = cycle_dir / ".runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
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
    (runtime_dir / "ledger.jsonl").write_text(
        "\n".join(lines) + ("\n" if lines else ""), encoding="utf-8"
    )


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
        from promptpotter.infrastructure.store import CampaignStore

        store = CampaignStore(tmp_path)
        _seed_rewind_cycle(store, _CAMPAIGN, "cycle_a", rounds=5)

        # Seed a best-accuracy that lives in an archived round_data so the
        # rebuild has to recompute it.
        store.save_round_file(_CAMPAIGN, "cycle_a", _make_round_data(4, 0.99))
        before = json.loads(store._index_path(_CAMPAIGN, "cycle_a").read_text(encoding="utf-8"))
        assert before["best_accuracy"] == pytest.approx(0.99)

        store.rewind_to_round(_CAMPAIGN, "cycle_a", after_round=2)

        after = json.loads(store._index_path(_CAMPAIGN, "cycle_a").read_text(encoding="utf-8"))
        assert after["n_rounds"] == 3
        rounds_in_index = sorted(t["round"] for t in after["rounds"])
        assert rounds_in_index == [0, 1, 2]
        # Best round_data is round 2 (accuracy 0.3 per seed formula).
        assert after["best_round_id"] == "round_2"
        assert after["best_accuracy"] == pytest.approx(0.3)

    def test_resume_from_missing_round_raises(self, tmp_path):
        from promptpotter.infrastructure.store import CampaignStore

        store = CampaignStore(tmp_path)
        _seed_rewind_cycle(store, _CAMPAIGN, "cycle_a", rounds=3)

        # ``rewind_to_round`` consults the ledger first for admissibility.
        with pytest.raises(BadRequestError, match=r"ledger only has completed rounds 0\.\.2"):
            store.rewind_to_round(_CAMPAIGN, "cycle_a", after_round=99)

    def test_resume_from_missing_cycle_raises(self, tmp_path):
        from promptpotter.infrastructure.store import CampaignStore

        store = CampaignStore(tmp_path)
        # A nonexistent cycle has no ledger on disk — the ledger pre-check
        # raises before the public ``rounds/`` tree is consulted.
        with pytest.raises(NotFoundError, match="no ledger on disk"):
            store.rewind_to_round(_CAMPAIGN, "cycle_nonexistent", after_round=0)


# ===========================================================================
# 6. ``DiffScope`` — fork-vs-resume classifier
# ===========================================================================


def _cfg(**opt_overrides: object) -> CampaignConfig:
    opt = {"improvement_threshold": 0.01, "degradation_threshold": 0.4, **opt_overrides}
    return CampaignConfig.model_validate({"optimization": opt})


def test_classify_diff_scopes() -> None:
    """Per-scenario classifier contract: policy-only changes don't fork."""
    base = _cfg().model_dump(mode="json")

    assert _cfg().classify_diff_against(base) == (DiffScope.NONE, [])

    # Policy-only: PoBB epsilon change (the operator's exact scenario).
    scope, diffed = _cfg(pobb_epsilon=0.10).classify_diff_against(base)
    assert scope is DiffScope.POLICY_ONLY
    assert diffed == ["optimization.pobb_epsilon"]

    # Data-affecting: scoring formula change rescores past fitness.
    scope, _ = CampaignConfig.model_validate(
        {
            "optimization": {"improvement_threshold": 0.01, "degradation_threshold": 0.4},
            "scoring": "score(top1_hit)",
        }
    ).classify_diff_against(base)
    assert scope is DiffScope.DATA_AFFECTING

    # Mixed: data wins (forks even if a policy field also moved).
    mixed_base = CampaignConfig.model_validate(
        {
            "optimization": {"improvement_threshold": 0.01, "degradation_threshold": 0.4},
            "scoring": "old",
        }
    ).model_dump(mode="json")
    mixed = CampaignConfig.model_validate(
        {
            "optimization": {
                "improvement_threshold": 0.01,
                "degradation_threshold": 0.4,
                "pobb_epsilon": 0.10,
            },
            "scoring": "new",
        }
    )
    scope, _ = mixed.classify_diff_against(mixed_base)
    assert scope is DiffScope.DATA_AFFECTING


def test_classify_unknown_path_defaults_to_data_affecting(caplog) -> None:
    """A path missing from ``_FIELD_SCOPES`` classifies as DATA_AFFECTING with a
    warning — adding a knob without an entry can't silently downgrade resume safety."""
    base = _cfg().model_dump(mode="json")
    base.setdefault("optimization", {})["unknown_new_knob"] = "old"
    with caplog.at_level("WARNING"):
        scope, _ = _cfg().classify_diff_against(base)
    assert scope is DiffScope.DATA_AFFECTING
    assert "unclassified config path" in caplog.text


# ===========================================================================
# 7. Cumulative pool merge + zero-signal sample filter
# ===========================================================================


def test_merge_into_cumulative_preserves_prior_on_untouched_samples() -> None:
    """Subset-measured winner must not shrink the cumulative pool — prior samples are preserved."""
    from promptpotter.application.optimization.cycle import _merge_into_cumulative

    prior = [{"sample_id": i, "fitness": 1.0 if i < 10 else 0.0, "hit": i < 10} for i in range(20)]
    winner_hits = {10, 12, 15}
    winner = [
        {"sample_id": sid, "fitness": 1.0 if sid in winner_hits else 0.0, "hit": sid in winner_hits}
        for sid in range(10, 18)
    ]
    merged = _merge_into_cumulative(prior, winner)
    by_sid = {r["sample_id"]: r for r in merged}

    assert set(by_sid.keys()) == set(range(20))
    assert by_sid[10]["hit"] is True
    assert by_sid[11]["hit"] is False
    assert by_sid[19]["hit"] is False
    assert all(by_sid[i]["hit"] is True for i in range(10))
    assert _merge_into_cumulative(prior, []) == prior


def test_dead_queries_respects_min_observations() -> None:
    """``SampleIndex.dead`` physically prunes always-hit/always-miss queries once
    enough observations accumulate; ``include_always_hit=False`` keeps the hits."""
    from promptpotter.application.intelligence.indexes import AxisIndex, SampleIndex
    from promptpotter.domain.sample import Sample

    def _seed_axes(hits: dict[str, list[bool]]) -> AxisIndex:
        idx = SampleIndex()
        for i, (query, seq) in enumerate(hits.items()):
            sample = Sample(id=i, query=query, ground_truth=f"gt_{i}")
            idx.register(sample)
            idx._hits[i] = list(seq)
        return AxisIndex(sample_index=idx)

    axes = _seed_axes(
        {
            "fresh_miss": [False] * 3,
            "proven_miss": [False] * 6,
            "proven_hit": [True] * 6,
            "variable": [True, False, True, False, True, True],
        }
    )

    dead = axes.sample_index.dead(min_observations=5)
    dead_qs = {r.query for r in dead}
    assert dead_qs == {"proven_miss", "proven_hit"}

    miss_only = axes.sample_index.dead(min_observations=5, include_always_hit=False)
    assert {r.query for r in miss_only} == {"proven_miss"}


def test_live_dashboard_for_session_recovers_round_after_interrupt(tmp_path: Path) -> None:
    """Resume invariant: when ``rounds/round_NNNN.json`` checkpoints exist on
    disk but the prior ``dashboard.json::round`` is stale (e.g. zeroed by an
    earlier re-init), ``for_session`` must restore ``round`` to the highest
    checkpoint — otherwise the webapp polls ``rounds/round_0000.json`` forever
    on a cycle that has rounds 1–3 completed.
    """
    from promptpotter.infrastructure.projections import LiveDashboardView

    project_root = tmp_path / "default"
    campaign_id = "ds__abc123abc123"
    cycle_id = "cycle_abc123abc123"
    cycle_dir = project_root / "campaigns" / campaign_id / "cycles" / cycle_id
    rounds_dir = cycle_dir / "rounds"
    rounds_dir.mkdir(parents=True)
    for n in (1, 2, 3):
        (rounds_dir / f"round_{n:04d}.json").write_text("{}", encoding="utf-8")
    # dashboard.json is per-session — in the session's root cycle dir.
    (cycle_dir / "dashboard.json").write_text(
        json.dumps({"state": "init", "round": 0, "best": 0.5}),
        encoding="utf-8",
    )

    proj = LiveDashboardView.for_session(
        cycle_id=cycle_id,
        project_root=str(project_root),
        session_id="s_test",
        campaign_id=campaign_id,
        l1_patience=3,
        n_variants=5,
        sp_budget_ttest=20,
    )
    assert proj is not None
    assert proj.state.round == 3
