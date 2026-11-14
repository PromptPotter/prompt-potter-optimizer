from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import NamedTuple, assert_never, get_args

from pydantic import Field

from promptpotter import connectors
from promptpotter.application.account import AccountKey, account_of, account_or_opening
from promptpotter.application.free_tier import model_calls, tier_ceiling
from promptpotter.application.jobs.capacity import SlotBound
from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.application.optimizer_manifest import select_optimizer
from promptpotter.config.settings import settings
from promptpotter.domain.campaign_config import CampaignConfig
from promptpotter.domain.cycle_paths import CycleHop, WorkspaceDir
from promptpotter.domain.job import (
    UNRESOLVED_HOP,
    CampaignLaunch,
    EmbeddedRun,
    InnerRun,
    Job,
    JobHolder,
    PaidCall,
    ScoringPass,
)
from promptpotter.domain.records.run_records import (
    CampaignCeilingRecord,
    CycleShareRecord,
    Hold,
    Release,
    StandingBudgetRecord,
)
from promptpotter.domain.said import Said
from promptpotter.domain.spend import (
    CEILING_METER_LABELS,
    CYCLE_AXES,
    MONEY_AXES,
    NOTHING_METERED,
    ZERO_SPEND,
    Budget,
    CeilingMeter,
    KeySource,
    LifetimeSpend,
    MoneyAxis,
    UserSpend,
    bill_is_floor,
    draws_coupon,
    money_text,
)
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.identity.accounts import is_host
from promptpotter.infrastructure.llm.registry import providers
from promptpotter.infrastructure.llm.spend_book import SpendBook
from promptpotter.infrastructure.llm.wallet import (
    ALLOWANCE_STATE_INFO,
    AccountWallet,
    BackendCells,
    ProviderSend,
    Send,
    paying_key,
    provider_send,
    read_coupon,
    unpaid,
)
from promptpotter.infrastructure.store.account_spend import account_bill, campaign_spend
from promptpotter.infrastructure.store.read_model import money_on
from promptpotter.infrastructure.store.stores import Stores, tenant_stores
from promptpotter.infrastructure.store.user_store import User
from promptpotter.shared.errors import (
    BudgetUnenforceableError,
    LaunchRefusalReason,
    NotFoundError,
    PayloadInvalidError,
    PotterError,
    SendRefusedError,
)
from promptpotter.shared.identity import (
    CAMPAIGN_BUDGET_CAP,
    IdentityContext,
    acting_principal,
    has_capability,
)
from promptpotter.shared.worded import Worded

logger = logging.getLogger(__name__)


