# Persistence and State

Where PromptPotter writes everything, the active-session pointer, and what each state file does.

---

## Active session pointer

PromptPotter remembers which campaign you're working on via an **active session pointer** at `.promptpotter/active_session.json`. This stores `{tenant_id, session_id, cycle_id}` — like a browser's active tab.

- **`init`** creates a new cycle and sets it as active (overwrites the pointer).
- **Every other command** (`optimize`, `show-status`, `show-results`, `set-task`, `control`) operates on the active cycle automatically — no flags needed.
- **`--session <id>`** overrides the active pointer for a single command.
- **`--backend-id`** is auto-derived from `dataset_name` in the config when not explicitly passed.

To resume a campaign, run `python -m promptpotter optimize`. No need to `init` again — `init` is only for starting a new campaign.

---

## Two trees: sessions + campaigns

Sessions and campaigns are separate concepts. Today the relation is 1:1; the layout is wired so a session can host multiple campaigns later (1:N) without a reorg.

- `{tenant_id}/sessions/{session_id}/` — operator session metadata: `session.json`, `journal.md` / `notes.md` (notebook ↔ Claude exchange), `control.json` (HITL signals).
- `{tenant_id}/campaigns/{cycle_id}/` — per-cycle optimization artifacts: `index.json` (campaign metadata + trial index + `parent_session_id` + `final` summary block), `dashboard.json`, `log.md` (rendered narrative digest), `output.log`, `phase_events.jsonl`, `trials/trial_NNNN.json`, `candidates/round_NNNN.json`, langfuse shadow, prompts.
- `{tenant_id}/library/` — cross-cycle reference: datasets, backends, dataset_runs, mlruns, search_memory, aliases.

Full tree:

```
.promptpotter/
  active_session.json                  # { tenant_id, session_id, cycle_id } pointer
  projects/{tenant_id}/
    sessions/{session_id}/             # per-session: operator workspace
      session.json                     # session metadata
      journal.md / notes.md            # notebook ↔ Claude exchange
      control.json                     # HITL pause/resume
    campaigns/{cycle_id}/              # per-cycle: all artifacts for one optimization
      index.json                       # campaign metadata + trial index + final summary block
      dashboard.json                   # live counters
      log.md                           # rendered narrative digest (per-round + heatmap + winner)
      output.log                       # append-only HIT/MISS history
      phase_events.jsonl               # structured phase event stream
      trials/trial_NNNN.json           # resume source of truth
      candidates/round_NNNN.json       # pre-scoring checkpoint
      rounds/round_NNNN.json           # per-round node I/O (l1_generate, l1_critique, l1_score, l2/l3 when escalated) + HITL snapshot
      langfuse/                        # trace persistence (incl. events.jsonl mirror — not read for state)
      prompts/{family}/{version}/      # rendered optimizer prompts
      archived/resumed_at_{ts}/        # mid-cycle rewind history
    library/                           # cross-cycle: shared reference data
      backends/{backend_id}/           # backend profile + datasets
      dataset_runs/                    # content-addressed evaluation archive
      search_memory.json               # materialized intelligence view
      prompt_aliases.json
      restructure_cache.json
```

Prior evaluation results are replayed without calling the backend when a new pipeline configuration shares a matching prefix with a stored run. `langfuse/events.jsonl` is a pure observability mirror — nothing reads it for state reconstruction. Resume and rewind are driven entirely by `trials/trial_NNNN.json`.

