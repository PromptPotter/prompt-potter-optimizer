# Persistence and State

Where PromptPotter writes everything, the active-session pointer, and what each state file does.

---

## Active session pointer

PromptPotter remembers which campaign you're working on via an **active session pointer** at `.promptpotter/active_session.json`. This stores `{tenant_id, session_id, cycle_id}` — like a browser's active tab.

- **`init`** creates a new cycle and sets it as active (overwrites the pointer).
- **`optimize`** operates on the active cycle automatically — no flags needed.
- **`--session <id>`** overrides the active pointer for a single command.
- **`--backend-id`** is auto-derived from `dataset_name` in the config when not explicitly passed.

To resume a campaign, run `python -m promptpotter optimize`. No need to `init` again — `init` is only for starting a new campaign.

---

## Two trees: sessions + campaigns

Sessions and campaigns are separate concepts. Today the relation is 1:1; the layout is wired so a session can host multiple campaigns later (1:N) without a reorg.

- `{tenant_id}/sessions/{session_id}/` — operator session metadata: `session.json`, `journal.md` / `notes.md` (notebook ↔ Claude exchange).
- `{tenant_id}/campaigns/{cycle_id}/` — per-cycle optimization artifacts. Within a campaign dir, files split into two bands: **root telemetry** (live observability stream — `dashboard.json`, `output.log`, `phase_events.jsonl`) lives at the **family root** cycle (the cycle with no `parent_cycle_id`), so all forks of a family share one continuous stream; **per-cycle audit** (`index.json`, `log.md`, `trials/`, `candidates/`, `rounds/`, `langfuse/`, `prompts/`) lives in each individual cycle's dir.
- `{tenant_id}/library/` — **the measurement archive** (database core, cross-cycle, cross-session, cross-tenant): every measurement ever taken plus shared reference (datasets, backends, aliases). Concept doc: [`../concepts/measurement-archive.md`](../concepts/measurement-archive.md).

Full tree:

```
.promptpotter/
  active_session.json                  # { tenant_id, session_id, cycle_id } pointer
  projects/{tenant_id}/
    sessions/{session_id}/             # per-session: operator workspace
      session.json                     # session metadata
      journal.md / notes.md            # notebook ↔ Claude exchange
    campaigns/{root_cycle_id}/         # family root (cycle with no parent_cycle_id)
      # Root telemetry — shared across all forks of this family:
      dashboard.json                   # live counters; cycle_id field tracks active fork
      output.log                       # append-only HIT/MISS history; FORK banner on cutover
      phase_events.jsonl               # structured phase event stream; each record carries cycle_id
      # Plus the root cycle's own per-cycle audit:
      index.json                       # campaign metadata + trial index + final summary block
      log.md                           # rendered narrative digest (per-round + heatmap + winner)
      trials/trial_NNNN.json           # resume source of truth
      candidates/round_NNNN.json       # pre-scoring checkpoint
      rounds/round_NNNN.json           # per-round node I/O (l1_generate, l1_critique, l1_score, l2/l3 when escalated)
      langfuse/                        # trace persistence (incl. events.jsonl mirror — not read for state)
      prompts/{family}/{version}/      # rendered optimizer prompts
      archived/resumed_at_{ts}/        # mid-cycle rewind history
      forks/                           # all forks of this family nest here, regardless of depth
        {root_cycle_id}_fork_xxx/      # one subdir per fork; per-cycle audit only
          index.json   log.md
          trials/   candidates/   rounds/   langfuse/   prompts/
          # NO dashboard.json / output.log / phase_events.jsonl — those live at the family root above
    library/                           # the measurement archive — database core
      measurements/{run_id}.json       # MeasurementArchive: facts, append-only, content-addressed
      measurements.json                # archive index (denormalized read-side projection)
      samples.json                     # SampleIndex: per-sample derived state
      backends/{backend_id}/           # backend profile + datasets
      prompt_aliases.json
      # AxisIndex (axis-side digest view) is in-memory only — rebuilt from the archive every refresh.
```

**Why split this way?** Telemetry is *temporal* — a stream that flows through whichever fork is currently active. Anchoring it at the family root means a single `tail dashboard.json` covers every fork without chasing dirs. Audit is *structural* — frozen records keyed by the cycle that produced them. Each cycle owns its own and the parent stays intact when you fork.

Prior evaluation results are replayed without calling the backend when a new pipeline configuration shares a matching prefix with a stored run. `langfuse/events.jsonl` is a pure observability mirror — nothing reads it for state reconstruction. Resume and rewind are driven entirely by `trials/trial_NNNN.json`.

