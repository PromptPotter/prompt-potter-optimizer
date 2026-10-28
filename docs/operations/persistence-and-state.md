# Persistence, State, and Recovery

Your work lives in `.promptpotter/`: one campaign per `campaigns/{campaign_id}/`, every cycle (root, forks, diags) flat under its `cycles/`. **A campaign is an object on disk, not a script that has to finish** — pause, resume, rewind or fork it ([§ Recovery](#recovery-resume-rewind-fork)).

## Four entities — where each one lands on disk

**Workspace → Dataset → Campaign → Cycle** — owned by [`../architecture.md`](../architecture.md) § Four entities (outermost → innermost). This page adds only the on-disk shape.

- **Workspace** — `projects/{tenant}/`, partitioned by lifecycle, so "what survives a delete?" is answered by looking.
- **Dataset** — tenant upload (`projects/{tenant}/datasets/{slug}/`) first, then shipped template (`promptpotter/assets/datasets/{name}/`). An ingested slug is first-class to `new` and `resume`.
- **Campaign** — `campaign_id = {dataset}__{rand6_hex}`, fresh per `new`; a nested name's `/` is written `.`. `campaign.json` is `domain/campaign.py::Campaign`; none of its fields is the id.
- **Cycle** — `cycle_{content_hash[:12]}` (+ `_fork_`/`_diag_` on branches). Paths always resolve as `(campaign_id, cycle_id)`.

### The hierarchy address

Every served entry (`GET /hierarchy`, [`../specs/frontend-surface-contract.md`](../specs/frontend-surface-contract.md) § The served hierarchy) is keyed by an address the server mints and never renames:

```
campaign := [inside "~"] campaign_id                 bbeh__20261024-091500
course   := campaign "::" cycle_id                   bbeh__20261024-091500::cycle_7f3a
round    := course "/r" round_n                      bbeh__20261024-091500::cycle_7f3a/r2
candidate:= round "/" arm_key                        bbeh__20261024-091500::cycle_7f3a/r2/<ArmKey>
step     := course "/@" offset                       bbeh__20261024-091500::cycle_7f3a/@418
inside   := course ("~" course)*                     the L4 sandbox chain, the `?descend=` wire byte-exact
```

`(round, arm_key)` is unique per course, so a candidate's address keys a row on every surface; its individual does not, since a repair re-measures one individual on two courses. A step's offset is the line index in its course's own ledger — the axis `?at=` already speaks. One minter, `domain/cycle_paths.py::hierarchy_address`; **a course's address IS its encoded `CyclePath`** (`encode_cycle_path`, `webapp/lib/ids.ts::encodeCyclePath`), so the webapp mints none, and `webapp/lib/ids.ts::courseAddressOf` is the one place a candidate's address is cut back to its course. A cycle directory is never renamed, so a retired branch keeps its address for ever. **A fork is stored as a sibling cycle and served as a child course**, its `facts.spawn` naming parent course, cut round and offset, source candidate, trigger and direction (`domain/run_records.py::ForkSpec`).

**There is no Session tier** — owned by [`../architecture.md`](../architecture.md) § A campaign has one root cycle.

## Active session pointer

`projects/{tenant_id}/.workspace/active_session.json` (`{campaign_id, cycle_id}`, `store/session_pointer.py::active_pointer_path`) is your latest launch: `new`, a browser Start and a fork each overwrite it. **It names ONE cycle and runs can be many**, so it is the terminal's default target and what an unpinned webapp follows — never "what is running"; that is each course entry's `state` on `GET /hierarchy`. `--campaign` / `--cycle` override it for one command; `--tenant` (default `"default"`) selects the partition. Re-running `new` on an unchanged declaration reuses the content-addressed root-cycle id and origin score, then diverges from round 1.

**The verbs are `presentation/cli/commands/verbs.py`** (`--help`); the read verbs are root `CLAUDE.md` § Commands'. **`compact-archive` is the only verb that can destroy a measurement**: `compact` moves `domain/scoring.py::UNREAD_PIPELINE_KEYS` off `panel` answers into `measurements/cold/`, `restore` puts them back row for row, `purge-cold` is the irreversible step; `origin`/`parent` answers and L4 outer traces are never eligible.

**The diagnostics are fenced — nothing the loop decides reads one, and nothing may.** The loop fires one (`application/diagnostics/verify.py::verify_on_saturation` on a 100% round) and reads none of it. A paid diagnostic or `bench` holds a machine slot and an account reservation while it scores, refused `409 cycle_busy` / `409 machine_busy` rather than queued — one door, `application/jobs/launcher/admission.py::paid_pass`. **`verify` is the canonical way to deepen a winner you want to believe**: fresh search cells, never a re-ask of a scored one (a re-ask measures model noise, and the L4 backend replays it outright).

## Layout

```
.promptpotter/
  block_packs/{name}/pack.json          # installed block packs (`config/paths.py::block_packs_root`)
  jobs/{job_id}.json                    # machine-global admission records
  projects/{tenant_id}/
    .workspace/
      active_session.json
      events.jsonl                      # workspace ledger — commands with no cycle, the account's events
    head_to_heads/{id}.json             # `domain/campaign.py::HeadToHeadRecord`
    drafts/{draft_id}/                  # an open check-in, until Start or Discard
    campaigns/{campaign_id}/
      campaign.json  log.md
      cycles/{cycle_id}/                # root + forks + diags, ALL FLAT
        dashboard.json  index.json  export.json  optimized.md
        log.md  review.md  hard_samples.json  readout.log
        rounds/round_NNNN.json
        langfuse/  prompts/
        .runtime/
          ledger.jsonl                  # append-only spine
          producer.lock                 # what "attached" means
          streams/round_NNNN_{member}.jsonl
          cache/rounds/
    measurements/                       # PAID; rooted at `Stores.archive_home`
      index.jsonl  configs/  cells/  cold/  claims/  derived/
    optimizer_reuse/  judge_reuse/      # PAID; a grader never reads the loop's answers
    diagnostics/                        # scoring verbs that mint no cycle
    datasets/  materialized/  task-context/
    uploads/{sha256[:16]}.{ext}         # every ingested file, kept before parsing; never served
```

**Why this shape.** Telemetry is temporal (each cycle owns its `dashboard.json`), audit is structural (frozen records keyed by the producing cycle), and cycles sit flat because a fork tree keyed by `parent` scales where nested directories don't. `measurements/` is a cross-cycle peer of `campaigns/`, so a fresh `new` cache-hits every origin sample under its own `campaign_id`. **`config/paths.py::SHARED_CACHE_DIRS` is the whole of the paid tier** — what `build_stores` roots at the archive home, what `reset` preserves and what the storage report counts — so a cache missing from it is destroyed by the first `reset`. No recycle bin: an archived campaign stays in `campaigns/`, hidden by `lifecycle_status`.

## File reference

Each row's contract is its model; this table says only what the type cannot.

| File | What the type does not say |
|---|---|
| `campaign.json` | One editor, `CampaignStore.update_campaign` (`Campaign.edited`). The origin's locked criterion is a `ScoringLockedRecord` on the root ledger, never written back. Run state is the ledger's. |
| `dashboard.json` | `LiveDashboardState`, a fold of the ledger. No read parses it: a read serves `application/served_dashboard.py::ServedDashboard`, built off `projection.py::fold_at`. |
| `index.json` | `CycleIndex`, one writer, no reader — every reader folds the ledger (`projections/cycle_index.py::read_cycle_index`). Branch kind is parsed from the id (`store/layout.py::sibling_kind`). |
| `export.json` | `domain/export.py`; written with `cycle_final`, absent when no round closed. |
| `optimized.md` | Which resolved values the optimizer MOVES and whether the model sees each — a person's file. |
| `hard_samples.json` | `domain/cells.py::HardSamples`, rewritten each round close. |
| `readout.log` | Every launch writes it, whatever started it; one file per cycle, each launch opening with a `Readout:` line. A fork mid-run moves it to the fork. `logs/latest-readout-path.txt` names only the newest terminal launch's. |
| `rounds/round_NNNN.json` | A checkout of the round's last statement for the operator; no code reads it back. One writer, `closed_rounds.py::RoundFileProjection`. |
| `.runtime/ledger.jsonl` | The only surface that says which optimizer node RAN (`llm_call` records); an `arm` record carries the individual's whole `lineage`. |

**What the cycle runs on is ledger records, read over the fork chain.** `bank_partition` (`domain/bench.py::PartitionRecord`) is appended at mint; a fork reads its parent's, so a re-cut never moves a cycle off its draw (`loop_start.py::cycle_partition`). `resolved_run` holds two halves at opposite cadences: its `declaration` (search SPACE) — the LAST wins; its `experiment` (CELLS) — the FIRST wins, a later disagreeing resolution logged, never adopted (`pipeline_resolve.py::resolve_pipeline_for_campaign`).

Material facts land on disk human-readable; entry points never write campaign artifacts directly — every write rides the ledger.

**A round is resumed from its close, never from its file.** `resume` and every fork read each round's last statement off the ledger (`closed_rounds.py::closed_rounds`); entering round N displaces N and later, a `rewound` entry displaces their generations and skips too. Editing a round file changes nothing a resume reads.

## Diagnosing a live or stuck run

**Ledger tail → `cycles` → process table by command line → only then mtimes.** Guessing from timestamps first is how a healthy pause gets read as a crash.

1. **Ledger tail** (`.runtime/ledger.jsonl`) — the chronology, and each `command`'s `command_ack`: `accepted` (armed), `applied` (taken), `rejected` (no loop will).
2. **`python -m promptpotter cycles`** — derived `run_phase`, the producer reading, the pause cause.
3. **Process table, by command line** — which process holds the cycle; several python processes are normal.
4. **Mtimes** — only to date what the above explained.

**The trap:** a command is taken at the next **per-sample** checkpoint, so a pause asked mid-candidate lands within seconds and looks exactly like a freeze.

### `declared_phase` is not `run_phase`

The declaration is the runner's own `run_phase` record, written by the process that dies, so raw it says `running` forever after a `kill -9`; `dashboard.json::declared_phase` mirrors it. **`run_phase` is derived** in one place (`infrastructure/runtime_flags.py::derive_run_state`) from the ledger and the producer lock, never written.

### The phase vocabulary

`domain/phases.py::RunPhase` and `RUN_PHASE_INFO` are the vocabulary; derivation is `runtime_flags.py::read_run_state`. `queued` and `starting` read `producer: claimed`, which every guard treats as attached — a second launch, a delete, a paid verb are refused from acceptance, not from process start; the one writer is `launcher/admission.py::claim_cycle`. **Which verb a cycle admits is served** (`RunAdmission`), never inferred from the phase. Every paused exit names its `PauseCause`, written at one seam (`run_observers.py::declare_run_stop`).

### Liveness — the lock says attached, the ledger grades it

**Attached is the producer lock (`infrastructure/producer_lock.py`) and nothing else**; `live` / `idle` / `wedged` is graded off the LEDGER's mtime — never `dashboard.json`'s, which is debounced. `detached` ≠ `paused` ≠ wedged: wedged is a producer state, and nothing takes the cycle from under it. **No silence is reaped**: `application/jobs/reaper.py` stamps `producer_vanished` only on a `running` cycle whose lock nobody holds. **A long await must still heartbeat** — owned by [`application/CLAUDE.md`](../../promptpotter/application/CLAUDE.md) § Conventions.

### Commands ride the ledger

`.runtime/` holds no control file. A command is acked `accepted` into the cycle's inbox (`ledger_scan.py::Controls`) and `applied` at the checkpoint that takes it; a new `running` declaration or any stop clears what stood, and one no loop will take is refused at dispatch. **The standing budget is the last `standing_budget` record on the cycle's own ledger** (`runtime_flags.py::standing_budget`), so `set-limits` from another process binds at one stat; its money axes bound the whole campaign.

### Where the error text is

A failed cell's `ErrorCategory` and message land in the latest `rounds/round_NNNN.json` and `readout.log`. **An overnight death with no terminal record is machine-sleep or session-end class, not a code fault.** Only a hard kill leaves containers behind; the next run on that backend sweeps them (`infrastructure/docker_host.py::reap_dead_producers`), keeping task images and the package cache.

## Recovery: resume, rewind, fork

| Workflow | Command | Effect |
|----------|---------|--------|
| **Resume** | `resume` | Continue the active cycle from its latest closed round. |
| **Rewind** | `resume --from N` | Same `cycle_id`; rounds after N displaced; resume at N+1. |
| **Fork on divergence** | `resume --fork-on-divergence` | Mint a sibling cycle rooted at the divergent round and continue. |

**Until an L1 round closes, a resume re-enters round 0** and holds at the origin gate again. Refusals, from every way in: `409 cycle_busy` (one live run per cycle), `409 config_drift` (the dataset no longer resolves to the minted origin, unless policy-only — `application/jobs/mint.py::refuse_drifted_resume`), `409 ledger_unreadable` (a line this build cannot read; remedy `delete <campaign>`, which keeps the caches; `restamp` lists every such cycle).

### The primitive

A fork is a new cycle whose first record (`cycle_minted`) names its `parent`, the `forked_at_offset` its history begins at, the `cut_round` (how many parent rounds it LIFTS; `0` = clean offshoot) and its `ForkSpec`. It lands flat; the tree is rebuilt from mint records, never from nesting or `index.json`. **Nothing is copied at the cut** — the parent's prefix is walked (`ledger.py::ledger_chain`), and the parent's ledger gets a `FORK_CUT` checkpoint. `from_candidate` is a required `RoundArm`, resolved once at mint (`fork_siblings.py::fork_edge`); a parent standing on no elected candidate cannot be forked. A fork inherits no standing budget: its rounds are bounded by what the parent's cap left (`served_dashboard.py::fork_remainder`), its money by the campaign ceiling.

**A new fork driver** adds a `ForkTrigger` member and fills a `ForkSpec` — no edit to `mint_fork`'s body; an override that is not OSP-carriable, or that needs a parallel persistence directory, wants its own layer. Two forks read the same `measurements/` rows by content hash, so a second fork's origin costs zero LLM calls.

### Rewind — `resume --from N`

`cycle_id` stays; the archive replays per-sample results free. `--from M` is admissible only if round M closed — after a pause mid-round-1, `--from 1` refuses and `--from 0` resumes.

### Fork — `resume --fork-on-divergence`

For a **data-affecting** edit (scoring formula, `pipeline_overlay`, `exclude_nodes`, `dataset_name`) whose replay no longer holds: the optimizer halts rather than drift; revert, or fork. Rewind is not enough — it restarts under the same policy and hits the same divergence. **Policy-only edits** (PoBB knobs, patience, `n_variants`, `exploration.*`) resume in place; `--fork-on-divergence` is then a no-op.

**A cut has a DIRECTION** (`ForkSpec.resolved_direction`): a diag or steered fork is an `offshoot`; a `scoring_divergence`, `operator_rewind` or L2/L3 rebase **supersedes**; a correction records its measured answer, the only source of `equivalent`. How a cut READS once served, how far it retires, and *identity outranks position* after a repair — owned by [`infrastructure/store/CLAUDE.md`](../../promptpotter/infrastructure/store/CLAUDE.md) § The lineage tree; this layer must have the direction and its reach on disk before any reader asks. **A supersede retires the parent at the cut** (`CampaignStore.mark_superseded`, `StopReason.REBASED`) unless a producer holds it; an undeclared deliberate stop would read as `producer_vanished`.

**Every resume first repairs** — re-measuring cells recorded without a measurement and re-projecting stale headlines; incompleteness, not divergence, so it runs whatever the config diff says, and a resume's own corrections branch without asking. **A correction cuts first and is graded second**, so the parent stays byte-identical to what ran. **The cut is a CANDIDATE, not a round** (`ForkSpec.from_candidate`), and the correction reaches the branch's LEDGER through the whole ingress (`repair.py::_rebank_on_branch`). A cached generation records the `round_document_digest` it read; an unvouched cache branches. A hole is plugged with a **real measurement, never an archive row** — a cached row may be a PoBB backfill measured out of the round's order.

### Human in the loop — steer & fork, pause

**HITL is not a separate I/O kind** — it is the fork primitive. The webapp **Steer & fork** (`SteerForkPanel`; refused below the top level) and `resume --fork-on-divergence` fork a sibling tagged `operator_steered` via `CycleEventLog.inherit_from(parent, offset)`. **`keep_rounds` picks the act**: unset, a clean offshoot from the origin; set, an `operator_rewind` lifting rounds `0..N-1` and continuing at N — the shape an applied mask takes ([`mask-projection.md`](mask-projection.md) § Applying one); the terminal's gesture is `resume --rewind N`.

**`config_overrides` carries the fork's whole `OptimizationConfig` delta** — budget plus policy toggles — so a fork at offset 0 A/Bs a behaviour knob in isolation without moving the global default; a behaviour-knob change is a sibling cycle. **`budget.rounds` on a fork is an absolute target, not a remainder**: the fork's counter continues from its parent's. **Teardown-only state-sync was rejected — do not re-propose** (it reverses the folder-UI §0 commitment).

**Pause** — the button, `pause` and the first Ctrl+C all fire `pause-cycle` through `CommandDispatcher`; the cycle stays non-terminal, `paused` with cause `command`. **The arm gate** (`optimization.arm_gate`: `off` · `review` · `author`) holds a round before any arm is measured; `submit-arms` answers it (`SubmittedArm`, an edit of the round's parent), banked as a second `round_proposed` record so a resume re-holds only where no answer stands. The knob is `policy`-scoped. **An inner cycle stops when its owner does** (cause `enclosing`). **The look-ahead (⇉)** — who may press and why — is owned by [`access-model.md`](access-model.md) § host-admin ↔ user; on disk, the request is the accepted `set-sample-lookahead` and what ran is the served `sample_lookahead`.

## CLI flags — `new` and `resume`

`new <name>` mints from `promptpotter/assets/datasets/<name>/`. `new <file>` ingests a CSV into a check-in draft, runs the origin check-in, and Starts it once the readiness gate passes; a surviving gap prints its questions and exits non-zero — nothing is minted on a guessed default (`--set` and re-run). **The flag set is `presentation/cli/commands/verbs.py`.** The second Ctrl+C force-quits; a sent call is cancelled only where that stops its bill (`Connector.cancel_stops_billing`).

## Will a config change re-score? — the measurement cache

1. **The measurement key includes the connector config, model included** — `node_configs` (overlay-merged `pipeline_params`) plus the sample's content (`Sample.key`), never its dataset or position. A change at node *N* re-measures every sample whose pipeline ran past *N*.
2. **A running campaign runs its minted DECLARATION.** Editing `dataset.yaml` changes no existing campaign: mint a fresh `new`, or `--fork-on-divergence` in place.
3. **The cycle id agrees with the measurement key** (`runner/campaign_ids.py::origin_content_hash` over the same params), so two origins differing only by model get distinct `cycle_id`s. **Always key a surface by `(campaign_id, cycle_id)`** — two campaigns on one dataset+config share a root `cycle_id`.
4. **On L4 the identity inputs are NOT frozen.** `application/runner/inner/connector.py::_identity_config` fingerprints the inner treatment (prompts, response schemas, configs, member knobs, the runtime's `source_digest`), the estimator source and the inner dataset's `pipeline` + `campaign`; both source digests are AST-normalized, so a comment is free. These are read live, so an edit re-measures the *running* campaign's origin. **Land config fixes before an origin is measured, never between its rounds.** Deliberately excluded: non-inner manifest nodes, `APP_VERSION`, and the `Seat` roster (each seat is its own `source_pin`).

**Concurrent walks buy a cell once.** A cell `(node_configs, sample_key)` measured once is the row every walk reads; a second measurement is money spent to make headlines disagree. The protocol is `measurement_archive.py::ReplayFeed` (`search_point_scorer.py::_claim_cell`): read forward, claim by OS file lock, share the row the moment it returns, no expiry (the kernel drops a dead holder's lock). **`force_fresh` claims nothing.**

## Changing the composite formula — fork, never swap

The scorer compiles once at run init (`initialization/loop_start.py::populate_session_scoring`) and is never re-read: edit `campaign.json::scoring`, then `resume --fork-on-divergence`. **A swap mid-cycle would make the composite incomparable to its own past** — `escalation/state.py::_improved` falls back to `best_composite_fitness` and would read a change of scale as progress or stall. No command kind changes it mid-cycle, and adding one reintroduces exactly this.

**A `per_cell` formula is what θ is fit on** (`domain/scoring.py::Scorer`), so it is the only route a latency, cost or reliability term reaches the election; charge it per cell, and keep correctness `per_sample` so a correct-but-slow answer still reads HIT. Names: `scoring/formula/compiler.py::objective_namespace` for a formula, `evaluators.py::_REGISTRY` + `metrics.py::_MEANED_CHANNELS` for a mask; helpers `SAFE_BUILTINS`; undefined names raise. **`target_prompt_chars` is a fact about the CANDIDATE**, so a length penalty's normaliser is a constant written into the formula (the origin's reading via `evidence --metric 'expr:target_prompt_chars'`) — the scorer id hashes formula text alone.

## Lifecycle — archive, delete

`owner_user_id` on `campaign.json`: cross-user reads return **404, not 403**. **`archive`** is a flag (`lifecycle_status`), restorable by `unarchive`; **`delete`** removes the tree, or with `--keep-results` spares the keepsake. Both refuse only while a cycle is LIVE, and both record on the **workspace** ledger first, since `delete` removes the campaign's own. The measurement store is never touched.

## The storage taxonomy — Connector / Loop / Dataset

Every byte lands in exactly one leaf — Dataset, Connector, and Loop's State / Trace / History / Reports — classified by `store/layout.py::classify` and reported by `application/maintenance/storage_report.py`. Reports derive from `store/layout.py::_REPORT_NAMES`, never hand-listed: a surface missing from it is deleted by `--keep-results`. The keepsake is a cross-cutting subset, never a summed figure.

**Jobs (`.promptpotter/jobs/{job_id}.json`)** record ADMISSION only (`jobs/registry.py::JobStage`, caps, a refusal, `released_at`); how a cycle ended is its ledger's.
