# Persistence, State, and Recovery

Your work lives in `.promptpotter/`, in two trees:

- `sessions/{session_id}/` — your operator workspace (journal, notes).
- `campaigns/{campaign_id}/` — one campaign per directory; every cycle (session roots + forks + diags + sweeps) sits flat under `cycles/`.

## Four entities

A strict containment hierarchy: **Workspace → Dataset → Campaign → Cycle**.

- **Workspace** (`projects/{tenant}/`) — the tenant's datastore: every dataset, every campaign, the shared `archive/`.
- **Dataset** — the optimization target plus its config. Resolved tenant-first: a tenant upload (`projects/{tenant}/datasets/{slug}/`, the `new <file>` ingest path) wins over a repo benchmark (`datasets/{name}/`). An ingested slug is first-class to both `new <slug>` and `resume`, not just the mint that made it.
- **Campaign** — one declared optimization effort: a dataset, a pipeline origin, context text. `campaign_id = {dataset}__{rand6_hex}`, minted fresh per `new`. The declaration's hashes (`root_content_hash`, `optimizer_prompt_hash`) ride `campaign.json` for drift detection on resume — not as the id. Dataset is embedded, so "campaigns for dataset X" is a prefix scan.
- **Cycle** — one node in a campaign's lineage tree: root | fork | diag | sweep. Id is `cycle_{content_hash[:12]}` (+ `_fork_`/`_diag_`/`_sweep_` on branches). Path resolution is always `(campaign_id, cycle_id)`.

A **Session** is one `new` invocation; a campaign holds one. `resume` extends it; `resume --fork-on-divergence` adds sibling cycles. Each session is a tree: a root cycle plus its fork descendants.

## Active session pointer

`.promptpotter/active_session.json` (`{tenant_id, session_id, campaign_id, cycle_id}`) is your active tab.

- **`new`** mints a fresh campaign + session + root cycle and overwrites the pointer. Re-running `new` on an unchanged declaration reuses the content-addressed root-cycle id and origin score (cache-served), then diverges from round 1.
- **`resume`** reads the pointer and picks up that cycle. No re-`new` needed.
- **fork** mints a new cycle in the same session and retargets the pointer.
- **`--session <id>`** overrides the pointer for one command.
- **`--tenant <id>`** (default `"default"`) selects the partition under `projects/` for the command.

Every subcommand runs as `python -m promptpotter [--tenant <id>] <subcommand> [options]`. Loop-mint verbs: `new`, `resume`. Lifecycle verbs: `archive`, `delete`, `unarchive`, `reset`. Diagnostic verbs: `verify`, `ab`, `sweep`. Reads happen by opening the on-disk artifact tree — there is no read CLI.

## Layout

```
.promptpotter/
  active_session.json                  # { tenant_id, session_id, campaign_id, cycle_id }
  projects/{tenant_id}/
    sessions/{session_id}/session.json
    campaigns/{campaign_id}/            # {dataset}__{rand6_hex}, fresh per `new`
      campaign.json                    # manifest (dataset, status, config snapshot, declaration hashes)
      log.md                           # campaign digest — session + forks + rounds + heatmap
      hard_samples.json                # campaign-scope hard-sample artifact
      cycles/{cycle_id}/               # session root + forks + diags + sweeps, ALL FLAT
        dashboard.json                 # live per-cycle telemetry (forks carry their own, seeded at the cut)
        index.json                     # phase, trials, final block, sibling_kind, parent_cycle_id (forks)
        log.md  review.md              # per-cycle digests (derived — safe to recompute)
        rounds/round_NNNN.json         # resume source of truth (serialized OptSearchPoint)
        langfuse/  prompts/            # trace shadow; rendered optimizer prompts
        .runtime/
          ledger.jsonl                 # append-only Decision/Phase/Snapshot/LLMCall/TokenUsage spine
          streams/round_NNNN_p_best.jsonl   # PoBB telemetry (sparkline in log.md)
          cache/rounds|candidates/     # per-round node I/O + pre-scoring checkpoint
          archived/resumed_at_{ts}/    # mid-cycle rewind sweepup (--from)
    measurements/                       # measurement store (DB core) — cross-cycle/session/tenant, peer of campaigns/
      {run_id}.json  measurements_index.json  prompt_aliases.json
    archive/{campaign_id}/              # recycle bin — `archive` MOVES a campaign tree here; `unarchive` moves it back
```

