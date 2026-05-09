# Optimizer of the optimizer

PromptPotter can optimize **its own meta-prompts**. The four optimizer LLM
calls — `l1_generate`, `l1_critique`, `l2_context`, `l3_plan` — are
themselves prompts driven by the six-field `PromptTemplate` scheme. Expose
those template fields as a connector's `pipeline_params`, point an outer
PromptPotter cycle at them, and you get **PromptPotter optimizing
PromptPotter**.

This is the headline self-referential capability of M12. Connector:
`promptpotter/connectors/promptpotter.py`. Demo dataset:
`datasets/promptpotter-self/`. Spec:
[`../specs/m12-promptpotter-as-connector.md`](../specs/m12-promptpotter-as-connector.md).

## Why it's interesting

Most prompt-optimization work treats the *meta-prompt* as fixed — the L1
and critique templates are written by humans and frozen. But the
meta-prompts are prompts; they're as optimizable as any task prompt.
Self-optimization tests two beliefs:

1. The mutation-and-elimination loop is general enough to improve any
   prompt — including its own scaffolding.
2. The connector boundary is genuinely backend-agnostic. If
   "PromptPotter" can be a backend in the same registry as "TermNorm",
   the abstraction holds.

## The three composable proxies

Each outer "sample" runs an inner PromptPotter cycle on a cheap proxy
benchmark. The connector reports three metrics per inner cycle, all
exposed simultaneously to the outer scoring formula:

| Proxy | Definition | When to use |
|---|---|---|
| `first_round_delta` | inner score after round 1 minus inner baseline | development — fast iteration on outer hyperparameters |
| `after_N_rounds_delta` | inner score after N rounds minus inner baseline | calibration — captures improvement rate |
| `rounds_to_N` | rounds to reach an inner target score (times out at `max_inner_rounds`) | publication — closest to "did this meta-prompt actually help" |

The outer `campaign.json::scoring` composes these. Example:

```
0.4 * first_round_delta + 0.4 * after_N_rounds_delta + 0.2 * (1 / max(rounds_to_N, 1))
```

Operators **don't pick one proxy and commit** — they accumulate evidence
across runs. Start with `first_round_delta` for cheap iteration; add
`after_N_rounds_delta` once outer-loop dynamics stabilize; switch to a
`rounds_to_N`-weighted formula for final runs. The three proxies are a
tool for the operator's experimental judgment, not a fixed choice.

## Cost realism

Each outer sample is at minimum a partial inner cycle. Cost compounds:

```
outer_cost ≈ outer_n_samples × outer_n_candidates × inner_n_samples × inner_n_rounds × per_call_cost
```

For the demo dataset's defaults (`n_variants: 3`, `sp_budget_ttest: 4`,
`n_samples_per_inner_round: 10`, `max_inner_rounds: 3`), one outer round is
roughly 360 inner candidate-evaluations. Plan accordingly:

- Development: `inner_n_rounds: 1`, `first_round_delta` only — minutes
  per outer round.
- Calibration: `inner_n_rounds: 3`, all three proxies — tens of minutes.
- Publication: `inner_n_rounds: 5`, `rounds_to_N`-weighted, target
  benchmark — hours.

Outer dashboard's `dashboard.json::spend` block (see
[`../specs/m11-spend-tracking.md`](../specs/m11-spend-tracking.md))
surfaces accumulated cost; check it before extending runs.

## What stays the same on the outer cycle

The outer cycle is just a PromptPotter cycle. The loop, escalation,
PoBB elimination, dispatch hub, dashboard — all the same. From the
outer L1's perspective, it's evolving prompts whose "score" happens to
be "did this prompt make the inner loop converge faster." The
self-reference is only visible when you read what the prompts are about.

This is by design: the connector boundary keeps the outer cycle
provider-agnostic. Today: TermNorm or PromptPotter-self. M12 expands the
registry; the outer cycle doesn't change.

## Status

The connector and dataset architecture are landed; inner-cycle execution
(turning a wire payload into an actual inner campaign run) is the follow-up
deliverable. Until then, the demo dataset loads, validates, and renders on
the outer dashboard, but `optimize` raises a clear `NotImplementedError` at
the first inner match request. See
`promptpotter/connectors/CLAUDE.md` § Inner-cycle execution for the two
design options under consideration (localhost endpoint vs in-process
dispatch).
