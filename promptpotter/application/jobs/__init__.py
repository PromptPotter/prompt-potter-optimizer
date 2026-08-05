"""Job orchestration for campaign minting and runner spawning.

Friend signs in → picks dataset → clicks Start → ``mint-campaign`` mints
campaign+cycle on disk → ``start-run`` launches the runner in a detached
asyncio task tracked by :class:`JobRegistry`. The ``_CYCLE_LEDGER``
ContextVar isolation (`infrastructure/llm/telemetry.py`) makes concurrent
campaigns safe.

Persistence: ``.promptpotter/jobs/{job_id}.json``. Process-wide singleton
stashed on ``app.state.job_registry``; reads filter by ``user_id``.
"""
