"""Tests for BackendClient pipeline parameter passthrough."""

import pytest
import httpx

from api.services.backend_client import BackendClient


@pytest.mark.asyncio
async def test_run_match_merges_pipeline_params(monkeypatch):
    """pipeline_params are merged into the POST /matches payload."""
    captured: dict = {}

    async def fake_post(self, url, *, json=None, **kwargs):
        captured.update(json)
        resp = httpx.Response(
            200,
            json={"status": "success", "data": {}},
            request=httpx.Request("POST", url),
        )
        return resp

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    client = BackendClient("http://fake:8000")
    await client.run_match(
        query="x",
        skip_llm_ranking=True,
        pipeline_params={"max_sites": 3, "profiling_temperature": 0.1},
    )

    assert captured["query"] == "x"
    assert captured["skip_llm_ranking"] is True
    assert captured["max_sites"] == 3
    assert captured["profiling_temperature"] == 0.1
