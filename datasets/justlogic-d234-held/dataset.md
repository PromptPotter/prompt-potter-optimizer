# JustLogic d234, held — the same pools under a bench that resolves 0.05

[`justlogic-d234`](../justlogic-d234/dataset.md) with a wider bench and nothing else moved. The
task, the pin and the `:nitro` trade are that file's; this one states only the cut.

## The cut

The bank is `justlogic-d234`'s 600 rows at the same ids in the same order, followed by 900 rows
of the same depths that cut never drew (300 per depth, ids 600 onward), each marked `bench_only`.
Both halves are derived from the dataset NAME by `_load_justlogic_held`
(`promptpotter/application/datasets/loaders.py`).

`dataset_split` is `justlogic-d234`'s, value for value. A bench-only row is held out by
declaration and never ranked (`promptpotter/domain/bench.py::partition_bank`), so:

- **the search pool (520) and the demo pool (50) are `justlogic-d234`'s, key for key** — every
  cell measured there replays here under the same instrument;
- **the bench is 930 rows**: the 30 `justlogic-d234` holds out, plus the 900.

Per-sample history is kept under this name, so a δ fitted on `justlogic-d234` is not read here.
`justlogic-d234` stays the L4 inner instrument; this is the bench instrument for a head-to-head.

## Deviation from the authors' protocol

JustLogic's canonical test set is withheld, and HF `train` is its public training fold. The
loader's own train cut is 200 rows per depth of that fold; the 900 bench-only rows are drawn from
the rest of the same fold, the loader's `test` side. So the bench is held out from OUR search, by
construction, and is not the authors' test set: numbers here are not leaderboard-comparable.
