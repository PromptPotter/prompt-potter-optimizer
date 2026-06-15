"""Leaf-level string helpers shared across layers.

``shared/`` is leaf-level, so any layer may import this without reaching
into infrastructure.
"""

from __future__ import annotations

__all__ = ["truncate_ellipsis"]


def truncate_ellipsis(s: str, n: int) -> str:
    """Clamp ``s`` to ``n`` chars, ending in ``…`` when it had to be cut."""
    return s if len(s) <= n else s[: n - 1] + "…"
