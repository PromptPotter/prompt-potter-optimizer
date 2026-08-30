"""The δ scale a θ is read on — the value, its identity, and the two ways to read it.

Domain rather than `intelligence/` because the ruler is PERSISTED, FORKED and STAMPED: it rides
the cycle ledger as a `RulerRecord`, a fork inherits its parent's, and every `RoundResult` names
the one it was read on. `intelligence/exploration.py` keeps the estimators that produce it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from enum import StrEnum
from typing import Literal

from pydantic import ConfigDict, Field

from promptpotter.domain.pipeline_schema import stable_hash
from promptpotter.domain.strict_model import StrictModel

# A difficulty-ruler entry is either a bare δ (1PL — discrimination ≡ 1) or a ``(δ, a)`` pair
# (2PL — per-sample discrimination ``a``). The richer 2PL value rides *inside* the same ruler
# mapping so every θ consumer reads one ``Ruler`` and the 1PL→2PL switch is invisible above
# ``fit_theta_given_delta`` (the seam).
RulerEntry = float | tuple[float, float]
Ruler = Mapping[int, RulerEntry]

# A COLD ruler's id, qualified by the OBJECTIVE θ was fit on: flat δ depends on no fit, so the
# objective is the only thing separating two cold readings. A FITTED ruler needs no qualifier —
# `anchor_id_of` hashes δ that were fit from the objective's own responses.
_FLAT_PREFIX = "flat"

# Which IRT model a cycle's δ ruler was fitted under. The ABSENCE of a member is the third,
# real state — a cold ruler is flat, so θ degenerates to logit-accuracy. Never collapse it
# into "1PL".
CalibrationModel = Literal["1PL", "2PL"]

# A round whose cells span less than this FRACTION of the ruler's own δ range is a collapsed band.
# Deliberately loose: it must catch the case `verdict-resolution.md` records without firing on an
# ordinary acquisition draw. A first estimate, to refine against banked rounds.
BAND_COLLAPSE_RATIO = 0.20
# ...and this many LOGITS absolutely. The ratio measures the round against the ruler, so on a ruler
# that is itself collapsed the yardstick is the thing under test and a wide-looking fraction of a
# narrow scale stays silent. Below this span θ is logit-accuracy plus a constant either way.
BAND_COLLAPSE_LOGITS = 1.0


class ThetaCaveat(StrEnum):
    """A state in which θ is NOT ability — decided beside the ruler, never in a view.

    All four render every number and raise nothing, so the reading looks identical to a sound one.
    WHICH one fired is the whole value, because the fix differs: one is an instrument, one is an
    acquisition, one is the absence of a scale, one is the arm itself. The ABSENCE of a caveat is
    the fifth state, and the only one where θ is ability.

    **Two SCOPES, one vocabulary.** The first three are facts about the ROUND's scale, decided by
    :func:`theta_caveat` and stamped on its ``AbilityReading``; ``FLOOR_PINNED`` is a fact about
    ONE ARM, decided by ``results.py::is_floor_pinned`` where that arm is scored. One enum because
    the question a reader asks is identical — *may I read this θ as ability?* — and a second
    vocabulary for it would be a synonym, not a channel.

    `docs/methods/verdict-resolution.md` § Reading a round."""

    # No δ scale at all — θ is plain logit-accuracy on whatever subset this arm answered, so two
    # readings are comparable to each other and to nothing else.
    COLD_RULER = "cold_ruler"
    # The INSTRUMENT: the ruler itself spans almost nothing, so no draw could have been wider.
    FLAT_RULER = "flat_ruler"
    # The ACQUISITION: a warm, wide ruler, and this round bought a thin slice of it. The silent
    # one — the ruler id matches, the cell count is healthy, and every number renders.
    COLLAPSED_BAND = "collapsed_band"
    # The ARM: it scored 0.0 on every cell it answered, so the fit has no response to separate
    # ability from the prior and θ settles on the floor the δ vector and n imply. Per-CANDIDATE,
    # so it rides the candidate row rather than the round's reading — and it is the one caveat
    # that makes a LIFT unreadable rather than a level: every lift measured against a floor
    # constant reads `0.000` whatever the arm did.
    FLOOR_PINNED = "floor_pinned"


def theta_caveat(
    *,
    calibration_model: CalibrationModel | None,
    round_span: float | None,
    ruler_span: float | None,
) -> ThetaCaveat | None:
    """Which of the three SCALE states this reading is in, or ``None`` where θ is genuinely
    ability on the evidence this function can see.

    The SOLE decision for those three: the served reading and the optimizer's ``confounds`` panel
    both call here, or the screen and the generator disagree about whether a number means anything.
    Spans below two cells arrive as ``None`` and are not a verdict — an unmeasurable band is not a
    narrow one.

    Never returns ``FLOOR_PINNED``: that one is a property of ONE ARM's responses, which are not an
    input here. A round can be sound by this function and still carry a floor-pinned arm.
    """
    if calibration_model is None:
        return ThetaCaveat.COLD_RULER
    if round_span is None or ruler_span is None:
        return None
    if ruler_span <= BAND_COLLAPSE_LOGITS:
        return ThetaCaveat.FLAT_RULER
    if round_span <= max(BAND_COLLAPSE_LOGITS, BAND_COLLAPSE_RATIO * ruler_span):
        return ThetaCaveat.COLLAPSED_BAND
    return None


__all__ = [
    "BAND_COLLAPSE_LOGITS",
    "BAND_COLLAPSE_RATIO",
    "CalibrationModel",
    "DeltaRuler",
    "Ruler",
    "RulerEntry",
    "ThetaCaveat",
    "anchor_id_of",
    "flat_ruler_id",
    "is_flat_ruler_id",
    "ruler_entry",
    "theta_caveat",
]


def flat_ruler_id(objective_id: str) -> str:
    """The scale of a COLD ruler, per the prefix above."""
    return f"{_FLAT_PREFIX}:{objective_id}"


def is_flat_ruler_id(value: str) -> bool:
    """Whether a STAMPED id names a cold ruler. A fitted anchor is a hex digest, so the prefix
    cannot collide with one."""
    return value.startswith(_FLAT_PREFIX)


def ruler_entry(value: RulerEntry) -> tuple[float, float]:
    """Split a ruler entry into ``(δ, a)``; a bare float is 1PL (a≡1)."""
    if isinstance(value, tuple):
        return float(value[0]), float(value[1])
    return float(value), 1.0


def anchor_id_of(
    delta: Mapping[int, float],
    mu_delta: float,
    sigma_delta: float,
    calibration_model: CalibrationModel,
) -> str:
    """The identity of the ANCHORING fit — computed once, at lock, and then carried verbatim.

    Deliberately NOT a hash of the current membership. Anchored extension adds cells without
    moving the ones already there, so a θ read on the smaller ruler and one read on the larger
    are on the same scale and must share an id. Hashing the membership would churn the id every
    round, make a cycle read as incomparable with ITSELF, and — because `evidence.py` reads round
    0's `ruler_id` into `Comparability` — poison cross-campaign comparison too.
    """
    return stable_hash(
        [
            [[sid, delta[sid]] for sid in sorted(delta)],
            mu_delta,
            sigma_delta,
            calibration_model,
        ]
    )


class DeltaRuler(StrictModel):
    """A cycle's locked δ scale, plus everything an anchored EXTENSION needs to add a cell to it
    without bending it: the prior the anchoring fit converged to (``mu_delta`` / ``sigma_delta``
    / ``sigma_theta``) and which model it was fit under. Those four were discarded before, which
    is precisely why the ruler could only ever be frozen or re-fit — never grown."""

    model_config = ConfigDict(frozen=True)

    delta: dict[int, float]
    delta_se: dict[int, float]
    # 2PL only; empty under 1PL, where a ≡ 1.
    discrimination: dict[int, float] = Field(default_factory=dict)
    mu_delta: float
    sigma_delta: float
    # The θ prior the anchoring fit converged to. Every later read must use THIS one, or the
    # extension is regularized differently from the anchor and the scale bends.
    sigma_theta: float
    calibration_model: CalibrationModel
    anchor_id: str
    anchored_at_round: int

    def entries(self) -> dict[int, RulerEntry]:
        """``{sid: δ}`` under 1PL, ``{sid: (δ, a)}`` where discrimination was estimated — the shape
        the θ seam reads. Covers exactly the cells this ruler carries, and nothing else."""
        return {
            sid: ((d, self.discrimination[sid]) if sid in self.discrimination else d)
            for sid, d in self.delta.items()
        }

    @property
    def delta_span(self) -> float:
        """Total δ range in logits — how much difficulty this scale can actually tell apart. Near
        zero means θ is logit-accuracy plus a constant however many cells the ruler carries."""
        return max(self.delta.values()) - min(self.delta.values()) if self.delta else 0.0

    def band_span(self, sample_ids: Iterable[int]) -> tuple[float, float] | None:
        """``(round_span, ruler_span)`` in logits — ``None`` below two cells on either side, where
        a span is not a reading.

        The collapsed-band check `docs/methods/verdict-resolution.md` § "A collapsed band" names
        and nothing performed. The acquisition buys the cells whose δ sits nearest the leader's θ,
        and against a wide bank that collapses onto a razor-thin range; inside one every cell is
        equally hard, so the 1PL fit reduces to ``θ = logit(accuracy) + c`` and the difficulty
        adjustment does no work. Unlike a cold ruler it is SILENT — the ruler is warm, the id
        matches, every number renders — which is why the state has to be computed to be seen.
        """
        on = [self.delta[sid] for sid in sample_ids if sid in self.delta]
        if len(on) < 2 or len(self.delta) < 2:
            return None
        return (max(on) - min(on), self.delta_span)

    def entries_covering(self, sample_ids: Iterable[int]) -> dict[int, RulerEntry]:
        """This ruler completed with a PROVISIONAL entry at its own centre (``mu_delta``, a=1) for
        each id it does not carry.

        One sanctioned caller: PoBB, which reads θ mid-round on cells the extension cannot have
        reached yet — extension needs the round's grades, which do not exist while it is running.
        A provisional δ at the centre is the honest prior for "unknown"; 0.0 is not, because zero
        is a POSITION on this scale and claims "easier than anything measured". The comparison
        stays paired on identical cells, so both arms see the identical δ vector and a constant
        misspecification cannot favour one.
        """
        out = self.entries()
        for sid in sample_ids:
            if sid not in out:
                out[sid] = self.mu_delta
        return out


class AbilityReading(StrictModel):
    """A Rasch θ and the δ scale it was read on — meaningless apart, so they are one value.
    ``ruler_id`` ``None`` names NO scale: that reading is comparable to nothing.

    Beside :class:`DeltaRuler` rather than in ``results.py`` because ``run_records.py`` needs it
    too, and ``results.py`` imports that module — the same constraint that put the ruler here.
    """

    model_config = ConfigDict(frozen=True)

    theta: float
    se: float | None
    ruler_id: str | None
    # The ruler grows by anchored extension, so the id alone cannot say how much scale was real.
    ruler_n: int
    # ...and the count cannot either: 600 cells inside a quarter-logit is a WARM ruler reading
    # flat, the one degenerate state that renders every number and says nothing. On disk beside
    # the θ it qualifies, so an operator surface can read it and not only the optimizer node.
    ruler_span: float | None
    # The δ span of the cells THIS reading was taken on. Beside `ruler_span` because the pair is
    # the reading: a thin round on a wide ruler and a wide round on a thin one are different
    # faults with identical θ. ``None`` below two cells, where a span is not a reading.
    round_span: float | None
    # ``None`` = the ruler is cold (flat δ) and θ is plain logit-accuracy — neither model.
    calibration_model: CalibrationModel | None
    # SERVED, never re-derived: which of the three states this θ is in, or ``None`` where it is
    # genuinely ability. Stamped from `theta_caveat` at the one minting site, so the browser and
    # the optimizer's `confounds` panel cannot disagree about whether a number means anything.
    caveat: ThetaCaveat | None

    def comparable_to(self, other: AbilityReading) -> bool:
        return self.ruler_id is not None and self.ruler_id == other.ruler_id

    def scale(self) -> str:
        """The scale in words — the one rendering, so no surface reassembles it."""
        model = f", {self.calibration_model}" if self.calibration_model else ""
        return f"ruler {self.ruler_id or 'unscaled'}, {self.ruler_n} cells{model}"
