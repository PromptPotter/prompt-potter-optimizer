from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Literal, NamedTuple, get_args

from pydantic import Field

from promptpotter.domain.arms import (
    ARM_END_INFO,
    ArmEnd,
    ArmRole,
    ArmState,
    ArmStateKind,
    Cut,
    Measured,
    Measuring,
    Proposed,
    Refused,
    arm_end,
    walked_short,
)
from promptpotter.domain.bench import BandedValue, BenchReading, OwnLevel
from promptpotter.domain.cell_fold import CellFold, ScoreSummary
from promptpotter.domain.origin_panel import LineRate
from promptpotter.domain.paired_reading import (
    ArmKey,
    ArmName,
    LiftEstimate,
    MeasurandUnit,
    PairedReading,
)
from promptpotter.domain.ruler import ThetaCaveat, theta_band
from promptpotter.domain.spend import PrefixReading, TokenAccount
from promptpotter.domain.strict_model import FrozenModel
from promptpotter.domain.verification import VerifyReading
from promptpotter.shared.hashing import shapes_optimizer_prompt
from promptpotter.shared.measurement_context import MeasurementRole
from promptpotter.shared.worded import Worded


@shapes_optimizer_prompt
def order_floor(score: float | None) -> float:
    return score if score is not None else -math.inf


ScoreboardRankKey = tuple[bool, bool, float, float, float]


def scoreboard_rank_key(
    composite_fitness: float | None,
    fitness: float | None,
    theta: float | None = None,
    *,
    is_leading: bool = False,
    is_partial: bool = False,
) -> ScoreboardRankKey:
    """A mask lens (``mask/divergence.py``) passes no `is_leading`: pinned to rank 1 it cannot disagree."""
    return (
        is_leading,
        # The round order is stratified, so a walk cut short kept a biased slice, not a smaller sample.
        not is_partial,
        order_floor(theta),
        order_floor(composite_fitness),
        order_floor(fitness),
    )


class ScoreboardRow(FrozenModel):
    """One rank-ordered row of a round's scoreboard: an arm's reading at its rank."""

    rank: int
    reading: ArmReading


type DisplayMetric = Literal["fitness", "composite", "ability"]


class UnitFormat(NamedTuple):
    """How one unit reads: a level is ``prefix``, the value times ``scale`` at ``digits``, then ``suffix``; a lift is signed."""

    scale: float
    digits: int
    prefix: str
    suffix: str
    lift_digits: int
    lift_word: str
    lift_name: str
    compact: bool


# A rate's lift reads in percentage points, never in percent.
UNIT_FORMATS: Worded[MeasurandUnit, UnitFormat] = Worded(
    {
        "rate": UnitFormat(100.0, 0, "", "%", 1, "pts", "point", False),
        "score": UnitFormat(1.0, 2, "", "", 3, "score", "unit of score", False),
        "theta": UnitFormat(1.0, 2, "θ ", "", 3, "logits", "logit", False),
        "seconds": UnitFormat(1.0, 2, "", "s", 2, "s", "second", False),
        "usd": UnitFormat(1.0, 4, "$", "", 4, "USD", "dollar", False),
        "tokens": UnitFormat(1.0, 0, "", " tok", 0, "tok", "token", True),
        "rank": UnitFormat(1.0, 2, "", "", 2, "ranks", "rank", False),
        "rounds": UnitFormat(1.0, 1, "", "", 1, "rounds", "round", False),
    }
)

_COMPACT_STEPS = ((1_000_000.0, "M"), (1_000.0, "k"))


def _number(value: float, fmt: UnitFormat, digits: int, sign: str) -> str:
    scaled = fmt.scale * value
    for step, mark in _COMPACT_STEPS if fmt.compact else ():
        if abs(scaled) >= step:
            return f"{scaled / step:{sign}.1f}{mark}"
    return f"{scaled:{sign}.{digits}f}"


def level_text(value: float | None, unit: MeasurandUnit) -> str:
    """A level as a person reads it in its unit; `—` where it is absent."""
    if value is None:
        return "—"
    fmt = UNIT_FORMATS[unit]
    return f"{fmt.prefix}{_number(value, fmt, fmt.digits, '')}{fmt.suffix}"


@shapes_optimizer_prompt
def fold_fitness_text(fold: CellFold | None) -> str:
    return "—" if fold is None else level_text(fold.fitness, fold.fitness_unit)


def _signed(value: float, fmt: UnitFormat) -> str:
    return _number(value, fmt, fmt.lift_digits, "+")


def lift_shown(value: float, unit: MeasurandUnit) -> float:
    """The lift as `lift_text` prints it, so a colour or a sign reads what the reader sees."""
    fmt = UNIT_FORMATS[unit]
    return round(fmt.scale * value, fmt.lift_digits)


