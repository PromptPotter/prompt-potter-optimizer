# Roadmap: PromptPotter Optimizer

**Version:** 0.10.0
**Date:** 2026-03-05
**Status:** Active
**Depends on:** [WBS v0.10.0](wbs.md)

---

## Milestones

| Milestone | Focus | Status |
|-----------|-------|--------|
| M0 | Specifications | Complete |
| M1 | Foundation (PromptState, ProjectStore, comparison, CI) | Complete |
| M2 | Core Optimizer (eval, grid search, prompt optimizer, notebook) | Complete |
| M3 | Optimization Infrastructure | Complete |
| M4 | Integration and Polish (reclassified — absorbed into M3-M5) | Complete |
| M5 | Observability Layer | Complete |
| M6 | PipelineSchema + Pipeline Composability | Waves 0-3 complete, Wave 4 deferred to M7, Waves 5-6 active |
| M7 | Multi-Connector Architecture | Future |
| M8 | Optimizer-as-Pipeline | Draft |

---

## M3: Optimization Infrastructure -- Complete

Optimizer nodes, feedback cycle (3-path routing, patience), CampaignStore, Langfuse tracing, sensitivity scan + coverage, grid refactor, campaign init, notebook integration, data loop. ~1,300 LOC removed across 4 cleanup passes.

---

## M4: Integration and Polish -- Complete (Reclassified)

Cleanup absorbed into M3-M5. SC5 variant comparison → backlog (needs ConnectorProtocol). Streamlit → backlog. Docker → M6. Docs → M6 exit.

---

## M5: Observability Layer -- Complete

File-based observability (Langfuse traces, MLflow experiments, prompt versioning), LLM retry, pipeline observation extraction. See [`docs/observability.md`](../observability.md).

---

## M6: PipelineSchema + Pipeline Composability -- In Progress

| Wave | Scope | Status |
|------|-------|--------|
| 0 | TermNorm fuzzy cleanup (single threshold) | Complete |
| 1 | `GET /pipeline` endpoint (6-step config) | Complete |
| 2 | PipelineSchema model + schema derivation (6 chokepoints resolved) | Complete |
| 3 | Unified tracing (one trace per query) | Complete |
| 4 | Workflow nodes (runtime_config, DatasetLoadNode, FeedbackCycleNode, ScanNode) | Deferred to M7 |
| 5 | Composite scoring + rank display (hardcoded stepping stone) | Active |
| 6 | Node-role-driven intermediate metrics (auto-wired) | Planned |

**Exit gate (reframed):** MVP performance validation — TermNorm accuracy from ~15% to >90%. Composite scoring (accuracy + intermediate metrics) used as optimization target. No hardcoded pipeline step names in service layer.

Full spec: [`docs/specs/m6-pipeline-composability.md`](m6-pipeline-composability.md)

---

## M7: Multi-Connector Architecture -- Future

Generalize beyond TermNorm to support arbitrary LLM application backends. Resolves remaining chokepoints (4,5,7,10,11,12,13) that require ConnectorProtocol.

- **Connector interface** — abstract `BackendClient` into a connector protocol
- **Connector registry** — discover and configure connectors at runtime
- **Backend-agnostic evaluation** — `evaluate_prompt_cached()` works with any connector
- **Query parser registry** — replace `parse_bom_material()` with connector-specific parsers
- **Generic eval config** — replace hardcoded hit@1 exact match with `schema.eval_config`
- **Notebook migration + Docker** (absorbed from former M6 Wave 5)
- **Workflow nodes** (absorbed from M6 Wave 4: runtime_config, DatasetLoadNode, FeedbackCycleNode, ScanNode)

**Entry criteria:** M6 exit gate passed (PipelineSchema + composite scoring active).

**Exit gate:** A second backend connector exists and runs through the same optimization workflow.

Full spec: [`docs/specs/m7-multi-connector.md`](m7-multi-connector.md)

---

## M8: Optimizer-as-Pipeline -- Draft

The optimizer itself is a 4-step pipeline (`l1_generate`, `l1_evaluate`, `l2_refine_context`, `l3_modify_plan`), modeled using the same `PipelineSchema`/`PipelineStep` architecture as the target backend. This solves three problems by design: tracing (optimizer steps get Langfuse observations), reproducibility (every meta-optimizer decision traced with full I/O), and self-optimization (a meta-PromptPotter optimizing its own prompts).

Today, key artifacts are lost after each cycle: `critique_text`, `thinking_styles`, L2/L3 transition rationale and inputs, candidate generation meta-prompts. M8 captures all step-level artifacts, defines `OPTIMIZER_PIPELINE_SCHEMA`, and wires optimizer steps into the existing tracing infrastructure.

**Entry criteria:** M6 exit gate passed (PipelineSchema + composite scoring active).

**Exit gate:** Optimizer pipeline traced end-to-end with full reproducibility. Given a trial JSON, every LLM call in the optimization cycle can be reconstructed.

Full spec: [`docs/specs/m8-optimizer-pipeline.md`](m8-optimizer-pipeline.md)

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
