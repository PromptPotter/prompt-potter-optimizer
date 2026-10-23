from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import NamedTuple

from pydantic import Field

from promptpotter.application.account import account_of
from promptpotter.application.campaign_config import CampaignConfig
from promptpotter.application.jobs.registry import (
    UNRESOLVED_HOP,
    Job,
    JobRegistry,
)
from promptpotter.config.settings import settings
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.spend import (
    CEILING_METER_LABELS,
    CYCLE_AXES,
    MONEY_AXES,
    NOTHING_METERED,
    RATE_PRICED_LABEL,
    Budget,
    CeilingMeter,
    bill_is_floor,
)
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.identity.migration import is_host
from promptpotter.infrastructure.llm.send_pacing import set_rate_tenant
from promptpotter.infrastructure.llm.spend_book import (
    Hold,
    SpendBook,
    spending_under,
    unbounded_spend_book,
)
from promptpotter.infrastructure.store.account_spend import (
    ZERO_SPEND,
    LifetimeSpend,
    UserSpend,
    account_ledgers,
    campaign_spend,
    sum_user_spend,
)
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.infrastructure.store.user_store import User
from promptpotter.shared.errors import NotFoundError, PayloadInvalidError, PotterError
from promptpotter.shared.identity import (
    CAMPAIGN_BUDGET_CAP,
    has_capability,
)

logger = logging.getLogger(__name__)


