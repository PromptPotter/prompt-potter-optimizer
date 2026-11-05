from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from promptpotter.application.datasets.draft_campaign import DraftCampaign, owned_drafts
from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.application.pipeline_resolve import LineConfig, campaign_runs_with, line_config
from promptpotter.application.runner.campaign_result import cycle_bench
from promptpotter.application.runner.inner.tasks import is_self_optimization
from promptpotter.application.served_dashboard import binding_run_limits, run_said
from promptpotter.domain.arm_reading import (
    DISPLAY_METRIC_INFO,
    ArmReading,
    level_text,
    lift_band_text,
)
from promptpotter.domain.bench import BenchScore
from promptpotter.domain.campaign import (
    LIFECYCLE_STATUS_LABELS,
    Campaign,
    CampaignRunsWith,
    Compare,
    LifecycleFilter,
    ceiling_meter,
)
from promptpotter.domain.cycle_index import STATUS_MARK_INFO, CycleIndex, RunStatus
from promptpotter.domain.cycle_paths import (
    CycleHop,
    CyclePath,
    Subject,
)
from promptpotter.domain.hierarchy import (
    CANDIDATE_STANDING_INFO,
    CampaignFacts,
    CandidateStanding,
    CourseClimb,
    CourseFacts,
    CourseKind,
    CourseSpawn,
    Figure,
    FigureKey,
    HierarchyCampaign,
    HierarchyCourse,
    HierarchyResponse,
    HierarchySpan,
    HierarchyState,
    HierarchyTag,
    ListingLifecycle,
    UnstartedCampaign,
    UnstartedFacts,
)
from promptpotter.domain.paired_reading import READING_STATE_INFO, PairedReading, ReadingState
from promptpotter.domain.phases import LaunchStage
from promptpotter.domain.ruler import THETA_CAVEAT_INFO
from promptpotter.domain.said import Said
from promptpotter.domain.scoring import ScoringFormulaError
from promptpotter.domain.spend import RATE_PRICED_LABEL, LifetimeSpend, MeteredSpend, usd_text
from promptpotter.infrastructure.projections.cycle_index import read_cycle_index
from promptpotter.infrastructure.runtime_flags import derive_run_state
from promptpotter.infrastructure.store.account_spend import billed_spend, campaign_spend
from promptpotter.infrastructure.store.campaign_store.ledger_scan import scan_ledger_spend
from promptpotter.infrastructure.store.dataset_access import definition_version
from promptpotter.infrastructure.store.layout import sibling_kind
from promptpotter.infrastructure.store.read_model import LedgerSpan, Moment, file_sig
from promptpotter.infrastructure.store.stores import Stores, descend_store
from promptpotter.shared.errors import BadRequestError

_MINT_WORDS: dict[CourseKind, str] = {
    "root": "root",
    "fork": "fork",
    "diag": "diag",
    "inner": "inner run",
}
_UNRESOLVED = "Its root pipeline did not resolve, so its settings and bench are unread."
_UNMINTED = "Its line course has no mint record, so its rounds and standing are unread."
_UNSCORED = "Its config did not resolve, so its limits and bench are unread."


def _course_label(mint: CourseKind, cycle_id: str) -> str:
    return f"{_MINT_WORDS[mint]} ·{cycle_id.rpartition('_')[2][:6]}"


def _figure(key: FigureKey, label: str, value: str, sub: str | None = None) -> Figure:
    return Figure(key=key, tone="quiet", label=label, value=value, sub=sub)


def caveat_row(key: str, label: str, detail: str | None = None) -> Said:
    """What an entry could not read, among its lines."""
    return Said(key=key, tone="warn", label=label, detail=detail)


def _rounds_row(closed: int, cap: int | None) -> Figure:
    value = (
        "origin only"
        if cap == 0
        else f"{closed} · no rounds axis"
        if cap is None
        else f"{closed} of {cap}"
    )
    return _figure("rounds", "Rounds", value)


