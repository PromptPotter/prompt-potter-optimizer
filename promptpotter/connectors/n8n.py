"""One n8n eval-harness execution per cell: a webhook holding a copy of a production LLM node.

The wire is the harness's, not ``/matches``: ``POST N8N_HARNESS_URL`` with
``{case_id, input, expected, prompt, params}`` answers ``{case_id, answer, raw}``; a row value that
is JSON object text rides as the object. The model is the n8n node's own, so the graph declares no
model and the wire sends none; n8n reports no usage, so a cell is unbounded and unpriced, and only
its round trip is measured.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import httpx

from promptpotter.config.settings import settings
from promptpotter.connectors.protocol import Connector, cell_envelope_of
from promptpotter.domain.connector import AuthScheme
from promptpotter.domain.opt_search_point import PROMPT_FIELDS
from promptpotter.domain.pipeline_overlay import node_config_items
from promptpotter.domain.pipeline_schema import OUTPUT_CONTRACT_KEYS
from promptpotter.shared.hashing import stable_hash
from promptpotter.shared.tls import tls_context

if TYPE_CHECKING:
    from collections.abc import Mapping

    from promptpotter.domain.sample import Sample
    from promptpotter.infrastructure.store.dataset_access import ResolvedDataset
    from promptpotter.infrastructure.store.stores import Stores

HARNESS_NODE = "harness"
ANSWER_KEY = "answer"
# Hashed into every cell's identity: a repointed harness re-keys every banked cell.
INSTRUMENT_KEY = "n8n_harness"

# One harness execution and its resends.
_CELL_ENVELOPE_S = 300.0


def _object_or_text(text: str) -> Any:
    try:
        value = json.loads(text)
    except ValueError:
        return text
    return value if isinstance(value, dict) else text


def n8n_wire_adapter(sample: Sample, pipeline_params: dict[str, Any] | None) -> dict[str, Any]:
    # `prompt: ""` is the contract's word for the harness's production text.
    payload: dict[str, Any] = {
        "case_id": sample.key,
        "input": _object_or_text(sample.query),
        "expected": _object_or_text(sample.ground_truth or ""),
        "prompt": "",
        "params": {},
    }
    for node, cfg in node_config_items(pipeline_params):
        if node != HARNESS_NODE:
            continue
        payload["prompt"] = cfg.get("prompt") or ""
        payload["params"] = {k: cfg[k] for k in OUTPUT_CONTRACT_KEYS if k in cfg}
    return payload


def _harness_reply(body: dict[str, Any]) -> dict[str, Any] | None:
    answer = body.get(ANSWER_KEY)
    # Graded as text: an object answer is the JSON `fields_match` parses back.
    if isinstance(answer, dict):
        answer = json.dumps(answer)
    if not isinstance(answer, str) or not answer.strip():
        return None
    return {ANSWER_KEY: answer, "terminal_node": HARNESS_NODE}


def _pipeline_declaration(_stores: Stores, _experiment: Mapping[str, Any] | None) -> dict[str, Any]:
    return {
        "name": "n8n eval harness",
        "version": "v1",
        "backend_type": "n8n",
        "nodes": {
            HARNESS_NODE: {
                "type": "llm",
                "runtime": "backend",
                "description": "The LLM node an n8n eval harness holds, on the n8n node's model.",
                "prompt_info": {"fields": list(PROMPT_FIELDS)},
                "config": {},
                "optimizer": {"param_keys": []},
            },
        },
        "pipelines": {"default": [HARNESS_NODE]},
    }


def _identity_config(
    _stores: Stores, _dataset: ResolvedDataset, _experiment: Mapping[str, Any] | None
) -> dict[str, dict[str, Any]]:
    return {HARNESS_NODE: {INSTRUMENT_KEY: stable_hash(settings.N8N_HARNESS_URL)}}


async def _preflight(backend_url: str) -> str | None:
    if not backend_url:
        return "N8N_HARNESS_URL is unset: name the harness webhook in .env."
    if not settings.N8N_HARNESS_TOKEN.get_secret_value():
        return "N8N_HARNESS_TOKEN is unset: the harness refuses a request without its secret."
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(5.0, connect=3.0), verify=tls_context()
    ) as http:
        try:
            # Reachability, not health: n8n answers a GET on a POST webhook with a 404.
            await http.get(backend_url)
        except httpx.ConnectError as exc:
            return f"{str(exc).strip() or 'connection refused'} at {backend_url}"
        except httpx.ConnectTimeout:
            return "connect timeout"
    return None


def _endpoint() -> str:
    return settings.N8N_HARNESS_URL


def _auth_token() -> str | None:
    return settings.N8N_HARNESS_TOKEN.get_secret_value() or None


def _auth_scheme() -> AuthScheme:
    return AuthScheme(settings.N8N_HARNESS_AUTH_HEADER, settings.N8N_HARNESS_AUTH_PREFIX)


CONNECTOR = Connector(
    name="n8n",
    wire_adapter=n8n_wire_adapter,
    cell_envelope_s=cell_envelope_of(_CELL_ENVELOPE_S),
    cell_path="",
    read_reply=_harness_reply,
    round_trip_is_latency=True,
    answer_key=ANSWER_KEY,
    pipeline_declaration=_pipeline_declaration,
    identity_config=_identity_config,
    preflight=_preflight,
    endpoint=_endpoint,
    auth_token=_auth_token,
    auth_scheme=_auth_scheme,
)
