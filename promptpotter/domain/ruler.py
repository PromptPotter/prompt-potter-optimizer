from __future__ import annotations

from collections import Counter
from collections.abc import Collection, Iterable, Mapping
from enum import StrEnum
from statistics import NormalDist
from typing import TYPE_CHECKING, Annotated, Literal, NamedTuple

from pydantic import Field

from promptpotter.domain.strict_model import FrozenModel
from promptpotter.shared.hashing import shapes_optimizer_prompt, stable_hash

if TYPE_CHECKING:
    from promptpotter.domain.sample import Sample

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


class CellScopes(NamedTuple):
    """The dataset each cell measures, by `Sample.key`; one scorer reads them all, so the dataset
    alone tells two `RulerScope`s apart."""

    by_key: Mapping[str, str]

    @classmethod
    def of(cls, samples: Iterable[Sample], dataset: str) -> CellScopes:
        return cls({s.key: s.measured_dataset or dataset for s in samples})

    def spanned(self, keys: Iterable[str]) -> int:
        return len({self.by_key[k] for k in keys})


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


# A cold id carries the OBJECTIVE: flat δ depends on no fit, so nothing else separates two readings.
_FLAT_PREFIX = "flat"

# ``None`` beside these is a third state (cold ruler, θ is logit-accuracy); never collapse it to "1PL".
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

    # All-miss, so θ sits on the prior's floor and every LIFT taken against it reads 0.000.
    FLOOR_PINNED = "floor_pinned"
    # The cells span several `RulerScope`s, which share no cell, so no one δ scale reads them.
    DISJOINT_SCOPES = "disjoint_scopes"
    # No δ scale: θ is logit-accuracy, read on the cells an arm shares with the parent it is judged against.
    COLD_RULER = "cold_ruler"
    # The INSTRUMENT: the ruler itself spans almost nothing, so no draw could have been wider.
    FLAT_RULER = "flat_ruler"
    # The ACQUISITION: a thin slice of a wide ruler. Silent: the id matches and every number renders.
    COLLAPSED_BAND = "collapsed_band"
    # Most cells sit on a δ shared with other cells: the prior, whose pin MOVES with the version. Silent.
    PRIOR_PINNED = "prior_pinned"
    # θ skips cells the ruler does not carry, so it is read on fewer cells than accuracy.
    UNMEASURED_DELTA = "unmeasured_delta"


class ThetaCaveatInfo(NamedTuple):
    head: str
    body: str


THETA_CAVEAT_INFO: Annotated[dict[ThetaCaveat, ThetaCaveatInfo], shapes_optimizer_prompt] = {
    ThetaCaveat.DISJOINT_SCOPES: ThetaCaveatInfo(
        "θ mixes datasets that share no scale",
        "These cells measure more than one dataset, and a difficulty is only ever fitted within "
        "one, so θ here averages scales that never met. Read each dataset's cells on their own; "
        "a lift paired on the same cells still holds.",
    ),
    ThetaCaveat.COLD_RULER: ThetaCaveatInfo(
        "θ is not ability yet",
        "No difficulty ruler could be fitted from what this account measured before the round, "
        "so θ is plain accuracy on the logit scale. A candidate and the parent it is judged "
        "against are read on the cells both answered, so their lift holds; a θ compared across "
        "rounds or against another candidate's does not.",
    ),
    ThetaCaveat.FLAT_RULER: ThetaCaveatInfo(
        "θ is not ability here",
        "The ruler itself spans almost nothing, so every cell counts the same and θ is accuracy "
        "plus a constant. That is the instrument, not this round's draw — no round could have "
        "read wider.",
    ),
    ThetaCaveat.COLLAPSED_BAND: ThetaCaveatInfo(
        "θ is not ability this round",
        "This round bought a thin slice of a wide ruler. Inside a band that narrow every cell is "
        "equally hard, so ranking on θ ranks on accuracy. That is the draw, not the instrument.",
    ),
    ThetaCaveat.PRIOR_PINNED: ThetaCaveatInfo(
        "θ is not ability here",
        "The ruler gave most of this round's cells one shared difficulty, its prior, because "
        "every candidate that saw them answered the same way. That pinned value moves as the "
        "ruler takes in new rounds, so a higher θ than before can be the scale shifting, not the "
        "prompt improving. Compare within a round; don't read the level across rounds.",
    ),
    ThetaCaveat.UNMEASURED_DELTA: ThetaCaveatInfo(
        "θ skips some of these cells",
        "The ruler does not carry some of these cells, because no candidate outside this round's "
        "judging had answered them when the round opened. θ leaves them out, so it is read on "
        "fewer cells than the accuracy beside it, and two candidates can be read on different "
        "ones. A later round reads them once this round's answers have joined the ruler.",
    ),
    ThetaCaveat.FLOOR_PINNED: ThetaCaveatInfo(
        "θ reads nothing for an all-miss arm",
        "This arm missed every cell it answered. With no hit the fit has nothing to read: every "
        "all-miss arm lands on the same floor whatever cells it saw, so its θ, and any lift taken "
        "from it, is not a measurement. The ruler, the election and the other arms' θ are "
        "unaffected.",
    ),
}

