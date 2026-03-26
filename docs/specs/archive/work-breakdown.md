# Work Breakdown Structure: PromptPotter Optimizer

**Version:** 0.11.0
**Date:** 2026-03-19
**Status:** Active
**Depends on:** [PRD v0.9.0](product-requirements.md), [ADD v0.10.0](architecture-design.md)

---

## Estimation Approach

- **1 session = 1 work package** -- each package is scoped for a single Claude Code session
- Multi-session packages are split into separate packages
- Dependencies are explicit; a package cannot start until all listed predecessors are complete

---

## Phase 0: Specifications (M0) -- Complete

7 packages, all Complete. Spec documents: PRD, ADD, WBS, roadmap, CLAUDE.md, two rewrites (v0.6.0, v0.7.0).

---

## Phase 1: Foundation (M1) -- Complete

8 packages, all Complete. PromptState model, test fixtures, evaluator/workflow tests, CI, CLAUDE.md, ablation comparison, pipeline parameter passthrough.

---

## Phase 2: Core Optimizer (M2) -- Complete

9 packages, all Complete. HITL campaign notebook, sensitivity scan, prompt eval, prompt optimizer, backend-only evaluation, per-point sampling, spec rewrite, TermNorm `GET /pipeline`, discovery protocol.

---

## Phase 3: Optimization Infrastructure (M3) -- Complete

11 packages, all Complete. Optimizer nodes, feedback cycle, CampaignStore, Langfuse tracing, sensitivity scan + coverage, scan refactor, campaign init, notebook integration, data loop verification, service cleanup, spec rewrite.

---

## Phase 4: Integration and Polish (M4) -- Complete (Reclassified)

Cleanup absorbed into M3-M5. Remaining items: SC5 → backlog, Streamlit → backlog, Docker → M6, docs → M6 exit.

---

## Phase 5: Observability Layer (M5) -- Complete

7 packages, all Complete (5.0-5.6). ObsLogger, prompt registry, LLM retry, service wiring, integration test, pipeline observation extraction. See [`docs/observability.md`](../observability.md).

---

## Phase 6: PipelineSchema + Cross-Repo Pipeline Composability (M6) -- In Progress

See [M6 spec](m6-pipeline-composability.md) for full details. Cross-repo: TermNorm task doc at [`TermNorm: docs/pipeline-composability.md`](../../../OfficeAdminApps/TermNorm-excel/docs/pipeline-composability.md). n8n research: [n8n mapper spec](m6-n8n-mapper.md) (implementation deferred to M9).

**Waves 0-3: Complete**

| ID | Work Package | Status |
|----|-------------|--------|
| 6.0a | Simplify fuzzy matcher + confidence constants (TermNorm) | Complete |
| 6.0b | GET /pipeline endpoint + pipeline config JSON (TermNorm) | Complete |
| 6.0 | Write M6 spec | Complete |
| 6.0d | n8n → PipelineSchema mapper spec (research, architecture) | Complete |
| 6.1 | PipelineSchema model + TermNorm factory | Complete |
| 6.2 | Replace hardcoded dicts with schema derivation | Complete |
| 6.0c | Unified tracing (TermNorm) | Complete |

**Wave 4: Workflow Nodes** — Deferred to M9 (WP 6.3-6.6 move to M9 alongside notebook migration).

**Wave 5: Composite Scoring + Rank Display**

| ID | Work Package | Sessions | PRD Ref | Depends | Status |
|----|-------------|:--------:|---------|---------|--------|
| 6.7 | `compute_composite_score()` + rank display + integration | 1 | P1.14 | 6.2 | Planned |

**Wave 6: Node-Role-Driven Intermediate Metrics**

| ID | Work Package | Sessions | PRD Ref | Depends | Status |
|----|-------------|:--------:|---------|---------|--------|
| 6.8 | IntermediateMetric model + PipelineStep.node_role | 1 | P1.14 | 6.7 | Planned |
| 6.9 | `derive_metrics()` + composite scoring | 1 | P1.14 | 6.8 | Planned |
| 6.10 | Wire through eval/search/feedback paths | 1 | P1.14 | 6.9 | Planned |

---

