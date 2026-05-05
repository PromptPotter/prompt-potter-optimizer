"""Cycle state + decision ledger + fork helpers.

Decisions are two-tier: (inputs_ref + outcome) compared on resume; data is
archival. Action driver (escalate_l2 + L2/L3 strategies) lives in escalation.py
— one-way arrow, enforced by tests/test_layer_imports.py.
"""

from __future__ import annotations

import enum
import hashlib
import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, NamedTuple, cast

from promptpotter.application.optimization.elimination import update_query_tracker
from promptpotter.application.scoring.formula import rescore_results
from promptpotter.application.scoring.metrics import (
    compile_failure_analysis,
    compute_composite_fitness,
)
from promptpotter.config.settings import PROMPT_STRING_FIELDS
from promptpotter.domain.analysis import RuntimeFailure
from promptpotter.domain.cycle_paths import CycleDir
from promptpotter.domain.opt_search_point import OptSearchPoint, RoundSummary
from promptpotter.domain.phases import StopReason
from promptpotter.domain.results import RoundBaseline, RoundResult
from promptpotter.domain.run_records import (
    DECISION_GATING,
    CycleRecord,
    DecisionKind,
    DecisionRecord,
    GatingMode,
    PhaseRecord,
    SweepPayload,
    record_decision,
)
from promptpotter.domain.search_point import JobSearchPoint
from promptpotter.infrastructure.ledger import CycleLedger
from promptpotter.infrastructure.store import root_cycle_id, save_active_pointer
from promptpotter.shared.errors import ResumeDivergenceError, graceful
from promptpotter.shared.statistics import (
    pobb_should_stop,
    posterior_best_probabilities,
)

if TYPE_CHECKING:
    from promptpotter.application.bootstrap import Session
    from promptpotter.application.config import CampaignConfig
    from promptpotter.application.intelligence.indexes import AxisIndex
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.scoring import QueryMeasurement
    from promptpotter.domain.search_point import TaskDecomposition
    from promptpotter.infrastructure.store import CampaignStore

logger = logging.getLogger(__name__)

__all__ = [
    "REPLAYERS",
    "Cycle",
    "DecisionRecord",
    "Divergence",
    "EscalationEvent",
    "EscalationState",
    "ForkResult",
    "NextAction",
    "ReplayContext",
    "build_escalation_entry",
    "fork_for_diag_sibling",
    "fork_for_sweep_sibling",
    "replay_decisions",
    "resume_with_divergence_check",
]


# DecisionRecord / DecisionKind / record_decision live in domain/run_records.py.


def _build_scoreboard(
    candidate_scores: list[dict[str, Any]], winner_label: str
) -> list[dict[str, Any]]:
    """Trial-JSON `scoreboard`: rank by (composite_fitness, accuracy) desc; tag winner."""
    ranked = sorted(
        candidate_scores,
        key=lambda c: (c["composite_fitness"], c["accuracy"]),
        reverse=True,
    )
    rows: list[dict[str, Any]] = []
    for i, c in enumerate(ranked, start=1):
        is_winner = bool(c.get("changes_description")) and c["changes_description"] == winner_label
        rows.append(
            {
                "rank": i,
                "candidate_id": c.get("candidate_id"),
                "label": c.get("changes_description", ""),
                "accuracy": c.get("accuracy"),
                "composite_fitness": c.get("composite_fitness"),
                "hits": c.get("hits"),
                "total": c.get("total"),
                "ci_lo": c.get("ci_lo"),
                "ci_hi": c.get("ci_hi"),
                "is_winner": is_winner,
                "escalation_aborted": c.get("escalation_aborted", False),
            }
        )
    return rows


class Divergence(NamedTuple):
    """A recorded decision re-derived to a different outcome under the current scorer."""

    round_num: int
    kind: str
    recorded_outcome: Any
    current_outcome: Any
    inputs_ref: dict[str, Any]


class ReplayContext(NamedTuple):
    """Context passed to replayers — round_data + prior_rounds + baseline_results all rescored."""

    round_data: dict[str, Any]
    prior_rounds: list[dict[str, Any]]
    baseline_results: list[dict[str, Any]]


class ForkResult(NamedTuple):
    """Resume detected divergence and forked into a sibling cycle."""

    new_cycle_id: str
    new_resumed_from_round: int


Replayer = Callable[[ReplayContext, dict[str, Any]], Any]


def replay_decisions(
    round_data: dict[str, Any],
    prior_rounds: list[dict[str, Any]] | None = None,
    baseline_results: list[dict[str, Any]] | None = None,
) -> Divergence | None:
    """Walk ``round_data['decisions']`` in order; return the first mismatch."""
    ctx = ReplayContext(
        round_data=round_data,
        prior_rounds=list(prior_rounds or []),
        baseline_results=list(baseline_results or []),
    )
    for rec in round_data.get("decisions") or []:
        kind = rec.get("kind", "")
        fn = REPLAYERS.get(kind)
        if fn is None:
            continue
        try:
            current = fn(ctx, rec.get("inputs_ref") or {})
        except Exception:
            # Replayer failure shouldn't poison resume — treat as non-divergence.
            continue
        recorded = rec.get("outcome")
        if current != recorded:
            return Divergence(
                round_num=int(round_data.get("round", -1)),
                kind=kind,
                recorded_outcome=recorded,
                current_outcome=current,
                inputs_ref=dict(rec.get("inputs_ref") or {}),
            )
    return None


