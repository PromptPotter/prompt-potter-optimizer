"""Behaviour descriptors: an arm's rows read as the point a quality-diversity archive files it
under — input-side off the scored prompt, output-side off its per-cell profile."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal, assert_never

from promptpotter.domain.scoring import is_graded
from promptpotter.shared.hashing import shapes_optimizer_prompt

shapes_optimizer_prompt(__name__)

__all__ = ["DescriptorFeature", "behaviour_descriptor", "cell_objectives", "prompt_chars"]

DescriptorFeature = Literal["target_prompt_chars", "cell_objectives"]


def prompt_chars(rows: Sequence[Mapping[str, Any]]) -> int | None:
    """One arm's scored prompt length, off whichever row carries it: a charged error is graded
    but banks no ``pipeline_data``. ``None`` where the pipeline renders no prompt node."""
    return next(
        (
            int(pd["target_prompt_chars"])
            for r in rows
            if (pd := r.get("pipeline_data")) and pd.get("target_prompt_chars") is not None
        ),
        None,
    )


def cell_objectives(rows: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    """Each graded cell's campaign objective, by sample key; errored and unscored cells are absent."""
    return {str(r["sample_key"]): float(r["objective"]) for r in rows if is_graded(r)}


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
                if (chars := prompt_chars(rows)) is None:
                    return None
                out.append(float(chars))
            case "cell_objectives":
                if any(c not in graded for c in cells):
                    return None
                out.extend(graded[c] for c in cells)
            case _:
                assert_never(feature)
    return out
