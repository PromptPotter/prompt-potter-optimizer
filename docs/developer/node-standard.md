# Node standard + the `pipeline.yaml` contract

The optimizer loop (`l1_generate`, `l1_critique`, `l2_context`, `l3_plan`) and every backend pipeline step are nodes — **one `pipeline.yaml` declaration shape, one parser** (`domain/pipeline_parsing.py::parse_pipeline_response`) — so warnings, self-healing and patience counters read a backend and the optimizer alike, and the cache reuses a walk up to the node that changed. A backend serves it from `GET /pipeline` (or a local `{dataset}/dataset.yaml::pipeline`); an optimizer's lives in its registered manifest directory (`optimizers/__init__.py::ManifestDir`; a built-in's is `promptpotter/assets/optimizers/{name}/pipeline.yaml`). Wiring a new node into self-healing: [`../operations/backend-integration.md`](../operations/backend-integration.md) § Self-healing a node.

## Pipeline declaration format

Shown as JSON, the wire form; authored as YAML.

```json
{
  "name": "Pipeline Name",
  "version": "v1.0",
  "backend_type": "termnorm",
  "nodes": {
    "node_name": {
      "type": "llm",
      "node_role": "cache | candidate_source | enricher | ranker",
      "config": {"prompt_family": "node_name", "prompt_version": 1,
                 "schema_family": "node_name", "schema_version": 1},
      "optimizer": {
        "observation_mappings": [{"pipeline_key": "output_key_name", "output_field": "field_in_response"}]
      }
    }
  },
  "pipelines": {"pipeline_name": ["node1", "node2", "node3"]},
  "resolved_prompts": {"node_name/1": {"persona": "...", "task_intent": "..."}},
  "resolved_schemas": {"node_name/1": {"fields": ["..."], "json_schema": {}}}
}
```

A node may appear in several `pipelines`. An optimizer manifest takes `resolved_schemas` from its generated sibling `resolved_schemas.json` (`scripts/build_optimizer_schemas.py`).

## The shape, and what PromptPotter reads of it

**Read the required set, the types and the defaults off `domain/pipeline_schema.py`'s models.** `description`, `runtime`, `short_circuit` and `input_schema` are the backend's self-description for a human reader; a key PP does not use still belongs in the file — "required" means *a connector must publish it*.

What the models cannot state:

- **`type` is a closed vocabulary in three families** (`domain/pipeline_schema.py::NodeKind`). A THINKING node runs a model, so a model, a reasoning rung and a temperature are its own; `llm` answers in one call, `agent` works in a tool loop. A retrieval or plumbing node moves data. A GATEWAY node (`measurement`) runs another PIPELINE and carries no config of its own; its `nested_datasets` names each dataset whose pipeline it runs, the served pipeline's `nests` (`pipeline_resolve.py::_nests`), and on any other type it is refused at parse.
- **`backend_type` is required and never a `PipelineSchema` field**: readers take it off the raw overlay; it picks the connector at init (`dataset_access.py::runnable_definition` raises when absent). Whether its connector declares `self_optimization` — the ONE test for an L4 campaign — is `runner/inner/tasks.py::is_self_optimization`, served as the hierarchy entry's `self_optimization` tag.
- **`pipelines` must contain `default`.** The other names decide whether a node is drawn (`derive_pipeline_view`): a sequence sharing steps with `default` is an ALTERNATIVE a controller picks at the round boundary; one sharing none is a separate PHASE; **a node named by no pipeline is not in the flow** — why the check-in node is a one-step pipeline.
- **The CONFIG surface follows the declaration, not the run.** `PipelineSchema.declared_nodes` covers every node under `nodes:`; identity follows only where a point CONFIGURES an off-chain node (`node_configs` → `sp_hash`).
- **`runtime` is orthogonal to `Connector.execution`**: where a node runs inside the backend's topology vs how PromptPotter reaches the backend.
- **`output_schema` is declared at `config.output_schema`**, never node-level, and is locked against the optimizer (`SCHEMA_OWNED_FIELDS`).
- **`response_format` is PromptPotter's axis; a connector must not declare it.** The toggle (`SCHEMA_TOGGLE_PARAM`) is synthesized at parse time onto every node `PipelineNode.tunes_llm` names and resolved at the wire seam: `json` sends `output_schema` + `answer_fields`, `text` sends NEITHER. What a connector owes is the READING — a schema on the wire means structured output, its absence means prose; `json` with no schema is a caller error to raise on.
- **`param_allowed_values` drives three things**: L1's prompt guidance, the JSON-schema enum on structured-output generation, and `ValidationFailure` attachment in `overlay_failures`.

## Node capabilities

Opt-in; a deterministic node declares none.

- **Exit point** — a node producing candidates writes its `pipeline_key`; its cells are filed under the node-chain prefix that produced them.
- **Stop rules** — a verdict flows by return value (`domain/validators.py`), never an exception. The bench's `application/scoring/classification.py::DegradationCheck` stops a walk `broken` on a deprecated-sample rate or on the first row `classify_result()` marks fatal.
- **Warnings** — per-sample warnings pin to the configuration that produced them and reach the optimizer through `evidence_health` / `diagnostics`, never selecting samples.

LLM nodes additionally:

