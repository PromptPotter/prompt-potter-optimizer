from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping
from typing import Literal, NamedTuple, get_args

from pydantic import Field

from promptpotter.application.evidence.metric_catalogue import (
    CUSTOM_METRIC_KEY,
    MetricSpec,
    catalogue_for,
)
from promptpotter.application.evidence.subjects import SubjectReading
from promptpotter.application.scoring.paired import (
    MemberRows,
    as_family,
    flipped_keys,
    grade_measurands,
    read_pair,
)
from promptpotter.domain.paired_reading import (
    ROUND_LIFT_SPEC,
    CellSetName,
    Measurand,
    MeasurandKind,
    MeasurandUnit,
    PairedReading,
)
from promptpotter.domain.strict_model import StrictModel
from promptpotter.shared.measurement_context import RoleScope
from promptpotter.shared.statistics import (
    min_detectable_effect,
    rank_correlation,
    two_way_effect_sds,
)

_ORDER_CONFOUND_RHO = 0.9

ComparabilityReason = Literal["one_ruler", "rulers_differ", "ruler_unstamped", "datasets_differ"]


class SubjectMember(NamedTuple):
    rows: MemberRows
    masked: bool


def pair_subjects(a: SubjectMember, b: SubjectMember, measurand: Measurand) -> PairedReading:
    return read_pair(
        a=a.rows,
        b=b.rows,
        cell_set=CellSetName.MEASURED_BY_BOTH,
        cells=None,
        masked=a.masked or b.masked,
        dataset_hash=None,
        measurands=[measurand],
        spec=ROUND_LIFT_SPEC,
        scope=RoleScope.REPORT,
        instrument_id=a.rows.instrument_id,
    )


def metric_measurand(spec: MetricSpec, scorer_id: str) -> Measurand:
    return Measurand(
        kind=MeasurandKind.EXPR if spec.key == CUSTOM_METRIC_KEY else MeasurandKind.CHANNEL,
        key=spec.expression,
        scorer_id=scorer_id,
        binary=False,
        unit=MeasurandUnit.SCORE,
    )


class PairwiseComparison(StrictModel):
    """One unordered pair of the roster. ``a`` precedes ``b`` in its oldest-first order — by cycle,
    then by round within one — so every lift here reads parent-to-descendant across the table.
    Every pair is served: one that shares too little says so in its reading's ``state``."""

    subject_a: str
    subject_b: str
    reading: PairedReading = Field(
        description="`subject_b` over `subject_a` under the selected metric, on the cells both "
        "scored under it. Its headline's `family` is Holm across this table's pairs.",
    )
    hit: PairedReading = Field(
        description="The same pair on the hit grade, whatever metric the read selected, on the "
        "cells both graded: its headline's `flips` are what a level nets against each other.",
    )
    gained: list[int] = Field(
        description="The cells `hit` counts as gained — `subject_a` missed, `subject_b` hit — "
        "as sample ids in `subject_b`'s numbering.",
    )
    lost: list[int] = Field(description="The cells `hit` counts as lost, numbered the same way.")


