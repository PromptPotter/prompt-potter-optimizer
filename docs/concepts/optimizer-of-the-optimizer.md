# Optimizer of the optimizer

> **Audience:** Developer reference. Operators see [`../manual/`](../manual/) for usage docs.

PromptPotter can optimize **its own meta-prompts**. The four optimizer LLM
calls — `l1_generate`, `l1_critique`, `l2_context`, `l3_plan` — are
themselves prompts driven by the eight-field `PromptTemplate` scheme. Expose
those template fields as a connector's `pipeline_params`, point an outer
PromptPotter cycle at them, and you get **PromptPotter optimizing
PromptPotter**.

This is the headline self-referential capability of M12. Connector:
`promptpotter/connectors/promptpotter.py`. Demo dataset:
`datasets/promptpotter-self/`. Spec:
[`../specs/roadmap.md`](../specs/roadmap.md) § Connectors + L4 inner-cycle execution.

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
| `first_round_delta` | inner score after round 1 minus inner origin | development — fast iteration on outer hyperparameters |
| `after_N_rounds_delta` | inner score after N rounds minus inner origin | calibration — captures improvement rate |
| `rounds_to_N` | rounds to reach an inner target score (times out at `max_inner_rounds`) | publication — closest to "did this meta-prompt actually help" |

The outer `campaign.json::scoring` composes these (each delta recentred `(delta+1)/2` so
regression < no-op < improvement stay distinct). Example:

```
0.5 * ((first_round_delta + 1) / 2) + 0.5 * ((after_N_rounds_delta + 1) / 2)
```

**`rounds_to_N` only carries a candidate gradient when the inner target is REACHABLE within
the round budget.** If the target sits above what the inner loop reaches in `max_inner_rounds`,
`rounds_to_N` is a constant (`len(levels)+1` on every task) that cancels in the candidate
election — dead weight. The shipped `promptpotter-self` formula (above) therefore weights only
the two deltas 0.5/0.5: its justlogic target (0.60) is above 2-round reach from a ~0.44 origin,
so `rounds_to_N` is retired from scoring (still computed for the inner narrative; re-add it once
the target becomes reachable). A per-sample cost multiplier is deliberately NOT composed here —
inner token count is a property of the seed, not the candidate, so folding it per-sample injects
per-seed noise rather than candidate signal; `spend_budget_usd` is the real cost control.

Operators **don't pick one proxy and commit** — they accumulate evidence
across runs: start with `first_round_delta` for cheap iteration, add
`after_N_rounds_delta` once outer-loop dynamics stabilize, and add a
`rounds_to_N` term only once the inner target is within round-budget reach.

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
[`../adr/0003-spend-and-tenancy.md`](../adr/0003-spend-and-tenancy.md))
surfaces accumulated cost; check it before extending runs.

## What stays the same on the outer cycle

The outer cycle is just a PromptPotter cycle — same loop, escalation,
PoBB elimination, dispatch hub, dashboard. From the outer L1's
perspective it's evolving prompts whose "score" happens to be "did this
prompt make the inner loop converge faster"; the self-reference is only
visible when you read what the prompts are about. By design, the
connector boundary keeps the outer cycle provider-agnostic: today
TermNorm or PromptPotter-self, and M12's registry expansion doesn't
change the outer cycle.

## Status

**The recursion is SHIPPED & live-validated (2026-06-30).** `new promptpotter-self`
runs real inner campaigns: each outer "sample" (an inner task in `inner_tasks.json`)
mints + runs a full inner PromptPotter cycle **in its own asyncio task** under a
**flat `<workspace>/.inner/<spawn_cycle_id>/` sandbox** (NOT physically nested —
that overflows Windows `MAX_PATH`; flat keeps it re-entrant at any depth), and the
three proxy metrics flow into the outer scoring formula. The shared `in_process`
seam also yields the in-process `llm_only` connector (no TermNorm server for the
basic case). Implementation: `promptpotter/application/runner/inner_recursion.py`.

**The project is now in its closing phase: ship a *distributable* `promptpotter-self`.**
The remaining work and the live-run learnings (gsm8k retired as the inner benchmark —
no headroom; `justlogic` high-depth chosen; the specialized `_optimizer_meta/` outer
prompt set is the gating slice; inner-spend rollup; bounded cheap default config) are
the **living finish-line plan** in [`../specs/l4-outer-loop.md`](../specs/l4-outer-loop.md)
§ Finish line — the single SoT an AI agent driving L4 reads first.
