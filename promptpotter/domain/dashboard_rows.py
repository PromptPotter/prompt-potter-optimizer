"""What `dashboard.json` SERVES, as opposed to what a round MEASURED.

`RoundResult` is the measurement — 62 modules import it. These two shapes are read by exactly
one package, `infrastructure/projections/live_dashboard/`, and exist to be a narrower, frozen
projection of it for the browser. Keeping them beside the measurement made a display field look
like a measurement field, which is the confusion `webapp/CLAUDE.md` § Scoring authority exists
to prevent.

Named `dashboard_rows` and not `round_summary`: the projection that BUILDS these already owns
that name (`live_dashboard/round_summary.py`), and two files a layer apart under one noun is
the collision root `CLAUDE.md` § STOP names.

`DegradationHealth` deliberately stays in `results.py` — `RoundResult.health` is typed on it,
so moving it here would invert this module's one-way import."""

from __future__ import annotations

from pydantic import ConfigDict, Field

from promptpotter.domain.l4.verdict import OuterVerdict
from promptpotter.domain.results import CalibrationModel, DegradationHealth
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
    # Composite-fitness CI (`ScoredCandidate.composite_ci_lo/hi`) — the whisker the chart draws,
    # stamped off the candidate's own rows when it finishes (`l1/population.py`), so it is
    # present live and not only at round close. ONE band per candidate, from that one writer: a
    # second estimator overriding it for the arms an election happened to reach is what made the
    # whisker come and go by gating rather than by evidence.
    composite_ci_lo: float | None = None
    composite_ci_hi: float | None = None
    # The floor this candidate was JUDGED against (`ScoredCandidate.matched_origin_*`): the
    # origin restricted to the samples this candidate actually measured. Served because
    # `accuracy` alone is unreadable under elimination — a PoBB-locked candidate ran 8 of 20,
    # so what it beat is NOT the origin's full-set rate, and the terminal has always printed
    # "was 42%" beside the verdict while the webapp printed the verdict alone. `None` unless the
    # candidate covered the origin's panel — a prefix rate is set by where PoBB stopped it.
    matched_origin_accuracy: float | None = None
    matched_origin_composite: float | None = None
    # The round's elected winner. On the BASE, because the election is not a closing act:
    # `elect_round_winner` runs at the end of SCORING, before `l1_critique` and two LLM calls
    # before the round closes, and the live row is the only surface that can say so at that
    # moment. `False` until that election lands, and on every row of a round that held none —
    # never a claim that this candidate lost, only that nothing has crowned it yet.
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
    # The cross-round-comparable series: ability on the cycle's fixed δ ruler, which is
    # subset-invariant. `accuracy`/`composite_fitness` above are subset-relative — under
    # `per_round_resubset` they swing round-to-round on a fresh 6–16 sample draw, which reads
    # as a false "great start → decay". The trend/sparkline plot THIS series so progress is
    # honest; the per-round measured number stays on `candidates[]` (badged with its count).
    #
    # Never add a `cumulative_accuracy` beside it: a mean over rows from DIFFERENT
    # configurations fabricates a number no individual scored. Mirrors
    # `RoundResult.cumulative_theta`.
    cumulative_theta: float | None = None
    # No `cumulative_theta_se` beside it. The round-close handler used to push one in via
    # `model_copy(update=…)`, which does not validate — the key landed outside `model_fields`
    # and the serializer dropped it, so it was never on disk and nothing ever read it. The
    # interval that IS read is per candidate (`theta_se`); the round-level SE stays on the
    # ledger and the round document, which is where a reader for it would come from.
    # Mirrors `RoundResult.calibration_model` — the model the webapp's ability popover reads.
    calibration_model: CalibrationModel | None = None
    # The round's verdict and the evidence it rests on — the two bits that decide how long the
    # cycle lives, so they are the two an operator most needs to see. `improved` moves the stall
    # counter and the life bank; `electable_count` decides whether the bank moves AT ALL, since a
    # round no candidate reached measured nothing about the search (`EscalationFSM._bank_round`).
    # Both were engine-only, which is why a campaign could visibly climb while its bank drained
    # and the dashboard showed neither number. Round 0 holds no election, so both stay unset there.
    improved: bool | None = None
    electable_count: int | None = None
    candidates: list[RoundSummaryCandidate] = Field(default_factory=list)
    # Per-round selection from the adaptive queue mechanism — sample ids
    # in measurement order (longest candidate sequence carries the full
    # series since PoBB truncates losers, not the queue mechanism itself).
    selection: list[int] = Field(default_factory=list)
    # PP-computed (round-close) degradation verdict for this round (origin included).
    # ``None`` only when the round measured zero samples. Webapp/CLI render it;
    # never recompute (R-36).
    health: DegradationHealth | None = None
    # Blocked, paired L4 outer verdict: the target variant's pooled (variant − noop)
    # effect across the panel's cells + a 3-way decision. ``None`` on any non-L4 round
    # (no no-op probe to pair against). Webapp/CLI render it; never recompute.
    outer_verdict: OuterVerdict | None = None
