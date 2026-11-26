"""A replay is PURE over ``ReplayContext``; a kind whose inputs are no ``Replayed`` is archived and never compared."""

from __future__ import annotations

import abc
from collections.abc import Iterator
from typing import Any, NamedTuple

from promptpotter.domain.records.run_records import CheckpointInputs, ResumeCheckpointRecord
from promptpotter.domain.results import RoundResult
from promptpotter.domain.ruler import DeltaRuler


class ReplayMismatch(NamedTuple):
    """Evidence, not a departure point: where a branch stops carrying over is a *divergence*."""

    round_num: int
    kind: str
    recorded_outcome: Any
    current_outcome: Any
    inputs_ref: dict[str, Any]
    # `Replayed.stands_on` of `current_outcome`: the one-step counterfactual's arm.
    stands_on: str | None


class ReplayContext(NamedTuple):
    """ONE round and no history, so a decision needing the cycle's is archived; the anchor rides the decision."""

    round_data: RoundResult
    decisions: list[ResumeCheckpointRecord]
    ruler: DeltaRuler | None


class Replayed(CheckpointInputs, kind=None):
    """A kind a resume re-derives off the round's rows, by the rule its live call ran."""

    @abc.abstractmethod
    def replay(self, ctx: ReplayContext) -> Any: ...

    def stands_on(self, outcome: object) -> str | None:  # noqa: ARG002 -- a kind deciding an arm reads it
        """The arm the round ends on where *outcome* decides it; a kind deciding no such arm
        names none."""
        return None


def _iter_mismatches(ctx: ReplayContext) -> Iterator[ReplayMismatch]:
    for rec in ctx.decisions:
        if not isinstance(rec.inputs_ref, Replayed):
            continue
        current = rec.inputs_ref.replay(ctx)
        if current != rec.outcome:
            yield ReplayMismatch(
                round_num=ctx.round_data.round,
                kind=rec.kind,
                recorded_outcome=rec.outcome,
                current_outcome=current,
                inputs_ref=rec.inputs_ref.model_dump(mode="json"),
                stands_on=rec.inputs_ref.stands_on(current),
            )


def replay_all_mismatches(
    round_data: RoundResult,
    decisions: list[ResumeCheckpointRecord],
    ruler: DeltaRuler | None = None,
) -> list[ReplayMismatch]:
    return list(_iter_mismatches(ReplayContext(round_data, decisions, ruler)))
