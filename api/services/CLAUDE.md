# api/services — Service Layer Patterns

This is where 90% of implementation work happens. All core logic lives here.

## Evaluation gateway

`evaluate_prompt_cached()` in `prompt_eval.py` is the **single entry point** for all eval persistence:

- Content-addressed deduplication via `eval_content_hash()`
- Incremental `.partial.jsonl` writes for crash recovery
- Final result storage in `dataset_runs/`

All evaluation paths converge here — grid search, smart search, and feedback cycle all call `evaluate_prompt_cached()`. No data is siloed per campaign.

### Evaluation details

- **Backend eval**: `backend_reranker_eval()` calls `POST /matches` with rendered ranking prompt override, checks top-ranked candidate against ground truth (exact string match = hit@1)
- **Grid search**: Cartesian product of Layer 1 field variants. `eval_queries_per_point` + `shared_queries` control sampling. Results deduplicated by content hash.
- **Smart search**: One-at-a-time axis perturbations against baseline. Coverage advisor checks historical index before backend calls.
- **Feedback cycle**: `GrowFilterNode` generates candidates via LLM, `AnalysisEvalNode` evaluates each via `evaluate_prompt_cached()`.

## ProjectStore disk layout

```
.promptpotter/projects/{backend_id}/
  backend.json
  sync/experiments/{id}.json
  executions/{id}.json
  dataset_runs/{run_id}.json          # completed eval runs (shared across all eval paths)
  dataset_runs/{run_id}.partial.jsonl  # in-progress (crash recovery)
  dataset_runs.json                   # index of all runs (content_hash -> run_id)
  grid_plans/{plan_id}.json           # persisted grid search plans (resume on restart)
  smart_search_plans/{plan_id}.json   # sensitivity scan plans (axis profiles, scan results)
  campaigns/{campaign_id}.json        # campaign metadata + trial index
  campaigns/{campaign_id}/trial_NNNN.json
  obs/
    langfuse/events.jsonl             # flat navigation log (START HERE for data exploration)
    langfuse/traces/{trace_id}.json
    langfuse/scores/{trace_id}.jsonl
    experiments/{campaign_id}/        # MLflow FileStore format (mlflow ui compatible)
    prompts/{family}/{version}/       # prompt versioning (prompt.txt + metadata.json)
```

## Store conventions

- **Atomic writes**: `base.py` uses tempfile + `os.replace()` for crash safety
- **Lock-protected index updates**: `dataset_run_store.py` serializes concurrent writes
- **Incremental recovery**: `.partial.jsonl` files resume on crash; finalized to `.json` on completion
- **Path safety**: `validate_path_component()` in `base.py` prevents path traversal

## Observability dual-write

File-first + cloud-optional. `ObsLogger` writes to disk, delegates to `CloudObsBackend`. Cloud failures never crash main flow. `OBS_ENABLED` gates everything.

## Key singletons

- `LangfuseLogger.get_instance()` — Langfuse v2 cloud client (reset in tests via `_reset_langfuse` fixture)
- `get_llm_client()` — LLM abstraction (Groq/OpenAI). Swap with `MockLLMClient` in tests.
