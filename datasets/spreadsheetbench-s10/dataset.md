# spreadsheetbench-s10

Ten SpreadsheetBench tasks, run as containerized agent episodes through Harbor.

## Source and pin

- **Benchmark:** SpreadsheetBench (Ma et al., NeurIPS 2024 Datasets & Benchmarks). HF
  `KAKA22/SpreadsheetBench`, **CC BY-SA 4.0** — copyleft, so anything we redistribute *from the
  rows* carries the licence. Nothing is redistributed here: this directory commits a name, a
  version and ten ids.
- **Adapter:** Harbor's own `spreadsheetbench-verified@1.0`, 400 tasks. We authored no adapter and
  claim no parity — the container, the instruction and the verifier are all upstream's, pinned per
  task to a git commit that the connector resolves at init
  (`connectors/harbor.py::_registry_tasks`).
- **The cut:** the first ten of the published roster, in published order. See
  `harbor_tasks.yaml`, which owns the ids and the argument for that count.

## Why this exists

**An instrument check, not a result.** `campaign.yaml` sets `max_rounds: 0` — measure the origin
and stop. The question is whether this model lands mid-range on these cells. A model that scores
0 everywhere pins every arm to a floor constant; one that scores 1 everywhere leaves a prompt
nothing to move. Either way no round can be won on evidence, and the previous harbor campaign
learned that the expensive way — a one-cell panel whose origin read 0, on a task the same prompt
had solved five minutes earlier.

Ten cells put roughly ±0.16 on a proportion. That is enough to tell "mid-range" from "pinned",
and nowhere near enough to report a score. **No number from this directory is a benchmark
result**, and a wider cut is a new dataset name rather than an edit here.

## Sample shape

One sample is one task id; there is no CSV. `Sample.ground_truth` is `None` — the cell carries no
label, because the verifier grades it. That is a declared state, not a missing value: it is what
keeps the round-health grade from reading `predicted == NO_RESULT` as a broken extraction
contract, and what routes the evidence panels away from a hit/miss contrast that would partition
nothing (`domain/scoring.py::is_verifier_graded`).

## Measured

| | |
|---|---|
| origin accuracy | *not yet measured — this screen is what fills it* |
| per-cell cost | one cell measured at **$0.0101** on `qwen/qwen3.5-9b:nitro` (9 turns, 47.6k in / 8.2k out) |
| per-cell wall clock | **254s** for that cell: 183s agent, 71s floor (container start, LibreOffice recalc, teardown) |

The floor is irreducible — it does not move with the model — so a faster or cheaper model buys
the agent half only. The model recorded in `pipeline.yaml` is the one this dataset measures on;
changing it changes what every row means, and the reason for the current pin belongs beside it
there.
