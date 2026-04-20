# CLI Campaign Workflow

The CLI provides a terminal-based interface for HITL (human-in-the-loop) prompt optimization. Each subcommand persists its output under `{tenant_id}/campaigns/{cycle_id}/`, so progress survives interrupts and the workflow can be resumed at any step.

```bash
python -m promptpotter [--tenant <id>] <subcommand> [options]
```

`--tenant` is a root flag (default `"default"`) that selects the partition under `.promptpotter/projects/`. Single-user CLI workflows can ignore it; multi-tenant setups set it once per session.

## Active Session

PromptPotter remembers which campaign you're working on via an **active session pointer** at `.promptpotter/active_session.json`. This stores `{tenant_id, cycle_id}` — like a browser's active tab.

- **`init`** creates a new cycle and sets it as active (overwrites the pointer).
- **Every other command** (`optimize`, `show-status`, `show-results`, `set-task`, `control`) operates on the active cycle automatically — no flags needed.
- **`--session <id>`** overrides the active pointer for a single command.
- **`--backend-id`** is auto-derived from `dataset_name` in the config when not explicitly passed (so `init --config datasets/aime_2025/campaign.json` correctly uses `backend_id=aime_2025`, not the default `local`). Under v3, the backend lives under `{tenant_id}/library/backends/{backend_id}/` — it is no longer the outer axis.

To resume a campaign: just run `python -m promptpotter optimize`. No need to `init` again — `init` is only for starting a **new** campaign.

---

## Subcommand Reference

### Subcommand Sequence

```
init ──→ [set-task] ──→ optimize ──→ show-results ──→ export
```

Steps in brackets are optional. Minimum viable workflow: `init` then `optimize`.

| Step | Command | What it does | Reads from |
|------|---------|-------------|------------|
| 1 | `init` | Connect to backend, configure pipeline (baseline deferred) | Config file |
| 2 | `set-task` | Decompose a task description into structured domain context | init_params |
| 3 | `optimize` | Run L1/L2/L3 optimization cycle | All above |
| 4 | `show-results` | Show summary, optionally save winner to backend | Campaign cycles |
| 5 | `export` | Generate supplemental materials or JSON | Campaign data |

### init

```bash
python -m promptpotter init \
    --backend-url http://127.0.0.1:8000 \
    --config datasets/lca-termnorm/campaign.json
```

Connects to the backend, fetches pipeline schema via `GET /pipeline`, applies `exclude_nodes` and `pipeline_overrides` from config. Pure prep — no backend scoring. The baseline runs automatically as phase 0 of `optimize` on the same seeded `sp_budget_ttest` slice L1 uses, so its results cache-hit every L1 round-1 candidate.

Produces: `campaigns/{cycle_id}/` with `index.json`, `dashboard.json`, `control.json`, `events.jsonl`, `output.log`, `log.md`.

**Init flags**:

| Flag | Purpose |
|---|---|
| `--backend-url` | Backend service URL (default from settings) |
| `--backend-id` | Override backend id (auto-derived from `dataset_name` otherwise) |
| `--dataset-name` | Override dataset name from config |
| `--config` | Campaign config JSON file |

Rewinding within an active cycle is not done here — see `optimize --from <round>` below. `init` handles registration/setup only. Fork-across-cycles (new `cycle_id`, parent pointer) is available via `python -m promptpotter fork` after `optimize` halts on a resume-divergence — see `docs/architecture/optimization.md § Decision-replay and fork`.

### set-task

```bash
python -m promptpotter set-task \
    --task-file description.txt
```

Passes a plain-text task description through LLM decomposition to produce structured `task_context` fields (problem_description, success_criteria, domain_vocabulary). These feed L2 context refinement.

### optimize

```bash
# Resume: run the loop from the active session's current state.
python -m promptpotter optimize

# Rewind: resume the active cycle from after a specific round N.
# Later trial files are moved to archived/resumed_at_<ts>/ and the loop
# continues at round N+1 from the restored OptSearchPoint.
python -m promptpotter optimize --from <round>
```

Runs the full autonomous loop (L1 → L2 → L3 until convergence or `max_rounds`). Use `control --stop` or Ctrl+C to pause gracefully — state is checkpointed between rounds and resumes from the last completed round.

#### `--from <round>` (mid-cycle rewind)

Rewinds the active cycle to after round N and resumes in-place. Same `cycle_id`, same campaign directory — **not** a new campaign. Trial files for rounds > N are moved into `campaigns/{cycle_id}/archived/resumed_at_<ts>/` and the cycle's trial index is rebuilt to reflect only the surviving 0..N entries. `dataset_runs/` content-addressed cache replays any unchanged per-query results automatically.

To edit optimizer state by hand, modify `campaigns/{cycle_id}/trials/trial_{N:04d}.json` between runs — keep the `opt_search_point` block round-trippable through `OptSearchPoint.model_validate`. See [architecture/optimization.md § Resuming mid-cycle](architecture/optimization.md#resuming-mid-cycle).

Examples:

```bash
# Resume from after round 2 — archives trials/trial_0003+ and continues at round 3.
python -m promptpotter optimize --from 2
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

Bidirectional control lives in `control.json` (a sibling of `dashboard.json`). You can also edit it directly: set `requested_state` to `"pause"`, `"resume"`, or `"stop"`.

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

# 3. Run optimization (full loop — default)
python -m promptpotter optimize

# 4. View results
python -m promptpotter show-results

# 5. Export for paper
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

`configure_pipeline(svc, campaign_config)` applies `exclude_nodes` and `pipeline_overrides` and returns `pipeline_params`, which then flows unchanged through `init` and `optimize`. If `pipeline_params` is `None`, the backend runs the full pipeline including excluded nodes.

---

## Cycle Directory (v3)

The active pointer lives at `.promptpotter/active_session.json` (see [Active Session](#active-session) above). Per-cycle state lives under `.promptpotter/projects/{tenant_id}/campaigns/{cycle_id}/`:

| File | Updated | Content |
|------|---------|---------|
| `index.json` | Each phase transition | Config, phase, pipeline_params, cycle_id, best_accuracy |
| `dashboard.json` | Every optimization event | Live state: round, baseline, best, candidates, counters |
| `control.json` | Pause / resume / stop signals | HITL control surface (bidirectional) |
| `output.log` | Append per eval query | Raw eval output (ANSI-stripped) |
| `log.md` | End of each round | Structured markdown report |
| `journal.md` / `notes.md` | Notebook ↔ CLI exchange | User narrative and Claude notes |
| `trials/trial_NNNN.json` | Each completed round | Serialized `OptSearchPoint` for resume |
| `candidates/round_NNNN.json` | Each round's pre-scoring step | Generated candidate list checkpoint |
| `events.jsonl` | Every observability event | Flat navigation log |
| `langfuse/` | During optimization | Trace/observation/score shadow + id-map `state.json` |
| `prompts/` | When prompts render | Rendered optimizer prompts per family/version |

### dashboard.json

Scalar-only live dashboard. Atomically rewritten on every event during optimization. Carries display counters across cycles via `resume_from`.

Key fields: `workflow`, `phase`, `round`, `baseline`, `best`, `cycle_id`, `rounds_completed`, `total_queries_scored`, `total_backend_calls`, `cache_hit_rate`, `hit_rate`, `eta_s`, `candidate`, `query`. For per-query / per-candidate / per-round detail, read `output.log` or `rounds/round_NNN.json` directly.

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
