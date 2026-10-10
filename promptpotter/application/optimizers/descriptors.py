from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Literal, assert_never

from promptpotter.domain.scoring import ROW_GRADES, CellSheet, GradedCell
from promptpotter.shared.hashing import shapes_optimizer_prompt

shapes_optimizer_prompt(__name__)

__all__ = ["DescriptorFeature", "behaviour_descriptor", "cell_objectives", "prompt_chars"]

DescriptorFeature = Literal["target_prompt_chars", "cell_objectives"]


def prompt_chars(rows: Iterable[GradedCell]) -> int | None:
    """Off whichever row carries it: a charged error banks none. ``None`` where no prompt node renders."""
    return next(
        (chars for cell in rows if (chars := cell.facts.pipeline.target_prompt_chars) is not None),
        None,
    )


def cell_objectives(rows: Iterable[GradedCell]) -> dict[str, float]:
    objective = ROW_GRADES["objective"]
    return {cell.key: float(objective.read(cell)) for cell in rows if cell.scored}


def behaviour_descriptor(
    rows: CellSheet,
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
