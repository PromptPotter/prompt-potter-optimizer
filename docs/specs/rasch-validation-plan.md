# Rasch Validation — Step-by-Step Operator Plan

**Status:** Working plan, not committed. Reused across sessions to give context.
**Goal:** One BBEH run that proves Rasch + scoring-set evolution + hard-sample sorter fire correctly and produce M10-usable traces. **Smoking gun:** round-2+ scoring set contains sample IDs *outside* the deterministic 0-19 prefix.

## Why this gates M10

M10's `review.md` and cross-cycle leaderboard read OSP traces and round JSONs directly. If those traces are correct AND Rasch produces a non-empty `hard_samples_campaign.json` + a coherent hard-samples heatmap, M10 builds on that. If anything is broken, fix here, not in M10 Track 1.

Litmus test: *"if OSP traces all correct, that's a good starting point."*

---

## Step 1 — Pre-flight fixes (one session, ~50 LOC, before any run)

Close OSP/persistence trace gaps. Order: 1a + 1b + 1c additive; 1d + 1e together (adds strictness).

| 1a | `domain/opt_search_point.py` | Add `IndividualLineage.source: str = ""` (values: `l1_generate` / `l2_context` / `l3_plan` / `origin`) | ~3 LOC |
| 1b | `application/optimization/pipeline.py` | Populate `lineage.source` at the 3 mint sites + origin | ~6 LOC |
| 1c | `application/optimization/pipeline.py::load_optimizer_prompt` | Compute SHA-256 per template, stash `prompt_hashes: dict[str, str]` on `Session`, persist to `index.json::final.prompt_hashes` | ~10 LOC |
| 1d | `domain/opt_search_point.py` | Set `model_config = ConfigDict(extra="forbid")` on `OptSearchPoint` | ~1 LOC |
| 1e | `application/optimization/cycle.py::replay_priors` | Migration shim: detect legacy flat `id`/`parent_id`/`memory` keys, re-pack into `lineage` + memory fields before construct. Log warning per migrated trial | ~15 LOC |
| 1f | `infrastructure/store/stores.py::_migrate_legacy_fork_dirs` | Extend (rename to `_migrate_legacy_layout`): also rename `rounds/`→`.cache/rounds/`, `candidates/`→`.cache/candidates/`; unlink `phase_events.jsonl`, `optimize_result.json`. Idempotent | ~15 LOC |

Verify 1d+1e by resuming any pre-2026-03 cycle and confirming the shim log fires + load succeeds.

---

## Step 2 — Configure the validation run

No edits needed. `swap_out_delta_se=0.7` is now the schema default in `ExplorationConfig`, sized to fire round 1→2 on the typical 20-sample / 5-candidate budget. L2/L3 stay on (defaults). If you want a shorter validation, just pass `max_rounds=3` to `run_bbeh_campaign` from the notebook.

---

## Step 3 — Start the backend

TermNorm at `:8000`. From `C:\Users\dsacc\OfficeAddinApps\TermNorm-excel\backend-api`, launch the API. Verify:

```bash
curl http://127.0.0.1:8000/status
```

Should return 200. If connection refused → backend not up; do not proceed.

---

## Step 4 — Launch the run

**BBEH is notebook-driven.** Open `notebooks/bbeh_potter.ipynb`, run cells 1-3 (env, load BBEH data, kick `run_bbeh_campaign`). The notebook auto-mints a session+cycle and claims `.promptpotter/active_session.json`.

Run for 5 rounds (~10-30 min depending on Groq latency). Or interrupt after round 2 if you only want the smoking-gun check.

---

## Step 5 — Locate the artifacts

After launch, find the cycle dir. Active pointer:

```
.promptpotter/active_session.json
```

→ gives `{tenant_id, session_id, cycle_id}`. The campaign dir is:

```
.promptpotter/projects/{tenant_id}/campaigns/{root_cycle_id}/
```

For forks (likely on a fresh notebook run that re-mints from an existing root): the actual cycle audit dir is at `forks/{cycle_id}/`. The root carries `dashboard.json` + `output.log` (telemetry stream); the leaf carries `index.json` + `log.md` + `rounds/` + `.cache/`.

**Files that matter for this validation:**