# Ties a value to its word and a bound to its pair, so a narrow line breaks only between them.
_NBSP = "\N{NO-BREAK SPACE}"


def lift_text(value: float, unit: MeasurandUnit) -> str:
    """A lift as a person reads it in its unit: every surface that prints one prints this."""
    fmt = UNIT_FORMATS[unit]
    return f"{_signed(value, fmt)}{_NBSP}{fmt.lift_word}"


def lift_interval_text(lo: float, hi: float, unit: MeasurandUnit) -> str:
    """A lift's interval, its bounds signed in the unit `lift_text` names."""
    fmt = UNIT_FORMATS[unit]
    return f"[{_signed(lo, fmt)},{_NBSP}{_signed(hi, fmt)}]"


def fitness_terms_text(terms: Mapping[str, float]) -> str:
    """Each scoring-formula term's round mean, as the composite block prints one."""
    return "  ".join(f"{name}={value:.3f}" for name, value in terms.items())


def lift_band_text(estimate: LiftEstimate, unit: MeasurandUnit) -> str:
    interval = lift_interval_text(estimate.ci_lo, estimate.ci_hi, unit)
    return f"{lift_text(estimate.value, unit)} {interval}"


class DisplayMetricInfo(NamedTuple):
    label: str
    glyph: str
    title: str
    unit: MeasurandUnit


#: IN PICK ORDER — the tiebreak where the elected column is not among those shown.
DISPLAY_METRIC_INFO: Worded[DisplayMetric, DisplayMetricInfo] = Worded(
    {
        "fitness": DisplayMetricInfo(
            "fitness",
            "ƒ",
            "Mean per-cell fitness under the scoring formula, over the candidate's measured subset "
            "(subset-relative).",
            "score",
        ),
        "ability": DisplayMetricInfo(
            "ability θ",
            "θ",
            "Difficulty-adjusted ability θ — what a selector declaring ability (potter's) elects on. "
            "A logit (not a %): comparable within a round; cross-round comparison waits on the stable "
            "δ bank.",
            "theta",
        ),
        "composite": DisplayMetricInfo(
            "composite",
            "∑",
            "Composite fitness under the active scoring formula (equals fitness when no formula is "
            "set).",
            "score",
        ),
    }
)
if set(DISPLAY_METRIC_INFO) != set(get_args(DisplayMetric.__value__)):
    raise RuntimeError(
        "DISPLAY_METRIC_INFO is out of step with DisplayMetric (domain/arm_reading.py)"
    )

# A count a surface takes over the candidates it shows, read as "N of M".
type CandidateCount = Literal["replayed"]
CANDIDATE_COUNT_WORDS: Worded[CandidateCount, str] = Worded(
    {
        "replayed": "Counted per candidate: N of the M shown carry at least one replayed cell.",
    }
)


class ArmFit(FrozenModel):
    """What the election read of one arm: θ lives here and nowhere else."""

    # Rasch ability on the round's joint-fit scale; `None` outside the election fit.
    theta: float | None = None
    theta_se: float | None = None
    # This ARM's own reason θ is not ability (`bench/difficulty.py::AbilityScale.caveat`).
    theta_caveat: ThetaCaveat | None = None
    # This arm over the individual it was judged against; `None` on an arm with no reference.
    vs_reference: PairedReading | None = None


def panel_counts(arm: ArmState, fold: ScoreSummary | None) -> tuple[int | None, int | None]:
    match arm:
        case Proposed():
            return None, None
        case Measuring() | Cut() | Measured():
            return (None if fold is None else fold.walked), arm.expected_samples


def panel_cuts(panels: Sequence[tuple[int | None, int | None]]) -> list[bool]:
    fullest = max((scored for scored, _ in panels if scored is not None), default=0)
    return [
        scored is not None and ((expected is not None and scored < expected) or scored < fullest)
        for scored, expected in panels
    ]


Crown = Literal["elected", "uncontested"]


class ArmPanel(FrozenModel):
    """The cells one arm's walk holds of the panel its round asked of it."""

    scored: int | None = Field(
        description="Cells the walk holds. Null on an arm with no score report — never measured, "
        "which is not a walk of zero cells."
    )
    expected: int | None = Field(
        description="The walk's length; null until its first cell announces it."
    )
    cached: int | None = Field(
        description="Of `scored`, how many were replayed from the archive rather than measured. "
        "Null on an arm never measured."
    )
    cached_share: float | None = Field(
        description="`cached` over `scored`. Null where either is, and on a walk holding no cell."
    )
    cut: bool = Field(
        description="Stopped short of its round's panel (`panel_cuts`): under its own `expected`, "
        "or, for a panel arm, under the fullest panel a panel arm of its round reached."
    )

    @classmethod
    def of(
        cls, scored: int | None, expected: int | None, cached: int | None, *, cut: bool
    ) -> ArmPanel:
        share = cached / scored if scored and cached is not None else None
        return cls(scored=scored, expected=expected, cached=cached, cached_share=share, cut=cut)


