from __future__ import annotations

from pathlib import Path

from promptpotter.application.campaign_config import CampaignConfig
from promptpotter.application.pipeline_resolve import campaign_runs_with, line_config
from promptpotter.application.runner.campaign_result import cycle_bench
from promptpotter.application.runner.inner.tasks import is_self_optimization
from promptpotter.application.served_dashboard import binding_run_limits
from promptpotter.domain.bench import BenchScore
from promptpotter.domain.campaign import (
    LIFECYCLE_STATUS_LABELS,
    Campaign,
    CampaignRunsWith,
    Compare,
    LifecycleFilter,
    ceiling_meter,
)
from promptpotter.domain.cycle_listing import CycleIndex
from promptpotter.domain.cycle_paths import (
    CycleHop,
    CyclePath,
    encode_cycle_path,
    hierarchy_address,
)
from promptpotter.domain.hierarchy import (
    CampaignFacts,
    CourseFacts,
    CourseKind,
    CourseSpawn,
    HierarchyCampaign,
    HierarchyCourse,
    HierarchyResponse,
    HierarchySpan,
    HierarchyStat,
    HierarchyState,
    HierarchyTag,
    StatKey,
)
from promptpotter.domain.paired_reading import READING_STATE_INFO, MeasurandUnit
from promptpotter.domain.phases import RunPhase
from promptpotter.domain.results import RunStanding
from promptpotter.domain.spend import RATE_PRICED_LABEL, LifetimeSpend, MeteredSpend
from promptpotter.infrastructure.projections.cycle_index import read_cycle_index
from promptpotter.infrastructure.runtime_flags import derive_run_state
from promptpotter.infrastructure.store.account_spend import campaign_spend
from promptpotter.infrastructure.store.campaign_store.ledger_scan import scan_ledger_spend
from promptpotter.infrastructure.store.dataset_access import definition_version
from promptpotter.infrastructure.store.layout import sibling_kind
from promptpotter.infrastructure.store.lineage_queries import iter_family_courses
from promptpotter.infrastructure.store.read_model import LedgerSpan, Moment, file_sig
from promptpotter.infrastructure.store.stores import Stores, descend_store
from promptpotter.shared.errors import BadRequestError

_MINT_WORDS: dict[CourseKind, str] = {
    "root": "root",
    "fork": "fork",
    "diag": "diag",
    "inner": "inner run",
}
_THETA_CLAUSE = "rounds are won on θ"
_UNRESOLVED = "Its root pipeline did not resolve, so its settings and bench are unread."
_UNMINTED = "Its line course has no mint record, so its rounds and standing are unread."


def _course_label(mint: CourseKind, cycle_id: str) -> str:
    return f"{_MINT_WORDS[mint]} ·{cycle_id.rpartition('_')[2][:6]}"


def _rate_priced(spent: LifetimeSpend | MeteredSpend) -> str | None:
    if not spent.calls_rate_priced:
        return None
    return f"+ ${spent.rate_priced_usd:.2f} {RATE_PRICED_LABEL}"


def _spend_stat(
    key: StatKey, label: str, spent: LifetimeSpend | MeteredSpend, sub: str | None
) -> HierarchyStat:
    return HierarchyStat(
        key=key,
        label=label,
        value=f"{'≥ ' if spent.bill_is_floor else ''}${spent.billed_usd:.2f}",
        sub=sub,
        amount=spent.billed_usd,
    )


def _lifetime_stat(spent: LifetimeSpend) -> HierarchyStat:
    unreported = f"up to ${spent.unreported_usd:.2f} more unreported"
    sub = unreported if spent.sends_unreported else _rate_priced(spent)
    return _spend_stat("spend_lifetime", "Spent", spent, sub)


def _rounds_stat(closed: int, cap: int | None) -> HierarchyStat:
    if cap == 0:
        value = "origin only"
    elif cap is None:
        value = f"{closed} · no rounds axis"
    else:
        value = f"{closed} of {cap}"
    return HierarchyStat(key="rounds", label="Rounds", value=value, sub=None, amount=closed)


def _accuracy_stat(standing: RunStanding, *, on_theta: bool) -> HierarchyStat:
    theta = [_THETA_CLAUSE] if on_theta else []
    lift = standing.vs_origin.headline
    if lift is None:
        return HierarchyStat(
            key="accuracy",
            label="Lift vs origin",
            value=READING_STATE_INFO[standing.vs_origin.state].label,
            sub=" · ".join(theta) or None,
            amount=None,
        )
    est = lift.estimate
    if lift.measurand.unit is MeasurandUnit.RATE:
        value = f"{est.value * 100:+.1f} pp [{est.ci_lo * 100:.1f}, {est.ci_hi * 100:.1f}]"
        levels = f"{lift.rate_a:.1%} → {lift.rate_b:.1%}"
    else:
        value = f"{est.value:+.3f} [{est.ci_lo:.3f}, {est.ci_hi:.3f}]"
        levels = f"{lift.rate_a:.3f} → {lift.rate_b:.3f}"
    return HierarchyStat(
        key="accuracy",
        label="Lift vs origin",
        value=value,
        sub=" · ".join([f"origin {levels}", *theta]),
        amount=est.value,
    )


