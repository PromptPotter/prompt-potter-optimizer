# api/core — Node Primitive

## `llm_call.py`

`llm_call()` is the shared LLM interaction primitive. Config-driven from `api/config/optimizer_pipeline.json` with runtime overrides. Used by all optimizer nodes (`l1_generate`, `l2_refine_context`, `l3_modify_plan`, `CritiqueAgent`). `get_node_config(node_name)` loads node configs from the pipeline declaration.

See [`docs/node-standard.md`](../../docs/node-standard.md) for the node standard.
