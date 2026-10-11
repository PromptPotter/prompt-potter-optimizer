from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import NamedTuple

from pydantic import Field

from promptpotter.application.campaign_config import CampaignConfig
from promptpotter.application.jobs.registry import UNRESOLVED_HOP, JobRegistry
from promptpotter.config.settings import settings
from promptpotter.domain.cycle_paths import CycleDir, CycleHop
from promptpotter.domain.launch_limits import HeldLimits, LaunchLimits, RoundsCap
from promptpotter.domain.spend import (
    RATE_PRICED_LABEL,
    SpendCeilings,
    bill_is_floor,
    declare_ceiling,
)
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.identity.migration import registered_user_id
from promptpotter.infrastructure.identity.paths import default_identity_paths
from promptpotter.infrastructure.llm.spend_book import (
    SpendBook,
    spending_under,
    unbounded_spend_book,
)
from promptpotter.infrastructure.runtime_flags import derive_run_state, is_checkin
from promptpotter.infrastructure.store.account_spend import (
    ZERO_SPEND,
    LifetimeSpend,
    UserSpend,
    account_ledgers,
    history_spend,
    sum_user_spend,
)
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.infrastructure.store.user_store import User
from promptpotter.shared.errors import ConflictError, PayloadInvalidError, PotterError
from promptpotter.shared.identity import (
    CAMPAIGN_BUDGET_CAP,
    TERMINAL_IDENTITY_ID,
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
    rate_limited = not spends_the_hosts_own_key(stores) and (
        hop == UNRESOLVED_HOP or is_checkin(stores.campaigns.cycle_dir(hop))
    )
    if rate_limited and not _consume_rate_token(user.user_id):
        raise QuotaExceededError(
            code="rate_limited",
            message="Too many campaign launches; slow down and retry shortly.",
        )

    # A queued launch counts, or an account queues without bound and the drain order can starve.
    in_flight = len(job_registry.list_running(user_id=user.user_id)) + len(
        job_registry.list_queued(user_id=user.user_id)
    )
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
        today = job_registry.list_created_today(user_id=user.user_id)
        if len(today) >= user.max_campaigns_per_day:
            raise QuotaExceededError(
                code="quota_exceeded",
                message=(
                    f"Daily campaigns ceiling reached "
                    f"({len(today)}/{user.max_campaigns_per_day} today); "
                    f"resets at UTC midnight."
                ),
            )


def overrun(ceilings: SpendCeilings, spent: UserSpend) -> tuple[float, int]:
    """The OPERATOR's number: ``/quota-status`` never serves it."""
    return (
        0.0 if ceilings.usd is None else max(0.0, spent.sent_usd - ceilings.usd),
        0 if ceilings.tokens is None else max(0, spent.used_tokens - ceilings.tokens),
    )


class AccountWallet(NamedTuple):
    """One read of an account's position: what it has spent, what it answers to, and what a launch
    may still declare once in-flight reservations are held back."""

    spent: UserSpend
    ceilings: SpendCeilings
    headroom: SpendCeilings
    # Headroom is quoted at zero, not twice: a contended account has money and needs a retry.
    contended: bool = False
    # The excluded cycle's own history, which `headroom` makes room for.
    history: UserSpend = ZERO_SPEND

    @property
    def exhausted(self) -> bool:
        """Nothing left past the excluded cycle's history, in either unit. A contended wallet quotes
        zero and is not this: its money is held by a launch still being admitted."""
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
    excluding_hop: CycleHop | None = None,
) -> AccountWallet:
    """A running cycle's spend-so-far is counted twice (ledger + reserve): it errs toward refusing."""
    ceilings = lifetime_ceilings(user=user, spends_own_key=spends_the_hosts_own_key(stores))
    spent = sum_user_spend(ledgers=account_ledgers(stores.campaigns))
    if ceilings.usd is None and ceilings.tokens is None:
        return AccountWallet(spent, ceilings, ceilings)
    history = (
        ZERO_SPEND
        if excluding_hop is None
        else history_spend(CycleDir(stores.campaigns.cycle_dir(excluding_hop)))
    )
    held = _outstanding_reservations(
        job_registry, user_id=user.user_id, excluding_job_id=excluding_job_id
    )
    if held is None:
        return AccountWallet(
            spent,
            ceilings,
            SpendCeilings(
                None if ceilings.usd is None else 0.0,
                None if ceilings.tokens is None else 0,
            ),
            contended=True,
        )
    held_usd, held_tokens = held
    headroom = SpendCeilings(
        None
        if ceilings.usd is None
        else _grace_bounded(max(0.0, ceilings.usd - spent.at_most_usd - held_usd), spent)
        + history.at_most_usd,
        None
        if ceilings.tokens is None
        else max(0, ceilings.tokens - spent.at_most_tokens - held_tokens) + history.at_most_tokens,
    )
    return AccountWallet(spent, ceilings, headroom, history=history)


