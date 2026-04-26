"""LLM client error translation — narrow contract on top of the SDK's retry policy.

The SDK owns transient-retry. PromptPotter owns translation:
  * 404 → ValueError("not found")
  * 413/429 with parsable body → RequestTooLargeError(limit, requested)
  * 400 json_validate_failed with failed_generation → salvaged LLMResponse
  * Anything else re-raises unchanged (no app-level retry)
"""

from unittest.mock import AsyncMock

import pytest
from _helpers import MockCompletion, MockHTTPError, make_http_error

from promptpotter.infrastructure.llm.client import OpenAICompatibleClient
from promptpotter.shared.errors import RequestTooLargeError


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
