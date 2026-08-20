"""What `dashboard.json` SERVES, as opposed to what a round MEASURED.

`RoundResult` is the measurement; these shapes are a narrower frozen projection of it for the
browser, read by exactly one package (`infrastructure/projections/live_dashboard/`). Living
beside the measurement made a display field look like a measurement field — the confusion
`webapp/CLAUDE.md` § Scoring authority exists to prevent. Named `dashboard_rows` and not
`round_summary` because the projection that BUILDS these already owns that name.

`DegradationHealth` stays in `results.py` — `RoundResult.health` is typed on it, so moving it
here would invert this module's one-way import."""

from __future__ import annotations

from pydantic import ConfigDict, Field

from promptpotter.domain.l4.proxies import PanelPrecision
from promptpotter.domain.results import CalibrationModel, DegradationHealth, OverlapReading
from promptpotter.domain.strict_model import StrictModel

__all__ = ["DashboardCandidate", "RoundSummary", "RoundSummaryCandidate"]


class DashboardCandidate(StrictModel):
    """One candidate as `dashboard.json` serves it, in ANY round state — the live rows under
    `current_round.candidates` and the closed rows under `rounds[].candidates` are this shape,
    so a reader takes a whole row rather than merging two shapes field by field.

    The optionals are the facts a candidate genuinely lacks until it finishes;
    `RoundSummaryCandidate` re-declares required exactly what closing guarantees."""

    model_config = ConfigDict(frozen=True)

    # Canonical `C{round}.{n}` — composed at mint, so it exists before any measurement.
    label: str
    # Minted with the searchpoint but only carried on the score report, so a seeded row
    # that has not reached `candidate_scored` has no id to serve yet.
    candidate_id: str | None = None
    accuracy: float | None = None
    composite_fitness: float | None = None
    scored_samples: int = 0
    cached_samples: int = 0
    # Unknown until the first sample announces the walk's length.
    expected_samples: int | None = None
    evaluators: dict[str, float] = Field(default_factory=dict)
    changes_description: str = ""
    partial_reason: str = ""  # "" | "skip" — see ScoredCandidate.partial_reason
    # Difficulty-adjusted Rasch ability + SE (`ScoredCandidate.theta`) — the metric the winner
    # was elected on, so the chart can explain a lower-accuracy winner. `None` outside the fit,
    # which includes every live row: the fit is round-scoped and needs two arms.
    theta: float | None = None
    theta_se: float | None = None
    # The whisker the chart draws (`ScoredCandidate.mean_fitness_ci_lo/hi`), stamped off the
    # candidate's own rows when it finishes (`l1/population.py`) so it is present live and not
    # only at round close. ONE band per candidate from that one writer: a second estimator
    # overriding it makes the whisker come and go by gating rather than by evidence.
    mean_fitness_ci_lo: float | None = None
    mean_fitness_ci_hi: float | None = None
    # The floor this candidate was JUDGED against (`ScoredCandidate.matched_parent_*`): the
    # origin restricted to the samples it actually measured. Served because `accuracy` alone is
    # unreadable under elimination — a PoBB-locked candidate beat something that is NOT the
    # origin's full-set rate. `None` unless the candidate covered the origin's panel, since a
    # prefix rate is set by where PoBB stopped it.
    matched_parent_accuracy: float | None = None
    matched_parent_composite: float | None = None
    # The blocked lift over that floor and its interval — the one number saying whether this
    # candidate beat the origin or the panel merely wobbled, and the one the L4 outer level
    # reads. Same scale as `mean_fitness_ci_*`, sharper on the same rows because pairing cancels
    # the origin's cell-to-cell variation. `None` below two shared cells, which at a one-cell
    # panel is every round and is the honest reading rather than a missing feature.
    matched_parent_lift: float | None = None
    matched_parent_lift_ci_lo: float | None = None
    matched_parent_lift_ci_hi: float | None = None
    # On the BASE, because the election is not a closing act: `elect_round_winner` runs at the
    # end of SCORING, two LLM calls before the round closes, and the live row is the only
    # surface that can say so then. `False` until it lands, and on every row of a round that
    # held none — never a claim that this candidate lost.
    is_winner: bool = False


