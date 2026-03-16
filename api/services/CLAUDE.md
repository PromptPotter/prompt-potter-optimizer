# api/services — Service Layer Patterns

This is where 90% of implementation work happens. All core logic lives here.

## Service catalog

| Service | Purpose |
|---------|---------|
| `prompt_eval.py` | Evaluate prompts against datasets via backend `/matches` endpoint |
| `search/grid_core.py` | Grid search over Layer 1 prompt fields |
| `prompt_optimizer.py` | LLM meta-prompt candidate generation and round winner selection |
| `backend_client.py` | HTTP client for backend APIs (sync, replay, `fetch_pipeline()`) |
| `pipeline_discovery.py` | Pipeline schema factory (`TERMNORM_DEFAULT_SCHEMA` + live metadata merging) |
| `project_store.py` | Facade over focused store modules in `stores/` |
| `campaign/feedback_cycle.py` | Iterative optimization: 3-loop escalation (L1→L2→L3) with patience-based stopping + pluggable escalation checks |
| `campaign/layer_transitions.py` | L2/L3 LLM-driven transitions |
| `dataset_builder.py` | Excel ground-truth loading and train/test splitting |
| `campaign/campaign_init.py` | Campaign initialization: store setup, backend sync, baseline eval |
| `search/smart_search.py` | Sensitivity scan (OAT), adaptive search, `filter_variant_library()` |
| `search/scan_advisor.py` | LLM-driven scan recommendations |
| `search/scan_seeding.py` | Deterministic scan context builder for feedback cycle |
| `search/coverage.py` | Historical index, coverage advisor, scan variant diagnostics |
| `obs/observability_logger.py` | File-based observability (Langfuse-compatible traces, MLflow) |
| `obs/langfuse_client.py` | Langfuse v2 cloud integration (singleton) |
| `obs/langfuse_push.py` | Push eval runs to Langfuse cloud |
| `stores/` | Focused store modules: Backend, Execution, DatasetRun, Dataset, GridPlan, SmartSearch, Campaign |
| `llm_client.py` | Unified LLM abstraction (Groq, OpenAI) with exponential backoff |
| `query_utils.py` | Shared query-parsing utilities |

## Evaluation gateway

`evaluate_prompt_cached()` in `prompt_eval.py` is the **single entry point** for all eval persistence. All evaluation paths (grid search, smart search, feedback cycle) converge here — no data is siloed per campaign.

## Scan baseline restructure

PromptPotter internally decomposes prompts into specific elements (persona, task_intent, etc.) to perturb them independently during sensitivity scanning. The scan baseline is created by LLM restructure (`restructure_context()` in `search/context.py`) — semantically equivalent, just reorganized for optimization.

## Pipeline discovery — ownership principle

**TermNorm owns all registry artifacts** (schemas, prompts). PromptPotter never hardcodes them:

- `TERMNORM_DEFAULT_SCHEMA` carries **structural metadata only**: observation_mappings, langfuse_type, param_keys, runtime. No `output_schema` or `prompt_meta`.
- Registry-owned metadata (`StepOutputSchema`, `StepPromptMeta`) comes exclusively from the live response's `resolved_schemas`/`resolved_prompts` dicts.
- `parse_pipeline_response()` merges live metadata onto known pipeline steps — **live always wins**.

## Observability dual-write

File-first + cloud-optional. `ObsLogger` writes to disk, delegates to `CloudObsBackend`. Cloud failures never crash main flow.

## ProjectStore disk layout

```
.promptpotter/projects/{backend_id}/
  backend.json
  sync/experiments/{id}.json
  executions/{id}.json
  datasets/{name}.json
  dataset_runs/{run_id}.json          # completed eval runs (shared across all eval paths)
  dataset_runs/{run_id}.partial.jsonl  # in-progress (crash recovery)
  dataset_runs.json                   # index of all runs (content_hash -> run_id)
  grid_plans/{plan_id}.json
  smart_search_plans/{plan_id}.json
  campaigns/{campaign_id}.json
  campaigns/{campaign_id}/trial_NNNN.json
  obs/
    langfuse/events.jsonl
    langfuse/traces/{trace_id}.json
    langfuse/scores/{trace_id}.jsonl
    experiments/{campaign_id}/
    prompts/{family}/{version}/
```

## Future / Scaffold

- **`api/evaluators/`** — Evaluator framework: `ExactMatchEvaluator` used by `prompt_eval.py`. ABC base supports future evaluator types.
- **AnthropicClient** — in `llm_client.py`. Available via `get_llm_client("anthropic")`. Not yet wired as default.
