"""Tests for LLM client retry logic.

Covers retry behavior for transient errors (503, 429) and
non-retryable errors (400) in _OpenAICompatibleClient.
"""

from unittest.mock import AsyncMock

import pytest

from _helpers import MockCompletion


@pytest.mark.asyncio
async def test_llm_retry_503():
    """Retry on 503 twice, then succeed on third attempt."""
    from api.services.llm_client import _OpenAICompatibleClient

    client = _OpenAICompatibleClient(
        api_key="test-key",
        base_url="http://localhost:1234",
        default_model="test-model",
        provider_name="test",
    )

    call_count = [0]

    class Mock503Error(Exception):
        status_code = 503

    async def mock_create(**kwargs):
        call_count[0] += 1
        if call_count[0] <= 2:
            raise Mock503Error("Service unavailable")
        return MockCompletion()

    # Patch the client
    mock_async_client = AsyncMock()
    mock_async_client.chat.completions.create = mock_create
    client._client = mock_async_client

    # Should succeed after 2 retries
    response = await client.chat(
        messages=[{"role": "user", "content": "test"}],
        model="test-model",
    )

    assert response.content == '{"result": "ok"}'
    assert call_count[0] == 3  # 2 failures + 1 success


@pytest.mark.asyncio
async def test_llm_retry_429():
    """Retry on 429 once, then succeed."""
    from api.services.llm_client import _OpenAICompatibleClient

    client = _OpenAICompatibleClient(
        api_key="test-key",
        base_url="http://localhost:1234",
        default_model="test-model",
        provider_name="test",
    )

    call_count = [0]

    class Mock429Error(Exception):
        status_code = 429

    async def mock_create(**kwargs):
        call_count[0] += 1
        if call_count[0] <= 1:
            raise Mock429Error("Rate limited")
        return MockCompletion()

    mock_async_client = AsyncMock()
    mock_async_client.chat.completions.create = mock_create
    client._client = mock_async_client

    response = await client.chat(
        messages=[{"role": "user", "content": "test"}],
        model="test-model",
    )

    assert response.content == '{"result": "ok"}'
    assert call_count[0] == 2


@pytest.mark.asyncio
async def test_llm_no_retry_400():
    """400 errors raise immediately with no retry."""
    from api.services.llm_client import _OpenAICompatibleClient

    client = _OpenAICompatibleClient(
        api_key="test-key",
        base_url="http://localhost:1234",
        default_model="test-model",
        provider_name="test",
    )

    call_count = [0]

    class Mock400Error(Exception):
        status_code = 400

    async def mock_create(**kwargs):
        call_count[0] += 1
        raise Mock400Error("Bad request")

    mock_async_client = AsyncMock()
    mock_async_client.chat.completions.create = mock_create
    client._client = mock_async_client

    with pytest.raises(Mock400Error):
        await client.chat(
            messages=[{"role": "user", "content": "test"}],
            model="test-model",
        )

    assert call_count[0] == 1  # No retry


@pytest.mark.asyncio
async def test_llm_retry_exhausted():
    """503 on every attempt raises after max retries."""
    from api.services.llm_client import _MAX_APP_RETRIES, _OpenAICompatibleClient

    client = _OpenAICompatibleClient(
        api_key="test-key",
        base_url="http://localhost:1234",
        default_model="test-model",
        provider_name="test",
    )

    call_count = [0]

    class Mock503Error(Exception):
        status_code = 503

    async def mock_create(**kwargs):
        call_count[0] += 1
        raise Mock503Error("Service unavailable")

    mock_async_client = AsyncMock()
    mock_async_client.chat.completions.create = mock_create
    client._client = mock_async_client

    with pytest.raises(Mock503Error):
        await client.chat(
            messages=[{"role": "user", "content": "test"}],
            model="test-model",
        )

    assert call_count[0] == _MAX_APP_RETRIES + 1