_missing_caveat_info = set(ThetaCaveat) - set(THETA_CAVEAT_INFO)
if _missing_caveat_info:
    raise RuntimeError(
        f"THETA_CAVEAT_INFO is missing rows for {sorted(c.value for c in _missing_caveat_info)} "
        "(domain/ruler.py)."
    )


def theta_caveat(
    ruler: DeltaRuler | None, cells: Collection[str], *, scopes: int, all_miss: bool
) -> ThetaCaveat | None:
    """The one deriver, over the cells a θ was read on; the order is severity."""
    if all_miss:
        return ThetaCaveat.FLOOR_PINNED
    if scopes > 1:
        return ThetaCaveat.DISJOINT_SCOPES
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


@shapes_optimizer_prompt
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
        return {
            key: ((d, self.discrimination[key]) if key in self.discrimination else d)
            for key, d in self.delta.items()
        }

    @property
    def delta_span(self) -> float:
        """In logits; near zero, θ is logit-accuracy plus a constant, whatever the cell count."""
        return max(self.delta.values()) - min(self.delta.values()) if self.delta else 0.0

    def band_span(self, cells: Iterable[str]) -> tuple[float, float] | None:
        """``(round_span, ruler_span)``. Inside a thin band 1PL reduces to ``θ = logit(accuracy) + c``."""
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

    def entries_covering(self, cells: Iterable[str]) -> dict[str, RulerEntry]:
        """PoBB only, where the paired comparison absorbs it: an uncarried cell reads the prior centre, never 0.0."""
        out = self.entries()
        for key in cells:
            if key not in out:
                out[key] = self.mu_delta
        return out


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
    """Whether a scope has a δ ruler, and which one."""

    state: Literal["fitted", "not_fitted"]
    ruler_id: str | None = Field(
        description="The scale every θ and δ of this scope is read on (`RulerStamp.id`). "
        "Null while not fitted."
    )
    calibration_model: CalibrationModel | None = Field(description="Null while not fitted.")

    @classmethod
    def of(cls, ruler: DeltaRuler | None) -> RulerStanding:
        if ruler is None:
            return cls(state="not_fitted", ruler_id=None, calibration_model=None)
        return cls(
            state="fitted", ruler_id=ruler.anchor_id, calibration_model=ruler.calibration_model
        )

    @property
    def label(self) -> str | None:
        """What to print in place of every δ of the scope; ``None`` once the ruler is fitted."""
        return DELTA_STATE_LABEL[self.delta_state(True)]

    def delta_state(self, carried: bool) -> DeltaState:
        if self.state == "not_fitted":
            return DeltaState.NOT_FITTED
        return DeltaState.LINKED if carried else DeltaState.UNLINKED


class AbilityReading(FrozenModel):
    """A Rasch θ and the δ scale it was read on, held as one value because apart they mean nothing."""

    theta: float
    se: float | None
    ruler: RulerStamp
    # The cells θ was READ on: a reading on none is no reading.
    cells: int = Field(ge=1)
    # Many cells inside a narrow span is a WARM ruler reading flat.
    ruler_span: float | None
    # A thin round on a wide ruler and a wide round on a thin one read identical θ. ``None`` below two cells.
    round_span: float | None
    # ``None`` = the ruler is cold (flat δ) and θ is plain logit-accuracy — neither model.
    calibration_model: CalibrationModel | None
    # Stamped from `theta_caveat` at the one minting site, never re-derived; ``None`` = θ is ability.
    caveat: ThetaCaveat | None
    # The two counts `caveat` was decided on, beside it.
    unlinked: int
    pinned_share: float | None

    def comparable_to(self, other: AbilityReading) -> bool:
        return self.ruler.id == other.ruler.id

    def minus(self, other: AbilityReading) -> float | None:
        """θ less `other`'s θ; ``None`` where the two sit on different scales."""
        if not self.comparable_to(other):
            return None
        theta, base = self.theta, other.theta
        return theta - base

    def scale(self, *, named: bool = True) -> str:
        if self.ruler.cold:
            return f"{self.cells} cells, cold ruler"
        name = f"ruler {self.ruler.id} v{self.ruler.version}, " if named else ""
        return f"{name}{self.cells} cells, {self.calibration_model}"


# Well inside one round's θ SE: a plateau advises stopping, and a false stop costs more than a miss.
PLATEAU_THETA_BAND = 0.05
PLATEAU_ROUNDS = 3


_THETA_BAND_Z = NormalDist().inv_cdf(0.975)


def theta_band(theta: float | None, se: float | None) -> tuple[float, float] | None:
    if theta is None or se is None:
        return None
    return theta - _THETA_BAND_Z * se, theta + _THETA_BAND_Z * se


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
