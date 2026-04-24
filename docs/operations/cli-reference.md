# CLI Reference

Every subcommand, flag, and a worked example. The CLI provides a terminal-based interface for HITL (human-in-the-loop) prompt optimization. Each subcommand persists its output under `{tenant_id}/campaigns/{cycle_id}/`, so progress survives interrupts and the workflow can be resumed at any step.

```bash
python -m promptpotter [--tenant <id>] <subcommand> [options]
```

`--tenant` is a root flag (default `"default"`) that selects the partition under `.promptpotter/projects/`. Single-user CLI workflows can ignore it; multi-tenant setups set it once per session.

For the active-session pointer and state files, see [persistence-and-state.md](persistence-and-state.md). For `optimize --from` and `fork`, see [rewind-and-fork.md](rewind-and-fork.md).

---

## Subcommand sequence

```
init ──→ [set-task] ──→ optimize ──→ show-results ──→ export
```

Steps in brackets are optional. Minimum viable workflow: `init` then `optimize`.

| Step | Command | What it does | Reads from |
|------|---------|-------------|------------|
| 1 | `init` | Connect to backend, configure pipeline (baseline deferred) | Config file |
| 2 | `set-task` | Decompose a task description into structured domain context | `init_params` |
| 3 | `optimize` | Run L1/L2/L3 optimization cycle | All above |
| 4 | `show-results` | Show summary, optionally save winner to backend | Campaign cycles |
| 5 | `export` | Generate supplemental materials or JSON | Campaign data |

---

## init

```bash
python -m promptpotter init \
    --backend-url http://127.0.0.1:8000 \
    --config datasets/lca-termnorm/campaign.json
```

Connects to the backend, fetches pipeline schema via `GET /pipeline`, applies `exclude_nodes` and `pipeline_overrides` from config. Pure prep — no backend scoring. The baseline runs automatically as phase 0 of `optimize` on the same seeded `sp_budget_ttest` slice L1 uses, so its results cache-hit every L1 round-1 candidate.

Produces the campaign directory — see [persistence-and-state.md](persistence-and-state.md) for its contents.

**Init flags:**

| Flag | Purpose |
|---|---|
| `--backend-url` | Backend service URL (default from settings) |
| `--backend-id` | Override backend id (auto-derived from `dataset_name` otherwise) |
| `--dataset-name` | Override dataset name from config |
| `--config` | Campaign config JSON file |

Rewinding within an active cycle is not done here — see [rewind-and-fork.md](rewind-and-fork.md). `init` handles registration/setup only.

---

## set-task

```bash
python -m promptpotter set-task \
    --task-file description.txt
```

Passes a plain-text task description through LLM decomposition to produce structured `task_context` fields (problem_description, success_criteria, domain_vocabulary). These feed L2 context refinement.

---

## optimize

```bash
# Resume: run the loop from the active session's current state.
python -m promptpotter optimize

# Rewind: resume the active cycle from after a specific round N.
python -m promptpotter optimize --from <round>
```

Runs the full autonomous loop (L1 → L2 → L3 until convergence or `max_rounds`). Use `control --stop` or Ctrl+C to pause gracefully — state is checkpointed between rounds and resumes from the last completed round.

`--from <round>` rewinds the active cycle to after round N and resumes in-place. Same `cycle_id`, not a new campaign. See [rewind-and-fork.md](rewind-and-fork.md) for full mechanics.

While `optimize` runs, the live in-flight round's per-node I/O (l1_generate, l1_critique, l1_score with per-sample lines and stats) mirrors into `campaigns/{cycle_id}/dashboard.json::current_round`; each completed round is snapshotted to `campaigns/{cycle_id}/rounds/round_NNNN.json`. Full shape in [persistence-and-state.md § `rounds/round_NNNN.json`](persistence-and-state.md#roundsround_nnnnjson).

---

## show-results

```bash
python -m promptpotter show-results
python -m promptpotter show-results --save  # save winner to backend
```

Renders the best configuration found — prompt fields, pipeline parameters, accuracy achieved vs. baseline, which layer (L1/L2/L3) produced it, how many rounds it took. `--save` persists the winner to the backend as the default.

---

## control

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

## Export commands

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

See [../research/benchmarks.md](../research/benchmarks.md) for the full benchmark methodology and result table format.

---

## Worked example

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

## Zero-signal sample filtering

On by default (the `min_observations=5` gate prevents premature exclusion on a fresh campaign). Queries with variance 0 (always-hit or always-miss) across at least `zero_signal_filter_min_observations` samples are physically moved from `datasets/{name}.json::items` into a `datasets/{name}.json::excluded` sidelist after each round. A fresh campaign will see the shrunken dataset.

Disable via `optimization.zero_signal_filter_enabled: false` in `campaign.json`. Tune `optimization.zero_signal_filter_min_observations` (default 5).

```bash
# Inspect what's been excluded
cat .promptpotter/projects/{backend_id}/datasets/{name}.json \
  | jq '.excluded | map({query: .item.query, hit_rate, observations, reason})'
```

Restoration is manual — either use `BackendStore.restore_dataset_items()` in a Python shell, or move entries from `excluded` back into `items` and delete the `excluded` array. When the filter fires during a run, a `zero_signal_filter` phase event is emitted with count + examples.

---

## Pipeline params threading

`configure_pipeline(svc, campaign_config)` applies `exclude_nodes` and `pipeline_overrides` and returns `pipeline_params`, which then flows unchanged through `init` and `optimize`. If `pipeline_params` is `None`, the backend runs the full pipeline including excluded nodes.

---

## Interrupt handling

The CLI uses a signal-flag pattern for graceful interrupts:

- **First Ctrl+C** — finishes the in-flight backend call, saves all completed work, exits cleanly
- **Second Ctrl+C** — force-quits immediately

No completed work is ever discarded. On resume, the session picks up from the last persisted state.

After any interrupted run, check for orphan processes:

```bash
ps aux | grep python         # Linux/Mac
tasklist | findstr python     # Windows
```
