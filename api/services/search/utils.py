"""Shared utilities for search modules."""

from typing import Any


def preview(value: Any, max_len: int = 40) -> str:
    """Truncated preview of a variant value."""
    s = str(value)
    if not s:
        return "(empty)"
    return s[:max_len] + ("..." if len(s) > max_len else "")
