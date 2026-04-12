# CLI Campaign Workflow

The CLI provides a terminal-based interface for HITL (human-in-the-loop) prompt optimization. Each subcommand persists its output to `SessionStore`, so progress survives interrupts and the workflow can be resumed at any step.

```bash
python -m promptpotter <subcommand> [options]
```

## Active Session

PromptPotter remembers which campaign you're working on via an **active session pointer** at `.promptpotter/active_session.json`. This stores `{backend_id, session_id}` — like a browser's active tab.

- **`init`** creates a new session and sets it as active (overwrites the pointer).
- **Every other command** (`optimize`, `show-status`, `show-results`, `set-task`, `scan`, `control`) operates on the active session automatically — no flags needed.
- **`--session <id>`** overrides the active pointer for a single command.
- **`--backend-id`** is auto-derived from `dataset_name` in the config when not explicitly passed (so `init --config datasets/aime_2025/campaign.json` correctly uses `backend_id=aime_2025`, not the default `local`).

To resume a campaign: just run `python -m promptpotter optimize`. No need to `init` again — `init` is only for starting a **new** campaign.

---

## Subcommand Reference

### Subcommand Sequence

```
init ──→ [set-task] ──→ [scan] ──→ [show-scan] ──→ optimize ──→ show-results ──→ export
```

Steps in brackets are optional. Minimum viable workflow: `init` then `optimize`.

| Step | Command | What it does | Reads from |
|------|---------|-------------|------------|
| 1 | `init` | Connect to backend, configure pipeline (baseline deferred) | Config file |
| 2 | `set-task` | Decompose a task description into structured domain context | init_params |
| 3 | `scan` | Run sensitivity scan over parameter variants | init_params, config |
| 4 | `show-scan` | Seed campaign from scan winner | scan_results |
| 5 | `optimize` | Run L1/L2/L3 optimization cycle | All above |
| 6 | `show-results` | Show summary, optionally save winner to backend | Campaign cycles |
| 7 | `export` | Generate supplemental materials or JSON | Campaign data |

### init

```bash
python -m promptpotter init \
    --backend-url http://127.0.0.1:8000 \
    --config datasets/lca-termnorm/campaign.json
```

Connects to the backend, fetches pipeline schema via `GET /pipeline`, applies `exclude_nodes` and `pipeline_overrides` from config. Baseline is skipped by default (`--skip-baseline`) — the optimizer evaluates it automatically before the first round. Omit `--skip-baseline` only when you have substantial historical data and want an explicit baseline comparison before starting.

Produces: `session.json` with `pipeline_params`, `init_params`, `phase: "init"`.

### set-task

```bash
python -m promptpotter set-task \
    --task-file description.txt
```

Passes a plain-text task description through LLM decomposition to produce structured `task_context` fields (problem_description, success_criteria, domain_vocabulary). These feed L2 context refinement.

### scan

```bash
python -m promptpotter scan \
    --variants-file variants.json
```

Runs a one-axis-at-a-time (OAT) sensitivity scan. Each parameter axis is varied independently while others stay at baseline. Identifies which axes have the most impact on accuracy.

### show-scan

```bash
python -m promptpotter show-scan
```

Displays scan results leaderboard and seeds the optimization campaign with the best-performing variant as starting point.

### optimize

Three modes for different workflows:

```bash
# Generate candidates, then pause for human review
python -m promptpotter optimize --round

# Resume evaluation of reviewed candidates
python -m promptpotter optimize --evaluate

# Full autonomous loop (L1→L2→L3 until convergence — default)
python -m promptpotter optimize
```

The `--round` / `--evaluate` split enables HITL: after `--round`, candidates are persisted to `round_NNNN_candidates.json`. You can inspect, edit, or approve them before running `--evaluate`.

### show-results

```bash
python -m promptpotter show-results
python -m promptpotter show-results --save  # save winner to backend
```

### control

```bash
# Pause a running campaign
python -m promptpotter control --pause

# Resume a paused campaign
python -m promptpotter control --resume

# Stop a running campaign
python -m promptpotter control --stop
```

Bidirectional control lives in `campaign_control.json` (a sibling of `campaign_state.json`). You can also edit it directly: set `requested_state` to `"pause"`, `"resume"`, or `"stop"`.

---

## Export Commands

Generate paper-ready supplemental materials from completed campaigns:

```bash
# Supplemental materials as markdown (tables, CI, significance, reproducibility)
python -m promptpotter export supplemental \
    --backend-id local --output supplemental.md

# Structured JSON for paper repositories
python -m promptpotter export json \
    --backend-id local --output paper_results.json

# Export specific campaigns only
python -m promptpotter export supplemental \
    --backend-id local \
    --campaigns campaign_001,campaign_002 \
    --output supplemental.md
```

See [`benchmarks.md`](../research/benchmarks.md) for the full benchmark methodology and result table format.

---

## Worked Example

A complete workflow from initialization to export:

```bash
# 1. Initialize session against a running backend
python -m promptpotter init \
    --backend-url http://127.0.0.1:8000 \
    --config datasets/lca-termnorm/campaign.json

# 2. (Optional) Add domain context
python -m promptpotter set-task \
    --task-file my_task_description.txt

# 3. (Optional) Run sensitivity scan to find impactful axes
python -m promptpotter scan \
    --variants-file configs/variants.json

# 4. (Optional) Seed campaign from scan winner
python -m promptpotter show-scan

# 5. Run optimization (full loop — default)
python -m promptpotter optimize

# 6. View results
python -m promptpotter show-results

# 7. Export for paper
python -m promptpotter export supplemental \
    --backend-id local --output supplemental.md
```

---

## Pipeline Params Threading

`configure_pipeline(svc, campaign_config)` applies `exclude_nodes` and `pipeline_overrides`, returning `pipeline_params`. Node ordering comes from `PipelineSchema` (pre-filtered to active nodes); `pipeline_params["steps"]` is only read at wire-format boundaries.

This `pipeline_params` dict flows to every eval call:
- `init` — baseline eval uses the configured pipeline (skipped by default, runs automatically before first optimize round)
- `optimize` — each candidate is evaluated with the configured pipeline
- `scan` — builds per-variant pipeline_params (one axis changed at a time)

If `pipeline_params` is `None`, the backend runs the full pipeline including excluded nodes.

---

## Session Directory

The active session pointer lives at `.promptpotter/active_session.json` (see [Active Session](#active-session) above). Per-session state lives under `.promptpotter/projects/{backend_id}/sessions/{session_id}/`:

| File | Updated | Content |
|------|---------|---------|
| `session.json` | Each phase transition | Config, phase, pipeline_params, cycle_id, best_accuracy |
| `campaign_state.json` | Every optimization event | Live state: round, baseline, best, candidates, counters |
| `campaign_output.log` | Append per eval query | Raw eval output (ANSI-stripped) |
| `campaign_log.md` | End of each round | Structured markdown report |
| `scan_results.json` | After scan completes | scan_df + axis_profiles |

### campaign_state.json

Scalar-only live dashboard. Atomically rewritten on every event during optimization. Carries display counters across cycles via `resume_from`.

Key fields: `workflow`, `phase`, `round`, `baseline`, `best`, `cycle_id`, `rounds_completed`, `total_queries_scored`, `total_backend_calls`, `cache_hit_rate`, `hit_rate`, `eta_s`, `candidate`, `query`. For per-query / per-candidate / per-round detail, read `campaign_output.log` or `rounds/round_NNN.json` directly.

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