class RoundSummaryCandidate(DashboardCandidate):
    """A `DashboardCandidate` on a CLOSED round — `dashboard.json::rounds[].candidates`.
    Narrows to what closing guarantees; the field list is inherited, so a field added to the base
    flows to both halves and `_SUMMARY_INCLUDE` keeps picking it up."""

    candidate_id: str
    accuracy: float
    composite_fitness: float
    expected_samples: int
    is_winner: bool


class RoundSummary(StrictModel):
    """Display row for `dashboard.json::rounds[]` — webapp's completed-round source.
    Top-level `accuracy` is what the round MEASURED; `cumulative_theta` is the invariant series."""

    model_config = ConfigDict(frozen=True)

    round: int
    accuracy: float
    composite_fitness: float
    # The cross-round-comparable series: ability on the cycle's fixed δ ruler, subset-invariant
    # where `accuracy`/`composite_fitness` above are subset-relative — under
    # `per_round_resubset` those swing on each fresh draw, reading as a false
    # "great start → decay". The trend/sparkline plot THIS series; the per-round measured
    # number stays on `candidates[]`, badged with its count. Never add a `cumulative_accuracy`
    # beside it: a mean over rows from DIFFERENT configurations fabricates a number no
    # individual scored. Mirrors `RoundResult.cumulative_theta`.
    cumulative_theta: float | None = None
    # And no `cumulative_theta_se` beside it: the interval that IS read is per candidate
    # (`theta_se`), and the round-level SE stays on the ledger and the round document, which is
    # where a reader for it would come from.
    # Mirrors `RoundResult.calibration_model` — the model the webapp's ability popover reads.
    calibration_model: CalibrationModel | None = None
    # The round's verdict and the evidence it rests on — the two bits that decide how long the
    # cycle lives. `improved` moves the stall counter and the life bank; `electable_count`
    # decides whether the bank moves AT ALL, since a round no candidate reached measured
    # nothing about the search (`EscalationFSM._bank_round`). Both engine-only would let a
    # campaign visibly climb while its bank drained. Round 0 holds no election ⇒ both unset.
    improved: bool | None = None
    electable_count: int | None = None
    # WHY it ended that way, in the numbers it was decided on — mirrors
    # ``RoundResult.verdict_reason``. `improved` alone says a round held and cannot say which arm
    # came closest or how far short, which is the question a browser reader actually has; this is
    # that answer's only route out of the engine. Round 0 holds no election ⇒ unset.
    verdict_reason: str | None = None
    candidates: list[RoundSummaryCandidate] = Field(default_factory=list)
    # Sample ids in measurement order; the longest candidate sequence carries the full series,
    # since PoBB truncates losers rather than the queue mechanism itself.
    selection: list[int] = Field(default_factory=list)
    # Round-close degradation verdict, origin included. ``None`` only when the round measured
    # zero samples. Webapp/CLI render it; never recompute.
    health: DegradationHealth | None = None
    # The adopted line read on ONE shared set of cells — C0 and every winner since, on the same
    # exam. `accuracy` above and this are not rivals: that one is the round's own subset, this one
    # is the only basis two rounds can be differenced on. `None` until the line has a second
    # member. Mirrors `RoundResult.overlap`; the rows behind it stay on the round
    # document, since a browser reading them would be reading a quarantine.
    overlap: OverlapReading | None = None
    # How sharply the L4 panel's cells were measured against how far apart they landed — the
    # monitoring read saying which lever the round's spread calls for. ``None`` on any non-L4
    # round: an ordinary sample is graded and carries no error bar to decompose. The VERDICT is
    # not here; it rides `candidates[].matched_parent_lift*` like every other level's.
    panel_precision: PanelPrecision | None = None
