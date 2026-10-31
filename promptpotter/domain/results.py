from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import ConfigDict, Field, computed_field

from promptpotter.domain.arm_reading import (
    ArmElection,
    ArmFit,
    ArmReading,
    DisplayMetric,
    ScoreboardRankKey,
    ScoreboardRow,
    panel_counts,
    panel_cuts,
    scoreboard_rank_key,
)
from promptpotter.domain.arms import ArmState, Concluded, arm_name, is_leader_eligible
from promptpotter.domain.bench import BenchScore, OwnLevel
from promptpotter.domain.cell_fold import ScoreSummary
from promptpotter.domain.optimizer_state import OptimizerState
from promptpotter.domain.origin_panel import OverlapReading, line_by_individual
from promptpotter.domain.paired_reading import ArmKey, RoundArm
from promptpotter.domain.phases import ErrorRecord, StopReason
from promptpotter.domain.round_diagnostics import RoundDiagnostics
from promptpotter.domain.round_health import DegradationHealth
from promptpotter.domain.ruler import LevelSeries, RulerStamp
from promptpotter.domain.scoring import (
    NO_CELLS,
    CellSheet,
    GradedCell,
    WalkedCell,
    fitness_bands,
)
from promptpotter.domain.spend import SpendRollup
from promptpotter.domain.strict_model import FrozenModel, StrictModel
from promptpotter.shared.hashing import stable_hash
from promptpotter.shared.measurement_context import (
    SCOPE_ROLES,
    IndividualWalk,
    MeasurementRole,
    RoleScope,
    individual_cells,
)


def round_document_digest(rr: RoundResult) -> str:
    """The WHOLE outcome, never a chosen subset: under-firing goes stale in silence."""
    outcome = rr.model_dump(mode="json", include=set(RoundOutcome.model_fields))
    return stable_hash([outcome, rr.addresses().model_dump(mode="json")])


class OptimizerFact(FrozenModel):
    """One labelled reading an optimizer reports about a round, worded by its own runtime."""

    key: str
    label: str
    text: str
    value: float | None
    # A `stat` reads on one line; a `note` is prose the optimizer carries into its next round.
    kind: Literal["stat", "note"]


class RoundCells(FrozenModel):
    """A :class:`RoundResult`'s rows as archive addresses, in walk order."""

    arms: dict[ArmKey, list[WalkedCell]] = Field(default_factory=dict)
    overlap: dict[str, list[WalkedCell]] = Field(default_factory=dict)

    def walks(self) -> list[IndividualWalk[WalkedCell]]:
        return _round_walks(arms=self.arms, overlap=self.overlap)

    def sample_keys(self) -> set[str]:
        walked = [*self.arms.values(), *self.overlap.values()]
        return {key for cells in walked for key, _, _ in cells}


# The WALK's role, never the answer's `role` stamp: that names who FILED it, and a replay reads it.
_ROW_SET_ROLES: dict[str, frozenset[MeasurementRole]] = {
    "arms": SCOPE_ROLES[RoleScope.DECISION],
    "overlap": frozenset({MeasurementRole.OVERLAP}),
}
assert _ROW_SET_ROLES.keys() == RoundCells.model_fields.keys()


def _round_walks[C: tuple[Any, ...]](
    **by_individual: Mapping[ArmKey, Sequence[C]] | Mapping[str, Sequence[C]],
) -> list[IndividualWalk[C]]:
    """An arm's cells are its individual's: a refused arm walked none, so its slot key holds none."""
    return [
        IndividualWalk(individual_id, _ROW_SET_ROLES[row_set], cells)
        for row_set in ("arms", "overlap")
        for individual_id, cells in by_individual[row_set].items()
    ]


class Election(FrozenModel):
    """What a round's selector decided over its arms, by key; ``selected`` null = the round HELD."""

    # THE pick: round 0 selects the origin it adopted.
    selected: ArmKey | None
    leading: ArmKey | None = Field(
        description="The ONE arm this round is read off, as its selector named it and never "
        "re-ranked: the selection where there is one, on a held round the challenger that came "
        "closest on the selector's own objective, at round 0 the origin. Rank 1 of `scoreboard`. "
        "`null` where the selector could read no arm. NOT whose numbers the headline carries: "
        "that is `ended_on`."
    )
    ended_on: ArmKey = Field(
        description="WHOSE numbers the round's headline carries: the selection where the round "
        "selected; on a held round the retained parent, re-read on this round's cells as its "
        "reference arm — never the leading challenger."
    )
    # Arms that entered the election. Zero says the proposer made no testable variant, not a stall.
    electable: int
    fit: dict[ArmKey, ArmFit] = Field(default_factory=dict)
    # `Selector.elects_on`: the column of `scoreboard` the round was WON on.
    elects_on: DisplayMetric
    # The selector's own reason; ``None`` on the origin round, which chose between nobody.
    verdict_reason: str | None = None
    # The δ scale the election read, whether or not a θ was fit on it.
    ruler: RulerStamp


