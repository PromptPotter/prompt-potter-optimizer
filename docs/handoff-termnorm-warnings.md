# Hand-off: TermNorm Warning Contract for Stale Data Protocol

**Date:** 2026-04-02
**From:** PromptPotter Optimizer
**To:** TermNorm backend

---

## Summary

PromptPotter now has a **stale data load protocol** that reacts to degraded cached queries. The protocol's trigger is the `diagnostics.warnings[]` array in TermNorm's `/matches` response. Currently, TermNorm's `web_search` node emits warnings when **all** fetched URLs return content (e.g., "10 of 10 fetched URLs returned content"), which is not a degradation — it's the happy path. This causes every query to be flagged as degraded, triggering unnecessary cache invalidation and protocol execution.

---

## What PromptPotter Does with Warnings

### Warning format (expected from TermNorm)

Each item in `diagnostics.warnings[]` must be:

```json
{
  "step": "web_search",
  "code": "partial_scrape",
  "message": "3 of 10 fetched URLs returned content"
}
```

### How PromptPotter consumes warnings

| Consumer | What it reads | Effect |
|----------|--------------|--------|
| **`_is_degraded()`** (`prompt_eval.py`) | `bool(diagnostics.warnings)` — any non-empty list | Triggers stale data protocol on all degraded queries (cached and fresh) |
| **`count_degraded_queries()`** (`metrics.py`) | Count of results with non-empty warnings | Invalidates full-run cache when >0; feeds composite score |
| **`DegradationCheck`** (`escalation.py`) | `degraded_rate = count / total` | Aborts evaluation mid-round if rate >= threshold (default 0.4) → escalates to L2/L3 |
| **`extract_warning_types()`** (`critique.py`) | `"{step}:{code}"` strings | Classifies warning type for escalation routing (e.g., `web_search:partial_scrape` → L2) |
| **Warning inventory** (`critique.py`) | Per-query warning accumulation across rounds | Feeds probe round targeting + critique analysis |
| **SearchMemory** (`search_memory.py`) | Per-query degradation counting | Historical degradation rate for `sampleswitch` step |
| **Display** (`display.py`) | `w['step']` + `w['message']` | Shows `⚠ web_search: ...` lines under each query result |

### Stale data protocol (3-step ladder on degraded cached queries)

When a cached query has non-empty `diagnostics.warnings`:

1. **rerun** — re-run the query fresh (after N observations, configurable `rerun_trigger_count=3`)
2. **samplescan** — re-run with no pipeline_params override to test if degradation is param-dependent
3. **sampleswitch** — consult SearchMemory; exclude query if historically unreliable (degradation rate >= 0.5)

All thresholds are tunable on `l1_evaluate` node config in `optimizer_pipeline.json`.

---

## What Needs to Change in TermNorm

### Problem

The `web_search` node currently emits a warning whenever it reports URL fetch counts, regardless of whether the count is below the minimum threshold. Example:

```json
{"step": "web_search", "code": "partial_scrape", "message": "10 of 10 fetched URLs returned content"}
```

This is **not degradation** — all 10 URLs succeeded. But PromptPotter treats any non-empty `diagnostics.warnings` as degradation.

### Fix

The `web_search` node should **only emit a warning when the number of usable documents falls below the minimum threshold**. The threshold is a hyperparameter (currently ~7 in the default config).

**Emit warning when:** `fetched_count < min_documents_threshold`

```json
{"step": "web_search", "code": "low_document_count", "message": "3 of 10 fetched URLs returned content (min: 7)"}
```

**Do NOT emit warning when:** `fetched_count >= min_documents_threshold`

Instead, the fetch stats can go into a non-warning field (e.g., `diagnostics.stats` or `diagnostics.info`) for observability without triggering degradation logic.

### Warning code semantics

| Code | When to emit | Severity for PromptPotter |
|------|-------------|--------------------------|
| `low_document_count` | `usable_docs < min_threshold` | Degradation — triggers stale data protocol + escalation |
| `partial_scrape` | Some URLs failed to scrape but total still >= threshold | Informational — could go in `diagnostics.info` instead |
| `no_results` | Zero documents returned | Critical degradation |
| `timeout` | Search timed out | Critical degradation |

### Other nodes

The same contract applies to any TermNorm node that emits warnings:

- `entity_profiling` — the "Token limit error" warning (seen in the logs) is a real degradation and should stay
- Any future node — only emit `diagnostics.warnings[]` for conditions that actually degrade result quality

### Backward compatibility

PromptPotter has no backward compatibility constraint. Once TermNorm fixes the warning emission, PromptPotter's protocol will work correctly with no changes needed.

---

## PromptPotter Files Reference

| File | What it does with warnings |
|------|---------------------------|
| `promptpotter/services/prompt_eval.py` | `_is_degraded()`, `_execute_stale_data_protocol()`, cache invalidation |
| `promptpotter/services/metrics.py` | `count_degraded_queries()` |
| `promptpotter/services/campaign/escalation.py` | `DegradationCheck`, `DEFAULT_STRATEGIES` routing |
| `promptpotter/services/campaign/critique.py` | `extract_warning_types()`, warning inventory |
| `promptpotter/services/search/search_memory.py` | `query_degradation_rate()`, degradation counting in `_ingest_run()` |
| `promptpotter/config/optimizer_pipeline.json` | Stale data protocol thresholds on `l1_evaluate` node |
| `notebooks/campaign_lib/display.py` | `_fmt_query_result()` — per-query warning + protocol action display |

---

## Testing After TermNorm Fix

1. Run a sensitivity scan — queries should NOT show `⚠ web_search: N of N fetched URLs` when all URLs succeed
2. Cached results should NOT be invalidated when no real degradation exists
3. Only queries with actual degradation (low doc count, timeouts, token limit errors) should trigger the stale data protocol
4. `DegradationCheck` should only fire when real degradation rate exceeds threshold
