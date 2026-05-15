"""LLM client error translation + RateLimiter rolling-window gating.

Two named invariants:
  1. ``OpenAICompatibleClient`` translates HTTP errors at the boundary:
     404 → ValueError("not found"), 413/429 (terminal) → RequestTooLargeError
     with parsed limit/requested, 400 ``json_validate_failed`` →
     salvaged LLMResponse via ``failed_generation``. Anything else
     re-raises unchanged (no app-level retry — the SDK owns 5xx retry).
  2. ``RateLimiter`` blocks until the rolling window rolls (RPM cap and
     TPM cap), and ``record_actual`` retroactively corrects an over-
     reservation so subsequent ``acquire`` calls don't block.
"""

from unittest.mock import AsyncMock

import pytest
from _helpers import MockCompletion, MockHTTPError, make_http_error

from promptpotter.infrastructure.llm import OpenAICompatibleClient, RateLimiter
from promptpotter.shared.errors import RequestTooLargeError

# ===========================================================================
# OpenAICompatibleClient — error translation
# ===========================================================================


class _RawResponse:
    def __init__(self, parsed, headers=None):
        self._parsed = parsed
        self.headers = headers or {}

    def parse(self):
        return self._parsed


@pytest.fixture
def llm_client():
    client = OpenAICompatibleClient(
        api_key="test-key",
        base_url="http://localhost:1234",
        default_model="test-model",
        provider_name="test",
    )
    mock_async_client = AsyncMock()
    client._client = mock_async_client
    return client, mock_async_client


@pytest.mark.asyncio
async def test_404_translates_to_model_not_found(llm_client):
    client, mock_async = llm_client
    mock_async.chat.completions.with_raw_response.create = AsyncMock(
        side_effect=make_http_error(404, "model not found")
    )
    with pytest.raises(ValueError, match="not found on test"):
        await client.chat(messages=[{"role": "user", "content": "x"}])


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [413, 429])
async def test_request_too_large_terminal(llm_client, status):
    client, mock_async = llm_client
    body = f"Limit 8000, Requested 9660 (status {status})"
    mock_async.chat.completions.with_raw_response.create = AsyncMock(
        side_effect=make_http_error(status, body)
    )
    with pytest.raises(RequestTooLargeError) as excinfo:
        await client.chat(messages=[{"role": "user", "content": "x"}])
    assert excinfo.value.limit == 8000
    assert excinfo.value.requested == 9660


@pytest.mark.asyncio
async def test_json_validate_failed_salvage(llm_client):
    client, mock_async = llm_client
    body = (
        "400 Bad Request {'error': {'code': 'json_validate_failed', "
        "'failed_generation': '{\"ok\": 1}'}}"
    )
    mock_async.chat.completions.with_raw_response.create = AsyncMock(
        side_effect=make_http_error(400, body)
    )
    resp = await client.chat(messages=[{"role": "user", "content": "x"}])
    assert resp.parsed == {"ok": 1}


@pytest.mark.asyncio
async def test_unknown_error_reraised_no_app_retry(llm_client):
    client, mock_async = llm_client
    call_count = [0]

    async def mock_create(**_kwargs):
        call_count[0] += 1
        raise make_http_error(500, "server error")

    mock_async.chat.completions.with_raw_response.create = mock_create
    with pytest.raises(MockHTTPError, match="server error"):
        await client.chat(messages=[{"role": "user", "content": "x"}])
    assert call_count[0] == 1, "app layer must not retry — SDK handles 5xx internally"


@pytest.mark.asyncio
async def test_happy_path_calls_sdk_once(llm_client):
    client, mock_async = llm_client
    mock_async.chat.completions.with_raw_response.create = AsyncMock(
        return_value=_RawResponse(MockCompletion())
    )
    resp = await client.chat(messages=[{"role": "user", "content": "x"}])
    assert resp.content == '{"result": "ok"}'


@pytest.mark.asyncio
async def test_pydantic_validation_failure_retries_once_then_raises(llm_client):
    """Wire-contract guard: when a response fails Pydantic validation against
    ``response_model``, ``chat()`` retries exactly once with a repair-hint
    user turn appended. Two malformed replies in a row raise
    :class:`MetaPromptParseError` with ``attempts=2`` — the L1 catch site
    converts that into a ``ValidationFailure`` wound so L2 can heal next round.
    """
    from pydantic import BaseModel

    from promptpotter.infrastructure.llm.json_parse import MetaPromptParseError

    class _Strict(BaseModel):
        required_field: str

    client, mock_async = llm_client
    bad = type(
        "Bad",
        (),
        {
            "choices": [
                type(
                    "C",
                    (),
                    {
                        "message": type("M", (), {"content": '{"wrong_field": "x"}'})(),
                        "finish_reason": "stop",
                    },
                )()
            ],
            "usage": None,
            "model": "test-model",
        },
    )()
    mock_async.chat.completions.with_raw_response.create = AsyncMock(return_value=_RawResponse(bad))
    with pytest.raises(MetaPromptParseError) as excinfo:
        await client.chat(
            messages=[{"role": "user", "content": "x"}],
            response_model=_Strict,
        )
    assert excinfo.value.attempts == 2
    # The repair-hint retry fired exactly once on top of the original call.
    assert mock_async.chat.completions.with_raw_response.create.await_count == 2
    # The retry must have appended the bad output + repair hint to the
    # message list — the original system/user turn stays intact.
    second_call_messages = mock_async.chat.completions.with_raw_response.create.await_args_list[
        1
    ].kwargs["messages"]
    assert second_call_messages[0] == {"role": "user", "content": "x"}
    assert second_call_messages[-2]["role"] == "assistant"
    assert "schema validation" in second_call_messages[-1]["content"]


# ===========================================================================
# RateLimiter — rolling-window RPM + TPM
# ===========================================================================


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
    monkeypatch.setattr("promptpotter.infrastructure.llm.rate_limit.time.monotonic", c)
    return c


@pytest.fixture
def fast_sleep(monkeypatch, clock):
    async def _sleep(seconds):
        clock.advance(seconds)

    monkeypatch.setattr("promptpotter.infrastructure.llm.rate_limit.asyncio.sleep", _sleep)


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