def _mean_score(results: list[dict]) -> float:
    """Mean of rescored ``score`` projection."""
    if not results:
        return 0.0
    return sum(float(r.get("fitness", 0.0)) for r in results) / len(results)


def _replay_round_winner(ctx: ReplayContext, inputs_ref: dict[str, Any]) -> str:
    """Re-derive round winner from rescored per-candidate results; beat-threshold derived, not read."""
    all_results: dict[str, list[dict]] = ctx.round_data.get("all_candidate_results") or {}
    if ctx.prior_rounds:
        best_acc = _mean_score(list(ctx.prior_rounds[-1].get("results") or []))
    else:
        best_acc = _mean_score(list(ctx.baseline_results))
    winner_id = ""
    for cid in inputs_ref.get("candidate_ids") or []:
        acc = _mean_score(all_results.get(cid) or [])
        if acc > best_acc:
            best_acc = acc
            winner_id = cid
    return winner_id


def _pobb_replay_snapshot(
    ctx: ReplayContext, inputs_ref: dict[str, Any]
) -> tuple[str, dict[str, float], list[float]] | None:
    """Build (candidate_id, posterior snapshot, current scores) for PoBB replay; None when underspecified."""
    all_results: dict[str, list[dict]] = ctx.round_data.get("all_candidate_results") or {}
    candidate_id = str(inputs_ref.get("candidate_id", ""))
    prior_ids = list(inputs_ref.get("prior_candidate_ids") or [])
    n = int(inputs_ref.get("queries_scored", 0))
    current = [float(r.get("fitness", 0.0)) for r in (all_results.get(candidate_id) or [])[:n]]
    priors = {
        pid: [float(r.get("fitness", 0.0)) for r in (all_results.get(pid) or [])]
        for pid in prior_ids
    }
    priors = {pid: p for pid, p in priors.items() if p}
    if not priors or len(current) < 2:
        return None
    return candidate_id, posterior_best_probabilities({**priors, candidate_id: current}), current


def _replay_elimination_cut(ctx: ReplayContext, inputs_ref: dict[str, Any]) -> bool:
    """PoBB gate under rescored scores."""
    snap = _pobb_replay_snapshot(ctx, inputs_ref)
    if snap is None:
        return False
    candidate_id, snapshot, _ = snap
    return pobb_should_stop(snapshot.get(candidate_id, 1.0), float(inputs_ref["epsilon"]))


def _replay_leader_lock_in(ctx: ReplayContext, inputs_ref: dict[str, Any]) -> bool:
    """PoBB leader-lock under rescored scores; argmax + P(best) ≥ lock_in + n ≥ lock_in_n_min."""
    if int(inputs_ref.get("queries_scored", 0)) < int(inputs_ref.get("lock_in_n_min", 8)):
        return False
    snap = _pobb_replay_snapshot(ctx, inputs_ref)
    if snap is None:
        return False
    candidate_id, snapshot, _ = snap
    leader = max(snapshot.items(), key=lambda kv: kv[1])[0]
    return leader == candidate_id and snapshot.get(candidate_id, 0.0) >= float(
        inputs_ref.get("lock_in", 0.95)
    )


def _derive_stall_count(
    prior_rounds: list[dict[str, Any]],
    entry_round: int,
    this_round: int,
) -> int:
    """Reconstruct stall_count at end of this_round."""
    if entry_round < 0:
        return 0
    sorted_trials = sorted(prior_rounds, key=lambda t: int(t.get("round", -1)))
    running_max = 0.0
    baseline: float | None = None
    rounds_after = 0
    for t in sorted_trials:
        r = int(t.get("round", -1))
        if r < 0 or r > this_round:
            continue
        winner_results = t.get("results") or []
        comp = _mean_score(winner_results) if winner_results else float(t["composite_fitness"])
        running_max = max(running_max, comp)
        if r <= entry_round:
            if r == entry_round:
                baseline = running_max
            continue
        rounds_after += 1
        if baseline is not None and running_max > baseline:
            return 0
    return rounds_after if baseline is not None else 0


def _replay_layer_trigger(patience_key: str) -> Replayer:
    """Build a replayer that re-derives `triggered = stalls < patience` from prior rounds."""

    def _replay(ctx: ReplayContext, inputs_ref: dict[str, Any]) -> bool:
        patience = inputs_ref.get(patience_key)
        if patience is None:
            return True
        stalls = _derive_stall_count(
            ctx.prior_rounds,
            int(inputs_ref.get("entry_round", -1)),
            int(inputs_ref.get("round_num", -1)),
        )
        return stalls < int(patience)

    return _replay


