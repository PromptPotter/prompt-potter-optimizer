"""The **record** — the realized lineage as the mask fold reads it.

A campaign is a *forest*: cycles linked parent→child by ``parent_cycle_id`` +
``fork_from_round``, each cycle a spine of round-winners. The mask fold
(:func:`promptpotter.application.mask.divergence.find_divergences`) walks this
forest and asks a verdict, per round, "would an alternative criterion have gone
differently here?". The record is the *read model* that walk needs — raw recorded
facts only, no scoring math: each candidate's measured rows, whether it was the
recorded winner, and whether it was a fair-measured eligible competitor. The
verdict (bound with a pipeline schema + the swapped criterion) supplies the math;
the record supplies the data; the fold supplies the traversal.

Pure data — no I/O, no schema. The loader that reads round files into this shape
lives beside it (the read-time service the API calls); these models are what it
produces and what the self-consistency test builds by hand. The verdict re-scores
from the **stored evaluator namespace** (a formula swap), so the record carries no
raw measurements and needs no ``PipelineSchema`` — which is never persisted.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class MaskCandidate(BaseModel):
    """One round candidate as the fold sees it — recorded facts, no derived score.

    ``evaluators`` is the candidate's stored, already-materialized per-round
    evaluator namespace (``accuracy``, ``latency_norm``, ``*_recall``, the self-heal
    rates, …) computed on the set it was actually measured on; the scoring verdict
    re-scores *this* under the swapped formula, reproducing the realized composite
    when fed the realizing formula. ``accuracy`` is the criterion-independent
    tiebreak of ``round_winner_key``. ``is_eligible`` is the recorded election
    eligibility — ``is_leader_eligible`` plus a guard against structurally-invalid /
    validation-failed candidates whose realized composite was forced to 0.0 (not
    honest competitors). Eligibility is a recorded fact, invariant under a *scoring*
    swap — the verdict reuses it verbatim, which is what makes the self-consistency
    gate hold by construction.
    """

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


class MaskRound(BaseModel):
    """One round on a cycle's spine — its candidates plus the anchor they ran against.

    ``anchor_evaluators`` / ``anchor_accuracy`` are the round's origin /
    carried-forward-winner stored values (round ``N``'s anchor = round ``N-1``'s
    elected winner; empty for the origin round 0). The realized winner is ``argmax
    round_winner_key`` over ``{anchor} ∪ eligible candidates`` — origin holds when no
    challenger beats it — so the verdict needs the anchor to reproduce the "origin
    held, no promotion" outcome under the swap.
    """

    model_config = ConfigDict(frozen=True)

    cycle_id: str
    round: int
    candidates: list[MaskCandidate] = Field(default_factory=list)
    anchor_evaluators: dict[str, float] = Field(default_factory=dict)
    anchor_accuracy: float = 0.0
    # The round's Rasch ability frontier (``RoundResult.cumulative_theta``) — the
    # backprop fold's value signal. Subset-invariant BY CONSTRUCTION, which is what
    # makes it the only honest thing to average up a lineage whose rounds scored
    # different sample subsets; accuracy is subset-relative and would drift.
    cumulative_theta: float = 0.0

    @property
    def node_key(self) -> str:
        """The lineage spine key — matches the frontend tree node id."""
        return f"{self.cycle_id}::r{self.round}"


class MaskCycle(BaseModel):
    """One cycle — a spine of rounds plus its edge to the parent it forked from.

    ``fork_from_round`` is the parent round this cycle was cut at (parent
    coordinates, even when the fork restarts its own numbering). ``None`` for roots
    and for forks whose origin round the index didn't record.
    """

    model_config = ConfigDict(frozen=True)

    cycle_id: str
    parent_cycle_id: str | None = None
    fork_from_round: int | None = None
    rounds: list[MaskRound] = Field(default_factory=list)


class MaskRecord(BaseModel):
    """The whole campaign forest the fold walks. Edges live on the cycles."""

    model_config = ConfigDict(frozen=True)

    cycles: list[MaskCycle] = Field(default_factory=list)


__all__ = ["MaskCandidate", "MaskCycle", "MaskRecord", "MaskRound"]