def _state_row(status: RunStatus) -> Figure:
    return Figure(
        key="state", tone=STATUS_MARK_INFO[status.mark].tone, label="State", value=status.label
    )


def _spend_row(
    key: FigureKey, label: str, spent: MeteredSpend | LifetimeSpend, unreported: float | None
) -> Figure:
    return _figure(
        key,
        label,
        f"{'≥' if spent.bill_is_floor else ''}{usd_text(spent.billed_usd)}",
        f"up to {usd_text(unreported)} more unreported"
        if unreported is not None
        else f"{usd_text(spent.rate_priced_usd)} {RATE_PRICED_LABEL}"
        if spent.calls_rate_priced
        else None,
    )


def _lifetime_row(spent: LifetimeSpend) -> Figure:
    return _spend_row(
        "spend_lifetime", "Spent", spent, spent.unreported_usd if spent.sends_unreported else None
    )


def _lift_row(key: FigureKey, label: str, pair: PairedReading, against: str) -> Figure:
    lift = pair.headline
    if lift is None or pair.coverage is None:
        info = READING_STATE_INFO[pair.state]
        return _figure(key, label, info.label, info.sentence)
    unit = lift.measurand.unit
    return _figure(
        key,
        label,
        lift_band_text(lift.estimate, unit),
        f"{1 - pair.spec.alpha:.0%} band · "
        f"{against} {level_text(lift.rate_a, unit)} → {level_text(lift.rate_b, unit)}",
    )


def _course_climb(facts: CourseFacts) -> CourseClimb | None:
    climb = None if facts.standing is None else facts.standing.climb
    if climb is None:
        return None
    theta = ["rounds are won on θ"] if facts.elects_on == "ability" else []
    return CourseClimb(
        rates=[
            _figure(rate.key, rate.label, "—" if rate.value is None else rate.value, rate.sub)
            for rate in climb.rates
        ],
        note=Said(
            key="climb_note", tone="quiet", label=" · ".join([f"lift in {climb.measurand}", *theta])
        ),
    )


def _bench_row(state: ReadingState, bench: BenchScore | None) -> Figure:
    level = None if bench is None or bench.selected is None else bench.selected.level
    notes = [READING_STATE_INFO[state].sentence]
    # How far a pass got rides the sentence, where it stopped short of its rows.
    if (
        bench is not None
        and (scored := bench.status.scored) is not None
        and (expected := bench.status.expected) is not None
        and scored < expected
    ):
        notes.append(f"{scored} of {expected} rows")
    return _figure(
        "bench",
        "Bench",
        READING_STATE_INFO[state].label
        if bench is None or level is None
        else level_text(level.value, DISPLAY_METRIC_INFO[bench.headline].unit),
        " · ".join(notes),
    )


def _course_figures(
    status: RunStatus,
    facts: CourseFacts,
    cap: int | None,
    runs_with: CampaignRunsWith | None,
    *,
    on_line: bool,
) -> list[Figure]:
    bench = facts.bench
    rows = [_state_row(status)]
    if runs_with is not None:
        models = [model for vendor in runs_with.vendors for model in vendor.models]
        rows.append(_figure("optimizer", "Optimizer", runs_with.optimizer, " · ".join(models)))
    rows.append(_spend_row("spend", "Spend", facts.spend, None))
    if not on_line:
        rows.append(_bench_row(ReadingState.HELD_ELSEWHERE, None))
    elif bench is not None:
        rows.append(_bench_row(bench.status.state, bench))
    rows.append(_rounds_row(facts.rounds_closed, cap))
    if (standing := facts.standing) is None:
        unread = PairedReading.unread(ReadingState.PENDING)
        return [*rows, _lift_row("lift", "Lift vs origin", unread, "origin")]
    rows.append(_figure("candidates", "Candidates", str(standing.candidates)))
    rows.append(_figure("selection", "Leader", standing.selection_name))
    rows.append(_lift_row("lift", "Lift vs origin", standing.vs_origin, "origin"))
    if (lives := standing.lives) is not None:
        rows.append(_figure("lives", "Lives", lives.bar, lives.label))
    return rows


