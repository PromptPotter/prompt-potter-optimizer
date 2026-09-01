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

from pathlib import Path
from typing import Any

import pytest

from promptpotter.application.archive_maintenance import (
    compact_measurement_archive,
    purge_cold_store,
    restore_measurement_archive,
)
from promptpotter.application.optimization.resume_and_fork.replayers import replay_decisions
from promptpotter.application.scoring.formula import compile_scorer, rescore_results
from promptpotter.application.scoring.search_point_scorer import (
    merge_with_unprocessed_priors,
    rescored_prior_tail,
)
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.results import RoundResult
from promptpotter.domain.run_records import CycleSeed
from promptpotter.domain.scoring import is_hit
from promptpotter.domain.search_point import TaskDecomposition
from promptpotter.infrastructure.store.stores import Stores

# Every cycle lives inside a campaign; the foundation factory's default id.
_CAMPAIGN = "testds__20260101-000000"


def _r(score: float) -> dict:
    # ``objective`` is what θ is fit on; equal to ``fitness`` under no ``per_cell`` composite.
    return {
        "query": "q",
        "predicted": "p",
        "ground_truth": "g",
        "fitness": score,
        "objective": score,
    }


def _decisions(*recs: dict[str, Any]) -> list[dict[str, Any]]:
    """The round's ledger decisions, as `scan_ledger_decisions` hands them to the replay."""
    return list(recs)