_replay_l2_trigger = _replay_layer_trigger("l2_patience")
_replay_l3_trigger = _replay_layer_trigger("l3_patience")


# Explicit decision-replayer registry. ``DECISION_GATING`` is the source of
# truth for which kinds exist; the assertion below enforces that every
# REPLAYED kind has a replayer here, so resume can never silently treat an
# unhandled kind as non-divergence.
REPLAYERS: dict[DecisionKind, Replayer] = {
    DecisionKind.ROUND_WINNER: _replay_round_winner,
    DecisionKind.ELIMINATION_CUT: _replay_elimination_cut,
    DecisionKind.LEADER_LOCK_IN: _replay_leader_lock_in,
    DecisionKind.L2_ESCALATION_TRIGGER: _replay_l2_trigger,
    DecisionKind.L3_ESCALATION_TRIGGER: _replay_l3_trigger,
}

_missing_replayers = {
    k for k, mode in DECISION_GATING.items() if mode is GatingMode.REPLAYED and k not in REPLAYERS
}
if _missing_replayers:
    raise RuntimeError(
        f"DECISION_GATING declares {sorted(_missing_replayers)} as REPLAYED, "
        "but no replayer is registered in cycle.py::REPLAYERS."
    )
del _missing_replayers


def _fork_sibling_setup(
    campaign_store: CampaignStore,
    tenant_id: str,
    session_id: str,
    parent_cycle_id: str,
    new_cycle_id: str,
    *,
    from_round: int,
    fork_data: dict[str, Any] | None = None,
    log_extra: str = "",
) -> str:
    """Common fork plumbing: dir create, FORK_CUT append, pointer + log.

    Returns ``now_iso`` — pass to the matching ``CampaignStore.save_*_fork``
    so its ``forked_at`` matches the FORK_CUT decision record.
    """
    parent_dir = campaign_store.campaign_dir(parent_cycle_id)
    new_dir = campaign_store.campaign_dir(new_cycle_id)
    if new_dir.exists():
        raise FileExistsError(f"forked cycle dir already exists: {new_dir}")
    new_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(UTC).isoformat()
    with graceful("FORK_CUT decision append failed"):
        record_decision(
            CycleLedger.open(CycleDir(parent_dir)),
            DecisionKind.FORK_CUT,
            {"from_round": from_round},
            new_cycle_id,
            data={"forked_at": now, **(fork_data or {})},
        )

    save_active_pointer(tenant_id, session_id, new_cycle_id)
    logger.info(
        "Forked %s → %s at round %d%s (active pointer retargeted)",
        parent_cycle_id,
        new_cycle_id,
        from_round,
        log_extra,
    )
    return now


def _fork_at_divergence(
    campaign_store: CampaignStore,
    tenant_id: str,
    session_id: str,
    old_cycle_id: str,
    fork_from_round: int,
    surviving_rounds: list[dict[str, Any]],
) -> str:
    """Divergence-fork that inherits parent's < fork_from_round artifacts (deterministic replay).

    NOT for clean-slate siblings (sweep/diag) — those would short-circuit
    L1 on the inherited round-0 checkpoint. Use _fork_for_{sweep,diag}_sibling.
    """
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    suffix = hashlib.sha256(f"{old_cycle_id}|{ts}".encode()).hexdigest()[:8]
    new_cycle_id = f"{old_cycle_id}_fork_{suffix}"

    now = _fork_sibling_setup(
        campaign_store,
        tenant_id,
        session_id,
        old_cycle_id,
        new_cycle_id,
        from_round=fork_from_round,
    )
    campaign_store.save_divergence_fork(
        old_cycle_id,
        new_cycle_id,
        surviving_rounds=surviving_rounds,
        forked_at=now,
        forked_from_round=fork_from_round,
    )
    campaign_store.copy_parent_rounds_and_candidates(
        old_cycle_id, new_cycle_id, before_round=fork_from_round
    )
    return new_cycle_id


def _next_diag_sibling_id(campaign_store: CampaignStore, parent_cycle_id: str) -> str:
    """Next ``{root}_diag_NNN`` id; siblings always root at the family root so the BFS tree stays one level deep."""
    root_id = root_cycle_id(parent_cycle_id)
    diag_dir = campaign_store.campaign_dir(root_id) / "diag"
    pattern = re.compile(rf"^{re.escape(root_id)}_diag_(\d+)$")
    max_n = 0
    if diag_dir.is_dir():
        for entry in diag_dir.iterdir():
            if not entry.is_dir():
                continue
            m = pattern.match(entry.name)
            if m:
                max_n = max(max_n, int(m.group(1)))
    return f"{root_id}_diag_{max_n + 1:03d}"


def fork_for_diag_sibling(
    campaign_store: CampaignStore,
    tenant_id: str,
    session_id: str,
    parent_cycle_id: str,
) -> str:
    """Mint a diag-BFS sibling rooted at round 0; records ``FORK_CUT`` and retargets the active pointer."""
    new_cycle_id = _next_diag_sibling_id(campaign_store, parent_cycle_id)
    now = _fork_sibling_setup(
        campaign_store,
        tenant_id,
        session_id,
        parent_cycle_id,
        new_cycle_id,
        from_round=0,
        fork_data={"kind": "diag_sibling"},
    )
    campaign_store.save_diag_fork(parent_cycle_id, new_cycle_id, forked_at=now)
    return new_cycle_id


