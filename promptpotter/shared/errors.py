"""Error classification and shared exception types.

Leaf module — no domain model or service dependencies.
"""

from __future__ import annotations

import asyncio
import enum
import logging
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger(__name__)


class ErrorCategory(enum.StrEnum):
    """Categorical tags for backend scoring errors."""

    CLIENT = "CLIENT"
    SERVER = "SERVER"
    CONNECTION = "CONNECTION"
    PIPELINE = "PIPELINE"
    UNKNOWN = "UNKNOWN"


class PotterError(Exception):
    """Base for every API error — maps to ONE flat wire envelope at ONE seam.

    Carries the three things the wire needs: ``http_status`` (response code),
    ``code`` (the stable machine string), and ``details`` (optional structured
    context); ``message`` is ``str(self)``. The single FastAPI handler in
    ``main.py`` serializes these to the flat ``ErrorEnvelope`` declared in
    ``docs/specs/m12-api-openapi.yaml`` — ``{"error", "message", "details"?}`` —
    and the command-dispatcher's central catch emits a rejected ack then re-raises
    the same instance for that handler. There is no per-class handler and no
    ``HTTPException`` in the API layer (locked by
    ``test_no_raw_httpexception_in_api`` / the surface contract).

    A subclass fixes the HTTP *status family* (``NotFoundError`` → 404); the
    ``code`` is the stable category, defaulted per subclass and overridable per
    raise site (``NotFoundError(msg, code="command_target_not_found")``) for the
    closed codes the OpenAPI enum names. Routes that need extra context still add
    ``details`` at the raise site.
    """

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
    """Syntactically/semantically bad request the route rejects up front — 400."""

    http_status = 400
    code = "bad_request"


class UnauthorizedError(PotterError):
    """No valid identity on a route that requires sign-in — 401."""

    http_status = 401
    code = "unauthenticated"


class ForbiddenError(PotterError):
    """Authenticated but not permitted — 403."""

    http_status = 403
    code = "capability_denied"


class NotFoundError(PotterError):
    """Target resource doesn't exist (or isn't visible to the caller) — 404."""

    http_status = 404
    code = "not_found"


class ConflictError(PotterError):
    """Request conflicts with current state (slug taken, version clash) — 409."""

    http_status = 409
    code = "version_conflict"


class ContentTooLargeError(PotterError):
    """Request/target exceeds a hard size cap (too many file entries) — 413."""

    http_status = 413
    code = "too_large"


class PayloadInvalidError(PotterError):
    """Well-formed request that fails a business/shape rule — 422."""

    http_status = 422
    code = "payload_invalid"


class ServiceUnavailableError(PotterError):
    """A required dependency isn't ready (job registry, draft registry) — 503."""

    http_status = 503
    code = "service_unavailable"


class RequestTooLargeError(RuntimeError):
    """Raised when a single LLM request exceeds the provider's per-minute token cap.

    Terminal — retrying will not help. Caller (CLI/notebook) should surface the
    message to the user without a traceback.
    """

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
            f"  - point CampaignConfig.optimizer_llm.provider at a tier whose "
            f"per-minute cap exceeds {requested} tokens (e.g. OpenRouter, or a paid "
            f"Groq tier) — the free Groq on_demand tier caps at {limit}\n"
            f"  - or shorten the optimizer meta-prompt (task_description.md)."
        )


class ResumeDivergenceError(RuntimeError):
    """Raised when a resumed campaign diverges from the recorded trajectory.

    A decision recorded in a prior round_data (round winner, elimination cut,
    escalation trigger, …) re-derives to a different outcome under the
    currently active scorer. The only mechanism: every recorded decision is
    a pure function of scored results, and rescoring changes the inputs.

    Rerun ``resume --fork-on-divergence`` to branch a sibling cycle from
    this point under the new policy, or revert ``campaign.json::scoring``
    to continue the original trajectory.
    """

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


def is_error_result(result: Mapping[str, Any]) -> bool:
    """Return True if *result* represents a failed measurement.

    Detection rides the typed ``error_category`` channel — the single owner of
    "this sample errored". ``predicted == "ERROR"`` is a display token, not a
    detection mechanism, and ``error`` is a plain human message.
    """
    return result.get("error_category") is not None


def has_pipeline_warnings(result: Mapping[str, Any]) -> bool:
    """A sample carries pipeline warnings iff ``pipeline_data.diagnostics.warnings`` is non-empty.

    Sibling of :func:`is_error_result` (backend failed outright). Renamed
    from ``is_degraded`` so the name describes what it actually checks —
    "degraded" was confusable with the backend-error sentinel.
    """
    return bool((result.get("pipeline_data") or {}).get("diagnostics", {}).get("warnings"))


@contextmanager
def graceful(msg: str, *, level: int = logging.WARNING) -> Iterator[None]:
    """Suppress non-interrupt exceptions with a log message.

    Re-raises ``KeyboardInterrupt`` and ``asyncio.CancelledError``
    so that graceful-shutdown logic is never swallowed.

    Args:
        msg: Log message on failure.
        level: Log level (default WARNING). Use ``logging.DEBUG`` for
               observability/tracing code where failures are expected.
    """
    try:
        yield
    except (KeyboardInterrupt, asyncio.CancelledError):
        raise
    except Exception:
        logger.log(level, msg, exc_info=True)


def error_category(result: Mapping[str, Any]) -> ErrorCategory | None:
    """Read the typed error category off a measurement (``None`` when clean).

    Tolerates the on-disk round-trip form: a persisted row read back from JSON
    carries the bare ``StrEnum`` value (a plain ``str``), coerced back here.
    """
    cat = result.get("error_category")
    if cat is None:
        return None
    if isinstance(cat, ErrorCategory):
        return cat
    try:
        return ErrorCategory(cat)
    except ValueError:
        return None


__all__ = [
    "BadRequestError",
    "ConflictError",
    "ContentTooLargeError",
    "ErrorCategory",
    "ForbiddenError",
    "NotFoundError",
    "PayloadInvalidError",
    "PotterError",
    "RequestTooLargeError",
    "ResumeDivergenceError",
    "ServiceUnavailableError",
    "UnauthorizedError",
    "error_category",
    "graceful",
    "has_pipeline_warnings",
    "is_error_result",
]
