# promptpotter-self-e2e — the L4 fixture

**A test fixture, not an instrument. Read no result off it.**

This is `promptpotter-self`'s recursion with every property that makes an outer lift falsifiable
taken out, so that the browser walk (`webapp/e2e/`) can exercise the L4 spawn path for a few cents:
one cell, one inner round, one inner variant, two samples, one outer round of one variant.
`campaign.yaml` and `inner_tasks.yaml` beside this file carry those numbers, and the real panel's
are its own `inner_tasks.yaml`'s — the only place a per-cell cost may be read off.

At one cell PoBB can never reach its elimination floor of four, so nothing is ever eliminated
and the outer δ ruler never warms. At one inner round `mean_round_delta` is a single difference
with no series behind it. At two samples the inner θ is noise. **Every number this emits is an
artefact of that geometry.**

## Why it could not be made cheaper

Cost here is dominated by the inner **optimizer** call, which is roughly fixed per inner round
(~40% of a four-round cell, so ~$0.0135) and does not shrink with the sample count. Cutting
samples to two removes nearly all of the worker share and none of that, so one cell has a floor
near 1.5¢ and **sub-1¢ per arm is not reachable while an inner optimization actually runs**. The
campaign's `spend_budget_usd` is a stop at $0.05, not a forecast: a run that halts there means the
geometry has drifted and this file is wrong.

The inner benchmark stays `justlogic-d234` for the same reason. A shallower cut would save worker
tokens that are already near zero at two samples, and it has no banked rows — it needs the
`benchmarks` extra and a HuggingFace fetch for about 6% of a cell.

## What is copied, and what that costs

`pipeline.yaml`, `task_description.md` and `task_context.yaml` are byte-for-byte the real
dataset's, because the point is to exercise the real recursion rather than a stand-in. Nothing
checks that they still agree, so an edit to any of them has to reach both directories in the same
commit — and for the graph that is a THIRD copy, which `pipeline.yaml`'s own header states in
full. What differs is `campaign.yaml` and `inner_tasks.yaml`, and what they differ in is the
geometry above.
