# Persistence, State, and Recovery

Your work lives in `.promptpotter/`. Two trees:

- `sessions/{session_id}/` — your operator workspace (journal, notes).
- `campaigns/{root_cycle_id}/` — one cycle family per directory; siblings under `forks/`.

The rest of this page covers the active-session pointer, what each file holds, and the three recovery workflows (resume, rewind, fork).

---

## Active session pointer

PromptPotter remembers which campaign you're on via `.promptpotter/active_session.json` — `{tenant_id, session_id, cycle_id}`, like a browser's active tab.

- **`init`** creates a new cycle and overwrites the pointer.
- **`optimize`** operates on the active cycle automatically.
- **`--session <id>`** overrides the pointer for one command.
- **`--backend-id`** auto-derives from `dataset_name` when not passed.

Resume = `python -m promptpotter optimize`. No re-`init` needed.

---

## Two trees: sessions + campaigns

Sessions and campaigns are separate. Today the relation is 1:1; the layout supports 1:N later without reorg.

- `{tenant_id}/sessions/{session_id}/` — operator metadata: `session.json`.
- `{tenant_id}/campaigns/{cycle_id}/` — per-cycle artifacts. Three bands: **root telemetry** (live observability — `dashboard.json`) at the family root cycle; **per-cycle audit** (`index.json`, `log.md`, `review.md`, `rounds/`, `langfuse/`, `prompts/`) at each cycle's top level; **per-cycle internals** (`.runtime/...`) under a `.runtime/` umbrella. Sibling cycles split by kind into `forks/`, `diag/`, `sweeps/`.
- `{tenant_id}/archive/` — the **measurement archive**, cross-cycle/session/tenant. See [`../concepts/scoring-and-memory.md`](../concepts/scoring-and-memory.md).

```
.promptpotter/
  active_session.json                  # { tenant_id, session_id, cycle_id }
  projects/{tenant_id}/
    sessions/{session_id}/
      session.json
    campaigns/{root_cycle_id}/         # family root (no parent_cycle_id)
      # ── Family telemetry (root only, shared across all forks) ──
      dashboard.json                   # live counters; cycle_id field tracks active fork
      # ── Root cycle's own audit ──
      index.json                       # config, phase, trial index, final block
      log.md                           # rendered narrative digest
      review.md                        # per-cycle review (M10)
      rounds/round_NNNN.json           # resume source of truth
      langfuse/                        # trace persistence
      prompts/{family}/{version}/      # rendered optimizer prompts
      # ── Root cycle's internals ──
      .runtime/
        ledger.jsonl                   # CycleLedger spine — typed Decision/Phase/Snapshot
        streams/round_NNNN_p_best.jsonl  # PoBB telemetry (rendered as sparkline in log.md)
        cache/
          rounds/round_NNNN.json       # per-round node I/O
          candidates/round_NNNN.json   # pre-scoring candidate checkpoint
        archived/resumed_at_{ts}/      # mid-cycle rewind sweepup (--from)
      # ── Sibling cycles ──
      forks/                           # --fork-on-divergence
        {root}_fork_xxx/               # per-cycle audit + .runtime/
      diag/                            # diagnostic-BFS auto-spawned
        {root}_diag_NNN/
      sweeps/{batch_id}/               # --sweep batches, grouped by batch_id
        index.json                     # batch metadata
        summary.md
        forks/{root}_sweep_{batch_id}_xxx/
    archive/                            # the measurement archive
      measurements/{run_id}.json
      measurements.json                # archive index
      backends/{backend_id}/
      prompt_aliases.json
      # AxisIndex + SampleIndex are in-memory only — rebuilt every refresh.
```

**Why split this way?** Telemetry is *temporal* — a stream that flows through whichever fork is active. Anchoring it at the family root means a single `tail dashboard.json` covers every fork. Audit is *structural* — frozen records keyed by the cycle that produced them. Sibling kinds (`forks/`, `diag/`, `sweeps/`) live in different parents because they answer different questions.

Prior evaluation results replay without backend calls when a new config shares a matching prefix with a stored run. `langfuse/events.jsonl` is a pure observability mirror — nothing reads it for state reconstruction. Resume / rewind are driven entirely by `rounds/round_NNNN.json`.

