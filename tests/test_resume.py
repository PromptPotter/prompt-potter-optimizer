"""Resume / fork — the data-integrity core.

The silent-harm slice of resume safety: a rescore that corrupts prior fitness,
a replay that misses a flipped outcome, a fork that inherits the wrong origin,
or an aborted-run merge that silently shrinks an already-fuller archive. A
killed-and-restarted run produces NO error in these cases — it just quietly
carries wrong numbers forward. The loud failures (rewind mechanics, fork id
shapes, lineage walks, DiffScope policy) were dropped: if they break you see it
and fix it.
"""

from __future__ import annotations

from typing import Any

from promptpotter.application.optimization.resume_and_fork import replay_decisions
from promptpotter.application.scoring.formula import compile_scorer, rescore_results
from promptpotter.application.scoring.search_point_scorer import merge_with_unprocessed_priors
from promptpotter.domain.results import RoundResult
from promptpotter.domain.run_records import CycleSeed
from promptpotter.infrastructure.store import Stores

# Every cycle lives inside a campaign; the foundation factory's default id.
_CAMPAIGN = "testds__20260101-000000"


def _r(score: float) -> dict:
    return {"query": "q", "predicted": "p", "ground_truth": "g", "fitness": score, "hit": False}


def _round(**kw: Any) -> RoundResult:
    """A round carrying only what the replayers read; the scoring scalars are inert."""
    return RoundResult.model_validate(
        {
            "label": "C0",
            "accuracy": 0.0,
            "hits": 0,
            "total": 0,
            "improved": False,
            "prompt_fields": {},
            "candidates_scored": 0,
            **kw,
        }
    )


def _prior(sample_id: int, predicted: str = "p", gt: str = "g") -> dict:
    """A cached measurement. ``sample_id`` IS the cell's identity — the merge keys on it;
    ``query`` rides along as the human-readable label."""
    return {
        "sample_id": sample_id,
        "query": f"q{sample_id}",
        "predicted": predicted,
        "ground_truth": gt,
        "error": None,
        "pipeline_data": {"total_time": 1.5},
    }


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


def test_round_winner_replay_uses_rescored_origin() -> None:
    """Rescoring preserves the recorded winner. The replay re-elects via the canonical θ-ability
    rule (shared with the live scorer) against the RESCORED origin: c1 clears the samples the origin
    misses → a confident difficulty-adjusted ability lift, c2 does not — so the replay re-derives c1
    and flags no divergence, while the stale ``current_best_accuracy_at_record`` is never consulted.
    The silent harm guarded: a resumed run that re-elects a *different* winner forks the lineage off
    the recorded path with no error."""

    def _m(sid: int, hit: bool) -> dict:
        return {**_r(1.0 if hit else 0.0), "sample_id": sid, "hit": hit}

    round_data = _round(
        round=0,
        all_candidate_results={
            "c1": [_m(i, i < 5) for i in range(6)],  # 5/6 — clears the hard tail
            "c2": [_m(i, i < 1) for i in range(6)],  # 1/6 — below the origin
        },
        decisions=[
            {
                "kind": "round_winner",
                "inputs_ref": {
                    "candidate_ids": ["c1", "c2"],
                    "round_num": 0,
                    "coverage_floor": 4,
                },
                "outcome": "c1",
                "data": {"current_best_accuracy_at_record": 0.8},  # stale, never read
            }
        ],
    )
    # Origin hits only the two easiest samples → c1's wins on the rest are real lift, c2's are not.
    assert replay_decisions(round_data, origin_results=[_m(i, i < 2) for i in range(6)]) is None


