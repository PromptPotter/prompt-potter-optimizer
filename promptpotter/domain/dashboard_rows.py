"""What `dashboard.json` SERVES, as opposed to what a round MEASURED.

`RoundResult` is the measurement; these shapes are a narrower frozen projection of it for the
browser, read by exactly one package (`infrastructure/projections/live_dashboard/`). Living
beside the measurement made a display field look like a measurement field — the confusion
`webapp/CLAUDE.md` § Scoring authority exists to prevent. Named `dashboard_rows` and not
`round_summary` because the projection that BUILDS these already owns that name.

`DegradationHealth` stays in `results.py` — `RoundResult.health` is typed on it, so moving it
here would invert this module's one-way import."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import ConfigDict, Field

from promptpotter.domain.bench import BenchReading
from promptpotter.domain.l4.proxies import PanelPrecision
from promptpotter.domain.results import (
    ArmOutcome,
    DegradationHealth,
    OptimizerFact,
    OverlapReading,
    VerifyReading,
)
from promptpotter.domain.ruler import AbilityReading, ThetaCaveat
from promptpotter.domain.scoring import is_hit, is_unscored
from promptpotter.domain.strict_model import StrictModel
from promptpotter.domain.wounds import ValidationFailure

__all__ = [
    "DashboardCandidate",
    "DashboardSample",
    "LiveCandidate",
    "OptimizerLimit",
    "RoundSummary",
    "RoundSummaryCandidate",
    "RunStanding",
    "SampleStatus",
    "sample_status",
]

#: The tape's four marks. ERR and UNSC are each a state of their OWN, not a bad MISS — neither row
#: was graded, so reading an absent fitness as one reports a backend fault (ERR) or the active
#: formula's own silence (UNSC) as a wrong answer. UNSC is the row the backend ANSWERED and the
#: formula could not read, which is why it is not an error: the measurement is worth keeping and a
#: re-grade recovers it (`domain/scoring.py::is_unscored`).
SampleStatus = Literal["HIT", "MISS", "ERR", "UNSC"]


def sample_status(row: Mapping[str, Any]) -> SampleStatus:
    """The ONE ladder from a measured row to its mark — the live tape (`blocks.py::sample_row`) and
    the served cells (`infrastructure/store/cell_queries.py`) both ask it, so two readouts of one
    row cannot disagree about whether it was ever graded. `ERR` and `UNSC` are asked BEFORE the
    grade: neither row was graded, so an absent fitness through `is_hit` would report a backend
    fault — or the formula's own silence — as a wrong answer."""
    if row.get("error"):
        return "ERR"
    if is_unscored(row):
        return "UNSC"
    return "HIT" if is_hit(row.get("fitness")) else "MISS"


