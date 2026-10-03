# Peer round cost — an observation, not yet a result

Head-to-head `h2h-1003`, `justlogic-d234`, 2026-10-03. Four arms, one instrument (500 search
rows, 100 held-out bench rows, target `openai/gpt-oss-20b:nitro`), one declared budget of $0.30
per arm. Every peer runs at its paper configuration. **One run, no seeds: this is a lead for the
results section, not supplementary data.**

## What was seen

**A peer's first round does not fit a small budget; potter's rounds do.** The unit of spend a
method needs before it can elect anything differs by an order of magnitude:

| Optimizer | Round 1 at paper config | Cells before the first election | Rounds closed by $0.10 incurred |
|---|---|---|---|
| potter | 3 candidates on 34 cells, eliminated early | ~100 | 5 (stopped on `max_rounds`) |
| CAPO | 14 candidates racing on up to 300 cells | up to 4,200 (the run's preflight prices it at $0.51) | 0 |
| LEVI | 4 seeds scored on the whole search pool | 2,000 | 0 |
| GEPA | 1 child on a 3-cell minibatch, then the Pareto set | 3 to 253 | 1 |

So below roughly one peer round of budget, a peer returns its origin: there is no intermediate
answer to grade. Potter returned a selection at $0.097 incurred.

**Potter's reading at that spend** (bench column, 100 held-out rows, composite fitness): origin
0.541 → selected 0.575, lift +0.034, CI [-0.027, +0.094]. **Not separable from zero** — quote it
as "a selection existed", never as a gain.

## Why this is a property of the methods, and what is ours

- CAPO's population and LEVI's calibration pass are the papers' own designs; the cost is theirs.
- GEPA's minibatch gate is 3 cells (App. E.4). On a target that sits near 40% accuracy a 3-cell
  gate is close to a coin flip, so a child is rejected on noise.

## Confounds to remove or declare before this is a claim

`h2h-1003` carried three handicaps that were our wiring and not the peers' methods, so none of its
numbers compares the methods. The wiring is fixed; `h2h-1004` is the first run under it.

1. **A peer wrote one field.** It could append to the origin's prompt and never retract it, and
   GEPA's first reflection was shown an empty instruction. A peer now reads and replaces the whole
   prompt (`paper_templates.py::prompt_text` / `rewritten`).
2. **CAPO ran without shots**, because the dataset held out no demo pool. It now holds out 50
   rows, which opens the same surface to potter and shrinks the search pool to 450.
3. **Optimizer models differed by preset** — paper-faithful, and a confound for "whose method is
   better". `h2h-1004` runs every optimizer call on `openai/gpt-6-luna`, a declared deviation
   from the LEVI and GEPA presets; LEVI's paradigm-shift node takes `max_tokens` 8000, that
   model's reasoning floor.

## `h2h-1004` — the same wiring at $0.50, and two more of ours

Bench column, 100 held-out rows, composite fitness; origin 0.541 for every arm.

| Optimizer | Selected at | Bench | Lift, 95% CI | Stopped on | Billed |
|---|---|---|---|---|---|
| GEPA | round 10 | 0.716 | +0.175 [+0.094, +0.256] | `spend_budget` | $0.483 |
| LEVI | round 2 | 0.707 | +0.166 [+0.075, +0.257] | `spend_budget` | $0.374 |
| potter | round 8 | 0.674 | +0.133 [+0.050, +0.216] | `optimizer_abort` | $0.237 |
| CAPO | round 1 | 0.632 | +0.091 [+0.016, +0.166] | `spend_budget` | $0.463 |

**Every lift excludes zero and every pair of intervals overlaps: no arm is separated from
another.** And two arms did not spend the budget they were given, both through our code:

- **Potter aborted at half its budget.** A retry that recovered was counted as a failed node, so a
  round whose every cell answered read as evidence-starved and the optimizer ended the run. A
  warning now counts only where its node did not finish (`results_health.py`).
- **LEVI stopped with about a fifth of its budget stranded.** Its refinements went out together,
  the first refusal cancelled the rest mid-flight, and a cancelled send is held at its full bound.
  A peer's fan-out now lands every send before it raises one's refusal
  (`paper_templates.py::ask_each`); CAPO's two fan-outs ride the same helper.

So `h2h-1004` ranks nothing either. The run after it is the first with no known handicap.

Still standing, to declare beside any claim:

4. **The budget is under one CAPO round** (preflight prices it at $0.53), so CAPO is graded on
   whatever its race had elected when the money ran out.
5. **The target answers "Uncertain" about three times in four** whatever the prompt, so every
   candidate sits in a 33–50% band and a round's winner is often inside the noise.
6. **Round-level accuracy is read on moving subsets** — only the bench column and the `overlap`
   line compare like with like.
7. **A reply broken mid-read is held at the cell's whole bound**, about a hundred times what the
   cell bills, because the backend may have billed it. One such cell took about 3% of an arm's
   budget in `h2h-1004`; it lands on arms at random.
8. **One benchmark.** A three-label logic task at a low-accuracy target is one setting; a peer
   that looks weak here is not shown weak on the tasks its paper reports.

## What would make it a result

Each arm bug-free at one equal budget ($0.50), then seeds; the claim then rests on the bench
column with its interval, and on budget-to-first-election as its own reported dimension.
