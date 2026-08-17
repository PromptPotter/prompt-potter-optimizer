# PromptPotter-self — Optimizer-of-the-Optimizer

A self-referential dataset: outer PromptPotter optimizes the **optimizer prompts** that drive the
inner PromptPotter cycle. Connector boundary: `promptpotter/connectors/promptpotter.py`. Spec:
[`../../docs/specs/l4-outer-loop.md`](../../docs/specs/l4-outer-loop.md).

Each outer "sample" is one entry in `inner_tasks.yaml`: it mints and runs a full inner campaign on
`justlogic-d234`. Each inner run reports a vector of proxy measurements
(`domain/l4/proxies.py::OuterSampleProxies`); the outer formula in `campaign.yaml::scoring`
re-anchors **one** of them — `mean_round_delta` — into [0,1] and nothing else, the quality terms
having carried more SEED variance than arm variance while holding authority over the ordering. The
rationale lives on `OuterSampleProxies`.

**What remains** — owned by
[`../../docs/specs/l4-outer-loop.md`](../../docs/specs/l4-outer-loop.md) § Open.

**The panel geometry, its per-cell cost and why it is 6 cells** — owned by `inner_tasks.yaml`, whose
figures come off the banked cycle ledgers. Do not re-derive a cost from the knob values: the factors
multiply, and an arithmetic estimate was 3× under the measured rate.

## Reading a cost figure

**An absolute campaign total is not quotable** — how many inner rounds each cell runs depends on the
`justlogic-d234` origin→target gap and on where the lives brake stops it. The per-cell rate is, and
it comes off the ledger, never a stopwatch: group `token_usage` records by `(kind, node, cached)`
and price them with `shared/pricing.py::compute_usd`.

**`TokenUsageRecord.cached` is what makes such a figure honest.** The content-addressed caches are
tenant-global, so a replayed cell costs $0 while doing the same search — on the banked corpus that
was roughly half the bill. All records give the cold-equivalent (incurred) figure, `cached=False`
alone gives what was billed; a clock cannot say which it measured, and the last figure taken that
way was an order of magnitude optimistic. One trap: the duration distribution of *completing* cells
is censored, since a cell that hits its wall-clock deadline emits no record at all.

**Cut samples or rounds to save money, never tasks** — the panel width is what makes elimination and
the winner election work, and `inner_tasks.yaml` states the floor and its reason.