def _line_stats(index: CycleIndex, cap: int | None, *, on_theta: bool) -> list[HierarchyStat]:
    rounds = _rounds_stat(index.rounds_closed, cap)
    standing = index.standing
    if standing is None:
        return [rounds]
    lives = (
        []
        if standing.lives is None
        else [
            HierarchyStat(
                key="lives",
                label="Lives",
                value=standing.lives.bar,
                sub=standing.lives.label,
                amount=standing.stalls_left,
            )
        ]
    )
    return [rounds, _accuracy_stat(standing, on_theta=on_theta), *lives]


def _bench_stat(bench: BenchScore) -> HierarchyStat:
    level = None if bench.selected is None else bench.selected.level
    return HierarchyStat(
        key="bench",
        label="Bench",
        value=READING_STATE_INFO[bench.status.state].label
        if level is None
        else f"{level.value:.3f}",
        sub=bench.line,
        amount=None if level is None else level.value,
    )


def course_entry(
    stores: Stores,
    inside: CyclePath,
    campaign: Campaign,
    cycle_dir: Path,
    index: CycleIndex,
    *,
    moment: Moment | None,
    runs_with: CampaignRunsWith | None,
    config: CampaignConfig | None,
) -> HierarchyCourse:
    hop = CycleHop(campaign_id=campaign.campaign_id, cycle_id=cycle_dir.name)
    # The producer as it is now: a moment replays the record, never a process.
    run = derive_run_state(cycle_dir)
    limits = binding_run_limits(stores, hop, config, run)
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
        tags.append(
            HierarchyTag(
                kind="superseded",
                value=hierarchy_address(inside, hop.campaign_id, successor),
                label=f"retired by {_course_label(sibling_kind(successor), successor)}",
            )
        )
    fork, parent_id, cut = index.fork, index.parent_cycle_id, index.forked_at_offset
    spawn = (
        None
        if fork is None or parent_id is None or cut is None
        else CourseSpawn(
            parent=hierarchy_address(inside, hop.campaign_id, parent_id),
            at_round=fork.from_round,
            at_offset=cut,
            from_candidate=fork.from_candidate_id,
            trigger=fork.trigger,
            direction=fork.resolved_direction,
            reason=fork.reason,
            issued_by=fork.issued_by.display,
        )
    )
    on_theta = index.elects_on == "ability"
    return HierarchyCourse(
        address=hierarchy_address(inside, hop.campaign_id, hop.cycle_id),
        parent=hierarchy_address(inside, hop.campaign_id),
        label=_course_label(mint, hop.cycle_id),
        lede=None if index.standing is None else index.standing.selection_line,
        caveat=None,
        tags=tags,
        state=HierarchyState.of(run, index, lifecycle=campaign.lifecycle_status),
        stats=[
            _spend_stat("spend", "Spend", spent, _rate_priced(spent)),
            *_line_stats(index, cap, on_theta=on_theta),
        ],
        span=HierarchySpan(
            opened_at=index.created_at,
            last_at=index.updated_at,
            closed_at=index.finished_at if run.run_phase is RunPhase.TERMINAL else None,
        ),
        child_count=len(index.rounds) + len(index.open_rounds),
        children=[],
        facts=CourseFacts(
            mint=mint,
            spawn=spawn,
            task=None if index.spawned_by is None else index.spawned_by.task or None,
            standing=index.standing,
            rounds_closed=index.rounds_closed,
        ),
    )


def family_course_entries(
    stores: Stores, path: CyclePath, *, moment: Moment | None
) -> dict[str, HierarchyCourse]:
    """Every course the tree at *path* folds, by address: the entries the listing serves for each."""
    lines: dict[tuple[Path, str], tuple[Campaign, CampaignRunsWith | None]] = {}
    out: dict[str, HierarchyCourse] = {}
    for family in iter_family_courses(stores, path):
        store, hop = family.store, family.path[-1]
        if (key := (store.base_dir, hop.campaign_id)) not in lines:
            campaign = store.campaigns.load_campaign(hop.campaign_id)
            if campaign is None:
                continue
            lines[key] = (campaign, campaign_runs_with(store, campaign))
        campaign, runs_with = lines[key]
        cycle_dir = store.campaigns.cycle_dir(hop)
        if (index := read_cycle_index(cycle_dir, moment=moment)) is None:
            continue
        out[encode_cycle_path(family.path)] = course_entry(
            store,
            family.path[:-1],
            campaign,
            cycle_dir,
            index,
            moment=moment,
            runs_with=runs_with,
            config=line_config(store, campaign, hop),
        )
    return out


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
        key = purpose.arm.arm_key
        tags.append(HierarchyTag(kind="arm", value=key, label=f"arm {key}"))
    return tags


