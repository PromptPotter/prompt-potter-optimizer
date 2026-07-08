"""Composite-score utilities — the short-code vocabulary + render primitives.

This module is the **single source** for the evaluator short codes (``acc``,
``H``, ``lat``, ``R``, ``pc``, …). It lives in ``shared/`` because all three
caller layers use it — application (``views``), presentation (``live``) and
infrastructure (``live_dashboard``) — and infrastructure must not import the
application-layer evaluator registry. The vocabulary is therefore data here, not
a field on ``Evaluator``; ``evaluators.py::default_per_round_formula_short``
derives its short form from this table (``to_short_formula``) rather than
hand-syncing a literal.

Two concerns share the module:

1. **Short-form formula inlining** (``inline_short_formula_values``) — transforms
   ``0.65*acc + 0.15*H + ...`` into ``0.65*acc|0.667 + 0.15*H|0.972 + ...`` for
   ``dashboard.json`` so the operator sees the formula and its resolved inputs on
   one line.
2. **Composite-score render primitives** (``render_composite_fitness_oneliner`` /
   ``render_composite_fitness_block``) — operator-facing forms surfaces share.
   Without the inputs the operator can't tell *why* composite_fitness moved when
   accuracy didn't.

Both read the one ``SHORT_NAMES`` table (+ ``AGGREGATES`` for the synthesized
``H``/``R`` terms); the code regex and the short↔full inversion are derived, so
adding or renaming an evaluator is a single edit here. Pure functions; no I/O.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "SHORT_NAMES",
    "inline_short_formula_values",
    "render_composite_fitness_block",
    "render_composite_fitness_oneliner",
    "to_short_formula",
]


# ===========================================================================
# The short-code vocabulary — SINGLE SOURCE
# ===========================================================================

# Full evaluator name → short code. Both the short-formula inliner and the
# value-breakdown renderer read this one map; names absent from it render as
# themselves (so operator-defined evaluators still surface). Adding or renaming
# an evaluator's short code is a single edit here — everything else derives.
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


@dataclass(frozen=True)
class _ShortAggregate:
    """A synthesized short code (``H``/``R``) that rolls several evaluators into
    one displayed term. Its membership is declared once here — never re-listed in
    a renderer. ``H`` and ``R`` are *display* aggregates over the evaluator dict,
    not registry evaluators (adding them to ``_REGISTRY`` would materialize and
    serve them, changing behavior)."""

    short_code: str
    members: tuple[str, ...]
    complement: bool  # True → mean(1 - v) (health); False → mean(v) (recall)


AGGREGATES: tuple[_ShortAggregate, ...] = (
    _ShortAggregate("H", ("error_rate", "degraded_rate", "runtime_failure_rate"), complement=True),
    _ShortAggregate("R", ("source_recall", "candidate_recall", "cache_hit_rate"), complement=False),
)

# short code → full evaluator name; inverse of SHORT_NAMES, for value lookup.
_FULL_BY_SHORT: dict[str, str] = {short: full for full, short in SHORT_NAMES.items()}

# Every short code that can appear in a formula — direct evaluators + aggregates.
# Longest-first so the alternation never settles for a shorter prefix.
_ALL_SHORT_CODES: tuple[str, ...] = tuple(
    sorted({*SHORT_NAMES.values(), *(a.short_code for a in AGGREGATES)}, key=len, reverse=True)
)
_SHORT_CODE_RE = re.compile(r"\b(" + "|".join(re.escape(c) for c in _ALL_SHORT_CODES) + r")\b")

_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z_0-9]*")


def to_short_formula(formula: str) -> str:
    """Translate a full-name formula (``0.7*accuracy + 0.3*latency_norm``) into its
    short form (``0.7*acc + 0.3*lat``) via ``SHORT_NAMES``. Names without a short
    code render unchanged. This is how ``default_per_round_formula_short`` derives —
    no hand-synced literal."""
    return _NAME_RE.sub(lambda m: SHORT_NAMES.get(m.group(0), m.group(0)), formula)


def _aggregate_value(agg: _ShortAggregate, evaluators: dict[str, float]) -> float | None:
    """Reduce an aggregate over whichever of its members are present; None if none."""
    vals: list[float] = []
    for member in agg.members:
        v = evaluators.get(member)
        if v is not None:
            vals.append(1.0 - float(v) if agg.complement else float(v))
    return sum(vals) / len(vals) if vals else None


def inline_short_formula_values(
    formula_short: str | None,
    evaluators: dict[str, float] | None,
) -> str | None:
    """Inline resolved values into the short formula string.

    Transforms ``0.65*acc + 0.15*H + 0.10*lat + 0.05*R + 0.05*pc`` into
    ``0.65*acc|0.667 + 0.15*H|0.972 + 0.10*lat|0.965 + ...`` so an operator tailing
    dashboard.json sees the formula and its resolved inputs in a single line — no
    separate legend, no separate ``evaluators`` lookup.

    Direct codes resolve from the evaluators dict via ``SHORT_NAMES``; the ``H`` /
    ``R`` aggregates reduce their member evaluators (see ``AGGREGATES``). A code
    present in the formula but unresolved (its evaluator absent this round) renders
    unchanged.

    Returns *formula_short* unchanged when *evaluators* is empty (e.g. before the
    first candidate has scored). Returns ``None`` when *formula_short* is None
    (custom formula authored by the operator — no template structure to inline into).
    """
    if formula_short is None:
        return None
    if not evaluators:
        return formula_short

    values: dict[str, float] = {}
    for short, full in _FULL_BY_SHORT.items():
        v = evaluators.get(full)
        if v is not None:
            values[short] = float(v)
    for agg in AGGREGATES:
        av = _aggregate_value(agg, evaluators)
        if av is not None:
            values[agg.short_code] = av

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


def render_composite_fitness_oneliner(composite_fitness: float, origin: float | None = None) -> str:
    """Per-candidate / per-row 1-line composite_fitness render.

    Anchors against the campaign origin so the operator sees how far
    the run has come from origin even at round 50. ``origin=None``
    (e.g. before init has fired) collapses to ``composite_fitness=0.6042``.
    """
    if origin is None:
        return f"composite_fitness={composite_fitness:.4f}"
    delta = composite_fitness - origin
    return f"composite_fitness={composite_fitness:.4f}  (Δ{delta:+.4f} vs origin {origin:.4f})"


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
    origin: float | None = None,
    use_short_names: bool = False,
    legend: str | None = None,
) -> list[str]:
    """3-line round-level composite_fitness block.

    Layout:
        line 1: ``composite_fitness = X.XXXX   origin=Y.YYYY  Δ+0.103``
        line 2: ``formula:  <formula>``
        line 3: ``name1=val  name2=val  ...``  (named evaluators present in
                 the formula, full names by default; short codes when
                 ``use_short_names`` is set)

    *legend* (optional) appends a 4th line ``  legend: <text>`` so callers
    can name the abbreviations they used in *formula*. Skipped when None.

    Falls back to a single line ``composite_fitness=0.6042 (formula unavailable)``
    when *formula* is None / empty.
    """
    # Line 1: composite_fitness + trajectory anchor
    line1 = f"composite_fitness = {composite_fitness:.4f}"
    if origin is not None:
        delta = composite_fitness - origin
        line1 += f"   origin={origin:.4f}  Δ{delta:+.4f}"

    if not formula:
        return [f"{line1}  (formula unavailable)"]

    # Line 2: formula text. In short-names mode the formula carries
    # synthesized codes (``H``, ``R``) that don't appear as keys in the
    # ``evaluators`` dict — inline their resolved values so the operator
    # can reconcile the formula against the breakdown without a separate
    # legend lookup.
    if use_short_names and evaluators:
        line2 = f"formula:  {inline_short_formula_values(formula, evaluators) or formula}"
    else:
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
