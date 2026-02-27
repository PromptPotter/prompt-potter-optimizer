# Work Breakdown Structure: PromptPotter Optimizer

**Version:** 0.7.0
**Date:** 2026-02-25
**Status:** Active
**Depends on:** [PRD v0.7.0](prd.md), [ADD v0.7.0](add.md)

---

## Estimation Approach

- **1 session = 1 work package** -- each package is scoped for a single Claude Code session
- Multi-session packages are split into separate packages
- Dependencies are explicit; a package cannot start until all listed predecessors are complete

---

## Phase 0: Specifications (M0) -- Complete

| ID | Work Package | Sessions | Status |
|----|-------------|:--------:|--------|
| 0.1 | Write project charter | 1 | Complete |
| 0.2 | Write PRD | 1 | Complete |
| 0.3 | Write ADD | 1 | Complete |
| 0.4 | Write WBS and roadmap | 1 | Complete |
| 0.5 | Update CLAUDE.md | 1 | Complete |
| 0.6 | Spec rewrite v0.6.0 | 1 | Complete |
| 0.7 | Spec rewrite v0.7.0 (M3 snapshot) | 1 | In Progress |

---

## Phase 1: Foundation (M1) -- Complete

| ID | Work Package | Sessions | PRD Ref | Status |
|----|-------------|:--------:|---------|--------|
| 1.1 | PROMPT_STATE model | 1 | P0.5 | Complete (`06b6635`) |
| 1.2 | Test fixtures and dataset helpers | 1 | -- | Complete (`28833e3`, `7664b52`) |
| 1.3 | Evaluator tests | 1 | P0.1 | Complete (later pruned in `ceb9031`) |
| 1.4 | Workflow runner tests | 1 | P0.1 | Complete (later pruned in `ceb9031`) |
| 1.5 | CI pipeline | 1 | -- | Complete (`7664b52`) |
| 1.6 | CLAUDE.md update | 1 | -- | Complete (`3cc31f1`) |
| 1.7 | Ablation comparison | 1 | P1.6 | Complete (`88e3b83`) |
| 1.8 | Pipeline parameter passthrough | 1 | P1.7 | Complete (`19b975f`) |

---

## Phase 2: Core Optimizer (M2) -- Complete

| ID | Work Package | Sessions | PRD Ref | Status |
|----|-------------|:--------:|---------|--------|
| 2.1 | HITL campaign notebook | 2 | P0.3, P0.4 | Complete (`89d4a2f`+) |
| 2.2 | Grid search service | 1 | P0.6 | Complete (`534fa3e`+) |
| 2.3 | Prompt evaluation service | 1 | P0.1 | Complete |
| 2.4 | Prompt optimizer service | 1 | P0.2, P0.3 | Complete |
| 2.5 | Backend-only evaluation | 1 | P0.1 | Complete (`82157ef`) |
| 2.6 | Per-point query sampling | 1 | P0.6 | Complete (`fcd6ae6`) |
| 2.7 | Spec rewrite v0.6.0 | 1 | -- | Complete |
| 2.8 | TermNorm `GET /pipeline` (external) | 1 | P1.5 | Complete |
| 2.9 | Discovery protocol integration | 1 | P1.5 | Complete |

---

## Phase 3: Optimization Infrastructure (M3) -- Complete

| ID | Work Package | Sessions | PRD Ref | Status |
|----|-------------|:--------:|---------|--------|
| 3.1 | Optimizer nodes (Init, GrowFilter, AnalysisEval) | 2 | P1.1 | Complete |
| 3.2 | Feedback cycle orchestrator | 2 | P1.2 | Complete |
| 3.3 | Campaign registry + CampaignStore | 1 | P1.3 | Complete |
| 3.4 | Langfuse per-trial tracing | 1 | P1.4 | Complete |
| 3.5 | Smart search (sensitivity scan, coverage) | 2 | P1.8 | Complete |
| 3.6 | Grid search refactor (search module) | 1 | P0.6 | Complete |
| 3.7 | Campaign init service | 1 | -- | Complete |
| 3.8 | Notebook integration (feedback cycle + progress) | 1 | P1.2 | Complete |
| 3.9 | Data loop verification | 1 | P1.9 | Complete |
| 3.10 | Service layer cleanup (campaign_lib dedup) | 1 | -- | Complete |
| 3.11 | Spec rewrite v0.7.0 | 1 | -- | Complete |

---

## Phase 4: Integration and Polish (M4) -- Planned

| ID | Work Package | Sessions | PRD Ref | Status |
|----|-------------|:--------:|---------|--------|
| 4.1 | TermNorm variant comparison (SC5) | 2 | SC5 | Planned |
| 4.2 | Streamlit dashboard | 2 | P2.3 | Planned |
| 4.3 | Docker Compose update | 1 | -- | Planned |
| 4.4 | Documentation update | 1 | -- | Planned |

