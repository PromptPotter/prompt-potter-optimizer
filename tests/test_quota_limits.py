"""Per-user quota gates — one bundled test per the charter.

Exercises ``check_launch_quotas`` (the
concurrent-cycles + daily-campaigns + rate-limit gates) and
``effective_spend_cap_usd`` (per-cycle ∧ daily-cap composition) without
spinning up the full asyncio launcher — the gates are pure-ish enough to
poke directly.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from promptpotter.application.jobs import (
    JobRegistry,
    QuotaExceededError,
    check_launch_quotas,
    effective_spend_cap_usd,
)
from promptpotter.application.jobs.quota import reset_rate_buckets
from promptpotter.domain.identity import TenantId, UserId
from promptpotter.domain.run_records import TokenUsageRecord
from promptpotter.infrastructure.store import build_stores
from promptpotter.infrastructure.store.user_store import User
from promptpotter.shared.identity import IdentityContext


def _mk_user(**overrides: object) -> User:
    base: dict[str, object] = {
        "user_id": "u_alice",
        "tenant_id": "u_alice",
        "email": None,
        "spend_budget_usd_daily": None,
        "max_concurrent_cycles": 2,
        "max_campaigns_per_day": 10,
        "created_at": datetime.now(UTC).isoformat(),
    }
    base.update(overrides)
    return User.model_validate(base)


def test_quota_contract(tmp_path: Path) -> None:
    """All four Phase-5 gates fire on a single user; spend caps compose."""
    reset_rate_buckets()
    jobs_dir = tmp_path / "jobs"
    registry = JobRegistry(jobs_dir)

    # --- Gate 1: concurrent-cycles ceiling ------------------------------
    user_concurrent = _mk_user(max_concurrent_cycles=2)
    for i in range(2):
        job = registry.create(
            user_id=user_concurrent.user_id,
            campaign_id=f"camp-{i}",
            cycle_id=f"cycle_{i:012x}",
            dataset_name="aime",
        )
        registry.mark_started(job.job_id)
    with pytest.raises(QuotaExceededError) as exc:
        # ``rate_limited=False`` isolates concurrent ceiling from the rate bucket.
        check_launch_quotas(user=user_concurrent, job_registry=registry, rate_limited=False)
    assert exc.value.code == "quota_exceeded"
    assert "Concurrent-cycles" in str(exc.value)

    # --- Gate 2: campaigns-per-day ceiling -------------------------------
    daily_dir = tmp_path / "jobs_daily"
    daily_registry = JobRegistry(daily_dir)
    user_daily = _mk_user(user_id="u_daily", tenant_id="u_daily", max_campaigns_per_day=3)
    for i in range(3):
        job = daily_registry.create(
            user_id=user_daily.user_id,
            campaign_id=f"d-{i}",
            cycle_id=f"cycle_d{i:011x}",
            dataset_name="aime",
        )
        # Finish each so concurrent-cycles ceiling stays clear.
        daily_registry.mark_finished(job.job_id, status="completed")
    with pytest.raises(QuotaExceededError) as exc:
        check_launch_quotas(user=user_daily, job_registry=daily_registry, rate_limited=True)
    assert exc.value.code == "quota_exceeded"
    assert "Daily campaigns" in str(exc.value)

    # --- Gate 3: rate-limit bucket --------------------------------------
    reset_rate_buckets()
    fresh_dir = tmp_path / "jobs_rate"
    fresh_registry = JobRegistry(fresh_dir)
    user_rate = _mk_user(user_id="u_rate", tenant_id="u_rate", max_campaigns_per_day=100)
    # First 5 admits drain the burst capacity; 6th raises ``rate_limited``.
    for _ in range(5):
        check_launch_quotas(user=user_rate, job_registry=fresh_registry, rate_limited=True)
    with pytest.raises(QuotaExceededError) as exc:
        check_launch_quotas(user=user_rate, job_registry=fresh_registry, rate_limited=True)
    assert exc.value.code == "rate_limited"

    # --- Gate 4: spend-cap composition -----------------------------------
    # User's daily cap is $1.00; today's per-cycle ledger carries $0.40 of token
    # spend. Per-cycle request of $0.80 collapses to remaining $0.60. Spend is
    # read from the canonical ledger (TokenUsageRecord), not dashboard.json.
    user_spend = _mk_user(
        user_id="u_spend",
        tenant_id="u_spend",
        spend_budget_usd_daily=1.0,
    )
    spend_registry = JobRegistry(tmp_path / "jobs_spend")
    identity = IdentityContext(user_id=UserId("u_spend"), tenant_id=TenantId("u_spend"))
    stores = build_stores(identity, projects_root=tmp_path / "projects_spend")
    # Plant a job + a token-usage record on its per-cycle ledger.
    job = spend_registry.create(
        user_id=user_spend.user_id,
        campaign_id="cmp",
        cycle_id="cycle_abcdef012345",
        dataset_name="aime",
    )
    runtime_dir = stores.campaigns.cycle_dir(job.campaign_id, job.cycle_id) / ".runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    usage = TokenUsageRecord(
        kind="backend", node="x", input_tokens=0, output_tokens=0, cost_usd=0.40
    )
    (runtime_dir / "ledger.jsonl").write_text(usage.model_dump_json() + "\n", encoding="utf-8")
    cap = effective_spend_cap_usd(
        requested_cap_usd=0.80,
        user=user_spend,
        job_registry=spend_registry,
        stores=stores,
    )
    assert cap is not None
    assert cap == pytest.approx(0.60)
    # No request → daily-remaining alone.
    cap_unset = effective_spend_cap_usd(
        requested_cap_usd=None,
        user=user_spend,
        job_registry=spend_registry,
        stores=stores,
    )
    assert cap_unset == pytest.approx(0.60)
    # No daily cap configured → request passes through.
    assert (
        effective_spend_cap_usd(
            requested_cap_usd=2.50,
            user=_mk_user(),
            job_registry=spend_registry,
            stores=stores,
        )
        == 2.50
    )
