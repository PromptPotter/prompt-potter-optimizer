# Roadmap: PromptPotter Optimizer

**Version:** 0.12.0
**Date:** 2026-03-26
**Status:** Active

---

## Milestones

| Milestone | Focus | Status |
|-----------|-------|--------|
| M0-M5 | Specifications, Foundation, Core Optimizer, Infrastructure, Observability | Complete |
| M6 | PipelineSchema + Pipeline Composability | Complete (Wave 4 → M9) |
| M7 | Optimizer-as-Pipeline | Complete |
| M8 | Campaign Intelligence | **Next** |
| M9 | Multi-Connector Architecture | Future |

Archived specs (M0-M7, governance docs): [`docs/specs/archive/`](archive/)

---

## M6: PipelineSchema + Pipeline Composability -- Complete

PipelineSchema model, `GET /pipeline` self-describing config, schema derivation (6 chokepoints resolved), unified tracing, composite scoring, node_role-driven intermediate metrics, consolidated pipeline control surfaces. Wave 4 (workflow nodes) deferred to M9. Spec: [`archive/m6-pipeline-composability.md`](archive/m6-pipeline-composability.md)

---

## M7: Optimizer-as-Pipeline -- Complete

5-node optimizer pipeline (l1_generate, l1_evaluate, critique, l2_refine_context, l3_modify_plan) with `llm_call()` primitive, `observed_node()` tracing, OptSearchPoint consolidation, warning inventory, L2 probe rounds, l2_directive bridge. Spec: [`archive/m7-optimizer-pipeline.md`](archive/m7-optimizer-pipeline.md)

---

## M8: Campaign Intelligence -- Next

Make campaigns smarter and faster by using accumulated data better. Three pillars: (1) cache intermediate node outputs so prompt variants skip redundant upstream computation, (2) adaptive sensitivity scan that prunes dead axes early, (3) inject accumulated analysis (query difficulty, failure clusters, axis sensitivity) into L1/L2/scan advisor prompts.

**Entry criteria:** M7 exit gate passed.

**Exit gate:** Upstream caching active, scan prunes dead axes, L1 receives accumulated analysis context.

Full spec: [`m8-campaign-intelligence.md`](m8-campaign-intelligence.md)

---

## M9: Multi-Connector Architecture -- Future

Generalize beyond TermNorm to support arbitrary LLM application backends. Resolves remaining chokepoints (4,5,7,10,11,12,13) that require ConnectorProtocol.

- **Connector interface** — abstract `BackendClient` into a connector protocol
- **Connector registry** — discover and configure connectors at runtime
- **Backend-agnostic evaluation** — `evaluate_prompt_cached()` works with any connector
- **Query parser registry** — replace `parse_bom_material()` with connector-specific parsers
- **Generic eval config** — replace hardcoded hit@1 exact match with `schema.eval_config`
- **Notebook migration + Docker** (absorbed from former M6 Wave 5)
- **Workflow nodes** (absorbed from M6 Wave 4)

**Entry criteria:** M8 exit gate passed.

**Exit gate:** A second backend connector exists and runs through the same optimization workflow.

Full spec: [`m9-multi-connector.md`](m9-multi-connector.md)

---

## Backlog (unscheduled)

| Feature | Notes |
|---------|-------|
| TermNorm Variant Comparison | Needs ConnectorProtocol + pipeline comparison (post-M9) |
| Web scrape ablation | Quality vs cost/latency tradeoff |
| Streamlit Dashboard | Campaign browser, trial comparison, dataset explorer |
| Public service deployment | Auth, rate limiting, multi-tenancy |
| Non-prompt targets | Scoring functions, fuzzy matchers, retrieval queries, GA settings |
| Evolutionary operators | GA/DE population-based search |
| MCP server mode | Expose tools to Claude Code |

---

## Non-Functional Requirements

| Requirement | Target |
|-------------|--------|
| Single evaluation (500-item dataset) | < 10 minutes |
| Full optimization run (5 iterations, 500 items) | < 60 minutes |
| Project store per campaign | < 10 MB |
| LLM providers | Groq and OpenAI (any OpenAI-compatible) |
| Python | 3.13 |
| Evaluation mode | Backend via `/matches` (no local fallback) |
| Crash recovery | Incremental `.partial.jsonl` with partial-run resume |

---

## Progression Rules

- Complete current milestone before starting the next
- Each milestone ends with a decision gate
- Update CLAUDE.md at each milestone boundary
