# Roadmap: PromptPotter Optimizer

**Version:** 0.9.0
**Date:** 2026-02-27
**Status:** Active
**Depends on:** [WBS v0.9.0](wbs.md)

---

## Milestones

| Milestone | Focus | Status |
|-----------|-------|--------|
| M0 | Specifications | Complete |
| M1 | Foundation (PromptState, ProjectStore, comparison, CI) | Complete |
| M2 | Core Optimizer (eval, grid search, prompt optimizer, notebook) | Complete |
| M3 | Optimization Infrastructure | Complete |
| M4 | Integration and Polish (reclassified — absorbed into M3–M5) | Complete |
| M5 | Observability Layer | Complete |
| M6 | CWL Workflow Migration + PipelineSchema Foundation | Planned |
| M7 | Multi-Connector Architecture | Future |

---

## M3: Optimization Infrastructure -- Complete

**Complete:**
- Optimizer nodes (InitNode, GrowFilterNode, AnalysisEvalNode)
- Feedback cycle with 3-path routing, patience-based stopping, progress callbacks
- Campaign registry + CampaignStore
- Langfuse per-trial tracing with scores
- Sensitivity scan + coverage advisor
- Grid search refactor (search module)
- Campaign init service
- Notebook integration with progress display
- Data loop (all eval paths -> shared dataset_runs)
- Service layer cleanup (campaign_lib dedup, ~1,300 LOC removed across 4 passes)

**Exit gate:** End-to-end optimization with progress output, eval data feeds back into scans, Langfuse traces with scores.

---

## M4: Integration and Polish -- Complete (Reclassified)

Originally planned for variant comparison, Streamlit, Docker, and docs. Cleanup and polish work was absorbed into M3–M5 sessions. Remaining items redistributed:

| Original Item | New Home |
|--------------|----------|
| 4.1 TermNorm Variant Comparison (SC5) | Backlog (needs ConnectorProtocol + real pipeline comparison infrastructure) |
| 4.2 Streamlit Dashboard | P2.3 Backlog |
| 4.3 Docker Compose update | M6 WP 6.7 |
| 4.4 Documentation update | M6 exit criteria |

---

## M5: Observability Layer -- Complete

Adopted TermNorm-excel's zero-dependency file-based patterns for production-grade logging. Three layers of human-accessible data: `events.jsonl` (flat nav log), Langfuse traces (structured detail), MLflow experiments (metrics viewer). See [`docs/obs-guide.md`](../obs-guide.md) for data exploration.

**Complete:**
- `ObsLogger` with Langfuse-compatible traces, MLflow experiments, prompt versioning
- `events.jsonl` flat navigation log for human data exploration
- LLM retry logic (exponential backoff for Groq 503s)
- Wired into `evaluate_prompt_cached()` and `run_feedback_cycle()`
- Generic pipeline observation extraction via `OBS_EXTRACTION_MAP`
- Langfuse cloud push (`push_run()`, `push_all_runs()`)

**Exit gate:** All eval runs produce obs files. `mlflow ui` visualizes optimization history. `OBS_EXTRACTION_MAP` is the single config point for observation mapping.

---

## M6: CWL Workflow Migration + PipelineSchema Foundation -- Planned

Wire existing service functions into the workflow engine scaffold (`api/core/`, `api/nodes/`). PipelineSchema as a prerequisite foundation — backend-agnostic pipeline description that provides derivation methods for all pipeline-specific constants.

**Wave 1: Schema Foundation** (prerequisite)
- **PipelineSchema model** — `api/models/pipeline_schema.py` with derivation methods replacing hardcoded constants
- **TermNorm factory** — `api/services/pipeline_discovery.py` parses `GET /pipeline` into `PipelineSchema`
- **Replace hardcoded dicts** — `PIPELINE_STEP_PARAMS`, `_STEP_PARAM_KEYS`, `OBS_EXTRACTION_MAP`, `REQUIRED_PIPELINE_KEY`, `REQUIRED_TEMPLATE_VARS`, `DATASET_NAME` all derived from schema

**Wave 2: Workflow Nodes** (existing M6 scope)
- **runtime_config injection** — `WorkflowRunner.execute()` accepts and merges `runtime_config` including `PipelineSchema`
- **DatasetLoadNode** — load experiment, build eval dataset
- **FeedbackCycleNode** — wrap `run_feedback_cycle()` with `CycleConfig`
- **ScanNode + YAML workflows** — `sensitivity_scan.yaml`, `optimization_campaign.yaml`

**Wave 3: Notebook Migration**
- **Notebook migration** — `_campaign_lib.run_workflow()` drives optimization through `WorkflowRunner`
- **Docker Compose update** (from M4.3)

**Entry criteria:** M5 exit gate passed (observability integrated).

**Exit gate:** `optimization_campaign.yaml` executes end-to-end via `WorkflowRunner`. No hardcoded pipeline step names in service layer (all derived from PipelineSchema). Docker Compose updated.

Full spec: [`docs/specs/m6-workflow-migration.md`](m6-workflow-migration.md)

---

## M7: Multi-Connector Architecture -- Future

Generalize beyond TermNorm to support arbitrary LLM application backends. Resolves remaining chokepoints (4,5,7,10,11,12,13) that require ConnectorProtocol.

- **Connector interface** — abstract `BackendClient` into a connector protocol
- **Connector registry** — discover and configure connectors at runtime
- **Backend-agnostic evaluation** — `evaluate_prompt_cached()` works with any connector
- **Query parser registry** — replace `parse_bom_material()` with connector-specific parsers
- **Generic eval config** — replace hardcoded hit@1 exact match with `schema.eval_config`

**Entry criteria:** M6 exit gate passed (PipelineSchema + workflow engine active).

**Exit gate:** A second backend connector exists and runs through the same optimization workflow.

Full spec: [`docs/specs/m7-multi-connector.md`](m7-multi-connector.md)

---

## Backlog (unscheduled)

| Feature | Notes |
|---------|-------|
| TermNorm Variant Comparison (SC5) | Needs ConnectorProtocol + pipeline comparison infrastructure (post-M7) |
| Web scrape ablation | How many websites to scrape? Quality vs cost/latency tradeoff. |
| Streamlit Dashboard (P2.3) | Campaign browser, trial comparison, dataset explorer. |
| Public service deployment | Auth, rate limiting, multi-tenancy. API already stateless. |
| Non-prompt targets (P2.4, SC6) | Scoring functions, fuzzy matchers, retrieval queries, GA settings. |
| Evolutionary operators (P2.1) | GA/DE population-based search |
| MCP server mode (P2.2) | Expose tools to Claude Code |

Prioritization decided at milestone exit gates.

---

## Progression Rules

- Complete current milestone before starting the next
- Each milestone ends with a decision gate
- Update CLAUDE.md at each milestone boundary
- One Claude Code session = one WBS work package