**Deprecated-sample eviction.** Entries whose `classify_result()` returns a fatal code are written normally for forensic analysis but evicted at load — never served as cache. Next encounter gets a fresh backend call, tagged `retry_of_deprecated_cache`. See [`../concepts/scoring-and-memory.md`](../concepts/scoring-and-memory.md#deprecated-samples).

---

## Cycle directory file reference

| File | Lives at | Updated | Content |
|------|----------|---------|---------|
| `dashboard.json` | family root | every event | Live state: round, baseline, best, candidates, counters. `cycle_id` field names the active fork. |
| `index.json` | per cycle | phase / finalize | Config, `pipeline_params`, `cycle_id`, `parent_cycle_id` (forks), `best_accuracy`, `trials[]`, `final` block (winner + stop_reason on completion). |
| `log.md` | per cycle | round-complete + finalize | Narrative digest. Pure derived view — safe to delete and recompute. |
| `review.md` | per cycle | round-complete + finalize | Per-cycle review (M10). |
| `rounds/round_NNNN.json` | per cycle | each completed round | Serialized `OptSearchPoint` for resume. |
| `langfuse/` | per cycle | during optimization | Trace shadow + `events.jsonl` mirror. Not read for state reconstruction. |
| `prompts/` | per cycle | when prompts render | Rendered optimizer prompts. |
| `.runtime/ledger.jsonl` | per cycle | every fact | Append-only `Decision` / `Phase` / `Snapshot` stream. |
| `.runtime/streams/round_NNNN_p_best.jsonl` | per cycle | per-sample | PoBB Posterior-of-Being-Best snapshots. |
| `.runtime/cache/rounds/round_NNNN.json` | per cycle | each round | Per-node I/O: l1_generate, l1_critique, l1_score, l2/l3 (when escalated). |
| `.runtime/cache/candidates/round_NNNN.json` | per cycle | each round's pre-scoring | Generated candidate checkpoint — overwritten next round. |
| `.runtime/archived/resumed_at_{ts}/` | per cycle | `--from` runs | Mid-cycle rewind sweepup. |
| `sweeps/{batch_id}/index.json` | family root | sweep mint + per-payload + finalize | Batch metadata: payload list with status. |

### `dashboard.json`

Scalar-only live dashboard. Atomically rewritten on every event. Carries display counters across cycles via `resume_from`. Key fields: `phase`, `round`, `layer`, `candidate`, `query`, `patience`, `baseline`, `best`, `current_acc`, `cycle_id`, `total_queries_scored`, `total_backend_calls`, `n_variants`, `sp_budget_ttest`. Post-mortem `stop_reason` is in `index.json::final::stop_reason`, not the live dashboard.

### `.runtime/cache/rounds/round_NNNN.json`

One JSON object per node that ran. Fields: `round`, `started_at`, `finished_at`, `nodes` (keyed by node type):

- `l1_generate`, `l1_critique`, `l2_context`, `l3_plan` — LLM meta-prompt calls. Each has `input.template_fields`, `input.variables`, `output.response`, `usage`, `model`, `duration_s`.
- `l1_score` — scoring phase. `input.candidates` lists what L1 generate produced; `output.candidates[*].stats` carries accuracy/composite/hits/total/invalid; `output.candidates[*].samples` lists per-sample outcomes (`qi`, `sample_id`, `hit`, `cached`, `time_s`, `terminated_at`, `input_tokens`, `output_tokens`, `prediction`, `ground_truth`, `query`).

### `rounds/round_NNNN.json`

The resume source of truth. On resume, `Cycle.restore_from_trial` rehydrates the exact optimizer state — no separate write-ahead log. You can edit a trial by hand between runs to modify optimizer state; keep the `opt_search_point` block round-trippable through `OptSearchPoint.model_validate`.

---

## Entry-point emission boundary

Entry points (notebook, CLI, `/potter-run`, API, webapp) MUST NOT write campaign artifacts directly. Writes go through two newtype-guarded projections in `promptpotter/infrastructure/projections/`: `LiveDashboardProjection` (family-root telemetry) and `AuditTrailProjection` (per-cycle audit). Both subscribe to the per-cycle `CycleLedger` (`infrastructure/ledger.py`) which persists every fact to `.runtime/ledger.jsonl`. Allowlists — `ROOT_TELEMETRY_ARTIFACTS`, `PER_CYCLE_OPERATOR_ARTIFACTS`, `PER_CYCLE_INTERNAL_UMBRELLA`, `SIBLING_GROUP_DIRS` — live in `tests/test_artifact_parity.py`.

---

## Recovery: resume, rewind, fork

Three workflows over the same fork primitive.

| Workflow | Command | Effect |
|----------|---------|--------|
| **Resume** | `optimize` | Pick up from latest completed round of the active cycle. |
| **Rewind** | `optimize --from N` | Same `cycle_id`; archive trials after round N; resume at round N+1. |
| **Fork on divergence** | `optimize --fork-on-divergence` | On scorer divergence, mint a sibling `cycle_id` rooted at the divergence point and continue under the current scorer. |
| **Sweep batch** | `optimize --sweep` (with payloads) | Mint N siblings under one root from operator-authored override files; run a 2-round sweep on each. |

Conceptual picture: [`../concepts/campaign-tree.md`](../concepts/campaign-tree.md).

### Rewind — `optimize --from N`

Use when the active campaign went down a path you don't want — e.g. a bad L3 replan, or you edited config and want to re-explore from a specific round. `cycle_id` stays the same; you're rolling back history inside it.

```bash
python -m promptpotter optimize --from 2
```

Archives `rounds/round_0003.json` onward into `campaigns/{cycle_id}/.runtime/archived/resumed_at_<ts>/`, rebuilds the round file index for rounds 0–2, restores optimizer state from round 2's trial, resumes at round 3.

- **Preserved:** the content-addressed measurement archive. Per-sample results unchanged under the new search replay from `archive/measurements/` without backend calls.
- **Discarded:** rounds after N are moved aside, not deleted. Inspectable in the archive directory.

**Editing optimizer state by hand.** Open `campaigns/{cycle_id}/rounds/round_{N:04d}.json` and edit before `optimize --from N`. Keep the `opt_search_point` block shape round-trippable. Schema: [`../developer/self-healing-internals.md`](../developer/self-healing-internals.md).

### Fork — `optimize --fork-on-divergence`

Use when the scoring formula changed and resume detects that decisions recorded under the old scorer don't match rescored results under the new scorer. The optimizer halts rather than drift silently. Two choices: revert the scoring change, or commit by rerunning with `--fork-on-divergence`.

```bash
python -m promptpotter optimize --fork-on-divergence
```

Mints a new `cycle_id` rooted at the divergence point, copies pre-divergence trials into the new cycle, records `parent_cycle_id`, retargets the active session pointer, re-runs the divergent round under the current scorer. The shared `archive/measurements/` archive is **not duplicated** — both cycles read the same measurements, each through their own scoring ledger.

**Layout after a fork:**

- **Live telemetry** (`dashboard.json`, `output.log`) **stays at the family root** (`campaigns/{root_cycle_id}/` — the cycle with no `parent_cycle_id`). One stream covers the whole family. `output.log` gets a `=== FORK <id> from round N (parent: …) ===` banner; `dashboard.json::cycle_id` always names the active fork.
- **Per-cycle audit** (`index.json`, `log.md`, `rounds/`, `langfuse/`, `prompts/`, `.runtime/`) **lives in the fork's own dir** under `campaigns/{root_cycle_id}/forks/{cycle_id}/`. The parent's audit stays frozen as the historical record.

To monitor a forked run: tail the **root**, not the fork. To inspect a specific fork's history: open the fork's `index.json` / `log.md` / `rounds/`.

**Why rewind is not enough:** rewind restarts under the same policy; fork restarts under a different policy. If scoring changed, rewind would re-run decisions the recorded history expects to match, and halt again on the same divergence. Fork cuts the cord. See [`../concepts/scoring-and-memory.md`](../concepts/scoring-and-memory.md).

### Sweep batch — `optimize --sweep` with payloads

Breadth-first comparison of N L1-prompt hypotheses. Instead of one cheap-trial cycle on the active OSP, mints N cheap-trial siblings under one parent, each starting from a different operator-authored override.

**Per-fork protocol:** baseline (cache-hit after the first fork) + 1 full scored round + 1 generation-only round + halt with `SWEEP_COMPLETE`. The leaderboard pairs sweep cycles with their full counterparts via `proxy_lift_corr` once at least 4 paired branches exist.

**Authoring a payload.** One JSON file per candidate under `datasets/{name}/sweep/`. Schema (`SweepPayload`) — the L1-surface fields L2 already mutates, plus a `reason` label:

```json
{
  "reason": "step-by-step layout",
  "l1_layout": {
    "task_intent": ["task_context"],
    "problem_description": ["rendered_prompt", "pipeline_param_catalogue", "plan", "diagnostics", "failures", "critique"]
  }
}
```

Every field optional; `reason` defaults to empty string. The Pydantic model is `extra='forbid'` — typos raise `ValidationError` at parse time, before any fork mints.

| Field | Effect on L1 |
|-------|--------------|
| `l1_layout` | Per-slot list of signal names; stamped onto `OptSearchPoint.l1_layout`. Mandatory placeholders `{plan, task_context, rendered_prompt, pipeline_param_catalogue, critique}` must appear somewhere across the four slots. |

This is the same L1-surface field L2 writes when it fires — sweep just lets the operator stage one without firing L2. See [`../developer/l1-generate-surface.md`](../developer/l1-generate-surface.md).

**Running a batch:**

```bash
python -m promptpotter init --backend-url http://127.0.0.1:8000 --config datasets/bbeh/campaign.json
python -m promptpotter optimize --sweep
```

The runner: parses every `*.json` under `datasets/{name}/sweep/` (sorted by filename), mints a fork per payload, stamps the payload's overrides onto the fork's starting OSP, runs round 1 scored + round 2 generation-only + halt, restores the active session pointer to root.

**Reading results.** Each fork produces:

- `campaigns/{root}/forks/{fork_id}/rounds/round_0001.json` — round 1 scored.
- `campaigns/{root}/forks/{fork_id}/rounds/round_0002.json` — `status: "generation_only"`, no `composite`/`accuracy`.
- `campaigns/{root}/forks/{fork_id}/review.md` — per-fork review.
- `campaigns/{root}/forks/{fork_id}/index.json::final.mode == "sweep"`.

Side-by-side: `python scripts/ppot_review.py --sweep`. Sweep view groups by parent root, sorts by `round_1_top_lift` desc, reports `proxy_lift_corr` once at least 4 paired (sweep, full) branches share an `l1_generate_hash`.

**Sweep is screening, not validation.** Promote winners to a full `optimize` run. Sweep is for L1-surface overrides — pipeline / scoring changes are intentionally absent from `SweepPayload`. Forks run sequentially (the active session pointer doesn't tolerate concurrent mints).

---

## Steering composite scoring between rounds

The cycle's per-round formula can be hot-swapped between rounds by dropping a JSON file. The next round-end consumes it; the running optimizer never restarts.

### File-drop mechanism

1. Author a new `per_round` formula. The namespace is the active per-round evaluator registry — check `evaluators` in any `rounds/round_NNNN.json` for valid names.
2. Write `campaigns/{cycle_id}/scoring_steer.json`:

   ```json
   {"per_round": "0.5 * accuracy + 0.3 * prompt_compactness + 0.2 * latency_norm"}
   ```

3. Wait for the next round to complete. The operator log emits a `scoring_steer applied` phase event.

Under the hood: file is shape-validated (JSON object with non-empty string `per_round`), formula is smoke-compiled against a synthetic namespace (every registered evaluator at `0.5`) so undefined names or syntax errors fail before swap. On success, `session.round_scorer` is replaced and the file renamed to `scoring_steer.applied.{ts}.json`. On failure the running formula stays untouched and the file stays in place — fix and the next round retries.

### Available names

Gated by `applies(schema)` — only present when the corresponding pipeline node is active.

| Name | Range | Meaning |
| --- | --- | --- |
| `accuracy` | `[0, 1]` | Mean per-sample score |
| `error_rate` | `[0, 1]` | Fraction of errored queries |
| `degraded_rate` | `[0, 1]` | Fraction with degradation warnings |
| `runtime_failure_rate` | `[0, 1]` | OptSP runtime-failure count, normalized |
| `latency_norm` | `[0, 1]` | `1 - mean_ms / 10_000`; 1.0 = instant |
| `prompt_compactness` | `[0, 1]` | `1 - len(rendered_prompt) / 4_000`; 1.0 = short |
| `pipeline_compactness` | `[0, 1]` | `1 - (active_steps - 1) / 11`; 1.0 = single-node |
| `source_recall` | `[0, 1]` | GT in candidate-source output (when active) |
| `candidate_recall` | `[0, 1]` | GT in ranker `final_ranking` (when active) |
| `cache_hit_rate` | `[0, 1]` | Cache-node short-circuit fraction |
| `mean_retrieval_shortfall` | `[0, 1]` | Mean `min(observed/target, 1.0)` across `max_*`/`num_*` nodes |

Helpers: `min`, `max`, `float`, `int`, `bool`, `abs`, `round`, `log`, `sqrt`, `exp`, `pow`. Output clamped to `[0, 1]`. Undefined names raise `NameError` — fail loud is the contract.

### When NOT to steer

Per-sample steering is intentionally not supported by file-drop. Changing `compile_scorer` mid-run rewrites recorded `hit`/`score` semantics on every prior trace, triggering the divergence-replay walker on next resume. The right tool there is `optimize --fork-on-divergence`, which forks a new cycle from the divergence point under the new policy.

### Composite block in operator surfaces

**Per-candidate (1 line):** `composite=0.6042  (Δ+0.1030 vs baseline 0.5012)`.

**Round summary (3 lines, log.md):**

```
composite = 0.6042   baseline=0.5012  Δ+0.1030
formula:  0.65*acc + 0.15*H + 0.10*lat + 0.05*R + 0.05*pc
  acc=0.667  err=0.000  degr=0.083  rf=0.000  lat=0.965  pc=0.812
```

`H` is health `((1-error_rate) + (1-degraded_rate) + (1-runtime_failure_rate)) / 3`; `R` is the average of applicable recall evaluators. Custom formulas (`campaign.json::scoring`) render verbatim. `log.md` always carries the full formula text — source of truth when reviewing finished cycles.

### Code references

- Evaluator registry + default formula: `promptpotter/application/scoring/evaluators.py`
- Composite computation: `promptpotter/application/scoring/metrics.py::compute_composite_score`
- Hot-swap module: `promptpotter/application/scoring/formula.py`
- Per-round trajectory mirror: `promptpotter/domain/opt_search_point.py::RoundSummary`
