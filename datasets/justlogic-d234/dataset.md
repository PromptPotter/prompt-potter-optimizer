# JustLogic d234 — the live L4 inner instrument

3-class deductive reasoning (Chen 2025, `michaelchenkj/JustLogic`, [arXiv 2501.14851](https://arxiv.org/abs/2501.14851)):
given a paragraph of logical premises and a claim, answer `TRUE` / `FALSE` / `Uncertain`. Synthetic
and knowledge-independent — no factual recall.

## The cut

An iid random mix of JustLogic depths 2, 3 and 4, interleaved before numbering, so any `n`-sample
prefix is an iid draw across d2/d3/d4. The depths, the per-depth count and the seed are all derived
from the dataset NAME by `_load_justlogic` / `justlogic_depths`
(`promptpotter/application/datasets/loaders.py`) — read them there; a new combination such as
`justlogic-d34` needs a dataset dir and no code.

**Each cut is a separate dataset name, never a re-cut of another** — owned by
[`../CLAUDE.md`](../CLAUDE.md) § Re-cutting a dataset needs a NEW name; here that means a
cross-cut comparison reads the keying difference, not the capability.

Two things no other file carries:

- **The authors' canonical test set is withheld** (leakage control), so numbers here are NOT
  leaderboard-comparable. HF `train` is the public training fold.
- **Mode-prediction baseline = 33%.** Per-depth label distribution is ~balanced, so a class bias the
  pipeline shows is a reasoning failure, not a label-skew artifact.

## Pinning

The model, provider and the `output_schema` that carries the answer enum live in `pipeline.yaml`;
the pin rationale in
[`../../docs/operations/dataset-reasoning-matrix.md`](../../docs/operations/dataset-reasoning-matrix.md).
Only `provider` is operator-locked — `model` **is** in `optimizer.param_keys`, and
`reasoning_effort` is pinned by `param_allowed_values` rather than by exclusion.

**The `:nitro` suffix is a deliberate speed trade.** Nitro routes each call to the fastest upstream,
so a `seed` buys nothing across stacks and is not set — inner-run noise is drawn fresh per arm
instead of cancelling as common random numbers in the paired (variant − origin) outer diff, raising
the bar every optimizer prompt verdict must clear. Panel size is the lever that buys
minimum-detectable-effect back. A true pin needs OpenRouter provider pass-through or a single-stack
provider — both operator calls, not loop decisions.