**Why this shape.** Telemetry is *temporal* — each cycle owns its `dashboard.json`, so `tail cycles/{cycle_id}/dashboard.json` follows exactly the cycle you're watching (a fork seeds from its parent, then diverges). Audit is *structural* — frozen records keyed by the cycle that produced them. Cycles sit flat because a fork tree keyed by `parent_cycle_id` scales where nested fork-of-fork directories don't. The measurement store (`measurements/`, formerly mis-homed under `archive/`) is a cross-cycle peer of `campaigns/`, so a fresh `new` on an unchanged declaration cache-hits every origin sample (zero LLM calls, byte-identical origin score) yet still gets its own `campaign_id` and trajectory. `archive/` is now the **recycle bin** (archived campaign trees), a distinct concept from the measurement store. The `langfuse/` mirror is observability only — resume/rewind read solely from `rounds/round_NNNN.json`.

## File reference

| File | Lives at | Content |
|------|----------|---------|
| `campaign.json` | campaign dir | Manifest: dataset, label, status, `root_cycle_id`, declaration hashes, backend, and the frozen `CampaignConfig` snapshot (single owner — no per-cycle copies). |
| `dashboard.json` | the cycle's dir | Live per-cycle scalars: round, origin, best, candidates, counters. One stream per cycle. Post-mortem `stop_reason` is in `index.json`, not here. |
| `log.md` / `hard_samples.json` (campaign) | campaign dir | Campaign digest + campaign-scope hard-sample artifact (across all its cycles). |
| `index.json` | per cycle | `pipeline_params`, `cycle_id`, `parent_cycle_id`/`sibling_kind`/`sweep_batch_id` (branches), `trials[]`, `final` block (winner + stop_reason). |
| `log.md` / `review.md` (cycle) | per cycle | Per-cycle digests. Derived views — safe to delete and recompute. |
| `rounds/round_NNNN.json` | per cycle | Serialized `OptSearchPoint` — the resume source of truth. |
| `.runtime/ledger.jsonl` | per cycle | Append-only fact stream. Escalation firings ride a `PhaseRecord(phase="escalation", event="rule_fired")` — no separate signals stream. |
| `.runtime/streams/…_p_best.jsonl` | per cycle | Per-sample PoBB snapshots. |
| `.runtime/cache/rounds\|candidates/` | per cycle | Per-node I/O (l1_generate/critique/score, l2/l3) + pre-scoring candidate checkpoint. |

The most-recent run's live readout (per-sample HIT/MISS, round summaries, SP tables), ANSI-stripped, also mirrors to the repo-root gitignored **`.goldmine/latest.log`** — the headless tail when you're not watching `dashboard.json::current_round`.

Material facts land on disk in human-readable form; reads happen by opening files (no read CLI). Entry points never write campaign artifacts directly — every write rides the per-cycle ledger through two projections (live telemetry + audit). The allowlist is a structural invariant that fails loud (an out-of-allowlist write shows up in the file tree); no standing test, see [`../../tests/CLAUDE.md`](../../tests/CLAUDE.md).

**Editing optimizer state by hand.** Open `cycles/{cycle_id}/rounds/round_{N:04d}.json` before `resume --from N` and edit; keep the `opt_search_point` block round-trippable through `OptSearchPoint.model_validate`. On resume the cycle replays every prior `round_NNNN.json` in order to rebuild its state — there is no separate write-ahead log.

## Recovery: resume, rewind, fork, sweep

Three workflows over one fork primitive (conceptual picture: [`../concepts/campaign-tree.md`](../concepts/campaign-tree.md)).

