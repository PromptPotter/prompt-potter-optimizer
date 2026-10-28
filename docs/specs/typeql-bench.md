# TypeQL bench — TypeDB's query-generation benchmark under the optimizer

[`typedb/db-llm-bench`](https://github.com/typedb/db-llm-bench) (Apache-2.0) has a model write SQL,
Cypher or TypeQL against the Reactome pathway database; the query is **executed** and its result
compared with a known answer. TypeQL starts behind, catches up with the official skill and examples,
and overtakes once errors are fed back, because its type checker turns most wrong queries into a
visible error.

This spec is the plan for optimizing the TypeQL prompt there and handing the result back in a form
their tooling verifies. What is built lives in [`connectors/CLAUDE.md`](../../promptpotter/connectors/CLAUDE.md)
(the `dbllmbench` connector) and the `reactome-typeql-42` tenant definition's `README.md` (protocol,
published rows, cut, database setup).

## Decisions

1. **The harness is their binary**, built from a pinned upstream commit; nothing is ported.
   Rejected: Harbor (replaces their harness with an agent episode), a Python port (a second
   benchmark nobody else runs).
2. **The candidate is the head of their prompt** — the instruction lines and the TypeQL skill. The
   template's tail (schema, examples, output contract, question) is not a search axis.
3. **Fitness is accuracy after two retries**, with the first-attempt reading banked beside it from
   the same execution. The benchmark's finding rests on error feedback; two bounds what a miss
   costs. The reported comparison still reads zero, two and four.
4. **Development runs on a cheap model; the reported cells run on the operator's pick.** The
   published rows are Claude Sonnet 5 and DeepSeek V4 Pro; a number on any other model is a
   different benchmark.
5. **The database is ours to stand up**, locally or on TypeDB Cloud; the connector reads its
   address from the environment.

## The maturity gate

Before any spend on the reported model, all four hold, and then the operator picks the reported model:

- upstream's `verify` reproduces every reference query's expected value on the loaded store,
  through the same image the cells run;
- one origin cell banks all three retry readings, its turns, its tokens and its cost;
- the origin's spread on the development model has been read — pinned at 0 or 1 is an unusable
  instrument, and the remedy for 0 is a stronger development model, not a search;
- the reading is stable across a repeat (`noise-floor`).

## What is owed after the gate

- **The campaign** on the search split, supervised, on the operator's model.
- **The reported comparison:** origin against the selected candidate over all 42 questions at zero,
  two and four retries, by difficulty tier, with the visible-error and silently-wrong shares. Their
  published rows are quoted beside it, never pooled. The held-out bench questions are reported
  separately, because the other 28 were searched on.
- **Their own analysis on our output:** a cell's results file is in their format; merged per
  question it runs through their `analysis/accuracy_by_variation.py` and `failure_modes.py`
  unchanged — the check that our accounting and theirs agree.
- **The drop-in:** the selected candidate as one skill file plus their template cut at its skill
  slot — two files their runner takes with no code change.

## Upstream changes worth offering

- A `repetitions:` config key in place of the `REPETITIONS` constant (`src/runner/src/lib.rs`):
  `connectors/resources/dbllmbench-repetitions.patch`.
- Their Claude provider caches only within one question, since the question shares the schema's
  block; a separate block for the stable head caches it across all 42.
- `data/reactome/typedb/load.sh` runs `typedb loader`/`console` with TLS on against a TLS-off
  compose server and fails with `InvalidContentType`; pass `--tls-disabled` to both.

## Parked

- **The tool-using agent** (TypeDB's MCP server via Harbor's agent config): a many-turn measurement
  not comparable to the published rows, so its own dataset. First when TypeDB work resumes.
- **Text2TypeQL** (`typedb-osi/text2typeql`) as generic TypeQL few-shot material.
