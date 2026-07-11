"""Bootstrap helper for ``LiveDashboardView.for_session``.

The factory classmethod resolves the cycle's own dir, reads any prior
``dashboard.json`` (from a seed dir — the cycle's own, or the parent's for a
fork), and self-heals its stale ``round`` / ``best`` / ``rounds`` pointers
against what completed-round checkpoints actually exist on disk. That
disk-reconciliation logic lives here as a free function so the classmethod
stays a thin assembly of: resolve ids → resolve resume state → construct.
"""

from __future__ import annotations

from pathlib import Path

from promptpotter.domain.results import best_round_by_cumulative_accuracy
from promptpotter.infrastructure.projections.live_dashboard.state import LiveDashboardState
from promptpotter.infrastructure.projections.live_state import backfill_spend_rates
from promptpotter.infrastructure.store.io import read_json_tolerant
from promptpotter.infrastructure.store.layout import CycleLayout

__all__ = ["resolve_resume_state"]


def _max_round_on_disk(rounds_dir: Path) -> int:
    """Highest ``round_NNNN.json`` index in ``rounds_dir``, or ``0`` if none."""
    if not rounds_dir.is_dir():
        return 0
    highest = 0
    for path in rounds_dir.glob("round_*.json"):
        suffix = path.stem[len("round_") :]  # ``round_0003`` → ``0003``
        if suffix.isdigit():
            highest = max(highest, int(suffix))
    return highest


def resolve_resume_state(
    seed_dir: Path,
    active_cycle_dir: Path,
    resumed_from_round: int | None,
) -> LiveDashboardState | None:
    """Read the prior ``dashboard.json`` and self-heal its trajectory pointers.

    Returns the seed state for the :class:`LiveDashboardView` constructor, or ``None``
    when no prior file exists. The file IS a ``LiveDashboardState`` dump, so it is
    validated back into one here — the view then carries the whole prior state forward
    instead of a hand-picked subset of its fields.

    ``seed_dir`` is where the prior ``dashboard.json`` is read from — the cycle's own
    dir on a root/resume, or the parent cycle's dir on a fork (the fork inherits the
    parent's trajectory up to the cut). ``active_cycle_dir`` is always the cycle's own
    dir; its ``rounds/`` checkpoints drive the round-pointer self-heal. Origin rides
    ``rounds[0]`` like any round, so the seeded ``rounds`` already carries it — no
    separate origin reconciliation.

    **This is the one place the surviving trajectory is cut.** Rounds at or past
    ``resumed_from_round`` are about to be re-run, so they are dropped here and ``best``
    is re-derived from what is left via the shared ``best_round_by_cumulative_accuracy``
    (the cycle index's ``_apply_best`` rides the same helper) — never trusted from the
    prior scalar, whose rolling max is monotonic and would keep a high-water mark from a
    round that no longer exists. The cut used to be applied to ``best`` only, while the
    full ``rounds`` list was handed on intact: a fork's first dashboard therefore showed
    the parent's post-cut rounds under a ``best`` that excluded them, and the campaign
    store carried a second, schema-bypassing writer to paper over the resume half of it.
    """
    raw = read_json_tolerant(CycleLayout(seed_dir).dashboard)
    if not isinstance(raw, dict):
        return None

    # An optimizer cache hit short-circuits ``emit_token_usage``, so a re-run never
    # re-fires the loop bucket's TokenUsageRecords — re-resolve any rate that now prices.
    if isinstance(spend := raw.get("spend"), dict):
        backfill_spend_rates(spend)

    prior = LiveDashboardState.model_validate(raw)
    surviving = [
        r for r in prior.rounds if resumed_from_round is None or r.round < resumed_from_round
    ]
    best, _ = best_round_by_cumulative_accuracy([r.model_dump() for r in surviving])
    return prior.model_copy(
        update={
            "rounds": surviving,
            "round": max(prior.round, _max_round_on_disk(CycleLayout(active_cycle_dir).rounds)),
            "best": best,
        }
    )
