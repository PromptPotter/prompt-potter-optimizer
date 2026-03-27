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
    UNKNOWN = "UNKNOWN"
