from __future__ import annotations

from typing import Literal, NamedTuple, get_args

from pydantic import Field

from promptpotter.domain.results import RoundResult
from promptpotter.domain.run_records import ResumeCheckpointRecord
from promptpotter.domain.strict_model import FrozenModel

LensKind = Literal["score", "dials", "abort"]
_LENS_KINDS: dict[str, LensKind] = {kind: kind for kind in get_args(LensKind)}

#: The `score:` formulas a picklist offers by name; any other is the operator's own expression.
SCORE_LENS_LABELS: dict[str, str] = {"accuracy": "Accuracy"}


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


class MaskReading(FrozenModel):
    composite_fitness: float
    accuracy: float | None
    # `accuracy` is the mean over exactly these.
    n_scored: int


class MaskCandidate(FrozenModel):
    """``is_eligible`` is a recorded fact, invariant under a scoring swap."""

    candidate_id: str
    reading: MaskReading | None = None
    is_selected: bool = False
    is_eligible: bool = True
    # An ``EliminationGate`` value; ``None`` ran to completion.
    abort: str | None = None


class MaskRound(FrozenModel):
    """``parent`` is round ``N-1``'s winner under the record's scorer; under a SAMPLE-SET mask, ``load.py::_parent``."""

    cycle_id: str
    round: int
    candidates: list[MaskCandidate] = Field(default_factory=list)
    parent: MaskReading | None = None
    # Carried only under ``with_replay``.
    round_data: RoundResult | None = None
    decisions: list[ResumeCheckpointRecord] = Field(default_factory=list)


class MaskCycle(FrozenModel):
    """``fork_from_round`` is in PARENT coordinates, even when the fork restarts its own numbering."""

    cycle_id: str
    parent_cycle_id: str | None = None
    fork_from_round: int | None = None
    rounds: list[MaskRound] = Field(default_factory=list)


class MaskRecord(FrozenModel):
    cycles: list[MaskCycle] = Field(default_factory=list)
    # The `per_cell` formula a fork applying the lens carries: a `dials:` lens realized.
    criterion: str | None = None


class SpineCycle(FrozenModel):
    """Every term is a round-CLOSE fact on the ledger, so a rewind is decided without opening one round file."""

    cycle_id: str
    parent_cycle_id: str | None = None
    fork_from_round: int | None = None
    # Subset-invariant, so it alone averages up a lineage whose rounds scored different subsets.
    # Every round the cycle closed is a node; ``None`` where its level does not difference.
    theta_by_round: dict[int, float | None]
