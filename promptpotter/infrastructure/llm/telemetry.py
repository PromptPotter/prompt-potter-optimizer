from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import TYPE_CHECKING, Any, Literal

from promptpotter.domain.phases import StopReason
from promptpotter.domain.results import ScoreSummary
from promptpotter.domain.run_records import (
    BackendWarningRecord,
    CommandAckRecord,
    CommandAckStatus,
    CommandRecord,
    CycleRecord,
    ErrorRecord,
    PricedKeyRecord,
    RoundWarningKind,
    RoundWarningRecord,
    SampleScoredRecord,
    SampleStartedRecord,
    SpendHoldRecord,
    TokenUsageRecord,
)
from promptpotter.domain.scoring import GradedCell
from promptpotter.domain.spend import TokenAccount, TokenUsageKind
from promptpotter.infrastructure.llm.pricing import compute_usd
from promptpotter.shared.measurement_context import MeasurementRole, measured_candidate

if TYPE_CHECKING:
    from promptpotter.infrastructure.ledger import CycleEventLog

logger = logging.getLogger(__name__)


_CYCLE_LEDGER: ContextVar[CycleEventLog | None] = ContextVar("cycle_ledger", default=None)
_CURRENT_ROUND: ContextVar[int | None] = ContextVar("current_round", default=None)


def set_cycle_ledger(ledger: CycleEventLog | None) -> Token[CycleEventLog | None]:
    return _CYCLE_LEDGER.set(ledger)


def reset_cycle_ledger(token: Token[CycleEventLog | None]) -> None:
    _CYCLE_LEDGER.reset(token)


def active_cycle_ledger() -> CycleEventLog | None:
    """Ask before OPENING one: a second handle is a second appender, and ``append`` is not atomic."""
    return _CYCLE_LEDGER.get()


_FILED_AS: ContextVar[TokenUsageKind | None] = ContextVar("filed_as", default=None)


@contextmanager
def filed_as(kind: TokenUsageKind | None) -> Iterator[None]:
    if kind is None:
        yield
        return
    token = _FILED_AS.set(kind)
    try:
        yield
    finally:
        _FILED_AS.reset(token)


def filed_kind(kind: TokenUsageKind) -> TokenUsageKind:
    return _FILED_AS.get() or kind


def set_current_round(round_num: int | None) -> Token[int | None]:
    return _CURRENT_ROUND.set(round_num)


def reset_current_round(token: Token[int | None]) -> None:
    _CURRENT_ROUND.reset(token)


def _append_record(record: CycleRecord, ledger: CycleEventLog | None = None) -> int | None:
    if ledger is None:
        ledger = _CYCLE_LEDGER.get()
    if ledger is None:
        return None
    try:
        return ledger.append(record)
    except Exception:
        # Telemetry must not break its call site.
        logger.exception("ledger append failed for %s", type(record).__name__)
        return None


def rate_priced_usd(
    usage: TokenAccount,
    *,
    model: str | None,
    provider: str | None,
    cost_usd: float | None,
    recorded: float | None = None,
) -> float | None:
    if cost_usd is not None:
        return None
    if recorded is not None:
        return recorded
    return compute_usd(
        model,
        usage.input,
        usage.output,
        provider=provider,
        cache_read_tokens=usage.cache_read or 0,
        cache_write_tokens=usage.cache_write,
    )


def emit_token_usage(
    *,
    node: str,
    kind: TokenUsageKind,
    usage: TokenAccount,
    duration_s: float,
    model: str | None = None,
    provider: str | None = None,
    served_by: str | None = None,
    cost_usd: float | None = None,
    recorded_rate_usd: float | None = None,
    cached: bool = False,
    hold_id: str | None = None,
    mirrored: bool = False,
) -> TokenUsageRecord | None:
    record = TokenUsageRecord(
        kind=filed_kind(kind),
        node=node,
        model=model,
        provider=provider,
        served_by=served_by,
        # Flat on purpose: every lifetime-spend read sums these counts off raw JSON.
        input_tokens=usage.input,
        output_tokens=usage.output,
        reasoning_tokens=usage.reasoning,
        cache_read_tokens=usage.cache_read or 0,
        cache_write_tokens=usage.cache_write,
        duration_s=float(duration_s),
        cost_usd=cost_usd,
        # Priced once, here: readers sum the stamp, so a rate refresh rewrites nothing.
        rate_priced_usd=rate_priced_usd(
            usage, model=model, provider=provider, cost_usd=cost_usd, recorded=recorded_rate_usd
        ),
        hold_id=hold_id,
        mirrored=mirrored,
        cached=cached,
        round=_CURRENT_ROUND.get(),
        role=cand.role if (cand := measured_candidate()) else None,
    )
    return record if _append_record(record) is not None else None


