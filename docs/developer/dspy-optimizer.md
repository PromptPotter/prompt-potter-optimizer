# PromptPotter as a DSPy optimizer *(draft — package not yet released)*

`promptpotteropt` wraps the PromptPotter loop as a DSPy `Teleprompter`. It is a separate
package for a separate audience: you install it beside DSPy and never clone this repo, run
a server, or open the operator webapp.

> CAPO ships in [promptolution](https://github.com/finitearth/promptolution), not DSPy —
> DSPy's own are MIPROv2, COPRO, GEPA and BootstrapFewShot. The swap reads the same either way.

## What you trade away

The search is the same engine either way — PoBB pruning, Rasch scoring, the block library
and the measurement cache all come along. What you give up is the machinery around it, the
part that assumes a campaign on disk and an operator watching it:

| | With `promptpotteropt` | Full install |
|---|---|---|
| **Live view of a run** — phase, round, candidate, in-flight query | MLflow child runs per trial, refreshed by hand | `dashboard.json` + webapp |
| **Pause and resume** a running search | ✗ — DSPy has no `Teleprompter`-level checkpoint | `pause`, then `resume` |
| **Rewind and fork** — restart from round N, branch at a divergence | ✗ — no cross-run lineage exists in DSPy | `resume --from N` · `--fork-on-divergence` |
| **Rescore without re-measuring** — change the formula, replay the decisions | partial: DSPy's LM cache replays a new metric only if every request param is byte-identical | what-if · lens · replay, off stored measurements |
| **Diagnostics before you spend** — noise floor, seed screen, A/B, deepen a candidate | ✗ | `noise-floor` · `seed-screen` · `ab` · `verify` |
| **An agent driving the campaign** — launch, supervise, diagnose, act on a stall | ✗ | `/potter-run` in Claude Code |
| **Self-optimization** — the optimizer improving its own prompts | ✗ | L4 |
| **Dollar spend ceiling** — abort before the budget is gone | ✗ — `track_usage` counts tokens, in no currency | cost ledger + control plane |

Reach for this page when you want the optimizer inside an existing DSPy program. Reach for
the [full install](../manual/README.md) when the campaign is the thing you are working on.

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

`dataset_name` plus each example's `sample_id` keys the measurement cache. Reuse the name
and a second `compile()` skips every measurement it already paid for — this is what makes a
re-run cheap. Change the rows under a name you have already used and the cache serves scores
for the old ones. **New rows, new name.**
