"""LLM client error translation + tier auto-discovery invariants.

The SDK owns transient-retry policy (``max_retries`` + ``Retry-After``).
What PromptPotter owns — and thus what's tested here — is the narrow
translation layer in ``OpenAICompatibleClient.chat``:

1. 404 → ``ValueError`` with actionable model-not-found message.
2. 413/429 whose body parses as ``Requested > Limit`` → ``RequestTooLargeError``.
3. 400 ``json_validate_failed`` carrying recoverable ``failed_generation``
   is salvaged into a parsed ``LLMResponse``.
4. Any other error re-raises unchanged (no app-level retry).
5. On success, ``x-ratelimit-limit-*`` headers populate the attached
   limiter's unpinned RPM/TPM caps (self-tuning).
"""

from unittest.mock import AsyncMock

import pytest
from _helpers import MockCompletion, MockHTTPError, make_http_error

from promptpotter.infrastructure.llm.client import OpenAICompatibleClient
from promptpotter.infrastructure.llm.rate_limiter import RateLimiter
from promptpotter.shared.errors import RequestTooLargeError


class _RawResponse:
    """Minimal stand-in for ``openai.AsyncAPIResponse`` — parse() + headers."""

    def __init__(self, parsed, headers=None):
        self._parsed = parsed
        self.headers = headers or {}

    def parse(self):
        return self._parsed


def _make_raw_stub(*, return_value=None, side_effect=None):
    """Wire an AsyncMock to play the ``with_raw_response.create`` role."""
    if side_effect is not None:
        return AsyncMock(side_effect=side_effect)
    return AsyncMock(return_value=_RawResponse(return_value))


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
    mock_async.chat.completions.with_raw_response.create = _make_raw_stub(
        side_effect=make_http_error(404, "model not found")
    )

    with pytest.raises(ValueError, match="not found on test"):
        await client.chat(messages=[{"role": "user", "content": "x"}])


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [413, 429])
async def test_request_too_large_terminal(llm_client, status):
    client, mock_async = llm_client
    err_body = f"Limit 8000, Requested 9660 (status {status})"
    mock_async.chat.completions.with_raw_response.create = _make_raw_stub(
        side_effect=make_http_error(status, err_body)
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
    mock_async.chat.completions.with_raw_response.create = _make_raw_stub(
        side_effect=make_http_error(400, body)
    )

    resp = await client.chat(messages=[{"role": "user", "content": "x"}])
    assert resp.parsed == {"ok": 1}


@pytest.mark.asyncio
async def test_unknown_error_reraised_no_retry(llm_client):
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
    call_count = [0]

    async def mock_create(**_kwargs):
        call_count[0] += 1
        return _RawResponse(MockCompletion())

    mock_async.chat.completions.with_raw_response.create = mock_create
    resp = await client.chat(messages=[{"role": "user", "content": "x"}])
    assert call_count[0] == 1
    assert resp.content == '{"result": "ok"}'


@pytest.mark.asyncio
async def test_discovers_tier_caps_from_headers(llm_client):
    client, mock_async = llm_client
    client._rate_limiter = RateLimiter()  # unpinned — ready to learn
    headers = {
        "x-ratelimit-limit-requests": "5",
        "x-ratelimit-limit-tokens": "8000",
    }
    mock_async.chat.completions.with_raw_response.create = _make_raw_stub(
        return_value=MockCompletion(),
    )
    # Attach headers via our stub
    mock_async.chat.completions.with_raw_response.create.return_value = _RawResponse(
        MockCompletion(), headers=headers
    )

    await client.chat(messages=[{"role": "user", "content": "x"}])
    assert client._rate_limiter.rpm == 5
    assert client._rate_limiter.tpm == 8000


@pytest.mark.asyncio
async def test_pinned_caps_not_overridden_by_headers(llm_client):
    client, mock_async = llm_client
    client._rate_limiter = RateLimiter(rpm=3, tpm=4000, rpm_pinned=True, tpm_pinned=True)
    headers = {"x-ratelimit-limit-requests": "5", "x-ratelimit-limit-tokens": "8000"}
    mock_async.chat.completions.with_raw_response.create = AsyncMock(
        return_value=_RawResponse(MockCompletion(), headers=headers)
    )

    await client.chat(messages=[{"role": "user", "content": "x"}])
    assert client._rate_limiter.rpm == 3
    assert client._rate_limiter.tpm == 4000
