"""In-process ``llm_only`` connector — a single direct LLM call, no backend server.

The basic case: a one-node pipeline whose only step is an LLM that answers the
query. Instead of posting to a TermNorm ``/matches`` endpoint, this connector
runs the LLM **in this process** via the shared ``in_process`` execution seam
(``Connector.in_process_run``), so the whitelabeled app ships runnable for the
basic case with nothing to download or start.

This deliberately re-introduces the in-process LLM-only execution the deleted
``LLMOnlyAdapter`` once did — but now as a first-class connector on the canonical
``execution`` seam (the one ``BackendClient.run_query`` dispatches on), not a
sidecar adapter routed around the backend. Decided in
``docs/specs/l4-outer-loop.md`` § 1 / Feature A.

The runner reads the **already-rendered + interpolated** prompt out of the node
config (PromptTemplate.render injects it as ``node_config[node]["prompt"]``;
``measure_sample`` interpolates ``{{query}}`` before the call), calls the provider
client, and returns the same ``{"data": {…}}`` shape ``measure_sample`` parses
from an HTTP body — so the scorer reads an in-process result identically to a
remote one.

Exports the ``CONNECTOR`` binding consumed by :data:`promptpotter.connectors.CONNECTORS`.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from promptpotter.connectors.protocol import Connector

logger = logging.getLogger(__name__)

# The single LLM node's name + the terminal ranking key it emits. The committed
# `datasets/{name}/pipeline.json` for an llm_only campaign declares one `ranker`
# node by this name whose output maps to LLM_ONLY_RESULT_KEY, so `terminal_ranking`
# finds the answer here. Kept as constants so the connector output and the dataset
# schema agree on one name.
LLM_ONLY_NODE = "llm_only"
LLM_ONLY_RESULT_KEY = "final_ranking"


# ---------------------------------------------------------------------------
# Wire payload shape
# ---------------------------------------------------------------------------


def llm_only_wire_adapter(
    query: str,
    pipeline_params: dict[str, Any] | None,
) -> dict[str, Any]:
    """Shape the in-process payload — ``{"query", "node_config"}``.

    Mirrors the TermNorm wire shape (so the in_process arm reads node config the
    same way) minus the HTTP ``steps`` list: a single-node pipeline has nothing to
    route. Every per-node dict rides ``node_config``; non-dict params are dropped.
    """
    payload: dict[str, Any] = {"query": query}
    node_config: dict[str, dict[str, Any]] = {}
    for k, v in (pipeline_params or {}).items():
        if k == "steps":
            continue
        if isinstance(v, dict):
            node_config[k] = v
        else:
            logger.debug("llm_only_wire_adapter: dropping non-dict pipeline_param %r=%r", k, v)
    if node_config:
        payload["node_config"] = node_config
    return payload


# ---------------------------------------------------------------------------
# In-process execution arm
# ---------------------------------------------------------------------------


def _pick_llm_node(node_config: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """The LLM node's resolved config — ``llm_only`` by name, else the lone node
    carrying a ``model`` (so a renamed single node still resolves)."""
    if LLM_ONLY_NODE in node_config:
        return node_config[LLM_ONLY_NODE]
    with_model = [cfg for cfg in node_config.values() if isinstance(cfg, dict) and cfg.get("model")]
    return with_model[0] if len(with_model) == 1 else {}


async def llm_only_in_process_run(query: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Run the single LLM node in-process; return the ``{"data": {…}}`` scorer shape.

    Reads the rendered prompt + model/provider/decoding config out of the node
    config, makes one ``chat`` call, and projects the answer onto the terminal
    ranking key + per-node ``step_tokens``/``step_timings`` the dashboard ledger
    and ``classify_result`` read — the same fields TermNorm's wire body carries.
    """
    from promptpotter.infrastructure.llm import get_llm_client

    cfg = _pick_llm_node(payload.get("node_config") or {})
    provider = str(cfg.get("provider") or "")
    model = str(cfg.get("model") or "")
    prompt = str(cfg.get("prompt") or query)
    if not provider or not model:
        raise ValueError(
            "llm_only node config must carry 'provider' and 'model' "
            f"(got provider={provider!r}, model={model!r}). Set them in the dataset's "
            "pipeline.json::nodes.llm_only.config."
        )

    chat_kwargs: dict[str, Any] = {"temperature": float(cfg.get("temperature", 0.0) or 0.0)}
    if cfg.get("max_tokens") is not None:
        chat_kwargs["max_tokens"] = int(cfg["max_tokens"])
    # Forward reasoning_effort when set — the optimizer's llm_call already passes
    # it through chat(**kwargs), so the provider client handles it identically.
    if cfg.get("reasoning_effort"):
        chat_kwargs["reasoning_effort"] = cfg["reasoning_effort"]

    client = get_llm_client(provider)
    start = time.monotonic()
    resp = await client.chat([{"role": "user", "content": prompt}], model=model, **chat_kwargs)
    duration_s = time.monotonic() - start

    answer = resp.content or ""
    usage = resp.usage or {}
    data: dict[str, Any] = {
        # The terminal ranking the scorer reads: the answer is the (single) candidate.
        LLM_ONLY_RESULT_KEY: [answer],
        "terminated_at": LLM_ONLY_NODE,
        "llm_provider": provider,
        "total_time": duration_s,
        "step_timings": {LLM_ONLY_NODE: duration_s},
        "step_tokens": {
            LLM_ONLY_NODE: {
                "input": int(usage.get("prompt_tokens", 0)),
                "output": int(usage.get("completion_tokens", 0)),
                "model": resp.model or model,
            }
        },
    }
    return {"data": data}


