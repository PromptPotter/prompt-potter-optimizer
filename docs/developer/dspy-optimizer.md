# PromptPotter as a DSPy optimizer

`PromptPotterOpt` wraps the loop as a DSPy `Teleprompter`. It is an **extra on the one
package** — `pip install promptpotter[dspy]` — for an audience that never clones this repo,
runs a server, or opens the operator webapp. A plain install is already just the engine, so
the DSPy dependency and this module are the only things the extra adds.

> CAPO ships in [promptolution](https://github.com/finitearth/promptolution), not DSPy —
> DSPy's own are MIPROv2, COPRO, GEPA and BootstrapFewShot. The swap reads the same either way.

## What you trade away

Less than you would expect. `compile()` mints a real campaign on disk, so the engine and
everything keyed to it come along — PoBB pruning, Rasch scoring, the block library, the
measurement cache, the spend ceiling, and the campaign tree the CLI verbs operate on. It is
the same distribution, so those verbs are already on your PATH:

| | With `promptpotteropt` | Full install |
|---|---|---|
| **Live view of a run** — phase, round, candidate, in-flight query | the terminal readout · `dashboard.json` on disk · an MLflow child run per trial | the same, plus the operator webapp |
| **Pause and resume** · **rewind and fork** | `promptpotter pause` · `resume --from N` · `--fork-on-divergence`, against the campaign `compile()` minted | the same |
| **Rescore without re-measuring** — change the formula, replay the decisions | mask · lens · replay, off the measurements your run stored | the same |
| **Diagnostics before you spend** | `noise-floor` · `seed-screen` · `ab` · `verify` | the same |
| **The operator webapp** — live tree, candidate diffs, run control from a browser | ✗ | ✓ |
| **An agent driving the campaign** — launch, supervise, diagnose, act on a stall | ✗ | `/potter-run` in Claude Code |
| **Self-optimization** — the optimizer improving its own prompts | ✗ | L4 |

So the trade is the **operator surfaces**, not the search. Reach for this page when you want
the optimizer inside an existing DSPy program; reach for the
[full install](../manual/README.md) when the campaign is the thing you are working on.

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

Every field below has a default, so that is a complete run. The rest of this page is what
you override once you want the search shaped to your task.

`compile()` returns a **copy** of your program with the winning prompt applied — your
original is never mutated, and neither is it during scoring. The winner's provenance is on
`optimizer.export`: the formula its fitness was computed under, n, the lift and its interval,
θ, and the rows' own hash ([`stable-api.md`](stable-api.md) § 5c).

**`valset` is accepted and unused.** PromptPotter holds out nothing of its own — PoBB prunes
on the training rows — so evaluate the returned program however you already do. It is named
in the signature rather than dropped silently.

**Your program's spend is counted.** Its calls go through litellm rather than our client, so the
adapter tracks their usage per prediction and rolls it onto the campaign ledger — which is what
makes `Loop(spend_budget_usd=…)` bound the whole compile rather than half of it. DSPy does not
record usage for a completion its own cache served, and PromptPotter's measurement cache sits
above that, so the only calls that go uncounted are ones DSPy replayed that we did not. For exact
metering, `dspy.configure_cache(enable_disk_cache=False, enable_memory_cache=False)`.

**Your metric is the scorer.** It runs against each candidate's output and its float is what
the campaign optimizes, so no scoring rule is restated on our side. `dspy.settings` is
carried across the async boundary intact: a module with only `forward` goes through
`dspy.asyncify`, never a bare worker thread, which would run it under a default
configuration and attribute the measurement to a model you never chose.

**In a notebook, or anywhere you already have an event loop, `await optimizer.acompile(…)`
instead.** `compile()` still works there — it moves the run onto its own thread — but a thread
never receives SIGINT, so Ctrl+C stops pausing the campaign; `promptpotter pause` and the webapp
control are unaffected. `acompile()` keeps the interrupt.

## The loop is three layers

L1 proposes candidates each round from the last winner. When L1 stops improving, **L2
observes** — it reads the whole round history rather than the last result, and re-aims L1's
strategy. When the branch itself is spent, L3 rewinds to a better ancestor and climbs a
different ridge. Patience is how many flat rounds each layer tolerates before handing up:

```python
from promptpotter.presentation.teleprompter import Loop

loop = Loop(
    max_rounds=5,
    n_variants=6,             # candidates generated per round
    samples_per_round=20,     # rows each candidate is scored on — the cost knob
    l1_patience=0,            # L1 mutates the winner
    l2_patience=2,            # L2 observes the history, re-aims L1
    l3_patience=1,            # L3 rewinds the lineage, climbs elsewhere
    elimination_n_min=4,      # samples a candidate gets before it may be pruned
    pobb_epsilon=0.2,         # how aggressively trailing candidates are killed
    spend_budget_usd=None,    # a ceiling the run stops at; None runs uncapped
)
```

The values above are an illustration, not the defaults — those live on the `Loop` dataclass
itself and move without this page hearing about it. Read them off the fields.

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

`dataset_name` plus each example's position in `trainset` keys the measurement cache. Reuse
the name and a second `compile()` skips every measurement it already paid for — this is what
makes a re-run cheap.

Because identity is positional, **the order of `trainset` is part of it.** Shuffle it, filter
it, or swap in new rows, and the rows no longer mean what the cached scores were measured
against. Every measurement stores the example it was taken against, so a `trainset` that
disagrees with what a name already measured is refused before the first call rather than
silently scored from the old rows. **New rows, new name.**
