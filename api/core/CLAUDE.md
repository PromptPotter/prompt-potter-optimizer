# api/core — Building Block Primitive

## `llm_call.py`

`llm_call()` is the shared LLM interaction primitive. Config-driven from `api/config/optimizer_pipeline.json` with runtime overrides. Used by all optimizer building block nodes (`generate_candidates`, `refine_context`, `modify_plan`, `CritiqueAgent`). `get_node_config(node_name)` loads node configs from the pipeline declaration.

See [`docs/building-blocks.md`](../../docs/building-blocks.md) for the full standard.
