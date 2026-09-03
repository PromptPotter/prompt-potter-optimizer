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
from promptpotter.application.scoring.selection import elect_round_winner, elimination_p_best
from promptpotter.domain.results import EliminationGate, RoundResult
from promptpotter.domain.scoring import is_answer_collapsed

if TYPE_CHECKING:
    from promptpotter.domain.ruler import DeltaRuler
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
    """One round's measurements, rescored — a replayer re-derives from its own round and nothing
    else, so a decision needing the cycle's HISTORY is ``ARCHIVAL``, never ``REPLAYED``.

    ``decisions`` arrives from the LEDGER (``scan_ledger_decisions``), which is where a decision is
    written and stamped with the round that made it — never a second copy off the round document,
    which is assembled from whatever was pending when it happened to be written.

    Deliberately carries NO comparison anchor: a caller-supplied one is a caller-private one, so
    the anchor rides the decision that used it."""

    round_data: RoundResult
    decisions: list[dict[str, Any]]
    ruler: DeltaRuler | None


Replayer = Callable[[ReplayContext, dict[str, Any], dict[str, Any]], Any]


def _iter_mismatches(ctx: ReplayContext) -> Iterator[ReplayMismatch]:
    round_data = ctx.round_data
    for rec in ctx.decisions:
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
        except Exception as exc:
            # A replayer that cannot ANSWER is a third state, never a match: they raise on a record
            # that does not re-derive (`RulerCoverageError` on an uncarried cell, a `ROUND_WINNER`
            # missing its `parent_cells` anchor), so counting that as agreement reports a clean pass
            # for exactly the rounds nothing verified and `--fork-on-divergence` never fires. Its own
            # kind, so the operator reads WHICH check went blind.
            # `.get` here only: the raise may BE the missing key, and an error path may not raise.
            logger.warning(
                "replayer for decision kind %r could not re-derive its record", kind, exc_info=True
            )
            yield ReplayMismatch(
                round_num=round_data.round,
                kind=f"replay_error:{kind.value}",
                recorded_outcome=rec.get("outcome"),
                current_outcome=f"{type(exc).__name__}: {exc}",
                inputs_ref=dict(rec.get("inputs_ref") or {}),
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
    decisions: list[dict[str, Any]] | None,
    ruler: DeltaRuler | None,
) -> ReplayContext:
    return ReplayContext(
        round_data=round_data,
        decisions=list(decisions or []),
        ruler=ruler,
    )


def replay_decisions(
    round_data: RoundResult,
    decisions: list[dict[str, Any]] | None = None,
    ruler: DeltaRuler | None = None,
) -> ReplayMismatch | None:
    """Walk this round's ledger decisions in order; return the FIRST mismatch (resume's halt seam)."""
    ctx = _replay_context(round_data, decisions, ruler)
    return next(_iter_mismatches(ctx), None)


def replay_all_mismatches(
    round_data: RoundResult,
    decisions: list[dict[str, Any]] | None = None,
    ruler: DeltaRuler | None = None,
) -> list[ReplayMismatch]:
    """Every decision in this round that re-derives differently — the A/B engine's per-round diff, where
    ``replay_decisions`` short-circuits at the first."""
    ctx = _replay_context(round_data, decisions, ruler)
    return list(_iter_mismatches(ctx))


def _replay_round_winner(
    ctx: ReplayContext, inputs_ref: dict[str, Any], data: dict[str, Any]
) -> str:
    """Re-derive the round winner through the SAME ``elect_round_winner`` the live scorer ran, against
    the SAME parent — READ from the decision, never reconstructed. One shared rule is not enough
    alone: it ranks each arm against the parent panel, and the three callers that each reconstructed
    one reconstructed a different panel."""
    parent = data.get("parent_cells")
    if parent is None:
        # Never fall back to a reconstruction — guessing quietly is the defect itself.
        raise ValueError(
            "this ROUND_WINNER decision carries no `parent_cells`, so the panel its election "
            "ranked against is unrecoverable and the winner cannot be re-derived"
        )
    all_results = ctx.round_data.all_candidate_results
    candidate_ids = [str(c) for c in (inputs_ref.get("candidate_ids") or [])]
    coverage_floor = int(inputs_ref["coverage_floor"])
    # Read, never re-derived: it is a function of the round HISTORY, which this replay does not
    # hold, so recomputing it is the same defect as reconstructing the parent panel above. A
    # record missing it RAISES — `restamp` writes the bias every election ran under.
    winner_id, _ = elect_round_winner(
        candidate_ids,
        cast("dict[str, list[QueryMeasurement]]", all_results),
        cast("list[QueryMeasurement]", parent),
        coverage_floor,
        ctx.ruler,
        parent_bias=float(inputs_ref["parent_bias"]),
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
        ctx.ruler,
    )
    return float(p_best)


def _replay_elimination_cut(
    ctx: ReplayContext, inputs_ref: dict[str, Any], data: dict[str, Any]
) -> bool:
    """Dispatches on the gate the PRODUCER named, never on the ε rule alone: a collapse cut returns
    before ``elimination_p_best`` is reached, so it holds no posterior and re-deriving it under ε
    tests a real ``p_best`` against a bar nobody set — which no collapse can re-derive as true."""
    if inputs_ref.get("gate") == EliminationGate.COLLAPSED:
        # Bit-exact by construction: `is_answer_collapsed` reads only `predicted` / `ground_truth`,
        # which rescoring never touches, so this arm can never false-positive on a scorer change.
        cid = str(inputs_ref.get("candidate_id", ""))
        rows = ctx.round_data.all_candidate_results.get(cid) or []
        return is_answer_collapsed(rows[: int(inputs_ref["queries_scored"])])
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