class ArmAbility(FrozenModel):
    """One arm's difficulty-adjusted Rasch ability on its round's joint-fit scale."""

    theta: float | None
    se: float | None
    ci_lo: float | None = Field(
        description="Lower bound of the 95% interval on `theta` (`ruler.py::theta_band`); null "
        "where `theta` or `se` is."
    )
    ci_hi: float | None = Field(description="Upper bound of the same interval.")
    caveat: ThetaCaveat | None = Field(
        description="Why `theta` is not this arm's ability, of the ARM's own scope."
    )

    @classmethod
    def of(
        cls, theta: float | None, se: float | None, caveat: ThetaCaveat | None
    ) -> ArmAbility | None:
        if (theta, se, caveat) == (None, None, None):
            return None
        lo, hi = theta_band(theta, se) or (None, None)
        return cls(theta=theta, se=se, ci_lo=lo, ci_hi=hi, caveat=caveat)


class ElectionColumn(FrozenModel):
    """The column a pick's election is quoted on: θ where it was won on θ, unless θ is unread or
    carries a caveat — there θ is not ability, and the election reads on fitness."""

    elects_on: DisplayMetric = Field(description="What the electing round's selector ranked on.")
    ability: ArmAbility | None = Field(description="The pick's θ on its electing round.")
    reads_on: DisplayMetric

    @classmethod
    def of(cls, elects_on: DisplayMetric, ability: ArmAbility | None) -> ElectionColumn:
        unread = ability is None or ability.theta is None or ability.caveat is not None
        caveated = elects_on == "ability" and unread
        return cls(
            elects_on=elects_on, ability=ability, reads_on="fitness" if caveated else elects_on
        )


class ArmElection(FrozenModel):
    """What one arm's round decided about it. Built by :meth:`of` alone."""

    held: bool = Field(
        description="The arm's round has held its election. `selected: false` reads the same on a "
        "round still scoring and on one that crowned nobody; only this tells them apart."
    )
    selected: bool
    leading: bool = Field(
        description="The ONE arm its round is read off, as the selector named it "
        "(`Election.leading`); false until the election."
    )
    crown: Crown | None = Field(
        description="How a selected arm advanced, read off the election's electable count; null "
        "on every arm the election did not select."
    )

    @classmethod
    def of(
        cls,
        key: ArmKey,
        *,
        held: bool,
        selected: ArmKey | None,
        leading: ArmKey | None,
        electable: int | None,
    ) -> ArmElection:
        won = key == selected
        crown: Crown | None = None
        if won and electable is not None:
            crown = "elected" if electable > 1 else "uncontested"
        return cls(held=held, selected=won, leading=key == leading, crown=crown)


ArmVerdict = Literal[
    "retired",
    "invalid",
    "origin",
    "elected",
    "uncontested",
    "selected",
    "not_elected",
    "unmeasured",
    "awaiting",
]

ARM_VERDICT_LABELS: Worded[ArmVerdict, str] = Worded(
    {
        "retired": "retired",
        "invalid": f"{ARM_END_INFO['invalid'].label} — never measured",
        "origin": "origin",
        "elected": "won its round",
        "uncontested": "advanced uncontested",
        "selected": "selected",
        "not_elected": "not elected",
        "unmeasured": "no cell measured yet",
        "awaiting": "awaiting election",
    }
)
assert ARM_VERDICT_LABELS.keys() == set(get_args(ArmVerdict))


def arm_verdict(reading: ArmReading, *, retired: bool) -> ArmVerdict:
    if retired:
        return "retired"
    if reading.end == "invalid":
        return "invalid"
    if reading.role == MeasurementRole.ORIGIN:
        return "origin"
    election = reading.election
    if election.crown is not None:
        return election.crown
    if election.selected:
        return "selected"
    if election.held:
        return "not_elected"
    return "unmeasured" if reading.state in ("proposed", "measuring") else "awaiting"


def _own_level(fold: ScoreSummary) -> OwnLevel | None:
    fitness, composite = fold.fitness, fold.composite_fitness
    if fitness is None and composite is None:
        return None
    band = (fold.mean_fitness_ci_lo, fold.mean_fitness_ci_hi)
    lo, hi = band if None not in band else (None, None)
    return OwnLevel(
        fitness=None if fitness is None else BandedValue(value=fitness, ci_lo=lo, ci_hi=hi),
        composite=None
        if composite is None
        else BandedValue(value=composite, ci_lo=None, ci_hi=None),
        fitness_unit=fold.fitness_unit,
        n=fold.total,
    )


