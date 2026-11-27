from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from promptpotter.application.datasets.draft_campaign import owned_drafts
from promptpotter.application.jobs.launcher.admission import admitted
from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.application.pipeline_resolve import (
    LineConfig,
    RootRunsWith,
    campaign_runs_with,
    line_config,
    resolved,
)
from promptpotter.application.runner.bench import cycle_bench, cycle_curve, cycle_verify
from promptpotter.application.runner.inner.tasks import is_self_optimization
from promptpotter.application.served_dashboard import binding_run_limits, run_said
from promptpotter.domain.arm_reading import ArmReading, fitness_terms_text
from promptpotter.domain.bench import BenchCurve, BenchScore
from promptpotter.domain.campaign import (
    LIFECYCLE_STATUS_LABELS,
    Campaign,
    CampaignCycle,
    CampaignRunsWith,
    Compare,
    LifecycleFilter,
    ceiling_meter,
)
from promptpotter.domain.checkin_draft import DraftFile
from promptpotter.domain.command_kinds import filable
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
    CycleEntryFacts,
    CycleMint,
    CycleSpawn,
    Figure,
    FigureKey,
    HierarchyCampaign,
    HierarchyCycle,
    HierarchyResponse,
    HierarchySpan,
    HierarchyState,
    HierarchyTag,
    ListingLifecycle,
    UnstartedCampaign,
)
from promptpotter.domain.paired_reading import (
    READING_STATE_INFO,
    PairedReading,
    ReadingState,
    UnreadPair,
    level_text,
    lift_versus,
)
from promptpotter.domain.phases import RUN_PHASE_INFO, LaunchStage
from promptpotter.domain.ruler import THETA_CAVEAT_INFO, withheld
from promptpotter.domain.said import Said
from promptpotter.domain.scoring import ScoringFormulaError
from promptpotter.domain.spend import (
    LifetimeSpend,
    MeteredSpend,
    UserSpend,
    calls_rate_priced,
    rate_priced_text,
    usd_text,
)
from promptpotter.infrastructure.run_state import producer_stamps
from promptpotter.infrastructure.store.account_spend import (
    billed_spend,
    campaign_spend,
    unreported_under,
)
from promptpotter.infrastructure.store.campaign_store.ledger_scan import scan_ledger_spend
from promptpotter.infrastructure.store.dataset_access import definition_version
from promptpotter.infrastructure.store.io import stamp_of
from promptpotter.infrastructure.store.read_model import Moment
from promptpotter.infrastructure.store.stores import Stores, descend_store
from promptpotter.shared.errors import BadRequestError

_MINT_WORDS: dict[CycleMint, str] = {
    "root": "root",
    "fork": "fork",
    "inner": "inner run",
}
_UNMINTED = "Its live cycle has no mint record, so its rounds and standing are unread."
_UNSCORED = "Its config did not resolve, so its limits and bench are unread."


def _cycle_label(mint: CycleMint, cycle_id: str) -> str:
    return f"{_MINT_WORDS[mint]} ·{cycle_id.rpartition('_')[2][:6]}"


def campaign_short_label(campaign: Campaign) -> str:
    """Two runs of one dataset never share it: the id's suffix is what was minted to part them."""
    return campaign.label or f"{campaign.dataset_name} ·{campaign.campaign_id.rpartition('__')[2]}"


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


def _spend_row(spent: MeteredSpend) -> Figure:
    return _figure("spend", "Spend", spent.bill, spent.rate_priced_line)


def _lifetime_row(spent: UserSpend) -> Figure:
    return _figure(
        "spend_lifetime",
        "Spent",
        LifetimeSpend.of(spent).bill,
        f"up to {usd_text(spent.unreported_usd)} more unreported"
        if spent.sends_unreported
        else rate_priced_text(
            spent.rate_priced_usd, priced=calls_rate_priced(spent.rate_priced_usd)
        ),
    )


def _lift_row(key: FigureKey, pair: PairedReading, over: str) -> Figure:
    label = f"Lift {lift_versus(over, pair.cell_set)}"
    if pair.state is not ReadingState.READ:
        info = READING_STATE_INFO[pair.state]
        return _figure(key, label, info.label, info.sentence)
    lift = pair.headline
    unit = lift.measurand.unit
    return _figure(
        key,
        label,
        lift.estimate.text,
        f"{1 - pair.spec.alpha:.0%} band · "
        f"{over} {level_text(lift.rate_a, unit)} → {level_text(lift.rate_b, unit)}",
    )


def _cycle_climb(facts: CycleEntryFacts) -> Said | None:
    climb = None if facts.standing is None else facts.standing.climb
    if climb is None:
        return None
    theta = ["rounds are won on θ"] if facts.elects_on == "ability" else []
    return Said(
        key="climb",
        tone="quiet",
        label=climb.line,
        detail=" · ".join([f"lift in {climb.measurand}", *theta]),
    )


