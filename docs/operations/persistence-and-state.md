# Persistence, State, and Recovery

Your work lives in `.promptpotter/`, in two trees:

- `sessions/{session_id}/` — your operator workspace (journal, notes).
- `campaigns/{campaign_id}/` — one campaign per directory; every cycle (session roots + forks + diags) sits flat under `cycles/`.

## Four entities — where each one lands on disk

**Workspace → Dataset → Campaign → Cycle** — owned by [`../architecture.md`](../architecture.md) § Four entities (outermost → innermost). This page adds only the on-disk shape.

- **Workspace** — `projects/{tenant}/`. Every directory is named for what it holds, and the root partitions by lifecycle, which is how "what survives a delete?" is answered by looking.
- **Dataset** — resolved tenant-first: a tenant upload (`projects/{tenant}/datasets/{slug}/`, the `new <file>` ingest path) wins over a repo benchmark (`datasets/{name}/`). An ingested slug is first-class to both `new <slug>` and `resume`, not just the mint that made it.
- **Campaign** — `campaign_id = {dataset}__{rand6_hex}`, minted fresh per `new`. `campaign.json` carries `root_content_hash` (resume's config-drift check) and `optimizer_prompt_hash`; neither is the id.
- **Cycle** — `cycle_{content_hash[:12]}` (+ `_fork_`/`_diag_` on branches). Path resolution is always `(campaign_id, cycle_id)`.

**There is no Session tier** — owned by [`../architecture.md`](../architecture.md) § A campaign has one root cycle. What `sessions/{session_id}/` and `active_session.json` hold is the operator's workspace and pointer, never a container for cycles.

## Active session pointer

`projects/{tenant_id}/.workspace/active_session.json` (`{session_id, campaign_id, cycle_id}`) is your active tab — the workspace root selects the file, so the tenant is the path, not a payload field (`store/session_pointer.py::_active_pointer_path`).

- **`new`** mints a fresh campaign + session + root cycle and overwrites the pointer. Re-running `new` on an unchanged declaration reuses the content-addressed root-cycle id and origin score (cache-served), then diverges from round 1.
- **`resume`** reads the pointer and picks up that cycle. No re-`new` needed.
- **fork** mints a new cycle in the same session and retargets the pointer.
- **`--session <id>`** overrides the pointer for one command; **`--tenant <id>`** (default `"default"`) selects the partition under `projects/`.

Every subcommand runs as `python -m promptpotter [--tenant <id>] <subcommand> [options]`. **Loop-mint:** `new`, `resume`. **Lifecycle:** `archive`, `delete`, `unarchive`, `reset`. **Manifest-edit:** `rename` (display name only; `campaign_id` still addresses it), `replace-dataset`. **Run-control:** `pause` (stops a running cycle at its next checkpoint, resumable), `set-budget` (raises or lowers a live cycle's ceiling — how a budget-halted cycle is continued), `cancel-queued` (withdraws a launch still waiting for a machine slot; `pause` cannot serve one, since a queued mint has no cycle to write a flag into), `skip-searchpoint`, `step-cycle`. **Diagnostic:** `verify`, `ab`, `noise-floor`, `seed-screen`, `evidence`. **Maintenance** — the three that REWRITE stored artifacts rather than reading them, all dry-run by default and all refusing while a producer could still be appending: `reindex`, `restamp`, `compact-archive`.

Reads happen by opening the on-disk artifact tree. `evidence` is the one read VERB, because a comparison ACROSS subjects — a campaign, one branch or one searchpoint, repeated `--subject` and at any L4 depth — is in no single file.

**`compact-archive` is the only verb that can destroy a measurement.** `compact` moves the fields nothing reads — `hit`, `scored`, `objective`, and `pipeline_data`'s `reasoning_trace` / `result_ranking` / `final_ranking` / `total_time` — out of `candidate_*` runs into `measurements/cold/{run_id}.jsonl.gz`, stamping the run's header row with what left so a compacted row is never mistaken for one that never carried the field. `restore` puts them back; `purge-cold` deletes the cold store, and only that step is irreversible. `origin` and `round_parent` runs are never eligible — they serve the overwhelming majority of cache replays — and a row carrying `pipeline_data.mean_round_delta` keeps its trace, because the L4 narrative panel needs both.

**The diagnostics are fenced — nothing the loop decides reads one, and nothing may.** The loop does fire one: a round reading 100% runs `verify` on its winner (`application/diagnostics/verify.py::verify_on_saturation`) and discards the verdict.

- **`verify`** re-scores ONE candidate on more samples without touching the cycle — **the canonical way to deepen a winner you want to believe.** Depth comes from more samples, never from re-asking a cell: a re-ask measures how noisy the model is, which the loop cannot act on, and against the recursive L4 backend it replays the first answer outright.
- **`ab [--campaign <id>]`** re-derives a campaign's recorded decisions under the current engine/scorer and reports where the change stops carrying over ([`mask-projection.md`](mask-projection.md)). Zero LLM calls. It ablates code, not knobs: PoBB's ε and lock-in floor replay off each decision's recorded `inputs_ref`.
- **`noise-floor`** re-scores a campaign's cached origin `--k` times with `force_fresh`, reading the backend's run-to-run noise.
- **`seed-screen`** scores a seeded bank draw of ANY dataset against that dataset's own origin over repeated passes, and rejects any bank whose constant-answer floor EXCEEDS the origin by more than the measurement's own error bar — such a bank pays a candidate for collapsing to a single label, the degeneracy the instrument exists to catch. Each pass also reports median and mean per-call latency, wire cost, and the share of answers that went to a single label, so screening a candidate *model* needs no second instrument. The gap between the two latency readings says the route is retrying; the answer share is the twin of the floor — the floor says what a constant answer would SCORE, the share says how nearly this model IS one. It is the one SCREEN (N independent draws, no round-to-round dependence), which is why its concurrency is `--parallel N` held for the whole run rather than the browser's ⇉ control: a depth is cleared by the round that scored under it, and a screen has no round.

## Layout

```
.promptpotter/
  projects/{tenant_id}/
    .workspace/
      active_session.json              # { session_id, campaign_id, cycle_id }
      events.jsonl                     # workspace ledger — commands with no cycle to address
    sessions/{session_id}/session.json
    campaigns/{campaign_id}/            # {dataset}__{rand6_hex}, fresh per `new`
      campaign.json                    # manifest (dataset, config snapshot, declaration hashes — no run state)
      log.md                           # campaign digest — session + forks + rounds + heatmap
      hard_samples.json                # campaign-scope hard-sample artifact
      cycles/{cycle_id}/               # session root + forks + diags, ALL FLAT
        dashboard.json                 # live per-cycle telemetry (forks carry their own, seeded at the cut)
        index.json                     # phase, rounds, final block, parent_cycle_id (forks)
        export.json                    # the winner + its provenance, for a program that is not us
        pipeline.resolved.yaml         # the declaration this cycle RUNS — backend under dataset overlay
        log.md  review.md              # per-cycle digests (derived — safe to recompute)
        rounds/round_NNNN.json         # serialized RoundResult; its opt_search_point is the resume SoT
        langfuse/  prompts/            # trace shadow; rendered optimizer prompts
        .runtime/
          ledger.jsonl                 # append-only Decision/Phase/Snapshot/LLMCall/TokenUsage spine
          streams/round_NNNN_p_best.jsonl   # PoBB telemetry (sparkline in log.md)
          cache/rounds|candidates/     # per-round node I/O + pre-scoring checkpoint
    measurements/                       # PAID — measurements. Cross-cycle/session/tenant, peer of campaigns/
      index.jsonl                  # append-only, last-wins by run_id; `reindex` rebuilds it from runs/
      runs/{run_id}.jsonl          # one append-only log per run: a `k:"run"` header row + a `k:"m:{sample_id}"` row each
      derived/                     # read models folded FROM the runs (regenerable)
    optimizer_reuse/{hash}.json         # PAID — optimizer-LLM answers, replayed instead of re-sampled
    judge_reuse/{hash}.json             # PAID — LLM-as-judge grading replies, same shape, own tree:
                                        #   a grader must not read the loop's answers
    diagnostics/                        # seed-screen + noise-floor + verify — the three verbs that mint no cycle
      runs/{ts}_{config_hash}.json      #   verify + noise-floor, as a typed DiagnosticRunRecord
      seed-screen-{dataset}-{date}.json #   seed-screen's own shape — it screens SEATS, before a campaign
                                        #   exists, so the record's source-campaign fields would name
                                        #   nothing (`seed_screen.py` argues it). Only `runs/` is served.
    traces/obs|mlruns/                  # observability sinks (regenerable; mlruns is settings-gated)
    backends/{backend_id}/backend.json  # backend registration + synced API responses
    datasets/  benchmark-rows/  task-context/   # dataset tier — definition, materialized rows, decomposed context
```

**Why this shape.** Telemetry is *temporal* — each cycle owns its `dashboard.json`, so `tail cycles/{cycle_id}/dashboard.json` follows exactly the cycle you're watching. Audit is *structural* — frozen records keyed by the cycle that produced them. Cycles sit flat because a fork tree keyed by `parent_cycle_id` scales where nested fork-of-fork directories don't. `measurements/` is a cross-cycle peer of `campaigns/`, so a fresh `new` on an unchanged declaration cache-hits every origin sample yet still gets its own `campaign_id` and trajectory; `optimizer_reuse/` is its peer on the optimizer's leg and `judge_reuse/` on scoring's.

**`store/layout.py::SHARED_CACHE_DIRS` is the whole of the paid tier** — that tuple is what `reset` preserves and what the workspace storage report counts as shared, so a cache is added to it or it is silently destroyed by the first. Everything else in the root is campaign state, regenerable, or config. There is deliberately no recycle bin: an archived campaign stays in `campaigns/` and is hidden by `campaign.json::lifecycle_status`, so a campaign has ONE home and no enumerator has a second parent to remember. The `langfuse/` mirror is observability only — resume/rewind read solely from `rounds/round_NNNN.json`.

## File reference

| File | Lives at | Content |
|------|----------|---------|
| `campaign.json` | campaign dir | Manifest: dataset, label, `root_cycle_id`, declaration hashes, backend, lifecycle intent, and the frozen `CampaignConfig` snapshot (single owner — no per-cycle copies). Run state is per-cycle (`index.json::status`), derived on read for campaign surfaces. |
| `dashboard.json` | the cycle's dir | Live per-cycle scalars: round, origin, best, candidates, counters. One stream per cycle. Post-mortem `stop_reason` is in `index.json`, not here. |
| `log.md` / `hard_samples.json` (campaign) | campaign dir | Campaign digest + campaign-scope hard-sample artifact (across all its cycles). |
| `index.json` | per cycle | `pipeline_params`, `cycle_id`, `parent_cycle_id` (branches), `rounds[]`, `final` block (winner + stop_reason). A branch's KIND is not stored — `layout.py::sibling_kind` parses it from the id. |
| `export.json` | per cycle | The winning prompt by field name, the node config it ran under, and the provenance a consumer needs to trust the number (fitness under its named formula, n, lift + CI, θ, the rows' hash, the optimizer manifest). Written from the same call that stamps `index.json::final`; absent when no round ever closed. Contract: `domain/export.py`. |
| `pipeline.resolved.yaml` | per cycle | The declaration this cycle RUNS — the live backend's, under the dataset overlay, as `wiring::_resolve_pipeline_schema` merged it. Written at `init_cycle` and REWRITTEN on every resume, because what the operator is owed is the space the next round will search. It exists because a campaign's committed dataset file deliberately snapshots values and not the backend's `param_keys` (`draft_campaign::merge_pipeline_overlay`), so the served read had every node's settings and none of its axes. Absent until a cycle starts, and the dataset file answers then — which is honest, since no backend has spoken to that campaign yet. |
| `log.md` / `review.md` (cycle) | per cycle | Per-cycle digests. Derived views — safe to delete and recompute. |
| `rounds/round_NNNN.json` | per cycle | Serialized `RoundResult` — the model IS the document (`save_round_file` persists `model_dump()`, `load_round_file` validates it back). Its `opt_search_point` field is the resume source of truth. |
| `.runtime/ledger.jsonl` | per cycle | Append-only fact stream. Escalation firings ride a `PhaseRecord(phase="escalation", event="rule_fired")` — no separate signals stream. **It is also the only surface that says which optimizer node actually RAN, and what each dispatch panel cost it**: the `llm_call` record carries `prompt_chars` plus `injection_chars` / `injection_dropped` / `injection_silent` (`dispatch/facade.py`). The round document cannot answer either — its `optimizer_prompt_hashes` names every node on every round by construction. |
| `.runtime/streams/…_p_best.jsonl` | per cycle | Per-sample PoBB snapshots. |
| `.runtime/cache/rounds\|candidates/` | per cycle | Per-node I/O (l1_generate/critique/score, l2/l3) + pre-scoring candidate checkpoint. |

The most-recent run's live readout (per-sample HIT/MISS, round summaries, SP tables), ANSI-stripped, also mirrors to the repo-root gitignored **`logs/latest.log`** — the headless tail when you're not watching `dashboard.json::current_round`.

Material facts land on disk in human-readable form. Entry points never write campaign artifacts directly — every write rides the per-cycle ledger through two projections (live telemetry + audit). The allowlist is a structural invariant that fails loud; no standing test, see [`../../tests/CLAUDE.md`](../../tests/CLAUDE.md).

**Editing optimizer state by hand.** Open `cycles/{cycle_id}/rounds/round_{N:04d}.json` before `resume --from N` and edit; keep the `opt_search_point` block round-trippable through `OptSearchPoint.model_validate`. On resume the cycle replays every prior `round_NNNN.json` in order to rebuild its state — there is no separate write-ahead log.

## Diagnosing a live or stuck run

A run that looks frozen is usually one of five things, and they are distinguishable in a fixed order. Follow it — guessing from file timestamps first is how a healthy pause gets read as a crash.

**Ledger tail → `dashboard.json::declared_phase` → `.runtime/` flags → process table by command line → only then mtimes.** Each step answers a question the next cannot:

1. **Ledger tail** (`.runtime/ledger.jsonl`) — the append-only chronology. The only surface that can say *against which rival* and *in what sequence*.
2. **`dashboard.json::declared_phase`** — what the runner last *declared* about itself. A declaration, not the answer; see below.
3. **`.runtime/` flags** — what the operator asked for. A `pause.flag` present means the run is stopping on purpose.
4. **Process table, by command line** — is a producer actually attached. Match the command line, not the image name; several python processes are normal.
5. **Mtimes** — last, and only to date something the four steps above already explained.

**The trap this order exists to avoid:** control flags are consumed at the next **per-sample** checkpoint, not at the round close. A pause written mid-candidate takes effect within seconds — and to anyone watching file timestamps, a deliberate, clean, resumable stop is indistinguishable from a freeze.

### `declared_phase` is not `run_phase`

Two different facts, and conflating them is the costliest mistake here. **`declared_phase`** is written into `dashboard.json` by the runner's own process — the process that dies — so served raw it reports `running` forever after a `kill -9`. **`run_phase`** is **derived**, in exactly one place (`derive_run_phase`, `infrastructure/runtime_flags.py`), for every reader, and is never written to disk. The declaration is one *input* to that derivation, consulted for `paused` and `gate` only.

### The phase vocabulary

`RunPhase` (`domain/phases.py`) composes two orthogonal facts — lifecycle (active or finished) and control + liveness. Derivation is a first-match ladder, which is why a check-in cycle never reads as detached:

| Phase | Means | Derived from |
|---|---|---|
| `checkin` | Still authoring its origin — pre-loop, resumable, holds no machine slot | `.runtime/checkin.flag` |
| `terminal` | Finished; the reason is the cycle's `StopReason` | `index.json::finished_at` |
| `paused` | Worker exited cleanly, cycle stays **active and resumable** | `pause.flag` **or** the runner's declaration |
| `gate` | Alive, holding at the round-0 origin gate for an operator decision | declaration, freshness-gated |
| `running` | A process is attached and driving | producer is fresh |
| `detached` | Active lifecycle, **no live producer** | producer is stale — the one phase using the freshness heuristic |

**Three ways into `paused`** — the pause button (writes the flag), Ctrl+C, and an `asyncio.CancelledError` (typically an L4 outer sample deadline cancelling its inner campaign). Only the first writes a flag; the other two are derived off the runner's declaration at the finalize seam. Leaving that to each raise site is what once let a deliberately-cancelled inner cycle read `detached` and get stamped `producer_vanished`.

**`detached` ≠ `paused` ≠ wedged.** `paused` is a clean, deliberate, resumable exit; `detached` means nobody is driving; **wedged** is a producer attached and heartbeating but no longer *progressing* — `run_phase` cannot express that one, and it is derived separately from non-heartbeat ledger appends ([`../specs/frontend-surface-contract.md`](../specs/frontend-surface-contract.md)).

### Silence means dead, not thinking

A live cycle heartbeats its ledger through to `dashboard.json`. If that file goes untouched longer than `RUN_FRESH_S` (`infrastructure/runtime_flags.py`), an active cycle's producer is treated as vanished and the liveness reaper (`application/jobs/reaper.py`) stamps it `terminal` with `producer_vanished`.

**So an await that can outlast `RUN_FRESH_S` and writes nothing MUST heartbeat** (`optimization/dispatch/llm_call/heartbeat.py`) — this obligates every long await, not just LLM calls. The L4 outer cycle heartbeats its own ledger while awaiting each inner run for exactly this reason. The reaper never reaps a paused, check-in or origin-gated cycle.

### The `.runtime/` flags

Polled per checkpoint and consumed at the next **sample** boundary — transient, never to be confused with a durable ledger fact.

| Flag | Meaning |
|---|---|
| `pause.flag` | The single operator-interrupt flag. **There is no `stop.flag`.** The loop exits at the next checkpoint; the cycle stays resumable. |
| `checkin.flag` | The campaign is still authoring its origin. Dropped at skeleton creation, cleared when Start flips `checkin` → `active`. |
| `sample_lookahead.json` | The operator's *request* that the walk hold a second sample in flight. What the loop actually ran at is `dashboard.json::sample_lookahead` — never serve the flag as that. |
| `skip.flag` | Skip the current unit at the next checkpoint. |
| `spend_cap` | Live `(usd, tokens)` ceilings. |

A fresh launch clears every polled run-control flag: a flag surviving the gesture it answered would re-answer the next one.

### Where the error text is

Error prefixes — `[CLIENT]` / `[SERVER]` / `[CONNECTION]` / `[PIPELINE]` — land in the latest `rounds/round_NNNN.json`, alongside the mirrored `logs/latest.log`. The optimizer-call path carries a hard wall-clock (`_chat_under_deadline` → `OPTIMIZER_TIMEOUT`), so a hung optimizer call terminates itself. **An overnight death with no terminal record is machine-sleep or session-end class, not a code fault** — do not go looking for a bug in the loop.

## Recovery: resume, rewind, fork

Three workflows over one fork primitive.

| Workflow | Command | Effect |
|----------|---------|--------|
| **Resume** | `resume` | Pick up from the latest completed round of the active cycle. |
| **Rewind** | `resume --from N` | Same `cycle_id`; archive rounds after N; resume at N+1. |
| **Fork on divergence** | `resume --fork-on-divergence` | On divergence — a round produced by a different optimizer, a package that no longer reproduces, or a decision that re-derives differently — mint a sibling cycle rooted at that round and continue. |

### The primitive

A fork is a new cycle whose `index.json` carries `parent_cycle_id`. Its KIND is stored nowhere, because the id already answers it — `layout.py::root_cycle_id` / `::sibling_kind` know exactly two separators (`_fork_`, `_diag_`). Forks land **flat** under `cycles/`; the tree is reconstructed from `parent_cycle_id`, never from directory nesting. The parent's ledger gets a `ResumeCheckpointRecord(kind=FORK_CUT)` naming the child's `cycle_id` and the cut round, and the child inherits the parent's history up to that cut.

Three things a fork owns rather than shares: its own `dashboard.json` (seeded from the parent at the cut), `index.json::forked_at_offset` naming *where* on the parent it cut, and a ledger carrying **own appends only** — the parent's prefix is walked, not copied. **`mint_kind`** is the webapp sidebar label for what minted a cycle (`domain/run_records.py::MINT_KIND_FOR_TRIGGER`, which refuses an unbadged trigger at import); the raw kind is not served beside it, because the browser parses the id for the family tail anyway.

Every cut serializes ONE typed `ForkSpec` to `FORK_CUT.data.fork` + `index.json::fork`, and its callers differ only in what they fill: **scoring divergence** (trigger/reason/issued_by only) and an **operator-steered fork** (`seed: CycleSeed` + `from_candidate_id`). The primitive does not know which fired — a new caller adds a `ForkTrigger` member and nothing else.

**`from_round` is provenance; `_mint_fork(fork_from_round=…)` is mechanics.** The arg says how many parent rounds this cut LIFTS (`0` = a clean offshoot lifting none); the spec field says which round it was CUT FROM. A rebase makes them equal, so the seam back-fills the spec when its author left it unset — but only then. Only a steered cut names `from_candidate_id`, so only it can be labelled by the candidate it came from.

**Three checks for a new fork driver.** If any fails, the primitive has reached its scope and the feature wants its own layer: the driver must be **trigger-agnostic** (a new `ForkTrigger` member and a filled `ForkSpec`, no edits to `_mint_fork`'s body); its override must be **OSP-carriable** (a different pipeline shape or scoring formula is a layer above); and it must cause **no data fracture** (no parallel persistence directory, no duplicate of something already in `measurements/`, `rounds/` or the ledger). Library measurements are deliberately not on the tree — content-addressed by `JobSearchPoint.content_hash`, two forks see identical hashes and read the same `measurements/` row, which is why a second fork's origin costs zero LLM calls.

### Rewind — `resume --from N`

Use when the active cycle went somewhere you don't want. `cycle_id` stays; rounds after N are deleted, state is restored from round N, and the run resumes at N+1. The measurement archive is preserved — per-sample results replay without backend calls.

**Partial rounds.** Ctrl+C mid-round (a resumable pause, `StopReason.PAUSED`) leaves ledger events but no `round:complete`; the public `rounds/round_NNNN.json` stays absent (the audit cache carries the partial with `"interrupted": true`) and the cycle stays non-terminal and resumable. `--from M` is admissible only if round `M` has a closing event — so after a pause mid-round-1, `--from 1` refuses and `--from 0` resumes cleanly.

### Fork — `resume --fork-on-divergence`

Use when a **data-affecting** edit (scoring formula, `pipeline_overlay`, `exclude_nodes`, `dataset_name`) makes resume's replayer find recorded decisions no longer hold. The optimizer halts rather than drift; either revert, or commit with `--fork-on-divergence`. It mints a new `cycle_id` **in the same session**, rooted at the divergence point, copies pre-divergence rounds, records `parent_cycle_id`, and re-runs the divergent round under the current scorer. The shared archive is not duplicated — both cycles read the same measurements through their own scoring ledger. **Why rewind isn't enough:** rewind restarts under the *same* policy and would re-hit the same divergence.

**A cut has a DIRECTION** — which side the run continues on, written at the cut and served as `fork_direction`. The trigger usually implies it (`FORK_DIRECTION`, derived, so every fork already on disk answers it): a diag / steered fork is an `offshoot`, the child hanging off a line that keeps running; a `scoring_divergence`, `operator_rewind` or L2/L3 rebase **supersedes**, the child being the continuation the pointer moves to and the *parent* what was left behind. Same shape on disk, opposite reading — which is why nothing is deleted on a supersede. A correction is the one cut taken before its consequence is known, so it records the answer it later measured on `ForkSpec.direction`, which outranks the derived default; that is the only way `equivalent` arises. **How a cut READS once served** — the timeline renumber, which side wears `superseded_by`, which cycle speaks for the campaign — is owned by [`infrastructure/CLAUDE.md`](../../promptpotter/infrastructure/CLAUDE.md) § The lineage tree. What THIS layer must get right is that the direction, and how far it reaches, are on disk before any reader asks.

**A cut retires only what the branch has actually replaced.** *How far* it reaches is read back from the branch's own ledger — the last round it minted a candidate for. Asserting "everything after this is replaced" at mint time is a claim about the future rendered as a fact about the past: a branch that was cut and then died retired a whole measured tail in favour of nothing. The write side hands the branch exactly the candidates it retires (`_rebank_on_branch`), so the two sides cannot drift.

**A supersede retires the parent, on disk, at the cut** — `_mint_fork` stamps it terminal with `StopReason.REBASED` (`campaigns.mark_superseded`, idempotent). The parent stops writing *by design*, and an unstamped deliberate silence is indistinguishable from a crash: cold dashboard ⇒ `detached` ⇒ the reaper stamps `producer_vanished` fifteen minutes later. Resumability is untouched — `finished_at` is a latch and `reopen_for_continuation` clears it. An **`equivalent`** cut moves the pointer the same way and retires nothing.

**After a REPAIR both sides carry the same `candidate_id`** — a repair re-measures, it does not re-mint, so one individual holds two measurements: the corrected one and the one it withdrew, which is what the round was actually steered by. A retired candidate wears **no crown**: it was elected over rows the cut replaced, so `is_winner` is withdrawn until the branch re-elects.

**Policy-only edits** (PoBB knobs, patience, thresholds, `n_variants`, `exploration.*`) can't have changed the data trace, so resume continues in-place and `--fork-on-divergence` is a no-op. Past decisions stay as the audit record of the policy that made them.

**Unless a repair lands.** Every resume first makes each closed round re-derive from its own rows — re-measuring cells it recorded without a measurement, and re-projecting a headline that no longer matches its winner's row. That is *incompleteness*, not divergence, so it runs whatever the config diff says; the winner replay runs on top, so a flipped crown forks rather than overwrites.

**A correction cuts first and is graded second.** Whether a round needs correcting is decided from the round document alone, and the branch is taken right there — before any re-measure begins, because the version a correction replaces is the version its descendants read and there is one copy of it. The parent therefore stays byte-identical to what ran, and the operator watches the old round move to its own branch while the repair is still running. The grade lands once the correction does: every round's optimizer packages are fingerprinted before and after, each rendered at its own point in the run, and the cut is stamped `supersede` if anything read differently or **`equivalent`** if nothing did. An `equivalent` cut is not a dead end — both sides carry the same content forward, so candidates already generated for the next round come *across* rather than being regenerated.

**The cut is a CANDIDATE, not a round** (`repair_cut` → `ForkSpec.from_candidate_id`): everything from the first candidate the repair moves retires with it, so a break in a round's third candidate leaves the first two on the line — while the fork still *lifts* whole rounds, because a round is the unit of election.

**The correction reaches the branch's LEDGER, not only its round file** (`repair.py::_rebank_on_branch`): a repair is a new measurement and a measurement enters through the ingress, so each retired candidate's identity is copied forward and its corrected score re-emitted onto the branch. Without that half the branch holds round files no ledger scan can see, cannot name a single candidate of its own, and every reader falls back to the version the correction just replaced.

**A resume's own corrections branch without asking.** `--fork-on-divergence` decides what happens when something changed from OUTSIDE — a different scorer, a different optimizer — and those still halt by default. A repair, or a generation the resume finds stale, is the resume doing its job.

**A cached generation records what it read.** Candidates are persisted with `consumed`, the `round_document_digest` of the round they were composed from; on resume that digest is recomputed from disk and compared. Both sides are persisted JSON, so unlike the package differential this reproduces across processes — which is what catches a critique re-distilled by an *earlier* resume, the common case. A cache with no recorded digest is **unvouched**, and unvouched branches too.

A hole is plugged with a **real measurement, never an archive row** — a cached row for that `(node_configs, sample_id)` may have been produced as a PoBB *backfill*, measured out of the round's shared order to fill someone else's paired comparison, and adopting it as this candidate's own panel cell is what makes a repaired round unreproducible. The re-measure bypasses only the outer archive, so the inner spawn still resolves content-addressed and **continues the furthest-along campaign banked for that cell** instead of restarting it.

### Human in the loop — steer & fork, pause

**HITL is not a separate I/O kind** — it collapses into the fork primitive above. The operator forks via `resume --fork-on-divergence` (CLI) or the webapp **Steer & fork** flow (the searchpoint drill-in on either cladogram → `SteerForkPanel`; it refuses below the top level, where `fork-cycle` carries no `descend`): pause the run, edit the chosen searchpoint's prompt + node config + limits, and fork a sibling cycle tagged `operator_steered`. The fork roots at the chosen offset via `CycleEventLog.inherit_from(parent, offset)`, inheriting the parent's typed state at the cut.

**One verb, two acts, and `keep_rounds` is which.** Unset it is the steer above: a clean offshoot from the origin, rounds numbered from 1, re-scoring the edited searchpoint. Set, the same command mints an `operator_rewind` — rounds `0..N-1` are LIFTED and the branch continues at N under the seed's `config_overrides`. That is the shape an applied scoring mask takes ([`mask-projection.md`](mask-projection.md) § Applying one). It refuses a seed carrying `origin_prompt_fields`: the lifted round 0 already IS the origin. The terminal's equivalent gesture is `resume --rewind N`.

**Pause run.** The webapp button and the CLI `pause` verb fire the same `pause-cycle` command through the same `CommandDispatcher`, which writes `{cycle_dir}/.runtime/pause.flag`. `Session.pause_check` polls it at the next checkpoint and the worker exits cleanly, but the cycle stays **non-terminal and resumable** (no `finished_at`). Recorded as `StopReason.PAUSED`, and the request lands on the cycle's ledger as a `CommandRecord`, so *who asked* is auditable — writing the flag by hand skips that record, so use the verb. `python -m promptpotter pause` targets the active cycle (`--campaign` / `--cycle` to name another, `--reason` to annotate). Ctrl+C is the third route and the fastest, since it does not wait for a checkpoint.

**An inner cycle stops when its owner does.** An L4 inner campaign runs in a child task under its own sandbox, whose pause flag nobody writes; it inherits the outer's pause predicate at the run-control binding seam (`runner/entry.py::_bind_run_controls`) rather than overwriting it. Without that a pause on the outer waited out the whole inner campaign, because one outer *sample* is an entire inner run.

**Make a slow round finish sooner — the look-ahead control.** The remote's **⇉** control runs the walk with several of a candidate's samples in flight instead of one, cutting that walk's wall clock roughly in proportion. Suggest it whenever someone asks why a round is taking so long; it is the only speed lever needing no config change and no restart. **Every clause of it** — who may press, what one press buys, why the overshot sample is discarded — is owned by [`access-model.md`](access-model.md) § host-admin ↔ user. What this layer must hold is the on-disk half: the operator's *request* is `.runtime/sample_lookahead.json` and what the loop actually ran at is `dashboard.json::sample_lookahead`, never the flag served as that.

## CLI flags — `new` and `resume`

`new <name>` mints a fresh session+cycle from an authored `datasets/<name>/` and runs from round 0. `new <file>` (a CSV — `Path.is_file()`) parses the file into a durable check-in campaign, runs the AI origin check-in (the same `checkin` node the web ingest uses), auto-confirms high-confidence findings, and — once the readiness gate passes — flips the check-in to `active` and runs the loop inline. If a gap survives the resolver, `new` prints the open fields + questions and exits non-zero — nothing is minted on a guessed default; confirm with `--set` and re-run. After a successful file run the committed slug is first-class to `new <slug>` / `resume`.

**The flag set is `presentation/cli/parsers.py`** — every row a page like this could carry was its `help=` string one `--help` away, and the two drifted. Read the flags there; this page owns what they do to the tree, above.

### Interrupt handling

- **First Ctrl+C** — cancels the in-flight call, banks completed work, declares the cycle `paused` (resumable), exits **130**.
- **Second Ctrl+C** — force-quits immediately.

After an interrupted run, check for orphan processes. An interrupt mid-round leaves ledger events but no closing `round:complete` — see **Partial rounds** under Rewind for which `--from N` offsets are then admissible.

## Will a config change re-score? — the measurement cache

The single most-asked operating question: *"I edited a connector tunable — will the next run actually re-measure, or replay the old score?"* Four facts answer it. (`configure_and_apply_pipeline` applies `exclude_nodes` + `pipeline_overlay` and returns the `pipeline_params` that flow unchanged through both `new` and `resume`; a `None` result means the backend runs its full pipeline.)

1. **The measurement key includes the connector config, model included.** Per-sample results pool in `measurements/` keyed by `node_configs` — the effective per-node config derived from the overlay-merged `session.pipeline_params`. On a config change at node *N*, the prefix match breaks at *N*: **every sample whose pipeline ran past *N* is re-measured**; only samples that short-circuited upstream replay. So changing `entity_profiling.model` from `120b` to `20b` genuinely re-scores the LLM-path samples.

2. **A running/resumed campaign uses its FROZEN `CampaignConfig` snapshot.** `campaign.json` owns the config; editing `datasets/{name}/campaign.yaml` or `pipeline.yaml` does **not** change an existing campaign. To apply a connector-config change, mint a fresh `new` — it reads the edited dataset configs, gets a fresh `campaign_id`, and re-scores a new origin. For an *in-place* re-explore after a data-affecting edit, that is `--fork-on-divergence`.

3. **The cycle id is config-aware, and agrees with the measurement key.** `cycle_id` / `Campaign.root_content_hash` are built by `build_origin_cycle_id` from the SAME overlay-merged params the measurement key hashes, so two origins differing only by model get **distinct** `cycle_id`s.

   **Invariant — always key a surface by `(campaign_id, cycle_id)`, never `cycle_id` alone.** A direct consequence of the content-addressed id: two campaigns on the same dataset+config share the **same** root `cycle_id` and differ only in their random `campaign_id`. Every persistence path already resolves as the pair, and the webapp keys its unit map by it, so two same-dataset campaigns render distinctly. Any **new** read/write surface MUST carry the campaign id too; a lookup by bare `cycle_id` would cross-wire siblings.

4. **On L4 the identity inputs are NOT frozen — editing one mid-campaign re-measures the origin.** `connectors/promptpotter.py::_identity_config` fingerprints what the inner optimizer nodes RESOLVE TO (`_inner_optimizer_revision` — each node's prompt body, its resolved response schema, which is prompt text riding every call as `response_format`, and its config), plus the per-node information-flow layouts, **the source deciding what each node is handed** (`injection_source_digest`), **the estimator's own source** (`_measurement_source_digest`), the dataset's whole `inner_tasks.yaml`, and the inner benchmark's `pipeline.yaml` node configs *and* `campaign.yaml` — the worker model and the scoring formula included, since either changes what every cell measures. Both source digests are normalized through the AST, so a comment costs nothing while an expression voids the origin. Fact 2 does not cover these — they are read live, not snapshotted — so an edit lands on the *running* campaign: the banked outer origin stops joining and the next round pays to score it again. **Land config fixes before an origin is measured, never between its rounds.**

   **Deliberately NOT in it, because a corpus that cannot survive them cannot accumulate:** the manifest's non-inner nodes (`checkin`, descriptions, `available_models`) and `APP_VERSION`. The version constant voided every banked cell on every release while saying nothing about whether the measurement had changed.

## Changing the composite formula — fork, never swap

**There is no live swap, and the reason is a gate rather than plumbing.** The scorer compiles once during run init (`initialization/loop_start.py::populate_session_scoring`) from `campaign.json::scoring` and is never re-read. To change it: author a `per_cell` formula over the names below, edit `campaign.json::scoring`, and `resume --fork-on-divergence` — the sibling starts at the divergence point and every round it banks is scored under one formula.

**A `per_cell` formula is what θ is fit on** (`domain/scoring.py::CellScorer`), so it is the only route by which a latency, cost or reliability term reaches the election at all — a round is won on θ, and a term outside it moves the display alone. It is charged where it happened rather than meaned over the panel: at round scope one 2000-second cell hides behind a fast one, and which prompt provoked the slowdown cannot be recovered. The cell's own correctness stays `per_sample` and stays what `is_hit` thresholds, or a correct-but-slow answer would render MISS and send L1 to repair an answer that was already right.

Swapping it between rounds would make the composite **incomparable to its own past** inside one cycle, silently. `EscalationFSM._improved` — the L2/L3 stall gate — asks whether the cycle's best advanced since a layer fired, and answers on `best_composite_fitness` whenever the θ ruler is unavailable. Redefine the composite mid-cycle and that comparison reads a change of scale as progress or as stall, with nothing to error on. `POST /commands/change-scoring-composite` is declared in [`../specs/api-openapi.yaml`](../specs/api-openapi.yaml) and carries `x-status: declared-not-wired`; wiring it as specified would reintroduce exactly this.

### Available names

**A `per_cell` formula reads `scoring/formula/compiler.py::objective_namespace`** — the declared channels (`fitness`, `latency`, `cost`, `tokens`, `ground_truth_rank`, the L4 lift family), the three row-health facts (`errored`, `degraded`, `cached`), and whatever else that dataset's own trace carries. **A MASK reads `evaluators.py::_REGISTRY`** plus the per-cell channel means `metrics.py::_MEANED_CHANNELS` folds in, which is what lets one formula be written once and read in both places. Read both there: a table here goes stale in the one direction that costs the operator a name they never learn exists, and this page listed eleven of sixteen for long enough to hide five. Registry entries are gated by `applies(schema)`, so each is present only when the matching node is active.

Helpers are `scoring/formula/compiler.py::SAFE_BUILTINS`. Output clamped to `[0, 1]`; undefined names raise `NameError` — fail loud is the contract, which is why the name list must come from the registry rather than from prose.

The default composite renders in operator surfaces as `composite=0.6042 (Δ+0.1030 vs parent 0.5012)` per candidate — anchored on the row's matched parent, the same floor the accuracy Δ beside it uses — with the full formula text always in `log.md`.

## Beta hosting state

Single-operator (auth-off) and hosted-beta (OIDC) share the same on-disk shape. The beta adds three operator-visible surfaces under `projects/{tenant_id}/`.

**Per-user quotas (`user.json`)** — one tenant per user, missing file ⇒ defaults, hand-editable, checked on every `mint-campaign` and `start-run`:

```json
{ "spend_budget_usd_total": null, "max_concurrent_cycles": 2, "max_campaigns_per_day": 10 }
```

**What each knob bounds, and what bounds resource use generally** — owned by [`access-model.md`](access-model.md) § What bounds resource use.

**Campaign ownership + lifecycle (`campaign.json`).** Each manifest carries `owner_user_id` (cross-user reads return **404, not 403** — existence leakage is itself a violation) and `lifecycle_status: active | archived | deleted` (+ `_changed_at`, `_reason`).

**`archive`** is the flag and nothing else — the tree stays in `campaigns/`, hidden from the default listing and restorable by `unarchive`, because a campaign with two possible homes made every enumerator carry a second parent. **`delete`** is physical and destructive with no recovery: it removes the tree outright, or with `--keep-results` strips the heavy tiers and spares the keepsake (manifest + reports + the shallow langfuse loop trace), flagging the manifest `deleted`. Either is refused only while one of the campaign's cycles is LIVE; the active pointer is released and the verb proceeds.

The cross-campaign measurement store is never touched, so siblings still cache-hit — and by the same token a delete STRANDS every row no surviving campaign can replay, with no verb that reclaims them. **Build that reclaim before a bulk delete, never after it:** the orphans are only measurable while the campaigns that named them are still on disk, so deleting first destroys the evidence and the motivation in one gesture. What it must do is [`../specs/code-debt-cleanup.md`](../specs/code-debt-cleanup.md) § Blocked — named blocker.

```bash
python -m promptpotter archive   <campaign_id> [--reason TEXT]
python -m promptpotter delete    <campaign_id> [--reason TEXT]
python -m promptpotter unarchive <campaign_id>
```

Each is idempotent, and each — from the terminal exactly as from the web — dispatches through `CommandDispatcher`, which writes a `CommandRecord` to the **workspace** ledger (`.workspace/events.jsonl`) before marking the manifest. The workspace ledger is the audit home because `delete` removes the campaign's own ledger, so it cannot record its own disappearance.

## The storage taxonomy — Connector / Loop / Dataset

There is **one** storage vocabulary, the operator's mental model. Every byte in a campaign tree lands in exactly one of six leaves — mutually exclusive and exhaustive, summing to the on-disk total. The top-level axis is **Connector vs Loop vs Dataset**; **Loop** breaks into four. Classifier + endpoints: `presentation/api/routers/campaigns/storage.py` (`_leaf` / `_campaign_split`).

| Leaf | Parent | Contents |
|---|---|---|
| **Dataset** | — | `langfuse/datasets/` — the ground-truth mirror (input-data copy; usually the biggest chunk) |
| **Connector** | — | `.runtime/cache/**` + the per-sample `results`/`all_candidate_results` arrays carved from the public `rounds/round_*.json` |
| **State** | Loop | the resume point — non-array remainder of `rounds/round_*.json` (the read-once cycle seed rides the ledger, so it lands in **History**) |
| **Trace** | Loop | telemetry — `.runtime/streams/`, `prompts/`, `langfuse/{traces,observations,scores}/` |
| **History** | Loop | the durable event spine — `.runtime/ledger.jsonl` |
| **Reports** | Loop | readable output — the campaign manifest plus every top-level cycle surface, DERIVED from `CycleLayout` by `layout.py::_REPORT_NAMES`; the hand-copy it replaced had dropped `export.json`, so `--keep-results` deleted the campaign's answer |

**The keepsake is not a leaf.** What `delete --keep-results` spares (Reports + the langfuse loop trace) is a cross-cutting subset, surfaced as a one-line UI note — never a summed figure, so the partition stays MECE.

**Ledger writers store once.** A cycle's `init` record carries a dataset *reference* (`dataset_size`), never an embedded copy of the rows; round 0's display record drops `round_result`. The bulk of a mature ledger is `snapshot` telemetry (65–77% of bytes), which is the live per-sample stream and is meant to be there.

**Running jobs (`.runtime/jobs/{job_id}.json`).** The browser-launched runner is tracked one file per job (`campaign_id, cycle_id, user_id, status, …`); reads filter by user. Concurrent campaigns are isolated via the per-cycle ledger ContextVar.

**Identity** is the fifth I/O kind ([`../architecture.md`](../architecture.md) §0): OIDC verification at the API trust boundary populates `IdentityContext`, and tokens never appear past the middleware ([`../adr/0002-identity-foundation.md`](../adr/0002-identity-foundation.md) — review-enforced, no standing test). Stage 0 substitutes `default_identity()`.