class QuotaExceededError(PotterError):
    """A user-scoped limit refused a request — 409, a state a retry does not change, as against
    ``PayloadInvalidError``'s 422 for a malformed or unowned request. Only a retryable refusal
    serves 429 (`RateLimitedError`). Both map to one HTTP response through the ``PotterError`` seam."""

    http_status = 409

    def __init__(self, *, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


# Per process: survives the request scope, not a restart, and each server grants a stranger its
# own rate — abuse-bound, not audit-bound.
_rate_buckets: dict[str, tuple[float, float]] = {}  # bucket key → (tokens, last_refill_ts)
_rate_lock = threading.Lock()

# Sized above a person opening every tab in a row, each mount firing its reads at once
# (`e2e/cold/first-run.spec.ts`'s tab sweep is the probe), then two tabs re-asking what the feed moved.
_READ_BURST = 300.0
_READ_PER_S = 20.0


def _refilled(bucket: str, *, burst: float, per_s: float, now: float) -> float:
    """What *bucket* holds at *now*; the caller holds ``_rate_lock``."""
    held, last = _rate_buckets.get(bucket, (burst, now))
    return min(burst, held + (now - last) * per_s)


def _consume_rate_token(bucket: str, *, burst: float, per_s: float) -> bool:
    """One token or none: a refused ask takes nothing from the bucket."""
    now = time.monotonic()
    with _rate_lock:
        held = _refilled(bucket, burst=burst, per_s=per_s, now=now)
        taken = held >= 1
        _rate_buckets[bucket] = (held - 1 if taken else held, now)
        return taken


class _Meter(NamedTuple):
    """An admission's rate bucket: asked before anything can refuse, debited only on admission."""

    bucket: str
    refusal: str


def _new_campaign(stores: Stores, hop: CycleHop) -> bool:
    """A stranger's fresh mint, a check-in's Start included (it names no cycle yet): what the
    launch rate (per server process) and the daily ceiling (per home) meter. The host is exempt: they bound a stranger on its key."""
    return not is_host(stores.identity) and hop == UNRESOLVED_HOP


def _verb_meter(stores: Stores, verb: str) -> _Meter | None:
    if is_host(stores.identity):
        return None
    return _Meter(
        f"{verb}:{stores.identity.user_id}",
        f"Too many {verb} requests; slow down and retry shortly.",
    )


def _slot_meter(stores: Stores, holder: JobHolder, hop: CycleHop) -> _Meter | None:
    match holder:
        case CampaignLaunch():
            if not _new_campaign(stores, hop):
                return None
            return _Meter(
                f"launch:{stores.identity.user_id}",
                "Too many campaign launches; slow down and retry shortly.",
            )
        case ScoringPass(pass_kind=pass_kind):
            return _verb_meter(stores, pass_kind)
        case PaidCall(bucket=bucket):
            return _verb_meter(stores, bucket)
        # Neither asks the slot door: an embedded run is a declared exemption (`admit_slotless`).
        case EmbeddedRun() | InnerRun():
            return None
        case _:
            assert_never(holder)


def _metered_held(meter: _Meter, now: float) -> float:
    return _refilled(
        meter.bucket,
        burst=settings.USER_RATE_BURST,
        per_s=settings.USER_RATE_PER_MIN / 60.0,
        now=now,
    )


def _refuse_metered(meter: _Meter | None, *, asked: int) -> None:
    if meter is None:
        return
    with _rate_lock:
        held = _metered_held(meter, time.monotonic())
    if held < asked:
        raise RateLimitedError(
            meter.refusal, retry_after_s=(asked - held) * 60.0 / settings.USER_RATE_PER_MIN
        )


def _debit_metered(meter: _Meter | None, *, tokens: int) -> None:
    if meter is None:
        return
    now = time.monotonic()
    with _rate_lock:
        _rate_buckets[meter.bucket] = (_metered_held(meter, now) - tokens, now)


@contextmanager
def slot_rate(stores: Stores, holder: JobHolder, hop: CycleHop, *, asked: int) -> Iterator[None]:
    """Refused at the slot door, debited *asked* tokens when its body admits: the caller holds the
    admission gate across both, so no two asks read one bucket; a refused ask spends no token."""
    meter = _slot_meter(stores, holder, hop)
    _refuse_metered(meter, asked=asked)
    yield
    _debit_metered(meter, tokens=asked)


class RateLimitedError(QuotaExceededError):
    """A refusal a retry after ``retry_after_s`` passes: it repeats as fast as it is asked, so the
    trail banks none of it (`main.py::_bank_refusal`). It alone serves 429, with `Retry-After`."""

    http_status = 429

    def __init__(self, message: str, *, retry_after_s: float, code: str = "rate_limited") -> None:
        super().__init__(code=code, message=message)
        self.retry_after_s = retry_after_s
        self.details = {"retry_after_s": round(retry_after_s, 3)}


# Sized above a person working a check-in by hand: each committed edit is one write, none on a timer.
_WRITE_BURST = 60.0
_WRITE_PER_S = 1.0


def refuse_request_rate(identity: IdentityContext, *, read: bool) -> None:
    """A read is what a poll repeats, so it has the wider bucket; a write is every other request, a
    free verb's included. The host is exempt, as from every bucket that meters a stranger. Each
    bucket is this server process's (`_rate_buckets`): N servers over one home grant N times the rate."""
    if is_host(identity):
        return
    principal = acting_principal(identity).id
    if read and not _consume_rate_token(f"read:{principal}", burst=_READ_BURST, per_s=_READ_PER_S):
        raise RateLimitedError(
            "Too many reads, faster than the dashboard polls; slow down and retry shortly.",
            retry_after_s=1 / _READ_PER_S,
        )
    if not read and not _consume_rate_token(
        f"write:{principal}", burst=_WRITE_BURST, per_s=_WRITE_PER_S
    ):
        raise RateLimitedError(
            "Too many changes in a row; slow down and retry shortly.",
            retry_after_s=1 / _WRITE_PER_S,
        )


def check_launch_quotas(
    *, user: User, stores: Stores, job_registry: JobRegistry, hop: CycleHop, launches: int
) -> None:
    """Runs BEFORE ``JobRegistry.request_slot``, same gate: it counts prior launches, then the
    *launches* asked together — a head-to-head's arms, all admitted or none."""
    asking = "" if launches == 1 else f", and this head-to-head starts {launches} at once"

    # A queued launch counts, or an account queues without bound and the drain order can starve.
    # A scoring pass does not: the limit is on campaigns, and a pass that finds no slot is refused.
    in_flight = len(job_registry.list_launches(user_id=user.user_id))
    if in_flight + launches > user.max_concurrent_cycles:
        raise QuotaExceededError(
            code="quota_exceeded",
            message=(
                f"Concurrent-cycles ceiling reached "
                f"({in_flight}/{user.max_concurrent_cycles}{asking}); "
                f"stop or cancel one before starting another."
            ),
        )

    if _new_campaign(stores, hop):
        today = campaigns_today(job_registry, user.user_id)
        if today + launches > user.max_campaigns_per_day:
            raise QuotaExceededError(
                code="quota_exceeded",
                message=(
                    f"Daily campaigns ceiling reached "
                    f"({today}/{user.max_campaigns_per_day} today{asking}); "
                    f"resets at UTC midnight."
                ),
            )


def campaigns_today(job_registry: JobRegistry, user_id: str) -> int:
    """The daily quota's count: a launch admitted or still in flight (`JobRegistry.in_flight`); a
    refused, withdrawn or orphaned one spent nothing."""
    return sum(
        1
        for job in job_registry.list_launched_today(user_id=user_id)
        if job.admitted or job_registry.in_flight(job)
    )


def overrun(face: Budget, spent: UserSpend) -> Budget:
    """The OPERATOR's number: ``/auth/coupon`` never serves it."""
    return Budget.amount(
        0.0 if face.usd is None else max(0.0, spent.sent_usd - face.usd),
        0 if face.tokens is None else max(0, spent.used_tokens - face.tokens),
    )


def _nothing_on(face: Budget) -> Budget:
    """Zero on each unit *face* bounds, unbounded on the rest."""
    return Budget(usd=None if face.usd is None else 0.0, tokens=None if face.tokens is None else 0)


def read_account_wallet(
    *,
    user: User,
    stores: Stores,
    job_registry: JobRegistry,
    excluding_job_id: str | None = None,
    excluding_campaign: str | None = None,
) -> AccountWallet:
    """What the coupon has left past every bill, less what each live run may still draw. A dead
    producer's hold closes at the sweep (`reaper.py::sweep`), never at a read."""
    coupon = read_coupon(stores)
    if coupon.unmetered:
        return AccountWallet(coupon, coupon.face)
    history = (
        ZERO_SPEND
        if excluding_campaign is None
        else campaign_spend(stores.campaigns, stores.charges, excluding_campaign).host
    )
    held = _live_holds(
        stores, job_registry, user_id=user.user_id, excluding_job_id=excluding_job_id
    )
    if held is None:
        return AccountWallet(coupon, _nothing_on(coupon.face), contended=True)
    left, spent = coupon.left, coupon.spent
    headroom = Budget(
        usd=None
        if left.usd is None
        else _grace_bounded(max(0.0, left.usd - held.usd_amount), spent) + history.at_most_usd,
        tokens=None
        if left.tokens is None
        else max(0, left.tokens - held.token_amount) + history.at_most_tokens,
    )
    return AccountWallet(coupon, headroom, history=history, held=held)


def account_wallet(stores: Stores) -> AccountWallet:
    """The wallet a fresh launch on this account is admitted against: what a check-in offers and
    ``/auth/coupon`` serves, so neither can say other than ``admit_launch``."""
    return read_account_wallet(
        user=account_or_opening(stores),
        stores=stores,
        job_registry=JobRegistry.attach(stores.jobs_dir),
    )


def sends_on(stores: Stores, config: CampaignConfig, connector: str) -> tuple[Send, ...]:
    """What a run declaring *config* on *connector* sends, and who pays each: the connector's
    cells as it declares them, then each provider its `model_calls` name, per `paying_key`."""
    keys = stores.api_keys.load()
    calls = model_calls(select_optimizer(config.optimization), config)
    declared = connectors.CONNECTORS.get(connector)
    return (
        BackendCells(connector, declared.cell_payer, declared.cells_priced),
        *(provider_send(keys, provider) for provider in dict.fromkeys(c.provider for c in calls)),
    )


def launch_bound(wallet: AccountWallet, sends: tuple[Send, ...]) -> Budget:
    """The most a launch sending *sends* may declare before ``admit_launch`` refuses it whole."""
    if wallet.contended or not any(draws_coupon(send.payer) for send in sends):
        return Budget()
    return wallet.headroom


LAUNCH_REFUSAL_LABELS: Worded[LaunchRefusalReason, str] = Worded(
    {
        LaunchRefusalReason.USED_UP: "Allowance used up",
        LaunchRefusalReason.HELD: "Allowance held",
        LaunchRefusalReason.OVER_DECLARED: "Ceiling past the allowance",
        LaunchRefusalReason.NO_KEY: "No key for a provider",
        LaunchRefusalReason.UNPRICED: "No rate bounds the cells",
    }
)
assert LAUNCH_REFUSAL_LABELS.keys() == set(LaunchRefusalReason)


class ProviderRoute(StrictModel):
    """Who pays this account's sends on one provider."""

    provider: str
    payer: KeySource | None = Field(
        description="`user` where the account holds a key for it, else `host` where the box "
        "does, under the allowance; null where neither does, so a send on it is refused."
    )
    key: AccountKey | None = Field(description="The account's own key for it, if it holds one.")


class CouponStatus(StrictModel):
    """The host's allowance for this account, and who pays a send on each provider."""

    headline: Said = Field(
        description="The allowance in one line: its state's label, what the box's keys drew of "
        "the face on the unit drawn nearest it (else the state's word), and the providers the "
        "account's own keys pay."
    )
    used_share: float | None = Field(
        description="What is drawn over the face on the unit drawn nearest it, at most 1; null "
        "where no unit is bounded."
    )
    host_key_spend: LifetimeSpend = Field(description="What the box's keys paid for this account.")
    own_key_spend: LifetimeSpend = Field(description="What this account's own keys paid.")
    routes: list[ProviderRoute] = Field(description="Every provider a node may name.")


def _routes(stores: Stores) -> list[ProviderRoute]:
    keys = stores.api_keys.load()
    return [
        ProviderRoute(
            provider=provider,
            payer=None if (paying := paying_key(keys, provider)) is None else paying.paid_by,
            key=None
            if (held := keys.get(provider)) is None
            else AccountKey(provider=provider, last4=held.last4, added_at=held.added_at),
        )
        for provider in sorted(providers())
    ]


def _drawn_of_face(drawn: Budget, face: Budget, binding: MoneyAxis) -> str:
    on = Budget.model_validate({binding: getattr(face, binding)})
    return f"{money_text(drawn, on=on)} of {money_text(on)}"


def coupon_status(stores: Stores) -> CouponStatus:
    paid = account_bill(stores.charges)
    wallet = account_wallet(stores)
    drawn, face = wallet.coupon.drawn, wallet.coupon.face
    shares: dict[MoneyAxis, float] = {
        axis: min(1.0, getattr(drawn, axis) / bound) if bound > 0 else 1.0
        for axis in get_args(MoneyAxis)
        if (bound := getattr(face, axis)) is not None
    }
    binding = max(shares, key=shares.__getitem__, default=None)
    routes = _routes(stores)
    words = ALLOWANCE_STATE_INFO[wallet.state]
    own = [route.provider for route in routes if route.payer == "user"]
    return CouponStatus(
        headline=Said(
            key=f"allowance:{wallet.state}",
            tone=words.tone,
            label=words.label,
            value=words.left_word if binding is None else _drawn_of_face(drawn, face, binding),
            sub=f"your own key pays {', '.join(own)}" if own else None,
            detail=words.lede,
        ),
        used_share=None if binding is None else shares[binding],
        host_key_spend=LifetimeSpend.of(paid.host),
        own_key_spend=LifetimeSpend.of(paid.user),
        routes=routes,
    )


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
        "`set-concurrent-cycles`. False for any caller but the host account with the budget capability."
    )
    ceiling: int = Field(description="The machine's `MACHINE_RUN_CAPACITY`.")
    bound: SlotBound | None = Field(
        description="`machine` while `max_concurrent_cycles` is above `ceiling`; null where the "
        "account's own limit is the one that binds."
    )
    campaigns_today: int
    max_campaigns_per_day: int