class DashboardSample(StrictModel):
    """One scored sample as `dashboard.json` serves it — the rule `DashboardCandidate` states,
    applied to samples: ONE shape whatever the round's state.

    Display-TRIMMED at the producer, because this rides a file polled every couple of seconds
    and the untrimmed measurement already sits in `rounds/round_NNNN.json::results`."""

    qi: int = Field(description="Iteration position within the candidate's walk — the #000 column.")
    sample_id: int | None = Field(
        default=None,
        description="Dataset sample id, which diverges from qi once the hard-sample sorter "
        "drives the order. Null where the row carries none.",
    )
    status: SampleStatus = Field(description="The grading verdict.")
    fitness: float | None = Field(
        default=None,
        description="The graded per-cell score `status` is the verdict OF — the same number "
        "`CellRow.fitness` carries, so a live cell shades a partial grade `status` rounds to "
        "HIT or MISS. Null on an errored row, which was never graded.",
    )
    terminal_node: str = Field(
        default="", description="Pipeline node the row terminated at; the tape badges it."
    )
    cached: bool = Field(
        default=False,
        description="Measurement reused from a prior identical searchpoint, not a fresh call.",
    )
    time_s: float | None = Field(
        default=None,
        description="Recorded elapsed seconds. Null where the row never reached the pipeline — "
        "distinct from a cached replay's real 0.0.",
    )
    cost_s: float | None = Field(
        default=None,
        description="Seconds producing this row COST, summed off `step_timings` — the half that "
        "survives the cache stamp. A replay occupies no clock, so `time_s` is 0.0 and this is what "
        "the cell took when it was measured; on a fresh row the two agree. Null where the row "
        "recorded no per-node timing.",
    )
    predicted: str = Field(
        default="",
        description="Prediction, trimmed for display. EMPTY on a verifier-graded row (see "
        "ground_truth) — the pair is both halves of a comparison nobody made there.",
    )
    ground_truth: str = Field(
        default="",
        description="Ground truth, trimmed for display. EMPTY declares a VERIFIER-GRADED row: "
        "the backend answered with a number its own verifier decided (a Harbor task's "
        "tests/test.sh, L4's outer proxies), so there is no truth for `predicted` to match and "
        "`status` carries the whole verdict. A client tells that from a broken extraction by the "
        "pair: both empty is verifier-graded, `NO_RESULT` beside a real truth is extraction.",
    )
    query: str = Field(default="", description="Query, trimmed for display.")
    input_tokens: int | None = Field(default=None)
    output_tokens: int | None = Field(default=None)
    cache_read_tokens: int | None = Field(
        default=None,
        description="How many of `input_tokens` the PROVIDER served off its own prefix cache — a "
        "SUBSET, never an addition, and distinct from `cached`, which says OUR archive answered. "
        "Null where no breakdown was reported; 0 where one was and there was no hit.",
    )


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
    # The archive run the candidate's cells land in (`ScoredCandidate.run_id`) — known from its
    # FIRST sample, unlike `candidate_id`, because the walk mints it before measuring. With a
    # sample's `sample_id` it addresses one cell. `None` before any sample and on an invalid row.
    run_id: str | None = None
    accuracy: float | None = None
    composite_fitness: float | None = None
    # How the arm's walk ended (`ScoredCandidate.outcome`), `None` until it is decided. Served
    # because an `invalid` row's scores are SYNTHETIC, and a broken arm is not an eliminated one.
    outcome: ArmOutcome | None = None
    scored_samples: int = 0
    cached_samples: int = 0
    # What measuring this searchpoint CONSUMED — the served twin of ``ScoredCandidate``'s three,
    # which own the prose. Same fold, same exclusions, same ``None``-is-not-0 reading.
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    # Unknown until the first sample announces the walk's length.
    expected_samples: int | None = None
    evaluators: dict[str, float] = Field(default_factory=dict)
    changes_description: str = ""
    # Difficulty-adjusted Rasch ability + SE (`ScoredCandidate.theta`) — the metric the winner
    # was elected on, so the chart can explain a lower-accuracy winner. `None` outside the fit,
    # which is round-scoped and needs two arms: every row is null until the ELECTION stamps it,
    # and none is after (`ElectionRecord.fit`). Not fittable sooner — `calibrate_ruler` extends
    # the δ scale onto the round's cells first, and `fit_theta_given_delta` skips one it does not
    # carry rather than defaulting it to a position on the scale.
    theta: float | None = None
    theta_se: float | None = None
    # This candidate read on the held-out bench set, a second reading beside the search cells
    # above. `None` unless a bench pass graded it: the origin, and each selection the bench read.
    bench: BenchReading | None = None
    # This candidate's last `verify` pass — re-scored on search cells it had never met — as that
    # pass read under the scorer it ran with. WIRE-ONLY: whichever process ran the pass banked it
    # on the cycle's ledger, so the serving seam places it (`overlay_verify`) and the runner's
    # file never holds a copy to go stale.
    verify: VerifyReading | None = Field(default=None, exclude=True)
    # Why the θ above is NOT this arm's ability (`ScoredCandidate.theta_caveat`) — `FLOOR_PINNED`
    # or `UNMEASURED_DELTA`, since the rest are facts about the round's scale and ride
    # `RoundResult.ability` once instead of being copied onto every row. Served rather than
    # derived in the browser: the rows a client would test are the fat per-sample arrays the
    # candidate row exists to avoid shipping.
    theta_caveat: ThetaCaveat | None = None
    # The whisker the chart draws (`ScoredCandidate.mean_fitness_ci_lo/hi`), folded off the
    # candidate's own rows by the scoring gateway (`search_point_scorer::_composite`) on every
    # sample, so it widens with the accuracy bar instead of arriving whole at the end. ONE band per
    # candidate from that one writer: a second estimator overriding it makes the whisker come and
    # go by gating rather than by evidence.
    mean_fitness_ci_lo: float | None = None
    mean_fitness_ci_hi: float | None = None
    # The floor this candidate was JUDGED against (`ScoredCandidate.reference_*`): the
    # origin restricted to the samples it actually measured. Served because `accuracy` alone is
    # unreadable under elimination — a PoBB-locked candidate beat something that is NOT the
    # origin's full-set rate. `None` unless the candidate covered the origin's panel, since a
    # prefix rate is set by where PoBB stopped it.
    reference_accuracy: float | None = None
    reference_composite: float | None = None
    # The blocked lift over that floor and its interval — the one number saying whether this
    # candidate beat the origin or the panel merely wobbled, and the one the L4 outer level
    # reads. Same scale as `mean_fitness_ci_*`, sharper on the same rows because pairing cancels
    # the origin's cell-to-cell variation. `None` below two shared cells, which at a one-cell
    # panel is every round and is the honest reading rather than a missing feature.
    reference_lift: float | None = None
    reference_lift_ci_lo: float | None = None
    reference_lift_ci_hi: float | None = None
    # On the BASE, because the election is not a closing act: `elect_round_winner` runs at the
    # end of SCORING, two LLM calls before the round closes, and the live row is the only
    # surface that can say so then. `False` until it lands, and on every row of a round that
    # held none — never a claim that this candidate lost.
    is_selected: bool = False