class QuotaStatus(StrictModel):
    """An account's usage against the limits its next launch is gated on.

    The spend and token pairs are LIFETIME, used-ever against the account's total ceilings; the
    campaign pair is per-day, because one is an allowance and the other an abuse limit.
    """

    spend_lifetime: LifetimeSpend
    spend_budget_usd_total: float | None
    spend_budget_used_share: float | None = Field(
        description="How full the USD meter draws: `spend_lifetime.billed_usd` plus its "
        "`rate_priced_usd`, over `spend_budget_usd_total`, at most 1. `None` where no ceiling "
        "bounds the account."
    )
    allowance_spent: bool = Field(
        description="Admission's own answer: the next launch is refused for want of allowance. "
        "Headroom in either unit, after what running launches hold, the unreported sends and the "
        "unpriced grace. False on an account no ceiling bounds."
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
    user = stores.users.get_or_create(
        user_id=str(stores.identity.user_id),
        tenant_id=str(stores.identity.tenant_id),
        email=stores.identity.email,
    )
    wallet = read_account_wallet(user=user, stores=stores, job_registry=job_registry)
    spent, ceilings = wallet.spent, wallet.ceilings
    return QuotaStatus(
        spend_lifetime=LifetimeSpend.of(spent),
        spend_budget_usd_total=ceilings.usd,
        spend_budget_used_share=_used_share(spent.sent_usd, ceilings.usd),
        allowance_spent=wallet.exhausted,
        tokens_used_total=spent.used_tokens,
        token_budget_total=ceilings.tokens,
        token_budget_used_share=_used_share(spent.used_tokens, ceilings.tokens),
        concurrent_running=len(job_registry.list_running(user_id=user.user_id)),
        concurrent_queued=len(job_registry.list_queued(user_id=user.user_id)),
        max_concurrent_cycles=user.max_concurrent_cycles,
        max_concurrent_cycles_writable=concurrent_cycles_writable(stores),
        machine_binds=_machine_binds(user.max_concurrent_cycles),
        campaigns_today=len(job_registry.list_created_today(user_id=user.user_id)),
        max_campaigns_per_day=user.max_campaigns_per_day,
    )


def admit_launch(
    *,
    declared: SpendCeilings,
    user: User,
    stores: Stores,
    job_registry: JobRegistry,
    job_id: str,
    hop: CycleHop | None,
) -> tuple[SpendCeilings, SpendCeilings]:
    """Refused WHOLE, never clamped: a clamped launch starts, spends and halts mid-campaign."""
    wallet = read_account_wallet(
        user=user,
        stores=stores,
        job_registry=job_registry,
        excluding_job_id=job_id,
        excluding_hop=hop,
    )
    if wallet.contended:
        raise _contended("what this account has left")
    if bill_is_floor(wallet.spent.unpriced_tokens) and wallet.headroom.usd is not None:
        logger.warning(
            "spend: account %s has %d unpriced tokens, so its USD total is a floor; admitting "
            "against the $%.2f grace and leaning on the token ceiling",
            user.user_id,
            wallet.spent.unpriced_tokens,
            settings.UNPRICED_GRACE_USD,
        )
    if wallet.exhausted:
        raise _refused(wallet, "This account has nothing left to spend.")
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
                wallet,
                f"A run on this account is admitted at ${step:.2f} and this one declares "
                f"${usd:.2f}.",
            )
        if room.usd is not None and usd is not None and usd > room.usd:
            raise _refused(
                wallet,
                f"This account funds a ceiling of ${room.usd:.2f} for this cycle and the run "
                f"declares ${usd:.2f}.",
            )
    if room.tokens is not None:
        tokens = room.tokens if tokens is None else tokens
        if tokens > room.tokens:
            raise _refused(
                wallet,
                f"This account funds a ceiling of {room.tokens:,} tokens for this cycle and the "
                f"run declares {tokens:,}.",
            )
    ceiling = SpendCeilings(usd, tokens)
    return ceiling, _reserve(ceiling, room, delegated)


