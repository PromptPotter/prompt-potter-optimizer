"""Decision records + replayers — kind-agnostic divergence + inline fork."""

from __future__ import annotations

import hashlib
import logging
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from promptpotter.application.campaign.campaign_setup import Session
    from promptpotter.application.optimization.cycle import Cycle
    from promptpotter.infrastructure.store.campaign_store import CampaignStore

__all__ = [
    "REPLAYERS",
    "Decision",
    "Divergence",
    "ForkResult",
    "ReplayContext",
    "record_decision",
    "replay_decisions",
    "replayer",
    "resume_with_divergence_check",
]


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Decision:
    """One recorded decision: ``inputs_ref`` + ``outcome`` drive divergence; ``data`` is archival."""

    kind: str
    inputs_ref: dict[str, Any]
    outcome: Any
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "inputs_ref": dict(self.inputs_ref),
            "outcome": self.outcome,
            "data": dict(self.data),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Decision:
        return cls(
            kind=d["kind"],
            inputs_ref=dict(d.get("inputs_ref") or {}),
            outcome=d.get("outcome"),
            data=dict(d.get("data") or {}),
        )


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

REPLAYERS: dict[str, Replayer] = {}


def replayer(kind: str) -> Callable[[Replayer], Replayer]:
    """Register a replayer function for a decision kind."""

    def deco(fn: Replayer) -> Replayer:
        REPLAYERS[kind] = fn
        return fn

    return deco


def record_decision(
    decisions: list[dict[str, Any]],
    kind: str,
    inputs_ref: dict[str, Any],
    outcome: Any,
    *,
    data: dict[str, Any] | None = None,
) -> Any:
    """Append a ``Decision`` to *decisions* and return *outcome* for passthrough."""
    decisions.append(Decision(kind, dict(inputs_ref), outcome, dict(data or {})).to_dict())
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


@replayer("round_winner")
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


@replayer("elimination_cut")
def _replay_elimination_cut(ctx: ReplayContext, inputs_ref: dict[str, Any]) -> bool:
    """Re-run the Wilcoxon signed-rank gate under rescored scores."""
    from promptpotter.shared.statistics import should_stop_early

    all_results: dict[str, list[dict]] = ctx.trial.get("all_candidate_results") or {}
    candidate_id = str(inputs_ref.get("candidate_id", ""))
    prior_ids = list(inputs_ref.get("prior_candidate_ids") or [])
    n = int(inputs_ref.get("queries_scored", 0))
    alpha = float(inputs_ref.get("alpha", 0.2))

    current = [float(r.get("score", 0.0)) for r in (all_results.get(candidate_id) or [])[:n]]
    priors = [
        [float(r.get("score", 0.0)) for r in (all_results.get(pid) or [])] for pid in prior_ids
    ]
    priors = [p for p in priors if p]
    if not priors or len(current) < 2:
        return False
    stop, _ = should_stop_early(current, priors, alpha)
    return bool(stop)


def _rescored_composite(trial: dict[str, Any]) -> float:
    """Approximate rescored composite as mean of winner's rescored scores."""
    winner_results = trial.get("results") or []
    if winner_results:
        return _mean_score(winner_results)
    return float(trial.get("composite", trial.get("accuracy", 0.0)))


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
        comp = _rescored_composite(t)
        running_max = max(running_max, comp)
        if r <= entry_round:
            if r == entry_round:
                baseline = running_max
            continue
        rounds_after += 1
        if baseline is not None and running_max > baseline:
            return 0
    return rounds_after if baseline is not None else 0


@replayer("l2_escalation_trigger")
def _replay_l2_trigger(ctx: ReplayContext, inputs_ref: dict[str, Any]) -> bool:
    """Re-derive L2 fire/patience-defer."""
    patience = inputs_ref.get("l2_patience")
    if patience is None:
        return True
    entry_round = int(inputs_ref.get("entry_round", -1))
    this_round = int(inputs_ref.get("round_num", -1))
    stalls = _derive_stall_count(ctx.prior_trials, entry_round, this_round)
    return stalls < int(patience)


@replayer("l3_escalation_trigger")
def _replay_l3_trigger(ctx: ReplayContext, inputs_ref: dict[str, Any]) -> bool:
    """Re-derive whether L3 fires. Same shape as ``l2_escalation_trigger``."""
    patience = inputs_ref.get("l3_patience")
    if patience is None:
        return True
    entry_round = int(inputs_ref.get("entry_round", -1))
    this_round = int(inputs_ref.get("round_num", -1))
    stalls = _derive_stall_count(ctx.prior_trials, entry_round, this_round)
    return stalls < int(patience)


def _mint_fork_cycle_id(old_cycle_id: str) -> str:
    """Derive a stable-looking new cycle id rooted at the parent."""
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    suffix = hashlib.sha256(f"{old_cycle_id}|{ts}".encode()).hexdigest()[:8]
    return f"{old_cycle_id}_fork_{suffix}"


def _fork_at_divergence(
    campaign_store: CampaignStore,
    tenant_id: str,
    session_id: str,
    old_cycle_id: str,
    fork_from_round: int,
    surviving_trials: list[dict[str, Any]],
) -> str:
    """Mint a sibling cycle that re-runs round ``fork_from_round``."""
    from promptpotter.infrastructure.store.base import read_json_optional, write_json
    from promptpotter.infrastructure.store.stores import save_active_pointer

    new_cycle_id = _mint_fork_cycle_id(old_cycle_id)
    old_dir = campaign_store.campaign_dir(old_cycle_id)
    new_dir = campaign_store.campaign_dir(new_cycle_id)
    if new_dir.exists():
        raise FileExistsError(f"forked cycle dir already exists: {new_dir}")
    new_dir.mkdir(parents=True, exist_ok=True)

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
