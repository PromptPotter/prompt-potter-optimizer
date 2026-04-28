"""Composite-score rendering primitives — single source of truth.

The composite is a weighted aggregate of named evaluators (see
``application/scoring/evaluators.py``); without seeing the inputs the
operator can't tell *why* composite moved when accuracy didn't. This
module renders the operator-facing forms that surfaces share:

- ``render_composite_oneliner`` — 1 line for the per-candidate box.
  Anchors progress against the campaign baseline so even at deep rounds
  the operator sees how far the run has come from origin.
- ``render_composite_block`` — 3-line block for round-level surfaces
  (round summary, L1_SCORE:exit, log.md fenced section). Composite +
  trajectory anchor on line 1; formula on line 2; named evaluator
  values on line 3. Width-honest: short evaluator names are used when
  ``use_short_names`` is set so the values line fits 70-char inner
  width on the node frame.

``PROMPTPOTTER_COMPACT_DISPLAY=1`` collapses the live surfaces to the
legacy single-line ``composite=0.4f`` bottom rule (only when composite
≠ accuracy). ``log.md`` always carries the full block.

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
    # Step-function ternary keywords that show up in custom formulas.
    "if",
    "else",
    "and",
    "or",
    "not",
    "True",
    "False",
}

# Short codes used by ``render_composite_block`` when ``use_short_names``
# is enabled. Names not in this map fall back to themselves so user-
# defined evaluators still surface — only the registry-known ones get
# squeezed into short codes for the round-level live frame.
SHORT_NAMES: dict[str, str] = {
    "accuracy": "acc",
    "error_rate": "err",
    "degraded_rate": "degr",
    "runtime_failure_rate": "rf",
    "latency_norm": "lat",
    "prompt_compactness": "pc",
    "pipeline_compactness": "ppl",
    "source_recall": "src",
    "candidate_recall": "cand",
    "cache_hit_rate": "cache",
    "mean_retrieval_shortfall": "retr",
}

_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z_0-9]*")

__all__ = [
    "SHORT_NAMES",
    "compact_display_enabled",
    "extract_evaluator_names",
    "render_composite_block",
    "render_composite_inline",
    "render_composite_oneliner",
]


def compact_display_enabled() -> bool:
    """Return True when ``PROMPTPOTTER_COMPACT_DISPLAY`` is set to a truthy value."""
    val = os.environ.get("PROMPTPOTTER_COMPACT_DISPLAY", "").strip().lower()
    return val in {"1", "true", "yes", "on"}


def extract_evaluator_names(formula: str, available: set[str]) -> list[str]:
    """Return evaluator names present in *formula*, in first-appearance order.

    Only names that also appear in *available* are returned. Builtins,
    bare numbers, and `if/else` keywords are filtered out.
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


def render_composite_oneliner(composite: float, baseline: float | None = None) -> str:
    """Per-candidate / per-row 1-line composite render.

    Anchors against the campaign baseline so the operator sees how far
    the run has come from origin even at round 50. ``baseline=None``
    (e.g. before init has fired) collapses to ``composite=0.6042``.
    """
    if baseline is None:
        return f"composite={composite:.4f}"
    delta = composite - baseline
    return f"composite={composite:.4f}  (Δ{delta:+.4f} vs baseline {baseline:.4f})"


def _pairs_line(
    names: list[str],
    evaluators: dict[str, float],
    *,
    use_short_names: bool,
) -> str:
    """Format the values line: ``name1=val  name2=val  ...`` with chosen labels."""
    if use_short_names:
        return "  ".join(f"{SHORT_NAMES.get(n, n)}={evaluators[n]:.3f}" for n in names)
    return "  ".join(f"{n}={evaluators[n]:.3f}" for n in names)


def render_composite_block(
    composite: float,
    evaluators: dict[str, float] | None,
    formula: str | None,
    *,
    baseline: float | None = None,
    width: int = 70,
    use_short_names: bool = False,
    legend: str | None = None,
) -> list[str]:
    """3-line round-level composite block.

    Layout:
        line 1: ``composite = X.XXXX   baseline=Y.YYYY  Δ+0.103``
        line 2: ``formula:  <formula>``
        line 3: ``name1=val  name2=val  ...``  (named evaluators present in
                 the formula, full names by default; short codes when
                 ``use_short_names`` is set)

    *legend* (optional) appends a 4th line ``  legend: <text>`` so callers
    can name the abbreviations they used in *formula*. Skipped when None.

    Falls back to a single line ``composite=0.6042 (formula unavailable)``
    when *formula* is None / empty.

    *width* is informational only — used by callers to decide whether to
    request short names; the function does NOT wrap. If the values line
    exceeds *width*, it overflows and the caller must pick a wider frame
    or set ``use_short_names=True``.
    """
    # Line 1: composite + trajectory anchor
    line1 = f"composite = {composite:.4f}"
    if baseline is not None:
        delta = composite - baseline
        line1 += f"   baseline={baseline:.4f}  Δ{delta:+.4f}"

    if not formula:
        return [f"{line1}  (formula unavailable)"]

    # Line 2: formula text (literal — caller chose short or full)
    line2 = f"formula:  {formula}"

    # Line 3 (+ optional legend): named evaluator values.
    #
    # Full-names mode: list evaluators that literally appear in the formula
    # text (so a custom formula's namespace gets displayed).
    #
    # Short-names mode: the formula carries codes (``acc``, ``H``, ``R``)
    # that don't match registry full names — list every evaluator from the
    # dict with its short code. Operator sees the full input vector; the
    # formula text above tells them which codes apply.
    if not evaluators:
        return [line1, line2]

    if use_short_names:
        names = list(evaluators.keys())
    else:
        names = extract_evaluator_names(formula, set(evaluators))

    if not names:
        return [line1, line2]

    line3 = f"  {_pairs_line(names, evaluators, use_short_names=use_short_names)}"
    out = [line1, line2, line3]
    if legend:
        out.append(f"  legend: {legend}")
    return out
