# Node Standard

The optimizer loop (`l1_generate`, `l1_critique`, `l2_context`, `l3_plan`) and every backend pipeline step are built from nodes. Anyone can write a new one — a JSON declaration is all that's needed to register it.

Built-in nodes cover fixed-config deterministic steps (lookup, fuzzy matching), LLM nodes, and multi-step agent nodes. PromptPotter ships a basic database-backed candidate-assignment pipe. In practice most pipelines reduce to one or more LLM nodes.

Concept-level: [`../concepts/nodes-and-pipelines.md`](../concepts/nodes-and-pipelines.md). Operator integration walk-through (wiring a new node into self-healing): [`../operations/backend-integration.md § Wiring a new node into self-healing`](../operations/backend-integration.md).

## Pipeline declaration format

Both backends and the optimizer loop declare pipelines as JSON. Optimizer's at `promptpotter/application/optimization/dispatch/pipeline.json`; backend's served by `GET /pipeline`.

```json
{
  "name": "Pipeline Name",
  "version": "v1.0",
  "nodes": {
    "node_name": {
      "type": "llm | llm/structured | llm/meta | agent | deterministic | measurement | web_search",
      "node_role": "cache | candidate_source | enricher | ranker",
      "config": {
        "prompt_family": "node_name",
        "prompt_version": 1,
        "schema_family": "node_name",
        "schema_version": 1
      },
      "optimizer": {
        "observation_mappings": [
          {"pipeline_key": "output_key_name", "output_field": "field_in_response"}
        ]
      }
    }
  },
  "pipelines": {
    "pipeline_name": ["node1", "node2", "node3"]
  },
  "resolved_prompts": {
    "node_name/1": { "persona": "...", "task_intent": "...", "...": "..." }
  },
  "resolved_schemas": {
    "node_name/1": { "fields": ["..."], "json_schema": { "...": "..." } }
  }
}
```

The `pipelines` dict composes named sequences from the node pool. The same node can appear in multiple sequences.

Prompts and structured-output schemas are referenced by `(family, version)` from each node's `config` and resolved against top-level `resolved_prompts` / `resolved_schemas` registries — same shape `parse_pipeline_response` (`domain/pipeline_parsing.py`) consumes for backends. The optimizer manifest carries the registries inline; the backend serves them via `GET /pipeline`.

## Node capabilities

Capabilities are opt-in. A deterministic node declares none; an LLM node in the optimizer loop may use all.

### All nodes

- **Exit-point declaration** — a node producing candidates declares where its output lives. Enables step-sequence cache reuse and partial run replay.
- **Escalation signals** — return `EscalationSignal` to eliminate a candidate or abort the round, rather than failing silently.

### LLM nodes additionally

- **Prompt exposure** — expose the prompt as a `PromptTemplate`. PromptPotter reads, displays, and optimises it. See [`README.md § Prompt structure`](README.md).
- **Optimizer-discoverable parameters** — declare accepted parameters and valid values. PromptPotter picks these up automatically as optimisation axes.
- **Self-healing Wound 1** — `ValidationFailure` caught at L1 parse time by `L1_SCHEMA_COMPLIANCE`; L2 teaches L1 not to repeat. See [`self-healing-internals.md`](self-healing-internals.md).
- **Self-healing Wound 2** — `RuntimeFailure` attached to the candidate mid-eval; L2 adjusts; L3 replans on persistence.
- **Warnings → optimizer context** — per-sample warnings drive probe-round subset selection on the cycle (`Cycle.warned_queries`).
- **Warnings → escalation counter** — sustained degradation increments a patience counter.
- **Warnings → search-point attachment** — failures pin to the exact configuration that caused them, not the round.
- **Skip** — a candidate producing too many degraded or empty results is eliminated mid-run.
- **Abort** — a candidate can signal the round should stop.
- **Fatal fast-path** — fatal codes derived by `classify_result()` (`application/optimization/pobb/elimination.py`) eliminate a candidate on the first query, with no rate threshold.

## Reference

| Resource | Covers |
|----------|--------|
| [![self-healing-internals](https://img.shields.io/badge/self--healing--internals-red?style=for-the-badge)](self-healing-internals.md) | Four LLM-to-LLM wounds |
| [![candidate-elimination](https://img.shields.io/badge/candidate--elimination-black?style=for-the-badge)](../methods/candidate-elimination.md) | Full elimination ladder — validation skip through PoBB cutoff |
| [![developer/README](https://img.shields.io/badge/developer%2FREADME-red?style=for-the-badge)](README.md) | Architecture brief — prompt structure, dispatch, scoring node, cross-run memory |
| [![observability](https://img.shields.io/badge/observability-black?style=for-the-badge)](../operations/observability.md) | Node tracing and Langfuse integration |
| `promptpotter/application/optimization/dispatch/pipeline.json` | Live optimizer node declarations |
| `GET /pipeline` | Backend self-description — source of pipeline schema at runtime |
