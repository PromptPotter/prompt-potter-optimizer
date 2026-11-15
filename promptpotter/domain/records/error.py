from __future__ import annotations

from typing import Literal

from pydantic import Field

from promptpotter.domain.phases import StopReason
from promptpotter.domain.records import CycleRecord
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.errors import fault_said


class ErrorRecord(CycleRecord):
    record_type: Literal["error"] = "error"
    # Exception class name — operator-facing diagnostic, never load-bearing for routing.
    kind: str
    # The exception's first non-blank line, else its class name; the lines after it are `detail`.
    message: str
    detail: str | None
    traceback: str | None = None
    stop_reason: StopReason
    round: int | None = None
    timestamp: str = Field(default_factory=utcnow_iso)

    @classmethod
    def raised(
        cls,
        exc: BaseException,
        *,
        kind: str,
        stop_reason: StopReason,
        traceback: str | None = None,
        round: int | None = None,
    ) -> ErrorRecord:
        message, detail = fault_said(exc)
        return cls(
            kind=kind,
            message=message,
            detail=detail,
            traceback=traceback,
            stop_reason=stop_reason,
            round=round,
        )
