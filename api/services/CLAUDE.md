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
- **Smart search**: One-at-a-time axis perturbations against baseline. Coverage advisor checks historical index before backend calls. `filter_variant_library()` in `smart_search.py` drops axes not owned by active pipeline steps before evaluation (e.g. drops `prompt_fields` when `llm_ranking` is inactive).
- **Feedback cycle**: `GrowFilterNode` generates candidates via LLM, `AnalysisEvalNode` evaluates each via `evaluate_prompt_cached()`.

## Pipeline discovery and registry metadata

`pipeline_discovery.py` is the bridge between TermNorm's live `GET /pipeline` response and PromptPotter's `PipelineSchema` model.

### Ownership principle

**TermNorm owns all registry artifacts** (schemas, prompts). PromptPotter never hardcodes them:

- `TERMNORM_DEFAULT_SCHEMA` carries **structural metadata only**: observation_mappings, langfuse_type, param_keys, runtime. No `output_schema` or `prompt_meta`.
- Registry-owned metadata (`StepOutputSchema`, `StepPromptMeta`) comes exclusively from the live response's `resolved_schemas`/`resolved_prompts` dicts.
- `parse_pipeline_response()` merges live metadata onto known pipeline steps — **live always wins** (no `is None` guard).

### Response formats

`parse_pipeline_response()` handles three response formats:

1. **New** (current): top-level `resolved_schemas`/`resolved_prompts` dicts. Nodes reference by `schema_family`/`prompt_family`; resolved objects live in separate top-level sections.
2. **Legacy enriched**: inline `_resolved_schema`/`_resolved_prompt` in each node's config.
3. **Legacy steps list**: minimal `steps` array with name + config keys.

### Scan advisor enrichment

`scan_advisor.py` builds a pipeline anatomy dict from `PipelineSchema` that now includes:
- `output_schema` — field names and descriptions (what each step produces)
- `prompt_meta` — template variables and description (what prompts look like)

This gives the LLM advisor visibility into the full pipeline structure for better axis recommendations.

## ProjectStore disk layout

```
.promptpotter/projects/{backend_id}/
  backend.json
  sync/experiments/{id}.json
  executions/{id}.json
  datasets/train.json                 # Excel ground-truth train split
  datasets/test_processes.json        # Excel ground-truth test split (Processes sheet)
  datasets/test_material.json         # Excel ground-truth test split (Material+Sheet1)
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