## Phase 7: Optimizer-as-Pipeline (M7) -- In Progress

See [M7 spec](m7-optimizer-pipeline.md) for full details (§1-§13).

**Waves A-E: Complete**

| ID | Work Package | Status |
|----|-------------|--------|
| 7.A1 | Prompt templates + optimizer_prompt_loader.py | Complete |
| 7.A2 | OptSearchPoint model | Complete |
| 7.A3 | NodeBase in base.py | Complete (superseded by Wave G node standard) |
| 7.A4 | Node I/O Pydantic models | Complete (superseded by Wave G node standard) |
| 7.B2 | L1GenerateNode implementation | Complete (superseded by Wave G node standard) |
| 7.B3 | L1EvaluateNode implementation | Complete (superseded by Wave G node standard) |
| 7.B4 | L2RefineNode + L3ModifyPlanNode | Complete (superseded by Wave G node standard) |
| 7.B5 | Node tests | Complete (superseded by Wave G node standard) |
| 7.C1 | ObsLogger node step methods | Complete |
| 7.C2 | CloudObsBackend node step support | Complete |
| 7.C3 | Wire NodeBase tracing hooks | Complete (superseded — tracing now via `observed_step()`) |
| 7.D1 | Sync httpx client for notebook path | Complete |
| 7.D2 | Swap L1Generate call site | Complete |
| 7.D3 | Swap L1Evaluate call site | Complete |
| 7.D4 | Swap L2/L3 escalation | Complete |
| 7.D5 | OptSearchPoint checkpointing | Complete |
| 7.D6 | Verify cycle_config_identity | Complete |
| 7.E1 | EscalationCheck framework + DegradationCheck | Complete |

**Wave F: Warning Inventory + OptSearchPoint Consolidation + L2 Probe Rounds**

| ID | Work Package | Sessions | Depends | Status |
|----|-------------|:--------:|---------|--------|
| 7.F1 | OptSearchPoint consolidation (unfreeze, add critique/escalation_journal/warning_inventory, consolidate _LoopState) | 1 | 7.D5 | Active |
| 7.F2 | Warning inventory: `update_query_tracker()`, `summarize_warning_inventory()` in critique_stats.py | 1 | 7.F1 | Planned |
| 7.F3 | Context injection: wire inventory into critique, L2, L1-gen prompts | 1 | 7.F2 | Planned |
| 7.F4 | L2 diagnostic probe rounds: `TransitionResult.probe_queries`, L2 prompt update, orchestrator probe logic | 1 | 7.F3 | Planned |

---

## Phase 8: Campaign Intelligence (M8) -- Planned

See [M8 spec](m8-campaign-intelligence.md) for full details. Three pillars: partial pipeline caching, adaptive sensitivity scan, data-informed generation.

| ID | Work Package | Sessions | Depends | Status |
|----|-------------|:--------:|---------|--------|
| 8.0 | Upstream config hash + `split_at_ranker()` + IntermediateCache store | 1 | -- | Planned |
| 8.1 | TermNorm: `node_outputs` response + `precomputed` request | 1 | -- | Planned |
| 8.2 | Eval gateway integration (cache lookup + populate) | 1 | 8.0, 8.1 | Planned |
| 8.3 | Adaptive sensitivity scan (two-phase + per-axis circuit breaker) | 1 | -- | Planned |
| 8.4 | Data-informed L1/L2/advisor (analysis context injection) | 1 | -- | Planned |

---

## Phase 9: Multi-Connector Architecture (M9) -- Planned

See [M9 spec](m9-multi-connector.md) for full details. Absorbs former M6 Wave 5 (notebook migration + Docker Compose) and M6 Wave 4 (workflow nodes).

| ID | Work Package | Sessions | PRD Ref | Status |
|----|-------------|:--------:|---------|--------|
| 9.0 | Write M9 spec | 1 | -- | Complete |
| 9.1 | ConnectorProtocol + MockConnector | 1 | P1.13 | Planned |
| 9.2 | ConnectorRegistry | 1 | P1.13 | Planned |
| 9.3 | Service migration (type annotation swap) | 1 | P1.13 | Planned |
| 9.4 | Docs + integration test | 1 | P1.13 | Planned |
| 9.5 | Notebook migration + Docker Compose (from former M6 Wave 5) | 1 | P1.12 | Planned |
| 9.6 | Workflow nodes: runtime_config, DatasetLoadNode, FeedbackCycleNode, ScanNode (from M6 Wave 4) | 2 | P1.12 | 9.3 | Planned |