def _course_lines(runs_with: CampaignRunsWith | None, line: LineConfig) -> list[Said]:
    if runs_with is None:
        return [caveat_row("unresolved", _UNRESOLVED)]
    if line.unread is not None:
        return [caveat_row("unscored", _UNSCORED, detail=line.unread)]
    return []


def candidate_lines(reading: ArmReading, standing: CandidateStanding) -> list[Said]:
    lines = [Said(key="standing", tone="quiet", label=CANDIDATE_STANDING_INFO[standing])]
    if reading.ability is None or (theta := reading.ability.caveat) is None:
        return lines
    info = THETA_CAVEAT_INFO[theta]
    return [*lines, Said(key="theta_caveat", tone=info.tone, label=info.label, detail=info.detail)]


def candidate_figures(reading: ArmReading) -> list[Figure]:
    panel, ability = reading.panel, reading.ability
    accuracy = None if reading.own is None else reading.own.accuracy
    rows = [
        _figure(
            "accuracy",
            "Accuracy",
            level_text(None if accuracy is None else accuracy.value, "rate"),
            None
            if panel.scored is None
            else f"{panel.scored}{'' if panel.expected is None else f' of {panel.expected}'} scored",
        )
    ]
    if ability is not None and (ability.theta is not None or ability.caveat is not None):
        theta = "—" if ability.theta is None else f"{ability.theta:.2f}"
        rows.append(
            _figure(
                "ability",
                "Ability θ",
                theta
                if ability.se is None or ability.theta is None
                else f"{theta} ± {ability.se:.2f}",
                None if ability.caveat is None else "not ability — see below",
            )
        )
    if reading.vs_reference is not None:
        rows.append(_lift_row("lift", "Lift vs parent", reading.vs_reference, "parent"))
    return rows


def superseded_tag(successor: str) -> HierarchyTag:
    """Worn by a retired course or candidate: the course that took its place, named as its card."""
    cycle_id = Subject.parse_address(successor).cycle_id
    label = f"retired by {_course_label(sibling_kind(cycle_id), cycle_id)}"
    return HierarchyTag(kind="superseded", value=successor, label=label)


