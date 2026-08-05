"""Decision replayers — re-derive each ``REPLAYED`` decision under the active scorer.

``replay_decisions`` walks ``round_data['decisions']`` and returns the
first :class:`Divergence`. Replayers are pure over :class:`ReplayContext`;
MUST NOT touch the live ``Cycle`` or ledger. Import-time check below
catches any ``REPLAYED`` kind without a registered replayer.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING, Any, NamedTuple, cast

from promptpotter.application.intelligence.exploration import graded_response
from promptpotter.application.optimization.resume_and_fork.decisions import (
    RESUME_CHECKPOINT_GATING,
    GatingMode,
    ResumeCheckpointKind,
)
from promptpotter.application.scoring.metrics import (
    elect_round_winner,
    elimination_p_best,
)
from promptpotter.domain.results import RoundResult

if TYPE_CHECKING:
    from promptpotter.application.intelligence.exploration import RulerEntry
    from promptpotter.domain.scoring import QueryMeasurement

__all__ = [
    "REPLAYERS",
    "Divergence",
    "ReplayContext",
    "Replayer",
    "replay_all_divergences",
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
    """Context passed to replayers — one round's measurements + the origin, all rescored.

    This is the whole substrate: a replayer re-derives its decision from the round it was
    taken in, and nothing else. A decision that needs the cycle's *history* to re-derive
    (the L2/L3 layer triggers — a fold whose counters reset on each fire) is not
    expressible here and is ``ARCHIVAL``, not ``REPLAYED``.

    ``delta_scale`` is the cycle's FIXED difficulty ruler (``cycle.delta_scale``,
    calibrated once at ``Cycle.start``) — the same scale the live election and PoBB gates
    fit θ on, so replay re-derives them bit-for-bit. ``None`` ⇒ cold ruler.
    """

    round_data: RoundResult
    origin_results: list[dict[str, Any]]
    delta_scale: dict[int, RulerEntry] | None


Replayer = Callable[[ReplayContext, dict[str, Any], dict[str, Any]], Any]


def _iter_divergences(ctx: ReplayContext) -> Iterator[Divergence]:
    """Yield every recorded decision in ``ctx.round_data`` whose replayer re-derives a
    different outcome under the active scorer/engine — in record order."""
    round_data = ctx.round_data
    for rec in round_data.decisions:
        try:
            kind = ResumeCheckpointKind(rec["kind"])
        except ValueError:
            # Not a known checkpoint kind — corrupt/foreign decision record; skip (not a divergence).
            continue
        fn = REPLAYERS.get(kind)
        if fn is None:
            continue  # valid kind, but ARCHIVAL gating — recorded, never replayed

        try:
            current = fn(ctx, rec["inputs_ref"], rec["data"])
        except Exception:
            # Non-divergence on replayer crash, but surface — silent skip hides scorer drift.
            logger.warning(
                "replayer for decision kind %r crashed during divergence "
                "check; treating as non-divergence",
                kind,
                exc_info=True,
            )
            continue
        recorded = rec["outcome"]
        if current != recorded:
            yield Divergence(
                round_num=round_data.round,
                kind=kind,
                recorded_outcome=recorded,
                current_outcome=current,
                inputs_ref=dict(rec["inputs_ref"]),
            )


def _replay_context(
    round_data: RoundResult,
    origin_results: list[dict[str, Any]] | None,
    delta_scale: dict[int, RulerEntry] | None,
) -> ReplayContext:
    return ReplayContext(
        round_data=round_data,
        origin_results=list(origin_results or []),
        delta_scale=delta_scale,
    )


def replay_decisions(
    round_data: RoundResult,
    origin_results: list[dict[str, Any]] | None = None,
    delta_scale: dict[int, RulerEntry] | None = None,
) -> Divergence | None:
    """Walk ``round_data.decisions`` in order; return the FIRST mismatch (resume's halt seam)."""
    ctx = _replay_context(round_data, origin_results, delta_scale)
    return next(_iter_divergences(ctx), None)


def replay_all_divergences(
    round_data: RoundResult,
    origin_results: list[dict[str, Any]] | None = None,
    delta_scale: dict[int, RulerEntry] | None = None,
) -> list[Divergence]:
    """Every decision in this round that re-derives differently — the A/B engine's per-round
    diff (collect-all, where ``replay_decisions`` short-circuits at the first)."""
    ctx = _replay_context(round_data, origin_results, delta_scale)
    return list(_iter_divergences(ctx))


def _replay_round_winner(
    ctx: ReplayContext, inputs_ref: dict[str, Any], _data: dict[str, Any]
) -> str:
    """Re-derive the round winner under the canonical paired-LCB election — the SAME
    ``elect_round_winner`` the live scorer ran (``l1/score/winner.py``), not a parallel rule. An
    unchanged scorer therefore re-elects the recorded winner exactly; only a genuine scorer change
    can diverge. ``coverage_floor`` rides the recorded inputs.
    """
    all_results = ctx.round_data.all_candidate_results
    candidate_ids = [str(c) for c in (inputs_ref.get("candidate_ids") or [])]
    coverage_floor = int(inputs_ref["coverage_floor"])
    winner_id, _ = elect_round_winner(
        candidate_ids,
        cast("dict[str, list[QueryMeasurement]]", all_results),
        cast("list[QueryMeasurement]", ctx.origin_results),
        coverage_floor,
        ctx.delta_scale or {},
    )
    return winner_id


def _pobb_replay_snapshot(
    ctx: ReplayContext, inputs_ref: dict[str, Any], data: dict[str, Any]
) -> float | None:
    """Re-derive this candidate's ``p_best`` for PoBB replay. ``None`` ⇒ not derivable.

    Re-fits θ on the cycle's fixed δ ruler (``ctx.delta_scale``) over the recorded
    per-prior GRADED responses + the candidate's rescored grades on
    ``data.candidate_sample_ids`` and recomputes ``p_best`` via ``elimination_p_best``
    — the same closed-form, MC-free rule the live ``PoBBCheck.check`` ran, so replay
    is bit-for-bit when no scorer change moved the candidate's outcomes. Recorded
    booleans from pre-graded decisions coerce to 0.0/1.0 — the identical values the
    live path fed. ``None`` ⇒ rescored measurements aren't available.
    """
    candidate_id = str(inputs_ref.get("candidate_id", ""))
    candidate_sample_ids = [str(s) for s in (data.get("candidate_sample_ids") or [])]
    prior_histories: dict[str, dict[str, float]] = data.get("prior_histories") or {}
    if not candidate_sample_ids or not prior_histories:
        return None

    all_results = ctx.round_data.all_candidate_results
    cur_results = all_results.get(candidate_id) or []
    cur_by_sample = {
        str(r.get("sample_id")): graded_response(r)
        for r in cur_results
        if r.get("sample_id") is not None
    }
    if not all(sid in cur_by_sample for sid in candidate_sample_ids):
        return None
    candidate_grades = [cur_by_sample[sid] for sid in candidate_sample_ids]

    paired_prior_grades: dict[str, list[float]] = {}
    for cid, hist in prior_histories.items():
        if all(sid in hist for sid in candidate_sample_ids):
            paired_prior_grades[cid] = [float(hist[sid]) for sid in candidate_sample_ids]
    if not paired_prior_grades:
        return None

    p_best, _per_prior = elimination_p_best(
        candidate_grades,
        paired_prior_grades,
        [int(s) for s in candidate_sample_ids],
        ctx.delta_scale or {},
    )
    return float(p_best)


def _replay_elimination_cut(
    ctx: ReplayContext, inputs_ref: dict[str, Any], data: dict[str, Any]
) -> bool:
    """PoBB ε-gate re-derived on θ ability under the current scorer (deterministic)."""
    p_best = _pobb_replay_snapshot(ctx, inputs_ref, data)
    if p_best is None:
        return False
    return p_best < float(inputs_ref["epsilon"])


def _replay_leader_lock_in(
    ctx: ReplayContext, inputs_ref: dict[str, Any], data: dict[str, Any]
) -> bool:
    """PoBB leader-lock re-derived on θ ability under the current scorer (deterministic)."""
    if int(inputs_ref["queries_scored"]) < int(inputs_ref["lock_in_n_min"]):
        return False
    p_best = _pobb_replay_snapshot(ctx, inputs_ref, data)
    if p_best is None:
        return False
    # ``min`` over priors is the lock-in metric; no separate leader guard.
    return p_best >= float(inputs_ref["lock_in"])


# ``RESUME_CHECKPOINT_GATING`` enumerates the kinds; the assertion below
# fails import if any REPLAYED kind has no replayer here.
REPLAYERS: dict[ResumeCheckpointKind, Replayer] = {
    ResumeCheckpointKind.ROUND_WINNER: _replay_round_winner,
    ResumeCheckpointKind.ELIMINATION_CUT: _replay_elimination_cut,
    ResumeCheckpointKind.LEADER_LOCK_IN: _replay_leader_lock_in,
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
