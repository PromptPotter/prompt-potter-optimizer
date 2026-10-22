from __future__ import annotations

import asyncio
import enum
import logging
import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Annotated, Any

from promptpotter.shared.hashing import shapes_optimizer_prompt

logger = logging.getLogger(__name__)


class ErrorCategory(enum.StrEnum):
    CLIENT = "CLIENT"
    SERVER = "SERVER"
    CONNECTION = "CONNECTION"
    # Refusals no retry clears: a hole, like CONNECTION, since every later cell meets the same one.
    PROVIDER_CREDIT = "PROVIDER_CREDIT"
    PROVIDER_THROTTLED = "PROVIDER_THROTTLED"
    SPEND_CEILING = "SPEND_CEILING"
    TOKEN_CEILING = "TOKEN_CEILING"
    WALL_CLOCK = "WALL_CLOCK"
    # Never sent, so not CONNECTION: the backend the cell would have reached is untouched.
    PRICE_LIST_UNREACHABLE = "PRICE_LIST_UNREACHABLE"
    # Not SPEND_CEILING: no amount of room admits a send no rate bounds.
    NO_RATE = "NO_RATE"
    PIPELINE = "PIPELINE"
    # A bound WE declared: re-measuring ends at the same place, so a repair leaves it alone.
    HALTED = "HALTED"
    UNSCOREABLE = "UNSCOREABLE"
    UNKNOWN = "UNKNOWN"


class CellUnscoreableError(RuntimeError):
    """``spent``: ``{}`` = already ledgered or never sent; ``None`` = unknown, holds the bound."""

    category: ErrorCategory = ErrorCategory.UNSCOREABLE

    def __init__(self, message: str, *, spent: Mapping[str, Mapping[str, object]] | None) -> None:
        super().__init__(message)
        self.spent = spent


class CellHaltedError(CellUnscoreableError):
    category = ErrorCategory.HALTED


class CellInfrastructureError(CellUnscoreableError):
    category = ErrorCategory.CONNECTION


class CellThrottledError(CellUnscoreableError):
    """``BackendClient.run_query`` catches it and re-sends the cell under ``Backpressure``."""

    category = ErrorCategory.PROVIDER_THROTTLED


class CellSendRefusedError(CellInfrastructureError):
    def __init__(
        self,
        message: str,
        *,
        category: ErrorCategory,
        spent: Mapping[str, Mapping[str, object]] | None,
    ) -> None:
        super().__init__(message, spent=spent)
        self.category = category


def cell_failure(
    message: str, category: ErrorCategory, *, spent: Mapping[str, Mapping[str, object]] | None
) -> CellUnscoreableError:
    if category is ErrorCategory.PROVIDER_THROTTLED:
        return CellThrottledError(message, spent=spent)
    if category is ErrorCategory.CONNECTION:
        return CellInfrastructureError(message, spent=spent)
    return CellSendRefusedError(message, category=category, spent=spent)


# OpenRouter's three (HTTP 402 / 403) and Anthropic's, which arrives as an HTTP 400.
_PROVIDER_CREDIT_REFUSAL = re.compile(
    r"requires more credits|[Ii]nsufficient credits|Key limit exceeded|credit balance is too low"
)


def is_provider_credit_refusal(detail: str) -> bool:
    return _PROVIDER_CREDIT_REFUSAL.search(detail) is not None


class SendRefusedError(RuntimeError):
    """The run ENDS on the stop ``category`` maps to (``domain/phases.py::REFUSAL_STOPS``)."""

    def __init__(self, message: str, *, category: ErrorCategory) -> None:
        super().__init__(message)
        self.category = category


