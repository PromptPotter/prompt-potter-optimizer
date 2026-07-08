"""Deterministic A/B replay engine — re-derive a recorded cycle's decisions under the
current engine/scorer and report every divergence.

The honest A/B. A recorded round holds every candidate's per-sample measurements
(``all_candidate_results``) + the decisions, and scoring / election / elimination is a
**pure function** over them. So re-deriving the decisions over the recorded measurements
under the current engine + scorer is fully deterministic — **zero LLM calls**. Run a cycle
under engine/scorer A (it records its decisions to disk), then run this under engine/scorer
B → every round-winner / elimination / escalation-trigger that flips is reported. (Running a
campaign twice can't be an A/B: candidate generation is non-deterministic. Replaying recorded
measurements can.)

Arm A = what is on disk (the engine/scorer that recorded the cycle). Arm B = the current
process (current code + the active campaign scorer). Same code + same scorer ⇒ no divergence
(a self-consistency check); a code change (recorded by an older checkout) or a scorer change
surfaces exactly where the decision flips.

Caveat: the per-cycle δ ruler is re-calibrated here from the *current* grade-A archive
(``_calibrate_delta_ruler``), not the exact start-of-cycle ruler (which is never persisted),
so θ values are reconstructed, not bit-identical to the live run — fine for a decision diff.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from promptpotter.application.optimization.resume_and_fork.replayers import (
    Divergence,
    replay_all_divergences,
)
from promptpotter.application.scoring.formula import rescore_results

if TYPE_CHECKING:
    from promptpotter.application.bootstrap.session import Session
    from promptpotter.infrastructure.store import CampaignStore

logger = logging.getLogger(__name__)

__all__ = ["AbReport", "ab_replay_cycle"]


@dataclass(frozen=True)
class AbReport:
    """Result of an A/B replay — every recorded decision that re-derives differently."""

    cycle_id: str
    scorer_id: str
    n_rounds: int
    divergences: list[Divergence]

    @property
    def n_divergences(self) -> int:
        return len(self.divergences)

    def by_kind(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for d in self.divergences:
            out[str(d.kind)] = out.get(str(d.kind), 0) + 1
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "cycle_id": self.cycle_id,
            "scorer_id": self.scorer_id,
            "n_rounds": self.n_rounds,
            "n_divergences": self.n_divergences,
            "by_kind": self.by_kind(),
            "divergences": [
                {
                    "round": d.round_num,
                    "kind": str(d.kind),
                    "recorded": d.recorded_outcome,
                    "current": d.current_outcome,
                    "inputs_ref": d.inputs_ref,
                }
                for d in self.divergences
            ],
        }


def ab_replay_cycle(
    campaign_store: CampaignStore,
    campaign_id: str,
    cycle_id: str,
    session: Session,
    n_min: int,
    *,
    enable_2pl: bool,
) -> AbReport:
    """Re-derive every recorded decision of *cycle_id* under the session's active engine +
    scorer; return all divergences. Deterministic, no LLM calls. ``n_min`` is the campaign's
    ``optimization.elimination_n_min`` — the ruler-warmth floor (see ``_calibrate_delta_ruler``);
    ``enable_2pl`` is ``optimization.enable_2pl_graduation`` — the same 1PL/2PL
    calibration gate the live cycle ran, so the reconstructed ruler matches its model."""
    # Lazy import: cycle.py is a sibling in this layer and pulls the whole loop; importing it
    # at module load would risk an import cycle through resume_and_fork/__init__.
    from promptpotter.application.optimization.cycle import (
        _calibrate_delta_ruler,
        _merge_into_cumulative,
    )

    sc = session.scoring
    scorer = sc.scorer
    assert scorer is not None, "session.scoring.scorer required for A/B replay"
    # ``load_rounds_range`` iterates ``range(0, end+1)`` — read the real max round from the
    # cycle index rather than scanning a huge fixed ceiling.
    index = campaign_store.load(campaign_id, cycle_id)
    round_summaries = (index or {}).get("rounds") or []
    max_round = max((int(r["round"]) for r in round_summaries), default=-1)
    rounds = (
        campaign_store.load_rounds_range(campaign_id, cycle_id, 0, max_round)
        if max_round >= 0
        else []
    )

    def _rescore(items: Any) -> None:
        rescore_results(list(items or []), scorer, sc.scorer_id, sc.scorer_formula)

    for t in rounds:
        _rescore(t.get("results"))
        for items in (t.get("all_candidate_results") or {}).values():
            _rescore(items)

    # Cumulative winner frontier — the same sample-keyed fold the live loop keeps in
    # ``Cycle.tracking.current_results`` (resume's election origin floor), so replay and live
    # agree by construction rather than by two hand-copied merges.
    origin_frontier: list[dict[str, Any]] = []
    for t in rounds:
        origin_frontier = _merge_into_cumulative(origin_frontier, t.get("results") or [])
    # Round 0 = the origin scored; its results calibrate the ruler, exactly as Cycle.start did.
    origin_results = rounds[0].get("results") or [] if rounds else []
    delta_scale, _ = _calibrate_delta_ruler(session, origin_results, n_min, enable_2pl=enable_2pl)

    divergences: list[Divergence] = []
    for i, t in enumerate(rounds):
        divergences.extend(
            replay_all_divergences(
                t,
                prior_rounds=rounds[:i],
                origin_results=origin_frontier,
                delta_scale=delta_scale,
            )
        )
    logger.info(
        "A/B replay: cycle %s, %d rounds, %d divergence(s)", cycle_id, len(rounds), len(divergences)
    )
    return AbReport(
        cycle_id=cycle_id,
        scorer_id=sc.scorer_id or "",
        n_rounds=len(rounds),
        divergences=divergences,
    )
