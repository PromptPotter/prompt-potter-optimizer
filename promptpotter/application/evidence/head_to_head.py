from __future__ import annotations

from collections.abc import Callable, Collection, Sequence
from enum import StrEnum
from typing import TYPE_CHECKING, NamedTuple, Self

from pydantic import ConfigDict, Field, model_validator

from promptpotter.application.datasets.authored import scorer_of
from promptpotter.application.evidence.subjects import SubjectReading
from promptpotter.application.jobs.quota import next_launch_ceiling
from promptpotter.application.optimizer_manifest import select_optimizer
from promptpotter.application.pipeline_resolve import resolve_campaign_config
from promptpotter.application.runner.bench import bench_members, pair_on_bench, read_bench
from promptpotter.application.runner.campaign_result import bench_line, line_passes, line_run
from promptpotter.application.scoring.paired import MemberRows, as_family
from promptpotter.domain.bench import BENCH_HEADLINE, BenchColumn, BenchScore
from promptpotter.domain.campaign import (
    Arm,
    ArmBudget,
    BenchSet,
    Campaign,
    HeadToHeadRecord,
    bench_set_of,
    ceiling_meter,
)
from promptpotter.domain.cycle_listing import RunStatus
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.paired_reading import PairedReading, ReadingState
from promptpotter.domain.spend import MeteredSpend, SpendRollup
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.runtime_flags import derive_run_state
from promptpotter.shared.clock import epoch_seconds

if TYPE_CHECKING:
    from promptpotter.application.campaign_config import CampaignConfig
    from promptpotter.domain.scoring import Scorer
    from promptpotter.infrastructure.store.stores import Stores


class GuardState(StrEnum):
    """Whether the bench headlines a guard is taken over are one quantity."""

    CONTROLLED = "controlled"
    UNCONTROLLED = "uncontrolled"
    DIFFERS = "differs"


GUARD_STATE_INFO: dict[GuardState, str] = {
    GuardState.CONTROLLED: (
        "One quantity, as declared: every campaign is an arm of one head-to-head, on its bank, "
        "split, held-out rows, scorer, target model, origin and budget, with one optimizer model "
        "set and no operator's steer."
    ),
    GuardState.UNCONTROLLED: (
        "One quantity, undeclared: nothing read here differs, and a campaign that is no arm of a "
        "head-to-head could read other campaigns' measurements and take an operator's steer."
    ),
    GuardState.DIFFERS: (
        "Not one quantity: what graded or bounded these headlines differs, so a gap between two "
        "is not the optimizer's alone."
    ),
}
assert GUARD_STATE_INFO.keys() == set(GuardState)

GUARD_STATE_LABELS: dict[GuardState, str] = {
    GuardState.CONTROLLED: "controlled",
    GuardState.UNCONTROLLED: "not controlled",
    GuardState.DIFFERS: "differs",
}
assert GUARD_STATE_LABELS.keys() == set(GuardState)


class GuardDifference(StrEnum):
    """One thing two bench headlines can differ on. The first seven are ``BenchSet``'s fields."""

    INSTRUMENT_ID = "instrument_id"
    DATASET_HASH = "dataset_hash"
    SPLIT = "split"
    BENCH_ROWS = "bench_rows"
    SCORER_ID = "scorer_id"
    MODELS = "models"
    ORIGIN = "origin"
    BUDGET = "budget"
    OPTIMIZER_MODELS = "optimizer_models"
    HUMAN_INTERVENED = "human_intervened"
    ORIGIN_READING = "origin_reading"