**Deprecated-sample eviction.** Entries in `library/measurements/` whose `classify_result()` (in `application/optimization/diagnostics.py`) returns any fatal code are written normally so the trace record stays intact for forensic analysis, but they are evicted at load — never served as cache. The next encounter with that query gets a fresh backend call and the resulting `QueryResult` is tagged `retry_of_deprecated_cache`. Eviction is purely load-side, in `score_search_point::_filter_deprecated_priors`. See [`../concepts/scoring-and-traces.md`](../concepts/scoring-and-traces.md#deprecated-samples) for the full operator-facing framing.

---

## Cycle directory file reference

| File | Lives at | Updated | Content |
|------|----------|---------|---------|
| `dashboard.json` | family root | Every optimization event | Live state: round, baseline, best, candidates, counters. Its `cycle_id` field identifies which fork is currently active. |
| `output.log` | family root | Append per eval query | Raw eval output (ANSI-stripped). On fork-on-divergence, gets a `=== FORK <id> from round N (parent: …) ===` banner inline. |
| `phase_events.jsonl` | family root | Each phase event | One structured JSON per line. Each record carries `cycle_id` so consumers can demux fork-vs-parent records when tailing the family root. |
| `index.json` | per cycle | Each phase transition + finalize | Config, phase, `pipeline_params`, `cycle_id`, `parent_cycle_id` (forks only), `best_accuracy`, `trials[]`, `final` (winner + stop_reason on completion) |
| `log.md` | per cycle | Round-complete + finalize | Rendered narrative digest: status, per-round critique / L2 directive / changes, hard-samples heatmap, final winner. Pure derived view — safe to delete and recompute. |
| `rounds/round_NNNN.json` | per cycle | Each round | One JSON object per node: l1_generate, l1_critique, l1_score, l2_context/l3_plan (when escalated) |
| `trials/trial_NNNN.json` | per cycle | Each completed round | Serialized `OptSearchPoint` for resume |
| `candidates/round_NNNN.json` | per cycle | Each round's pre-scoring step | Generated candidate list checkpoint |
| `langfuse/events.jsonl` | per cycle | Every observability event | Flat navigation log (mirror — not read for state) |
| `langfuse/` | per cycle | During optimization | Trace/observation/score shadow + id-map `state.json` |
| `prompts/` | per cycle | When prompts render | Rendered optimizer prompts per family/version |
| `journal.md` / `notes.md` | per session | Notebook ↔ CLI exchange | User narrative and Claude notes. Live in `sessions/{session_id}/`, not in the campaign tree. |

### `dashboard.json`

Scalar-only live dashboard. Atomically rewritten on every event during optimization. Carries display counters across cycles via `resume_from`.

Key fields: `phase`, `round`, `layer`, `candidate`, `query`, `patience`, `baseline`, `best`, `current_acc`, `cycle_id`, `total_queries_scored`, `total_backend_calls`, `n_variants`, `sp_budget_ttest`. The post-mortem `stop_reason` is written to `index.json::final::stop_reason` at finalize, not to the live dashboard. For per-query / per-candidate / per-round detail, read `rounds/round_NNNN.json` directly.

### `rounds/round_NNNN.json`

Consolidated per-round view — one JSON object per node that ran. Fields: `round`, `started_at`, `finished_at`, `nodes` (keyed by node type). Node types:

- `l1_generate`, `l1_critique`, `l2_context`, `l3_plan` — LLM meta-prompt calls. Each has `input.template_fields` (the canonical prompt-string fields from `PROMPT_STRING_FIELDS` plus `few_shot_examples`), `input.variables`, `output.response`, `usage`, `model`, `duration_s`.
- `l1_score` — scoring phase. `input.candidates` lists what L1 generate produced; `output.candidates[*].stats` carries accuracy/composite/hits/total/invalid/validation_failures, and `output.candidates[*].samples` lists per-query outcomes (`qi`, `sample_id`, `hit`, `cached`, `time_s`, `terminated_at`, `input_tokens`, `output_tokens`, `prediction`, `ground_truth`, `query`).

### `trials/trial_NNNN.json`

The resume source of truth. Each completed round writes its serialized `OptSearchPoint` here. On resume, `Cycle.restore_from_trial` rehydrates the exact optimizer state — no separate write-ahead log. You can edit a trial by hand between runs to modify optimizer state; keep the `opt_search_point` block round-trippable through `OptSearchPoint.model_validate`.

---

## Entry-point emission boundary

Entry points (notebook, CLI, `/potter-run` skill, API, webapp) MUST NOT write campaign artifacts directly. The sole writer is `CampaignPersistenceEmitter` in `promptpotter/infrastructure/persistence/session_emitter.py`. The `CAMPAIGN_ARTIFACTS` and `SESSION_ARTIFACTS` allowlists live in `tests/test_artifact_parity.py`; the test owns the contract and enforces both sets. See [../developer/code-layout.md § Three-layer I/O architecture](../developer/code-layout.md).

---

## Resume, rewind, fork

- **Resume** — `optimize` with no flags. Picks up from the latest completed round.
- **Rewind** — `optimize --from N`. Same `cycle_id`, archive trials after round N.
- **Fork** — `optimize --fork-on-divergence`. On detected divergence, mints a new `cycle_id` from the divergence point and continues.

Full mechanics: [rewind-and-fork.md](rewind-and-fork.md).
