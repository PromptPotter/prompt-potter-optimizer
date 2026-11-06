from __future__ import annotations

from pydantic import Field

from promptpotter.domain.arms import ArmStateKind
from promptpotter.domain.cycle_paths import SUBJECT_ARITY, CycleHop, SubjectKind
from promptpotter.domain.ruler import AbilityReading
from promptpotter.domain.run_records import OPERATOR_ORIGIN_SOURCES
from promptpotter.domain.said import Said
from promptpotter.domain.strict_model import StrictModel
from promptpotter.shared.identity import Principal
from promptpotter.shared.worded import Worded

SUBJECT_KIND_LABELS: Worded[SubjectKind, str] = Worded(
    {
        "campaign": "origin",
        "course": "branch head",
        "candidate": "searchpoint",
    }
)
assert SUBJECT_KIND_LABELS.keys() == SUBJECT_ARITY.keys()


def authorship_of(source: str, issued_by: Principal | None) -> str:
    """An operator-authored origin under the principal whose fork seeded it; a root names its source."""
    if source not in OPERATOR_ORIGIN_SOURCES or issued_by is None:
        return source
    return f"operator:{issued_by.id}"


class SubjectMask(StrictModel):
    """The mask this channel is read under, echoed back. Served rather than left implicit in the
    key, so a chart legend can say what a channel IS without re-splitting an address."""

    lens: str | None
    samples: list[int] | None


_SCENARIO_CAVEAT = (
    "This re-ranks the RECORD under the formula you named — it does not re-run the campaign. θ is "
    "not re-fitted and no election is replayed, so the round named here is where the two readings "
    "first part, not a verdict the campaign reached. The chain STOPS at that round: past it the run "
    "would have stood on a parent it never had, and no measurement says what that produces. `ab` "
    "replay is what re-derives an election exactly."
)


def scenario_said(
    lens: str | None,
    *,
    recorded_winner_key: str | None,
    scenario_winner_key: str | None,
    first_divergent_round: int | None,
    invariant_rounds: int,
    total_rounds: int,
    n_samples_scored: int,
) -> Said:
    """What the mask did to this branch, worded once for every surface after the subject's label:
    how far it agrees with the record, and the round it stops; ``warn`` where it parts, its detail
    the caveat, since the honest limit of a lens is not something a surface can be trusted to
    remember. *lens* is the address's (`SubjectMask.lens`).

    The chain ENDS where the two readings part (`mask/divergence.py`), so the pair of winners is one
    round's disagreement, both read at *first_divergent_round*, or at the branch's last round where
    there is none: a mid-chain counterfactual against the branch's final crown would compare two
    different rounds and report the gap between them as a change."""
    verdict = (
        "never parts from the record"
        if first_divergent_round is None
        else (
            f"parts from the record at round {first_divergent_round}, where it would have crowned "
            f"{(scenario_winner_key or '-')[:8]} instead of {(recorded_winner_key or '-')[:8]}"
        )
    )
    rounds = "round" if total_rounds == 1 else "rounds"
    samples = "sample" if n_samples_scored == 1 else "samples"
    return Said(
        key="scenario",
        tone="quiet" if first_divergent_round is None else "warn",
        label=(
            f"under {lens or '-'}: {verdict}. {invariant_rounds} of {total_rounds} {rounds} "
            f"unchanged; the head reads over {n_samples_scored} {samples}."
        ),
        detail=_SCENARIO_CAVEAT,
    )


class WinnerChainPoint(StrictModel):
    """One step of the branch standing behind a subject — the winner chain from the origin up to
    its head, each point read on ITS OWN cells under the selected metric. Opt-in
    (``include_winner_chain``), because every point past the origin opens a round file.

    **Named for the chain, never "trajectory".** The subsets move between rounds, so this reads
    each point on the evidence that point actually had; the round's own `overlap` line is the
    OPPOSITE basis — that same chain on one shared set of cells. Two readings of one sequence
    that disagree by construction, and under one word a reader could tell them apart from
    neither name. The word survives where a series genuinely is one (`p_best_trajectory`,
    the Sample-trajectory grid)."""

    candidate_id: str
    round: int
    label: str
    value: float | None
    ci_lo: float | None
    ci_hi: float | None
    n_cells: int


class SubjectShortLabels(StrictModel):
    """What a row with no room for an id names the subject's campaign, course and outer campaign by:
    each the `short_label` its hierarchy entry is served, by the one function serving it."""

    campaign: str
    course: str
    # The campaign whose run seeded this sandbox; null outside one.
    seed_of: str | None


class SubjectReading(StrictModel):
    """One subject, read under the selected metric — its identity, its per-cell values and the one
    estimate they merge to. ONE row, because a roster row and a metric reading that live in separate
    lists can disagree about the same subject, and under the default metric they held the same
    number reached two ways.

    ``values`` is keyed by the cell's QUERY, the identity that survives across campaigns; a cell the
    metric cannot read is ABSENT from it and named in ``unscorable_cells`` rather than scored — the
    two absences are different facts and a surface renders them as different glyphs.
    ``ci_lo``/``ci_hi`` are ``None`` below two scored cells — one reading has no spread, and a
    bracket drawn from it is a fiction.
    """

    key: str
    kind: SubjectKind
    inside: list[CycleHop]
    campaign_id: str
    cycle_id: str
    candidate_id: str
    # What every row names the subject by: a campaign's is its `short_labels.campaign`.
    label: str
    short_labels: SubjectShortLabels
    dataset_name: str
    created_at: str
    # `None` is UNKNOWN (an unstamped ruler): never render it as `True`.
    comparable: bool | None
    comparable_note: str
    mask: SubjectMask | None
    # What the mask did to this branch (`scenario_said`), on a masked course alone.
    scenario: Said | None
    winner_chain: list[WinnerChainPoint] | None
    config: dict[str, str] | None
    # `None` where round 0 carries no hashes: an unknown arm groups with nothing.
    arm_id: str | None
    # `""` where the ledger names no source for the point, which groups with nothing.
    authorship: str
    human_intervened: bool
    status: ArmStateKind = Field(
        description="Where the resolved searchpoint's arm stands, in the lineage tree's own words. "
        "`proposed` / `measuring` = its walk is still open (or ended with its producer), so "
        "`n_cells`, `values` and `cell_means` cover only the cells it has scored SO FAR and every "
        "reading here is a prefix, of `expected_samples`. `cut` = its walk stopped short of its "
        "panel; `measured` = its walk ended and these are all its cells.",
    )
    expected_samples: int | None = Field(
        description="How many cells the point's walk was sized for — the denominator `n_cells` is "
        "read against. The score report's own once it lands; before that, what the walk last "
        "announced, which a racing arm raises block by block. Null until its first cell lands.",
    )
    # `None` where the point carries no score report — never 0, which means "it earned every cell".
    cached_samples: int | None
    instrument_id: str
    # The point's own rows on the scale its cycle's last close read the series on; null off it.
    ability: AbilityReading | None
    round: int
    cycle_spend_usd: float | None
    cycle_rounds_scored: int
    spend_to_round: dict[str, float]
    # A channel none of its cells carries is absent, never 0.
    cell_means: dict[str, float]
    values: dict[str, float]
    bench_values: dict[str, float] = Field(
        default_factory=dict,
        description="The subject's values on the cells it was graded on HELD OUT — its bench "
        "pass, which only a round's selection has — keyed like `values` and never pooled with "
        "them: two scopes never pair.",
    )
    value: float | None
    ci_lo: float | None
    ci_hi: float | None
    n_cells: int
    unscorable_cells: list[str]
    levels: dict[str, str] = Field(default_factory=dict)