GUARD_DIFFERENCE_INFO: dict[GuardDifference, str] = {
    GuardDifference.INSTRUMENT_ID: (
        "Different instruments measured the cells: another dataset, or another inner origin under it."
    ),
    GuardDifference.DATASET_HASH: "The banks hold different rows.",
    GuardDifference.SPLIT: "The banks were split differently.",
    GuardDifference.BENCH_ROWS: (
        "Different rows were held out, so the headlines are graded on different cells."
    ),
    GuardDifference.SCORER_ID: "Different scorers graded the headlines.",
    GuardDifference.MODELS: "The target pipelines ran different models.",
    GuardDifference.ORIGIN: "The campaigns started from different origins.",
    GuardDifference.BUDGET: "The campaigns ran under different budgets.",
    GuardDifference.OPTIMIZER_MODELS: (
        "The optimizers called different models, so a gap between two headlines is the model's "
        "as much as the method's."
    ),
    GuardDifference.HUMAN_INTERVENED: (
        "An operator steered, skipped or model-overrode a cycle on a campaign's line."
    ),
    GuardDifference.ORIGIN_READING: (
        "Campaigns on one bench set read the same origin apart beyond its own noise under one "
        "formula, so the backend or a judge moved between them — a change no stamp names."
    ),
}
assert GUARD_DIFFERENCE_INFO.keys() == set(GuardDifference)
assert set(BenchSet.model_fields) <= {d.value for d in GuardDifference}


class PairGuard(StrictModel):
    """The comparability guard: whether the bench headlines it is taken over — a pair's, a row's
    against the table's standard, the whole table's — are one quantity. It LABELS a comparison
    and refuses none: a pair that differs is still read, and says on what."""

    model_config = ConfigDict(frozen=True)

    state: GuardState
    sentence: str = Field(description="`state` in words, with what differs behind it.")
    head_to_head_id: str | None = Field(
        description="The declared head-to-head the headlines were read against; `None` where no "
        "campaign read is an arm of one."
    )
    controlled: bool = Field(
        description="Every campaign it is taken over is an arm of that head-to-head."
    )
    differs_on: list[GuardDifference]

    @model_validator(mode="after")
    def _state_fits(self) -> Self:
        expected = (
            GuardState.DIFFERS
            if self.differs_on
            else GuardState.CONTROLLED
            if self.controlled
            else GuardState.UNCONTROLLED
        )
        if self.state is not expected:
            raise ValueError(f"a guard that reads {expected.value} cannot say {self.state.value}")
        return self


class GuardMember(NamedTuple):
    arm: Arm | None
    # `None` where the line drew no partition: nothing is held out for it to be graded on.
    bench_set: BenchSet | None
    budget: ArmBudget
    optimizer_models: tuple[str, ...]
    human_intervened: bool


def _apart(values: Sequence[object]) -> bool:
    return any(value != values[0] for value in values[1:])


def pair_guard(
    members: Sequence[GuardMember],
    record: HeadToHeadRecord | None,
    *,
    origin_read_apart: bool,
) -> PairGuard:
    bench_sets = [m.bench_set for m in members if m.bench_set is not None]
    budgets = [m.budget for m in members]
    if record is not None:
        bench_sets.append(record.bench_set)
        budgets.append(record.budget)
    differs_on = [
        GuardDifference(field)
        for field in BenchSet.model_fields
        if _apart([getattr(bench_set, field) for bench_set in bench_sets])
    ]
    if _apart(budgets):
        differs_on.append(GuardDifference.BUDGET)
    if len({m.optimizer_models for m in members}) > 1:
        differs_on.append(GuardDifference.OPTIMIZER_MODELS)
    if any(m.human_intervened for m in members):
        differs_on.append(GuardDifference.HUMAN_INTERVENED)
    if origin_read_apart:
        differs_on.append(GuardDifference.ORIGIN_READING)
    controlled = (
        bool(members)
        and record is not None
        and all(
            m.arm is not None and m.arm.head_to_head_id == record.head_to_head_id for m in members
        )
    )
    state = (
        GuardState.DIFFERS
        if differs_on
        else GuardState.CONTROLLED
        if controlled
        else GuardState.UNCONTROLLED
    )
    sentence = GUARD_STATE_INFO[state]
    if differs_on:
        sentence += f" Differs on: {', '.join(d.value for d in differs_on)}."
    return PairGuard(
        state=state,
        sentence=sentence,
        head_to_head_id=None if record is None else record.head_to_head_id,
        controlled=controlled,
        differs_on=differs_on,
    )


