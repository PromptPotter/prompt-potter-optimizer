"""The **record** — the realized lineage as the mask fold reads it: raw recorded facts, no scoring
math. The verdict supplies the math, the record the data, the fold the traversal. Pure data, no I/O."""

from __future__ import annotations

from typing import Any

from pydantic import ConfigDict, Field

from promptpotter.domain.results import RoundResult
from promptpotter.domain.strict_model import StrictModel


class MaskCandidate(StrictModel):
    """One round candidate as the fold sees it; ``evaluators`` is the STORED namespace over the set it was
    measured on. ``is_eligible`` is a recorded fact, invariant under a scoring swap — hence the gate."""

    model_config = ConfigDict(frozen=True)

    candidate_id: str
    evaluators: dict[str, float] = Field(default_factory=dict)
    accuracy: float = 0.0
    # How many samples this candidate carries a SCOREABLE verdict for — the full measured set,
    # or the intersection with the sample-set mask. Lets a subset re-score serve an honest
    # "n of N": a candidate that never ran some chosen samples, or errored on them, reads a
    # smaller n, and `accuracy` beside it is the mean over exactly these.
    n_scored: int = 0
    is_winner: bool = False
    is_eligible: bool = True
    # Which PoBB gate cut this candidate's measurement early, if any — an ``EliminationGate``
    # value, or ``None`` (ran to completion). The abort verdict reads this; the scoring verdict
    # ignores it. Recorded fact, read off ``elimination_context.gate``, never re-derived.
    abort: str | None = None


class MaskRound(StrictModel):
    """One round on a cycle's spine. The parent is round ``N-1``'s elected winner (empty at round 0),
    and the verdict needs it to reproduce the "parent held, no promotion" outcome under a swap."""

    model_config = ConfigDict(frozen=True)

    cycle_id: str
    round: int
    candidates: list[MaskCandidate] = Field(default_factory=list)
    parent_evaluators: dict[str, float] = Field(default_factory=dict)
    parent_accuracy: float = 0.0
    # The recorded round itself, and the pool of known per-sample outcomes as it stood
    # BEFORE this round ran — the substrate a REPLAY verdict re-derives from, and the only
    # thing on this model that is a raw measurement rather than a summary of one. Carried
    # only when the caller asked (``load_mask_record(..., with_replay=True)``): a scoring or
    # abort lens reads neither, and a lens should not pay for rows it never touches.
    round_data: RoundResult | None = None
    known_outcomes: list[dict[str, Any]] = Field(default_factory=list)
    # This round's ledger decisions — the replay verdict re-derives them. Carried only under
    # ``with_replay``, like the two above: a scoring or abort lens never asks.
    decisions: list[dict[str, Any]] = Field(default_factory=list)


class MaskCycle(StrictModel):
    """One cycle — a spine of rounds plus its edge to the parent. ``fork_from_round`` is in PARENT
    coordinates, even when the fork restarts its own numbering."""

    model_config = ConfigDict(frozen=True)

    cycle_id: str
    parent_cycle_id: str | None = None
    fork_from_round: int | None = None
    rounds: list[MaskRound] = Field(default_factory=list)


class MaskRecord(StrictModel):
    """The whole campaign forest the fold walks. Edges live on the cycles."""

    model_config = ConfigDict(frozen=True)

    cycles: list[MaskCycle] = Field(default_factory=list)


class SpineCycle(StrictModel):
    """One cycle reduced to what UCB needs. Every term is a round-CLOSE fact already on the
    ledger, so a rewind is decided without opening one round document — where the mask record
    opens every one of them, for every cycle in the campaign, on the escalation path."""

    model_config = ConfigDict(frozen=True)

    cycle_id: str
    parent_cycle_id: str | None = None
    fork_from_round: int | None = None
    # round -> Rasch ability frontier. Subset-invariant BY CONSTRUCTION, which is what makes it
    # the only honest thing to average up a lineage whose rounds scored different subsets.
    theta_by_round: dict[int, float] = Field(default_factory=dict)


__all__ = ["MaskCandidate", "MaskCycle", "MaskRecord", "MaskRound", "SpineCycle"]
