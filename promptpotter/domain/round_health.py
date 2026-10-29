from __future__ import annotations

from typing import Literal

from pydantic import Field

from promptpotter.domain.strict_model import FrozenModel

HealthGrade = Literal["healthy", "degraded", "critical", "unmeasured"]

HealthCause = Literal[
    "origin_unmeasured",
    # The origin measured SOME cells: `origin_unmeasured` is none, `holed` is any round's rate.
    "origin_incomplete",
    "structural",
    "unscoreable",
    "holed",
    "evidence_starved",
    "structural_untested",
    "persistent",
    "degraded",
]


class DegradationHealth(FrozenModel):
    """A round's degradation verdict from the backend's warning stamps; it never stops the run."""

    grade: HealthGrade
    # `None` on `healthy` and on `unmeasured`, a non-origin round that measured nothing.
    cause: HealthCause | None = None
    samples: int
    structural_count: int
    transient_count: int
    # Attempted cells that reported nothing (re-run).
    hole_count: int = 0
    # Panel cells never SENT: no row, and outside ``samples``.
    not_attempted: int = 0
    # Sent and measured, no verdict (the formula named a term the row lacks): in ``samples``, no rate.
    unscored: int = 0
    # Answers cut at the output-token ceiling, scored as written. REPORTED, never graded.
    output_ceiling_count: int = 0
    # ``None`` where the answer space makes collapse meaningless. REPORTED, never graded.
    answer_modal_share: float | None = None
    degraded_rate: float
    consecutive_degraded_rounds: int
    prior_clean_rounds: int
    dominant_node: str | None = None
    node_failure_rates: dict[str, float] = Field(default_factory=dict)
    node_warnings: dict[str, list[str]] = Field(default_factory=dict)
    suggested_action: str | None = None
    last_error: str | None = None