class LiveCandidate(DashboardCandidate):
    """A `DashboardCandidate` in the round in flight — `dashboard.json::current_round.candidates`.
    The only carrier, until the round file lands, of the searchpoint it runs, its sample tape and
    why validation rejected it."""

    # The evolved prompt (`OptSearchPoint.prompt_field_dict()` shape) and the config-only
    # resolved params — the half a steered fork seeds from, as `candidate_scores[]` carries it.
    prompt_fields: dict[str, Any] | None = None
    resolved_pipeline_params: dict[str, Any] | None = None
    pipeline_overlay: dict[str, Any] | None = None
    samples: list[DashboardSample] = Field(default_factory=list)
    validation_failures: list[ValidationFailure] = Field(default_factory=list)
    # The composite's short formula with this candidate's own evaluator values inlined.
    composite_fitness_formula_short: str | None = None


class RoundSummaryCandidate(DashboardCandidate):
    """A `DashboardCandidate` on a CLOSED round — `dashboard.json::rounds[].candidates`.
    Narrows to what closing guarantees; the field list is inherited, so a field added to the base
    flows to both halves and `_SUMMARY_INCLUDE` keeps picking it up."""

    candidate_id: str
    accuracy: float | None
    composite_fitness: float
    outcome: ArmOutcome
    expected_samples: int
    is_selected: bool
    # The arm this round's READING is taken off, and the same one `RoundSummary.panel_precision`
    # is measured on (`round_summary.py::_leading_arm`). Distinct from `is_selected`, which says the
    # election CROWNED it: a held round crowns nothing and its reading still comes off one arm.
    # Served because the tie-break a reader would reach for — argmax on `composite_fitness` —
    # cannot apply `is_electable`, so it hangs the lift interval off a collapsed arm the election
    # refused. `False` on every row of round 0, which holds no election.
    is_leading: bool = False


class OptimizerLimit(StrictModel):
    """One knob an optimizer declares as bounding its run, which a fork may reconcile."""

    model_config = ConfigDict(frozen=True)

    node: str
    knob: str
    label: str
    # ``None`` where the knob is declared off.
    value: float | None
    # A whole count; otherwise a fraction in [0, 1].
    integer: bool