VERDICT_ABSENT_INFO: dict[ReadingState, str] = {
    ReadingState.UNDER_TWO_CELLS: (
        "No verdict: a head-to-head needs two campaigns with a graded bench set."
    ),
    ReadingState.PENDING: (
        "No verdict yet: a declared arm of this head-to-head has no bench headline, so the "
        "graded rows are not the comparison that was declared."
    ),
}
assert VERDICT_ABSENT_INFO.keys() <= set(ReadingState)


class HeadToHeadRow(StrictModel):
    """One campaign's bench headline beside what it cost to reach."""

    subject: str
    campaign_id: str
    optimizer: str
    optimizer_models: list[str]
    arm: Arm | None
    guard: PairGuard
    treatment_digest: str | None
    budget: ArmBudget
    spend_metered: MeteredSpend | None
    human_intervened: bool
    status: RunStatus
    bench: BenchScore
    bench_set: BenchSet | None
    spend: SpendRollup | None
    calls: int | None
    worked_s: float | None
    rounds: int
    incurred_usd_ratio: float | None
    optimizer_incurred_usd_ratio: float | None
    worked_ratio: float | None
    concurrent_with: list[str]


class SelectionPair(StrictModel):
    """Two campaigns' selections paired on the bench rows both scored."""

    reading: PairedReading
    guard: PairGuard


class HeadToHead(StrictModel):
    """The campaigns' bench headlines side by side, and whether one bench set graded them all."""

    rows: list[HeadToHeadRow]
    covers_selection: bool
    optimizers_differ: bool
    guard: PairGuard
    headline: BenchColumn
    verdict: bool | None
    verdict_absent: ReadingState | None
    pairs: list[SelectionPair]
    ratio_reference: str | None
    verdict_line: str
    notes: list[str]

    @model_validator(mode="after")
    def _verdict_or_why_not(self) -> Self:
        if (self.verdict is None) is (self.verdict_absent is None):
            raise ValueError("a head-to-head gives its verdict, or says why it gives none")
        return self


class HeadToHeadEntry(NamedTuple):
    reading: SubjectReading
    stores: Stores


class Grader(NamedTuple):
    scorer: Scorer
    record: HeadToHeadRecord | None


class _Graded(NamedTuple):
    row: HeadToHeadRow
    origin: MemberRows | None
    selected: MemberRows | None
    sample_keys: list[str]
    windows: list[tuple[float, float]]


def _declared(campaigns: list[tuple[Stores, Campaign]]) -> HeadToHeadRecord | None:
    """The record most arms name; a tie goes to the oldest arm's, so *campaigns* is oldest first."""
    named = [c.arm.head_to_head_id for _, c in campaigns if c.arm is not None]
    if not named:
        return None
    h2h_id = max(dict.fromkeys(named), key=named.count)
    stores = next(s for s, c in campaigns if c.arm is not None and c.arm.head_to_head_id == h2h_id)
    return stores.campaigns.load_head_to_head(h2h_id)


def comparison_grader(campaigns: list[tuple[Stores, Campaign]]) -> Grader:
    graders = {
        scorer.id: scorer
        for scorer in (
            scorer_of(resolve_campaign_config(stores, c, c.root_hop), verifier_graded=False)
            for stores, c in campaigns
        )
    }
    if len(graders) > 1:
        raise ValueError(
            f"These subjects are graded under {len(graders)} scorers ({', '.join(sorted(graders))}) "
            "and share none, so no column reads them as one quantity. Compare subjects one "
            "formula grades."
        )
    (scorer,) = graders.values()
    record = _declared(campaigns)
    if record is not None and scorer.id != record.bench_set.scorer_id:
        raise ValueError(
            f"head-to-head {record.head_to_head_id} declares scorer {record.bench_set.scorer_id}; "
            f"its arm's config grades as {scorer.id}"
        )
    return Grader(scorer, record)


