from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from promptpotter.application.datasets.draft_campaign import DraftCampaign, owned_drafts
from promptpotter.application.jobs.registry import JobRegistry
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
from promptpotter.domain.campaign_config import CampaignConfig
from promptpotter.domain.cycle_index import CycleIndex
from promptpotter.domain.cycle_paths import (
    CycleHop,
    CyclePath,
    decode_cycle_path,
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
    HierarchyState,
    HierarchyTag,
    ListingLifecycle,
    UnstartedCampaign,
    UnstartedFacts,
)
from promptpotter.domain.spend import LifetimeSpend, MeteredSpend
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


def _course_label(mint: CourseKind, cycle_id: str) -> str:
    return f"{_MINT_WORDS[mint]} ·{cycle_id.rpartition('_')[2][:6]}"


def superseded_tag(successor: str) -> HierarchyTag:
    """Worn by a retired course or candidate: the course that took its place, named as its card."""
    cycle_id = decode_cycle_path(successor)[-1].cycle_id
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
    moment: Moment | None,
    runs_with: CampaignRunsWith | None,
    config: CampaignConfig | None,
    bench: BenchScore | None,
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
        tags.append(superseded_tag(hierarchy_address(inside, hop.campaign_id, successor)))
    fork, parent_id, cut = index.fork, index.parent_cycle_id, index.cut_round
    spawn = (
        None
        if fork is None or parent_id is None or cut is None
        else CourseSpawn(
            parent=hierarchy_address(inside, hop.campaign_id, parent_id),
            cut_round=cut,
            trigger=fork.trigger,
            direction=fork.resolved_direction,
            issued_by=fork.issued_by.display,
        )
    )
    task = None if index.spawned_by is None else index.spawned_by.task or None
    return HierarchyCourse(
        address=hierarchy_address(inside, hop.campaign_id, hop.cycle_id),
        parent=hierarchy_address(inside, hop.campaign_id),
        # An inner course is named for the outer panel cell it measures, by its last segment.
        label=_course_label(mint, hop.cycle_id) if task is None else task.rpartition("/")[2],
        caveat=None,
        tags=tags,
        # Run state is the head's under any moment: a replayed stop beside a live phase is no state.
        state=HierarchyState.of(run, head, lifecycle=campaign.lifecycle_status),
        span=HierarchySpan(
            opened_at=index.created_at,
            last_at=index.updated_at,
        ),
        facts=CourseFacts(
            mint=mint,
            spawn=spawn,
            task=task,
            standing=index.standing,
            rounds_closed=index.rounds_closed,
            rounds_cap=cap,
            elects_on=index.elects_on,
            spend=spent,
            bench=bench,
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
        # The value is `new --arm`'s own address, so a taken head-to-head id is read off the listing.
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
    config = line_config(stores, campaign, holder)
    # The bench passes are read whole, so a replayed moment has no bench reading, as on the dashboard.
    bench = (
        cycle_bench(stores, campaign, holder, config)
        if moment is None and config is not None
        else None
    )
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
            moment=moment,
            runs_with=runs_with,
            config=config if hop == holder else line_config(stores, campaign, hop),
            bench=bench if hop == holder else None,
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
    spend_lifetime = LifetimeSpend.of(campaign_spend(stores.campaigns, campaign.campaign_id).total)
    return HierarchyCampaign(
        address=hierarchy_address(inside, campaign.campaign_id),
        parent=encode_cycle_path(inside) if inside else None,
        label=campaign.label or campaign.dataset_name,
        caveat=" ".join(caveats) or None,
        tags=_campaign_tags(campaign, runs_with),
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
            root=hierarchy_address(inside, campaign.campaign_id, campaign.root_cycle_id),
            line=hierarchy_address(inside, campaign.campaign_id, holder.cycle_id),
            lifecycle=campaign.lifecycle_status,
            spend_lifetime=spend_lifetime,
            runs_with=runs_with,
            line_course=None if line is None else line.facts,
        ),
    )


def queued_drafts(stores: Stores) -> frozenset[str]:
    """This account's check-ins a queued Start waits on: the listing and the reopen serve them."""
    jobs = JobRegistry.attach(stores.jobs_dir)
    return jobs.queued_drafts(user_id=str(stores.identity.user_id))


def unstarted_entry(stores: Stores, draft: DraftCampaign, *, queued: bool) -> UnstartedCampaign:
    """An open check-in as a campaign: its spend is what the check-in cost, its lifecycle word
    `queued` while a Start waits on a slot for it."""
    ledger = stores.drafts.ledger(draft.draft_id)
    spend_lifetime = LifetimeSpend.of(billed_spend([ledger] if ledger.is_file() else []).total)
    dataset = draft.dataset_name
    lifecycle = "queued" if queued else "unstarted"
    return UnstartedCampaign(
        address=draft.draft_id,
        parent=None,
        label=draft.slug,
        caveat=None,
        tags=[
            HierarchyTag(kind="dataset", value=dataset, label=dataset),
            HierarchyTag(kind="lifecycle", value=lifecycle, label=lifecycle),
        ],
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
        # A queue entry moves no draft file: its own fact, read only where a draft could wait on it.
        *(sorted(queued_drafts(s)) for s in stores if s.drafts.draft_ids()),
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
        queued = queued_drafts(leaf) if drafts else frozenset()
        unstarted = [
            unstarted_entry(leaf, draft, queued=draft.draft_id in queued)
            for draft in drafts
            if dataset in (None, draft.dataset_name)
        ]
    return HierarchyResponse(
        entries=[*unstarted, *(entry for group in rooted.values() for entry in group)]
    )
