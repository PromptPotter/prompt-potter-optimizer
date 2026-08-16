# Prompt optimizability — can this dataset separate candidates at all?

A dataset is optimizable when changing the prompt moves the score more than re-running the same
prompt does. That is separability, not level: the origin-*score* bar and its two failure modes are
owned by [`../research/benchmarks.md`](../research/benchmarks.md) § The admission bar.

Source: **`p1`, [arXiv:2604.08801](https://arxiv.org/abs/2604.08801)** — its *system prompt* /
*user prompt* are our **candidate** / **sample**.

## The decomposition

Per-sample score variance splits in two:

- **Within-candidate** — one candidate, one sample, re-run. Generation stochasticity. Noise.
- **Between-candidate** — hold the sample, vary the candidate. The signal an optimizer climbs.

Optimization fails wherever the first dominates, and it fails *silently*: a round whose samples
carry no between-candidate variance cannot rank its arms however good the proposals are, so an
unusable instrument reads as a stalled optimizer. That is the whole reason to screen.

L4 names the same property **informative width**
([`../specs/l4-outer-loop.md`](../specs/l4-outer-loop.md) § Finish line, item 1) — what one panel
cell is worth. Between-candidate variance is the measurement underneath it.

## Both halves already have an instrument

- **Within** → the `noise-floor` verb, but only where a repeat is a real second call. Above the
  sample level a re-ask replays the content-addressed caches and its spread is zero by
  construction, which is why the L4 panel carries no within-cell term.
- **Between** → Rasch discrimination `aₛ` ([`verdict-resolution.md`](verdict-resolution.md)): a
  high-`aₛ` item separates abilities by construction. So `p1` is published justification for
  `aₛ`-weighted subset selection — the open remainder in
  [`../specs/roadmap.md`](../specs/roadmap.md) § Fitness comparability, where
  `select_round_subset` is still 1PL.

Screen a whole dataset before wiring it, then pick the subset inside one; the second use is the
[hard-sample leaderboard](exploration-exploitation.md).

## Two protocol notes from the same source

- **Few high-separation samples beat many.** `p1` optimizes on a small high-variance subset and
  generalizes, so separability is the axis that buys signal — not subset size.
- **Prefer a temporally later edition as the held-out set** (one year's AIME → the next):
  transfer rather than fit, contamination-resistant by construction. Applicable wherever a
  benchmark ships dated editions; not a general substitute for a canonical split.
