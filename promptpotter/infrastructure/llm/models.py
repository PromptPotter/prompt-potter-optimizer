"""Data types crossing the LLM client boundary.

- ``LLMResponse`` — standardized response every client returns.
- ``emit_token_usage`` — single emission path. Builds ``TokenUsageRecord`` and
  appends it to the active cycle ledger read from a ContextVar. The runner
  installs the ledger via ``set_cycle_ledger`` at cycle start and clears it
  on ``drain_all``; concurrent cycles (M12+) get task-local isolation for
  free. No process global, no wrapper dataclass — call site to ledger in
  one hop."""

from __future__ import annotations

import logging
from contextvars import ContextVar, Token
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field

from promptpotter.config.settings import settings
from promptpotter.domain.run_records import CommandAckRecord, CommandRecord, TokenUsageRecord

if TYPE_CHECKING:
    from promptpotter.infrastructure.ledger import CycleEventLog

logger = logging.getLogger(__name__)


class LLMResponse(BaseModel):
    """Standardized response from LLM providers."""

    content: str = Field(..., description="Response content")
    model: str = Field(..., description="Model used")
    usage: dict[str, int] = Field(
        default_factory=dict,
        description="Token usage: prompt_tokens, completion_tokens, total_tokens",
    )
    finish_reason: str | None = Field(None, description="Why generation stopped")
    parsed: Any | None = Field(
        None,
        description=(
            "Parsed response. Typed Pydantic instance when ``chat`` was "
            "called with ``response_model``; plain dict when only "
            "``response_schema`` was supplied; ``None`` for text-mode."
        ),
    )
    schema_repair_attempts: int = Field(
        0,
        description=(
            "Times the schema-validation repair-retry path fired before "
            "the parsed response landed. 0 = clean first parse; 1 = one "
            "repair round-trip. Surfaces L1-prompt parse-failure rate as a "
            "quality signal — bad templates produce schema-noncompliant "
            "JSON and silently double-up the LLM spend."
        ),
    )


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


def emit_token_usage(
    *,
    node: str,
    kind: Literal["optimizer", "backend"],
    input_tokens: int,
    output_tokens: int,
    duration_s: float,
    model: str | None = None,
    cost_usd: float | None = None,
) -> None:
    """Build ``TokenUsageRecord`` and append it to the active cycle ledger.

    Reads ledger + round from ContextVars (per-asyncio-task isolation —
    concurrent cycles for M12+ just work). Overlong-prompt warning fires
    on the optimizer kind only, regardless of ledger presence (kept here
    because it's a real signal about meta-prompt drift)."""
    if kind == "optimizer":
        threshold = settings.OPTIMIZER_PROMPT_WARN_TOKENS
        if input_tokens > threshold:
            logger.warning(
                "optimizer node %r prompt at %d tokens (threshold=%d) — tune the "
                "template or drop context; large meta-prompts reduce signal-to-noise "
                "for the optimizer LLM and risk provider TPM caps",
                node,
                input_tokens,
                threshold,
            )
    ledger = _CYCLE_LEDGER.get()
    if ledger is None:
        return
    record = TokenUsageRecord(
        kind=kind,
        node=node,
        model=model,
        input_tokens=int(input_tokens),
        output_tokens=int(output_tokens),
        duration_s=float(duration_s),
        cost_usd=cost_usd,
        round=_CURRENT_ROUND.get(),
    )
    try:
        ledger.append(record)
    except Exception:
        logger.exception("emit_token_usage append failed")


def emit_command(
    *,
    command_id: str,
    kind: str,
    payload: dict[str, Any],
    idempotency_key: str,
    expected_version: int | None = None,
    issued_by_user_id: str = "",
    client_metadata: dict[str, Any] | None = None,
) -> int | None:
    """Append a ``CommandRecord`` to the active cycle ledger.

    Reads ledger from the per-task ``_CYCLE_LEDGER`` ContextVar; the
    dispatcher binds the target cycle's ledger via ``set_cycle_ledger``
    around its work. Returns the offset the record was appended at;
    ``None`` when no ledger is bound (informational — callers above
    the dispatcher already raised by then)."""
    ledger = _CYCLE_LEDGER.get()
    if ledger is None:
        return None
    record = CommandRecord(
        command_id=command_id,
        kind=kind,
        payload=dict(payload),
        idempotency_key=idempotency_key,
        expected_version=expected_version,
        issued_by_user_id=issued_by_user_id,
        client_metadata=dict(client_metadata or {}),
    )
    try:
        return ledger.append(record)
    except Exception:
        logger.exception("emit_command append failed")
        return None


def emit_command_ack(
    *,
    command_id: str,
    status: Literal["applied", "rejected"],
    detail: str = "",
) -> None:
    """Append a ``CommandAckRecord`` to the active cycle ledger.

    Same ContextVar surface as ``emit_command``; the actuator that applied
    (or refused) the command emits this through the same binding."""
    ledger = _CYCLE_LEDGER.get()
    if ledger is None:
        return
    record = CommandAckRecord(command_id=command_id, status=status, detail=detail)
    try:
        ledger.append(record)
    except Exception:
        logger.exception("emit_command_ack append failed")


__all__ = [
    "LLMResponse",
    "emit_command",
    "emit_command_ack",
    "emit_token_usage",
    "reset_current_round",
    "reset_cycle_ledger",
    "set_current_round",
    "set_cycle_ledger",
]
