"""Error classification for backend evaluation responses.

Leaf module shared by prompt_eval and any future error-handling code.
Lives in ``api/shared/`` — no domain model or service dependencies.
"""
from __future__ import annotations

import enum


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
