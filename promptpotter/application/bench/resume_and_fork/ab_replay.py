"""Decisions are a pure function of a round's recorded measurements, so replay costs no LLM call."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from promptpotter.application.bench.difficulty import judging_ruler, session_ruler_scope
from promptpotter.application.bench.resume_and_fork.replayers import (
    ReplayMismatch,
    replay_all_mismatches,
)
from promptpotter.application.mask.divergence import (
    Divergence,
    Verdict,
    VerdictOutcome,
    find_divergences,
)
from promptpotter.application.mask.load import load_mask_record
from promptpotter.application.mask.record import MaskRound
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.shared.errors import DiagnosticError

if TYPE_CHECKING:
    from promptpotter.application.initialization.session import Session
    from promptpotter.domain.campaign_config import CampaignConfig
    from promptpotter.domain.results import RoundResult
    from promptpotter.domain.scoring import CellSheet, Scorer

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AbReport:
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


def _regraded(rd: RoundResult, scorer: Scorer) -> RoundResult:
    """Both row sets: a replayer reads an individual across them, and a sheet holds one scorer."""

    def under[K: str](sheets: dict[K, CellSheet]) -> dict[K, CellSheet]:
        return {key: scorer.sheet(cell.facts for cell in sheet) for key, sheet in sheets.items()}

    return rd.model_copy(
        update={"cells": under(rd.cells), "overlap_results": under(rd.overlap_results)}
    )


def _make_replay_verdict(
    session: Session,
    campaign_config: CampaignConfig,
    sink: list[ReplayMismatch],
) -> Verdict:
    """Rescored HERE, each round on the scale its election read: the fold asks only of carried-over rounds."""
    scorer = session.state.scoring.require_scorer()
    scope = session_ruler_scope(session)
    opt = campaign_config.optimization

    def verdict(rnd: MaskRound) -> VerdictOutcome:
        if rnd.round_data is None:
            return VerdictOutcome(diverged=False)
        rd = _regraded(rnd.round_data, scorer)
        if rd.election is None:
            return VerdictOutcome(diverged=False)
        read = rd.election.ruler
        ruler = judging_ruler(
            session.store.ruler,
            scope,
            read.reach,
            read.excluded,
            objective_id=scorer.id,
            n_min=opt.elimination_n_min,
            enable_2pl=opt.enable_2pl_graduation,
        ).ruler
        found = replay_all_mismatches(rd, rnd.decisions, ruler=ruler)
        if not found:
            return VerdictOutcome(diverged=False)
        sink.extend(found)
        alternative = next(
            (
                m.current_outcome
                for m in found
                if isinstance(m.current_outcome, str) and m.current_outcome in rd.panel
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
    """The walk is the CAMPAIGN's: a fork shares its parent's measurements."""
    scorer = session.state.scoring.require_scorer()

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
        raise DiagnosticError(
            f"{hop.campaign_id}/{hop.cycle_id} has no scored round 0, so it names no origin and "
            "every replayed decision would be read against nothing."
        )
    mismatches: list[ReplayMismatch] = []
    result = find_divergences(record, _make_replay_verdict(session, campaign_config, mismatches))
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
        scorer_id=scorer.id,
        n_cycles=len(record.cycles),
        n_rounds=n_rounds,
        mismatches=mismatches,
        divergences=result.divergences,
        divergent=result.divergent,
    )
