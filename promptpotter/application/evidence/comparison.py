from __future__ import annotations

import math
from collections import Counter
from typing import Literal

from promptpotter.application.evidence.metric_catalogue import MetricSpec, catalogue_for
from promptpotter.application.evidence.subjects import SubjectReading
from promptpotter.domain.strict_model import StrictModel
from promptpotter.shared.statistics import (
    cells_for_exact_verdict,
    exact_p_floor,
    exact_paired_reading,
    holm_adjusted,
    min_detectable_effect,
    rank_correlation,
    two_way_effect_sds,
)

# |rho| at or above this and the roster's ordering IS its chronology — see `OrderConfound`.
_ORDER_CONFOUND_RHO = 0.9

ComparabilityReason = Literal["one_ruler", "rulers_differ", "ruler_unstamped", "datasets_differ"]


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


def metric_reading(
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


def stamp_comparable(rows: list[SubjectReading]) -> list[SubjectReading]:
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


def comparability(rows: list[SubjectReading]) -> Comparability:
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


def variance(by_subject: dict[str, dict[str, float]]) -> EvidenceVariance | None:
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


def power(
    decomposition: EvidenceVariance | None, rows: list[SubjectReading], *, n_tests: int
) -> EvidencePower | None:
    """The paired SE of a two-arm contrast: pairing removes the cell effect — the term that is
    largest here — and leaves the residual on both arms, hence ``residual * sqrt(2 / cells)``.

    Beside it the width limit, which no SE can see: an exact test on this many cells has a smallest
    reachable p, and *n_tests* is the correction it has to clear."""
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
        exact_p_floor=exact_p_floor(decomposition.n_cells),
        cells_for_corrected_verdict=cells_for_exact_verdict(n_tests),
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
    "comparability",
    "metric_reading",
    "order_confound",
    "power",
    "replicates",
    "stamp_comparable",
    "variance",
]
