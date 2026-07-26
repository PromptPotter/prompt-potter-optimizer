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

from pydantic import Field

from promptpotter.domain.run_records import (
    CommandAckRecord,
    CommandRecord,
    CycleRecord,
    ErrorRecord,
    RoundWarningKind,
    RoundWarningRecord,
    TokenUsageRecord,
)
from promptpotter.domain.strict_model import StrictModel

if TYPE_CHECKING:
    from promptpotter.infrastructure.ledger import CycleEventLog

logger = logging.getLogger(__name__)


class LLMResponse(StrictModel):
    """Standardized response from LLM providers.

    ``reasoning`` is a **core, permanent** member of this shape — see its field note.
    Do not "clean it up" because no gate reads it; that is the point of it.
    """

    content: str = Field(..., description="Response content")
    reasoning: str = Field(
        "",
        description=(
            # ─────────────────────────────────────────────────────────────────────
            # DO NOT DELETE THIS FIELD. It has no code reader ON PURPOSE, and a
            # dead-code audit WILL flag it. It has been flagged before. Read this
            # first.
            #
            # WHAT: the model's own thinking channel — `message.reasoning` on the
            # OpenAI-compat wire, captured verbatim. Empty for non-reasoning models.
            #
            # WHY IT IS LOAD-BEARING, and it is not observability: a model needs
            # somewhere to put its internal process. Give it a bare classification
            # task with no slot to think in, and it emits the label with no
            # reasoning behind it — measurably worse answers. The place to think
            # is part of the ASK, so capturing what lands there is part of the
            # contract. Deleting this does not remove a field; it removes our
            # record of the one thing that explains an answer.
            #
            # STRICTLY ANALYTICAL — and that is a hard invariant, not an accident.
            # It reaches the ledger, the audit twin's `nodes[*].output.reasoning`,
            # and the operator's node-detail pane. It MUST NEVER reach a gate, a
            # metric, a validator, a scorer, an escalation signal, or a cache key.
            # Scoring the model's narration of its work instead of its work is how
            # a loop learns to narrate. If you find yourself wiring this into a
            # decision, that is the bug.
            #
            # NOT the same thing as: `reasoning_chars` (the truncation diagnostic
            # in `openai_compat`, which counts and discards), the backend's
            # per-sample `reasoning_trace` (the TARGET model's thinking, carried
            # over the wire), or a node's `output_schema` `reasoning` slot (the
            # structured-output version of this same principle — see
            # `docs/concepts/structured-output.md`).
            # ─────────────────────────────────────────────────────────────────────
            "The model's own thinking channel (``message.reasoning`` on the "
            "OpenAI-compat wire); empty for non-reasoning models. A CORE field: a "
            "model with nowhere to put its internal process answers without one, so "
            "the slot is part of the ask and capturing it is part of the contract. "
            "Strictly ANALYTICAL — it rides the ledger to the audit trail and the "
            "operator's node detail, and must NEVER feed a gate, metric, validator, "
            "scorer, or cache key. Having no code reader is by design; do not delete "
            "it as dead surface."
        ),
    )
    model: str = Field(..., description="Model used")
    usage: dict[str, int] = Field(
        default_factory=dict,
        description="Token usage: prompt_tokens, completion_tokens, total_tokens",
    )
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
    cost_usd: float | None = None,
    cached: bool = False,
) -> None:
    """Build ``TokenUsageRecord`` and append it to the active cycle ledger.

    Reads ledger + round from ContextVars (per-asyncio-task isolation —
    concurrent cycles for M12+ just work). The overlong-prompt signal is the
    char gate at the pre-call site (``OPTIMIZER_PROMPT_WARN_CHARS``), which
    fires before the call on the composed prompt — the duplicate post-call
    token gate that could never fire first is gone.

    ``cached`` marks a call served from a content-addressed cache: it consumed the
    recorded tokens but spent no money. See ``TokenUsageRecord`` for why the ledger
    carries both and the rollup keeps them apart."""
    _append_record(
        TokenUsageRecord(
            kind=kind,
            node=node,
            model=model,
            input_tokens=int(input_tokens),
            output_tokens=int(output_tokens),
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
    "LLMResponse",
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