def quota_status(*, stores: Stores, job_registry: JobRegistry) -> QuotaStatus:
    user = account_or_opening(stores)
    # The launches `max_concurrent_cycles` binds: a scoring pass is not among them.
    launches = job_registry.list_launches(user_id=user.user_id)
    ceiling = settings.MACHINE_RUN_CAPACITY
    return QuotaStatus(
        concurrent_running=sum(j.holds_slot for j in launches),
        concurrent_queued=sum(j.queued for j in launches),
        max_concurrent_cycles=user.max_concurrent_cycles,
        max_concurrent_cycles_writable=concurrent_cycles_writable(stores),
        ceiling=ceiling,
        bound=SlotBound.MACHINE if user.max_concurrent_cycles > ceiling else None,
        campaigns_today=campaigns_today(job_registry, user.user_id),
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
    sends: tuple[Send, ...],
) -> tuple[Budget, Budget]:
    """Refused WHOLE, never clamped. The admitted budget, then what the coupon reserves for it.

    A run none of whose *sends* draws the coupon is refused nothing here and reserves none of it,
    so a send on the box's key it did not declare is refused where it is held."""
    wallet = read_account_wallet(
        user=user,
        stores=stores,
        job_registry=job_registry,
        excluding_job_id=job_id,
        excluding_campaign=None if hop is None else hop.campaign_id,
    )
    refusal = launch_refusal(
        wallet, declared, sends, stores=stores, refusing=job_registry.get(job_id)
    )
    if refusal is not None:
        raise refusal
    admitted = _admitted(stores, declared, sends)
    if not any(draws_coupon(send.payer) for send in sends):
        return admitted, _nothing_on(wallet.coupon.face)
    if wallet.contended:
        raise _contended("what this account has left")
    spent = wallet.coupon.spent
    if bill_is_floor(spent.unpriced_tokens) and wallet.headroom.usd is not None:
        logger.warning(
            "spend: account %s has %d unpriced tokens, so its USD total is a floor; admitting "
            "against the $%.2f grace and leaning on the token coupon",
            user.user_id,
            spent.unpriced_tokens,
            settings.UNPRICED_GRACE_USD,
        )
    return admitted, _reserve(admitted, wallet.headroom)