---

## Phase 5: Observability Layer (M5) -- In Progress

See [M5 spec](m5-observability.md) for full details.

| ID | Work Package | Sessions | PRD Ref | Status |
|----|-------------|:--------:|---------|--------|
| 5.0 | Write M5 spec | 1 | -- | Complete |
| 5.1 | ObsLogger core (traces, experiments, rounds) | 1 | P1.10 | Complete |
| 5.2 | Prompt registry (prompt versioning on disk) | 1 | P1.10 | Complete |
| 5.3 | LLM retry logic (exponential backoff) | 1 | P1.11 | Complete |
| 5.4 | Wire into services (prompt_eval, feedback_cycle) | 1 | P1.10 | Complete |
| 5.5 | Integration test (obs file output E2E) | 1 | P1.10 | Complete |
| 5.6 | Generic pipeline observation extraction | 1 | P1.10 | In Progress |

---

## Phase 6: CWL Workflow Migration (M6) -- Planned

See [M6 spec](m6-workflow-migration.md) for full details.

| ID | Work Package | Sessions | PRD Ref | Status |
|----|-------------|:--------:|---------|--------|
| 6.0 | Write M6 spec | 1 | -- | Complete |
| 6.1 | runtime_config injection in WorkflowRunner | 1 | P1.12 | Planned |
| 6.2 | DatasetLoadNode | 1 | P1.12 | Planned |
| 6.3 | FeedbackCycleNode | 1 | P1.12 | Planned |
| 6.4 | ScanNode + YAML workflows | 1 | P1.12 | Planned |
| 6.5 | Notebook migration (_campaign_lib → WorkflowRunner) | 1 | P1.12 | Planned |

---

## Phase 7: Multi-Connector Architecture (M7) -- Planned

See [M7 spec](m7-multi-connector.md) for full details.

| ID | Work Package | Sessions | PRD Ref | Status |
|----|-------------|:--------:|---------|--------|
| 7.0 | Write M7 spec | 1 | -- | Complete |
| 7.1 | ConnectorProtocol + MockConnector | 1 | P1.13 | Planned |
| 7.2 | ConnectorRegistry | 1 | P1.13 | Planned |
| 7.3 | Service migration (type annotation swap) | 1 | P1.13 | Planned |
| 7.4 | Docs + integration test | 1 | P1.13 | Planned |

---

## Session Summary

| Phase | Packages | Sessions | Status |
|-------|:--------:|:--------:|--------|
| M0: Specifications | 7 | 7 | Complete |
| M1: Foundation | 8 | 8 | Complete |
| M2: Core Optimizer | 9 | 10 | Complete |
| M3: Optimization Infrastructure | 11 | 14 | Complete |
| M4: Integration and Polish | 4 | 6 | Planned |
| M5: Observability Layer | 7 | 7 | In Progress |
| M6: CWL Workflow Migration | 6 | 6 | Planned |
| M7: Multi-Connector | 5 | 5 | Planned |
| **Total** | **57** | **~63** | |

---

## PRD Coverage

| PRD Req | Work Packages | Phase | Status |
|---------|--------------|-------|--------|
| P0.1 Backend Evaluation | 2.3, 2.5 | M2 | Complete |
| P0.2 Failure Analysis | 2.4 | M2 | Complete |
| P0.3 Candidate Generation | 2.1, 2.4 | M2 | Complete |
| P0.4 Optimization Loop | 2.1, 3.2 | M2, M3 | Complete |
| P0.5 PROMPT_STATE Tracking | 1.1 | M1 | Complete |
| P0.6 Grid Search | 2.2, 2.6, 3.6 | M2, M3 | Complete |
| P1.1 Optimizer Nodes | 3.1 | M3 | Complete |
| P1.2 Feedback Cycling | 3.2, 3.8 | M3 | Complete |
| P1.3 Campaign Registry | 3.3 | M3 | Complete |
| P1.4 Langfuse Integration | 3.4 | M3 | Complete |
| P1.5 Discovery Protocol | 2.8, 2.9 | M2 | Complete |
| P1.6 Ablation Comparison | 1.7 | M1 | Complete |
| P1.7 Parameter Passthrough | 1.8 | M1 | Complete |
| P1.8 Sensitivity Scan | 3.5 | M3 | Complete |
| P1.9 Data Loop | 3.9 | M3 | Complete |
| P1.10 File-Based Observability | 5.1, 5.2, 5.4, 5.5 | M5 | Complete |
| P1.11 LLM Retry Logic | 5.3 | M5 | Complete |
| P1.12 Workflow-Driven Optimization | 6.1–6.5 | M6 | Planned |
| P1.13 Multi-Connector Support | 7.1–7.4 | M7 | Planned |
| P2.3 Streamlit Dashboard | 4.2 | M4 | Planned |
| SC5 TermNorm Validation | 4.1 | M4 | Planned |
