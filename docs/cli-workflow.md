# CLI Campaign Workflow

The CLI campaign runner provides a terminal-based interface for HITL (human-in-the-loop) prompt optimization. Each subcommand persists its output to `SessionStore`, so progress survives interrupts and the workflow can be resumed at any step.

```bash
python -m promptpotter.cli.campaign_runner <subcommand> [options]
```

---

## Subcommand Reference

### Subcommand Sequence

```
init ──→ [task-context] ──→ [scan] ──→ [scan-results] ──→ optimize ──→ results ──→ export
```

Steps in brackets are optional. Minimum viable workflow: `init` then `optimize --auto`.

| Step | Command | What it does | Reads from |
|------|---------|-------------|------------|
| 1 | `init` | Connect to backend, configure pipeline (baseline deferred) | Config file |
| 2 | `task-context` | Decompose a task description into structured domain context | init_params |
| 3 | `scan` | Run sensitivity scan over parameter variants | init_params, config |
| 4 | `scan-results` | Seed campaign from scan winner | scan_results |
| 5 | `optimize` | Run L1/L2/L3 optimization cycle | All above |
| 6 | `results` | Show summary, optionally save winner to backend | Campaign cycles |
| 7 | `export` | Generate supplemental materials or JSON | Campaign data |

### init

```bash
python -m promptpotter.cli.campaign_runner init \
    --backend-url http://127.0.0.1:8000 \
    --config configs/datasets/lca-termnorm/campaign.json
```

Connects to the backend, fetches pipeline schema via `GET /pipeline`, applies `exclude_nodes` and `pipeline_overrides` from config. Baseline is skipped by default (`--skip-baseline`) — the optimizer evaluates it automatically before the first round. Omit `--skip-baseline` only when you have substantial historical data and want an explicit baseline comparison before starting.

Produces: `session.json` with `pipeline_params`, `init_params`, `phase: "init"`.

### task-context

```bash
python -m promptpotter.cli.campaign_runner task-context \
    --task-file description.txt
```

Passes a plain-text task description through LLM decomposition to produce structured `task_context` fields (problem_description, success_criteria, domain_vocabulary). These feed L2 context refinement.

### scan

```bash
python -m promptpotter.cli.campaign_runner scan \
    --variants-file variants.json
```

Runs a one-axis-at-a-time (OAT) sensitivity scan. Each parameter axis is varied independently while others stay at baseline. Identifies which axes have the most impact on accuracy.

### scan-results

```bash
python -m promptpotter.cli.campaign_runner scan-results
```

Displays scan results leaderboard and seeds the optimization campaign with the best-performing variant as starting point.

### optimize

Three modes for different workflows:

```bash
# Generate candidates, then pause for human review
python -m promptpotter.cli.campaign_runner optimize --round

# Resume evaluation of reviewed candidates
python -m promptpotter.cli.campaign_runner optimize --evaluate

# Full autonomous loop (L1→L2→L3 until convergence)
python -m promptpotter.cli.campaign_runner optimize --auto
```

The `--round` / `--evaluate` split enables HITL: after `--round`, candidates are persisted to `round_NNNN_candidates.json`. You can inspect, edit, or approve them before running `--evaluate`.

### results

```bash
python -m promptpotter.cli.campaign_runner results
python -m promptpotter.cli.campaign_runner results --save  # save winner to backend
```

### control

```bash
# Pause a running campaign
python -m promptpotter.cli.campaign_runner control --pause

# Resume a paused campaign
python -m promptpotter.cli.campaign_runner control --resume

# Stop a running campaign
python -m promptpotter.cli.campaign_runner control --stop
```

Bidirectional control via `campaign_state.json`. You can also edit the file directly: set `control.requested_state` to `"pause"`, `"resume"`, or `"stop"`.

---

## Export Commands

Generate paper-ready supplemental materials from completed campaigns:

```bash
# Supplemental materials as markdown (tables, CI, significance, reproducibility)
python -m promptpotter.cli.export_results supplemental \
    --backend-id local --output supplemental.md

# Structured JSON for paper repositories
python -m promptpotter.cli.export_results json \
    --backend-id local --output paper_results.json

# Export specific campaigns only
python -m promptpotter.cli.export_results supplemental \
    --backend-id local \
    --campaigns campaign_001,campaign_002 \
    --output supplemental.md
```

See [`docs/benchmarks.md`](benchmarks.md) for the full benchmark methodology and result table format.

---

## Worked Example

A complete workflow from initialization to export:

```bash
# 1. Initialize session against a running backend
python -m promptpotter.cli.campaign_runner init \
    --backend-url http://127.0.0.1:8000 \
    --config configs/datasets/lca-termnorm/campaign.json

# 2. (Optional) Add domain context
python -m promptpotter.cli.campaign_runner task-context \
    --task-file my_task_description.txt

# 3. (Optional) Run sensitivity scan to find impactful axes
python -m promptpotter.cli.campaign_runner scan \
    --variants-file configs/variants.json

# 4. (Optional) Seed campaign from scan winner
python -m promptpotter.cli.campaign_runner scan-results

# 5. Run optimization (autonomous mode)
python -m promptpotter.cli.campaign_runner optimize --auto

# 6. View results
python -m promptpotter.cli.campaign_runner results

# 7. Export for paper
python -m promptpotter.cli.export_results supplemental \
    --backend-id local --output supplemental.md
```

---

## Pipeline Params Threading

`configure_pipeline(svc, campaign_config)` is the single source of truth for pipeline configuration. It applies `exclude_nodes`, `pipeline_overrides`, and returns `pipeline_params` with an active `steps` list.

This `pipeline_params` dict flows to every eval call:
- `init` — baseline eval uses the configured pipeline (skipped by default, runs automatically before first optimize round)
- `optimize` — each candidate is evaluated with the configured pipeline
- `scan` — builds per-variant pipeline_params (one axis changed at a time)

If `pipeline_params` is `None`, the backend runs the full pipeline including excluded nodes.

---

## Session Directory

All session state lives under `{backend_id}/sessions/{session_id}/`:

| File | Updated | Content |
|------|---------|---------|
| `session.json` | Each phase transition | Config, phase, pipeline_params, cycle_id, best_accuracy |
| `campaign_state.json` | Every optimization event | Live state: round, baseline, best, candidates, counters |
| `campaign_output.log` | Append per eval query | Raw eval output (ANSI-stripped) |
| `campaign_log.md` | End of each round | Structured markdown report |
| `scan_results.json` | After scan completes | scan_df + axis_profiles |

### campaign_state.json

Overwritten on every event during optimization. Carries counters across cycles via `resume_from`.

Key fields: `workflow`, `phase`, `round`, `baseline`, `best`, `cycle_id`, `rounds_completed`, `total_queries_evaluated`, `total_backend_calls`, `cache_hit_rate`, `degraded_count`, `current_queries[]`, `round_candidates[]`, `last_round{}`.

Accumulators reset: `current_queries` per candidate, `round_candidates` per round, `degraded_count` per round.

---

## Interrupt Handling

The CLI uses a signal-flag pattern for graceful interrupts:

- **First Ctrl+C** — finishes the in-flight backend call, saves all completed work, exits cleanly
- **Second Ctrl+C** — force-quits immediately

No completed work is ever discarded. On resume, the session picks up from the last persisted state.

After any interrupted run, check for orphan processes:

```bash
ps aux | grep python         # Linux/Mac
tasklist | findstr python     # Windows
```
