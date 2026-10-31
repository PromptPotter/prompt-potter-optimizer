# Node standard + the `pipeline.yaml` contract

The optimizer loop (`l1_generate`, `l1_critique`, `l2_context`, `l3_plan`) and every backend pipeline step are nodes — **one `pipeline.yaml` declaration shape, one parser** — so warnings, self-healing and patience counters read a backend and the optimizer alike, and the cache reuses a walk up to the node that changed.

This page is the node model and the **strict wire shape** PromptPotter parses from `GET /pipeline` (or a local `{dataset}/dataset.yaml::pipeline`). Every connector publishes it, and the same parser consumes every optimizer manifest under `promptpotter/assets/optimizers/` unchanged — the PromptPotter-as-backend connector and L4 self-optimization stand on that parity. Wiring a new node into self-healing: [`../operations/backend-integration.md`](../operations/backend-integration.md) § Self-healing a node.

## Pipeline declaration format

Both backends and every optimizer declare pipelines in this shape (shown as JSON, the wire form; authored as YAML). An optimizer's lives in its registered manifest directory (`optimizers/__init__.py::ManifestDir`; a built-in's is `promptpotter/assets/optimizers/{name}/pipeline.yaml`); a backend's is served by `GET /pipeline`.

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

**PromptPotter parses a SUBSET of this file, by design.** `PipelineNode` is built from `type`,
`node_role`, `config` and the `optimizer` sub-object; `description`, `runtime`, `short_circuit` and
`input_schema` are the **backend's self-description** for a human reader (`description` rides the
served `view` as the node's explainer). A key PP does not use gets no model field but still belongs
in the file — "required" on a connector's side means *a connector must publish it*.

The decisions the models cannot state:

- **`type` is a closed vocabulary in three families** (`domain/pipeline_schema.py::NodeKind`; an
  unlisted spelling is refused at parse). A THINKING node runs a model, so a model, a reasoning
  rung and a temperature are its own, and the family splits on what the model DOES — `llm` answers
  in one call, `agent` works in a tool loop — never on who runs it. A retrieval or plumbing node
  moves data. A GATEWAY node (`measurement`) runs another PIPELINE: every tunable it appears to
  have belongs to the pipeline it hands off to, so it carries no config of its own.
- **`backend_type` is required and is never a `PipelineSchema` field.** The parser drops it, so
  readers take it off the raw overlay. It picks the connector at init (`dataset_access.py::declared_backend_type`
  raises when absent). Whether it names the recursion connector — the ONE
  test for a self-optimizing (L4) campaign — is `runner/inner/tasks.py::is_self_optimization`,
  served as the hierarchy entry's `self_optimization` tag so the webapp branches on a tag and never
  on the name.
- **`pipelines` must contain `default`** — the active step order unless a campaign overrides it.
  **The other names decide whether a node is drawn at all** (`derive_pipeline_view`; no manifest
  declares a `view`): a sequence sharing steps with `default` is an ALTERNATIVE a controller picks
  at the round boundary, its new nodes tiered above the chain by an `alternative` edge; one sharing
  none is a separate PHASE, drawn ahead of an optimizer's `loop`; **a node named by no pipeline is
  not in the flow, so nothing draws it** — which is why the check-in node is a one-step pipeline.
- **The CONFIG surface follows the declaration, not the run.** `PipelineSchema.declared_nodes` covers
  every node under `nodes:`; a node absent from it has no row and no lock. Identity follows only
  where a point CONFIGURES an off-chain node (`node_configs` → `sp_hash`); merely declaring a step
  re-keys no banked cell.
- **`runtime` is orthogonal to `Connector.execution`.** It says where a node runs inside the
  *backend's* topology (`backend` / `frontend` / `in_process`); `Connector.execution` says how
  PromptPotter reaches the backend, so one `remote_http` connector may mix all three.
- **`output_schema` is not a node-level key.** An inline one is declared at `config.output_schema`,
  the same place the connector forwards it from, so there is one schema rather than a display copy
  beside a wire copy. It is locked against the optimizer (`SCHEMA_OWNED_FIELDS`).
- **`response_format` is PromptPotter's axis, and a connector must not declare it.** Whether the
  request carries a schema is decided here — PromptPotter composes the wire config — so the toggle
  (`SCHEMA_TOGGLE_PARAM`) is synthesized at parse time onto every node `PipelineNode.tunes_llm`
  names — a thinking `type` whose `optimizer.param_keys` opens an axis — and resolved at the wire
  seam: `json` sends `output_schema` + `answer_field`, `text` sends NEITHER. A node declaring the
  key in its own `param_keys` opens an axis its `output_schema` silently outranks — every arm makes
  the identical call. What a connector owes instead is the READING: **a schema
  on the wire means structured output, its absence means prose**, and `json` arriving with no
  schema is a caller error to raise on, never one to guess a key out of.
