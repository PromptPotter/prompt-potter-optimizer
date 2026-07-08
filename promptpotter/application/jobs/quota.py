"""Per-user quota + abuse-limit gates fired from the launcher.

Reads ``User.spend_budget_usd_daily`` / ``max_concurrent_cycles`` /
``max_campaigns_per_day`` from :class:`UserStore`; cross-references
:class:`JobRegistry` for live counts. Token-bucket rate limiter at module
scope (process-wide; refilled on each ``check``) keeps ``mint-campaign``
slow enough that one bad actor can't drain the budget in a single burst.

Spend-cap composition: at mint time, the per-cycle ``spend_budget_usd``
collapses to ``min(requested, daily_cap - daily_spent_today)`` so the user's
daily cap can't be exceeded by stacking N small per-cycle caps. Per-cycle
enforcement still rides the runner's ``BudgetGate`` (``runner/termination.py``).
"""

from __future__ import annotations

import logging
import threading
import time

from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.application.jobs.spend import start_of_utc_day, sum_user_spend
from promptpotter.infrastructure.store import Stores
from promptpotter.infrastructure.store.user_store import User
from promptpotter.shared.errors import PotterError

logger = logging.getLogger(__name__)


class QuotaExceededError(PotterError):
    """Raised when a user-scoped abuse limit blocks a launch (429).

    ``code`` rides the HTTP layer (the abuse-limit kind, e.g. ``quota_exceeded``)
    — distinct from :class:`~promptpotter.application.jobs.LaunchError` which is a
    422 (malformed request / dataset not found / not owned). Maps to one HTTP
    response via the :class:`~promptpotter.shared.errors.PotterError` seam.
    """

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
    """Token-bucket admit — return True iff a token was available."""
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
    """Enforce per-user abuse limits before a launch.

    The *global* run-admission gate (one campaign at a time across all users —
    409 ``machine_busy``) rides :meth:`JobRegistry.reserve` at the launcher, not
    here; this function owns only the per-user limits:

    - rate-limit miss (``mint-campaign`` only; ``rate_limited=False`` skips it
      for ``start-run`` against an existing cycle, which is a retry path)
    - concurrent-cycles ceiling (the caller's *own* second launch)
    - campaigns-per-day ceiling

    Runs *before* ``reserve`` so it counts the caller's prior runs, not the
    reservation it is about to make.
    """
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
    """Compose the per-cycle spend cap with the user's daily cap.

    Sums today's ``TokenUsageRecord`` cost from the canonical ledger
    (:func:`~promptpotter.application.jobs.spend.sum_user_spend`) to get
    ``daily_spent``; returns ``min(requested, daily_cap - daily_spent)``. A
    negative remainder collapses to ``0.0`` so the runner halts at the first
    round boundary.
    """
    if user.spend_budget_usd_daily is None:
        return requested_cap_usd
    spent = sum_user_spend(store=stores, since=start_of_utc_day(), until=time.time())
    daily_remaining = max(0.0, user.spend_budget_usd_daily - spent)
    if requested_cap_usd is None:
        return daily_remaining
    return min(float(requested_cap_usd), daily_remaining)


__all__ = [
    "QuotaExceededError",
    "check_launch_quotas",
    "effective_spend_cap_usd",
]
