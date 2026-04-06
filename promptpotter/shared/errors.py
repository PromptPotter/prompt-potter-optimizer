"""Error classification and shared exception types.

Leaf module — no domain model or service dependencies.
"""

from __future__ import annotations

import asyncio
import enum
import logging
from collections import Counter
from contextlib import contextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from promptpotter.services.campaign.escalation import EscalationSignal

logger = logging.getLogger(__name__)


class ErrorCategory(enum.StrEnum):
    """Categorical tags for backend evaluation errors."""

    CLIENT = "CLIENT"
    SERVER = "SERVER"
    CONNECTION = "CONNECTION"
    PIPELINE = "PIPELINE"
    UNKNOWN = "UNKNOWN"


def is_error_result(result: dict) -> bool:
    """Return True if *result* represents a failed evaluation.

    Catches all error forms:
    - Truthy ``error`` field (tagged error message from exception handling)
    - ``predicted == "ERROR"`` (backend returned ERROR as candidate name,
      or legacy cached data with ``error=""`` / ``error=None``)
    """
    return bool(result.get("error")) or result.get("predicted") == "ERROR"


class EscalationError(Exception):
    """Raised inside _run_eval_batch when an escalation check fires.

    Carries ``partial_results`` so the campaign loop can use the data
    collected before the escalation triggered.
    """

    def __init__(self, signal: EscalationSignal, partial_results: list[dict]):
        self.signal = signal
        self.partial_results = partial_results
        super().__init__(f"EscalationCheck '{signal.check_name}' triggered")


@contextmanager
def graceful(msg: str, *, level: int = logging.WARNING):
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


def error_category(error: str | None) -> ErrorCategory | None:
    """Extract error category from a ``[TAG] ...`` prefixed error string."""
    if error and error.startswith("["):
        bracket_end = error.find("]")
        if bracket_end > 0:
            tag = error[1:bracket_end]
            try:
                return ErrorCategory(tag)
            except ValueError:
                return None
    return None


def most_common_error_category(results: list) -> ErrorCategory | None:
    """Return the most common error category across errored results."""
    cats = [error_category(r.get("error")) for r in results if is_error_result(r)]
    cats = [c for c in cats if c]
    if not cats:
        return None
    return Counter(cats).most_common(1)[0][0]
