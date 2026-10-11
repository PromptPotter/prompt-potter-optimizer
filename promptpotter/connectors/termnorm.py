from __future__ import annotations

import logging
from typing import Any

import httpx

from promptpotter.config.settings import settings
from promptpotter.connectors.protocol import Connector
from promptpotter.domain.pipeline_overlay import node_config_items
from promptpotter.domain.pipeline_schema import NodeRole
from promptpotter.infrastructure.tls import tls_context

logger = logging.getLogger(__name__)


def termnorm_wire_adapter(
    query: str,
    pipeline_params: dict[str, Any] | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"query": query}

    _pp = pipeline_params or {}

    if "steps" in _pp:
        payload["steps"] = _pp["steps"]

    wire_overrides = dict(node_config_items(_pp))
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
    __slots__ = ("_terms",)

    def __init__(self) -> None:
        self._terms: list[str] | None = None

    async def set_terms(
        self, http: httpx.AsyncClient, base_url: str, terms: list[str]
    ) -> dict[str, Any]:
        if not terms:
            logger.warning(
                "init_session called with empty terms — session won't support /matches",
            )
            return {"status": "skipped", "terms_count": 0}
        if self._terms == terms:
            return {"status": "already_initialized", "terms_count": len(terms)}
        resp = await http.post(f"{base_url}/sessions", json={"terms": terms})
        resp.raise_for_status()
        self._terms = terms
        result: dict[str, Any] = resp.json()
        return result

    async def recover(self, http: httpx.AsyncClient, base_url: str, reply: httpx.Response) -> bool:
        # A backend reload drops its in-memory sessions.
        if _error_envelope(reply).get("code") != "no_session":
            return False
        if not self._terms:
            logger.error(
                "Backend requires session but no terms available. "
                "Call init_session() with terms before running matches."
            )
            return False
        logger.warning("Got 400 (no session) — re-initializing")
        terms = self._terms
        self._terms = None  # clear so idempotency guard re-sends
        await self.set_terms(http, base_url, terms)
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
        try:
            # Reachability, not health: a 5xx past the connect surfaces later on the ledger.
            await http.get(f"{backend_url}/status")
        except httpx.ConnectError as exc:
            return (
                f"{str(exc).strip() or 'connection refused'} at {backend_url}.\n\n"
                "The TermNorm backend ships in a sibling repo. Clone it beside "
                "this checkout, then start it:\n"
                "  TermNorm-excel\\backend-api\\start-server-py-LLMs.bat\n\n"
                "Install guide: docs/manual/02-install.md"
            )
        except httpx.ConnectTimeout:
            return "connect timeout"
    return None


async def _termnorm_version_check(
    http: httpx.AsyncClient,
    base_url: str,
) -> str | None:
    try:
        resp = await http.get(f"{base_url}/status")
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
    except Exception:
        return None
    for key in ("version", "revision", "git_sha"):
        val = data.get(key)
        if val:
            return str(val)
    return None


# `None` opts out of the drift WARN until TermNorm's `GET /status` reports a stable revision.
_EXPECTED_REVISION: str | None = None


def _termnorm_auth_token() -> str | None:
    return settings.TERMNORM_TOKEN or None


CONNECTOR = Connector(
    name="termnorm",
    wire_adapter=termnorm_wire_adapter,
    session_factory=TermNormSession,
    expected_revision=_EXPECTED_REVISION,
    version_check=_termnorm_version_check,
    preflight=_termnorm_preflight,
    auth_token=_termnorm_auth_token,
    # The knee on `llm_only`: past it the wall clock stops falling and the call tail grows.
    max_cells_in_flight=16,
    # A fresh CSV upload skips the retrieval nodes: no Brave Search billing in round 1.
    default_pipeline=("llm_only",),
    node_roles={
        "token_matching": NodeRole.CANDIDATE_SOURCE,
        "fuzzy_matching": NodeRole.CANDIDATE_SOURCE,
    },
    default_optimization=(("degradation_threshold", 0.4),),
    available_models=(
        "openai/gpt-oss-20b",
        "qwen/qwen3.7-flash:nitro",
        "inclusionai/ling-3.0-flash",
        "upstage/solar-pro4",
        "nex-agi/nex-n2-mini",
    ),
    # No `reasoning_effort`: a rung named here follows no model the tenant swaps in.
    default_node_config={
        "llm_only": {
            "config": {
                "provider": "openrouter",
                "model": "openai/gpt-oss-20b",
                "temperature": 0.0,
            },
        },
    },
)


__all__ = ["CONNECTOR", "TermNormSession"]