def emit_backend_warning(
    *,
    kind: str,
    attempt: int,
    max_attempts: int,
    wait_s: float,
    query: str,
    detail: str = "",
    error_class: str | None = None,
    status_code: int | None = None,
    final: bool = False,
) -> None:
    _append_record(
        BackendWarningRecord(
            kind=kind,
            attempt=attempt,
            max_attempts=max_attempts,
            wait_s=float(wait_s),
            error_class=error_class,
            status_code=status_code,
            final=final,
            detail=detail[:400],
            query=query[:80],
        )
    )


def emit_priced_key(priced_key: str) -> None:
    _append_record(PricedKeyRecord(priced_key=priced_key))


_PRICED: ContextVar[set[str] | None] = ContextVar("priced", default=None)


def bind_priced(priced: set[str]) -> None:
    _PRICED.set(priced)


def call_priced(key: str) -> bool:
    priced = _PRICED.get()
    if priced is None:
        return False
    name = f"call:{key}"
    if name in priced:
        return True
    priced.add(name)
    emit_priced_key(name)
    return False


def emit_spend_hold(
    *,
    hold_id: str,
    node: str,
    kind: TokenUsageKind,
    input_tokens: int,
    output_tokens: int | None,
    cost_usd: float | None,
    model: str | None,
    provider: str | None,
    ledger: CycleEventLog | None,
) -> None:
    """``kind`` arrives already filed (``spend_book.py::filed``)."""
    _append_record(
        SpendHoldRecord(
            hold_id=hold_id,
            kind=kind,
            node=node,
            model=model,
            provider=provider,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
            round=_CURRENT_ROUND.get(),
        ),
        ledger,
    )


def emit_command(
    *,
    command_id: str,
    kind: str,
    payload: dict[str, Any],
    idempotency_key: str,
    issued_by_user_id: str = "",
) -> int | None:
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
    status: CommandAckStatus,
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
    stop_reason: StopReason,
    traceback: str | None = None,
) -> ErrorRecord:
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
    _append_record(
        RoundWarningRecord(
            kind=kind,
            severity=severity,
            message=message,
            round=_CURRENT_ROUND.get(),
            detail=dict(detail or {}),
        )
    )


# Capped at the writer, never sliced off a full query by a reader.
QUERY_PREVIEW_CHARS = 120


def emit_sample_started(
    *,
    candidate_idx: int,
    candidate_total: int,
    sample_idx: int,
    sample_total: int,
    sample_id: int,
    query: str,
    sample_lookahead: int,
    stop_horizon: int | None,
) -> None:
    _append_record(
        SampleStartedRecord(
            round=_CURRENT_ROUND.get() or 0,
            candidate_idx=candidate_idx,
            candidate_total=candidate_total,
            sample_idx=sample_idx,
            sample_total=sample_total,
            sample_id=int(sample_id),
            query_preview=query[:QUERY_PREVIEW_CHARS],
            sample_lookahead=int(sample_lookahead),
            stop_horizon=stop_horizon,
        )
    )


def emit_sample_scored(
    *,
    candidate_idx: int,
    candidate_total: int,
    individual_id: str,
    role: MeasurementRole,
    sample_idx: int,
    sample_total: int,
    cell: GradedCell,
    running: ScoreSummary | None,
) -> None:
    _append_record(
        SampleScoredRecord(
            round=_CURRENT_ROUND.get() or 0,
            candidate_idx=candidate_idx,
            candidate_total=candidate_total,
            individual_id=individual_id,
            role=role,
            sample_idx=sample_idx,
            sample_total=sample_total,
            result=cell.ledger_wire(),
            running=running,
        )
    )


__all__ = [
    "active_cycle_ledger",
    "bind_priced",
    "call_priced",
    "emit_command",
    "emit_command_ack",
    "emit_error_record",
    "emit_priced_key",
    "emit_round_warning",
    "emit_sample_scored",
    "emit_sample_started",
    "emit_token_usage",
    "filed_as",
    "filed_kind",
    "rate_priced_usd",
    "reset_current_round",
    "reset_cycle_ledger",
    "set_current_round",
    "set_cycle_ledger",
]
