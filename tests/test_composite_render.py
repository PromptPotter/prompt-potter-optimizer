"""Composite-render primitive — formula text + named evaluator pairs.

Two invariants:

1. ``render_composite_block`` includes the formula text verbatim and every
   named evaluator that appears in both *formula* and *evaluators*.
2. ``extract_evaluator_names`` filters out builtins and bare numbers; only
   names present in the *available* set survive.
"""

from __future__ import annotations

from promptpotter.presentation.views.composite_render import (
    extract_evaluator_names,
    render_composite_block,
)


def test_block_includes_formula_and_named_evaluators() -> None:
    formula = "0.65 * accuracy + 0.10 * latency_norm + 0.05 * prompt_compactness"
    evaluators = {
        "accuracy": 0.5,
        "latency_norm": 0.985,
        "prompt_compactness": 0.998,
        "error_rate": 0.0,  # not in formula → must NOT appear
    }
    block = "\n".join(render_composite_block(0.5567, evaluators, formula))

    assert "composite = 0.5567" in block
    # Formula text must survive verbatim (allowing for word-wrap; the
    # original substring may be split, but each whitespace-separated token
    # appears in order).
    for token in formula.split():
        assert token in block

    # Every named evaluator the formula references must appear, with its value.
    assert "accuracy=0.500" in block
    assert "latency_norm=0.985" in block
    assert "prompt_compactness=0.998" in block
    # Names not in the formula must NOT appear.
    assert "error_rate" not in block


def test_block_falls_back_when_formula_missing() -> None:
    block = render_composite_block(0.42, {"accuracy": 0.5}, formula=None)
    assert block == ["composite = 0.4200  (formula unavailable)"]
    block_empty = render_composite_block(0.42, {"accuracy": 0.5}, formula="")
    assert block_empty == ["composite = 0.4200  (formula unavailable)"]


def test_extract_names_filters_builtins_and_unknowns() -> None:
    formula = "0.5 * accuracy + log(1.0 + latency_norm) + max(0.0, recall) - 1.0"
    available = {"accuracy", "latency_norm", "recall", "error_rate"}
    names = extract_evaluator_names(formula, available)

    # log + max are builtins → excluded. error_rate is in available but
    # not in formula → excluded. Bare numbers (0.5, 1.0, 0.0) → excluded
    # because they aren't valid Python identifiers.
    assert names == ["accuracy", "latency_norm", "recall"]
