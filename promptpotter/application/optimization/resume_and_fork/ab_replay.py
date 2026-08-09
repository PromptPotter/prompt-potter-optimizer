"""Deterministic A/B replay. Scoring, election and elimination are a pure function of a round's recorded
measurements, so re-deriving them under another engine is exact and costs zero LLM calls."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from promptpotter.application.mask.divergence import (
    Divergence,
    Verdict,
    VerdictOutcome,
    find_divergences,
)
from promptpotter.application.mask.load import load_mask_record
from promptpotter.application.mask.record import MaskRound
from promptpotter.application.optimization.resume_and_fork.replayers import (
    ReplayMismatch,
    replay_all_mismatches,
)
from promptpotter.application.scoring.formula import rescore_results
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.run_records import ResumeCheckpointKind
from promptpotter.domain.search_point import JobSearchPoint

if TYPE_CHECKING:
    from promptpotter.application.initialization.session import Session
    from promptpotter.application.intelligence.exploration import RulerEntry

logger = logging.getLogger(__name__)

__all__ = ["ab_replay_cycle"]


@dataclass(frozen=True)
class AbReport:
    """``mismatches`` is the EVIDENCE (decisions that re-derive differently); ``divergences`` is the CONSEQUENCE — the
    first round per branch where the change departs, below which every round is counterfactual."""

    campaign_id: str
    cycle_id: str
    scorer_id: str
    n_cycles: int
    n_rounds: int
    mismatches: list[ReplayMismatch]
    divergences: list[Divergence]
    divergent: list[str]

    @property
    def n_mismatches(self) -> int:
        return len(self.mismatches)

    def by_kind(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for d in self.mismatches:
            out[str(d.kind)] = out.get(str(d.kind), 0) + 1
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "campaign_id": self.campaign_id,
            "cycle_id": self.cycle_id,
            "scorer_id": self.scorer_id,
            "n_cycles": self.n_cycles,
            "n_rounds": self.n_rounds,
            "n_mismatches": self.n_mismatches,
            "by_kind": self.by_kind(),
            "mismatches": [
                {
                    "round": d.round_num,
                    "kind": str(d.kind),
                    "recorded": d.recorded_outcome,
                    "current": d.current_outcome,
                    "inputs_ref": d.inputs_ref,
                }
                for d in self.mismatches
            ],
            "divergences": [
                {
                    "node": d.node_key,
                    "cycle_id": d.cycle_id,
                    "round": d.round,
                    "alternative_candidate_id": d.alternative_candidate_id,
                }
                for d in self.divergences
            ],
            "divergent": list(self.divergent),
        }


def _make_replay_verdict(
    session: Session,
    delta_scale: dict[int, RulerEntry] | None,
    sink: list[ReplayMismatch],
) -> Verdict:
    """The replay as a verdict on the shared fold. Rescoring happens HERE, not in a prior pass, because the fold only
    asks about rounds still on the carried-over side of the departure."""
    sc = session.scoring
    scorer = sc.scorer
    assert scorer is not None, "session.scoring.scorer required for A/B replay"

    def verdict(rnd: MaskRound) -> VerdictOutcome:
        rd = rnd.round_data
        if rd is None:
            return VerdictOutcome(diverged=False)
        rescore_results(rd.results, scorer, sc.scorer_id, sc.scorer_formula)
        for items in rd.all_candidate_results.values():
            rescore_results(items, scorer, sc.scorer_id, sc.scorer_formula)
        found = replay_all_mismatches(
            rd, origin_results=rnd.known_outcomes, delta_scale=delta_scale
        )
        if not found:
            return VerdictOutcome(diverged=False)
        sink.extend(found)
        # A flipped ROUND_WINNER names the candidate the current engine would elect, and it
        # was measured — so unlike the abort verdict, this one CAN name the one-step
        # alternative. Any other kind moved without naming a replacement leader.
        alternative = next(
            (
                str(m.current_outcome)
                for m in found
                if m.kind == ResumeCheckpointKind.ROUND_WINNER and m.current_outcome
            ),
            None,
        )
        return VerdictOutcome(diverged=True, alternative_candidate_id=alternative)

    return verdict


def ab_replay_cycle(
    hop: CycleHop,
    session: Session,
    n_min: int,
    *,
    enable_2pl: bool,
) -> AbReport:
    """Re-derive a campaign under the active engine + scorer; no LLM calls. The walk is the CAMPAIGN's — a fork shares its
    parent's measurements, so an invalidating change reaches every branch below and a per-cycle answer cannot say that."""
    # Lazy import: cycle.py is a sibling in this layer and pulls the whole loop; importing it
    # at module load would risk an import cycle through resume_and_fork/__init__.
    from promptpotter.application.intelligence.hard_sample_archive import (
        build_archive_observations,
    )
    from promptpotter.application.optimization.cycle import _calibrate_delta_ruler

    sc = session.scoring
    scorer = sc.scorer
    assert scorer is not None, "session.scoring.scorer required for A/B replay"

    record = load_mask_record(session.store, hop.campaign_id, with_replay=True)
    origin = next(
        (
            rnd.round_data
            for cyc in record.cycles
            if cyc.cycle_id == hop.cycle_id
            for rnd in cyc.rounds
            if rnd.round == 0 and rnd.round_data is not None
        ),
        None,
    )
    # Round 0 = the origin scored; its results calibrate the ruler, exactly as Cycle.start did —
    # including its searchpoint identity, which folds the archive's copies of the origin into the
    # one ``ORIGIN_ABILITY_ID`` candidate the live ruler saw. Rescored first: the ruler is fitted
    # on the grades the CURRENT scorer gives, or arm B is measured against arm A's δ.
    origin_results = origin.results if origin is not None else []
    rescore_results(origin_results, scorer, sc.scorer_id, sc.scorer_formula)
    origin_sp_hash = (
        JobSearchPoint(pipeline_params=origin.pipeline_params).sp_hash(session.pipeline_schema)
        if origin is not None
        else ""
    )
    delta_scale, _, _ = _calibrate_delta_ruler(
        session,
        origin_results,
        n_min,
        enable_2pl=enable_2pl,
        archive_obs=build_archive_observations(
            session.store,
            dataset_name=session.dataset_name,
            origin_sp_hash=origin_sp_hash,
        ),
    )

    mismatches: list[ReplayMismatch] = []
    result = find_divergences(record, _make_replay_verdict(session, delta_scale, mismatches))
    n_rounds = sum(len(c.rounds) for c in record.cycles)
    logger.info(
        "A/B replay: campaign %s, %d cycle(s), %d round(s), %d mismatch(es), %d divergence(s)",
        hop.campaign_id,
        len(record.cycles),
        n_rounds,
        len(mismatches),
        len(result.divergences),
    )
    return AbReport(
        campaign_id=hop.campaign_id,
        cycle_id=hop.cycle_id,
        scorer_id=sc.scorer_id or "",
        n_cycles=len(record.cycles),
        n_rounds=n_rounds,
        mismatches=mismatches,
        divergences=result.divergences,
        divergent=result.divergent,
    )
