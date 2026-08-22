"""The δ scale a θ is read on — the value, its identity, and the two ways to read it.

Domain rather than `intelligence/` because the ruler is PERSISTED, FORKED and STAMPED: it rides
the cycle ledger as a `RulerRecord`, a fork inherits its parent's, and every `RoundResult` names
the one it was read on. `intelligence/exploration.py` keeps the estimators that produce it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
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

# The identity a COLD ruler shares everywhere: flat δ means θ is plain logit-accuracy, which
# depends on no fit and is therefore comparable across cycles. A fitted ruler is not.
FLAT_RULER_ID = "flat"

# Which IRT model a cycle's δ ruler was fitted under. The ABSENCE of a member is the third,
# real state — a cold ruler is flat, so θ degenerates to logit-accuracy. Never collapse it
# into "1PL".
CalibrationModel = Literal["1PL", "2PL"]

__all__ = [
    "FLAT_RULER_ID",
    "CalibrationModel",
    "DeltaRuler",
    "Ruler",
    "RulerEntry",
    "anchor_id_of",
    "ruler_entry",
    "ruler_id",
]


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
        full = list(self.delta.values())
        return (max(on) - min(on), max(full) - min(full))

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


def ruler_id(ruler: DeltaRuler | None) -> str:
    """The scale a θ was read ON. Two θ readings are comparable iff these match."""
    return FLAT_RULER_ID if ruler is None else ruler.anchor_id


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
    # ``None`` = the ruler is cold (flat δ) and θ is plain logit-accuracy — neither model.
    calibration_model: CalibrationModel | None

    def comparable_to(self, other: AbilityReading) -> bool:
        return self.ruler_id is not None and self.ruler_id == other.ruler_id

    def scale(self) -> str:
        """The scale in words — the one rendering, so no surface reassembles it."""
        model = f", {self.calibration_model}" if self.calibration_model else ""
        return f"ruler {self.ruler_id or FLAT_RULER_ID}, {self.ruler_n} cells{model}"
