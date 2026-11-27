"""A campaign's ceiling shared over its live cycles, and each run's hold on its account: what
admission, a fork's rebase and a set-limits move, beside the spend book that reads them, and the
cycle's standing budget a launch stands. Counts and rates are `quota.py`'s."""

from __future__ import annotations

from pathlib import Path

from promptpotter.application.jobs.quota import (
    QuotaExceededError,
    admitting_before,
    clamped_change,
    launch_contended,
)
from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.domain.campaign import Campaign, CampaignCycle, ceiling_meter
from promptpotter.domain.cycle_paths import CycleHop, WorkspaceDir
from promptpotter.domain.job import InnerRun, Job
from promptpotter.domain.records.run_records import (
    CampaignCeilingRecord,
    CycleShareRecord,
    Hold,
    Release,
    StandingBudgetRecord,
)
from promptpotter.domain.spend import (
    CEILING_METER_LABELS,
    CYCLE_AXES,
    MONEY_AXES,
    NOTHING_METERED,
    Budget,
    CeilingMeter,
    HeldBudget,
    money_text,
)
from promptpotter.infrastructure.llm.wallet import Send
from promptpotter.infrastructure.store.read_model import money_on
from promptpotter.infrastructure.store.stores import Stores, tenant_stores
from promptpotter.shared.errors import NextAct


def share_campaign_ceiling(
    ceiling: Budget,
    *,
    stores: Stores,
    job_registry: JobRegistry,
    job: Job,
    hop: CycleHop | None,
    meter: CeilingMeter,
) -> Budget:
    """Other live cycles' holds count as spent, so a fork's parent holds its share until its pause is recorded."""
    money = Budget(usd=ceiling.usd, tokens=ceiling.tokens)
    if hop is None:
        return money
    _refuse_unshared(stores, job_registry, money, hop=hop, job=job)
    beside = _held_by_siblings(stores, job_registry, hop, meter)
    spent = stores.campaigns.metered_spend(hop.campaign_id, meter)
    left = money.less(spent, beside)
    if left.run_out:
        held = (
            f" and {money_text(beside, on=money)} held by its other live cycles. Pause one, or "
            "raise"
            if beside != NOTHING_METERED
            else ". Raise"
        )
        raise QuotaExceededError(
            code="campaign_ceiling_held",
            message=(
                f"This campaign's ceiling ({money_text(money)}) is used up: "
                f"{money_text(spent, on=money)} {CEILING_METER_LABELS[meter]}{held} the campaign's "
                "ceiling."
            ),
            next_acts=(NextAct(kind="set-limits"),),
        )
    return left.bound_over(stores.campaigns.cycle_metered(hop, meter))


def cycle_share(stores: Stores, job: Job) -> Budget:
    """A live job's share, off its cycle: admission lands it there before the job is on the campaign."""
    record = stores.campaigns.last(job.hop, CycleShareRecord)
    if record is None:
        raise RuntimeError(f"job {job.job_id} holds spend on {job.hop} with no share landed there")
    return record.allotment


def _held(share: Budget, own: Budget) -> Budget:
    """A sibling's share less what its cycle has metered, which the campaign's spend counts."""
    usd, tokens = share.usd, share.tokens
    return Budget.amount(
        0.0 if usd is None else max(0.0, usd - own.usd_amount),
        0 if tokens is None else max(0, tokens - own.token_amount),
    )


def _held_by_siblings(
    stores: Stores, job_registry: JobRegistry, hop: CycleHop, meter: CeilingMeter
) -> Budget:
    held = NOTHING_METERED
    for job in _live_on(stores, job_registry, hop.campaign_id):
        if job.hop != hop:
            own = stores.campaigns.cycle_metered(job.hop, meter)
            held = held.plus(_held(cycle_share(stores, job), own))
    return held


def _live_on(stores: Stores, job_registry: JobRegistry, campaign_id: str) -> list[Job]:
    """Launches, embedded and inner runs and scoring passes alike: each spends on the campaign."""
    return [
        job
        for job in job_registry.list_holding_spend(user_id=str(stores.identity.user_id))
        if job.on_campaign(stores.campaigns.workspace, campaign_id) and job.admitted
    ]


def _refuse_unshared(
    stores: Stores, job_registry: JobRegistry, money: Budget, *, hop: CycleHop, job: Job
) -> None:
    for other in job_registry.list_holding_spend(user_id=job.user_id):
        if (
            other.job_id == job.job_id
            or not other.on_campaign(stores.campaigns.workspace, hop.campaign_id)
            or other.hop == hop
        ):
            continue
        if admitting_before(other, job):
            raise launch_contended("this campaign's ceiling")
        if not other.admitted:
            continue
        held = cycle_share(stores, other)
        for axis in sorted(MONEY_AXES):
            ours, theirs = getattr(money, axis), getattr(held, axis)
            if (ours is None) != (theirs is None):
                unbounded = f"cycle {other.cycle_id}" if theirs is None else "this launch"
                raise QuotaExceededError(
                    code="campaign_ceiling_unshared",
                    message=(
                        f"Only one of this launch and cycle {other.cycle_id} holds a {axis} "
                        f"ceiling ({unbounded} runs with none), and a campaign's {axis} ceiling "
                        "binds every live cycle or none. Pause the other one, or move the "
                        "campaign's ceiling."
                    ),
                    next_acts=(NextAct(kind="set-limits", field=f"budget.{axis}"),),
                )


