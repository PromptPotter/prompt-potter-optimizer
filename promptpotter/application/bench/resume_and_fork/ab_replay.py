"""Deterministic A/B replay. Scoring, election and elimination are a pure function of a round's recorded
measurements, so re-deriving them under another engine is exact and costs zero LLM calls."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from promptpotter.application.bench.difficulty import DifficultyView, calibrate_delta_ruler
from promptpotter.application.bench.resume_and_fork.replayers import (
    ReplayMismatch,
    replay_all_mismatches,
)
from promptpotter.application.bench.task_context import campaign_framing
from promptpotter.application.mask.divergence import (
    Divergence,
    Verdict,
    VerdictOutcome,
    find_divergences,
)
from promptpotter.application.mask.load import load_mask_record
from promptpotter.application.mask.record import MaskRound
from promptpotter.application.scoring.formula import rescore_results
from promptpotter.domain.cycle_paths import CycleHop

if TYPE_CHECKING:
    from promptpotter.application.campaign_config import CampaignConfig
    from promptpotter.application.initialization.session import Session
    from promptpotter.domain.ruler import DeltaRuler

logger = logging.getLogger(__name__)

__all__ = ["AbReplayError", "AbReport", "ab_replay_cycle"]


class AbReplayError(Exception):
    """The addressed campaign cannot be replayed off disk. The CLI shell maps it to a clean ``SystemExit``."""


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
    divergent: list[tuple[str, int]]

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
            # `node` is a LABEL for a human reading the report, composed here at the render
            # site. It was a field on the record and got parsed back into this same pair by the
            # tree's overlay — the two spellings of one coordinate that the pair now replaces.
            "divergences": [
                {
                    "node": f"{d.cycle_id}::r{d.round}",
                    "cycle_id": d.cycle_id,
                    "round": d.round,
                    "alternative_candidate_id": d.alternative_candidate_id,
                }
                for d in self.divergences
            ],
            "divergent": [f"{cid}::r{rnd}" for cid, rnd in self.divergent],
        }


def _make_replay_verdict(
    session: Session,
    ruler: DeltaRuler | None,
    sink: list[ReplayMismatch],
) -> Verdict:
    """The replay as a verdict on the shared fold. Rescoring happens HERE, not in a prior pass, because the fold only
    asks about rounds still on the carried-over side of the departure."""
    sc = session.scoring
    scorer = sc.require_scorer()

    def verdict(rnd: MaskRound) -> VerdictOutcome:
        rd = rnd.round_data
        if rd is None:
            return VerdictOutcome(diverged=False)
        rescore_results(rd.results, scorer)
        for items in rd.all_candidate_results.values():
            rescore_results(items, scorer)
        # No anchor passed: `known_outcomes` pools rows from DIFFERENT configurations and its own
        # docstring forbids scoring it. Each decision carries the anchor its election used.
        found = replay_all_mismatches(rd, rnd.decisions, ruler=ruler)
        if not found:
            return VerdictOutcome(diverged=False)
        sink.extend(found)
        # A decision re-deriving to a measured arm is a flipped election: unlike the abort verdict,
        # it names the one-step alternative. Any other kind moved without naming a leader.
        alternative = next(
            (
                m.current_outcome
                for m in found
                if isinstance(m.current_outcome, str)
                and m.current_outcome in rd.all_candidate_results
            ),
            None,
        )
        return VerdictOutcome(diverged=True, alternative_candidate_id=alternative)

    return verdict


def ab_replay_cycle(
    hop: CycleHop,
    session: Session,
    campaign_config: CampaignConfig,
) -> AbReport:
    """Re-derive a campaign under the active engine + scorer; no LLM calls. The walk is the CAMPAIGN's — a fork shares its
    parent's measurements, so an invalidating change reaches every branch below and a per-cycle answer cannot say that."""
    sc = session.scoring
    scorer = sc.require_scorer()

    record = load_mask_record(session.store, hop.campaign_id, lens=None, with_replay=True)
    round_0 = next(
        (
            rnd.round_data
            for cyc in record.cycles
            if cyc.cycle_id == hop.cycle_id
            for rnd in cyc.rounds
            if rnd.round == 0 and rnd.round_data is not None
        ),
        None,
    )
    if round_0 is None:
        raise AbReplayError(
            f"{hop.campaign_id}/{hop.cycle_id} has no scored round 0, so there is no origin to "
            "calibrate the δ ruler on and every replayed decision would be read against nothing."
        )
    # Round 0 = the origin scored; its results calibrate the ruler, exactly as Cycle.start did —
    # including its searchpoint identity, which folds the archive's copies of the origin into the
    # one ``ORIGIN_ABILITY_ID`` candidate the live ruler saw. Rescored first: the ruler is fitted
    # on the grades the CURRENT scorer gives, or arm B is measured against arm A's δ.
    rescore_results(round_0.results, scorer)
    origin_sp_hash = round_0.origin.searchpoint(
        schema=session.pipeline_schema,
        framing=campaign_framing(session.store, campaign_config, session.dataset_name),
        demo=sc.require_partition().demo,
    ).sp_hash(session.pipeline_schema)
    view = DifficultyView(
        session=session,
        n_min=campaign_config.optimization.elimination_n_min,
        enable_2pl=campaign_config.optimization.enable_2pl_graduation,
        origin_sp_hash=origin_sp_hash,
    )
    ruler, _ = calibrate_delta_ruler(
        round_0.results, view.n_min, enable_2pl=view.enable_2pl, archive_obs=view.archive()
    )

    mismatches: list[ReplayMismatch] = []
    result = find_divergences(record, _make_replay_verdict(session, ruler, mismatches))
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
