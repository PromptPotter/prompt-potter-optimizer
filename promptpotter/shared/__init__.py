"""Shared leaf-level utilities — no service or model dependencies."""

from __future__ import annotations

__all__ = ["truncate"]


def truncate(s: str, max_len: int, ellipsis: str = "…") -> str:
    """Cut *s* to ``max_len`` at the nearest preceding word boundary; returns unchanged if it fits."""
    if len(s) <= max_len:
        return s
    cut = s[: max_len - len(ellipsis)].rsplit(" ", 1)[0]
    return (cut if cut else s[: max_len - len(ellipsis)]) + ellipsis
