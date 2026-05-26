"""Per-user quota + abuse-limit gates fired from the launcher.

Reads ``User.spend_budget_usd_daily`` / ``max_concurrent_cycles`` /
``max_campaigns_per_day`` from :class:`UserStore`; cross-references
:class:`JobRegistry` for live counts. Token-bucket rate limiter at module
scope (process-wide; refilled on each ``check``) keeps ``mint-campaign``
slow enough that one bad actor can't drain the budget in a single burst.

Spend-cap composition: at mint time, the per-cycle ``spend_budget_usd``
collapses to ``min(requested, daily_cap - daily_spent_today)`` so the user's
daily cap can't be exceeded by stacking N small per-cycle caps. Per-cycle
enforcement still rides the existing ``spend_cap_probe`` in the runner.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

from promptpotter.application.jobs.registry import Job, JobRegistry
from promptpotter.infrastructure.store import Stores
from promptpotter.infrastructure.store.user_store import User

logger = logging.getLogger(__name__)


class QuotaExceededError(RuntimeError):
    """Raised when a user-scoped abuse limit blocks a launch.

    ``code`` rides the HTTP layer (429 ``quota_exceeded``) — distinct from
    :class:`~promptpotter.application.jobs.LaunchError` which is a 422
    (malformed request / dataset not found / not owned).
    """

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


def reset_rate_buckets() -> None:
    """Test helper — clear the module-scope rate-limiter state."""
    with _rate_lock:
        _rate_buckets.clear()


def check_launch_quotas(
    *,
    user: User,
    job_registry: JobRegistry,
    rate_limited: bool = True,
) -> None:
    """Enforce per-user abuse limits before a launch.

    Raises :class:`QuotaExceededError` on:

    - rate-limit miss (``mint-campaign`` only; ``rate_limited=False`` skips it
      for ``start-run`` against an existing cycle, which is a retry path)
    - concurrent-cycles ceiling
    - campaigns-per-day ceiling
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
    job_registry: JobRegistry,
    stores: Stores,
) -> float | None:
    """Compose the per-cycle spend cap with the user's daily cap.

    Sums ``dashboard.json::spend.total_used_usd`` across the user's
    today-created jobs to get ``daily_spent``; returns
    ``min(requested, daily_cap - daily_spent)``. A negative remainder
    collapses to ``0.0`` so the runner halts at the first round boundary.
    """
    if user.spend_budget_usd_daily is None:
        return requested_cap_usd
    spent = _sum_daily_spend(job_registry=job_registry, stores=stores, user_id=user.user_id)
    daily_remaining = max(0.0, user.spend_budget_usd_daily - spent)
    if requested_cap_usd is None:
        return daily_remaining
    return min(float(requested_cap_usd), daily_remaining)


def _sum_daily_spend(*, job_registry: JobRegistry, stores: Stores, user_id: str) -> float:
    """Aggregate ``dashboard.json::spend.total_used_usd`` across today's jobs."""
    total = 0.0
    for job in job_registry.list_created_today(user_id=user_id):
        spend = _read_dashboard_spend(stores=stores, job=job)
        if spend is not None:
            total += spend
    return total


def _read_dashboard_spend(*, stores: Stores, job: Job) -> float | None:
    """Read the campaign's session-root dashboard spend bucket; ``None`` on miss."""
    try:
        cycle_dir = stores.campaigns.cycle_dir(job.campaign_id, job.cycle_id)
    except Exception:
        return None
    # LiveDashboardView writes into the session-family root cycle dir; we
    # follow ``parent_cycle_id`` to the root if this job started on a fork.
    root_dir = _resolve_session_root(cycle_dir)
    dashboard_path = root_dir / "dashboard.json"
    if not dashboard_path.is_file():
        return None
    try:
        data = json.loads(dashboard_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    spend = data.get("spend") or {}
    raw = spend.get("total_used_usd")
    if isinstance(raw, int | float):
        return float(raw)
    return None


def _resolve_session_root(cycle_dir: Path) -> Path:
    """Walk ``index.json::parent_cycle_id`` to the session-family root dir."""
    current = cycle_dir
    for _ in range(32):  # depth ceiling — paranoia, real depth <5
        idx = current / "index.json"
        if not idx.is_file():
            return current
        try:
            data = json.loads(idx.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return current
        parent_id = data.get("parent_cycle_id")
        if not parent_id:
            return current
        current = current.parent / str(parent_id)
    return current


__all__ = [
    "QuotaExceededError",
    "check_launch_quotas",
    "effective_spend_cap_usd",
    "reset_rate_buckets",
]