def _admitted(stores: Stores, declared: Budget, sends: tuple[Send, ...]) -> Budget:
    """*declared*, capped at the USD one launch of this account may hold: the free tier's ceiling
    on a run any of whose *sends* draws the coupon, so neither a `start-run` budget nor a
    `set-limits` lifts it. An axis the run leaves undeclared stays unnamed, so the reserve takes
    it for no ceiling; declaring nothing reads the cap alone."""
    bound = _tier_bound(stores, sends)
    if bound is None:
        return declared
    return declared.over(Budget(usd=_lowest(declared.usd, bound)))


def _tier_bound(stores: Stores, sends: tuple[Send, ...]) -> float | None:
    """The free tier's dollar ceiling on a run any of whose *sends* draws the coupon."""
    return tier_ceiling(stores) if any(draws_coupon(send.payer) for send in sends) else None


def _unenforceable(
    stores: Stores, sends: tuple[Send, ...], *, usd: float | None, room: Budget
) -> BudgetUnenforceableError | None:
    """THE unenforceable test, of a launch and of a set-limits, as `SpendBook.refuse_unpriced`
    would refuse at round 0: cells no rate bounds under a dollar ceiling, the run's own *usd* or
    the account's allowance (the free tier's cap, or the coupon's dollar *room* on a send that
    draws it). The allowance is named where it binds, since no run can drop it."""
    tier = _tier_bound(stores, sends)
    for send in sends:
        if not isinstance(send, BackendCells) or send.priced:
            continue
        allowance = tier is not None or (draws_coupon(send.payer) and room.usd is not None)
        if allowance or usd is not None:
            return BudgetUnenforceableError(
                backend=send.backend, binds="allowance" if allowance else "run"
            )
    return None


