"""Shared leaf-level utilities — no service or model dependencies."""

from __future__ import annotations

import math

__all__ = ["sigmoid", "truncate"]


def truncate(s: str, max_len: int, ellipsis: str = "…") -> str:
    """Cut *s* to ``max_len`` at the nearest preceding word boundary; returns unchanged if it fits."""
    if len(s) <= max_len:
        return s
    cut = s[: max_len - len(ellipsis)].rsplit(" ", 1)[0]
    return (cut if cut else s[: max_len - len(ellipsis)]) + ellipsis


def sigmoid(x: float) -> float:
    """Numerically-stable logistic σ(x) — no SciPy dependency."""
    x = float(x)
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    ex = math.exp(x)
    return ex / (1.0 + ex)