def test_elimination_cut_replay_flags_divergence_when_scores_flip() -> None:
    priors = [_r(1.0)] * 10
    current = [_r(1.0)] * 6  # rescored: now ties with priors
    round_data = _round(
        round=2,
        all_candidate_results={"c0": priors, "c1": priors, "c2": current},
        decisions=[
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
    )
    div = replay_decisions(round_data)
    assert div is not None
    assert div.kind == "elimination_cut"
    assert div.recorded_outcome is True and div.current_outcome is False


def test_margin_cut_replay_rederives_and_flags_flip() -> None:
    """A recorded paired-margin cut re-derives bit-for-bit from the recorded seed
    strata + the RESCORED candidate hits; a scorer change that flips one candidate
    hit into a win flips the verdict and flags divergence. Silent harm guarded: a
    resumed run silently keeping (or re-killing) a candidate the current scorer
    would judge differently."""

    def _m(sid: int, hit: bool) -> dict:
        return {**_r(1.0 if hit else 0.0), "sample_id": sid, "hit": hit}

    def _round_data(candidate_results: list[dict]) -> RoundResult:
        return _round(
            round=1,
            all_candidate_results={"c2": candidate_results},
            decisions=[
                {
                    "kind": "margin_cut",
                    "inputs_ref": {
                        "candidate_id": "c2",
                        "queries_scored": 5,
                        "epsilon": 0.05,
                        "n_min": 4,
                        "round_num": 1,
                        "gate": "margin",
                        "margin": {
                            "margin": 1,
                            "budget": 8,
                            "seed_hit_ids": ["0", "1", "2"],
                            "seed_miss_ids": ["3", "4", "5", "6", "7"],
                            "universe_ids": [str(i) for i in range(8)],
                        },
                    },
                    "outcome": True,
                    "data": {"candidate_sample_ids": ["3", "4", "5", "6", "7"]},
                }
            ],
        )

    # Unchanged scorer: every seed-miss attempted and unwon → need 1 > 0 left,
    # the deterministic corner re-derives the cut → no divergence.
    unchanged = _round_data([_m(i, False) for i in range(3, 8)])
    assert replay_decisions(unchanged) is None

    # Rescore flips sample 3 into a win → net clears the margin → cut no longer
    # re-derives → divergence.
    flipped = _round_data([_m(3, True)] + [_m(i, False) for i in range(4, 8)])
    div = replay_decisions(flipped)
    assert div is not None
    assert div.kind == "margin_cut"
    assert div.recorded_outcome is True and div.current_outcome is False


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
        _round(
            round=1,
            label="C1.1",
            accuracy=0.4,
            cumulative_accuracy=0.4,
            hits=4,
            total=10,
            improved=True,
            candidate_scores=[
                {
                    "candidate_id": "c1",
                    "label": "C1.1",
                    "prompt_fields": prompt,
                    "accuracy": 0.2,
                    "composite_fitness": 0.2,
                    "hits": 2,
                    "total": 10,
                },
            ],
        ),
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


def test_merge_with_unprocessed_priors_preserves_full_archive_on_partial_run() -> None:
    """The load-bearing invariant: a partial state.results merged with cached_sample_results
    yields back every dataset query the archive already covered.

    Aborted runs must not shrink an already-fuller archive — without this the
    overwrite-on-save ``_persist_fresh`` would grind down the cache file each Ctrl+C.
    """
    dataset_sample_ids = set(range(20))
    cached_sample_results = {i: _prior(i) for i in dataset_sample_ids}
    # Simulate a partial run: 6 cache hits + 1 fresh measurement.
    state_results = [_prior(i) for i in range(7)]
    formula = "exact_match(predicted, ground_truth)"
    merged = merge_with_unprocessed_priors(
        state_results,
        cached_sample_results=cached_sample_results,
        dataset_sample_ids=dataset_sample_ids,
        deprecated_samples={},
        scorer=compile_scorer(formula),
        scorer_id="x",
        scorer_formula=formula,
    )
    assert len(merged) == 20
    assert {r["sample_id"] for r in merged} == dataset_sample_ids


def test_merge_with_unprocessed_priors_filters_off_dataset_and_evicted() -> None:
    """Only samples in the current dataset get merged; evicted (deprecated) priors are
    excluded so they re-measure on the next encounter."""
    dataset_sample_ids = {1, 2}
    cached_sample_results = {
        1: _prior(1),
        2: _prior(2),
        99: _prior(99),  # not in the current dataset
    }
    deprecated = {2: _prior(2)}  # sample 2 deprecated → must remeasure
    formula = "exact_match(predicted, ground_truth)"
    merged = merge_with_unprocessed_priors(
        [],
        cached_sample_results=cached_sample_results,
        dataset_sample_ids=dataset_sample_ids,
        deprecated_samples=deprecated,
        scorer=compile_scorer(formula),
        scorer_id="x",
        scorer_formula=formula,
    )
    assert {r["sample_id"] for r in merged} == {1}


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


# Minimal valid OptimizationConfig — the two thresholds are required (no default).
_OPT = {"improvement_threshold": 0.0, "degradation_threshold": 0.0}


def _frozen_with_lock(node: str, open_keys: list[str]) -> dict:
    """A frozen `Campaign.config` dict carrying one per-node param lock (the
    operator's mint-time narrowing): `open_keys` are tunable, the rest held."""
    return {
        "optimization": _OPT,
        "optimizer_narrowing": {node: {"param_keys": open_keys, "param_allowed_values": {}}},
    }


def test_resume_preserves_per_campaign_locks_dropped_by_dataset_rebuild() -> None:
    """The lock-drop regression: resume rebuilds config from the live dataset file,
    which never carries `optimizer_narrowing`, so per-param locks reopen. The
    inherited-overlay re-merge must restore them from the frozen snapshot."""
    from promptpotter.application.config import apply_inherited_overlay, load_campaign_config

    # Config as rebuilt from the live dataset file: no narrowing (the bug surface).
    live = load_campaign_config({"optimization": _OPT})
    assert live.optimizer_narrowing == {}

    restored = apply_inherited_overlay(live, _frozen_with_lock("llm", ["temperature"]), seed=None)
    assert restored.optimizer_narrowing["llm"].param_keys == ["temperature"]


def test_steered_fork_seed_narrowing_overrides_campaign_locks_per_node() -> None:
    """A steered fork edits one node's locks; its seed `optimizer_narrowing`
    overrides the campaign-wide narrowing for THAT node, leaving others inherited."""
    from promptpotter.application.config import apply_inherited_overlay, load_campaign_config

    frozen = {
        "optimization": _OPT,
        "optimizer_narrowing": {
            "llm": {"param_keys": ["temperature"], "param_allowed_values": {}},
            "retriever": {"param_keys": ["top_k"], "param_allowed_values": {}},
        },
    }
    seed = CycleSeed(
        optimizer_narrowing={"llm": {"param_keys": [], "param_allowed_values": {}}},
        origin_source="fork_seed",
    )
    merged = apply_inherited_overlay(load_campaign_config({"optimization": _OPT}), frozen, seed)
    # Edited node: the fork's empty-keys lock (everything held) wins.
    assert merged.optimizer_narrowing["llm"].param_keys == []
    # Untouched node: the campaign's mint-time narrowing is inherited unchanged.
    assert merged.optimizer_narrowing["retriever"].param_keys == ["top_k"]


def test_a_campaign_frozen_before_todays_config_still_loads(built_stores: Stores) -> None:
    """A `CampaignConfig` field rename silently bricks every campaign already on disk.

    Both `Campaign` and `CampaignConfig` are `extra="forbid"`, and `campaign.json` embeds a
    config snapshot. Rename a field and `load_campaign_config` raises `extra_forbidden` for
    every campaign minted before the rename — `resume`, `ab`, `verify`, `noise-floor` and L4's
    inner cycles all die at load, before any scoring. Nothing else catches it: the campaign that
    can no longer be read is one nobody is currently running, so the failure surfaces days later
    as lost, irreplaceable measurement data. It has fired twice (50/177, then 156/169 campaigns).

    So this test does the one thing no freshly-built dict can: it feeds a **pinned** manifest
    through the real store reader. See `tests/fixtures/cycles/frozen_campaign/README.md` for what
    to do when it fails — a re-stamp, never `extra="allow"` and never a shim.
    """
    import json
    from pathlib import Path

    from promptpotter.application.config import apply_inherited_overlay, load_campaign_config
    from promptpotter.application.knobs import DiffScope, classify_config_diff

    fixture = Path(__file__).parent / "fixtures" / "cycles" / "frozen_campaign" / "campaign.json"
    manifest = json.loads(fixture.read_text(encoding="utf-8"))
    campaign_id = manifest["campaign_id"]

    store = built_stores.campaigns
    path = store.campaign_root_dir(campaign_id) / "campaign.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest), encoding="utf-8")

    # `Campaign` itself is extra="forbid" — a renamed manifest field dies here.
    campaign = store.load_campaign(campaign_id)
    assert campaign is not None
    # ...and so is the embedded `CampaignConfig` snapshot, non-default leaves and all.
    frozen = load_campaign_config(campaign.config)
    assert frozen.optimization.max_rounds == 7
    assert frozen.optimization.mechanisms.elimination.leader_lock_in is True

    # The resume path: the live dataset file never carries these two, so they are read back off
    # the snapshot. They must survive the round trip or the campaign resumes with its locks open.
    live = load_campaign_config({"optimization": _OPT})
    restored = apply_inherited_overlay(live, campaign.config, seed=None)
    assert restored.optimizer_narrowing["llm"].param_keys == ["temperature"]
    assert restored.pipeline_overrides == {"llm": {"temperature": 0.2}}

    # The snapshot is already a delta, so resuming on the config it was minted from is a no-op.
    assert classify_config_diff(frozen, campaign.config) == (DiffScope.NONE, [])


def test_lives_resume_fold_matches_live_observe() -> None:
    """Resume-integrity: the banked-lives ("hearts") count rebuilt from the ledger's
    ``improved`` sequence (``EscalationFSM.fold``) must equal the live in-run count
    (``observe_round``). A mismatch is silent — a resumed run would grant a different
    round budget than the un-interrupted run, quietly changing how long it optimizes."""
    from promptpotter.application.config import LivesConfig
    from promptpotter.application.optimization.escalation.state import EscalationFSM, NextAction
    from promptpotter.domain.phases import StopReason
    from promptpotter.domain.run_records import PhaseRecord

    cfg = LivesConfig(start=2, cap=4)
    sequence = [True, True, True, True, False, False, False]  # streak saturates at cap, then drains

    live = EscalationFSM()
    live_trace: list[int | None] = []
    last_event = None
    for improved in sequence:
        last_event = live.observe_round(
            improved=improved, current_accuracy=0.5, l1_patience=99, lives=cfg
        )
        live_trace.append(live.lives)

    replay = EscalationFSM()
    replay_trace: list[int | None] = []
    for improved in sequence:
        replay.fold(
            PhaseRecord(phase="round", event="complete", payload={"improved": improved}),
            lives=cfg,
        )
        replay_trace.append(replay.lives)

    assert replay_trace == live_trace == [3, 4, 4, 4, 3, 2, 1]
    # And exhausting the bank on the resumed FSM stops with the same reason the live loop uses.
    exhaust = replay.observe_round(improved=False, current_accuracy=0.5, l1_patience=99, lives=cfg)
    assert replay.lives == 0
    assert exhaust.next_action is NextAction.STOP_LIVES
    assert exhaust.stop_reason is StopReason.LIVES_EXHAUSTED
    assert last_event is not None  # streak never stopped mid-sequence


def test_cycle_seed_ledger_roundtrip(built_stores: Stores) -> None:
    """The read-once cycle seed now rides the ledger as a ``CycleSeedRecord``; a broken
    write→read round-trip silently starts a fork / campaign-from-origin from the WRONG
    origin (or none). An unseeded cycle reads back ``None``; a seeded one reads back
    intact even after later records land (the scan doesn't assume it's the last line)."""
    stores = built_stores
    cyc = "cycle_seed_roundtrip"
    stores.campaigns.create(_CAMPAIGN, cyc, {"sibling_kind": "root"})
    assert stores.campaigns.read_cycle_seed(_CAMPAIGN, cyc) is None  # unseeded → None

    seed = CycleSeed(
        origin_prompt_fields={"instruction": "do the thing"},
        origin_source="campaign_origin",
    )
    stores.campaigns.write_cycle_seed(_CAMPAIGN, cyc, seed)
    # A second seed after the first must win (last-wins), proving the scan spans the file.
    final_seed = CycleSeed(
        origin_prompt_fields={"instruction": "do it precisely"},
        origin_source="campaign_origin",
    )
    stores.campaigns.write_cycle_seed(_CAMPAIGN, cyc, final_seed)

    got = stores.campaigns.read_cycle_seed(_CAMPAIGN, cyc)
    assert got == final_seed
    assert got is not None and got.origin_source == "campaign_origin"


def _archive_run(archive: object, *, run_id: str, content_hash: str) -> None:
    """Minimal ``MeasurementArchive.save`` envelope — one dataset-tagged sample."""
    archive.save(  # type: ignore[attr-defined]
        run_id,
        {
            "run_id": run_id,
            "name": run_id,
            "content_hash": content_hash,
            "prompt_fields_id": "pf",
            "item_count": 1,
            "scores": {"accuracy": 1.0, "total": 1},
            "node_configs": [("llm_only", {"model": "X"})],
            "created_at": f"2026-05-19T00:00:{run_id[-2:]}Z",
            "measurements": [{"sample_id": 1, "query": f"q_{run_id}", "hit": True}],
            "dataset_name": "reidx",
        },
    )


def test_reindex_rebuilds_index_and_gcs_orphans_without_shrinking(built_stores: Stores) -> None:
    """The measurement index is an append-only JSONL fold (last-wins by ``content_hash``);
    ``reindex`` rebuilds it from the detail files and GCs orphans. Two silent harms it must
    not commit: (1) a re-measure of the same hash under a NEW run_id must last-win, never
    serve the stale row; (2) reindex must delete ONLY the orphaned detail file, never a live
    winner — an over-eager GC silently shrinks an irreplaceable archive."""
    archive = built_stores.archive
    _archive_run(archive, run_id="run_10", content_hash="h_a")
    _archive_run(archive, run_id="run_11", content_hash="h_b")
    # Re-measure h_a under a DIFFERENT run_id → the old detail (run_10) is now an orphan.
    _archive_run(archive, run_id="run_12", content_hash="h_a")

    rows = {e["content_hash"]: e["run_id"] for e in archive.list_all(dataset_name="reidx")}
    assert rows == {"h_a": "run_12", "h_b": "run_11"}  # last-wins fold

    counts = archive.reindex()
    assert counts["indexed"] == 2  # two live hashes
    assert counts["orphans_removed"] == 1  # run_10's detail

    after = {e["content_hash"]: e["run_id"] for e in archive.list_all(dataset_name="reidx")}
    assert after == rows  # reindex reproduces the fold, doesn't shrink it
    assert archive.load_by_id("run_12") is not None  # winners survive
    assert archive.load_by_id("run_11") is not None
    assert archive.load_by_id("run_10") is None  # orphan GC'd