def fork_for_sweep_sibling(
    campaign_store: CampaignStore,
    tenant_id: str,
    session_id: str,
    parent_cycle_id: str,
    sweep_batch_id: str,
    payload_source_file: str,
    payload: SweepPayload,
) -> str:
    """Sweep-batch sibling — clean slate. cycle_id encodes _sweep_{batch_id}_;
    sweep_batch_id must not contain '_' (cycle-id regex splits on it).
    """
    if "_" in sweep_batch_id:
        raise ValueError(f"sweep_batch_id must not contain underscores; got {sweep_batch_id!r}")
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    suffix = hashlib.sha256(f"{parent_cycle_id}|{ts}|{payload_source_file}".encode()).hexdigest()[
        :8
    ]
    new_cycle_id = f"{parent_cycle_id}_sweep_{sweep_batch_id}_{suffix}"

    now = _fork_sibling_setup(
        campaign_store,
        tenant_id,
        session_id,
        parent_cycle_id,
        new_cycle_id,
        from_round=0,
        fork_data={
            "kind": "sweep_fork",
            "sweep_batch_id": sweep_batch_id,
            "source_file": payload_source_file,
            "sweep_payload": payload.model_dump(mode="json"),
        },
        log_extra=f" [batch={sweep_batch_id}, payload={payload_source_file}]",
    )
    campaign_store.save_sweep_fork(
        parent_cycle_id, new_cycle_id, sweep_batch_id=sweep_batch_id, forked_at=now
    )
    return new_cycle_id


# ---------------------------------------------------------------------------
# Cycle state — escalation counters + per-round mutation
# ---------------------------------------------------------------------------


def _rf_dedup_key(rf_dict: dict) -> tuple:
    cfg = rf_dict.get("observed_config") or {}
    return (
        rf_dict.get("source", ""),
        rf_dict.get("dominant_warning", ""),
        json.dumps(cfg, sort_keys=True, default=str),
    )


class NextAction(enum.StrEnum):
    """What the round loop does after an escalation observation.

    Computed inside ``EscalationState`` from stall depth + layer history.
    Stop variants carry the matching ``StopReason`` via
    ``EscalationEvent.stop_reason``; ``CONTINUE`` / ``FIRE_*`` carry None.
    """

    CONTINUE = "continue"
    FIRE_L2 = "fire_l2"
    FIRE_L3 = "fire_l3"
    STOP_PERFECT = "stop_perfect"
    STOP_L1_PATIENCE = "stop_l1_patience"
    STOP_L2_PATIENCE = "stop_l2_patience"
    STOP_L3_PATIENCE = "stop_l3_patience"


_NEXT_ACTION_TO_STOP: dict[NextAction, StopReason] = {
    NextAction.STOP_PERFECT: StopReason.PERFECT,
    NextAction.STOP_L1_PATIENCE: StopReason.PATIENCE,
    NextAction.STOP_L2_PATIENCE: StopReason.L2_PATIENCE,
    NextAction.STOP_L3_PATIENCE: StopReason.L3_PATIENCE,
}


@dataclass(frozen=True)
class EscalationEvent:
    """Outcome of one escalation observation — what the loop does next.

    ``stall_depth`` is the layer-relevant counter at decision time (L1 stall
    for ``observe_round``; L2 or L3 stall for ``observe_l2_escalation``).
    ``reason`` is human-readable for telemetry; consumers branch on
    ``next_action`` and read ``stop_reason`` for the StopLoop projection.
    """

    next_action: NextAction
    stall_depth: int
    reason: str

    @property
    def stop_reason(self) -> StopReason | None:
        return _NEXT_ACTION_TO_STOP.get(self.next_action)


