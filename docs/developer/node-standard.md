# Node standard + the `pipeline.yaml` contract

The optimizer loop (`l1_generate`, `l1_critique`, `l2_context`, `l3_plan`) and every backend pipeline step are built from nodes — **same JSON declaration format, same registry.** That symmetry is what lets the optimizer self-inspect: search memory tracks warnings from both sides, self-healing applies to both, and the patience counters watching a backend degrade and an optimizer stall are the same shape.

Three reasons nodes-not-monoliths, mirroring prompt decomposition: measurable axes per node · independent mutation (cache reuses up to the changed node) · extensibility without coupling (anyone can write a node — a JSON declaration registers it).

Built-in nodes cover fixed-config deterministic steps (lookup, fuzzy matching), LLM nodes, and multi-step agent nodes. PromptPotter ships a basic database-backed candidate-assignment pipe. In practice most pipelines reduce to one or more LLM nodes.

This page is both the node model and the **strict wire shape** PromptPotter parses from `GET /pipeline` (or from a local `datasets/{name}/pipeline.yaml`). Every connector publishes this shape, and the **same parser** consumes `promptpotter/assets/optimizer/pipeline.yaml` unchanged. Writing a connector or extending the optimizer manifest — this is the contract you implement against.

The silent-harm parts are tested — flat-format rejection and content-hash sensitivity, by [`tests/test_integrity.py`](../../tests/test_integrity.py) (`test_pipeline_params_rejects_flat_param_map`, `test_content_hash_distinguishes_pipeline_params`). The rest fails loud: a malformed pipeline is a loud setup error, so no standing test — see [`../../tests/CLAUDE.md`](../../tests/CLAUDE.md). Operator walk-through for wiring a new node into self-healing: [`../operations/backend-integration.md`](../operations/backend-integration.md) § Self-healing a node.

## Pipeline declaration format

Both backends and the optimizer loop declare pipelines as JSON. The optimizer's lives at `promptpotter/assets/optimizer/pipeline.yaml`; a backend's is served by `GET /pipeline`.

