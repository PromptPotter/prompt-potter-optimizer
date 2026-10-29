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
from promptpotter.application.jobs.registry import (
    UNRESOLVED_HOP,
    Job,
    JobRegistry,
    PaidCall,
)
from promptpotter.application.optimizer_manifest import select_optimizer
from promptpotter.config.settings import settings
from promptpotter.domain.campaign_config import CampaignConfig
from promptpotter.domain.connector import ConnectorExecution
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.spend import (
    CEILING_METER_LABELS,
    CYCLE_AXES,
    MONEY_AXES,
    NOTHING_METERED,
    ZERO_SPEND,
    Budget,
    CeilingMeter,
    UserSpend,
    bill_is_floor,
)
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.identity.migration import is_host
from promptpotter.infrastructure.llm.account_key import (
    Coupon,
    account_spend,
    bind_account,
    named_providers,
    payers_of,
    read_coupon,
)
from promptpotter.infrastructure.llm.registry import providers
from promptpotter.infrastructure.llm.spend_book import (
    Hold,
    SpendBook,
    spending_under,
    unbounded_spend_book,
)
from promptpotter.infrastructure.store.account_spend import campaign_spend
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.infrastructure.store.user_store import User
from promptpotter.shared.errors import (
    HostAllowanceExhaustedError,
    NotFoundError,
    PayloadInvalidError,
    PotterError,
)
from promptpotter.shared.identity import (
    CAMPAIGN_BUDGET_CAP,
    IdentityContext,
    acting_principal,
    has_capability,
)

logger = logging.getLogger(__name__)


