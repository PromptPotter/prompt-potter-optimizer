# api/models -- Data Models

## PromptState (`prompt_state.py`)

Immutable, versioned prompt configuration organized into 3 optimization layers:

- **Layer 1 (Generate)**: `persona`, `task_intent`, `problem_description`, `instruction`, `thinking_style`, `answer_format`, `few_shot_examples` -- change every optimization pass
- **Layer 2 (Refine Context)**: `context`, `parameters` -- adjust when Layer 1 stalls
- **Layer 3 (Modify Plan)**: `plan` -- rarely changed (strategy defaults)

Frozen (`model_config = {"frozen": True}`). Key API:
- `derive(**changes)` -- creates child (sets `parent_id` automatically)
- `render()` -- assembles Layer 1 fields into prompt string
- `diff(a, b)` -- structured diff between two PromptStates

## SearchPoint (`search_point.py`)

A point in the optimization search space. Bundles the four dimensions that fully specify one evaluation:

- `prompt_state: PromptState` -- the prompt configuration (layers 1-3)
- `model: str` -- LLM model identifier
- `temperature: float` -- LLM inference temperature
- `pipeline_params: dict | None` -- backend pipeline overrides (node_overrides)

Frozen (`model_config = {"frozen": True}`). Key API:
- `render()` -- delegates to `prompt_state.render()`
- `content_hash(eval_data)` -- delegates to `eval_content_hash()`
- `derive(**changes)` -- routes PromptState fields to `prompt_state.derive()`, keeps SearchPoint-level fields (model, temperature, pipeline_params) at this level

Relationship: `EvalContext` bundles a SearchPoint with infrastructure (backend_client, store, obs). `CycleConfig` configures the feedback loop that evolves SearchPoints across rounds.

Formula: `f(SearchPoint, PipelineSchema, eval_data) -> scores`

## PipelineSchema (`pipeline_schema.py`)

Backend-agnostic pipeline description with derivation methods: `step_param_keys()`, `obs_extraction_map()`, `template_variables`, `langfuse_type_map()`. Each `PipelineStep` can carry `output_schema` (field names/descriptions) and `prompt_meta` (template variables, prompt template).

Factory in `api/services/pipeline_discovery.py`: `parse_pipeline_response()` parses `GET /pipeline` and merges live metadata (live always wins). `TERMNORM_DEFAULT_SCHEMA` carries structural metadata only (observation_mappings, langfuse_type, param_keys, runtime) -- registry-owned `output_schema`/`prompt_meta` come exclusively from the live response's `resolved_schemas`/`resolved_prompts`.

**ProjectStore** disk layout, store conventions, and evaluation flow details: see [`../services/CLAUDE.md`](../services/CLAUDE.md).