class QuotaExceededError(PotterError):
    """A user-scoped abuse limit blocked a launch — 429, as against ``LaunchError``'s 422 for a malformed
    or unowned request. Both map to one HTTP response through the ``PotterError`` seam."""

    http_status = 429

    def __init__(self, *, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


# Process-wide: survives the request scope, not a restart — abuse-bound, not audit-bound.
_rate_buckets: dict[str, tuple[float, float]] = {}  # bucket key → (tokens, last_refill_ts)
_rate_lock = threading.Lock()


def _consume_rate_token(bucket: str) -> bool:
    now = time.monotonic()
    burst = float(settings.USER_RATE_BURST)
    refill_per_sec = settings.USER_RATE_PER_MIN / 60.0
    with _rate_lock:
        tokens, last = _rate_buckets.get(bucket, (burst, now))
        tokens = min(burst, tokens + (now - last) * refill_per_sec)
        if tokens < 1.0:
            _rate_buckets[bucket] = (tokens, now)
            return False
        _rate_buckets[bucket] = (tokens - 1.0, now)
        return True


def check_launch_quotas(
    *, user: User, stores: Stores, job_registry: JobRegistry, hop: CycleHop
) -> None:
    """Runs BEFORE ``JobRegistry.request_slot``, same gate: it counts prior launches, not this one."""
    # A fresh mint, a check-in's Start included, names no cycle yet.
    rate_limited = not spends_the_hosts_own_key(stores) and hop == UNRESOLVED_HOP
    if rate_limited and not _consume_rate_token(user.user_id):
        raise QuotaExceededError(
            code="rate_limited",
            message="Too many campaign launches; slow down and retry shortly.",
        )

    # A queued launch counts, or an account queues without bound and the drain order can starve.
    # A scoring pass does not: the limit is on campaigns, and a pass that finds no slot is refused.
    in_flight = len(job_registry.list_launches(user_id=user.user_id))
    if in_flight >= user.max_concurrent_cycles:
        raise QuotaExceededError(
            code="quota_exceeded",
            message=(
                f"Concurrent-cycles ceiling reached "
                f"({in_flight}/{user.max_concurrent_cycles}); "
                f"stop or cancel one before starting another."
            ),
        )

    if rate_limited:
        today = job_registry.list_launched_today(user_id=user.user_id)
        if len(today) >= user.max_campaigns_per_day:
            raise QuotaExceededError(
                code="quota_exceeded",
                message=(
                    f"Daily campaigns ceiling reached "
                    f"({len(today)}/{user.max_campaigns_per_day} today); "
                    f"resets at UTC midnight."
                ),
            )


def overrun(ceilings: Budget, spent: UserSpend) -> Budget:
    """The OPERATOR's number: ``/quota-status`` never serves it."""
    return Budget.amount(
        0.0 if ceilings.usd is None else max(0.0, spent.sent_usd - ceilings.usd),
        0 if ceilings.tokens is None else max(0, spent.used_tokens - ceilings.tokens),
    )


class AccountWallet(NamedTuple):
    """One read of an account's position: what it has spent, what it answers to, and what a launch
    may still declare once in-flight reservations are held back."""

    spent: UserSpend
    ceilings: Budget
    headroom: Budget
    # Headroom is quoted at zero, not twice: a contended account has money and needs a retry.
    contended: bool = False
    # The excluded campaign's own spend, which `headroom` makes room for: its ceiling counts it.
    history: UserSpend = ZERO_SPEND

    @property
    def exhausted(self) -> bool:
        """A contended wallet quotes zero and is not exhausted: a launch still being admitted holds it."""
        return not self.contended and (
            (self.headroom.usd is not None and self.headroom.usd <= self.history.at_most_usd)
            or (
                self.headroom.tokens is not None
                and self.headroom.tokens <= self.history.at_most_tokens
            )
        )


def read_account_wallet(
    *,
    user: User,
    stores: Stores,
    job_registry: JobRegistry,
    excluding_job_id: str | None = None,
    excluding_campaign: str | None = None,
) -> AccountWallet:
    """A running cycle's spend-so-far is counted twice (ledger + reserve): it errs toward refusing."""
    ceilings = lifetime_ceilings(user=user, spends_own_key=spends_the_hosts_own_key(stores))
    spent = sum_user_spend(ledgers=account_ledgers(stores.campaigns))
    if ceilings.usd is None and ceilings.tokens is None:
        return AccountWallet(spent, ceilings, ceilings)
    history = (
        ZERO_SPEND
        if excluding_campaign is None
        else campaign_spend(stores.campaigns, excluding_campaign)
    )
    held = _outstanding_reservations(
        job_registry, user_id=user.user_id, excluding_job_id=excluding_job_id
    )
    if held is None:
        return AccountWallet(
            spent,
            ceilings,
            Budget(
                usd=None if ceilings.usd is None else 0.0,
                tokens=None if ceilings.tokens is None else 0,
            ),
            contended=True,
        )
    headroom = Budget(
        usd=None
        if ceilings.usd is None
        else _grace_bounded(max(0.0, ceilings.usd - spent.at_most_usd - held.usd_amount), spent)
        + history.at_most_usd,
        tokens=None
        if ceilings.tokens is None
        else max(0, ceilings.tokens - spent.at_most_tokens - held.token_amount)
        + history.at_most_tokens,
    )
    return AccountWallet(spent, ceilings, headroom, history=history)


class QuotaStatus(StrictModel):
    """Usage against the next launch's gates: spend and tokens LIFETIME, campaigns per day."""

    spend_lifetime: LifetimeSpend
    spend_budget_usd_total: float | None
    spend_budget_used_share: float | None = Field(
        description="How full the USD meter draws: `spend_lifetime.billed_usd` plus its "
        "`rate_priced_usd`, over `spend_budget_usd_total`, at most 1. `None` where no ceiling "
        "bounds the account."
    )
    allowance_exhausted: bool = Field(
        description="The allowance is SPENT in either unit, unreported sends counted. What a "
        "running launch holds is not spent: a run declaring no cap holds the whole headroom. "
        "False on an account no ceiling bounds."
    )
    tokens_used_total: int
    token_budget_total: int | None
    token_budget_used_share: float | None = Field(
        description="The token meter's twin of `spend_budget_used_share`."
    )
    concurrent_running: int
    concurrent_queued: int = Field(
        description="This account's launches waiting for a machine slot. They count against "
        "`max_concurrent_cycles` exactly as running ones do."
    )
    max_concurrent_cycles: int
    max_concurrent_cycles_writable: bool = Field(
        description="Whether this caller may move `max_concurrent_cycles` through "
        "`set-concurrent-cycles`. False on the host's key, where the host sets it."
    )
    machine_binds: str | None = Field(
        description="`max_concurrent_cycles` above the machine's `MACHINE_RUN_CAPACITY`, in "
        "words; null where the account's own limit is the one that binds."
    )
    campaigns_today: int
    max_campaigns_per_day: int


def _used_share(used: float, ceiling: float | None) -> float | None:
    """A ceiling of nothing is full: there is no room under it to draw."""
    if ceiling is None:
        return None
    return min(1.0, used / ceiling) if ceiling > 0 else 1.0


def _machine_binds(limit: int) -> str | None:
    ceiling = settings.MACHINE_RUN_CAPACITY
    if limit <= ceiling:
        return None
    return (
        f"This account's limit of {limit} is above the machine's {ceiling}, so the machine is "
        "what binds."
    )


def quota_status(*, stores: Stores, job_registry: JobRegistry) -> QuotaStatus:
    """Usage is uncapped, so an account past its ceiling reads its overage."""
    user = account_of(stores)
    # No reservation is read: this reports what was spent, and admission alone weighs what is held.
    ceilings = lifetime_ceilings(user=user, spends_own_key=spends_the_hosts_own_key(stores))
    spent = sum_user_spend(ledgers=account_ledgers(stores.campaigns))
    # The launches `max_concurrent_cycles` binds: a scoring pass is not among them.
    launches = job_registry.list_launches(user_id=user.user_id)
    return QuotaStatus(
        spend_lifetime=LifetimeSpend.of(spent),
        spend_budget_usd_total=ceilings.usd,
        spend_budget_used_share=_used_share(spent.sent_usd, ceilings.usd),
        allowance_exhausted=(ceilings.usd is not None and spent.at_most_usd >= ceilings.usd)
        or (ceilings.tokens is not None and spent.at_most_tokens >= ceilings.tokens),
        tokens_used_total=spent.used_tokens,
        token_budget_total=ceilings.tokens,
        token_budget_used_share=_used_share(spent.used_tokens, ceilings.tokens),
        concurrent_running=sum(j.holds_slot for j in launches),
        concurrent_queued=sum(j.queued for j in launches),
        max_concurrent_cycles=user.max_concurrent_cycles,
        max_concurrent_cycles_writable=concurrent_cycles_writable(stores),
        machine_binds=_machine_binds(user.max_concurrent_cycles),
        campaigns_today=len(job_registry.list_launched_today(user_id=user.user_id)),
        max_campaigns_per_day=user.max_campaigns_per_day,
    )


def admit_launch(
    *,
    declared: Budget,
    user: User,
    stores: Stores,
    job_registry: JobRegistry,
    job_id: str,
    hop: CycleHop | None,
) -> tuple[Budget, Budget]:
    """Refused WHOLE, never clamped. The admitted budget, then what the account reserves for it."""
    wallet = read_account_wallet(
        user=user,
        stores=stores,
        job_registry=job_registry,
        excluding_job_id=job_id,
        excluding_campaign=None if hop is None else hop.campaign_id,
    )
    if wallet.contended:
        raise _contended("what this account has left")

    def _refused(reason: str) -> QuotaExceededError:
        return _refusal(wallet, reason, job_registry.get(job_id))

    if bill_is_floor(wallet.spent.unpriced_tokens) and wallet.headroom.usd is not None:
        logger.warning(
            "spend: account %s has %d unpriced tokens, so its USD total is a floor; admitting "
            "against the $%.2f grace and leaning on the token ceiling",
            user.user_id,
            wallet.spent.unpriced_tokens,
            settings.UNPRICED_GRACE_USD,
        )
    if wallet.exhausted:
        raise _refused("This account has nothing left to spend.")
    delegated = _delegated_spend_ceiling(stores)
    step = _launch_step(user, wallet, delegated)
    room = wallet.headroom
    tokens = declared.tokens
    if declared.usd is None:
        # Declaring nothing declares the headroom under the grant, never the grant itself.
        usd = _lowest(room.usd, delegated, step)
    else:
        usd = _lowest(declared.usd, delegated)
        if step is not None and usd is not None and usd > step:
            raise _refused(
                f"A run on this account is admitted at ${step:.2f} and this one declares "
                f"${usd:.2f}.",
            )
        if room.usd is not None and usd is not None and usd > room.usd:
            raise _refused(
                f"This account funds a ceiling of ${room.usd:.2f} for this campaign and the run "
                f"declares ${usd:.2f}.",
            )
    if room.tokens is not None:
        tokens = room.tokens if tokens is None else tokens
        if tokens > room.tokens:
            raise _refused(
                f"This account funds a ceiling of {room.tokens:,} tokens for this campaign and "
                f"the run declares {tokens:,}.",
            )
    admitted = declared.over(Budget(usd=usd, tokens=tokens))
    return admitted, _reserve(admitted, room, delegated)


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
    left = _left(money, spent=spent, beside=beside)
    if (left.usd is not None and left.usd <= 0) or (left.tokens is not None and left.tokens <= 0):
        held = (
            f" and {_amounts(beside)} held by its other live cycles. Pause one, or raise"
            if beside != NOTHING_METERED
            else ". Raise"
        )
        raise QuotaExceededError(
            code="campaign_ceiling_held",
            message=(
                f"This campaign's ceiling ({_amounts(money)}) is used up: "
                f"{_amounts(spent)} {CEILING_METER_LABELS[meter]}{held} the campaign's ceiling "
                "with `set-limits`."
            ),
        )
    return _allotment(left, own=stores.campaigns.cycle_metered(hop, meter))


def _left(ceiling: Budget, *, spent: Budget, beside: Budget) -> Budget:
    """Per axis *ceiling* names: what neither the campaign's spend nor its siblings' holds took."""
    return Budget(
        usd=None if ceiling.usd is None else ceiling.usd - spent.usd_amount - beside.usd_amount,
        tokens=None
        if ceiling.tokens is None
        else ceiling.tokens - spent.token_amount - beside.token_amount,
    )


def _allotment(left: Budget, *, own: Budget) -> Budget:
    return Budget(
        usd=None if left.usd is None else own.usd_amount + max(0.0, left.usd),
        tokens=None if left.tokens is None else own.token_amount + max(0, left.tokens),
    )


def _held(job: Job, own: Budget) -> Budget:
    """A sibling's share less what its cycle has metered, which the campaign's spend counts."""
    usd, tokens = job.allotment.usd, job.allotment.tokens
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
            held = held.plus(_held(job, stores.campaigns.cycle_metered(job.hop, meter)))
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
    mine = (job.created_at, job.job_id)
    for other in job_registry.list_holding_spend(user_id=job.user_id):
        if (
            other.job_id == job.job_id
            or not other.on_campaign(stores.campaigns.workspace, hop.campaign_id)
            or other.hop == hop
        ):
            continue
        if not other.admitted:
            # Only an EARLIER one blocks, or two simultaneous launches refuse each other.
            if (other.created_at, other.job_id) < mine:
                raise _contended("this campaign's ceiling")
            continue
        for axis in sorted(MONEY_AXES):
            ours, theirs = getattr(money, axis), getattr(other.allotment, axis)
            if (ours is None) != (theirs is None):
                unbounded = f"cycle {other.cycle_id}" if theirs is None else "this launch"
                raise QuotaExceededError(
                    code="campaign_ceiling_unshared",
                    message=(
                        f"Only one of this launch and cycle {other.cycle_id} holds a {axis} "
                        f"ceiling ({unbounded} runs with none), and a campaign's {axis} ceiling "
                        "binds every live cycle or none. Pause the other one, or move the "
                        "campaign's ceiling with `set-limits`."
                    ),
                )


def _reshare_campaign_ceiling(
    moved: Budget, *, stores: Stores, job_registry: JobRegistry, campaign_id: str
) -> None:
    """Oldest first, each live cycle from what the rest hold; never refuses, a share running short."""
    jobs = _live_on(stores, job_registry, campaign_id)
    if not jobs or not moved.named:
        return
    meter = stores.campaigns.ceiling_meter_of(campaign_id)
    spent = stores.campaigns.metered_spend(campaign_id, meter)
    own = {job.job_id: stores.campaigns.cycle_metered(job.hop, meter) for job in jobs}
    for job in jobs:
        beside = NOTHING_METERED
        for other in jobs:
            if other.job_id != job.job_id:
                beside = beside.plus(_held(other, own[other.job_id]))
        share = _allotment(_left(moved, spent=spent, beside=beside), own=own[job.job_id])
        job.allotment = job.allotment.over(share.only(moved.named))
        # A run's book reads its hold off the job file, live.
        job_registry.set_held(job.job_id, job.reserve, job.allotment)


def move_campaign_money(
    money: Budget, *, stores: Stores, job_registry: JobRegistry, campaign_id: str
) -> None:
    """THE move of a campaign's ceiling: stored as asked (``None`` lifts), then re-shared."""
    money = money.only(MONEY_AXES)
    if not money.named:
        return
    with job_registry.admission_gate():
        campaign = stores.campaigns.load_campaign(campaign_id)
        if campaign is None:
            raise NotFoundError(f"campaign not found: {campaign_id}")
        standing = campaign.standing_budget.over(money)
        if standing == campaign.standing_budget:
            return
        stores.campaigns.update_campaign(campaign_id, standing_budget=standing)
        _reshare_campaign_ceiling(
            money, stores=stores, job_registry=job_registry, campaign_id=campaign_id
        )


def _amounts(budget: Budget) -> str:
    usd = "no USD bound" if budget.usd is None else f"${budget.usd:.4f}"
    tokens = "no token bound" if budget.tokens is None else f"{budget.tokens:,} tokens"
    return f"{usd} / {tokens}"


def launch_usd_bound(stores: Stores) -> float | None:
    """The most a launch may declare before ``admit_launch`` refuses it whole: what a check-in offers."""
    user = account_of(stores)
    if lifetime_ceilings(user=user, spends_own_key=spends_the_hosts_own_key(stores)).usd is None:
        return None
    wallet = read_account_wallet(user=user, stores=stores, job_registry=JobRegistry.attach())
    step = _launch_step(user, wallet, _delegated_spend_ceiling(stores))
    return _lowest(None if wallet.contended else wallet.headroom.usd, step)


def refuse_verb_rate(stores: Stores, user: User, bucket: str) -> None:
    # The host is exempt in the RATE arm too: the meter bounds a stranger on the host's key.
    if not spends_the_hosts_own_key(stores) and not _consume_rate_token(f"{bucket}:{user.user_id}"):
        raise QuotaExceededError(
            code="rate_limited",
            message=f"Too many {bucket} requests; slow down and retry shortly.",
        )


def _admit_call(*, stores: Stores, bucket: str) -> SpendBook:
    # Nothing is reserved: the book itself refuses a call that would pass the headroom.
    user = account_of(stores)
    refuse_verb_rate(stores, user, bucket)
    ceilings = lifetime_ceilings(user=user, spends_own_key=spends_the_hosts_own_key(stores))
    if ceilings.usd is None and ceilings.tokens is None:
        return unbounded_spend_book()
    wallet = read_account_wallet(
        user=user,
        stores=stores,
        job_registry=JobRegistry.attach(),
    )
    if wallet.contended:
        raise _contended("what this account has left")
    headroom = wallet.headroom
    if wallet.exhausted:
        unreported = (
            f", up to ${wallet.spent.unreported_usd:.2f} more in sends whose bill never came"
            if wallet.spent.sends_unreported
            else ""
        )
        raise QuotaExceededError(
            code="spend_ceiling_reached",
            message=(
                f"This account has used its allowance (${wallet.spent.used_usd:.2f} billed, "
                f"${wallet.spent.rate_priced_usd:.2f} {RATE_PRICED_LABEL}, "
                f"{wallet.spent.used_tokens:,} tokens{unreported}), so nothing further "
                "runs on the host's key."
            ),
        )
    return SpendBook(
        declared=Budget(),
        hold=Hold(headroom, headroom),
        meters="bill",
        opened=time.monotonic(),
    )


@asynccontextmanager
async def paid_call(*, stores: Stores, bucket: str) -> AsyncIterator[SpendBook]:
    """ONE LLM call on no cycle, slotless so a chat turn never queues behind campaigns."""
    # Offloaded: admission globs every cycle ledger.
    book = await asyncio.to_thread(_admit_call, stores=stores, bucket=bucket)
    set_rate_tenant(str(stores.identity.tenant_id))
    with spending_under(book):
        yield book


def declare_run_budget(
    config: CampaignConfig,
    *,
    stores: Stores,
    hop: CycleHop | None,
    requested: Budget,
) -> tuple[Budget, Budget]:
    """Composed BEFORE admission; second, the axes an operator named (campaign, cycle, *requested*)."""
    seed = Budget()
    asked = Budget()
    if hop is not None:
        cycle_seed = stores.campaigns.read_cycle_seed(hop)
        if cycle_seed is not None:
            seed = cycle_seed.config_overrides.budget
        campaign = stores.campaigns.load_campaign(hop.campaign_id)
        if campaign is None:
            raise NotFoundError(f"campaign not found: {hop.campaign_id}")
        asked = campaign.standing_budget.over(stores.campaigns.read_standing_budget(hop).budget)
    asked = asked.over(requested)
    return config.optimization.budget.over(seed, asked), asked


def next_launch_budget(config: CampaignConfig, *, stores: Stores, hop: CycleHop) -> Budget:
    return declare_run_budget(config, stores=stores, hop=hop, requested=Budget())[0]


def clamp_budget_change(
    *,
    requested: Budget,
    user: User,
    stores: Stores,
    job_registry: JobRegistry,
    hop: CycleHop,
) -> tuple[Budget, Budget]:
    """Clamps only a NAMED, bounded money axis: a lift stays a lift, which the reservation binds."""
    held = job_registry.running_job_for(stores.campaigns.workspace, hop)
    wallet = read_account_wallet(
        user=user,
        stores=stores,
        job_registry=job_registry,
        excluding_job_id=None if held is None else held.job_id,
        excluding_campaign=hop.campaign_id,
    )
    # Refuse rather than clamp: a contended wallet's zero would write a $0 ceiling on a funded run.
    if wallet.contended:
        raise _contended("this campaign's ceiling")
    delegated = _delegated_spend_ceiling(stores)
    room = wallet.headroom
    step = _launch_step(user, wallet, delegated)
    # Per named axis: the clamped ceiling, and the bound the account reserves against.
    bounds: dict[str, tuple[float | int | None, float | int | None]] = {}
    if "usd" in requested.named:
        room_usd = _lowest(room.usd, delegated, step)
        usd = None if requested.usd is None else _lowest(requested.usd, room_usd)
        bounds["usd"] = (usd, room_usd if usd is None else usd)
    if "tokens" in requested.named:
        tokens = None if requested.tokens is None else _lowest(requested.tokens, room.tokens)
        bounds["tokens"] = (tokens, room.tokens if tokens is None else tokens)
    change = requested.over(Budget.model_validate({k: v for k, (v, _) in bounds.items()}))
    bound = Budget.model_validate({k: b for k, (_, b) in bounds.items()})
    return change, _reserve(bound, room, delegated)


def _reserve(budget: Budget, headroom: Budget, delegated: float | None) -> Budget:
    """On the money axes *budget* names alone; ``None`` where no account bounds it or *budget* lifts."""
    usd = _lowest(headroom.usd, delegated)
    tokens = headroom.tokens
    held: dict[str, float | int | None] = {
        "usd": None if usd is None or budget.usd is None else min(usd, 2 * budget.usd),
        "tokens": None
        if tokens is None or budget.tokens is None
        else min(tokens, 2 * budget.tokens),
    }
    return Budget.model_validate(
        {axis: value for axis, value in held.items() if axis in budget.named}
    )


def hold_budget_change(
    *,
    job_registry: JobRegistry,
    stores: Stores,
    hop: CycleHop,
    change: Budget,
    reserve: Budget,
) -> None:
    """Money moves the CAMPAIGN's ceiling and re-shares it over every live cycle; holds land first."""
    # Under the gate a launch admits under, so no share is taken from a split mid-move.
    with job_registry.admission_gate():
        job = job_registry.running_job_for(stores.campaigns.workspace, hop)
        if job is not None:
            job_registry.set_held(job.job_id, job.reserve.over(reserve), job.allotment)
        move_campaign_money(
            change, stores=stores, job_registry=job_registry, campaign_id=hop.campaign_id
        )
    cycle = change.only(CYCLE_AXES)
    if cycle.named:
        prior = stores.campaigns.read_standing_budget(hop)
        stores.campaigns.write_standing_budget(
            hop, prior.budget.over(cycle), pause_at_round=prior.pause_at_round
        )


def _contended(subject: str) -> QuotaExceededError:
    """The ``request_slot``→``mark_admitted`` window: retryable, as an exhausted ceiling is not."""
    return QuotaExceededError(
        code="launch_contended",
        message=(
            f"Another launch on this account is still being admitted, so {subject} cannot be "
            f"quoted yet. Retry in a moment."
        ),
    )


def _refusal(wallet: AccountWallet, reason: str, refusing: Job | None) -> QuotaExceededError:
    """Names what it refuses off *refusing*'s holder — a campaign, or a paid verb's scoring pass."""
    over = overrun(wallet.ceilings, wallet.spent)
    if over != NOTHING_METERED:
        reason += f" It is already {_amounts(over)} past its ceiling."
    what = "this launch" if refusing is None else refusing.what
    return QuotaExceededError(
        code="spend_ceiling_reached",
        message=(
            f"{reason} Refused whole, never clamped: {what}. Lower its ceiling or raise the "
            f"account's."
        ),
    )


def _outstanding_reservations(
    job_registry: JobRegistry, *, user_id: str, excluding_job_id: str | None
) -> Budget | None:
    """``None`` fails closed on an unstamped sibling. Exclusion is by job, never by hop."""
    running = job_registry.list_holding_spend(user_id=user_id)
    own = next((j for j in running if j.job_id == excluding_job_id), None)
    mine = None if own is None else (own.created_at, own.job_id)
    held = NOTHING_METERED
    for job in running:
        if job.job_id == excluding_job_id:
            continue
        if not job.admitted:
            # Only an EARLIER unstamped sibling blocks, or two simultaneous launches refuse each other.
            if mine is None or (job.created_at, job.job_id) < mine:
                return None
            continue
        # An axis no account bounds reserves nothing on it.
        usd, tokens = job.reserve.usd, job.reserve.tokens
        held = held.plus(Budget.amount(usd or 0.0, tokens or 0))
    return held


def _grace_bounded(remaining: float, spent: UserSpend) -> float:
    """A CEILING on the remainder, so an already-exhausted account gets nothing."""
    if bill_is_floor(spent.unpriced_tokens):
        return min(remaining, settings.UNPRICED_GRACE_USD)
    return remaining


def _lowest(*values: float | None) -> float | None:
    present = [v for v in values if v is not None]
    return min(present) if present else None


def lifetime_ceilings(*, user: User, spends_own_key: bool) -> Budget:
    """The host is exempt in both axes: metering bounds a STRANGER on the host's key."""
    if spends_own_key:
        return Budget()
    usd = user.spend_budget_usd_total
    tokens = user.token_budget_total
    return Budget(
        usd=usd if usd is not None else settings.FREE_TIER_SPEND_CAP_USD,
        tokens=tokens if tokens is not None else settings.FREE_TIER_TOKEN_CAP,
    )


def spends_the_hosts_own_key(stores: Stores) -> bool:
    return is_host(stores.identity)


def concurrent_cycles_writable(stores: Stores) -> bool:
    return spends_the_hosts_own_key(stores) and has_capability(stores.identity, CAMPAIGN_BUDGET_CAP)


def set_concurrent_cycles(*, stores: Stores, limit: int) -> User:
    """Refused rather than clamped: a limit written lower than asked is a ceiling nobody chose."""
    if not spends_the_hosts_own_key(stores):
        raise PayloadInvalidError(
            "This account's concurrent-cycles limit is set by whoever runs this box.",
            code="concurrency_set_by_host",
        )
    ceiling = settings.MACHINE_RUN_CAPACITY
    if limit > ceiling:
        raise PayloadInvalidError(
            f"This machine runs at most {ceiling} campaigns at once, so an account limit of "
            f"{limit} could never bind. That ceiling is MACHINE_RUN_CAPACITY in .env, read when "
            "the process starts: raise it there and restart the server.",
            code="concurrency_above_machine",
            details={"requested": limit, "machine_ceiling": ceiling},
        )
    updated = account_of(stores).model_copy(update={"max_concurrent_cycles": limit})
    stores.users.save(updated)
    return updated


def _launch_step(user: User, wallet: AccountWallet, delegated: float | None) -> float | None:
    """Rations the ANONYMOUS grant only: the host, a delegate and a hand-raised account are outside."""
    if (
        wallet.ceilings.usd is None
        or delegated is not None
        or user.spend_budget_usd_total is not None
    ):
        return None
    return settings.FREE_TIER_LAUNCH_STEP_USD


def _delegated_spend_ceiling(stores: Stores) -> float | None:
    ceiling = stores.identity.claims.get("spend_ceiling_usd")
    return float(ceiling) if isinstance(ceiling, int | float) else None
