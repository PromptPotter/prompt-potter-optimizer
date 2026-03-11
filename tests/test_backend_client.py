"""Tests for BackendClient session auto-recovery."""

import httpx
import pytest
from unittest.mock import AsyncMock, patch

from api.services.backend_client import BackendClient


def _mock_response(status_code: int, json_data: dict) -> httpx.Response:
    """Build an httpx.Response with a request set (needed for raise_for_status)."""
    resp = httpx.Response(status_code, json=json_data)
    resp._request = httpx.Request("POST", "http://mock:8000/")
    return resp


@pytest.mark.asyncio
async def test_run_match_auto_reinit_on_400():
    """run_match re-initializes session and retries on 400."""
    client = BackendClient("http://mock:8000")
    client._session_terms = ["term_a", "term_b"]

    call_count = 0

    async def mock_post(url, **kwargs):
        nonlocal call_count
        call_count += 1
        if "/sessions" in url:
            return _mock_response(200, {"status": "ok"})
        if "/matches" in url:
            if call_count == 1:
                return _mock_response(400, {"detail": "No session found"})
            return _mock_response(200, {"data": {"ranked_candidates": []}})
        return _mock_response(404, {})

    mock_client = AsyncMock()
    mock_client.post = mock_post
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("api.services.backend_client.httpx.AsyncClient", return_value=mock_client):
        result = await client.run_match("test query")

    assert result["data"]["ranked_candidates"] == []
    assert client._session_terms == ["term_a", "term_b"]
    assert call_count == 3  # 1st /matches (400) + /sessions + 2nd /matches (200)


@pytest.mark.asyncio
async def test_run_match_no_reinit_without_stored_terms():
    """run_match does not retry when no session terms are stored."""
    client = BackendClient("http://mock:8000")
    assert client._session_terms is None

    async def mock_post(url, **kwargs):
        return _mock_response(400, {"detail": "No session found"})

    mock_client = AsyncMock()
    mock_client.post = mock_post
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("api.services.backend_client.httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(httpx.HTTPStatusError):
            await client.run_match("test query")


@pytest.mark.asyncio
async def test_init_session_stores_terms():
    """init_session saves terms for later auto-recovery."""
    client = BackendClient("http://mock:8000")
    assert client._session_terms is None

    async def mock_post(url, **kwargs):
        return _mock_response(200, {"status": "ok", "data": {"term_count": 2}})

    mock_client = AsyncMock()
    mock_client.post = mock_post
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("api.services.backend_client.httpx.AsyncClient", return_value=mock_client):
        await client.init_session(["alpha", "beta"])

    assert client._session_terms == ["alpha", "beta"]
