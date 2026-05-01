"""Optimization cycle state — pure optimizer progress tracking.

Three layers in one file:

1. **Decision ledger** (``Decision``, ``Divergence``, ``record_decision``,
   ``replay_decisions``, ``REPLAYERS``) — recorded decisions that drive
   resume divergence-checking. Each decision is two-tier: ``inputs_ref`` +
   ``outcome`` are compared on resume; ``data`` is archival.

2. **Cycle state** (``LayerCounter``, ``EscalationState``, ``Cycle``) —
   the mutable orchestration state for the feedback cycle round loop.
   ``Cycle`` aggregates rounds, current/best tracking, escalation counters,
   and pending decisions.

3. **Layer escalation** (``escalate_l2``, ``build_escalation_entry``) —
   the action driver that fires L2 (and optionally L3) transitions when
   patience exhausts. Uses ``record_decision`` to log trigger gates.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

from promptpotter.application.optimization.pipeline import (
    L2RefineStrategy,
    L3ModifyPlan,
    LayerTransition,
)
from promptpotter.domain.analysis import RuntimeFailure
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.phases import PhaseEvent, StopReason, emit_phase
from promptpotter.domain.results import RoundBaseline, RoundResult
from promptpotter.domain.run_records import (
    DECISION_GATING,
    Decision,
    DecisionKind,
    GatingMode,
    SweepPayload,
)
from promptpotter.domain.search_point import JobSearchPoint
from promptpotter.infrastructure import llm as _llm_client
from promptpotter.infrastructure.tracing import LayerApplied, observed_node
from promptpotter.shared.errors import graceful

if TYPE_CHECKING:
    from promptpotter.application.bootstrap import Session
    from promptpotter.application.config import CampaignConfig
    from promptpotter.application.intelligence.indexes import AxisIndex
    from promptpotter.application.optimization.l1 import L1ScoringResult
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.scoring import QueryResult
    from promptpotter.domain.search_point import TaskDecomposition
    from promptpotter.infrastructure.store import CampaignStore
    from promptpotter.infrastructure.tracing import ObservabilityBridge

logger = logging.getLogger(__name__)

__all__ = [
    "REPLAYERS",
    "Cycle",
    "Decision",
    "Divergence",
    "EscalationState",
    "ForkResult",
    "LayerCounter",
    "ReplayContext",
    "_fork_for_diag_sibling",
    "apply_sweep_payload_to_osp",
    "build_escalation_entry",
    "escalate_l2",
    "record_decision",
    "replay_decisions",
    "replayer",
    "resume_with_divergence_check",
]


# ---------------------------------------------------------------------------
# Decision ledger — records that drive resume divergence-checking
# ---------------------------------------------------------------------------
#
# ``Decision`` and ``DecisionKind`` are defined in
# :mod:`promptpotter.domain.run_records` so the broader ledger spine can
# reach them. They are re-exported here for compatibility with call sites
# that still import from this module.


def _build_scoreboard(
    candidate_scores: list[dict[str, Any]], winner_label: str
) -> list[dict[str, Any]]:
    """Rank candidates by composite (desc), then accuracy (desc); tag the winner.

    The trial JSON's ``scoreboard`` field — a renderer-friendly array that
    callers (CLI, log.md, webapp) read instead of re-deriving from
    ``candidate_scores``. Source of truth for rank is composite-then-accuracy;
    the winner is identified by ``changes_description == winner_label``.
    """
    ranked = sorted(
        candidate_scores,
        key=lambda c: (c.get("composite", c.get("accuracy", 0.0)), c.get("accuracy", 0.0)),
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
                "composite": c.get("composite"),
                "hits": c.get("hits"),
                "total": c.get("total"),
                "ci_lo": c.get("ci_lo"),
                "ci_hi": c.get("ci_hi"),
                "is_winner": is_winner,
                "escalation_aborted": c.get("escalation_aborted", False),
            }
        )
    return rows


@dataclass(frozen=True)
class Divergence:
    """A recorded decision re-derived to a different outcome under the current scorer."""

    round_num: int
    kind: str
    recorded_outcome: Any
    current_outcome: Any
    inputs_ref: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ReplayContext:
    """Context passed to replayers — trial + prior_trials + baseline_results all rescored."""

    trial: dict[str, Any]
    prior_trials: list[dict[str, Any]] = field(default_factory=list)
    baseline_results: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class ForkResult:
    """Resume detected divergence and forked into a sibling cycle."""

    new_cycle_id: str
    new_resumed_from_round: int


Replayer = Callable[[ReplayContext, dict[str, Any]], Any]

REPLAYERS: dict[DecisionKind, Replayer] = {}


def replayer(kind: DecisionKind) -> Callable[[Replayer], Replayer]:
    """Register a replayer function for a ``REPLAYED`` decision kind.

    ``ARCHIVAL`` kinds (per ``DECISION_GATING``) MUST NOT be registered;
    ``test_decision_kinds_registry.py`` enforces the pairing.
    """
    if DECISION_GATING.get(kind) is not GatingMode.REPLAYED:
        raise ValueError(
            f"replayer registered for {kind!r}, which is not REPLAYED in DECISION_GATING"
        )

    def deco(fn: Replayer) -> Replayer:
        REPLAYERS[kind] = fn
        return fn

    return deco


def record_decision(
    decisions: list[Decision],
    kind: DecisionKind,
    inputs_ref: dict[str, Any],
    outcome: Any,
    *,
    data: dict[str, Any] | None = None,
) -> Any:
    """Append a ``Decision`` to *decisions* and return *outcome* for passthrough.

    ``Decision.to_dict()`` projects to the legacy wire shape when the
    in-memory list is folded into ``RoundResult.decisions`` (which stays
    ``list[dict]`` for Pydantic + JSON wire compatibility).
    """
    decisions.append(
        Decision(
            kind=kind,
            inputs_ref=dict(inputs_ref),
            outcome=outcome,
            data=dict(data or {}),
        )
    )
    return outcome


def replay_decisions(
    trial: dict[str, Any],
    prior_trials: list[dict[str, Any]] | None = None,
    baseline_results: list[dict[str, Any]] | None = None,
) -> Divergence | None:
    """Walk ``trial['decisions']`` in order; return the first mismatch."""
    ctx = ReplayContext(
        trial=trial,
        prior_trials=list(prior_trials or []),
        baseline_results=list(baseline_results or []),
    )
    for rec in trial.get("decisions") or []:
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
                round_num=int(trial.get("round", -1)),
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
    return sum(float(r.get("score", 0.0)) for r in results) / len(results)


@replayer(DecisionKind.ROUND_WINNER)
def _replay_round_winner(ctx: ReplayContext, inputs_ref: dict[str, Any]) -> str:
    """Re-derive round winner from rescored per-candidate results; beat-threshold derived, not read."""
    all_results: dict[str, list[dict]] = ctx.trial.get("all_candidate_results") or {}
    if ctx.prior_trials:
        best_acc = _mean_score(list(ctx.prior_trials[-1].get("results") or []))
    else:
        best_acc = _mean_score(list(ctx.baseline_results))
    winner_id = ""
    for cid in inputs_ref.get("candidate_ids") or []:
        acc = _mean_score(all_results.get(cid) or [])
        if acc > best_acc:
            best_acc = acc
            winner_id = cid
    return winner_id


@replayer(DecisionKind.ELIMINATION_CUT)
def _replay_elimination_cut(ctx: ReplayContext, inputs_ref: dict[str, Any]) -> bool:
    """Re-run the Bayesian PoBB gate under rescored scores.

    Inputs accept both the new ``epsilon`` knob and (for backward-compat
    on archived ledgers) the legacy ``alpha`` field; either resolves to the
    same threshold semantics — stop when the candidate's P(best) drops
    below it.
    """
    from promptpotter.shared.statistics import (
        pobb_should_stop,
        posterior_best_probabilities,
    )

    all_results: dict[str, list[dict]] = ctx.trial.get("all_candidate_results") or {}
    candidate_id = str(inputs_ref.get("candidate_id", ""))
    prior_ids = list(inputs_ref.get("prior_candidate_ids") or [])
    n = int(inputs_ref.get("queries_scored", 0))
    # Prefer the PoBB knob; fall back to legacy ``alpha`` (which used a
    # 0.2 family-wise default) re-mapped to a more conservative ε so old
    # ledgers don't replay as confident stops.
    epsilon_val = inputs_ref.get("epsilon")
    if epsilon_val is None:
        epsilon_val = float(inputs_ref.get("alpha", 0.2)) * 0.25
    epsilon = float(epsilon_val)

    current = [float(r.get("score", 0.0)) for r in (all_results.get(candidate_id) or [])[:n]]
    priors = {
        pid: [float(r.get("score", 0.0)) for r in (all_results.get(pid) or [])] for pid in prior_ids
    }
    priors = {pid: p for pid, p in priors.items() if p}
    if not priors or len(current) < 2:
        return False
    histories = {**priors, candidate_id: current}
    snapshot = posterior_best_probabilities(histories)
    return pobb_should_stop(snapshot.get(candidate_id, 1.0), epsilon)


@replayer(DecisionKind.LEADER_LOCK_IN)
def _replay_leader_lock_in(ctx: ReplayContext, inputs_ref: dict[str, Any]) -> bool:
    """Re-run the PoBB lock-in gate under rescored scores.

    Mirrors ``_replay_elimination_cut`` but checks the leader condition:
    current candidate is the snapshot argmax AND its P(best) ≥ lock_in
    threshold AND it had at least ``lock_in_n_min`` queries.
    """
    from promptpotter.shared.statistics import posterior_best_probabilities

    all_results: dict[str, list[dict]] = ctx.trial.get("all_candidate_results") or {}
    candidate_id = str(inputs_ref.get("candidate_id", ""))
    prior_ids = list(inputs_ref.get("prior_candidate_ids") or [])
    n = int(inputs_ref.get("queries_scored", 0))
    lock_in = float(inputs_ref.get("lock_in", 0.95))
    lock_in_n_min = int(inputs_ref.get("lock_in_n_min", 8))

    if n < lock_in_n_min:
        return False
    current = [float(r.get("score", 0.0)) for r in (all_results.get(candidate_id) or [])[:n]]
    priors = {
        pid: [float(r.get("score", 0.0)) for r in (all_results.get(pid) or [])] for pid in prior_ids
    }
    priors = {pid: p for pid, p in priors.items() if p}
    if not priors or len(current) < 2:
        return False
    histories = {**priors, candidate_id: current}
    snapshot = posterior_best_probabilities(histories)
    leader = max(snapshot.items(), key=lambda kv: kv[1])[0]
    return leader == candidate_id and snapshot.get(candidate_id, 0.0) >= lock_in


def _derive_stall_count(
    prior_trials: list[dict[str, Any]],
    entry_round: int,
    this_round: int,
) -> int:
    """Reconstruct stall_count at end of this_round."""
    if entry_round < 0:
        return 0
    sorted_trials = sorted(prior_trials, key=lambda t: int(t.get("round", -1)))
    running_max = 0.0
    baseline: float | None = None
    rounds_after = 0
    for t in sorted_trials:
        r = int(t.get("round", -1))
        if r < 0 or r > this_round:
            continue
        winner_results = t.get("results") or []
        comp = (
            _mean_score(winner_results)
            if winner_results
            else float(t.get("composite", t.get("accuracy", 0.0)))
        )
        running_max = max(running_max, comp)
        if r <= entry_round:
            if r == entry_round:
                baseline = running_max
            continue
        rounds_after += 1
        if baseline is not None and running_max > baseline:
            return 0
    return rounds_after if baseline is not None else 0


@replayer(DecisionKind.L2_ESCALATION_TRIGGER)
def _replay_l2_trigger(ctx: ReplayContext, inputs_ref: dict[str, Any]) -> bool:
    """Re-derive L2 fire/patience-defer."""
    patience = inputs_ref.get("l2_patience")
    if patience is None:
        return True
    entry_round = int(inputs_ref.get("entry_round", -1))
    this_round = int(inputs_ref.get("round_num", -1))
    stalls = _derive_stall_count(ctx.prior_trials, entry_round, this_round)
    return stalls < int(patience)


@replayer(DecisionKind.L3_ESCALATION_TRIGGER)
def _replay_l3_trigger(ctx: ReplayContext, inputs_ref: dict[str, Any]) -> bool:
    """Re-derive whether L3 fires. Same shape as ``l2_escalation_trigger``."""
    patience = inputs_ref.get("l3_patience")
    if patience is None:
        return True
    entry_round = int(inputs_ref.get("entry_round", -1))
    this_round = int(inputs_ref.get("round_num", -1))
    stalls = _derive_stall_count(ctx.prior_trials, entry_round, this_round)
    return stalls < int(patience)


def _fork_at_divergence(
    campaign_store: CampaignStore,
    tenant_id: str,
    session_id: str,
    old_cycle_id: str,
    fork_from_round: int,
    surviving_trials: list[dict[str, Any]],
    extra_data: dict[str, Any] | None = None,
) -> str:
    """Mint a sibling cycle that re-runs round ``fork_from_round``.

    Records a ``Decision(kind=FORK_CUT, ...)`` on the parent's ledger
    naming the new cycle id and the fork-from-round so any downstream
    reader following the parent's ``events.jsonl`` sees the cutover
    inline. The fork's own ledger inherits from the parent up to (but
    not including) the FORK_CUT — wired in :mod:`runner` after the
    fork is detected.

    ``extra_data`` is an optional dict merged into the FORK_CUT
    decision's archival ``data`` block. The scoring-divergence caller
    passes nothing; the M10 sweep caller threads the parsed
    ``SweepPayload`` (under key ``sweep_payload``) so leaderboard
    rendering and downstream review can attribute the fork.
    """
    from promptpotter.domain.cycle_paths import CycleDir
    from promptpotter.infrastructure.ledger import RunLedger
    from promptpotter.infrastructure.store.base import read_json_optional, write_json
    from promptpotter.infrastructure.store.stores import save_active_pointer

    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    suffix = hashlib.sha256(f"{old_cycle_id}|{ts}".encode()).hexdigest()[:8]
    new_cycle_id = f"{old_cycle_id}_fork_{suffix}"
    old_dir = campaign_store.campaign_dir(old_cycle_id)
    new_dir = campaign_store.campaign_dir(new_cycle_id)
    if new_dir.exists():
        raise FileExistsError(f"forked cycle dir already exists: {new_dir}")
    new_dir.mkdir(parents=True, exist_ok=True)

    # Parent ledger gets a FORK_CUT record naming the new cycle so a
    # tail of the parent sees the cutover. Best-effort — a missing or
    # corrupt ledger doesn't block the fork from minting.
    decision_data: dict[str, Any] = {"forked_at": datetime.now(UTC).isoformat()}
    if extra_data:
        decision_data.update(extra_data)
    with graceful("FORK_CUT decision append failed"):
        parent_ledger = RunLedger.open(CycleDir(old_dir))
        parent_ledger.append(
            Decision(
                kind=DecisionKind.FORK_CUT,
                inputs_ref={"from_round": fork_from_round},
                outcome=new_cycle_id,
                data=decision_data,
            )
        )

    index = read_json_optional(old_dir / "index.json") or {}
    best_acc = max((float(t.get("accuracy", 0.0)) for t in surviving_trials), default=0.0)
    best_trial_id = next(
        (t.get("trial_id") for t in surviving_trials if float(t.get("accuracy", 0.0)) == best_acc),
        None,
    )
    now = datetime.now(UTC).isoformat()
    index.update(
        {
            "campaign_id": new_cycle_id,
            "parent_cycle_id": old_cycle_id,
            "forked_from_round": fork_from_round,
            "forked_at": now,
            "trials": list(surviving_trials),
            "n_trials": len(surviving_trials),
            "best_accuracy": best_acc,
            "best_trial_id": best_trial_id,
            "status": "resumed",
            "updated_at": now,
        }
    )
    write_json(new_dir / "index.json", index)

    for sub, prefix in (("trials", "trial_"), ("candidates", "round_")):
        src = old_dir / sub
        if not src.exists():
            continue
        dst = new_dir / sub
        dst.mkdir(parents=True, exist_ok=True)
        for p in sorted(src.glob(f"{prefix}*.json")):
            try:
                n = int(p.stem.removeprefix(prefix))
            except ValueError:
                continue
            if n < fork_from_round:
                shutil.copyfile(p, dst / p.name)

    save_active_pointer(tenant_id, session_id, new_cycle_id)
    logger.info(
        "Forked cycle %s → %s at round %d (active pointer retargeted)",
        old_cycle_id,
        new_cycle_id,
        fork_from_round,
    )
    return new_cycle_id


def _next_diag_sibling_id(campaign_store: CampaignStore, parent_cycle_id: str) -> str:
    """Allocate the next ``{root}_diag_NNN`` id by counting existing siblings.

    Scans the family-root's ``forks/`` dir for entries already matching
    ``{root}_diag_NNN`` and returns ``NNN+1`` zero-padded to 3 digits. Diag
    siblings of nested cycles still root at the family root so the BFS tree
    flattens to one level under ``campaigns/{root}/forks/``."""
    from promptpotter.infrastructure.store.stores import root_cycle_id

    root_id = root_cycle_id(parent_cycle_id)
    forks_dir = campaign_store.campaign_dir(root_id) / "forks"
    pattern = re.compile(rf"^{re.escape(root_id)}_diag_(\d+)$")
    max_n = 0
    if forks_dir.is_dir():
        for entry in forks_dir.iterdir():
            if not entry.is_dir():
                continue
            m = pattern.match(entry.name)
            if m:
                max_n = max(max_n, int(m.group(1)))
    return f"{root_id}_diag_{max_n + 1:03d}"


def _fork_for_diag_sibling(
    campaign_store: CampaignStore,
    tenant_id: str,
    session_id: str,
    parent_cycle_id: str,
) -> str:
    """Mint a diag-BFS sibling cycle that re-runs from round 0 with no
    inherited trials.

    Each ``optimize --diag`` re-invocation against a finalized diag cycle
    branches off a new sibling instead of overwriting the parent's archive.
    The sibling shares the parent's baseline measurements via the JSP-keyed
    measurement archive (no copy needed). Records a ``Decision(kind=FORK_CUT)``
    on the parent's ledger so a tail of the parent's events captures the
    cutover. Active pointer retargets to the new sibling."""
    from promptpotter.domain.cycle_paths import CycleDir
    from promptpotter.infrastructure.ledger import RunLedger
    from promptpotter.infrastructure.store.base import read_json_optional, write_json
    from promptpotter.infrastructure.store.stores import save_active_pointer

    new_cycle_id = _next_diag_sibling_id(campaign_store, parent_cycle_id)
    parent_dir = campaign_store.campaign_dir(parent_cycle_id)
    new_dir = campaign_store.campaign_dir(new_cycle_id)
    if new_dir.exists():
        raise FileExistsError(f"diag sibling dir already exists: {new_dir}")
    new_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(UTC).isoformat()
    with graceful("FORK_CUT decision append failed"):
        parent_ledger = RunLedger.open(CycleDir(parent_dir))
        parent_ledger.append(
            Decision(
                kind=DecisionKind.FORK_CUT,
                inputs_ref={"from_round": 0},
                outcome=new_cycle_id,
                data={"forked_at": now, "kind": "diag_sibling"},
            )
        )

    parent_index = read_json_optional(parent_dir / "index.json") or {}
    new_index: dict[str, Any] = {
        "campaign_id": new_cycle_id,
        "type": parent_index.get("type", "optimization_loop"),
        "config": parent_index.get("config", {}),
        "connector_type": parent_index.get("connector_type", ""),
        "backend_id": parent_index.get("backend_id", ""),
        "parent_cycle_id": parent_cycle_id,
        "parent_session_id": parent_index.get("parent_session_id", ""),
        "forked_from_round": 0,
        "forked_at": now,
        "fork_kind": "diag_sibling",
        "trials": [],
        "n_trials": 0,
        "best_accuracy": 0.0,
        "best_trial_id": None,
        "baseline_accuracy": parent_index.get("baseline_accuracy", 0.0),
        "status": "active",
        "created_at": now,
        "updated_at": now,
    }
    write_json(new_dir / "index.json", new_index)

    save_active_pointer(tenant_id, session_id, new_cycle_id)
    logger.info(
        "Diag sibling: %s → %s (active pointer retargeted)",
        parent_cycle_id,
        new_cycle_id,
    )
    return new_cycle_id


def apply_sweep_payload_to_osp(opt_sp: OptSearchPoint, payload: SweepPayload) -> None:
    """Stamp ``SweepPayload`` L1-surface deltas onto the cycle's starting OSP.

    Mirrors the merge pattern in
    :meth:`L2RefineStrategy.apply_side_effects` so a sweep-supplied
    override behaves identically to one L2 would have written. Called
    after ``bootstrap_cycle`` returns the cycle and before the round
    loop reads ``cycle.opt_sp`` — the override then rides the
    cycle's existing checkpoint code into the trial JSON.
    """
    if payload.l1_section_overrides:
        opt_sp.l1_section_overrides = {
            **opt_sp.l1_section_overrides,
            **payload.l1_section_overrides,
        }
    if payload.l1_section_overrides_text:
        opt_sp.l1_section_overrides_text = {
            **opt_sp.l1_section_overrides_text,
            **payload.l1_section_overrides_text,
        }
    if payload.l1_template_override:
        opt_sp.l1_template_override = payload.l1_template_override
    if payload.directive:
        opt_sp.l2_directive = payload.directive


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


@dataclass
class LayerCounter:
    """Per-layer escalation tracking (stall count, round count, entry baseline)."""

    round: int = 0
    stall_count: int = 0
    best_accuracy_at_entry: float = 0.0
    best_composite_at_entry: float = 0.0

    def record_outcome(self, best_composite: float) -> bool:
        """Update stall_count after a round. Returns True if stalled (not improved)."""
        improved = best_composite > self.best_composite_at_entry
        self.stall_count = 0 if improved or self.round == 0 else self.stall_count + 1
        return not improved and self.round > 0

    def record_entry(self, best_accuracy: float, best_composite: float) -> None:
        self.round += 1
        self.best_accuracy_at_entry = best_accuracy
        self.best_composite_at_entry = best_composite


@dataclass
class EscalationState:
    """All escalation-layer tracking — L1 stall counter + L2/L3 ``LayerCounter`` instances."""

    l1_stall_count: int = 0
    l2: LayerCounter = field(default_factory=LayerCounter)
    l3: LayerCounter = field(default_factory=LayerCounter)

    def reset_for_l3(self, best_accuracy: float, best_composite: float) -> None:
        self.l2 = LayerCounter(
            best_accuracy_at_entry=best_accuracy,
            best_composite_at_entry=best_composite,
        )

    def to_checkpoint_dict(self) -> dict[str, Any]:
        """Serialize for trial dict — entry baselines required so resume preserves L2/L3 patience."""
        return {
            "l1_stall_count": self.l1_stall_count,
            "l2_round": self.l2.round,
            "l3_round": self.l3.round,
            "l2_stall_count": self.l2.stall_count,
            "l3_stall_count": self.l3.stall_count,
            "l2_best_accuracy_at_entry": self.l2.best_accuracy_at_entry,
            "l2_best_composite_at_entry": self.l2.best_composite_at_entry,
            "l3_best_accuracy_at_entry": self.l3.best_accuracy_at_entry,
            "l3_best_composite_at_entry": self.l3.best_composite_at_entry,
        }

    @classmethod
    def from_checkpoint_dict(cls, d: dict) -> EscalationState:
        """Restore — every key defaults so gen-only trials (sweep/diag stubs
        that never wrote escalation state) restore as a fresh counter."""
        return cls(
            l1_stall_count=d.get("l1_stall_count", 0),
            l2=LayerCounter(
                round=d.get("l2_round", 0),
                stall_count=d.get("l2_stall_count", 0),
                best_accuracy_at_entry=d.get("l2_best_accuracy_at_entry", 0.0),
                best_composite_at_entry=d.get("l2_best_composite_at_entry", 0.0),
            ),
            l3=LayerCounter(
                round=d.get("l3_round", 0),
                stall_count=d.get("l3_stall_count", 0),
                best_accuracy_at_entry=d.get("l3_best_accuracy_at_entry", 0.0),
                best_composite_at_entry=d.get("l3_best_composite_at_entry", 0.0),
            ),
        )


@dataclass
class Cycle:
    """Mutable orchestration state for the feedback cycle round loop."""

    session: Session
    config: CampaignConfig

    rounds: list[RoundResult] = field(default_factory=list)
    current_sp: JobSearchPoint | None = None
    current_accuracy: float = 0.0
    current_composite: float = 0.0
    current_results: list[dict] = field(default_factory=list)
    best_accuracy: float = 0.0
    best_composite: float = 0.0
    best_round: int = -1
    best_sp: JobSearchPoint | None = None
    # Frozen at ``start()`` — the baseline composite that anchors the
    # campaign's "origin" for trajectory rendering. Lets renderers print
    # ``Δ from baseline=0.5012`` even at deep rounds.
    baseline_composite: float = 0.0

    opt_sp: OptSearchPoint = field(default_factory=OptSearchPoint)

    probe_next_round: bool = False
    axes: AxisIndex | None = None
    escalation: EscalationState = field(default_factory=EscalationState)

    # Flushed into the next trial's ``decisions`` list before ``campaign_store.add_trial``.
    # Stored as ``Decision`` instances; converted to dict at the RoundResult boundary.
    pending_decisions: list[Decision] = field(default_factory=list)

    state_version: int = 1

    # Round-end Rasch posterior cached for finalize-time hard-sample
    # heatmap reuse (one fit per round, used by both scoring-set evolution
    # and the heatmap renderer). Not persisted across resume — recomputed
    # at the next round-end if needed.
    last_rasch_posterior: Any = None

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
        from promptpotter.application.scoring.metrics import compute_composite_score

        composite = (
            compute_composite_score(
                baseline_results,  # type: ignore[arg-type]
                schema,
                round_scorer=round_scorer,
            )["composite"]
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
            current_sp=sp,
            current_accuracy=baseline_accuracy,
            current_composite=composite,
            current_results=baseline_results or [],
            best_accuracy=baseline_accuracy,
            best_composite=composite,
            best_sp=sp,
            opt_sp=opt_sp,
            baseline_composite=composite,
        )

    def restore_from_trial(self, trial: dict[str, Any]) -> None:
        """Restore optimizer state from a campaign checkpoint dict (in-place)."""
        self.opt_sp = OptSearchPoint(**trial["opt_search_point"])
        self.escalation = EscalationState.from_checkpoint_dict(trial)

    def adopt_transition(
        self,
        new_opt: OptSearchPoint,
        pipeline_params: dict | None,
        *,
        schema: PipelineSchema | None,
    ) -> None:
        """Adopt a new OptSearchPoint, preserving accumulated memory."""
        self.opt_sp.copy_memory_to(new_opt)
        self.opt_sp = new_opt
        assert self.current_sp is not None
        self.current_sp = self.opt_sp.to_job_search_point(
            base_pipeline_params=pipeline_params or self.current_sp.pipeline_params,
            schema=schema,
        )

    def update_current(
        self,
        rr: RoundResult,
        search_point: JobSearchPoint,
        round_num: int,
    ) -> None:
        """Apply a round result to current/best tracking."""
        self.current_sp = search_point
        self.current_accuracy = rr.accuracy
        self.current_composite = rr.composite
        self.current_results = list(rr.results)
        if self.current_composite > self.best_composite:
            self.best_composite = self.current_composite
            self.best_accuracy = self.current_accuracy
            self.best_round = round_num
            self.best_sp = self.current_sp

    def record_round(self, rr: RoundResult, round_num: int) -> None:
        """Append a RoundResult and propagate to memory + current/best tracking."""
        from promptpotter.config.settings import PROMPT_STRING_FIELDS
        from promptpotter.domain.opt_search_point import RoundSummary

        schema = self.session.pipeline_schema
        self.rounds.append(rr)
        self.opt_sp.round_history.append(
            RoundSummary(
                round=rr.round,
                accuracy=rr.accuracy,
                composite=rr.composite,
                improved=rr.improved,
                degraded_queries=rr.degraded_queries,
                pipeline_params=rr.pipeline_params,
                candidate_scores=list(rr.candidate_scores),
            )
        )
        for f in PROMPT_STRING_FIELDS:
            setattr(self.opt_sp, f, rr.prompt_fields.get(f, ""))
        assert self.current_sp is not None
        _pp = (
            rr.pipeline_params
            if rr.pipeline_params is not None
            else self.current_sp.pipeline_params
        )
        self.update_current(
            rr,
            self.opt_sp.to_job_search_point(base_pipeline_params=_pp, schema=schema),
            round_num,
        )

    def record_decision(self, d: Decision) -> None:
        """Queue a decision produced outside the normal round flow (escalation/probe)."""
        self.pending_decisions.append(d)

    def flush_decisions(self) -> list[Decision]:
        """Drain queued decisions (used before checkpointing a round)."""
        out = list(self.pending_decisions)
        self.pending_decisions.clear()
        return out

    def set_probe(self, flag: bool) -> None:
        """Mark whether the next round is a probe."""
        self.probe_next_round = flag

    def apply_round_outcome(self, scoring_result: L1ScoringResult, critique_text: str) -> None:
        """Fold per-round optimizer-memory updates onto ``opt_sp`` atomically.

        Single mutation point for: l1_critique_text, failure_analysis,
        warning_inventory (from all candidate results), runtime_failures
        (deduped by source/warning/observed-config).
        """
        from promptpotter.application.optimization.elimination import update_query_tracker
        from promptpotter.application.scoring.metrics import compile_failure_analysis

        schema = self.session.pipeline_schema

        self.opt_sp.l1_critique_text = critique_text

        if scoring_result.winner_results and schema is not None:
            self.opt_sp.failure_analysis = compile_failure_analysis(
                scoring_result.winner_results, schema
            )
        else:
            self.opt_sp.failure_analysis = None

        # Aborted candidates also carry warnings — span all candidate results.
        all_results: list = [r for rs in scoring_result.all_candidate_results.values() for r in rs]
        if all_results:
            update_query_tracker(self.opt_sp.warning_inventory, all_results)

        existing_keys = {_rf_dedup_key(rf.to_dict()) for rf in self.opt_sp.runtime_failures}
        for cs in scoring_result.candidate_scores:
            for rf_dict in cs.runtime_failures:
                k = _rf_dedup_key(rf_dict)
                if k in existing_keys:
                    continue
                existing_keys.add(k)
                self.opt_sp.runtime_failures.append(RuntimeFailure(**rf_dict))

    def baseline_for_round(self, scoring_dataset: list[Sample], round_num: int) -> RoundBaseline:
        """Build the round's baseline — probe-aware.

        On a probe round, the previous winner's accuracy is recomputed over the
        probe subset (probe queries are typically harder, so a probe round
        without subset-rescore would always look like regression).
        """
        from promptpotter.application.scoring.metrics import compute_composite_score

        schema = self.session.pipeline_schema
        accuracy = self.current_accuracy
        composite = self.current_composite
        results: list[dict] = list(self.current_results)
        if self.probe_next_round and self.current_results and schema is not None:
            probe_queries = {s.query for s in scoring_dataset}
            subset = [r for r in self.current_results if r.get("query") in probe_queries]
            if subset:
                subset_scores = compute_composite_score(
                    cast("list[QueryResult]", subset),
                    schema,
                    round_scorer=self.session.scoring.round_scorer,
                )
                accuracy = subset_scores["accuracy"]
                composite = subset_scores.get("composite", accuracy)
                results = subset
        return RoundBaseline(
            accuracy=accuracy,
            composite=composite,
            osp=self.opt_sp,
            results=results,
            label=f"round_{round_num}" if round_num > 0 else "baseline",
        )

    def checkpoint(self, rr: RoundResult, round_num: int) -> dict[str, Any]:
        """Build the trial dict for ``campaign_store.add_trial`` — self-contained replay."""
        return {
            "trial_id": f"round_{round_num}",
            "round": round_num,
            "label": rr.label,
            "accuracy": rr.accuracy,
            "composite": rr.composite,
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
            **self.escalation.to_checkpoint_dict(),
            "opt_search_point": self.opt_sp.model_dump(),
            **(
                {"scoring_set_events": list(rr.scoring_set_events)} if rr.scoring_set_events else {}
            ),
        }


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
    """Rescore prior trials under the active scorer; halt or fork on divergence."""
    from promptpotter.application.scoring.formula import rescore_results
    from promptpotter.shared.errors import ResumeDivergenceError

    sc = session.scoring
    assert sc.scorer is not None, "session.scoring.scorer required for divergence replay"
    prior = campaign_store.load_trials_range(backend_id, cycle_id, 0, resumed_from_round - 1)
    for t in prior:
        rescore_results(
            list(t.get("results") or []),
            sc.scorer,
            sc.scorer_id,
            sc.scorer_formula,
        )
        for items in (t.get("all_candidate_results") or {}).values():
            rescore_results(
                list(items or []),
                sc.scorer,
                sc.scorer_id,
                sc.scorer_formula,
            )

    baseline_results_rescored = list(cycle.current_results or [])
    rescore_results(
        baseline_results_rescored,
        sc.scorer,
        sc.scorer_id,
        sc.scorer_formula,
    )

    if not skip_divergence_check:
        for i, t in enumerate(prior):
            div = replay_decisions(
                t,
                prior_trials=prior[:i],
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
    return None


# ---------------------------------------------------------------------------
# Layer escalation — L1→L2 (and optional L2→L3) action driver
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


_TEMP_ATTR: dict[str, str] = {"L2": "l2_temperature", "L3": "l3_temperature"}


async def _run_transition(
    transition: LayerTransition,
    cycle: Cycle,
    config: CampaignConfig,
    pipeline_schema: Any,
    round_num: int,
    on_phase: Callable[[PhaseEvent], None] | None,
    *,
    obs: ObservabilityBridge | None,
    obs_campaign_id: str,
    escalation_check_result: dict | None = None,
) -> Any:
    """Unified L2/L3 orchestrator: enter → call → adopt → LayerApplied → side-effects → exit."""
    assert cycle.current_sp is not None
    client = _llm_client.get_llm_client(config.optimizer_llm.provider)
    current_pp = cycle.current_sp.pipeline_params

    emit_phase(
        on_phase, transition.phase, "enter", round=round_num, **transition.enter_payload(cycle)
    )
    async with observed_node(
        f"{transition.template_name}_r{round_num}",
        "llm/meta",
        obs=obs,
        campaign_id=obs_campaign_id,
        round_num=round_num,
    ):
        result = await transition.run(
            cycle,
            client,
            model=config.optimizer_llm.model,
            temperature=getattr(config.optimization, _TEMP_ATTR[transition.layer]),
            pipeline_params=current_pp,
            round_num=round_num,
            escalation_check_result=escalation_check_result,
        )
    cycle.adopt_transition(
        result.opt_search_point,
        result.pipeline_params,
        schema=pipeline_schema,
    )
    if obs is not None:
        with graceful(f"LayerApplied({transition.layer}) emit failed"):
            obs.emit_write_point(
                LayerApplied,
                layer=transition.layer,
                campaign_id=obs_campaign_id,
                round_num=round_num,
                changes_description=result.opt_search_point.lineage.changes_description or "",
            )
    transition.apply_side_effects(cycle, result, round_num)
    emit_phase(
        on_phase,
        transition.phase,
        "exit",
        round=round_num,
        **transition.exit_payload(cycle, result),
    )
    return result


async def escalate_l2(
    cycle: Cycle,
    config: CampaignConfig,
    pipeline_schema: Any,
    round_num: int,
    on_phase: Callable[[PhaseEvent], None] | None = None,
    obs: ObservabilityBridge | None = None,
    obs_campaign_id: str = "",
    escalation_check_result: dict | None = None,
) -> StopReason | None:
    """L1→L2 (and optional L2→L3) escalation; vanilla patience-exhausts → next layer / stop."""
    opt = config.optimization
    esc = cycle.escalation
    esc.l2.record_outcome(cycle.best_composite)

    l2_stalled = opt.l2_patience is not None and esc.l2.stall_count >= opt.l2_patience
    # entry_round = round whose rescored best_composite is the stall baseline (-1 = never fired).
    entry_round_l2 = esc.l2.round if esc.l2.round > 0 else -1
    record_decision(
        cycle.pending_decisions,
        DecisionKind.L2_ESCALATION_TRIGGER,
        {
            "round_num": round_num,
            "l2_patience": opt.l2_patience,
            "entry_round": entry_round_l2,
        },
        not l2_stalled,
        data={
            "l2_round": esc.l2.round,
            "stall_count": esc.l2.stall_count,
            "best_composite_at_entry": esc.l2.best_composite_at_entry,
            "best_composite_this_round": cycle.best_composite,
            "best_accuracy": cycle.best_accuracy,
        },
    )
    if not l2_stalled:
        await _run_transition(
            L2RefineStrategy(),
            cycle,
            config,
            pipeline_schema,
            round_num,
            on_phase,
            obs=obs,
            obs_campaign_id=obs_campaign_id,
            escalation_check_result=escalation_check_result,
        )

        # Loop 4 — post-L2 validator force: if L2 produced broken output,
        # L3 fires immediately to heal L2, bypassing l2_patience and
        # l3_patience. The trigger is deterministic from L2's output (which
        # itself rides on the trial JSON), so resume reproduces it without
        # needing a separate decision record.
        if cycle.opt_sp.l2_output_failures and opt.enable_l3:
            logger.info(
                "L3 force-triggered by %d L2-output validator failure(s) at round %d",
                len(cycle.opt_sp.l2_output_failures),
                round_num,
            )
            await _run_transition(
                L3ModifyPlan(),
                cycle,
                config,
                pipeline_schema,
                round_num,
                on_phase,
                obs=obs,
                obs_campaign_id=obs_campaign_id,
            )
        return None

    if not opt.enable_l3:
        logger.debug("L2 patience exhausted (%d stalls) at round %d", esc.l2.stall_count, round_num)
        return StopReason.L2_PATIENCE

    esc.l3.record_outcome(cycle.best_composite)
    l3_exhausted = opt.l3_patience is not None and esc.l3.stall_count >= opt.l3_patience
    entry_round_l3 = esc.l3.round if esc.l3.round > 0 else -1
    record_decision(
        cycle.pending_decisions,
        DecisionKind.L3_ESCALATION_TRIGGER,
        {
            "round_num": round_num,
            "l3_patience": opt.l3_patience,
            "entry_round": entry_round_l3,
        },
        not l3_exhausted,
        data={
            "l3_round": esc.l3.round,
            "stall_count": esc.l3.stall_count,
            "best_composite_at_entry": esc.l3.best_composite_at_entry,
            "best_composite_this_round": cycle.best_composite,
        },
    )
    if not l3_exhausted:
        await _run_transition(
            L3ModifyPlan(),
            cycle,
            config,
            pipeline_schema,
            round_num,
            on_phase,
            obs=obs,
            obs_campaign_id=obs_campaign_id,
        )
        return None

    logger.debug("L3 patience exhausted (%d stalls) at round %d", esc.l3.stall_count, round_num)
    return StopReason.L3_PATIENCE
