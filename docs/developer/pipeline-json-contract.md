# `pipeline.json` contract

The wire shape PromptPotter parses from `GET /pipeline` (or from a
local file under `datasets/{name}/pipeline.json`). Every connector
publishes this shape; the **same parser** consumes
`promptpotter/application/optimization/optimizer_pipeline.json`
unchanged. If you're writing a new connector or extending the
optimizer manifest, this is the contract you implement against.

Concept-level intro for the node model lives in
[`node-standard.md`](node-standard.md); this doc is the strict
field-level shape, pinned by
[`tests/test_optimizer_pipeline_parity.py`](../../tests/test_optimizer_pipeline_parity.py).

## Top-level shape

| Field | Required | Type | Notes |
|---|---|---|---|
| `name` | required | `str` | Connector / pipeline display name. Lowercased into `PipelineSchema.name`. |
| `nodes` | required | `dict[str, NodeDecl]` | Map of node-name → node declaration. See **Node declaration**. |
| `pipelines` | required | `dict[str, list[str]]` | Named sequences over `nodes` keys. Must contain `default` — that's the active step order PromptPotter uses unless a campaign overrides it. |
| `version` | optional | `str` | Connector schema version; rendered into `PipelineSchema.version`. |
| `description` | optional | `str` | One-line human description. |
| `available_models` | optional | `list[str]` | Models the connector exposes — surfaced into `PipelineSchema.available_models`. |
| `backend_name` / `backend_type` | optional | `str` | Connector identity metadata. Read by dataset registration; not parsed into the schema today. |
| `llm_defaults` | optional | `dict` | Default LLM provider/model bundle. Read by bootstrap, not by the parser. |
| `resolved_prompts` | optional | `dict[str, ResolvedPrompt]` | Prompt registry keyed by `"{family}/{version}"`. Each node references its prompt via `config.prompt_family` + `config.prompt_version`. |
| `resolved_schemas` | optional | `dict[str, ResolvedSchema]` | Output-schema registry keyed by `"{family}/{version}"`. Each node references its schema via `config.schema_family` + `config.schema_version`. |
| `view` | optional | `dict` | Diagram metadata for the webapp; ignored by the parser. |

## Node declaration (`nodes[name]`)

| Field | Required | Type | Notes |
|---|---|---|---|
| `type` | required | `str` | Wire type — one of `llm`, `llm/structured`, `llm/meta`, `agent`, `deterministic`, `measurement`, `web_search`. Mapped to `PipelineNode.wire_type`. |
| `node_role` | required | `str` | One of `""`, `candidate_source`, `ranker`, `enricher`, `cache`. Mapped to `PipelineNode.node_type` (the typed `NodeType` enum). |
| `description` | required | `str` | One-line node description. |
| `runtime` | required | `str` | One of `backend`, `optimizer`. Distinguishes connector-served nodes from optimizer-internal LLM calls. |
| `short_circuit` | required | `bool` | Whether a successful match in this node bypasses downstream nodes. |
| `config` | optional | `dict` | Node-local defaults. For LLM nodes typically `{model, temperature, max_tokens, ...}`. For optimizer nodes also `{prompt_family, prompt_version, schema_family, schema_version}` keys that index into the registries. |
| `prompt_meta` | optional | `dict` | Inline `{family, template_variables, description}`. Used when no `resolved_prompts` registry is present (rare). |
| `output_schema` | optional | `dict` | Inline output schema. Same role as a `resolved_schemas` entry. |
| `input_schema` | optional | `dict` | Reserved for future input-validation work. |
| `optimizer` | optional | `dict` | See **`optimizer` sub-object**. Required for any node PromptPotter is allowed to mutate or trace. |

## `optimizer` sub-object

Pinned per-node so PromptPotter knows what's mutable and how trace
data maps back into pipeline state.