class RoundOutcome(FrozenModel):
    """``RoundClosedRecord`` carries exactly this: a field declared here reaches the ledger."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    round: int
    # Its panel arms in slot order, then each reference the round re-read: one key, one state.
    arms: dict[ArmKey, ArmState] = Field(default_factory=dict)
    # The candidate its line stood on entering it, which the election reads every arm against
    # (`line_reference`); none at round 0 and on a diag round, which read nothing against one.
    reference: RoundArm | None
    # Each arm's fold of the cells the round files for it, taken once as the round is measured
    # (`MeasuredRound.folds`); none for an arm refused before a cell.
    folds: dict[ArmKey, ScoreSummary]
    # ``None``: a diag round, its variants generated and never scored.
    election: Election | None
    # Not `improved`, the selector's point estimate. `pending` until the overlap pass lands.
    overlap: OverlapReading
    diagnostics: RoundDiagnostics | None = None
    # Graded against the rounds before it, so it is stamped, never re-derived from this one.
    health: DegradationHealth
    # Built once, at the round's close (`Cycle.close_state`); `None` from its election until then.
    optimizer_state: OptimizerState | None = None
    optimizer_facts: list[OptimizerFact] = Field(default_factory=list)

    @property
    def closed_state(self) -> OptimizerState:
        if self.optimizer_state is None:
            raise ValueError(
                f"round {self.round} holds no optimizer state: it has not closed, or it is a "
                "member's view, whose parts open through `NodeContext.recall`"
            )
        return self.optimizer_state

    @property
    def generation_only(self) -> bool:
        return self.election is None

    @property
    def head(self) -> Concluded | None:
        """The arm the round ENDED on: the headline is its numbers."""
        if self.election is None:
            return None
        arm = self.arms[self.election.ended_on]
        assert isinstance(arm, Concluded), "a round ends on an arm it concluded"
        return arm

    @property
    def fold(self) -> ScoreSummary | None:
        """The headline: the fold of the arm the round ENDED on, never a field of its own."""
        return None if self.election is None else self.folds.get(self.election.ended_on)

    @property
    def improved(self) -> bool:
        """A promotion, on a bare point estimate; round 0 adopts its origin and promotes nothing."""
        return self.round > 0 and self.election is not None and self.election.selected is not None

    @property
    def ruler(self) -> RulerStamp | None:
        return None if self.election is None else self.election.ruler

    @property
    def panel(self) -> dict[ArmKey, ArmState]:
        """The arms the round PROPOSED, in slot order: no reference it re-read."""
        return {key: arm for key, arm in self.arms.items() if arm.role != MeasurementRole.PARENT}

    @property
    def candidates_scored(self) -> int:
        return sum(1 for arm in self.panel.values() if is_leader_eligible(arm))

    @property
    def origin(self) -> Concluded:
        head = self.head
        assert self.round == 0 and head is not None, "round 0 ends on the origin"
        return head

    def fit(self, key: ArmKey) -> ArmFit | None:
        return None if self.election is None else self.election.fit.get(key)

    def arm_election(self, key: ArmKey, *, held: bool = True) -> ArmElection:
        election = self.election
        return ArmElection.of(
            key,
            held=held and election is not None,
            selected=None if election is None else election.selected,
            leading=None if election is None else election.leading,
            electable=None if election is None else election.electable,
        )

    def arm_readings(self) -> list[ArmReading]:
        counts = {key: panel_counts(arm, self.folds.get(key)) for key, arm in self.arms.items()}
        panel = list(self.panel)
        cuts = dict(zip(panel, panel_cuts([counts[key] for key in panel]), strict=True))
        # A reference reads the cells its readers asked, so no panel arm's fuller walk cuts it.
        cuts |= {key: panel_cuts([counts[key]])[0] for key in self.arms if key not in cuts}
        line = line_by_individual(self.overlap)
        return [
            ArmReading.of(
                arm_name(key, arm, self.round),
                arm,
                fold=self.folds.get(key),
                cut=cuts[key],
                election=self.arm_election(key),
                fit=self.fit(key),
            ).on_line(line)
            for key, arm in self.arms.items()
        ]

    def ranked(self) -> list[ScoreboardRow]:
        """THE rank: the leading arm first, then θ where the round was won on it, then composite."""
        leading = None if self.election is None else self.election.leading
        on_theta = self.election is not None and self.election.elects_on == "ability"

        def rank(reading: ArmReading) -> ScoreboardRankKey:
            own = reading.own
            ability = reading.ability
            return scoreboard_rank_key(
                None if own is None or own.composite is None else own.composite.value,
                None if own is None or own.accuracy is None else own.accuracy.value,
                ability.theta if on_theta and ability is not None else None,
                is_leading=reading.arm.key == leading,
                is_partial=reading.end == "skipped",
            )

        ranked = sorted(self.arm_readings(), key=rank, reverse=True)
        return [ScoreboardRow(rank=i, reading=r) for i, r in enumerate(ranked, start=1)]


class RoundResult(RoundOutcome):
    """A round with its rows, which is the round file itself."""

    # `extra="ignore"`: the computed `round_id`/`scoreboard` are dumped and must read back.
    model_config = ConfigDict(extra="ignore")

    # Offset of the statement it stands on. ``None`` where no ledger was bound: 0 is a real offset.
    at_offset: int | None = None
    # By arm: a reference re-read on the round's cells is an arm, so a held round's head is here.
    cells: dict[ArmKey, CellSheet] = Field(default_factory=dict)
    # Keyed by the MEASURED individual, outside the arms: the election pools them (`election_rows`).
    overlap_results: dict[str, CellSheet] = Field(default_factory=dict)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def round_id(self) -> str:
        return f"round_{self.round}"

    @property
    def results(self) -> CellSheet:
        """The rows of the arm the round ENDED on: the headline's own."""
        return NO_CELLS if self.election is None else self.rows(self.election.ended_on)

    def rows(self, key: ArmKey) -> CellSheet:
        return self.cells.get(key, NO_CELLS)

    def reference_rows(self, key: ArmKey) -> CellSheet:
        """The rows of the arm *key* was judged against, re-read on this round's cells."""
        fit = self.fit(key)
        against = None if fit is None or fit.vs_reference is None else fit.vs_reference.a
        return NO_CELLS if against is None else self.rows(ArmKey(against.address.individual_id))

    def addresses(self) -> RoundCells:
        return RoundCells(
            arms={k: v.addresses() for k, v in self.cells.items()},
            overlap={k: v.addresses() for k, v in self.overlap_results.items()},
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def scoreboard(self) -> list[ScoreboardRow]:
        """`ranked`, served: the ledger's `RoundOutcome` cannot carry a computed field."""
        return self.ranked()

    @computed_field(  # type: ignore[prop-decorator]
        description="The arm the round ENDED on (`election.ended_on`), read: the headline's "
        "numbers are its own. Null on a diag round, which elects nothing."
    )
    @property
    def headline(self) -> ArmReading | None:
        ended_on = None if self.election is None else self.election.ended_on
        return next((r for r in self.arm_readings() if r.arm.key == ended_on), None)

    @computed_field(  # type: ignore[prop-decorator]
        description="How many scored rows of the `headline` arm's cells graded into each tenth of "
        "[0, 1], lowest first: a band is closed above, so a 0.5 counts in the fifth and a 1.0 in "
        "the tenth. A row carrying no verdict is in none — an uncharged error's 0.0 is a floor, "
        "not a low score."
    )
    @property
    def fitness_bands(self) -> list[int]:
        return fitness_bands(self.results)

    def sheet_of(self, individual_id: str) -> CellSheet:
        return InRunCells([self]).sheet(individual_id, RoleScope.DECISION)


class InRunCells:
    def __init__(self, rounds: Sequence[RoundResult]) -> None:
        self._walks = [
            walk
            for rr in rounds
            for walk in _round_walks(
                arms={k: _by_sample(v) for k, v in rr.cells.items()},
                overlap={k: _by_sample(v) for k, v in rr.overlap_results.items()},
            )
        ]

    def sheet(self, individual_id: str, scope: RoleScope) -> CellSheet:
        folded = individual_cells(self._walks, individual_id, scope)
        if not folded:
            return NO_CELLS
        return CellSheet(folded[0][2], tuple(cell for _, cell, _ in folded)).standing()


def _by_sample(sheet: CellSheet) -> list[tuple[str, GradedCell, str]]:
    return [(cell.key, cell, sheet.scorer_id) for cell in sheet]


class CycleResult(StrictModel):
    rounds: list[RoundResult]
    # The LINE's, origin-exclusive: every round its cycles ran, a rebase's discarded ones included.
    n_rounds_after_origin: int
    result_accuracy: float | None
    result_round: int
    # On its OWN round-0 rows. `None`, not a 0.0 level, on a cycle that never started.
    origin: OwnLevel | None
    # The individual each round ended on, never the proposals; `None` on a cycle that never started.
    level_series: LevelSeries | None
    # The held-out bench: the origin's headline on its rows, then per round the LINE spent, in the
    # order spent, the STANDING selection's on the same rows, held forward across rounds that held.
    # ``None`` in the series is a round whose selection pass read nothing or was never sent;
    # ``None`` origin, no bench pass read.
    bench_origin_level: float | None = None
    bench_round_levels: list[float | None] = Field(default_factory=list)
    # The line's, from config, never the rounds that ran: `lives` stops a stalling cycle short.
    # ``None`` = no cap.
    round_budget: int | None = None
    result_prompt_fields: dict[str, dict[str, str]]
    result_pipeline_params: dict[str, Any] | None = None
    stop_reason: StopReason
    started_at: str
    finished_at: str
    langfuse_trace_id: str | None = None
    cycle_id: str | None = None
    resumed_from_round: int = 1
    # ``None`` only on an init-crash before any observer wired up.
    spend: SpendRollup | None = None
    # Set where the stop's ``STOP_REASON_INFO`` row is FAILED (``runner/termination.py::end_run_on``).
    error: ErrorRecord | None = None
    bench: BenchScore