def _round(**kw: Any) -> RoundResult:
    """A round carrying only what the replayers read; the scoring scalars are inert."""
    return RoundResult.model_validate(
        {
            "label": "C0",
            "accuracy": 0.0,
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


def test_rescore_results_projects_the_scorer_it_was_handed() -> None:
    """A re-read row carries the LATEST scorer's verdict. Silent if it broke: every estimator reads
    ``fitness`` and would simply be answering an older formula."""
    result = {
        "query": "q",
        "predicted": "**42**",
        "ground_truth": "42",
        "fitness": 0.0,
        "error": None,
        "pipeline_data": None,
        "ground_truth_rank": 1,
    }
    rescore_results([result], compile_scorer("exact_match(predicted, ground_truth)"))
    assert result["fitness"] == 1.0 and is_hit(result["fitness"])

    rescore_results([result], compile_scorer("1 - exact_match(predicted, ground_truth)"))
    assert result["fitness"] == 0.0 and not is_hit(result["fitness"])


def test_round_winner_replay_ranks_against_the_recorded_parent() -> None:
    """Rescoring preserves the recorded winner, and the panel it is re-ranked against is READ off
    the decision instead of reconstructed: c1 clears the cells the parent misses → a confident
    difficulty-adjusted ability lift, c2 does not.

    The silent harm is the PARENT, not the rule. All three callers of one shared election supplied
    their own panel and each supplied a different one, so a resume ranked round 3 against C0 while
    the live scorer had ranked it against the round-2 winner — admitting arms the round refused,
    and reporting a divergence with no scorer change behind it."""

    def _m(sid: int, hit: bool) -> dict:
        return {**_r(1.0 if hit else 0.0), "sample_id": sid}

    inputs_ref = {"candidate_ids": ["c1", "c2"], "round_num": 0, "coverage_floor": 4}
    round_data = _round(
        round=0,
        all_candidate_results={
            "c1": [_m(i, i < 5) for i in range(6)],  # 5/6 — clears the hard tail
            "c2": [_m(i, i < 1) for i in range(6)],  # 1/6 — below the parent
        },
    )
    # The parent hit only the two easiest cells → c1's wins on the rest are real lift, c2's are not.
    parent = [_m(i, i < 2) for i in range(6)]
    assert (
        replay_decisions(
            round_data,
            _decisions(
                {
                    "kind": "round_winner",
                    "inputs_ref": inputs_ref,
                    "outcome": "c1",
                    "data": {"parent_cells": parent},
                }
            ),
        )
        is None
    )

    # A decision carrying no parent is REFUSED, never answered against a reconstructed one —
    # guessing quietly is the whole defect, so the replayer must raise rather than pick a panel.
    from promptpotter.application.optimization.resume_and_fork.replayers import (
        ReplayContext,
        _replay_round_winner,
    )

    with pytest.raises(ValueError, match="parent_cells"):
        _replay_round_winner(
            ReplayContext(round_data=round_data, decisions=[], ruler=None), inputs_ref, {}
        )


def test_elimination_cut_replay_flags_divergence_when_scores_flip() -> None:
    priors = [_r(1.0)] * 10
    current = [_r(1.0)] * 6  # rescored: now ties with priors
    round_data = _round(
        round=2,
        all_candidate_results={"c0": priors, "c1": priors, "c2": current},
    )
    decisions = _decisions(
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
    )
    div = replay_decisions(round_data, decisions)
    assert div is not None
    assert div.kind == "elimination_cut"
    assert div.recorded_outcome is True and div.current_outcome is False


def test_a_collapse_cut_is_replayed_as_a_collapse_not_under_the_epsilon_rule() -> None:
    """A collapse cut returns before ``elimination_p_best`` is ever reached, so it holds no
    posterior and the ``epsilon`` / ``recorded_p_best`` on its record are placeholders. Re-derived
    under the ε rule it computes a REAL p_best — this arm beats its prior 4 cells to 0 — reads
    "not cut", and reports a divergence no scorer change caused.

    Silent harm, in both directions: plain ``resume`` halts blaming a scorer that never moved, and
    ``resume --fork-on-divergence`` mints a fork here and DISCARDS every round from this one on."""

    def _cell(sid: int, gt: str) -> dict:
        # One label for every cell IS the collapse; the truths vary, so it is detectable.
        return {
            "sample_id": sid,
            "query": f"q{sid}",
            "predicted": "FALSE",
            "ground_truth": gt,
            "fitness": 1.0 if gt == "FALSE" else 0.0,
        }

    truths = ["FALSE", "FALSE", "FALSE", "FALSE", "TRUE", "TRUE"]
    round_data = _round(
        round=3,
        all_candidate_results={"c2": [_cell(i, gt) for i, gt in enumerate(truths)]},
    )
    decisions = _decisions(
        {
            "kind": "elimination_cut",
            "round": 3,
            # `gate` arrives off the ledger as its plain string, which is what a StrEnum compares to.
            "inputs_ref": {
                "candidate_id": "c2",
                "gate": "collapsed",
                "prior_candidate_ids": ["R2_winner"],
                "queries_scored": 6,
                "epsilon": 0.15,
                "n_min": 6,
                "round_num": 3,
                "recorded_p_best": 0.0,
            },
            "outcome": True,
            "data": {
                "candidate_sample_ids": [str(i) for i in range(6)],
                # Scored to make the ε rule re-derive the OPPOSITE answer, so this test fails the
                # moment the gate dispatch is removed.
                "prior_histories": {"R2_winner": {str(i): 0.0 for i in range(6)}},
            },
        }
    )
    assert replay_decisions(round_data, decisions) is None


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

    stores.campaigns.create(CycleHop(campaign_id=_CAMPAIGN, cycle_id=parent), {})
    stores.campaigns.save_round_file(
        CycleHop(campaign_id=_CAMPAIGN, cycle_id=parent),
        _round(
            round=1,
            label="C1.1",
            accuracy=0.4,
            total=10,
            improved=True,
            candidate_scores=[
                {
                    "candidate_id": "c1",
                    "label": "C1.1",
                    "prompt_fields": prompt,
                    "accuracy": 0.2,
                    "composite_fitness": 0.2,
                    "total": 10,
                },
            ],
        ),
    )
    stores.campaigns.create(
        CycleHop(campaign_id=_CAMPAIGN, cycle_id=fork),
        {
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
        hop=CycleHop(campaign_id=_CAMPAIGN, cycle_id=fork),
        experiment_extract={},
        dataset_config_dir=None,
    )

    # Resolve the origin OSP exactly as ``establish_campaign_origin`` does (fork-seed wins).
    unmodified_seed = CycleSeed(origin_prompt_fields=dict(prompt), origin_source="fork_seed")
    unmodified_osp = resolve_origin_opt_search_point(
        {}, task_context=TaskDecomposition(), seed=unmodified_seed
    )
    inherited = try_inherit_fork_origin(
        session,  # type: ignore[arg-type]
        unmodified_seed,
        resolved_origin=unmodified_osp,
    )
    assert inherited is not None
    # The branch point's OWN measurement, carried whole — not a re-rolled number, and not
    # an accuracy with the rest of the report re-derived around it.
    assert inherited.report.accuracy == 0.2
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
        resolved_origin=resolve_origin_opt_search_point(
            {}, task_context=TaskDecomposition(), seed=edited_seed
        ),
    )
    assert edited is None


def test_merge_with_unprocessed_priors_preserves_full_archive_on_partial_run() -> None:
    """The load-bearing invariant: a partial state.results merged with the prior tail
    yields back every dataset query the archive already covered.

    Aborted runs must not shrink an already-fuller archive — without this a Ctrl+C would
    record the run as having measured only what the walk reached, and the run's derived
    fields (scores, provenance, item_count) would be computed off that short set.
    """
    dataset_sample_ids = set(range(20))
    formula = "exact_match(predicted, ground_truth)"
    prior_tail = rescored_prior_tail(
        cached_sample_results={i: _prior(i) for i in dataset_sample_ids},
        dataset_sample_ids=dataset_sample_ids,
        deprecated_samples={},
        scorer=compile_scorer(formula),
    )
    # Simulate a partial run: 6 cache hits + 1 fresh measurement.
    merged = merge_with_unprocessed_priors([_prior(i) for i in range(7)], prior_tail)
    assert len(merged) == 20
    assert {r["sample_id"] for r in merged} == dataset_sample_ids


def test_rescored_prior_tail_filters_off_dataset_and_evicted() -> None:
    """Only samples in the current dataset are archivable without re-measuring; evicted
    (deprecated) priors are excluded so they re-measure on the next encounter."""
    formula = "exact_match(predicted, ground_truth)"
    tail = rescored_prior_tail(
        cached_sample_results={
            1: _prior(1),
            2: _prior(2),
            99: _prior(99),  # not in the current dataset
        },
        dataset_sample_ids={1, 2},
        deprecated_samples={2: _prior(2)},  # sample 2 deprecated → must remeasure
        scorer=compile_scorer(formula),
    )
    assert set(tail) == {1}
    assert {r["sample_id"] for r in merge_with_unprocessed_priors([], tail)} == {1}


def test_merge_known_outcomes_preserves_prior_on_untouched_samples() -> None:
    """Subset-measured winner must not shrink the known-outcome pool — priors are preserved.

    The pool seeds PoBB and resume's election floor; shrinking it loses measurement.
    It is NOT a score — its rows come from different configurations, so a mean over it
    belongs to no individual (``domain/results.py::merge_known_outcomes``).
    """
    from promptpotter.domain.results import merge_known_outcomes as _merge

    prior = [{"sample_id": i, "fitness": 1.0 if i < 10 else 0.0, "hit": i < 10} for i in range(20)]
    winner_hits = {10, 12, 15}
    winner = [
        {"sample_id": sid, "fitness": 1.0 if sid in winner_hits else 0.0, "hit": sid in winner_hits}
        for sid in range(10, 18)
    ]
    merged = _merge(prior, winner)
    by_sid = {r["sample_id"]: r for r in merged}

    assert set(by_sid.keys()) == set(range(20))
    assert by_sid[10]["hit"] is True
    assert by_sid[11]["hit"] is False
    assert by_sid[19]["hit"] is False
    assert all(by_sid[i]["hit"] is True for i in range(10))
    assert _merge(prior, []) == prior


# Minimal valid OptimizationConfig — the two thresholds are required (no default).
_OPT = {"degradation_threshold": 0.0}


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
    from promptpotter.application.campaign_config import (
        apply_inherited_overlay,
        load_campaign_config,
    )

    # Config as rebuilt from the live dataset file: no narrowing (the bug surface).
    live = load_campaign_config({"optimization": _OPT})
    assert live.optimizer_narrowing == {}

    restored = apply_inherited_overlay(live, _frozen_with_lock("llm", ["temperature"]), seed=None)
    assert restored.optimizer_narrowing["llm"].param_keys == ["temperature"]


def test_steered_fork_seed_narrowing_overrides_campaign_locks_per_node() -> None:
    """A steered fork edits one node's locks; its seed `optimizer_narrowing`
    overrides the campaign-wide narrowing for THAT node, leaving others inherited."""
    from promptpotter.application.campaign_config import (
        apply_inherited_overlay,
        load_campaign_config,
    )

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

    from promptpotter.application.campaign_config import (
        apply_inherited_overlay,
        load_campaign_config,
    )
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


def test_a_dataset_template_frozen_before_todays_config_still_loads() -> None:
    """Sibling of the manifest test above, for the *other* forbid surface.

    `datasets/{slug}/campaign.json::campaign_config` is a second `CampaignConfig` on disk,
    read by three endpoints (`/datasets/{name}/pipeline`, `/preview`, and the draft mint) plus
    the launcher. It spells out its knobs in FULL — an ingest-written template carries every
    `mechanisms` toggle at its default — so a knob dropped from `CampaignConfig` makes it
    `extra_forbidden` and every read of that dataset 500s. The manifest fixture beside this one
    does NOT cover it: different wrapper key, different reader (`read_campaign_config_file`).
    That gap is exactly how `swiss-invoices-eval` bricked on a live deploy after a rename — a
    user's ingested dataset carries a materialized origin (paid check-in output) we cannot
    re-stamp, so the loss is theirs and irreversible.

    Pinned, never regenerated: a rename of any knob this template names must fail HERE, in CI,
    not on a user's disk. Remedy on failure is the `restamp` verb (which
    `deploy-linux/update.sh` now runs on every deploy) — never `extra="allow"`, never a shim.
    """
    from pathlib import Path

    from promptpotter.application.datasets.authored import load_dataset_campaign_config

    fixture = (
        Path(__file__).parent / "fixtures" / "cycles" / "frozen_dataset_template" / "campaign.yaml"
    )
    # The real reader the three endpoints share — unwraps `campaign_config`, validates through
    # the live `CampaignConfig` (extra="forbid"). A dropped knob raises here.
    config = load_dataset_campaign_config(fixture)
    assert config.optimization.mechanisms.elimination.leader_lock_in is False
    assert config.optimization.mechanisms.selection.per_round_resubset is False


def test_lives_resume_fold_matches_live_observe() -> None:
    """Resume-integrity: the banked-lives ("hearts") count rebuilt from the ledger's
    ``improved`` sequence (``EscalationFSM.fold``) must equal the live in-run count
    (``observe_round``). A mismatch is silent — a resumed run would grant a different
    round budget than the un-interrupted run, quietly changing how long it optimizes."""
    from promptpotter.application.campaign_config import LivesConfig
    from promptpotter.application.optimization.escalation.state import EscalationFSM, NextAction
    from promptpotter.domain.phases import StopReason
    from promptpotter.domain.run_records import PhaseRecord

    cfg = LivesConfig(start=2, cap=4)
    # (improved, electable_count) — the streak saturates at cap, then a round where NOTHING
    # reached the election (every proposal rejected before it was scored) must cost no life,
    # then real stalls drain. Both halves must replay identically: an uncompared round banked
    # as a stall on one side and skipped on the other silently hands the resumed run a
    # different round budget, which is the whole harm this test exists for.
    sequence = [(True, 2), (True, 2), (True, 2), (True, 2), (False, 0), (False, 2), (False, 2)]

    live = EscalationFSM()
    live_trace: list[int | None] = []
    last_event = None
    for improved, electable in sequence:
        last_event = live.observe_round(
            improved=improved,
            compared=electable > 0,
            separable=None,
            current_accuracy=0.5,
            l1_patience=99,
            lives=cfg,
        )
        live_trace.append(live.lives)

    replay = EscalationFSM()
    replay_trace: list[int | None] = []
    # Round 0 leads, TWICE — the shape a real ledger has. The origin closes once at its own
    # `emit_origin_round` and again when the ruler warms at round 1 (`runner/loop.py`), because
    # its θ cannot be fit before a second arm exists. The live side banks neither: the origin
    # reaches `close_round` without going through `post_round`, so `observe_round` never sees
    # it. Folding them advanced the stall counter by two per resume and escalated to L2 early.
    for _ in range(2):
        replay.fold(
            PhaseRecord(
                phase="round",
                event="complete",
                round=0,
                payload={"improved": False, "electable_count": 0},
            ),
            lives=cfg,
        )
    assert (replay.lives, replay.l1_stall_count) == (None, 0), "round 0 banks nothing"

    for i, (improved, electable) in enumerate(sequence, start=1):
        replay.fold(
            PhaseRecord(
                phase="round",
                event="complete",
                round=i,
                payload={"improved": improved, "electable_count": electable},
            ),
            lives=cfg,
        )
        replay_trace.append(replay.lives)

    assert replay_trace == live_trace == [3, 4, 4, 4, 4, 3, 2]
    # The counter the two round-0 records used to inflate. Live: three trailing non-improving
    # rounds, one of them uncompared — all three advance the stall.
    assert replay.l1_stall_count == live.l1_stall_count == 3
    # And exhausting the bank on the resumed FSM stops with the same reason the live loop uses.
    replay.observe_round(
        improved=False,
        compared=True,
        separable=None,
        current_accuracy=0.5,
        l1_patience=99,
        lives=cfg,
    )
    exhaust = replay.observe_round(
        improved=False,
        compared=True,
        separable=None,
        current_accuracy=0.5,
        l1_patience=99,
        lives=cfg,
    )
    assert replay.lives == 0
    assert exhaust.next_action is NextAction.STOP_LIVES
    assert exhaust.stop_reason is StopReason.LIVES_EXHAUSTED
    assert last_event is not None  # streak never stopped mid-sequence


def test_unresolved_round_stalls_and_replays_as_one() -> None:
    """A round that crowned a winner and resolved NOTHING must advance L1 patience, live and on
    replay alike. Silent twice over: the round sets ``improved`` so every surface reads it as a
    win, and the escalation it should have triggered simply never happens — the loop spends its
    whole budget re-asking a question the panel could not answer, with no error anywhere. If the
    replay disagrees with the live run, a resumed cycle escalates on a different round than the
    one it interrupted, which silently changes what the campaign measured."""
    from promptpotter.application.optimization.escalation.state import EscalationFSM
    from promptpotter.domain.run_records import PhaseRecord

    # (improved, separable) — a resolved win, then two wins that told no arm from the parent.
    sequence = [(True, True), (True, False), (True, False)]

    live = EscalationFSM()
    for improved, separable in sequence:
        live.observe_round(
            improved=improved,
            compared=True,
            separable=separable,
            current_accuracy=0.5,
            l1_patience=99,
        )
    # Two unresolved rounds banked as stalls despite `improved` — at l1_patience 2 this is the
    # fire the loop was missing.
    assert live.l1_stall_count == 2

    replay = EscalationFSM()
    for i, (improved, separable) in enumerate(sequence, start=1):
        replay.fold(
            PhaseRecord(
                phase="round",
                event="complete",
                round=i,
                payload={
                    "improved": improved,
                    "electable_count": 2,
                    "separable": separable,
                },
            ),
            lives=None,
        )
    assert replay.l1_stall_count == live.l1_stall_count

    # A round whose arms carried no interval is UNREADABLE, not unresolved: it banks on
    # `improved` alone, so an old record that names no verdict replays as it was decided.
    unreadable = EscalationFSM()
    unreadable.fold(
        PhaseRecord(
            phase="round",
            event="complete",
            round=1,
            payload={"improved": True, "electable_count": 2},
        ),
        lives=None,
    )
    assert unreadable.l1_stall_count == 0


def test_l2_l3_escalation_state_survives_resume() -> None:
    """Resume-integrity: L2/L3 counters rebuilt from the ledger must equal the live in-run ones.

    Builds the records the way the firing seam writes them — the same ``CampaignPhase``, and the
    counters on the typed exit VIEW — so this pins reader-against-writer rather than
    reader-against-itself. It has to, because the arm has been wrong in both halves at once:
    ``fold`` compared ``record.phase`` to ``"l2_context"``/``"l3_plan"`` (the NODE names, which
    no PhaseRecord carries) and read the counters from ``payload["data"]``, which is
    in-memory-only and never reached disk. Either alone rebuilds L2/L3 as never-fired, handing
    the resumed run a fresh escalation budget and re-firing layers it had already spent. Silent
    in the resume sense: nothing raises, the counters just read zero.
    """
    from promptpotter.application.optimization.escalation.state import EscalationFSM
    from promptpotter.application.views.view_models import L2RefineExitView, PlanExitView
    from promptpotter.domain.phases import CampaignPhase
    from promptpotter.domain.run_records import PhaseRecord

    def snapshot(f: EscalationFSM) -> tuple[int, int, float, int, int, float, int]:
        return (
            f.l2_round,
            f.l2_stall_count,
            f.l2_best_composite_fitness_at_entry,
            f.l3_round,
            f.l3_stall_count,
            f.l3_best_composite_fitness_at_entry,
            f.l1_stall_count,
        )

    live = EscalationFSM()
    live_trace = []
    live.record_l2_fired(best_composite_fitness=0.60)
    live_trace.append(snapshot(live))
    # A second request at an unimproved fitness bumps the L2 stall before the fire banks it.
    live.observe_l2_escalation(current_composite_fitness=0.60, l2_patience=3, l3_patience=2)
    live.record_l2_fired(best_composite_fitness=0.60)
    live_trace.append(snapshot(live))
    # L3 firing wipes L2's progress — a new plan invalidates it. Checked BEFORE the wipe above,
    # or the L2 half of this test would assert zeros and pass against the bug it exists for.
    live.record_l3_fired(best_composite_fitness=0.75)
    live_trace.append(snapshot(live))

    def l2_view(l2_round: int, stall: int, comp: float) -> L2RefineExitView:
        return L2RefineExitView(
            param_changes_count=0,
            l1_layout_changed=False,
            axis_targeted="",
            changes_description="",
            l2_round=l2_round,
            l2_stall_count=stall,
            l2_best_composite_fitness_at_entry=comp,
            l2_best_theta_at_entry=None,
        )

    # What the exit records carry on disk: the post-fire state, on the persisted view. Real
    # views, not dicts — a namespace here would let a renamed field pass with every gate green.
    banked: list[tuple[CampaignPhase, object]] = [
        (CampaignPhase.REFINE_STRATEGY, l2_view(1, 0, 0.60)),
        (CampaignPhase.REFINE_STRATEGY, l2_view(2, 1, 0.60)),
        (
            CampaignPhase.MODIFY_PLAN,
            PlanExitView(
                new_plan_preview="",
                changes_description="",
                l3_round=1,
                l3_stall_count=0,
                l3_best_composite_fitness_at_entry=0.75,
                l3_best_theta_at_entry=None,
            ),
        ),
    ]
    replay = EscalationFSM()
    replay_trace = []
    for phase, view in banked:
        # Round-trip through Pydantic exactly as a resume does: `fold` must read the
        # dict `ledger.iter()` hands back, not only the live dataclass.
        on_disk = PhaseRecord.model_validate_json(
            PhaseRecord(phase=phase, event="exit", payload={"view": view}).model_dump_json()
        )
        replay.fold(on_disk, lives=None)
        replay_trace.append(snapshot(replay))

    assert replay_trace == live_trace
    # Pinned literally too: an arm that never matches leaves every one of these at 0/0.0.
    assert replay_trace == [
        (1, 0, 0.60, 0, 0, 0.0, 0),
        (2, 1, 0.60, 0, 0, 0.0, 0),
        (0, 0, 0.75, 1, 0, 0.75, 0),
    ]


def test_pending_decisions_file_by_round_and_survive_teardown(tmp_path: Path) -> None:
    """A decision reaches the ledger exactly once, stamped with the round that MADE it.

    Silent, and it bites resume. The round document used to carry a second copy assembled at
    drain time, which put the same fact in two places keyed differently: the ledger by its own
    stamp, the document by whatever was pending when it happened to be written. Both failure
    modes were real. A record drained into a round that did not make it was indistinguishable
    from a native one, so ``replay_decisions`` re-derived it against the WRONG round's
    measurements — a spurious halt or a missed one. Filtering the drain fixed that direction and
    opened the other: a record stamped for an already-written round was dropped from the drain
    and cleared from the buffer in one step, reaching no document at all. Only the stamp ever
    identified the round, so the copy is gone and the replay reads the ledger.

    The second half stands unchanged: escalation fires after its round has persisted, so a cycle
    stopping right there has no next ``persist_round`` and teardown is the only flush.
    """
    from types import SimpleNamespace

    from promptpotter.application.optimization.resume_and_fork.decisions import (
        ResumeCheckpointKind,
        record_decision,
    )
    from promptpotter.application.run_observers import RunCallbacks
    from promptpotter.application.runner.round import flush_pending_decisions, persist_round
    from promptpotter.domain.cycle_paths import CycleDir
    from promptpotter.domain.run_records import ResumeCheckpointRecord
    from promptpotter.infrastructure.ledger import CycleEventLog
    from tests.factories import round_result

    cycle_dir = CycleDir(tmp_path)
    (tmp_path / ".runtime").mkdir()
    ledger = CycleEventLog.open(cycle_dir)
    cb = RunCallbacks(ledger=ledger)
    # `cycle_id=None` short-circuits the store half of `persist_round`; the ledger is real.
    session = SimpleNamespace(
        state=SimpleNamespace(ledger=ledger, cycle_id=None, audit_projection=None)
    )
    cycle = SimpleNamespace(pending_decisions=[], axes=None)

    # Round 1's own cut, then round 1's post-round escalation — recorded AFTER round 1
    # persisted, so it is still pending when round 2 closes.
    record_decision(
        cycle.pending_decisions,
        ResumeCheckpointKind.ELIMINATION_CUT,
        {"round_num": 1},
        True,
        round=1,
    )
    persist_round(cycle, round_result(1), session, cb)  # type: ignore[arg-type]

    record_decision(
        cycle.pending_decisions,
        ResumeCheckpointKind.L2_ESCALATION_TRIGGER,
        {"round_num": 1},
        True,
        round=1,
    )
    record_decision(
        cycle.pending_decisions,
        ResumeCheckpointKind.ROUND_WINNER,
        {"round_num": 2},
        "c2",
        round=2,
    )
    persist_round(cycle, round_result(2), session, cb)  # type: ignore[arg-type]

    # A fire with no round after it: the buffer is the only copy until teardown flushes it.
    record_decision(
        cycle.pending_decisions,
        ResumeCheckpointKind.L2_ESCALATION_TRIGGER,
        {"round_num": 2},
        True,
        round=2,
    )
    assert flush_pending_decisions(cycle, session) == 1  # type: ignore[arg-type]
    assert cycle.pending_decisions == []
    assert flush_pending_decisions(cycle, session) == 0  # type: ignore[arg-type]

    # Every decision reached the ledger exactly once, each stamped with the round that made it.
    on_disk = [
        (r.kind.value, r.round)
        for _offset, r in CycleEventLog(ledger.path).iter()
        if isinstance(r, ResumeCheckpointRecord)
    ]
    assert on_disk == [
        ("elimination_cut", 1),
        ("l2_escalation_trigger", 1),
        ("round_winner", 2),
        ("l2_escalation_trigger", 2),
    ]


def test_cycle_seed_ledger_roundtrip(built_stores: Stores) -> None:
    """The read-once cycle seed now rides the ledger as a ``CycleSeedRecord``; a broken
    write→read round-trip silently starts a fork / campaign-from-origin from the WRONG
    origin (or none). An unseeded cycle reads back ``None``; a seeded one reads back
    intact even after later records land (the scan doesn't assume it's the last line)."""
    stores = built_stores
    cyc = "cycle_seed_roundtrip"
    stores.campaigns.create(CycleHop(campaign_id=_CAMPAIGN, cycle_id=cyc), {})
    assert (
        stores.campaigns.read_cycle_seed(CycleHop(campaign_id=_CAMPAIGN, cycle_id=cyc)) is None
    )  # unseeded → None

    seed = CycleSeed(
        origin_prompt_fields={"instruction": "do the thing"},
        origin_source="campaign_origin",
    )
    stores.campaigns.write_cycle_seed(CycleHop(campaign_id=_CAMPAIGN, cycle_id=cyc), seed)
    # A second seed after the first must win (last-wins), proving the scan spans the file.
    final_seed = CycleSeed(
        origin_prompt_fields={"instruction": "do it precisely"},
        origin_source="campaign_origin",
    )
    stores.campaigns.write_cycle_seed(CycleHop(campaign_id=_CAMPAIGN, cycle_id=cyc), final_seed)

    got = stores.campaigns.read_cycle_seed(CycleHop(campaign_id=_CAMPAIGN, cycle_id=cyc))
    assert got == final_seed
    assert got is not None and got.origin_source == "campaign_origin"


def _archive_run(
    archive: object,
    *,
    run_id: str,
    content_hash: str,
    measurements: list[dict[str, object]] | None = None,
    name: str | None = None,
) -> None:
    """Minimal ``MeasurementArchive.append_run`` envelope — one dataset-tagged sample.

    ``name`` is the run LABEL, which compaction reads to decide eligibility; it defaults to the
    run_id, as it does for every other caller here."""
    items = measurements or [{"sample_id": 1, "query": f"q_{run_id}", "hit": True}]
    archive.append_run(  # type: ignore[attr-defined]
        run_id,
        {
            "run_id": run_id,
            "name": name if name is not None else run_id,
            "content_hash": content_hash,
            "prompt_fields_id": "pf",
            "item_count": len(items),
            "scores": {"accuracy": 1.0, "total": len(items)},
            "node_configs": [("llm_only", {"model": "X"})],
            "created_at": f"2026-05-19T00:00:{run_id[-2:]}Z",
            "measurements": items,
            "dataset_name": "reidx",
        },
        items,
    )


def _compactable_cell(sample_id: int, **extra: object) -> dict[str, object]:
    """One richly-stamped measured cell — every field compaction may move, and every field the
    ruler re-grades from, so a test can assert both halves off one row."""
    return {
        "sample_id": sample_id,
        "query": f"q{sample_id}",
        "ground_truth": "g",
        "predicted": "p",
        "fitness": 1.0,
        "objective": 1.0,
        "hit": True,
        "scored": {"auto": {"fitness": 1.0, "formula": "exact_match(predicted, ground_truth)"}},
        "error_category": "",
        "ground_truth_rank": 0,
        "pipeline_data": {
            "terminal_node": "llm_only",
            "diagnostics": {"warnings": []},
            "step_tokens": {"llm_only": {"input": 10, "output": 5}},
            "step_timings": {"llm_only": 0.5},
            "reasoning_trace": "T" * 3000,
            "result_ranking": [1, 2, 3],
            "final_ranking": [1, 2, 3],
            "total_time": 1.25,
            **extra,
        },
    }


def test_partial_walk_log_folds_to_the_full_record(built_stores: Stores) -> None:
    """The run detail is an append-only log: a save writes only the NEW rows, so a walk that
    dies mid-dataset must still fold back to everything already paid for — and a re-walk of
    the same run must supersede a sample in place, never duplicate it and never shrink.

    This is the archive's half of the aborted-run invariant (the merge's half is above): the
    silent harm is an interrupted run whose log reads back short, quietly dropping
    measurements nothing will ever pay for again."""
    archive = built_stores.archive
    # Two saves, as the scoring walk makes them: sample 1, then sample 2 appended.
    _archive_run(archive, run_id="r_a", content_hash="h", measurements=[{"sample_id": 1}])
    _archive_run(
        archive,
        run_id="r_a",
        content_hash="h",
        measurements=[{"sample_id": 2, "hit": True}],
    )
    detail = archive.load_by_id("r_a")
    assert detail is not None
    assert [m["sample_id"] for m in detail["measurements"]] == [1, 2]

    # A re-measure of sample 1 supersedes in place — last-wins by sample_id.
    _archive_run(
        archive,
        run_id="r_a",
        content_hash="h",
        measurements=[{"sample_id": 1, "hit": True}],
    )
    detail = archive.load_by_id("r_a")
    assert detail is not None
    assert [m["sample_id"] for m in detail["measurements"]] == [1, 2]
    assert detail["measurements"][0]["hit"] is True

    # Compaction drops the superseded rows without losing a measurement.
    archive.compact_run("r_a")
    compacted = archive.load_by_id("r_a")
    assert compacted == detail

    # force_fresh REPLACES: an append-only log has to be told to forget.
    archive.reset_run("r_a")
    assert archive.load_by_id("r_a") is None


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


def test_compaction_round_trips_every_field_it_moved(built_stores: Stores) -> None:
    """Compaction MOVES fields out of a measurement row into a gzip cold store; restore puts them
    back. If the round trip is lossy the harm is silent and irreversible in the worst way — the
    rows are paid LLM spend, and nothing raises when one comes back missing a key.

    Also pins the half that must survive compaction on its own: the archive is re-graded by the
    READING campaign's scorer, so ``predicted``/``ground_truth``/``step_tokens``/``step_timings``
    have to still be there while the run is compacted, or a later campaign start raises
    ``ScoringTermMissingError`` over an archive that looks fine on disk."""
    archive = built_stores.archive
    # TWO saves, as the scoring walk makes them — so the log carries TWO header rows and the fold
    # reads the LAST. A stamp cleared from only the first header leaves the winning row still
    # reading as compacted after a restore, which nothing raises on; real logs here run to 12.
    _archive_run(
        archive,
        run_id="run_c0",
        content_hash="h_c0",
        name="candidate_0",
        measurements=[_compactable_cell(1)],
    )
    _archive_run(
        archive,
        run_id="run_c0",
        content_hash="h_c0",
        name="candidate_0",
        measurements=[_compactable_cell(2)],
    )
    before = archive.load_by_id("run_c0")
    assert before is not None
    assert "compaction" not in before

    report = compact_measurement_archive(built_stores, dataset="reidx", apply=True)
    assert report.runs_touched == 1
    assert report.rows_moved == 2
    assert report.bytes_freed > 0

    mid = archive.load_by_id("run_c0")
    assert mid is not None
    hot = mid["measurements"][0]
    # Moved out...
    assert "hit" not in hot
    assert "scored" not in hot
    assert "reasoning_trace" not in hot["pipeline_data"]
    # ...and the re-grading inputs still present, which is the whole safety of the field choice.
    assert hot["predicted"] == "p"
    assert hot["ground_truth"] == "g"
    assert hot["pipeline_data"]["step_tokens"] == {"llm_only": {"input": 10, "output": 5}}
    assert hot["pipeline_data"]["step_timings"] == {"llm_only": 0.5}
    # The compacted state is LEGIBLE — absent a stamp, "never had a trace" and "lost its trace to
    # compaction" are the same read, and every consumer has to guess which.
    assert mid["compaction"]["rows"] == 2

    restored = restore_measurement_archive(built_stores, dataset="reidx", apply=True)
    assert restored.runs_touched == 1
    assert restored.conflicts == 0
    assert archive.load_by_id("run_c0") == before
    assert "compaction" not in (archive.load_by_id("run_c0") or {})


def test_compaction_spares_the_runs_that_actually_serve_the_cache(built_stores: Stores) -> None:
    """``origin`` and ``round_parent`` replay 78.6% and 84.5% of their cells from the archive
    against 4.5% for a candidate — 82% of all cache value for a third of the bytes. Compacting one
    of them would silently turn cache hits into re-measurements: no error, just spend.

    An unrecognized label is SKIPPED and counted, never compacted on a guess."""
    archive = built_stores.archive
    _archive_run(
        archive,
        run_id="run_o1",
        content_hash="h_o",
        name="origin",
        measurements=[_compactable_cell(1)],
    )
    _archive_run(
        archive,
        run_id="run_p1",
        content_hash="h_p",
        name="round_parent",
        measurements=[_compactable_cell(1)],
    )
    _archive_run(
        archive,
        run_id="run_z1",
        content_hash="h_z",
        name="brand_new_label",
        measurements=[_compactable_cell(1)],
    )
    untouched = {rid: archive.load_by_id(rid) for rid in ("run_o1", "run_p1", "run_z1")}

    report = compact_measurement_archive(built_stores, dataset="reidx", apply=True)

    assert report.runs_touched == 0
    assert report.runs_skipped == 3
    assert report.skipped_by_label["brand_new_label"] == 1
    for rid, doc in untouched.items():
        assert archive.load_by_id(rid) == doc


def test_compaction_leaves_an_l4_row_still_narratable(built_stores: Stores) -> None:
    """The ``_inner_narrated`` panel gate needs BOTH ``mean_round_delta`` and a trace. A row that
    keeps the first and loses the second does not crash — it drops out of ``inner_narratives`` and
    flips the round onto ``sample_transcripts``, where every row reads as a miss by construction.
    A wrong panel, silently, on the recursion we are trying to improve."""
    archive = built_stores.archive
    _archive_run(
        archive,
        run_id="run_c9",
        content_hash="h_c9",
        name="candidate_0",
        measurements=[_compactable_cell(1, mean_round_delta=0.25), _compactable_cell(2)],
    )

    compact_measurement_archive(built_stores, dataset="reidx", apply=True)

    detail = archive.load_by_id("run_c9")
    assert detail is not None
    l4_row, plain_row = detail["measurements"]
    assert l4_row["pipeline_data"]["reasoning_trace"] == "T" * 3000  # protected
    assert "reasoning_trace" not in plain_row["pipeline_data"]  # its neighbour still moved
    # The protection is per-ROW, so the rest of the L4 row compacts like any other.
    assert "result_ranking" not in l4_row["pipeline_data"]


def test_a_purged_cold_store_is_the_only_irreversible_step(built_stores: Stores) -> None:
    """Compaction alone is always undoable; the purge is what spends the money. Kept apart so the
    reversible step can never quietly perform the irreversible one — and once purged, restore has
    to report the loss rather than silently 'succeed' over rows it cannot reach."""
    archive = built_stores.archive
    _archive_run(
        archive,
        run_id="run_c1",
        content_hash="h_c1",
        name="candidate_1",
        measurements=[_compactable_cell(1)],
    )
    compact_measurement_archive(built_stores, dataset="reidx", apply=True)
    assert archive.has_cold("run_c1")

    purged = purge_cold_store(built_stores, dataset="reidx", apply=True)
    assert purged.runs_touched == 1
    assert purged.purged == 1
    assert not archive.has_cold("run_c1")

    # The run must still REGISTER as measured-then-dropped. "We deleted this deliberately for
    # storage" and "this was never compacted" are different facts, and a purge that goes quiet
    # is indistinguishable from one that never ran.
    doc = archive.load_by_id("run_c1")
    assert doc is not None
    assert doc["purged"]["rows"] == 1

    after = restore_measurement_archive(built_stores, dataset="reidx", apply=True)
    assert after.runs_touched == 0  # nothing to put back...
    assert after.purged == 1  # ...and it is a COUNTED no-op, not a silent skip
    assert after.runs_skipped == 0
    assert "hit" not in doc["measurements"][0]


def test_a_dry_run_writes_nothing_and_predicts_what_the_apply_costs(built_stores: Stores) -> None:
    """A destructive verb's dry run is the operator's whole basis for consenting, so it must not
    write, and its byte figure must be the real one — an estimated ratio standing in for the gzip
    would make the consent rest on a number the apply never has to honour."""
    archive = built_stores.archive
    _archive_run(
        archive,
        run_id="run_c2",
        content_hash="h_c2",
        name="candidate_2",
        measurements=[_compactable_cell(1)],
    )
    before = archive.load_by_id("run_c2")

    dry = compact_measurement_archive(built_stores, dataset="reidx", apply=False)
    assert dry.runs_touched == 1
    assert not dry.applied
    assert archive.load_by_id("run_c2") == before  # untouched
    assert not archive.has_cold("run_c2")

    wet = compact_measurement_archive(built_stores, dataset="reidx", apply=True)
    assert (wet.bytes_before, wet.bytes_after, wet.cold_bytes) == (
        dry.bytes_before,
        dry.bytes_after,
        dry.cold_bytes,
    )


def test_a_fork_inherits_the_decisions_of_the_rounds_it_lifted(built_stores: Stores) -> None:
    """A fork must answer for its lifted rounds ITSELF, on its own ledger.

    Every ``scan_ledger_*`` reads the physical file — deliberately, so a fork's history begins at
    its own first append. Resume replays the lifted rounds to find where the branch departs and
    reads their decisions from that file, so a record only the parent holds is invisible: the
    check sees an empty list and passes every inherited round, silently, which is the exact
    shape of "a writer with no reader". The mint already copies those rounds' FILES; this is the
    same copy for the same reason. Records at or after the cut are the parent's own future and
    must NOT come along, or the branch replays a decision it never made.
    """
    from promptpotter.application.optimization.resume_and_fork.decisions import (
        ResumeCheckpointKind,
        record_decision,
    )
    from promptpotter.domain.cycle_paths import CycleDir
    from promptpotter.infrastructure.ledger import CycleEventLog
    from promptpotter.infrastructure.store.campaign_store.ledger_scan import (
        scan_ledger_decisions,
    )
    from promptpotter.infrastructure.store.layout import CycleLayout

    store = built_stores.campaigns
    # NOT `cycle_fork_decisions`: `_SIBLING_LAST_SEP_RE` matches `_fork_` anywhere, so that name
    # read as a fork of a family rooted at `cycle` — a root fixture lying about its own kind.
    parent = CycleHop(campaign_id=_CAMPAIGN, cycle_id="cycle_decisions")
    store.create(parent, {})
    parent_ledger = CycleEventLog.open(CycleDir(store.cycle_dir(parent)))
    for rnd, kind in (
        (0, ResumeCheckpointKind.ROUND_WINNER),
        (1, ResumeCheckpointKind.ELIMINATION_CUT),
        (2, ResumeCheckpointKind.ROUND_WINNER),
    ):
        record_decision(parent_ledger, kind, {"round_num": rnd}, "x", round=rnd)

    child = parent.model_copy(update={"cycle_id": "cycle_decisions_fork_a"})
    store.create(child, {})
    store.copy_parent_rounds_and_candidates(
        _CAMPAIGN, parent.cycle_id, child.cycle_id, before_round=2
    )

    lifted = scan_ledger_decisions(CycleLayout(store.cycle_dir(child)).ledger)
    assert sorted(lifted) == [0, 1]
    assert [d["kind"] for d in lifted[0]] == ["round_winner"]
    assert [d["kind"] for d in lifted[1]] == ["elimination_cut"]


def test_an_applied_scenario_forks_at_its_round_and_carries_the_criterion(
    built_stores: Stores,
) -> None:
    """ "Apply this criterion from round N" must lift rounds 0..N-1 AND land the criterion.

    Both halves are silent when wrong. A fork that took the OFFSHOOT branch would restart round
    numbering at 1 and re-measure an origin the operator meant to keep — no error, just a branch
    answering a different question. And a `scoring` override dropped on the way in leaves the fork
    running under the parent's criterion, which is the one thing the operator pressed the button to
    change: every round it then produces is evidence for a formula that was never applied.

    `scoring` sits on `CampaignConfig` itself rather than under `optimization`, so it needs its own
    bucket in `_apply_config_overrides` — folded into the nested copy beside the run limits it
    would vanish with every gate green.
    """
    from promptpotter.application.campaign_config import CampaignConfig, OptimizationConfig
    from promptpotter.application.optimization.resume_and_fork.fork_siblings import (
        mint_operator_fork,
    )
    from promptpotter.application.runner.entry import _apply_config_overrides
    from promptpotter.domain.run_records import ConfigOverrides

    store = built_stores.campaigns
    parent = CycleHop(campaign_id=_CAMPAIGN, cycle_id="cycle_applyscenario")
    store.create(parent, {"parent_session_id": "sess-apply"})
    criterion = "0.9 * accuracy + 0.1 * (1 - mean_latency_s)"

    fork_id = mint_operator_fork(
        stores=built_stores,
        hop=parent,
        from_round=2,
        from_candidate_id="",
        seed=CycleSeed(config_overrides=ConfigOverrides(scoring=criterion)),
        steered_by="tester",
        keep_rounds=True,
    )
    child = parent.model_copy(update={"cycle_id": fork_id})

    # The REBASE branch: the record says which act this was, and it is the one that lifts rounds.
    assert (store.load(child) or {}).get("fork", {}).get("trigger") == "operator_rewind"
    assert (store.load(child) or {}).get("forked_from_round") == 2

    # …and the criterion survives the ledger round-trip into the fork's effective config.
    seed = store.read_cycle_seed(child)
    assert seed is not None
    base = CampaignConfig(optimization=OptimizationConfig(degradation_threshold=0.05))
    applied = _apply_config_overrides(base, seed.config_overrides)
    assert applied.scoring == criterion
    assert base.scoring is None  # the parent's frozen config is never mutated

    # An origin-bearing seed asks for two different origins at once — the lifted round 0 already
    # is one. Refused rather than silently dropped.
    with pytest.raises(ValueError, match="origin_prompt_fields"):
        mint_operator_fork(
            stores=built_stores,
            hop=parent,
            from_round=1,
            from_candidate_id="",
            seed=CycleSeed(origin_prompt_fields={"instruction": "x"}, origin_source="fork_seed"),
            steered_by="tester",
            keep_rounds=True,
        )
