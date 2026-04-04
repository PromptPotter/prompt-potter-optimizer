# CLI Campaign Workflow

## Subcommand Sequence

Each subcommand reconstructs services from session state, runs its step, and persists results.

| Step | Command | Produces | Reads from session |
|------|---------|----------|--------------------|
| 1 | `init --config campaign_config.json --run-baseline` | Session, pipeline_params, baseline eval | — |
| 2 | `task-context --task-file desc.txt` | Structured domain context | init_params |
| 3 | `scan --variants-file variants.json` | Sensitivity scan results | init_params, campaign_config |
| 4 | `scan-results` | Campaign seeded from scan winner | scan_results |
| 5 | `optimize --auto` | Optimization cycle (L1/L2/L3) | All above |
| 6 | `results [--save]` | Summary, optionally save winner | campaign cycles |

Steps 2-4 are optional. Minimum viable: `init` then `optimize --auto`.

## Pipeline Params Threading

`configure_pipeline(svc, campaign_config)` is the single source of truth for pipeline configuration. It applies `exclude_nodes`, `pipeline_overrides`, and returns `pipeline_params` with an active `steps` list.

This `pipeline_params` dict must flow to every eval:
- `init --run-baseline`: `prepare_eval_context(pipeline_params=...)` → `run_baseline_eval(pipeline_params=...)`
- `optimize`: `run_optimization_notebook(pipeline_params=...)` → `RunConfig` → `eval_search_point()`
- `scan`: `sensitivity_scan()` builds its own pipeline_params per variant

If `pipeline_params` is `None`, the backend runs the full pipeline including excluded nodes.

## Session Directory Layout

```
{backend_id}/sessions/{session_id}/
    session.json            — config, phase, pipeline_params, cycle_id, best_accuracy
    campaign_state.json     — live optimization state (overwritten per update)
    campaign_output.log     — append-only eval log, ANSI-stripped
    campaign_log.md         — structured markdown report
    scan_results.json       — scan_df + axis_profiles (if scan was run)
```

## campaign_state.json

Overwritten on every event during optimization. Carries counters across cycles via `resume_from`.

Key fields: `workflow`, `phase`, `round`, `baseline`, `best`, `cycle_id`, `rounds_completed`,
`total_queries_evaluated`, `total_backend_calls`, `cache_hit_rate`, `degraded_count`,
`current_queries[]`, `round_candidates[]`, `last_round{}`.

Accumulators reset on transitions: `current_queries` per candidate, `round_candidates` per round,
`degraded_count` per round.
