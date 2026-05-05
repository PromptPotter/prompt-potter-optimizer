"""Composite-score utilities — short-form formula inlining + render primitives.

Two related concerns share this module so the short codes (``acc``, ``H``,
``lat``, ``R``, ``pc``) stay in lockstep with the renderers that use them:

1. **Short-form formula inlining** (``inline_short_formula_values``) —
   transforms ``0.65*acc + 0.15*H + ...`` into
   ``0.65*acc|0.667 + 0.15*H|0.972 + ...`` for ``dashboard.json``. Used by
   both the application layer (``evaluators``, ``phase_views``) and the
   infrastructure layer (``session_emitter``). Codes are tightly coupled to
   ``application/scoring/evaluators.py::default_per_round_formula_short``;
   keep them in sync by hand if the registry default changes.

2. **Composite-score render primitives** (``render_composite_fitness_oneliner`` /
   ``render_composite_fitness_block`` / ``render_composite_fitness_inline``) — operator-
   facing forms that surfaces share. Without seeing the inputs the operator
   can't tell *why* composite_fitness moved when accuracy didn't.
   ``PROMPTPOTTER_COMPACT_DISPLAY=1`` collapses the live surfaces to a
   single-line ``composite_fitness=0.4f`` bottom rule (``log.md`` always carries
   the full block).

Pure functions; no I/O, no Session, no logging side-effects.
"""

from __future__ import annotations

import os
import re

__all__ = [
    "SHORT_NAMES",
    "compact_display_enabled",
    "extract_evaluator_names",
    "inline_short_formula_values",
    "render_composite_fitness_block",
    "render_composite_fitness_inline",
    "render_composite_fitness_oneliner",
]


# Direct-mapping codes used by ``default_per_round_formula_short``;
# ``H`` and ``R`` are synthesized aggregates handled separately below.
_SHORT_DIRECT: dict[str, str] = {
    "acc": "accuracy",
    "lat": "latency_norm",
    "pc": "prompt_compactness",
}

_SHORT_CODE_RE = re.compile(r"\b(acc|H|lat|R|pc)\b")


def inline_short_formula_values(
    formula_short: str | None,
    evaluators: dict[str, float] | None,
) -> str | None:
    """Inline resolved values into the short formula string.

    Transforms ``0.65*acc + 0.15*H + 0.10*lat + 0.05*R + 0.05*pc``
    into ``0.65*acc|0.667 + 0.15*H|0.972 + 0.10*lat|0.965 + ...`` so
    an operator tailing dashboard.json sees the formula and its
    resolved inputs in a single line — no separate legend, no separate
    ``evaluators`` lookup.

    ``acc`` / ``lat`` / ``pc`` resolve directly from the evaluators
    dict. ``H`` and ``R`` are synthesized: ``H = mean(1 - error_rate,
    1 - degraded_rate, 1 - runtime_failure_rate)``; ``R`` is the mean
    of whichever recall evaluators (``source_recall``,
    ``candidate_recall``, ``cache_hit_rate``) are present.

    Returns *formula_short* unchanged when *evaluators* is empty (e.g.
    before the first candidate has scored). Returns ``None`` when
    *formula_short* is None (custom formula authored by the operator —
    no template structure to inline into).
    """
    if formula_short is None:
        return None
    if not evaluators:
        return formula_short

    values: dict[str, float] = {}
    for short, full in _SHORT_DIRECT.items():
        v = evaluators.get(full)
        if v is not None:
            values[short] = float(v)

    health: list[float] = []
    for name in ("error_rate", "degraded_rate", "runtime_failure_rate"):
        v = evaluators.get(name)
        if v is not None:
            health.append(1.0 - float(v))
    if health:
        values["H"] = sum(health) / len(health)

    recall: list[float] = []
    for name in ("source_recall", "candidate_recall", "cache_hit_rate"):
        v = evaluators.get(name)
        if v is not None:
            recall.append(float(v))
    if recall:
        values["R"] = sum(recall) / len(recall)

    def _sub(match: re.Match[str]) -> str:
        code = match.group(0)
        v = values.get(code)
        return f"{code}|{v:.3f}" if v is not None else code

    return _SHORT_CODE_RE.sub(_sub, formula_short)


# ===========================================================================
# Composite-score rendering primitives
# ===========================================================================

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

# Short codes used by ``render_composite_fitness_block`` when ``use_short_names``
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


def render_composite_fitness_inline(composite_fitness: float) -> str:
    """One-line composite_fitness tag — for slots that can't carry a multi-line block."""
    return f"composite_fitness={composite_fitness:.4f}"


def render_composite_fitness_oneliner(composite_fitness: float, baseline: float | None = None) -> str:
    """Per-candidate / per-row 1-line composite_fitness render.

    Anchors against the campaign baseline so the operator sees how far
    the run has come from origin even at round 50. ``baseline=None``
    (e.g. before init has fired) collapses to ``composite_fitness=0.6042``.
    """
    if baseline is None:
        return f"composite_fitness={composite_fitness:.4f}"
    delta = composite_fitness - baseline
    return f"composite_fitness={composite_fitness:.4f}  (Δ{delta:+.4f} vs baseline {baseline:.4f})"


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


def render_composite_fitness_block(
    composite_fitness: float,
    evaluators: dict[str, float] | None,
    formula: str | None,
    *,
    baseline: float | None = None,
    width: int = 70,
    use_short_names: bool = False,
    legend: str | None = None,
) -> list[str]:
    """3-line round-level composite_fitness block.

    Layout:
        line 1: ``composite_fitness = X.XXXX   baseline=Y.YYYY  Δ+0.103``
        line 2: ``formula:  <formula>``
        line 3: ``name1=val  name2=val  ...``  (named evaluators present in
                 the formula, full names by default; short codes when
                 ``use_short_names`` is set)

    *legend* (optional) appends a 4th line ``  legend: <text>`` so callers
    can name the abbreviations they used in *formula*. Skipped when None.

    Falls back to a single line ``composite_fitness=0.6042 (formula unavailable)``
    when *formula* is None / empty.

    *width* is informational only — used by callers to decide whether to
    request short names; the function does NOT wrap. If the values line
    exceeds *width*, it overflows and the caller must pick a wider frame
    or set ``use_short_names=True``.
    """
    # Line 1: composite_fitness + trajectory anchor
    line1 = f"composite_fitness = {composite_fitness:.4f}"
    if baseline is not None:
        delta = composite_fitness - baseline
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