# ---------------------------------------------------------------------------
# Session lifecycle (in-process noop)
# ---------------------------------------------------------------------------


class LLMOnlySession:
    """In-process noop session — implements ``SessionProtocol``. No remote service,
    so ``set_terms``/``recover`` are no-ops (mirrors ``PromptPotterSession``)."""

    __slots__ = ()

    async def set_terms(
        self, http: httpx.AsyncClient, base_url: str, terms: list[str]
    ) -> dict[str, Any]:
        return {"status": "noop", "terms_count": len(terms)}

    async def recover(self, http: httpx.AsyncClient, base_url: str) -> bool:
        return True


# ---------------------------------------------------------------------------
# Experiment-data extraction
# ---------------------------------------------------------------------------


def _extract_experiment(
    experiment_data: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    """A flat ``{"tasks": [{"query", "ground_truth"}, …]}`` → ``(queries, [])``.

    No retrieval index (a bare LLM has no candidate library), so ``index_terms``
    is empty — the same impedance-match the connector owns (`connectors/CLAUDE.md`).
    """
    queries: list[dict[str, Any]] = []
    for t in experiment_data.get("tasks", []):
        q = t.get("query")
        if not q:
            continue
        queries.append({"query": q, "ground_truth": str(t.get("ground_truth", ""))})
    return queries, []


CONNECTOR = Connector(
    name="llm_only",
    execution="in_process",
    wire_adapter=llm_only_wire_adapter,
    session_factory=LLMOnlySession,
    extract_experiment=_extract_experiment,
    in_process_run=llm_only_in_process_run,
    # A single LLM node — no candidate library, no heavy retrieval. The fresh-drop
    # default pins a cheap, fast model the operator widens via the check-in.
    default_node_config={
        LLM_ONLY_NODE: {
            "config": {
                "provider": "openrouter",
                "model": "openai/gpt-oss-20b",
                "reasoning_effort": "low",
                "temperature": 0.0,
            },
        },
    },
)


__all__ = [
    "CONNECTOR",
    "LLM_ONLY_NODE",
    "LLM_ONLY_RESULT_KEY",
    "LLMOnlySession",
    "llm_only_in_process_run",
    "llm_only_wire_adapter",
]
