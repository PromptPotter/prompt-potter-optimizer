"""Statistical helpers for feedback cycle display annotations.

Core functions re-exported from ``api.services.search._stats``.
Display-only formatters (fmt_ci, fmt_pvalue) remain here.
"""

from __future__ import annotations

# Re-export core functions from service layer (canonical location)
from promptpotter.services.search._stats import (
    min_detectable_effect,
    proportion_test,
    wilson_ci,
)

__all__ = [
    "fmt_ci",
    "fmt_pvalue",
    "min_detectable_effect",
    "proportion_test",
    "wilson_ci",
]


def fmt_ci(lower: float, upper: float) -> str:
    """Format a CI as '[X.X%-Y.Y%]'."""
    return f"[{lower:.1%}-{upper:.1%}]"


def fmt_pvalue(p: float) -> str:
    """Format p-value with significance stars."""
    if p < 0.001:
        return "p<0.001 ***"
    if p < 0.01:
        return f"p={p:.3f} **"
    if p < 0.05:
        return f"p={p:.2f} *"
    return f"p={p:.2f} (ns)"
