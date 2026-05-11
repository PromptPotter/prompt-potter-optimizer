"""Decision replayers — re-derive each ``REPLAYED`` decision under the active scorer.

``replay_decisions`` walks ``round_data['decisions']`` in order and
returns the first :class:`Divergence` (recorded outcome != current
outcome). Replayers are pure functions over a :class:`ReplayContext`
snapshot — they MUST NOT touch the live ``Cycle`` or write to the
ledger; resume policy lives in :func:`resume_with_divergence_check`.

``REPLAYERS`` is the single registry; ``RESUME_CHECKPOINT_GATING`` (in
:mod:`.decisions`) declares which kinds are ``REPLAYED`` and an
import-time check fails if any ``REPLAYED`` kind has no replayer here.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, NamedTuple

from promptpotter.application.optimization.resume_and_fork.decisions import (
    RESUME_CHECKPOINT_GATING,
    GatingMode,
    ResumeCheckpointKind,
)
from promptpotter.shared.statistics import (
    pobb_should_stop,
    posterior_best_probabilities,
)

__all__ = [
    "REPLAYERS",
    "Divergence",
    "ReplayContext",
    "Replayer",
    "replay_decisions",
]


class Divergence(NamedTuple):
    """A recorded decision re-derived to a different outcome under the current scorer."""

    round_num: int
    kind: str
    recorded_outcome: Any
    current_outcome: Any
    inputs_ref: dict[str, Any]


class ReplayContext(NamedTuple):
    """Context passed to replayers — round_data + prior_rounds + origin_results all rescored."""

    round_data: dict[str, Any]
    prior_rounds: list[dict[str, Any]]
    origin_results: list[dict[str, Any]]


Replayer = Callable[[ReplayContext, dict[str, Any]], Any]


def replay_decisions(
    round_data: dict[str, Any],
    prior_rounds: list[dict[str, Any]] | None = None,
    origin_results: list[dict[str, Any]] | None = None,
) -> Divergence | None:
    """Walk ``round_data['decisions']`` in order; return the first mismatch."""
    ctx = ReplayContext(
        round_data=round_data,
        prior_rounds=list(prior_rounds or []),
        origin_results=list(origin_results or []),
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
        best_acc = _mean_score(list(ctx.origin_results))
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
    origin: float | None = None
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
                origin = running_max
            continue
        rounds_after += 1
        if origin is not None and running_max > origin:
            return 0
    return rounds_after if origin is not None else 0


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


# Explicit decision-replayer registry. ``RESUME_CHECKPOINT_GATING`` is the source of
# truth for which kinds exist; the assertion below enforces that every
# REPLAYED kind has a replayer here, so resume can never silently treat an
# unhandled kind as non-divergence.
REPLAYERS: dict[ResumeCheckpointKind, Replayer] = {
    ResumeCheckpointKind.ROUND_WINNER: _replay_round_winner,
    ResumeCheckpointKind.ELIMINATION_CUT: _replay_elimination_cut,
    ResumeCheckpointKind.LEADER_LOCK_IN: _replay_leader_lock_in,
    ResumeCheckpointKind.L2_ESCALATION_TRIGGER: _replay_l2_trigger,
    ResumeCheckpointKind.L3_ESCALATION_TRIGGER: _replay_l3_trigger,
}

_missing_replayers = {
    k
    for k, mode in RESUME_CHECKPOINT_GATING.items()
    if mode is GatingMode.REPLAYED and k not in REPLAYERS
}
if _missing_replayers:
    raise RuntimeError(
        f"RESUME_CHECKPOINT_GATING declares {sorted(_missing_replayers)} as REPLAYED, "
        "but no replayer is registered in resume_and_fork/replayers.py::REPLAYERS."
    )
del _missing_replayers
