"""Decision records and replayers — the kind-agnostic divergence mechanism.

Every loop decision (round winner, sequential-elimination cut, L2/L3
escalation trigger, …anything future) is a pure function of scored
results plus local state. We persist each decision's ``kind`` + minimal
``inputs_ref`` pointers + ``outcome`` inside the trial JSON. On resume,
we rescore the trial's per-query results under the currently active
scorer and re-run each decision via a kind-keyed replayer registry. The
first mismatch is a :class:`Divergence`, which the CLI converts into a
:class:`ResumeDivergenceError` and the user either reverts the scorer or
runs ``fork`` to branch.

The shape is "stable forever": adding a new decision kind is one call to
:func:`record_decision` at the site plus a replayer registered via
:func:`replayer`. Neither the walker nor the fork machinery ever inspects
``kind``.

Option A per design: ``inputs_ref`` stores only pointers (candidate_ids,
round_num, thresholds) — replayers re-derive their own inputs from the
trial's rescored view. Trial JSONs stay compact; replay genuinely
re-executes the decision rather than comparing cached numbers.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "REPLAYERS",
    "Decision",
    "Divergence",
    "ReplayContext",
    "record_decision",
    "replay_decisions",
    "replayer",
]


@dataclass(frozen=True)
class Decision:
    """One recorded decision in a trial.

    ``inputs_ref`` holds pointers (ids, round_num, thresholds) — not full
    inputs. The replayer fetches its actual inputs from the trial's
    rescored view via :class:`ReplayContext`.
    """

    kind: str
    inputs_ref: dict[str, Any]
    outcome: Any

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "inputs_ref": dict(self.inputs_ref), "outcome": self.outcome}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Decision:
        return cls(
            kind=d["kind"], inputs_ref=dict(d.get("inputs_ref") or {}), outcome=d.get("outcome")
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
    """Context passed to replayers.

    ``trial`` is the currently-being-replayed trial dict with its
    ``results`` / ``all_candidate_results`` already rescored under the
    active scorer — replayers read from this directly.

    ``prior_trials`` carries earlier rounds (same rescoring applied) for
    decisions that span multiple rounds (L2/L3 improvement-gate triggers).
    """

    trial: dict[str, Any]
    prior_trials: list[dict[str, Any]] = field(default_factory=list)


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
) -> Any:
    """Append a ``Decision`` to *decisions* and return *outcome* for passthrough.

    Callers pass their round-local decisions list (sitting on
    ``RoundResult.decisions`` or equivalent). The outcome returns so
    call sites stay one-line::

        winner_id = record_decision(
            state.current_decisions,
            "round_winner",
            {"candidate_ids": [...], "round_num": R},
            _select_round_winner(...),
        )
    """
    decisions.append(Decision(kind, dict(inputs_ref), outcome).to_dict())
    return outcome


def replay_decisions(
    trial: dict[str, Any],
    prior_trials: list[dict[str, Any]] | None = None,
) -> Divergence | None:
    """Walk ``trial['decisions']`` in order; return the first mismatch.

    Unknown kinds (added in a newer version, resumed under an older one)
    are silently skipped — forward-compat. A decision whose replayer
    raises is treated as a non-divergence (``logged, continue``); hard
    failures belong in the scorer or replayer itself, not here.
    """
    ctx = ReplayContext(trial=trial, prior_trials=list(prior_trials or []))
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


# ---------------------------------------------------------------------------
# Built-in replayers
# ---------------------------------------------------------------------------


def _mean_score(results: list[dict]) -> float:
    """Mean of the active-scorer projection (``score``) over results.

    Expects results to have been rescored by the caller before replay.
    """
    if not results:
        return 0.0
    return sum(float(r.get("score", 0.0)) for r in results) / len(results)


@replayer("round_winner")
def _replay_round_winner(ctx: ReplayContext, inputs_ref: dict[str, Any]) -> str:
    """Re-derive the round's winner id from rescored per-candidate results.

    Mirrors ``_select_round_winner`` in ``application/optimization/nodes/score.py``:
    pick the candidate whose rescored mean score beats
    ``current_best_accuracy`` by more than the nothing (strict >), else
    the recorded current-best wins (outcome ``""``).

    ``inputs_ref`` carries:

    - ``candidate_ids``: list of candidate ids evaluated this round.
    - ``current_best_accuracy``: float — baseline-or-prior-winner score.

    Reads ``trial['all_candidate_results']`` — per-candidate result lists,
    already rescored under the active scorer at trial-load time.
    """
    all_results: dict[str, list[dict]] = ctx.trial.get("all_candidate_results") or {}
    best_acc = float(inputs_ref.get("current_best_accuracy", 0.0))
    winner_id = ""
    for cid in inputs_ref.get("candidate_ids") or []:
        acc = _mean_score(all_results.get(cid) or [])
        if acc > best_acc:
            best_acc = acc
            winner_id = cid
    return winner_id
