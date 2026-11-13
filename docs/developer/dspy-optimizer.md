# PromptPotter as a DSPy optimizer

`presentation/teleprompter.py::PromptPotterOpt` wraps the loop as a DSPy `Teleprompter` — the `pip install promptpotter[dspy]` extra, for an audience that never clones this repo or opens the webapp.

## What you trade away

Only the operator surfaces — the webapp, an agent driving the campaign (`/potter-run`) and L4. `compile()` mints a real campaign on disk, so the engine and the CLI verbs of the same distribution work on it (`pause`, `resume`, `ab`, `verify`).

## The contract

```python
from promptpotter.presentation.teleprompter import PromptPotterOpt, compile_loop, Node

optimizer = PromptPotterOpt(metric=my_metric, dataset_name="my-task",
                            loop=compile_loop(optimizer="potter", budget={"rounds": 5}),
                            node=Node(model="openai/gpt-oss-120b", tune=("instruction", "persona", "model"),
                                      allowed={"model": ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]}))
compiled = optimizer.compile(my_program, trainset=trainset)
```

`compile(student, *, trainset, teacher=None, valset=None, **kwargs) -> Module` is DSPy's own; every field defaults, so that is a complete run. `compile_loop(**knobs)` returns the knobs a compile sets, a layer of the campaign's own `OptimizationConfig`, so any field of it is a keyword: `optimizer=` names any manifest `optimizer_roster()` lists, `nodes=` that manifest's node knobs (`pipeline_responses.py::OptimizerSetup.knobs`; one the manifest does not take is refused when the loop is built), `budget=` per axis (a named null lifts it); a compile lays them over `promptpotter/assets/datasets/defaults.yaml`, as every definition does. The layers: [`../concepts/the-loop.md`](../concepts/the-loop.md).

What the signatures do not say:

- `compile()` returns a **copy** of your program with the winning prompt applied; the original is never mutated, during scoring either. The winner's provenance is on `optimizer.export` ([`stable-api.md`](stable-api.md) § 5c).
- **`valset` is accepted and unused**: PromptPotter holds out nothing of its own; evaluate the returned program however you already do.
- **Your program's spend is counted, call by call**: each candidate runs on a copy of your LM whose every call is admitted against the campaign's ceiling before it leaves and billed with the usage DSPy records, a call that raised included. On that copy DSPy's own retries and reply cache are off — PromptPotter's measurement cache is the one replay. `max_calls`, `max_input_tokens` and `max_tokens` in `Node.extra` run it under a ceiling.
- **Your metric is the scorer**; its float is what the campaign optimizes. `dspy.settings` is carried across the async boundary intact: a module with only `forward` goes through `dspy.asyncify`, never a bare worker thread.
- **In a running event loop, `await optimizer.acompile(…)`**: `compile()` works there by moving the run onto its own thread, but a thread never receives SIGINT, so Ctrl+C stops pausing the campaign; `acompile()` keeps the interrupt.

## The node is your program

`tune` is the axis list — prompt fields and model params evolve together; `model` on it lets the search switch models, bounded by `allowed`; anything off `tune` is frozen at the value set here. **One node, and the program is it**: a DSPy program is a single call from here, so the evolved prompt and tuned settings are applied to every predictor in the copy that scores — the wrong shape for predictors doing different jobs; split such a program into two compiles, or keep the others' `lm` set explicitly. Model settings are applied with DSPy's own `lm.copy(**kwargs)`, so a custom LM keeps its class and provider session.

Each example's CONTENT keys the measurement cache, never its name or position: a second `compile()` replays every cell already paid for however the rows were shuffled or extended, and a `trainset` holding twins is refused before the first call. `dataset_name` names where the campaign and its dataset directory live; that directory is rewritten on every compile — a projection of the arguments, never config to edit by hand.
