# Persistence, State, and Recovery

Your work lives in `.promptpotter/`, in two trees:

- `sessions/{session_id}/` — your operator workspace (journal, notes).
- `campaigns/{campaign_id}/` — one campaign per directory; every cycle (session roots + forks + diags + sweeps) sits flat under `cycles/`.

## Four entities — where each one lands on disk

**Workspace → Dataset → Campaign → Cycle** — owned by [`../architecture.md`](../architecture.md) § Four entities (outermost → innermost). This page adds only the on-disk shape.

- **Workspace** — `projects/{tenant}/`. Every directory is named for what it holds, and the root partitions by lifecycle, which is how "what survives a delete?" is answered by looking.
- **Dataset** — resolved tenant-first: a tenant upload (`projects/{tenant}/datasets/{slug}/`, the `new <file>` ingest path) wins over a repo benchmark (`datasets/{name}/`). An ingested slug is first-class to both `new <slug>` and `resume`, not just the mint that made it.
- **Campaign** — `campaign_id = {dataset}__{rand6_hex}`, minted fresh per `new`. `campaign.json` carries `root_content_hash` (resume's config-drift check) and `optimizer_prompt_hash`; neither is the id.
- **Cycle** — `cycle_{content_hash[:12]}` (+ `_fork_`/`_diag_`/`_sweep_` on branches). Path resolution is always `(campaign_id, cycle_id)`.

**There is no Session tier** — owned by [`../architecture.md`](../architecture.md) § A campaign has one root cycle. What `sessions/{session_id}/` and `active_session.json` hold is the operator's workspace and pointer, never a container for cycles.

## Active session pointer

`projects/{tenant_id}/.workspace/active_session.json` (`{session_id, campaign_id, cycle_id}`) is your active tab — the workspace root selects the file, so the tenant is the path, not a payload field (`store/session_pointer.py::_active_pointer_path`).

- **`new`** mints a fresh campaign + session + root cycle and overwrites the pointer. Re-running `new` on an unchanged declaration reuses the content-addressed root-cycle id and origin score (cache-served), then diverges from round 1.
- **`resume`** reads the pointer and picks up that cycle. No re-`new` needed.
- **fork** mints a new cycle in the same session and retargets the pointer.
- **`--session <id>`** overrides the pointer for one command.
- **`--tenant <id>`** (default `"default"`) selects the partition under `projects/` for the command.

Every subcommand runs as `python -m promptpotter [--tenant <id>] <subcommand> [options]`. Loop-mint verbs: `new`, `resume`. Lifecycle verbs: `archive`, `delete`, `unarchive`, `reset`. Manifest-edit verbs: `rename` (sets the campaign's display name; the `campaign_id` still addresses it). Run-control verbs: `pause` (stops a running cycle at its next checkpoint, resumable), `set-budget` (raises or lowers a live cycle's ceiling — how a budget-halted cycle is continued). Diagnostic verbs: `verify`, `ab`, `reindex`, `restamp`, `noise-floor`, `seed-screen`, `evidence`. Reads happen by opening the on-disk artifact tree; `evidence` is the one read VERB, because a comparison ACROSS campaigns is in no single file.

**The diagnostics are fenced — none is wired into the loop, and none may become so.** `verify` re-scores ONE candidate on more samples and records the result without touching the cycle — **the canonical way to deepen a winner you want to believe.** Depth comes from more samples, never from asking the same cell again: a re-ask measures how noisy the model is, which is not something the loop can act on, and against the recursive L4 backend it replays the first answer outright. `ab` re-derives a campaign's recorded decisions under the current engine/scorer and reports where the change stops carrying over ([`mask-projection.md`](mask-projection.md)), zero LLM calls. `noise-floor` re-scores a campaign's cached origin `--k` times with `force_fresh`, reading the backend's run-to-run noise. `seed-screen` scores a candidate inner-bank draw against the dataset origin over repeated passes and rejects any whose constant-answer floor EXCEEDS it by more than the measurement's own error bar — such a bank pays a candidate for collapsing to a single label, which is the degeneracy the instrument exists to catch. Each pass also reports its own median and mean per-call latency, wire cost, and the share of the model's answers that went to a single label, off the rows it already scored — so screening a candidate *model* needs no second instrument. The gap between the two latency readings is itself the signal that the route is retrying, and the answer share is the twin of the floor: the floor says what a constant answer would SCORE here, the share says how nearly this model IS one, which a margin alone cannot distinguish. Cheap L1 A/B sweeps ride `new --sweep-batch`, not a verb of their own.

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
      cycles/{cycle_id}/               # session root + forks + diags + sweeps, ALL FLAT
        dashboard.json                 # live per-cycle telemetry (forks carry their own, seeded at the cut)
        index.json                     # phase, trials, final block, parent_cycle_id (forks)
        export.json                    # the winner + its provenance, for a program that is not us
        log.md  review.md              # per-cycle digests (derived — safe to recompute)
        rounds/round_NNNN.json         # serialized RoundResult; its opt_search_point is the resume SoT
        langfuse/  prompts/            # trace shadow; rendered optimizer prompts
        .runtime/
          ledger.jsonl                 # append-only Decision/Phase/Snapshot/LLMCall/TokenUsage spine
          streams/round_NNNN_p_best.jsonl   # PoBB telemetry (sparkline in log.md)
          cache/rounds|candidates/     # per-round node I/O + pre-scoring checkpoint
    measurements/                       # PAID cache 1 — measurements. Cross-cycle/session/tenant, peer of campaigns/
      index.jsonl                  # append-only, last-wins by content_hash; `reindex` rebuilds it from runs/
      runs/{run_id}.jsonl          # one append-only log per run: a `k:"run"` header row + a `k:"m:{sample_id}"` row each
      derived/                     # read models folded FROM the runs (regenerable)
    optimizer_reuse/{hash}.json         # PAID cache 2 — optimizer-LLM answers, replayed instead of re-sampled
    diagnostics/                        # seed-screen + noise-floor + verify — the three verbs that mint no cycle
      runs/{ts}_{config_hash}.json
    traces/obs|mlruns/                  # observability sinks (regenerable; mlruns is settings-gated)
    backends/{backend_id}/backend.json  # backend registration + synced API responses
    datasets/  benchmark-rows/  task-context/   # dataset tier — definition, materialized rows, decomposed context
```

**Why this shape.** Telemetry is *temporal* — each cycle owns its `dashboard.json`, so `tail cycles/{cycle_id}/dashboard.json` follows exactly the cycle you're watching (a fork seeds from its parent, then diverges). Audit is *structural* — frozen records keyed by the cycle that produced them. Cycles sit flat because a fork tree keyed by `parent_cycle_id` scales where nested fork-of-fork directories don't. The measurement store (`measurements/`) is a cross-cycle peer of `campaigns/`, so a fresh `new` on an unchanged declaration cache-hits every origin sample (zero LLM calls, byte-identical origin score) yet still gets its own `campaign_id` and trajectory. `optimizer_reuse/` is its peer on the optimizer's own leg: the same optimizer call, asked again, replays its stored answer rather than being re-sampled. **The two are the only paid tiers** — everything else in the root is campaign state, regenerable, or config, which is what `reset` acts on. There is deliberately no recycle bin: an archived campaign stays in `campaigns/` and is hidden by `campaign.json::lifecycle_status`, so a campaign has ONE home and no enumerator has a second parent to remember. The `langfuse/` mirror is observability only — resume/rewind read solely from `rounds/round_NNNN.json`.

## File reference

| File | Lives at | Content |
|------|----------|---------|
| `campaign.json` | campaign dir | Manifest: dataset, label, `root_cycle_id`, declaration hashes, backend, lifecycle intent, and the frozen `CampaignConfig` snapshot (single owner — no per-cycle copies). Run state is per-cycle (`index.json::status`), derived on read for campaign surfaces. |
| `dashboard.json` | the cycle's dir | Live per-cycle scalars: round, origin, best, candidates, counters. One stream per cycle. Post-mortem `stop_reason` is in `index.json`, not here. |
| `log.md` / `hard_samples.json` (campaign) | campaign dir | Campaign digest + campaign-scope hard-sample artifact (across all its cycles). |
| `index.json` | per cycle | `pipeline_params`, `cycle_id`, `parent_cycle_id`/`sweep_batch_id` (branches), `trials[]`, `final` block (winner + stop_reason). A branch's KIND is not stored — `layout.py::sibling_kind` parses it from the id. |
| `export.json` | per cycle | The export artifact — the winning prompt by field name, the node config it ran under, and the provenance a consumer needs to trust the number (fitness under its named formula, n, lift + CI, θ, the rows' hash, the optimizer manifest). Written from the same call that stamps `index.json::final`; absent when no round ever closed. Contract: `domain/export.py`. |
| `log.md` / `review.md` (cycle) | per cycle | Per-cycle digests. Derived views — safe to delete and recompute. |
| `rounds/round_NNNN.json` | per cycle | Serialized `RoundResult` — the model IS the document (`save_round_file` persists `model_dump()`, `load_round_file` validates it back). Its `opt_search_point` field is the resume source of truth. |
| `.runtime/ledger.jsonl` | per cycle | Append-only fact stream. Escalation firings ride a `PhaseRecord(phase="escalation", event="rule_fired")` — no separate signals stream. |
| `.runtime/streams/…_p_best.jsonl` | per cycle | Per-sample PoBB snapshots. |
| `.runtime/cache/rounds\|candidates/` | per cycle | Per-node I/O (l1_generate/critique/score, l2/l3) + pre-scoring candidate checkpoint. |

The most-recent run's live readout (per-sample HIT/MISS, round summaries, SP tables), ANSI-stripped, also mirrors to the repo-root gitignored **`logs/latest.log`** — the headless tail when you're not watching `dashboard.json::current_round`.

Material facts land on disk in human-readable form; reads happen by opening files (`evidence` is the one read verb — a cross-campaign comparison is in no single file). Entry points never write campaign artifacts directly — every write rides the per-cycle ledger through two projections (live telemetry + audit). The allowlist is a structural invariant that fails loud (an out-of-allowlist write shows up in the file tree); no standing test, see [`../../tests/CLAUDE.md`](../../tests/CLAUDE.md).

**Editing optimizer state by hand.** Open `cycles/{cycle_id}/rounds/round_{N:04d}.json` before `resume --from N` and edit; keep the `opt_search_point` block round-trippable through `OptSearchPoint.model_validate`. On resume the cycle replays every prior `round_NNNN.json` in order to rebuild its state — there is no separate write-ahead log.

## Diagnosing a live or stuck run

A run that looks frozen is usually one of five things, and they are distinguishable in a fixed order. Follow it. Guessing from file timestamps first is how a healthy pause gets read as a crash.

**Ledger tail → `dashboard.json::declared_phase` → `.runtime/` flags → process table by command line → only then mtimes.** Each step answers a question the next cannot:

1. **Ledger tail** (`.runtime/ledger.jsonl`) — the append-only chronology. What the loop last actually did, and in what order. The only surface that can say *against which rival* and *in what sequence*.
2. **`dashboard.json::declared_phase`** — what the runner last *declared* about itself. A declaration, not the answer; see below.
3. **`.runtime/` flags** — what the operator asked for. A `pause.flag` present means the run is stopping on purpose.
4. **Process table, by command line** — is a producer actually attached. Match the command line, not the image name; several python processes are normal.
5. **Mtimes** — last, and only to date something the four steps above already explained.

**The trap this order exists to avoid:** control flags are consumed at the next **per-sample** checkpoint, not at the round close. A pause written mid-candidate takes effect within seconds — and to anyone watching file timestamps, a deliberate, clean, resumable stop is indistinguishable from a freeze.

### `declared_phase` is not `run_phase`

Two different facts, and conflating them is the costliest mistake here.

- **`declared_phase`** is written into `dashboard.json` by the runner's own process — the process that dies. Served raw it reports `running` forever after a `kill -9`.
- **`run_phase`** is **derived**, in exactly one place (`derive_run_phase`, `infrastructure/runtime_flags.py`), for every reader. It is never written to disk.

The declaration is one *input* to that derivation, consulted for `paused` and `gate` only. Everything else is computed.

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

**`detached` ≠ `paused` ≠ wedged.** `paused` is a clean, deliberate, resumable exit; `detached` means nobody is driving (the CLI exited, or `kill -9` left no terminal record); **wedged** is a producer attached and heartbeating but no longer *progressing* — `run_phase` cannot express that one, and it is derived separately from non-heartbeat ledger appends ([`../specs/frontend-surface-contract.md`](../specs/frontend-surface-contract.md)).

### Silence means dead, not thinking

A live cycle heartbeats its ledger through to `dashboard.json`. If that file goes untouched longer than `RUN_FRESH_S` (`infrastructure/runtime_flags.py`), an active cycle's producer is treated as vanished and the liveness reaper (`application/jobs/reaper.py`) stamps it `terminal` with `producer_vanished`.

**So an await that can outlast `RUN_FRESH_S` and writes nothing MUST heartbeat** (`optimization/dispatch/llm_call/heartbeat.py`) — this obligates every long await, not just LLM calls. The L4 outer cycle heartbeats its own ledger while awaiting each inner run for exactly this reason; without it a healthy outer round looks dead for the whole multi-minute inner campaign. The reaper never reaps a paused, check-in or origin-gated cycle — none of those is a dead producer.

### The `.runtime/` flags

Polled per checkpoint and consumed at the next **sample** boundary — transient, never to be confused with a durable ledger fact.

| Flag | Meaning |
|---|---|
| `pause.flag` | The single operator-interrupt flag. **There is no `stop.flag`.** The loop exits at the next checkpoint; the cycle stays resumable. |
| `checkin.flag` | The campaign is still authoring its origin. Dropped at skeleton creation, cleared when Start flips `checkin` → `active`. |
| `sample_lookahead.flag` | The operator's *request* that the walk hold a second sample in flight. What the loop actually ran at is `dashboard.json::sample_lookahead` — never serve the flag as that. |
| `skip.flag` | Skip the current unit at the next checkpoint. |
| `spend_cap` | Live `(usd, tokens)` ceilings. |

A fresh launch clears every polled run-control flag: a flag surviving the gesture it answered would re-answer the next one.

### Where the error text is

Error prefixes — `[CLIENT]` / `[SERVER]` / `[CONNECTION]` / `[PIPELINE]` — land in the latest `rounds/round_NNNN.json`, alongside the mirrored `logs/latest.log` above. The optimizer-call path carries a hard wall-clock (`_chat_under_deadline` → `OPTIMIZER_TIMEOUT`), so a hung optimizer call terminates itself. **An overnight death with no terminal record is machine-sleep or session-end class, not a code fault** — do not go looking for a bug in the loop.

## Recovery: resume, rewind, fork, sweep

Three workflows over one fork primitive (conceptual picture: [`../concepts/campaign-tree.md`](../concepts/campaign-tree.md)).

| Workflow | Command | Effect |
|----------|---------|--------|
| **Resume** | `resume` | Pick up from the latest completed round of the active cycle. |
| **Rewind** | `resume --from N` | Same `cycle_id`; archive trials after round N; resume at N+1. |
| **Fork on divergence** | `resume --fork-on-divergence` | On divergence — a round produced by a different optimizer, a package that no longer reproduces, or a decision that re-derives differently — mint a sibling cycle rooted at that round and continue. |
| **Sweep batch** | `new --sweep-batch` | Mint N siblings under one root from operator-authored overrides; 2-round sweep each. |

### Rewind — `resume --from N`

Use when the active cycle went somewhere you don't want (a bad L3 replan, or you edited config and want to re-explore from a round). `cycle_id` stays; you roll history back inside it. Rounds after N are deleted, state is restored from round N, and the run resumes at N+1. The measurement archive is preserved — per-sample results replay without backend calls.

**Partial rounds.** Ctrl+C (a resumable pause, `StopReason.PAUSED`) mid-round leaves ledger events but no `round:complete`; the public `rounds/round_NNNN.json` stays absent (the audit cache carries the partial with `"interrupted": true`) and the cycle stays non-terminal and resumable — no `finished_at`. `--from M` is admissible only if round `M` has a closing event — so after a pause mid-round-1, `--from 1` refuses and `--from 0` resumes cleanly. A plain `resume` (no `--from`) continues from the last completed round.

### Fork — `resume --fork-on-divergence`

Use when a **data-affecting** edit (scoring formula, `pipeline_overrides`, `exclude_nodes`, `dataset_name`) makes resume's replayer find recorded decisions no longer hold. The optimizer halts rather than drift; either revert, or commit with `--fork-on-divergence`. It mints a new `cycle_id` **in the same session**, rooted at the divergence point, copies pre-divergence trials, records `parent_cycle_id`, and re-runs the divergent round under the current scorer. The shared archive is not duplicated — both cycles read the same measurements through their own scoring ledger.

**A cut has a DIRECTION** — which side the run continues on, written at the cut and served as `fork_direction`. Usually the trigger implies it (`FORK_DIRECTION`, derived, so every fork already on disk answers it): a sweep / diag / steered fork is an `offshoot`, the child hanging off a line that keeps running; a `scoring_divergence`, `operator_rewind` or L2/L3 rebase **supersedes**, the child being the continuation the pointer moves to and the *parent* what was left behind. Same shape on disk, opposite reading — which is why nothing is deleted on a supersede: whatever the old version produced above the cut stays with the branch that produced it. A correction is the one cut taken before its consequence is known, so it records the answer it later measured on `ForkSpec.direction`, which outranks the derived default; that is the only way `equivalent` arises. **How a cut READS once served** — the timeline renumber, which side wears `superseded_by`, which cycle speaks for the campaign — is owned by [`infrastructure/CLAUDE.md`](../../promptpotter/infrastructure/CLAUDE.md) § The lineage tree, which `store/lineage_views.py` serves without deciding anything. What THIS layer must get right is that the direction, and how far it reaches, are on disk before any reader asks.

**A cut retires only what the branch has actually replaced.** The direction is recorded at the cut, but *how far* it reaches is read back from the branch's own ledger — the last round it minted a candidate for. A cut is taken before its consequence is known, so asserting "everything after this is replaced" at mint time is a claim about the future rendered as a fact about the past: a branch that was cut and then died retired a whole measured tail in favour of nothing, and rounds it had not re-run yet showed retired while still being the only record of themselves. The write side hands the branch exactly the candidates it retires (`_rebank_on_branch`), so the two sides cannot drift — one produces the other, and a cut that never finished simply reads as one that replaced nothing.

**A supersede retires the parent, on disk, at the cut** — `_mint_fork` stamps it terminal with `StopReason.REBASED` (`campaigns.mark_superseded`, idempotent, so the L2/L3 rebase that already finalized its own parent is a no-op). The parent stops writing *by design*, and an unstamped deliberate silence is indistinguishable from a crash: cold dashboard ⇒ `detached` ⇒ the liveness reaper stamped the record of what ran `producer_vanished` fifteen minutes later. Resumability is untouched — `finished_at` is a latch and `reopen_for_continuation` clears it the moment anyone resumes that cycle. An **`equivalent`** cut moves the pointer the same way and retires nothing: the two consequences are separate, and conflating them read the parent's terminal phase onto a campaign whose branch was mid-round.

**After a REPAIR both sides carry the same `candidate_id`** — a repair re-measures, it does not re-mint, so one individual holds two measurements: the corrected one and the one it withdrew, which is what the round was actually steered by. A retired candidate wears **no crown**: it was elected over rows the cut replaced, so `is_winner` is withdrawn until the branch re-elects.

**Policy-only edits** (PoBB knobs, patience, thresholds, `n_variants`, `exploration.*`) can't have changed the data trace, so resume continues in-place on the same cycle and `--fork-on-divergence` is a no-op. Past decisions stay as the audit record of the policy that made them; the new policy governs unevaluated rounds.

**Unless a repair lands.** Every resume first makes each closed round re-derive from its own rows — re-measuring cells it recorded without a measurement (a winner crowned on a holed panel; the panel gate only stops the round it fires in) and re-projecting a headline that no longer matches its winner's row. That is *incompleteness*, not divergence, so it runs whatever the config diff says. The winner replay above still runs on top, so a flipped crown forks rather than overwrites.

**A correction cuts first and is graded second.** Whether a round needs correcting is decided from the round document alone, and the branch is taken right there — before any re-measure begins, because the version a correction replaces is the version its descendants read and there is one copy of it. The parent therefore stays byte-identical to what ran, pre-repair round and stale candidate cache both still inspectable, and the operator watches the old round move to its own branch while the repair is still running instead of seeing an unchanged cycle that later jumps. The grade lands once the correction does: every round's optimizer packages are fingerprinted before and after, each rendered at its own point in the run, and the cut is stamped `supersede` if anything read differently or **`equivalent`** if nothing did. An `equivalent` cut is not a dead end — both sides carry the same content forward, so the candidates already generated for the next round come *across* rather than being regenerated from a package that never moved.

**The cut is a CANDIDATE, not a round** (`repair_cut` → `ForkSpec.from_candidate_id`): everything from the first candidate the repair moves retires with it, so a break in a round's third candidate leaves the first two on the line — while the fork still *lifts* whole rounds, because a round is the unit of election.

**The correction reaches the branch's LEDGER, not only its round file** (`repair.py::_rebank_on_branch`): a repair is a new measurement and a measurement enters through the ingress, so each retired candidate's identity is copied forward and its corrected score re-emitted onto the branch. Without that half the branch holds round files no ledger scan can see, cannot name a single candidate of its own, and every reader falls back to the version the correction just replaced.

**A resume's own corrections branch without asking.** `--fork-on-divergence` decides what happens when something changed from OUTSIDE — a different scorer, a different optimizer; those are the operator's call and still halt by default. A repair, or a generation the resume finds stale, is the resume doing its job: it branches itself. `resume` means "carry on from what is there" — find what is broken, fix it, keep what still holds, regenerate what does not.

**A cached generation records what it read.** Candidates are persisted with `consumed`, the `round_document_digest` of the round they were composed from; on resume that digest is recomputed from disk and compared. Both sides are persisted JSON, so unlike the package differential this reproduces across processes — which is what catches a critique re-distilled by an *earlier* resume, the common case, since the run that repairs and the run that continues are usually different processes. A cache with no recorded digest is **unvouched**, and unvouched branches too: nothing can say what it read.

A hole is plugged with a **real measurement, never an archive row** — a cached row for that `(node_configs, sample_id)` may have been produced as a PoBB *backfill*, measured out of the round's shared order to fill someone else's paired comparison, and adopting it as this candidate's own panel cell is what makes a repaired round unreproducible. The re-measure bypasses only the outer archive, so the inner spawn still resolves content-addressed and **continues the furthest-along campaign banked for that cell** instead of restarting it. Budget one inner run per hole; the cells that already have their own measurement replay from cache.

**Why rewind isn't enough:** rewind restarts under the *same* policy and would re-hit the same divergence; fork restarts under the *new* one.

### Human in the loop — steer & fork, pause

**HITL is not a separate I/O kind** — it collapses into the fork primitive above. The operator forks via `resume --fork-on-divergence` (CLI) or the webapp **Steer & fork** flow (Scoring inspector → `SteerForkPanel`): pause the run, edit the chosen searchpoint's prompt + node config + limits, and fork a sibling cycle tagged `operator_steered` (Control-remote design in [`../adr/0001-m12-control-plane.md`](../adr/0001-m12-control-plane.md); details in [`../specs/roadmap.md`](../specs/roadmap.md)). The fork mints a sibling cycle rooted at the chosen offset via `CycleEventLog.inherit_from(parent, offset)`; the new cycle inherits the parent's typed state at the cut and L1 picks up from there.

**Pause run.** Webapp's "Pause run" button and the CLI's `pause` verb fire the same `pause-cycle` command through the same `CommandDispatcher`, which writes `{cycle_dir}/.runtime/pause.flag` — the single operator-interrupt flag (there is no separate "stop"). The running loop's `Session.pause_check` polls it at the next checkpoint and the worker exits cleanly, but the cycle stays **non-terminal and resumable** (no `finished_at`): `start-run`/`resume` relaunches from the last completed round. Recorded as `StopReason.PAUSED`, and the request itself lands on the cycle's ledger as a `CommandRecord` — so *who asked* is auditable. Writing the flag by hand skips that record; use the verb.

`python -m promptpotter pause` targets the active cycle (`--campaign` / `--cycle` to name another, `--reason` to annotate). Ctrl+C in the running terminal is the third route — the fastest, since it does not wait for a checkpoint — and is also a resumable pause, not a terminal stop (see Interrupt handling below).

**An inner cycle stops when its owner does.** An L4 inner campaign runs in a child task under its own sandbox, whose pause flag nobody writes; it inherits the outer's pause predicate at the run-control binding seam (`runner/entry.py::_bind_run_controls`) rather than overwriting it. Without that a pause on the outer waited out the whole inner campaign, because one outer *sample* is an entire inner run.

Every ledger record is already typed; combined with `inherit_from`, "human in the loop" is just "operator chooses where to fork." No dedicated record type, no watched-file ingest, no new I/O kind — the existing fork primitive (Persistence) carries the whole semantic. See also [`../architecture.md`](../architecture.md) §0 (the five I/O kinds) and [`../../promptpotter/presentation/CLAUDE.md`](../../promptpotter/presentation/CLAUDE.md) (sanctioned mutating endpoints).

**Make a slow round finish sooner — the look-ahead control.** When the operator is watching a run crawl through its samples, the remote's **⇉** control runs the walk with several of a candidate's samples in flight instead of one, cutting that walk's wall clock roughly in proportion. Suggest it whenever someone asks why a round is taking so long; it is the only speed lever that needs no config change and no restart.

**What one press buys is the backend's to declare** (`Connector.concurrency_arming`), and the two forms behave differently enough that saying the wrong one misleads:

- `round` — the control is a button. It arms the next round's scoring and **expires by itself** when that round finishes scoring, so the button unlights on its own.
- `batch` — the control is a number field, because a round on a backend whose sample is a whole nested campaign runs hours and cannot bound a press. The operator names how many launch **together**; the walk waits for all of them before releasing the next group, and the arming is spent by exactly that group. `promptpotter-self` is this form — so the press does reach it, contrary to what this page claimed while the button was round-only.

Two things hold under either form. It **does not make the cycle babysat**: samples are absorbed in walk order and an in-flight one is discarded rather than recorded, so the run's rows are identical at any depth (unlike Skip, which does taint the cycle). And it **costs at most one discarded backend call per eliminated candidate**, shown as `sample_lookahead_discards` on the dashboard.

**Both layers are armed by naming the one you mean.** The arming is deliberately not inherited into a nested run — one press would otherwise multiply concurrency at every level at once — so arming an L4 outer campaign releases several inner campaigns together, and arming one of those inner runs (drill into it first; the command carries the path) holds several of its own rows in flight. Its ceiling is its own connector's.

It is browser-only and host-admin-gated (`scoring.sample_lookahead`) — there is deliberately no CLI verb and no config key, so an assistant can *recommend* the control but cannot press it. Contract: [`../specs/m12-api-openapi.yaml`](../specs/m12-api-openapi.yaml)`::setSampleLookahead`.

### Sweep batch — `new --sweep-batch`

Breadth-first comparison of N L1-prompt hypotheses: instead of one trial cycle on the active OSP, mint N cheap sibling cycles, each from a different operator-authored override. Sweep cycles sit flat under `cycles/` with `sibling_kind: "sweep"` and a shared `sweep_batch_id`.

**Per-fork protocol:** origin (cache-hit after the first) + 1 scored round + 1 generation-only round + halt with `SWEEP_COMPLETE`.

**Authoring.** One `*.yaml` file per arm under `datasets/{name}/sweep/`, shape `OperatorSweepFile` (`extra='forbid'` — typos fail at parse). `reason` is a label and changes nothing the fork runs, so **an arm setting no contrast lever is refused at load** (`application/sweep.py::load_sweep_payloads`, which names every offending arm at once): it would fork a copy of its parent and pay a full scored round to measure it.

```yaml
reason: >-
  Does the objective read better leading the prompt than buried mid-panel? Same evidence,
  measurand and confounds moved to the front slot.
l1_layout:
  persona: [measurand, confounds]
  problem_description: [rendered_prompt, pipeline_param_catalogue, plan, answer_distribution,
    critique, failing_samples]
```

`l1_layout` stamps per-slot signal-name lists onto the fork's starting OSP — the same L1 surface L2 writes when it fires, staged without firing L2. Edit only the slots you mean to move; an omitted slot keeps what it holds (`domain/l1_layout.py::coerce_l1_layout`). The slots are `L1_LAYOUT_SLOTS` and the placeholders that must each appear somewhere across them are `NODE_LAYOUTS["l1_generate"].mandatory` — read both there, never a copy: a layout failing them raises only once the fork is minted and its origin paid for.

```bash
python -m promptpotter new bbeh --backend-url http://127.0.0.1:8000
python -m promptpotter new --sweep-batch   # dispatches sweep-mode against the freshly-minted cycle
```

**Reading results.** The sweep branches are ordinary forks on the campaign tree — read them side-by-side in the webapp, or open each branch's `round_NNNN.json`. A batch groups by parent root and sorts by `round_1_top_lift`; `proxy_lift_corr` is meaningful once ≥4 paired sweep/full branches share an `l1_generate_hash`. **Sweep is screening, not validation** — promote winners to a full `new` run. L1-surface only; pipeline/scoring changes are intentionally absent from the operator file shape. Forks run sequentially (the active pointer doesn't tolerate concurrent mints).

## CLI flags — `new` and `resume`

`new <name>` mints a fresh session+cycle from an authored `datasets/<name>/` and runs from round 0. `new <file>` (a CSV — `Path.is_file()`) parses the file into a durable check-in campaign, runs the AI origin check-in (the same `checkin` node the web ingest uses), auto-confirms high-confidence findings, and — once the readiness gate passes — flips the check-in to `active` and runs the loop inline. It reuses the exact orchestration behind web onboarding (`ingest_draft` → `resolve_origin_turn` → `prepare_checkin_run`); the only CLI↔web difference is the CLI runs inline while the web start-checkin detaches. If a gap survives the resolver, `new` prints the open fields + questions and exits non-zero — nothing is minted on a guessed default; confirm with `--set` and re-run. After a successful file run the committed slug is first-class to `new <slug>` / `resume`.

| `new` flag | Purpose |
|---|---|
| `<name\|file>` (positional) | Dataset name under `./datasets/` (auto-loads its `campaign.json`) **or** a path to a raw CSV to ingest |
| `--config` | Campaign config JSON — overrides the dataset's default `campaign.yaml` (name form) |
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

- **First Ctrl+C** — cancels the in-flight call, banks completed work, declares the cycle `paused` (resumable), exits **130**.
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
   `datasets/{name}/campaign.yaml` or `pipeline.yaml` does **not** change an existing
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

4. **On L4 the identity inputs are NOT frozen — editing one mid-campaign re-measures the
   origin.** `connectors/promptpotter.py::_identity_config` fingerprints what the inner
   optimizer nodes RESOLVE TO (`_inner_optimizer_revision` — each node's prompt body, its
   resolved response schema, which is prompt text riding every call as `response_format`, and
   its config), plus the per-node information-flow layouts, **the injection renderers' own
   source** (`injection_source_digest`), **the estimator's own source**
   (`_measurement_source_digest`), the dataset's whole `inner_tasks.yaml`, and the inner
   benchmark's `pipeline.yaml` node configs *and* `campaign.yaml` — the worker model and the
   scoring formula included, since either changes what every cell measures. Both source digests
   are normalized through the AST, so a comment or docstring costs nothing while an expression
   voids the origin. Fact 2 does not cover
   these — they are read live, not snapshotted into `campaign.json` — so an edit lands on the
   *running* campaign: the banked outer origin stops joining and the next round pays to score
   it again. The function's docstring carries why a stale join would be the worse outcome.
   **Land config fixes before an origin is measured, never between its rounds.**

   **What is deliberately NOT in it, because a corpus that cannot survive them cannot
   accumulate:** the manifest's non-inner nodes (`checkin`, descriptions, `available_models`)
   and `APP_VERSION`. The version constant voided every banked cell on every release while
   saying nothing about whether the measurement had changed; `_measurement_source_digest` hashes
   the four modules that actually decide the number instead.

## Changing the composite formula — fork, never swap

**There is no live swap, and the reason is a gate rather than plumbing.** `round_scorer` compiles once during run init (`initialization/loop_start.py::populate_session_scoring`) from `campaign.json::scoring` and is never re-read. To change it: author a `per_round` formula over the names below, edit `campaign.json::scoring`, and `resume --fork-on-divergence` — the sibling starts at the divergence point and every round it banks is scored under one formula.

Swapping it between rounds instead would make the composite **incomparable to its own past** inside one cycle, silently. `EscalationFSM._advanced` — the L2/L3 stall gate — asks whether the cycle's best advanced since a layer fired, and answers on `best_composite_fitness` whenever the θ ruler is unavailable (a cold-started cycle). Redefine the composite mid-cycle and that comparison reads a change of scale as progress or as stall, with nothing to error on. The same argument retires the per-sample/per-round distinction this section used to draw: the per-sample scorer additionally rewrites recorded `hit`/`score` on every prior trace and trips the divergence replayer on the next resume, but neither is safe mid-cycle, and both have the same cure. `POST /commands/change-scoring-composite` is declared in `docs/specs/m12-api-openapi.yaml` and carries `x-status: declared-not-wired`; wiring it as specified would reintroduce exactly this.

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

The default composite renders in operator surfaces as `composite=0.6042 (Δ+0.1030 vs parent 0.5012)` per candidate — anchored on the row's matched parent, the same floor the accuracy Δ beside it uses — with the full formula text always in `log.md` (source of truth when reviewing finished cycles).

## Beta hosting state

Single-operator (auth-off) and hosted-beta (OIDC) share the same on-disk shape. The beta adds three operator-visible surfaces under `projects/{tenant_id}/`.

**Per-user quotas (`user.json`).** Abuse-limit knobs the launcher gates against (one tenant per user; missing file ⇒ defaults):

```json
{ "spend_budget_usd_total": null, "max_concurrent_cycles": 2, "max_campaigns_per_day": 10 }
```

Hand-edit to lift/lower caps; checked on every `mint-campaign` and `start-run`. The effective per-cycle spend cap is `min(requested, daily_cap - daily_spent)`.

**Campaign ownership + lifecycle (`campaign.json`).** Each manifest carries `owner_user_id` (cross-user reads return **404, not 403** — existence leakage is itself a violation) and `lifecycle_status: active | archived | deleted` (+ `_changed_at`, `_reason`). **`archive`** is the flag and nothing else — the tree stays in `campaigns/`, hidden from the default listing and restorable by `unarchive`, because a campaign with two possible homes made every enumerator carry a second parent. **`delete`**, by contrast, is physical and destructive (no recovery) — it removes the tree outright, or with `--keep-results` strips the heavy tiers and spares the keepsake (manifest + reports + the shallow langfuse loop trace — the Reports leaf + loop trace of the [storage taxonomy](#the-storage-taxonomy--connector--loop--dataset)), flagging the manifest `deleted`. Archiving or deleting a campaign is refused only while one of its cycles is LIVE (the one liveness derivation, `derive_run_phase`) — the active pointer is released and the verb proceeds. It used to refuse the **active** campaign outright and tell the operator to "switch first", naming a gesture that exists in neither the command vocabulary nor the webapp; in a single-operator workspace the campaign in view IS the active one, so both verbs were dead buttons. The cross-campaign measurement store (`measurements/`) is never touched, so siblings still cache-hit.

```bash
python -m promptpotter archive   <campaign_id> [--reason TEXT]
python -m promptpotter delete    <campaign_id> [--reason TEXT]
python -m promptpotter unarchive <campaign_id>
```

Each is idempotent, and each — from the terminal exactly as from the web — dispatches through `CommandDispatcher`, which writes a `CommandRecord` to the **workspace** ledger (`.workspace/events.jsonl`) before marking the manifest. The workspace ledger is the audit home because `delete` removes the campaign's own ledger, so it cannot record its own disappearance.

## The storage taxonomy — Connector / Loop / Dataset

There is **one** storage vocabulary, the operator's mental model. Every byte in a campaign tree lands in exactly one of six leaves (mutually exclusive, exhaustive — they sum to the on-disk total). The top-level axis is **Connector vs Loop vs Dataset**; **Loop** breaks into four. Classifier + endpoints: `presentation/api/routers/campaigns/storage.py` (`_leaf` / `_campaign_split`) — the code is the source of truth.

| Leaf | Parent | Contents |
|---|---|---|
| **Dataset** | — | `langfuse/datasets/` — the ground-truth mirror (input-data copy; usually the biggest chunk) |
| **Connector** | — | `.runtime/cache/**` + the per-sample `results`/`all_candidate_results` arrays carved from the public `rounds/round_*.json` |
| **State** | Loop | the resume point — non-array remainder of `rounds/round_*.json` (the read-once cycle seed rides the ledger, so it lands in **History**) |
| **Trace** | Loop | telemetry — `.runtime/streams/`, `prompts/`, `langfuse/{traces,observations,scores}/` |
| **History** | Loop | the durable event spine — `.runtime/ledger.jsonl` |
| **Reports** | Loop | readable output — the campaign manifest plus every top-level cycle surface, DERIVED from `CycleLayout` by `layout.py::_REPORT_NAMES`; the hand-copy it replaced had dropped `export.json`, so `--keep-results` deleted the campaign's answer |

**The keepsake is not a leaf.** What `delete --keep-results` spares (Reports + the langfuse loop trace) is a cross-cutting subset, surfaced as a one-line UI note — never a summed figure, so the partition stays MECE. The lifecycle ladder is a plain binary (`keep_results: bool` → `_strip_to_keepsake`), independent of this taxonomy.

**Ledger writers store once.** A cycle's `init` record carries a dataset *reference* (`dataset_size`), never an embedded copy of the rows; round 0's display record drops `round_result`. The bulk of a mature ledger is `snapshot` telemetry (65–77% of bytes), which is the live per-sample stream and is meant to be there.

**Running jobs (`.runtime/jobs/{job_id}.json`).** The browser-launched runner is tracked one file per job (`campaign_id, cycle_id, user_id, status, …`); reads filter by user. Concurrent campaigns are isolated via the per-cycle ledger ContextVar. The Account modal's Security pane surfaces live spend/concurrency/daily-mint counts against their caps.

**Identity** is the fifth I/O kind (§0 of `docs/architecture.md`): OIDC verification at the API trust boundary populates `IdentityContext`; tokens never appear past the middleware (ADR-0002 — review-enforced; no standing test). Stage 0 substitutes `default_identity()`. Contract: `docs/adr/0002-identity-foundation.md`.
