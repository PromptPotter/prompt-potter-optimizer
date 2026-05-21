# Persistence, State, and Recovery

Your work lives in `.promptpotter/`. Two trees:

- `sessions/{session_id}/` — your operator workspace (journal, notes).
- `campaigns/{campaign_id}/` — one campaign per directory; every cycle (all session roots + forks + diag + sweeps) flat under `cycles/`.

The rest of this page covers the four-entity model, the active-session pointer, what each file holds, and the three recovery workflows (resume, rewind, fork).

---

## Four entities: Workspace, Dataset, Campaign, Cycle

PromptPotter's persisted world is a strict containment hierarchy:

- **Workspace** — the tenant-level container and queryable datastore (`projects/{tenant}/`): every dataset, every campaign, the shared `archive/`.
- **Dataset** — the optimization target plus its config (`datasets/{name}/`).
- **Campaign** — one declared optimization effort: a dataset, a pipeline origin, and context text. A first-class entity with its own directory and `campaign.json` manifest, and a **forest** — it holds N **sessions**. `campaign_id = {dataset}__{origin_content_hash}`, where `origin_content_hash` is the origin declaration's 12-hex content hash (the same hash that is the root cycle id). The id is **stable**: re-running `new <dataset>` on an unchanged declaration resolves to the *same* campaign (find-or-create). The dataset is embedded so "campaigns for dataset X" is a prefix scan.
- **Session** — one run of `new` on a campaign's declaration. A campaign holds N sessions; re-running `new` on the same declaration **adds** a session. `resume` extends the *active* session — it does not add one. A session's identity is its `session_id` (`s_xxxx`). Each session is a tree: a root cycle plus its fork descendants. The session root cycle id is `cycle_{hash}` for session 1 and `cycle_{hash}_s{N}` for session N — the `_s{N}` suffix only disambiguates the directory, it is **not** a sibling separator (`root_cycle_id` / `sibling_kind` treat `cycle_X_s2` as its own family root, `cycle_X_s2_fork_abc` as a fork rooted at it).
- **Cycle** — one node in a session's lineage tree: root | fork | diag | sweep. Identity is `cycle_{content_hash[:12]}` (+ the `_s{N}` session-root suffix, or `_fork_`/`_diag_`/`_sweep_` for branches); `cycle_id` is campaign-scoped, so path resolution is always `(campaign_id, cycle_id)`.

The **Session** is a unit of a campaign — its identity is the `session_id`. `active_session.json` is your active pointer/lens into the Workspace: which tenant, session, campaign, and cycle are live.

---

## Active session pointer

PromptPotter remembers which session you're on via `.promptpotter/active_session.json` — `{tenant_id, session_id, campaign_id, cycle_id}`, like a browser's active tab.

- **`new`** find-or-creates the Campaign for the dataset's declaration (`campaign_id` is the stable `{dataset}__{origin_content_hash}`), mints a fresh Session + its root cycle inside it, and overwrites the pointer. Re-running `new` on an unchanged declaration adds another session to the *same* campaign.
- **`resume`** reads `{campaign_id, cycle_id}` and operates on that cycle automatically, extending the active session.
- **`fork`** mints a new cycle inside the same session and retargets the pointer's `cycle_id`.
- **`--session <id>`** overrides the pointer for one command.
- **`--backend-id`** auto-derives from `dataset_name` when not passed.

Resume = `python -m promptpotter resume`. No re-`new` needed.

---

## Two trees: sessions + campaigns

Sessions and campaigns are separate. The Session is a pointer/lens; the Campaign is the entity.

- `{tenant_id}/sessions/{session_id}/` — operator metadata: `session.json`.
- `{tenant_id}/campaigns/{campaign_id}/` — one campaign per directory. Three bands: **campaign-level artifacts** (`campaign.json` manifest, `log.md` campaign digest, `hard_samples.json`) at the campaign root; **per-cycle audit** (`index.json`, `log.md`, `review.md`, `rounds/`, `langfuse/`, `prompts/`, and `dashboard.json` for session-root cycles) inside each `cycles/{cycle_id}/`; **per-cycle internals** (`.runtime/...`) under a `.runtime/` umbrella. Every cycle — all N session roots, plus their forks, diag, sweeps — sits **flat** under `cycles/`; sibling kind and sweep batch id are `index.json` metadata, not directory nesting. `dashboard.json` is per-session: it lives in the session's root cycle dir and is shared by that session's forks.
- `{tenant_id}/archive/` — the **measurement archive**, cross-cycle/session/tenant — a peer of `campaigns/`. See [`../concepts/scoring-and-memory.md`](../concepts/scoring-and-memory.md).