def course_entry(
    stores: Stores,
    inside: CyclePath,
    campaign: Campaign,
    cycle_dir: Path,
    index: CycleIndex,
    *,
    head: CycleIndex,
    holder: CycleHop,
    moment: Moment | None,
    runs_with: CampaignRunsWith | None,
    line: LineConfig,
    bench: BenchScore | None,
) -> HierarchyCourse:
    hop = CycleHop(campaign_id=campaign.campaign_id, cycle_id=cycle_dir.name)
    # The producer as it is now: a moment replays the record, never a process.
    run = derive_run_state(cycle_dir)
    limits = binding_run_limits(stores, hop, line.config, run)
    # Before its first launch a course declares no wiring, so its campaign's declared cap binds.
    cap = (
        (None if runs_with is None else runs_with.budget_rounds)
        if limits is None
        else limits.budget.rounds
    )
    ledger = stores.campaigns.ledger(hop)
    spent = MeteredSpend.of(
        scan_ledger_spend(
            [LedgerSpan(ledger, None if moment is None else moment.span(ledger).until)]
        ).spend,
        ceiling_meter(campaign.declaration.purpose),
    )
    mint: CourseKind = sibling_kind(hop.cycle_id)
    # Inner by where it lives: a rebase pair in the sandbox has no `spawned_by`.
    if mint == "root" and (index.spawned_by is not None or stores.inner):
        mint = "inner"
    tags = [
        HierarchyTag(kind="dataset", value=campaign.dataset_name, label=campaign.dataset_name),
        HierarchyTag(kind="mint", value=mint, label=_MINT_WORDS[mint]),
    ]
    if (successor := index.superseded_by) is not None:
        tags.append(superseded_tag(Subject(hop.campaign_id, successor, inside=inside).address))
    fork, parent_id, cut = index.fork, index.parent_cycle_id, index.cut_round
    spawn = (
        None
        if fork is None or parent_id is None or cut is None
        else CourseSpawn(
            parent=Subject(hop.campaign_id, parent_id, inside=inside).address,
            cut_round=cut,
            trigger=fork.trigger,
            direction=fork.resolved_direction,
            issued_by=fork.issued_by.display,
        )
    )
    task = None if index.spawned_by is None else index.spawned_by.task or None
    # Run state is the head's under any moment: a replayed stop beside a live phase is no state.
    state = HierarchyState.of(run, head, lifecycle=campaign.lifecycle_status)
    facts = CourseFacts(
        mint=mint,
        spawn=spawn,
        task=task,
        standing=index.standing,
        rounds_closed=index.rounds_closed,
        elects_on=index.elects_on,
        spend=spent,
        bench=bench,
    )
    return HierarchyCourse(
        address=Subject(hop.campaign_id, hop.cycle_id, inside=inside).address,
        parent=Subject(hop.campaign_id, inside=inside).address,
        # An inner course is named for the outer panel cell it measures, by its last segment.
        label=_course_label(mint, hop.cycle_id) if task is None else task.rpartition("/")[2],
        tags=tags,
        figures=_course_figures(state.status, facts, cap, runs_with, on_line=hop == holder),
        climb=_course_climb(facts),
        lines=_course_lines(runs_with, line),
        state=state,
        span=HierarchySpan(
            opened_at=index.created_at,
            last_at=index.updated_at,
        ),
        facts=facts,
        said=run_said(
            index.error,
            run.run_phase,
            index.rounds[-1].health if index.rounds else None,
        ),
    )


def _campaign_tags(campaign: Campaign, runs_with: CampaignRunsWith | None) -> list[HierarchyTag]:
    tags = [HierarchyTag(kind="dataset", value=campaign.dataset_name, label=campaign.dataset_name)]
    if runs_with is not None:
        tags.append(
            HierarchyTag(kind="optimizer", value=runs_with.optimizer, label=runs_with.optimizer)
        )
        tags.extend(
            HierarchyTag(kind="vendor", value=v.vendor, label=v.vendor) for v in runs_with.vendors
        )
    if campaign.lifecycle_status != "active":
        status = campaign.lifecycle_status
        tags.append(
            HierarchyTag(
                kind="lifecycle", value=status, label=LIFECYCLE_STATUS_LABELS[status].lower()
            )
        )
    if is_self_optimization(campaign.connector):
        tags.append(HierarchyTag(kind="self_optimization", value="l4", label="L4"))
    if isinstance(purpose := campaign.declaration.purpose, Compare):
        arm = purpose.arm
        # The value names the head-to-head and the key, so a taken id is read off the listing.
        tags.append(
            HierarchyTag(
                kind="arm",
                value=f"{arm.head_to_head_id}:{arm.arm_key}",
                label=f"arm {arm.arm_key} of {arm.head_to_head_id}",
            )
        )
    return tags


@dataclass(frozen=True)
class CourseReads:
    """A campaign's courses as of one moment: each index at it and at its head, root first."""

    moment: Moment | None
    courses: list[tuple[CycleHop, CycleIndex, CycleIndex]]


def campaign_courses(stores: Stores, campaign: Campaign, *, moment: Moment | None) -> CourseReads:
    """Each course's index read once: a course minted after the moment is not among them."""
    return CourseReads(
        moment,
        sorted(
            (
                (hop, index, now)
                for hop, now in stores.campaigns.campaign_cycles(campaign.campaign_id)
                if (
                    index := now
                    if moment is None
                    else read_cycle_index(stores.campaigns.cycle_dir(hop), moment=moment)
                )
                is not None
            ),
            key=lambda entry: (entry[0].cycle_id != campaign.root_cycle_id, entry[1].created_at),
        ),
    )


