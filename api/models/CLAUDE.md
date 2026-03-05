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

## PipelineSchema (`pipeline_schema.py`)

Backend-agnostic pipeline description with derivation methods: `step_param_keys()`, `obs_extraction_map()`, `template_variables`, `langfuse_type_map()`. Each `PipelineStep` can carry `output_schema` (field names/descriptions) and `prompt_meta` (template variables, prompt template).

Factory in `api/services/pipeline_discovery.py`: `parse_pipeline_response()` parses `GET /pipeline` and merges live metadata (live always wins). `TERMNORM_DEFAULT_SCHEMA` carries structural metadata only (observation_mappings, langfuse_type, param_keys, runtime) -- registry-owned `output_schema`/`prompt_meta` come exclusively from the live response's `resolved_schemas`/`resolved_prompts`.

**ProjectStore** disk layout, store conventions, and evaluation flow details: see [`../services/CLAUDE.md`](../services/CLAUDE.md).
