"""Job orchestration for campaign minting and runner spawning.

Friend signs in → picks dataset → clicks Start → ``mint-campaign`` mints
campaign+cycle on disk → ``start-run`` launches the runner in a detached
asyncio task tracked by :class:`JobRegistry`. The ``_CYCLE_LEDGER``
ContextVar isolation (`infrastructure/llm/models.py`) makes concurrent
campaigns safe.

Persistence: ``.promptpotter/jobs/{job_id}.json``. Process-wide singleton
stashed on ``app.state.job_registry``; reads filter by ``user_id``.
"""

from promptpotter.application.jobs.launcher import (
    LaunchError,
    mint_campaign_command,
    start_run_command,
)
from promptpotter.application.jobs.mint import (
    CyclePlan,
    MintedCycle,
    prepare_fresh_cycle,
    resolve_cycle_plan,
)
from promptpotter.application.jobs.quota import (
    QuotaExceededError,
    check_launch_quotas,
    effective_spend_cap_usd,
)
from promptpotter.application.jobs.registry import (
    Job,
    JobRegistry,
    JobStatus,
    default_jobs_dir,
)

__all__ = [
    "CyclePlan",
    "Job",
    "JobRegistry",
    "JobStatus",
    "LaunchError",
    "MintedCycle",
    "QuotaExceededError",
    "check_launch_quotas",
    "default_jobs_dir",
    "effective_spend_cap_usd",
    "mint_campaign_command",
    "prepare_fresh_cycle",
    "resolve_cycle_plan",
    "start_run_command",
]
