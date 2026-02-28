# Work Breakdown Structure: PromptPotter Optimizer

**Version:** 0.9.0
**Date:** 2026-02-27
**Status:** Active
**Depends on:** [PRD v0.9.0](prd.md), [ADD v0.9.0](add.md)

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

9 packages, all Complete. HITL campaign notebook, grid search, prompt eval, prompt optimizer, backend-only evaluation, per-point sampling, spec rewrite, TermNorm `GET /pipeline`, discovery protocol.

---

## Phase 3: Optimization Infrastructure (M3) -- Complete

11 packages, all Complete. Optimizer nodes, feedback cycle, CampaignStore, Langfuse tracing, sensitivity scan + coverage, grid refactor, campaign init, notebook integration, data loop verification, service cleanup, spec rewrite.

---

## Phase 4: Integration and Polish (M4) -- Complete (Reclassified)

M4 was originally planned for TermNorm variant comparison, Streamlit, Docker, and docs. Reclassified as complete — cleanup/lint/polish work was absorbed into M3–M5 sessions. Remaining items redistributed:

| Original Item | New Home |
|--------------|----------|
| 4.1 TermNorm Variant Comparison (SC5) | Backlog (needs ConnectorProtocol + real pipeline comparison infrastructure) |
| 4.2 Streamlit Dashboard | P2.3 Backlog (unchanged) |
| 4.3 Docker Compose update | M6 WP 6.7 |
| 4.4 Documentation update | M6 exit criteria |

---

## Phase 5: Observability Layer (M5) -- Complete

M5 is complete. See [`docs/obs-guide.md`](../obs-guide.md) for data exploration.

| ID | Work Package | Sessions | PRD Ref | Status |
|----|-------------|:--------:|---------|--------|
| 5.0 | Write M5 spec | 1 | -- | Complete |
| 5.1 | ObsLogger core (traces, experiments, rounds) | 1 | P1.10 | Complete |
| 5.2 | Prompt registry (prompt versioning on disk) | 1 | P1.10 | Complete |
| 5.3 | LLM retry logic (exponential backoff) | 1 | P1.11 | Complete |
| 5.4 | Wire into services (prompt_eval, feedback_cycle) | 1 | P1.10 | Complete |
| 5.5 | Integration test (obs file output E2E) | 1 | P1.10 | Complete |
| 5.6 | Generic pipeline observation extraction | 1 | P1.10 | Complete |

---

## Phase 6: PipelineSchema + Cross-Repo Pipeline Composability (M6) -- Planned

See [M6 spec](m6-workflow-migration.md) for full details. Cross-repo: TermNorm task doc at [`TermNorm: docs/pipeline-composability.md`](../../../OfficeAddinApps/TermNorm-excel/docs/pipeline-composability.md).

**Wave 0: TermNorm Cleanup** (TermNorm repo)

| ID | Work Package | Sessions | PRD Ref | Depends | Status |
|----|-------------|:--------:|---------|---------|--------|
| 6.0a | Simplify fuzzy matcher + confidence constants | 1 | -- | -- | Planned |

**Wave 1: Pipeline Contract** (TermNorm repo)

| ID | Work Package | Sessions | PRD Ref | Depends | Status |
|----|-------------|:--------:|---------|---------|--------|
| 6.0b | GET /pipeline endpoint + pipeline config JSON | 1 | P1.14 | 6.0a | Planned |

**Wave 2: Schema Foundation** (PromptPotter repo — prerequisite for Wave 4)

| ID | Work Package | Sessions | PRD Ref | Depends | Status |
|----|-------------|:--------:|---------|---------|--------|
| 6.0 | Write M6 spec | 1 | -- | -- | Complete |
| 6.1 | PipelineSchema model + TermNorm factory | 1 | P1.14 | 6.0b | Planned |
| 6.2 | Replace hardcoded dicts with schema derivation | 1 | P1.14 | 6.1 | Planned |

**Wave 3: Unified Tracing** (TermNorm repo — parallel with Wave 2)

| ID | Work Package | Sessions | PRD Ref | Depends | Status |
|----|-------------|:--------:|---------|---------|--------|
| 6.0c | Unified tracing (trace lifecycle + frontend integration) | 1 | P1.10 | 6.0b | Planned |

**Wave 4: Workflow Nodes** (PromptPotter repo)

| ID | Work Package | Sessions | PRD Ref | Depends | Status |
|----|-------------|:--------:|---------|---------|--------|
| 6.3 | runtime_config injection in WorkflowRunner | 1 | P1.12 | 6.2 | Planned |
| 6.4 | DatasetLoadNode | 1 | P1.12 | 6.3 | Planned |
| 6.5 | FeedbackCycleNode | 1 | P1.12 | 6.3 | Planned |
| 6.6 | ScanNode + YAML workflows | 1 | P1.12 | 6.4, 6.5 | Planned |

**Wave 5: Notebook Migration** (PromptPotter repo)

| ID | Work Package | Sessions | PRD Ref | Depends | Status |
|----|-------------|:--------:|---------|---------|--------|
| 6.7 | Notebook migration + Docker Compose | 1 | P1.12 | 6.6 | Planned |

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
| M4: Integration and Polish | -- | -- | Complete (reclassified) |
| M5: Observability Layer | 7 | 7 | Complete |
| M6: PipelineSchema + Pipeline Composability | 11 | 11 | Planned |
| M7: Multi-Connector | 5 | 5 | Planned |
| **Total** | **58** | **~62** | |

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
| P1.10 File-Based Observability | 5.1–5.6 | M5 | Complete |
| P1.11 LLM Retry Logic | 5.3 | M5 | Complete |
| P1.12 Workflow-Driven Optimization | 6.3–6.7 | M6 | Planned |
| P1.13 Multi-Connector Support | 7.1–7.4 | M7 | Planned |
| P1.14 PipelineSchema | 6.1–6.2 | M6 | Planned |
| P2.3 Streamlit Dashboard | -- | Backlog | Planned |
| SC5 TermNorm Validation | -- | Backlog | Planned |
