from __future__ import annotations

import math
from collections import Counter
from collections.abc import Collection, Iterable, Mapping
from enum import StrEnum
from statistics import NormalDist
from typing import Literal, NamedTuple

from pydantic import Field

from promptpotter.domain.paired_reading import Estimate
from promptpotter.domain.said import Tone
from promptpotter.domain.strict_model import FrozenModel
from promptpotter.shared.hashing import stable_hash
from promptpotter.shared.worded import Worded

# A bare δ is 1PL (a ≡ 1); a ``(δ, a)`` pair is 2PL.
RulerEntry = float | tuple[float, float]
# Keyed by what the cell IS (`Sample.key`), never its position: one scale reads every shape alike.
Ruler = Mapping[str, RulerEntry]

# ``(archive address, Sample.key, graded response)``: one individual's answer to one cell.
type RulerRow = tuple[str, str, float]


class RulerScope(FrozenModel):
    """What one δ scale spans: one scorer's cells on ONE dataset. Arms link a scale only through
    cells they share, and two datasets share none, so a δ is never compared across datasets."""

    scorer: str
    dataset: str


class RulerFold(FrozenModel):
    """One line of the tenant's ruler: an election's rows, folded after it. The line count is the
    version, so a version is a prefix and a re-fit at it is the fit an election read."""

    scope: RulerScope
    rows: list[RulerRow]
    # The campaign whose election folded it: a compare arm reads its own lines past the freeze.
    campaign: str


class RulerOwn(FrozenModel):
    """A compare arm's reach past its head-to-head's freeze: its own campaign's lines alone, which
    is what a fresh run of itself would read."""

    campaign: str
    since: int = Field(ge=0)


class RulerReach(FrozenModel):
    """Which lines a fit reads: every line before ``version``, except past ``own.since`` only
    ``own.campaign``'s."""

    version: int = Field(ge=0)
    own: RulerOwn | None

    def holds(self, line: int, fold: RulerFold) -> bool:
        return self.own is None or line < self.own.since or fold.campaign == self.own.campaign


class RulerStamp(FrozenModel):
    """The δ scale a reading was taken on: the tenant ruler at ``version`` (narrowed by ``own``)
    less the rows of ``excluded``, the parent line it held. A cold stamp's ``id`` is flat."""

    id: str
    version: int = Field(ge=0)
    own: RulerOwn | None
    scope: RulerScope
    # Archive addresses, sorted; only those the version held, so one scale is one stamp.
    excluded: tuple[str, ...]

    @property
    def cold(self) -> bool:
        return is_flat_ruler_id(self.id)

    @property
    def reach(self) -> RulerReach:
        return RulerReach(version=self.version, own=self.own)


# ``E[σ(N(m, v))] ≈ σ(m / √(1 + π·v/8))``.
PROBIT_SCALE = math.pi / 8.0

# A cold id carries the OBJECTIVE: flat δ depends on no fit, so nothing else separates two readings.
_FLAT_PREFIX = "flat"

# ``None`` beside these is a third state (cold ruler, θ is logit-fitness); never collapse it to "1PL".
CalibrationModel = Literal["1PL", "2PL"]

# The share of a round's cells on a shared δ; a chosen value, unvalidated against banked rounds.
PRIOR_PINNED_RATIO = 0.5
# A FRACTION of the ruler's own δ range; a chosen value, unvalidated against banked rounds.
BAND_COLLAPSE_RATIO = 0.20
# The absolute floor in LOGITS: on a collapsed ruler the ratio measures against the thing under test.
BAND_COLLAPSE_LOGITS = 1.0


class ThetaCaveat(StrEnum):
    """A state in which θ is NOT ability; every number still renders, so WHICH fired is the fact.
    Each is read off the cells a θ was read on: one level's, one arm's, a whole series'."""

    # All-miss or all-hit: θ is wherever the prior stops it, so a lift from it reads the prior.
    PRIOR_DOMINATED = "prior_dominated"
    # No δ scale: θ is logit-fitness, read on the cells an arm shares with the parent it is judged against.
    COLD_RULER = "cold_ruler"
    # The INSTRUMENT: the ruler itself spans almost nothing, so no draw could have been wider.
    FLAT_RULER = "flat_ruler"
    # The ACQUISITION: a thin slice of a wide ruler. Silent: the id matches and every number renders.
    COLLAPSED_BAND = "collapsed_band"
    # Most cells sit on a δ shared with other cells: the prior, whose pin MOVES with the version. Silent.
    PRIOR_PINNED = "prior_pinned"
    # θ skips cells the ruler does not carry, so it is read on fewer cells than fitness.
    UNMEASURED_DELTA = "unmeasured_delta"


