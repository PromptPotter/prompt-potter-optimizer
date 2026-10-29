from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Literal, NamedTuple, get_args

from pydantic import Field

from promptpotter.domain.arms import (
    ArmEnd,
    ArmRole,
    ArmState,
    ArmStateKind,
    Cut,
    Measured,
    Measuring,
    Proposed,
    arm_end,
    walked_short,
)
from promptpotter.domain.bench import BandedValue, BenchReading, OwnLevel
from promptpotter.domain.cell_fold import ScoreSummary
from promptpotter.domain.origin_panel import LineRate
from promptpotter.domain.paired_reading import ArmKey, ArmName, MeasurandUnit, PairedReading
from promptpotter.domain.ruler import ThetaCaveat, theta_band
from promptpotter.domain.spend import PrefixReading, TokenAccount
from promptpotter.domain.strict_model import FrozenModel
from promptpotter.domain.verify_pass import VerifyReading
from promptpotter.shared.hashing import shapes_optimizer_prompt
from promptpotter.shared.measurement_context import MeasurementRole


@shapes_optimizer_prompt
def order_floor(score: float | None) -> float:
    return score if score is not None else -math.inf


ScoreboardRankKey = tuple[bool, bool, float, float, float]


def scoreboard_rank_key(
    composite_fitness: float | None,
    accuracy: float | None,
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
        order_floor(accuracy),
    )


class ScoreboardRow(FrozenModel):
    """One rank-ordered row of a round's scoreboard: an arm's reading at its rank."""

    rank: int
    reading: ArmReading


DisplayMetric = Literal["accuracy", "composite", "ability"]
# A displayed level's unit: a measurand's, or θ — a fit over cells, never read off one.
LevelUnit = Literal["rate", "score", "theta"]
if not set(MeasurandUnit) < set(get_args(LevelUnit)):
    raise RuntimeError("LevelUnit must hold every MeasurandUnit (domain/arm_reading.py)")


class DisplayMetricInfo(NamedTuple):
    label: str
    glyph: str
    title: str
    unit: LevelUnit


#: IN PICK ORDER — the tiebreak where the elected column is not among those shown.
DISPLAY_METRIC_INFO: dict[DisplayMetric, DisplayMetricInfo] = {
    "accuracy": DisplayMetricInfo(
        "accuracy",
        "%",
        "Raw accuracy — correctness rate over the candidate's measured subset (subset-relative).",
        "rate",
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
        "Composite fitness under the active scoring formula (equals accuracy when no formula is "
        "set).",
        "score",
    ),
}
if set(DISPLAY_METRIC_INFO) != set(get_args(DisplayMetric)):
    raise RuntimeError(
        "DISPLAY_METRIC_INFO is out of step with DisplayMetric (domain/arm_reading.py)"
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

ARM_VERDICT_LABELS: dict[ArmVerdict, str] = {
    "retired": "retired",
    "invalid": "invalid — never measured",
    "origin": "origin",
    "elected": "won its round",
    "uncontested": "advanced uncontested",
    "selected": "selected",
    "not_elected": "not elected",
    "unmeasured": "not measured yet",
    "awaiting": "awaiting election",
}
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


def _own_level(
    accuracy: float | None,
    composite: float | None,
    band: tuple[float | None, float | None],
    *,
    n: int,
) -> OwnLevel | None:
    if accuracy is None and composite is None:
        return None
    lo, hi = band if None not in band else (None, None)
    return OwnLevel(
        accuracy=None if accuracy is None else BandedValue(value=accuracy, ci_lo=lo, ci_hi=hi),
        composite=None
        if composite is None
        else BandedValue(value=composite, ci_lo=None, ci_hi=None),
        n=n,
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
    own: OwnLevel | None = Field(
        description="The arm's own level on its round's cells: accuracy with the band the "
        "gateway folds per cell, the composite beside it, over `n` rows carrying a verdict. Null "
        "before the first row is graded, and on an `invalid` arm, which holds no cell."
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
            own=None
            if fold is None
            else _own_level(
                fold.accuracy,
                fold.composite_fitness,
                (fold.mean_fitness_ci_lo, fold.mean_fitness_ci_hi),
                n=fold.total,
            ),
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
