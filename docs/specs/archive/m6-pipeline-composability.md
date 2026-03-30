# Milestone 6: PipelineSchema + Pipeline Composability — Complete

**Version:** 0.10.0 | **Completed:** 2026-03-26 | **Depends on:** M5 Observability

---

## Context

Eliminated hardcoded pipeline assumptions from PromptPotter by introducing `PipelineSchema` — a backend-agnostic pipeline description built entirely from `GET /pipeline`. Cross-repo: TermNorm exposes self-describing pipeline config, PromptPotter consumes it via `parse_pipeline_response()`.

**Wave 4 (workflow nodes) deferred to M9** — the YAML-driven workflow engine is not needed for the current notebook-driven loop.

---

## The 13 Chokepoints

**Resolved (Wave 2):**

| # | Hardcoded Thing | Fix |
|---|----------------|-----|
| 1 | `PIPELINE_STEP_PARAMS` | `schema.step_param_keys()` |
| 2 | `_STEP_PARAM_KEYS` | `schema.step_param_keys()` |
| 3 | `OBS_EXTRACTION_MAP` | `schema.obs_extraction_map()` |
| 6 | `REQUIRED_PIPELINE_KEY` | `schema.required_step` |
| 8 | `REQUIRED_TEMPLATE_VARS` | `schema.template_variables` |
| 9 | `DATASET_NAME` | `schema.dataset_name` |

**Remaining (M9 — require ConnectorProtocol):**

| # | Hardcoded Thing | Fix |
|---|----------------|-----|
| 4 | `split_query_parts()` | Query parser registry |
| 5 | GT mapping (bom→entry) | `schema.query_config` |
| 7 | Hit@1 exact match | `schema.eval_config` |
| 10 | ~~`skip_llm_ranking`~~ | Controlled via `steps` list |
| 11 | `BackendClient` concrete | `ConnectorProtocol` |
| 12 | `ExecutionResultItem.bom_material` | Generic `query_fields` |
| 13 | `extract_session_terms()` | `schema.session_config` |

---

## Waves

| Wave | Scope | Status |
|------|-------|--------|
| 0 | TermNorm fuzzy cleanup (single threshold) | Complete |
| 1 | `GET /pipeline` endpoint (6-step config) | Complete |
| 2 | PipelineSchema model + schema derivation (6 chokepoints resolved) | Complete |
| 3 | Unified tracing (one trace per query) | Complete |
| 4 | Workflow nodes (runtime_config, DatasetLoadNode, FeedbackCycleNode, ScanNode) | Deferred → M9 |
| 5 | Composite scoring + rank display | Complete |
| 6 | `node_type`, `IntermediateMetric`, `compute_pipeline_metrics()` | Complete |
| 7 | Consolidated pipeline control surfaces (`pipeline_params`) | Complete |

---

## Key Design Artifacts

**Node type taxonomy:** `candidate_source` (produces candidate set), `ranker` (ranks/selects), `enricher` (adds context), `cache` (short-circuits). `PipelineNode.node_type` drives auto-wired intermediate metrics via `compute_pipeline_metrics()`.

**Composite scoring:** `composite = accuracy_weight * accuracy + sum(metric.weight * metric.value)`. When PipelineSchema has typed nodes, delegates to `compute_pipeline_metrics()`; otherwise uses hardcoded `token_recall`.

**Self-describing pipeline:** `TERMNORM_DEFAULT_SCHEMA` deleted. TermNorm's `pipeline.json` carries full step metadata + `optimizer` sub-object. `parse_pipeline_response()` builds PipelineSchema entirely from `GET /pipeline`.

---

## Exit Gate — Passed

MVP performance validation — TermNorm accuracy from ~15% to >90%. Composite scoring active. No hardcoded pipeline step names in service layer.
