"""A replayer is PURE over ``ReplayContext``; a kind with none registered is archived and never compared."""

from __future__ import annotations

import functools
import logging
from collections.abc import Callable, Iterator, Mapping
from typing import TYPE_CHECKING, Any, NamedTuple

from promptpotter.application import optimizers
from promptpotter.domain.results import RoundResult
from promptpotter.domain.run_records import ResumeCheckpointRecord

if TYPE_CHECKING:
    from promptpotter.domain.ruler import DeltaRuler

__all__ = [
    "ReplayContext",
    "ReplayMismatch",
    "replay_all_mismatches",
    "replay_decisions",
    "replayers",
]

logger = logging.getLogger(__name__)


class ReplayMismatch(NamedTuple):
    """Evidence, not a departure point: where a branch stops carrying over is a *divergence*."""

    round_num: int
    kind: str
    recorded_outcome: Any
    current_outcome: Any
    inputs_ref: dict[str, Any]


class ReplayContext(NamedTuple):
    """ONE round and no history, so a decision needing the cycle's is ``ARCHIVAL``; the anchor rides the decision."""

    round_data: RoundResult
    decisions: list[ResumeCheckpointRecord]
    ruler: DeltaRuler | None


Replayer = Callable[[ReplayContext, dict[str, Any], dict[str, Any]], Any]


def _iter_mismatches(ctx: ReplayContext) -> Iterator[ReplayMismatch]:
    round_data = ctx.round_data
    for rec in ctx.decisions:
        kind = rec.kind
        fn = replayers().get(kind)
        if fn is None:
            continue

        try:
            current = fn(ctx, rec.inputs_ref, rec.data)
        except Exception as exc:
            # A replayer that cannot ANSWER is a third state, never a match: agreement would pass rounds nothing verified.
            logger.warning(
                "replayer for decision kind %r could not re-derive its record", kind, exc_info=True
            )
            yield ReplayMismatch(
                round_num=round_data.round,
                kind=f"replay_error:{kind}",
                recorded_outcome=rec.outcome,
                current_outcome=f"{type(exc).__name__}: {exc}",
                inputs_ref=dict(rec.inputs_ref),
            )
            continue
        if current != rec.outcome:
            yield ReplayMismatch(
                round_num=round_data.round,
                kind=kind,
                recorded_outcome=rec.outcome,
                current_outcome=current,
                inputs_ref=dict(rec.inputs_ref),
            )


def replay_decisions(
    round_data: RoundResult,
    decisions: list[ResumeCheckpointRecord],
    ruler: DeltaRuler | None = None,
) -> ReplayMismatch | None:
    return next(_iter_mismatches(ReplayContext(round_data, decisions, ruler)), None)


def replay_all_mismatches(
    round_data: RoundResult,
    decisions: list[ResumeCheckpointRecord],
    ruler: DeltaRuler | None = None,
) -> list[ReplayMismatch]:
    return list(_iter_mismatches(ReplayContext(round_data, decisions, ruler)))


@functools.cache
def replayers() -> Mapping[str, Replayer]:
    table: dict[str, Replayer] = {}
    for runtime in optimizers.runtimes().values():
        if clash := sorted(set(table) & set(runtime.replayers)):
            raise RuntimeError(f"{runtime.name} registers replayers another holds: {clash}")
        table.update(runtime.replayers)
    return table
