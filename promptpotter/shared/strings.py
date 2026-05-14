"""Tiny string helpers — currently just ``truncate``."""

from __future__ import annotations

__all__ = ["truncate"]


def truncate(s: str, max_len: int, ellipsis: str = "…") -> str:
    """Cut *s* to ``max_len`` chars at the nearest preceding word boundary.

    Ends with *ellipsis* (a single Unicode "…" by default — three chars
    of ASCII "..." adds enough length to push past callers that pad
    by hand). Returns *s* unchanged when it already fits.
    """
    if len(s) <= max_len:
        return s
    cut = s[: max_len - len(ellipsis)].rsplit(" ", 1)[0]
    return (cut if cut else s[: max_len - len(ellipsis)]) + ellipsis
