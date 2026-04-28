# Code Map

Alphabetical index of Python symbols and the module that owns each. When prose in other docs needs to name a symbol, it should link here instead of embedding the path — this page is the single point of update when code moves.

---

## Domain models

| Symbol | Module |
|--------|--------|
| `AxisImpact` | `promptpotter/application/intelligence/axis_index.py` |
| `CampaignConfig` | `promptpotter/application/campaign/config.py` |
| `Cycle` | `promptpotter/application/optimization/cycle.py` |
| `Decision` | `promptpotter/application/campaign/decisions.py` |
| `Divergence` | `promptpotter/application/campaign/decisions.py` |
| `FailureCluster` | `promptpotter/application/intelligence/axis_index.py` |
| `JobSearchPoint` | `promptpotter/domain/search_point.py` |
| `OptSearchPoint` | `promptpotter/domain/opt_search_point.py` |
| `OptimizationConfig` | `promptpotter/application/campaign/config.py` |
| `PipelineNode` | `promptpotter/domain/pipeline_schema.py` |
| `PipelineSchema` | `promptpotter/domain/pipeline_schema.py` |
| `PromptTemplate` | `promptpotter/domain/opt_search_point.py` |
| `QueryRecord` | `promptpotter/application/intelligence/axis_index.py` |
| `ReplayContext` | `promptpotter/application/campaign/decisions.py` |
| `RoundResult` | `promptpotter/application/optimization/results.py` |
| `RuntimeFailure` | `promptpotter/domain/analysis.py` |
| `Session` | `promptpotter/application/campaign/campaign_setup.py` |
| `AxisIndex` | `promptpotter/application/intelligence/axis_index.py` |
| `ValidationFailure` | `promptpotter/domain/analysis.py` |
| `ValueRecord` | `promptpotter/application/intelligence/axis_index.py` |

## Optimization loop

| Symbol | Module |
|--------|--------|
| `assemble_inbox` | `promptpotter/application/optimization/nodes/inbox_registry.py` |
| `DegradationCheck` | `promptpotter/application/optimization/elimination.py` |
| `EliminationCheck` | `promptpotter/application/optimization/elimination.py` |
| `EscalationSignal` | `promptpotter/domain/analysis.py` |
| `EscalationState` | `promptpotter/application/optimization/cycle.py` |
| `execute_round` | `promptpotter/application/optimization/nodes/l1/execute.py` |
| `classify_result` | `promptpotter/application/optimization/diagnostics.py` |
| `L1ScoringResult` | `promptpotter/application/optimization/nodes/l1/score.py` |
| `l1_generate` | `promptpotter/application/optimization/nodes/l1/generate.py` |
| `l1_score` | `promptpotter/application/optimization/nodes/l1/score.py` |
| `LayerCounter` | `promptpotter/application/optimization/cycle.py` |
| `LayerTransition` | `promptpotter/application/optimization/nodes/layer_transitions.py` |
| `llm_call` | `promptpotter/application/optimization/pipeline.py` |
| `load_optimizer_prompt` | `promptpotter/application/optimization/pipeline.py` |
| `run_l1_critique` | `promptpotter/application/optimization/nodes/l1/critique.py` |
| `score_population` | `promptpotter/application/optimization/nodes/l1/measure.py` |
| `TransitionResult` | `promptpotter/application/optimization/nodes/layer_transitions.py` |
| `validate_overrides` | `promptpotter/application/optimization/nodes/l1/generate.py` |

Meta-prompt template names (`l2_context`, `l3_plan`) are declared as `ClassVar[str]` on transition classes in `nodes/layer_transitions.py` and registered in `optimizer_pipeline.json` alongside the loop.

## Prompt scheme

| Symbol | Module |
|--------|--------|
| `mutate` | `promptpotter/domain/opt_search_point.py` |
| `format_axis_digest_block` | `promptpotter/application/optimization/nodes/formatting.py` |
| `PROMPT_STRING_FIELDS` | `promptpotter/shared/constants.py` |
| `PromptTemplate.render` | `promptpotter/domain/opt_search_point.py` |
| `to_job_search_point` | `promptpotter/domain/opt_search_point.py` |

