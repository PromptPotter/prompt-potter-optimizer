"""A round's records: entered, proposed, each arm's transition, its close, and a self-heal it ran past."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal, Self

from pydantic import ConfigDict, Field

from promptpotter.domain.arms import ArmRole, ArmState, Proposal, arm_name
from promptpotter.domain.dashboard_rows import DashboardCandidate, RoundHealth, RoundSummary
from promptpotter.domain.l4.proxies import PanelPrecision, panel_precision
from promptpotter.domain.optimizer_state import OptimizerState
from promptpotter.domain.origin_panel import RoundEnding
from promptpotter.domain.paired_reading import ArmKey, ArmName
from promptpotter.domain.pass_kinds import IndividualWalk
from promptpotter.domain.records import CycleRecord
from promptpotter.domain.results import Election, RoundOutcome, RoundResult, round_walks
from promptpotter.domain.ruler import RulerReach
from promptpotter.domain.run_standing import RunStanding
from promptpotter.domain.scoring import WalkedCell
from promptpotter.domain.strict_model import FrozenModel
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.errors import fault_said


class RoundEnteredRecord(CycleRecord):
    """Entering N displaces N and every later round; ``rewound`` discards their proposals too."""

    record_type: Literal["round_entered"] = "round_entered"
    round: int
    rewound: bool = False
    timestamp: str = Field(default_factory=utcnow_iso)


class RoundStatement(RoundOutcome):
    """A closed round as a ledger states it: the last statement per round stands and IS the round."""

    optimizer_state: OptimizerState
    # Its arms' rows as archive addresses, in walk order, then its origin-panel rows by individual.
    cells: dict[ArmKey, list[WalkedCell]]
    panel_cells: dict[str, list[WalkedCell]]
    # Each walked cell's position in the bank the cycle drew: a cell carries its key alone.
    positions: dict[str, int]
    run_standing: RunStanding
    # Leading arm against the ORIGIN's rows: a pairing across two rounds of the closing line.
    panel_precision: PanelPrecision | None
    timestamp: str = Field(default_factory=utcnow_iso)

    @classmethod
    def of(
        cls,
        line: Sequence[RoundResult],
        position_of: Mapping[str, int],
        run_standing: RunStanding,
        **own: Any,
    ) -> Self:
        rr = line[-1]
        cells = rr.addresses()
        election = rr.election
        leading = (
            None if election is None or election.ending is RoundEnding.ADOPTED else election.leading
        )
        panel = {key: rows.addresses() for key, rows in rr.panel_rows.items()}
        walked = {key for walk in (*cells.values(), *panel.values()) for key, _, _ in walk}
        return cls(
            **{name: getattr(rr, name) for name in RoundOutcome.model_fields},
            cells=cells,
            panel_cells=panel,
            positions={key: position_of[key] for key in walked},
            run_standing=run_standing,
            panel_precision=None
            if leading is None
            else panel_precision(rr.rows(leading).cells, line[0].results.cells),
            **own,
        )

    def summary(self) -> RoundSummary:
        """This round as the dashboard banks it."""
        arms = self.cells
        longest = arms[max(arms, key=lambda k: (len(arms[k]), k))] if arms else []
        election = self.election

        def name(key: ArmKey) -> ArmName:
            return arm_name(key, self.arms[key], self.round)

        return RoundSummary(
            round=self.round,
            leading=None
            if election is None or election.leading is None
            else name(election.leading),
            ending=None if election is None else election.ending,
            verdict_reason=None if election is None else election.verdict_reason,
            candidates=[
                DashboardCandidate(reading=reading)
                for reading in self.arm_readings()
                if reading.role.is_proposal
            ],
            selection=list(dict.fromkeys(self.positions[key] for key, _, _ in longest)),
            health=RoundHealth.of(self.round, self.health),
            panel_reading=self.origin_panel.reading,
            panel_precision=self.panel_precision,
            optimizer_facts=self.optimizer_facts,
        )

    def walks(self) -> list[IndividualWalk[WalkedCell]]:
        return round_walks(self.cells, self.panel_cells)

    def restated(self, optimizer_state: OptimizerState) -> RoundClosedRecord:
        """This round holding *optimizer_state*: what a member banks once the round has closed."""
        body = {name: getattr(self, name) for name in RoundStatement.model_fields}
        del body["timestamp"]
        return RoundClosedRecord(**body | {"optimizer_state": optimizer_state})


class RoundClosedRecord(RoundStatement, CycleRecord):
    """A round's statement, and the run's clock: a close is its line's latest round, so its
    standing is the run's. A round stated again is a restatement, which announces nothing: it is
    the round WHOLE, so a repair's restatement makes its ledger the owner of every arm in it."""

    record_type: Literal["round_closed"] = "round_closed"


