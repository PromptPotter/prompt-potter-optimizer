from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from promptpotter.application.views.view_models import ViewContext
from promptpotter.domain.arms import ArmState
from promptpotter.domain.phases import CampaignPhase
from promptpotter.domain.results import Election, RoundResult
from promptpotter.domain.run_records import (
    ArmRecord,
    CycleRecord,
    ElectionRecord,
    FlightRecord,
    FlightWaiting,
    PhaseRecord,
    RaceStandingRecord,
    RoundClosedRecord,
    RoundEnteredRecord,
)
from promptpotter.domain.run_standing import RunStanding
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.llm.telemetry import bound_round
from promptpotter.infrastructure.store.campaign_store.ledger_scan import scan_bench_open

if TYPE_CHECKING:
    from promptpotter.application.bench.difficulty import DifficultyView
    from promptpotter.application.optimizers.nodes import RaceSnapshot
    from promptpotter.application.run_phase_control import Flight
    from promptpotter.domain.campaign_config import LivesConfig
    from promptpotter.domain.paired_reading import ArmKey
    from promptpotter.domain.phase_views import PhaseView
    from promptpotter.domain.ruler import LevelSeries
    from promptpotter.domain.run_standing import LineOutlay


def stated_line(
    closing: RoundResult,
    prior: Sequence[RoundResult],
    difficulty: DifficultyView,
    *,
    lives: LivesConfig | None,
    outlay: LineOutlay,
) -> tuple[list[RoundResult], LevelSeries, RunStanding]:
    """The line a statement of *closing* reads, and the level and standing it reads it at."""
    line = [*(rr for rr in prior if rr.round < closing.round), closing]
    standing = RunStanding.after(line, lives=None if lives is None else lives.bank, outlay=outlay)
    [series] = difficulty.level_series([line])
    return line, series, standing


@dataclass
class RunCallbacks:
    ledger: CycleEventLog
    # What one phase view needs of an earlier one (`views/ingress.py`); a fork keeps its parent's.
    view_context: ViewContext = field(default_factory=ViewContext)

    def _emit(self, record: CycleRecord) -> int:
        """A failed append raises: a run that goes on past a lost record states what it cannot fold."""
        return self.ledger.append(record)

    def on_phase(
        self,
        phase: CampaignPhase | str,
        event: str,
        *,
        round: int | None = None,
        view: PhaseView | None = None,
    ) -> None:
        self._emit(PhaseRecord(phase=str(phase), event=event, round=round, view=view))

    def close_dead_bench(self) -> None:
        """Every holder calls it as it takes the cycle: a bench bracket still open is a dead one's."""
        if scan_bench_open(self.ledger.path):
            self.on_phase(CampaignPhase.BENCH, "exit")

    def on_round_entered(self, round_num: int) -> None:
        self._emit(RoundEnteredRecord(round=round_num))

    def on_election(self, round_num: int, election: Election) -> None:
        self._emit(ElectionRecord(round=round_num, **dict(election)))

    def on_round_close(
        self,
        closing: RoundResult,
        prior: Sequence[RoundResult],
        slot_of: Mapping[str, int],
        difficulty: DifficultyView,
        *,
        lives: LivesConfig | None,
        outlay: LineOutlay,
    ) -> tuple[int, RunStanding]:
        line, series, standing = stated_line(closing, prior, difficulty, lives=lives, outlay=outlay)
        anchors = self.view_context.anchors()
        return self._emit(RoundClosedRecord.of(line, slot_of, series, standing, anchors)), standing

    def on_arm(self, round_num: int, key: ArmKey, state: ArmState) -> None:
        self._emit(
            ArmRecord(round=round_num, arm=key, state=state, anchors=self.view_context.anchors())
        )

    def on_flight(self, flight: Flight) -> None:
        self._emit(
            FlightRecord(
                round=bound_round(),
                out=int(flight.out),
                allowed=int(flight.allowed),
                most=int(flight.most),
                affordable=None if flight.affordable is None else int(flight.affordable),
                cell_usd=None if flight.cell_usd is None else float(flight.cell_usd),
                waiting=None
                if flight.waiting is None
                else FlightWaiting(position=int(flight.waiting[0]), since=float(flight.waiting[1])),
            )
        )

    def on_race_standing(self, member: str, round_num: int, snapshot: RaceSnapshot) -> None:
        self._emit(
            RaceStandingRecord(
                round=round_num,
                member=member,
                current=snapshot.current,
                n_samples=int(snapshot.n_samples),
                p_best=float(snapshot.p_best),
                paired_breakdown=dict(snapshot.paired_breakdown),
            )
        )
