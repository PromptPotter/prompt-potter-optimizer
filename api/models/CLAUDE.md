# api/models — Data Models

## PromptState (`prompt_state.py`)

Immutable prompt configuration organized into 3 optimization layers:

- **Layer 1 (Generate)**: persona, task_intent, thinking_style, answer_format, etc. — change every pass
- **Layer 2 (Refine Context)**: optimizer_params — adjust when Layer 1 stalls. `task_context` lives on OptSearchPoint (not PromptState).
- **Layer 3 (Modify Plan)**: plan — rarely changed (strategy defaults)
- **Layer 4 (Meta-Optimize)**: the optimizer's own prompts/params — it's just another pipeline. Only pays off with lots of campaign data.

## OptSearchPoint (`opt_search_point.py`)

Optimizer-level search point — the optimizer's configuration at a moment in the feedback cycle. Cross-reference design: holds `content_hashes` linking to target-layer `dataset_runs` produced under this config. Checkpointed in trial JSON as `opt_search_point` after each round.

Fields: `critique_text` (formatted string), `thinking_styles`, `plan`, `optimizer_params`, `task_context`, `l2_directive`, `content_hashes`. The feedback cycle also carries `critique` (5-field dict: `positive_critique`, `negative_critique`, `priority_fix`, `suggested_axes`, `summary`) on `_LoopState` — fed to both L1 Generate and L2 Refine.

`l2_directive` is a 2-3 sentence string carrying L2's diagnostic reasoning and action guidance for L1 Generate. Sliding window of 1: set after L2 runs, cleared when L2 doesn't fire. Injected into L1's meta-prompt as primary guidance signal. L2 also receives the previous directive (evolve or supersede) and the critique text (to build on rather than re-analyze).

`task_context` is a structured domain context dict (domain, pipeline_purpose, data_characteristics, optimization_goals, key_challenges, raw_description) decomposed from `TASK_DESCRIPTION` via `decompose_task_context()`. Set at campaign init, refinable by L2. `PromptState.context` is auto-synced from `task_context` — one source of truth. Flows to L1 candidate generation meta-prompt and L2 refine_context.

## SearchPoint (`search_point.py`)

Frozen model bundling `prompt_state` + `model` + `temperature` + `pipeline_params`. `content_hash(eval_data)` is the dedup key for `evaluate_prompt_cached()`.

See [`../services/CLAUDE.md`](../services/CLAUDE.md) for store layout and evaluation flow.
