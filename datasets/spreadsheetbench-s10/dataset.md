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

## Step schema — `open → adhere`

**Fixed before a cell is bought, because the measurement identity folds it and retrofitting one
re-pays for every row** (`promptpotter/judges/CLAUDE.md` § The step schema). The obligation is
`../../docs/operations/dataset-selection-rationale.md` § Adding a dataset step 2b: this task is
turn-structured, so the schema is named now and each step banks its own term.

| step | graded by | what it separates |
|---|---|---|
| open | `connectors/harbor.py::_skill_opened` — no model call | did the candidate's prompt reach the model at all |
| adhere | the task's own verifier, as `env_reward` | did the episode do the job |

**Not `retrieve → ground → answer`.** That schema is for a search-augmented task and there is
nothing here to retrieve; coining it on this panel would be a schema that is wrong rather than
merely new. And the ANSWER step needs no judge on a verifier-graded backend — SpreadsheetBench
recalculates the workbook and compares cell by cell, which is a stronger grader than a rubric.

The `open` step exists because on this backend **injection is not consumption**: the prompt is
written into the container as `SKILL.md` and the agent is shown only its frontmatter, so an episode
that never opens the file ran as no-skill. A round of those is arms-all-identical and reports a tie
it never measured. `campaign.yaml::scoring.per_cell` carries the term at a floored weight, and
`fitness` is left alone so a right-but-unopened cell stays a HIT.

## Measured

**The first reading this panel has ever taken with the candidate's prompt actually delivered**
(2026-09-12, 10 cells, serial). Everything measured before it ran with the injected skill silently
dropped, so it measured a no-skill episode — see § Step schema.

| | post-fix | the earlier screen |
|---|---|---|
| skill opened | **10 / 10** | 0 / 4 re-measured |
| origin accuracy | **0.600** | 0.800 |
| objective mean | **0.492** | — (no `per_cell` then) |
| per-cell tokens | **56,844** median · 63,291 mean · 113,243 p90 | 29,924 median |
| per-cell wall clock | **181.8 s** median, 31.4 min for the panel | 172 s mean |
| per-cell cost | **$0.00146** median, $0.0184 for the panel | $0.00137 mean |

**Reading the skill makes this panel WORSE and roughly doubles the tokens**, and that is a finding
about the origin prompt rather than a defect: cell `109-21` flipped HIT→MISS once the prompt
arrived. Cost is bimodal by OUTCOME — every miss is a high-token cell (77k / 113k / 163k) against
every hit being lower — so an episode that flails is an episode that burns, which is a property a
prompt moves.

**Four cells of headroom, and that is still a verdict about the PANEL.** Under the admission bar
(`../../docs/research/benchmarks.md` § The admission bar), so this cut can carry an instrument
check and never a search. Widening is a new dataset name, never an edit here.

The container floor is irreducible — it does not move with the model — so a faster or cheaper model
buys the agent half only. The model recorded in `pipeline.yaml` is the one this dataset measures
on; changing it changes what every row means, and the reason for the current pin belongs beside it
there.