```json
{
  "name": "Pipeline Name",
  "version": "v1.0",
  "backend_type": "termnorm",
  "nodes": {
    "node_name": {
      "type": "generation",
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

The `pipelines` dict composes named sequences from the node pool; the same node can appear in several. Prompts and structured-output schemas are referenced by `(family, version)` from each node's `config` and resolved against the top-level `resolved_prompts` / `resolved_schemas` registries — the same shape `parse_pipeline_response` (`domain/pipeline_parsing.py`) consumes for backends. The optimizer manifest carries the registries inline; a backend serves them via `GET /pipeline`.

## Top-level shape

| Field | Required | Type | Notes |
|---|---|---|---|
| `name` | required | `str` | Connector / pipeline display name. Lowercased into `PipelineSchema.name`. |
| `nodes` | required | `dict[str, NodeDecl]` | Map of node-name → node declaration. See **Node declaration**. |
| `pipelines` | required | `dict[str, list[str]]` | Named sequences over `nodes` keys. Must contain `default` — the active step order unless a campaign overrides it. |
| `version` | optional | `str` | Connector schema version; rendered into `PipelineSchema.version`. |
| `description` | optional | `str` | One-line human description. |
| `available_models` | optional | `list[str]` | Models the connector exposes — surfaced into `PipelineSchema.available_models`. |
| `backend_name` | optional | `str` | Human label for the backend. |
| `backend_type` | **required** | `str` | The connector KIND (`termnorm` / `promptpotter` / …). Picks the connector at init (`wiring._read_backend_type` raises when absent), and is served on `CampaignSummary.backend_type` — the ONE test for a self-optimizing (L4) campaign, which the webapp branches on (`isSelfOptimization`). Never a `PipelineSchema` field: the parser drops it, so readers take it off the raw overlay. |
| `resolved_prompts` | optional | `dict[str, ResolvedPrompt]` | Prompt registry keyed by `"{family}/{version}"`. Each node references its prompt via `config.prompt_family` + `config.prompt_version`. |
| `resolved_schemas` | optional | `dict[str, ResolvedSchema]` | Output-schema registry keyed by `"{family}/{version}"`. Each node references its schema via `config.schema_family` + `config.schema_version`. |
| `view` | optional | `dict` | Diagram metadata for the webapp; ignored by the parser. |

## Node declaration (`nodes[name]`)

**PromptPotter parses a SUBSET of this file.** The parser builds `PipelineNode` from `type`, `node_role`, `config` and the `optimizer` sub-object — nothing else. The rest (`description`, `runtime`, `short_circuit`, `input_schema`) is the **backend's self-description**: it states the node's own topology for a human or a future reader, and no `PipelineNode` field carries it. That is by design, not rot — do not re-file these as dead keys. The mirror rule: a key PP does not *use* gets no model field, but the key still belongs in the file and in this table. "Required" below means *a connector must publish it*, not *PP reads it*.

| Field | Required | Type | Notes |
|---|---|---|---|
| `type` | required | `str` | Wire type — `generation`, `cache`, `retriever`, `tool`, `optimizer_prompt` for connector-served nodes; `llm/optimizer`, `measurement`, `agent` for the optimizer's own internal nodes (`assets/optimizer/pipeline.yaml`). Mapped to `PipelineNode.wire_type`. |
| `node_role` | optional | `str` | One of `""`, `candidate_source`, `ranker`, `enricher`, `cache`. Mapped to `PipelineNode.node_type` (the typed `NodeType` enum). Absent on every `_optimizer` node; parsed as `""`. |
| `description` | required | `str` | One-line node description. Self-description — not parsed. |
| `runtime` | connector-served | `str` | Where the node runs **inside the backend's own topology**: `backend`, `frontend` (e.g. TermNorm's Excel add-in runs `cache_lookup` / `fuzzy_matching` client-side), `in_process`. Orthogonal to `Connector.execution`, which says how *PromptPotter* reaches the backend — one `remote_http` connector legitimately mixes all three (`lca-termnorm` does). Self-description — not parsed. Absent on `_optimizer` nodes. |
| `short_circuit` | connector-served | `bool` | Whether a successful match here bypasses downstream nodes (`lca-termnorm`'s two `frontend` nodes set it — a cache hit answers without reaching the backend). Self-description — not parsed. |
| `config` | optional | `dict` | Node-local defaults. For LLM nodes typically `{model, temperature, max_tokens, ...}`. For optimizer nodes also `{prompt_family, prompt_version, schema_family, schema_version}` keys that index into the registries. |
| `prompt_info` | optional | `dict` | Inline `{family, template_variables, description}` — marks the node as prompt-bearing (the candidate-prompt injection point). Used when no `resolved_prompts` registry is present. |
| `output_schema` | — | — | **Not a node-level key.** An inline output schema is declared at `config.output_schema` — the same place the connector forwards it to the backend from, so there is one schema, not a display copy beside a wire copy. Parsed by `parse_resolved_schema` into the node's read-model exactly like a `resolved_schemas` entry, and locked against the optimizer (`SCHEMA_OWNED_FIELDS`). |
| `input_schema` | optional | `dict` | Reserved for future input-validation work. |
| `optimizer` | optional | `dict` | See **`optimizer` sub-object**. Required for any node PromptPotter is allowed to mutate or trace. |

## `optimizer` sub-object

Pinned per-node so PromptPotter knows what is mutable and how trace data maps back into pipeline state.

| Field | Required | Type | Notes |
|---|---|---|---|
| `param_keys` | required | `list[str]` | Wire-name params PromptPotter is allowed to mutate. Drives L1's mutation surface and the JSON-schema enum constraints fed to the LLM. |
| `observation_mappings` | required | `list[ObservationMapping]` | One entry per pipeline-data field this node writes. Each is `{pipeline_key: str, output_field: str \| null, is_llm: bool}`. |
| `langfuse_type` | required | `str` | Trace-span kind — one of `generation`, `tool`, `retriever`, `span`. |
| `observation_name` | optional | `str` | Trace-name override. Defaults to the node name. |
| `display_tag` | optional | `str` | Short name for dashboard / webapp display. |
| `param_descriptions` | optional | `dict[str, str]` | One-line description per param key. Surfaced into L1's prompt as the param catalogue. |
| `param_allowed_values` | optional | `dict[str, list[str]]` | Enum constraint per param. Drives both L1 prompt guidance and the JSON-schema enum constraint on structured-output generation, plus post-hoc `ValidationFailure` attachment in `validate_overrides`. |

## Node capabilities

Capabilities are opt-in. A deterministic node declares none; an LLM node in the optimizer loop may use all.

### All nodes

- **Exit-point declaration** — a node producing candidates declares where its output lives. Enables step-sequence cache reuse and partial run replay.
- **Escalation signals** — return `EscalationSignal` to eliminate a candidate or abort the round, rather than failing silently.

### LLM nodes additionally

- **Prompt exposure** — expose the prompt as a `PromptTemplate`. PromptPotter reads, displays, and optimises it. See [`README.md`](README.md) § 1. Prompt structure.
- **Optimizer-discoverable parameters** — declare accepted parameters and valid values. PromptPotter picks these up automatically as optimisation axes, with no hardcoding on either side.
- **Self-healing Wound 1** — `ValidationFailure` caught at L1 parse time by `L1_SCHEMA_COMPLIANCE`; L2 teaches L1 not to repeat. See [`self-healing-internals.md`](self-healing-internals.md).
- **Self-healing Wound 2** — `RuntimeFailure` attached to the candidate mid-eval; L2 adjusts; L3 replans on persistence.
- **Warnings → optimizer context** — per-sample warnings surface to the optimizer through the round's `evidence_health` / `diagnostics` panels. They do **not** select samples: the cumulative warned-query subset that once fed probe-round selection is gone, along with the probe lever it served.
- **Warnings → escalation counter** — sustained degradation increments a patience counter.
- **Warnings → search-point attachment** — failures pin to the exact configuration that caused them, not the round.
- **Skip** — a candidate producing too many degraded or empty results is eliminated mid-run.
- **Abort** — a candidate can signal the round should stop.
- **Fatal fast-path** — fatal codes derived by `classify_result()` (`application/optimization/pobb/classification.py`) eliminate a candidate on the first query, with no rate threshold.

## How the prediction is read

The per-sample `predicted` value is the **head of the terminal ranker's output** — not a fixed key. `terminal_ranking(result, schema)` (`promptpotter/application/optimization/pobb/classification.py`) walks the schema **in reverse** for the last node with `node_role ∈ {ranker, candidate_source}` that wrote its `pipeline_key`, and `sample_measurement.py` takes the head of that list (shape-agnostic via `extract_item_label`). So:

- a pipeline ending at `token_matching` (a `candidate_source`) yields its `candidate_ranking`;
- a pipeline ending at `llm_ranking` / `llm_only` (a `ranker`) yields its `final_ranking`.

`final_ranking` is the *universal* answer key (the typed `PipelineData.final_ranking`), but the pipeline **shape** — which ranker terminates it — decides the source, not the key name. A pipeline with no terminal ranker emits no prediction (every sample scores `NO_RESULT`); init warns loudly when a resolved schema has no `ranker` / `candidate_source` node with an output key.

## Strict parsing — the contract is the contract

`parse_pipeline_response()` in `promptpotter/domain/pipeline_parsing.py` is the single ingress for every `pipeline.yaml`. **Two non-negotiables:**

1. **No silent-default forgiveness.** Either a field is required and the connector supplies it, or it is optional and PromptPotter ignores it absent. The "TermNorm doesn't supply X so PromptPotter assumes Y" pattern is what makes a second connector painful.
2. **Same parser, same shape, every time.** A backend's `pipeline.yaml` and PromptPotter's own `promptpotter/assets/optimizer/pipeline.yaml` MUST round-trip through `parse_pipeline_response()` identically. The parity test pins this — add a special-case field to one and the test fails until both agree.

## Example — TermNorm, sanitized

A full real-shape example lives at [`datasets/lca-termnorm/pipeline.yaml`](../../datasets/lca-termnorm/pipeline.yaml). A minimal one — the GSM8K single-LLM-node pipeline — at [`datasets/gsm8k/pipeline.yaml`](../../datasets/gsm8k/pipeline.yaml):

```json
{
  "name": "GSM8K",
  "version": "v0.1",
  "description": "...",
  "backend_name": "TermNorm",
  "backend_type": "termnorm",
  "available_models": ["openai/gpt-oss-20b", "openai/gpt-oss-120b"],
  "nodes": {
    "llm_only": {
      "type": "generation",
      "runtime": "backend",
      "node_role": "ranker",
      "description": "Direct LLM generation on the TermNorm backend.",
      "short_circuit": false,
      "config": {"model": "...", "temperature": 0.0, "reasoning_effort": "medium"},
      "prompt_info": {"family": "llm_only", "template_variables": ["query"], "description": "..."},
      "optimizer": {
        "param_keys": ["temperature", "max_tokens", "model", "..."],
        "param_allowed_values": {"reasoning_effort": ["none", "default", "low", "medium", "high"]},
        "observation_name": "llm_only",
        "observation_mappings": [{"pipeline_key": "final_ranking", "output_field": "generated_text", "is_llm": true}],
        "langfuse_type": "generation"
      }
    }
  },
  "pipelines": {"default": ["llm_only"]}
}
```

## Optimizer-manifest parity

PromptPotter's own optimizer prompt pipeline uses the **same shape** as a backend's: the same `nodes` dict keyed by node name (`l1_generate`, `l1_critique`, `l2_context`, `l3_plan`, `l1_score`, `checkin`), the same `config` + `optimizer` per-node sub-objects, the same `pipelines` dict over node names (it publishes `l1_round`, `l1_round_with_l1_critique`, `l2_escalation`, `l3_escalation`), and the same `resolved_prompts` + `resolved_schemas` registries, carried inline where a backend serves them via `GET /pipeline`.

So the same parser, scoring gateway, projection, tracing and observability pathway PromptPotter applies to a target pipeline applies to the optimizer itself — that is the foundation the PromptPotter-as-backend connector and the L4 self-optimization closure are built on ([`../specs/roadmap.md`](../specs/roadmap.md)).

The parity fails loud: if the optimizer manifest ever drifts from a backend pipeline's shape (parallel registries, ad-hoc keys, special-case fields), the shared parser rejects it at load. No standing test — see [`../../tests/CLAUDE.md`](../../tests/CLAUDE.md).

## Reference

| Resource | Covers |
|----------|--------|
| [![self-healing-internals](https://img.shields.io/badge/self--healing--internals-red?style=for-the-badge)](self-healing-internals.md) | LLM-to-LLM wounds — detection point × nurse owner |
| [![candidate-elimination](https://img.shields.io/badge/candidate--elimination-black?style=for-the-badge)](../methods/candidate-elimination.md) | Full elimination ladder — validation skip through PoBB cutoff |
| [![developer/README](https://img.shields.io/badge/developer%2FREADME-red?style=for-the-badge)](README.md) | Architecture brief — prompt structure, dispatch, scoring node, cross-run memory |
| [![observability](https://img.shields.io/badge/observability-black?style=for-the-badge)](../operations/observability.md) | Node tracing and Langfuse integration |
| `promptpotter/assets/optimizer/pipeline.yaml` | Live optimizer node declarations |
| `GET /pipeline` | Backend self-description — source of pipeline schema at runtime |
