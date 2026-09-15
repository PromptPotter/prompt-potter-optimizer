from __future__ import annotations

from typing import Literal, NamedTuple

from pydantic import Field

from promptpotter.application.mask.load import parse_sample_ids
from promptpotter.domain.cycle_paths import (
    CycleHop,
    CyclePath,
    decode_cycle_path,
    encode_cycle_path,
)
from promptpotter.domain.ruler import AbilityReading
from promptpotter.domain.strict_model import StrictModel

SubjectKind = Literal["campaign", "course", "candidate"]

# How many path segments each kind addresses. The parse is arity-checked off this, so a kind added
# here without a resolver fails at the door rather than resolving to the wrong depth.
_SUBJECT_ARITY: dict[SubjectKind, int] = {"campaign": 1, "course": 2, "candidate": 3}

# The one lens a comparable LEVEL can be read under. `abort:` is deliberately absent: switching a
# PoBB gate off changes which candidates ran to term, not what any of them scored, so it decorates
# the lineage tree and has no per-cell value to plot here.
LENS_SCORE_PREFIX = "score:"


class SubjectSpec(NamedTuple):
    """One addressed subject, plus the MASK it is read under. INTERNAL — what crosses the
    wire is the ``key`` spelling and, coming back, a :class:`SubjectReading`.

    The mask is part of the ADDRESS, not a second query parameter, which is what lets one read
    carry a course beside the same course under a different formula: two channels, two keys, one
    selection. Without it the mask would be selection-wide and the comparison the operator wants —
    the record against the counterfactual — would need two page loads to see.
    """

    kind: SubjectKind
    campaign_id: str
    cycle_id: str = ""
    candidate_id: str = ""
    # The sandbox chain the address lives INSIDE — empty for a top-level campaign, one hop per L4
    # recursion below it. An inner cycle is a cycle in a tree of its own, so every resolver here
    # works on it unchanged once the store has descended; without it the whole of a
    # `promptpotter-self` tree is unaddressable.
    inside: CyclePath = ()
    # `score:<formula>`. Course-only: a campaign is an origin no election reaches, and a
    # candidate is one point rather than a chain, so neither has an election to re-decide.
    lens: str = ""
    samples: frozenset[int] | None = None

    @property
    def key(self) -> str:
        """The canonical spelling — what was asked for, what the reading is stamped with, and what
        the pairwise table refers to. One string, so nothing joins on a tuple it re-derived."""
        addressed = [p for p in (self.campaign_id, self.cycle_id, self.candidate_id) if p]
        parts = [f"{self.kind}:{'/'.join(addressed)}"]
        # WHERE first, then how to read it: the address half of the segments before the mask half.
        if self.inside:
            parts.append(f"in={encode_cycle_path(self.inside)}")
        if self.lens:
            parts.append(f"lens={self.lens}")
        if self.samples:
            parts.append("samples=" + ",".join(str(s) for s in sorted(self.samples)))
        return ";".join(parts)


def parse_subject(spec: str) -> SubjectSpec:
    """``kind:<campaign>[/<cycle>[/<candidate>]][;in=<c::y~…>][;lens=score:…][;samples=1,2,3]``.

    ``in=`` names the sandbox chain the address lives inside — the same ``campaign::cycle`` codec
    the read side's ``?descend=`` uses, because it is the same question. Without it every L4 inner
    run is unaddressable, which on a ``promptpotter-self`` campaign is almost the whole tree.

    Raises ``ValueError`` on anything unresolvable, for each entry point to turn into its own kind
    of refusal — a 400 on the route, a printed line on the terminal.

    ``;`` separates the segments because it cannot appear in a safe-AST formula, so a lens needs
    no escaping and the address stays one readable URL parameter.
    """
    address, *segments = spec.split(";")
    kind, sep, rest = address.partition(":")
    if not sep or kind not in _SUBJECT_ARITY:
        raise ValueError(
            f"Unknown subject kind in {spec!r} (expected one of {sorted(_SUBJECT_ARITY)}, "
            "as `kind:<campaign>[/<cycle>[/<candidate>]]`)."
        )
    parts = rest.split("/")
    arity = _SUBJECT_ARITY[kind]
    if len(parts) != arity or not all(parts):
        raise ValueError(
            f"Subject {spec!r} addresses {len([p for p in parts if p])} id(s); a "
            f"{kind!r} subject takes exactly {arity}."
        )
    lens, samples, inside = "", None, CyclePath()
    for segment in segments:
        name, _, value = segment.partition("=")
        if name == "in":
            inside = decode_cycle_path(value)
        elif name == "lens":
            if not value.startswith(LENS_SCORE_PREFIX):
                raise ValueError(
                    f"Unknown lens {value!r} on {spec!r} (expected "
                    f"'{LENS_SCORE_PREFIX}<formula>'; an abort lens is a lineage-tree question, "
                    "not a comparable level)."
                )
            lens = value
        elif name == "samples":
            samples = parse_sample_ids(value)
        else:
            raise ValueError(
                f"Unknown subject segment {segment!r} on {spec!r} "
                "(expected 'in=', 'lens=' or 'samples=')."
            )
    if lens and kind != "course":
        raise ValueError(
            f"A {kind!r} subject takes no lens: an alternative formula re-decides ELECTIONS, and "
            "only a course has any. Address the branch instead."
        )
    ids = [*parts, "", ""]
    return SubjectSpec(kind, ids[0], ids[1], ids[2], inside=inside, lens=lens, samples=samples)


