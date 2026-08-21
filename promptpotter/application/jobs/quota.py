"""Per-user quota + abuse-limit gates fired from the launcher. A launch is admitted at the ceiling it
declares or refused outright, and holds that ceiling as a reservation for as long as it runs."""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import NamedTuple

from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.config.settings import settings
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.infrastructure.identity.migration import registered_user_id
from promptpotter.infrastructure.identity.paths import default_identity_paths
from promptpotter.infrastructure.runtime_flags import read_spend_caps, write_spend_caps
from promptpotter.infrastructure.store.account_spend import (
    UserSpend,
    account_ledgers,
    sum_user_spend,
)
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.infrastructure.store.user_store import User
from promptpotter.shared.errors import PotterError
from promptpotter.shared.identity import TERMINAL_IDENTITY_ID

logger = logging.getLogger(__name__)


class QuotaExceededError(PotterError):
    """A user-scoped abuse limit blocked a launch — 429, as against ``LaunchError``'s 422 for a malformed
    or unowned request. Both map to one HTTP response through the ``PotterError`` seam."""

    http_status = 429

    def __init__(self, *, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


# Token bucket, sized by ``USER_RATE_BURST`` / ``USER_RATE_PER_MIN``. Process-wide module state;
# it survives the request scope but not a restart, which is what abuse-bound rather than
# audit-bound means.
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
    *,
    user: User,
    job_registry: JobRegistry,
    rate_limited: bool = True,
) -> None:
    """Per-USER limits only: rate, concurrent cycles, campaigns per day. The global one-campaign-at-a-time
    admission rides ``JobRegistry.reserve``; this runs BEFORE it, so it counts prior runs, not this one."""
    if rate_limited and not _consume_rate_token(user.user_id):
        raise QuotaExceededError(
            code="rate_limited",
            message="Too many campaign launches; slow down and retry shortly.",
        )

    running = job_registry.list_running(user_id=user.user_id)
    if len(running) >= user.max_concurrent_cycles:
        raise QuotaExceededError(
            code="quota_exceeded",
            message=(
                f"Concurrent-cycles ceiling reached "
                f"({len(running)}/{user.max_concurrent_cycles}); "
                f"stop a running cycle before starting another."
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


class SpendCeilings(NamedTuple):
    """The two units a run is metered in; ``None`` on an arm means unmetered."""

    usd: float | None
    tokens: int | None

    def overrun(self, spent: UserSpend) -> tuple[float, int]:
        """What went past these ceilings anyway. Admission bounds what a run may DECLARE, not what a
        round boundary overshoots or an unpriced call turns out to have cost — so this is the residue
        neither arm could refuse, and it is the OPERATOR's number: ``/quota-status`` never serves
        it."""
        return (
            0.0 if self.usd is None else max(0.0, spent.used_usd - self.usd),
            0 if self.tokens is None else max(0, spent.used_tokens - self.tokens),
        )


class AccountWallet(NamedTuple):
    """One read of an account's position: what it has spent, what it answers to, and what a launch
    may still declare once in-flight reservations are held back."""

    spent: UserSpend
    ceilings: SpendCeilings
    headroom: SpendCeilings


def read_account_wallet(
    *,
    user: User,
    stores: Stores,
    job_registry: JobRegistry,
    excluding_hop: CycleHop | None = None,
) -> AccountWallet:
    """A running cycle holds its whole declared ceiling until it finishes: counting only what is on
    the ledger admits two concurrent launches against one remainder and lets the pair spend double
    the ceiling. Its spend-so-far is therefore counted twice, which errs toward refusing — the safe
    direction for a wallet the account cannot top up."""
    ceilings = lifetime_ceilings(user=user, spends_own_key=spends_the_hosts_own_key(stores))
    spent = sum_user_spend(ledgers=account_ledgers(stores.campaigns), since=0.0, until=time.time())
    if ceilings.usd is None and ceilings.tokens is None:
        return AccountWallet(spent, ceilings, ceilings)
    held_usd, held_tokens = _outstanding_reservations(
        job_registry, user_id=user.user_id, excluding_hop=excluding_hop
    )
    headroom = SpendCeilings(
        None
        if ceilings.usd is None
        else _grace_bounded(max(0.0, ceilings.usd - spent.used_usd - held_usd), spent),
        None
        if ceilings.tokens is None
        else max(0, ceilings.tokens - spent.used_tokens - held_tokens),
    )
    return AccountWallet(spent, ceilings, headroom)


def admit_launch(
    *,
    requested_cap_usd: float | None,
    requested_cap_tokens: int | None,
    user: User,
    stores: Stores,
    job_registry: JobRegistry,
) -> SpendCeilings:
    """**One host-wallet gate in two units** — owned by
    [`0003-spend-and-tenancy.md`](../../../docs/adr/0003-spend-and-tenancy.md) § D1; every launch
    admits through here. A declaration the account cannot cover is refused WHOLE rather than clamped
    down, because a clamped launch starts, spends and halts mid-campaign — the outcome the ceiling
    exists to prevent, not to cause. Declaring nothing declares the headroom under whatever bounds
    the DECLARATION — for a metered account, one step of it (:func:`_launch_step`)."""
    wallet = read_account_wallet(user=user, stores=stores, job_registry=job_registry)
    if wallet.spent.unpriced_tokens and wallet.headroom.usd is not None:
        logger.warning(
            "spend: account %s has %d unpriced tokens, so its USD total is a floor; admitting "
            "against the $%.2f grace and leaning on the token ceiling",
            user.user_id,
            wallet.spent.unpriced_tokens,
            settings.UNPRICED_GRACE_USD,
        )
    if (wallet.headroom.usd is not None and wallet.headroom.usd <= 0.0) or (
        wallet.headroom.tokens is not None and wallet.headroom.tokens <= 0
    ):
        raise _refused(wallet, "This account has nothing left to spend.")
    delegated = _delegated_spend_ceiling(stores)
    step = _launch_step(user, wallet, delegated)
    tokens = requested_cap_tokens
    if requested_cap_usd is None:
        # A grant bounds what may be DECLARED, so declaring nothing declares the headroom under it
        # — never the grant itself, which would refuse an account that can still afford the run.
        usd = _lowest(wallet.headroom.usd, delegated, step)
    else:
        usd = _lowest(requested_cap_usd, delegated)
        if step is not None and usd is not None and usd > step:
            raise _refused(
                wallet,
                f"A run on this account is admitted at ${step:.2f} and this one declares "
                f"${usd:.2f}.",
            )
        if wallet.headroom.usd is not None and usd is not None and usd > wallet.headroom.usd:
            raise _refused(
                wallet,
                f"This account has ${wallet.headroom.usd:.2f} left and the run declares "
                f"${usd:.2f}.",
            )
    if wallet.headroom.tokens is not None:
        tokens = wallet.headroom.tokens if tokens is None else tokens
        if tokens > wallet.headroom.tokens:
            raise _refused(
                wallet,
                f"This account has {wallet.headroom.tokens:,} tokens left and the run declares "
                f"{tokens:,}.",
            )
    return SpendCeilings(usd, tokens)


def admit_llm_turn(*, stores: Stores) -> None:
    """A one-shot optimizer call outside any run — today the origin resolver, which spends the host's
    key before a campaign exists. It declares no budget, so there is nothing to reserve and nothing
    to clamp; the only question is whether the account has anything left at all. Its rate bucket is
    keyed apart from the launch one, or a conversation would starve the verb it leads to."""
    user = stores.users.get_or_create(
        user_id=str(stores.identity.user_id), tenant_id=str(stores.identity.tenant_id)
    )
    spends_own_key = spends_the_hosts_own_key(stores)
    # Exempt in the RATE arm too: the free-tier meter exists to bound a stranger on the host's
    # key, and counting the host's own turns per minute meters them against their own money.
    if not spends_own_key and not _consume_rate_token(f"turn:{user.user_id}"):
        raise QuotaExceededError(
            code="rate_limited",
            message="Too many resolver turns; slow down and retry shortly.",
        )
    ceilings = lifetime_ceilings(user=user, spends_own_key=spends_own_key)
    if ceilings.usd is None and ceilings.tokens is None:
        return
    spent = sum_user_spend(ledgers=account_ledgers(stores.campaigns), since=0.0, until=time.time())
    if (ceilings.usd is not None and spent.used_usd >= ceilings.usd) or (
        ceilings.tokens is not None and spent.used_tokens >= ceilings.tokens
    ):
        raise QuotaExceededError(
            code="spend_ceiling_reached",
            message=(
                f"This account has spent its allowance (${spent.used_usd:.2f} / "
                f"{spent.used_tokens:,} tokens), so nothing further runs on the host's key."
            ),
        )


def clamp_budget_change(
    *,
    max_usd: float | None,
    max_tokens: int | None,
    user: User,
    stores: Stores,
    job_registry: JobRegistry,
    hop: CycleHop,
) -> SpendCeilings:
    """Moving a RUNNING cycle's ceiling clamps where a launch refuses: the campaign is already
    admitted, so the only question is how far the operator may move it, and lowering one must always
    work. The cycle's own reservation is excluded, or it would be denied headroom it holds itself.

    Only a SUPPLIED arm composes: folded into an absent one, a delegate's grant becomes a ceiling
    the caller asked to leave alone, and the file merge downstream makes it stick."""
    wallet = read_account_wallet(
        user=user, stores=stores, job_registry=job_registry, excluding_hop=hop
    )
    delegated = _delegated_spend_ceiling(stores)
    usd = (
        None
        if max_usd is None
        else _lowest(max_usd, wallet.headroom.usd, delegated, _launch_step(user, wallet, delegated))
    )
    tokens = max_tokens
    if tokens is not None and wallet.headroom.tokens is not None:
        tokens = min(tokens, wallet.headroom.tokens)
    return SpendCeilings(usd, tokens)


def hold_ceiling(
    *,
    job_registry: JobRegistry,
    hop: CycleHop,
    cycle_dir: Path,
    max_usd: float | None,
    max_tokens: int | None,
) -> SpendCeilings:
    """Land a moved ceiling on BOTH homes it has to be true in, from ONE prior.

    A running cycle's ceiling lives twice on purpose: the JOB carries what the account has committed
    while the run is in flight — the only home a mint has, since it reserves its slot before its
    cycle exists — and ``spend_cap.json`` carries what the run may spend right now, the probe the
    ``BudgetGate`` re-reads. An ABSENT arm means "leave it alone", so each home needs a prior to
    leave alone, and **the two priors are not interchangeable**: the job's pair is complete from
    admission, the file's starts empty and reads an untouched arm as unmetered. So the running job
    is the prior and the file its projection, written whole."""
    job = job_registry.running_job_for(hop)
    prior = (
        SpendCeilings(job.cap_usd, job.cap_tokens)
        if job is not None
        else SpendCeilings(*read_spend_caps(cycle_dir))
    )
    moved = SpendCeilings(
        prior.usd if max_usd is None else max_usd,
        prior.tokens if max_tokens is None else max_tokens,
    )
    write_spend_caps(cycle_dir, usd=moved.usd, tokens=moved.tokens)
    if job is not None:
        job_registry.set_caps(job.job_id, cap_usd=moved.usd, cap_tokens=moved.tokens)
    return moved


def _refused(wallet: AccountWallet, reason: str) -> QuotaExceededError:
    """Every refusal names the overrun, which is where the operator reads that a ceiling was
    CROSSED rather than merely reached."""
    over_usd, over_tokens = wallet.ceilings.overrun(wallet.spent)
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
    job_registry: JobRegistry, *, user_id: str, excluding_hop: CycleHop | None
) -> tuple[float, int]:
    """What this account's in-flight runs may still spend. A job admitted but not yet stamped holds
    nothing, a window the capacity-1 machine slot covers — raising ``MACHINE_RUN_CAPACITY`` is what
    would make it matter."""
    usd = 0.0
    tokens = 0
    for job in job_registry.list_running(user_id=user_id):
        if excluding_hop is not None and job.hop == excluding_hop:
            continue
        usd += job.cap_usd or 0.0
        tokens += job.cap_tokens or 0
    return usd, tokens


def _grace_bounded(remaining: float, spent: UserSpend) -> float:
    """An account whose USD total is known to be understated may still declare the grace and never
    more — a CEILING on the remainder, so an already-exhausted one gets nothing."""
    return min(remaining, settings.UNPRICED_GRACE_USD) if spent.unpriced_tokens else remaining


def _lowest(*values: float | None) -> float | None:
    present = [v for v in values if v is not None]
    return min(present) if present else None


def lifetime_ceilings(*, user: User, spends_own_key: bool) -> SpendCeilings:
    """The total-spend ceilings this account answers to. Free-tier metering exists to bound a
    STRANGER spending the host's provider key, so the person running the box is exempt in both arms
    — metering them would cap the operator against their own money."""
    if spends_own_key:
        return SpendCeilings(None, None)
    usd = user.spend_budget_usd_total
    tokens = user.token_budget_total
    return SpendCeilings(
        usd if usd is not None else settings.FREE_TIER_SPEND_CAP_USD,
        tokens if tokens is not None else settings.FREE_TIER_TOKEN_CAP,
    )


def _is_host(*, terminal: bool, user_id: str) -> bool:
    """The one definition of "this is the operator of the box, not a free-tier signup". The host is
    reached two ways — from the terminal, or as the identity that CLAIMED the box — and only the
    second is the same question for every caller. **How the terminal is detected is passed IN, and
    that is the whole security boundary:** a live identity proves it by carrying no issuer, a
    directory walk by the un-renamed ``projects/default/``. Merging the two detectors would let an
    identity that merely omits an issuer resolve as the operator, which is the trap the anonymous
    tier is specced around (``docs/specs/roadmap.md``)."""
    claimed = registered_user_id(default_identity_paths().default_claim_marker)
    return terminal or (claimed is not None and user_id == claimed)


def spends_the_hosts_own_key(stores: Stores) -> bool:
    """The LIVE-identity reading: metering exists to bound a stranger spending the host's provider
    key, so the person running the box is exempt. On-disk twin: :func:`is_host_tenant_dir`."""
    return _is_host(terminal=stores.identity.issuer is None, user_id=str(stores.identity.user_id))


def is_host_tenant_dir(user_id: str) -> bool:
    """The DIRECTORY-walk reading, for the cross-tenant install report, which has tenant dirs rather
    than sessions and no issuer surviving on disk. Live twin: :func:`spends_the_hosts_own_key`."""
    return _is_host(terminal=user_id == TERMINAL_IDENTITY_ID, user_id=user_id)


def _launch_step(user: User, wallet: AccountWallet, delegated: float | None) -> float | None:
    """The most ONE run on the ANONYMOUS grant may declare, whatever its headroom. The offer is
    denominated in runs, and a single run declaring the rest of the grant leaves the others
    unfunded — so the ceiling divides into steps and each launch takes one.

    It rations that grant and nothing else, so three accounts fall outside it: the operator of the
    box, who is not metered at all; a DELEGATED principal, whose attenuated ceiling (ADR-0005) is a
    narrower authority granted on purpose; and one the operator hand-raised on ``user.json``, where
    stepping the allowance they just wrote would quietly undo it."""
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
    "SpendCeilings",
    "admit_launch",
    "admit_llm_turn",
    "check_launch_quotas",
    "clamp_budget_change",
    "hold_ceiling",
    "is_host_tenant_dir",
    "lifetime_ceilings",
    "read_account_wallet",
    "spends_the_hosts_own_key",
]
