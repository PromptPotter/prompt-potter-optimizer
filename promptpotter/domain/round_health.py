from __future__ import annotations

from typing import Literal, NamedTuple, get_args

from pydantic import Field

from promptpotter.domain.said import Tone
from promptpotter.domain.strict_model import FrozenModel
from promptpotter.shared.worded import Worded

HealthGrade = Literal["healthy", "degraded", "critical", "unmeasured"]


class HealthGradeInfo(NamedTuple):
    tone: Tone


HEALTH_GRADE_INFO: Worded[HealthGrade, HealthGradeInfo] = Worded(
    {
        "healthy": HealthGradeInfo("success"),
        "degraded": HealthGradeInfo("warn"),
        "critical": HealthGradeInfo("danger"),
        "unmeasured": HealthGradeInfo("quiet"),
    }
)

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
    # Over the degraded bar: `degraded` with a structural failure among them, `transient` with none.
    "degraded",
    "transient",
]


class HealthCauseInfo(NamedTuple):
    """``title`` follows the round's name; ``remedy`` names the decisions the surfaces offer."""

    title: str
    remedy: str


HEALTH_CAUSE_INFO: Worded[HealthCause, HealthCauseInfo] = Worded(
    {
        "origin_unmeasured": HealthCauseInfo(
            "not measured",
            "Re-score the origin. If it stops in the same place, read the error on the last cell that "
            "was measured — the cause is upstream of the prompt. A cell measured but not graded is "
            "the formula's: fix the formula and re-read that cell, never re-buy it.",
        ),
        "origin_incomplete": HealthCauseInfo(
            "cells missing",
            "Re-score the origin before spending a round on top of it — errored cells are never "
            "served from cache.",
        ),
        "structural": HealthCauseInfo(
            "failing structurally",
            "Consider aborting and fixing the config before spending more rounds.",
        ),
        "unscoreable": HealthCauseInfo(
            "no extractable answer",
            "Fix the prompt's answer_format so the model commits one parseable label (or the "
            "extraction contract), then re-score — don't optimize against it.",
        ),
        "holed": HealthCauseInfo(
            "cells returned nothing",
            "Re-measure: errored cells are never served from cache. If they keep failing, the cause "
            "is upstream of the prompt — read the row's error before changing anything.",
        ),
        "evidence_starved": HealthCauseInfo(
            "an enricher is starved",
            "Restore the backend (its quota) and re-measure — don't burn rounds chasing it.",
        ),
        "structural_untested": HealthCauseInfo(
            "failing structurally, never clean",
            "Consider aborting and fixing the config before spending more rounds.",
        ),
        "persistent": HealthCauseInfo(
            "degraded round after round",
            "Consider aborting and fixing the config.",
        ),
        "degraded": HealthCauseInfo(
            "degraded",
            "Read the node's error before trusting this round's numbers; no action is needed if the "
            "next round comes back clean.",
        ),
        "transient": HealthCauseInfo(
            "degraded by transient failures",
            "No action is needed; if the next round degrades too, read the node's error.",
        ),
    }
)
assert HEALTH_CAUSE_INFO.keys() == set(get_args(HealthCause))


class HealthReading(FrozenModel):
    """A graded cause in words, read off the facts its round stamped (`results_health.py::health_reading`)."""

    cause: HealthCause
    title: str = Field(description="The round and its cause in one line.")
    advice: str = Field(description="What the counts say, then the cause's remedy.")


class DegradationHealth(FrozenModel):
    """A round's degradation verdict from the backend's warning stamps; it never stops the run.

    Facts only: its words are read off them, never stamped (`results_health.py::health_reading`)."""

    grade: HealthGrade
    # `None` on `healthy` and on `unmeasured`, a non-origin round that measured nothing.
    cause: HealthCause | None = None
    samples: int
    structural_count: int
    transient_count: int
    # Attempted cells that reported nothing (re-run).
    hole_count: int
    # Attempted cells whose answer carried no extractable label: in ``samples``.
    no_result_count: int
    # Panel cells never SENT: no row, and outside ``samples``.
    not_attempted: int
    # Sent and measured, no verdict (the formula named a term the row lacks): in ``samples``, no rate.
    unscored: int
    # Answers cut at the output-token ceiling, scored as written. REPORTED, never graded.
    output_ceiling_count: int
    # ``None`` where the answer space makes collapse meaningless. REPORTED, never graded.
    answer_modal_share: float | None = None
    degraded_rate: float
    consecutive_degraded_rounds: int
    prior_clean_rounds: int
    dominant_node: str | None = None
    node_failure_rates: dict[str, float] = Field(default_factory=dict)
    node_warnings: dict[str, list[str]] = Field(default_factory=dict)
    last_error: str | None = None

    @property
    def scored_cells(self) -> int:
        """Sent cells that answered a label and carry a verdict: a ``NO_RESULT`` row (a schema
        with no answer field, an output failure) is as unscoreable as a hole."""
        return max(0, self.samples - self.hole_count - self.no_result_count - self.unscored)