def _bench_row(state: ReadingState, bench: BenchScore | None) -> Figure:
    selected = None if bench is None else bench.selected
    level = None if selected is None else selected.level
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
        READING_STATE_INFO[state].label if selected is None or level is None else level.point,
        " · ".join(notes),
    )


def _cycle_figures(
    status: RunStatus,
    facts: CycleEntryFacts,
    cap: int | None,
    runs_with: CampaignRunsWith | None,
    *,
    on_live_cycle: bool,
) -> list[Figure]:
    bench = facts.bench
    rows = [_state_row(status)]
    if runs_with is not None:
        models = [model for vendor in runs_with.vendors for model in vendor.models]
        rows.append(_figure("optimizer", "Optimizer", runs_with.optimizer, " · ".join(models)))
    rows.append(_spend_row(facts.spend))
    if not on_live_cycle:
        rows.append(_bench_row(ReadingState.HELD_ELSEWHERE, None))
    elif bench is not None:
        rows.append(_bench_row(bench.status.state, bench))
    rows.append(_rounds_row(facts.rounds_closed, cap))
    if (standing := facts.standing) is None:
        unread = UnreadPair.of(ReadingState.PENDING)
        return [*rows, _lift_row("lift", unread, "origin")]
    rows.append(_figure("selection", "Leader", standing.selection_name))
    rows.append(_lift_row("lift", standing.vs_origin, "origin"))
    if (lives := standing.lives) is not None:
        rows.append(_figure("lives", "Lives", lives.bar, lives.label))
    return rows


def _unresolved_lines(root: RootRunsWith) -> list[Said]:
    """A root's refusal is served as it was raised: which data broke is what the reader acts on."""
    return [] if root.unread is None else [caveat_row("unresolved", root.unread)]


def _cycle_said_lines(root: RootRunsWith, line: LineConfig) -> list[Said]:
    if root.unread is not None:
        return _unresolved_lines(root)
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
    samples, ability, own = reading.samples, reading.ability, reading.own
    rows = [
        _figure(
            "fitness",
            "Fitness",
            "—" if own is None or own.fitness is None else own.fitness.point,
            None
            if samples.scored is None
            else f"{samples.scored}{'' if samples.expected is None else f' of {samples.expected}'} scored",
        )
    ]
    if terms := fitness_terms_text(reading.fitness_terms):
        rows.append(_figure("fitness_terms", "Formula terms", terms))
    if ability is not None and (ability.theta is not None or withheld(ability.caveat)):
        rows.append(
            _figure(
                "ability",
                "Ability θ",
                "—" if ability.theta is None else ability.theta.text,
                "not ability — see below" if withheld(ability.caveat) else None,
            )
        )
    if reading.vs_reference is not None:
        rows.append(_lift_row("lift", reading.vs_reference, "parent"))
    return rows


def superseded_tag(successor: str, mint: CycleMint) -> HierarchyTag:
    """Worn by a retired cycle or candidate: the cycle that took its place, named as its card."""
    label = f"retired by {_cycle_label(mint, Subject.parse_address(successor).cycle_id)}"
    return HierarchyTag(kind="superseded", value=successor, label=label)


