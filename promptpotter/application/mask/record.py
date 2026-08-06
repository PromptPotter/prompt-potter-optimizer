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
    # How many samples this candidate was actually scored on — the full measured set,
    # or the intersection with the sample-set mask. Lets a subset re-score serve an
    # honest "n of N" (a candidate that never ran some chosen samples reads a smaller n).
    n_scored: int = 0
    is_winner: bool = False
    is_eligible: bool = True
    # Which PoBB abort contributor cut this candidate's measurement early, if any:
    # ``"epsilon"`` (ε-elimination) | ``"lock_in"`` (leader lock-in) | ``None``
    # (ran to completion). The abort verdict reads this; the scoring verdict ignores
    # it. Recorded fact from ``elimination_context.leader_locked``.
    abort: str | None = None


class MaskRound(StrictModel):
    """One round on a cycle's spine. The anchor is round ``N-1``'s elected winner (empty at round 0), and
    the verdict needs it to reproduce the "origin held, no promotion" outcome under a swap."""

    model_config = ConfigDict(frozen=True)

    cycle_id: str
    round: int
    candidates: list[MaskCandidate] = Field(default_factory=list)
    anchor_evaluators: dict[str, float] = Field(default_factory=dict)
    anchor_accuracy: float = 0.0
    # The recorded round itself, and the pool of known per-sample outcomes as it stood
    # BEFORE this round ran — the substrate a REPLAY verdict re-derives from, and the only
    # thing on this model that is a raw measurement rather than a summary of one. Carried
    # only when the caller asked (``load_mask_record(..., with_replay=True)``): a scoring or
    # abort lens reads neither, and a lens should not pay for rows it never touches.
    round_data: RoundResult | None = None
    known_outcomes: list[dict[str, Any]] = Field(default_factory=list)
    # The round's Rasch ability frontier (``RoundResult.cumulative_theta``) — the
    # backprop fold's value signal. Subset-invariant BY CONSTRUCTION, which is what
    # makes it the only honest thing to average up a lineage whose rounds scored
    # different sample subsets; accuracy is subset-relative and would drift.
    cumulative_theta: float = 0.0

    @property
    def node_key(self) -> str:
        """The lineage spine key — matches the frontend tree node id."""
        return f"{self.cycle_id}::r{self.round}"


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


__all__ = ["MaskCandidate", "MaskCycle", "MaskRecord", "MaskRound"]