- **`param_allowed_values` drives three things at once** — L1's prompt guidance, the JSON-schema
  enum constraint on structured-output generation, and post-hoc `ValidationFailure` attachment in
  `overlay_failures`.

## Node capabilities

Capabilities are opt-in. A deterministic node declares none.

### All nodes

- **Exit point** — a node producing candidates writes its `pipeline_key`; the cells it answers are filed under the node-chain prefix that produced them, so a walk reuses every step before the one that changed.
- **Stop rules** — a `StopRule` returns a `StopSignal` whose `WalkStop` is `broken`, `eliminated` or `locked_in` (`domain/validators.py`); a verdict flows by return value, never an exception. The bench's `application/scoring/classification.py::DegradationCheck` stops a walk `broken` on a deprecated-sample rate or, on the fatal fast-path, on the first row `classify_result()` (`domain/results_health.py`) marks fatal.
- **Warnings** — per-sample warnings pin to the configuration that produced them and reach the optimizer through the round's `evidence_health` / `diagnostics` panels; they never select samples.

### LLM nodes additionally

- **Prompt exposure** — `prompt_info.fields` (`domain/pipeline_schema.py::NodePromptInfo`, required) names the target prompt's parts in render order, and that order sits inside the archive key — read it at the declaration, never from a copy. The optimizer's own nodes render an `OptimizerPromptTemplate` instead.
- **Shots are part of the target prompt, carried by key and rendered last.** A node's `shots` name demo-pool rows (`DatasetSplit.demo`) by `Sample.key`; `OptSearchPoint.scored_prompts(schema, framing, demo=)` resolves them and appends the block after the fields, so the prompt the archive hashes is one function of fields, keys and pool. The individual never carries a row's text; the export carries the resolved block because its reader has no pool. A demo row is never scored and never reaches a panel or the bench set. Editors: `l1_generate`'s `shots` slot, bounded by its `k_max` and checked by `validators/l1_strict.py::l1_shots_in_demo_pool`; an `algorithm` member without a model call (`optimizers/capo/members.py::FewShot`, `cross_shots`).
- **Optimizer-discoverable parameters** — declared accepted parameters and values become search axes, hardcoded on neither side.
- **Self-healing Wounds 1 and 2** — a `ValidationFailure` at L1 parse time, a `RuntimeFailure` attached mid-run. **Who heals each** — owned by [`self-healing-internals.md`](self-healing-internals.md) § The wounds, mapped to the two axes.

### Optimizer node types

An optimizer manifest uses `llm` and `measurement` nodes plus five types no backend declares,
serving the contract in [`../architecture.md`](../architecture.md) § Bench and optimizer. **Each
of the five — and every `llm` node the bench walks — is backed by an implementation registered
under the node's NAME** through the one entry-point registry (`promptpotter.optimizer_nodes`), so
`paired_t:` in a manifest resolves to the `paired_t` member; the node's `config` is that member's
typed knobs — every field required, so the manifest is the one place a value comes from — and a
paper's configuration is a set of those values. No member sees the bench set, a `Cycle`, a store
or a live client: each receives the `NodeContext` the bench builds for its node
(`application/bench/node_context.py` — read its accessors there), and **a node shared between
manifests reads that context alone** — the readings it declares it `sees`, the state slices it
`reads` / `writes`, the members it `consults`. A roster where any of those is not closed by exactly
one member is refused at load, and so is a read the member cannot hold (`CycleRead.refusal`). A
child is made through `Children.child` / `Children.edit`, so its lineage
names every node that wrote it and the loci each moved. What an optimizer tells the harness
itself — its arms, prompt sources, replayers, limits, facts — is a declaration of the member that
owns it, defaulted on its role's Protocol (`nodes.py::NodeMember`, `Proposer`, `Sampler` …), and a
backbone fact no member answers, or two do, is refused at load.