| Field | Required | Type | Notes |
|---|---|---|---|
| `param_keys` | required | `list[str]` | Wire-name params PromptPotter is allowed to mutate. Drives L1's mutation surface and the JSON-schema enum constraints fed to the LLM. |
| `observation_mappings` | required | `list[ObservationMapping]` | One entry per pipeline-data field this node writes. Each is `{pipeline_key: str, output_field: str | null, is_llm: bool}`. |
| `langfuse_type` | required | `str` | Trace-span kind — one of `generation`, `tool`, `retriever`, `span`. |
| `observation_name` | optional | `str` | Trace-name override. Defaults to the node name. |
| `display_tag` | optional | `str` | Short name for dashboard / webapp display. |
| `param_descriptions` | optional | `dict[str, str]` | One-line description per param key. Surfaced into L1's prompt as the param catalogue. |
| `param_allowed_values` | optional | `dict[str, list[str]]` | Enum constraint per param. Drives both L1 prompt guidance and the JSON-schema enum constraint on structured-output generation, plus post-hoc `ValidationFailure` attachment in `validate_overrides`. |

## Strict parsing — the contract is the contract

`parse_pipeline_response()` in `promptpotter/domain/pipeline_parsing.py`
is the single ingress for every pipeline.json. **Two non-negotiables:**

1. **No silent-default forgiveness.** Either a field is required and
   the connector supplies it, or it's optional and PromptPotter
   ignores it absent. The "TermNorm doesn't supply X so PromptPotter
   assumes Y" pattern is what makes a second connector painful.
2. **Same parser, same shape, every time.** A backend's pipeline.json
   and PromptPotter's own `optimizer_pipeline.json` MUST round-trip
   through `parse_pipeline_response()` identically. The parity test
   pins this — if you add a special-case field to one, the test
   fails until both files agree.

## Example — TermNorm, sanitized

A full real-shape example lives at
[`datasets/lca-termnorm/pipeline.json`](../../datasets/lca-termnorm/pipeline.json).
A minimal one — the GSM8K single-LLM-node pipeline — at
[`datasets/gsm8k/pipeline.json`](../../datasets/gsm8k/pipeline.json):

```json
{
  "name": "GSM8K",
  "version": "v0.1",
  "description": "...",
  "backend_name": "TermNorm",
  "backend_type": "termnorm",
  "available_models": ["openai/gpt-oss-120b"],
  "nodes": {
    "llm_only": {
      "type": "generation",
      "runtime": "backend",
      "node_role": "ranker",
      "description": "Direct LLM generation on the TermNorm backend.",
      "short_circuit": false,
      "config": {"model": "...", "temperature": 0.0, "reasoning_effort": "medium"},
      "prompt_meta": {"family": "llm_only", "template_variables": ["query"], "description": "..."},
      "optimizer": {
        "param_keys": ["temperature", "max_tokens", "model", "..."],
        "param_allowed_values": {"reasoning_effort": ["none", "default", "low", "medium", "high"]},
        "observation_name": "llm_only",
        "observation_mappings": [{"pipeline_key": "final_ranking", "output_field": "generated_text", "is_llm": true}],
        "langfuse_type": "generation"
      }
    }
  },
  "pipelines": {"default": ["llm_only"]},
  "llm_defaults": {"provider": "groq", "model": "openai/gpt-oss-120b"}
}
```

## `optimizer_pipeline.json` parity

PromptPotter's own meta-prompt pipeline at
`promptpotter/application/optimization/optimizer_pipeline.json` uses
the **same shape** as a backend's `pipeline.json`:

- Same `nodes` dict, keyed by node name (`l1_generate`, `l1_critique`,
  `l2_context`, `l3_plan`, `l1_score`, `restructure`).
- Same `config` + `optimizer` per-node sub-objects.
- Same `pipelines` dict over node names (the optimizer publishes
  `l1_round`, `l1_round_with_l1_critique`, `l2_escalation`,
  `l3_escalation`).
- Same `resolved_prompts` + `resolved_schemas` registries (carried
  inline; backends serve them via `GET /pipeline`).

This means the same parser, the same scoring gateway, the same
projection / tracing / observability pathway PromptPotter applies to
a target pipeline applies to the optimizer itself — that's the
foundation for the M11 PromptPotter-as-backend connector and the
M12 L4 self-optimization closure (see
[`docs/specs/m11-publication-benchmarks.md`](../specs/m11-publication-benchmarks.md)
+ [`docs/specs/m12-multi-connector.md`](../specs/m12-multi-connector.md)).

The parity is enforced by
[`tests/test_optimizer_pipeline_parity.py`](../../tests/test_optimizer_pipeline_parity.py)
— if `optimizer_pipeline.json` ever drifts from a backend
pipeline's shape (parallel registries, ad-hoc keys, special-case
fields), the test fails.
