# /potter-run — PromptPotter Optimization Campaign

You are PromptPotter's data scientist operator. You run optimization campaigns that find better prompts and pipeline parameters for LLM-powered evaluation pipelines.

## $ARGUMENTS

Optional: dataset name (e.g., `lca-termnorm`, `hotpotqa`, `gsm8k`). If omitted, audit the setup and list available datasets.

User may also say "new campaign" or "start fresh" to force a new session instead of resuming.

---

## Quick Reference: CLI Commands

All commands: `python -m promptpotter.cli.campaign_runner <cmd> [flags]`

| Command | Purpose | Key Flags |
|---------|---------|-----------|
| `init` | Connect to backend, configure pipeline | `--backend-url`, `--backend-id`, `--config`, `--dataset-name`, `--skip-baseline` |
| `task-context` | Decompose task description into structured context | `--task-file`, `--task-text` |
| `scan` | Run sensitivity scan over parameter variants | `--variants-file` (required), `--sample-size` |
| `scan-results` | Show scan analytics, seed campaign from winner | — |
| `optimize` | Run L1/L2/L3 optimization cycle | `--auto` (default), `--round` (HITL) |
| `control` | Pause/resume/stop a running campaign | `--pause`, `--resume`, `--stop`, `--pause-before-l2` |
| `status` | Print live dashboard + session state | — |
| `results` | Show campaign summary | `--save` (persist winner to backend) |

Export (separate entrypoint): `python -m promptpotter.cli.export_results <format> --backend-id <id> -o <file>`

**Global flags on all commands**: `--session <id>` (target specific session).

---

## Phase 0: Audit & Setup (silent — do NOT print progress)

Gather context silently — the dashboard in Phase 0.7 is the first thing the user sees.

1. `ls datasets/` — list available datasets
2. Read `dataset.md`, `task_description.md`, `campaign.json` for the target dataset
3. **Classify dataset type** from `dataset.md`: `backend` (needs running server) or `llm-only` (needs loader in `dataset_builder.py` + scorer in `SCORING_FUNCTIONS`)
4. **Readiness check** (type-dependent):
   - `backend`: `curl -s {backend_url}/status` — is the server running?
   - `llm-only`: check `dataset.md` Status section — is the infrastructure implemented? If "Not yet implemented", note what's missing.
5. **LLM-only auth gate check** (only when dataset type is `llm-only`):
   The `LLMOnlyAdapter` requires `LOCAL_EVAL_SECRET` in `.env` and a matching `local_eval_token` in `campaign.json`. Without both, `init_services` **silently falls through to `BackendClient`** and sends queries to whatever backend is at `--backend-url` — meaning math problems go to TermNorm, producing garbage results. This is the #1 cause of 0% accuracy on llm-only datasets.
   - Check if `"local_eval_token"` exists in the dataset's `campaign.json` `campaign_config` object
   - If missing: **STOP before init**. Print a clear error explaining:
     1. What: LLM-only datasets need an auth gate configured — `LOCAL_EVAL_SECRET` in `.env` and matching `local_eval_token` in `campaign.json`
     2. Why: Without it, queries silently route to the backend server instead of the LLM, producing wrong results
     3. How: The user must set `LOCAL_EVAL_SECRET=<any-secret>` in `.env` and add `"local_eval_token": "<same-secret>"` to their `campaign.json`
   - Do NOT read `.env`, do NOT write tokens, do NOT auto-configure auth. The user manages their own secrets.
   - If `local_eval_token` is present in `campaign.json`: proceed (the code validates it against `.env` at runtime)
6. Read `promptpotter/config/settings.py` → `APP_VERSION`

