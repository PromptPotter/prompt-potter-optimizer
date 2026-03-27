"""Tests for LLM client retry logic.

Covers retry behavior for transient errors (503, 429) and
non-retryable errors (400) in OpenAICompatibleClient.
"""

from unittest.mock import AsyncMock

import pytest
from _helpers import MockCompletion, MockHTTPError, make_http_error

from api.config.settings import LLM_MAX_APP_RETRIES
from api.services.llm_client import OpenAICompatibleClient


@pytest.fixture(autouse=True)
def _instant_sleep(monkeypatch):
    """Eliminate real asyncio.sleep — we test retry logic, not delays."""
    async def _noop(seconds):
        pass
    monkeypatch.setattr("asyncio.sleep", _noop)


@pytest.fixture
def llm_client():
    """Create a fresh OpenAICompatibleClient with a mocked async client."""
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
@pytest.mark.parametrize("status_code,n_failures,expected_calls,succeeds", [
    (503, 2, 3, True),
    (429, 1, 2, True),
    (503, LLM_MAX_APP_RETRIES + 1, LLM_MAX_APP_RETRIES + 1, False),
])
async def test_llm_retry_transient(
    llm_client, status_code, n_failures, expected_calls, succeeds,
):

    client, mock_async = llm_client
    call_count = [0]

    async def mock_create(**kwargs):
        call_count[0] += 1
        if call_count[0] <= n_failures:
            raise make_http_error(status_code, f"Error {status_code}")
        return MockCompletion()

    mock_async.chat.completions.create = mock_create

    if succeeds:
        response = await client.chat(
            messages=[{"role": "user", "content": "test"}],
            model="test-model",
        )
        assert response.content == '{"result": "ok"}'
    else:
        with pytest.raises(MockHTTPError):
            await client.chat(
                messages=[{"role": "user", "content": "test"}],
                model="test-model",
            )

    assert call_count[0] == expected_calls


@pytest.mark.asyncio
async def test_llm_no_retry_400(llm_client):

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
