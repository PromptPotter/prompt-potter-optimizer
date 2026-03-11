# api/services — Service Layer Patterns

This is where 90% of implementation work happens. All core logic lives here.

## Service catalog

| Service | Purpose |
|---------|---------|
| `prompt_eval.py` | Evaluate prompts against datasets via backend `/matches` endpoint. Content-addressed deduplication via `eval_content_hash()`. Incremental writes (`.partial.jsonl`) for crash recovery. |
| `search/grid_core.py` | Grid search over Layer 1 prompt fields. Distance-weighted stratified sampling. LLM-assisted context restructuring and result analysis. Grid plan persistence. Skips `init_session` when all points are cached. |
| `prompt_optimizer.py` | LLM meta-prompt candidate generation, round winner selection, improvement suggestions. |
| `backend_client.py` | HTTP client for backend APIs (sync experiments, replay queries, init sessions, `fetch_pipeline()`). |
| `pipeline_discovery.py` | Pipeline schema factory. `TERMNORM_DEFAULT_SCHEMA` (structural only) + `parse_pipeline_response()` merges live `GET /pipeline` metadata. `compute_pipeline_view()` combines backend pipeline + local nodes with 30s TTL cache. |
| `project_store.py` | Facade over focused store modules in `stores/`. File I/O for `.promptpotter/projects/`. |
| `campaign/feedback_cycle.py` | Iterative optimization orchestrator: `CycleConfig` → generate_candidates → evaluate_and_select_winner loop with patience-based stopping. Hierarchical 3-loop escalation (L1 generate → L2 refine_context → L3 modify_plan) when enable_l2/enable_l3 are set. 4-path routing (generate/refine_context/modify_plan/stop). |
| `campaign/layer_transitions.py` | L2 (refine_context) and L3 (modify_plan) LLM-driven transitions for the 3-loop feedback cycle. |
| `dataset_builder.py` | Excel ground-truth loading (`load_excel_ground_truth`) and train/test splitting. Column mapping via `SHEET_COLUMN_MAP`. |
| `campaign/campaign_init.py` | Campaign initialization: project store setup, backend sync, baseline evaluation. |
| `search/smart_search.py` | Sensitivity scan (OAT perturbation), adaptive search (coordinate descent), axis classification. `filter_variant_library()` drops axes not in active pipeline. |
| `search/scan_advisor.py` | LLM-driven scan recommendations. Enriched with output schema fields + prompt metadata from `PipelineSchema`. |
| `search/coverage.py` | Historical index (`build_prompt_result_index`) and coverage advisor. Discovers all stored `dataset_runs` for reuse across optimization threads. |
| `obs/observability_logger.py` | File-based observability: Langfuse-compatible traces, MLflow experiments, prompt versioning. `events.jsonl` flat nav log. |
| `obs/langfuse_client.py` | Langfuse v2 cloud integration (singleton). |
| `obs/langfuse_push.py` | Push eval runs to Langfuse cloud. Single path via `push_all_runs()` (batch, with dataset-item linking). |
| `stores/` | Focused store modules: `BackendStore`, `ExecutionStore`, `DatasetRunStore`, `DatasetStore`, `GridPlanStore`, `SmartSearchStore`, `CampaignStore`. Shared I/O in `stores/base.py`. |
| `llm_client.py` | Unified LLM abstraction (Groq, OpenAI; Anthropic available but not wired as default) with `_OpenAICompatibleClient` base. Global singleton via `get_llm_client()`. Exponential backoff for transient 503/429 errors. |
| `query_utils.py` | Shared query-parsing utilities (e.g. `parse_bom_material()`). |
| `comparison.py` | Statistical comparison (hit@k, McNemar, Wilcoxon). |

## Evaluation gateway

`evaluate_prompt_cached()` in `prompt_eval.py` is the **single entry point** for all eval persistence:

- Content-addressed deduplication via `eval_content_hash()`
- Incremental `.partial.jsonl` writes for crash recovery
- Final result storage in `dataset_runs/`

All evaluation paths converge here — grid search, smart search, and feedback cycle all call `evaluate_prompt_cached()`. No data is siloed per campaign.

### Evaluation details

- **Backend eval**: `backend_reranker_eval()` calls `POST /matches` with rendered ranking prompt override, checks top-ranked candidate against ground truth (exact string match = hit@1)
- **Grid search**: Cartesian product of Layer 1 field variants. `sample_size` + `shared_queries` control sampling. Results deduplicated by content hash.
- **Smart search**: One-at-a-time axis perturbations against baseline. Coverage advisor checks historical index before backend calls. `filter_variant_library()` in `smart_search.py` drops axes not owned by active pipeline steps before evaluation (e.g. drops `prompt_fields` when `llm_ranking` is inactive).
- **Feedback cycle**: `GrowFilterNode` generates candidates via LLM, `AnalysisEvalNode` evaluates each via `evaluate_prompt_cached()`.

## Scan baseline restructure

The sensitivity scan baseline is created by LLM restructure (`restructure_context()` in `search/context.py`) because PromptPotter internally decomposes prompts into specific elements (persona, task_intent, problem_description, instruction, thinking_style, answer_format). The backend prompt may be a monolithic string, but PromptPotter needs these fields separated to perturb them independently during sensitivity scanning. The restructure is semantically equivalent — it doesn't change what the prompt says, just how it's organized for optimization. The notebook's `prepare_scan_baseline()` wraps this into a `SearchPoint` with pipeline_params.

`sensitivity_scan()` takes a `SearchPoint` baseline + flat `scan_variants: dict[str, list]` (axis names mapped to value lists). Axis type is auto-detected: names in `_PROMPT_STATE_FIELDS` → prompt field, otherwise → pipeline param. `select_scan_winner()` composes the best value per improving axis into a single `SearchPoint`.

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

## Future / Scaffold

- **`api/evaluators/`** — Pluggable evaluator framework (EvaluatorBase ABC + ExactMatchEvaluator). No consumers yet.
- **AnthropicClient** — in `llm_client.py`. Available via `get_llm_client("anthropic")`. Not yet wired as default.