**Deprecated-sample eviction.** Entries in `library/dataset_runs/` whose `classify_result()` (in `application/optimization/diagnostics.py`) returns any fatal code are written normally so the trace record stays intact for forensic analysis, but they are evicted at load — never served as cache. The next encounter with that query gets a fresh backend call and the resulting `QueryResult` is tagged `retry_of_deprecated_cache`. Eviction is purely load-side, in `score_search_point::_filter_deprecated_priors`. See [`../concepts/scoring-and-traces.md`](../concepts/scoring-and-traces.md#deprecated-samples) for the full operator-facing framing.

---

## Cycle directory file reference

| File | Updated | Content |
|------|---------|---------|
| `index.json` | Each phase transition + finalize | Config, phase, `pipeline_params`, `cycle_id`, `best_accuracy`, `trials[]`, `final` (winner + stop_reason on completion) |
| `dashboard.json` | Every optimization event | Live state: round, baseline, best, candidates, counters |
| `log.md` | Round-complete + finalize | Rendered narrative digest: status, per-round critique / L2 directive / changes, hard-samples heatmap, final winner. Pure derived view — safe to delete and recompute. |
| `control.json` | Pause / resume / stop signals | HITL control surface (bidirectional) |
| `output.log` | Append per eval query | Raw eval output (ANSI-stripped) |
| `phase_events.jsonl` | Each phase event | One structured JSON per line; consumed by phase event renderer |
| `rounds/round_NNNN.json` | Each round | One JSON object per node: l1_generate, l1_critique, l1_score, l2_context/l3_plan (when escalated); plus HITL snapshot |
| `journal.md` / `notes.md` | Notebook ↔ CLI exchange | User narrative and Claude notes |
| `trials/trial_NNNN.json` | Each completed round | Serialized `OptSearchPoint` for resume |
| `candidates/round_NNNN.json` | Each round's pre-scoring step | Generated candidate list checkpoint |
| `langfuse/events.jsonl` | Every observability event | Flat navigation log (mirror — not read for state) |
| `langfuse/` | During optimization | Trace/observation/score shadow + id-map `state.json` |
| `prompts/` | When prompts render | Rendered optimizer prompts per family/version |

### `dashboard.json`

Scalar-only live dashboard. Atomically rewritten on every event during optimization. Carries display counters across cycles via `resume_from`.

Key fields: `phase`, `round`, `layer`, `candidate`, `query`, `patience`, `baseline`, `best`, `current_acc`, `cycle_id`, `total_queries_scored`, `total_backend_calls`, `n_variants`, `sp_budget_ttest`, `hitl` (nested: `requested_state`, `pause_point`, `stop_reason`). For per-query / per-candidate / per-round detail, read `rounds/round_NNNN.json` directly.

### `rounds/round_NNNN.json`

Consolidated per-round view — one JSON object per node that ran. Fields: `round`, `started_at`, `finished_at`, `nodes` (keyed by node type), `hitl` (snapshot at round end). Node types:

- `l1_generate`, `l1_critique`, `l2_context`, `l3_plan` — LLM meta-prompt calls. Each has `input.template_fields` (the canonical prompt-string fields from `PROMPT_STRING_FIELDS` plus `few_shot_examples`), `input.variables`, `output.response`, `usage`, `model`, `duration_s`.
- `l1_score` — scoring phase. `input.candidates` lists what L1 generate produced; `output.candidates[*].stats` carries accuracy/composite/hits/total/invalid/validation_failures/is_winner/eliminated_at, and `output.candidates[*].samples` lists per-query outcomes (`qi`, `sample_id`, `hit`, `cached`, `time_s`, `terminated_at`, `input_tokens`, `output_tokens`, `prediction`, `ground_truth`, `query`).

### `trials/trial_NNNN.json`

The resume source of truth. Each completed round writes its serialized `OptSearchPoint` here. On resume, `Cycle.restore_from_trial` rehydrates the exact optimizer state — no separate write-ahead log. You can edit a trial by hand between runs to modify optimizer state; keep the `opt_search_point` block round-trippable through `OptSearchPoint.model_validate`.

### `control.json`

Bidirectional HITL control. `requested_state` ∈ `{"pause", "resume", "stop"}`. Checked between queries (~5–10s lag); respect the setting and write back when processed.

---

## Entry-point emission boundary

Entry points (notebook, CLI, `/potter-run` skill, API, webapp) MUST NOT write campaign artifacts directly. The sole writer is `CampaignPersistenceEmitter` in `promptpotter/infrastructure/persistence/session_emitter.py`. New artifacts are added to the `CAMPAIGN_ARTIFACTS` or `SESSION_ARTIFACTS` allowlists; `tests/test_artifact_parity.py` enforces both sets. See [../developer/code-layout.md § Three-layer I/O architecture](../developer/code-layout.md).

---

## Resume, rewind, fork

- **Resume** — `optimize` with no flags. Picks up from the latest completed round.
- **Rewind** — `optimize --from N`. Same `cycle_id`, archive trials after round N.
- **Fork** — `optimize --fork-on-divergence`. On detected divergence, mints a new `cycle_id` from the divergence point and continues.

Full mechanics: [rewind-and-fork.md](rewind-and-fork.md).
