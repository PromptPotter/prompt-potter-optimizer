# PromptPotter as a DSPy optimizer

`PromptPotterOpt` wraps the loop as a DSPy `Teleprompter`. It is an **extra on the one
package** — `pip install promptpotter[dspy]` — for an audience that never clones this repo,
runs a server, or opens the operator webapp. A plain install is already just the engine, so
the DSPy dependency and this module are the only things the extra adds.

> CAPO ships in [promptolution](https://github.com/gepromptet/promptolution), not DSPy —
> DSPy's own are MIPROv2, COPRO, GEPA and BootstrapFewShot. The swap reads the same either way.

## What you trade away

Only the **operator surfaces**, not the search. `compile()` mints a real campaign on disk, so the
engine and everything keyed to it come along — PoBB pruning, Rasch scoring, the block library,
the measurement cache, the spend ceiling — and the CLI verbs of the same distribution work on it:
the terminal readout, `dashboard.json`, an MLflow child run per trial; `pause` · `resume --from N`
· `--fork-on-divergence`; mask · lens · replay off the stored measurements; `noise-floor` ·
`seed-screen` · `ab` · `verify`.

What the extra does not give you: the operator webapp, an agent driving the campaign
(`/potter-run`), and self-optimization (L4). Reach for the [full install](../manual/README.md)
when the campaign is the thing you are working on.

## The swap

Before — CAPO drives its own loop over a prompt list:

```python
from promptolution.optimizers import CAPO

optimizer = CAPO(predictor=predictor, task=task, n_steps=10)
best = optimizer.optimize(prompts)
```

After — `pip install promptpotter[dspy]`, then obey DSPy's `compile()` contract:

```python
from promptpotter.presentation.teleprompter import PromptPotterOpt

optimizer = PromptPotterOpt(metric=my_metric, dataset_name="my-task")
compiled = optimizer.compile(my_program, trainset=trainset)
```

The contract, as DSPy's source states it (`dspy/teleprompt/teleprompt.py`), is
`Teleprompter.compile(self, student: Module, *, trainset, teacher=None, valset=None, **kwargs) -> Module`.
Every field has a default, so that is a complete run; shape the search with a `loop`:

```python
from promptpotter.presentation.teleprompter import compile_loop

loop = compile_loop(
    optimizer="potter",       # which optimizer proposes — any name optimizer_roster() lists
    nodes={                   # that optimizer's node knobs; omitted keeps its manifest's
        "adaptive_queue": {"config": {"sp_budget_round": 20}},  # rows each candidate is scored on — the cost knob
        "l1_generate": {"config": {"n_variants": 6}},       # candidates generated per round
        "escalation": {"config": {"l1_patience": 0,         # L1 mutates the winner
                                  "l2_patience": 2,         # L2 observes the history, re-aims L1
                                  "l3_patience": 1}},       # L3 replans the strategy L1 works within
        "pobb": {"config": {"epsilon": 0.2}},               # how aggressively trailing candidates are killed
    },
    budget={"rounds": 5, "usd": None},  # what the run stops at, per axis; a named null lifts it
    elimination_n_min=4,      # samples a candidate gets before it may be pruned
)
```

`optimizer="capo"` (or `gepa`, `levi`) swaps the proposer and keeps everything around it; its
`nodes` are that manifest's knobs (`GET /optimizers/{name}/knobs`), and one the manifest does not
take is refused when the loop is built. `compile_loop` returns the campaign's own
`OptimizationConfig`, so any field of it is a keyword. The values above are an illustration:
omitting `nodes` runs the manifest as declared, and a compile starts from
`teleprompter.py::_COMPILE_DEFAULTS`. The layers themselves: [`../concepts/the-loop.md`](../concepts/the-loop.md).

`compile()` returns a **copy** of your program with the winning prompt applied — your
original is never mutated, and neither is it during scoring. The winner's provenance is on
`optimizer.export`: the formula its fitness was computed under, n, the lift and its interval,
θ, and the rows' own hash ([`stable-api.md`](stable-api.md) § 5c).

**`valset` is accepted and unused.** PromptPotter holds out nothing of its own — PoBB prunes
on the training rows — so evaluate the returned program however you already do. It is named
in the signature rather than dropped silently.

**Your program's spend is counted, call by call.** Each candidate runs on a copy of your LM
whose every call is admitted against the campaign's ceiling before it leaves and billed with the
usage DSPy records for it, a call that raised included — which is what makes
`compile_loop(ceiling=…)` bound the whole compile rather than half of it. On that copy
DSPy's own retries and reply cache are off: a resend comes from the cell's budget, and
PromptPotter's measurement cache is the one replay. Declare `max_calls`, `max_input_tokens` and
`max_tokens` in `Node.extra` to run under a ceiling; a program that calls its LM more than
`max_calls` times per example is stopped there.

**Your metric is the scorer.** It runs against each candidate's output and its float is what
the campaign optimizes, so no scoring rule is restated on our side. `dspy.settings` is
carried across the async boundary intact: a module with only `forward` goes through
`dspy.asyncify`, never a bare worker thread, which would run it under a default
configuration and attribute the measurement to a model you never chose.

**In a notebook, or anywhere you already have an event loop, `await optimizer.acompile(…)`
instead.** `compile()` still works there — it moves the run onto its own thread — but a thread
never receives SIGINT, so Ctrl+C stops pausing the campaign; `promptpotter pause` and the webapp
control are unaffected. `acompile()` keeps the interrupt.

## The node is your program

`tune` is the axis list — prompt fields *and* model params evolve together, which is the half
`with_instructions()` cannot reach. Listing `model` lets the search switch models between
candidates, bounded by `allowed`. Anything left off `tune` is frozen at the value set here:

```python
from promptpotter.presentation.teleprompter import Node

node = Node(
    model="openai/gpt-oss-120b",     # any OpenAI-compatible base URL
    temperature=0.0,
    tune=("instruction", "persona", "thinking_style", "answer_format",
          "temperature", "max_tokens", "model"),
    allowed={"model": ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]},
)

optimizer = PromptPotterOpt(metric=my_metric, dataset_name="my-task",
                            loop=loop, node=node)
```

**One node, and the program is it.** A DSPy program is a single call from here — its
predictors are its own composition, not a chain we route through — so the evolved prompt and
the tuned model settings are applied to **every** predictor in the copy that scores. That
suits a program whose predictors share a task, and it is the wrong shape for one whose
predictors do genuinely different jobs: they will all receive the same instruction. Split
such a program into two compiles, or optimize the predictor that carries the task and leave
the rest pinned by keeping their `lm` set explicitly.

Model settings are applied with DSPy's own `lm.copy(**kwargs)`, so a custom LM — an Azure
wrapper, a local handle, a cached client — keeps its class and its provider session. It is
never reconstructed as a generic `dspy.LM`, which would quietly re-point the measurement.

## Why it asks for a name

Each example's CONTENT keys the measurement cache, never its name or its position in
`trainset`: a second `compile()` replays every cell an example already paid for, whatever the
name and however the rows were shuffled, filtered or extended — this is what makes a re-run
cheap. Two examples with the same input would share one measurement, so a `trainset` holding
twins is refused before the first call. `dataset_name` is a label: it names where the campaign
and its dataset directory live. That directory is rewritten on every compile: it is a projection
of the arguments just passed, never config to edit by hand.
