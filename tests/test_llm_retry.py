"""Tests for LLM client retry logic.

Covers retry behavior for transient errors (503, 429) and
non-retryable errors (400) in _OpenAICompatibleClient.
"""

from unittest.mock import AsyncMock

import pytest

from api.services.llm_client import _MAX_APP_RETRIES, _OpenAICompatibleClient
from _helpers import MockCompletion, make_http_error


@pytest.fixture
def llm_client():
    """Create a fresh _OpenAICompatibleClient with a mocked async client."""
    client = _OpenAICompatibleClient(
        api_key="test-key",
        base_url="http://localhost:1234",
        default_model="test-model",
        provider_name="test",
    )
    mock_async_client = AsyncMock()
    client._client = mock_async_client
    return client, mock_async_client


@pytest.mark.asyncio
async def test_llm_retry_503(llm_client):
    """Retry on 503 twice, then succeed on third attempt."""
    client, mock_async = llm_client
    call_count = [0]

    async def mock_create(**kwargs):
        call_count[0] += 1
        if call_count[0] <= 2:
            raise make_http_error(503, "Service unavailable")
        return MockCompletion()

    mock_async.chat.completions.create = mock_create

    response = await client.chat(
        messages=[{"role": "user", "content": "test"}],
        model="test-model",
    )

    assert response.content == '{"result": "ok"}'
    assert call_count[0] == 3  # 2 failures + 1 success


@pytest.mark.asyncio
async def test_llm_retry_429(llm_client):
    """Retry on 429 once, then succeed."""
    client, mock_async = llm_client
    call_count = [0]

    async def mock_create(**kwargs):
        call_count[0] += 1
        if call_count[0] <= 1:
            raise make_http_error(429, "Rate limited")
        return MockCompletion()

    mock_async.chat.completions.create = mock_create

    response = await client.chat(
        messages=[{"role": "user", "content": "test"}],
        model="test-model",
    )

    assert response.content == '{"result": "ok"}'
    assert call_count[0] == 2


@pytest.mark.asyncio
async def test_llm_no_retry_400(llm_client):
    """400 errors raise immediately with no retry."""
    client, mock_async = llm_client
    call_count = [0]

    async def mock_create(**kwargs):
        call_count[0] += 1
        raise make_http_error(400, "Bad request")

    mock_async.chat.completions.create = mock_create

    with pytest.raises(Exception, match="Bad request"):
        await client.chat(
            messages=[{"role": "user", "content": "test"}],
            model="test-model",
        )

    assert call_count[0] == 1  # No retry


@pytest.mark.asyncio
async def test_llm_retry_exhausted(llm_client):
    """503 on every attempt raises after max retries."""
    client, mock_async = llm_client
    call_count = [0]

    async def mock_create(**kwargs):
        call_count[0] += 1
        raise make_http_error(503, "Service unavailable")

    mock_async.chat.completions.create = mock_create

    with pytest.raises(Exception, match="Service unavailable"):
        await client.chat(
            messages=[{"role": "user", "content": "test"}],
            model="test-model",
        )

    assert call_count[0] == _MAX_APP_RETRIES + 1