class ThetaCaveatInfo(NamedTuple):
    """One caveat as a `domain/said.py::Said` row, worded for any level: the operator's screen and
    the L4 optimizer's prompt read the same words."""

    label: str
    detail: str
    tone: Tone


# A footnote to a θ, never an alarm: the reading stands, read with this in mind.
THETA_CAVEAT_INFO: Worded[ThetaCaveat, ThetaCaveatInfo] = Worded(
    {
        ThetaCaveat.COLD_RULER: ThetaCaveatInfo(
            "θ is plain fitness so far",
            "Too few earlier answers to rate how hard each sample is, so θ is fitness on another "
            "scale. A lift against the parent holds; θ compared across rounds or candidates does not.",
            "attention",
        ),
        ThetaCaveat.FLAT_RULER: ThetaCaveatInfo(
            "θ is plain fitness here",
            "Every sample of this dataset rates as equally hard, so θ ranks candidates exactly as "
            "fitness does. No round can change that.",
            "attention",
        ),
        ThetaCaveat.COLLAPSED_BAND: ThetaCaveatInfo(
            "θ is plain fitness this round",
            "This round's samples were all about equally hard, so θ ranks candidates exactly as "
            "fitness does. A round drawing harder and easier samples reads differently.",
            "attention",
        ),
        ThetaCaveat.PRIOR_PINNED: ThetaCaveatInfo(
            "θ shifts between rounds",
            "Most samples this round got the default difficulty, because every candidate answered "
            "them alike. That default moves as rounds come in, so compare θ within a round, not "
            "across rounds.",
            "attention",
        ),
        ThetaCaveat.UNMEASURED_DELTA: ThetaCaveatInfo(
            "θ leaves some samples out",
            "Some samples have no difficulty rating yet, so θ reads fewer samples than the fitness "
            "beside it, and two candidates may be read on different ones. A later round includes them.",
            "attention",
        ),
        ThetaCaveat.PRIOR_DOMINATED: ThetaCaveatInfo(
            "No θ for an all-wrong or all-right candidate",
            "It missed, or got right, every sample it answered, so θ has no edge to measure: its θ "
            "is where the prior stopped it, and a lift taken from it reads the prior. Other "
            "candidates are unaffected.",
            "attention",
        ),
    }
)

_missing_caveat_info = set(ThetaCaveat) - set(THETA_CAVEAT_INFO)
if _missing_caveat_info:
    raise RuntimeError(
        f"THETA_CAVEAT_INFO is missing rows for {sorted(c.value for c in _missing_caveat_info)} "
        "(domain/ruler.py)."
    )


def theta_caveat(
    ruler: DeltaRuler | None, cells: Collection[str], *, prior_dominated: bool
) -> ThetaCaveat | None:
    """The one deriver, over the cells a θ was read on; the order is severity."""
    if prior_dominated:
        return ThetaCaveat.PRIOR_DOMINATED
    if ruler is None:
        return ThetaCaveat.COLD_RULER
    if (band := ruler.band_span(cells)) is not None:
        round_span, ruler_span = band
        if ruler_span <= BAND_COLLAPSE_LOGITS:
            return ThetaCaveat.FLAT_RULER
        if round_span <= max(BAND_COLLAPSE_LOGITS, BAND_COLLAPSE_RATIO * ruler_span):
            return ThetaCaveat.COLLAPSED_BAND
        if (pinned := ruler.pinned_share(cells)) is not None and pinned >= PRIOR_PINNED_RATIO:
            return ThetaCaveat.PRIOR_PINNED
    return ThetaCaveat.UNMEASURED_DELTA if ruler.unlinked(cells) else None


def flat_ruler_id(objective_id: str) -> str:
    return f"{_FLAT_PREFIX}:{objective_id}"


def is_flat_ruler_id(value: str) -> bool:
    """A fitted anchor is a hex digest, so the prefix cannot collide with one."""
    return value.startswith(_FLAT_PREFIX)


def ruler_entry(value: RulerEntry) -> tuple[float, float]:
    if isinstance(value, tuple):
        return float(value[0]), float(value[1])
    return float(value), 1.0


def anchor_id_of(
    delta: Mapping[str, float],
    mu_delta: float,
    sigma_delta: float,
    calibration_model: CalibrationModel,
) -> str:
    """A hash of the δ themselves: two versions that fit one scale are one id, and comparable."""
    return stable_hash(
        [
            [[key, delta[key]] for key in sorted(delta)],
            mu_delta,
            sigma_delta,
            calibration_model,
        ]
    )


