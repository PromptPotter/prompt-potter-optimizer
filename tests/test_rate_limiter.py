"""RateLimiter — rolling-window RPM + TPM gating + retroactive correction."""

import pytest

from promptpotter.infrastructure.llm import RateLimiter


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, delta: float) -> None:
        self.now += delta


@pytest.fixture
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr("promptpotter.infrastructure.llm.time.monotonic", c)
    return c


@pytest.fixture
def fast_sleep(monkeypatch, clock):
    async def _sleep(seconds):
        clock.advance(seconds)

    monkeypatch.setattr("promptpotter.infrastructure.llm.asyncio.sleep", _sleep)


@pytest.mark.asyncio
async def test_rpm_cap_blocks_until_window_rolls(clock, fast_sleep):
    rl = RateLimiter(rpm=3, window_s=60.0)
    for _ in range(3):
        await rl.acquire(100)
    assert clock.now == 0.0
    await rl.acquire(100)  # 4th must wait for the oldest reservation to age out
    assert clock.now == pytest.approx(60.0)


@pytest.mark.asyncio
async def test_tpm_cap_blocks_until_tokens_age_out(clock, fast_sleep):
    rl = RateLimiter(tpm=8000, window_s=60.0)
    await rl.acquire(3000)
    await rl.acquire(3000)
    assert clock.now == 0.0
    await rl.acquire(3000)  # 9000 > 8000, wait for oldest 3000 to expire
    assert clock.now == pytest.approx(60.0)


@pytest.mark.asyncio
async def test_record_actual_corrects_reservation(clock, fast_sleep):
    rl = RateLimiter(tpm=8000, window_s=60.0)
    await rl.acquire(5000)
    rl.record_actual(5000, 1000)  # actual usage was much smaller
    await rl.acquire(6000)  # 1000 + 6000 ≤ 8000 → no block
    assert clock.now == 0.0
