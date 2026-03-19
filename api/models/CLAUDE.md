# api/models — Data Models

## PromptState (`prompt_state.py`)

Immutable prompt configuration organized into 3 optimization layers:

- **Layer 1 (Generate)**: persona, task_intent, thinking_style, answer_format, etc. — change every pass
- **Layer 2 (Refine Context)**: context, parameters — adjust when Layer 1 stalls
- **Layer 3 (Modify Plan)**: plan — rarely changed (strategy defaults)
- **Layer 4 (Meta-Optimize)**: the optimizer's own prompts/params — it's just another pipeline. Only pays off with lots of campaign data.

## OptSearchPoint (`opt_search_point.py`)

Optimizer-level search point — the optimizer's configuration at a moment in the feedback cycle. Cross-reference design: holds `content_hashes` linking to target-layer `dataset_runs` produced under this config. Checkpointed in trial JSON as `opt_search_point` after each round.

Fields: `critique_text`, `thinking_styles`, `plan`, `context`, `parameters`, `task_context`, `content_hashes`.

`task_context` is a structured domain context dict (domain, pipeline_purpose, data_characteristics, optimization_goals, key_challenges) decomposed from `TASK_DESCRIPTION` via `restructure_context()`. Set at campaign init, refinable by L2. Flows to L1 candidate generation meta-prompt and L2 refine_context.

## SearchPoint (`search_point.py`)

Frozen model bundling `prompt_state` + `model` + `temperature` + `pipeline_params`. `content_hash(eval_data)` is the dedup key for `evaluate_prompt_cached()`.

See [`../services/CLAUDE.md`](../services/CLAUDE.md) for store layout and evaluation flow.