class WalkRead(FrozenModel):
    """An arm a walk read, on the cells it asked in the order its rows came: a replayed walk reads
    it again off the archive, so the round's later reads of that arm continue the walk they did."""

    individual_id: str
    cells: list[str]
    role: ArmRole


class RoundProposedRecord(CycleRecord):
    """Banked by the bench once per round; replayed on a resume unless a digest the round ``consumed`` has moved."""

    record_type: Literal["round_proposed"] = "round_proposed"
    round: int
    consumed: str
    proposals: list[Proposal]
    walked: OptimizerState
    reads: list[WalkRead]
    # The ruler lines the round opened on: a re-entry draws on them, never on a fold since.
    opened_on: RulerReach
    submitted: bool = False
    timestamp: str = Field(default_factory=utcnow_iso)


class ArmRecord(CycleRecord):
    """One arm's transition, LAST wins: proposed, walking, then cut or measured."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    record_type: Literal["arm"] = "arm"
    round: int
    arm: ArmKey
    state: ArmState
    timestamp: str = Field(default_factory=utcnow_iso)


# A halt is its `StopReason`, and an outcome the loop decided is a field of the record of its step.
RoundWarningKind = Literal[
    # An L2/L3 reply that never parsed: its refinement is discarded, the prior framing kept.
    "layer_parse_failure",
    # A state panel over its runaway backstop: the items past it never reach the LLM.
    "injection_budget_overrun",
    # A projection that could not write its derived file: the line landed, the file is stale until its next write.
    "derived_file_stale",
    # A round's adapter that raised: the next walk re-runs it first (`settle_owed_outputs`).
    "adapter_landed_nothing",
    # A stopped walk whose returned cells could not be banked: a resume re-measures them.
    "cells_unbanked",
]


# `error` = the step produced nothing usable; `warning` = degraded but progressed.
RoundWarningSeverity = Literal["warning", "error"]


class RoundWarningRecord(CycleRecord):
    """A SELF-HEAL the run continued past, mid-round or at its start; never re-add a kind with no emitter."""

    record_type: Literal["round_warning"] = "round_warning"
    kind: RoundWarningKind
    severity: RoundWarningSeverity = "warning"
    message: str
    round: int | None = None
    detail: dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=utcnow_iso)

    @classmethod
    def handled(
        cls, kind: RoundWarningKind, fault: BaseException, *, said: str, round: int | None
    ) -> RoundWarningRecord:
        """A fault its step handled, said once as its sentence; never its traceback."""
        sentence, _ = fault_said(fault)
        return cls(
            kind=kind,
            severity="error",
            message=f"{said}: {sentence}",
            round=round,
            detail={"fault": type(fault).__name__},
        )


def stale_file_warning(file: str, offset: int, reason: str) -> RoundWarningRecord:
    return RoundWarningRecord(
        kind="derived_file_stale",
        message=f"{file} could not take line {offset}; it is stale until its next write",
        detail={"file": file, "offset": offset, "reason": reason},
    )


class ElectionRecord(Election, CycleRecord):
    """A round's election, the moment it is held: the close carries the same one."""

    record_type: Literal["election"] = "election"
    round: int
    timestamp: str = Field(default_factory=utcnow_iso)
