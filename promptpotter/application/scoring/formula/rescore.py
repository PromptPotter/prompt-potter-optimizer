"""Sole writer of top-level ``fitness``, ``objective`` and ``unscored``, and idempotent. There is
deliberately no ``hit`` beside them — that stores a threshold's output.

**The one derivation from facts to graded cells**: the archive keeps none of the three
(``GRADE_KEYS``), so every reader of a row — the walk, a replay, an archive read — grades it here."""

from __future__ import annotations

from typing import Any

from promptpotter.application.scoring.formula.compiler import ScoringTermMissingError
from promptpotter.domain.scoring import CellScorer
from promptpotter.shared.errors import is_error_result


def rescore_results(results: list[dict[str, Any]], scorer: CellScorer) -> list[dict[str, Any]]:
    """Apply *scorer* to each result, resolving it into exactly one of three states.

    **SCORED** — both keys stamped. ``fitness`` first, then ``objective``: the composite reads the
    correctness it is composed OF (``compiler.py::objective_namespace`` binds ``fitness``), so the
    order is the dependency.

    **ERRORED** — stamped ``0.0`` without the formula, which may parse ``predicted`` and an error
    row has none. Whether that 0.0 is a MISS or a display convention is ``is_graded``'s call
    (``domain/scoring.py``): an error the configuration caused is its miss, a provider's is nothing.

    **UNSCORED** — the backend answered and this formula cannot grade the answer. Both keys are
    REMOVED and ``unscored`` carries the missing term's own message, so a row graded again under a
    second formula keeps no verdict of the first.

    Only ``ScoringTermMissingError`` resolves that way, never its parent: that one is a contract bug
    every cell fails, so it keeps halting loud. Halting on a missing TERM instead discards an
    episode already paid for."""

    for r in results:
        if is_error_result(r):
            r["fitness"] = r["objective"] = 0.0
            continue
        try:
            # STAMPED in this order, not computed then assigned: ``objective_namespace`` binds
            # ``fitness`` off the ROW, so a composite naming it — which every shipped ``per_cell``
            # does — cannot be evaluated until the first stamp has landed.
            r["fitness"] = scorer.fitness(r)
            r["objective"] = scorer.objective(r)
        except ScoringTermMissingError as exc:
            r.pop("fitness", None)
            r.pop("objective", None)
            r["unscored"] = str(exc)
            continue
        r.pop("unscored", None)
    return results


__all__ = ["rescore_results"]