def campaign_entry(
    stores: Stores,
    inside: CyclePath,
    campaign: Campaign,
    held: CourseReads,
    *,
    depth: int,
) -> HierarchyCampaign:
    moment = held.moment
    holder = stores.campaigns.line_holder(campaign.root_hop, moment=moment)
    runs_with = campaign_runs_with(stores, campaign)
    held_line = line_config(stores, campaign, holder)
    bench: BenchScore | None = None
    # The bench passes are read whole, so a replayed moment has no bench reading, as on the dashboard.
    if moment is None and held_line.config is not None:
        try:
            bench = cycle_bench(stores, campaign, holder, held_line.config)
        except ScoringFormulaError as exc:
            held_line = LineConfig(None, str(exc))
    courses: dict[str, HierarchyCourse] = {}
    for hop, index, now in held.courses:
        if depth == 0 and hop != holder:
            continue
        courses[hop.cycle_id] = course_entry(
            stores,
            inside,
            campaign,
            stores.campaigns.cycle_dir(hop),
            index,
            head=now,
            holder=holder,
            moment=moment,
            runs_with=runs_with,
            line=held_line if hop == holder else line_config(stores, campaign, hop),
            bench=bench if hop == holder else None,
        )
    line = courses.get(holder.cycle_id)
    state = (
        HierarchyState.of(
            derive_run_state(stores.campaigns.cycle_dir(holder)),
            None,
            lifecycle=campaign.lifecycle_status,
        )
        if line is None
        else line.state
    )
    spend_lifetime = LifetimeSpend.of(campaign_spend(stores.campaigns, campaign.campaign_id).total)
    return HierarchyCampaign(
        address=Subject(campaign.campaign_id, inside=inside).address,
        parent=Subject.of(inside).address if inside else None,
        label=campaign.label or campaign.dataset_name,
        tags=_campaign_tags(campaign, runs_with),
        # The campaign's row reads its line course's as its own, its bill the campaign's whole life.
        figures=[_state_row(state.status), _lifetime_row(spend_lifetime)]
        if line is None
        else [_lifetime_row(spend_lifetime) if row.key == "spend" else row for row in line.figures],
        lines=[
            caveat_row("unminted", _UNMINTED),
            *([] if runs_with is not None else [caveat_row("unresolved", _UNRESOLVED)]),
        ]
        if line is None
        else line.lines,
        state=state,
        span=HierarchySpan(
            opened_at=campaign.created_at,
            last_at=max(
                (index.updated_at for _, index, _ in held.courses), default=campaign.created_at
            ),
        ),
        children=list(courses.values()) if depth > 0 else [],
        facts=CampaignFacts(
            label=campaign.label or None,
            root=Subject(campaign.campaign_id, campaign.root_cycle_id, inside=inside).address,
            line=Subject(campaign.campaign_id, holder.cycle_id, inside=inside).address,
            lifecycle=campaign.lifecycle_status,
            spend_lifetime=spend_lifetime,
            runs_with=runs_with,
        ),
    )


def draft_starts(stores: Stores) -> dict[str, LaunchStage]:
    """This account's check-ins a Start holds, at its stage: the listing and the reopen serve them."""
    jobs = JobRegistry.attach(stores.jobs_dir)
    return jobs.draft_starts(user_id=str(stores.identity.user_id))


