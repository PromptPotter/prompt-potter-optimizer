# Compaction — a group of tasks

**Parked, and deliberately out of the way.** A side project with no schedule: it is not on the
roadmap and no rule file links here, so it is read only when someone is working on this group.
Several tasks share one shape, and this page defines the shape and how any of them would be
measured. No member has rows and no number below is measured. A member that gets rows becomes a
dataset of its own (`promptpotter/assets/datasets/compaction/{member}-vN/`).

## What makes a task a compaction task

An **information artifact** goes in, is processed into a smaller one, and the question is whether
the processed artifact still holds the information a downstream task needs. The downstream task can
be solved from the original; the thing under test is only whether it can still be solved
afterwards. It reads as information retrieval with one addition: what is retrieved must be enough
*on its own*.

Prompt and skill optimization score an output against a label. Here the output has no label; it is
scored by what another step can do with it.

## Members

| Member | Artifact in | Compacted by, today | Downstream task that must still work | Material today |
|---|---|---|---|---|
| **session-read** | a page, log, dump or set of files, plus a question | one prompt on `qwen/qwen3.8-flash`, in an operator-local script | the coding session answers from the returned lines | traces collect on every call, see [`session-read.md`](session-read.md) |
| **archive** | a finished run's measurement archive | a code rule, `compact-archive` (`application/maintenance/archive_maintenance.py::compact_measurement_archive`) | replaying a searchpoint and re-grading it with the dataset's ruler | none collected |
| **cycle-ledger** | a cycle's ledger | a code rule (`application/maintenance/restamp.py::compact_cycle_ledgers`) | reading the ledger back and resuming the cycle | none collected |

The first member is compacted by a prompt, the other two by code. For those two the thing a search
would move is the keep rule, and the downstream check is exact: the run replays and re-grades, or
it does not.

These are atomic steps. Once enough real cases have collected they could be optimized here one at
a time; for session-read today that would climb too slowly to be worth a campaign.

## Pipeline, in the session-read form

```
source + question ─▶ compactor ─▶ compaction ─▶ downstream solver ─▶ answer ─▶ matcher vs gold
                     (searched)                 (fixed)
```

- **Compactor**: one system prompt on a cheap model. This is the node the optimizer mutates.
- **Downstream solver**: a fixed model and prompt that answers the question from the compaction
  ALONE. It is part of the instrument, pinned outside the search exactly as a judge is; a campaign
  that could move it would be tuning the ruler.

Two compactor output shapes are compared on the same cells: **extract**, where the compactor names
line ranges and code quotes them from the source, and **digest**, where it writes the support in
its own words.

## Sample shape

`(source, question, gold answer)`. The source is many times longer than the passage that answers.

## Scoring

A cell banks two terms, and the dataset declares two anchors:

| | What it is | Read as |
|---|---|---|
| `answered` | the solver is right, given only the compaction | the score |
| `kept` | compaction tokens ÷ source tokens | the price |
| **full** anchor | the same solver given the whole source | the ceiling: what reading everything buys |
| **blind** anchor | the same solver given the question and nothing else | the floor: what it knew or guessed unread |

The headline is **retained = (`answered` − blind) ÷ (full − blind), stated at its `kept`**: how
much of what reading buys survives the compaction, and what it cost. The two terms are reported as
a frontier and folded into one number only by a formula the campaign names, since "smaller" and
"still right" trade against each other and a single score hides which one moved.

## Admission

A dataset whose full and blind anchors sit close measures nothing: either the solver already knows
the answers or the source does not contain them. A sample the blind solver gets right is dropped at
cutting time. This is the constant-answer floor of
[`dataset-selection-rationale.md`](../../../../docs/operations/dataset-selection-rationale.md)
§ Selection criteria, criterion 1b, in this task's form.

## Two degenerate optima, and both must lose

- **Copy everything** scores full at `kept` = 1, so `kept` is never optional in the formula.
- **Answer instead of compacting** makes a digest that is just the answer: the solver copies it,
  `answered` is high and `kept` is tiny, and the task has quietly become question answering on the
  cheap model with nothing a solver can check. The extract shape cannot do this by construction,
  which makes it the reference shape and the digest the arm that must beat it.

## A cheaper screen, not a substitute

*Span recall*, whether the gold string appears in the compaction, needs no solver and no second
call. It holds only for the extract shape, it counts a passage as found whether or not a solver
could use it, and it fails a correct paraphrase. It screens a prompt for a few cents before a
campaign funds the solver.

## Where rows will come from

Questions a compactor failed in real use are the honest hard set and arrive a few at a time. A
canonical split comes from a long-source question-answering benchmark; HotpotQA's distractor
setting, queued in [`benchmarks.md`](../../../../docs/research/benchmarks.md)
§ Order of use, is compaction by another name: two paragraphs that answer among eight that do not.

## Open before a first cut

- **How the solver is wired**: as a second node in a member's pipeline, or as a judge banking
  `answered` through [`judges/CLAUDE.md`](../../../../promptpotter/judges/CLAUDE.md).
  Either way the two anchors are two more measured configurations of the same solver.
- **The first rows.** Compute both anchors on them before a single round is paid for.
