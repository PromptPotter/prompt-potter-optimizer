# Node Standard

**Version:** 0.5.0
**Date:** 2026-04-24
**Status:** Describes what's in the tree.

---

## Overview

The optimizer loop itself — `l1_generate`, `critique`, `l2_refine_strategy`, `l3_modify_plan` — is built from nodes. So is every backend pipeline step. Anyone can write a new node; a JSON declaration is all that's needed to register it.

Built-in nodes cover the common cases: fixed-config deterministic steps (lookup, fuzzy matching), LLM nodes, and multi-step agent nodes. For pipelines that need database-backed candidate assignment, PromptPotter ships a basic assignment pipe. In practice, most pipelines reduce to one or more LLM nodes — an LLM is a universal approximator and handles the majority of pipeline tasks.

---

## Node capabilities

Capabilities are opt-in. A deterministic node declares none of these; an LLM node in the optimizer loop may use all of them.

### All nodes

- **Exit-point declaration** — a node that produces candidates declares where its output lives. PromptPotter reads this to find the last active exit point, enabling step-sequence cache reuse and partial run replay. See [`overview.md`](overview.md).
- **Escalation signals** — a node signals the orchestrator to eliminate a candidate or abort the round entirely, rather than failing silently.

### LLM nodes additionally

- **Prompt exposure** — an LLM node exposes its prompt so PromptPotter can read, display, and optimize it. The prompt is broken into named fields (`system`, `few_shot`, `instruction`, `output_format`, `cot_directive`). See [`prompt-scheme.md`](prompt-scheme.md).
- **Optimizer-discoverable parameters** — the node declares which parameters it accepts and their valid values. PromptPotter picks these up automatically as optimization axes — no hardcoding required on either side. This is what makes a node tunable by the AI without any PromptPotter code knowing the node's internals.
- **Self-healing — Rail 1** — if the optimizer proposes a parameter value the node doesn't accept, the proposal is rejected before any run. The optimizer learns from this and won't propose it again. See [`optimization.md § Self-healing`](optimization.md).
- **Self-healing — Rail 2** — if a candidate's configuration produces degraded results consistently, the failure is recorded against that configuration. The optimizer adjusts its strategy; if the pattern continues, a higher-level replanning step takes over. See [`optimization.md § Self-healing`](optimization.md).
- **Warnings → optimizer context** — per-query warnings from the node accumulate and are fed to the optimizer as context, even when no hard failure has fired.
- **Warnings → escalation counter** — sustained degradation increments a patience counter. When patience runs out, the orchestrator escalates to a higher layer or halts the round. See [`optimization.md § Escalation ladder`](optimization.md).
- **Warnings → search-point attachment** — failures are pinned to the exact configuration that caused them, not to the round. Future proposals that resemble the failing config are penalized.
- **Skip** — a candidate producing too many degraded or empty results is eliminated mid-run; the remaining candidates continue normally.
- **Abort** — a candidate can signal that the round should stop entirely.
- **Fatal fast-path** — certain failure codes eliminate a candidate on the very first query, with no rate threshold.

---

## Wiring a New Node

Reference: `web_search`. Default chain works for **any** target pipeline node that emits warnings.

| Step | What | Required? |
|------|------|-----------|
| **1** | Emit `diagnostics.warnings[]` with `{step, code, message}` from the backend | **Yes** |
| **2** | Add routing strategy for `{step}:{code}` | No (defaults to L2) |
| **3** | Add anomaly detector | No |
| **4** | Set `degradation_threshold` in campaign config | **Yes** (0 = disabled) |

Example — adding `entity_profiling` error detection:

```json
{"step": "entity_profiling", "code": "schema_error", "message": "Failed to parse JSON"}
```

That's it. The degradation check counts the warning, synthesises a runtime failure on the offending candidate, and the round completes normally. L2 reads the failure next round and adjusts its own strategy to steer L1 away from the failing config region. If the pattern persists, L3 replans.

---

## Pipeline declaration format

Both backends and the optimizer loop declare their pipelines as JSON. The optimizer's pipeline lives at [`promptpotter/application/optimization/optimizer_pipeline.json`](../../promptpotter/application/optimization/optimizer_pipeline.json); the backend's pipeline is fetched via `GET /pipeline`.

```json
{
  "name": "Pipeline Name",
  "version": "v1.0",
  "nodes": {
    "node_name": {
      "type": "llm | llm/structured | llm/meta | agent | deterministic | evaluation | web_search",
      "node_role": "cache | candidate_source | enricher | ranker",
      "config": { },
      "optimizer": {
        "observation_mappings": [
          {"pipeline_key": "output_key_name", "output_field": "field_in_response"}
        ]
      }
    }
  },
  "pipelines": {
    "pipeline_name": ["node1", "node2", "node3"]
  }
}
```

The `pipelines` dict composes named sequences from the node pool. The same node can appear in multiple pipeline sequences.

---

## Reference

| Resource | What it covers |
|----------|---------------|
| [`optimization.md § Self-healing optimization`](optimization.md) | Rail 1 + Rail 2 mechanics in full |
| [`optimization.md § Escalation ladder`](optimization.md) | Full elimination sequence — validation skip through campaign abort |
| [`prompt-scheme.md`](prompt-scheme.md) | Prompt field decomposition, `PromptTemplate` |
| [`observability.md`](../observability.md) | Node tracing and Langfuse integration |
| [`overview.md`](overview.md) | Pipeline exit points, cache reuse |
| [`optimizer_pipeline.json`](../../promptpotter/application/optimization/optimizer_pipeline.json) | Live optimizer node declarations |
| `GET /pipeline` | Backend self-description — source of the pipeline schema at runtime |