def unstarted_entry(
    stores: Stores, draft: DraftCampaign, *, start: LaunchStage | None
) -> UnstartedCampaign:
    """An open check-in as a campaign: its spend is what the check-in cost, its lifecycle word
    the stage of a Start that holds it."""
    ledger = stores.drafts.ledger(draft.draft_id)
    spend_lifetime = LifetimeSpend.of(billed_spend([ledger] if ledger.is_file() else []).total)
    dataset = draft.dataset_name
    lifecycle = "unstarted" if start is None else start.value
    return UnstartedCampaign(
        address=draft.draft_id,
        parent=None,
        label=draft.slug,
        tags=[
            HierarchyTag(kind="dataset", value=dataset, label=dataset),
            HierarchyTag(kind="lifecycle", value=lifecycle, label=lifecycle),
        ],
        figures=[_lifetime_row(spend_lifetime)],
        lines=[],
        span=HierarchySpan(opened_at=draft.created_at, last_at=draft.updated_at),
        facts=UnstartedFacts(spend_lifetime=spend_lifetime),
    )


def entries_version(held: Iterable[Stores]) -> tuple[object, ...]:
    """Stats only: every input an entry reads in *held* — run state, the archive rows its bench and
    lens grade, the ruler, each dataset's scorer — so a client holding it is answered unread."""
    stores = {s.base_dir: s for s in held}.values()
    archives = {s.archive.base_dir: s.archive for s in stores}.values()
    rulers = sorted({s.ruler.path for s in stores})
    definitions = sorted(
        {
            (s.base_dir, c.dataset_name): s
            for s in stores
            for c in s.campaigns.list_campaigns(lifecycle="all")
        }.items(),
        key=lambda item: (str(item[0][0]), item[0][1]),
    )
    return (
        *{str(s.identity.user_id) for s in stores},
        *(s.campaigns.version() for s in stores),
        *(s.drafts.version() for s in stores),
        # A Start's stage moves no draft file: its own fact, read only where a draft could be held.
        *(sorted(draft_starts(s).items()) for s in stores if s.drafts.draft_ids()),
        *(archive.version() for archive in archives),
        *(file_sig(path) for path in rulers),
        *(definition_version(s, name) for (_, name), s in definitions),
    )


def hierarchy_version(stores: Stores, *, inside: CyclePath) -> tuple[object, ...]:
    return entries_version([descend_store(stores, inside)])


def list_hierarchy(
    stores: Stores,
    *,
    inside: CyclePath,
    dataset: str | None,
    lifecycle: ListingLifecycle,
    campaign_id: str | None,
    attached_only: bool,
    depth: int,
) -> HierarchyResponse:
    if attached_only and depth == 0:
        raise BadRequestError("`attached` keeps the courses a producer holds; depth 0 serves none.")
    leaf = descend_store(stores, inside)
    # No manifest holds an open check-in, so the store is asked only after a stored lifecycle.
    stored: LifecycleFilter | None = None if lifecycle == "unstarted" else lifecycle
    built = [
        (
            campaign.root_cycle_id,
            campaign_entry(
                leaf,
                inside,
                campaign,
                campaign_courses(leaf, campaign, moment=None),
                depth=depth,
            ),
        )
        for campaign in (
            []
            if stored is None
            else leaf.campaigns.list_campaigns(
                dataset, lifecycle=stored, owner_user_id=str(leaf.identity.user_id)
            )
        )
        if campaign_id in (None, campaign.campaign_id)
    ]
    rooted: dict[str, list[HierarchyCampaign]] = {}
    for root, entry in sorted(built, key=lambda pair: pair[1].span.last_at, reverse=True):
        if attached_only:
            attached = [c for c in entry.children if c.state.attached]
            if not attached:
                continue
            entry = entry.model_copy(update={"children": attached})
        rooted.setdefault(root, []).append(entry)
    unstarted: list[UnstartedCampaign] = []
    if campaign_id is None and not attached_only and lifecycle in ("unstarted", "all"):
        drafts = sorted(owned_drafts(leaf), key=lambda d: d.updated_at, reverse=True)
        starts = draft_starts(leaf) if drafts else {}
        unstarted = [
            unstarted_entry(leaf, draft, start=starts.get(draft.draft_id))
            for draft in drafts
            if dataset in (None, draft.dataset_name)
        ]
    return HierarchyResponse(
        entries=[*unstarted, *(entry for group in rooted.values() for entry in group)]
    )
