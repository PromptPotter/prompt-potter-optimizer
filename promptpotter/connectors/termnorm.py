"""TermNorm connector — all TermNorm-specific code, exporting the ``CONNECTOR`` binding."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from promptpotter.connectors.protocol import BackendUnreachableError, Connector
from promptpotter.domain.pipeline_overlay import node_config_items
from promptpotter.domain.pipeline_schema import NodeType

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

    async def recover(self, http: httpx.AsyncClient, base_url: str) -> bool:
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


# ---------------------------------------------------------------------------
# Experiment-data extraction
# ---------------------------------------------------------------------------


def _split_query(query: str) -> tuple[str, str]:
    """Split ``"bom_material / process"``; no slash ⇒ an empty process."""
    if "/" in query:
        last_slash = query.rfind("/")
        primary = query[:last_slash].strip()
        secondary = query[last_slash + 1 :].strip()
    else:
        primary = query.strip()
        secondary = ""
    return primary, secondary


def _build_query_item(query: str) -> dict[str, Any]:
    primary, secondary = _split_query(query)
    return {
        "query": query,
        "bom_material": primary,
        "process": secondary,
        "query_fields": {"bom_material": primary, "process": secondary},
    }


def _extract_index_terms(experiment_data: dict[str, Any]) -> list[str]:
    entries = set()
    for m in experiment_data.get("mappings", []):
        entry = m.get("dataset_entry", "").strip()
        if entry and entry != "--":
            entries.add(entry)
    return sorted(entries)


def _extract_ground_truth_map(experiment_data: dict[str, Any]) -> dict[str, str]:
    gt_map: dict[str, str] = {}
    for m in experiment_data.get("mappings", []):
        bom = m.get("bom_material", "")
        entry = m.get("dataset_entry", "").strip()
        if bom and entry and entry != "--":
            gt_map[bom] = entry
    return gt_map


def _extract_queries(experiment_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Queries with valid ground truth — joins evaluation_result queries to mappings via bom_material."""
    gt_map = _extract_ground_truth_map(experiment_data)

    runs = experiment_data.get("runs", [])
    if not runs:
        return []

    queries: list[dict[str, Any]] = []
    for er in runs[0].get("evaluation_results", []):
        query = er["query"]
        primary, _ = _split_query(query)

        if primary not in gt_map:
            continue

        queries.append(
            {
                **_build_query_item(query),
                "ground_truth": gt_map[primary],
                "original_predicted": er.get("predicted", ""),
                "original_latency_ms": er.get("latency_ms", 0),
                "original_confidence": er.get("confidence", 0),
            }
        )

    return queries


def _extract_experiment(
    experiment_data: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    return _extract_queries(experiment_data), _extract_index_terms(experiment_data)


# ---------------------------------------------------------------------------
# Reachability probe
# ---------------------------------------------------------------------------


async def _termnorm_preflight(backend_url: str) -> None:
    """Ping ``GET /status`` before the launcher accepts a write command; only a TCP-level connect failure
    raises. Every other shape passes silently — the runner surfaces it as an ``ErrorRecord`` if it recurs."""
    async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=3.0)) as http:
        try:
            resp = await http.get(f"{backend_url}/status")
        except httpx.ConnectError as exc:
            raise BackendUnreachableError(
                backend_type="termnorm",
                backend_url=backend_url,
                # Where to GET this backend is TermNorm's fact, not the launcher's: stated at the
                # ingress it prints for every connector, over a probe that named its own cause.
                detail=(
                    f"{str(exc).strip() or 'connection refused'} at {backend_url}.\n\n"
                    "The TermNorm backend ships in a sibling repo. Clone it beside "
                    "this checkout, then start it:\n"
                    "  TermNorm-excel\\backend-api\\start-server-py-LLMs.bat\n\n"
                    "Install guide: docs/manual/02-install.md"
                ),
            ) from exc
        except httpx.ConnectTimeout as exc:
            raise BackendUnreachableError(
                backend_type="termnorm",
                backend_url=backend_url,
                detail="connect timeout",
            ) from exc
        # 5xx responses past the connect are operator-visible later via the
        # ledger; preflight is concerned with reachability, not health.
        del resp


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
    from promptpotter.config.settings import settings

    return settings.TERMNORM_TOKEN or None


CONNECTOR = Connector(
    name="termnorm",
    wire_adapter=termnorm_wire_adapter,
    session_factory=TermNormSession,
    extract_experiment=_extract_experiment,
    expected_revision=_EXPECTED_REVISION,
    version_check=_termnorm_version_check,
    preflight=_termnorm_preflight,
    auth_token=_termnorm_auth_token,
    # First-tenant default — skip the heavy retrieval/scoring nodes (R4).
    # The production-benchmark pipeline includes them; a fresh CSV upload
    # should not pay Brave Search billing + multi-second latency on round 1.
    default_pipeline=("llm_only",),
    # The retrieval nodes rank each query against the session's term index — so a
    # pipeline that includes one needs a candidate library, surfaced as a
    # dependency the operator drops in place. ``llm_only`` (the fresh-upload
    # default) lists neither, so no dependency appears until the operator selects
    # the full pipeline.
    node_types={
        "token_matching": NodeType.CANDIDATE_SOURCE,
        "fuzzy_matching": NodeType.CANDIDATE_SOURCE,
    },
    # R4: connector-owned seed for ``campaign.json::optimization``. The required
    # thresholds mirror ``datasets/gsm8k/campaign.json``. (``n_variants`` is the
    # round candidate count, NOT a single-request size lever — the optimizer-LLM
    # TPM relief comes from the OpenRouter optimizer default, not from this.)
    default_optimization=(
        ("n_variants", 3),
        ("degradation_threshold", 0.4),
    ),
    # A fresh drop's committed pipeline.yaml must OWN its task model — the dataset
    # is the authority for what the backend runs, never the backend's own hidden
    # GET /pipeline default (which would silently pick the heavy groq/120b). This
    # seed is copied verbatim into the new dataset's file by ``merge_pipeline_overlay``,
    # so the dataset owns ``openrouter/gpt-oss-20b`` explicitly, visible on disk.
    # Conservative reasoning rail rides alongside: origin floor ``low`` + an allowed
    # set with ``medium``/``high`` crossed out, so the optimizer can never escalate
    # ``reasoning_effort`` campaign-wide on a tenant's untuned first run. Operator
    # widens model or reasoning via the check-in's ``backend.node_config``.
    default_node_config={
        "llm_only": {
            "config": {
                "provider": "openrouter",
                "model": "openai/gpt-oss-20b",
                "reasoning_effort": "low",
                "temperature": 0.0,
            },
            "optimizer": {"param_allowed_values": {"reasoning_effort": ["none", "default", "low"]}},
        },
    },
)


__all__ = ["CONNECTOR", "TermNormSession"]