class PotterError(Exception):
    http_status: int = 500
    code: str = "internal_error"

    def __init__(
        self, message: str, *, code: str | None = None, details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code
        self.details: dict[str, Any] = details or {}

    @property
    def message(self) -> str:
        return str(self)


class BadRequestError(PotterError):
    http_status = 400
    code = "bad_request"


class UnauthorizedError(PotterError):
    http_status = 401
    code = "unauthenticated"


class NotFoundError(PotterError):
    http_status = 404
    code = "not_found"


class DatasetAccessError(NotFoundError):
    """A 404, never a 403: the existence-leak posture."""

    def __init__(self, name: str) -> None:
        super().__init__(f"Dataset '{name}' not found")
        self.name = name


class ConflictError(PotterError):
    http_status = 409
    code = "version_conflict"


class DiagnosticError(PotterError):
    http_status = 409
    code = "diagnostic_refused"


class MachineBusyError(PotterError):
    """The holder may be the caller's OWN run, so the message stays neutral."""

    http_status = 409
    code = "machine_busy"

    def __init__(
        self,
        *,
        holding: str,
        holder_user: str,
        started_at: str | None,
    ) -> None:
        super().__init__(
            "Every run slot on this machine is taken. Try again once one finishes.",
            details={
                "holding": holding,
                "holder_user": holder_user,
                "started_at": started_at,
            },
        )


class CycleBusyError(PotterError):
    http_status = 409
    code = "cycle_busy"

    def __init__(
        self,
        *,
        job_id: str,
        status: str,
        holder_user: str,
        campaign_id: str,
        cycle_id: str,
        started_at: str | None,
    ) -> None:
        super().__init__(
            f"Cycle {cycle_id} of {campaign_id} already has a {status} run (job {job_id}, "
            f"started {started_at or 'not yet'}). Pause or cancel it, or wait for it to finish.",
            details={
                "job_id": job_id,
                "status": status,
                "holder_user": holder_user,
                "campaign_id": campaign_id,
                "cycle_id": cycle_id,
                "started_at": started_at,
            },
        )


class ContentTooLargeError(PotterError):
    http_status = 413
    code = "too_large"


class PayloadInvalidError(PotterError):
    http_status = 422
    code = "payload_invalid"


class ServiceUnavailableError(PotterError):
    http_status = 503
    code = "service_unavailable"


class StoredConfigInvalidError(PotterError):
    """500, not 422: the request was fine and the caller cannot fix a file the server persisted."""

    http_status = 500
    code = "stored_config_invalid"

    def __init__(self, *, path: str, reason: str) -> None:
        super().__init__(
            f"{path} is no longer readable by this build: {reason}. "
            "`python -m promptpotter restamp --apply` rewrites it.",
            details={"path": path, "reason": reason},
        )


class ManifestUnreadableError(ConflictError):
    """A campaign manifest is never migrated: one this build cannot read is refused by name."""

    code = "manifest_unreadable"

    def __init__(self, *, path: str, reason: str) -> None:
        super().__init__(
            f"{path} was written in a shape this build does not read ({reason}). No migration "
            "exists: delete that campaign, or wipe the store.",
            details={"path": path, "reason": reason},
        )


class RequestTooLargeError(RuntimeError):
    def __init__(
        self,
        *,
        provider_name: str,
        limit: int,
        requested: int,
    ) -> None:
        self.provider_name = provider_name
        self.limit = limit
        self.requested = requested
        super().__init__(
            f"{provider_name}: single request exceeds tier TPM cap "
            f"(limit={limit}, requested={requested}). Retrying will not help — "
            f"the request alone is larger than the per-minute token allowance.\n"
            f"This is one optimizer (L1) call; n_variants is the per-round candidate "
            f"count (parallel calls), NOT a single-request lever — lowering it does "
            f"not shrink this request. Biggest lever first:\n"
            f"  - point the optimizer node `provider` in "
            f"`promptpotter/assets/optimizers/potter/pipeline.yaml` at a tier whose per-minute cap "
            f"exceeds {requested} tokens (e.g. OpenRouter, or a paid Groq tier) — "
            f"the free Groq on_demand tier caps at {limit}\n"
            f"  - or shorten the optimizer prompt (the dataset's task.md)."
        )


class ResumeDivergenceError(RuntimeError):
    def __init__(
        self,
        *,
        round_num: int,
        kind: str,
        recorded_outcome: Any,
        current_outcome: Any,
        diagnostics: dict[str, Any] | None = None,
    ) -> None:
        self.round_num = round_num
        self.kind = kind
        self.recorded_outcome = recorded_outcome
        self.current_outcome = current_outcome
        self.diagnostics = diagnostics or {}
        super().__init__(self._format())

    def _format(self) -> str:
        lines = [
            f"Resume divergence at round {self.round_num}, decision {self.kind!r}:",
            f"  recorded: {self.recorded_outcome}",
            f"  current:  {self.current_outcome}",
        ]
        for k, v in self.diagnostics.items():
            lines.append(f"  {k}: {v}")
        return "\n".join(lines)


class PromptCompositionError(Exception):
    """The run halts with ``RENDER_ERROR`` rather than sending a degraded prompt."""


class OptimizerTimeoutError(TimeoutError):
    """The run halts with ``OPTIMIZER_TIMEOUT``; any other timeout is a crash."""


@shapes_optimizer_prompt
def is_error_result(result: Mapping[str, Any]) -> bool:
    """``predicted == "ERROR"`` is a display token and ``error`` a message: neither detects."""
    return result.get("error_category") is not None


@contextmanager
def graceful(msg: str) -> Iterator[None]:
    try:
        yield
    except (KeyboardInterrupt, asyncio.CancelledError, SendRefusedError):
        raise
    except Exception:
        logger.warning(msg, exc_info=True)


@shapes_optimizer_prompt
def error_category(result: Mapping[str, Any]) -> ErrorCategory | None:
    cat = result.get("error_category")
    if cat is None:
        return None
    if isinstance(cat, ErrorCategory):
        return cat
    try:
        return ErrorCategory(cat)
    except ValueError:
        return None


ERROR_IS_CHARGED: Annotated[dict[ErrorCategory, bool], shapes_optimizer_prompt] = {
    ErrorCategory.CLIENT: True,
    ErrorCategory.SERVER: False,
    ErrorCategory.CONNECTION: False,
    ErrorCategory.PROVIDER_CREDIT: False,
    ErrorCategory.PROVIDER_THROTTLED: False,
    ErrorCategory.SPEND_CEILING: False,
    ErrorCategory.TOKEN_CEILING: False,
    ErrorCategory.WALL_CLOCK: False,
    ErrorCategory.PRICE_LIST_UNREACHABLE: False,
    ErrorCategory.NO_RATE: False,
    ErrorCategory.PIPELINE: True,
    ErrorCategory.HALTED: True,
    ErrorCategory.UNSCOREABLE: False,
    ErrorCategory.UNKNOWN: True,
}
assert set(ERROR_IS_CHARGED) == set(ErrorCategory), "every ErrorCategory must take a side"


__all__ = [
    "ERROR_IS_CHARGED",
    "BadRequestError",
    "CellHaltedError",
    "CellInfrastructureError",
    "CellSendRefusedError",
    "CellThrottledError",
    "CellUnscoreableError",
    "ConflictError",
    "ContentTooLargeError",
    "CycleBusyError",
    "DatasetAccessError",
    "DiagnosticError",
    "ErrorCategory",
    "MachineBusyError",
    "ManifestUnreadableError",
    "NotFoundError",
    "PayloadInvalidError",
    "PotterError",
    "PromptCompositionError",
    "RequestTooLargeError",
    "ResumeDivergenceError",
    "SendRefusedError",
    "ServiceUnavailableError",
    "StoredConfigInvalidError",
    "UnauthorizedError",
    "cell_failure",
    "error_category",
    "graceful",
    "is_error_result",
    "is_provider_credit_refusal",
]
