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

from promptpotter.connectors.protocol import Connector

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

    wire_overrides: dict[str, dict] = {}
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
        return resp.json()

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


def _extract_index_terms(experiment_data: dict) -> list[str]:
    """Extract unique non-empty ``dataset_entry`` values from mappings."""
    entries = set()
    for m in experiment_data.get("mappings", []):
        entry = m.get("dataset_entry", "").strip()
        if entry and entry != "--":
            entries.add(entry)
    return sorted(entries)


def _extract_ground_truth_map(experiment_data: dict) -> dict[str, str]:
    """Build ``{bom_material: ground_truth}`` from experiment mappings."""
    gt_map: dict[str, str] = {}
    for m in experiment_data.get("mappings", []):
        bom = m.get("bom_material", "")
        entry = m.get("dataset_entry", "").strip()
        if bom and entry and entry != "--":
            gt_map[bom] = entry
    return gt_map


def _extract_queries(experiment_data: dict) -> list[dict[str, Any]]:
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


def _extract_experiment(experiment_data: dict) -> tuple[list[dict], list[str]]:
    return _extract_queries(experiment_data), _extract_index_terms(experiment_data)


def _resolve_ground_truth(experiment_data: dict, query: str) -> str | None:
    return _extract_ground_truth_map(experiment_data).get(_split_query(query)[0])


CONNECTOR = Connector(
    name="termnorm",
    wire_adapter=termnorm_wire_adapter,
    session_factory=TermNormSession,
    extract_experiment=_extract_experiment,
    resolve_ground_truth=_resolve_ground_truth,
)