def _admit_spend(*, stores: Stores, bucket: str) -> SpendBook:
    """Nothing is reserved: the book itself refuses a call that would pass the headroom."""
    user = stores.users.get_or_create(
        user_id=str(stores.identity.user_id), tenant_id=str(stores.identity.tenant_id)
    )
    spends_own_key = spends_the_hosts_own_key(stores)
    # The host is exempt in the RATE arm too: the meter bounds a stranger on the host's key.
    if not spends_own_key and not _consume_rate_token(f"{bucket}:{user.user_id}"):
        raise QuotaExceededError(
            code="rate_limited",
            message=f"Too many {bucket} requests; slow down and retry shortly.",
        )
    ceilings = lifetime_ceilings(user=user, spends_own_key=spends_own_key)
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
    return SpendBook(declared=headroom, reserved=headroom, meters="bill")


@asynccontextmanager
async def paid_verb(
    *, stores: Stores, bucket: str, hop: CycleHop | None
) -> AsyncIterator[SpendBook]:
    """A cycle a producer holds is refused: a second biller is money its ceiling never sees."""
    if hop is not None and derive_run_state(stores.campaigns.cycle_dir(hop)).producer.attached:
        raise ConflictError(
            f"cycle {hop.cycle_id} has a run in flight. Pause it or let it end, then ask again.",
            code="producer_live",
        )
    # Offloaded: admission globs every cycle ledger.
    book = await asyncio.to_thread(_admit_spend, stores=stores, bucket=bucket)
    with spending_under(book):
        yield book


def declare_run_ceiling(
    config: CampaignConfig,
    *,
    stores: Stores,
    hop: CycleHop | None,
    requested: SpendCeilings,
) -> tuple[SpendCeilings, SpendCeilings]:
    """Composed BEFORE admission: after it, the knob is a bound no launch flag can raise.
    Second is the arms an operator gesture declared, standing or *requested*."""
    seed = SpendCeilings()
    standing = SpendCeilings()
    if hop is not None:
        cycle_seed = stores.campaigns.read_cycle_seed(hop)
        if cycle_seed is not None:
            seed = cycle_seed.config_overrides.ceiling
        standing = stores.campaigns.read_run_limits(hop).ceiling
    operator = declare_ceiling(standing, requested)
    return declare_ceiling(config.optimization.ceiling, seed, operator), operator


def next_launch_ceiling(config: CampaignConfig, *, stores: Stores, hop: CycleHop) -> SpendCeilings:
    return declare_run_ceiling(config, stores=stores, hop=hop, requested=SpendCeilings())[0]


def unadmitted_limits(
    config: CampaignConfig,
    *,
    stores: Stores,
    hop: CycleHop | None,
    requested: LaunchLimits,
) -> HeldLimits:
    """For the entries that hold no slot BY DESIGN: an embedded run and an L4 inner cell."""
    declared, operator = declare_run_ceiling(
        config, stores=stores, hop=hop, requested=requested.ceiling
    )
    return HeldLimits.admitted(requested, declared, operator, reserve=SpendCeilings())


def clamp_budget_change(
    *,
    requested: SpendCeilings,
    user: User,
    stores: Stores,
    job_registry: JobRegistry,
    hop: CycleHop,
) -> tuple[SpendCeilings, SpendCeilings]:
    """Clamps where a launch refuses, and only a SUPPLIED arm composes: an absent one stays absent."""
    held = job_registry.running_job_for(hop)
    wallet = read_account_wallet(
        user=user,
        stores=stores,
        job_registry=job_registry,
        excluding_job_id=None if held is None else held.job_id,
        excluding_hop=hop,
    )
    # Refuse rather than clamp: a contended wallet's zero would write a $0 ceiling on a funded run.
    if wallet.contended:
        raise _contended("this cycle's ceiling")
    delegated = _delegated_spend_ceiling(stores)
    room = wallet.headroom
    usd = (
        None
        if requested.usd is None
        else _lowest(requested.usd, room.usd, delegated, _launch_step(user, wallet, delegated))
    )
    tokens = requested.tokens
    if tokens is not None and room.tokens is not None:
        tokens = min(tokens, room.tokens)
    change = SpendCeilings(usd, tokens)
    return change, _reserve(change, room, delegated)


def _reserve(
    ceiling: SpendCeilings, headroom: SpendCeilings, delegated: float | None
) -> SpendCeilings:
    """``None`` on an arm no account bounds, or one *ceiling* leaves alone."""
    usd = _lowest(headroom.usd, delegated)
    tokens = headroom.tokens
    return SpendCeilings(
        None if usd is None or ceiling.usd is None else min(usd, 2 * ceiling.usd),
        None if tokens is None or ceiling.tokens is None else min(tokens, 2 * ceiling.tokens),
    )


