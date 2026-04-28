"""Pure utilities for the short-form composite formula.

Lives in ``shared/`` because both the application layer (``evaluators``,
``phase_views``) and the infrastructure layer (``session_emitter``) need
to inline values into the short formula string for dashboard.json. The
short codes (``acc``, ``H``, ``lat``, ``R``, ``pc``) are tightly coupled
to ``application/scoring/evaluators.py::default_per_round_formula_short``;
keep them in sync by hand if the registry default changes.
"""

from __future__ import annotations

import re

__all__ = ["inline_short_formula_values"]


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
