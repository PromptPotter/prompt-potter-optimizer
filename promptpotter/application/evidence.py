"""What a SET of SUBJECTS jointly says — the read that answers "I ran this four times, now
what?". Recomputed from disk per read, never persisted, zero LLM calls, nothing crowned.

A **subject** is whatever a channel of the comparison is anchored on: a whole ``campaign`` (its
root origin), a ``course`` (one branch, read at its last elected winner), or a single ``candidate``
(one searchpoint). All three reduce to ``{cell: {channel: value}}`` through ``cell_channels``, so
everything below the resolution — the pairing, the decomposition, the power, the confound — is one
arithmetic over one shape and knows nothing about which kind produced it.

Two halves, and the origin half is the one that keeps being needed: a campaign stopped before its
first L1 round still has an origin panel, a ruler and a spend, so the roster, the comparability
check, the variance decomposition and the confound flags all answer. The ranking needs a scored
candidate, walks every round document, and is therefore opt-in (``include_ranking``) — as is the
per-subject ``trajectory``, for the same reason.

**No L4 anywhere in here.** A cell is whatever the campaign scored one row against — an inner
campaign on the recursion, a sample everywhere else — and `evidence_metrics.py::cell_channels` is
the only path from a row to a number, reading the L4 proxy where it exists and the row's own
fitness otherwise. Same arithmetic either way (`scoring/selection.py::matched_parent_lift` states
the same rule for the per-round interval).

Everything is in the SELECTED metric's own units, never composite fitness: fitness is what a
campaign's own `scoring` formula made of the measurand, so pooling on it averages numbers produced
by different formulas and calls the result one quantity.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, NamedTuple

from pydantic import Field

from promptpotter.application.evidence_metrics import (
    MEASURAND,
    MetricSpec,
    available_channels,
    catalogue_for,
    cell_channels,
    resolve_metric,
)
from promptpotter.application.mask.load import load_mask_record, parse_sample_ids
from promptpotter.application.mask.scenario import scenario_spine
from promptpotter.application.scoring.formula import compile_round_scorer
from promptpotter.application.scoring.formula.compiler import (
    CompiledExpression,
    ScoringFormulaError,
)
from promptpotter.domain.candidate_diff import build_candidate_flat, flatten_sp_summary
from promptpotter.domain.cycle_paths import (
    CycleHop,
    CyclePath,
    decode_cycle_path,
    encode_cycle_path,
)
from promptpotter.domain.ruler import AbilityReading
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.store.account_spend import record_cost_usd
from promptpotter.infrastructure.store.campaign_store.ledger_scan import scan_ledger_elections
from promptpotter.infrastructure.store.io import read_json_tolerant
from promptpotter.infrastructure.store.layout import ROUND_GLOB, CycleLayout, campaign_cycles_dir
from promptpotter.infrastructure.store.read_model import iter_jsonl
from promptpotter.infrastructure.store.stores import descend_store
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.errors import BadRequestError, NotFoundError
from promptpotter.shared.statistics import (
    cells_for_exact_verdict,
    exact_p_floor,
    exact_paired_reading,
    holm_adjusted,
    mean_ci_t,
    min_detectable_effect,
    paired_diff_posterior,
    rank_correlation,
    sample_sd,
    two_way_effect_sds,
)

if TYPE_CHECKING:
    from promptpotter.infrastructure.store.stores import Stores

_ORIGIN_HASH = "origin"
# |rho| at or above this and the roster's ordering IS its chronology — see `OrderConfound`.
_ORDER_CONFOUND_RHO = 0.9

ComparabilityReason = Literal["one_ruler", "rulers_differ", "ruler_unstamped", "datasets_differ"]

SubjectKind = Literal["campaign", "course", "candidate"]

# How many path segments each kind addresses. The parse is arity-checked off this, so a kind added
# here without a resolver fails at the door rather than resolving to the wrong depth.
_SUBJECT_ARITY: dict[SubjectKind, int] = {"campaign": 1, "course": 2, "candidate": 3}

# The one lens a comparable LEVEL can be read under. `abort:` is deliberately absent: switching a
# PoBB gate off changes which candidates ran to term, not what any of them scored, so it decorates
# the lineage tree and has no per-cell value to plot here.
_LENS_SCORE_PREFIX = "score:"


class SubjectSpec(NamedTuple):
    """One addressed subject, plus the WHAT-IF mask it is read under. INTERNAL — what crosses the
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
    # works on it unchanged once the store has descended; this is the only thing that was missing,
    # and without it the whole of a `promptpotter-self` tree was unaddressable.
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
    run was unaddressable, which on a ``promptpotter-self`` campaign is almost the whole tree.

    Raises ``ValueError`` on anything unresolvable, for each entry point to turn into its own kind
    of refusal — a 400 on the route, a printed line on the terminal. Spelled ONCE here so the CLI
    and the browser cannot address subjects in two grammars.

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
            if not value.startswith(_LENS_SCORE_PREFIX):
                raise ValueError(
                    f"Unknown lens {value!r} on {spec!r} (expected "
                    f"'{_LENS_SCORE_PREFIX}<formula>'; an abort lens is a lineage-tree question, "
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


# --- the ranking: what a scored EDIT is worth (needs round >= 1, and is opt-in) ---


class CellEffect(NamedTuple):
    """One environment cell's paired (candidate − origin) effect. INTERNAL to ``_finalize`` — the
    two numbers a surface renders, ``n_cells`` and ``n_measurements``, are folded from it here."""

    cell: str
    mean_d: float
    n: int


class EffectProvenance(StrictModel):
    """Where one occurrence of an edit was measured on disk."""

    campaign_id: str
    cycle_id: str
    round: int
    candidate_id: str


class RankedEdit(StrictModel):
    """One unique candidate state — a ``pipeline_params_override`` — aggregated across every
    occurrence in the selection. An L1 target-prompt edit on an ordinary campaign, an
    optimizer-prompt edit on the recursion; the arithmetic does not care which.

    It pools by EDIT IDENTITY, so it earns its keep where the same edit recurs across campaigns.
    That is routine on the recursion and rarer elsewhere, where most rows will carry one campaign's
    cells and an interval to match.
    """

    state_hash: str
    label: str
    # Neither the edit's own text nor its per-cell breakdown rides here: a {node: {field: prose}}
    # map per ranked edit is the largest thing this read can put on the wire, and no surface opens
    # it. `state_hash` names the edit and `provenance` says where to read it.
    provenance: list[EffectProvenance]
    anchor_effect: float  # mean of the PER-CELL paired diffs — one point per cell, not per
    # occurrence, so an over-measured cell cannot outweigh uniform goodness (see _finalize)
    ci_lo: float | None
    ci_hi: float | None
    n_cells: int
    n_measurements: int


class EditSpread(StrictModel):
    """How far apart the ranked edits actually are — the SD of ``anchor_effect`` across them.

    **Not a signal-to-noise ratio, and deliberately not one.** The noise half would be repeated
    readings of ONE (edit, cell), and the instrument cannot produce them: measurements are
    content-addressed, so a second ask replays the first answer and its spread is zero by
    construction, which reads as a perfect instrument. A replicate ARM is the honest noise reading
    (see :class:`ArmReplicate`); measuring one candidate HARDER is ``verify``'s job. ``None`` when
    fewer than two edits have been measured.
    """

    edit_effect_sd: float | None = None
    n_edits: int = 0


# --- the subject half: what each channel of the comparison is worth on its own cells ---


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


class TrajectoryPoint(StrictModel):
    """One step of the branch standing behind a subject — the winner chain from the origin up to
    its head, each point read on ITS OWN cells under the selected metric. Opt-in
    (``include_trajectory``), because every point past the origin opens a round document."""

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
    # whole trajectory.
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
    trajectory: list[TrajectoryPoint] | None
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


class PairwiseComparison(StrictModel):
    """One unordered pair, blocked on the cells BOTH subjects scored — pairing removes cell
    difficulty instead of carrying it as noise, which is the same reason ``matched_parent_lift``
    pairs rather than differencing two means.

    ``a`` precedes ``b`` in the roster's oldest-first order, so ``median_shift = b - a`` has one
    reading across the whole table. The interval and both p-values are ``None`` below two shared
    cells: nothing was tested there, which a ``1.0`` would misreport as a test that found nothing.

    The test is EXACT (``exact_paired_reading``), never Student-t: at the widths a panel runs, a t
    p can sit below what any exact test on that many pairs is able to return, which is resolution
    taken from the assumed tail rather than from the cells. ``EvidencePower.exact_p_floor`` says
    which verdicts the width can reach at all, before a cell is spent.
    """

    subject_a: str
    subject_b: str
    # Hodges-Lehmann: the median of the pairwise Walsh averages, not the mean of the differences.
    # One outlier cell moves the mean by 1/n of itself and moves this by nothing.
    median_shift: float
    ci_lo: float | None
    ci_hi: float | None
    p_value: float | None
    # Holm-Bonferroni across every pair in THIS read that carries a p. Served beside the raw value
    # rather than replacing it, so the correction is visible instead of baked in.
    p_adjusted: float | None
    n_cells: int


class MetricReading(StrictModel):
    """The selection read under ONE metric, echoed back with the vocabulary it was chosen from — a
    stale render cannot then show new bars under the old metric's label."""

    spec: MetricSpec
    catalogue: list[MetricSpec]
    # The channel names an expression may use ON THIS SELECTION — served rather than documented,
    # so a composed metric cannot name a term the selection would silently read as zero. It is the
    # INTERSECTION: a channel only some subjects carry would compare one side against nothing.
    namespace: list[str]
    # The cells EVERY subject SCORED under this metric — a subset of the measured intersection,
    # because a cell can be measured and still be unreadable here. The PAIRING and the variance
    # split are over this set, and only this set.
    scored_cells: list[str]
    # Every cell ANY subject reached, scored or not. The cell-wise charts plot THIS axis: on the
    # intersection a subject that came up short simply is not on the board, and a comparison
    # narrowed to what everyone answered cannot show who failed to answer.
    covered_cells: list[str]
    pairwise: list[PairwiseComparison]
    n_tests: int


class Comparability(StrictModel):
    """Whether the selection's ABSOLUTE levels are one quantity, and WHY — two ways to fail and a
    reader must be able to tell them apart.

    ``verdict`` is ``None`` for UNKNOWN, which is not ``True`` and must never render as it.
    ``datasets_differ`` dominates: subjects on different datasets measure different things, so no
    ruler agreement could rescue the comparison. Their cells never intersect either, which is why
    the variance decomposition is correctly absent rather than empty.

    This is the SELECTION's verdict; ``SubjectReading.comparable`` is the same question asked of
    one channel, and it is what a surface strikes a row through on.
    """

    verdict: bool | None
    reason: ComparabilityReason
    datasets: list[str]
    n_rulers: int
    # The sentence BOTH surfaces render, so neither keeps a per-reason map that can go an arm out
    # of step with the other's — including the one reason that QUALIFIES rather than disqualifies
    # the column, which a map indexed defensively drops in silence.
    note: str


class EvidenceVariance(StrictModel):
    """The additive cell + subject decomposition over the cells every subject measured.

    ``subject_effect_sd`` alone decides nothing: under the null a subject mean still scatters by
    ``null_subject_scatter``, so a subject SD at or below it is noise wearing a ranking.
    """

    cell_effect_sd: float
    subject_effect_sd: float
    residual_sd: float
    null_subject_scatter: float
    subject_sd_below_noise: bool
    n_cells: int
    n_subjects: int


class EvidencePower(StrictModel):
    """What this instrument can and cannot resolve, at the width it is currently run.

    ``cells_for_largest_gap`` prices the question actually on the table — how many cells per subject
    it would take to resolve the biggest gap the roster already shows.

    The last two are the harder limit and they answer a different question. Effect size decides the
    first three; ``exact_p_floor`` is what the WIDTH alone permits, so a panel below
    ``cells_for_corrected_verdict`` cannot produce a Holm-corrected result however large the effect —
    a clean sweep of every cell included. Buy width to that line before buying it for power.
    """

    paired_se: float
    min_detectable_effect: float
    largest_subject_gap: float
    cells_per_subject: int
    cells_for_largest_gap: int | None
    exact_p_floor: float
    cells_for_corrected_verdict: int


class ArmReplicate(StrictModel):
    """One arm — a CONFIGURATION identity, which is what the word keeps meaning here — that ran
    more than once. Computed over ``campaign`` subjects only: a course and a candidate share their
    campaign's round-0 hashes, so grouping them by it would report a branch as a replicate of the
    trunk it grew out of. ``level_spread`` is the cheapest noise reading on the board — taken from
    campaigns already paid for, and invisible to a roster that lists campaigns rather than arms.

    It is a noise reading ONLY at ``n_instruments == 1``. Above that the arm was held constant while
    the INSTRUMENT moved underneath it, so the spread measures the engine's own drift and is served
    as that — a replicate that was never one is the more useful finding, but it is not noise."""

    arm_id: str
    campaign_ids: list[str]
    level_spread: float
    n_instruments: int


class OrderConfound(StrictModel):
    """Run order against outcome. A one-at-a-time comparison confounds the subject with WHEN it
    ran — the archive grows between runs, so the ruler and the caches both move with the calendar.
    """

    level_vs_order: float | None
    spend_vs_order: float | None
    n_subjects: int
    # The VERDICT, served rather than left to a threshold each reader picks: a near-perfect rank
    # correlation means the roster's ordering is also its chronology and the two cannot be told
    # apart. Deliberately strict — this disqualifies a comparison, so it fires only on a monotone.
    order_confounded: bool


class Evidence(StrictModel):
    """The whole read for one selection of subjects — recomputed on every fetch."""

    generated_at: str
    # Oldest first — the confound reads off this order. Identity AND the metric reading in one
    # row, so nothing downstream joins two lists on `key`.
    subjects: list[SubjectReading]
    comparability: Comparability
    # WHICH number everything below is about — the picker's vocabulary, the merged per-subject
    # intervals and every pairwise test, all under one selection. Non-optional: the default always
    # resolves, so there is no state where a chart is drawn under no named metric.
    metric: MetricReading
    # Subject keys that were ASKED for and answered nothing — nothing of that name, no round
    # document at the head they address, or a head whose rows carry no channel. Served because the
    # roster is otherwise the only evidence they were dropped, and a subject that silently thins a
    # selection is the channel-level twin of scoring an unread cell as zero.
    unread_subjects: list[str] = Field(default_factory=list)
    replicates: list[ArmReplicate] = Field(default_factory=list)
    variance: EvidenceVariance | None = None
    power: EvidencePower | None = None
    order_confound: OrderConfound | None = None
    # Ranked desc by anchor_effect. Empty unless `include_ranking` was asked for — the only walk
    # here that opens a round document past round 0, and the reason it alone is opt-in.
    ranking_computed: bool = False
    edits: list[RankedEdit] = Field(default_factory=list)
    spread: EditSpread = Field(default_factory=EditSpread)


def _state_hash(prompt_state: dict[str, dict[str, str]]) -> str:
    """Stable short hash of a candidate state; empty state ⇒ the origin sentinel."""
    if not prompt_state:
        return _ORIGIN_HASH
    canonical = json.dumps(prompt_state, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:12]


class _Accum:
    def __init__(self, prompt_state: dict[str, dict[str, str]], label: str) -> None:
        self.prompt_state = prompt_state
        self.label = label
        self.provenance: list[EffectProvenance] = []
        # cell -> paired (candidate_fit, origin_fit) lists across occurrences
        self.cand_by_cell: dict[str, list[float]] = {}
        self.orig_by_cell: dict[str, list[float]] = {}


def _dataset_name(manifest: dict[str, Any]) -> str:
    block_raw = manifest.get("campaign_config")
    block = block_raw if isinstance(block_raw, dict) else manifest
    return str(block.get("dataset_name", ""))


def _dataset_of(campaign_dir: Path) -> str:
    return _dataset_name(read_json_tolerant(campaign_dir / "campaign.json", {}))


def campaigns_on_dataset(stores: Stores, dataset_name: str) -> list[str]:
    """Campaign ids bound to *dataset_name*, read off each manifest rather than off the directory
    name — a name-shaped guess silently skips an A/B arm, a fork, a rename."""
    return [
        child.name
        for child in stores.campaigns.iter_campaign_dirs()
        if _dataset_of(child) == dataset_name
    ]


class _ChainPoint(NamedTuple):
    """One MEASURED step of a branch: which candidate, at which round, the rows it left, and its
    own ``candidate_scores`` entry — the document is already open, so carrying the entry costs a
    reference and saves every consumer a second read of the same file."""

    round: int
    candidate_id: str
    label: str
    rows: list[dict[str, Any]]
    scores: dict[str, Any]


class _Head(NamedTuple):
    """Where one subject's numbers come from — always ONE searchpoint's rows, whichever kind
    addressed it. ``label`` is what to call the CHANNEL, which is not the same thing: a course is
    named by its branch, and the searchpoint it currently reads at is ``point``.

    ``chain`` is the branch behind the head where a LENS produced one — the counterfactual winners,
    which are not the crowned ones and cannot be re-derived from the ledger. ``None`` means the
    trajectory walk reads the crowns instead."""

    cycle_dir: Path
    label: str
    dataset_name: str
    created_at: str
    point: _ChainPoint
    scenario: ScenarioReading | None = None
    chain: list[_ChainPoint] | None = None


def subject_evidence(
    stores: Stores,
    specs: list[SubjectSpec],
    *,
    include_ranking: bool = False,
    include_trajectory: bool = False,
    include_config: bool = False,
    metric: str = MEASURAND,
) -> Evidence:
    """Reduce the named subjects into one evidence read, under one metric. *specs* may span
    datasets, and may name something that no longer exists — an absent one is simply not in the
    roster. *metric* is a catalogue key or ``expr:<formula>``; one this selection cannot answer
    raises ``ValueError`` for the route to turn into a 400.

    EVERY metric is a fact about one CELL, read off the row that stands for it — on the recursion
    a cell is a whole inner campaign, so its lift, its origin, its cost and its round count are
    that seed's own. Nothing here opens a second document to derive a number the cell already
    carries.

    ONE metric then reaches everything: the roster's merged estimates, the pairwise tests, the
    variance decomposition, the resolving power, the run-order confound and the edit ranking, so
    no two numbers on the page can be about different quantities.
    """
    wanted: dict[str, SubjectSpec] = {s.key: s for s in specs}
    heads: dict[str, _Head] = {}
    channels_by_subject: dict[str, dict[str, dict[str, float]]] = {}
    for key, spec in wanted.items():
        resolved = _at(stores, spec)
        if resolved is None:
            continue
        leaf_stores, campaign_dir = resolved
        head = _resolve_head(leaf_stores, spec, campaign_dir)
        if head is None:
            continue
        channels = cell_channels(head.point.rows)
        if not channels:
            continue
        heads[key] = head
        channels_by_subject[key] = channels

    # An unmeasured selection is not a metric problem, and answering it as one is what two
    # ordinary actions — ticking a campaign whose origin has not run, mistyping an id — used to
    # get back: "Metric 'measurand' is not one this selection can answer", about a vocabulary,
    # when the fact is that there is nothing here to have a vocabulary over.
    if not heads:
        raise ValueError(
            f"None of {', '.join(sorted(wanted)) or 'the subjects named'} has scored rows to read. "
            "A campaign answers here once its origin has run, a course once its branch has, a "
            "candidate once it has been measured; one that does not exist answers never."
        )
    # WHICH metric is decidable only once the rows are in hand: the measurand is the seed's own
    # lift where the cells carry one and the cell's own fitness where they do not, and a metric
    # nothing in this selection answers is never offered.
    available = available_channels(channels_by_subject)
    spec_metric, compiled = resolve_metric(metric, available)

    rows = [
        _reading_row(
            wanted[key],
            head,
            compiled,
            channels_by_subject[key],
            include_trajectory=include_trajectory,
            include_config=include_config,
        )
        for key, head in heads.items()
    ]
    rows.sort(key=lambda r: (r.created_at, r.key))
    rows = _stamp_comparable(rows)

    # The one expensive walk, and the only one that opens EVERY round document. Campaign subjects
    # only: an edit is ranked against its own campaign's ORIGIN, which is the anchor a course or a
    # candidate does not define — both sit inside a campaign whose origin is already a subject the
    # operator can tick.
    accums: dict[str, _Accum] = {}
    if include_ranking:
        for row in (r for r in rows if r.kind == "campaign"):
            cycle_dir = heads[row.key].cycle_dir
            hop = CycleHop(campaign_id=row.campaign_id, cycle_id=cycle_dir.name)
            for round_file in sorted(CycleLayout(cycle_dir).rounds.glob(ROUND_GLOB)):
                if round_file.name == "round_0000.json":
                    continue
                _accumulate_round(
                    read_json_tolerant(round_file, {}), row.values, hop, accums, compiled
                )

    edits = sorted(
        (_finalize(state_hash, acc) for state_hash, acc in accums.items()),
        key=lambda r: r.anchor_effect,
        reverse=True,
    )
    scored = {r.key: r.values for r in rows if r.values}
    # Read before the envelope: the width a corrected verdict needs depends on how many tests the
    # correction spans, so `power` cannot be built without the count `metric` arrives at.
    metric_reading = _metric_reading(spec_metric, rows, available)
    return Evidence(
        generated_at=utcnow_iso(),
        subjects=rows,
        comparability=_comparability(rows),
        metric=metric_reading,
        unread_subjects=sorted(set(wanted) - set(heads)),
        replicates=_replicates(rows),
        variance=(variance := _variance(scored)),
        power=_power(variance, rows, n_tests=metric_reading.n_tests),
        order_confound=_order_confound(rows),
        ranking_computed=include_ranking,
        edits=edits,
        spread=_edit_spread(edits),
    )


# --- resolving a subject to the rows it stands for ---------------------------------------------


def _at(stores: Stores, spec: SubjectSpec) -> tuple[Stores, Path] | None:
    """The tree a subject lives in, and its campaign directory there. ``None`` where either is
    absent — a subject nothing answers rides ``unread_subjects``, so a mistyped id or a sandbox
    since deleted must not fail the whole read for every other channel in it.

    A sandbox is structurally an ordinary projects tree, so once ``descend_store`` has stepped
    into it every resolver below works unchanged — which is why an L4 inner run needed no second
    walker, only an address that could name one.
    """
    try:
        leaf = descend_store(stores, spec.inside)
    except (BadRequestError, NotFoundError):
        return None
    campaign_dir = next(
        (d for d in leaf.campaigns.iter_campaign_dirs() if d.name == spec.campaign_id), None
    )
    return None if campaign_dir is None else (leaf, campaign_dir)


def _resolve_head(stores: Stores, spec: SubjectSpec, campaign_dir: Path) -> _Head | None:
    """The rows a subject's numbers come from, or ``None`` where nothing on disk answers it."""
    manifest = read_json_tolerant(campaign_dir / "campaign.json", {})
    dataset_name = _dataset_name(manifest)
    if spec.kind == "campaign":
        # The campaign's ROOT cycle — C0 — named by the manifest, never whichever sibling a
        # directory walk happened to reach last. A forked campaign holds `cycle_x` beside
        # `cycle_x_fork_y`, so walking them read the FORK's origin under the campaign's name with
        # nothing on either surface saying which cycle the row came from. A fork is addressable
        # here as its own `course:` subject instead.
        cycle_dir = campaign_cycles_dir(campaign_dir) / str(manifest.get("root_cycle_id", ""))
        origin = _point_at(cycle_dir, 0, label="")
        if origin is None:
            return None
        return _Head(
            cycle_dir=cycle_dir,
            label=spec.campaign_id,
            dataset_name=dataset_name,
            created_at=str(manifest.get("created_at", "")),
            point=_masked(origin, spec.samples),
        )

    cycle_dir = campaign_cycles_dir(campaign_dir) / spec.cycle_id
    if not CycleLayout(cycle_dir).rounds.is_dir():
        return None
    index = read_json_tolerant(CycleLayout(cycle_dir).manifest, {})
    scenario, chain = (None, None)
    if spec.lens:
        # Under a lens the head is the last point the record still speaks for — the counterfactual
        # winner at the round the two readings part, or the branch's own if they never do. The
        # chain has to be walked to find it: each round's decision depends on the one before it.
        resolved = _scenario(stores, spec, cycle_dir)
        if resolved is None:
            return None
        scenario, chain = resolved
        point: _ChainPoint | None = chain[-1]
    else:
        point = (
            _course_head(cycle_dir)
            if spec.kind == "course"
            else _candidate_point(cycle_dir, spec.candidate_id)
        )
    if point is None:
        return None
    return _Head(
        cycle_dir=cycle_dir,
        # The cycle names the branch; the candidate names itself. A course's own `cycle_id` rather
        # than its campaign's, because two courses of one campaign are exactly what this compares.
        label=spec.cycle_id if spec.kind == "course" else point.label,
        dataset_name=dataset_name,
        created_at=str(index.get("created_at", "")),
        point=_masked(point, spec.samples),
        scenario=scenario,
        chain=chain,
    )


def _masked(point: _ChainPoint, samples: frozenset[int] | None) -> _ChainPoint:
    """The same searchpoint read over a SUBSET of what it was measured on — "seventeen of the
    twenty-eight", the operator's own question. Rows are dropped, never re-derived: every value
    that survives is one this candidate actually recorded on that sample."""
    if samples is None:
        return point
    return point._replace(rows=[r for r in point.rows if r.get("sample_id") in samples])


def _scenario(
    stores: Stores, spec: SubjectSpec, cycle_dir: Path
) -> tuple[ScenarioReading, list[_ChainPoint]] | None:
    """This branch as the lens would have run it: the counterfactual winner chain, plus what it
    says about the record. ``None`` where the branch has no readable chain at all.

    The chain's points are the arms the run MEASURED — the fold picks among them and invents
    none — so a channel plotted from it plots measurements, under a criterion that would have
    carried a different one of them forward.
    """
    criterion = compile_round_scorer(spec.lens.removeprefix(_LENS_SCORE_PREFIX))
    record = load_mask_record(stores, spec.campaign_id, spec.samples)
    cycle = next((c for c in record.cycles if c.cycle_id == spec.cycle_id), None)
    if cycle is None:
        return None
    steps = scenario_spine(cycle, criterion)
    points = [p for s in steps if (p := _point_at(cycle_dir, s.round, candidate_id=s.candidate_id))]
    if not points:
        return None
    last = steps[-1]
    parted = last.candidate_id != last.recorded_id
    return (
        ScenarioReading(
            recorded_winner_id=last.recorded_id,
            scenario_winner_id=last.candidate_id,
            winner_changed=parted,
            first_divergent_round=last.round if parted else None,
            # Every step but the one the walk returned on — a round the fold could not decide is
            # still a round the two readings agreed about.
            invariant_rounds=len(steps) - 1 if parted else len(steps),
            # Off the CYCLE, not the chain: the chain stops at the parting, and the denominator is
            # how many rounds this branch has for that prefix to be a fraction of.
            total_rounds=len(cycle.rounds),
            n_samples_scored=len(_masked(points[-1], spec.samples).rows),
            note=_SCENARIO_NOTE,
        ),
        [_masked(p, spec.samples) for p in points],
    )


# Served rather than documented, on the `Comparability.note` pattern: the honest limit of a lens is
# exactly what a surface forgets to restate, and a chart of counterfactual winners is read as a
# counterfactual RUN unless the reading says otherwise.
_SCENARIO_NOTE = (
    "This re-ranks the RECORD under the formula you named — it does not re-run the campaign. "
    "θ is not re-fitted and no election is replayed, so the round named here is where the two "
    "readings first part, not a verdict the campaign reached. The chain STOPS at that round: past "
    "it the run would have stood on a parent it never had, and no measurement says what that "
    "produces. `ab` replay is what re-derives an election exactly."
)


def _crowns(cycle_dir: Path) -> dict[int, str]:
    """``round -> the label its election crowned``. A round that HELD (empty label) or never
    elected at all is absent: neither moved the branch's head, and only the ledger separates
    them from a round that crowned somebody."""
    return {
        rnd: election.winner_label
        for rnd, election in scan_ledger_elections(CycleLayout(cycle_dir).ledger).items()
        if election.winner_label
    }


def _point_in_doc(
    doc: dict[str, Any], round_num: int, *, label: str = "", candidate_id: str = ""
) -> _ChainPoint | None:
    """One arm of one round document, addressed the way its caller HAS it.

    By LABEL for a crown, because that is the key the ledger's election records and the one a
    resume does not re-mint — joining a crown on ``candidate_id`` resolves to nothing after one.
    By ID for a counterfactual or an addressed searchpoint. Neither takes the round's FIRST arm,
    which at round 0 is the origin and the only one there is.
    """
    rows_by_id = doc.get("all_candidate_results") or {}
    if not rows_by_id:
        return None
    entries = {
        str(cs.get("candidate_id") or ""): cs
        for cs in doc.get("candidate_scores") or []
        if isinstance(cs, dict)
    }
    if label:
        found = next(
            (cid for cid, cs in entries.items() if cid and str(cs.get("label") or "") == label),
            None,
        )
    else:
        found = candidate_id or next(iter(rows_by_id))
    rows = rows_by_id.get(found) if found else None
    if found is None or not isinstance(rows, list):
        return None
    entry = entries.get(found) or {}
    return _ChainPoint(
        round=round_num,
        candidate_id=found,
        label=str(entry.get("label") or "") or found,
        rows=list(rows),
        scores=entry,
    )


def _point_at(
    cycle_dir: Path, round_num: int, *, label: str = "", candidate_id: str = ""
) -> _ChainPoint | None:
    return _point_in_doc(
        read_json_tolerant(CycleLayout(cycle_dir).round_file(round_num), {}),
        round_num,
        label=label,
        candidate_id=candidate_id,
    )


def _course_head(cycle_dir: Path) -> _ChainPoint | None:
    """A branch reads at its HEAD — the last winner it elected, or its origin where no round has
    crowned one yet. Not the best candidate ever measured on it: the branch is what the run
    actually carried forward, and a course whose rounds all held is honestly still at C0."""
    crowns = _crowns(cycle_dir)
    last = max(crowns) if crowns else 0
    return _point_at(cycle_dir, last, label=crowns.get(last, ""))


def _candidate_point(cycle_dir: Path, candidate_id: str) -> _ChainPoint | None:
    """One searchpoint, read off the LAST round document carrying it — a repair re-measures a
    candidate in place without re-minting it, so the newest document holds the rows that stand."""
    for round_file in sorted(CycleLayout(cycle_dir).rounds.glob(ROUND_GLOB), reverse=True):
        doc = read_json_tolerant(round_file, {})
        point = _point_in_doc(doc, int(doc.get("round", 0) or 0), candidate_id=candidate_id)
        if point is not None and point.rows:
            return point
    return None


def _trajectory(
    head: _Head, spec: SubjectSpec, compiled: CompiledExpression
) -> list[TrajectoryPoint]:
    """The branch standing behind a head: the origin, every winner before it, then the head itself.
    Each point is read on ITS OWN cells — the subsets move between rounds, so restricting the chain
    to the head's cells would redraw earlier rounds on evidence they never had.

    Under a lens the chain is already resolved (the counterfactual winners, which no ledger holds)
    and ENDS at the round the two readings part; otherwise it is the crowns, off the elections."""
    if head.chain is not None:
        return [_trajectory_point(p, compiled) for p in head.chain]
    crowns = _crowns(head.cycle_dir)
    at = head.point.round
    rounds = [r for r in sorted({0, *(r for r in crowns if r < at)}) if r != at]
    points = [
        p
        for r in rounds
        if (p := _point_at(head.cycle_dir, r, label=crowns.get(r, ""))) is not None
    ]
    return [_trajectory_point(_masked(p, spec.samples), compiled) for p in (*points, head.point)]


def _config_of(point: _ChainPoint) -> dict[str, str]:
    """One searchpoint as a flat ``key -> rendered value`` map, over the three disjoint keyspaces
    `build_candidate_flat` already owns: ``node.param`` from the RESOLVED config, then the bare
    prompt fields on top.

    Resolved, never the sparse ``pipeline_params_override``: a delta is relative to a parent, and
    two searchpoints from different campaigns share none — lined up on their deltas, a panel
    would show two lists with nothing in common and call it a comparison.

    ``lineage`` is dropped for the reason `results.py::_identity_config` drops it: it is IDENTITY,
    not configuration, and it differs between any two candidates by construction — carried, it
    would report a difference on every pair no matter what they were configured with.
    """
    entry = point.scores
    fields = {k: v for k, v in (entry.get("prompt_fields") or {}).items() if k != "lineage" and v}
    return build_candidate_flat(
        flatten_sp_summary(entry.get("resolved_pipeline_params")), {"prompt_fields": fields}
    )


def _trajectory_point(point: _ChainPoint, compiled: CompiledExpression) -> TrajectoryPoint:
    values, _ = _score_cells(compiled, cell_channels(point.rows))
    value, ci_lo, ci_hi, n_cells = _merged(values)
    return TrajectoryPoint(
        candidate_id=point.candidate_id,
        round=point.round,
        label=point.label,
        value=value,
        ci_lo=ci_lo,
        ci_hi=ci_hi,
        n_cells=n_cells,
    )


def _score_cells(
    compiled: CompiledExpression, channels: dict[str, dict[str, float]]
) -> tuple[dict[str, float], list[str]]:
    """``({cell: value}, unscorable_cells)``. A cell the metric cannot read is dropped and NAMED:
    ``ScoringTermMissingError`` is a term the row never carried and its parent a division by zero or
    a non-finite result, and both mean unscorable here — never a value of zero.

    Named rather than counted, because the two ways a cell can be blank on a chart are different
    facts: this subject MEASURED it and the metric cannot read it, or this subject never measured
    it at all. A count answers neither per cell, and a surface with only a count has to render both
    the same."""
    values: dict[str, float] = {}
    missed: list[str] = []
    for cell, row_channels in channels.items():
        try:
            values[cell] = compiled.evaluate(dict(row_channels), "this cell")
        except ScoringFormulaError:
            missed.append(cell)
    return (values, sorted(missed))


def _merged(values: dict[str, float]) -> tuple[float | None, float | None, float | None, int]:
    """``(value, ci_lo, ci_hi, n_cells)`` for one set of per-cell readings. Below two cells there is
    no spread, and a bracket drawn from one reading would claim certainty nothing measured."""
    ordered = [values[c] for c in sorted(values)]
    bracketed = mean_ci_t(ordered)
    if bracketed:
        return (bracketed[0], bracketed[1], bracketed[2], len(ordered))
    return (ordered[0] if ordered else None, None, None, len(ordered))


def _metric_reading(
    spec: MetricSpec, rows: list[SubjectReading], available: frozenset[str]
) -> MetricReading:
    """The vocabulary the selection was read under, plus every pairwise test over it. The
    per-subject half lives on the roster rows themselves."""
    scored = [r for r in rows if r.values]
    shared = sorted(set.intersection(*(set(r.values) for r in scored))) if scored else []
    pairwise = _pairwise(scored)
    return MetricReading(
        spec=spec,
        catalogue=list(catalogue_for(available)),
        namespace=sorted(available),
        scored_cells=shared,
        covered_cells=sorted({c for r in rows for c in (*r.values, *r.unscorable_cells)}),
        pairwise=pairwise,
        n_tests=sum(1 for p in pairwise if p.p_value is not None),
    )


def _pairwise(rows: list[SubjectReading]) -> list[PairwiseComparison]:
    """Every unordered pair, each blocked on the cells BOTH scored — strictly more evidence than
    the roster-wide intersection, and the honest paired n for that one comparison, which is why it
    is served per row."""
    out: list[PairwiseComparison] = []
    for i, a in enumerate(rows):
        for b in rows[i + 1 :]:
            cells = sorted(set(a.values) & set(b.values))
            if not cells:
                continue
            shift, lo, hi, p_value, n = exact_paired_reading(
                [b.values[c] for c in cells], [a.values[c] for c in cells]
            )
            out.append(
                PairwiseComparison(
                    subject_a=a.key,
                    subject_b=b.key,
                    median_shift=shift,
                    ci_lo=lo,
                    ci_hi=hi,
                    p_value=p_value,
                    p_adjusted=None,
                    n_cells=n,
                )
            )
    tested = [i for i, r in enumerate(out) if r.p_value is not None]
    adjusted = holm_adjusted([out[i].p_value or 0.0 for i in tested])
    for slot, i in enumerate(tested):
        out[i] = out[i].model_copy(update={"p_adjusted": adjusted[slot]})
    return out


def _spend_to_round(layout: CycleLayout) -> dict[str, float]:
    """``round -> USD this cycle had spent by the END of it``, cumulative and filled forward.

    **The only per-round split of cost there is.** ``dashboard.json::spend`` is one running total
    for the whole cycle and its ``rounds[]`` carry no cost at all, so "what had this branch spent
    by round 3" is answerable nowhere else — every call bills through a ``TokenUsageRecord`` that
    stamps its ``round``, and this folds them.

    CUMULATIVE, not per-round, because cumulative is the number a surface shows and a browser may
    not sum one for itself. Filled forward so every round from 0 to the last that billed has an
    entry: a round that spent nothing still HAS a cost-to-here, and leaving it out would make a
    lookup miss where the honest answer is "the same as the round before".

    A call carrying no ``round`` is banked at 0 — it ran before any round closed (init, the origin
    score), and dropping it would under-report every prefix. Pricing follows the account ledger's
    own rule (``record_cost_usd``): a cached call cost nothing, an unpriced one is re-priced from
    the rate table, and one with no rate on file is left out rather than counted as free.

    THIS CYCLE'S OWN ledger, so on a fork it answers what the BRANCH has spent since it cut, not
    what the line cost from the origin — the inherited prefix lives in the parent's file and the
    read side cannot follow that link. ``cycle_spend_usd`` beside it is the roll-up that does
    include it, which is why both are served.
    """
    per_round: dict[int, float] = {}
    for rec in iter_jsonl(layout.ledger, record_types=frozenset({"token_usage"})):
        usd = record_cost_usd(rec)
        if usd is None:
            continue
        rnd = rec.get("round")
        per_round[rnd if isinstance(rnd, int) else 0] = (
            per_round.get(rnd if isinstance(rnd, int) else 0, 0.0) + usd
        )
    if not per_round:
        return {}
    running = 0.0
    out: dict[str, float] = {}
    for rnd in range(max(per_round) + 1):
        running += per_round.get(rnd, 0.0)
        out[str(rnd)] = round(running, 6)
    return out


def _reading_row(
    spec: SubjectSpec,
    head: _Head,
    compiled: CompiledExpression,
    channels: dict[str, dict[str, float]],
    *,
    include_trajectory: bool,
    include_config: bool,
) -> SubjectReading:
    """One roster row. The arm, the instrument, the ruler and the spend are facts about the CYCLE
    the subject sits in, so a course and a candidate read them off their own cycle rather than off
    the campaign's root — two courses of one campaign can sit on different rulers."""
    values, unscorable = _score_cells(compiled, channels)
    layout = CycleLayout(head.cycle_dir)
    doc = read_json_tolerant(layout.round_file(0), {})
    dash = read_json_tolerant(layout.dashboard, {})
    spend = dash.get("spend")
    # The configuration the cycle ran under IS the arm — its hashes are stamped on round 0
    # precisely so a campaign paused before round 1 still names what it measured.
    hashes = doc.get("optimizer_prompt_hashes")
    # `None` on any backend declaring no measurement identity — every campaign shares that absence,
    # so the arm alone is the whole grouping there.
    params = doc.get("pipeline_params")
    generate = params.get("l1_generate") if isinstance(params, dict) else None
    instrument = generate.get("inner_origin") if isinstance(generate, dict) else None
    raw = doc.get("ability")
    value, ci_lo, ci_hi, n_cells = _merged(values)
    return SubjectReading(
        key=spec.key,
        kind=spec.kind,
        inside=list(spec.inside),
        campaign_id=spec.campaign_id,
        cycle_id=spec.cycle_id or head.cycle_dir.name,
        candidate_id=head.point.candidate_id,
        label=head.label,
        dataset_name=head.dataset_name,
        created_at=head.created_at,
        # Stamped against the whole selection one pass later — a verdict about how this row sits
        # with the others cannot be reached while the others are still being built.
        comparable=None,
        comparable_note="",
        mask=(
            SubjectMask(
                lens=spec.lens or None,
                samples=sorted(spec.samples) if spec.samples else None,
            )
            if spec.lens or spec.samples
            else None
        ),
        scenario=head.scenario,
        # A campaign is its origin and nothing precedes it; the other two stand on a branch.
        trajectory=(
            _trajectory(head, spec, compiled)
            if include_trajectory and spec.kind != "campaign"
            else None
        ),
        config=_config_of(head.point) if include_config else None,
        # An UNSTAMPED round is not the origin arm — it is an UNKNOWN one, which groups with
        # nothing. Collapsing the two onto one hash makes `_replicates` report every unstamped
        # campaign as a replicate of the rest, spread and all, over a shared absence.
        arm_id=_state_hash({"": dict(hashes)}) if isinstance(hashes, dict) and hashes else None,
        instrument_id=str(instrument) if isinstance(instrument, str) else None,
        ability=(AbilityReading.model_validate(raw) if isinstance(raw, dict) else None),
        round=head.point.round,
        cycle_spend_usd=(spend or {}).get("total_used_usd") if isinstance(spend, dict) else None,
        cycle_rounds_scored=max(len(list(layout.rounds.glob(ROUND_GLOB))) - 1, 0),
        spend_to_round=_spend_to_round(layout),
        values=values,
        value=value,
        ci_lo=ci_lo,
        ci_hi=ci_hi,
        n_cells=n_cells,
        unscorable_cells=unscorable,
    )


def _stamp_comparable(rows: list[SubjectReading]) -> list[SubjectReading]:
    """Each subject's OWN verdict against the rest of the selection — the majority dataset first,
    then the majority ruler. Served because a surface that struck rows through on its own would
    have to pick the odd one out from `comparability`'s selection-wide reason, and two surfaces
    would pick differently."""
    majority_dataset = Counter(r.dataset_name for r in rows).most_common(1)[0][0]
    stamped = Counter(
        r.ability.ruler_id for r in rows if r.ability is not None and r.ability.ruler_id is not None
    )
    majority_ruler = stamped.most_common(1)[0][0] if stamped else None

    def verdict(row: SubjectReading) -> tuple[bool | None, str]:
        if row.dataset_name != majority_dataset:
            return False, (
                f"Measured on {row.dataset_name or 'another dataset'}, while the rest of this "
                f"selection is on {majority_dataset or 'a different one'} — they share no "
                f"question, so nothing here pairs and only its own level is readable."
            )
        ruler = row.ability.ruler_id if row.ability is not None else None
        # UNKNOWN on either side, which is not a yes: an unstamped origin may sit on any scale,
        # and a selection where nothing is stamped can vouch for none of it.
        if ruler is None or majority_ruler is None:
            return None, ""
        if ruler != majority_ruler:
            return False, (
                "Read against a different ruler from the rest of this selection — its cells "
                "still pair where they overlap, its absolute level is on another scale."
            )
        return True, ""

    return [
        r.model_copy(update=dict(zip(("comparable", "comparable_note"), verdict(r), strict=True)))
        for r in rows
    ]


def _replicates(rows: list[SubjectReading]) -> list[ArmReplicate]:
    by_arm: dict[str, list[SubjectReading]] = {}
    for row in rows:
        if row.kind == "campaign" and row.arm_id is not None:
            by_arm.setdefault(row.arm_id, []).append(row)
    out = []
    for arm, same in sorted(by_arm.items()):
        levels = [r.value for r in same if r.value is not None]
        if len(levels) > 1:
            out.append(
                ArmReplicate(
                    arm_id=arm,
                    campaign_ids=[r.campaign_id for r in same],
                    level_spread=max(levels) - min(levels),
                    n_instruments=len({r.instrument_id for r in same}),
                )
            )
    return out


def _comparability(rows: list[SubjectReading]) -> Comparability:
    datasets = sorted({r.dataset_name for r in rows if r.dataset_name})
    readings = [r.ability for r in rows]
    stamped = [a for a in readings if a is not None and a.ruler_id is not None]
    reason: ComparabilityReason
    verdict: bool | None
    note: str
    if len(datasets) > 1:
        # Different measurands entirely — no ruler agreement could rescue this, so it outranks
        # everything below.
        reason, verdict = "datasets_differ", False
        note = (
            "Comparability NO — this selection spans several datasets, which measure different "
            "things. The values are not one quantity and no pairing rescues them; the roster and "
            "spend still compare, the numbers do not."
        )
    elif not readings or len(stamped) != len(readings):
        reason, verdict = "ruler_unstamped", None
        note = (
            "Comparability UNKNOWN — at least one origin predates the ruler stamp, which is not "
            "the same as yes. Absolute levels above may sit on different δ scales: pair on cells, "
            "do not read the value column across campaigns."
        )
    elif not all(a.comparable_to(stamped[0]) for a in stamped):
        reason, verdict = "rulers_differ", False
        note = (
            "Comparability NO — these origins were measured on different δ rulers, so their "
            "absolute values are not one quantity. Only within-ruler comparisons hold."
        )
    else:
        reason, verdict = "one_ruler", True
        note = (
            "Comparability YES — these origins were measured on one δ ruler, so their values are "
            "directly comparable."
        )
    return Comparability(
        verdict=verdict,
        reason=reason,
        datasets=datasets,
        n_rulers=len({a.ruler_id for a in stamped}),
        note=note,
    )


def _variance(by_subject: dict[str, dict[str, float]]) -> EvidenceVariance | None:
    """One column per SUBJECT, not per arm id: two campaigns sharing an arm are replicates and
    each is its own reading, which is exactly what the residual is estimated from."""
    decomposed = two_way_effect_sds(by_subject)
    if decomposed is None:
        return None
    cell_sd, subject_sd, residual = decomposed
    n_cells = len(set.intersection(*(set(v) for v in by_subject.values())))
    null_scatter = residual / (n_cells**0.5)
    return EvidenceVariance(
        cell_effect_sd=cell_sd,
        subject_effect_sd=subject_sd,
        residual_sd=residual,
        null_subject_scatter=null_scatter,
        subject_sd_below_noise=subject_sd <= null_scatter,
        n_cells=n_cells,
        n_subjects=len(by_subject),
    )


def _power(
    variance: EvidenceVariance | None, rows: list[SubjectReading], *, n_tests: int
) -> EvidencePower | None:
    """The paired SE of a two-arm contrast: pairing removes the cell effect — the term that is
    largest here — and leaves the residual on both arms, hence ``residual * sqrt(2 / cells)``.

    Beside it the width limit, which no SE can see: an exact test on this many cells has a smallest
    reachable p, and *n_tests* is the correction it has to clear."""
    levels = [r.value for r in rows if r.value is not None]
    if variance is None or len(levels) < 2 or variance.n_cells < 1:
        return None
    se = variance.residual_sd * (2.0 / variance.n_cells) ** 0.5
    mde = min_detectable_effect(se)
    gap = max(levels) - min(levels)
    # Same k the MDE used, re-solved for the cell count: n = 2 * (k * residual / gap)^2.
    needed = None
    if gap > 0.0 and se > 0.0:
        needed = math.ceil(2.0 * ((mde / se) * variance.residual_sd / gap) ** 2)
    return EvidencePower(
        paired_se=se,
        min_detectable_effect=mde,
        largest_subject_gap=gap,
        cells_per_subject=variance.n_cells,
        cells_for_largest_gap=needed,
        exact_p_floor=exact_p_floor(variance.n_cells),
        cells_for_corrected_verdict=cells_for_exact_verdict(n_tests),
    )


def _order_confound(rows: list[SubjectReading]) -> OrderConfound | None:
    """*rows* is already oldest-first, so the index IS the chronology."""
    if len(rows) < 3:
        return None
    order = [float(i) for i in range(len(rows))]

    def rho(values: list[float | None]) -> float | None:
        if any(v is None for v in values):
            return None
        return rank_correlation(order, [float(v) for v in values if v is not None])

    level_rho = rho([r.value for r in rows])
    return OrderConfound(
        level_vs_order=level_rho,
        spend_vs_order=rho([r.cycle_spend_usd for r in rows]),
        n_subjects=len(rows),
        order_confounded=level_rho is not None and abs(level_rho) >= _ORDER_CONFOUND_RHO,
    )


def _edit_spread(rows: list[RankedEdit]) -> EditSpread:
    return EditSpread(edit_effect_sd=sample_sd([r.anchor_effect for r in rows]), n_edits=len(rows))


def _accumulate_round(
    doc: dict[str, Any],
    origin_values: dict[str, float],
    hop: CycleHop,
    accums: dict[str, _Accum],
    compiled: CompiledExpression,
) -> None:
    """An edit is worth whatever the SELECTED metric says it is — seconds, dollars, rounds, lift.
    A candidate row is a cell like any other (on the recursion, its own inner campaign), so every
    channel the roster can answer, the ranking can answer too."""
    round_num = int(doc.get("round", 0) or 0)
    for cand in doc.get("candidate_scores") or []:
        cand_id = str(cand.get("candidate_id", ""))
        if not cand_id:
            continue
        prompt_state = _coerce_state(cand.get("pipeline_params_override"))
        state_hash = _state_hash(prompt_state)
        if state_hash == _ORIGIN_HASH:
            continue  # the no-op arm anchors others; it is not itself a ranked candidate
        cand_cells, _ = _score_cells(
            compiled, cell_channels((doc.get("all_candidate_results") or {}).get(cand_id) or [])
        )
        paired = {c: cand_cells[c] for c in cand_cells if c in origin_values}
        if not paired:
            continue
        acc = accums.get(state_hash)
        if acc is None:
            acc = _Accum(prompt_state, str(cand.get("label") or state_hash))
            accums[state_hash] = acc
        acc.provenance.append(
            EffectProvenance(
                campaign_id=hop.campaign_id,
                cycle_id=hop.cycle_id,
                round=round_num,
                candidate_id=cand_id,
            )
        )
        for cell, cand_fit in paired.items():
            acc.cand_by_cell.setdefault(cell, []).append(cand_fit)
            acc.orig_by_cell.setdefault(cell, []).append(origin_values[cell])


def _coerce_state(raw: Any) -> dict[str, dict[str, str]]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, str]] = {}
    for node, fields in raw.items():
        if isinstance(fields, dict):
            out[str(node)] = {str(k): str(v) for k, v in fields.items()}
    return out