def hold_run_limits(
    *,
    job_registry: JobRegistry,
    stores: Stores,
    hop: CycleHop,
    change: SpendCeilings,
    reserve: SpendCeilings,
    rounds: RoundsCap | None,
) -> None:
    """The job's reservation lands first: the account commits the money before the run holds it."""
    held = SpendCeilings()
    job = job_registry.running_job_for(hop)
    if job is not None:
        held = declare_ceiling(job.reserve, reserve)
        job_registry.set_reserve(job.job_id, held)
    prior = stores.campaigns.read_run_limits(hop)
    stores.campaigns.write_run_limits(
        hop,
        declare_ceiling(prior.ceiling, change),
        rounds=prior.rounds if rounds is None else rounds,
        pause_at_round=prior.pause_at_round,
        reserve=held,
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


def _refused(wallet: AccountWallet, reason: str) -> QuotaExceededError:
    over_usd, over_tokens = overrun(wallet.ceilings, wallet.spent)
    if over_usd or over_tokens:
        reason += f" It is already ${over_usd:.4f} / {over_tokens:,} tokens past its ceiling."
    return QuotaExceededError(
        code="spend_ceiling_reached",
        message=(
            f"{reason} A campaign is admitted whole or not at all, so lower the run's budget or "
            f"raise the account ceiling."
        ),
    )


def _outstanding_reservations(
    job_registry: JobRegistry, *, user_id: str, excluding_job_id: str | None
) -> tuple[float, int] | None:
    """``None`` fails closed on an admitted, unstamped sibling. Exclusion is by job, never by hop."""
    running = job_registry.list_running(user_id=user_id)
    own = next((j for j in running if j.job_id == excluding_job_id), None)
    mine = None if own is None else (own.created_at, own.job_id)
    usd = 0.0
    tokens = 0
    for job in running:
        if job.job_id == excluding_job_id:
            continue
        if job.admitted_at is None:
            # Only an EARLIER unstamped sibling blocks, or two simultaneous launches refuse each other.
            if mine is None or (job.created_at, job.job_id) < mine:
                return None
            continue
        usd += job.reserve.usd or 0.0
        tokens += job.reserve.tokens or 0
    return usd, tokens


def _grace_bounded(remaining: float, spent: UserSpend) -> float:
    """A CEILING on the remainder, so an already-exhausted account gets nothing."""
    if bill_is_floor(spent.unpriced_tokens):
        return min(remaining, settings.UNPRICED_GRACE_USD)
    return remaining


def _lowest(*values: float | None) -> float | None:
    present = [v for v in values if v is not None]
    return min(present) if present else None


def lifetime_ceilings(*, user: User, spends_own_key: bool) -> SpendCeilings:
    """The host is exempt in both arms: metering bounds a STRANGER on the host's key."""
    if spends_own_key:
        return SpendCeilings()
    usd = user.spend_budget_usd_total
    tokens = user.token_budget_total
    return SpendCeilings(
        usd if usd is not None else settings.FREE_TIER_SPEND_CAP_USD,
        tokens if tokens is not None else settings.FREE_TIER_TOKEN_CAP,
    )


def _is_host(*, terminal: bool, user_id: str) -> bool:
    """Terminal detection is passed IN: merged, an identity omitting an issuer reads as the host."""
    claimed = registered_user_id(default_identity_paths().default_claim_marker)
    return terminal or (claimed is not None and user_id == claimed)


def spends_the_hosts_own_key(stores: Stores) -> bool:
    return _is_host(terminal=stores.identity.issuer is None, user_id=str(stores.identity.user_id))


def is_host_tenant_dir(user_id: str) -> bool:
    """The DIRECTORY-walk reading: no issuer survives on disk."""
    return _is_host(terminal=user_id == TERMINAL_IDENTITY_ID, user_id=user_id)


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
    user = stores.users.get_or_create(
        user_id=str(stores.identity.user_id),
        tenant_id=str(stores.identity.tenant_id),
        email=stores.identity.email,
    )
    updated = user.model_copy(update={"max_concurrent_cycles": limit})
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


__all__ = [
    "AccountWallet",
    "QuotaExceededError",
    "QuotaStatus",
    "admit_launch",
    "check_launch_quotas",
    "clamp_budget_change",
    "concurrent_cycles_writable",
    "declare_run_ceiling",
    "hold_run_limits",
    "is_host_tenant_dir",
    "lifetime_ceilings",
    "next_launch_ceiling",
    "overrun",
    "paid_verb",
    "quota_status",
    "read_account_wallet",
    "set_concurrent_cycles",
    "spends_the_hosts_own_key",
    "unadmitted_limits",
]