class DeltaRuler(FrozenModel):
    delta: dict[str, float]
    delta_se: dict[str, float]
    # 2PL only; empty under 1PL, where a ≡ 1.
    discrimination: dict[str, float] = Field(default_factory=dict)
    mu_delta: float
    sigma_delta: float
    sigma_theta: float
    calibration_model: CalibrationModel
    anchor_id: str

    def entries(self) -> dict[str, RulerEntry]:
        """Each θ is read under δ ~ N(δ̂, se²): the probit rule folds the δ's SE into its slope."""
        return {
            key: (d, a / math.sqrt(1.0 + PROBIT_SCALE * (a * self.delta_se[key]) ** 2))
            for key, d in self.delta.items()
            for a in (self.discrimination.get(key, 1.0),)
        }

    @property
    def delta_span(self) -> float:
        """In logits; near zero, θ is logit-fitness plus a constant, whatever the cell count."""
        return max(self.delta.values()) - min(self.delta.values()) if self.delta else 0.0

    def band_span(self, cells: Iterable[str]) -> tuple[float, float] | None:
        """``(round_span, ruler_span)``. Inside a thin band 1PL reduces to ``θ = logit(fitness) + c``."""
        on = [self.delta[key] for key in cells if key in self.delta]
        if len(on) < 2 or len(self.delta) < 2:
            return None
        return (max(on) - min(on), self.delta_span)

    def pinned_share(self, cells: Iterable[str]) -> float | None:
        """A continuous fit produces no ties, so a shared δ is the PRIOR standing in for a measurement."""
        shared = {d for d, n in Counter(self.delta.values()).items() if n > 1}
        on = [self.delta[key] for key in cells if key in self.delta]
        if not on:
            return None
        return sum(1 for d in on if d in shared) / len(on)

    def unlinked(self, cells: Iterable[str]) -> int:
        return sum(1 for key in set(cells) if key not in self.delta)


class DeltaState(StrEnum):
    LINKED = "linked"
    UNLINKED = "unlinked"
    NOT_FITTED = "not_fitted"


DELTA_STATE_LABEL: dict[DeltaState, str | None] = {
    DeltaState.LINKED: None,
    DeltaState.UNLINKED: "not on the ruler yet",
    DeltaState.NOT_FITTED: "ruler not fitted yet",
}

_missing_delta_labels = set(DeltaState) - set(DELTA_STATE_LABEL)
if _missing_delta_labels:
    raise RuntimeError(
        f"DELTA_STATE_LABEL is missing rows for {sorted(s.value for s in _missing_delta_labels)} "
        "(domain/ruler.py)."
    )


class RulerStanding(FrozenModel):
    """Whether a scope has a δ ruler, and on what model."""

    state: Literal["fitted", "not_fitted"]
    calibration_model: CalibrationModel | None = Field(description="Null while not fitted.")

    @classmethod
    def of(cls, ruler: DeltaRuler | None) -> RulerStanding:
        if ruler is None:
            return cls(state="not_fitted", calibration_model=None)
        return cls(state="fitted", calibration_model=ruler.calibration_model)

    @property
    def label(self) -> str | None:
        """What to print in place of every δ of the scope; ``None`` once the ruler is fitted."""
        return DELTA_STATE_LABEL[self.delta_state(True)]

    def delta_state(self, carried: bool) -> DeltaState:
        if self.state == "not_fitted":
            return DeltaState.NOT_FITTED
        return DeltaState.LINKED if carried else DeltaState.UNLINKED


# The Newton fit stops within `tol` of its root, so two fits are never compared tighter than this.
FIT_SLACK = 1e-3
_POWER_Z = NormalDist().inv_cdf(0.8)


def theta_lift(theta: float, se: float, bar_theta: float, bar_se: float) -> Estimate:
    """θ over a bar's θ, and the noise of the DIFFERENCE, never either side's own SE."""
    return Estimate.normal(
        theta - bar_theta, math.hypot(se, bar_se), unit="theta", form="lift", n=None
    )


def p_better(lift: Estimate) -> float:
    """``P(θ > θ_bar)`` off a θ lift, unbounded: the election ranks on it."""
    se = lift.se or 0.0
    if se <= 1e-12:
        return 1.0 if lift.value > 0.0 else 0.0
    return NormalDist().cdf(lift.value / se)


