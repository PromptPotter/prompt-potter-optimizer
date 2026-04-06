# Node Standard

**Version:** 0.2.0
**Date:** 2026-03-24
**Status:** Established — `llm_call()` primitive + `optimizer_pipeline.json` config implemented; shared library extraction is future work

---

## Overview

Both TermNorm and PromptPotter use the same primitives — LLM calls, web search, deterministic functions — but each wires them ad-hoc. The node standard defines a shared vocabulary where the LLM interaction primitive is the same everywhere.

Each node type is self-contained: prompt assembly + execution + response parsing in one plug-and-play unit. Optimizer nodes (L1/L2/L3) do extra deterministic work around their LLM calls, but the LLM part uses the same structure as TermNorm's `llm_ranking` or `entity_profiling`.

---

## Type Hierarchy

```
llm                  ← raw prompt → response
├── llm/structured   ← + prompt template + output schema (TermNorm nodes)
│   └── llm/meta     ← + multi-source assembly + context parsing (optimizer nodes)
└── agent            ← + multi-step loop (CritiqueAgent)
web_search           ← external HTTP service
deterministic        ← pure function
evaluation           ← backend call + comparison
```

### `llm` — Base LLM node

Raw prompt → LLM → response. Shared config shape everywhere.

```json
{
  "type": "llm",
  "config": {
    "model": "...",
    "temperature": 0.3,
    "max_tokens": 4096,
    "output_format": "json"
  }
}
```

### `llm/structured` — Template + schema LLM node

Subtype of `llm`. Adds prompt template compilation (`prompt_family` → rendered prompt) + output schema validation.

**TermNorm examples:** `entity_profiling`, `llm_ranking` — each is an `llm/structured` node with a specific prompt template and output schema. Self-contained: give it input data, it assembles the prompt, calls the LLM, parses and validates the response.

```json
{
  "type": "llm/structured",
  "config": {
    "model": "...",
    "temperature": 0.0,
    "prompt_family": "ranking",
    "output_schema": "llm_ranking_output/1"
  }
}
```

### `llm/meta` — Context-aware LLM node

Subtype of `llm/structured`. Adds multi-source prompt assembly (scan_context, critique, task_context, escalation_journal, etc.) + context-aware response parsing.

**Optimizer examples:** `l1_generate`, `l2_refine_context`, `l3_modify_plan` — each is an `llm/meta` node. Self-contained: give it the optimizer context, it assembles the meta-prompt from multiple sources, calls the LLM, and parses the structured response.

```json
{
  "type": "llm/meta",
  "config": {
    "model": "...",
    "temperature": 0.7,
    "prompt_family": "meta_scan_aware",
    "context_sources": ["scan_context", "critique", "task_context", "escalation_journal"],
    "response_parser": "candidate_list"
  }
}
```

### `agent` — Multi-step LLM node

Subtype of `llm`. LLM call + analysis loop + tool use. The CritiqueAgent is an example: it assembles rich stats, calls the LLM, parses the 6-field critique dict.

```json
{
  "type": "agent",
  "config": {
    "model": "...",
    "temperature": 0.3,
    "agent_class": "CritiqueAgent"
  }
}
```

### Non-LLM types

| Type | Purpose | Example config keys |
|------|---------|-------------------|
| `web_search` | External HTTP service | `max_sites`, `num_results` |
| `deterministic` | Pure function | `threshold`, `scorer` |
| `evaluation` | Backend call + comparison (`l1_evaluate`) | `improvement_threshold`, `stale_data_load_protocol`, `rerun_trigger_count`, `samplescan_candidates`, `samplescan_threshold`, `sampleswitch_min_degradation_rate` |

---

## Composability

Every node has one signature: `async def run(ctx: Ctx) -> None`. Reads from ctx, writes to ctx. Self-contained — handles its own prompt assembly, LLM call, and parsing. A pipeline is a list of nodes; the runner loops through them. Node execution is traced via `observed_node()` — see [observability.md](observability.md).

