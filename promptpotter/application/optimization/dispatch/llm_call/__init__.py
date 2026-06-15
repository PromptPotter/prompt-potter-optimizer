"""Optimizer LLM chokepoint — every meta-prompt call goes through here.

* :mod:`call` — :func:`llm_call` (429-retry + heartbeat + token emit +
  recorder + cross-cycle cache) and :func:`run_optimizer_node`
  (template → compile → call → parse).
* :mod:`prompts` — optimizer-pipeline manifest loading: the schema loader
  (:func:`get_optimizer_schema`) and Langfuse-production → local-manifest
  prompt loading (:func:`load_optimizer_prompt` + the campaign-identity
  hashing helpers).

Every optimizer node dispatches a Pydantic response model from
:data:`schemas.OPTIMIZER_RESPONSE_MODELS` so the LLM call returns a typed
instance on ``LLMResponse.parsed`` — server-side ``response_format``
validation plus client-side ``model_validate`` on the parsed JSON.
"""

from __future__ import annotations

from promptpotter.application.optimization.dispatch.llm_call.call import (
    LLMCallContext,
    llm_call,
    run_optimizer_node,
)
from promptpotter.application.optimization.dispatch.llm_call.prompts import (
    combined_optimizer_prompt_hash,
    compute_optimizer_prompt_hashes,
    get_optimizer_schema,
    list_optimizer_prompts,
    load_optimizer_prompt,
    optimizer_model,
    optimizer_node_config,
)

__all__ = [
    "LLMCallContext",
    "combined_optimizer_prompt_hash",
    "compute_optimizer_prompt_hashes",
    "get_optimizer_schema",
    "list_optimizer_prompts",
    "llm_call",
    "load_optimizer_prompt",
    "optimizer_model",
    "optimizer_node_config",
    "run_optimizer_node",
]