def clears(lift: Estimate, admission: float | None, *, support: float | None) -> bool:
    """The election's one bar: past what a fit resolves, and ``p_better`` past *admission*, capped
    by the discordant cells' sign posterior *support* (``None``: no cell pairs the two)."""
    # No SE margin on the value: subtracting one turns a wide-posterior gain negative.
    if admission is None or lift.value <= FIT_SLACK:
        return False
    p = p_better(lift)
    if support is not None:
        # The bound's mass on the SIDE θ read, not `|bound - 0.5|`, which reads 3 losses as 3 wins.
        side = support if p > 0.5 else 1.0 - support
        extent = min(abs(p - 0.5), max(0.0, side - 0.5))
        p = 0.5 + extent if p > 0.5 else 0.5 - extent
    return p > admission


def detectable(se: float, admission: float) -> float:
    """The true θ lift `clears` meets at 80% power on *se*."""
    return (NormalDist().inv_cdf(admission) + _POWER_Z) * se


class AbilityReading(FrozenModel):
    """A Rasch θ and the δ scale it was read on, held as one value because apart they mean nothing."""

    theta: float
    se: float
    ruler: RulerStamp
    # The cells θ was READ on: a reading on none is no reading.
    cells: int = Field(ge=1)
    # Many cells inside a narrow span is a WARM ruler reading flat.
    ruler_span: float | None
    # A thin round on a wide ruler and a wide round on a thin one read identical θ. ``None`` below two cells.
    round_span: float | None
    # ``None`` = the ruler is cold (flat δ) and θ is plain logit-fitness — neither model.
    calibration_model: CalibrationModel | None
    # Stamped from `theta_caveat` at the one minting site, never re-derived; ``None`` = θ is ability.
    caveat: ThetaCaveat | None
    # The two counts `caveat` was decided on, beside it.
    unlinked: int
    pinned_share: float | None

    def comparable_to(self, other: AbilityReading) -> bool:
        """Never on a cold ruler: its id names no cell set, so easy and hard cells read alike."""
        return not self.ruler.cold and self.ruler.id == other.ruler.id

    def lift_over(self, other: AbilityReading) -> Estimate | None:
        """``None`` where the two sit on different scales."""
        if not self.comparable_to(other):
            return None
        return theta_lift(self.theta, self.se, other.theta, other.se)

    def scale(self, *, named: bool = True) -> str:
        if self.ruler.cold:
            return f"{self.cells} cells, cold ruler"
        name = f"ruler {self.ruler.id} v{self.ruler.version}, " if named else ""
        return f"{name}{self.cells} cells, {self.calibration_model}"


# Well inside one round's θ SE: a plateau advises stopping, and a false stop costs more than a miss.
PLATEAU_THETA_BAND = 0.05
PLATEAU_ROUNDS = 3


class SeriesReading(FrozenModel):
    """A round's level as its series read it, under the one caveat that withholds it."""

    reading: AbilityReading = Field(
        description="The round's level: the individual it ended on, read on the series' one scale"
    )
    caveat: ThetaCaveat | None = Field(
        description="The level's own caveat, else its series'. Null, and it differences any "
        "other level of the series that is null too."
    )


class LevelSeries(FrozenModel):
    """Every round's ended-on individual read on ONE δ scale, so any two of its levels difference."""

    stamp: RulerStamp = Field(
        description="The scale every level is read on: the tenant ruler at the run's clock, less "
        "the run's parent line (the origin and every pick)."
    )
    levels: dict[int, AbilityReading | None] = Field(
        description="By round, round 0 the origin: the individual the round ended on — on a round "
        "that elected nothing, the parent standing into it — read on all of its rows in the "
        "cycle. Null where it holds no scored cell on the scale."
    )
    caveat: ThetaCaveat | None = Field(
        description="`theta_caveat` over every cell this line read. Non-null, no two of its "
        "levels difference, whatever `stamp` says; another line on the stamp keeps its own."
    )

    @property
    def rounds(self) -> list[int]:
        return sorted(self.levels)

    def reading(self, rnd: int) -> SeriesReading | None:
        """A surface showing θ under its caveat asks here, by name; ``None``: no scored cell."""
        held = self.levels[rnd]
        if held is None:
            return None
        return SeriesReading(
            reading=held, caveat=held.caveat if held.caveat is not None else self.caveat
        )

    def level(self, rnd: int) -> AbilityReading | None:
        """The level two rounds may difference."""
        read = self.reading(rnd)
        return None if read is None or read.caveat is not None else read.reading

    def plateau(self) -> float | None:
        recent = [self.level(r) for r in self.rounds[-PLATEAU_ROUNDS:]]
        levels = [r.theta for r in recent if r is not None]
        if len(levels) < PLATEAU_ROUNDS:
            return None
        mean = sum(levels) / len(levels)
        return mean if all(abs(level - mean) < PLATEAU_THETA_BAND for level in levels) else None
