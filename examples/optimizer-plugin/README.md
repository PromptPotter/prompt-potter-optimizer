# An optimizer shipped from your own package

The smallest optimizer the bench runs, registered through entry points alone — nothing under
`promptpotter/` names it. Copy the directory, rename `example`, and replace the three members.

| File | What it is |
|---|---|
| `pyproject.toml` | The registration: one `promptpotter.optimizer_nodes` entry per member, one `promptpotter.optimizer_runtimes` entry for the manifest. |
| `example_optimizer/pipeline.yaml` | The manifest. `default` walks a sampler, the proposing node `propose`, the bench's measurement and a selector — the least a round takes. |
| `example_optimizer/members.py` | The three members, the round payload and the runtime. Every arm `propose` makes carries `source="example:propose"`. |
| `example_optimizer/operators.py` | What `propose` sends. |

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

The node types in `promptpotter/application/optimizers/nodes.py` hand a member the live `Cycle`,
so they are outside the v1 stability promise (`docs/developer/stable-api.md`). A plugin is trusted
code loaded into the process, it may not take a name a built-in holds, and one that fails to import
stops PromptPotter at startup.