| File | What it is |
|------|-----------|
| `dashboard.json` (at root) | Live state — phase, round, current accuracy, current candidate's per-sample HIT/MISS lines |
| `output.log` (at root) | Append-only HIT/MISS history, per query |
| `rounds/round_0002.json` (round 2) | Smoking-gun source — `candidate_scores[].samples` |
| `hard_samples_campaign.json` (at leaf, rewritten every round-end finalize) | End-of-round Rasch fit — `sample_order`, `candidate_order`, `cells`, `rasch.{theta, delta, ...}` |
| `log.md` (at leaf, regenerated each round) | Final digest with **Hard Samples** heatmap section |
| `index.json` (at leaf) | `final.prompt_hashes` (Step 1c) + `final.stop_reason` |

---

## Step 6 — Smoking gun: are sample IDs in 0-19 order?

Open `rounds/round_0002.json` (= round 2). Round 1 lives in `rounds/round_0001.json`; origin in `rounds/round_0000.json`.

**Check 1 — `hard_samples_campaign.json`:** open the cycle's `hard_samples_campaign.json` (rewritten every round-end finalize). Should carry:
```json
{
  "sample_order": [<sample_ids, hardest-δ_s first>],
  "candidate_order": [<candidate_ids, highest-θ_c first>],
  "cells": [{"c": "...", "s": 0, "hit": true}, ...],
  "rasch": {"theta": {...}, "delta": {...}, "theta_se": {...}, "delta_se": {...}, "converged": true}
}
```
If `sample_order` is non-empty AND `rasch.converged: true` → Rasch fit landed. ✓

**Check 2 — sample IDs in scoring set:** scroll to `candidate_scores[i].samples` (per candidate). Each entry is a string like:
```
0.0s #023 HIT  [ai]  -> '' gt:'proved' q:'A few players...'
```
The `#NNN` is the sample_id (positional index into the BBEH flattened mini list).

- Round 1 (`round_0001.json`): all IDs should be `#000` through `#019` — the deterministic 20-sample prefix.
- Round 2 (`round_0002.json`): if Rasch swapped any sample, you'll see at least one ID ≥ `#020` mixed in.

**Smoking gun:** any ID ≥ #020 in round 2 = the mechanic fired. If all 20 IDs are still 0-19 → no swap; either thresholds still too tight (push to 0.8) or Rasch isn't fitting (look at `log.md` heatmap).

---

## Step 7 — Verify the heatmap

After the campaign finalizes (5 rounds done, or Ctrl+C twice), open `log.md` at the leaf cycle dir. Find the **Hard Samples** section. Should render a candidate (θ_c) × sample (δ_s) matrix with HIT/MISS/unmeasured cells.

- Distinct δ_s values across the sample axis ≥ 4 → Rasch produced a real difficulty ranking.
- All-uniform / single value → Rasch is degenerate (bigger problem than thresholds).

This heatmap fires at finalize regardless of swap evolution — it's the independent "Rasch fitted at all" check.

---

## Step 8 — Verify trace correctness (Step 1 fixes)

Same round file:

- `opt_search_point.lineage.source` populated with one of `l1_generate` / `l2_context` / `l3_plan` / `origin` — Step 1a/1b ✓.
- All 9 `MEMORY_FIELDS` round-trip — reload produces no warning logs.
- `index.json::final.prompt_hashes` has 4 entries (`l1_generate`, `l1_critique`, `l2_context`, `l3_plan`) — Step 1c ✓.
- Reload of a pre-2026-03 cycle (e.g. `cycle_0cfa3a4f0136`) emits a migration warning instead of silently regenerating UUIDs — Step 1d/1e ✓.

---

## Acceptance

- Smoking gun: ≥ 1 sample_id ≥ 20 in round-2 scoring set.
- `hard_samples_campaign.json::sample_order` non-empty by round 3 (round 2 = stretch).
- Hard Samples heatmap renders with ≥ 4 distinct δ_s values.
- All Step 1 fixes verified by reading post-run round files and one legacy-cycle reload.

If any fail → file the failure mode as the M10 Track-1 input. Don't start M10 until this passes.

---

## Reuse across sessions

Hand this file to Claude. State which step you're on:
- `Step 1`: doing pre-flight fixes — point Claude at the row.
- `Step 2-4`: prepping config / starting backend / launching — Claude verifies the config block and TermNorm `/status`.
- `Step 5-6`: post-run validation — Claude reads the round file and checks the smoking gun.
- `Step 7-8`: heatmap + trace correctness — Claude reads `log.md` + `index.json`.

Acceptance criteria are stable; threshold knobs and run length may evolve as you iterate. Update this file as steps complete.