def launch_refusal(
    wallet: AccountWallet,
    declared: Budget,
    sends: tuple[Send, ...],
    *,
    stores: Stores,
    refusing: Job | None,
) -> SendRefusedError | None:
    """THE answer to a launch declaring *declared* and sending *sends*: ``admit_launch`` raises it
    and a check-in serves it. A provider no key pays, then cells no rate bounds under a dollar
    ceiling, then the coupon, which a run drawing none of, or a contended read, refuses nothing;
    each refusal names the first send it binds."""
    for send in sends:
        if isinstance(send, ProviderSend) and send.payer is None:
            return unpaid(send.provider)
    room, admitted = wallet.headroom, _admitted(stores, declared, sends)
    if (unpriced := _unenforceable(stores, sends, usd=declared.usd, room=room)) is not None:
        return unpriced
    drawing = next((send for send in sends if draws_coupon(send.payer)), None)
    if drawing is None or wallet.contended:
        return None
    what = "this launch" if refusing is None else refusing.what
    if wallet.launch_room_gone:
        return wallet.refused(drawing, what=what)
    # What the run itself declares: a delegate's cap past the coupon is held by the reserve.
    asked = None if declared.usd is None else admitted.usd
    if room.usd is not None and asked is not None and asked > room.usd:
        return wallet.refused(drawing, what=what, declares=Budget(usd=asked))
    if room.tokens is not None and admitted.tokens is not None and admitted.tokens > room.tokens:
        return wallet.refused(drawing, what=what, declares=Budget(tokens=admitted.tokens))
    return None


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
                "ceiling "
                "with `set-limits`."
            ),
        )
    return _allotment(left, own=stores.campaigns.cycle_metered(hop, meter))


