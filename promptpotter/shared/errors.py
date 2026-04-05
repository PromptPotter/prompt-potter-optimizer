"""Error classification and shared exception types.

Leaf module — no domain model or service dependencies.
"""
from __future__ import annotations

import asyncio
import enum
import logging
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
def graceful(msg: str):
    """Suppress non-interrupt exceptions with a warning log.

    Re-raises ``KeyboardInterrupt`` and ``asyncio.CancelledError``
    so that graceful-shutdown logic is never swallowed.
    """
    try:
        yield
    except (KeyboardInterrupt, asyncio.CancelledError):
        raise
    except Exception:
        logger.warning(msg, exc_info=True)


def find_rank(candidates: list, ground_truth: str) -> int | None:
    """Find 1-based rank of ground_truth in a candidates list.

    Works with plain strings, dicts with a ``candidate`` key,
    and list/tuple entries (uses first element).
    """
    if not candidates or not ground_truth:
        return None
    for i, c in enumerate(candidates):
        name = (
            c.get("candidate", c)
            if isinstance(c, dict)
            else (c[0] if isinstance(c, (list, tuple)) else str(c))
        )
        if str(name) == ground_truth:
            return i + 1
    return None
