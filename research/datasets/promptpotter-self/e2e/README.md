# promptpotter-self/e2e — the L4 fixture

**A test fixture, not an instrument. Read no result off it.**

This is `promptpotter-self/run`'s recursion with every property that makes an outer lift falsifiable
taken out, so that the browser walk (`webapp/e2e/`) can exercise the L4 spawn path for a few cents:
one search cell and one held-out seat the outer bench grades, two inner rounds, one inner variant,
two samples, one outer round of two variants.
`dataset.yaml::campaign` and `dataset.yaml::panel` beside this file carry those numbers, and the real
panels' are `research/datasets/promptpotter-self/family.yaml::panel`'s — the only place a per-cell cost may be read off.

At one cell PoBB can never reach its elimination floor of four, so nothing is ever eliminated
and the outer δ ruler never warms. At two inner rounds `held_out_lift` averages one adoption. At
two samples the inner θ is noise, at two held-out rows the scored lift is a coin, and one held-out
seat grades the outer crown on one cell. **Every number this emits is an artefact of that geometry.**

## Why it could not be made cheaper

Cost here is dominated by the inner **optimizer** call, which is roughly fixed per inner round
(~40% of a four-round cell, so ~$0.0135) and does not shrink with the sample count. Cutting
samples to two removes nearly all of the worker share and none of that, so one cell has a floor
near 1.5¢ and **sub-1¢ per arm is not reachable while an inner optimization actually runs**. The
campaign's `ceiling.usd` is a stop at $0.05, not a forecast: a run that halts there means the
geometry has drifted and this file is wrong. The held-out seat's bench passes, the origin's and one
per outer adoption, are each a cell under that same stop.

The inner benchmark stays `justlogic/d234` for the same reason. A shallower cut would save worker
tokens that are already near zero at two samples, and it has no banked rows — it needs the
`benchmarks` extra and a HuggingFace fetch for about 6% of a cell.

## What is copied, and what that costs

Nothing is. `task.md` is the family's (`promptpotter-self/task.md`), read through the definition
chain, because the point is to exercise the real recursion rather than a stand-in. The graph is no
copy either: `dataset.yaml::pipeline` carries its own name and header over the one the connector serves. What
differs otherwise is `dataset.yaml::campaign` and `dataset.yaml::panel`, and what they differ in is the
geometry above.
