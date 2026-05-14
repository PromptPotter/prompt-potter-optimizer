# PromptPotter — Self-optimization trace-replay fixture

## Status

Tiny **golden trace-replay fixture** for the L4 self-optimization closure.
Pinned in M10-cleanup §1 so the data shape exists on disk before its
consumer (the M11 PromptPotter-as-backend connector) is built.

Sibling shape to `datasets/gsm8k/` and `datasets/lca-termnorm/` for the
config files; the **rows themselves** live in
`golden_traces.json` and conform to the row contract emitted by
`promptpotter/application/datasets.py::load_potter_traces`.

## What this dataset IS

PromptPotter optimizes the L1 / L1_CRITIQUE / L2 / L3 **meta-prompts**
themselves — the four optimizer LLM calls described by
`promptpotter/application/optimization/optimizer_pipeline.json`. Each
sample is one round-to-round transition extracted from a previously-run
campaign: the optimizer state at round N (`round_context`), the change
the optimizer made (`next_brief`), and the accuracy delta that followed
(`score_delta`).

## Init Flags (M11 connector — not wired yet)

```
--backend-url http://127.0.0.1:8000      # PromptPotter-as-backend shim
--backend-id promptpotter
--dataset-name promptpotter
--config datasets/promptpotter/campaign.json
```

The shim that exposes `POST /match` over L1/L2/L3 is the M11
deliverable. Until then this directory is read-only fixture data —
the M10 commit lands the rows so the M11 connector PR has a concrete
shape to integrate against.

## Data

- `golden_traces.json` — list of rows; each is one
  `(round_context → next_brief → score_delta)` transition. Two rows:
  one L1→L1 transition, one L1→L2 escalation transition.
- `task_description.md` — minimal framing: optimize the optimizer.
- The fixture deliberately does NOT pin `pipeline.json` /
  `campaign.json` / `prompts/` — those are M11 connector
  deliverables. The spec ask is only the trace shape.

## Scoring

`score_delta` is the per-row label: `accuracy(round_{N+1}) -
accuracy(round_N)` from the source campaign. The M11 connector chooses
the scoring formula in its own `campaign.json`; common choices are
`score_delta` directly (regress on the lift) or `1.0 if score_delta > 0
else 0.0` (classify on improvement).

## Provenance

The two rows are **synthetic but shape-faithful** — handwritten to
match every field that `_build_row` emits, derived from the structure
of real prior campaigns. Not pulled from any single live campaign so
this fixture stays stable across PromptPotter releases.
