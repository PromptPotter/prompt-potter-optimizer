"""Composite-render primitive — formula text + named evaluator pairs.

Pinned invariants:

1. ``render_composite_block`` is a 3-line block when formula + evaluators
   are present: composite anchor, formula, named-evaluator values.
2. ``extract_evaluator_names`` filters out builtins, bare numbers, and
   keywords; only names present in the *available* set survive.
3. ``render_composite_oneliner`` carries Δ vs baseline so deep rounds
   still show the trajectory anchor.
4. Short-name mode renders short codes (``acc``, ``err``, ...) instead
   of full names so the round-level frame fits 70-char width.
"""

from __future__ import annotations

from promptpotter.presentation.views.composite_render import (
    extract_evaluator_names,
    render_composite_block,
    render_composite_oneliner,
)


def test_block_is_three_lines_with_formula_and_named_evaluators() -> None:
    formula = "0.65 * accuracy + 0.10 * latency_norm + 0.05 * prompt_compactness"
    evaluators = {
        "accuracy": 0.5,
        "latency_norm": 0.985,
        "prompt_compactness": 0.998,
        "error_rate": 0.0,  # not in formula → must NOT appear
    }
    block = render_composite_block(0.5567, evaluators, formula, baseline=0.5012)

    # Three lines: composite anchor, formula, named values.
    assert len(block) == 3
    assert "composite = 0.5567" in block[0]
    assert "baseline=0.5012" in block[0]
    assert "Δ" in block[0]  # delta marker reflects "origin way back"

    assert formula in block[1]

    values_line = block[2]
    assert "accuracy=0.500" in values_line
    assert "latency_norm=0.985" in values_line
    assert "prompt_compactness=0.998" in values_line
    # Names not in formula must NOT appear.
    assert "error_rate" not in values_line


def test_block_short_names_emit_codes_for_all_evaluators() -> None:
    """In short-names mode, every evaluator from the dict surfaces with its
    registry short code. The formula text already names the structure;
    the values line lists every input.
    """
    formula = "0.65*acc + 0.15*H + 0.10*lat + 0.05*pc"
    evaluators = {
        "accuracy": 0.667,
        "latency_norm": 0.965,
        "prompt_compactness": 0.812,
    }
    block = render_composite_block(
        0.6042, evaluators, formula, baseline=0.5012, use_short_names=True
    )
    assert len(block) == 3
    values_line = block[2]
    # Short codes appear, full names do not.
    assert "acc=" in values_line
    assert "lat=" in values_line
    assert "pc=" in values_line
    assert "accuracy=" not in values_line


def test_oneliner_carries_delta_vs_baseline() -> None:
    line = render_composite_oneliner(0.6042, baseline=0.5012)
    assert "composite=0.6042" in line
    assert "Δ" in line
    assert "baseline 0.5012" in line

    bare = render_composite_oneliner(0.6042, baseline=None)
    assert bare == "composite=0.6042"
    assert "Δ" not in bare


def test_block_falls_back_when_formula_missing() -> None:
    block = render_composite_block(0.42, {"accuracy": 0.5}, formula=None)
    assert block == ["composite = 0.4200  (formula unavailable)"]


def test_extract_names_filters_builtins_and_unknowns() -> None:
    formula = "0.5 * accuracy + log(1.0 + latency_norm) + max(0.0, recall) - 1.0"
    available = {"accuracy", "latency_norm", "recall", "error_rate"}
    names = extract_evaluator_names(formula, available)

    # log + max are builtins → excluded. error_rate is in available but
    # not in formula → excluded. Bare numbers (0.5, 1.0, 0.0) → excluded
    # because they aren't valid Python identifiers.
    assert names == ["accuracy", "latency_norm", "recall"]
