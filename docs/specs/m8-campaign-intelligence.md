# Milestone 8: Campaign Intelligence

**Version:** 0.1.0
**Date:** 2026-03-26
**Status:** Planned
**Depends on:** [M7 Optimizer-as-Pipeline](m7-optimizer-pipeline.md)

---

## Context

The core optimization loop works end-to-end, but campaigns don't yet produce the desired accuracy gains. Three independent weaknesses compound:

1. **Redundant computation** — Every prompt variant re-runs the entire backend pipeline, even though upstream nodes (web_search, token_matching) produce identical output when only the prompt changes. A 20-variant × 100-query campaign wastes ~1,900 upstream calls.

2. **Uninformed scanning** — The sensitivity scan probes all axes uniformly, including ones that never produce signal (e.g., an axis that is always negative). No early pruning, no adaptive allocation.

3. **Context-poor generation** — L1/L2/scan advisor receive failure examples and critique, but don't see the accumulated analysis: which query types are hard, which axes showed sensitivity, what upstream diagnostics reveal about *why* queries fail. The system gets more data each round but doesn't get smarter.

---

## 1. Partial Pipeline Caching

**Problem:** During optimization, only `llm_ranking` config changes between prompt variants. Steps before the first ranker node produce identical output for the same query.

**Approach:**
- Compute `upstream_config_hash` from pipeline_params excluding ranker nodes (using `PipelineSchema.split_at_ranker()`)
- Store per-node intermediate outputs (`node_outputs`) returned by TermNorm in a disk cache keyed by `(upstream_hash, query)`
- On subsequent evals with matching upstream config, send cached intermediates via new `precomputed` field in `/matches` request; TermNorm skips those nodes

**Wire protocol (TermNorm additions, backward-compatible):**
- Response: `data.node_outputs: {node_name: opaque_output_dict}`
- Request: `precomputed: {node_name: opaque_output_dict}` — TermNorm injects into pipeline data flow, executes only remaining steps

**PromptPotter changes:**
- `upstream_config_hash()` in `hashing.py`, `split_at_ranker()` on `PipelineSchema`
- New `IntermediateCache` store in `stores/intermediate_cache.py` (disk-backed, `{backend_id}/intermediate_cache/{upstream_hash}.json`)
- `backend_reranker_evaluate()`: check cache → build `precomputed` → call `run_match()` → store `node_outputs`
- `run_match()`: thread `precomputed` to wire payload

---

## 2. Smarter Sensitivity Scan

**Problem:** The OAT scan probes every axis variant uniformly. Some axes never produce signal at the current search space location, wasting eval budget on uninteresting regions.

**Design principles:**
- The sensitivity scan is **maximal exploration** — its job is to map the search space, not optimize. The optimizer (feedback cycle) carries the main load and balances exploitation/exploration.
- Sample selection must be **location-aware**: which variants are worth probing depends on where we currently are in the search-point space. An axis that's dead at one baseline may be alive at another.
- Think of it as combined exploration of suitable samples *given* the current search-point location — not blind enumeration, but informed coverage of the regions that matter from where we stand.
- Architecture must remain simple — add branch points to the algorithm where data justifies them, but avoid over-engineering.

*Approach to be designed when this wave starts.*

---

## 3. Data-Informed Suggestions

**Problem:** Each round accumulates more evaluation data (per-query hits/misses, pipeline diagnostics, axis sensitivity profiles, query difficulty), but this analysis doesn't flow into the LLM prompts that generate new candidates or recommend scan parameters.

**Design principles:**
- Every time L1, L2, L3, or the scan advisor runs, the data context and data analysis must be freshly compiled and injected. The system gets more data each round — the prompts must reflect that.
- Each decision-making step (L1/L2/L3/advisor) needs a **tailored** analysis context: not the same dump for everyone, but the right information for that step's decision.
- Information should be compiled from **multiple deterministic analysis functions** (failure clustering, query difficulty, axis sensitivity, step-level diagnostics, temporal trends) — not raw data dumps.
- Include as much compiled analysis as possible. More context = better suggestions, as long as it's structured and relevant to the decision at hand.

*Approach to be designed when this wave starts.*

---

## Waves

| Wave | Scope | Side |
|------|-------|------|
| 0 | Upstream hash + `split_at_ranker()` + `IntermediateCache` store | PP |
| 1 | TermNorm: `node_outputs` response + `precomputed` request | TN |
| 2 | Eval gateway integration (cache lookup/populate in `backend_reranker_evaluate`) | PP |
| 3 | Adaptive sensitivity scan (two-phase + per-axis circuit breaker) | PP |
| 4 | Data-informed L1/L2/advisor (analysis context injection) | PP |

Waves 0-2 (caching) and Waves 3-4 (scan + suggestions) are independent and can be developed in parallel.

---

## Entry Criteria

- M7 exit gate passed
- `PipelineSchema` with `node_role` populated (M6 Wave 6)

## Exit Criteria

- Second eval of same query with same upstream config skips upstream nodes (verified via step_timings)
- Sensitivity scan skips dead axes after quick probe phase
- L1 generate prompt includes query difficulty breakdown and failure clusters from accumulated data
