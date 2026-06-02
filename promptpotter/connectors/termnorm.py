"""TermNorm connector — wire payload + session lifecycle + experiment extract.

All TermNorm-specific code lives here:

- ``termnorm_wire_adapter`` — outbound payload shape ``{query, steps, node_config}``.
- ``TermNormSession`` — ``POST /sessions`` handshake with ``terms`` array.
- ``_extract_*`` helpers — TermNorm experiment-data → ``(queries, index_terms)``.

Exports the ``CONNECTOR`` binding consumed by :data:`promptpotter.connectors.CONNECTORS`.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from promptpotter.connectors.protocol import BackendUnreachableError, Connector

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Wire payload shape
# ---------------------------------------------------------------------------


def termnorm_wire_adapter(
    query: str,
    pipeline_params: dict[str, Any] | None,
) -> dict[str, Any]:
    """Outbound payload — TermNorm's ``{"query", "steps", "node_config"}``.

    ``pipeline_params`` carries ``steps`` (which nodes to run) plus per-node
    override dicts (e.g. ``{"entity_profiling": {"prompt": "..."}}``)
    which become the ``node_config`` key in the wire payload. Non-dict
    pipeline_param values are dropped with a debug log — the backend
    contract is "everything beyond steps is a per-node config dict".
    """
    payload: dict[str, Any] = {"query": query}

    _pp = pipeline_params or {}

    if "steps" in _pp:
        payload["steps"] = _pp["steps"]

    wire_overrides: dict[str, dict[str, Any]] = {}
    for k, v in _pp.items():
        if k == "steps":
            continue
        if isinstance(v, dict):
            wire_overrides[k] = v
        else:
            logger.debug(
                "termnorm_wire_adapter: dropping non-dict pipeline_param %r=%r",
                k,
                v,
            )

    if wire_overrides:
        payload["node_config"] = wire_overrides

    return payload


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------


class TermNormSession:
    """TermNorm-shaped session lifecycle — implements ``SessionProtocol``.

    ``POST /sessions`` handshake with a ``terms`` array. Keeps
    ``BackendClient.run_query()`` free of session semantics: the HTTP
    transport asks the session to initialize or recover; the session
    owns the indexing terms, the idempotency check, and the reinit
    handshake.
    """

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
        """Reinit the session using stored terms. Returns True on success."""
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
    """Split ``"bom_material / process"`` → ``(bom_material, process)``.

    If no slash is present, process is an empty string.
    """
    if "/" in query:
        last_slash = query.rfind("/")
        primary = query[:last_slash].strip()
        secondary = query[last_slash + 1 :].strip()
    else:
        primary = query.strip()
        secondary = ""
    return primary, secondary


def _build_query_item(query: str, ground_truth: str = "") -> dict[str, Any]:
    """Build a query dict with TermNorm bom_material/process fields."""
    primary, secondary = _split_query(query)
    item: dict[str, Any] = {
        "query": query,
        "bom_material": primary,
        "process": secondary,
        "query_fields": {"bom_material": primary, "process": secondary},
    }
    if ground_truth:
        item["ground_truth"] = ground_truth
    return item


def _extract_index_terms(experiment_data: dict[str, Any]) -> list[str]:
    """Extract unique non-empty ``dataset_entry`` values from mappings."""
    entries = set()
    for m in experiment_data.get("mappings", []):
        entry = m.get("dataset_entry", "").strip()
        if entry and entry != "--":
            entries.add(entry)
    return sorted(entries)


def _extract_ground_truth_map(experiment_data: dict[str, Any]) -> dict[str, str]:
    """Build ``{bom_material: ground_truth}`` from experiment mappings."""
    gt_map: dict[str, str] = {}
    for m in experiment_data.get("mappings", []):
        bom = m.get("bom_material", "")
        entry = m.get("dataset_entry", "").strip()
        if bom and entry and entry != "--":
            gt_map[bom] = entry
    return gt_map


def _extract_queries(experiment_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract queries with valid ground truth — joins evaluation_result queries to mappings via bom_material."""
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


def _resolve_ground_truth(experiment_data: dict[str, Any], query: str) -> str | None:
    return _extract_ground_truth_map(experiment_data).get(_split_query(query)[0])


# ---------------------------------------------------------------------------
# Reachability probe
# ---------------------------------------------------------------------------


async def _termnorm_preflight(backend_url: str) -> None:
    """Ping ``GET /status`` before the launcher accepts a write command.

    Raises :class:`BackendUnreachableError` on TCP-level connect failure
    (``httpx.ConnectError`` / ``ConnectTimeout``). Other shapes
    (``not_implemented`` / ``error`` for a partial response, HTTP 5xx, etc.)
    pass through silently — the runner will surface them via the canonical
    ``ErrorRecord`` if they recur mid-run.
    """
    async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=3.0)) as http:
        try:
            resp = await http.get(f"{backend_url}/status")
        except httpx.ConnectError as exc:
            raise BackendUnreachableError(
                backend_type="termnorm",
                backend_url=backend_url,
                detail=str(exc).strip() or "connection refused",
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
    """Read TermNorm's self-reported version from ``GET /status``.

    Returns the ``version`` field (or ``revision``/``git_sha`` as fallbacks)
    or ``None`` when the field is absent / the call fails. Bootstrap WARNs
    on mismatch with :data:`CONNECTOR.expected_revision`.
    """
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


CONNECTOR = Connector(
    name="termnorm",
    wire_adapter=termnorm_wire_adapter,
    session_factory=TermNormSession,
    extract_experiment=_extract_experiment,
    resolve_ground_truth=_resolve_ground_truth,
    expected_revision=_EXPECTED_REVISION,
    version_check=_termnorm_version_check,
    preflight=_termnorm_preflight,
    # First-tenant default — skip the heavy retrieval/scoring nodes (R4).
    # The production-benchmark pipeline includes them; a fresh CSV upload
    # should not pay Brave Search billing + multi-second latency on round 1.
    default_pipeline=("llm_only",),
    # R4: connector-owned seed for ``campaign.json::optimization``. The required
    # thresholds mirror ``datasets/gsm8k/campaign.json``. (``n_variants`` is the
    # round candidate count, NOT a single-request size lever — the optimizer-LLM
    # TPM relief comes from the OpenRouter optimizer default, not from this.)
    default_optimization=(
        ("n_variants", 3),
        ("improvement_threshold", 0.01),
        ("degradation_threshold", 0.4),
    ),
    # Conservative reasoning rail seeded into a fresh dataset's pipeline.json:
    # origin floor ``low`` + an allowed set with ``medium``/``high`` crossed out,
    # so the optimizer can never escalate ``reasoning_effort`` campaign-wide on a
    # tenant's untuned first run. Operator widens it via the check-in's
    # ``backend.node_config`` if their task genuinely needs deeper reasoning.
    # (Model/provider are already pinned by ``forbidden_axes_strict``.)
    default_node_config={
        "llm_only": {
            "config": {"reasoning_effort": "low", "temperature": 0.0},
            "optimizer": {"param_allowed_values": {"reasoning_effort": ["none", "default", "low"]}},
        },
    },
)


__all__ = ["CONNECTOR", "TermNormSession", "termnorm_wire_adapter"]