def head_to_head(
    entries: list[HeadToHeadEntry], grader: Grader, asked: Collection[str]
) -> HeadToHead | None:
    """*entries* oldest first: the ratio reference and the reference-row tie both read that order."""
    if not entries:
        return None
    campaigns = [_campaign(entry) for entry in entries]
    configs = [
        resolve_campaign_config(e.stores, c, c.root_hop)
        for e, c in zip(entries, campaigns, strict=True)
    ]
    scorer, record = grader
    read = [
        _read(e, c, config, scorer, record)
        for e, c, config in zip(entries, campaigns, configs, strict=True)
    ]
    base = next(
        (g.row for g in read if g.row.spend is not None and g.row.worked_s is not None), None
    )
    graded = [g for g in read if g.row.bench.graded is not None]
    reference = (
        max(graded, key=lambda g: sum(_agree(g, o, None) for o in graded), default=None)
        if record is None
        else None
    )
    guard = _guard(graded, record)
    ungraded_arms = [
        g.row.campaign_id
        for g in read
        if g.row.bench.graded is None
        and record is not None
        and g.row.arm is not None
        and g.row.arm.head_to_head_id == record.head_to_head_id
    ]
    verdict_absent = (
        ReadingState.UNDER_TWO_CELLS
        if len(graded) < 2
        else ReadingState.PENDING
        if ungraded_arms and guard.state is not GuardState.DIFFERS
        else None
    )
    verdict = None if verdict_absent is not None else guard.state is not GuardState.DIFFERS
    concurrent = {
        g.row.campaign_id: [o.row.campaign_id for o in read if o is not g and _overlapped(g, o)]
        for g in read
    }
    return HeadToHead(
        rows=[
            g.row.model_copy(
                update={
                    "guard": g.row.guard
                    if reference is None or g.row.bench.graded is None
                    else _guard([g, reference], None),
                    "concurrent_with": concurrent[g.row.campaign_id],
                    **_ratios(g.row, base),
                }
            )
            for g in read
        ],
        covers_selection=set(asked) <= {g.row.subject for g in read},
        optimizers_differ=len({g.row.optimizer for g in read}) > 1,
        guard=guard,
        headline=BENCH_HEADLINE,
        verdict=verdict,
        verdict_absent=verdict_absent,
        pairs=_pairs(graded, record),
        ratio_reference=None if base is None else base.campaign_id,
        verdict_line=_verdict_line(guard, verdict_absent, len(graded), ungraded_arms),
        notes=_notes(
            verdict,
            guard,
            len(read) - len(graded) - len(ungraded_arms),
            [cid for cid, others in concurrent.items() if others],
            uncontrolled=[
                g.row.campaign_id for g in read if g.row.guard.state is GuardState.UNCONTROLLED
            ],
        ),
    )


def _member(g: _Graded) -> GuardMember:
    row = g.row
    return GuardMember(
        arm=row.arm,
        bench_set=row.bench_set,
        budget=row.budget,
        optimizer_models=tuple(row.optimizer_models),
        human_intervened=row.human_intervened,
    )


def _guard(graded: Sequence[_Graded], record: HeadToHeadRecord | None) -> PairGuard:
    return pair_guard(
        [_member(g) for g in graded],
        record,
        origin_read_apart=any(
            a.row.bench_set == b.row.bench_set and not _read_alike(a, b)
            for i, a in enumerate(graded)
            for b in graded[i + 1 :]
        ),
    )


def _agree(a: _Graded, b: _Graded, record: HeadToHeadRecord | None) -> bool:
    return _guard([a, b], record).state is not GuardState.DIFFERS


def _ratios(row: HeadToHeadRow, base: HeadToHeadRow | None) -> dict[str, float | None]:
    def over(fact: Callable[[HeadToHeadRow], float | None]) -> float | None:
        value, of_base = fact(row), None if base is None else fact(base)
        return None if value is None or of_base is None or of_base <= 0.0 else value / of_base

    return {
        "incurred_usd_ratio": over(
            lambda r: None if r.spend is None else r.spend.total_incurred_usd
        ),
        "optimizer_incurred_usd_ratio": over(
            lambda r: None if r.spend is None else r.spend.by_kind["optimizer"].incurred_usd
        ),
        "worked_ratio": over(lambda r: r.worked_s),
    }


def _overlapped(a: _Graded, b: _Graded) -> bool:
    return any(sa < eb and sb < ea for sa, ea in a.windows for sb, eb in b.windows)


