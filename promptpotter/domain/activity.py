from __future__ import annotations

from typing import ClassVar, Literal, NamedTuple

from pydantic import Field

from promptpotter.domain.arms import candidate_label
from promptpotter.domain.command_kinds import CommandKind
from promptpotter.domain.narration import Narrated, NarratedRecord, Narration
from promptpotter.domain.said import Said
from promptpotter.domain.strict_model import FrozenModel, StrictModel


class ActivityState(FrozenModel):
    """A run's current state: what it is doing, and what was said that still holds."""

    status: Said | None = Field(
        description="The one line saying what the run is doing; each new one replaces it."
    )
    notices: list[Said] = Field(
        description="Warnings and controls of the round the run is in, and any error of this "
        "launch."
    )
    decision: ActivityDecision | None = Field(
        description="What the run is holding for the operator to decide; null while it holds "
        "for nothing."
    )


class ActivityAt(NamedTuple):
    """The chat as of a ledger line: what a frame carries, never the feed's history."""

    at_offset: int
    state: ActivityState


class KeepDraftsAction(FrozenModel):
    """One button: a press sends `payload` as `kind` to the cycle holding for its round's arms."""

    label: str
    variant: Literal["primary", "ghost", "danger"]
    kind: Literal[CommandKind.SUBMIT_ARMS]
    payload: dict[str, list[int]] = Field(
        description="What the press sends beside the cycle's address, as served: the drafts it "
        "keeps, by index at the hold."
    )


class ActivityDecision(FrozenModel):
    """A choice the run is holding for: what to say about it, and the commands that answer it."""

    title: str
    lead: str = Field(description="The paragraph under the title.")
    facts: list[Said] = Field(description="The evidence, in reading order.")
    reasons: list[str] = Field(description="Why the run is holding, one fragment each.")
    actions: list[KeepDraftsAction]


def _arms_gate(round_num: int | None, drafts: list[Said]) -> ActivityDecision:
    """One button keeps every draft; an arm of the operator's own is written text."""
    held = "The round" if round_num is None else f"Round {round_num}"
    drafted = (
        "Keep a draft below, add arms of your own, or both."
        if drafts
        else "No proposer ran: every arm this round measures is yours."
    )
    return ActivityDecision(
        title=f"{held} holds for your arms",
        lead=f"{held} measures nothing until you answer it with its arms. {drafted}",
        facts=drafts,
        reasons=[],
        actions=[
            KeepDraftsAction(
                label="Measure the drafts",
                variant="primary",
                kind=CommandKind.SUBMIT_ARMS,
                payload={"keep": list(range(len(drafts)))},
            )
        ]
        if drafts
        else [],
    )


class ActivityFeed:
    """The chat: the narration's latest status row and every other row still alive."""

    records: ClassVar[tuple[type[StrictModel], ...]] = Narration.records

    def __init__(self) -> None:
        self.at_offset = -1
        self.narration = Narration()
        self._status: Narrated | None = None
        self._notices: list[Narrated] = []

    def state(self) -> ActivityState:
        narration, status, gate = self.narration, self._status, self.narration.gate
        drafts = [
            Said(
                key=f"draft_{idx}",
                tone="quiet",
                label=f"draft {idx} · {candidate_label(narration.round or 0, idx)}",
                value=draft.individual.lineage.changes_description or "—",
            )
            for idx, draft in enumerate(() if gate is None else gate.drafts)
        ]
        # A holding gate's row is its decision, so the chat says it once.
        if gate is not None or (status is not None and not narration.lives(status)):
            status = None
        return ActivityState(
            status=None if status is None else status.said,
            notices=[n.said for n in self._notices if narration.lives(n)],
            decision=None if gate is None else _arms_gate(narration.round, drafts),
        )

    def feed(self, record: NarratedRecord) -> None:
        self.read(self.at_offset, record)

    def value(self) -> ActivityAt:
        return ActivityAt(self.at_offset, self.state())

    def read(self, sequence: int, record: NarratedRecord) -> Narrated | None:
        narration = self.narration
        row = narration.read(sequence, record)
        if row is None:
            return None
        if row.lifetime in ("status", "tick"):
            self._status = row
        else:
            self._notices = [*(n for n in self._notices if narration.lives(n)), row]
        return row
