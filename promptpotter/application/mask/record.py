from __future__ import annotations

from typing import Literal, NamedTuple, get_args

from pydantic import ConfigDict, Field

from promptpotter.domain.results import RoundResult
from promptpotter.domain.run_records import ResumeCheckpointRecord
from promptpotter.domain.scoring import GradedCell
from promptpotter.domain.strict_model import StrictModel

LensKind = Literal["score", "dials", "abort"]
_LENS_KINDS: dict[str, LensKind] = {kind: kind for kind in get_args(LensKind)}


class Lens(NamedTuple):
    kind: LensKind
    body: str

    @property
    def spelling(self) -> str:
        return f"{self.kind}:{self.body}"


def parse_lens(value: str, *, allow_abort: bool) -> Lens:
    """A comparable LEVEL refuses ``abort:``: a gate switched off changes which candidates ran, not a score."""
    name, sep, body = value.partition(":")
    kind = _LENS_KINDS.get(name) if sep else None
    if kind is None or (kind == "abort" and not allow_abort):
        expected = (
            "'score:<formula>', 'dials:<term=weight,…>' or 'abort:<variant>'"
            if allow_abort
            else "'score:<formula>' or 'dials:<term=weight,…>'; an abort lens is a lineage-tree "
            "question, not a comparable level"
        )
        raise ValueError(f"Unknown lens: {value!r} (expected {expected})")
    return Lens(kind, body)


def parse_sample_ids(text: str | None) -> frozenset[int] | None:
    if not text or not text.strip():
        return None
    try:
        ids = frozenset(int(tok) for tok in text.split(",") if tok.strip())
    except ValueError as exc:
        raise ValueError(f"Invalid samples list: {text!r} ({exc})") from exc
    return ids or None


class MaskReading(StrictModel):
    model_config = ConfigDict(frozen=True)

    composite_fitness: float
    accuracy: float | None
    # `accuracy` is the mean over exactly these.
    n_scored: int


class MaskCandidate(StrictModel):
    """``is_eligible`` is a recorded fact, invariant under a scoring swap."""

    model_config = ConfigDict(frozen=True)

    candidate_id: str
    reading: MaskReading | None = None
    is_selected: bool = False
    is_eligible: bool = True
    # An ``EliminationGate`` value; ``None`` ran to completion.
    abort: str | None = None


class MaskRound(StrictModel):
    """``parent`` is round ``N-1``'s winner under the record's scorer; under a SAMPLE-SET mask, ``load.py::_parent``."""

    model_config = ConfigDict(frozen=True)

    cycle_id: str
    round: int
    candidates: list[MaskCandidate] = Field(default_factory=list)
    parent: MaskReading | None = None
    # Carried only under ``with_replay``; the pool is as it stood BEFORE this round ran.
    round_data: RoundResult | None = None
    known_outcomes: list[GradedCell] = Field(default_factory=list)
    decisions: list[ResumeCheckpointRecord] = Field(default_factory=list)


class MaskCycle(StrictModel):
    """``fork_from_round`` is in PARENT coordinates, even when the fork restarts its own numbering."""

    model_config = ConfigDict(frozen=True)

    cycle_id: str
    parent_cycle_id: str | None = None
    fork_from_round: int | None = None
    rounds: list[MaskRound] = Field(default_factory=list)


class MaskRecord(StrictModel):
    model_config = ConfigDict(frozen=True)

    cycles: list[MaskCycle] = Field(default_factory=list)
    # The `per_cell` formula a fork applying the lens carries: a `dials:` lens realized.
    criterion: str | None = None


class SpineCycle(StrictModel):
    """Every term is a round-CLOSE fact on the ledger, so a rewind is decided without opening one round file."""

    model_config = ConfigDict(frozen=True)

    cycle_id: str
    parent_cycle_id: str | None = None
    fork_from_round: int | None = None
    # Subset-invariant, so it alone averages up a lineage whose rounds scored different subsets.
    theta_by_round: dict[int, float] = Field(default_factory=dict)


__all__ = [
    "Lens",
    "MaskCandidate",
    "MaskCycle",
    "MaskReading",
    "MaskRecord",
    "MaskRound",
    "SpineCycle",
    "parse_lens",
    "parse_sample_ids",
]
