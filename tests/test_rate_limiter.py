"""RateLimiter invariants — rolling-window RPM + TPM gating.

Covers the three behaviors the rest of the system leans on:

1. RPM cap: once N requests have been reserved inside the window, the next
   call to ``acquire`` blocks until the oldest ages out.
2. TPM cap: cumulative token reservations over the rolling window never
   exceed ``tpm``.
3. ``record_actual`` retroactively corrects the last reservation so a
   subsequent ``acquire`` sees the true tally.

Real time is replaced with a deterministic clock and an instant
``asyncio.sleep`` so tests stay fast and stable.
"""

import asyncio

import pytest

from promptpotter.infrastructure.llm.rate_limiter import RateLimiter


class _Clock:
    """Monotonic clock stub with manual advance."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, delta: float) -> None:
        self.now += delta


@pytest.fixture
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr("promptpotter.infrastructure.llm.rate_limiter.time.monotonic", c)
    return c


@pytest.fixture
def fast_sleep(monkeypatch, clock):
    """Replace ``asyncio.sleep`` with a clock-advancing no-op — keeps loop fast."""

    async def _sleep(seconds):
        clock.advance(seconds)

    monkeypatch.setattr("promptpotter.infrastructure.llm.rate_limiter.asyncio.sleep", _sleep)


@pytest.mark.asyncio
async def test_rpm_cap_blocks_until_window_rolls(clock, fast_sleep):
    rl = RateLimiter(rpm=3, window_s=60.0)
    for _ in range(3):
        await rl.acquire(100)
    assert clock.now == 0.0  # first 3 fit

    await rl.acquire(100)
    # 4th call must wait for the oldest (at t=0) to age out at t=60.
    assert clock.now == pytest.approx(60.0)


@pytest.mark.asyncio
async def test_tpm_cap_blocks_until_tokens_age_out(clock, fast_sleep):
    rl = RateLimiter(tpm=8000, window_s=60.0)
    await rl.acquire(3000)
    await rl.acquire(3000)
    assert clock.now == 0.0  # 6000 ≤ 8000

    await rl.acquire(3000)
    # Adding 3000 would hit 9000 > 8000, so we must wait for a prior 3000 to
    # age out — the first reservation expires at t=60.
    assert clock.now == pytest.approx(60.0)


@pytest.mark.asyncio
async def test_record_actual_corrects_reservation(clock, fast_sleep):
    rl = RateLimiter(tpm=8000, window_s=60.0)
    await rl.acquire(5000)
    rl.record_actual(5000, 1000)

    # After correction, cumulative is 1000, so a 6000-token request fits
    # without blocking.
    await rl.acquire(6000)
    assert clock.now == 0.0


@pytest.mark.asyncio
async def test_no_caps_never_blocks(clock, fast_sleep):
    rl = RateLimiter()  # both rpm and tpm unset
    for _ in range(10):
        await rl.acquire(999999)
    assert clock.now == 0.0


@pytest.mark.asyncio
async def test_concurrent_acquire_is_serialized(clock, fast_sleep):
    rl = RateLimiter(rpm=2, window_s=60.0)
    # Two slots available; two concurrent acquirers should both complete
    # at t=0, a third should wait for t=60.
    await asyncio.gather(rl.acquire(100), rl.acquire(100))
    assert clock.now == 0.0
    await rl.acquire(100)
    assert clock.now == pytest.approx(60.0)
