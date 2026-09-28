# Node standard + the `pipeline.yaml` contract

The optimizer loop (`l1_generate`, `l1_critique`, `l2_context`, `l3_plan`) and every backend pipeline step are built from nodes — **same JSON declaration format, same registry.** That symmetry is what lets the optimizer self-inspect: search memory tracks warnings from both sides, self-healing applies to both, and the patience counters watching a backend degrade and an optimizer stall are the same shape.

Three reasons nodes-not-monoliths, mirroring prompt decomposition: measurable axes per node · independent mutation (cache reuses up to the changed node) · extensibility without coupling (anyone can write a node — a JSON declaration registers it).

Built-in nodes cover fixed-config deterministic steps (lookup, fuzzy matching), LLM nodes, and multi-step agent nodes. PromptPotter ships a basic database-backed candidate-assignment pipe. In practice most pipelines reduce to one or more LLM nodes.

This page is both the node model and the **strict wire shape** PromptPotter parses from `GET /pipeline` (or from a local `datasets/{name}/pipeline.yaml`). Every connector publishes this shape, and the **same parser** consumes `promptpotter/assets/optimizers/potter/pipeline.yaml` unchanged. Writing a connector or extending the optimizer manifest — this is the contract you implement against.

The silent-harm part is tested — content-hash sensitivity, by [`tests/test_integrity.py`](../../tests/test_integrity.py) (`test_content_hash_distinguishes_pipeline_params`). Flat-format rejection is a `JobSearchPoint` model validator, so Pydantic raises on a malformed authored config; that and the rest fail loud, so no standing test — see [`../../tests/CLAUDE.md`](../../tests/CLAUDE.md). Operator walk-through for wiring a new node into self-healing: [`../operations/backend-integration.md`](../operations/backend-integration.md) § Self-healing a node.

## Pipeline declaration format