```
.promptpotter/
  active_session.json                  # { tenant_id, session_id, campaign_id, cycle_id }
  projects/{tenant_id}/
    sessions/{session_id}/
      session.json
    campaigns/{campaign_id}/            # one campaign — {dataset}__{origin_content_hash}
      # ── Campaign-level artifacts ──
      campaign.json                    # manifest: dataset_name, label, created_at, status,
                                       #   root_cycle_id, root_content_hash, backend_id, config
      log.md                           # campaign digest — every session + its forks + rounds, heatmap
      hard_samples.json                # campaign-scope hard-sample artifact (all the campaign's cycles)
      # ── Cycles — every session root + forks + diag + sweeps, ALL FLAT ──
      cycles/{session_root_cycle_id}/   # session 1 root: cycle_{hash}; session N: cycle_{hash}_s{N}
        dashboard.json                 # live PER-SESSION telemetry; shared by this session's forks
        index.json                     # phase, trial index, final block, sibling_kind: root
        log.md                         # per-cycle narrative digest
        review.md                      # per-cycle review (M10)
        rounds/round_NNNN.json         # resume source of truth
        hard_samples.json              # cycle-scope hard-sample artifact (this cycle only)
        langfuse/                      # trace persistence
        prompts/{family}/{version}/    # rendered optimizer prompts
        .runtime/
          ledger.jsonl                 # CycleEventLog spine — typed Decision/Phase/Snapshot
          streams/round_NNNN_p_best.jsonl  # PoBB telemetry (rendered as sparkline in log.md)
          cache/
            rounds/round_NNNN.json     # per-round node I/O
            candidates/round_NNNN.json # pre-scoring candidate checkpoint
          archived/resumed_at_{ts}/    # mid-cycle rewind sweepup (--from)
      cycles/{fork_cycle_id}/           # a fork — flat alongside its session root
        index.json                     # parent_cycle_id, sibling_kind, sweep_batch_id?
        rounds/ langfuse/ prompts/ .runtime/   # (no dashboard.json — shared from the session root)
    archive/                            # the measurement archive (peer of campaigns/)
      measurements/{run_id}.json
      measurements.json                # archive index
      backends/{backend_id}/
      prompt_aliases.json
      # AxisIndex + SampleIndex are in-memory only — rebuilt every refresh.
```

**Why split this way?** Telemetry is *temporal* — a stream that flows through whichever cycle of a session is active. Anchoring it at the session's root cycle means a single `tail cycles/{session_root}/dashboard.json` covers that session and all its forks. A campaign carries N such streams, one per session. Audit is *structural* — frozen records keyed by the cycle that produced them, where per-cycle detail belongs. The flat `cycles/` store is deliberate: a fork tree keyed by `parent_cycle_id` metadata scales where nested fork-of-fork directories do not.

**Why Campaign is first-class.** The campaign directory is self-describing: `campaigns/justlogic__a1b2c3d4e5f6/` groups every session run against one declared origin — dataset grouping is a prefix scan, and re-running `new` on the unchanged declaration find-or-creates the same campaign rather than scattering hash-soup directories. The campaign root is self-describing — `campaign.json` (manifest), `log.md` (human digest spanning every session) — and each session's `dashboard.json` lives in its root cycle, so you can read a campaign and any of its live sessions without guessing directory names.

Prior evaluation results replay without backend calls when a new config shares a matching prefix with a stored run. `langfuse/events.jsonl` is a pure observability mirror — nothing reads it for state reconstruction. Resume / rewind are driven entirely by `rounds/round_NNNN.json`.