| Type | Reads | Returns | Binds it |
|---|---|---|---|
| `llm` | its prompt, filled through the dispatch hub | its parsed response — for a proposing node, individuals, each written through `Children.child` (a later node's change through `Children.edit`), the one writer of every locus, so its lineage carries its `parent_ids` and every node that wrote it | every child is admitted by the bench as it is written, once (`promptpotter/application/bench/children.py::admission_failures`): forbidden keys, `overlay_failures`, the node's permitted model set, the backend's mandatory placeholders |
| `measurement` | the round's candidates, the sampler's panel, the eliminator's checks | the round's rows | the bench's scoring gateway, walked unchanged; `config` stays empty |
| `sampler` | the search split, the bench's per-sample difficulty, prior rows | the round's panel: ordered sample ids, cut into the blocks an eliminator decides between | draws from the search split alone; deterministic given its inputs and seed, so resume and fork replay it |
| `eliminator` | the panel, the candidates, rows as they land | a continue or cut per arm per block, each cut a ledger decision stamped with this node | cuts on evidence about the arm, never on a technical failure — that is the bench's `DegradationCheck`, which runs whatever the eliminator; the `none` member walks every arm to the end |
| `selector` | the round's rows, lineage, the population on its context (`ctx.read(Lineage).population`) | a `Selection` — `selected`, one arm's key or `None` to hold the round, and `leading` — and the population it keeps (`population`) | its choice is what the optimizer keeps, never a score the bench serves |
| `algorithm` | individuals, and the demo pool when it edits shots | individuals it wrote through `Children.child` / `Children.edit`, no model call and no measurement | deterministic given its inputs and seed |
| `controller` | the round's envelope and the optimizer's own state | which of the manifest's other `pipelines:` entries runs at the round boundary | whether the run STOPS is the bench's, for every optimizer (`runner/termination.py::standing_tripped`: lives, a perfect objective, convergence); the bench walks `default` alone; the entries a controller picks are the optimizer's, and a manifest without one runs `default` every round |

An `llm` node's role is its position: before the measurement it PROPOSES, after the selector it
ADAPTS (potter's critique). The round's phases follow the walk — PROPOSE, MEASURE, SELECT, ADAPT.
An `algorithm` node before the measurement proposes too, and so do the nodes of a manifest's
`initial_population` pipeline, walked in a round the run enters with no population: in place of
`default` (`initial_population_walks: alone`) or ahead of it, into the population `default` reads
(`before_default`). **An operator's arms are written through the bench's own seat**
(`nodes.py::OPERATOR_SEAT`, `runner/arm_gate.py`), in no manifest; it may write as many shots as
the round's proposers may draw (`SelectedOptimizer.shot_cap`). **A switch, never a copy**:
recombine members by a manifest declaring `extends: <manifest>` and
`swap: {<node>: {node: <name>, of: <manifest>}}`; it is the one it extends with each swapped node
replaced, in every walk, by `of`'s node of the same type at `of`'s configuration (`capo-pobb`). An eliminator's `Race` stops a walk on its own rows (`rule`) or every live walk
together at each block's close (`blocks`); its catch-ups are the calls pairing a prior with the arm
on turn.

**`default` lists its nodes in round order, and the bench places them by role at load**
(`optimizer_manifest.py::_round_plan`) — the roles are `RoundPlan`'s fields, in field order, each
declaring the types it admits and how many it holds. A node takes the first role at or past the
previous node's that admits its type, so **an `eliminator` is listed before the `measurement` it
races**, and a walk out of that order is refused before a mint. Exactly one `measurement`: with
none the round has no rows, with two nothing says which set a selector reads. At most one
`controller`: a round has one boundary decision.

**One structural rule rejects a manifest at parse** — in `parse_pipeline_response`, the same parser
a backend's file goes through; a backend pipeline declares none of the five types and passes:
**an `eliminator` needs a `sampler` before it in the same pipeline**, since a cut decides between
blocks and only a sampler cuts the panel into blocks.

## How the prediction is read

The per-sample `predicted` value is the **head of the terminal ranker's output** — not a fixed key. `terminal_ranking(result, schema)` (`promptpotter/application/scoring/classification.py`) walks the schema **in reverse** for the last node with `node_role ∈ {ranker, candidate_source}` that wrote its `pipeline_key`, and `sample_measurement.py` takes the head of that list (shape-agnostic via `extract_item_label`). So:

- a pipeline ending at `token_matching` (a `candidate_source`) yields its `candidate_ranking`;
- a pipeline ending at `llm_ranking` / `llm_only` (a `ranker`) yields its `final_ranking`.

The pipeline **shape** — which ranker terminates it — decides the source, never the key name. A pipeline with no terminal ranker emits no prediction (every sample scores `NO_RESULT`), and init warns.

## Strict parsing — the contract is the contract

`parse_pipeline_response()` in `promptpotter/domain/pipeline_parsing.py` is the single ingress for every `pipeline.yaml`. **Two non-negotiables:**

1. **No silent-default forgiveness.** Either a field is required and the connector supplies it, or it is optional and PromptPotter ignores it absent. The "TermNorm doesn't supply X so PromptPotter assumes Y" pattern is what makes a second connector painful.
2. **Same parser, same shape, every time.** A backend's `pipeline.yaml` and every optimizer manifest under `promptpotter/assets/optimizers/` MUST round-trip through `parse_pipeline_response()` identically. No test pins this ([`../../tests/CLAUDE.md`](../../tests/CLAUDE.md)); the shared parser does — a manifest that drifts (parallel registries, ad-hoc keys, special-case fields) is rejected at load.

## Worked examples

Read the real files rather than a copy: the `pipeline` section of [`defaults.yaml`](../../promptpotter/assets/datasets/defaults.yaml)
is the single-LLM-node case whole, which every shipped dataset's `pipeline` section is read laid
over before it reaches the parser ([`promptpotter/assets/datasets/CLAUDE.md`](../../promptpotter/assets/datasets/CLAUDE.md)
§ A dataset file is a partial), and [`justlogic/family.yaml`](../../promptpotter/assets/datasets/justlogic/family.yaml)
is one family's delta on it.
