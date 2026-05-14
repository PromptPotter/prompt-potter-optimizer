"""PoBB telemetry block for ``dashboard.json::current_round.pobb``.

Pure projection over the shared ``LiveStateCore``: leader probability,
posterior-width, and the round-wide top-5 leaderboard.
"""

from __future__ import annotations

from typing import Any

from promptpotter.infrastructure.projections.live_state import LiveStateCore


def build_pobb_block(core: LiveStateCore, round_block: dict[str, Any]) -> dict[str, Any]:
    """Round-wide PoBB telemetry: leader probability, posterior-width, top-5.

    ``posterior_width = 1 - max(p_best)`` — distance the leader has
    from certainty. Width near 0 = leader confirmed; width near 1 =
    flat posterior, more samples needed before elimination is safe.
    Operators read this to judge whether ``elimination_n_min`` is set
    appropriately for the dataset's per-sample variance.
    """
    p_best = core.current_p_best
    if not p_best:
        return {
            "current_id": "",
            "n_samples": 0,
            "leader_prob": 0.0,
            "posterior_width": 1.0,
            "top": [],
        }
    leader_prob = max(p_best.values())
    return {
        "current_id": core.current_p_best_id,
        "n_samples": core.current_p_best_n,
        "leader_prob": float(leader_prob),
        "posterior_width": float(1.0 - leader_prob),
        "top": list(round_block.get("p_best_top") or []),
    }


__all__ = ["build_pobb_block"]
