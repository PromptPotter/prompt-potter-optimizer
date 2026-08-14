# PromptPotter as a DSPy optimizer *(draft — package not yet released)*

`promptpotteropt` wraps the PromptPotter loop as a DSPy `Teleprompter`. It is a separate
package for a separate audience: you install it beside DSPy and never clone this repo, run
a server, or open the operator webapp.

> CAPO ships in [promptolution](https://github.com/finitearth/promptolution), not DSPy —
> DSPy's own are MIPROv2, COPRO, GEPA and BootstrapFewShot. The swap reads the same either way.

## What you trade away

Less than you would expect. `compile()` mints a real campaign on disk, so the engine and
everything keyed to it come along — PoBB pruning, Rasch scoring, the block library, the
measurement cache, the spend ceiling, and the campaign tree the CLI verbs operate on.
`promptpotteropt` installs that CLI as a dependency, so those verbs are already on your PATH:

| | With `promptpotteropt` | Full install |
|---|---|---|
| **Live view of a run** — phase, round, candidate, in-flight query | the terminal readout · `dashboard.json` on disk · an MLflow child run per trial | the same, plus the operator webapp |
| **Pause and resume** · **rewind and fork** | `promptpotter pause` · `resume --from N` · `--fork-on-divergence`, against the campaign `compile()` minted | the same |
| **Rescore without re-measuring** — change the formula, replay the decisions | what-if · lens · replay, off the measurements your run stored | the same |
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

After — `pip install promptpotteropt`, then obey DSPy's `compile()` contract:

```python
from promptpotteropt import PromptPotterOpt

optimizer = PromptPotterOpt(metric=my_metric, dataset_name="my-task")
compiled = optimizer.compile(my_program, trainset=trainset, valset=valset)
compiled.save("optimized.json")
```

Every field below has a default, so that is a complete run. The rest of this page is what
you override once you want the search shaped to your task.

## The loop is three layers

L1 proposes candidates each round from the last winner. When L1 stops improving, **L2
observes** — it reads the whole round history rather than the last result, and re-aims L1's
strategy. When the branch itself is spent, L3 rewinds to a better ancestor and climbs a
different ridge. Patience is how many flat rounds each layer tolerates before handing up:

```python
from promptpotteropt import Loop

loop = Loop(
    max_rounds=5,
    n_variants=6,             # candidates generated per round
    l1_patience=0,            # L1 mutates the winner
    l2_patience=2,            # L2 observes the history, re-aims L1
    l3_patience=1,            # L3 rewinds the lineage, climbs elsewhere
    improvement_threshold=0.01,
    elimination_n_min=4,      # samples a candidate gets before it may be pruned
    pobb_epsilon=0.2,         # how aggressively trailing candidates are killed
)
```

## The nodes are your predictors

One entry per predictor in `my_program`, keyed by name. `tune` is the axis list — prompt
fields *and* model params evolve together, which is the half `with_instructions()` cannot
reach. Listing `model` lets the optimizer switch models between candidates, bounded by
`allowed`. Anything you leave off `tune` is frozen at the value you set:

```python
from promptpotteropt import Node

nodes = {
    "extract": Node(
        model="openai/gpt-oss-20b",
        temperature=0.0,
        tune=["instruction", "persona", "answer_format"],
    ),
    "classify": Node(
        model="openai/gpt-oss-120b",     # any OpenAI-compatible base URL
        temperature=0.0,
        reasoning_effort="medium",
        tune=["instruction", "persona", "thinking_style", "answer_format",
              "temperature", "reasoning_effort", "max_tokens", "model"],
        allowed={
            "model": ["openai/gpt-oss-120b", "openai/gpt-oss-20b"],
            "reasoning_effort": ["low", "medium", "high"],
        },
    ),
}

optimizer = PromptPotterOpt(metric=my_metric, dataset_name="my-task",
                            loop=loop, nodes=nodes)
```

## Why it asks for a name

`dataset_name` plus each example's position in `trainset` keys the measurement cache. Reuse
the name and a second `compile()` skips every measurement it already paid for — this is what
makes a re-run cheap.

Because identity is positional, **the order of `trainset` is part of it.** Shuffle it, filter
it, or swap in new rows, and the rows no longer mean what the cached scores were measured
against. Every measurement stores the example it was taken against, so a `trainset` that
disagrees with what a name already measured is refused before the first call rather than
silently scored from the old rows. **New rows, new name.**