def _allotment(left: Budget, *, own: Budget) -> Budget:
    return Budget(
        usd=None if left.usd is None else own.usd_amount + left.usd,
        tokens=None if left.tokens is None else own.token_amount + left.tokens,
    )


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


def _admitting_before(other: Job, mine: Job | None) -> bool:
    """*other* is still at its gate, its hold unwritten: only one EARLIER than *mine* blocks, or two
    simultaneous launches refuse each other."""
    return not other.admitted and (
        mine is None or (other.created_at, other.job_id) < (mine.created_at, mine.job_id)
    )


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
        if _admitting_before(other, job):
            raise _contended("this campaign's ceiling")
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
        share = _allotment(moved.less(spent, beside), own=own[job.job_id])
        hold_moved(stores, job.hop, held[job.job_id].over(share.only(moved.named)))


def hold_moved(stores: Stores, hop: CycleHop, allotment: Budget) -> None:
    """THE move of a cycle's share: its record is the one store, which its running book reads live."""
    held = stores.campaigns.last(hop, CycleShareRecord)
    if held is not None and held.allotment == allotment:
        return
    stores.campaigns.append(hop, CycleShareRecord(allotment=allotment))


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
        prior = stores.campaigns.read_campaign_ceiling(campaign.root_hop)
        standing = prior.over(money)
        if standing == prior:
            return
        stores.campaigns.append(campaign.root_hop, CampaignCeilingRecord(budget=standing))
        _reshare_campaign_ceiling(
            money, stores=stores, job_registry=job_registry, campaign_id=campaign_id
        )