def _read_alike(a: _Graded, b: _Graded) -> bool:
    if a.origin is None or b.origin is None:
        return True
    gap = _across(a.origin, b.origin, a.sample_keys).headline
    return gap is None or gap.estimate.side == "spans"


def _across(a: MemberRows, b: MemberRows, sample_keys: list[str]) -> PairedReading:
    return pair_on_bench(a, b, sample_keys=sample_keys, instrument_id=a.instrument_id)


def _campaign(entry: HeadToHeadEntry) -> Campaign:
    campaign = entry.stores.campaigns.load_campaign(entry.reading.campaign_id)
    if campaign is None:
        raise ValueError(
            f"campaign {entry.reading.campaign_id} has no manifest to read its bench from"
        )
    return campaign


def _status(stores: Stores, campaign: Campaign) -> RunStatus:
    holder = stores.campaigns.line_holder(campaign.root_hop)
    index = stores.campaigns.load(holder)
    run = derive_run_state(stores.campaigns.cycle_dir(holder))
    return RunStatus.of(run.run_phase, None if index is None else index.stop_reason)


def _held_budget(stores: Stores, config: CampaignConfig, hop: CycleHop) -> ArmBudget:
    ceiling = next_launch_ceiling(config, stores=stores, hop=hop)
    rounds = stores.campaigns.read_run_limits(hop).rounds
    return config.optimization.arm_budget.model_copy(
        update={
            "usd": ceiling.usd,
            "max_rounds": config.optimization.max_rounds if rounds is None else rounds.max_rounds,
        }
    )


def _read(
    entry: HeadToHeadEntry,
    campaign: Campaign,
    config: CampaignConfig,
    scorer: Scorer,
    record: HeadToHeadRecord | None,
) -> _Graded:
    reading, stores = entry
    result = stores.campaigns.load_result(campaign.campaign_id)
    hop = stores.campaigns.line_holder(campaign.root_hop)
    selected = select_optimizer(config.optimization)
    optimizer_models = sorted({selected.model(node) for node in selected.llm_nodes})
    passes = line_passes(stores, campaign.campaign_id)
    line = bench_line(stores, campaign, hop, config, run=line_run(stores, hop))
    bench = read_bench(stores, passes, scorer, line=line)
    origin_pass, selected_pass = (
        (None, None) if passes is None else bench_members(stores, passes, scorer, line=line)
    )

    drawn = stores.campaigns.read_bank_partition(hop)
    origin_round = stores.campaigns.standing_rounds(hop).rounds.get(0)
    bench_set = (
        None
        if drawn is None or campaign.root_content_hash is None
        else bench_set_of(
            dataset_name=campaign.dataset_name,
            dataset_hash=drawn.dataset_hash,
            split=drawn.split,
            bench_ids=drawn.bench_ids,
            scorer_id=scorer.id,
            origin_params=None if origin_round is None else origin_round.close.pipeline_params,
            origin=campaign.root_content_hash,
        )
    )
    cost = None if result is None else result.cost
    budget = _held_budget(stores, config, hop)
    # Live off the line's ledgers: the banked cost is the last ended launch's, so a running arm's lags.
    spend = None if cost is None else line.spend
    windows = [
        (start, end)
        for run in ([] if cost is None else cost.launches)
        if (start := epoch_seconds(run.started_at)) is not None
        and (end := epoch_seconds(run.finished_at)) is not None
    ]
    return _Graded(
        row=HeadToHeadRow(
            subject=reading.key,
            campaign_id=reading.campaign_id,
            optimizer=config.optimization.optimizer,
            optimizer_models=optimizer_models,
            arm=campaign.arm,
            guard=pair_guard(
                [
                    GuardMember(
                        arm=campaign.arm,
                        bench_set=bench_set,
                        budget=budget,
                        optimizer_models=tuple(optimizer_models),
                        human_intervened=reading.human_intervened,
                    )
                ],
                record,
                origin_read_apart=False,
            ),
            treatment_digest=None if campaign.treatment is None else campaign.treatment.digest,
            budget=budget,
            spend_metered=None
            if spend is None
            else MeteredSpend.of(spend, ceiling_meter(campaign.arm)),
            human_intervened=reading.human_intervened,
            status=_status(stores, campaign),
            bench=bench,
            bench_set=bench_set,
            spend=spend,
            calls=None if cost is None else cost.calls,
            worked_s=None if cost is None else cost.worked_s,
            rounds=reading.cycle_rounds_scored,
            incurred_usd_ratio=None,
            optimizer_incurred_usd_ratio=None,
            worked_ratio=None,
            concurrent_with=[],
        ),
        origin=origin_pass,
        selected=selected_pass,
        sample_keys=[] if passes is None else passes.origin.sample_keys,
        windows=windows,
    )


