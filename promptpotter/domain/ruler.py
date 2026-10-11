from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from enum import StrEnum
from statistics import NormalDist
from typing import Literal, NamedTuple

from pydantic import ConfigDict, Field

from promptpotter.domain.strict_model import StrictModel
from promptpotter.shared.hashing import stable_hash

# A bare δ is 1PL (a ≡ 1); a ``(δ, a)`` pair is 2PL.
RulerEntry = float | tuple[float, float]
Ruler = Mapping[int, RulerEntry]

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
    """A state in which θ is NOT ability; every number still renders, so WHICH fired is the fact."""

    # No δ scale: θ is logit-accuracy on the arm's own subset, comparable only to another cold θ.
    COLD_RULER = "cold_ruler"
    # The INSTRUMENT: the ruler itself spans almost nothing, so no draw could have been wider.
    FLAT_RULER = "flat_ruler"
    # The ACQUISITION: a thin slice of a wide ruler. Silent: the id matches and every number renders.
    COLLAPSED_BAND = "collapsed_band"
    # Most cells sit on a δ shared with other cells: the prior, whose pin MOVES as the ruler grows. Silent.
    PRIOR_PINNED = "prior_pinned"
    # θ skips cells the ruler does not carry, so it is read on fewer cells than accuracy. Round AND arm scope.
    UNMEASURED_DELTA = "unmeasured_delta"
    # Per-ARM: all-miss, so θ sits on the prior's floor and every LIFT taken against it reads 0.000.
    FLOOR_PINNED = "floor_pinned"


class ThetaCaveatInfo(NamedTuple):
    head: str
    body: str


THETA_CAVEAT_INFO: dict[ThetaCaveat, ThetaCaveatInfo] = {
    ThetaCaveat.COLD_RULER: ThetaCaveatInfo(
        "θ is not ability yet",
        "No difficulty ruler has been fitted, so θ is plain accuracy on the logit scale, read on "
        "each candidate's own cells. These θ compare to each other and to nothing else.",
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
        "ruler grows, so a higher θ than before can be the scale shifting, not the prompt "
        "improving. Compare within a round; don't read the level across rounds.",
    ),
    ThetaCaveat.UNMEASURED_DELTA: ThetaCaveatInfo(
        "θ skips some of these cells",
        "The ruler does not carry some of these cells, because no candidate already on the scale "
        "answered them. θ leaves them out, so it is read on fewer cells than the accuracy beside "
        "it, and two candidates can be read on different ones. A later round places a cell on the "
        "scale once such a candidate answers it.",
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
    *,
    calibration_model: CalibrationModel | None,
    round_span: float | None,
    ruler_span: float | None,
    unlinked: int,
    pinned_share: float | None = None,
) -> ThetaCaveat | None:
    """A ``None`` span or share is no verdict; the order is severity."""
    if calibration_model is None:
        return ThetaCaveat.COLD_RULER
    if round_span is not None and ruler_span is not None:
        if ruler_span <= BAND_COLLAPSE_LOGITS:
            return ThetaCaveat.FLAT_RULER
        if round_span <= max(BAND_COLLAPSE_LOGITS, BAND_COLLAPSE_RATIO * ruler_span):
            return ThetaCaveat.COLLAPSED_BAND
        if pinned_share is not None and pinned_share >= PRIOR_PINNED_RATIO:
            return ThetaCaveat.PRIOR_PINNED
    return ThetaCaveat.UNMEASURED_DELTA if unlinked else None


__all__ = [
    "BAND_COLLAPSE_LOGITS",
    "BAND_COLLAPSE_RATIO",
    "DELTA_STATE_LABEL",
    "THETA_CAVEAT_INFO",
    "CalibrationModel",
    "DeltaRuler",
    "DeltaState",
    "Ruler",
    "RulerEntry",
    "RulerStanding",
    "ThetaCaveat",
    "ThetaCaveatInfo",
    "anchor_id_of",
    "flat_ruler_id",
    "is_flat_ruler_id",
    "ruler_entry",
    "series_levels",
    "theta_band",
    "theta_caveat",
    "theta_plateau",
]


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
    delta: Mapping[int, float],
    mu_delta: float,
    sigma_delta: float,
    calibration_model: CalibrationModel,
) -> str:
    """Computed once at lock and carried verbatim: anchored extension grows the membership, not the id."""
    return stable_hash(
        [
            [[sid, delta[sid]] for sid in sorted(delta)],
            mu_delta,
            sigma_delta,
            calibration_model,
        ]
    )


