# An optimizer shipped from your own package

The smallest optimizer the bench runs, registered through entry points alone — nothing under
`promptpotter/` names it. Copy the directory, rename `example`, and replace the three members.

| File | What it is |
|---|---|
| `pyproject.toml` | The registration: one `promptpotter.optimizer_nodes` entry per member, one `promptpotter.optimizer_runtimes` entry for the manifest. |
| `example_optimizer/pipeline.yaml` | The manifest. `default` walks a sampler, the proposing node `propose`, the bench's measurement and a selector — the least a round takes. |
| `example_optimizer/members.py` | The three members, the round payload and the runtime. Every arm `propose` makes carries the variation `example:propose` in its lineage. |
| `example_optimizer/prompts.py` | What `propose` sends. |

## Install and check

```bash
pip install -e .
python -c "from promptpotter.application.optimizers import runtime_origins as o; print(o()['example'])"
```

It prints `example-optimizer: example_optimizer.members:RUNTIME`, the distribution and entry point
that registered the name. `GET /optimizers` serves the same origin.

## Run it

`optimization.optimizer: example` in a campaign's config selects it, and `nodes.{name}.config`
adjusts its knobs, at every entry point. At zero spend, from a PromptPotter checkout:

```bash
PROMPTPOTTER_HOME=<scratch dir> python scripts/offline_run.py --optimizer example
```

## What you are building on

Everything `members.py` and `prompts.py` use, and nothing else:

| You write | Against |
|---|---|
| A knobs model per member | `StrictModel`, each field `Annotated[T, Knob(Scope, Estimand)]`. The manifest's `nodes.{name}.config` is validated as it. |
| A member per manifest node | The Protocol of its node type in `promptpotter/application/optimizers/nodes.py` — here `Sampler`, `Proposer`, `Selector`. Each states `name`, `kind`, `knobs` and its type's own members. |
| The round, inside a member | `ctx: NodeContext[YourKnobs]` (`promptpotter/application/bench/node_context.py`): `ctx.knobs`, typed; the readings `ctx.parent`, `ctx.round_num` and `ctx.population` — the individuals the run carries, which a selector replaces with `ctx.keep(...)`; `ctx.variation`, this node's own entry for a child's lineage; `ctx.rng()` for a draw a resume repeats; `ctx.fill(**values)` for the node's template; `ctx.ask_each({idx: prompt})` for its llm calls; `ctx.decide(kind, inputs, outcome)` to put a decision on the record. |
| What a member returns | `nodes.Panel`, `nodes.Proposals`, `nodes.Selection`; a selector reads `nodes.Measured`. |
| An arm, from a reply | `paper_templates.child(ctx, parents, raw, changes)`, which writes it through `ctx.child` — the one writer of an individual, which stamps `ctx.variation` and has the bench admit the child as it is made (`promptpotter/application/bench/children.py`); a node later in the walk changes one through `ctx.edit`. A knobs model subclassing `paper_templates.RewriteKnobs` declares whether the reply replaces the whole prompt or the `instruction` alone. `paper_templates.task_description(ctx)` is the campaign's framing for a template. |
| The state a round banks | The population is the bench's and needs no code. |
| What else a round banks | A `RoundPayload` subclass named for the manifest, carried in `nodes.BankedState` and read back with `nodes.state_as`. |
| The runtime | `nodes.OptimizerRuntime`: `name`, `manifest_dir`, `prompt_sources`, `start`, `arms`. Every other member has a default. |

A plugin is trusted code loaded into the process, it may not take a name a built-in holds, and one
that fails to import stops PromptPotter at startup.