class SubjectMask(StrictModel):
    """The mask this channel is read under, echoed back. Served rather than left implicit in the
    key, so a chart legend can say what a channel IS without re-splitting an address."""

    lens: str | None
    samples: list[int] | None


class ScenarioReading(StrictModel):
    """What the mask did to this branch: how far it agrees with the record, and the round it stops.

    The chain ENDS where the two readings part (`mask/scenario.py`), so the pair of winners below is
    one round's disagreement — both read at ``first_divergent_round``, or at the branch's last round
    where there is none. Reading a mid-chain counterfactual against the branch's final crown instead
    would compare two different rounds and report the gap between them as a change.

    ``note`` carries the caveat as a SERVED FACT, the way ``Comparability.note`` does — because the
    honest limit of a lens is not something a surface can be trusted to remember.
    """

    # Both read at the round the chain ends on, and equal where the two readings never part.
    recorded_winner_id: str | None
    scenario_winner_id: str | None
    winner_changed: bool
    # Where the two part — and the round a fork applying this formula is minted at, which is the
    # same fact. `None` = they never part within this branch.
    first_divergent_round: int | None
    # Rounds before that point — the prefix both readings agree on, and the honest measure of how
    # much of this branch a formula change leaves standing.
    invariant_rounds: int
    total_rounds: int
    # How many cells the head was actually read over once the sample mask was applied. Served
    # beside the subject's own `n_cells` because "17 of 28" is the question the mask was asked.
    n_samples_scored: int
    note: str


class WinnerChainPoint(StrictModel):
    """One step of the branch standing behind a subject — the winner chain from the origin up to
    its head, each point read on ITS OWN cells under the selected metric. Opt-in
    (``include_winner_chain``), because every point past the origin opens a round document.

    **Named for the chain, never "trajectory".** The subsets move between rounds, so this reads
    each point on the evidence that point actually had; the round's own `overlap` line is the
    OPPOSITE basis — that same chain on one shared set of cells. Two readings of one sequence
    that disagree by construction, and under one word a reader could tell them apart from
    neither name. The word survives where a series genuinely is one (`p_best_trajectory`,
    `parent_level_trajectory`, the Sample-trajectory grid)."""

    candidate_id: str
    round: int
    label: str
    value: float | None
    ci_lo: float | None
    ci_hi: float | None
    n_cells: int


