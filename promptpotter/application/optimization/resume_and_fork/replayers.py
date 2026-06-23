"""Decision replayers — re-derive each ``REPLAYED`` decision under the active scorer.

``replay_decisions`` walks ``round_data['decisions']`` and returns the
first :class:`Divergence`. Replayers are pure over :class:`ReplayContext`;
MUST NOT touch the live ``Cycle`` or ledger. Import-time check below
catches any ``REPLAYED`` kind without a registered replayer.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, NamedTuple, cast

from promptpotter.application.optimization.resume_and_fork.decisions import (
    RESUME_CHECKPOINT_GATING,
    GatingMode,
    ResumeCheckpointKind,
)
from promptpotter.application.scoring.metrics import elect_round_winner, elimination_p_best

if TYPE_CHECKING:
    from promptpotter.domain.scoring import QueryMeasurement

__all__ = [
    "REPLAYERS",
    "Divergence",
    "ReplayContext",
    "Replayer",
    "replay_decisions",
]

logger = logging.getLogger(__name__)


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


Replayer = Callable[[ReplayContext, dict[str, Any], dict[str, Any]], Any]


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
        try:
            kind = ResumeCheckpointKind(rec.get("kind", ""))
        except ValueError:
            # Not a known checkpoint kind — corrupt/foreign decision record; skip (not a divergence).
            continue
        fn = REPLAYERS.get(kind)
        if fn is None:
            continue  # valid kind, but ARCHIVAL gating — recorded, never replayed

        try:
            current = fn(ctx, rec.get("inputs_ref") or {}, rec.get("data") or {})
        except Exception:
            # Non-divergence on replayer crash, but surface — silent skip hides scorer drift.
            logger.warning(
                "replayer for decision kind %r crashed during resume divergence "
                "check; treating as non-divergence",
                kind,
                exc_info=True,
            )
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


def _mean_score(results: list[dict[str, Any]]) -> float:
    """Mean of rescored ``score`` projection."""
    if not results:
        return 0.0
    return sum(float(r.get("fitness", 0.0)) for r in results) / len(results)


def _replay_round_winner(
    ctx: ReplayContext, inputs_ref: dict[str, Any], _data: dict[str, Any]
) -> str:
    """Re-derive the round winner under the canonical paired-LCB election — the SAME
    ``elect_round_winner`` the live scorer ran (``l1/score/winner.py``), not a parallel rule. An
    unchanged scorer therefore re-elects the recorded winner exactly; only a genuine scorer change
    can diverge. ``coverage_floor`` rides the recorded inputs (defaulting to 0 for pre-floor
    records — most permissive, so it never rejects a candidate the live path kept).
    """
    all_results: dict[str, list[dict[str, Any]]] = ctx.round_data.get("all_candidate_results") or {}
    candidate_ids = [str(c) for c in (inputs_ref.get("candidate_ids") or [])]
    coverage_floor = int(inputs_ref.get("coverage_floor", 0))
    return elect_round_winner(
        candidate_ids,
        cast("dict[str, list[QueryMeasurement]]", all_results),
        cast("list[QueryMeasurement]", ctx.origin_results),
        coverage_floor,
    )


def _pobb_replay_snapshot(
    ctx: ReplayContext, inputs_ref: dict[str, Any], data: dict[str, Any]
) -> tuple[str, dict[str, float]] | None:
    """Build (candidate_id, θ-ability snapshot) for PoBB replay.

    Re-fits the joint Rasch over the recorded per-prior HITS + the candidate's
    rescored hits on ``data.candidate_sample_ids`` and recomputes ``p_best`` via
    ``elimination_p_best`` — the same closed-form, MC-free rule the live
    ``PoBBCheck.check`` ran, so replay is bit-for-bit when no scorer change moved
    the candidate's hits. ``None`` ⇒ rescored measurements aren't available.
    """
    candidate_id = str(inputs_ref.get("candidate_id", ""))
    candidate_sample_ids = [str(s) for s in (data.get("candidate_sample_ids") or [])]
    prior_histories: dict[str, dict[str, bool]] = data.get("prior_histories") or {}
    if not candidate_sample_ids or not prior_histories:
        return None

    all_results: dict[str, list[dict[str, Any]]] = ctx.round_data.get("all_candidate_results") or {}
    cur_results = all_results.get(candidate_id) or []
    cur_by_sample = {
        str(r.get("sample_id")): bool(r.get("hit"))
        for r in cur_results
        if r.get("sample_id") is not None
    }
    if not all(sid in cur_by_sample for sid in candidate_sample_ids):
        return None
    candidate_hits = [cur_by_sample[sid] for sid in candidate_sample_ids]

    paired_prior_hits: dict[str, list[bool]] = {}
    for cid, hist in prior_histories.items():
        if all(sid in hist for sid in candidate_sample_ids):
            paired_prior_hits[cid] = [bool(hist[sid]) for sid in candidate_sample_ids]
    if not paired_prior_hits:
        return None

    p_best, per_prior = elimination_p_best(candidate_hits, paired_prior_hits)
    # Mirror PoBBCheck.check shape: per-prior P(cand > prior) + ``cid → min``
    # so replayers read ``snapshot[candidate_id]`` exactly like the live path.
    snapshot: dict[str, float] = {**per_prior, candidate_id: p_best}
    return candidate_id, snapshot


def _replay_elimination_cut(
    ctx: ReplayContext, inputs_ref: dict[str, Any], data: dict[str, Any]
) -> bool:
    """PoBB ε-gate re-derived on θ ability under the current scorer (deterministic)."""
    snap = _pobb_replay_snapshot(ctx, inputs_ref, data)
    if snap is None:
        return False
    candidate_id, snapshot = snap
    return float(snapshot[candidate_id]) < float(inputs_ref["epsilon"])


def _replay_leader_lock_in(
    ctx: ReplayContext, inputs_ref: dict[str, Any], data: dict[str, Any]
) -> bool:
    """PoBB leader-lock re-derived on θ ability under the current scorer (deterministic)."""
    if int(inputs_ref["queries_scored"]) < int(inputs_ref["lock_in_n_min"]):
        return False
    snap = _pobb_replay_snapshot(ctx, inputs_ref, data)
    if snap is None:
        return False
    candidate_id, snapshot = snap
    # ``cid → min`` is the lock-in metric; no separate leader guard.
    return float(snapshot[candidate_id]) >= float(inputs_ref["lock_in"])


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

    def _replay(ctx: ReplayContext, inputs_ref: dict[str, Any], _data: dict[str, Any]) -> bool:
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


# ``RESUME_CHECKPOINT_GATING`` enumerates the kinds; the assertion below
# fails import if any REPLAYED kind has no replayer here.
REPLAYERS: dict[ResumeCheckpointKind, Replayer] = {
    ResumeCheckpointKind.ROUND_WINNER: _replay_round_winner,
    ResumeCheckpointKind.ELIMINATION_CUT: _replay_elimination_cut,
    ResumeCheckpointKind.LEADER_LOCK_IN: _replay_leader_lock_in,
    ResumeCheckpointKind.L2_ESCALATION_TRIGGER: _replay_l2_trigger,
    ResumeCheckpointKind.L3_ESCALATION_TRIGGER: _replay_l3_trigger,
}

# REPLAYERS must register a replayer for exactly the REPLAYED kinds — both
# directions fail import: a REPLAYED kind with no replayer (silent non-replay on
# resume) and an ARCHIVAL kind with one (replaying a kind that must never be
# re-derived). The registry's key set must equal the REPLAYED kind set.
_replayed_kinds = {k for k, mode in RESUME_CHECKPOINT_GATING.items() if mode is GatingMode.REPLAYED}
_missing_replayers = _replayed_kinds - set(REPLAYERS)
if _missing_replayers:
    raise RuntimeError(
        f"RESUME_CHECKPOINT_GATING declares {sorted(_missing_replayers)} as REPLAYED, "
        "but no replayer is registered in resume_and_fork/replayers.py::REPLAYERS."
    )
_archival_with_replayer = set(REPLAYERS) - _replayed_kinds
if _archival_with_replayer:
    raise RuntimeError(
        f"REPLAYERS registers {sorted(_archival_with_replayer)}, but those kinds are "
        "ARCHIVAL in RESUME_CHECKPOINT_GATING — an archival kind must never be replayed."
    )
del _replayed_kinds, _missing_replayers, _archival_with_replayer