class RunStanding(StrictModel):
    """Where an optimizer stands after a round, whichever optimizer runs: the rounds since its
    selection last advanced, and the stalls it may still absorb out of its ceiling."""

    model_config = ConfigDict(frozen=True)

    rounds_without_advance: int
    # ``None`` where the optimizer banks no stalls; the run then ends on its other limits.
    stalls_left: int | None
    stalls_left_cap: int | None


class RoundSummary(StrictModel):
    """Display row for `dashboard.json::rounds[]` — webapp's completed-round source.
    Top-level `accuracy` is what the round MEASURED; `ability` is the invariant series."""

    model_config = ConfigDict(frozen=True)

    round: int
    # ``None`` where the round measured nothing readable, so the chart draws a GAP rather
    # than a point at zero (`ScoredCandidate.accuracy`).
    accuracy: float | None
    composite_fitness: float
    # The rows `accuracy` is a mean over — the winner's, or on a held round the parent's on this
    # panel. Mirrors `RoundResult.total`; no arm's own count stands in for it.
    total: int
    # The cross-round-comparable series and the scale that makes it one: ability on the cycle's
    # fixed δ ruler, subset-invariant where `accuracy`/`composite_fitness` above are
    # subset-relative — under `per_round_resubset` those swing on each fresh draw, reading as a
    # false "great start → decay". The trend/sparkline plot THIS series, dropping any point whose
    # ruler differs; the per-round measured number stays on `candidates[]`, badged with its count.
    # Never add a `cumulative_accuracy` beside it: a mean over rows from DIFFERENT configurations
    # fabricates a number no individual scored. Mirrors `RoundResult.ability` where the round's
    # selector stamps θ, and is ``None`` everywhere else.
    ability: AbilityReading | None = None
    # The cycle's best on shared cells as it stood when this round closed, a fork's seeded rounds
    # included — the BEST line, served so no surface folds its own.
    best_so_far: float | None = None
    # The bench's grade of the selection this round declared — the origin at round 0, the final
    # pick, and under `bench_each_round` every round that selected. ``None`` where none graded it.
    bench: BenchReading | None = None
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
    # Did this round advance the best-so-far line — mirrors ``RoundResult.separable``
    # (`runner/round.py::_separability`). THREE-state: ``None`` is "the line carries no
    # interval", which is not inconclusive but nothing to be conclusive about, and a reader
    # collapsing it onto ``False`` reports an unasked question as a negative answer. An arm's own
    # bracket over its parent cannot answer this, so no surface may stand in for it with one.
    separable: bool | None = None
    # Mirrors `RoundResult.stamps_theta`.
    stamps_theta: bool = False
    candidates: list[RoundSummaryCandidate] = Field(default_factory=list)
    # Sample ids in measurement order; the longest candidate sequence carries the full series,
    # since PoBB truncates losers rather than the queue mechanism itself.
    selection: list[int] = Field(default_factory=list)
    # Round-close degradation verdict, origin included. ``None`` only when the round measured
    # zero samples. Webapp/CLI render it; never recompute.
    health: DegradationHealth | None = None
    # The best-so-far line read on ONE shared set of cells — C0 and each new best since, on one
    # exam. `accuracy` above and this are not rivals: that one is the round's own subset, this one
    # is the only basis two rounds can be differenced on. `None` until the line has a second
    # member. Mirrors `RoundResult.overlap`; the rows behind it stay on the round
    # document, since a browser reading them would be reading a quarantine.
    overlap: OverlapReading | None = None
    # How sharply the L4 panel's cells were measured against how far apart they landed — the
    # monitoring read saying which lever the round's spread calls for. ``None`` on any non-L4
    # round: an ordinary sample is graded and carries no error bar to decompose. The VERDICT is
    # not here; it rides `candidates[].reference_lift*` like every other level's.
    panel_precision: PanelPrecision | None = None
    # Mirrors `RoundResult.optimizer_facts`: the optimizer's own words about this round.
    optimizer_facts: list[OptimizerFact] = Field(default_factory=list)