def call_book(
    stores: Stores, provider: str, *, job_registry: JobRegistry, hold_id: str
) -> SpendBook:
    """A paid call's book, its sends held under *hold_id*; read under the admission gate.
    Nothing is refused up front: `keyed_client` refuses a send no key pays or one past a used-up
    coupon, and the book one past its headroom."""
    reserve = Budget()
    key = paying_key(stores.api_keys.load(), provider)
    if key is not None and draws_coupon(key.paid_by):
        wallet = read_account_wallet(
            user=account_of(stores),
            stores=stores,
            job_registry=job_registry,
            excluding_job_id=hold_id,
        )
        if wallet.contended:
            raise _contended("what this account has left")
        if not wallet.coupon.unmetered:
            reserve = wallet.headroom
    return SpendBook(
        declared=Budget(),
        share=Budget(),
        charges=stores.charges,
        hold_id=hold_id,
        headroom=reserve,
        metered_on=None,
        meters="bill",
        opened=time.monotonic(),
    )


def declare_run_budget(
    declared: Budget,
    *,
    stores: Stores,
    hop: CycleHop | None,
    requested: Budget,
) -> tuple[Budget, Budget]:
    """*declared* is the cycle's, its seed laid (`CycleConfig`), so composed BEFORE admission;
    second, the axes an operator named (campaign, cycle, *requested*)."""
    asked = Budget()
    if hop is not None:
        if stores.campaigns.load_campaign(hop.campaign_id) is None:
            raise NotFoundError(f"campaign not found: {hop.campaign_id}")
        asked = stores.campaigns.armed_budget(hop)
    asked = asked.over(requested)
    return declared.over(asked), asked


def next_launch_budget(config: CampaignConfig, *, stores: Stores, hop: CycleHop) -> Budget:
    return declare_run_budget(
        config.optimization.budget, stores=stores, hop=hop, requested=Budget()
    )[0]


def change_budget(
    requested: Budget,
    *,
    overshoot: bool | None = None,
    stores: Stores,
    job_registry: JobRegistry,
    hop: CycleHop,
    sends: tuple[Send, ...],
) -> bool:
    """Set-limits is an admission of the changed ceiling: the wallet read, the clamp, the reserve
    and the hold run under the one gate a launch admits under, so no admission between them goes
    unseen. Money moves the CAMPAIGN's ceiling and re-shares it over every live cycle; holds land
    first. Whether the ceiling *hop* runs under, or its overshoot, moved: a change restating them
    moves nothing."""
    campaigns = stores.campaigns
    armed = campaigns.armed_budget(hop)
    change = requested
    with job_registry.admission_gate():
        job = None if (held := campaigns.holder(hop)) is None else held.run
        # Rounds and a clock are no money: a change naming neither money axis reads no wallet.
        if requested.named & MONEY_AXES:
            change, reserve = _clamped(
                requested, stores=stores, job_registry=job_registry, job=job, hop=hop, sends=sends
            )
            if job is not None:
                hold = money_on(stores.charges.path).holds[job.job_id]
                hold_run(stores, job.job_id, hold.bound.over(reserve), parent=hold.parent)
        move_campaign_money(
            change, stores=stores, job_registry=job_registry, campaign_id=hop.campaign_id
        )
    cycle = change.only(CYCLE_AXES)
    prior = campaigns.read_standing_budget(hop)
    switched = overshoot is not None and overshoot != prior.overshoot
    if cycle.named or switched:
        campaigns.append(
            hop,
            StandingBudgetRecord(
                budget=prior.budget.over(cycle),
                pause_at_round=prior.pause_at_round,
                overshoot=prior.overshoot if overshoot is None else overshoot,
            ),
        )
    return switched or campaigns.armed_budget(hop) != armed


