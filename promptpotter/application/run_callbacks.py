from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextvars import Token
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from promptpotter.application.views.view_models import ViewContext
from promptpotter.domain.l4.proxies import PanelPrecision
from promptpotter.domain.phases import CampaignPhase
from promptpotter.domain.results import RoundResult, RunStanding, ScoredCandidate
from promptpotter.domain.run_records import (
    CandidateScoredRecord,
    CandidateStartedRecord,
    CycleRecord,
    ElectionRecord,
    FlightRecord,
    FlightWaiting,
    LedgerFit,
    PhaseRecord,
    RaceCatchUpRecord,
    RaceStandingRecord,
    RoundClosedRecord,
    RoundEnteredRecord,
    RoundStandingRecord,
    SampleOrderRecord,
)
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.llm.telemetry import set_current_round
from promptpotter.shared.errors import graceful

if TYPE_CHECKING:
    from promptpotter.application.optimizers.nodes import RaceSnapshot
    from promptpotter.application.run_phase_control import Flight
    from promptpotter.domain.opt_search_point import OptSearchPoint
    from promptpotter.domain.phase_views import PhaseView

__all__ = ["RunCallbacks"]


def _round_fit(round_result: RoundResult) -> dict[str, LedgerFit]:
    """Keyed by LABEL, the arm's own key: an id names an individual several rounds can read."""
    return {
        cs.label: fit
        for cs in round_result.candidate_scores
        if (
            fit := LedgerFit(
                theta=cs.theta,
                theta_se=cs.theta_se,
                theta_caveat=cs.theta_caveat,
                vs_reference=cs.vs_reference,
            )
        )
        != LedgerFit()
    }


@dataclass
class RunCallbacks:
    ledger: CycleEventLog
    # What one phase view needs of an earlier one (`views/ingress.py`); a fork keeps its parent's.
    view_context: ViewContext = field(default_factory=ViewContext)
    _current_round: int = 0
    _round_token: Token[int | None] | None = field(default=None, init=False, repr=False)

    def _emit(self, record: CycleRecord) -> int | None:
        """``None`` where the append failed — ``next_offset`` then still names the previous record."""
        offset: int | None = None
        with graceful("ledger append failed"):
            offset = self.ledger.append(record)
        return offset

    def on_phase(
        self,
        phase: CampaignPhase | str,
        event: str,
        *,
        round: int | None = None,
        view: PhaseView | None = None,
    ) -> None:
        self._emit(PhaseRecord(phase=str(phase), event=event, round=round, view=view))

    def on_round_entered(self, round_num: int) -> None:
        self._emit(RoundEnteredRecord(round=round_num))

    def on_election(self, round_result: RoundResult) -> None:
        self._emit(
            ElectionRecord(
                round=round_result.round,
                fit=_round_fit(round_result),
                selected_labels=list(round_result.selected_labels),
                elects_on=round_result.elects_on,
                verdict_reason=round_result.verdict_reason,
            )
        )

    def on_round_close(self, round_result: RoundResult) -> int | None:
        return self._emit(RoundClosedRecord.of(round_result))

    def on_round_complete(
        self,
        round_result: RoundResult,
        standing: RunStanding,
        panel_precision: PanelPrecision | None,
    ) -> None:
        self.view_context.run_standing = standing
        self._emit(
            RoundStandingRecord(
                round=round_result.round,
                run_standing=standing,
                panel_precision=panel_precision,
                anchors=self.view_context.anchors(),
            )
        )

    def on_candidate_started(
        self,
        idx: int,
        total: int,
        changes_description: str,
        pipeline_overlay: dict[str, Any] | None,
        prompt_fields: dict[str, Any],
        resolved_pipeline_params: dict[str, Any] | None,
        block: Mapping[str, int] | None = None,
    ) -> None:
        self._emit(
            CandidateStartedRecord(
                round=self._current_round,
                candidate_idx=idx,
                candidate_total=total,
                changes_description=changes_description,
                pipeline_overlay=pipeline_overlay,
                prompt_fields=prompt_fields,
                resolved_pipeline_params=resolved_pipeline_params,
                block=None if block is None else dict(block),
            )
        )

    def announce_candidate(
        self,
        round_num: int,
        idx: int,
        total: int,
        *,
        opt_sp: OptSearchPoint,
        resolved_pipeline_params: dict[str, Any] | None,
        sample_order: Sequence[int],
        n_priors: int = 0,
        pipeline_overlay: dict[str, Any] | None = None,
        block: Mapping[str, int] | None = None,
    ) -> None:
        """Every site that scores an arm calls this, bar `rescore_parent`: the parent holds no slot."""
        self.on_candidate_started(
            idx,
            total,
            opt_sp.lineage.changes_description or "",
            pipeline_overlay,
            opt_sp.prompt_field_dict(),
            resolved_pipeline_params,
            block,
        )
        self.on_sample_order(
            round_num, idx, total, n_priors=n_priors, sample_order=list(sample_order)
        )

    def on_candidate_scored(self, idx: int, total: int, scores: ScoredCandidate) -> None:
        self._emit(
            CandidateScoredRecord(
                round=self._current_round,
                candidate_idx=idx,
                candidate_total=total,
                scores=scores,
                anchors=self.view_context.anchors(),
            )
        )

    def on_flight(self, flight: Flight) -> None:
        self._emit(
            FlightRecord(
                round=self._current_round,
                out=int(flight.out),
                allowed=int(flight.allowed),
                most=int(flight.most),
                affordable=None if flight.affordable is None else int(flight.affordable),
                cell_usd=None if flight.cell_usd is None else float(flight.cell_usd),
                waiting=None
                if flight.waiting is None
                else FlightWaiting(
                    sample_id=int(flight.waiting[0]), since=float(flight.waiting[1])
                ),
                backpressure=flight.backpressure,
            )
        )

    def on_race_standing(
        self, member: str, round_num: int, ci: int, ct: int, snapshot: RaceSnapshot
    ) -> None:
        self._emit(
            RaceStandingRecord(
                round=round_num,
                candidate_idx=ci,
                candidate_total=ct,
                member=member,
                current_id=str(snapshot.current_id),
                n_samples=int(snapshot.n_samples),
                p_best=float(snapshot.p_best),
                paired_breakdown=dict(snapshot.paired_breakdown),
                decision_grade=snapshot.decision_grade,
            )
        )

    def on_sample_order(
        self,
        round_num: int,
        ci: int,
        ct: int,
        *,
        n_priors: int,
        sample_order: list[int],
    ) -> None:
        """Not the heatmap's ``sample_order``: absolute difficulty there, relevance here."""
        self._emit(
            SampleOrderRecord(
                round=round_num,
                candidate_idx=ci,
                candidate_total=ct,
                n_priors=int(n_priors),
                sample_order=[int(sid) for sid in sample_order],
            )
        )

    def on_race_catch_up(
        self,
        member: str,
        round_num: int,
        ci: int,
        ct: int,
        sample_id: int,
        prior_ids: list[str],
    ) -> None:
        """No record where the cache already covered the priors."""
        if not prior_ids:
            return
        self._emit(
            RaceCatchUpRecord(
                round=round_num,
                candidate_idx=ci,
                candidate_total=ct,
                member=member,
                sample_id=int(sample_id),
                prior_ids=[str(p) for p in prior_ids],
            )
        )

    def set_round(self, round_num: int) -> None:
        """Only the first ``set`` carries a meaningful restore token; ``drain_all`` resets that one."""
        self._current_round = round_num
        token = set_current_round(round_num)
        if self._round_token is None:
            self._round_token = token