def _reshare_campaign_ceiling(
    moved: Budget, *, stores: Stores, job_registry: JobRegistry, campaign: Campaign
) -> None:
    """Oldest first, each live cycle from what the rest hold; never refuses, a share running short."""
    jobs = _live_on(stores, job_registry, campaign.campaign_id)
    if not jobs or not moved.named:
        return
    meter = ceiling_meter(campaign.declaration.purpose)
    spent = stores.campaigns.metered_spend(campaign.campaign_id, meter)
    own = {job.job_id: stores.campaigns.cycle_metered(job.hop, meter) for job in jobs}
    # Read before any moves: each cycle is re-shared from what the rest held going in.
    held = {job.job_id: cycle_share(stores, job) for job in jobs}
    for job in jobs:
        beside = NOTHING_METERED
        for other in jobs:
            if other.job_id != job.job_id:
                beside = beside.plus(_held(held[other.job_id], own[other.job_id]))
        share = moved.less(spent, beside).bound_over(own[job.job_id])
        hold_moved(stores, job.hop, held[job.job_id].over(share.only(moved.named)))


def hold_moved(stores: Stores, hop: CycleHop, allotment: Budget) -> None:
    """THE move of a cycle's share: its record is the one store, which its running book reads live."""
    held = stores.campaigns.last(hop, CycleShareRecord)
    if held is not None and held.allotment == allotment:
        return
    stores.campaigns.append(hop, CycleShareRecord(allotment=allotment))


def move_campaign_money(
    money: Budget, *, asked: Budget, stores: Stores, job_registry: JobRegistry, campaign: Campaign
) -> None:
    """THE move of a campaign's ceiling: stored as armed (``None`` lifts) beside what was *asked*
    of the axes it moves, then re-shared."""
    money = money.only(MONEY_AXES)
    if not money.named:
        return
    with job_registry.admission_gate():
        prior = stores.campaigns.read_campaign_ceiling(campaign)
        standing = CampaignCeilingRecord(
            budget=prior.budget.over(money), asked=prior.asked.over(asked.only(money.named))
        )
        if (standing.budget, standing.asked) == (prior.budget, prior.asked):
            return
        stores.campaigns.append(campaign.root_hop, standing)
        _reshare_campaign_ceiling(
            money, stores=stores, job_registry=job_registry, campaign=campaign
        )


def change_budget(
    requested: Budget,
    *,
    overshoot: bool | None = None,
    stores: Stores,
    job_registry: JobRegistry,
    at: CampaignCycle,
    sends: tuple[Send, ...],
) -> bool:
    """Set-limits is an admission of the changed ceiling: the wallet read, the clamp, the reserve
    and the hold run under the one gate a launch admits under, so no admission between them goes
    unseen. Money moves the CAMPAIGN's ceiling and re-shares it over every live cycle; holds land
    first. Whether the ceiling *at*'s cycle runs under, or its overshoot, moved: a change
    restating them moves nothing."""
    campaigns, campaign, hop = stores.campaigns, at.campaign, at.hop
    armed = campaigns.armed_budget(at)
    change = requested
    with job_registry.admission_gate():
        job = None if (held := campaigns.holder(hop)) is None else held.run
        # Rounds and a clock are no money: a change naming neither money axis reads no wallet.
        if requested.named & MONEY_AXES:
            change, reserve = clamped_change(
                requested, stores=stores, job_registry=job_registry, job=job, hop=hop, sends=sends
            )
            if job is not None:
                hold = money_on(stores.charges.path).holds[job.job_id]
                hold_run(stores, job.job_id, hold.bound.over(reserve), parent=hold.parent)
        move_campaign_money(
            change, asked=requested, stores=stores, job_registry=job_registry, campaign=campaign
        )
    cycle = change.only(CYCLE_AXES)
    if overshoot == campaigns.read_standing_budget(hop).overshoot:
        overshoot = None
    if overshoot is not None:
        campaigns.append(hop, StandingBudgetRecord(budget=cycle, overshoot=overshoot))
    elif cycle.named:
        campaigns.append(hop, StandingBudgetRecord(budget=cycle))
    return overshoot is not None or campaigns.armed_budget(at) != armed


def hold_run(stores: Stores, hold_id: str, bound: Budget, *, parent: str | None) -> None:
    """THE run's hold on its account, opened at admission and restated by a set-limits: the
    coupon's reserve for it, which its book and every other launch's wallet read."""
    stores.charges.append(
        Hold(
            hold_id=hold_id,
            parent=parent,
            key_source="host",
            bound=bound,
            resource=None,
            node=None,
            ledger=None,
        )
    )


def release_run(stores: Stores, hold_id: str) -> None:
    """The run's hold closes with its job: by its release, or `reap_dead_runs` for a dead producer."""
    stores.charges.append(Release(hold_id=hold_id))


def reap_dead_runs(projects_root: Path, job_registry: JobRegistry) -> None:
    """Releases each job whose producer is gone and closes its hold; an inner run's closes with
    the enclosing run's, whose producer it shared."""
    for job in job_registry.reap_orphans():
        if not isinstance(job.holder, InnerRun):
            release_run(tenant_stores(projects_root, WorkspaceDir(Path(job.workspace))), job.job_id)


def stand_held_budget(stores: Stores, hop: CycleHop, held: HeldBudget) -> None:
    """This cycle's own axes; its money is the campaign's (``move_campaign_money``), its hold the job's."""
    cycle = held.standing.only(CYCLE_AXES)
    if stores.campaigns.read_standing_budget(hop).budget != cycle:
        stores.campaigns.append(hop, StandingBudgetRecord(budget=cycle))


def declare_round_allowance(
    stores: Stores, hop: CycleHop, *, resumed_from_round: int, step_rounds: int | None
) -> None:
    """A launch with no allowance clears the last launch's."""
    campaigns = stores.campaigns
    pause_at_round = None if step_rounds is None else max(resumed_from_round - 1, 0) + step_rounds
    if campaigns.read_standing_budget(hop).pause_at_round != pause_at_round:
        campaigns.append(hop, StandingBudgetRecord(pause_at_round=pause_at_round))