def _pairs(graded: list[_Graded], record: HeadToHeadRecord | None) -> list[SelectionPair]:
    read: list[tuple[PairedReading, PairGuard]] = []
    for i, a in enumerate(graded):
        for b in graded[i + 1 :]:
            if a.selected is None or b.selected is None:
                continue
            digest = a.row.treatment_digest
            if digest is not None and digest == b.row.treatment_digest:
                continue
            read.append((_across(a.selected, b.selected, a.sample_keys), _guard([a, b], record)))
    tested = iter(
        as_family(
            [pair for pair, guard in read if guard.state is not GuardState.DIFFERS],
            "bench" if record is None else record.head_to_head_id,
        )
    )
    return [
        SelectionPair(
            reading=pair if guard.state is GuardState.DIFFERS else next(tested), guard=guard
        )
        for pair, guard in read
    ]


def _verdict_line(
    guard: PairGuard, absent: ReadingState | None, n_graded: int, ungraded_arms: list[str]
) -> str:
    if absent is None:
        return guard.sentence
    counted = (
        f"Ungraded: {', '.join(ungraded_arms)}."
        if absent is ReadingState.PENDING
        else f"{n_graded} here."
    )
    return f"{VERDICT_ABSENT_INFO[absent]} {counted}"


def _notes(
    verdict: bool | None,
    guard: PairGuard,
    n_outside: int,
    concurrent: list[str],
    *,
    uncontrolled: list[str],
) -> list[str]:
    notes = []
    if guard.head_to_head_id is not None:
        notes.append(
            f"Read against head-to-head {guard.head_to_head_id}'s declared bench set and budget."
        )
    if guard.head_to_head_id is not None and uncontrolled:
        notes.append(
            f"NOT CONTROLLED: {', '.join(uncontrolled)}. Ran as no arm of head-to-head "
            f"{guard.head_to_head_id}, so its search could read other campaigns' measurements "
            "and take an operator's steer."
        )
    if verdict is True:
        notes.append("Every pair below is read on the same rows.")
    if verdict is False:
        notes.append(
            "A row off the declared or most shared bench set is marked, and a pair across two "
            "is read and labelled, outside the correction."
        )
    notes += [f"`{d.value}`: {GUARD_DIFFERENCE_INFO[d]}" for d in guard.differs_on]
    if n_outside > 0:
        notes.append(
            f"{n_outside} campaign(s) carry no bench headline — nothing held out, or the "
            "line has not graded its selection — and sit outside the verdict."
        )
    if concurrent:
        notes.append(
            f"{', '.join(concurrent)} ran concurrently on one content-addressed cache: the first "
            "to reach a cell paid and the rest replayed it, so the bill and the clock split by "
            "arrival. The USD ratios price INCURRED spend, which counts a replay as paid; the "
            "worked-seconds ratio stays confounded."
        )
    return notes


__all__ = [
    "GUARD_DIFFERENCE_INFO",
    "GUARD_STATE_INFO",
    "GUARD_STATE_LABELS",
    "VERDICT_ABSENT_INFO",
    "Grader",
    "GuardDifference",
    "GuardMember",
    "GuardState",
    "HeadToHead",
    "HeadToHeadEntry",
    "HeadToHeadRow",
    "PairGuard",
    "SelectionPair",
    "comparison_grader",
    "head_to_head",
    "pair_guard",
]
