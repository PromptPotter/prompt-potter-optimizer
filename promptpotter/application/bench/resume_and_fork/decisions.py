from __future__ import annotations

from typing import Any

from promptpotter.domain.records.run_records import CheckpointInputs, ResumeCheckpointRecord


def decision(
    inputs: CheckpointInputs, outcome: Any, *, node: str | None, round: int | None = None
) -> ResumeCheckpointRecord:
    """What *inputs* decided, filed where its caller keeps it: the cycle's pending decisions, the
    run's ledger, or a parent's own as a fork's cut."""
    return ResumeCheckpointRecord(
        kind=inputs.kind, node=node, inputs_ref=inputs, outcome=outcome, round=round
    )
