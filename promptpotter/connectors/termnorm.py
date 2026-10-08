"""TermNorm connector — all TermNorm-specific code, exporting the ``CONNECTOR`` binding."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from promptpotter.config.settings import settings
from promptpotter.connectors.protocol import Connector
from promptpotter.domain.pipeline_overlay import node_config_items
from promptpotter.domain.pipeline_schema import NodeRole

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Wire payload shape
# ---------------------------------------------------------------------------


def termnorm_wire_adapter(
    query: str,
    pipeline_params: dict[str, Any] | None,
) -> dict[str, Any]:
    """Outbound payload — TermNorm's ``{"query", "steps", "node_config"}``. ``node_config_items`` owns the
    reserved-key walk: the backend contract is "everything beyond ``steps`` is a per-node config dict"."""
    payload: dict[str, Any] = {"query": query}

    _pp = pipeline_params or {}

    if "steps" in _pp:
        payload["steps"] = _pp["steps"]

    wire_overrides = dict(node_config_items(_pp))
    if wire_overrides:
        payload["node_config"] = wire_overrides

    return payload


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------


def _error_envelope(reply: httpx.Response) -> dict[str, Any]:
    """TermNorm's error body: ``{status, message, code, detail}`` from the global handler in its
    ``main.py``, ``detail`` being whatever the raising handler passed."""
    try:
        body = reply.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


class TermNormSession:
    """TermNorm-shaped ``SessionProtocol``. Keeps ``BackendClient.run_query()`` free of session semantics:
    the transport asks the session to init or recover; the session owns terms, idempotency, reinit."""

    __slots__ = ("_terms",)

    def __init__(self) -> None:
        self._terms: list[str] | None = None

    async def set_terms(
        self, http: httpx.AsyncClient, base_url: str, terms: list[str]
    ) -> dict[str, Any]:
        """Install terms and ``POST /sessions``. Idempotent for identical terms."""
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
        # A backend reload drops its in-memory sessions, so this self-heals rather than aborting
        # the round.
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


# ---------------------------------------------------------------------------
# Reachability probe
# ---------------------------------------------------------------------------


async def _termnorm_preflight(backend_url: str) -> str | None:
    """Ping ``GET /status`` before the launcher accepts a write command; only a TCP-level connect failure
    is down. Every other shape passes silently — the runner surfaces it as an ``ErrorRecord`` if it recurs."""
    async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=3.0)) as http:
        try:
            # 5xx responses past the connect are operator-visible later via the
            # ledger; preflight is concerned with reachability, not health.
            await http.get(f"{backend_url}/status")
        except httpx.ConnectError as exc:
            # Where to GET this backend is TermNorm's fact, not the launcher's: stated at the
            # ingress it prints for every connector, over a probe that named its own cause.
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


# ---------------------------------------------------------------------------
# Revision check
# ---------------------------------------------------------------------------


async def _termnorm_version_check(
    http: httpx.AsyncClient,
    base_url: str,
) -> str | None:
    """TermNorm's self-reported version (``version``, else ``revision``/``git_sha``), or ``None``. Init
    WARNs on a mismatch with ``CONNECTOR.expected_revision``."""
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


# Pin once TermNorm exposes a stable ``version``/``revision``/``git_sha`` in
# ``GET /status``. ``None`` opts out of the drift WARN until then.
_EXPECTED_REVISION: str | None = None


# ---------------------------------------------------------------------------
# Wire credential
# ---------------------------------------------------------------------------


def _termnorm_auth_token() -> str | None:
    """TermNorm's bearer token, read per client construction so an env change lands without a reimport.
    TermNorm gates it behind its own flag, so an unset token is the normal local posture."""

    return settings.TERMNORM_TOKEN or None


CONNECTOR = Connector(
    name="termnorm",
    wire_adapter=termnorm_wire_adapter,
    session_factory=TermNormSession,
    expected_revision=_EXPECTED_REVISION,
    version_check=_termnorm_version_check,
    preflight=_termnorm_preflight,
    auth_token=_termnorm_auth_token,
    # The knee measured on `llm_only`: past 16 in flight the wall clock stops falling and the call
    # tail grows. The web-search pipeline was not measured, and Brave rate-limits per key.
    max_cells_in_flight=16,
    # First-tenant default — skip the heavy retrieval/scoring nodes (R4).
    # The production-benchmark pipeline includes them; a fresh CSV upload
    # should not pay Brave Search billing + multi-second latency on round 1.
    default_pipeline=("llm_only",),
    # The retrieval nodes rank each query against the session's term index — so a
    # pipeline that includes one needs a candidate library, surfaced as a
    # dependency the operator drops in place. ``llm_only`` (the fresh-upload
    # default) lists neither, so no dependency appears until the operator selects
    # the full pipeline.
    node_roles={
        "token_matching": NodeRole.CANDIDATE_SOURCE,
        "fuzzy_matching": NodeRole.CANDIDATE_SOURCE,
    },
    # R4: connector-owned seed for ``campaign.json::optimization`` — the bench's required
    # threshold, mirroring ``datasets/gsm8k/campaign.yaml``. An optimizer's knobs are its
    # manifest's, so a connector seeds none.
    default_optimization=(("degradation_threshold", 0.4),),
    # The MENU, which is not the same question as the origin's model below: this is what a
    # tenant may pick from, that is where they start. Without it the committed pipeline.yaml
    # carries no `available_models` and the check-in's model list has zero options — so an
    # operator could see their model and not change it.
    # The three below price at or under gpt-oss-20b on both axes and postdate it.
    available_models=(
        "openai/gpt-oss-20b",
        "qwen/qwen3.7-flash:nitro",
        "inclusionai/ling-3.0-flash",
        "upstage/solar-pro4",
        "nex-agi/nex-n2-mini",
    ),
    # A fresh drop's committed pipeline.yaml must OWN its task model — the dataset
    # is the authority for what the backend runs, never the backend's own hidden
    # GET /pipeline default (which would silently pick the heavy groq/120b). This
    # seed is copied verbatim into the new dataset's file by ``merge_pipeline_overlay``,
    # so the dataset owns ``openrouter/gpt-oss-20b`` explicitly, visible on disk.
    # No ``reasoning_effort``: a rung named here would follow no model the tenant swaps in, so
    # the origin's starts at the picked model's floor (``pipeline_resolve::_apply_model_floors``).
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
