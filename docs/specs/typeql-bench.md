# TypeQL bench — TypeDB's query-generation benchmark under the optimizer

TypeDB published a benchmark in which a model writes SQL, Cypher or TypeQL against the Reactome
pathway database and the query is **executed** and its result compared with a known answer
([`typedb/db-llm-bench`](https://github.com/typedb/db-llm-bench), Apache-2.0). Their finding is
that TypeQL starts far behind, catches up once the model is given the official skill and examples,
and overtakes both once errors are fed back — because TypeQL's type checker turns most wrong
queries into a visible error.

This spec is the plan for optimizing the TypeQL prompt on that benchmark and handing the result
back in a form their own tooling verifies. What is built is described where it lives:
[`connectors/CLAUDE.md`](../../promptpotter/connectors/CLAUDE.md) (the `dbllmbench` connector) and
[`datasets/reactome-typeql-42/dataset.md`](../../datasets/reactome-typeql-42/dataset.md) (the
protocol, the published rows, the cut, how to stand the database up).

## Decisions

1. **The harness is their binary.** The retry loop and the scorer run as `db-llm-bench` built from
   a pinned upstream commit; nothing of the benchmark is ported. Rejected: Harbor (it replaces
   their harness with an agent episode), a Python port (a second benchmark nobody else runs).
2. **The candidate is the head of their prompt** — the instruction lines and the TypeQL skill.
   Their template's tail (schema, examples, output contract, question) is not a search axis.
3. **Fitness is accuracy after two retries**, with the first-attempt reading banked beside it
   from the same execution. Feeding the error back is what the benchmark's finding rests on, so
   the search is scored after the attempts, and two bounds what a miss can cost. The reported
   comparison still reads zero, two and four.
4. **Development runs on a cheap model; the reported cells run on the operator's pick.** The
   published rows are Claude Sonnet 5 and DeepSeek V4 Pro, and a number on any other model is a
   different benchmark.
5. **The database is ours to stand up**, locally or on TypeDB Cloud; the connector reads where it
   is from the environment.

## The maturity gate

Before any spend on the reported model, all four hold, and then the operator is asked which model
runs the reported cells:

- upstream's `verify` reproduces every reference query's expected value on the loaded store,
  through the same image the cells run;
- one origin cell banks all three retry readings, its turns, its tokens and its cost;
- the origin's spread on the development model has been read — an origin pinned at 0 or at 1 is an
  unusable instrument, and the remedy for the first is a stronger development model, not a search;
- the reading is stable across a repeat (`noise-floor`).

## What is owed after the gate

- **The campaign** on the search pool, supervised, on the operator's model.
- **The reported comparison:** origin against the selected candidate over all 42 questions at
  zero, two and four retries, by difficulty tier, with the visible-error and silently-wrong shares.
  Their published rows are quoted beside it and never pooled with it. The held-out bench questions
  are reported separately, because the other 28 were searched on.
- **Their own analysis on our output.** A cell's results file is in their format; merged per
  question it runs through their `analysis/accuracy_by_variation.py` and `failure_modes.py`
  unchanged, which is the check that our accounting and theirs agree.
- **The drop-in:** the selected candidate as one skill file plus their template cut at its skill
  slot — two files their runner takes with no code change.
- **The post** to the TypeDB Discord, written from the run's record after it finishes.

## Upstream changes worth offering

- `REPETITIONS` is a constant (`src/runner/src/lib.rs`). A `repetitions:` config key lets a
  search read one run per question and the report read many; the patch that adds it is
  `connectors/resources/dbllmbench-repetitions.patch`, written to be offered as it stands.
- Their Claude provider caches a prompt across the repetitions of ONE question, and its own
  comment says different questions never share a hit because the question sits in the block with
  the schema. Splitting the stable head into its own block would cache it across all 42.
- `data/reactome/typedb/load.sh` calls `typedb loader` and `typedb console` with TLS on, against a
  compose server that runs with TLS off, so the documented seed fails at its first pass with
  `received corrupt message of type InvalidContentType`. Passing `--tls-disabled` to both, or
  reading it from an env var beside `ADDRESS`, fixes it.

## Parked

- **The tool-using agent.** TypeDB ships an MCP server, and Harbor's agent config takes MCP
  servers. An agent that inspects the schema and runs queries before answering is a different
  measurement — many turns, not comparable to the published rows — so it is its own dataset with
  its own `dataset.md`. It is the first thing to do when TypeDB work resumes.
- **Text2TypeQL** (`typedb-osi/text2typeql`) as a source of generic TypeQL few-shot material.
