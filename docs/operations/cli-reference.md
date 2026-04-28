# CLI Reference

The CLI is two write verbs: `init` creates a session+cycle, `optimize` runs a campaign against it. Reads happen by opening the on-disk artifact tree (`sessions/{id}/`, `campaigns/{cycle_id}/`) — `dashboard.json` for live state, `log.md` for the digest, `index.json` for the final summary including `stop_reason`. Stop with Ctrl+C (first finishes in-flight and saves; second force-quits) — there is no mid-run pause/resume.

```bash
python -m promptpotter [--tenant <id>] <subcommand> [options]
```

`--tenant` is a root flag (default `"default"`) that selects the partition under `.promptpotter/projects/`. Single-user CLI workflows can ignore it; multi-tenant setups set it once per session.

For the active-session pointer and state files, see [persistence-and-state.md](persistence-and-state.md). For `optimize --from` and `optimize --fork-on-divergence`, see [rewind-and-fork.md](rewind-and-fork.md).

---

## Subcommand sequence

```
init ──→ optimize
```

| Step | Command | What it does |
|------|---------|-------------|
| 1 | `init` | Create session+cycle for a dataset. Decomposes `datasets/<name>/task_description.md` once when present. |
| 2 | `optimize` | Run the optimization loop against the active session. |

---

## init

```bash
python -m promptpotter init \
    --backend-url http://127.0.0.1:8000 \
    --config datasets/lca-termnorm/campaign.json
```

Connects to the backend, fetches pipeline schema via `GET /pipeline`, applies `exclude_nodes` and `pipeline_overrides` from config. Pure prep — no backend scoring. The baseline runs automatically as phase 0 of `optimize` on the same seeded `sp_budget_ttest` slice L1 uses, so its results cache-hit every L1 round-1 candidate.

Produces the campaign directory — see [persistence-and-state.md](persistence-and-state.md) for its contents.

If `datasets/<name>/task_description.md` exists, `init` decomposes it once into `task_context` and stores it on the session — `optimize` reads it from there. The `--task-file` and `--task-text` flags override the dataset's default for ad-hoc cases. Result is disk-cached, so re-`init` against the same dataset is free.

**Flags:**

| Flag | Purpose |
|---|---|
| `--backend-url` | Backend service URL (default from settings) |
| `--backend-id` | Override backend id (auto-derived from `dataset_name` otherwise) |
| `--dataset-name` | Override dataset name from config |
| `--config` | Campaign config JSON file |
| `--task-file` | Override `datasets/<name>/task_description.md` |
| `--task-text` | Override `datasets/<name>/task_description.md` inline |

Rewinding within an active cycle is not done here — see [rewind-and-fork.md](rewind-and-fork.md). `init` handles registration/setup only.

---

## optimize

```bash
# Default: resume from the latest completed round of the active cycle.
python -m promptpotter optimize

# Rewind: resume the active cycle from after a specific round N.
python -m promptpotter optimize --from <round>
```

Runs the full autonomous loop (L1 → L2 → L3 until convergence or `max_rounds`). Stop with Ctrl+C — state is checkpointed between rounds and resumes from the last completed round on the next run.

`--from <round>` rewinds the active cycle to after round N and resumes in-place. Same `cycle_id`, not a new campaign. See [rewind-and-fork.md](rewind-and-fork.md) for full mechanics.

While `optimize` runs, the live in-flight round's per-node I/O (l1_generate, l1_critique, l1_score with per-sample lines and stats) mirrors into `campaigns/{root_cycle_id}/dashboard.json::current_round` (telemetry binds to the family root — see [persistence-and-state.md](persistence-and-state.md)); each completed round is snapshotted to `campaigns/{cycle_id}/rounds/round_NNNN.json`. Full shape in [persistence-and-state.md § `rounds/round_NNNN.json`](persistence-and-state.md#roundsround_nnnnjson).

**Flags:**

| Flag | Purpose |
|---|---|
| `--from <round>` | Rewind the active cycle to after round N before resuming |
| `--no-divergence-check` | On resume, rescore but skip the decision-replay halt |
| `--fork-on-divergence` | On divergence, mint a sibling cycle (with `parent_cycle_id`) and re-run the divergent round under the current scorer |

---

## Reading state

There is no read CLI. Open the artifact tree directly. Two bands — telemetry at the family root, audit per cycle (forks share the root's telemetry stream):

| File | Purpose |
|---|---|
| `campaigns/<root_cycle_id>/dashboard.json` | Live scalar state during a run (phase, round, candidate, in-flight payload, per-round node I/O). `cycle_id` field names the active fork. |
| `campaigns/<root_cycle_id>/output.log` | Append-only HIT/MISS history, fast to tail. `=== FORK ... ===` banner inline at each cutover. |
| `<cycle_dir>/log.md` | Per-round digest, regenerated on every round-complete and at finalize |
| `<cycle_dir>/index.json` | Campaign metadata + `final` block (best/baseline/stop_reason/winner) once finished. Forks have a `parent_cycle_id` field pointing back to the root chain. |
| `<cycle_dir>/trials/trial_NNNN.json` | Per-round optimizer checkpoint (critique, l2_directive, escalation state) |
| `<cycle_dir>/.cache/rounds/round_NNNN.json` | Per-round node I/O (internal — developer artifact) |
| `<cycle_dir>/.cache/candidates/round_NNNN.json` | Pre-scoring candidate checkpoint (internal — resume state) |

`<cycle_dir>` resolves to `campaigns/{cycle_id}/` for root cycles and `campaigns/{root_cycle_id}/forks/{cycle_id}/` for forks. Telemetry stays at the family root regardless; audit nests with each cycle.

---

## Worked example

```bash
# 1. Initialize against a running backend. task_description.md is auto-loaded.
python -m promptpotter init \
    --backend-url http://127.0.0.1:8000 \
    --config datasets/lca-termnorm/campaign.json

# 2. Run optimization. Ctrl+C to stop; re-run to resume.
python -m promptpotter optimize

# 3. Read state by opening files in your editor:
#    campaigns/<cycle_id>/dashboard.json   (live)
#    campaigns/<cycle_id>/log.md           (digest)
#    campaigns/<cycle_id>/index.json       (final summary, incl. stop_reason)
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
