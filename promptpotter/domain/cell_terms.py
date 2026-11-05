"""A per-cell term a criterion can name: the core cell's, or a connector's own vocabulary."""

from __future__ import annotations

from typing import Literal

from promptpotter.domain.scoring import DialKind
from promptpotter.domain.strict_model import FrozenModel


class CellTerm(FrozenModel):
    name: str
    direction: Literal["high", "low"]
    description: str
    # ``None`` reaches a criterion through a typed expression only.
    dial: DialKind | None = None
    primary: bool = False


# The CORE cell's: every connector's cells answer these. A connector's own terms are its
# declaration (`Connector.cell_terms`), answered by the row's `PipelineData.cell_report`.
CELL_TERMS: dict[str, CellTerm] = {
    term.name: term
    for term in (
        CellTerm(
            name="fitness",
            direction="high",
            description="The cell's correctness under the per-sample formula.",
            primary=True,
        ),
        CellTerm(
            name="ground_truth_rank",
            direction="low",
            description="Where the truth landed in the ranking; 1 is the top.",
        ),
        CellTerm(
            name="latency",
            direction="low",
            description="Seconds the cell took when measured; a replay keeps them.",
            dial="anchored",
            primary=True,
        ),
        CellTerm(
            name="unworked",
            direction="low",
            description="Seconds the cell sat blocked (suspend, rate-limit queue).",
            dial="anchored",
        ),
        CellTerm(
            name="cost",
            direction="low",
            description="USD the cell cost.",
            dial="anchored",
            primary=True,
        ),
        CellTerm(
            name="tokens",
            direction="low",
            description="Input plus output tokens the cell spent.",
            dial="anchored",
            primary=True,
        ),
        CellTerm(
            name="target_prompt_chars",
            direction="low",
            description="Characters of the candidate's prompt template.",
            dial="anchored",
            primary=True,
        ),
        CellTerm(
            name="errored",
            direction="low",
            description="1 where the cell errored, else 0.",
            dial="unit",
        ),
        CellTerm(
            name="degraded",
            direction="low",
            description="1 where a pipeline node did not finish cleanly, else 0.",
            dial="unit",
        ),
        CellTerm(
            name="cached",
            direction="high",
            description="1 where the cell was replayed from the archive, else 0.",
            dial="unit",
            primary=True,
        ),
    )
}

# What the formula namespace binds off the cell itself, beside the matchers
# (`compiler.py::CELL_INTRINSIC_NAMES`): a report key named like one is refused at registration.
CELL_BOUND_NAMES: frozenset[str] = frozenset(
    {
        "ground_truth_rank",
        "error",
        "predicted",
        "ground_truth",
        "n_candidates",
        "input_tokens",
        "output_tokens",
    }
)


def misdeclared_connector_terms(terms: tuple[CellTerm, ...]) -> list[str]:
    """Each declared term a dial would misread, as one sentence; asked once, at registration. A
    name another owner binds is refused where every owner is known
    (`scoring/evaluators.py::connector_name_refusals`)."""
    return [
        f"cell term {t.name!r} is anchored but not lower-is-better: a dial reads it against its "
        "origin level"
        for t in terms
        if t.dial == "anchored" and t.direction != "low"
    ]


assert all(term.direction == "low" for term in CELL_TERMS.values() if term.dial == "anchored"), (
    "an anchored dial reads a cost against its origin level, so it is lower-is-better"
)