def campaign_entry(
    stores: Stores, inside: CyclePath, campaign: Campaign, *, depth: int
) -> HierarchyCampaign:
    held = sorted(
        (
            (cycle_dir, index)
            for cycle_dir in stores.campaigns.campaign_cycle_dirs(campaign.campaign_id)
            if (index := read_cycle_index(cycle_dir, moment=None)) is not None
        ),
        key=lambda entry: (entry[0].name != campaign.root_cycle_id, entry[1].created_at),
    )
    holder = stores.campaigns.line_holder(campaign.root_hop)
    runs_with = campaign_runs_with(stores, campaign)
    config = line_config(stores, campaign, holder)
    courses: dict[str, HierarchyCourse] = {}
    for cycle_dir, index in held:
        hop = CycleHop(campaign_id=campaign.campaign_id, cycle_id=cycle_dir.name)
        if depth == 0 and hop != holder:
            continue
        courses[hop.cycle_id] = course_entry(
            stores,
            inside,
            campaign,
            cycle_dir,
            index,
            moment=None,
            runs_with=runs_with,
            config=config if hop == holder else line_config(stores, campaign, hop),
        )
    line = courses.get(holder.cycle_id)
    caveats = [
        text
        for missing, text in ((line is None, _UNMINTED), (runs_with is None, _UNRESOLVED))
        if missing
    ]
    state = (
        HierarchyState.of(
            derive_run_state(stores.campaigns.cycle_dir(holder)),
            None,
            lifecycle=campaign.lifecycle_status,
        )
        if line is None
        else line.state
    )
    bench = None if config is None else cycle_bench(stores, campaign, holder, config)
    spend_lifetime = LifetimeSpend.of(campaign_spend(stores.campaigns, campaign.campaign_id))
    return HierarchyCampaign(
        address=hierarchy_address(inside, campaign.campaign_id),
        parent=encode_cycle_path(inside) if inside else None,
        label=campaign.label or campaign.dataset_name,
        lede=None if line is None else line.lede,
        caveat=" ".join(caveats) or None,
        tags=_campaign_tags(campaign, runs_with),
        state=state,
        # The line's own spend is that course's headline; the campaign's is every cycle's.
        stats=[
            _lifetime_stat(spend_lifetime),
            *([] if bench is None else [_bench_stat(bench)]),
            *([] if line is None else line.stats[1:]),
        ],
        span=HierarchySpan(
            opened_at=campaign.created_at,
            last_at=max((index.updated_at for _, index in held), default=campaign.created_at),
            closed_at=None if line is None else line.span.closed_at,
        ),
        child_count=len(held),
        children=list(courses.values()) if depth > 0 else [],
        facts=CampaignFacts(
            label=campaign.label or None,
            root=hierarchy_address(inside, campaign.campaign_id, campaign.root_cycle_id),
            line=hierarchy_address(inside, campaign.campaign_id, holder.cycle_id),
            lifecycle=campaign.lifecycle_status,
            spend_lifetime=spend_lifetime,
            runs_with=runs_with,
        ),
    )


def hierarchy_version(stores: Stores, *, inside: CyclePath) -> tuple[object, ...]:
    """Stats only: every input a listing reads, so a client holding it is answered unread."""
    leaf = descend_store(stores, inside)
    names = sorted({c.dataset_name for c in leaf.campaigns.list_campaigns(lifecycle="all")})
    return (
        str(leaf.identity.user_id),
        leaf.campaigns.version(),
        leaf.archive.version(),
        file_sig(leaf.ruler.path),
        *(definition_version(leaf, name) for name in names),
    )


def list_hierarchy(
    stores: Stores,
    *,
    inside: CyclePath,
    dataset: str | None,
    lifecycle: LifecycleFilter,
    campaign_id: str | None,
    attached_only: bool,
    depth: int,
) -> HierarchyResponse:
    if attached_only and depth == 0:
        raise BadRequestError("`attached` keeps the courses a producer holds; depth 0 serves none.")
    leaf = descend_store(stores, inside)
    built = [
        (campaign.root_cycle_id, campaign_entry(leaf, inside, campaign, depth=depth))
        for campaign in leaf.campaigns.list_campaigns(
            dataset, lifecycle=lifecycle, owner_user_id=str(leaf.identity.user_id)
        )
        if campaign_id in (None, campaign.campaign_id)
    ]
    rooted: dict[str, list[HierarchyCampaign]] = {}
    for root, entry in sorted(built, key=lambda pair: pair[1].span.last_at, reverse=True):
        if attached_only:
            attached = [c for c in entry.children if c.state.producer.attached]
            if not attached:
                continue
            entry = entry.model_copy(update={"children": attached})
        rooted.setdefault(root, []).append(entry)
    return HierarchyResponse(entries=[entry for group in rooted.values() for entry in group])
