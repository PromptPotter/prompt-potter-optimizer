"""LLM-only evaluation adapter — drop-in replacement for BackendClient.

For ``llm-only`` datasets (GSM8K, HotPotQA, etc.) there is no backend
server.  This adapter implements the same ``run_query()`` interface as
:class:`BackendClient` so the eval pipeline (``evaluate_query``)
works unchanged.

The system prompt flows through ``pipeline_params`` via the standard
PromptTemplate path: ``OptSearchPoint.render()`` →
``to_job_search_point()`` → ``pipeline_params[node]["prompt"]`` →
``run_query()`` reads it here.  No hardcoded prompts.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from promptpotter.services.llm_client import LLMClientBase

logger = logging.getLogger(__name__)


class LLMOnlyAdapter:
    """Adapter that sends queries directly to an LLM instead of a backend.

    Implements the subset of :class:`BackendClient` used by
    ``evaluate_query``: ``run_query()``, ``check_status()``,
    ``fetch_pipeline()``, ``init_session()``, ``aclose()``.

    The system prompt is NOT set here — it flows through
    ``pipeline_params[node]["prompt"]`` from the PromptTemplate
    decomposition, just like every other dataset.
    """

    def __init__(
        self,
        llm_client: LLMClientBase,
        *,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 1024,
    ) -> None:
        self.llm_client = llm_client
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens

    async def run_query(
        self,
        query: str,
        pipeline_params: dict[str, Any] | None = None,
        precomputed: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send query to LLM and return a backend-compatible response.

        The system prompt is extracted from ``pipeline_params`` — the
        first node config dict containing a ``"prompt"`` key, mirroring
        how ``JobSearchPoint.render()`` reads the prompt.
        """
        pp = pipeline_params or {}

        # Extract prompt from pipeline_params (same path as TermNorm)
        system = ""
        for node_cfg in pp.values():
            if isinstance(node_cfg, dict) and "prompt" in node_cfg:
                system = node_cfg["prompt"]
                break

        # Allow node-level overrides for model params
        flat = {}
        for v in pp.values():
            if isinstance(v, dict):
                flat.update(v)

        model = flat.get("model", self.model)
        temperature = flat.get("temperature", self.temperature)
        max_tokens = flat.get("max_tokens", self.max_tokens)

        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": query})

        t0 = time.monotonic()
        response = await self.llm_client.chat(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        elapsed_ms = round((time.monotonic() - t0) * 1000)

        answer = response.content.strip()

        return {
            "data": {
                "final_ranking": [{"candidate": answer, "score": 1.0}],
                "node_outputs": {},
                "step_timings": {"llm_call": elapsed_ms},
                "terminated_at": "llm_call",
                "diagnostics": {"warnings": []},
                "pipeline_params": pipeline_params,
                "total_time": elapsed_ms,
                "llm_provider": response.model,
            }
        }

    async def check_status(self) -> dict[str, Any]:
        return {"status": "ok", "mode": "llm-only"}

    async def fetch_pipeline(self) -> dict[str, Any]:
        return {"steps": [{"name": "llm_call"}], "name": "llm-only", "version": "1.0"}

    async def init_session(self, terms: list[str]) -> dict[str, Any]:
        return {"status": "skipped", "terms_count": 0}

    async def aclose(self) -> None:
        pass
