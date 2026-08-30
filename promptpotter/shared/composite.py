"""``SHORT_NAMES`` (+ ``AGGREGATES``) is the single source for evaluator short codes — the regex and
the short↔full inversion derive from it. In ``shared/`` so infrastructure need not import the registry."""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
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
    "fitness": "fit",
    "error_rate": "err",
    "degraded_rate": "degr",
    "latency": "lat",
    "cost": "usd",
    "tokens": "tok",
    "source_recall": "src",
    "candidate_recall": "cand",
    "cache_hit_rate": "cache",
    "mean_retrieval_shortfall": "retr",
}


@dataclass(frozen=True)
class _ShortAggregate:
    """A synthesized code (``H``/``R``) rolling several evaluators into one displayed term, declared once
    here. A DISPLAY aggregate — putting it in ``_REGISTRY`` would materialize and serve it as a term."""

    short_code: str
    members: tuple[str, ...]
    complement: bool  # True → mean(1 - v) (health); False → mean(v) (recall)


AGGREGATES: tuple[_ShortAggregate, ...] = (
    _ShortAggregate("H", ("error_rate", "degraded_rate"), complement=True),
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
    """Translate a full-name formula into its short form via ``SHORT_NAMES``; a name with no short code
    renders unchanged. This is how ``resolve_cell_formula``'s short form derives, with no literal."""
    return _NAME_RE.sub(lambda m: SHORT_NAMES.get(m.group(0), m.group(0)), formula)


def _aggregate_value(agg: _ShortAggregate, evaluators: dict[str, float]) -> float | None:
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
    """Inline resolved values into the short formula, so a tail of ``dashboard.json`` shows the formula
    and its inputs on one line. ``None`` for an operator-authored formula — no template to inline into."""
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
    seen: set[str] = set()
    out: list[str] = []
    for match in _NAME_RE.finditer(formula):
        name = match.group(0)
        if name in seen or name in _FORMULA_BUILTINS or name not in available:
            continue
        seen.add(name)
        out.append(name)
    return out


def render_composite_fitness_oneliner(composite_fitness: float, parent: float | None = None) -> str:
    """``parent=None`` collapses to the bare value."""
    if parent is None:
        return f"composite_fitness={composite_fitness:.4f}"
    delta = composite_fitness - parent
    return f"composite_fitness={composite_fitness:.4f}  (Δ{delta:+.4f} vs parent {parent:.4f})"


def _pairs_line(
    names: list[str],
    evaluators: dict[str, float],
    *,
    use_short_names: bool,
) -> str:
    if use_short_names:
        return "  ".join(f"{SHORT_NAMES.get(n, n)}={evaluators[n]:.3f}" for n in names)
    return "  ".join(f"{n}={evaluators[n]:.3f}" for n in names)


def render_composite_fitness_block(
    composite_fitness: float,
    evaluators: dict[str, float] | None,
    formula: str | None,
    *,
    parent: float | None = None,
    use_short_names: bool = False,
) -> list[str]:
    line1 = f"composite_fitness = {composite_fitness:.4f}"
    if parent is not None:
        delta = composite_fitness - parent
        line1 += f"   parent={parent:.4f}  Δ{delta:+.4f}"

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

    # Line 3: named evaluator values.
    #
    # Full-names mode: list evaluators that literally appear in the formula
    # text (so a custom formula's namespace gets displayed).
    #
    # Short-names mode: the formula carries codes (``acc``, ``H``, ``R``)
    # that don't match registry full names — list every evaluator from the
    # dict with its short code. Operator sees the full input vector; the
    # formula text above tells them which codes apply. Line 2 inlines each
    # code's resolved value, which is why there is no legend line: the
    # abbreviations are already reconciled where they are used.
    if not evaluators:
        return [line1, line2]

    if use_short_names:
        names = list(evaluators.keys())
    else:
        names = extract_evaluator_names(formula, set(evaluators))

    if not names:
        return [line1, line2]

    return [line1, line2, f"  {_pairs_line(names, evaluators, use_short_names=use_short_names)}"]
