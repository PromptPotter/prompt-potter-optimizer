# Roadmap: PromptPotter Optimizer

**Version:** 0.7.0
**Date:** 2026-02-25
**Status:** Active
**Depends on:** [WBS v0.7.0](wbs.md)

---

## Milestones

| Milestone | Focus | Status |
|-----------|-------|--------|
| M0 | Specifications | Complete |
| M1 | Foundation (PromptState, ProjectStore, comparison, CI) | Complete |
| M2 | Core Optimizer (eval, grid search, prompt optimizer, notebook) | Complete |
| M3 | Optimization Infrastructure | Nearly Complete |
| M4 | Integration and Polish | Planned |

---

## M3: Optimization Infrastructure -- Nearly Complete

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

**Remaining:**
- Service layer cleanup: reduce duplication between `_campaign_lib.py` and service-layer functions

**Exit gate:** End-to-end optimization with progress output, eval data feeds back into scans, Langfuse traces with scores.

**Risk:** Groq rate limits / 503 errors on long runs (needs retry with backoff).

---

## M4: Integration and Polish -- Planned

- **TermNorm Variant Comparison** (SC5): Variant A vs B on BC5CDR 500-term subset using the full two-loop workflow
- **Streamlit Dashboard**: campaign browser, trial comparison, dataset explorer
- **Docker Compose**: optimizer + Langfuse with health checks
- **Documentation**: README, notebooks, cleanup

**Entry criteria:** M3 exit gate passed.

**Exit gate:** Variant A vs B comparison completes with clear recommendation. Docker deployment works. Decision: does optimized LLM2 justify its cost?

---

## Future (Post-M4)

| Feature | Notes |
|---------|-------|
| Web scrape ablation | How many websites to scrape? Quality vs cost/latency tradeoff. |
| Public service deployment | Auth, rate limiting, multi-tenancy. API already stateless. |
| Non-prompt targets (P2.4, SC6) | Scoring functions, fuzzy matchers, retrieval queries, GA settings. |
| Evolutionary operators (P2.1) | GA/DE population-based search |
| MCP server mode (P2.2) | Expose tools to Claude Code |
| LLM client retry logic | Groq 503 handling with exponential backoff |

Prioritization decided at M4 exit gate.

---

## Progression Rules

- Complete current milestone before starting the next
- Each milestone ends with a decision gate
- Update CLAUDE.md at each milestone boundary
- One Claude Code session = one WBS work package
