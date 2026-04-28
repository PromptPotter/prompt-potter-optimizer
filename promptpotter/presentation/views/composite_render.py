"""Composite-score rendering primitive — single source of truth for what the
operator sees about composite at any surface.

The composite is a weighted aggregate of named evaluators (see
``application/scoring/evaluators.py``); without seeing the formula and the
per-term values, the operator can't tell *why* composite moved when accuracy
didn't. This module renders one canonical block — composite + formula text +
named evaluator values — embedded by every live surface (per-candidate box,
L1_SCORE:exit summary, round summary, log.md).

``PROMPTPOTTER_COMPACT_DISPLAY=1`` collapses the live surfaces to today's
terse output (a single ``composite=0.4f`` line, only when composite differs
from accuracy). ``log.md`` is unaffected — the digest is the operator's
permanent record and always carries the full block.

Pure functions; no I/O, no Session, no logging side-effects.
"""

from __future__ import annotations

import os
import re

# Builtins exposed to ``compile_round_scorer``'s eval namespace — exclude
# them from name discovery so they don't get spuriously rendered as
# evaluator values.
_FORMULA_BUILTINS = {
    "min",
    "max",
    "float",
    "int",
    "bool",
    "abs",
    "round",
    "log",
    "sqrt",
    "exp",
    "pow",
    # Python keywords that show up in step-function formulas via the
    # `if/else` ternary — never an evaluator name.
    "if",
    "else",
    "and",
    "or",
    "not",
    "True",
    "False",
}

_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z_0-9]*")

__all__ = [
    "compact_display_enabled",
    "extract_evaluator_names",
    "render_composite_block",
    "render_composite_inline",
]


def compact_display_enabled() -> bool:
    """Return True when ``PROMPTPOTTER_COMPACT_DISPLAY`` is set to a truthy value."""
    val = os.environ.get("PROMPTPOTTER_COMPACT_DISPLAY", "").strip().lower()
    return val in {"1", "true", "yes", "on"}


def extract_evaluator_names(formula: str, available: set[str]) -> list[str]:
    """Return evaluator names present in *formula*, in first-appearance order.

    Only names that also appear in *available* are returned. Builtins and
    bare numbers are filtered out by the intersection with *available* and
    by the explicit builtin set, so a formula like
    ``0.5*accuracy + log(1 + latency_norm)`` returns
    ``["accuracy", "latency_norm"]``.
    """
    seen: set[str] = set()
    out: list[str] = []
    for match in _NAME_RE.finditer(formula):
        name = match.group(0)
        if name in seen or name in _FORMULA_BUILTINS or name not in available:
            continue
        seen.add(name)
        out.append(name)
    return out


def render_composite_inline(composite: float) -> str:
    """One-line composite tag — for slots that can't carry a multi-line block."""
    return f"composite={composite:.4f}"


def _wrap(text: str, prefix: str, continuation: str, width: int) -> list[str]:
    """Wrap *text* across multiple lines preserving *prefix* / *continuation* indents.

    Word-aware: splits on whitespace, re-joins greedily up to *width*. The
    first line carries *prefix*; continuation lines carry *continuation*.
    Used so the formula text wraps cleanly inside the box width without
    truncation.
    """
    inner = max(width - max(len(prefix), len(continuation)), 20)
    words = text.split()
    if not words:
        return [prefix.rstrip()]

    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        if len(current) + 1 + len(word) <= inner:
            current = f"{current} {word}"
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return [f"{prefix}{lines[0]}", *(f"{continuation}{rest}" for rest in lines[1:])]


def render_composite_block(
    composite: float,
    evaluators: dict[str, float] | None,
    formula: str | None,
    *,
    width: int = 66,
) -> list[str]:
    """Render the composite block: value + formula + named evaluator pairs.

    Output shape (returned as a list of plain strings; callers wrap with
    box rules / box lines as appropriate):

        composite = 0.3667
        formula:  0.65*accuracy + 0.15*((1-error_rate)+...)
                  + 0.05*prompt_compactness
          accuracy=0.167          latency_norm=0.985
          error_rate=0.000        degraded_rate=0.000
          prompt_compactness=0.998

    Falls back to a single-line ``composite=0.3667 (formula unavailable)``
    when *formula* is None / empty. *evaluators* may be None — the values
    section is then dropped, only composite + formula text show.
    """
    if not formula:
        return [f"composite = {composite:.4f}  (formula unavailable)"]

    lines = [f"composite = {composite:.4f}"]
    lines.extend(_wrap(formula, prefix="formula:  ", continuation="          ", width=width))

    if not evaluators:
        return lines

    names = extract_evaluator_names(formula, set(evaluators))
    if not names:
        return lines

    pairs = [f"{name}={evaluators[name]:.3f}" for name in names]
    # Two columns. The wider column drives padding so values align.
    col_w = max(len(p) for p in pairs) + 2
    for i in range(0, len(pairs), 2):
        left = pairs[i].ljust(col_w)
        right = pairs[i + 1] if i + 1 < len(pairs) else ""
        lines.append(f"  {left}{right}".rstrip())

    return lines
