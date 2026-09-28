"""Behaviour descriptors: an arm's rows read as the point a quality-diversity archive files it
under — input-side off the scored prompt, output-side off its per-cell profile."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal, assert_never

from promptpotter.shared.errors import is_error_result
from promptpotter.shared.hashing import shapes_optimizer_prompt

shapes_optimizer_prompt(__name__)

__all__ = ["DescriptorFeature", "behaviour_descriptor", "cell_objectives"]

DescriptorFeature = Literal["target_prompt_chars", "cell_objectives"]


def cell_objectives(rows: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    """Each graded cell's campaign objective, by sample key; errored and unscored cells are absent."""
    return {
        str(r["sample_key"]): float(r["objective"])
        for r in rows
        if not is_error_result(r) and "objective" in r
    }


def behaviour_descriptor(
    rows: Sequence[Mapping[str, Any]],
    cells: Sequence[str],
    features: Sequence[DescriptorFeature],
) -> list[float] | None:
    """``None`` where the rows cannot place the arm: a cell of ``cells`` ungraded, or no length."""
    graded = cell_objectives(rows)
    out: list[float] = []
    for feature in features:
        match feature:
            case "target_prompt_chars":
                lengths = [
                    pd["target_prompt_chars"]
                    for r in rows
                    if (pd := r["pipeline_data"]) and pd.get("target_prompt_chars") is not None
                ]
                if not lengths:
                    return None
                out.append(float(lengths[0]))
            case "cell_objectives":
                if any(c not in graded for c in cells):
                    return None
                out.extend(graded[c] for c in cells)
            case _:
                assert_never(feature)
    return out
