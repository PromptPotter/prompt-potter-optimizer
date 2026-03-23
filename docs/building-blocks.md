# Building Block Standard

**Version:** 0.1.0
**Date:** 2026-03-20
**Status:** Established — `llm_call()` primitive + `optimizer_pipeline.json` config implemented; shared library extraction is future work

---

## Overview

Both TermNorm and PromptPotter use the same primitives — LLM calls, web search, deterministic functions — but each wires them ad-hoc. The building block standard defines a shared vocabulary where the LLM interaction primitive is the same everywhere.

Each node type is self-contained: prompt assembly + execution + response parsing in one plug-and-play unit. Optimizer steps (L1/L2/L3) do extra deterministic work around their LLM calls, but the LLM part uses the same structure as TermNorm's `llm_ranking` or `entity_profiling`.

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

Subtype of `llm`. LLM call + analysis loop + tool use. The CritiqueAgent is an example: it assembles rich stats, calls the LLM, parses the 5-field critique dict.

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

### `web_search` — External service node

```json
{
  "type": "web_search",
  "config": {
    "max_sites": 7,
    "num_results": 20
  }
}
```

### `deterministic` — Pure function node

```json
{
  "type": "deterministic",
  "config": {
    "threshold": 70,
    "scorer": "WRatio"
  }
}
```

### `evaluation` — Backend evaluation node

Calls external backend, compares results. The `l1_evaluate` step.

```json
{
  "type": "evaluation",
  "config": {
    "improvement_threshold": 0.01
  }
}
```

---

## Composability

Every node has one signature: `async def run(ctx: Ctx) -> None`. Reads from ctx, writes to ctx. Each node is self-contained — it handles its own prompt assembly, LLM call, and parsing internally.

```python
# Each node is self-contained — no manual wiring of assembly/parse
l1_node = LLMMetaNode(config=pipeline["l1_generate"])       # llm/meta type
l2_node = LLMMetaNode(config=pipeline["l2_refine"])          # llm/meta type
ranking = LLMStructuredNode(config=pipeline["llm_ranking"])  # llm/structured type
critique = AgentNode(config=pipeline["critique"])             # agent type
fuzzy   = DeterministicNode(config=pipeline["fuzzy"])         # deterministic type

# Pipeline = list of nodes. Runner just loops:
async def run_pipeline(nodes: list, ctx: Ctx):
    for node in nodes:
        await node.run(ctx)

# TermNorm pipeline:
termnorm = [fuzzy, web_search, entity_profiling, token_match, ranking]

# Optimizer L1 round:
optimizer_round = [l1_node, evaluator, critique]

# Recombining is trivial — just change the list:
optimizer_with_l2 = [l1_node, evaluator, critique, l2_node]
ranking_with_critique = [ranking, critique]  # drop critique into any pipeline
```

**Key insight:** `llm/meta` inherits from `llm/structured` which inherits from `llm`. The LLM call is always the same internally — subtypes add prompt assembly and response parsing around it. A new node = configure which subtype + prompt_family + parser.

---

## Pipeline Declaration Format

Both TermNorm and PromptPotter declare their pipelines using the same JSON format. TermNorm's `GET /pipeline` returns a pipeline config; the optimizer declares its pipeline in `api/config/optimizer_pipeline.json`.

```json
{
  "name": "Pipeline Name",
  "version": "v1.0",
  "nodes": {
    "step_name": {
      "type": "llm|llm/structured|llm/meta|agent|deterministic|evaluation|web_search",
      "config": { ... }
    }
  },
  "pipelines": {
    "pipeline_name": ["step1", "step2", "step3"]
  }
}
```

The `pipelines` dict composes named sequences from the node pool. The same node can appear in multiple pipeline sequences.

---

## Current State vs Future

### Now (M7 Wave G)

- **`llm_call()`** (`api/core/llm_call.py`) — shared primitive that reads defaults from a node config dict and allows runtime overrides. All optimizer pipeline steps use it.
- **`get_node_config()`** — loads node configs from `optimizer_pipeline.json` (cached)
- **`optimizer_pipeline.json`** declares optimizer nodes with the same config shape as TermNorm's pipeline
- **`observed_step`** traces optimizer steps using the same observability infrastructure
- Optimizer building block nodes — `generate_candidates` (`llm/meta`), `refine_context` (`llm/meta`), `modify_plan` (`llm/meta`), `CritiqueAgent` (`agent`) — use `llm_call()` with their declared config from `optimizer_pipeline.json`

### Runtime tracing

- **`observed_step()`** (`api/services/obs/step_tracer.py`) — provides step-level tracing (timing + Langfuse observations). Callers use building block type names as `step_type` (e.g., `"llm/meta"`, `"evaluation"`).

### Future (milestone TBD, post-ConnectorProtocol)

- Extract building block types into a shared package importable by both repos
- Shared `PipelineContext`, config resolution, and runner

---

## Reference

- **TermNorm pipeline config:** `GET /pipeline` endpoint (see [`docs/connectors/termnorm.md`](connectors/termnorm.md))
- **Optimizer pipeline config:** [`api/config/optimizer_pipeline.json`](../api/config/optimizer_pipeline.json)
- **M7 spec §14:** [`docs/specs/m7-optimizer-pipeline.md`](specs/m7-optimizer-pipeline.md) §14
- **Observability:** [`docs/observability.md`](observability.md) — step tracing via `observed_step`
