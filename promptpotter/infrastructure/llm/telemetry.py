"""The ``emit_*`` seam — per-call telemetry from deep async chains straight to the active cycle
ledger, read from a ContextVar. No process global, no wrapper: call site to ledger in one hop."""

from __future__ import annotations

import logging
from contextvars import ContextVar, Token
from typing import TYPE_CHECKING, Any, Literal

from promptpotter.domain.run_records import (
    CommandAckRecord,
    CommandRecord,
    CycleRecord,
    ErrorRecord,
    RoundWarningKind,
    RoundWarningRecord,
    TokenUsageRecord,
)
from promptpotter.domain.spend import TokenUsageKind

if TYPE_CHECKING:
    from promptpotter.infrastructure.ledger import CycleEventLog

logger = logging.getLogger(__name__)


_CYCLE_LEDGER: ContextVar[CycleEventLog | None] = ContextVar("cycle_ledger", default=None)
_CURRENT_ROUND: ContextVar[int | None] = ContextVar("current_round", default=None)


def set_cycle_ledger(ledger: CycleEventLog | None) -> Token[CycleEventLog | None]:
    """Bind the ledger ``emit_token_usage`` appends to; returns the ``Token`` ``drain_all`` resets."""
    return _CYCLE_LEDGER.set(ledger)


def reset_cycle_ledger(token: Token[CycleEventLog | None]) -> None:
    _CYCLE_LEDGER.reset(token)


def set_current_round(round_num: int | None) -> Token[int | None]:
    return _CURRENT_ROUND.set(round_num)


def reset_current_round(token: Token[int | None]) -> None:
    _CURRENT_ROUND.reset(token)


def _append_record(record: CycleRecord) -> int | None:
    """Append *record* to the active cycle ledger, or ``None``. A missing ledger keeps pure/test paths
    side-effect-free; a raising append is logged and swallowed — telemetry must not break its call site."""
    ledger = _CYCLE_LEDGER.get()
    if ledger is None:
        return None
    try:
        return ledger.append(record)
    except Exception:
        logger.exception("ledger append failed for %s", type(record).__name__)
        return None


def emit_token_usage(
    *,
    node: str,
    kind: TokenUsageKind,
    input_tokens: int,
    output_tokens: int,
    duration_s: float,
    model: str | None = None,
    provider: str | None = None,
    served_by: str | None = None,
    cost_usd: float | None = None,
    cached: bool = False,
    reasoning_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> None:
    """Build ``TokenUsageRecord`` and append it. ``cached`` marks a call served from the content-addressed
    cache: it consumed the recorded tokens but spent no money, and the rollup keeps the two apart."""
    _append_record(
        TokenUsageRecord(
            kind=kind,
            node=node,
            model=model,
            provider=provider,
            served_by=served_by,
            input_tokens=int(input_tokens),
            output_tokens=int(output_tokens),
            reasoning_tokens=int(reasoning_tokens),
            cache_read_tokens=int(cache_read_tokens),
            cache_write_tokens=int(cache_write_tokens),
            duration_s=float(duration_s),
            cost_usd=cost_usd,
            cached=cached,
            round=_CURRENT_ROUND.get(),
        )
    )


def emit_command(
    *,
    command_id: str,
    kind: str,
    payload: dict[str, Any],
    idempotency_key: str,
    issued_by_user_id: str = "",
) -> int | None:
    """Append a ``CommandRecord``; the dispatcher binds the target cycle's ledger around its work."""
    return _append_record(
        CommandRecord(
            command_id=command_id,
            kind=kind,
            payload=dict(payload),
            idempotency_key=idempotency_key,
            issued_by_user_id=issued_by_user_id,
        )
    )


def emit_command_ack(
    *,
    command_id: str,
    status: Literal["applied", "rejected"],
    detail: str = "",
    effect: dict[str, Any] | None = None,
) -> None:
    _append_record(
        CommandAckRecord(command_id=command_id, status=status, detail=detail, effect=effect or {})
    )


def emit_error_record(
    *,
    kind: str,
    message: str,
    stop_reason: Literal["CRASHED", "RENDER_ERROR", "DIVERGED"],
    traceback: str | None = None,
) -> ErrorRecord:
    """Append an ``ErrorRecord`` and RETURN it — the runner's ``except`` sites carry the same object onto
    ``CycleResult.error``, so there is one build and no twin. Pre-loop errors carry ``round=None``."""
    record = ErrorRecord(
        kind=kind,
        message=message,
        traceback=traceback,
        stop_reason=stop_reason,
        round=_CURRENT_ROUND.get(),
    )
    _append_record(record)
    return record


def emit_round_warning(
    *,
    kind: RoundWarningKind,
    message: str,
    severity: Literal["warning", "error"] = "warning",
    detail: dict[str, Any] | None = None,
) -> None:
    """Append a ``RoundWarningRecord``, putting a non-fatal self-healed degradation on every channel
    instead of only the server log."""
    _append_record(
        RoundWarningRecord(
            kind=kind,
            severity=severity,
            message=message,
            round=_CURRENT_ROUND.get(),
            detail=dict(detail or {}),
        )
    )


__all__ = [
    "emit_command",
    "emit_command_ack",
    "emit_error_record",
    "emit_round_warning",
    "emit_token_usage",
    "reset_current_round",
    "reset_cycle_ledger",
    "set_current_round",
    "set_cycle_ledger",
]