---

## Phase 10: n8n Connector Implementation (M10) -- Planned

See [n8n mapper spec](m6-n8n-mapper.md) for full research and architecture. Depends on M9 ConnectorProtocol.

| ID | Work Package | Sessions | PRD Ref | Depends | Status |
|----|-------------|:--------:|---------|---------|--------|
| 10.1 | Node.js bridge (`external/n8n-bridge/`) + bridge output Pydantic models | 1 | P1.13 | 9.1 | Planned |
| 10.2 | n8n mapper (Phases A-E) + MappingReport + gap detection | 1 | P1.13 | 10.1 | Planned |
| 10.3 | n8n ConnectorProtocol adapter + pipeline_discovery integration | 1 | P1.13 | 10.2, 9.3 | Planned |
| 10.4 | Tests against real fixture + raw fallback path | 1 | P1.13 | 10.2 | Planned |

---

## Session Summary

| Phase | Packages | Sessions | Status |
|-------|:--------:|:--------:|--------|
| M0: Specifications | 7 | 7 | Complete |
| M1: Foundation | 8 | 8 | Complete |
| M2: Core Optimizer | 9 | 10 | Complete |
| M3: Optimization Infrastructure | 11 | 14 | Complete |
| M4: Integration and Polish | -- | -- | Complete (reclassified) |
| M5: Observability Layer | 7 | 7 | Complete |
| M6: PipelineSchema + Pipeline Composability | 14 | 14 | Waves 0-3, 5-7 complete; Wave 4 → M9 |
| M7: Optimizer-as-Pipeline | 22 | 22 | In Progress (Waves A-E complete, Wave F active) |
| M8: Campaign Intelligence | 5 | 5 | Planned |
| M9: Multi-Connector | 7 | 7 | Planned |
| M10: n8n Connector | 4 | 4 | Planned |
| **Total** | **69** | **~73** | |

---

## PRD Coverage

| PRD Req | Work Packages | Phase | Status |
|---------|--------------|-------|--------|
| P0.1 Backend Evaluation | 2.3, 2.5 | M2 | Complete |
| P0.2 Failure Analysis | 2.4 | M2 | Complete |
| P0.3 Candidate Generation | 2.1, 2.4 | M2 | Complete |
| P0.4 Optimization Loop | 2.1, 3.2 | M2, M3 | Complete |
| P0.5 PROMPT_STATE Tracking | 1.1 | M1 | Complete |
| P0.6 Sensitivity Scan | 2.2, 2.6, 3.6 | M2, M3 | Complete |
| P1.1 Optimizer Nodes | 3.1 | M3 | Complete |
| P1.2 Feedback Cycling | 3.2, 3.8 | M3 | Complete |
| P1.3 Campaign Registry | 3.3 | M3 | Complete |
| P1.4 Langfuse Integration | 3.4 | M3 | Complete |
| P1.5 Discovery Protocol | 2.8, 2.9 | M2 | Complete |
| P1.6 Ablation Comparison | 1.7 | M1 | Complete |
| P1.7 Parameter Passthrough | 1.8 | M1 | Complete |
| P1.8 Sensitivity Scan | 3.5 | M3 | Complete |
| P1.9 Data Loop | 3.9 | M3 | Complete |
| P1.10 File-Based Observability | 5.1-5.6 | M5 | Complete |
| P1.11 LLM Retry Logic | 5.3 | M5 | Complete |
| P1.12 Workflow-Driven Optimization | 6.3-6.6, 9.5 | M6, M9 | Planned |
| P1.13 Multi-Connector Support | 9.1-9.4, 10.1-10.4 | M9, M10 | Planned |
| P1.14 PipelineSchema | 6.1-6.2 | M6 | Complete |
| P2.3 Streamlit Dashboard | -- | Backlog | Planned |
| SC5 TermNorm Validation | -- | Backlog | Planned |
