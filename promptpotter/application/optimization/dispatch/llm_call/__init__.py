"""Optimizer LLM chokepoint — every optimizer prompt call goes through here.

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
