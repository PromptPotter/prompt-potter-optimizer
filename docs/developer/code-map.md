# Code Map

Alphabetical index of Python symbols and the module that owns each. When prose in other docs needs to name a symbol, it should link here instead of embedding the path — this page is the single point of update when code moves.

---

## Domain models

| Symbol | Module |
|--------|--------|
| `AxisImpact` | `promptpotter/application/intelligence/indexes.py` |
| `AxisIndex` | `promptpotter/application/intelligence/indexes.py` |
| `CampaignConfig` | `promptpotter/application/config.py` |
| `Cycle` | `promptpotter/application/optimization/cycle.py` |
| `Decision` | `promptpotter/application/optimization/cycle.py` |
| `Divergence` | `promptpotter/application/optimization/cycle.py` |
| `FailureCluster` | `promptpotter/application/intelligence/indexes.py` |
| `JobSearchPoint` | `promptpotter/domain/search_point.py` |
| `Measurement` | `promptpotter/domain/sample.py` |
| `OptSearchPoint` | `promptpotter/domain/opt_search_point.py` |
| `OptimizationConfig` | `promptpotter/application/config.py` |
| `PipelineNode` | `promptpotter/domain/pipeline_schema.py` |
| `PipelineSchema` | `promptpotter/domain/pipeline_schema.py` |
| `PromptTemplate` | `promptpotter/domain/opt_search_point.py` |
| `QueryRecord` | `promptpotter/application/intelligence/indexes.py` |
| `ReplayContext` | `promptpotter/application/optimization/cycle.py` |
| `RoundResult` | `promptpotter/domain/results.py` |
| `RuntimeFailure` | `promptpotter/domain/analysis.py` |
| `Session` | `promptpotter/application/bootstrap.py` |
| `ValidationFailure` | `promptpotter/domain/analysis.py` |
| `ValueRecord` | `promptpotter/application/intelligence/indexes.py` |

## Optimization loop

| Symbol | Module |
|--------|--------|
| `assemble_dispatch_msg` | `promptpotter/application/optimization/pipeline.py` |
| `classify_result` | `promptpotter/application/optimization/elimination.py` |
| `DegradationCheck` | `promptpotter/application/optimization/elimination.py` |
| `EliminationCheck` | `promptpotter/application/optimization/elimination.py` |
| `EscalationSignal` | `promptpotter/domain/analysis.py` |
| `EscalationState` | `promptpotter/application/optimization/cycle.py` |
| `execute_round` | `promptpotter/application/optimization/l1.py` |
| `is_deprecated` | `promptpotter/application/optimization/elimination.py` |
| `L1ScoringResult` | `promptpotter/application/optimization/l1.py` |
| `l1_generate` | `promptpotter/application/optimization/l1.py` |
| `l1_score` | `promptpotter/application/optimization/l1.py` |
| `LayerCounter` | `promptpotter/application/optimization/cycle.py` |
| `LayerTransition` | `promptpotter/application/optimization/pipeline.py` |
| `llm_call` | `promptpotter/application/optimization/pipeline.py` |
| `load_optimizer_prompt` | `promptpotter/application/optimization/pipeline.py` |
| `record_decision` / `replay_decisions` | `promptpotter/application/optimization/cycle.py` |
| `run_l1_critique` | `promptpotter/application/optimization/pipeline.py` |
| `score_population` | `promptpotter/application/optimization/l1.py` |
| `TransitionResult` | `promptpotter/application/optimization/pipeline.py` |
| `validate_overrides` | `promptpotter/application/optimization/l1.py` |

Meta-prompt template names (`l2_context`, `l3_plan`) are declared as `ClassVar[str]` on transition classes in `optimization/pipeline.py` and registered in `optimizer_pipeline.json` alongside the loop.

## Prompt scheme

| Symbol | Module |
|--------|--------|
| `format_axis_digest_block` | `promptpotter/application/optimization/pipeline.py` |
| `mutate` | `promptpotter/domain/opt_search_point.py` |
| `PROMPT_STRING_FIELDS` | `promptpotter/config/settings.py` |
| `PromptTemplate.render` | `promptpotter/domain/opt_search_point.py` |
| `to_job_search_point` | `promptpotter/domain/opt_search_point.py` |

## Scoring