def cycle_entry(
    stores: Stores,
    inside: CyclePath,
    campaign: Campaign,
    cycle_dir: Path,
    index: CycleIndex,
    *,
    head: CycleIndex,
    holder: CycleHop,
    moment: Moment | None,
    root: RootRunsWith,
    line: LineConfig,
    bench: BenchScore | None,
    curve: BenchCurve | None,
) -> HierarchyCycle:
    at = CampaignCycle(campaign, cycle_dir.name)
    hop = at.hop
    runs_with = root.runs_with
    # The producer as it is now: a moment replays the record, never a process.
    run = stores.run_state(hop)
    limits = binding_run_limits(stores, at, line.config, run)
    # Before its first launch a cycle declares no wiring, so its campaign's declared cap binds.
    cap = (
        (None if runs_with is None else runs_with.budget_rounds)
        if limits is None
        else limits.budget.rounds
    )
    spent = MeteredSpend.of(
        scan_ledger_spend(stores.campaigns.chain(hop, moment=moment)).spend,
        ceiling_meter(campaign.declaration.purpose),
    )
    mint: CycleMint = index.minted_as
    # Inner by where it lives: a rebase pair in the sandbox has no `spawned_by`.
    if mint == "root" and (index.spawned_by is not None or stores.inner):
        mint = "inner"
    tags = [
        HierarchyTag(kind="dataset", value=campaign.dataset_name, label=campaign.dataset_name),
        HierarchyTag(kind="mint", value=mint, label=_MINT_WORDS[mint]),
    ]
    if (successor := index.superseded_by) is not None:
        # Only a fork's take files a supersede: its successor is a fork.
        by = Subject(hop.campaign_id, successor, inside=inside).address
        tags.append(superseded_tag(by, "fork"))
    fork, parent_id, cut = index.fork, index.parent_cycle_id, index.cut_round
    spawn = (
        None
        if fork is None or parent_id is None or cut is None
        else CycleSpawn(
            parent=Subject(hop.campaign_id, parent_id, inside=inside).address,
            cut_round=cut,
            trigger=fork.trigger,
            direction=fork.direction,
            issued_by=fork.issued_by.display,
        )
    )
    task = None if index.spawned_by is None else index.spawned_by.task or None
    # Run state is the head's under any moment: a replayed stop beside a live phase is no state.
    state = HierarchyState.of(
        run, head, lifecycle=campaign.lifecycle_status, admission=admitted(stores, campaign, run)
    )
    facts = CycleEntryFacts(
        mint=mint,
        spawn=spawn,
        task=task,
        standing=index.standing,
        rounds_closed=index.rounds_closed,
        elects_on=index.elects_on,
        spend=spent,
        bench=bench,
        curve=curve,
        verify=None if moment is not None else cycle_verify(stores, at),
    )
    return HierarchyCycle(
        address=Subject(hop.campaign_id, hop.cycle_id, inside=inside).address,
        parent=Subject(hop.campaign_id, inside=inside).address,
        # An inner cycle is named for the outer panel cell it measures, by its last segment.
        label=_cycle_label(mint, hop.cycle_id) if task is None else task.rpartition("/")[2],
        tags=tags,
        figures=_cycle_figures(state.status, facts, cap, runs_with, on_live_cycle=hop == holder),
        climb=_cycle_climb(facts),
        lines=_cycle_said_lines(root, line),
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
    if not campaign.active:
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
class CycleReads:
    """A campaign's cycles as of one moment: each index at it and at its head, root first."""

    moment: Moment | None
    cycles: list[tuple[CycleHop, CycleIndex, CycleIndex]]


def campaign_cycle_reads(
    stores: Stores, campaign: Campaign, *, moment: Moment | None
) -> CycleReads:
    """Each cycle's index read once: a cycle minted after the moment is not among them."""
    return CycleReads(
        moment,
        sorted(
            (
                (hop, index, now)
                for hop, now in stores.campaigns.campaign_cycles(campaign.campaign_id)
                if (index := now if moment is None else stores.campaigns.load(hop, moment=moment))
                is not None
            ),
            key=lambda entry: (entry[0].cycle_id != campaign.root_cycle_id, entry[1].created_at),
        ),
    )


def campaign_entry(
    stores: Stores,
    inside: CyclePath,
    campaign: Campaign,
    held: CycleReads,
    *,
    depth: int,
) -> HierarchyCampaign:
    moment = held.moment
    holder = stores.campaigns.live_cycle(campaign.root_hop, moment=moment)
    live_at = CampaignCycle(campaign, holder.cycle_id)
    root = campaign_runs_with(stores, campaign)
    runs_with = root.runs_with
    held_line = line_config(stores, live_at)
    bench: BenchScore | None = None
    curve: BenchCurve | None = None
    # The bench passes are read whole, so a replayed moment has no bench reading, as on the dashboard.
    if moment is None and held_line.config is not None:
        try:
            under = resolved(stores, live_at)
            bench = cycle_bench(stores, live_at, under)
            curve = cycle_curve(stores, live_at, under)
        except ScoringFormulaError as exc:
            held_line = LineConfig(None, str(exc))
    cycles: dict[str, HierarchyCycle] = {}
    for hop, index, now in held.cycles:
        if depth == 0 and hop != holder:
            continue
        cycles[hop.cycle_id] = cycle_entry(
            stores,
            inside,
            campaign,
            stores.campaigns.cycle_dir(hop),
            index,
            head=now,
            holder=holder,
            moment=moment,
            root=root,
            line=held_line
            if hop == holder
            else line_config(stores, CampaignCycle(campaign, hop.cycle_id)),
            bench=bench if hop == holder else None,
            curve=curve if hop == holder else None,
        )
    live = cycles.get(holder.cycle_id)
    if live is None:
        held_run = stores.run_state(holder)
        state = HierarchyState.of(
            held_run,
            None,
            lifecycle=campaign.lifecycle_status,
            admission=admitted(stores, campaign, held_run),
        )
    else:
        state = live.state
    spent = campaign_spend(stores.campaigns, stores.charges, campaign.campaign_id)
    lifetime = spent.total
    return HierarchyCampaign(
        address=Subject(campaign.campaign_id, inside=inside).address,
        parent=Subject.of(inside).address if inside else None,
        label=campaign.label or campaign.dataset_name,
        tags=_campaign_tags(campaign, runs_with),
        # The campaign's row reads its live cycle's as its own, its bill the campaign's whole life.
        figures=[_state_row(state.status), _lifetime_row(lifetime)]
        if live is None
        else [_lifetime_row(lifetime) if row.key == "spend" else row for row in live.figures],
        lines=[caveat_row("unminted", _UNMINTED), *_unresolved_lines(root)]
        if live is None
        else live.lines,
        state=state,
        span=HierarchySpan(
            opened_at=campaign.created_at,
            last_at=max(
                (index.updated_at for _, index, _ in held.cycles), default=campaign.created_at
            ),
        ),
        children=list(cycles.values()) if depth > 0 else [],
        facts=CampaignFacts(
            label=campaign.label or None,
            short_label=campaign_short_label(campaign),
            root=Subject(campaign.campaign_id, campaign.root_cycle_id, inside=inside).address,
            root_label=_cycle_label("root", campaign.root_cycle_id),
            live_cycle=Subject(campaign.campaign_id, holder.cycle_id, inside=inside).address,
            lifecycle=campaign.lifecycle_status,
            runs_with=runs_with,
        ),
    )


def draft_starts(stores: Stores) -> dict[str, LaunchStage]:
    """This account's check-ins a Start holds, at its stage: the listing and the reopen serve them."""
    jobs = JobRegistry.attach(stores.jobs_dir)
    return jobs.draft_starts(user_id=str(stores.identity.user_id))


def unstarted_entry(
    stores: Stores, draft: DraftFile, *, start: LaunchStage | None
) -> UnstartedCampaign:
    """An open check-in as a campaign: its spend is what the check-in cost."""
    ledger = stores.drafts.ledger(draft.draft_id)
    billed = billed_spend([ledger] if ledger.is_file() else [])
    spent = billed.plus(unreported_under(stores.charges, ledger)).total
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
        figures=[_lifetime_row(spent)],
        lines=[],
        span=HierarchySpan(opened_at=draft.created_at, last_at=draft.updated_at),
    )


