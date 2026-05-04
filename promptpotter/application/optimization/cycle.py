"""Cycle state + decision ledger + fork helpers.

Decisions are two-tier: (inputs_ref + outcome) compared on resume; data is
archival. Action driver (escalate_l2 + L2/L3 strategies) lives in escalation.py
— one-way arrow, enforced by tests/test_layer_imports.py.
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
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple, cast

from promptpotter.application.optimization.elimination import update_query_tracker
from promptpotter.application.scoring.formula import rescore_results
from promptpotter.application.scoring.metrics import (
    compile_failure_analysis,
    compute_composite_score,
)
from promptpotter.config.settings import PROMPT_STRING_FIELDS
from promptpotter.domain.analysis import RuntimeFailure
from promptpotter.domain.cycle_paths import CycleDir
from promptpotter.domain.opt_search_point import OptSearchPoint, RoundSummary
from promptpotter.domain.results import RoundBaseline, RoundResult
from promptpotter.domain.run_records import (
    DECISION_GATING,
    Decision,
    DecisionKind,
    GatingMode,
    SweepPayload,
)
from promptpotter.domain.search_point import JobSearchPoint
from promptpotter.infrastructure.ledger import RunLedger
from promptpotter.infrastructure.store.base import (
    read_json_optional,
    write_json,
)
from promptpotter.infrastructure.store.stores import root_cycle_id, save_active_pointer
from promptpotter.shared.errors import ResumeDivergenceError, graceful
from promptpotter.shared.statistics import (
    pobb_should_stop,
    posterior_best_probabilities,
)

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
    "_fork_for_sweep_sibling",
    "build_escalation_entry",
    "record_decision",
    "replay_decisions",
    "resume_with_divergence_check",
]


# Decision / DecisionKind live in domain/run_records.py; re-exported here.


def _build_scoreboard(
    candidate_scores: list[dict[str, Any]], winner_label: str
) -> list[dict[str, Any]]:
    """Trial-JSON `scoreboard`: rank by (composite, accuracy) desc; tag winner."""
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


class Divergence(NamedTuple):
    """A recorded decision re-derived to a different outcome under the current scorer."""

    round_num: int
    kind: str
    recorded_outcome: Any
    current_outcome: Any
    inputs_ref: dict[str, Any]


class ReplayContext(NamedTuple):
    """Context passed to replayers — trial + prior_trials + baseline_results all rescored."""

    trial: dict[str, Any]
    prior_trials: list[dict[str, Any]]
    baseline_results: list[dict[str, Any]]


class ForkResult(NamedTuple):
    """Resume detected divergence and forked into a sibling cycle."""

    new_cycle_id: str
    new_resumed_from_round: int


Replayer = Callable[[ReplayContext, dict[str, Any]], Any]


def record_decision(
    decisions: list[Decision],
    kind: DecisionKind,
    inputs_ref: dict[str, Any],
    outcome: Any,
    *,
    data: dict[str, Any] | None = None,
) -> Any:
    """Append Decision to *decisions*; return outcome for passthrough."""
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


def _pobb_replay_snapshot(
    ctx: ReplayContext, inputs_ref: dict[str, Any]
) -> tuple[str, dict[str, float], list[float]] | None:
    """Build (candidate_id, posterior snapshot, current scores) for PoBB replay; None when underspecified."""
    all_results: dict[str, list[dict]] = ctx.trial.get("all_candidate_results") or {}
    candidate_id = str(inputs_ref.get("candidate_id", ""))
    prior_ids = list(inputs_ref.get("prior_candidate_ids") or [])
    n = int(inputs_ref.get("queries_scored", 0))
    current = [float(r.get("score", 0.0)) for r in (all_results.get(candidate_id) or [])[:n]]
    priors = {
        pid: [float(r.get("score", 0.0)) for r in (all_results.get(pid) or [])] for pid in prior_ids
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


def _replay_layer_trigger(patience_key: str) -> Replayer:
    """Build a replayer that re-derives `triggered = stalls < patience` from prior trials."""

    def _replay(ctx: ReplayContext, inputs_ref: dict[str, Any]) -> bool:
        patience = inputs_ref.get(patience_key)
        if patience is None:
            return True
        stalls = _derive_stall_count(
            ctx.prior_trials,
            int(inputs_ref.get("entry_round", -1)),
            int(inputs_ref.get("round_num", -1)),
        )
        return stalls < int(patience)

    return _replay


_replay_l2_trigger = _replay_layer_trigger("l2_patience")
_replay_l3_trigger = _replay_layer_trigger("l3_patience")


# Flat registry: DecisionKind → replayer. ARCHIVAL kinds (per ``DECISION_GATING``)
# MUST NOT appear here; ``test_decision_kinds_registry.py`` enforces the pairing.
# The check below runs once at import; if it ever trips, fix the table — don't
# silence it.
REPLAYERS: dict[DecisionKind, Replayer] = {
    DecisionKind.ROUND_WINNER: _replay_round_winner,
    DecisionKind.ELIMINATION_CUT: _replay_elimination_cut,
    DecisionKind.LEADER_LOCK_IN: _replay_leader_lock_in,
    DecisionKind.L2_ESCALATION_TRIGGER: _replay_l2_trigger,
    DecisionKind.L3_ESCALATION_TRIGGER: _replay_l3_trigger,
}
for _kind in REPLAYERS:
    if DECISION_GATING.get(_kind) is not GatingMode.REPLAYED:
        raise ValueError(f"REPLAYERS entry {_kind!r} is not REPLAYED in DECISION_GATING")
del _kind


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
) -> tuple[Path, Path, str, dict[str, Any]]:
    """Common fork plumbing: dir create, FORK_CUT append, parent index read, pointer + log.

    Returns ``(parent_dir, new_dir, now_iso, parent_index)``. Caller writes
    ``new_dir/index.json`` and any per-fork artifacts.
    """
    parent_dir = campaign_store.campaign_dir(parent_cycle_id)
    new_dir = campaign_store.campaign_dir(new_cycle_id)
    if new_dir.exists():
        raise FileExistsError(f"forked cycle dir already exists: {new_dir}")
    new_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(UTC).isoformat()
    with graceful("FORK_CUT decision append failed"):
        RunLedger.open(CycleDir(parent_dir)).append(
            Decision(
                kind=DecisionKind.FORK_CUT,
                inputs_ref={"from_round": from_round},
                outcome=new_cycle_id,
                data={"forked_at": now, **(fork_data or {})},
            )
        )

    parent_index = read_json_optional(parent_dir / "index.json") or {}
    save_active_pointer(tenant_id, session_id, new_cycle_id)
    logger.info(
        "Forked %s → %s at round %d%s (active pointer retargeted)",
        parent_cycle_id,
        new_cycle_id,
        from_round,
        log_extra,
    )
    return parent_dir, new_dir, now, parent_index


def _fresh_sibling_index(
    parent_index: dict[str, Any],
    new_cycle_id: str,
    parent_cycle_id: str,
    fork_kind: str,
    now: str,
    **extras: Any,
) -> dict[str, Any]:
    """Build a clean-slate sibling index inheriting type/config/backend from the parent."""
    return {
        "campaign_id": new_cycle_id,
        "type": parent_index.get("type", "optimization_loop"),
        "config": parent_index.get("config", {}),
        "connector_type": parent_index.get("connector_type", ""),
        "backend_id": parent_index.get("backend_id", ""),
        "parent_cycle_id": parent_cycle_id,
        "parent_session_id": parent_index.get("parent_session_id", ""),
        "forked_from_round": 0,
        "forked_at": now,
        "fork_kind": fork_kind,
        "trials": [],
        "n_trials": 0,
        "best_accuracy": 0.0,
        "best_trial_id": None,
        "baseline_accuracy": parent_index.get("baseline_accuracy", 0.0),
        "status": "active",
        "created_at": now,
        "updated_at": now,
        **extras,
    }


def _fork_at_divergence(
    campaign_store: CampaignStore,
    tenant_id: str,
    session_id: str,
    old_cycle_id: str,
    fork_from_round: int,
    surviving_trials: list[dict[str, Any]],
) -> str:
    """Divergence-fork that inherits parent's < fork_from_round artifacts (deterministic replay).

    NOT for clean-slate siblings (sweep/diag) — those would short-circuit
    L1 on the inherited round-0 checkpoint. Use _fork_for_{sweep,diag}_sibling.
    """
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    suffix = hashlib.sha256(f"{old_cycle_id}|{ts}".encode()).hexdigest()[:8]
    new_cycle_id = f"{old_cycle_id}_fork_{suffix}"

    old_dir, new_dir, now, index = _fork_sibling_setup(
        campaign_store,
        tenant_id,
        session_id,
        old_cycle_id,
        new_cycle_id,
        from_round=fork_from_round,
    )

    best_acc = max((float(t.get("accuracy", 0.0)) for t in surviving_trials), default=0.0)
    best_trial_id = next(
        (t.get("trial_id") for t in surviving_trials if float(t.get("accuracy", 0.0)) == best_acc),
        None,
    )
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

    copy_specs: tuple[tuple[Path, Path, str], ...] = (
        (old_dir / "trials", new_dir / "trials", "trial_"),
        (
            old_dir / ".runtime" / "cache" / "candidates",
            new_dir / ".runtime" / "cache" / "candidates",
            "round_",
        ),
    )
    for src, dst, prefix in copy_specs:
        if not src.exists():
            continue
        dst.mkdir(parents=True, exist_ok=True)
        for p in sorted(src.glob(f"{prefix}*.json")):
            try:
                n = int(p.stem.removeprefix(prefix))
            except ValueError:
                continue
            if n < fork_from_round:
                shutil.copyfile(p, dst / p.name)

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


def _fork_for_diag_sibling(
    campaign_store: CampaignStore,
    tenant_id: str,
    session_id: str,
    parent_cycle_id: str,
) -> str:
    """Mint a diag-BFS sibling rooted at round 0; records ``FORK_CUT`` and retargets the active pointer."""
    new_cycle_id = _next_diag_sibling_id(campaign_store, parent_cycle_id)
    _, new_dir, now, parent_index = _fork_sibling_setup(
        campaign_store,
        tenant_id,
        session_id,
        parent_cycle_id,
        new_cycle_id,
        from_round=0,
        fork_data={"kind": "diag_sibling"},
    )
    write_json(
        new_dir / "index.json",
        _fresh_sibling_index(parent_index, new_cycle_id, parent_cycle_id, "diag_sibling", now),
    )
    return new_cycle_id


def _fork_for_sweep_sibling(
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

    _, new_dir, now, parent_index = _fork_sibling_setup(
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
    write_json(
        new_dir / "index.json",
        _fresh_sibling_index(
            parent_index,
            new_cycle_id,
            parent_cycle_id,
            "sweep_fork",
            now,
            sweep_batch_id=sweep_batch_id,
        ),
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


_LAYER_COUNTER_FIELDS = (
    "round",
    "stall_count",
    "best_accuracy_at_entry",
    "best_composite_at_entry",
)


@dataclass
class EscalationState:
    """L1 stall counter + L2/L3 LayerCounter instances."""

    l1_stall_count: int = 0
    l2: LayerCounter = field(default_factory=LayerCounter)
    l3: LayerCounter = field(default_factory=LayerCounter)

    def reset_for_l3(self, best_accuracy: float, best_composite: float) -> None:
        self.l2 = LayerCounter(
            best_accuracy_at_entry=best_accuracy,
            best_composite_at_entry=best_composite,
        )

    def to_checkpoint_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"l1_stall_count": self.l1_stall_count}
        for layer in ("l2", "l3"):
            counter: LayerCounter = getattr(self, layer)
            for f in _LAYER_COUNTER_FIELDS:
                out[f"{layer}_{f}" if f != "round" else f"{layer}_round"] = getattr(counter, f)
        return out

    @classmethod
    def from_checkpoint_dict(cls, d: dict) -> EscalationState:
        def _counter(prefix: str) -> LayerCounter:
            return LayerCounter(**{f: d[f"{prefix}_{f}"] for f in _LAYER_COUNTER_FIELDS})

        return cls(l1_stall_count=d["l1_stall_count"], l2=_counter("l2"), l3=_counter("l3"))


@dataclass
class TrackingState:
    """Current/best searchpoint trajectory + frozen baseline composite."""

    current_sp: JobSearchPoint | None = None
    current_accuracy: float = 0.0
    current_composite: float = 0.0
    current_results: list[dict] = field(default_factory=list)
    best_accuracy: float = 0.0
    best_composite: float = 0.0
    best_round: int = -1
    best_sp: JobSearchPoint | None = None
    baseline_composite: float = 0.0


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
    # Flushed into the next trial's `decisions` before campaign_store.add_trial.
    pending_decisions: list[Decision] = field(default_factory=list)
    state_version: int = 1
    # Round-end Rasch posterior; one fit per round, reused by finalize.
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
            tracking=TrackingState(
                current_sp=sp,
                current_accuracy=baseline_accuracy,
                current_composite=composite,
                current_results=baseline_results or [],
                best_accuracy=baseline_accuracy,
                best_composite=composite,
                best_sp=sp,
                baseline_composite=composite,
            ),
            opt_sp=opt_sp,
        )

    def restore_from_trial(self, trial: dict[str, Any]) -> None:
        """Restore optimizer state from a campaign checkpoint dict (in-place)."""
        self.opt_sp = OptSearchPoint(**trial["opt_search_point"])
        self.escalation = EscalationState.from_checkpoint_dict(trial)

    def record_round(self, rr: RoundResult, round_num: int) -> None:
        """Append a RoundResult and propagate to memory + current/best tracking."""
        schema = self.session.pipeline_schema
        tr = self.tracking
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
        assert tr.current_sp is not None
        _pp = (
            rr.pipeline_params if rr.pipeline_params is not None else tr.current_sp.pipeline_params
        )
        tr.current_sp = self.opt_sp.to_job_search_point(base_pipeline_params=_pp, schema=schema)
        tr.current_accuracy = rr.accuracy
        tr.current_composite = rr.composite
        tr.current_results = list(rr.results)
        if tr.current_composite > tr.best_composite:
            tr.best_composite = tr.current_composite
            tr.best_accuracy = tr.current_accuracy
            tr.best_round = round_num
            tr.best_sp = tr.current_sp

    def record_decision(self, d: Decision) -> None:
        """Queue a decision produced outside the normal round flow (escalation/probe)."""
        self.pending_decisions.append(d)

    def apply_round_outcome(self, scoring_result: L1ScoringResult, critique_text: str) -> None:
        """Fold per-round optimizer-memory updates onto ``opt_sp`` atomically.

        Single mutation point for: l1_critique_text, failure_analysis,
        warning_inventory (from all candidate results), runtime_failures
        (deduped by source/warning/observed-config).
        """
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
        """Build round baseline; on probe rounds, rescore over the probe subset."""
        schema = self.session.pipeline_schema
        tr = self.tracking
        accuracy = tr.current_accuracy
        composite = tr.current_composite
        results: list[dict] = list(tr.current_results)
        if self.probe_next_round and tr.current_results and schema is not None:
            probe_queries = {s.query for s in scoring_dataset}
            subset = [r for r in tr.current_results if r.get("query") in probe_queries]
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
        """Trial dict for campaign_store.add_trial — self-contained replay."""
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
    sc = session.scoring
    scorer = sc.scorer
    assert scorer is not None, "session.scoring.scorer required for divergence replay"
    prior = campaign_store.load_trials_range(backend_id, cycle_id, 0, resumed_from_round - 1)

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
