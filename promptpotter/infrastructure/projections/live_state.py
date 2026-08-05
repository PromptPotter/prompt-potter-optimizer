"""LiveStateCore + spend bookkeeping — shared accumulators for live subscribers.

``LiveDisplay`` and ``LiveDashboardView`` both subscribe to the same ledger;
the overlap (round number, origin/best anchors, PoBB snapshot, spend
bucket shapes + resume backfill) lives here. The per-record spend-bucket
mutator lives on ``LiveDashboardView`` (the sole writer) — what stays
here is the bucket *shape* + the resume-time rate backfill that runs once
on construction."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from promptpotter.domain.phases import CampaignPhase, PhaseEvent
from promptpotter.shared.spend import compute_usd

__all__ = [
    "LiveStateCore",
    "apply_p_best_update",
    "apply_phase",
    "backfill_spend_rates",
    "roll_p_best_at_round_complete",
    "top_n_p_best",
]


def backfill_spend_rates(spend: dict[str, Any]) -> dict[str, Any]:
    """Recompute ``used_usd`` on buckets whose rate now resolves.

    Covers a bucket whose model had no rate on file when it was written (a new model,
    a stale rate table). Re-resolves on load rather than replaying the ledger —
    per-event historical drift accepted. Touches the BILL only; ``incurred_usd`` carries
    no ``rate_known`` flag, and an unpriced incurred cost is surfaced by
    ``incurred_unpriced_tokens`` instead of silently back-filled."""
    for b in spend.values():
        if not isinstance(b, dict):
            continue
        if b.get("rate_known") is True:
            continue
        model = b.get("model")
        in_tok = int(b.get("input_tokens") or 0)
        out_tok = int(b.get("output_tokens") or 0)
        if not model or (in_tok == 0 and out_tok == 0):
            continue
        usd = compute_usd(model, in_tok, out_tok)
        if usd is None:
            continue
        b["used_usd"] = round(float(usd), 6)
        b["rate_known"] = True
    spend["total_used_usd"] = round(
        sum(float(b.get("used_usd") or 0.0) for b in spend.values() if isinstance(b, dict)),
        6,
    )
    return spend


@dataclass
class LiveStateCore:
    round_num: int = 0
    origin_acc: float = 0.0
    best_acc: float = 0.0
    # Candidate id → its latest P(best) this round, and the value each held before that —
    # the population a round-wide reading is about. A ``PoBBSnapshot`` carries ONE
    # candidate's number, so anything ranking candidates accumulates here instead of
    # reading a single snapshot. ``current_p_best_id`` empty ⇒ no reading this round yet.
    round_p_best: dict[str, float] = field(default_factory=dict)
    round_p_best_prev: dict[str, float] = field(default_factory=dict)
    current_p_best_id: str = ""
    current_p_best_n: int = 0


def apply_phase(core: LiveStateCore, event: PhaseEvent, view: Any = None) -> None:
    """Update *core* from a ``PhaseEvent``. Origin set once on ``INIT:exit`` and
    never re-anchored (immutable campaign origin). Leader (``best_acc``) advances
    on improved ``L1_SCORE:exit``.

    ``view`` is the typed view object the producer built — ``InitExitView`` on
    ``INIT:exit``, ``RoundCompleteView`` on ``L1_SCORE:exit`` (``from_phase_event``
    binds each phase to its view type). Read by attribute so this projection stays
    agnostic to the view classes without importing them upward."""
    if event.round is not None:
        core.round_num = event.round
    if view is None:
        return
    if event.phase == CampaignPhase.INIT and event.event == "exit":
        new_origin: float = view.origin_acc
        core.origin_acc = new_origin
        if new_origin > core.best_acc:
            core.best_acc = new_origin
    elif event.phase == CampaignPhase.L1_SCORE and event.event == "exit" and view.improved:
        winner: float = view.winner_accuracy
        if winner > core.best_acc:
            core.best_acc = winner


def apply_p_best_update(
    core: LiveStateCore,
    current_id: str,
    n_samples: int,
    p_best: float,
) -> None:
    """Record one candidate's P(best) reading. Empty ``current_id`` ⇒ nothing to record."""
    if not current_id:
        return
    if current_id in core.round_p_best:
        core.round_p_best_prev[current_id] = core.round_p_best[current_id]
    core.round_p_best[current_id] = float(p_best)
    core.current_p_best_id = current_id
    core.current_p_best_n = n_samples


def roll_p_best_at_round_complete(core: LiveStateCore) -> None:
    """Clear the round's readings at round-end so the next round starts blank.

    Candidate ids are round-scoped, so carrying a previous round's map forward could only
    ever compare a candidate against a stranger — which is what the ``last_p_best`` field
    this replaces did, silently, since no id ever matched across the boundary.
    """
    core.round_p_best = {}
    core.round_p_best_prev = {}
    core.current_p_best_id = ""
    core.current_p_best_n = 0


def top_n_p_best(standings: dict[str, float], n: int = 5) -> list[tuple[str, float]]:
    """Top-*n* ``(candidate_id, p_best)`` descending, over a CANDIDATE→standing map.

    Every key must be a candidate's own P(best). It used to be handed one candidate's
    ``PoBBSnapshot`` dict, whose non-current keys were *P(current beats that prior)* — so the
    "leaderboard" ranked one real candidate against numbers describing that same candidate,
    filed under its opponents' ids.
    """
    return sorted(standings.items(), key=lambda kv: -kv[1])[:n]
