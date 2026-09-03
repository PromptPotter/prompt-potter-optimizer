# harbor-tbench-regex-log — one Terminal-Bench 2.0 task, run as an agent episode

**The first dataset on the `harbor` connector.** Its job is to establish that a containerized
agent episode is a measurable cell in this loop — not to publish a competitive number.

## Source and split

- **Benchmark:** Terminal-Bench 2.0 (Laude Institute / Stanford), the same team that builds
  Harbor.
- **Slice:** ONE task (`regex-log`) selected from `terminal-bench-sample` v2.0, upstream's own
  published sample of the 89-task set. Chosen because it is pure text work: the rest of that
  sample builds Cython extensions, boots qemu or compiles SQLite with coverage — minutes of
  container build before a token is spent. **Taking more tasks means a new dataset name**, not an
  edit here: `sample_id` is scoped by dataset name and the row text is not in the key.
- **Where the roster lives:** Harbor's registry, not this directory. `harbor_tasks.yaml` commits
  the dataset name and version; the connector resolves the task list and Harbor fetches the task
  bytes at the commits it pins. Nothing about the benchmark is vendored here, so there is no copy
  to drift — and upstream repinning a task changes the instrument fingerprint rather than quietly
  re-serving old rows. `harbor dataset list` is the roster.
- **Sample shape:** no rows and no labels. One "sample" is a task id; the cell is the whole
  episode. `Sample.ground_truth` is `None` — there is nothing for an answer to match, because
  the grade comes from the machine's final state.

## Verifier

Each task ships its own `tests/test.sh`, written by the benchmark authors, which inspects the
container and writes a reward to `/logs/verifier/reward.txt`. **Deterministic, and not an LLM
judge** — the bar `dataset-selection-rationale.md` sets, and the ground on which `AA-LCR` and
SealQA were previously rejected. We did not write, port or reimplement any of it, which is why
parity with the published benchmark is not a claim this directory has to defend.

## Two things this dataset is NOT

- **Not a headline number, and not even screening-grade.** One cell is far below the ≥200-sample
  bar in `docs/research/benchmarks.md`. With a single sample per candidate the δ ruler and PoBB
  elimination are degenerate by construction: this dataset answers "does the loop drive an agent
  through Harbor at all", and nothing about which prompt is better. The scale-up needs no new adapter —
  `terminal-bench` (89) and `terminal-bench-pro` (200) are published in the same registry and
  differ only by the task list in `harbor_tasks.yaml` — but it is a new dataset directory under a
  new name, never a re-cut of this one.
- **Not comparable to any published Terminal-Bench leaderboard entry.** Those pair a specific
  agent with a specific model; ours pins `terminus-2` and one model, and the thing under test is
  the injected skill. Compare against the origin skill measured here, and nothing else.

## Still owed before any number leaves this repo

Per `dataset-selection-rationale.md` § Adding a dataset:

- Confirm upstream's own evaluation protocol for the sample slice — trials per task, and whether
  a single run per task is the published convention or a mean over repeats. **Unconfirmed**;
  until it is, treat every reading here as a self-measured lift over our own origin.
- Record the measured cost of one cell. `campaign.yaml` deliberately ships **no `per_cell` cost
  term** because no episode has been priced yet, and an invented anchor grades every arm against
  a number nobody read.

## The lever

`prompts/default.yaml` is the origin skill — generic on purpose. It names no task, because a
skill that did would be the benchmark leaking into the baseline the lift is read against. What
varies across arms is the standing operating instruction; what is held fixed is the frontmatter
that makes the agent open it at all (`connectors/harbor.py`, `_SKILL_DESCRIPTION` — a candidate
free to write its own could win by hiding its own skill).
