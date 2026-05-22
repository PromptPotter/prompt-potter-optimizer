"""PromptPotter-as-connector — the optimizer-of-the-optimizer.

Self-referential connector: an outer PromptPotter cycle optimizes an inner
PromptPotter cycle's meta-prompts (``l1_generate`` / ``l1_critique`` /
``l2_context`` / ``l3_plan``). Each inner cycle is a real campaign run on a
cheap proxy benchmark; the outer L1's mutation surface is the inner
meta-prompt template fields, exposed via ``pipeline_params``.

See ``docs/specs/m12-multi-connector.md`` for the full design —
five-hook contract, three composable inner-cycle proxies
(``first_round_delta`` / ``after_N_rounds_delta`` / ``rounds_to_N``), inner
isolation under ``.runtime/inner/``, cost-realism warning.

**This module is the architectural skeleton.** The five hooks are wired to
the protocol; ``promptpotter_wire_adapter`` shapes the inner-cycle payload;
``PromptPotterSession`` is the in-process noop session. The piece that
actually runs an inner cycle (consuming the wire payload and producing
result dicts with the three proxy metrics) lands in a follow-up — it
requires either a localhost ``/inner/matches`` endpoint on the FastAPI app
or an in-process dispatch path off ``BackendClient``. Until that lands, an
outer cycle pointed at this connector will load the connector and validate
the dataset config but raise ``NotImplementedError`` on the first inner
match request.

Exports the ``CONNECTOR`` binding consumed by
:data:`promptpotter.connectors.CONNECTORS`.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from promptpotter.connectors.protocol import Connector

if TYPE_CHECKING:
    import httpx

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Wire payload shape
# ---------------------------------------------------------------------------


def promptpotter_wire_adapter(
    query: str,
    pipeline_params: dict[str, Any] | None,
) -> dict[str, Any]:
    """Outbound payload describing an inner PromptPotter cycle to run.

    ``query`` is the inner-benchmark task identifier (e.g.
    ``"gsm8k-small/sample-0"``). ``pipeline_params`` carries the outer L1's
    mutation surface — a nested dict keyed by inner-meta-prompt node:

    ```
    {
      "l1_generate":  {"instruction": "...", "decomposition_hint": "..."},
      "l1_critique":  {"negative_critique_framing": "..."},
      "l2_context":   {"refinement_instruction": "..."},
      "l3_plan":      {"replan_trigger": "..."},
    }
    ```

    Each per-node dict overrides template fields on the inner cycle's
    ``optimizer_pipeline.json`` for that run. Non-dict values are dropped
    with a debug log — the contract is "every key is a per-node config
    dict" (matching the TermNorm convention).
    """
    payload: dict[str, Any] = {"query": query}

    _pp = pipeline_params or {}

    meta_prompt_overrides: dict[str, dict[str, Any]] = {}
    for k, v in _pp.items():
        if isinstance(v, dict):
            meta_prompt_overrides[k] = v
        else:
            logger.debug(
                "promptpotter_wire_adapter: dropping non-dict pipeline_param %r=%r",
                k,
                v,
            )

    if meta_prompt_overrides:
        payload["meta_prompt_overrides"] = meta_prompt_overrides

    return payload


# ---------------------------------------------------------------------------
# Session lifecycle (in-process noop)
# ---------------------------------------------------------------------------


class PromptPotterSession:
    """In-process noop session — implements ``SessionProtocol``.

    PromptPotter-as-connector has no remote service, so there's no
    handshake. ``set_terms`` and ``recover`` are no-ops.
    """

    __slots__ = ()

    async def set_terms(
        self,
        http: httpx.AsyncClient,
        base_url: str,
        terms: list[str],
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
    """Inner-benchmark tasks → ``(queries, index_terms)``.

    PromptPotter-self ``experiment_data`` is a small JSON describing the
    inner-benchmark suite: a list of task identifiers + their target
    scores. ``queries`` contains one item per inner task; ``index_terms``
    is empty (no retrieval index on this connector).

    Shape::

        {
          "tasks": [
            {"id": "gsm8k-small/sample-0", "target_score": 0.80, "n_inner_rounds": 3},
            ...
          ]
        }
    """
    tasks = experiment_data.get("tasks", [])
    queries: list[dict[str, Any]] = []
    for t in tasks:
        tid = t.get("id")
        if not tid:
            continue
        queries.append(
            {
                "query": tid,
                "ground_truth": str(t.get("target_score", 0.0)),
                "n_inner_rounds": int(t.get("n_inner_rounds", 3)),
            }
        )
    return queries, []


def _resolve_ground_truth(experiment_data: dict[str, Any], query: str) -> str | None:
    """Return the inner cycle's target score for the given task."""
    for t in experiment_data.get("tasks", []):
        if t.get("id") == query:
            return str(t.get("target_score", ""))
    return None


CONNECTOR = Connector(
    name="promptpotter",
    wire_adapter=promptpotter_wire_adapter,
    session_factory=PromptPotterSession,
    extract_experiment=_extract_experiment,
    resolve_ground_truth=_resolve_ground_truth,
)


__all__ = ["CONNECTOR", "PromptPotterSession", "promptpotter_wire_adapter"]