Both backends and every optimizer declare pipelines as JSON. An optimizer's lives in its runtime's `manifest_dir` (a built-in's is `promptpotter/assets/optimizers/{name}/pipeline.yaml`); a backend's is served by `GET /pipeline`.

```json
{
  "name": "Pipeline Name",
  "version": "v1.0",
  "backend_type": "termnorm",
  "nodes": {
    "node_name": {
      "type": "llm",
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

The `pipelines` dict composes named sequences from the node pool; the same node can appear in several. Prompts and structured-output schemas are referenced by `(family, version)` from each node's `config` and resolved against the top-level `resolved_prompts` / `resolved_schemas` registries — the same shape `parse_pipeline_response` (`domain/pipeline_parsing.py`) consumes for backends. An optimizer manifest carries `resolved_prompts` inline and takes `resolved_schemas` from its generated sibling `resolved_schemas.json` (`scripts/build_optimizer_schemas.py`), merged at load; a backend serves both via `GET /pipeline`.

## The shape, and what PromptPotter reads of it

The parser is `domain/pipeline_parsing.py::parse_pipeline_response`, building `PipelineSchema` /
`PipelineNode` / `ObservationMapping` (`domain/pipeline_schema.py`). **Read the required set, the
types and the defaults off those models** — a table here is a second declaration that drifts.

**PromptPotter parses a SUBSET of this file, and that is by design, not rot — do not re-file the
remainder as dead keys.** `PipelineNode` is built from `type`, `node_role`, `config` and the
`optimizer` sub-object, nothing else; `description`, `runtime`, `short_circuit` and `input_schema`
are the **backend's self-description**, stating its own topology for a human reader — which is why
`description` rides the served `view` as the node's explainer, and no surface keeps a second copy
keyed by node id. The mirror
rule: a key PP does not *use* gets no model field, but the key still belongs in the file — and
"required" on a connector's side means *a connector must publish it*, not *PP reads it*.

The decisions the models cannot state:

- **`backend_type` is required and is never a `PipelineSchema` field.** The parser drops it, so
  readers take it off the raw overlay. It picks the connector at init (`wiring._read_backend_type`
  raises when absent) and is served on `CampaignSummary.backend_type` — the ONE test for a
  self-optimizing (L4) campaign, which the webapp branches on (`isSelfOptimization`).
- **`pipelines` must contain `default`** — the active step order unless a campaign overrides it,
  and the same node may appear in several sequences. **The other names are read too, and this is
  what decides whether a node is drawn at all:** a sequence sharing steps with `default` is an
  ALTERNATIVE — a controller picks it at the round boundary — and the nodes it introduces tier
  above the chain in the served `view`, reached by an `alternative` edge; one sharing
  none is a separate PHASE, running on the occasion the member that opens it chooses, drawn ahead
  of an optimizer's `loop` rather than inside it; and **a node named by no pipeline is not
  in the flow, so nothing draws it** — being declared is not the same as running, which is why the
  optimizer publishes its check-in node as a one-step pipeline of its own. `derive_pipeline_view`
  reads exactly this, and no manifest declares a `view` of its own.
- **Declaring a node and running it are separate, and the CONFIG surface follows the declaration.**
  `PipelineSchema.config_nodes` covers every node under `nodes:`, whether or not a pipeline names
  it — a node absent from the surface is not a locked node the operator can open, it is nothing at
  all, with no row and no lock. Identity follows the declaration too, but only where a point
  CONFIGURES an off-chain node (`node_configs` → `sp_hash`) — a node only an alternative reaches
  still changes the measurement, while merely declaring a step re-keys no banked cell.
- **`runtime` is orthogonal to `Connector.execution`.** It says where a node runs inside the
  *backend's* topology (`backend` / `frontend` / `in_process`); `Connector.execution` says how
  PromptPotter reaches the backend. One `remote_http` connector legitimately mixes all three —
  `lca-termnorm` runs `cache_lookup` / `fuzzy_matching` client-side, and `short_circuit` on those
  two means a hit answers without reaching the backend at all.
- **`output_schema` is not a node-level key.** An inline one is declared at `config.output_schema`,
  the same place the connector forwards it from, so there is one schema rather than a display copy
  beside a wire copy. It is locked against the optimizer (`SCHEMA_OWNED_FIELDS`).
- **`response_format` is PromptPotter's axis, and a connector must not declare it.** Whether the
  request carries a schema is decided here — PromptPotter composes the wire config — so the toggle
  (`SCHEMA_TOGGLE_PARAM`) is synthesized at parse time onto every node `PipelineNode.tunes_llm`
  names — a thinking `type` whose `optimizer.param_keys` opens an axis — and resolved at the wire
  seam: `json` sends `output_schema` + `answer_field`, `text` sends NEITHER. A node declaring the
  key in its own `param_keys` makes two mechanisms for one thing, which is how TermNorm came to
  offer an axis its `output_schema` silently outranked — every arm produced the identical call and
  the round scored the difference anyway. What a connector owes instead is the READING: **a schema
  on the wire means structured output, its absence means prose**, and `json` arriving with no
  schema is a caller error to raise on, never one to guess a key out of.
- **`param_allowed_values` drives three things at once** — L1's prompt guidance, the JSON-schema
  enum constraint on structured-output generation, and post-hoc `ValidationFailure` attachment in
  `validate_overrides`.

## Node capabilities

Capabilities are opt-in. A deterministic node declares none; an LLM node in the optimizer loop may use all.

### All nodes

- **Exit-point declaration** — a node producing candidates declares where its output lives. Enables step-sequence cache reuse and partial run replay.
- **Stop signals** — return a `StopSignal` naming the arm's `ArmOutcome` to stop a candidate, rather than failing silently.

### LLM nodes additionally

- **Prompt exposure** — expose the prompt as a `PromptTemplate`. PromptPotter reads, displays, and optimises it. See [`README.md`](README.md) § 1. Prompt structure.
- **Optimizer-discoverable parameters** — declare accepted parameters and valid values. PromptPotter picks these up automatically as optimisation axes, with no hardcoding on either side.
- **Self-healing Wounds 1 and 2** — a `ValidationFailure` caught at L1 parse time, a `RuntimeFailure` attached to the candidate mid-run. **Who heals each** — owned by [`self-healing-internals.md`](self-healing-internals.md) § The wounds, mapped to the two axes.
- **Warnings → optimizer context** — per-sample warnings surface to the optimizer through the round's `evidence_health` / `diagnostics` panels. They do **not** select samples: the cumulative warned-query subset that once fed probe-round selection is gone, along with the probe lever it served.
- **Warnings → escalation counter** — sustained degradation increments a patience counter.
- **Warnings → search-point attachment** — failures pin to the exact configuration that caused them, not the round.
- **Skip** — a candidate producing too many degraded or empty results is eliminated mid-run.
- **Abort** — a candidate can signal the round should stop.
- **Fatal fast-path** — fatal codes derived by `classify_result()` (`domain/results_health.py`) eliminate a candidate on the first query, with no rate threshold.

### Optimizer node types

An optimizer manifest uses `llm` and `measurement` nodes plus five types no backend declares,
serving the contract in [`../architecture.md`](../architecture.md) § Bench and optimizer. **Each
of the five — and every `llm` node the bench walks — is backed by an implementation registered
under the node's NAME** through the one entry-point registry (`promptpotter.optimizer_nodes`), so
`paired_t:` in a manifest resolves to the `paired_t` member; the node's `config` is that member's
typed knobs, and a paper's configuration is a set of those values. No member sees the bench set.
The target is that none is handed `Cycle`, a store or a live client either — only frozen `domain/`
inputs; potter's members still read the bench's `Cycle`, their own state riding apart on
`RoundContext.state`.

| Type | Reads | Returns | Binds it |
|---|---|---|---|
| `llm` | its prompt, filled through the dispatch hub | its parsed response — for a proposing node, individuals, each with its `parent_ids` and `source` | proposals are validated like any candidate: forbidden keys, `validate_overrides`, the node's permitted model set |
| `measurement` | the round's candidates, the sampler's panel, the eliminator's checks | the round's rows | the bench's scoring gateway, walked unchanged; `config` stays empty |
| `sampler` | the search pool, the bench's per-sample difficulty, prior rows | the round's panel: ordered sample ids, cut into the blocks an eliminator decides between | draws from the search pool alone; deterministic given its inputs and seed, so resume and fork replay it |
| `eliminator` | the panel, the candidates, rows as they land | a continue or cut per arm per block, each cut a ledger decision stamped with this node | cuts on evidence about the arm, never on a technical failure — that is the bench's `DegradationCheck`, which runs whatever the eliminator; the `none` member walks every arm to the end |
| `selector` | the round's rows, lineage, the population or archive in `optimizer_state` | `selected: list[label]` and the next `optimizer_state` | its choice is what the optimizer keeps, never a score the bench serves |
| `algorithm` | individuals, and the demo pool when it edits shots | new individuals with `parent_ids`, no model call and no measurement | deterministic given its inputs and seed |
| `controller` | the round's envelope and the optimizer's own state | stop or continue, and which of the manifest's other `pipelines:` entries runs at the round boundary | the bench walks `default` alone; the entries a controller picks are the optimizer's, and a manifest without one runs `default` every round |

An `llm` node's role is its position: before the measurement it PROPOSES, after the selector it
ADAPTS (potter's critique). The round's phases follow the walk — PROPOSE, MEASURE, SELECT, ADAPT.

**Three rules reject a manifest at parse** — in `parse_pipeline_response`, the same parser a
backend's file goes through, so a special case cannot reach one side only:

- **A manifest declaring any `sampler`, `eliminator`, `selector`, `algorithm` or `controller`
  node must name exactly one `measurement` node in its `default` pipeline.** With none the round
  has no rows; with two it has two sets and nothing says which one a selector reads.
- **An `eliminator` needs a `sampler` before it in the same pipeline.** A cut decides between
  blocks, and only a sampler cuts the panel into blocks.
- **`default` names at most one `controller`.** A round has one boundary decision.

All three are structural — the parser asks what the file declares, never how it was loaded — so a
backend pipeline, which declares none of the five types, passes them untouched.

## How the prediction is read

The per-sample `predicted` value is the **head of the terminal ranker's output** — not a fixed key. `terminal_ranking(result, schema)` (`promptpotter/application/scoring/classification.py`) walks the schema **in reverse** for the last node with `node_role ∈ {ranker, candidate_source}` that wrote its `pipeline_key`, and `sample_measurement.py` takes the head of that list (shape-agnostic via `extract_item_label`). So:

- a pipeline ending at `token_matching` (a `candidate_source`) yields its `candidate_ranking`;
- a pipeline ending at `llm_ranking` / `llm_only` (a `ranker`) yields its `final_ranking`.

`final_ranking` is the *universal* answer key (the typed `PipelineData.final_ranking`), but the pipeline **shape** — which ranker terminates it — decides the source, not the key name. A pipeline with no terminal ranker emits no prediction (every sample scores `NO_RESULT`); init warns loudly when a resolved schema has no `ranker` / `candidate_source` node with an output key.

## Strict parsing — the contract is the contract

`parse_pipeline_response()` in `promptpotter/domain/pipeline_parsing.py` is the single ingress for every `pipeline.yaml`. **Two non-negotiables:**

1. **No silent-default forgiveness.** Either a field is required and the connector supplies it, or it is optional and PromptPotter ignores it absent. The "TermNorm doesn't supply X so PromptPotter assumes Y" pattern is what makes a second connector painful.
2. **Same parser, same shape, every time.** A backend's `pipeline.yaml` and every optimizer manifest under `promptpotter/assets/optimizers/` MUST round-trip through `parse_pipeline_response()` identically. No test pins this; the shared parser does — add a special-case field to one and it is rejected at load (§ Optimizer-manifest parity).

## Worked examples

Read the real files rather than a copy: [`datasets/gsm8k/pipeline.yaml`](../../datasets/gsm8k/pipeline.yaml)
is the minimal single-LLM-node case, [`datasets/lca-termnorm/pipeline.yaml`](../../datasets/lca-termnorm/pipeline.yaml)
the full multi-node shape.

## Optimizer-manifest parity

PromptPotter's own optimizer prompt pipeline uses the **same shape** as a backend's: the same `nodes` dict keyed by node name, the same `config` + `optimizer` per-node sub-objects, the same `pipelines` dict over those names, and the same `resolved_prompts` + `resolved_schemas` registries — prompts inline, schemas from the generated `resolved_schemas.json` merged at load — where a backend serves them via `GET /pipeline`. It publishes its controller's alternative sequences beside `default`, which no backend needs; the shape is identical either way.

So the same parser, scoring gateway, projection, tracing and observability pathway PromptPotter applies to a target pipeline applies to the optimizer itself — that is the foundation the PromptPotter-as-backend connector and the L4 self-optimization closure are built on ([`../specs/roadmap.md`](../specs/roadmap.md)).

The parity fails loud: if the optimizer manifest ever drifts from a backend pipeline's shape (parallel registries, ad-hoc keys, special-case fields), the shared parser rejects it at load. No standing test — see [`../../tests/CLAUDE.md`](../../tests/CLAUDE.md).