| Workflow | Command | Effect |
|----------|---------|--------|
| **Resume** | `resume` | Pick up from the latest completed round of the active cycle. |
| **Rewind** | `resume --from N` | Same `cycle_id`; archive trials after round N; resume at N+1. |
| **Fork on divergence** | `resume --fork-on-divergence` | On scorer divergence, mint a sibling cycle rooted at the divergence point and continue under the current scorer. |
| **Sweep batch** | `new --sweep-batch` | Mint N siblings under one root from operator-authored overrides; 2-round sweep each. |

### Rewind — `resume --from N`

Use when the active cycle went somewhere you don't want (a bad L3 replan, or you edited config and want to re-explore from a round). `cycle_id` stays; you roll history back inside it. Rounds after N are moved into `.runtime/archived/resumed_at_<ts>/` (not deleted), state is restored from round N, and the run resumes at N+1. The measurement archive is preserved — per-sample results replay without backend calls.

**Partial rounds.** Ctrl+C (a resumable pause, `StopReason.PAUSED`) mid-round leaves ledger events but no `round:complete`; the public `rounds/round_NNNN.json` stays absent (the audit cache carries the partial with `"interrupted": true`) and the cycle stays non-terminal and resumable — no `finished_at`. `--from M` is admissible only if round `M` has a closing event — so after a pause mid-round-1, `--from 1` refuses and `--from 0` resumes cleanly. A plain `resume` (no `--from`) continues from the last completed round.

### Fork — `resume --fork-on-divergence`

Use when a **data-affecting** edit (scoring formula, `pipeline_overrides`, `exclude_nodes`, `dataset_name`) makes resume's replayer find recorded decisions no longer hold. The optimizer halts rather than drift; either revert, or commit with `--fork-on-divergence`. It mints a new `cycle_id` **in the same session**, rooted at the divergence point, copies pre-divergence trials, records `parent_cycle_id`, and re-runs the divergent round under the current scorer. The shared archive is not duplicated — both cycles read the same measurements through their own scoring ledger.

**Policy-only edits** (PoBB knobs, patience, thresholds, `n_variants`, `exploration.*`) can't have changed the data trace, so resume continues in-place on the same cycle and `--fork-on-divergence` is a no-op. Past decisions stay as the audit record of the policy that made them; the new policy governs unevaluated rounds.

**Why rewind isn't enough:** rewind restarts under the *same* policy and would re-hit the same divergence; fork restarts under the *new* one.

### Sweep batch — `new --sweep-batch`

Breadth-first comparison of N L1-prompt hypotheses: instead of one trial cycle on the active OSP, mint N cheap sibling cycles, each from a different operator-authored override. Sweep cycles sit flat under `cycles/` with `sibling_kind: "sweep"` and a shared `sweep_batch_id`.

**Per-fork protocol:** origin (cache-hit after the first) + 1 scored round + 1 generation-only round + halt with `SWEEP_COMPLETE`.

**Authoring.** One JSON file per candidate under `datasets/{name}/sweep/`, shape `OperatorSweepFile` (`extra='forbid'` — typos fail at parse). Every field optional; `reason` is a label.

```json
{
  "reason": "step-by-step layout",
  "l1_layout": {
    "task_intent": ["task_context"],
    "problem_description": ["rendered_prompt", "pipeline_param_catalogue", "plan", "diagnostics", "failures", "critique"]
  }
}
```

`l1_layout` stamps per-slot signal-name lists onto the fork's starting OSP — the same L1 surface L2 writes when it fires, staged without firing L2. Mandatory placeholders `{plan, task_context, rendered_prompt, pipeline_param_catalogue, critique}` must each appear somewhere across the four slots.

```bash
python -m promptpotter new bbeh --backend-url http://127.0.0.1:8000
python -m promptpotter new --sweep-batch   # dispatches sweep-mode against the freshly-minted cycle
```

