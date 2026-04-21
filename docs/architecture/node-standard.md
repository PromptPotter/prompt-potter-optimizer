# Node Standard

**Version:** 0.3.0
**Date:** 2026-04-21
**Status:** Describes what's in the tree. Anything aspirational lives under § Future work.

---

## Overview

A **node** is one pipeline step — prompt assembly + execution + response parsing in one plug-and-play unit. Both pipeline backends (TermNorm's `entity_profiling`, `llm_ranking`, etc.) and PromptPotter's optimizer loop (`l1_generate`, `critique`, `l2_refine_strategy`, `l3_modify_plan`) are composed from nodes. They share the same three primitives and the same JSON declaration shape.

Every node has one signature: `async def run(ctx: Ctx) -> None`. Reads from ctx, writes to ctx. Self-contained — each node handles its own prompt assembly, LLM call, and parsing.

---

## The three primitives

Everything builds on three shared primitives. There is no Python class hierarchy on top of them — `type:` and `node_role:` in the pipeline JSON (below) carry the taxonomy.

| Primitive | Purpose | Lives in |
|-----------|---------|----------|
| `llm_call(client, messages, node, model, trace_meta, ...)` | Make one LLM call with config overrides + tracing. | `application/optimization/pipeline.py` |
| `get_node_config(node_id)` | Look up a node's resolved config from `optimizer_pipeline.json` (cached). | `application/optimization/pipeline.py` |
| `observed_node(name, node_type, obs=, ...)` | Context manager that opens a Langfuse observation + emits `NodeStart`/`NodeEnd` events. | `infrastructure/tracing/` |

All optimizer nodes call `llm_call()` — none of them talk to an LLM client directly. All nodes wrap their body in `observed_node()`.

The `CritiqueAgent` class (`application/optimization/nodes/critique.py`) is the one multi-step node in-tree: it builds rich stat sections + calls the LLM + parses a 6-field dict. Its runtime surface is the same three primitives.

---

## Pipeline declaration format

Both backends and the optimizer loop declare their pipelines as JSON, consumed by `parse_pipeline_response()` (`application/pipeline_discovery.py`) which builds a `PipelineSchema`. The optimizer's pipeline lives at [`promptpotter/application/optimization/optimizer_pipeline.json`](../../promptpotter/application/optimization/optimizer_pipeline.json); the backend's pipeline is fetched via `GET /pipeline`.

```json
{
  "name": "Pipeline Name",
  "version": "v1.0",
  "nodes": {
    "node_name": {
      "type": "llm | llm/structured | llm/meta | agent | deterministic | evaluation | web_search",
      "node_role": "cache | candidate_source | enricher | ranker",
      "config": { ... },
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

### Node `type` — observability shape

The `type` string tells the tracing layer what kind of Langfuse observation to emit (generation vs. span vs. tool). It does NOT correspond to a Python class. Values in use today:

- `llm` — one LLM call, raw prompt → response
- `llm/structured` — one LLM call with a prompt template + output schema (TermNorm's `entity_profiling`, `llm_ranking`)
- `llm/meta` — one LLM call with multi-source prompt assembly (optimizer's `l1_generate`, `l2_refine_strategy`, `l3_modify_plan`)
- `agent` — multi-step LLM loop (`CritiqueAgent`)
- `web_search` — external HTTP service
- `deterministic` — pure function (e.g. fuzzy matching)
- `evaluation` — backend call + comparison (`l1_evaluate`)

### Node `node_role` — pipeline function

| Role | Purpose | Produces candidates? |
|------|---------|---------------------|
| `cache` | Short-circuit lookup (e.g., exact match cache) | No |
| `candidate_source` | Generates or retrieves candidate matches | **Yes** |
| `enricher` | Adds context without producing candidates (e.g., web search, profiling) | No |
| `ranker` | Re-ranks/scores candidates from upstream sources | **Yes** |

**Exit point convention.** Nodes with role `candidate_source` or `ranker` are valid pipeline exit points. They MUST declare an `observation_mappings` entry whose `pipeline_key` points to their candidate list output. The candidate list is an ordered array of `{"candidate": string, ...}` objects, best-first.

PromptPotter auto-detects exit points from this metadata. When a pipeline terminates early (e.g., `llm_ranking` excluded), the system reads candidates from the last active exit point's declared output key. This enables:

- **Step-sequence cache reuse**: A cached run with more nodes serves a request for fewer nodes by re-scoring from the appropriate exit point.
- **Partial prior-run reuse**: Queries that short-circuited before the divergence between two searchpoints can be replayed from the earlier run's stored results (`DatasetRunStore.load_reusable_results`).

**Example** — a multi-node pipeline's two exit points:

```
token_matching  (candidate_source) → "token_matched_candidates"
llm_ranking     (ranker)           → "ranked_candidates"
```

Excluding `llm_ranking` makes `token_matching` the last exit point. Cached full-pipeline results are re-scored by reading `token_matched_candidates` instead of `ranked_candidates`.

---

## Reference

- **Backend pipeline config:** `GET /pipeline` endpoint
- **Optimizer pipeline config:** [`promptpotter/application/optimization/optimizer_pipeline.json`](../../promptpotter/application/optimization/optimizer_pipeline.json)
- **Observability:** [`observability.md`](../observability.md) — node tracing via `observed_node`

---

## Future work

The `type` strings are currently **observability shape strings**, not class names. If a future refactor extracts a shared node-type package (post-ConnectorProtocol), the taxonomy below may become an actual class hierarchy:

```
llm                  ← raw prompt → response
├── llm/structured   ← + prompt template + output schema
│   └── llm/meta     ← + multi-source assembly + context parsing
└── agent            ← + multi-step loop
web_search
deterministic
evaluation
```

Do not cite this hierarchy as an existing invariant. The string is the contract today; the hierarchy is a sketch.