class MetricReading(StrictModel):
    """The selection read under ONE metric, echoed back with the vocabulary it was chosen from — a
    stale render cannot then show new bars under the old metric's label."""

    spec: MetricSpec
    catalogue: list[MetricSpec]
    namespace: list[str]
    scored_cells: list[str]
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
    note: str
    roster_note: str | None


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
    it would take to resolve the biggest gap the roster already shows. What a pair's WIDTH permits
    at any effect size is that pair's own ``estimate.p_floor``.
    """

    paired_se: float
    min_detectable_effect: float
    largest_subject_gap: float
    cells_per_subject: int
    cells_for_largest_gap: int | None


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
    order_confounded: bool


def metric_reading(
    spec: MetricSpec,
    rows: list[SubjectReading],
    available: frozenset[str],
    *,
    members: Mapping[str, SubjectMember],
    measurand: Measurand,
) -> MetricReading:
    scored = [r for r in rows if r.values]
    shared = sorted(set.intersection(*(set(r.values) for r in scored))) if scored else []
    pairwise = _pairwise(scored, members, measurand)
    return MetricReading(
        spec=spec,
        catalogue=list(catalogue_for(available)),
        namespace=sorted(available),
        scored_cells=shared,
        covered_cells=sorted({c for r in rows for c in (*r.values, *r.unscorable_cells)}),
        pairwise=pairwise,
        n_tests=sum(1 for p in pairwise if p.reading.headline is not None),
    )


def _pairwise(
    rows: list[SubjectReading], members: Mapping[str, SubjectMember], measurand: Measurand
) -> list[PairwiseComparison]:
    pairs = [(a.key, b.key) for i, a in enumerate(rows) for b in rows[i + 1 :]]
    readings = as_family(
        [pair_subjects(members[a], members[b], measurand) for a, b in pairs],
        f"pairwise:{measurand.key}",
    )
    (hit,) = (m for m in grade_measurands(measurand.scorer_id) if m.binary)
    out: list[PairwiseComparison] = []
    for (a, b), reading in zip(pairs, readings, strict=True):
        numbered = {key: cell.sample_id for key, cell in members[b].rows.sheet.by_key().items()}
        flips = flipped_keys(members[a].rows.by_key(), members[b].rows.by_key())
        out.append(
            PairwiseComparison(
                subject_a=a,
                subject_b=b,
                reading=reading,
                hit=pair_subjects(members[a], members[b], hit),
                gained=[numbered[key] for key in flips.gained],
                lost=[numbered[key] for key in flips.lost],
            )
        )
    return out


class _RowVerdict(NamedTuple):
    comparable: bool | None
    reason: ComparabilityReason
    note: str


def _row_verdicts(rows: list[SubjectReading]) -> list[_RowVerdict]:
    if not rows:
        return []
    majority_dataset = Counter(r.dataset_name for r in rows).most_common(1)[0][0]
    stamped = [r.ability for r in rows if r.ability is not None and r.ability.ruler_id is not None]
    majority_id = Counter(a.ruler_id for a in stamped).most_common(1)[0][0] if stamped else None
    majority = next((a for a in stamped if a.ruler_id == majority_id), None)

    def verdict(row: SubjectReading) -> _RowVerdict:
        if row.dataset_name != majority_dataset:
            return _RowVerdict(
                False,
                "datasets_differ",
                f"Measured on {row.dataset_name or 'another dataset'}, while the rest of this "
                f"selection is on {majority_dataset or 'a different one'} — they share no "
                f"question, so nothing here pairs and only its own level is readable.",
            )
        # Unknown, never a yes: an unstamped origin may sit on any scale.
        if row.ability is None or row.ability.ruler_id is None or majority is None:
            return _RowVerdict(None, "ruler_unstamped", "")
        if not row.ability.comparable_to(majority):
            return _RowVerdict(
                False,
                "rulers_differ",
                "Read against a different ruler from the rest of this selection — its cells "
                "still pair where they overlap, its absolute level is on another scale.",
            )
        return _RowVerdict(True, "one_ruler", "")

    return [verdict(r) for r in rows]


def stamp_comparable(rows: list[SubjectReading]) -> list[SubjectReading]:
    return [
        r.model_copy(update={"comparable": v.comparable, "comparable_note": v.note})
        for r, v in zip(rows, _row_verdicts(rows), strict=True)
    ]


def replicates(rows: list[SubjectReading]) -> list[ArmReplicate]:
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


# In PRECEDENCE order: the first reason any row carries is the selection's.
_SELECTION_VERDICT: dict[ComparabilityReason, tuple[bool | None, str]] = {
    "datasets_differ": (
        False,
        "Comparability NO — this selection spans several datasets, which measure different "
        "things. The values are not one quantity and no pairing rescues them; the roster and "
        "spend still compare, the numbers do not.",
    ),
    "ruler_unstamped": (
        None,
        "Comparability UNKNOWN — at least one origin carries no δ ruler, which is not "
        "the same as yes. Absolute levels above may sit on different δ scales: pair on cells, "
        "do not read the value column across campaigns.",
    ),
    "rulers_differ": (
        False,
        "Comparability NO — these origins were measured on different δ rulers, so their "
        "absolute values are not one quantity. Only within-ruler comparisons hold.",
    ),
    "one_ruler": (
        True,
        "Comparability YES — these origins were measured on one δ ruler, so their values are "
        "directly comparable.",
    ),
}
assert set(_SELECTION_VERDICT) == set(get_args(ComparabilityReason))


def comparability(rows: list[SubjectReading]) -> Comparability:
    verdicts = _row_verdicts(rows)
    carried = {v.reason for v in verdicts}
    notes = {v.note for v in verdicts}
    # An empty selection is UNKNOWN, never a yes.
    reason: ComparabilityReason = "ruler_unstamped"
    for candidate in _SELECTION_VERDICT:
        if candidate in carried:
            reason = candidate
            break
    verdict, note = _SELECTION_VERDICT[reason]
    return Comparability(
        verdict=verdict,
        reason=reason,
        datasets=sorted({r.dataset_name for r in rows if r.dataset_name}),
        n_rulers=len(
            {r.ability.ruler_id for r in rows if r.ability and r.ability.ruler_id is not None}
        ),
        note=note,
        roster_note=(
            next(iter(notes))
            if len(notes) == 1 and all(v.comparable is False for v in verdicts)
            else None
        ),
    )


def variance(by_subject: dict[str, dict[str, float]]) -> EvidenceVariance | None:
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


def power(
    decomposition: EvidenceVariance | None, rows: list[SubjectReading]
) -> EvidencePower | None:
    """Pairing removes the cell effect and leaves the residual on both arms: SE = residual * sqrt(2 / cells)."""
    levels = [r.value for r in rows if r.value is not None]
    if decomposition is None or len(levels) < 2 or decomposition.n_cells < 1:
        return None
    se = decomposition.residual_sd * (2.0 / decomposition.n_cells) ** 0.5
    mde = min_detectable_effect(se)
    gap = max(levels) - min(levels)
    # Same k the MDE used, re-solved for the cell count: n = 2 * (k * residual / gap)^2.
    needed = None
    if gap > 0.0 and se > 0.0:
        needed = math.ceil(2.0 * ((mde / se) * decomposition.residual_sd / gap) ** 2)
    return EvidencePower(
        paired_se=se,
        min_detectable_effect=mde,
        largest_subject_gap=gap,
        cells_per_subject=decomposition.n_cells,
        cells_for_largest_gap=needed,
    )


def order_confound(rows: list[SubjectReading]) -> OrderConfound | None:
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


__all__ = [
    "ArmReplicate",
    "Comparability",
    "EvidencePower",
    "EvidenceVariance",
    "MetricReading",
    "OrderConfound",
    "PairwiseComparison",
    "SubjectMember",
    "comparability",
    "metric_measurand",
    "metric_reading",
    "order_confound",
    "pair_subjects",
    "power",
    "replicates",
    "stamp_comparable",
    "variance",
]