class EscalationState:
    """Cause-driven L1/L2/L3 escalation. Counters are private; the only
    mutation surface is the observation methods (``observe_round``,
    ``observe_l2_escalation``) and the post-fire bookkeepers
    (``record_l2_fired``, ``record_l3_fired``). Read access is via flat
    ``l{1,2,3}_*`` properties — there is no public setter for any counter,
    so the "signals from measurement, not the calendar" rule (per
    ``promptpotter/CLAUDE.md``) is structural: there is no field to assign
    a ``round_num >= N`` literal to.
    """

    __slots__ = (
        "_l1_stall_count",
        "_l2_best_accuracy_at_entry",
        "_l2_best_composite_fitness_at_entry",
        "_l2_round",
        "_l2_stall_count",
        "_l3_best_accuracy_at_entry",
        "_l3_best_composite_fitness_at_entry",
        "_l3_round",
        "_l3_stall_count",
    )

    def __init__(self) -> None:
        self._l1_stall_count = 0
        self._l2_round = 0
        self._l2_stall_count = 0
        self._l2_best_accuracy_at_entry = 0.0
        self._l2_best_composite_fitness_at_entry = 0.0
        self._l3_round = 0
        self._l3_stall_count = 0
        self._l3_best_accuracy_at_entry = 0.0
        self._l3_best_composite_fitness_at_entry = 0.0

    # ---- Read-only access (telemetry, decision payloads, prompt vars) ----

    @property
    def l1_stall_count(self) -> int:
        return self._l1_stall_count

    @property
    def l2_round(self) -> int:
        return self._l2_round

    @property
    def l2_stall_count(self) -> int:
        return self._l2_stall_count

    @property
    def l2_best_accuracy_at_entry(self) -> float:
        return self._l2_best_accuracy_at_entry

    @property
    def l2_best_composite_fitness_at_entry(self) -> float:
        return self._l2_best_composite_fitness_at_entry

    @property
    def l3_round(self) -> int:
        return self._l3_round

    @property
    def l3_stall_count(self) -> int:
        return self._l3_stall_count

    @property
    def l3_best_accuracy_at_entry(self) -> float:
        return self._l3_best_accuracy_at_entry

    @property
    def l3_best_composite_fitness_at_entry(self) -> float:
        return self._l3_best_composite_fitness_at_entry

    # ---- Observations: the only mutation surface ----

    def observe_round(
        self,
        *,
        improved: bool,
        current_accuracy: float,
        l1_patience: int,
        enable_l2: bool,
    ) -> EscalationEvent:
        """L1 round outcome. Bumps the stall counter; returns CONTINUE /
        FIRE_L2 / STOP_PERFECT / STOP_L1_PATIENCE.

        L2/L3 stall observation lives in :meth:`observe_l2_escalation` so
        the mid-round signal path (DegradationCheck) shares the same cascade.
        """
        self._l1_stall_count = 0 if improved else self._l1_stall_count + 1

        if current_accuracy >= 1.0:
            return EscalationEvent(
                next_action=NextAction.STOP_PERFECT,
                stall_depth=self._l1_stall_count,
                reason="composite_fitness >= 1.0",
            )
        if self._l1_stall_count < l1_patience:
            return EscalationEvent(
                next_action=NextAction.CONTINUE,
                stall_depth=self._l1_stall_count,
                reason=f"L1 stall {self._l1_stall_count}/{l1_patience}",
            )
        if not enable_l2:
            return EscalationEvent(
                next_action=NextAction.STOP_L1_PATIENCE,
                stall_depth=self._l1_stall_count,
                reason="L1 patience exhausted; L2 disabled",
            )
        return EscalationEvent(
            next_action=NextAction.FIRE_L2,
            stall_depth=self._l1_stall_count,
            reason="L1 patience exhausted -> L2",
        )

    def observe_l2_escalation(
        self,
        *,
        current_composite_fitness: float,
        l2_patience: int | None,
        l3_patience: int | None,
        enable_l3: bool,
    ) -> EscalationEvent:
        """L2 escalation requested (L1 patience or mid-round signal). Updates
        the L2 stall counter (and L3's when cascading); returns FIRE_L2 /
        FIRE_L3 / STOP_L2_PATIENCE / STOP_L3_PATIENCE.

        First-invocation grace: ``stall_count`` only advances after a layer
        has fired at least once (``round > 0``) — the prior best composite_fitness
        captured at entry is the comparator.
        """
        if self._l2_round > 0:
            l2_improved = current_composite_fitness > self._l2_best_composite_fitness_at_entry
            self._l2_stall_count = 0 if l2_improved else self._l2_stall_count + 1

        if l2_patience is None or self._l2_stall_count < l2_patience:
            return EscalationEvent(
                next_action=NextAction.FIRE_L2,
                stall_depth=self._l2_stall_count,
                reason=f"L2 stall {self._l2_stall_count}/{l2_patience}",
            )

        if not enable_l3:
            return EscalationEvent(
                next_action=NextAction.STOP_L2_PATIENCE,
                stall_depth=self._l2_stall_count,
                reason="L2 patience exhausted; L3 disabled",
            )

        if self._l3_round > 0:
            l3_improved = current_composite_fitness > self._l3_best_composite_fitness_at_entry
            self._l3_stall_count = 0 if l3_improved else self._l3_stall_count + 1

        if l3_patience is None or self._l3_stall_count < l3_patience:
            return EscalationEvent(
                next_action=NextAction.FIRE_L3,
                stall_depth=self._l3_stall_count,
                reason=f"L2 patience -> L3 stall {self._l3_stall_count}/{l3_patience}",
            )

        return EscalationEvent(
            next_action=NextAction.STOP_L3_PATIENCE,
            stall_depth=self._l3_stall_count,
            reason="L3 patience exhausted",
        )

    # ---- Post-fire bookkeepers ----

    def record_l2_fired(self, *, best_accuracy: float, best_composite_fitness: float) -> None:
        """L2 LLM completed. Bumps L2 round, captures entry baseline; resets L1 stall."""
        self._l1_stall_count = 0
        self._l2_round += 1
        self._l2_best_accuracy_at_entry = best_accuracy
        self._l2_best_composite_fitness_at_entry = best_composite_fitness

    def record_l3_fired(self, *, best_accuracy: float, best_composite_fitness: float) -> None:
        """L3 LLM completed. Bumps L3 round, captures entry; resets L1 stall and
        the L2 counter — under a new plan, L2's prior progress is invalidated.
        """
        self._l1_stall_count = 0
        self._l3_round += 1
        self._l3_best_accuracy_at_entry = best_accuracy
        self._l3_best_composite_fitness_at_entry = best_composite_fitness
        self._l2_round = 0
        self._l2_stall_count = 0
        self._l2_best_accuracy_at_entry = best_accuracy
        self._l2_best_composite_fitness_at_entry = best_composite_fitness

    # ---- Reducer over the ledger ----
    #
    # State is the fold of three PhaseRecord signals: round-complete (improved →
    # L1 stall), l2_context exit (L2 fired → l2 state), l3_plan exit (L3
    # fired → l3 state, l2 reset). The live mutators above are the in-memory
    # cache of this fold; ``from_ledger`` rebuilds the same value on resume.

    def fold(self, record: CycleRecord) -> None:
        """Advance state from one ledger record. No-op for unrelated records."""
        if not isinstance(record, PhaseRecord):
            return
        if record.phase == "round" and record.event == "complete":
            # Audit emit only: display fires under event="display" and is
            # never folded. The lean scalar payload is the SoT for resume.
            self._l1_stall_count = (
                0 if bool(record.payload["improved"]) else self._l1_stall_count + 1
            )
        elif record.phase == "l2_context" and record.event == "exit":
            escalation_state = record.payload.get("data") or {}
            self._l1_stall_count = 0
            self._l2_round = int(escalation_state["l2_round"])
            self._l2_stall_count = int(escalation_state["l2_stall_count"])
            self._l2_best_accuracy_at_entry = float(escalation_state["l2_best_accuracy_at_entry"])
            self._l2_best_composite_fitness_at_entry = float(
                escalation_state["l2_best_composite_fitness_at_entry"]
            )
        elif record.phase == "l3_plan" and record.event == "exit":
            escalation_state = record.payload.get("data") or {}
            best_acc = float(escalation_state["l3_best_accuracy_at_entry"])
            best_comp = float(escalation_state["l3_best_composite_fitness_at_entry"])
            self._l1_stall_count = 0
            self._l3_round = int(escalation_state["l3_round"])
            self._l3_stall_count = int(escalation_state["l3_stall_count"])
            self._l3_best_accuracy_at_entry = best_acc
            self._l3_best_composite_fitness_at_entry = best_comp
            # record_l3_fired wipes L2 — under a new plan, prior L2 stall is gone.
            self._l2_round = 0
            self._l2_stall_count = 0
            self._l2_best_accuracy_at_entry = best_acc
            self._l2_best_composite_fitness_at_entry = best_comp

    @classmethod
    def from_ledger(cls, ledger: CycleLedger | None) -> EscalationState:
        """Rebuild state by folding every record in ``ledger``. ``None`` ⇒ fresh state."""
        s = cls()
        if ledger is None:
            return s
        for rec in ledger.iter():
            s.fold(rec)
        return s


