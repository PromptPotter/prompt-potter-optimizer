"""LiveStateCore — per-cycle scalars shared by terminal + dashboard projections.

Both ``LiveDisplay`` (terminal) and ``LiveDashboardView`` (``dashboard.json``)
subscribe the same ``CycleEventLog``. They maintain divergent surface-specific
state (tqdm bars on one side, JSON spend rollup on the other), but a small
core overlaps:

* the active round number,
* the running baseline + best anchors (updated on ``INIT:exit`` and on an
  improved ``L1_SCORE:exit``), and
* the round-wide Posterior-of-Being-Best snapshot used to render cross-round
  ▲/▼ arrows.

That overlap lives here so each renderer can stay thin against it. Surface
state stays on each class.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from promptpotter.domain.phases import CampaignPhase, PhaseEvent

__all__ = [
    "LiveStateCore",
    "apply_p_best_update",
    "apply_phase",
    "roll_p_best_at_round_complete",
    "top_n_p_best",
]


@dataclass
class LiveStateCore:
    """Shared per-cycle scalars for live ledger subscribers."""

    round_num: int = 0
    baseline_acc: float = 0.0
    best_acc: float = 0.0
    last_p_best: dict[str, float] = field(default_factory=dict)
    current_p_best: dict[str, float] = field(default_factory=dict)
    current_p_best_id: str = ""
    current_p_best_n: int = 0


def apply_phase(core: LiveStateCore, event: PhaseEvent, view: dict | None = None) -> None:
    """Update *core* from a ``PhaseEvent``.

    Round number tracks ``event.round`` directly. Baseline + best anchors
    pick up the post-baseline accuracy on ``INIT:exit`` and the new winner
    on an improved ``L1_SCORE:exit``, so cross-round arrows and per-candidate
    deltas always read against the freshest anchor.
    """
    if event.round is not None:
        core.round_num = event.round
    if view is None:
        return
    if event.phase == CampaignPhase.INIT and event.event == "exit":
        new_baseline = view.get("baseline_acc", core.baseline_acc)
        core.baseline_acc = new_baseline
        if new_baseline > core.best_acc:
            core.best_acc = new_baseline
    elif event.phase == CampaignPhase.L1_SCORE and event.event == "exit" and view.get("improved"):
        winner = view.get("winner_accuracy", core.baseline_acc)
        core.baseline_acc = winner
        if winner > core.best_acc:
            core.best_acc = winner


def apply_p_best_update(
    core: LiveStateCore,
    current_id: str,
    n_samples: int,
    p_best: dict[str, float],
) -> None:
    """Stash the latest mid-round P(best) snapshot for the round-end roll-up."""
    if not p_best:
        return
    core.current_p_best = dict(p_best)
    core.current_p_best_id = current_id
    core.current_p_best_n = n_samples


def roll_p_best_at_round_complete(core: LiveStateCore) -> None:
    """At round-end, promote the current snapshot to ``last`` for next round's arrows."""
    if core.current_p_best:
        core.last_p_best = core.current_p_best
        core.current_p_best = {}
        core.current_p_best_id = ""
        core.current_p_best_n = 0


def top_n_p_best(snapshot: dict[str, float], n: int = 5) -> list[tuple[str, float]]:
    """Top-*n* ``(cid, prob)`` from a snapshot, descending."""
    return sorted(snapshot.items(), key=lambda kv: -kv[1])[:n]