**Reading results.** Side-by-side: `python -m promptpotter sweep rank` (groups by parent root, sorts by `round_1_top_lift`, reports `proxy_lift_corr` once ≥4 paired sweep/full branches share an `l1_generate_hash`). **Sweep is screening, not validation** — promote winners to a full `new` run. L1-surface only; pipeline/scoring changes are intentionally absent from the operator file shape. Forks run sequentially (the active pointer doesn't tolerate concurrent mints).

## CLI flags — `new` and `resume`

`new <name>` mints a fresh session+cycle from an authored `datasets/<name>/` and runs from round 0. `new <file>` (a CSV — `Path.is_file()`) parses the file into a durable check-in campaign, runs the AI origin check-in (the same `checkin` node the web ingest uses), auto-confirms high-confidence findings, and — once the readiness gate passes — flips the check-in to `active` and runs the loop inline. It reuses the exact orchestration behind web onboarding (`ingest_draft` → `resolve_origin_turn` → `prepare_checkin_run`); the only CLI↔web difference is the CLI runs inline while the web start-checkin detaches. If a gap survives the resolver, `new` prints the open fields + questions and exits non-zero — nothing is minted on a guessed default; confirm with `--set` and re-run. After a successful file run the committed slug is first-class to `new <slug>` / `resume`.

| `new` flag | Purpose |
|---|---|
| `<name\|file>` (positional) | Dataset name under `./datasets/` (auto-loads its `campaign.json`) **or** a path to a raw CSV to ingest |
| `--config` | Campaign config JSON — overrides the dataset's default `campaign.json` (name form) |
| `--dataset-name` | Alternative to the positional name |
| `--slug` | *(file form)* Dataset slug under `projects/{tenant}/datasets/` (default: derived from the filename) |
| `--set FIELD=VALUE` | *(file form)* Confirm an origin field directly (operator-stated), repeatable. Fields: `task_description`, `column.query`, `column.ground_truth`, `connector`, `scoring_composite`, `max_rounds`. Applied before the resolver, so it seeds the rest |
| `--backend-url` | Backend service URL |
| `--backend-id` | Override backend id (auto-derived from `dataset_name` otherwise) |
| `--task-file` / `--task-text` | *(name form)* Override `<dataset>/task_description.md` from a file or inline |
| `--halt-at` / `--spend-budget` | Run-halt gates (both forms) |
| `--diag` | Diagnostic mode — marks `index.json::final.mode` as `'diag'`; branches off a counted sibling on re-run |
| `--excel-path` | Path to an Excel workbook to load alongside the dataset (name form) |
| `--sweep-batch` | Sweep-batch config path (name form — see [Sweep batch](#sweep-batch--new---sweep-batch)) |

The workflow flags `--from`, `--fork-on-divergence`, `--rewind`, `--rewind-reason` are covered under [Recovery](#recovery-resume-rewind-fork-sweep) above. The remaining `resume` flags:

| `resume` flag | Purpose |
|---|---|
| `--no-check` | Rescore but skip the decision-replay halt |
| `--diag` | Diagnostic mode (see `new --diag`); on a previously-completed diag cycle, branches off a counted sibling |

### Interrupt handling

- **First Ctrl+C** — finishes the in-flight backend call, saves all completed work, exits cleanly.
- **Second Ctrl+C** — force-quits immediately.

After an interrupted run, check for orphan processes (`tasklist | findstr python` on Windows; `ps aux | grep python` on Linux/Mac). An interrupt mid-round leaves ledger events but no closing `round:complete` — see **Partial rounds** under Rewind above for which `--from N` offsets are then admissible.

## Will a config change re-score? — the measurement cache

The single most-asked operating question: *"I edited a connector tunable (model,
temperature, a node param) — will the next run actually re-measure, or replay the old
score?"* Three facts answer it; together they're why editing a file can feel inert. (`configure_and_apply_pipeline` applies `exclude_nodes` + `pipeline_overrides` and returns the `pipeline_params` that flow unchanged through both `new` and `resume`; a `None` result means the backend runs its full pipeline.)

1. **The measurement key includes the connector config (model included).** Per-sample
   results pool in `measurements/` keyed by `node_configs` — the effective
   per-node config derived from the overlay-merged `session.pipeline_params`
   (`application/scoring/search_point_scorer.py` → `infrastructure/store/measurement_archive.py::load_reusable_results`).
   On a config change at node *N*, the prefix match breaks at *N*: **every sample whose
   pipeline ran past *N* is re-measured**; only samples that short-circuited upstream
   (a cache or high-confidence fuzzy hit before *N*) replay. So changing
   `entity_profiling.model` from `120b` to `20b` genuinely re-scores the LLM-path samples.

2. **A running/resumed campaign uses its FROZEN `CampaignConfig` snapshot.** The
   `Campaign` manifest (`campaign.json`) owns the config; editing
   `datasets/{name}/campaign.json` or `pipeline.json` does **not** change an existing
   campaign. To apply a connector-config change you mint a **fresh `new`** — it reads the
   edited dataset configs, gets a fresh random `campaign_id`, and re-scores a new origin
   on the changed config (the old campaign is untouched — campaigns are never mutated).
   (For an *in-place* re-explore after a data-affecting edit on the active cycle, that's
   `--fork-on-divergence`, above; `new` is the clean-slate path.)

3. **The cycle id is config-aware (it agrees with the measurement key).** `cycle_id` /
   `Campaign.root_content_hash` are built by `build_origin_cycle_id` from the SAME
   overlay-merged params the measurement key hashes (the connector `model`/config
   included). So two origins differing only by model get **distinct** `cycle_id`s — a
   connector-config edit yields a distinct origin, and the id agrees with which config was
   measured. (Resume of a campaign minted before this landed recomputes a config-aware
   hash that won't match its stored config-blind one; the drift check treats an identical
   config — `DiffScope.NONE` — as benign and re-stamps the hash in place.)

   **Invariant — always key a surface by `(campaign_id, cycle_id)`, never `cycle_id`
   alone.** A direct consequence of the content-addressed id: two campaigns on the same
   dataset+config (e.g. two `new lca-bom-termnorm` runs that didn't touch the pipeline)
   share the **same** root `cycle_id` — only their random `campaign_id`s differ. The cycle
   id is unique *within* a campaign, not globally. Every persistence path already resolves
   as `(campaign_id, cycle_id)` (`save_round_file`, `load`, the per-cycle dashboard route),
   and the webapp keys its unit map by the pair (`poll.tsx` `unitKey = \`${campaignId} ${cycleId}\``),
   so two same-dataset campaigns render distinctly. Any **new** read/write surface MUST
   carry the campaign id too; a lookup by bare `cycle_id` would cross-wire siblings.

## Steering composite scoring between rounds

Hot-swap the cycle's per-round formula by dropping a file; the next round-end consumes it, the running optimizer never restarts.

1. Author a `per_round` formula over the active evaluator registry (check `evaluators` in any `rounds/round_NNNN.json` for valid names).
2. Write `cycles/{cycle_id}/scoring_steer.json`:
   ```json
   {"per_round": "0.5 * accuracy + 0.3 * prompt_compactness + 0.2 * latency_norm"}
   ```
3. Wait for the next round. The formula is smoke-compiled against a synthetic namespace first, so undefined names or syntax errors fail *before* the swap; on success the file is renamed `scoring_steer.applied.{ts}.json`, on failure the running formula stays and you fix + retry.

### Available names

Gated by `applies(schema)` — present only when the matching node is active.

| Name | Range | Meaning |
| --- | --- | --- |
| `accuracy` | `[0, 1]` | Mean per-sample score |
| `error_rate` | `[0, 1]` | Fraction of errored queries |
| `degraded_rate` | `[0, 1]` | Fraction with degradation warnings |
| `runtime_failure_rate` | `[0, 1]` | OptSP runtime-failure count, normalized |
| `latency_norm` | `[0, 1]` | `1 - mean_ms / 10_000`; 1.0 = instant |
| `prompt_compactness` | `[0, 1]` | `1 - len(rendered_prompt) / 4_000`; 1.0 = short |
| `pipeline_compactness` | `[0, 1]` | `1 - (active_steps - 1) / 11`; 1.0 = single-node |
| `source_recall` / `candidate_recall` | `[0, 1]` | GT in candidate-source output / in ranker `final_ranking` (when active) |
| `cache_hit_rate` | `[0, 1]` | Cache-node short-circuit fraction |
| `mean_retrieval_shortfall` | `[0, 1]` | Mean `min(observed/target, 1.0)` across `max_*`/`num_*` nodes |

Helpers: `min`, `max`, `float`, `int`, `bool`, `abs`, `round`, `log`, `sqrt`, `exp`, `pow`. Output clamped to `[0, 1]`; undefined names raise `NameError` — fail loud is the contract.

**Don't steer per-sample by file-drop.** Changing the per-sample scorer mid-run rewrites recorded `hit`/`score` on every prior trace and trips the divergence replayer on next resume — use `resume --fork-on-divergence` instead.

The default composite renders in operator surfaces as `composite=0.6042 (Δ+0.1030 vs origin 0.5012)` per candidate, with the full formula text always in `log.md` (source of truth when reviewing finished cycles).

## Beta hosting state

Single-operator (auth-off) and hosted-beta (OIDC) share the same on-disk shape. The beta adds three operator-visible surfaces under `projects/{tenant_id}/`.

**Per-user quotas (`user.json`).** Abuse-limit knobs the launcher gates against (one tenant per user; missing file ⇒ defaults):

```json
{ "spend_budget_usd_daily": null, "max_concurrent_cycles": 2, "max_campaigns_per_day": 10 }
```

Hand-edit to lift/lower caps; checked on every `mint-campaign` and `start-run`. The effective per-cycle spend cap is `min(requested, daily_cap - daily_spent)`.

**Campaign ownership + lifecycle (`campaign.json`).** Each manifest carries `owner_user_id` (cross-user reads return **404, not 403** — existence leakage is itself a violation) and `lifecycle_status: active | archived | deleted` (+ `_changed_at`, `_reason`). The verbs are physical, not soft flags: **`archive`** MOVES the campaign tree into the `archive/{campaign_id}/` recycle bin (restorable by `unarchive`, which moves it back); **`delete`** is destructive (no recovery) — it removes the tree outright, or with `--keep-results` strips the heavy tiers and spares the keepsake (manifest + reports + the shallow langfuse loop trace — the Reports leaf + loop trace of the storage taxonomy, see [`storage-architecture.md`](../specs/storage-architecture.md)), flagging the manifest `deleted`. Archiving or deleting the **active** campaign is refused (switch first). The cross-campaign measurement store (`measurements/`) is never touched, so siblings still cache-hit. Destructive/move ops audit to the workspace ledger (`.workspace/events.jsonl`), since the campaign's own ledger goes with the tree.

```bash
python -m promptpotter archive   <campaign_id> [--reason TEXT]
python -m promptpotter delete    <campaign_id> [--reason TEXT]
python -m promptpotter unarchive <campaign_id>
```

Each is idempotent and writes a `CommandRecord` to the campaign's root-cycle ledger before marking the manifest.

**Running jobs (`.runtime/jobs/{job_id}.json`).** The browser-launched runner is tracked one file per job (`campaign_id, cycle_id, user_id, status, …`); reads filter by user. Concurrent campaigns are isolated via the per-cycle ledger ContextVar. The Account modal's Security pane surfaces live spend/concurrency/daily-mint counts against their caps.

**Identity** is the fifth I/O kind (§0 of `docs/architecture.md`): OIDC verification at the API trust boundary populates `IdentityContext`; tokens never appear past the middleware (ADR-0002 — review-enforced; no standing test). Stage 0 substitutes `default_identity()`. Contract: `docs/adr/0002-identity-foundation.md`.
