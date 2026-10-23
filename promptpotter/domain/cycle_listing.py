from __future__ import annotations

from typing import Literal

from pydantic import Field

from promptpotter.domain.campaign import LIFECYCLE_STATUS_LABELS, LifecycleStatus
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.phases import (
    STOP_REASON_INFO,
    PauseReading,
    RunAdmission,
    RunPhase,
    StopReason,
    run_phase_label,
)
from promptpotter.domain.results import RunStanding, closed_after_origin
from promptpotter.domain.run_records import (
    MINT_KIND_FOR_TRIGGER,
    CycleFinal,
    ForkSpec,
    MintKind,
    SpawnedBy,
    sibling_cycle_id,
)
from promptpotter.domain.strict_model import FrozenModel, StrictModel


class IndexRound(FrozenModel):
    """The level is ``None`` where the round's measurand is not an accuracy: an L4 outer round."""

    round: int
    accuracy: float | None


class Intervention(FrozenModel):
    kind: str
    at: str


class CycleIndex(FrozenModel):
    """Folded off the ledger; ``index.json`` is this written out for a human, and no code reads it."""

    cycle_id: str
    created_at: str
    updated_at: str
    # The mint record's own address (`CycleMintedRecord.parent`).
    parent: str | None = None
    forked_at_offset: int | None = None
    fork: ForkSpec | None = None
    spawned_by: SpawnedBy | None = None
    scorer_cell_formula: str | None = None
    interventions: list[Intervention] = Field(default_factory=list)
    # ``final`` is banked only by the cycle's own runner: a reaped or superseded cycle ends with none.
    stop_reason: StopReason | None = None
    finished_at: str | None = None
    interrupted_round: int | None = None
    crash_traceback: str | None = None
    final: CycleFinal | None = None
    superseded_by: str | None = None
    rounds: list[IndexRound] = Field(default_factory=list)
    standing: RunStanding | None = None

    @property
    def parent_cycle_id(self) -> str | None:
        """The parent where it is a cycle of this campaign; a cut from another tree reads as a root."""
        return sibling_cycle_id(self.parent)

    @property
    def human_intervened(self) -> bool:
        return bool(self.interventions)

    @property
    def rounds_closed(self) -> int:
        """Rounds closed AFTER the origin — the unit a rounds cap counts."""
        return closed_after_origin([r.round for r in self.rounds])

    @property
    def mint_kind(self) -> MintKind:
        """``session`` for a root run, else the badge the cut's trigger declares."""
        return "session" if self.fork is None else MINT_KIND_FOR_TRIGGER[self.fork.trigger]


type StatusMark = Literal[
    "archived",
    "running",
    "starting",
    "queued",
    "gate",
    "paused",
    "detached",
    "success",
    "halted",
    "failed",
    "unknown",
]


class RunStatus(FrozenModel):
    """How a run reads on a row: one word and the mark drawn beside it."""

    label: str
    mark: StatusMark

    @classmethod
    def of(
        cls,
        run_phase: RunPhase,
        stop_reason: StopReason | None,
        *,
        lifecycle: LifecycleStatus = "active",
    ) -> RunStatus:
        if lifecycle == "archived":
            return cls(label=LIFECYCLE_STATUS_LABELS[lifecycle], mark="archived")
        mark: str = "unknown"
        if run_phase is not RunPhase.TERMINAL:
            mark = run_phase.value
        elif stop_reason is not None:
            mark = STOP_REASON_INFO[stop_reason].outcome.value
        return cls.model_validate({"label": run_phase_label(run_phase, stop_reason), "mark": mark})


class CycleListEntry(StrictModel):
    campaign_id: str = Field(description="Campaign the cycle belongs to")
    cycle_id: str
    parent_cycle_id: str | None = Field(
        default=None,
        description="Immediate parent for siblings (forks/diag); null for roots. Sidebar uses this to nest siblings.",
    )
    dataset_name: str = ""
    # The raw separator is NOT served beside it: the browser parses the id itself (`lib/ids.ts`).
    mint_kind: MintKind
    is_root: bool
    stop_reason: StopReason | None = Field(
        default=None,
        description="Why the cycle ended; null while it has not. Label, outcome and next step "
        "derive from the one STOP_REASON_INFO table — never re-mapped per surface.",
    )
    run_phase: RunPhase = Field(
        default=RunPhase.DETACHED,
        description="The single run-state value (RunPhase). Computed once by derive_run_state from lifecycle + control flags + freshness; every picker dot and badge reads this, none re-derive it. 'terminal' pairs with `stop_reason` for the reason label.",
    )
    producer_attached: bool = Field(
        description="A process holds this cycle (`ProducerReading.attached`, derived with `run_phase`): running, holding for the operator, or running out a pause. What the dock counts as in flight.",
    )
    status: RunStatus = Field(
        description="How the cycle reads on a row: `run_phase` and `stop_reason` as one word and "
        "one mark, so no surface words a phase itself.",
    )
    run_admission: RunAdmission = Field(
        description="Which run verbs this cycle admits now, derived with `run_phase` — what the "
        "dispatcher refuses on, so a listing never offers a verb the cycle would decline.",
    )
    pause: PauseReading | None = Field(
        default=None,
        description="Why the cycle reads `paused` — what caused it, who asked and what the "
        "operator does next (`RunState.pause`); null in every other phase.",
    )
    rounds_closed: int = Field(
        default=0,
        description="Rounds this cycle has closed AFTER the origin — the unit a rounds cap counts.",
    )
    updated_at: str = ""
    human_intervened: bool = Field(
        default=False,
        description="True once an operator manually intervened (e.g. skip-searchpoint); the cycle is babysat and no longer purely reproducible. Drives the 'babysat' badge; orthogonal to run_phase.",
    )


class LineStanding(FrozenModel):
    """A campaign's line as one value: the cycle that answers for it now, and how that cycle stands."""

    holder: CycleHop = Field(description="The cycle answering for the campaign.")
    status: RunStatus = Field(
        description="The campaign row's word and mark: `Archived` where the campaign is, else "
        "the holder's run state as `run_phase_label` words it."
    )
    run_phase: RunPhase = Field(description="The holder's derived run state, as `/cycles` serves.")
    producer_attached: bool
    stop_reason: StopReason | None = Field(
        description="Why the holder ended; null while it has not."
    )
    standing: RunStanding | None = Field(
        description="Where the holder's run stands; null until its round 0 closes."
    )
    rounds_closed: int = Field(description="Rounds the holder closed AFTER the origin.")
    budget_rounds: int | None = Field(
        description="The `budget.rounds` binding the holder: what its last launch declared with "
        "the standing operator budget laid over, as its dashboard's `run_limits` reads; before "
        "any launch, its campaign's. 0 is origin only; null is no rounds axis."
    )
    rounds_line: str = Field(
        description="`rounds_closed` against `budget_rounds` as a campaign row prints it "
        "(`rounds_line`)."
    )
    rounds_cap_note: str | None = Field(
        description="What `budget_rounds` says beside a rounds count (`rounds_cap_note`); null "
        "where no rounds axis binds."
    )
    human_intervened: bool


def rounds_line(rounds_closed: int, budget_rounds: int | None) -> str:
    """Capped at zero it measured its origin and stopped; `R0` would read as a run that went nowhere."""
    if budget_rounds == 0:
        return "origin"
    return f"R{rounds_closed}" if budget_rounds is None else f"R{rounds_closed}/{budget_rounds}"


def rounds_cap_note(budget_rounds: int | None) -> str | None:
    if budget_rounds is None:
        return None
    return "origin only" if budget_rounds == 0 else f"of {budget_rounds} — rounds cap"
