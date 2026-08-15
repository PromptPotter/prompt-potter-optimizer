"""Per-user quota + abuse-limit gates fired from the launcher. At mint the per-cycle cap collapses to
``min(requested, lifetime_ceiling − spent_ever)``, so stacking N small caps cannot outrun the ceiling."""

from __future__ import annotations

import logging
import threading
import time
from typing import NamedTuple

from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.application.jobs.spend import sum_user_spend
from promptpotter.config.settings import settings
from promptpotter.infrastructure.identity.migration import registered_user_id
from promptpotter.infrastructure.identity.paths import default_identity_paths
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.infrastructure.store.user_store import User
from promptpotter.shared.errors import PotterError

logger = logging.getLogger(__name__)


class QuotaExceededError(PotterError):
    """A user-scoped abuse limit blocked a launch — 429, as against ``LaunchError``'s 422 for a malformed
    or unowned request. Both map to one HTTP response through the ``PotterError`` seam."""

    http_status = 429

    def __init__(self, *, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


# Token-bucket rate limiter — refilled at ``_RATE_REFILL_PER_SEC`` per user.
# Process-wide module state; survives the request scope but not a restart
# (rate limits are abuse-bound, not audit-bound; a restart resets is fine).
_RATE_CAPACITY = 5  # burst allowance
_RATE_REFILL_PER_SEC = 1.0 / 60.0  # one mint/min sustained
_rate_buckets: dict[str, tuple[float, float]] = {}  # user_id → (tokens, last_refill_ts)
_rate_lock = threading.Lock()


def _consume_rate_token(user_id: str) -> bool:
    now = time.monotonic()
    with _rate_lock:
        tokens, last = _rate_buckets.get(user_id, (float(_RATE_CAPACITY), now))
        tokens = min(float(_RATE_CAPACITY), tokens + (now - last) * _RATE_REFILL_PER_SEC)
        if tokens < 1.0:
            _rate_buckets[user_id] = (tokens, now)
            return False
        _rate_buckets[user_id] = (tokens - 1.0, now)
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


def effective_launch_caps(
    *,
    requested_cap_usd: float | None,
    requested_cap_tokens: int | None,
    user: User,
    stores: Stores,
) -> SpendCeilings:
    """**One host-wallet gate in two units** — owned by
    [`0003-spend-and-tenancy.md`](../../../docs/adr/0003-spend-and-tenancy.md) § D1; every path that
    sets a ceiling composes here. A remainder collapses to zero rather than going negative, and the
    ceilings being LIFETIME ones, a run halted on one stays halted.
    """
    usd_caps = [c for c in (requested_cap_usd, _delegated_spend_ceiling(stores)) if c is not None]
    token_caps = [] if requested_cap_tokens is None else [requested_cap_tokens]
    ceilings = lifetime_ceilings(user=user, stores=stores)
    if ceilings.usd is not None or ceilings.tokens is not None:
        spent = sum_user_spend(stores=stores, since=0.0, until=time.time())
        if ceilings.usd is not None:
            remaining = max(0.0, ceilings.usd - spent.used_usd)
            if spent.unpriced_tokens:
                remaining = min(remaining, settings.UNPRICED_GRACE_USD)
                logger.warning(
                    "spend: account %s has %d unpriced tokens, so its USD total is a floor; "
                    "capping this launch at the $%.2f grace and leaning on the token ceiling",
                    user.user_id,
                    spent.unpriced_tokens,
                    settings.UNPRICED_GRACE_USD,
                )
            usd_caps.append(remaining)
        if ceilings.tokens is not None:
            token_caps.append(max(0, ceilings.tokens - spent.used_tokens))
    return SpendCeilings(
        min(float(c) for c in usd_caps) if usd_caps else None,
        min(token_caps) if token_caps else None,
    )


def lifetime_ceilings(*, user: User, stores: Stores) -> SpendCeilings:
    """The total-spend ceilings this account answers to, or ``None`` arms when it answers to none.

    Free-tier metering exists to bound a STRANGER spending the host's provider key — that is the whole
    trade for making signup the grant. The person running the box is not that stranger, so metering them
    would cap the operator against their own money, which is what a shared default would silently do to
    every terminal run on every install.
    """
    if _spends_the_hosts_own_key(stores):
        return SpendCeilings(None, None)
    usd = user.spend_budget_usd_total
    tokens = user.token_budget_total
    return SpendCeilings(
        usd if usd is not None else settings.FREE_TIER_SPEND_CAP_USD,
        tokens if tokens is not None else settings.FREE_TIER_TOKEN_CAP,
    )


def _spends_the_hosts_own_key(stores: Stores) -> bool:
    """Is this identity the operator of the box rather than a free-tier signup? One question, two arms it
    can arrive by: an identity with no issuer came through the terminal, which only the operator reaches;
    an OIDC identity matching the claim marker is that same operator arriving by browser."""
    if stores.identity.issuer is None:
        return True
    claimed = registered_user_id(default_identity_paths().default_claim_marker)
    return claimed is not None and str(stores.identity.user_id) == claimed


def _delegated_spend_ceiling(stores: Stores) -> float | None:
    ceiling = stores.identity.claims.get("spend_ceiling_usd")
    return float(ceiling) if isinstance(ceiling, int | float) else None


__all__ = [
    "QuotaExceededError",
    "SpendCeilings",
    "check_launch_quotas",
    "effective_launch_caps",
    "lifetime_ceilings",
]
