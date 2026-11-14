from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from typing import Any

import httpx

from promptpotter.config.settings import settings
from promptpotter.connectors.protocol import (
    Connector,
    Endpoint,
    UploadDefaults,
    cell_envelope_of,
)
from promptpotter.domain.pipeline_params import STEPS_KEY, PipelineParams
from promptpotter.domain.sample import Sample
from promptpotter.shared.tls import tls_context

logger = logging.getLogger(__name__)

# One match and its resends, a backend outage's wait included.
_CELL_ENVELOPE_S = 600.0


def termnorm_wire_adapter(
    sample: Sample,
    pipeline_params: PipelineParams,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"query": sample.query}

    if pipeline_params.steps is not None:
        payload[STEPS_KEY] = list(pipeline_params.steps)

    wire_overrides = pipeline_params.laid_nodes
    if wire_overrides:
        payload["node_config"] = wire_overrides

    return payload


def _error_envelope(reply: httpx.Response) -> dict[str, Any]:
    # `{status, message, code, detail}`, from the global handler in TermNorm's `main.py`.
    try:
        body = reply.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


class TermNormSession:
    __slots__ = ("_reposting", "_terms")

    def __init__(self) -> None:
        self._terms: list[str] = []
        # A cell meeting `no_session` while it is out awaits it: a reload re-sends the terms once.
        self._reposting: asyncio.Task[None] | None = None

    async def open(
        self,
        http: httpx.AsyncClient,
        base_url: str,
        samples: Sequence[Sample],
        library: Sequence[str],
    ) -> None:
        # The term index: every label a row answers with, and the candidate library.
        truths = {s.ground_truth for s in samples if s.ground_truth}
        self._terms = sorted(truths | set(library))
        if not self._terms:
            logger.warning("no terms to index — the session won't support /matches")
            return
        await self._post_terms(http, base_url)

    async def _post_terms(self, http: httpx.AsyncClient, base_url: str) -> None:
        resp = await http.post(f"{base_url}/sessions", json={"terms": self._terms})
        resp.raise_for_status()

    async def _repost(self, http: httpx.AsyncClient, base_url: str) -> None:
        try:
            await self._post_terms(http, base_url)
        finally:
            self._reposting = None

    async def recover(self, http: httpx.AsyncClient, base_url: str, reply: httpx.Response) -> bool:
        # A backend reload drops its in-memory sessions.
        if _error_envelope(reply).get("code") != "no_session" or not self._terms:
            return False
        if self._reposting is None:
            logger.warning("Got 400 (no session) — re-initializing")
            self._reposting = asyncio.create_task(self._repost(http, base_url))
        await asyncio.shield(self._reposting)
        return True

    def resend_refused(self, reply: httpx.Response) -> str | None:
        """``detail.retryable: false`` — TermNorm's deadline on a provider request."""
        detail = _error_envelope(reply).get("detail")
        if not isinstance(detail, dict) or detail.get("retryable") is not False:
            return None
        return f"{detail.get('error_code')}: {detail.get('message')}"


async def _termnorm_preflight(backend_url: str) -> str | None:
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(5.0, connect=3.0), verify=tls_context()
    ) as http:
        # Reachability, not health: a 5xx past the connect surfaces later on the ledger.
        await http.get(f"{backend_url}/status")
    return None


async def _termnorm_version_check(
    http: httpx.AsyncClient,
    base_url: str,
) -> str | None:
    resp = await http.get(f"{base_url}/status")
    resp.raise_for_status()
    data: dict[str, Any] = resp.json()
    for key in ("version", "revision", "git_sha"):
        val = data.get(key)
        if val:
            return str(val)
    return None


# `None` opts out of the drift WARN until TermNorm's `GET /status` reports a stable revision.
_EXPECTED_REVISION: str | None = None


def _termnorm_auth_token() -> str | None:
    return settings.TERMNORM_TOKEN.get_secret_value() or None


CONNECTOR = Connector(
    name="termnorm",
    wire_adapter=termnorm_wire_adapter,
    cell_envelope_s=cell_envelope_of(_CELL_ENVELOPE_S),
    cell_payer="host",
    # Each node serves its `spend_bound` on the pipeline answer.
    sent_spend_bound=None,
    cells_priced=True,
    session_factory=TermNormSession,
    expected_revision=_EXPECTED_REVISION,
    version_check=_termnorm_version_check,
    preflight=_termnorm_preflight,
    endpoint=Endpoint("TERMNORM_ENDPOINTS", lambda: settings.TERMNORM_ENDPOINTS),
    auth_token=_termnorm_auth_token,
    # The knee on `llm_only`: past it the wall clock stops falling and the call tail grows.
    max_cells_in_flight=16,
    uploads=UploadDefaults(
        # A fresh CSV upload skips the retrieval nodes: no Brave Search billing in round 1.
        chain=("llm_only",),
        available_models=(
            "openai/gpt-oss-20b",
            "qwen/qwen3.7-flash:nitro",
            "inclusionai/ling-3.0-flash",
            "upstage/solar-pro4",
        ),
    ),
    # Only what `GET /pipeline` does not serve: its axes and mappings are the backend's.
    node_templates={
        "llm_only": {
            "description": "One LLM call answers each row.",
            # No `reasoning_effort`: a rung named here follows no model the tenant swaps in.
            "config": {"provider": "openrouter", "model": "openai/gpt-oss-20b", "temperature": 0.0},
            "prompt_info": {"rewrite_slot": "instruction"},
        },
    },
)
