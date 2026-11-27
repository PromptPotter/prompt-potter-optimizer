from __future__ import annotations

from typing import Any

from promptpotter.domain.records.run_records import DecisionInputs, DecisionRecord


def decision(
    inputs: DecisionInputs, outcome: Any, *, node: str | None, round: int | None = None
) -> DecisionRecord:
    """What *inputs* decided, filed where its caller keeps it: the cycle's pending decisions, the
    run's ledger, or a parent's own as a fork's cut."""
    return DecisionRecord(kind=inputs.kind, node=node, inputs=inputs, outcome=outcome, round=round)