class QuotaExceededError(PotterError):
    """A user-scoped abuse limit refused a request — 429, as against ``LaunchError``'s 422 for a malformed
    or unowned one. Both map to one HTTP response through the ``PotterError`` seam."""

    http_status = 429

    def __init__(self, *, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


# Process-wide: survives the request scope, not a restart — abuse-bound, not audit-bound.
_rate_buckets: dict[str, tuple[float, float]] = {}  # bucket key → (tokens, last_refill_ts)
_rate_lock = threading.Lock()

# Sized above a person opening every tab in a row, each mount firing its reads at once
# (`e2e/cold/first-run.spec.ts`'s tab sweep is the probe), then two tabs of `webapp/lib/cadence.ts`'s polls.
_READ_BURST = 300.0
_READ_PER_S = 20.0


def _consume_rate_token(bucket: str, *, burst: float, per_s: float) -> bool:
    now = time.monotonic()
    with _rate_lock:
        tokens, last = _rate_buckets.get(bucket, (burst, now))
        tokens = min(burst, tokens + (now - last) * per_s)
        if tokens < 1.0:
            _rate_buckets[bucket] = (tokens, now)
            return False
        _rate_buckets[bucket] = (tokens - 1.0, now)
        return True


def _consume_launch_token(bucket: str) -> bool:
    return _consume_rate_token(
        bucket, burst=settings.USER_RATE_BURST, per_s=settings.USER_RATE_PER_MIN / 60.0
    )


class RateLimitedError(QuotaExceededError):
    """A throttle: it repeats as fast as it is asked, so the trail banks its first per principal per
    window and logs the rest at DEBUG (`main.py::_repeats_a_throttle`)."""

    def __init__(self, message: str) -> None:
        super().__init__(code="rate_limited", message=message)


def refuse_read_rate(principal_id: str) -> None:
    """Only a read is metered here: it is what a poll repeats."""
    if not _consume_rate_token(f"read:{principal_id}", burst=_READ_BURST, per_s=_READ_PER_S):
        raise RateLimitedError(
            "Too many reads, faster than the dashboard polls; slow down and retry shortly."
        )


# Sized above a person working a check-in by hand: each committed edit is one write, none on a timer.
_WRITE_BURST = 60.0
_WRITE_PER_S = 1.0


def refuse_write_rate(identity: IdentityContext) -> None:
    """Every request but a read, a free verb's included; the host is exempt, as from every bucket that meters a stranger."""
    if not is_host(identity) and not _consume_rate_token(
        f"write:{acting_principal(identity).id}", burst=_WRITE_BURST, per_s=_WRITE_PER_S
    ):
        raise RateLimitedError("Too many changes in a row; slow down and retry shortly.")


def check_launch_quotas(
    *, user: User, stores: Stores, job_registry: JobRegistry, hop: CycleHop
) -> None:
    """Runs BEFORE ``JobRegistry.request_slot``, same gate: it counts prior launches, not this one."""
    # A fresh mint, a check-in's Start included, names no cycle yet.
    rate_limited = not spends_the_hosts_own_key(stores) and hop == UNRESOLVED_HOP
    if rate_limited and not _consume_launch_token(f"launch:{user.user_id}"):
        raise RateLimitedError("Too many campaign launches; slow down and retry shortly.")

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
    """The OPERATOR's number: ``/auth/coupon`` never serves it."""
    return Budget.amount(
        0.0 if ceilings.usd is None else max(0.0, spent.sent_usd - ceilings.usd),
        0 if ceilings.tokens is None else max(0, spent.used_tokens - ceilings.tokens),
    )


class AccountWallet(NamedTuple):
    """One read of an account's coupon: what the box's keys paid for it, its face, and what a launch
    may still hold of it once in-flight reservations are held back."""

    coupon: Coupon
    headroom: Budget
    # Headroom is quoted at zero, not twice: a contended account has money and needs a retry.
    contended: bool = False
    # The excluded campaign's own spend, which `headroom` makes room for: its ceiling counts it.
    history: UserSpend = ZERO_SPEND

    @property
    def spent(self) -> UserSpend:
        return self.coupon.spent

    @property
    def ceilings(self) -> Budget:
        return self.coupon.face

    @property
    def exhausted(self) -> bool:
        """A contended wallet quotes zero and is not exhausted: a launch still being admitted holds it."""
        return not self.contended and (
            self.coupon.void
            or (self.headroom.usd is not None and self.headroom.usd <= self.history.at_most_usd)
            or (
                self.headroom.tokens is not None
                and self.headroom.tokens <= self.history.at_most_tokens
            )
        )

    def refused(self, reason: str, refusing: Job | None) -> HostAllowanceExhaustedError:
        """Names what it refuses off *refusing*'s holder — a campaign, or a paid verb's scoring pass."""
        over = overrun(self.ceilings, self.spent)
        if over != NOTHING_METERED:
            reason += f" It is already {_amounts(over)} past it."
        what = "this launch" if refusing is None else refusing.what
        return HostAllowanceExhaustedError(
            f"{reason} Refused whole, never clamped: {what}. Lower its ceiling, or add your own "
            "key under Account → API keys to run past the host's allowance on your own money."
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
    coupon = read_coupon(stores, account_spend(stores))
    ceilings, spent = coupon.face, coupon.spent
    if ceilings.usd is None and ceilings.tokens is None:
        return AccountWallet(coupon, ceilings)
    history = (
        ZERO_SPEND
        if excluding_campaign is None
        else campaign_spend(stores.campaigns, excluding_campaign).host
    )
    held = _outstanding_reservations(
        job_registry, user_id=user.user_id, excluding_job_id=excluding_job_id
    )
    if held is None:
        return AccountWallet(
            coupon,
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
    return AccountWallet(coupon, headroom, history=history)


class QuotaStatus(StrictModel):
    """Usage against the next launch's machine gates; the money is `/auth/coupon`'s."""

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


def _machine_binds(limit: int) -> str | None:
    ceiling = settings.MACHINE_RUN_CAPACITY
    if limit <= ceiling:
        return None
    return (
        f"This account's limit of {limit} is above the machine's {ceiling}, so the machine is "
        "what binds."
    )


def quota_status(*, stores: Stores, job_registry: JobRegistry) -> QuotaStatus:
    user = account_of(stores)
    # The launches `max_concurrent_cycles` binds: a scoring pass is not among them.
    launches = job_registry.list_launches(user_id=user.user_id)
    return QuotaStatus(
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
    named: frozenset[str | None],
) -> tuple[Budget, Budget]:
    """Refused WHOLE, never clamped. The admitted budget, then what the coupon reserves for it.

    Asked of the providers the run *named*: a run all on the account's own keys draws no coupon;
    one sending anything on the box's keys is refused by an empty one; and only one wholly on them
    is bounded by its room up front — a mixed run's sends on the box's key meet the reserve where
    each is held."""
    wallet = read_account_wallet(
        user=user,
        stores=stores,
        job_registry=job_registry,
        excluding_job_id=job_id,
        excluding_campaign=None if hop is None else hop.campaign_id,
    )
    if wallet.contended:
        raise _contended("what this account has left")

    def _refused(reason: str) -> HostAllowanceExhaustedError:
        return wallet.refused(reason, job_registry.get(job_id))

    if bill_is_floor(wallet.spent.unpriced_tokens) and wallet.headroom.usd is not None:
        logger.warning(
            "spend: account %s has %d unpriced tokens, so its USD total is a floor; admitting "
            "against the $%.2f grace and leaning on the token coupon",
            user.user_id,
            wallet.spent.unpriced_tokens,
            settings.UNPRICED_GRACE_USD,
        )
    room = wallet.headroom
    # A delegate's claim bounds what its launch holds, whatever its campaign declares. Declaring
    # nothing declares no ceiling of the run's own: the coupon's reserve still binds the box's key.
    delegated = _delegated_spend_ceiling(stores)
    admitted = declared.over(Budget(usd=_lowest(declared.usd, delegated), tokens=declared.tokens))
    paying = payers_of(named, stores)
    if "host" in paying and wallet.exhausted:
        raise _refused(wallet.coupon.refusal(None))
    if paying == {"host"}:
        # What the run itself declares: a delegate's cap past the coupon is held by the reserve.
        asked = None if declared.usd is None else admitted.usd
        if room.usd is not None and asked is not None and asked > room.usd:
            raise _refused(
                f"The host's allowance funds ${room.usd:.2f} for this campaign and the run "
                f"declares ${asked:.2f}.",
            )
        if (
            room.tokens is not None
            and admitted.tokens is not None
            and admitted.tokens > room.tokens
        ):
            raise _refused(
                f"The host's allowance funds {room.tokens:,} tokens for this campaign and the "
                f"run declares {admitted.tokens:,}.",
            )
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


def cycle_share(stores: Stores, job: Job) -> Budget:
    """A live job's share, off its cycle: admission lands it there before the job is on the campaign."""
    record = stores.campaigns.read_cycle_share(job.hop)
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
    # Read before any moves: each cycle is re-shared from what the rest held going in.
    held = {job.job_id: cycle_share(stores, job) for job in jobs}
    for job in jobs:
        beside = NOTHING_METERED
        for other in jobs:
            if other.job_id != job.job_id:
                beside = beside.plus(_held(held[other.job_id], own[other.job_id]))
        share = _allotment(_left(moved, spent=spent, beside=beside), own=own[job.job_id])
        hold_moved(stores, job.hop, held[job.job_id].over(share.only(moved.named)))


def hold_moved(stores: Stores, hop: CycleHop, allotment: Budget) -> None:
    """THE move of a cycle's share: its record is the one store, which its running book reads live."""
    held = stores.campaigns.read_cycle_share(hop)
    if held is not None and held.allotment == allotment:
        return
    stores.campaigns.write_cycle_share(hop, allotment)


def land_job(
    stores: Stores, job_registry: JobRegistry, job_id: str, hop: CycleHop, allotment: Budget
) -> None:
    """A job onto the cycle it now spends on, its share with it, together under the gate."""
    with job_registry.admission_gate():
        hold_moved(stores, hop, allotment)
        job_registry.update_target(job_id, hop=hop)


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


def launch_usd_bound(stores: Stores, named: frozenset[str | None]) -> float | None:
    """The most a launch sending on *named* may declare before ``admit_launch`` refuses it whole:
    what a check-in offers."""
    if payers_of(named, stores) != {"host"}:
        return None
    wallet = read_account_wallet(
        user=account_of(stores), stores=stores, job_registry=JobRegistry.attach(stores.jobs_dir)
    )
    return None if wallet.contended else wallet.headroom.usd


def run_providers(config: CampaignConfig, execution: ConnectorExecution) -> frozenset[str | None]:
    """What a run sends on: its optimizer's nodes, and its cells — ``None`` for the box's keys,
    which a remote backend sends on, and an in-process one whose overlay names no provider."""
    optimizer = select_optimizer(config.optimization).schema.declared_nodes
    nodes = frozenset(
        n.provider if n.provider in providers() else None for n in optimizer if n.provider
    )
    cells = named_providers(config.pipeline_overlay) if execution == "in_process" else frozenset()
    return nodes | (cells or frozenset({None}))


def refuse_verb_rate(stores: Stores, user: User, bucket: str) -> None:
    # The host is exempt in the RATE arm too: the meter bounds a stranger on the host's key.
    if not spends_the_hosts_own_key(stores) and not _consume_launch_token(
        f"{bucket}:{user.user_id}"
    ):
        raise RateLimitedError(f"Too many {bucket} requests; slow down and retry shortly.")


def _admit_call(
    *, stores: Stores, job_registry: JobRegistry, bucket: str
) -> tuple[SpendBook, Job | None]:
    """Refused nothing up front: whose key the call goes out on decides
    (`account_key.py::resolve_api_key`), and the book refuses a send past what it holds. What it
    holds is registered as its job's reservation, so a launch or call beside it holds it back."""
    user = account_of(stores)
    refuse_verb_rate(stores, user, bucket)
    with job_registry.admission_gate():
        wallet = read_account_wallet(user=user, stores=stores, job_registry=job_registry)
        if wallet.ceilings.usd is None and wallet.ceilings.tokens is None:
            return unbounded_spend_book(), None
        if wallet.contended:
            raise _contended("what this account has left")
        job = job_registry.create(
            user_id=user.user_id,
            principal_id=acting_principal(stores.identity).id,
            workspace=stores.campaigns.workspace,
            hop=UNRESOLVED_HOP,
            holder=PaidCall(bucket),
        )
        job_registry.mark_admitted(job.job_id, wallet.headroom)
    book = SpendBook(
        declared=Budget(),
        hold=Hold(Budget(), wallet.headroom),
        meters="bill",
        opened=time.monotonic(),
    )
    return book, job


@asynccontextmanager
async def paid_call(*, stores: Stores, bucket: str) -> AsyncIterator[SpendBook]:
    """ONE LLM call on no cycle, slotless so a chat turn never queues behind campaigns."""
    job_registry = JobRegistry.attach(stores.jobs_dir)
    # Offloaded: admission globs every cycle ledger.
    book, job = await asyncio.to_thread(
        _admit_call, stores=stores, job_registry=job_registry, bucket=bucket
    )
    unbind = bind_account(stores)
    try:
        with spending_under(book):
            yield book
    finally:
        unbind()
        if job is not None:
            job_registry.release(job.job_id)


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
    named: frozenset[str | None],
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
    # A campaign ceiling is the operator's: past the coupon only where the account's own keys pay.
    clamp = (
        room.over(Budget(usd=_lowest(room.usd, delegated)))
        if payers_of(named, stores) == {"host"}
        else Budget(usd=delegated)
    )
    change = requested.over(
        Budget.model_validate(
            {
                axis: None
                if (v := getattr(requested, axis)) is None
                else _lowest(v, getattr(clamp, axis))
                for axis in requested.named & MONEY_AXES
            }
        )
    )
    return change, _reserve(change.only(requested.named & MONEY_AXES), room, delegated)


def _reserve(budget: Budget, headroom: Budget, delegated: float | None) -> Budget:
    """What the coupon holds for a run, on the money axes *budget* names: twice its ceiling, at
    most the headroom; the whole headroom where it names none. ``None`` where no coupon bounds it."""
    under = headroom.over(Budget(usd=_lowest(headroom.usd, delegated)))
    held: dict[str, float | int | None] = {}
    for axis in budget.named & MONEY_AXES:
        room, ceiling = getattr(under, axis), getattr(budget, axis)
        held[axis] = None if room is None else room if ceiling is None else min(room, 2 * ceiling)
    return Budget.model_validate(held)


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
            job_registry.set_reserve(job.job_id, job.reserve.over(reserve))
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


def _delegated_spend_ceiling(stores: Stores) -> float | None:
    ceiling = stores.identity.claims.get("spend_ceiling_usd")
    return float(ceiling) if isinstance(ceiling, int | float) else None
