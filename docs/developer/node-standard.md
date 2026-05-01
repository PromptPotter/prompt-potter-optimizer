# Node Standard

**Version:** 0.5.0
**Status:** Describes what's in the tree.

The optimizer loop itself — `l1_generate`, `l1_critique`, `l2_context`, `l3_plan` — is built from nodes. So is every backend pipeline step. Anyone can write a new node; a JSON declaration is all that's needed to register it.

Built-in nodes cover the common cases: fixed-config deterministic steps (lookup, fuzzy matching), LLM nodes, and multi-step agent nodes. For pipelines that need database-backed candidate assignment, PromptPotter ships a basic assignment pipe. In practice, most pipelines reduce to one or more LLM nodes — an LLM is a universal approximator and handles the majority of pipeline tasks.

For the concept-level view of what a node can do, see [../concepts/nodes-and-pipelines.md](../concepts/nodes-and-pipelines.md). This page is the implementation spec.

---

## Wiring a new node

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

That's it. The `DegradationCheck` counts the warning, synthesizes a `RuntimeFailure` on the offending candidate, and the round completes normally. L2 reads the failure next round and adjusts its own strategy to steer L1 away from the failing config region. If the pattern persists, L3 replans. Full mechanics in [self-healing-internals.md](self-healing-internals.md).

---

## Pipeline declaration format

Both backends and the optimizer loop declare their pipelines as JSON. The optimizer's pipeline lives at `promptpotter/application/optimization/optimizer_pipeline.json`; the backend's pipeline is fetched via `GET /pipeline`.

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

The `pipelines` dict composes named sequences from the node pool. The same node can appear in multiple pipeline sequences.

Prompts and structured-output schemas are referenced by `(family, version)` from each node's `config` and resolved against the top-level `resolved_prompts` / `resolved_schemas` registries — same shape `parse_pipeline_response` (in `application/pipeline_discovery.py`) consumes for backends. The optimizer manifest carries the registries inline; the backend serves them via `GET /pipeline`.

---

## Node capability reference

Capabilities are opt-in. A deterministic node declares none of these; an LLM node in the optimizer loop may use all of them.

### All nodes

- **Exit-point declaration** — a node that produces candidates declares where its output lives. PromptPotter reads this to find the last active exit point, enabling step-sequence cache reuse and partial run replay.
- **Escalation signals** — a node returns an `EscalationSignal` to the orchestrator to eliminate a candidate or abort the round, rather than failing silently.

### LLM nodes additionally

- **Prompt exposure** — an LLM node exposes its prompt as a `PromptTemplate`. PromptPotter reads, displays, and optimizes it. See [prompt-scheme-internals.md](prompt-scheme-internals.md).
- **Optimizer-discoverable parameters** — the node declares which parameters it accepts and their valid values. PromptPotter picks these up automatically as optimization axes — no hardcoding.
- **Self-healing Loop 1** — `ValidationFailure` caught at L1 parse time by `L1_SCHEMA_COMPLIANCE` validator; L2 teaches L1 not to repeat the invalid proposal. See [self-healing-internals.md](self-healing-internals.md).
- **Self-healing Loop 2** — `RuntimeFailure` attached to the candidate mid-evaluation; L2 adjusts its own strategy; L3 replans if the pattern persists.
- **Warnings → optimizer context** — per-query warnings from the node accumulate in `warning_inventory` and feed L2's `warning_inventory` surface field.
- **Warnings → escalation counter** — sustained degradation increments a patience counter. When patience runs out, the orchestrator escalates or halts the round.
- **Warnings → search-point attachment** — failures are pinned to the exact configuration that caused them, not to the round. Future proposals that resemble the failing config are penalized.
- **Skip** — a candidate producing too many degraded or empty results is eliminated mid-run; remaining candidates continue.
- **Abort** — a candidate can signal the round should stop entirely.
- **Fatal fast-path** — fatal codes derived by `classify_result()` (in `application/optimization/elimination.py`) eliminate a candidate on the first query, with no rate threshold.

---

## Reference

| Resource | What it covers |
|----------|---------------|
| [self-healing-internals.md](self-healing-internals.md) | Four LLM-to-LLM healing loops in full |
| [../methods/candidate-elimination.md](../methods/candidate-elimination.md) | Full elimination ladder — validation skip through Wilcoxon cutoff |
| [prompt-scheme-internals.md](prompt-scheme-internals.md) | Prompt field decomposition, `PromptTemplate` |
| [../operations/observability.md](../operations/observability.md) | Node tracing and Langfuse integration |
| [code-layout.md](code-layout.md) | Pipeline exit points, cache reuse |
| `promptpotter/application/optimization/optimizer_pipeline.json` | Live optimizer node declarations |
| `GET /pipeline` | Backend self-description — source of the pipeline schema at runtime |
