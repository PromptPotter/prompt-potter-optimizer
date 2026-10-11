"""Whether a resume re-derives a kind is whether a replayer is registered for it (``replayers.py``)."""

from __future__ import annotations

from typing import Any, Protocol

from promptpotter.domain.run_records import ResumeCheckpointRecord

__all__ = ["ResumeCheckpointRecord", "record_decision"]


class _DecisionSink(Protocol):
    def append(self, decision: ResumeCheckpointRecord, /) -> Any: ...


def record_decision(
    sink: _DecisionSink,
    kind: str,
    inputs_ref: dict[str, Any],
    outcome: Any,
    *,
    node: str | None,
    data: dict[str, Any] | None = None,
    round: int | None = None,
) -> Any:
    sink.append(
        ResumeCheckpointRecord(
            kind=kind,
            node=node,
            inputs_ref=dict(inputs_ref),
            outcome=outcome,
            data=dict(data or {}),
            round=round,
        )
    )
    return outcome