| Symbol | Module |
|--------|--------|
| `compile_scorer` | `promptpotter/application/scoring/formula.py` |
| `compute_composite_score` | `promptpotter/application/scoring/metrics.py` |
| `Evaluator` | `promptpotter/application/scoring/evaluators.py` |
| `measure_sample` | `promptpotter/application/scoring/sample_measurement.py` |
| `SCORING_FUNCTIONS` | `promptpotter/application/scoring/formula.py` |
| `score_search_point` | `promptpotter/application/scoring/search_point_scorer.py` |
| `scoring_steer` (hot-swap) | `promptpotter/application/scoring/formula.py` |
| `zero_signal_filter` | `promptpotter/application/scoring/formula.py` |

## Intelligence (cross-campaign memory)

| Symbol | Module |
|--------|--------|
| `AxisIndex.digest_for_l1_generate` / `digest_for_l1_critique` / `digest_for_l2` / `digest_for_l3` | `promptpotter/application/intelligence/indexes.py` |
| `evolve_scoring_set` (Rasch + KG) | `promptpotter/application/intelligence/exploration.py` |
| `hard_sample_sorter` | `promptpotter/application/intelligence/hard_sample_sorter.py` |
| `Rasch` (joint logistic-IRT fit) | `promptpotter/application/intelligence/exploration.py` |
| `SampleIndex` | `promptpotter/application/intelligence/indexes.py` |
| `ScoringSetConfig` | `promptpotter/application/intelligence/exploration.py` |

## Infrastructure

| Symbol | Module |
|--------|--------|
| `BackendClient` | `promptpotter/infrastructure/backend.py` |
| `BackendStore` | `promptpotter/infrastructure/store/stores.py` |
| `build_stores` | `promptpotter/infrastructure/store/stores.py` |
| `CampaignPersistenceEmitter` | `promptpotter/infrastructure/persistence.py` |
| `CampaignStore` | `promptpotter/infrastructure/store/stores.py` |
| `MeasurementArchive` | `promptpotter/infrastructure/store/measurement_archive.py` |
| `OpenAICompatibleClient` / `AnthropicClient` | `promptpotter/infrastructure/llm.py` |
| `parse_pipeline_response` | `promptpotter/application/pipeline_discovery.py` |
| `SessionStore` | `promptpotter/infrastructure/store/stores.py` |
| `Stores` | `promptpotter/infrastructure/store/stores.py` |
| Tracing (events + bridge + sinks + Langfuse + backfill) | `promptpotter/infrastructure/tracing.py` |

## Presentation

| Symbol | Module |
|--------|--------|
| `LiveDisplay` | `promptpotter/presentation/views/live.py` |
| `render_hard_sample_heatmap` | `promptpotter/presentation/views/log_md.py` |
| `render_log_md` | `promptpotter/presentation/views/log_md.py` |
| `RunListener` | `promptpotter/application/runner.py` |

## Configuration

| Symbol | Module | Notes |
|--------|--------|-------|
| `APP_VERSION` | `promptpotter/config/settings.py` | Single source for version string |
| `CAMPAIGN_ARTIFACTS` | `tests/test_artifact_parity.py` | Per-cycle artifact allowlist; test owns + enforces |
| `cycle_config_identity` | `promptpotter/application/runner.py` | Cycle-id derivation from JSP + dataset |
| `DATASET_LOADERS` | `promptpotter/application/datasets/datasets.py` | Dataset loader registry |
| `PROMPT_STRING_FIELDS` | `promptpotter/config/settings.py` | Prompt-vs-node-param split |
| `SESSION_ARTIFACTS` | `tests/test_artifact_parity.py` | Per-session artifact allowlist; test owns + enforces |

---

## File paths on disk

| Path | What lives there |
|------|-----------------|
| `.promptpotter/active_session.json` | `{tenant_id, session_id, cycle_id}` pointer |
| `.promptpotter/projects/{tenant}/sessions/{session_id}/` | Per-session workspace (journal, notes, control) |
| `.promptpotter/projects/{tenant}/campaigns/{cycle_id}/` | Per-cycle artifacts (index, dashboard, trials, candidates, rounds, events, langfuse, prompts) |
| `.promptpotter/projects/{tenant}/library/` | The measurement archive (database core) — measurements/, backends, datasets |
| `datasets/{name}/campaign.json` | Campaign hyperparameters per dataset |
| `datasets/{name}/pipeline.json` | Pipeline + model + caps per dataset |
| `datasets/{name}/prompts/{node}.json` | Canonical starting `PromptTemplate` JSON |
| `promptpotter/application/optimization/optimizer_pipeline.json` | Loop registration + meta-prompt families |
