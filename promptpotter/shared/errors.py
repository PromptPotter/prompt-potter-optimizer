"""Error classification and shared exception types.

Leaf module — no domain model or service dependencies.
"""

from __future__ import annotations

import asyncio
import enum
import logging
from collections import Counter
from collections.abc import Mapping
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


class ActiveSessionMismatchError(RuntimeError):
    """Raised when a caller requests a tenant different from the active pointer.

    ``.promptpotter/active_session.json`` is the "currently active tab" of the
    campaign workspace. Any entry point (CLI, notebook, smoke tool) that asks
    :func:`init_services` for a different ``tenant_id`` without explicitly
    passing ``take_over=True`` hits this error instead of silently drifting to
    a different workspace. Pass ``take_over=True`` to clear the pointer and
    proceed, or run ``python -m promptpotter init ...`` for the new dataset
    first (which rewrites the pointer as part of session creation).
    """

    def __init__(
        self,
        *,
        active_tenant_id: str,
        active_session_id: str,
        requested_tenant_id: str,
    ) -> None:
        self.active_tenant_id = active_tenant_id
        self.active_session_id = active_session_id
        self.requested_tenant_id = requested_tenant_id
        super().__init__(
            f"Active session points at tenant {active_tenant_id!r} "
            f"(session {active_session_id!r}), but this call requested "
            f"tenant {requested_tenant_id!r}. "
            f"Pass take_over=True to clear the pointer and proceed, or run "
            f"`python -m promptpotter init ...` for the new dataset first."
        )


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
            f"Reduce the L1 meta-prompt (biggest levers first):\n"
            f"  - CampaignConfig.optimization.max_failures: 15 -> 5\n"
            f"  - CampaignConfig.optimization.n_variants: 5 -> 3\n"
            f"  - shorten datasets/<name>/task_description.md\n"
            f"Or upgrade your provider tier."
        )


class ResumeDivergenceError(RuntimeError):
    """Raised when a resumed campaign diverges from the recorded trajectory.

    A decision recorded in a prior trial (round winner, elimination cut,
    escalation trigger, …) re-derives to a different outcome under the
    currently active scorer. The only mechanism: every recorded decision is
    a pure function of scored results, and rescoring changes the inputs.

    Rerun ``optimize`` with ``--fork-on-divergence`` to branch a sibling
    cycle from this point under the new policy, or revert
    ``campaign.json::scoring`` to continue the original trajectory.
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

    Catches all error forms:
    - Truthy ``error`` field (tagged error message from exception handling)
    - ``predicted == "ERROR"`` (backend returned ERROR as candidate name,
      or legacy cached data with ``error=""`` / ``error=None``)
    """
    return bool(result.get("error")) or result.get("predicted") == "ERROR"


def is_degraded(result: Mapping[str, Any]) -> bool:
    """A query is degraded iff its pipeline_data.diagnostics.warnings list is non-empty."""
    return bool((result.get("pipeline_data") or {}).get("diagnostics", {}).get("warnings"))


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