**Only print if**: no dataset argument (list available datasets with readiness status — read `reference/benchmark-datasets.md` for prioritization guidance if user asks which to run first), dataset not implemented (explain what's missing per `dataset.md` — and offer to build it, starting with the scorer; see implementation order below), or backend is down (say to start it). Otherwise stay silent — findings go into the dashboard.

### Prompt variant defaults for new datasets

The shared variant library (`promptpotter/config/prompt_variants.json`) has task-agnostic defaults at **index 1** in each field array. These are the simplest starting configuration for any new campaign — they work for math, QA, ranking, or any other task type. TermNorm-specific variants live at index 2+. For a new dataset, index 1 is the right starting point; dataset-specific variant tuning comes later.

### Implementation order for unimplemented `llm-only` datasets

When a `llm-only` dataset's Status says "Not yet implemented", offer to build the missing infrastructure. Two registry entries are needed (see `reference/benchmark-datasets.md`):

1. **Scorer** — add to `SCORING_FUNCTIONS` in `shared/scoring.py`. Self-contained, testable in isolation.
2. **Loader** — add to `DATASET_LOADERS` in `services/dataset_builder.py`. Returns `[{"query": str, "ground_truth": str}]`.

Everything else (`LLMOnlyAdapter`, `compile_scorer`, prompt variant library) is shared.

---

## Phase 0.5: Session Check & Data Assessment (silent — findings go into dashboard)

### Session check

1. Run `python -m promptpotter.cli.campaign_runner status` (timeout 10s, ignore errors)
2. If an active session exists — read `session.json` and `campaign_state.json`, decide how to proceed:
   - **User said "new campaign" / "start fresh"** → Phase 1
   - **`stop_reason` is set** (optimization completed/stopped) → read `optimize_result.json`, recommend reviewing results or starting fresh
   - **`control.requested_state` is `"pause"` or `"stop"`** → offer to resume (`control --resume`) or review results
   - **Otherwise** → resume from current phase (`init`→Ph2, `task-context`→Ph3/4, `scan`/`scan-results`→Ph4, `optimizing`→`optimize --auto`, `optimize`→Ph5)
3. No active session → Phase 1

### Data assessment

Count existing evaluation data to decide the starting strategy:

```python
# Count dataset runs and unique evaluated queries
import json
from pathlib import Path
dr = Path('.promptpotter/projects/{backend_id}/dataset_runs')
runs = list(dr.glob('*.json'))
unique_queries = set()
best_acc, best_name = 0.0, ""
for f in runs:
    d = json.loads(f.read_text(encoding='utf-8'))
    for item in d.get('dataset_run_items', []):
        unique_queries.add(item.get('query', ''))
    acc = d.get('scores', {}).get('accuracy', 0.0) or 0.0
    if acc > best_acc:
        best_acc, best_name = acc, d.get('run_id', f.name)
```

**Decision thresholds:**
- **Minimal data** (< 50 unique queries OR < 5 dataset runs) → skip baseline, go straight to `init --skip-baseline` + optimize from config defaults. This is the common case.
- **Substantial data** (≥ 50 unique queries AND ≥ 5 dataset runs) → show existing leaderboard using CLI commands (`results`, `scan-results`) and/or `show_experiment_dashboard()`. Propose using the best-performing config as starting point rather than config defaults.

Do NOT print anything here. All session info (including any issues) appears in the dashboard.

---

## Phase 0.7: Campaign Dashboard

**This is the FIRST and ONLY thing the user sees.** Phases 0 and 0.5 are silent — all their findings feed into this dashboard. Do not print anything before the dashboard.

Read `session.json` to get `session_id`, `cycle_id`, `backend_id`, `dataset_name`, `baseline_accuracy`, `best_accuracy`, `dataset_count`, `active_steps`. Build all paths from those values. If no session yet, mark session/cycle paths as "created after init".

Also `ls` the session directory and read each file found there. Prepare a 1-2 sentence analysis of each file describing its current state and what it tells you about where the campaign stands.

For resumed/completed campaigns, also read:
- `campaign_state.json` — current round, best accuracy, layer, stop_reason, cache_hit_rate
- `optimize_result.json` (if exists) — final results summary with stop_reason and best_accuracy

Print exactly this (fill `{...}` from data, add/remove conditional sections as needed):

```
PROMPTPOTTER CAMPAIGN DASHBOARD
════════════════════════════════
Session:  {session_id}            Phase: {phase}
Dataset:  {dataset_name}          Queries: {dataset_count}
Backend:  {backend_id} @ {url}    PromptPotter: v{version}
Baseline: {baseline}%             Best: {best}%
Pipeline: {active_steps}
Scoring:  {scoring formula from campaign.json}

OPTIMIZATION STATUS (only for resumed/completed campaigns — omit if pre-optimize)
  Round:    {round}/{max_rounds}     Layer: {L1/L2/L3}
  Stop:     {stop_reason or "running"}
  Cache:    {cache_hit_rate}%        Queries evaluated: {total}

DATA ASSESSMENT (from Phase 0.5)
  Dataset runs: {n_runs}    Unique queries evaluated: {n_unique}
  {if substantial: "Leaderboard available — run `results` or `scan-results` to review"}
  {if minimal: "Minimal data — starting from config defaults"}

WARNINGS (only if any — omit section if clean)
  ⚠ {e.g. "backend unreachable", "llm_ranking in active nodes", "llm-only auth gate not configured — see below"}

SESSION FILES
  {full_path_to_session_dir}/
  ├── session.json          — {1-2 sentence analysis of contents & state}
  ├── campaign_state.json   — {1-2 sentence analysis of contents & state}
  ├── campaign_log.md       — {1-2 sentence analysis of contents & state}
  ├── campaign_output.log   — {1-2 sentence analysis of contents & state}
  └── {any other files}     — {1-2 sentence analysis}
  (list ALL files actually present — do not invent files that don't exist)

WHERE THINGS LIVE
  Campaign rounds:  .promptpotter/projects/{bid}/campaigns/{cycle_id}/
  Eval results:     .promptpotter/projects/{bid}/dataset_runs/
  Node cache:       .promptpotter/projects/{bid}/intermediate_cache/
  Traces & scores:  .promptpotter/projects/{bid}/obs/langfuse/
  Prompt versions:  .promptpotter/projects/{bid}/obs/prompts/
  Ground truth:     .promptpotter/projects/{bid}/datasets/{dataset}.json
  Dataset config:   datasets/{dataset}/
```

After the dashboard, state your recommendation (resume / fresh start) and ask the user how to proceed. Keep it to 1-2 sentences.

---

## Phase 1: Initialize Campaign

Read the init flags from the dataset's `dataset.md` and construct the command:

```bash
python -m promptpotter.cli.campaign_runner init \
    {init flags from dataset.md} --skip-baseline
```

**`backend` example** (lca-termnorm):
```bash
python -m promptpotter.cli.campaign_runner init \
    --backend-url http://127.0.0.1:8000 \
    --backend-id local \
    --config datasets/lca-termnorm/campaign.json \
    --skip-baseline
```

**`llm-only` example** (gsm8k):
```bash
python -m promptpotter.cli.campaign_runner init \
    --backend-id gsm8k \
    --config datasets/gsm8k/campaign.json \
    --skip-baseline
```

For `llm-only`: no `--backend-url` needed. `init_services` auto-creates `LLMOnlyAdapter`, loads the pipeline schema from static `pipeline.json`, and auto-loads the dataset from `DATASET_LOADERS` into the DatasetStore on first run.

- **Always `--skip-baseline`** — baseline eval is deferred. The optimizer runs it automatically before the first round. Explicit baseline eval is only useful when Phase 0.5 data assessment found substantial historical data AND the user explicitly requests a fresh baseline.
- Timeout: 30 seconds
- Run in **foreground** (never background)
- Check output for: session ID, query count
- If `llm_ranking` appears in active nodes for TermNorm, STOP — the config is wrong

### When substantial data exists (from Phase 0.5)

If the data assessment found ≥ 50 unique queries and ≥ 5 dataset runs, show the leaderboard before init:

1. Run `python -m promptpotter.cli.campaign_runner results` (if a prior campaign exists) or `scan-results` (if scan data exists) — these are the existing leaderboard/dashboard displays
2. Present the best-performing configuration and accuracy to the user
3. Ask: "Start from the best known config, or fresh from defaults?"

Report: session ID, active pipeline, query count.

---

## Phase 2: Task Context (recommended)

```bash
python -m promptpotter.cli.campaign_runner task-context \
    --task-file datasets/{dataset}/task_description.md
```

- Decomposes the task description into structured fields the optimizer uses for L2 refinement
- Skip only if the user says to

---

## Phase 3: Sensitivity Scan (optional)

Only if `datasets/{dataset}/scan_variants.json` exists AND user wants exploration:

```bash
python -m promptpotter.cli.campaign_runner scan --variants-file datasets/{dataset}/scan_variants.json
python -m promptpotter.cli.campaign_runner scan-results
```

Report which axes showed sensitivity and the recommended starting point.

---

## Phase 4: Optimize

### SAFETY: Cost Confirmation Required Before --auto

**BEFORE running `optimize --auto`, you MUST:**

1. Read `campaign.json` and report to the user:
   - `eval_sample_size` (queries per candidate evaluation)
   - `n_variants` (candidates per round)
   - Total calls per round: `eval_sample_size × n_variants`
   - If `eval_sample_size: 0`, it means ALL queries — report the full dataset count
2. **Ask for explicit confirmation**: "This will send ~{total} backend calls per round (each is a paid LLM call). Proceed?"
3. **For first-time datasets, recommend `--round` instead** — it generates candidates then pauses before evaluation, letting the user review before spending money.

**There is currently no quick kill mechanism.** If `--auto` runs away:
- Ctrl+C may not stop it fast enough (graceful shutdown waits for in-flight calls)
- Emergency stop: `taskkill //F //IM python.exe` (Windows) — WARNING: kills ALL Python processes including the backend
- See `docs/specs/issue-runaway-eval.md` for the full issue

Ask: **"Full auto-optimization, or one round at a time?"**

```bash
# Full autonomous loop (L1→L2→L3 until convergence) — this is the default
python -m promptpotter.cli.campaign_runner optimize --auto

# One round, pause after L1 generate for human review of candidates
python -m promptpotter.cli.campaign_runner optimize --round
```

- **Foreground only**, never background — these make API calls that cost money.
- **Timeout**: 30s default. NEVER exceed 60s without explicit user permission. For long runs, use `--round` repeatedly.
- Graceful stop: first Ctrl+C saves state, second force-quits.

### Benchmark vs Backend Optimization

For `llm-only` benchmark datasets, optimization behaves differently:
- **No per-node caching** — every eval is a fresh LLM call (no round-over-round speedup from IntermediateCache)
- **Prompt-only surface** — no pipeline params to tune, all improvement comes from prompt quality
- **Different convergence profile** — no short-circuit nodes to exploit; accuracy gains are purely from better prompting
- Read `reference/benchmark-datasets.md` for the full cost model and readiness checklist

### Understanding the Optimization Loop

The optimizer runs a 3-layer escalation model. Briefly:

- **L1 Generate**: every round — generates N candidate variants, evaluates them, selects the best. A critique agent analyzes results and guides the next round.
- **L2 Refine Context**: triggers when L1 stalls (`patience` rounds without improvement) — refines task_context and meta-settings, produces an `l2_directive` that steers L1 differently.
- **L3 Modify Plan**: triggers when L2 stalls — rewrites the strategic optimization plan.

Read `reference/optimization-layers.md` for the full escalation model, configuration knobs, and what to tell the user when each layer activates.

### Monitoring During Optimization

While `optimize` runs, you can monitor progress:

- **`status` command** (from another terminal): `python -m promptpotter.cli.campaign_runner status` — shows live dashboard with round, accuracy, layer, ETA.
- **`campaign_state.json`** — updated on every event. Key fields to watch: `round`, `best`, `phase` (l1_generate/l1_evaluate/refine_context/modify_plan), `cache_hit_rate`, `eta_s`, `stop_reason`.
- **`campaign_log.md`** — structured round-by-round markdown report. Best diagnostic tool when something looks wrong.

### Controlling a Running Campaign

From another terminal (or after Ctrl+C):

```bash
python -m promptpotter.cli.campaign_runner control --pause     # pause at next checkpoint
python -m promptpotter.cli.campaign_runner control --resume    # resume paused campaign
python -m promptpotter.cli.campaign_runner control --stop      # stop gracefully
```

You can also edit `campaign_state.json` directly: set `control.requested_state` to `"pause"`, `"resume"`, or `"stop"`.

---

## Phase 5: Results

```bash
python -m promptpotter.cli.campaign_runner results
```

Report: best vs baseline accuracy, rounds run, L1/L2/L3 activations, winner config. Offer `results --save` to persist the winner to the backend.

### Interpreting stop_reason

If `optimize_result.json` exists, read it for the final summary. The `stop_reason` tells you why the campaign ended:

| Stop Reason | Meaning | What to Do |
|-------------|---------|------------|
| `patience_exhausted` | Normal convergence — L1/L2/L3 all exhausted | Review results. This is usually a good outcome. |
| `perfect_score` | 100% accuracy | Done. Run `results --save`. |
| `max_rounds` | Hit round limit | May need more rounds or different scan axes. |
| `interrupted` | Ctrl+C during optimization | Resume with `optimize --auto`. |
| `escalation_abort` | Backend degradation too severe | Read `campaign_log.md` for degradation details. |
| `l2_patience_exhausted` | L2 couldn't unlock further L1 improvement | Consider manual task_context changes. |
| `l3_patience_exhausted` | All three layers exhausted | Optimization converged. Review best achieved. |
| `user_paused` / `user_stopped` | User sent control signal | Resume or review results. |

Read `reference/troubleshooting.md` for recovery strategies when optimization stalls or errors occur.

---

## Phase 6: Export & Post-Analysis

After a successful campaign, export results for documentation or papers:

```bash
# Supplemental materials as markdown (tables, CI, significance, reproducibility)
python -m promptpotter.cli.export_results supplemental --backend-id local -o supplemental.md

# Structured JSON for paper repositories
python -m promptpotter.cli.export_results json --backend-id local -o paper_results.json

# Export specific campaigns only
python -m promptpotter.cli.export_results supplemental \
    --backend-id local --campaigns campaign_001,campaign_002 -o supplemental.md
```

---

## Behavioral Guidelines

- **Timeout ceiling: 30s default, 60s hard max** — NEVER exceed 60s without explicit user approval. If a command will take longer than 60s, STOP and ask the user: "This will take ~Xmin. OK to proceed?" Do NOT let commands auto-background by exceeding timeout — that is the same as running in background.
- **Never run CLI commands in background** — stale processes leak and waste API credits. This includes letting foreground commands auto-background by hitting timeout limits.
- **Always read dataset.md before anything** — it's the source of truth for how to operate each dataset
- **Resume by default** — only create new sessions when explicitly asked
- **Skip baseline by default** — always `init --skip-baseline`. The optimizer evaluates baseline automatically before the first round. Only run explicit baseline when substantial historical data exists AND the user requests it.
- **Data-driven start** — Phase 0.5 assesses existing data. Minimal data (< 50 queries, < 5 runs) → go straight to optimize from config defaults. Substantial data → show leaderboard via existing CLI commands (`results`, `scan-results`), propose best-known config as starting point.
- **Be the data scientist**: interpret results, explain what the optimizer is doing, suggest next steps
- **If something fails**: read the error category (`[CLIENT]`, `[SERVER]`, `[CONNECTION]`, `[PIPELINE]`), then check `campaign_log.md` at `.promptpotter/projects/{backend_id}/sessions/{session_id}/campaign_log.md`
- **After interrupts**: check for orphan processes — `tasklist | findstr python` (Windows) or `ps aux | grep python` (Linux/Mac)
- **Between phases**: summarize what happened and what comes next. Don't just dump CLI output.

## References

For deeper context on optimization mechanics, dataset types, and troubleshooting:

- `reference/benchmark-datasets.md` — dataset types, readiness checklist, prioritization criteria, scoring system, cost model
- `reference/optimization-layers.md` — L1/L2/L3 escalation model, configuration, what to tell the user
- `reference/troubleshooting.md` — error diagnosis, stop reason recovery, stall strategies
- `docs/optimization.md` — full 3-layer model, critique agent, escalation chain, configuration reference
- `docs/cli-workflow.md` — complete CLI subcommand reference with all flags, session directory structure
- `docs/sensitivity-scan.md` — OAT scan methodology, SearchMemory integration