def _finalize(state_hash: str, acc: _Accum) -> RankedEdit:
    """Aggregate one edit into its ranked row — **per cell, then across cells**, so the SE comes from
    n = CELLS and a cell measured five times cannot outweigh five cells measured once."""
    per_cell: list[CellEffect] = []
    cell_cand: list[float] = []
    cell_orig: list[float] = []
    n_meas = 0
    for cell in sorted(acc.cand_by_cell):
        cand_vals = acc.cand_by_cell[cell]
        orig_vals = acc.orig_by_cell[cell]
        mean_d, _se_d, n = paired_diff_posterior(cand_vals, orig_vals)
        per_cell.append(CellEffect(cell=cell, mean_d=mean_d, n=n))
        n_meas += n
        # ONE paired point per cell — the cell's own mean level. Equal-length lists make
        # the elementwise paired mean identical to the difference of means, so this is
        # exactly ``mean_d`` re-expressed as a (candidate, origin) pair for stage two.
        cell_cand.append(sum(cand_vals) / len(cand_vals))
        cell_orig.append(sum(orig_vals) / len(orig_vals))

    # The same exact test the pairwise table runs, so the two readings on one page cannot hold an
    # edit to different standards — and a single wild cell cannot carry an edit up the ranking.
    anchor, ci_lo, ci_hi, _p, _n = exact_paired_reading(cell_cand, cell_orig)
    return RankedEdit(
        state_hash=state_hash,
        label=acc.label,
        provenance=acc.provenance,
        anchor_effect=anchor,
        ci_lo=ci_lo,
        ci_hi=ci_hi,
        n_cells=len(per_cell),
        n_measurements=n_meas,
    )


__all__ = [
    "ArmReplicate",
    "Comparability",
    "EditSpread",
    "EffectProvenance",
    "Evidence",
    "EvidencePower",
    "EvidenceVariance",
    "OrderConfound",
    "RankedEdit",
    "ScenarioReading",
    "SubjectKind",
    "SubjectMask",
    "SubjectReading",
    "SubjectSpec",
    "TrajectoryPoint",
    "campaigns_on_dataset",
    "parse_subject",
    "subject_evidence",
]
