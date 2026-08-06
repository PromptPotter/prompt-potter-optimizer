"""The ``emit_*`` seam — per-call telemetry from deep async chains to the ledger.

``emit_token_usage`` is the template: build the ``*Record`` and append it to the
active cycle ledger read from a ContextVar. The runner installs the ledger via
``set_cycle_ledger`` at cycle start and clears it on ``drain_all``; concurrent
cycles (M12+) get task-local isolation for free. No process global, no wrapper
dataclass — call site to ledger in one hop."""

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

if TYPE_CHECKING:
    from promptpotter.infrastructure.ledger import CycleEventLog

logger = logging.getLogger(__name__)


_CYCLE_LEDGER: ContextVar[CycleEventLog | None] = ContextVar("cycle_ledger", default=None)
_CURRENT_ROUND: ContextVar[int | None] = ContextVar("current_round", default=None)


def set_cycle_ledger(ledger: CycleEventLog | None) -> Token[CycleEventLog | None]:
    """Bind the cycle ledger ``emit_token_usage`` appends to.

    Returns the reset ``Token``; ``drain_all`` pairs it with ``reset_cycle_ledger``."""
    return _CYCLE_LEDGER.set(ledger)


def reset_cycle_ledger(token: Token[CycleEventLog | None]) -> None:
    _CYCLE_LEDGER.reset(token)


def set_current_round(round_num: int | None) -> Token[int | None]:
    """Bind the round number stamped onto each ``TokenUsageRecord``."""
    return _CURRENT_ROUND.set(round_num)


def reset_current_round(token: Token[int | None]) -> None:
    _CURRENT_ROUND.reset(token)


def _append_record(record: CycleRecord) -> int | None:
    """Append *record* to the active cycle ledger; return its offset.

    No-ops to ``None`` when no ledger is bound (pure/test call paths stay
    side-effect-free) or when the append raises (logged, never propagated —
    telemetry must not break the call site)."""
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
    kind: Literal["optimizer", "backend"],
    input_tokens: int,
    output_tokens: int,
    duration_s: float,
    model: str | None = None,
    provider: str | None = None,
    cost_usd: float | None = None,
    cached: bool = False,
    reasoning_tokens: int = 0,
) -> None:
    """Build ``TokenUsageRecord`` and append it to the active cycle ledger.

    Reads ledger + round from ContextVars (per-asyncio-task isolation —
    concurrent cycles for M12+ just work). The overlong-prompt signal is the
    per-node char gate at the pre-call site (``OPTIMIZER_PROMPT_BUDGET_CHARS``),
    which fires before the call on the composed prompt — the duplicate
    post-call token gate that could never fire first is gone.

    ``cached`` marks a call served from a content-addressed cache: it consumed the
    recorded tokens but spent no money. See ``TokenUsageRecord`` for why the ledger
    carries both and the rollup keeps them apart."""
    _append_record(
        TokenUsageRecord(
            kind=kind,
            node=node,
            model=model,
            provider=provider,
            input_tokens=int(input_tokens),
            output_tokens=int(output_tokens),
            reasoning_tokens=int(reasoning_tokens),
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
    """Append a ``CommandRecord`` to the active cycle ledger.

    Reads ledger from the per-task ``_CYCLE_LEDGER`` ContextVar; the
    dispatcher binds the target cycle's ledger via ``set_cycle_ledger``
    around its work. Returns the offset the record was appended at;
    ``None`` when no ledger is bound (informational — callers above
    the dispatcher already raised by then)."""
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
    """Append a ``CommandAckRecord`` to the active cycle ledger.

    Same ContextVar surface as ``emit_command``; the actuator that applied
    (or refused) the command emits this through the same binding."""
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
    """Append an ``ErrorRecord`` to the active cycle ledger and return it.

    Same ContextVar surface as ``emit_token_usage``. The runner's three
    ``except`` sites call this from ``application/runner/{entry,loop}.py``
    and carry the returned record straight onto ``CycleResult.error`` — one
    build, no twin. ``LiveDashboardView`` is the sole subscriber writing
    ``dashboard.json::error``. Errors emitted before the round loop entered
    carry ``round=None``."""
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
    """Append a ``RoundWarningRecord`` to the active cycle ledger.

    Same ContextVar surface as :func:`emit_error_record` — reads the ledger +
    round from ``_CYCLE_LEDGER`` / ``_CURRENT_ROUND``. Makes a non-fatal,
    self-healed degradation visible on every channel (dashboard, round file,
    CLI) instead of only the server log. No-ops when no ledger is bound, so
    pure/test call paths stay side-effect-free."""
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