@dataclass
class TrackingState:
    """Current/best searchpoint trajectory + frozen baseline composite_fitness."""

    current_sp: JobSearchPoint | None = None
    current_accuracy: float = 0.0
    current_composite_fitness: float = 0.0
    current_results: list[dict] = field(default_factory=list)
    best_accuracy: float = 0.0
    best_composite_fitness: float = 0.0
    best_round: int = -1
    best_sp: JobSearchPoint | None = None
    baseline_composite_fitness: float = 0.0


@dataclass
class Cycle:
    """Mutable orchestration state for the feedback cycle round loop."""

    session: Session
    config: CampaignConfig

    rounds: list[RoundResult] = field(default_factory=list)
    tracking: TrackingState = field(default_factory=TrackingState)
    opt_sp: OptSearchPoint = field(default_factory=OptSearchPoint)
    probe_next_round: bool = False
    axes: AxisIndex | None = None
    escalation: EscalationState = field(default_factory=EscalationState)
    # Flushed into the next round_data's `decisions` before campaign_store.save_round_file.
    pending_decisions: list[DecisionRecord] = field(default_factory=list)
    state_version: int = 1
    # Round-end Rasch posterior; one fit per round, reused by finalize.
    last_rasch_posterior: Any = None

    @property
    def latest_diagnostics(self) -> Any | None:
        """Most recent round's :class:`RoundDiagnostics`, if any."""
        return self.rounds[-1].diagnostics if self.rounds else None

    @classmethod
    def start(
        cls,
        baseline_osp: OptSearchPoint,
        baseline_accuracy: float,
        *,
        task_context: TaskDecomposition,
        schema: PipelineSchema | None,
        baseline_results: list[dict] | None = None,
        round_scorer: Any = None,
        session: Session,
        config: CampaignConfig,
    ) -> Cycle:
        """Construct a fresh Cycle from a scored baseline."""
        composite_fitness = (
            compute_composite_fitness(
                baseline_results,  # type: ignore[arg-type]
                schema,
                round_scorer=round_scorer,
            )["composite_fitness"]
            if baseline_results and schema is not None
            else baseline_accuracy
        )
        opt_sp = baseline_osp.model_copy(
            update={
                "task_context": task_context,
                "optimizer_params": dict(baseline_osp.optimizer_params),
            }
        )
        sp = opt_sp.to_job_search_point(
            base_pipeline_params=schema.to_pipeline_params() if schema else None,
            schema=schema,
        )
        return cls(
            session=session,
            config=config,
            tracking=TrackingState(
                current_sp=sp,
                current_accuracy=baseline_accuracy,
                current_composite_fitness=composite_fitness,
                current_results=baseline_results or [],
                best_accuracy=baseline_accuracy,
                best_composite_fitness=composite_fitness,
                best_sp=sp,
                baseline_composite_fitness=composite_fitness,
            ),
            opt_sp=opt_sp,
        )

    def restore_from_trial(self, round_data: dict[str, Any]) -> None:
        """Restore optimizer state from a campaign checkpoint dict (in-place).

        ``EscalationState`` is NOT read from the round_data — it's a projection of
        the ledger and must be rebuilt by the resume path via
        ``EscalationState.from_ledger``.
        """
        self.opt_sp = OptSearchPoint(**round_data["opt_search_point"])

    def absorb_round(
        self,
        rr: RoundResult,
        critique_text: str,
        round_num: int,
    ) -> dict[str, Any]:
        """Sole sink for a finished L1 round: fold optimizer-memory onto opt_sp,
        append the round, propagate tracking, project the trial dict.

        l1.py never mutates Cycle — it returns the round result + critique
        text and the runner calls this once at the round boundary. The
        returned dict is the input to ``save_round_file`` on the normal
        path; probe and escalation paths discard it.
        """
        schema = self.session.pipeline_schema
        tr = self.tracking

        # 1. opt_sp memory — critique, failure analysis, warning inventory, runtime failures.
        self.opt_sp.l1_critique_text = critique_text
        if rr.results and schema is not None:
            self.opt_sp.failure_analysis = compile_failure_analysis(
                cast("list[QueryMeasurement]", rr.results), schema
            )
        else:
            self.opt_sp.failure_analysis = None
        all_results: list = [r for rs in rr.all_candidate_results.values() for r in rs]
        if all_results:
            update_query_tracker(self.opt_sp.warning_inventory, all_results)
        existing_keys = {_rf_dedup_key(rf.to_dict()) for rf in self.opt_sp.runtime_failures}
        for cs in rr.candidate_scores:
            for rf_dict in cs.get("runtime_failures") or []:
                k = _rf_dedup_key(rf_dict)
                if k in existing_keys:
                    continue
                existing_keys.add(k)
                self.opt_sp.runtime_failures.append(RuntimeFailure(**rf_dict))

        # 2. round + history + tracking.
        self.rounds.append(rr)
        self.opt_sp.round_history.append(
            RoundSummary(
                round=rr.round,
                accuracy=rr.accuracy,
                composite_fitness=rr.composite_fitness,
                improved=rr.improved,
                degraded_queries=rr.degraded_queries,
                pipeline_params=rr.pipeline_params,
                candidate_scores=list(rr.candidate_scores),
            )
        )
        for f in PROMPT_STRING_FIELDS:
            setattr(self.opt_sp, f, rr.prompt_fields.get(f, ""))
        assert tr.current_sp is not None
        _pp = (
            rr.pipeline_params if rr.pipeline_params is not None else tr.current_sp.pipeline_params
        )
        tr.current_sp = self.opt_sp.to_job_search_point(base_pipeline_params=_pp, schema=schema)
        tr.current_accuracy = rr.accuracy
        tr.current_composite_fitness = rr.composite_fitness
        tr.current_results = list(rr.results)
        if tr.current_composite_fitness > tr.best_composite_fitness:
            tr.best_composite_fitness = tr.current_composite_fitness
            tr.best_accuracy = tr.current_accuracy
            tr.best_round = round_num
            tr.best_sp = tr.current_sp

        # 3. trial dict — pure projection of post-mutation state.
        return {
            "round_id": f"round_{round_num}",
            "round": round_num,
            "label": rr.label,
            "accuracy": rr.accuracy,
            "composite_fitness": rr.composite_fitness,
            "hits": rr.hits,
            "total": rr.total,
            "improved": rr.improved,
            "p_value": rr.p_value,
            "baseline_accuracy": rr.baseline_accuracy,
            "scoreboard": _build_scoreboard(rr.candidate_scores, rr.label),
            "prompt_fields": rr.prompt_fields,
            "results": rr.results,
            "all_candidate_results": dict(rr.all_candidate_results),
            "candidates_scored": rr.candidates_scored,
            "candidate_scores": list(rr.candidate_scores),
            "decisions": list(rr.decisions),
            "evaluators": dict(rr.evaluators),
            "opt_search_point": self.opt_sp.model_dump(),
            **(
                {"scoring_set_events": list(rr.scoring_set_events)} if rr.scoring_set_events else {}
            ),
        }

    def baseline_for_round(self, scoring_set: list[Sample], round_num: int) -> RoundBaseline:
        """Build round baseline; on probe rounds, rescore over the probe subset."""
        schema = self.session.pipeline_schema
        tr = self.tracking
        accuracy = tr.current_accuracy
        composite_fitness = tr.current_composite_fitness
        results: list[dict] = list(tr.current_results)
        if self.probe_next_round and tr.current_results and schema is not None:
            probe_queries = {s.query for s in scoring_set}
            subset = [r for r in tr.current_results if r.get("query") in probe_queries]
            if subset:
                subset_scores = compute_composite_fitness(
                    cast("list[QueryMeasurement]", subset),
                    schema,
                    round_scorer=self.session.scoring.round_scorer,
                )
                accuracy = subset_scores["accuracy"]
                composite_fitness = subset_scores.get("composite_fitness", accuracy)
                results = subset
        return RoundBaseline(
            accuracy=accuracy,
            composite_fitness=composite_fitness,
            osp=self.opt_sp,
            results=results,
            label=f"round_{round_num}" if round_num > 0 else "baseline",
        )


