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

__all__ = ["RoundSummary", "RoundSummaryCandidate"]


class RoundSummaryCandidate(StrictModel):
    """Display-summary row for `dashboard.json::rounds[].candidates` — chart/lineage/sparkline subset of `ScoredCandidate`."""

    model_config = ConfigDict(frozen=True)

    candidate_id: str
    label: str
    accuracy: float
    composite_fitness: float
    scored_samples: int
    expected_samples: int
    cached_samples: int
    is_winner: bool
    evaluators: dict[str, float] = Field(default_factory=dict)
    changes_description: str = ""
    partial_reason: str = ""  # "" | "skip" — see ScoredCandidate.partial_reason
    # Difficulty-adjusted Rasch ability + SE (`ScoredCandidate.theta`) — the metric the winner
    # was elected on, so the chart can explain a lower-accuracy winner. `None` outside the fit.
    theta: float | None = None
    theta_se: float | None = None
    # Composite-fitness CI (`ScoredCandidate.composite_ci_lo/hi`) — always present for any
    # candidate with ≥1 scored sample; the whisker the chart draws around `composite_fitness`.
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
