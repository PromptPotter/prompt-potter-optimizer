"""The **record** — the realized lineage as the mask fold reads it: every arm's rows already read
under ONE scorer (the lens, or each cycle's own) — and the sample-set mask's parser. No I/O."""

from __future__ import annotations

from typing import Any

from pydantic import ConfigDict, Field

from promptpotter.domain.results import RoundResult
from promptpotter.domain.strict_model import StrictModel


def parse_sample_ids(text: str | None) -> frozenset[int] | None:
    """The sample-set mask as a CALLER names it — a comma-separated id list. Empty / unset ⇒
    ``None``, the full-set mask; a non-integer token raises ``ValueError`` for the entry point to
    turn into its own refusal. Here rather than at either edge, because both the lineage-tree
    route and an evidence subject address the same mask and may not spell it two ways."""
    if not text or not text.strip():
        return None
    ids = frozenset(int(tok) for tok in text.split(",") if tok.strip())
    return ids or None


class MaskReading(StrictModel):
    """One arm's rows graded and folded — what a fresh run under the same scorer reports for it."""

    model_config = ConfigDict(frozen=True)

    composite_fitness: float
    accuracy: float | None
    # How many samples the reading carries a verdict for — the full measured set, or its
    # intersection with the sample-set mask; `accuracy` is the mean over exactly these.
    n_scored: int


class MaskCandidate(StrictModel):
    """One round candidate as the fold sees it. ``reading`` is ``None`` where no row of it carries
    a verdict under the scorer. ``is_eligible`` is a recorded fact, invariant under a scoring swap."""

    model_config = ConfigDict(frozen=True)

    candidate_id: str
    reading: MaskReading | None = None
    is_selected: bool = False
    is_eligible: bool = True
    # Which PoBB gate cut this candidate's measurement early, if any — an ``EliminationGate``
    # value, or ``None`` (ran to completion). The abort verdict reads this; the scoring verdict
    # ignores it. Recorded fact, read off ``elimination_context.gate``, never re-derived.
    abort: str | None = None


class MaskRound(StrictModel):
    """One round on a cycle's spine. ``parent`` is round ``N-1``'s elected winner read under the
    record's scorer (``None`` at round 0), which the verdict needs to reproduce "parent held".

    Under a SAMPLE-SET mask it is read off the round's own ``reference_results`` instead
    (``load.py::_parent``), so the bar sits on the same cells as the arms; ``None`` means the round
    banked no parent panel and cannot be decided on a subset at all."""

    model_config = ConfigDict(frozen=True)

    cycle_id: str
    round: int
    candidates: list[MaskCandidate] = Field(default_factory=list)
    parent: MaskReading | None = None
    # The recorded round itself, and the pool of known per-sample outcomes as it stood
    # BEFORE this round ran — the substrate a REPLAY verdict re-derives from. Carried
    # only when the caller asked (``load_mask_record(..., with_replay=True)``): a scoring or
    # abort lens reads neither.
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
    # The `per_cell` formula every cycle here was read under, where the read asked for one — a
    # `dials:` lens realized, a `score:` one as given. What a fork applying the lens carries.
    criterion: str | None = None


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


__all__ = [
    "MaskCandidate",
    "MaskCycle",
    "MaskReading",
    "MaskRecord",
    "MaskRound",
    "SpineCycle",
    "parse_sample_ids",
]
