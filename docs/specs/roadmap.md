# Roadmap: PromptPotter Optimizer

**Version:** 0.2.0
**Date:** 2026-02-19
**Status:** Draft
**Depends on:** [WBS](wbs.md)

---

## Milestones at a Glance

| Milestone | Focus | Timeline | Status |
|-----------|-------|----------|--------|
| M0 | Specifications | Week 1 | Complete |
| M1 | Foundation | Weeks 2-3 | Next |
| M2 | Core Optimizer | Weeks 4-6 | Planned |
| M3 | Registry and Tracking | Weeks 7-8 | Planned |
| M4 | Integration and Polish | Weeks 9-11 | Planned |

---

## M0: Specifications — Complete

- **Deliverables:** Project charter, PRD, ADD, WBS, roadmap, CLAUDE.md, CHANGELOG.md
- **Entry criteria:** Project repository exists with working workflow engine, evaluators, and LLM client
- **Exit gate:** All five spec documents reviewed and internally consistent; charter goals trace to PRD requirements; PRD requirements trace to WBS work packages
- **Risks:** None (complete)

---

## M1: Foundation — Weeks 2-3

- **Deliverables:**
  - PROMPT_STATE Pydantic model with parameters dictionary
  - Test suite for evaluators and workflow runner
  - Test fixtures and dataset helpers
  - GitHub Actions CI (lint + test)
  - CLAUDE.md updated with M1 status

- **Entry criteria:** M0 specs approved, no open contradictions between charter/PRD/ADD

- **Exit gate:**
  - All tests pass, CI green
  - PROMPT_STATE model importable and unit tested
  - Decision: are there bugs in existing components that must be fixed before building the optimizer on top?

- **Risks:**
  - Existing code may have untested edge cases that surface during test writing
  - PROMPT_STATE schema affects every downstream milestone; getting it wrong means rework

---

## M2: Core Optimizer — Weeks 4-6

- **Deliverables:**
  - Analyzer, Generator, and Selector nodes (P0.2, P0.3, P0.4, P1.5)
  - Optimization orchestrator: full evaluate-analyze-generate-select loop (P0.4)
  - Updated optimize endpoint replacing the placeholder
  - E2E test: optimization on sample dataset produces measurable improvement

- **Entry criteria:** M1 exit gate passed; CI green; PROMPT_STATE model finalized

- **Exit gate:**
  - Optimize endpoint runs a real loop end-to-end
  - E2E test demonstrates score improvement from baseline
  - Decision: does the optimizer produce measurably better configurations? Is the API contract right?

- **Risks:**
  - LLM-based analysis/generation quality may vary across providers
  - Candidate generation may produce insufficiently diverse configurations
  - Largest milestone; highest scope creep risk

---

## M3: Registry and Tracking — Weeks 7-8

- **Deliverables:**
  - File-based CampaignRegistry with campaign/trial persistence (P1.1)
  - Registry integrated into the optimization loop
  - Lineage tracking: trial parent-child tree (P0.5)
  - Langfuse score integration: per-trial traces with scores (SC3)
  - JSONL export and campaign list/detail endpoints

- **Entry criteria:** M2 exit gate passed; optimization loop works end-to-end

- **Exit gate:**
  - Optimization runs automatically persist to `.promptpotter/campaigns/`
  - Campaigns viewable and exportable via API
  - Langfuse shows correct parent-child traces with scores
  - Decision: is the persisted data useful for comparing runs? Is the file layout right?

- **Risks:**
  - File-based storage may have concurrency issues if parallel execution is added later
  - JSONL format choices become a compatibility contract; changing later breaks consumers

---

## M4: Integration and Polish — Weeks 9-11

- **Deliverables:**
  - TermNorm Variant A vs Variant B comparison on BC5CDR 500-term subset (SC5)
  - Optimized `llm_ranking` prompt versions written back to TermNorm's prompt registry
  - Real web search provider replacing mock (P1.4)
  - Human-in-the-loop gates: pause/approve/reject/edit (P1.3)
  - Streamlit dashboard: campaign browser, trial comparison, dataset explorer (P2.4)
  - Docker compose for optimizer + Langfuse
  - Documentation update (README, notebooks)

- **Entry criteria:** M3 exit gate passed; registry and Langfuse working

- **Exit gate:**
  - **Variant A vs Variant B comparison completes** on the BC5CDR 500-term subset: Variant A baseline established, `llm_ranking` prompt optimized (v2+), best Variant B score compared against Variant A, clear recommendation produced (SC5)
  - Results are persisted in the campaign registry and traceable in Langfuse
  - Time to first optimization under 15 minutes (SC4)
  - Docker deployment works with `docker-compose up`
  - Decision: does the optimized LLM2 call justify its cost? Ready for others to use? Which P2 features to prioritize next?

- **Risks:**
  - TermNorm integration depends on external dataset availability and format stability
  - Optimizing `llm_ranking` may not produce enough improvement to justify the LLM2 call -- this is a valid result, not a failure
  - HITL UX is hard to get right for both API and notebook modes
  - Dashboard scope can expand; keep to three core views

---

## Future (Post-M4, Unscheduled)

### Next Validation: Web Scrape Ablation

After the Variant A vs Variant B comparison is settled, the next decision point is: **how many websites to scrape** for `entity_profiling`? More websites means better entity profiles but higher cost and latency. This ablation study varies the scrape count while holding the winning variant's prompts fixed, measuring the quality vs. cost/latency tradeoff. Also extends validation to the LCA dataset for real-world use case confirmation.

### Additional Features

| Feature | PRD | Notes |
|---------|-----|-------|
| Web scrape count ablation | -- | Vary number of websites scraped, measure quality vs. cost/latency |
| LCA dataset validation | -- | Real-world use case validation after BC5CDR development is complete |
| Reflection-based learning | P2.1 | Chained reflections to improve generation |
| Evolutionary operators (GA/DE) | P2.2 | Population-based search for multi-parameter optimization |
| MCP server mode | P2.3 | Expose optimization tools to Claude Code and MCP clients |
| Workflow-based optimization | P1.2 | Optimize single steps within multi-step pipelines |

Prioritization decided at the M4 exit gate based on Variant A vs Variant B results.

---

## Progression Rules

- Complete current milestone before starting the next; no skipping
- Each milestone ends with its decision gate; review before proceeding
- Update CLAUDE.md at each milestone boundary
- One Claude Code session = one WBS work package
- Specs can be updated at any milestone boundary (bump version + changelog)