- **Prompt exposure** — `prompt_info.fields` (`domain/pipeline_schema.py::NodePromptInfo`, required) names the target prompt's parts in render order, and that order sits inside the archive key.
- **Shots are part of the target prompt, carried by key and rendered last.** `shots` name demo-pool rows (`DatasetSplit.demo`) by `Sample.key`; `OptSearchPoint.scored_prompts(schema, framing, demo=)` resolves them after the fields. The individual never carries a row's text; the export carries the resolved block. A demo row is never scored and never reaches a panel or the bench set. Editors: `l1_generate`'s `shots` slot (`k_max`, `validators/l1_strict.py::l1_shots_in_demo_pool`); an `algorithm` member without a model call (`optimizers/capo/members.py::FewShot`, `cross_shots`).
- **Optimizer-discoverable parameters** — declared accepted values become search axes, hardcoded on neither side.
- **Self-healing** — a `ValidationFailure` at parse time (`bench/children.py::overlay_failures`), a `RuntimeFailure` mid-run (`application/scoring/arm_states.py::read_breakage`).

## Optimizer node types

An optimizer manifest uses `llm` and `measurement` nodes plus five types no backend declares ([`../architecture.md`](../architecture.md) § Bench and optimizer). Each — and every `llm` node the bench walks — is an implementation registered under the node's NAME through the entry-point registry `promptpotter.optimizer_nodes`; the node's `config` is that member's typed knobs, every field required. No member sees the bench set, a `Cycle`, a store or a live client: each receives the `NodeContext` the bench builds (`application/bench/node_context.py`) — the readings it `sees`, the slices it `reads` / `writes`, the members it `consults`; a roster where any of those is not closed by exactly one member is refused at load, as is a read the member cannot hold (`CycleRead.refusal`). A child is made through `Children.child` / `Children.edit`, so its lineage names every node that wrote it. A decision a member checkpoints is a typed `CheckpointInputs` it declares, re-derived on resume where it is `replayers.py::Replayed`. What an optimizer tells the harness — arms, prompt sources, limits, facts — is a declaration of the member that owns it, defaulted on its role's Protocol (`nodes.py::NodeMember`, `Proposer`, `Sampler` …); a backbone fact no member answers, or two do, is refused at load.

| Type | Reads | Returns | Binds it |
|---|---|---|---|
| `llm` | its prompt, filled through the dispatch hub | its parsed response — for a proposing node, individuals written through `Children.child` / `Children.edit`, the one writer of every locus | every child is admitted by the bench as it is written (`promptpotter/application/bench/children.py::admission_failures`): forbidden keys, `overlay_failures`, the node's permitted model set, the backend's mandatory placeholders |
| `measurement` | the round's candidates, the sampler's panel, the eliminator's checks | the round's rows | the bench's scoring gateway, walked unchanged; `config` stays empty |
| `sampler` | the search split, the bench's per-sample difficulty, prior rows | the round's panel: ordered sample ids, cut into the blocks an eliminator decides between | draws from the search split alone; deterministic given its inputs and seed |
| `eliminator` | the panel, the candidates, rows as they land | a continue or cut per arm per block, each cut a ledger decision stamped with this node | cuts on evidence about the arm, never on a technical failure — that is the bench's `DegradationCheck`; the `none` member walks every arm to the end |
| `selector` | the round's rows, lineage, the population (`ctx.read(Lineage).population`) | a `Selection` — `selected` (one arm's key or `None` to hold), `leading`, `population` | its choice is what the optimizer keeps, never a score the bench serves |
| `algorithm` | individuals, and the demo pool when it edits shots | individuals written through `Children.child` / `Children.edit`, no model call | deterministic given its inputs and seed |
| `controller` | the round's envelope and the optimizer's own state | which of the manifest's other `pipelines:` entries runs at the round boundary | whether the run STOPS is the bench's (`runner/termination.py::standing_tripped`); the bench walks `default` alone, and a manifest without a controller runs it every round |

An `llm` node's role is its position: before the measurement it PROPOSES, after the selector it ADAPTS. An `algorithm` node before the measurement proposes too, as do the nodes of `initial_population`, walked in a round entered with no population: in place of `default` (`initial_population_walks: alone`) or ahead of it (`before_default`). An operator's arms are written through the bench's own seat (`nodes.py::OPERATOR_SEAT`, `runner/round.py`), bounded by `SelectedOptimizer.shot_cap`. **A switch, never a copy**: recombine members by `extends: <manifest>` + `swap: {<node>: {node: <name>, of: <manifest>}}` (`capo-pobb`). An eliminator's `Race` stops a walk on its own rows (`rule`) or every live walk together at each block's close (`blocks`).

**`default` lists its nodes in round order; the bench places them by role at load** (`optimizer_manifest.py::_round_plan`, the roles `RoundPlan`'s fields in order). A node takes the first role at or past the previous node's that admits its type, so an `eliminator` is listed before the `measurement` it races; exactly one `measurement`, at most one `controller`. One structural rule rejects a manifest in `parse_pipeline_response` itself: **an `eliminator` needs a `sampler` before it in the same pipeline**.

## How the prediction is read

`predicted` is the head of the last `ranker` or `candidate_source` node's output that wrote its `pipeline_key` (`application/scoring/classification.py::terminal_ranking`): the pipeline SHAPE decides the source, never the key name. One with no terminal ranker emits no prediction (every sample `NO_RESULT`), and init warns.

## Strict parsing

`parse_pipeline_response()` is the single ingress for every `pipeline.yaml`: a field is required and supplied, or optional and ignored absent — no silent default; a backend's file and every optimizer manifest round-trip through it identically. Worked examples: `termnorm.py::CONNECTOR`'s `node_templates`, laid under every definition naming TermNorm ([`promptpotter/assets/datasets/CLAUDE.md`](../../promptpotter/assets/datasets/CLAUDE.md) § A dataset file is a partial), and [`justlogic/family.yaml`](../../promptpotter/assets/datasets/justlogic/family.yaml), one family's delta.
