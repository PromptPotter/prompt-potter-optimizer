# PromptPotter-self — Optimizer-of-the-Optimizer

## What this is

A self-referential dataset: outer PromptPotter optimizes the **meta-prompts**
that drive the inner PromptPotter cycle. Connector boundary:
`promptpotter/connectors/promptpotter.py`. Spec:
[`docs/specs/roadmap.md`](../../docs/specs/roadmap.md) § Connectors + L4 inner-cycle execution.
Concept: [`docs/concepts/optimizer-of-the-optimizer.md`](../../docs/concepts/optimizer-of-the-optimizer.md).

Each outer "sample" runs an inner PromptPotter campaign on a small GSM8K
subset and reports three proxy metrics:

- `first_round_delta` — score after inner round 1 minus inner origin
- `after_N_rounds_delta` — score after N inner rounds minus inner origin
- `rounds_to_N` — number of rounds to reach `inner_tasks.json::target_score`

The outer scoring formula in `campaign.json::scoring` composes these.
Operators iterate on the formula as evidence accumulates — there's no
single "right" weighting; the proxies serve different stages of the
development → calibration → publication arc.

## Status

**Architectural skeleton landed; inner-cycle execution NOT yet wired.** The
connector loads, the schema validates, the dashboard renders the cycle.
The actual "run an inner cycle" path is the follow-up — see
`promptpotter/connectors/CLAUDE.md` § Inner-cycle execution.

## Files

- `pipeline.json` — meta-prompt node schema (4 nodes × 6 template fields)
- `campaign.json` — outer campaign config with composite scoring formula
- `inner_tasks.json` — inner-benchmark task list consumed by
  `extract_experiment` (currently 4 GSM8K-small seeded tasks)
- `task_description.md` — outer L1 framing
- `prompts/` — outer meta-prompt overrides (none yet; outer cycle uses
  `datasets/_optimizer/pipeline.json` defaults)

## Run

```
python -m promptpotter new promptpotter-self \
  --backend-url http://127.0.0.1:8001/inner    # placeholder until execution wired
```

Until inner-cycle execution lands, `new` will load the connector and
fail at the first inner match request with a clear `NotImplementedError`
referencing the spec.

## Cost realism

Each outer sample is at minimum a partial inner cycle. With this dataset's
defaults (`n_samples_per_inner_round: 10`, `max_inner_rounds: 3`,
`n_variants: 3`, `sp_budget_ttest: 4`), expect order-of-minutes per outer
round. Publication-quality runs will scale up — see the spec's cost-realism
section.
