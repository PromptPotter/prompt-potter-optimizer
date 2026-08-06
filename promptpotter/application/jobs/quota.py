"""Per-user quota + abuse-limit gates fired from the launcher. At mint the per-cycle cap collapses to
``min(requested, daily_cap − spent_today)``, so stacking N small caps cannot outrun the daily one."""

from __future__ import annotations

import logging
import threading
import time

from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.application.jobs.spend import start_of_utc_day, sum_user_spend
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


def effective_spend_cap_usd(
    *,
    requested_cap_usd: float | None,
    user: User,
    stores: Stores,
) -> float | None:
    """Compose the per-cycle cap with the user's daily cap and any delegated ceiling, taking the ``min``.
    A negative remainder collapses to ``0.0`` so the runner halts at the first round boundary."""
    caps = [c for c in (requested_cap_usd, _delegated_spend_ceiling(stores)) if c is not None]
    if user.spend_budget_usd_daily is not None:
        spent = sum_user_spend(stores=stores, since=start_of_utc_day(), until=time.time())
        caps.append(max(0.0, user.spend_budget_usd_daily - spent))
    return min(float(c) for c in caps) if caps else None


def _delegated_spend_ceiling(stores: Stores) -> float | None:
    ceiling = stores.identity.claims.get("spend_ceiling_usd")
    return float(ceiling) if isinstance(ceiling, int | float) else None


__all__ = [
    "QuotaExceededError",
    "check_launch_quotas",
    "effective_spend_cap_usd",
]