def store_version(stores: Stores) -> tuple[object, ...]:
    """The `store:` subject's version: the stamps its writers left (its campaigns and ledgers, and
    the account's ruler, datasets, charges and archive it reads), and each producer it holds, whose
    liveness and beat no write marks, and each definition its campaigns name, which no writer
    stamps (`dataset_access.py::definition_version`)."""
    jobs = JobRegistry.attach(stores.jobs_dir)
    return (
        *stores.stamps(),
        *(definition_version(stores, name) for name in stores.campaigns.dataset_names()),
        *(
            (job.job_id, *producer_stamps(stores.campaigns.cycle_dir(job.hop), stores.jobs_dir))
            for job in jobs.list_holding_spend()
            if job.workspace == str(stores.base_dir)
        ),
    )


def account_version(stores: Stores) -> tuple[object, ...]:
    """The `account` subject's: what the account filed (its command log and workspace state, a
    grant) and its drafts. A run's appends move neither."""
    return (stamp_of(stores.account_state_dir), stamp_of(stores.account_drafts_dir))


def entries_version(held: Iterable[Stores]) -> tuple[object, ...]:
    """Every subject an entry in *held* reads, at its version, and its reader's kinds (the acts it
    is offered), so a client holding it is answered unread."""
    stores = {s.base_dir: s for s in held}.values()
    return (
        *{str(s.identity.user_id) for s in stores},
        # A grant or a token's scope moves no store, yet every offer the body serves.
        *{filable(s.identity) for s in stores},
        *(store_version(s) for s in stores),
        *{account_version(s) for s in stores},
        # A Start's stage moves no draft file: its own fact, read only where a draft could be held.
        *(sorted(draft_starts(s).items()) for s in stores if s.drafts.draft_ids()),
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
        raise BadRequestError("`attached` keeps the cycles a producer holds; depth 0 serves none.")
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
                campaign_cycle_reads(leaf, campaign, moment=None),
                depth=depth,
            ),
        )
        for campaign in (
            [] if stored is None else leaf.campaigns.list_campaigns(dataset, lifecycle=stored)
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
    started = [entry for group in rooted.values() for entry in group]
    attached = [c for entry in started for c in entry.children if c.state.attached]
    attached.sort(key=lambda c: RUN_PHASE_INFO[c.state.status.run_phase].dock_priority)
    return HierarchyResponse(
        entries=[*unstarted, *started], docked=[cycle.address for cycle in attached]
    )