class SubjectReading(StrictModel):
    """One subject, read under the selected metric — its identity, its per-cell values and the one
    estimate they merge to. ONE row, because a roster row and a metric reading that live in separate
    lists can disagree about the same subject, and under the default metric they held the same
    number reached two ways.

    ``values`` is keyed by the cell's QUERY, the identity that survives across campaigns; a cell the
    metric cannot read is ABSENT from it and counted in ``n_unscorable`` rather than scored — the
    two absences are different facts and a surface renders them as different glyphs.
    ``ci_lo``/``ci_hi`` are ``None`` below two scored cells — one reading has no spread, and a
    bracket drawn from it is a fiction.
    """

    # The canonical subject spelling (``SubjectSpec.key``) — what was asked for, what the pairwise
    # table refers to and what a series keys on. The three ids below are the same address parsed
    # out, carried so no consumer re-splits the string.
    key: str
    kind: SubjectKind
    # The sandbox chain this subject lives INSIDE, root-first — empty at the top level, one hop
    # per L4 recursion below it. The three ids below name the LEAF only, so this is what completes
    # the address: prepended to ``(campaign_id, cycle_id)`` it is the node's full path in the
    # served tree, and it is what a re-addressing surface appends a mask to.
    inside: list[CycleHop]
    campaign_id: str
    # RESOLVED, not echoed: the cycle these rows were read in, and the ONE searchpoint they came
    # off. A campaign resolves to its root cycle's origin arm and a course to the winner its last
    # election crowned, so "which point am I looking at" is answerable without asking for the
    # whole winner chain.
    cycle_id: str
    candidate_id: str
    # What to CALL this channel — the campaign, the branch, or the searchpoint. Deliberately not
    # the resolved point's label for a course: two branches of one campaign are what a course
    # comparison is about, and naming both by their current winner hides which is which.
    label: str
    dataset_name: str
    created_at: str
    # Whether THIS subject's absolute level sits on the same scale as the rest of the selection —
    # served rather than derived per surface, so the strike-through and the note cannot disagree.
    # ``None`` is UNKNOWN (an unstamped ruler), which is not ``True`` and must never render as it.
    comparable: bool | None
    # WHY, as the sentence to show — empty unless ``comparable`` is False. Served for the same
    # reason `Comparability.note` is: the two ways of failing are not one fact worded twice. A
    # different RULER still pairs cell by cell and only its level moves; a different DATASET
    # shares no cell at all, and a surface that guessed one sentence for both told the operator
    # their two subjects overlapped when nothing did.
    comparable_note: str
    # The mask this channel is read under, and what it did to the branch. Both ``None`` on an
    # unmasked channel — the record read as it stands.
    mask: SubjectMask | None
    scenario: ScenarioReading | None
    # The winner chain behind this subject, origin-first. ``None`` unless asked for.
    winner_chain: list[WinnerChainPoint] | None
    # WHAT this searchpoint IS, as against what it scored: one flat ``key -> rendered value`` map
    # over the RESOLVED config (`node.param`) plus the prompt fields. ``None`` unless asked for —
    # a prompt field is the largest thing this read can put on the wire, and a comparison of four
    # channels carries four of them. Resolved rather than the sparse override, because two
    # searchpoints from different campaigns share no delta to line up.
    config: dict[str, str] | None
    # The configuration the subject's own CYCLE ran under, hashed off round 0's
    # `optimizer_prompt_hashes`. Two campaigns sharing it are replicates of one arm however much
    # else differs, which is the fact a roster listing campaigns cannot show. `None` where round 0
    # carries no hashes: the arm is UNKNOWN, which groups with nothing — least of all with every
    # other unstamped campaign.
    arm_id: str | None
    # The connector's measurement-identity fingerprint — the RULER the arm was read against, moved
    # by any inner prompt, panel prose, layout or estimator edit. Two campaigns sharing an arm but
    # not this are NOT replicates: their spread is code drift wearing a noise label.
    instrument_id: str | None
    # The cycle origin's own reading, carried for the scale on it: subjects whose rulers differ
    # measured on different scales, so their ABSOLUTE levels are not one quantity however well
    # paired the cells are. `None` where round 0 carries no reading at all.
    ability: AbilityReading | None
    # The round the resolved point sits at. Every kind resolves to exactly ONE searchpoint, so
    # this is always answerable — and it is the only round number here that describes the SUBJECT
    # rather than the cycle it was found in.
    round: int
    # The CYCLE's figures, and named for it because neither narrows to the point above: the spend
    # is `dashboard.json`'s roll-up, cumulative-from-seed (so a fork carries what it inherited),
    # and the count is a glob of the cycle's whole `rounds/` dir. A candidate addressed at round 2
    # of a six-round branch is one point of six, and reading either as its own cost or its own
    # depth is the misreading these names exist to refuse.
    cycle_spend_usd: float | None
    cycle_rounds_scored: int
    # `round -> USD spent by the end of it`, cumulative, for THIS cycle's own ledger. What lets a
    # surface answer "what had it cost to get to the point I am looking at" as the operator walks
    # the branch, which no single scalar can: the pick moves in the browser and the read does not.
    spend_to_round: dict[str, float]
    values: dict[str, float]
    value: float | None
    ci_lo: float | None
    ci_hi: float | None
    n_cells: int
    # The cells this subject MEASURED and this metric cannot read. Named, not counted: a cell
    # blank on the chart is either this or a cell the subject never measured, and only naming
    # them lets a surface render the two as the different facts they are.
    unscorable_cells: list[str]
    # Where this subject sits on each factor the selection varies on — its address in the grid,
    # over the keys `Evidence.factors` discovered. Empty where nothing varies. It carries only the
    # VARYING keys, which is what makes it a coordinate rather than a second copy of `config`.
    levels: dict[str, str] = Field(default_factory=dict)


__all__ = [
    "LENS_SCORE_PREFIX",
    "ScenarioReading",
    "SubjectKind",
    "SubjectMask",
    "SubjectReading",
    "SubjectSpec",
    "WinnerChainPoint",
    "parse_subject",
]
