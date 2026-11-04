# TypeQL bench — TypeDB's query-generation benchmark under the optimizer

[`typedb/db-llm-bench`](https://github.com/typedb/db-llm-bench) (Apache-2.0) has a model write SQL, Cypher
or TypeQL against the Reactome pathway database; the query is **executed** and its result compared with a
known answer. What is built is the `dbllmbench` connector ([`connectors/CLAUDE.md`](../../promptpotter/connectors/CLAUDE.md))
and the `reactome-typeql-42` tenant definition's `README.md` (protocol, published rows, cut, database setup):
their harness as a pinned binary, the candidate is the head of their prompt, fitness is accuracy after two
retries with the first-attempt reading banked beside it. This page is what is owed.

## The maturity gate

Before any spend on the reported model, all four hold, then the operator picks the reported model:

- upstream's `verify` reproduces every reference query's expected value on the loaded store, through the
  same image the cells run;
- one origin cell banks all three retry readings, its turns, its tokens and its cost;
- the origin's spread on the development model has been read — pinned at 0 or 1 is an unusable
  instrument, and the remedy for 0 is a stronger development model, not a search;
- the reading is stable across a repeat (`noise-floor`).

Development runs on a cheap model; the published rows are Claude Sonnet 5 and DeepSeek V4 Pro, and a
number on any other model is a different benchmark.

## What is owed after the gate

- **The campaign** on the search split, supervised, on the operator's model.
- **The reported comparison:** origin against the selected candidate over all 42 questions at zero, two and
  four retries, by difficulty tier, with the visible-error and silently-wrong shares; their published rows
  quoted beside it, never pooled; the held-out bench questions reported separately.
- **Their own analysis on our output:** a cell's results file in their format runs through their
  `analysis/accuracy_by_variation.py` and `failure_modes.py` unchanged.
- **The drop-in:** the selected candidate as one skill file plus their template cut at its skill slot.

## Upstream changes worth offering

- `connectors/resources/dbllmbench-repetitions.patch`: a `repetitions:` config key in place of the
  `REPETITIONS` constant (`src/runner/src/lib.rs`).
- Their Claude provider caches only within one question; a separate block for the stable head caches it
  across all 42.
- `data/reactome/typedb/load.sh` runs `typedb loader`/`console` with TLS on against a TLS-off compose
  server and fails with `InvalidContentType`; pass `--tls-disabled` to both.

## Parked

- **The tool-using agent** (TypeDB's MCP server via Harbor's agent config): a many-turn measurement not
  comparable to the published rows, so its own dataset.
- **Text2TypeQL** (`typedb-osi/text2typeql`) as generic TypeQL few-shot material.