class DeltaRuler(StrictModel):
    model_config = ConfigDict(frozen=True)

    delta: dict[int, float]
    delta_se: dict[int, float]
    # 2PL only; empty under 1PL, where a ≡ 1.
    discrimination: dict[int, float] = Field(default_factory=dict)
    mu_delta: float
    sigma_delta: float
    # Every later read must use THIS θ prior, or the extension is regularized apart from the anchor.
    sigma_theta: float
    calibration_model: CalibrationModel
    anchor_id: str

    def entries(self) -> dict[int, RulerEntry]:
        return {
            sid: ((d, self.discrimination[sid]) if sid in self.discrimination else d)
            for sid, d in self.delta.items()
        }

    @property
    def delta_span(self) -> float:
        """In logits; near zero, θ is logit-accuracy plus a constant, whatever the cell count."""
        return max(self.delta.values()) - min(self.delta.values()) if self.delta else 0.0

    def band_span(self, sample_ids: Iterable[int]) -> tuple[float, float] | None:
        """``(round_span, ruler_span)``. Inside a thin band 1PL reduces to ``θ = logit(accuracy) + c``."""
        on = [self.delta[sid] for sid in sample_ids if sid in self.delta]
        if len(on) < 2 or len(self.delta) < 2:
            return None
        return (max(on) - min(on), self.delta_span)

    def pinned_share(self, sample_ids: Iterable[int]) -> float | None:
        """A continuous fit produces no ties, so a shared δ is the PRIOR standing in for a measurement."""
        shared = {d for d, n in Counter(self.delta.values()).items() if n > 1}
        on = [self.delta[sid] for sid in sample_ids if sid in self.delta]
        if not on:
            return None
        return sum(1 for d in on if d in shared) / len(on)

    def unlinked(self, sample_ids: Iterable[int]) -> int:
        return sum(1 for sid in set(sample_ids) if sid not in self.delta)

    def entries_covering(self, sample_ids: Iterable[int]) -> dict[int, RulerEntry]:
        """PoBB only, where the paired comparison absorbs it: an uncarried id reads the prior centre, never 0.0."""
        out = self.entries()
        for sid in sample_ids:
            if sid not in out:
                out[sid] = self.mu_delta
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


class RulerStanding(StrictModel):
    """Whether a scope has a δ ruler, and which one."""

    model_config = ConfigDict(frozen=True)

    state: Literal["fitted", "not_fitted"]
    ruler_id: str | None = Field(
        description="The anchor every θ and δ of this scope is read on (`AbilityReading.ruler_id`). "
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


class AbilityReading(StrictModel):
    """A Rasch θ and the δ scale it was read on, held as one value because apart they mean nothing.

    A null ``ruler_id`` names NO scale: that reading is comparable to nothing.
    """

    model_config = ConfigDict(frozen=True)

    theta: float
    se: float | None
    ruler_id: str | None
    # The ruler grows by anchored extension, so the id alone cannot say how much scale was real.
    ruler_n: int
    # ...and the count cannot either: many cells inside a narrow span is a WARM ruler reading flat.
    ruler_span: float | None
    # A thin round on a wide ruler and a wide round on a thin one read identical θ. ``None`` below two cells.
    round_span: float | None
    # ``None`` = the ruler is cold (flat δ) and θ is plain logit-accuracy — neither model.
    calibration_model: CalibrationModel | None
    # Stamped from `theta_caveat` at the one minting site, never re-derived; ``None`` = θ is ability.
    caveat: ThetaCaveat | None

    def comparable_to(self, other: AbilityReading) -> bool:
        return self.ruler_id is not None and self.ruler_id == other.ruler_id

    def scale(self, *, named: bool = True) -> str:
        model = f", {self.calibration_model}" if self.calibration_model else ""
        name = f"ruler {self.ruler_id or 'unscaled'}, " if named else ""
        return f"{name}{self.ruler_n} cells{model}"


# Well inside one round's θ SE: a plateau advises stopping, and a false stop costs more than a miss.
PLATEAU_THETA_BAND = 0.05
PLATEAU_ROUNDS = 3


_THETA_BAND_Z = NormalDist().inv_cdf(0.975)


def theta_band(theta: float | None, se: float | None) -> tuple[float, float] | None:
    if theta is None or se is None:
        return None
    return theta - _THETA_BAND_Z * se, theta + _THETA_BAND_Z * se


def series_levels(readings: Sequence[AbilityReading | None]) -> list[float | None]:
    """The series scale is the first ruler any reading names; a θ off it is ``None``."""
    series = next((r for r in readings if r is not None and r.ruler_id is not None), None)
    return [
        r.theta if r is not None and series is not None and r.comparable_to(series) else None
        for r in readings
    ]


def theta_plateau(readings: Sequence[AbilityReading | None]) -> float | None:
    """Withheld under any ``ThetaCaveat``: a flat run of a θ that is not ability advises a stop on nothing."""
    recent = list(zip(readings, series_levels(readings), strict=True))[-PLATEAU_ROUNDS:]
    levels = [
        level
        for reading, level in recent
        if level is not None and reading is not None and reading.caveat is None
    ]
    if len(levels) < PLATEAU_ROUNDS:
        return None
    mean = sum(levels) / len(levels)
    return mean if all(abs(level - mean) < PLATEAU_THETA_BAND for level in levels) else None
