"""L1-score block builder for ``dashboard.json::current_round.nodes.l1_score``.

Projects in-memory candidates dict to the dashboard's input/output shape.
Live mode renders samples as compact one-liners (keeps dashboard.json from
carrying 2 kB query strings per sample); round-complete flush emits the
full sample dicts.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from promptpotter.domain.results import candidate_label
from promptpotter.infrastructure.projections.live_dashboard.sample import fmt_sample_line
from promptpotter.shared.composite import inline_short_formula_values

if TYPE_CHECKING:
    from promptpotter.domain.results import RoundResult


def build_l1_score_block(
    state: dict[str, Any],
    round_block: dict[str, Any],
    short_formula_template: str | None,
    round_result: RoundResult | None = None,
) -> dict[str, Any]:
    """Project current candidates to dashboard's l1_score shape.

    ``label`` is canonical ``CN.M`` (``C0`` for origin), composed from
    the slot's round + idx via :func:`candidate_label`. Display sites
    read this field verbatim — no ``idx + 1`` arithmetic anywhere.

    ``live`` (round_result is None) renders samples as compact
    one-liners; ``not live`` emits the full sample dicts (round-complete
    flush). Each candidate's composite_fitness number is paired with the
    active formula and the value-inlined short formula derived from
    *short_formula_template*.
    """
    live = round_result is None
    active_formula = state.get("composite_fitness_formula")
    candidates = round_block.get("candidates") or {}
    round_num = int(round_block.get("round", 0))
    input_candidates: list[dict[str, Any]] = []
    output_candidates: list[dict[str, Any]] = []
    for idx in sorted(candidates.keys()):
        cand = candidates[idx]
        scores = cand.get("scores") or {}
        label = candidate_label(round_num, idx)
        input_candidates.append(
            {
                "idx": idx,
                "label": label,
                "changes_description": (
                    cand.get("changes_description") or scores.get("changes_description") or ""
                ),
                "pp_override": cand.get("pp_override"),
            }
        )
        cand_evaluators = dict(scores.get("evaluators") or {})
        stats: dict[str, Any] = {
            "accuracy": scores.get("accuracy"),
            "composite_fitness": scores.get("composite_fitness"),
            "composite_fitness_formula": active_formula,
            # Per-candidate value-inlined short formula. The legend
            # for short codes (``acc``, ``H``, ``lat``, ``R``,
            # ``pc``) lives in ``docs/operations/improvement-tracking.md``.
            "composite_fitness_formula_short": inline_short_formula_values(
                short_formula_template, cand_evaluators
            ),
            "hits": scores.get("hits"),
            "total": scores.get("total"),
            "invalid": scores.get("invalid", False),
            "validation_failures": scores.get("validation_failures") or [],
        }
        samples = cand.get("samples") or []
        output_candidates.append(
            {
                "idx": idx,
                "label": label,
                "stats": stats,
                "samples": [fmt_sample_line(s) for s in samples] if live else list(samples),
            }
        )
    return {
        "input": {"candidates": input_candidates},
        "output": {"candidates": output_candidates},
    }


__all__ = ["build_l1_score_block"]
