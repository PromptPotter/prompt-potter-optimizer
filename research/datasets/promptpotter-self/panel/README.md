# PromptPotter-self — Optimizer-of-the-Optimizer

A self-referential dataset: outer PromptPotter optimizes the **optimizer prompts** that drive the
inner PromptPotter cycle. Connector boundary: `promptpotter/application/runner/inner/spawn.py`. What is true of L4:
[`docs/concepts/l4.md`](../../../../docs/concepts/l4.md).

Each outer "sample" is one entry in `dataset.yaml::panel`: it mints and runs a full inner campaign on
the seat's benchmark. Each inner run reports a vector of measurements
(`domain/l4/proxies.py::OuterSampleProxies` is the scored one); the family formula
(`research/datasets/promptpotter-self/family.yaml::campaign.scoring`) re-anchors **one** of them — `held_out_lift`, the area
under the inner run's held-out curve — into [0,1] and nothing else. The rationale lives in
[`docs/concepts/l4.md`](../../../../docs/concepts/l4.md) § The measurand.

**Prerequisite: the account holds `hotpotqa-distractor` and `legalbench-fods`.** Four seats measure
those two account definitions (tenant tier), which no committed tier carries, so on any other account
— the box included — the panel refuses at init and names the seat.

**What remains** — owned by
[`docs/specs/roadmap.md`](../../../../docs/specs/roadmap.md) § L4 closure.

**The panel geometry, its per-cell cost and why it is 6 cells** — owned by `dataset.yaml::panel`, whose
figures come off the banked cycle ledgers. Do not re-derive a cost from the knob values: the factors
multiply, and an arithmetic estimate was 3× under the measured rate.

## Reading a cost figure

**An absolute campaign total is not quotable** — it depends on how many arms elimination cuts and
how much of each cell replays. The per-cell rate is, and it comes off the ledger, never a stopwatch: group `token_usage` records by `(kind, node, cached)`
and price them with `infrastructure/llm/pricing.py::compute_usd`.

**`TokenUsageRecord.cached` is what makes such a figure honest.** The content-addressed caches are
tenant-global, so a replayed cell costs $0 while doing the same search — on the banked corpus that
was roughly half the bill. All records give the cold-equivalent (incurred) figure, `cached=False`
alone gives what was billed; a clock cannot say which it measured, and the last figure taken that
way was an order of magnitude optimistic. One trap: the duration distribution of *completing* cells
is censored, since a cell that hits its wall-clock deadline emits no record at all.

**Cut samples or rounds to save money, never tasks** — the panel width is what makes elimination and
the winner election work, and `dataset.yaml::panel` states the floor and its reason.