class ArmReading(FrozenModel):
    """One arm of one round, read: the one carrier every surface that lists or ranks arms embeds.

    Each part lands when its fact exists, so an arm in flight is this type with fewer parts set.
    """

    arm: ArmName = Field(description="THE key, and where the arm was walked.")
    role: ArmRole = Field(
        description="What the arm IS in this round: its origin, one of its proposals, or a "
        "`parent` re-read on its cells as a reference — never one of the round's proposals, "
        "whatever round `arm` names."
    )
    sp_hash: str = Field(
        description="The archive address of this arm's measurements (`prompt_fields_id`). Not a "
        "key: a re-proposed configuration shares it across rounds. Empty before it is measured."
    )
    changes_description: str
    state: ArmStateKind = Field(
        description="Where the arm's walk stands: proposed, being measured, or ended — cut "
        "short against it, or measured."
    )
    end: ArmEnd | None = Field(
        description="How its walk ended short of its panel: cut against it (`invalid` refused it "
        "before a cell), or `locked_in` — settled FOR it by the eliminator. Null on a walk still "
        "running and one that ran its panel."
    )
    walked_short: bool = Field(
        description="`end` names a stop of its walk, for it or against it: false on an `invalid` "
        "arm, which never started one."
    )
    refused_parameter: str | None = Field(
        description="The request parameter the endpoint refused, where `end` is `refused`; null "
        "on every other arm."
    )
    own: OwnLevel | None = Field(
        description="The arm's own level on its round's cells: fitness with the band the "
        "gateway folds per cell, the composite beside it, over `n` rows carrying a verdict. Null "
        "before the first row is graded, and on an `invalid` arm, which holds no cell."
    )
    fitness_terms: dict[str, float] = Field(
        description="The round mean of each term the scoring formula reads, `fitness` aside, over "
        "the cells that answered it: an L4 outer arm's `held_out_lift` and `floored` share."
    )
    panel: ArmPanel
    tokens: TokenAccount | None = Field(
        description="What measuring it consumed, the BACKEND bucket alone, replayed rows "
        "excluded; null until its score report folds an account."
    )
    prefix: PrefixReading | None = Field(
        description="The provider's prefix-cache reading of `tokens.input`; null beside a null "
        "`tokens`."
    )
    ability: ArmAbility | None = Field(description="Null outside the round's election fit.")
    vs_reference: PairedReading | None = Field(
        description="This arm (`b`) over the individual it was judged against (`a`), on the "
        "cells both scored. Null on the origin and until the election stamps it."
    )
    election: ArmElection
    bench: BenchReading | None = Field(
        description="This arm's individual on the held-out bench set; null unless a pass graded it."
    )
    verify: VerifyReading | None = Field(
        description="Its last `verify` pass, on search cells its rounds never bought."
    )
    on_origin_panel: LineRate | None = Field(
        description="Its individual's rate on the origin panel, as the newest overlap reading "
        "holds it: the one level two arms of different rounds may be differenced on. Null for an "
        "arm neither on the best-so-far line nor triggered, and where it does not hold the whole "
        "panel."
    )

    @classmethod
    def of(
        cls,
        name: ArmName,
        arm: ArmState,
        *,
        fold: ScoreSummary | None,
        cut: bool,
        election: ArmElection,
        fit: ArmFit | None,
    ) -> ArmReading:
        """*fold* is its round's, or `AnnouncedArms.fold` in flight; *fit* the election's, ``None`` until held."""
        walked = None if isinstance(arm, Proposed) else arm
        tokens = None if fold is None else fold.tokens
        panel = ArmPanel.of(
            None if fold is None else fold.walked,
            None if walked is None else walked.expected_samples,
            None if fold is None else fold.cached,
            cut=cut,
        )
        return cls(
            arm=name,
            role=arm.role,
            sp_hash="" if walked is None else walked.sp_hash,
            changes_description=arm.changes_description,
            state=arm.state,
            end=arm_end(arm),
            walked_short=walked_short(arm),
            refused_parameter=arm.reason.parameter
            if isinstance(arm, Cut) and isinstance(arm.reason, Refused)
            else None,
            own=None if fold is None else _own_level(fold),
            fitness_terms={} if fold is None else fold.term_means,
            panel=panel,
            tokens=tokens,
            prefix=None if tokens is None else tokens.prefix(replayed=False),
            ability=None
            if fit is None
            else ArmAbility.of(fit.theta, fit.theta_se, fit.theta_caveat),
            vs_reference=None if fit is None else fit.vs_reference,
            election=election,
            bench=None,
            verify=None,
            on_origin_panel=None,
        )

    def on_line(self, line: Mapping[str, LineRate]) -> ArmReading:
        return self.model_copy(update={"on_origin_panel": line.get(self.arm.key)})
