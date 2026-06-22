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

from promptpotter.application.optimization.resume_and_fork import replay_decisions
from promptpotter.application.scoring.formula import compile_scorer, rescore_results
from promptpotter.application.scoring.search_point_scorer import merge_with_unprocessed_priors
from promptpotter.domain.run_records import CycleSeed
from promptpotter.infrastructure.store import Stores

# Every cycle lives inside a campaign; the foundation factory's default id.
_CAMPAIGN = "testds__20260101-000000"


def _r(score: float) -> dict:
    return {"query": "q", "predicted": "p", "ground_truth": "g", "fitness": score, "hit": False}


def _prior(query: str, predicted: str = "p", gt: str = "g") -> dict:
    return {
        "query": query,
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
    """Uniform rescaling preserves the recorded winner. The replay re-elects via the canonical
    paired-LCB rule (shared with the live scorer) against the RESCORED origin: c1's +0.5 lift over
    six paired samples confidently clears the floor, c2's negative lift does not — so the replay
    re-derives c1 and flags no divergence, while the stale ``current_best_accuracy_at_record`` (and
    the old raw-mean threshold) are never consulted."""

    def _s(sid: int, fitness: float) -> dict:
        return {**_r(fitness), "sample_id": sid}

    round_data = {
        "round": 0,
        "all_candidate_results": {
            "c1": [_s(i, 0.9) for i in range(6)],
            "c2": [_s(i, 0.1) for i in range(6)],
        },
        "decisions": [
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
    }
    assert replay_decisions(round_data, origin_results=[_s(i, 0.4) for i in range(6)]) is None


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
            "label": "C1.1",
            "accuracy": 0.4,
            "hits": 4,
            "total": 10,
            "improved": True,
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