## Scoring

| Symbol | Module |
|--------|--------|
| `compile_scorer` | `promptpotter/domain/scoring.py` |
| `compute_composite_score` | `promptpotter/application/scoring/metrics.py` |
| `compute_pipeline_metrics` | `promptpotter/application/scoring/metrics.py` |
| `measure_sample` | `promptpotter/application/scoring/sample_measurement.py` |
| `SCORING_FUNCTIONS` | `promptpotter/domain/scoring.py` |
| `score_search_point` | `promptpotter/application/scoring/search_point_scorer.py` |
| `zero_signal_filter` | `promptpotter/application/scoring/zero_signal_filter.py` |

## Intelligence (cross-campaign memory)

| Symbol | Module |
|--------|--------|
| `scoring_set` | `promptpotter/application/intelligence/scoring_set.py` |
| `rasch` | `promptpotter/application/intelligence/rasch.py` |
| `sample_index` | `promptpotter/application/intelligence/sample_index.py` |
| `AxisIndex.digest_for_l1_generate` / `digest_for_l1_critique` / `digest_for_l2` / `digest_for_l3` | `promptpotter/application/intelligence/axis_index.py` |
| `variant_library` | `promptpotter/application/intelligence/variant_library.py` |

## Infrastructure

| Symbol | Module |
|--------|--------|
| `BackendClient` | `promptpotter/infrastructure/backend/client.py` |
| `BackendStore` | `promptpotter/infrastructure/store/stores.py` |
| `build_stores` | `promptpotter/infrastructure/store/stores.py` |
| `CampaignPersistenceEmitter` | `promptpotter/infrastructure/persistence/session_emitter.py` |
| `CampaignStore` | `promptpotter/infrastructure/store/campaign_store.py` |
| `MeasurementArchive` | `promptpotter/infrastructure/store/measurement_archive.py` |
| `Measurement` | `promptpotter/domain/measurement.py` |
| `parse_pipeline_response` | `promptpotter/application/pipeline_discovery.py` |
| `SessionStore` | `promptpotter/infrastructure/store/session_store.py` |
| `Stores` | `promptpotter/infrastructure/store/stores.py` |

## Presentation

| Symbol | Module |
|--------|--------|
| `LiveDisplay` | `promptpotter/presentation/views/live.py` |
| `RunListener` | `promptpotter/application/campaign/runner.py` |

## Configuration

| Symbol | Module | Notes |
|--------|--------|-------|
| `APP_VERSION` | `promptpotter/config/settings.py` | Single source for version string |
| `CAMPAIGN_ARTIFACTS` | `tests/test_artifact_parity.py` | Per-cycle artifact allowlist; test owns + enforces |
| `SESSION_ARTIFACTS` | `tests/test_artifact_parity.py` | Per-session artifact allowlist; test owns + enforces |
| `DATASET_LOADERS` | `promptpotter/application/datasets/builder.py` | Dataset loader registry |

---

## File paths on disk

| Path | What lives there |
|------|-----------------|
| `.promptpotter/active_session.json` | `{tenant_id, session_id, cycle_id}` pointer |
| `.promptpotter/projects/{tenant}/sessions/{session_id}/` | Per-session workspace (journal, notes, control) |
| `.promptpotter/projects/{tenant}/campaigns/{cycle_id}/` | Per-cycle artifacts (index, dashboard, trials, candidates, rounds, events, langfuse, prompts) |
| `.promptpotter/projects/{tenant}/library/` | The measurement archive (database core) — measurements/, samples.json, backends, datasets |
| `datasets/{name}/campaign.json` | Campaign hyperparameters per dataset |
| `datasets/{name}/pipeline.json` | Pipeline + model + caps per dataset |
| `datasets/{name}/prompts/{node}.json` | Canonical starting `PromptTemplate` JSON |
| `promptpotter/application/optimization/optimizer_pipeline.json` | Loop registration + meta-prompt families |