def _clamped(
    requested: Budget,
    *,
    stores: Stores,
    job_registry: JobRegistry,
    job: Job | None,
    hop: CycleHop,
    sends: tuple[Send, ...],
) -> tuple[Budget, Budget]:
    """Clamps only a NAMED, bounded money axis: a lift stays a lift, which the reservation binds.
    The change, then what the coupon reserves for it."""
    wallet = read_account_wallet(
        user=account_of(stores),
        stores=stores,
        job_registry=job_registry,
        excluding_job_id=None if job is None else job.job_id,
        excluding_campaign=hop.campaign_id,
    )
    # Refuse rather than clamp: a contended wallet's zero would write a $0 ceiling on a funded run.
    if wallet.contended:
        raise _contended("this campaign's ceiling")
    bound = _admitted(stores, Budget(), sends).usd
    room = wallet.headroom
    # A dollar ceiling its cells cannot be held to is refused as a launch under it is.
    if requested.usd is not None and (
        unpriced := _unenforceable(stores, sends, usd=requested.usd, room=room)
    ):
        raise unpriced
    # A campaign ceiling is the operator's: past the coupon only where none of its sends draws it.
    clamp = (
        room.over(Budget(usd=_lowest(room.usd, bound)))
        if any(draws_coupon(send.payer) for send in sends)
        else Budget(usd=bound)
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
    moved = requested.named & MONEY_AXES
    return change, _reserve(change.only(moved), room).only(moved)


def _reserve(budget: Budget, headroom: Budget) -> Budget:
    """What the coupon holds for a run, per money axis: twice the run's ceiling where it caps the
    axis, at most the headroom; else the whole headroom. A USD ceiling the coupon bounds holds no
    tokens: every send under it is priced (`SpendBook.refuse_unpriced`), so the dollars bound it.
    ``None`` where no coupon bounds the axis."""
    priced = budget.usd is not None and headroom.usd is not None
    held: dict[str, float | int | None] = {}
    for axis in sorted(MONEY_AXES):
        room, ceiling = getattr(headroom, axis), getattr(budget, axis)
        if ceiling is not None:
            held[axis] = None if room is None else min(room, 2 * ceiling)
        elif axis != "tokens" or not priced:
            held[axis] = room
    return Budget.model_validate(held)


def _contended(subject: str) -> RateLimitedError:
    """The ``request_slot``→ADMIT window: retryable, as an exhausted ceiling is not."""
    return RateLimitedError(
        f"Another launch on this account is still being admitted, so {subject} cannot be "
        f"quoted yet. Retry in a moment.",
        retry_after_s=_ADMISSION_WINDOW_S,
        code="launch_contended",
    )


# One admission's slot-to-mark window holds no LLM call, so a second retries past it.
_ADMISSION_WINDOW_S = 1.0


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


def _live_holds(
    stores: Stores, job_registry: JobRegistry, *, user_id: str, excluding_job_id: str | None
) -> Budget | None:
    """What the open holds still hold of the coupon (`Money.held_back`). ``None`` fails closed on
    an unstamped sibling, whose hold is not written yet. Exclusion is by job, never by hop."""
    running = job_registry.list_holding_spend(user_id=user_id)
    own = next((j for j in running if j.job_id == excluding_job_id), None)
    if any(_admitting_before(job, own) for job in running if job.job_id != excluding_job_id):
        return None
    return money_on(stores.charges.path).held_back(excluding_job_id)


def _grace_bounded(remaining: float, spent: UserSpend) -> float:
    """A CEILING on the remainder, so an already-exhausted account gets nothing."""
    if bill_is_floor(spent.unpriced_tokens):
        return min(remaining, settings.UNPRICED_GRACE_USD)
    return remaining


def _lowest(*values: float | None) -> float | None:
    present = [v for v in values if v is not None]
    return min(present) if present else None


def concurrent_cycles_writable(stores: Stores) -> bool:
    return is_host(stores.identity) and has_capability(stores.identity, CAMPAIGN_BUDGET_CAP)


def set_concurrent_cycles(*, stores: Stores, limit: int) -> User:
    """Refused rather than clamped: a limit written lower than asked is a ceiling nobody chose."""
    if not concurrent_cycles_writable(stores):
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
    account_of(stores)
    return stores.users.update(
        lambda user: user.model_copy(update={"max_concurrent_cycles": limit})
    )
