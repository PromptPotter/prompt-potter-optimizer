# PromptPotter-self-pool — the optimizer tuned on a pool of environments

The same recursion as [`promptpotter-self`](../promptpotter-self/dataset.md), with one difference:
the outer samples are a **pool** the round draws from, not a panel every round runs whole. Each
task in `inner_tasks.yaml` is one **environment** — a dataset under one model — and that file owns
the pool's geometry, order and seeds.

**Nothing here is held out, and `justlogic-d234` is not in the pool.** The fixed JustLogic panel in
`promptpotter-self` is the calibration instrument: run it before and after a pool campaign, and
read the pool's effect there. A lift read on the pool's own environments is a training score.

**What a pool must hold to be a valid instrument** — owned by
[`../../docs/specs/l4-outer-loop.md`](../../docs/specs/l4-outer-loop.md) § Invariants.
