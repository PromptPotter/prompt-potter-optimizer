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
    "ErrorCategory",
    "RequestTooLargeError",
    "ResumeDivergenceError",
    "error_category",
    "graceful",
    "has_pipeline_warnings",
    "is_error_result",
]