**Key insight:** `llm/meta` inherits from `llm/structured` which inherits from `llm`. Subtypes add prompt assembly and response parsing around the same core LLM call. A new node = configure which subtype + prompt_family + parser.

---

## Pipeline Declaration Format

Both TermNorm and PromptPotter declare their pipelines using the same JSON format. TermNorm's `GET /pipeline` returns a pipeline config; the optimizer declares its pipeline in `promptpotter/config/optimizer_pipeline.json`.

```json
{
  "name": "Pipeline Name",
  "version": "v1.0",
  "nodes": {
    "node_name": {
      "type": "llm|llm/structured|llm/meta|agent|deterministic|evaluation|web_search",
      "node_role": "cache|candidate_source|enricher|ranker",
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

### Node Roles & Exit Points

Every node declares a `node_role` that describes its function in the pipeline:

| Role | Purpose | Produces candidates? |
|------|---------|---------------------|
| `cache` | Short-circuit lookup (e.g., exact match cache) | No |
| `candidate_source` | Generates or retrieves candidate matches | **Yes** |
| `enricher` | Adds context without producing candidates (e.g., web search, profiling) | No |
| `ranker` | Re-ranks/scores candidates from upstream sources | **Yes** |

**Exit point convention:** Nodes with role `candidate_source` or `ranker` are valid pipeline exit points. They MUST declare an `observation_mappings` entry whose `pipeline_key` points to their candidate list output. The candidate list is an ordered array of `{"candidate": string, ...}` objects, best-first.

PromptPotter auto-detects exit points from this metadata. When a pipeline terminates early (e.g., `llm_ranking` excluded), the system reads candidates from the last active exit point's declared output key. This enables:

- **Step-sequence cache reuse**: A cached run with more nodes serves a request for fewer nodes by re-scoring from the appropriate exit point.
- **Partial pipeline execution**: Cached node outputs from a shorter run feed into a longer run via `precomputed`, skipping already-computed nodes.

**Example** — TermNorm's two exit points:

```
token_matching  (candidate_source) → "token_matched_candidates"
llm_ranking     (ranker)           → "ranked_candidates"
```

Excluding `llm_ranking` makes `token_matching` the last exit point. Cached full-pipeline results are re-scored by reading `token_matched_candidates` instead of `ranked_candidates`.

---

## Current State vs Future

### Now (M7)

- **`llm_call()`** (`promptpotter/config/optimizer_pipeline.py`) — shared primitive that reads defaults from a node config dict and allows runtime overrides. All optimizer pipeline nodes use it.
- **`get_node_config()`** — loads node configs from `optimizer_pipeline.json` (cached)
- **`optimizer_pipeline.json`** declares optimizer nodes with the same config shape as TermNorm's pipeline
- **`observed_node()`** (`promptpotter/services/tracing/node_tracer.py`) — traces node execution with timing + Langfuse observations. Callers use node type names as `node_type` (e.g., `"llm/meta"`, `"evaluation"`).
- Optimizer nodes — `l1_generate` (`llm/meta`), `l1_evaluate` (`evaluation`), `critique` (`agent`), `l2_refine_context` (`llm/meta`), `l3_modify_plan` (`llm/meta`) — use `llm_call()` with their declared config from `optimizer_pipeline.json`

### Future (milestone TBD, post-ConnectorProtocol)

- Extract node types into a shared package importable by both repos
- Shared `PipelineContext`, config resolution, and runner

---

## Reference

- **TermNorm pipeline config:** `GET /pipeline` endpoint (see TermNorm repo)
- **Optimizer pipeline config:** [`promptpotter/config/optimizer_pipeline.json`](../promptpotter/config/optimizer_pipeline.json)
- **Observability:** [`docs/observability.md`](observability.md) — node tracing via `observed_node`
