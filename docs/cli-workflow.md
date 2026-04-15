# CLI Campaign Workflow

The CLI provides a terminal-based interface for HITL (human-in-the-loop) prompt optimization. Each subcommand persists its output to `SessionStore`, so progress survives interrupts and the workflow can be resumed at any step.

```bash
python -m promptpotter <subcommand> [options]
```

## Active Session

PromptPotter remembers which campaign you're working on via an **active session pointer** at `.promptpotter/active_session.json`. This stores `{backend_id, session_id}` — like a browser's active tab.

- **`init`** creates a new session and sets it as active (overwrites the pointer).
- **Every other command** (`optimize`, `show-status`, `show-results`, `set-task`, `recon`, `control`) operates on the active session automatically — no flags needed.
- **`--session <id>`** overrides the active pointer for a single command.
- **`--backend-id`** is auto-derived from `dataset_name` in the config when not explicitly passed (so `init --config datasets/aime_2025/campaign.json` correctly uses `backend_id=aime_2025`, not the default `local`).

To resume a campaign: just run `python -m promptpotter optimize`. No need to `init` again — `init` is only for starting a **new** campaign.

---

## Subcommand Reference

### Subcommand Sequence

```
init ──→ [set-task] ──→ [recon] ──→ [show-recon] ──→ optimize ──→ show-results ──→ export
```

Steps in brackets are optional. Minimum viable workflow: `init` then `optimize`.

| Step | Command | What it does | Reads from |
|------|---------|-------------|------------|
| 1 | `init` | Connect to backend, configure pipeline (baseline deferred) | Config file |
| 2 | `set-task` | Decompose a task description into structured domain context | init_params |
| 3 | `recon` | Run sensitivity scan (recon pass) over parameter variants | init_params, config |
| 4 | `show-recon` | Seed campaign from recon winner | recon_results |
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

**Init flags**:

| Flag | Purpose |
|---|---|
| `--backend-url` | Backend service URL (default from settings) |
| `--backend-id` | Override backend id (auto-derived from `dataset_name` otherwise) |
| `--dataset-name` | Override dataset name from config |
| `--config` | Campaign config JSON file |
| `--skip-baseline` | Skip explicit baseline eval (default — auto-baseline runs before round 1) |

Forking a campaign is not done here — see `optimize --from` below. `init` handles registration/setup only.

### set-task

```bash
python -m promptpotter set-task \
    --task-file description.txt
```

Passes a plain-text task description through LLM decomposition to produce structured `task_context` fields (problem_description, success_criteria, domain_vocabulary). These feed L2 context refinement.

### recon

```bash
python -m promptpotter recon \
    --variants-file variants.json
```

Runs a one-axis-at-a-time (OAT) sensitivity scan — the "recon pass". Each parameter axis is varied independently while others stay at baseline. Identifies which axes have the most impact on accuracy.

### show-recon

```bash
python -m promptpotter show-recon
```

Displays recon results leaderboard and seeds the optimization campaign with the best-performing variant as starting point.

### optimize

```bash
# Resume: run the loop from the active session's current state.
python -m promptpotter optimize

# Fork: run the loop with the baseline OptSearchPoint rehydrated from a
# prior cycle's events.jsonl write-point. Mints a new cycle_id branched
# off the parent. Inherits the active session's dataset/pipeline/task.
python -m promptpotter optimize --from <cycle_id>:<event_ref>
```

Runs the full autonomous loop (L1 → L2 → L3 until convergence or `max_rounds`). Use `control --stop` or Ctrl+C to pause gracefully — state is checkpointed between rounds and resumes from the last completed round.

#### `--from <cycle_id>:<event_ref>` (forking)

Forks a new cycle from any durable write point in a prior cycle's timeline. The baseline is seeded from the `state_snapshot` on that event; the new cycle's `CampaignStart` config carries `parent_cycle_id` + `fork_spec` so lineage is queryable. `dataset_runs/` content-addressed cache replays any already-evaluated queries, so mid-scoring forks resume at the next unrun query.

Addressing grammar: `<cycle_id>:<event_ref>`. Event ref is either `round:write_point[:i[:j]]` (human form, last match wins) or `@<event_index>` (absolute offset into the cycle's slice of `events.jsonl`). Write points: `l1_generate` / `query_scored` / `candidate` / `winner` / `critique` / `l2` / `l3`. See [architecture/optimization.md § Forking a campaign](architecture/optimization.md#forking-a-campaign) for the full write-point table.

Examples:

```bash
# Fork after L1 generated candidates for round 1, before scoring.
python -m promptpotter optimize --from cycle_89d1c661916f:1:l1_generate

# Fork mid-scoring: after query 7 of candidate 0 in round 1.
python -m promptpotter optimize --from cycle_89d1c661916f:1:query_scored:0:7
```

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

# 3. (Optional) Run recon pass to find impactful axes
python -m promptpotter recon \
    --variants-file configs/variants.json

# 4. (Optional) Seed campaign from recon winner
python -m promptpotter show-recon

# 5. Run optimization (full loop — default)
python -m promptpotter optimize

# 6. View results
python -m promptpotter show-results

# 7. Export for paper
python -m promptpotter export supplemental \
    --backend-id local --output supplemental.md
```

---

## Zero-Signal Sample Filtering

On by default (the `min_observations=5` gate prevents premature exclusion on a fresh campaign). Queries with variance 0 (always-hit or always-miss) across at least `zero_signal_filter_min_observations` samples are physically moved from `datasets/{name}.json::items` into a `datasets/{name}.json::excluded` sidelist after each round. A fresh campaign will see the shrunken dataset.

Disable via `optimization.zero_signal_filter_enabled: false` in `campaign.json`. Tune `optimization.zero_signal_filter_min_observations` (default 5).

```bash
# Inspect what's been excluded
cat .promptpotter/projects/{backend_id}/datasets/{name}.json \
  | jq '.excluded | map({query: .item.query, hit_rate, observations, reason})'
```

Restoration is manual — either use `BackendStore.restore_dataset_items()` in a Python shell, or move entries from `excluded` back into `items` and delete the `excluded` array. When the filter fires during a run, a `zero_signal_filter` phase event is emitted with count + examples.

---

## Pipeline Params Threading

`configure_pipeline(svc, campaign_config)` applies `exclude_nodes` and `pipeline_overrides` and returns `pipeline_params`, which then flows unchanged through `init`, `optimize`, and `recon`. If `pipeline_params` is `None`, the backend runs the full pipeline including excluded nodes.

---

## Session Directory

The active session pointer lives at `.promptpotter/active_session.json` (see [Active Session](#active-session) above). Per-session state lives under `.promptpotter/projects/{backend_id}/sessions/{session_id}/`:

| File | Updated | Content |
|------|---------|---------|
| `session.json` | Each phase transition | Config, phase, pipeline_params, cycle_id, best_accuracy |
| `campaign_state.json` | Every optimization event | Live state: round, baseline, best, candidates, counters |
| `campaign_output.log` | Append per eval query | Raw eval output (ANSI-stripped) |
| `campaign_log.md` | End of each round | Structured markdown report |
| `recon_results.json` | After recon completes | recon_df + axis_profiles |

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