**Deprecated-sample eviction.** Entries whose `classify_result()` returns a fatal code are written normally for forensic analysis but evicted at load — never served as cache. Next encounter gets a fresh backend call, tagged `retry_of_deprecated_cache`. See [`../concepts/scoring-and-memory.md`](../concepts/scoring-and-memory.md#deprecated-samples).

---

## Campaign + cycle file reference

| File | Lives at | Updated | Content |
|------|----------|---------|---------|
| `campaign.json` | campaign dir | mint + finalize | Manifest: `dataset_name`, `label`, `created_at`, `status`, `root_cycle_id`, `root_content_hash`, `backend_id`, `config` (the frozen `CampaignConfig` snapshot — single owner). |
| `dashboard.json` | session-root cycle dir | every event | Live PER-SESSION state: round, origin, best, candidates, counters. `cycle_id` field names the active cycle. One stream per session; a session's forks share their session root's `dashboard.json`. |
| `log.md` (campaign) | campaign dir | round-complete + finalize | Campaign digest: status, best cycle, every session + its forks + rounds, campaign-scoped heatmap. |
| `hard_samples.json` (campaign) | campaign dir | round-complete + finalize | Campaign-scope hard-sample artifact — aggregated across all the campaign's cycles. |
| `index.json` | per cycle | phase / finalize | `pipeline_params`, `cycle_id`, `parent_cycle_id` (forks), `sibling_kind`, `sweep_batch_id` (sweeps), `best_accuracy`, `trials[]`, `final` block (winner + stop_reason on completion). The frozen config lives in `campaign.json`, not here. |
| `log.md` (cycle) | per cycle | round-complete + finalize | Per-cycle narrative digest. Pure derived view — safe to delete and recompute. |
| `review.md` | per cycle | round-complete + finalize | Per-cycle review (M10). |
| `rounds/round_NNNN.json` | per cycle | each completed round | Serialized `OptSearchPoint` for resume. |
| `hard_samples.json` (cycle) | per cycle | round-complete + finalize | Cycle-scope hard-sample artifact — this cycle only. |
| `langfuse/` | per cycle | during optimization | Trace shadow + `events.jsonl` mirror. Not read for state reconstruction. |
| `prompts/` | per cycle | when prompts render | Rendered optimizer prompts. |
| `.runtime/ledger.jsonl` | per cycle | every fact | Append-only `Decision` / `Phase` / `Snapshot` / `LLMCall` / `TokenUsage` stream. Escalation-rule firings ride on `PhaseRecord(phase="escalation", event="rule_fired")` — there is no separate signals stream. |
| `.runtime/streams/round_NNNN_p_best.jsonl` | per cycle | per-sample | PoBB Posterior-of-Being-Best snapshots. |
| `.runtime/cache/rounds/round_NNNN.json` | per cycle | each round | Per-node I/O: l1_generate, l1_critique, l1_score, l2/l3 (when escalated). |
| `.runtime/cache/candidates/round_NNNN.json` | per cycle | each round's pre-scoring | Generated candidate checkpoint — overwritten next round. |
| `.runtime/archived/resumed_at_{ts}/` | per cycle | `--from` runs | Mid-cycle rewind sweepup. |
| `.runtime/inner/{outer_round}/{sample_idx}/` | per cycle | PromptPotter-as-connector only | Isolated inner-cycle sub-tree (own `sessions/`, `campaigns/`, `archive/`). Each outer "sample" gets its own root; pruned at outer cycle finalize unless `optimization.retain_inner_cycles: true`. See `docs/specs/m12-multi-connector.md`. |

### `campaign.json`

The Campaign manifest — one per campaign directory. Fields: `campaign_id`, `dataset_name`, `label` (operator-readable name), `created_at`, `status`, `root_cycle_id`, `root_content_hash` (the origin `JobSearchPoint` content hash, stored once for drift comparison), `backend_id`, and `config` (the frozen `CampaignConfig` dump — the single config-snapshot owner for the whole campaign, no per-cycle copies). Resume's drift check recomputes the current config's content hash and compares it to `root_content_hash` — a stored-value comparison, not a directory-name match.

### `dashboard.json`

Scalar-only live dashboard, **per-session** — at the session's root cycle dir (`cycles/{session_root}/dashboard.json`), shared by that session's forks. Atomically rewritten on every event. Carries display counters across the session's cycles via `resume_from`. Key fields: `phase`, `round`, `layer`, `candidate`, `query`, `patience`, `origin`, `best`, `current_acc`, `cycle_id`, `total_queries_scored`, `total_backend_calls`, `n_variants`, `sp_budget_ttest`. A campaign with N sessions has N independent `dashboard.json` streams. Post-mortem `stop_reason` is in `index.json::final::stop_reason`, not the live dashboard.

### `.runtime/cache/rounds/round_NNNN.json`

One JSON object per node that ran. Fields: `round`, `started_at`, `finished_at`, `nodes` (keyed by node type):

- `l1_generate`, `l1_critique`, `l2_context`, `l3_plan` — LLM meta-prompt calls. Each has `input.template_fields`, `input.variables`, `output.response`, `usage`, `model`, `duration_s`.
- `l1_score` — scoring phase. `input.candidates` lists what L1 generate produced; `output.candidates[*].stats` carries accuracy/composite/hits/total/invalid; `output.candidates[*].samples` lists per-sample outcomes (`qi`, `sample_id`, `hit`, `cached`, `time_s`, `terminated_at`, `input_tokens`, `output_tokens`, `prediction`, `ground_truth`, `query`).

### `rounds/round_NNNN.json`

The resume source of truth. On resume, `Cycle.replay_priors` walks every prior `round_NNNN.json` in order and reconstructs `cycle.rounds` (typed `RoundResult` list), `cycle.tracking` (current = last round, best = highest-composite across priors), and `cycle.opt_sp` (last round's snapshot) — no separate write-ahead log. You can edit a trial by hand between runs to modify optimizer state; keep the `opt_search_point` block round-trippable through `OptSearchPoint.model_validate`.

---

## Entry-point emission boundary

Entry points (notebook, CLI, `/potter-run`, API, webapp) MUST NOT write campaign artifacts directly. Writes go through two newtype-guarded projections in `promptpotter/infrastructure/projections/`: `LiveDashboardView` (per-session telemetry, written into the session-family root cycle dir) and `AuditTrailView` (per-cycle audit). Both subscribe to the per-cycle `CycleEventLog` (`infrastructure/ledger.py`) which persists every fact to `.runtime/ledger.jsonl`. Allowlists — covering the campaign-level artifacts (`campaign.json`, campaign `log.md`, `hard_samples.json`), the per-cycle operator artifacts (including `dashboard.json` on session-root cycles), and the `.runtime/` internal umbrella — live in `tests/test_invariants.py`.

---

## Recovery: resume, rewind, fork

Three workflows over the same fork primitive.

| Workflow | Command | Effect |
|----------|---------|--------|
| **Resume** | `resume` | Pick up from latest completed round of the active cycle. |
| **Rewind** | `resume --from N` | Same `cycle_id`; archive trials after round N; resume at round N+1. |
| **Fork on divergence** | `resume --fork-on-divergence` | On scorer divergence, mint a sibling `cycle_id` rooted at the divergence point and continue under the current scorer. |
| **Sweep batch** | `new --sweep-batch` (with payloads) | Mint N siblings under one root from operator-authored override files; run a 2-round sweep on each. |

Conceptual picture: [`../concepts/campaign-tree.md`](../concepts/campaign-tree.md).

### Rewind — `resume --from N`

Use when the active cycle went down a path you don't want — e.g. a bad L3 replan, or you edited config and want to re-explore from a specific round. `cycle_id` stays the same; you're rolling back history inside it.

```bash
python -m promptpotter resume --from 2
```

Archives `rounds/round_0003.json` onward into `campaigns/{campaign_id}/cycles/{cycle_id}/.runtime/archived/resumed_at_<ts>/`, rebuilds the round file index for rounds 0–2, restores optimizer state from round 2's trial, resumes at round 3.

- **Preserved:** the content-addressed measurement archive. Per-sample results unchanged under the new search replay from `archive/measurements/` without backend calls.
- **Discarded:** rounds after N are moved aside, not deleted. Inspectable in the archive directory.

**Editing optimizer state by hand.** Open `campaigns/{campaign_id}/cycles/{cycle_id}/rounds/round_{N:04d}.json` and edit before `resume --from N`. Keep the `opt_search_point` block shape round-trippable. Schema: [`../developer/self-healing-internals.md`](../developer/self-healing-internals.md).

#### Interrupted rounds

If you Ctrl+C mid-round, the ledger has the partial events but the round never received `round:complete`. On teardown the runner drains projections: `.runtime/cache/rounds/round_NNNN.json` is written with `"interrupted": true` so post-mortem readers can see what landed. The public `rounds/round_NNNN.json` stays absent — a partial round is not a complete round — and `index.json` records `status: "interrupted"` + `interrupted_round: N`. `--from M` admissibility consults the ledger: `M` is valid iff round `M` has a closing PhaseRecord (`round:complete`, or `origin:exit` for round 0). After an interrupt mid-round-1, `--from 1` correctly refuses with `"ledger only has completed rounds 0..0"`; `--from 0` resumes cleanly.

### Fork — `resume --fork-on-divergence`

Use when a **data-affecting** config edit (scoring formula, `optimizer_llm.provider`/`model`, `pipeline_overrides`, `exclude_nodes`, `dataset_name`) causes resume's decision-replayer to detect that recorded decisions don't match rederived ones. The optimizer halts rather than drift silently. Two choices: revert the change, or commit by rerunning with `--fork-on-divergence`.

Policy-only edits (PoBB knobs, patience, thresholds, `n_variants`, `exploration.*`) take a different path: `resume_with_divergence_check` reads the frozen config from `campaign.json::config`, classifies the diff via `CampaignConfig.classify_diff_against`, recognizes the change can't have affected the data trace, and continues in-place on the same cycle. Past decisions stay as the audit record of the policy that decided them; the new policy governs unevaluated rounds. The `--fork-on-divergence` flag is a no-op for this case.

```bash
python -m promptpotter resume --fork-on-divergence
```

Mints a new `cycle_id` **inside the same session**, rooted at the divergence point, copies pre-divergence trials into the new cycle, records `parent_cycle_id`, retargets the active session pointer's `cycle_id`, re-runs the divergent round under the current scorer. A fork is a new Cycle, not a new Session or Campaign — the campaign's `campaign.json` config and `root_content_hash` are unchanged. The shared `archive/measurements/` archive is **not duplicated** — both cycles read the same measurements, each through their own scoring ledger.

**Layout after a fork:**

- **Live telemetry** (`dashboard.json`, `output.log`) **stays at the session's root cycle dir** (`cycles/{session_root}/`). One stream covers the whole session, forks included — a fork's family root is its session root. `output.log` gets a `=== FORK <id> from round N (parent: …) ===` banner; `dashboard.json::cycle_id` always names the active cycle.
- **Per-cycle audit** (`index.json`, `log.md`, `rounds/`, `langfuse/`, `prompts/`, `.runtime/`) **lives in the fork's own dir** under `campaigns/{campaign_id}/cycles/{cycle_id}/` — flat alongside the session root cycle. The fork's `index.json` carries `parent_cycle_id` and `sibling_kind: "fork"`. The parent's audit stays frozen as the historical record.

To monitor a forked run: tail the session root's `dashboard.json`, not the fork. To inspect a specific fork's history: open the fork's `index.json` / `log.md` / `rounds/` under `cycles/{cycle_id}/`.

**Why rewind is not enough:** rewind restarts under the same policy; fork restarts under a different policy. If scoring changed, rewind would re-run decisions the recorded history expects to match, and halt again on the same divergence. Fork cuts the cord. See [`../concepts/scoring-and-memory.md`](../concepts/scoring-and-memory.md).

### Sweep batch — `new --sweep-batch` with payloads

Breadth-first comparison of N L1-prompt hypotheses. Instead of one cheap-trial cycle on the active OSP, mints N cheap-trial sibling cycles inside the active campaign, each starting from a different operator-authored override. Sweep cycles sit flat under `cycles/` alongside the root — each one's `index.json` carries `sibling_kind: "sweep"` and a shared `sweep_batch_id`; the batch is a metadata grouping, not a directory.

**Per-fork protocol:** origin (cache-hit after the first fork) + 1 full scored round + 1 generation-only round + halt with `SWEEP_COMPLETE`. The leaderboard pairs sweep cycles with their full counterparts via `proxy_lift_corr` once at least 4 paired branches exist.

**Authoring a payload.** One JSON file per candidate under `datasets/{name}/sweep/`. Schema (`OperatorSweepFile`) — the L1-surface fields L2 already mutates, plus a `reason` label. The dispatcher widens each parsed file into a `ForkPayload(trigger=OPERATOR_SWEEP, ...)` before calling `_mint_fork`, so operators never write trigger/issued_by boilerplate by hand.

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
python -m promptpotter new bbeh --backend-url http://127.0.0.1:8000
# Active session now points at the freshly-minted bbeh cycle; --sweep-batch on
# the next call dispatches sweep-mode against it:
python -m promptpotter new --sweep-batch
```

The runner: parses every `*.json` under `datasets/{name}/sweep/` (sorted by filename), mints a fork per payload, stamps the payload's overrides onto the fork's starting OSP, runs round 1 scored + round 2 generation-only + halt, restores the active session pointer to root.

**Reading results.** Each sweep cycle produces:

- `campaigns/{campaign_id}/cycles/{sweep_cycle_id}/rounds/round_0001.json` — round 1 scored.
- `campaigns/{campaign_id}/cycles/{sweep_cycle_id}/rounds/round_0002.json` — `status: "generation_only"`, no `composite`/`accuracy`.
- `campaigns/{campaign_id}/cycles/{sweep_cycle_id}/review.md` — per-cycle review.
- `campaigns/{campaign_id}/cycles/{sweep_cycle_id}/index.json::final.mode == "sweep"`, with `sweep_batch_id` linking the batch.

Side-by-side: `python scripts/ppot_review.py --sweep`. Sweep view groups by parent root, sorts by `round_1_top_lift` desc, reports `proxy_lift_corr` once at least 4 paired (sweep, full) branches share an `l1_generate_hash`.

**Sweep is screening, not validation.** Promote winners to a full `new` run. Sweep is for L1-surface overrides — pipeline / scoring changes are intentionally absent from the operator file shape (the unified `ForkPayload` reserves `pipeline_swap` / `scoring_swap` slots for M11/M12 LLM-rebase callers, but operators don't author those). Forks run sequentially (the active session pointer doesn't tolerate concurrent mints).

---

## Steering composite scoring between rounds

The cycle's per-round formula can be hot-swapped between rounds by dropping a JSON file. The next round-end consumes it; the running optimizer never restarts.

### File-drop mechanism

1. Author a new `per_round` formula. The namespace is the active per-round evaluator registry — check `evaluators` in any `rounds/round_NNNN.json` for valid names.
2. Write `campaigns/{campaign_id}/cycles/{cycle_id}/scoring_steer.json`:

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

Per-sample steering is intentionally not supported by file-drop. Changing `compile_scorer` mid-run rewrites recorded `hit`/`score` semantics on every prior trace, triggering the divergence-replay walker on next resume. The right tool there is `resume --fork-on-divergence`, which forks a new cycle from the divergence point under the new policy.

### Composite block in operator surfaces

**Per-candidate (1 line):** `composite=0.6042  (Δ+0.1030 vs origin 0.5012)`.

**Round summary (3 lines, log.md):**

```
composite = 0.6042   origin=0.5012  Δ+0.1030
formula:  0.65*acc + 0.15*H + 0.10*lat + 0.05*R + 0.05*pc
  acc=0.667  err=0.000  degr=0.083  rf=0.000  lat=0.965  pc=0.812
```

`H` is health `((1-error_rate) + (1-degraded_rate) + (1-runtime_failure_rate)) / 3`; `R` is the average of applicable recall evaluators. Custom formulas (`campaign.json::scoring`) render verbatim. `log.md` always carries the full formula text — source of truth when reviewing finished cycles.

### Code references

- Evaluator registry + default formula: `promptpotter/application/scoring/evaluators.py`
- Composite computation: `promptpotter/application/scoring/metrics.py::compute_composite_score`
- Hot-swap module: `promptpotter/application/scoring/formula/`
- Per-round trajectory: in-memory `Cycle.rounds` (transient); persistent trajectory derives from ledger events.