# ---------------------------------------------------------------------------
# Resume divergence-check + fork
# ---------------------------------------------------------------------------


def resume_with_divergence_check(
    campaign_store: CampaignStore,
    backend_id: str,
    cycle_id: str,
    resumed_from_round: int,
    session: Session,
    cycle: Cycle,
    *,
    skip_divergence_check: bool,
    fork_on_divergence: bool = False,
) -> ForkResult | None:
    """Rescore prior rounds under the active scorer; halt or fork on divergence."""
    sc = session.scoring
    scorer = sc.scorer
    assert scorer is not None, "session.scoring.scorer required for divergence replay"
    prior = campaign_store.load_rounds_range(backend_id, cycle_id, 0, resumed_from_round - 1)

    def _rescore(items: Any) -> list:
        out = list(items or [])
        rescore_results(out, scorer, sc.scorer_id, sc.scorer_formula)
        return out

    for t in prior:
        _rescore(t.get("results"))
        for items in (t.get("all_candidate_results") or {}).values():
            _rescore(items)

    baseline_results_rescored = _rescore(cycle.tracking.current_results)

    if not skip_divergence_check:
        for i, t in enumerate(prior):
            div = replay_decisions(
                t,
                prior_rounds=prior[:i],
                baseline_results=baseline_results_rescored,
            )
            if div is None:
                continue
            if fork_on_divergence:
                survivors = list(prior[:i])
                new_cycle_id = _fork_at_divergence(
                    campaign_store,
                    session.store.tenant_id,
                    session.session_id,
                    cycle_id,
                    div.round_num,
                    survivors,
                )
                if survivors:
                    cycle.restore_from_trial(survivors[-1])
                cycle.escalation = EscalationState.from_ledger(session.state.ledger)
                logger.warning(
                    "Resume diverged at round %d (%s); forked → %s",
                    div.round_num,
                    div.kind,
                    new_cycle_id,
                )
                return ForkResult(
                    new_cycle_id=new_cycle_id,
                    new_resumed_from_round=div.round_num,
                )
            raise ResumeDivergenceError(
                round_num=div.round_num,
                kind=div.kind,
                recorded_outcome=div.recorded_outcome,
                current_outcome=div.current_outcome,
                diagnostics={
                    "scorer_id": sc.scorer_id,
                    "fork_hint": (
                        "rerun `optimize --fork-on-divergence` to branch a new "
                        "cycle here under the current scorer"
                    ),
                },
            )

    if prior:
        cycle.restore_from_trial(prior[-1])
    cycle.escalation = EscalationState.from_ledger(session.state.ledger)
    return None


# ---------------------------------------------------------------------------
# Escalation journal helper — shapes a journal entry from DegradationCheck.
# The action driver itself lives in :mod:`escalation`.
# ---------------------------------------------------------------------------


def build_escalation_entry(
    round_num: int,
    check_result: dict[str, Any],
    current_pipeline_params: dict | None,
) -> dict[str, Any]:
    """Shape a journal entry from a DegradationCheck result + live pipeline params."""
    dominant = check_result.get("dominant_warning", "unknown:unknown")
    problem_step = dominant.split(":")[0] if ":" in dominant else "unknown"
    step_cfg = (current_pipeline_params or {}).get(problem_step, {})
    return {
        "round": round_num,
        "degraded_rate": check_result.get("degraded_rate", 0),
        "problem_step": problem_step,
        "step_config": dict(step_cfg) if isinstance(step_cfg, dict) else {},
        "warning_types": check_result.get("warning_types", {}),
        "outcome_degraded_rate": None,
    }
