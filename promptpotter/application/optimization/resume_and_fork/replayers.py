"""Decision replayers — re-derive each ``REPLAYED`` decision under the active scorer. A replayer is
PURE over :class:`ReplayContext` and must never touch the live ``Cycle`` or ledger."""

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
    "ReplayContext",
    "ReplayMismatch",
    "replay_all_mismatches",
    "replay_decisions",
]

logger = logging.getLogger(__name__)


class ReplayMismatch(NamedTuple):
    """A recorded value that no longer re-derives. Not "a decision" — three producers, none of them the
    loop. **Evidence, not a departure point**: where a branch stops carrying over is a *divergence*."""

    round_num: int
    kind: str
    recorded_outcome: Any
    current_outcome: Any
    inputs_ref: dict[str, Any]


class ReplayContext(NamedTuple):
    """One round's measurements + the origin, all rescored — a replayer re-derives from its own round and
    nothing else, so a decision needing the cycle's HISTORY is ``ARCHIVAL``, never ``REPLAYED``."""

    round_data: RoundResult
    origin_results: list[dict[str, Any]]
    delta_scale: dict[int, RulerEntry] | None


Replayer = Callable[[ReplayContext, dict[str, Any], dict[str, Any]], Any]


def _iter_mismatches(ctx: ReplayContext) -> Iterator[ReplayMismatch]:
    round_data = ctx.round_data
    for rec in round_data.decisions:
        try:
            kind = ResumeCheckpointKind(rec["kind"])
        except ValueError:
            # Not a known checkpoint kind — corrupt/foreign decision record; skip (no mismatch).
            continue
        fn = REPLAYERS.get(kind)
        if fn is None:
            continue  # valid kind, but ARCHIVAL gating — recorded, never replayed

        try:
            current = fn(ctx, rec["inputs_ref"], rec["data"])
        except Exception:
            # No mismatch on replayer crash, but surface — silent skip hides scorer drift.
            logger.warning(
                "replayer for decision kind %r crashed during the replay check; "
                "treating as a match",
                kind,
                exc_info=True,
            )
            continue
        recorded = rec["outcome"]
        if current != recorded:
            yield ReplayMismatch(
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
) -> ReplayMismatch | None:
    """Walk ``round_data.decisions`` in order; return the FIRST mismatch (resume's halt seam)."""
    ctx = _replay_context(round_data, origin_results, delta_scale)
    return next(_iter_mismatches(ctx), None)


def replay_all_mismatches(
    round_data: RoundResult,
    origin_results: list[dict[str, Any]] | None = None,
    delta_scale: dict[int, RulerEntry] | None = None,
) -> list[ReplayMismatch]:
    """Every decision in this round that re-derives differently — the A/B engine's per-round diff, where
    ``replay_decisions`` short-circuits at the first."""
    ctx = _replay_context(round_data, origin_results, delta_scale)
    return list(_iter_mismatches(ctx))


def _replay_round_winner(
    ctx: ReplayContext, inputs_ref: dict[str, Any], _data: dict[str, Any]
) -> str:
    """Re-derive the round winner through the SAME ``elect_round_winner`` the live scorer ran, never a
    parallel rule — so an unchanged scorer re-elects exactly, and only a real scorer change diverges."""
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
    """Re-derive ``p_best`` on the cycle's fixed δ ruler via the same closed-form ``elimination_p_best``
    the live check ran. ``None`` when the rescored measurements are not available."""
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
    p_best = _pobb_replay_snapshot(ctx, inputs_ref, data)
    if p_best is None:
        return False
    return p_best < float(inputs_ref["epsilon"])


def _replay_leader_lock_in(
    ctx: ReplayContext, inputs_ref: dict[str, Any], data: dict[str, Any]
) -> bool:
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
