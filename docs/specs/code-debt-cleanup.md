# Code-Debt Cleanup — Backlog

**Only work that is blocked or multi-arc** — including every shape a pass noticed off its task (root
`CLAUDE.md` `<situational-awareness>`). Anything one edit closes is fixed in the pass that found it; never the root of a patch just shipped (root `CLAUDE.md` `<root-fix>`). An item ships by
being deleted. Each entry: `file::symbol` — the defect — the action — **Rides with:** the work that
reaches it — **Re-test:** the command or question that says it still stands. Run the re-test before
acting.

## URGENT — shapes that hide the backbone from a reshape

**Rides with:** any pass in the file named.

- **Left by the upload check-in's 422** (`25be69eac` cut rows on every draft read; fixed 2026-11-02 by
  gating `startability.py::draft_samples` on `origin_readiness.py::columns_confirmed`):
  - **"Run the resolver now" lives three times**: webapp `useIngestFlow` (`advance`, `land`,
    `submitContext`), CLI `new.py::_hold_checkin`, `DraftCampaign.resolution_pending`. Action: the served flag
    alone. **Rides with:** the host assistant ([`host-assistant.md`](host-assistant.md)).
  **Re-test:** `git grep -n "void runCheckin" -- webapp/lib/hooks/useIngestFlow.ts` is one line, under `resolution_pending`.
  - **A Start's check-in stops at `queued` / `starting`**: the mint closes the draft, so its read
    404s, and no served answer names the campaign a draft's Start minted, so no poll can follow it
    there. Action: one read of a Start off its job (`CampaignLaunch.draft_id`, its stage, then
    `Job.hop`). **Re-test:** Start a queued check-in; the view lands on the minted campaign unreloaded.
- **A backend wire skew is discovered as a 100% degraded origin, not refused at launch** — the box's
  TermNorm read `answer_field` while PromptPotter sent `answer_fields` (2026-10-31); the origin paid
  for every cell to learn it. Action: the connector's health/probe serves the wire contract it
  speaks, and `new` refuses a node config that contract rejects before any cell. **Rides with:**
  the connector probe. **Re-test:** a launch against a backend one wire rename behind fails at
  the probe, naming the key.
- **TermNorm words a warning's step into its message** (`research_pipeline.py` `f"{step_name} failed: {exc}"`
  over a step that raises `"llm_only: …"`), though `StepWarning` carries the step as a field: a served
  health sentence still reads `llm_only failed: llm_only: …`. Action: the message is the fault alone.
  **Rides with:** the next TermNorm change. **Re-test:** `git grep -n "failed: {" -- backend-api/api`
  in the TermNorm checkout.
- **A skipped automatic verify is a log line**: `diagnostics/verify.py::verify_on_saturation` catches
  `DiagnosticError` / `BankedSearchPointError` (no round scored the winner, its banked point does not
  rebuild, no closed round 0, a pause before any cell, no scoreable fresh cell) into `logger.info`, and
  `verify_candidate` re-opens the session from disk mid-loop. No verify record holds a skip: most raise
  before `VerifyEnterView`, and propagating would stop a run over an optional pass. Action: the skip is
  a refusal banked on the one pass that § One record shape for every scoring pass builds, served in its
  can-run status. **Rides with:** that arc's first builder. **Re-test:**
  `git grep -n "verify skipped" -- promptpotter/application/diagnostics/verify.py`.

- **PoBB re-decides a round a pause cut, and decides it differently** — capo-pobb paused at a fresh
  cell past round 2's banked walk and resumed: the resume banks two eliminator decisions beside the
  first launch's, and one `p_best` reads 0.096135 where the straight run read 0.100021, on the same
  paired breakdown (the same on `batch/2~3`, before the walk bank). A resumed comparison votes on a
  number no straight run computed. Action: an interrupted round's eliminator decisions retract on
  re-entry, and its reading is the one the round opened on. **Rides with:** the eliminator.
  **Re-test:** drop `capo-pobb` from `scripts/offline_run.py::BOUNDARY_RESUMED`; its `resumed/` leg
  reads UNMOVED.

## NEXT ARC

**Rides with:** the next arc's first builder wave.

### The excision pass (six layer audits and a plan review, 2026-11-05)

One read-only audit per layer, each tracing a real path end to end; then four reviewers read the plan
for transition code, coherence, the risky cuts and the layers no audit walked. Each action is the
largest cut that holds: the layer goes unless something in it varies, and no arc builds on a shape a
later arc deletes. **Rides with:** the arc each group names, in [`roadmap.md`](roadmap.md) § The crunch
(operator, 2026-11-04).

**Decided once, read by every arc below.**
- **Two carriers.** The run's `RunScope` (§ Config resolution and run scope, item 4), bound once where the
  cycle opens, carries the plan, the ledger and the sender. The member's node context yields a per-call
  context holding node, config, round and index only. A `ContextVar` that stays propagates within one
  task. U2 binds `RunScope`, S fills its sender, the potter arc deletes the bundle. **Re-test:**
  `git grep -c "ContextVar(" -- promptpotter`.
- **One subject.** `domain/cycle_paths.py::Subject` (path, candidate, round, at, mask): `?descend=`
  (`Subject.below`), the evidence string (`Subject.parse`, rebuilt by `api/reads.ts::subjectKey`) and the
  hierarchy addresses (`Subject.parse_address`) are its encodings, and `cycle_addressing.py::resolve_subject`
  its one id-prefix resolution. The webapp's hash is not one yet: U5, which owns the workspace's keying,
  moves it onto the same grammar.
- **View state.** Whatever names the viewed thing (subject, candidate, at, view keys, round pick, sample
  set) lives in the hash; a module store holds only transient state.
- **One fold vocabulary**: a `Projection` on `read_model.py::LedgerIndex`. `LedgerFold.feed`, the tracing
  sinks' `on_*` and the `RunCallbacks` doors go (U3).
- **One word per thing.** "Arm" is the round's; a head-to-head's arms become `RunStart`s (U2).
  A host task is a command id (Q1).

**Batch E: each cut lands final.**
- **EN: a draft spells its config flat; the pipeline answer is flattened, re-packed and edited twice.**
  `domain/checkin_draft.py::DraftFile`, `domain/draft_edit.py::EditDraftPatch` and `draft_campaign.py::DraftCampaign`
  each hold the declaration as five flat fields (`scoring_matcher`, `scoring_dials`, `pipeline_overlay`,
  `pipeline_steps`, `optimization_overrides`). Two functions rebuild a `CampaignConfig` from them, one per
  basis; the scoring conversion loses a pinned formula's anchors both ways; one bag holds node config and
  narrowing, split by `isinstance`; the kept steps are a list turned into exclusions. Nothing varies:
  `authored.py::with_optimization` already lays a delta over a config. `draft_build.py::draft_wire`
  flattens it into the draft; `useConnector.ts::draftConnector` re-packs it into
  `types/connector.ts::ConnectorView` beside a second read-state vocabulary, `types/connector.ts::PipelineStatus`.
  Two editors edit one config row with two parsers (`node-config.ts::parseKnob` beside
  `node-config.ts::coerce`), behind `NodeSurface`, `NodeConfigEditor` and `PromptWithLibrary` forwarding
  to each other. Action: the draft holds one partial `CampaignConfig` laid over its basis, and the patch
  addresses config paths; the five fields and their converters go, absorbing the `DraftCampaign` twin and
  the optimizer-switch row of `draft_patch.py`; `draft.json` is run data, so it is wiped. The draft carries
  the pipeline response whole; `ConnectorView` and `PipelineStatus` go and `useConnector` returns the read
  result. The one node editor, with the strict parser, is U1b's. **Re-test:**
  `git grep -c "scoring_dials" -- promptpotter`; `git grep -n "interface ConnectorView" -- webapp`.
- **EM: `domain/wire_record.py::WireRecord` is a second model framework beside pydantic**: a
  source generator (exec plus linecache) behind six classes, each shadowed by a `TypedDict` and bridged
  back through `__get_pydantic_core_schema__`. What varies is speed only ("no per-cell field loop").
  Action: time a cold fold of a large ledger both ways; where pydantic holds, the generator goes and the
  six classes are plain frozen models. **Re-test:** `git grep -n "class WireRecord" -- promptpotter`.

**Batch 3: the run's start (U2) and the potter arc (U1b with EP).**
- **Five doors and three job lifecycles meet in one `HeldRun`.** `jobs/launcher/launch.py::_hold`
  switches over three request types; a head-to-head mints through a second door and plans and prices
  before admission, while a single launch admits first; `embedded_run.py::run_campaign` and
  `runner/inner/spawn.py::_open_seat` mint or resume their own way; `admission.py::embedded_share` and
  `admission.py::inner_share` copy one lifecycle beside `run_job.py::run_held_job`. The detached child
  rebuilds its plan through `run_job.py::open_held_cycle` and `mint.py::resolve_resume` (the drift
  check), while the inline path runs the mint's session: two sessions for one run. What varies, and
  stays: what starts (a mint, a cycle, a check-in, mint-or-continue), its admission (a slot that may
  queue only when one run is asked, no slot, the enclosing book, carried by a fork) and how it runs
  (detached, inline, a task); a plan holds a `Session` and cannot cross the process boundary. Action:
  `start(RunStart × N) → [Launched(job, held or none)]`, a `RunStart` holding those three fields; the
  running process builds its `HeldRun` by one drift-checked `open_cycle(hop)`, which binds `RunScope`; a
  fork is `open_cycle` under a carried admission inside `run()`, never through `start`. The two
  `run_records.py::RunWiringRecord` writes stay: they record two moments (declared and admitted budget).
  **Re-test:** `git grep -c "release_slot(\|job_registry.release(" -- promptpotter`.
- **Run init is five modules in the wrong order.** `runner/entry.py::run_optimization`'s preamble,
  `runner/run_prep.py::prepare_run`, `origin.py::establish_campaign_origin` and
  `initialization/loop_start.py::finalize_loop_state` write `session.state` from eight modules, and ORIGIN
  starts before INIT. Action: `open_cycle` in `runner/`, INIT then ORIGIN; it is § Config resolution
  items 4 and 5 made concrete. **Re-test:** `git grep -c "session.state.[a-z_]* =" -- promptpotter/application`.
- **Round 0 has two doors.** `origin.py::establish_campaign_origin` walks it before the cycle exists;
  `bench/node_context.py::rewalk_origin` walks it again for the gate and the repair. Seated from outside
  the walk and kept: `origin.py::try_inherit_fork_origin` (a fork's concluded origin), a standing origin,
  and an empty bank. `origin.py::lock_criterion` changes the scorer's id, which keys the difficulty
  ruler, `bench/difficulty.py::DifficultyView` and `indexes/sample.py::SampleIndex`. Action: round 0 is
  seated unmeasured, inherited or standing (an empty bank seats no cells), and walked only inside the round
  loop; its close locks the criterion, re-opens the scorer-keyed state and carries the origin's accuracy
  and formula, which INIT's exit stops serving; the bench pass and its set-aside run after the gate.
  Absorbs § The origin gate (batch-2 reviews, 2026-11-05). **Re-test:**
  `git grep -n "def establish_campaign_origin" -- promptpotter`.
- **Each entry point builds its own answer to a launch**, though the ack's `run_records.py::LaunchedEffect`
  records the job. Action: the dispatcher computes one wire answer (the applied model, else the launched
  jobs, else the record); `checkin.py::StartCheckinResponse` and `launcher/head_to_head.py::HeadToHeadStarted` stop
  being wire models. **Re-test:** `git grep -n "class StartCheckinResponse" -- promptpotter`.
- **`--diag` mints a sibling cycle** (`commands/launching.py::_diag_target`) and `potter/generation_only.py`
  drives its rounds a second way. Action: an unmeasured mode on `RunStart` that the round loop reads;
  both go. Shares `generation_only.py` with the potter arc. **Re-test:** `git grep -n "def mint_diag_sibling" -- promptpotter`.
- **The test builders replay production by hand**: `tests/factories.py::seat` builds a cycle as run init
  does, `tests/factories.py::close_round` replays the runner's append order, and two more mint "as the mint
  does". Action: they call `open_cycle` and the mint. **Re-test:** `git grep -c "def seat(" -- tests/factories.py`.
- **The potter arc (U1b with EP): potter's calls bypass the bench's call door**, and its prompt is a class
  U1b deletes, so the two land as one arc and one re-key. The peers ask through `node_context.py::Calls`;
  potter builds each call by hand (`dispatch/facade.py::build_bundle`, then `llm_call.py::run_optimizer_node`)
  and handles one fault four ways, one silently: the round's adapter step runs under `graceful`, so a
  failed critique is only logged. `escalation/firing.py::Layer` and `escalation/firing.py::TransitionResult`
  are a second member protocol; `dispatch/bundle.py::InjectionBundle` hand-copies run state for every
  renderer; `escalation/rules.py::_first_match` runs a fixed rule table. The fitness formula has a second,
  short spelling (`evaluators.py::resolve_cell_formula`) that only ever reads "fit", banked on four views
  and every arm record. Action: `Calls.ask(prompt, reply=Model)` with one fault policy, and
  `run_optimizer_node` goes; renderers read a potter-owned view of the node context and the ladder, its
  derived reads in a hashed potter module so a prompt change still re-keys, each signal's reads
  satisfiable wherever it renders; the rules three match functions that keep the rule name
  `escalation/rules.py::EscalationEvent` stamps; one formula spelling, the anchors off the records.
  Rewritten modules land at Wave C's paths. **Re-test:** `git grep -n "class InjectionBundle" -- promptpotter`.

**Batch 4: the ledger (U3, with ET) and spend (S).**
- **U3: the ledger read stack.** Three push paths (`ledger.py::CycleEventLog` subscribers,
  `read_model.py::appended_here` and `event_stream.py::CycleLedgerTail` re-reading bytes into its own feed),
  three fold protocols, every `CycleEventLog.open` re-counting the ledger, store accessors four hops deep,
  and `live_dashboard/projection.py::DashboardCheckout` writing a `dashboard.json` only
  `scripts/offline_run.py` reads. Action: `RunScope`'s ledger is the one producer door (`RunCallbacks` and
  the telemetry `emit_*` go); `LedgerIndex` the one live fold with the writing projections on it;
  accessors `append` and `last`; the audit sidecar a fold. **Re-test:** `git grep -c "CycleEventLog.open(" -- promptpotter`.
- **ET, inside U3: tracing translates the ledger twice.** `tracing/bridge.py::TracingProjection` re-encodes
  records into a sink vocabulary, `tracing/langfuse_sink.py::LangfuseSink` re-encodes it again over
  `tracing/langfuse_client.py::LangfuseLogger`; `tracing/file_sink.py::FileSink` has no reader. Action:
  Langfuse and MLflow are each a writing projection on `LedgerIndex` over the typed records; the
  translator, the fan-out and the file sink go. [`architecture.md`](../architecture.md) §0.5 names
  "Langfuse-shape events" as the tracing bucket's foundation; that line becomes the projections in the
  same commit (operator, 2026-11-05). **Re-test:** `git grep -n "class FileSink" -- promptpotter`.
- **U3: the ledger is narrated twice.** `views/readout.py::ReadoutProjection` (the readout log) and
  `domain/activity.py::ActivityFeed` (the chat) each word the same record kinds their own way, each with
  its own hold dedupe and arm labels, and the words already drift; announced arms are folded in five
  places. What varies is only verbosity and lifetime. Action: one narration table from record to `Said`
  with a lifetime: the readout log writes it as lines, the chat keeps the latest per lifetime, and the
  round tables render from fold state. **Re-test:** `git grep -c "AnnouncedArms" -- promptpotter`.
- **U3: one phase act is said three ways, and render text is banked on the ledger.**
  `run_records.py::PhaseRecord` carries a phase string, an untyped event string and a view whose kind
  repeats both; five folds switch on them. `InitEnterView` banks fields nothing reads and display text
  (a cut prompt preview, a short cycle id), and `view_models.py::ViewContext` is a mutable bag on
  `RunCallbacks` that a fork inherits. Action: the typed act is the record, phase and event derive from
  it, text is computed at render, and `ViewContext` goes. **Re-test:** `git grep -n "class ViewContext" -- promptpotter`.
- **S: spend is booked twice and folded many times.** `wallet_store.py::HeldCharge` and
  `wallet_store.py::BilledCharge` repeat `run_records.py::SpendHoldRecord` and `run_records.py::TokenUsageRecord`;
  `spend_book.py::SpendBook` counts through its ledger subscription and through admission.
  `spend_book.py::Admission` writes each home differently: a bill lands on the run's ledger, on the root's
  rewritten copy and on the account; a failed hold and a muted ledger reach the account only; a replay
  reaches the ledger only. One send reads about fifteen `ContextVar`s, and `jobs/coupon.py::keyed_client`
  rebuilds the client per call (it reads the key and grant files, re-folds the charges and hashes the
  key). Action: one charge value (hold id, key source, kind, model, provider, cost, rate, tokens, ledger,
  timestamp) that the ledger records embed; the account takes each send once, at the root, never a
  replay or a mirror; one fold parameterized by subject (the account, a campaign with its tombstones, a
  cycle's own lines) and meter; the sender (key, pacing, book, charges, reuse cache) built once at the
  account bind, on `RunScope`. `charges.jsonl` is a survivor: its migration maps each line one to one and
  keeps its timestamp. The ledger records' reshape wipes run data. **Re-test:**
  `git grep -n "class HeldCharge" -- promptpotter`.

**Batch 5 and after.**
- **U5: one viewed searchpoint.** Six hosts assemble a picked arm from several reads each, the Leader and
  the inspector show one pick at once, three shapes describe one program, and `SelectionContext.tsx::SelectionProvider`
  keys a pick beside the hash; the Leader shows the pick by default, so one pick renders twice. Action
  (operator, 2026-11-05): one searchpoint read and one rendering that hosts give identity only; the Leader
  always shows the leader; the pick lives in the hash and renders once, where
  `ScoringInspector.tsx::ScoringInspector` stands, a region A4 keeps. The webapp's selection half lands here, so
  every candidate component is rewritten once: `SelectionContext.tsx`, `useCandidatesModel`'s bag and the
  workspace's four-way address become the `Subject` and the served arm row. Compare channels and their
  picks are hash state too (`lib/state/compare-selection.ts` today; a reload drops both).
  **Re-test:** `git grep -n "searchpointCopyChoices(" -- webapp`.
- **D: the catch-up pool.** `validators.py::CatchUps` and `nodes.py::NoCatchUps` declare one protocol with
  one implementation, looped bench → race → bench. Action: the bench owns the pool and the race exposes
  only its priors. **Re-test:** `git grep -c "NoCatchUps" -- promptpotter`.
- **INVESTIGATE: each resume decision is spelled twice**, live and by a per-member replayer over untyped
  dicts (`potter/resume.py::_replay_round_winner`). Whether the live methods are pure over a seated round
  decides if the replay calls them. **Re-test:** `git grep -n "def _replay_round_winner" -- promptpotter`.
- **Two word tables over one enum, three times** (`evidence/head_to_head.py::GUARD_STATE_INFO` beside its labels,
  `paired_reading.py::READING_STATE_KIND` beside its info, `origin_panel.py::ROUND_ADVANCE_STALL` beside its
  info), and `library.py::LibrarySnippet` flattens a cited-or-authored snippet into nullable fields.
  Action: one table each; the snippet keeps its union. Rides with the arc that next owns each file.

### The quantum-leap pass (2026-11-05)

Batch E's reviews found one shape in every arc: a fact held or re-derived at many sites, kept
consistent by each site remembering a convention, so review finds one violation at a time and every
fix adds a site. A leap gives the fact one owner and derives every other site by construction, held
by an exhaustive table or state-machine test. Each is a pattern a mature project runs on. **Rides
with:** the Q rows of [`roadmap.md`](roadmap.md) § The crunch (operator, 2026-11-04).

**Decided once, read by every arc before a leap.**
- No arc before Q1 holds a command's state anywhere but the record `commands/dispatcher.py` writes.
- No arc before Q2 adds a cadence, a `revalidate` list or a cross-invalidation; a new read polls on
  the descriptor it has, and A4's conversation opens no second `EventSource`.
- No arc before Q3 adds a `logger.warning` in `application/`: a new fault is a record kind with a reader.
- Batch E closes EA's command states and EW's failed-read cadence at "no regression against main";
  Q1 and Q2 delete them.

- **Q1: one command log.** The fact: what was asked of the system and where it stands. Today it is
  split: `run_records.py::CommandRecord` and `run_records.py::CommandAckRecord` land on whichever
  ledger `commands/dispatcher.py::dispatch` picks (the cycle's, its root's for a delete, the draft's,
  the workspace's), a draft's records move with its ledger (`campaign_store/store.py::adopt_ledger`,
  from `jobs/mint.py::_mint_campaign`), a deleted cycle's go to the root's, the loop reads its
  standing commands off its own ledger (`runtime_flags.py::standing_controls`), the trail folds
  command lines out of every ledger and a tombstone's copies (`store/account_trail.py`), idempotency
  is a ledger scan (`commands/dispatcher.py::_find_idempotent_command`), a running act's status lives
  in MCP memory (EA), and whether the filer is alive is asked beside all of it. Sites:
  `git grep -l "CommandRecord\|CommandAckRecord\|standing_controls(" -- promptpotter`. Owner: one
  account-scoped append-only log; a command is one record stream in it (filed, applied, refused,
  voided, cancelled), each event written with its filing in one step, its status a fold of its own
  events, its subject the address it was filed on and the one it landed on (a draft's Start lands on
  the cycle the mint adopts, so the adopt step moves no record). The ledger keeps what HAPPENED
  (`RunPhaseRecord`, the effects); the log keeps what was ASKED. The pattern: event sourcing's
  per-aggregate stream with status a fold (EventStoreDB, Axon). Deletes: the two record kinds from
  the ledger, `ledger_scan.py`'s command fold, the trail's per-ledger walk and
  `run_records.py::TrailTombstoneRecord`'s copied commands, the idempotency scan, EA's index and
  `host_surface.py::_cancel_task`; a pass with no cycle (`launcher/admission.py::paid_pass`, `hop=None`)
  is addressed by its command id, so its stop is one command at every entry point. Shares with U2:
  the adopt step and U2's one launch answer, which is a log read. Edges inside: the loop's pending
  read becomes a by-subject read of the log, so the log keeps its own per-subject offsets, a fold,
  never a file beside it; a command filed by a process that died before applying stays "filed", and
  the fold reads the filer's OS lock (§0.5) to derive "filed, filer gone". Invariant tests: the
  exhaustive `(status, event)` table yields one status or a refusal for every pair; for any
  interleaving of file, apply, refuse, cancel and void with any ledger move or delete, the fold after
  the move equals the fold before. Amends [`../architecture.md`](../architecture.md) § Control-remote
  and [`../decisions.md`](../decisions.md) § 0001 in its commit: both place the record on the cycle's
  ledger. **Re-test:** `git grep -n "class CommandRecord" -- promptpotter/domain/run_records.py`.
- **Q2: one change feed — the server says what moved.** The fact: that a served subject moved. The
  webapp guesses it at many sites: a cadence per read (`cadence.ts`, read through
  `useRead.ts::useRead`'s `intervalMs`), a re-ask per write (`useCommand.ts::useCommand`'s
  `revalidate` and the `read-cache.ts::invalidateReads` calls beside it), the dashboard poll's phase
  re-ask and slow beat (`api/reads.ts::onViewedPhaseChange`, `poll.tsx::viewedIntervalMs`),
  `useEvidence.ts`'s and `AccountSpend.tsx`'s own re-asks, focus, visibility and online revalidation in
  `read-cache.ts`, and its failed-read cadence (`cadence.ts::RECONNECT_POLL_MS`); the
  server guesses beside it with mtimes and clock edges (`runtime_flags.py::run_state_stamps`) and one
  store-wide version (`application/hierarchy.py::entries_version`). Sites:
  `git grep -l "intervalMs\|invalidateReads(\|revalidate" -- webapp/lib webapp/components`. The server
  writes every ledger and runs every command, so it knows. Owner: a version per subject, the length
  of the ledgers the subject folds, from the one producer door (U3's `scope.ledger.append`), plus the
  moves no append makes (a process gone, a beat stale), published by the sweep that detects them. The
  door and `commands/dispatcher.py` publish `{subject, version}` on one account SSE stream
  (`routers/reads.py::_events` is the shape; `useCycleEvents.ts::useCycleEvents`
  folds in); the cache subscribes once and re-asks exactly the moved subject; a read's descriptor is
  `{id, load}` and nothing more; `routers/reads.py::_conditional`'s ETag is the same version. Which
  reads an append moved is the projections that consumed the record (U3), derived, never a hand
  table: before U3 that table is the convention site this leap removes, so it waits on U3. The
  pattern: server-pushed invalidation with the fetch as truth (Phoenix LiveView, Firestore listeners;
  the client half is SWR / React Query's `invalidateQueries`). Deletes: the read intervals,
  `intervalMs`, `invalidateReads`, `revalidate`, the phase re-ask, the slow beat, the focus and
  visibility hooks, the second channel, `run_state_stamps`' clock edges, the store-wide version. In
  U4, U5, W2, B and A4 nothing is added beyond a descriptor; U5's arm row and A4's conversation ride
  the one stream. One failure rule: the stream lost → reconnect, then re-ask every mounted read once.
  Edges inside: an event before, after or without the read it hints, or twice: harmless by
  construction, a hint only triggers a read, the body's version is what the cache keeps, a lower
  version never replaces a higher. Invariant tests: "hint, not truth" — for any sequence of events,
  reorderings, drops and duplicates, the cache's body equals the server's at the last read and never
  regresses in version; on the server, every record kind a projection consumes names a subject in a
  table derived from U3's projection roster, and every input `run_state_stamps` reads today is an
  append or a sweep publication. **Re-test:** `git grep -n "intervalMs" -- webapp/lib/hooks/useRead.ts`.
- **Q3: one fault channel — a fault is a value.** The fact: a step failed or was skipped, and why.
  Today it is a log line at many sites, each kept honest by remembering to serve it: engine decisions
  narrated into `logger.warning` (§ Config resolution item 10, moved here: `bench/resume_and_fork/repair.py`,
  `runner/origin_gate.py`, `runner/termination.py`, `escalation/firing.py`),
  `runner/origin_gate.py::run_origin_gate` logging a failed re-score and holding again,
  `origin_listing.py::list_origins` logging and skipping an origin whose dataset no longer resolves
  (so the list is silently short), `campaign_store/store.py::CampaignStore.bank_final` swallowing a
  failed export write under `shared/errors.py::graceful` (the export is the run's handed-on prompt).
  Sites: `git grep -c "logger\.\(warning\|error\|exception\)(" -- promptpotter/application`;
  `git grep -l "^import logging" -- promptpotter/application`. Owner: inside a run, a typed fault
  record through the one producer door, read by a projection (`run_records.py::RoundWarningKind` is the
  self-heal half already); inside a read, a field of the row it failed, served as its `Said`; `logging`
  leaves `application/` and `domain/`, banned at the gate as `gate.py::_IMPORTS_OPTIMIZER` bans an
  optimizer import. `infrastructure/`, `presentation/` and `main.py` keep theirs: a disk write that
  failed has no ledger to land on. The pattern: errors as values (Go, Rust's `Result`) and faults as
  data the next reader folds (Kubernetes `status.conditions`). Deletes: the logging imports and
  `graceful` in `application/`, the entries above. Edges inside: a fault kind nobody reads (U3's check
  fails a kind no projection reads); a row's fault field no surface renders (it is a `Said`, and W2
  renders every served `Said`). Invariant test: the gate lists every `except` in `application/` and
  `domain/` that neither raises nor hands its fault on (an append through the door, or a return whose
  type carries it), and the list is empty; every fault kind has one projection reader and one word
  row. **Re-test:** `git grep -l "^import logging" -- promptpotter/application`.

**Weighed and left.** Liveness (a verification orphaned at two seams, `started_at === null` in the
browser): `runtime_flags.py::derive_run_state` is the owner §0 already names, the sites are U3's and
W2's. The run lifecycle as one state machine: the command side is Q1's fold, the run side
`derive_run_state` and `phases.py::RUN_PHASE_INFO`'s admission table, one owner each. L4 measurement
identity hashed off rendered bytes instead of the AST reach (`optimizers/nodes.py`'s `source_digest`):
the static reach is §0.5 load-bearing and honest before any call runs; its over-reach is one entry
under § Typed state. Served words, typed records, the run scope, the subject, the read catalogue and
the typed file rows: W2, U3, U1a, EA and K3a each already are that leap. `??` defaults on served
facts: a lint over EW's one type door, not a mechanism.

### Config resolution and run scope (backbone hunt 2026-10-31, ranked by impact radius)

**Rides with:** one arc; 4 absorbs 7.

4. **Run scope is ~21 ambient `ContextVar`s bound piecemeal** — telemetry, send pacing, spend book,
   optimizer manifest, archive memory scope, coupon, measurement context, judges, spawn;
   `set_cycle_ledger` bound at 6 sites, `bind_memory_scope` sets without a reset token, one ledger with
   three doors (the var, `RunCallbacks.ledger`, `LaunchState.ledger`), an unbound memory scope defaulting
   to `OpenAt()` (the whole archive). Action: one frozen `RunScope` bound once at the run seam.
   **Re-test:** `git grep -c "ContextVar(" -- promptpotter`.
5. **`Session` / `LaunchState` is a staged builder modelled as a bag of Optionals** —
   `initialization/session.py`; ~62 guard or sentinel sites (`require_scorer()`, `launch_clock()`,
   `campaign_id=""`); `run_observers.declare_running` skips silently on a None ledger. Action: each init
   step returns the next typed stage. **Re-test:** `git grep -c "require_scorer\|require_partition" -- promptpotter`.
6. **Slot lifetime is a hand-rolled `except BaseException: release…` at ~20 sites** (admission,
   `run_job`, head-to-head, mint-and-start, check-in, embedded run). Action: the registry hands out a held
   slot as a context manager. **Re-test:** `git grep -c "release_slot(\|job_registry.release(" -- promptpotter`.
7. **Optimizer selection is ambient** — `optimizer_manifest.py` `_BOUND` (`bound_optimizer`, read by
   `loop_start.py`, `run_prep.py`, `run_observers.py`, `cycle_close.py`, `views/ingress.py`),
   `_DETERMINISM` and `_OPTIMIZER_PROMPT_OVERRIDES`, read by `bench/llm_call.py::_merged_config`,
   `potter/dispatch/layout.py` and `l1_wire_schema.py`; `_single_model` depends on node order. Action:
   selection rides `RunScope` (4), keyed by node. **Re-test:** `git grep -n "_BOUND.get()" -- promptpotter`.
8. **A root's typed refusal is served as one generic caveat** — `pipeline_resolve.py::campaign_runs_with`
   and `line_config` answer `None` for any `_ROOT_UNRESOLVED`, and `application/hierarchy.py::_UNRESOLVED`
   serves the same sentence whichever refusal it was. Action: the refusal rides the row, its message
   served. **Re-test:** read `application/hierarchy.py::_UNRESOLVED`.
9. **A minted cycle's dataset layer is read off today's definition** — `pipeline_resolve.py::_campaign_merge`
   lays the dataset's `node.config` and declared nodes from `dataset.yaml` as it stands; `ResolvedRun`
   records the laid declaration but not the layer the run sent. Action: run init records the dataset
   layer; a read lays the recorded one. **Re-test:** `git grep -n "runnable_definition" -- promptpotter/application/pipeline_resolve.py`.
10. Moved to § The quantum-leap pass (2026-11-05), Q3.

### The engine swept by shape (debt hunt 2026-11-02)

Grouped by the arc each would ride; workaround comments and dead top-level names came back clean.

**A. The pipeline carrier is typed.** **Rides with:** the next change to `pipeline_resolve.py::ParamLayers`.
- **`pipeline_params` is an untyped `dict[str, Any]`**: engine keys (`model`, `provider`,
  `route_order`, `max_tokens`, …) are string-read with mixed tolerance (`cfg["model"]`,
  `cfg.get("model")`, `str(cfg.get("model", ""))`); the reserved `steps` list shares the bag with the
  node dicts (`pipeline_overlay.py::node_config_items` skips it by `isinstance`, and `"steps"` is
  spelled again across connectors and domain, past `domain/CLAUDE.md`'s probe); the prompt is split
  off its config by `k != "prompt"` beside `search_point.py::strip_rendered_prompt`. Three merges
  (`campaign_config.py::merge_config_layers` / `merge_node_overlays`, `pipeline_overlay.py::apply_node_overlay`)
  fold one thing. An optimizer's llm nodes already read `search_point.py::NodeConfig`; the
  free tier's per-node bounds still land in `CampaignConfig.pipeline_overlay`
  (`application/free_tier.py::with_free_tier_layer`), read there by ~40 files, so they join `ParamLayers` as a
  layer of their own only with this sweep. Action: `PipelineParams(steps, nodes: {name: NodeConfig})`
  parsed once at the overlay seam, engine keys as fields, the backend's params one dict.
  **Re-test:** `git grep -c -E 'pipeline_params: (dict|Mapping)\[str, Any\]' -- promptpotter`;
  `git grep -n '"steps"\|k != "prompt"' -- promptpotter`.
- **`connectors/protocol.py::InProcessWorkload.experiment` is a `Mapping`** that
  `runner/inner/tasks.py::parse_resolved` re-parses at each reader (`dbllmbench.py` reads it with
  `.get("questions") or []`). Action: each connector declares its experiment model; the workload
  carries it parsed. **Re-test:** `git grep -n "parse_resolved(" -- promptpotter`.
- **`search_point.py::NodeConfig.prompt_edits` is a `dict[str, str]`** beside the
  `OptimizerPromptTemplate` that `pipeline_manifest.py::typed_config` validates the same edits as and
  drops; `node_prompt` lays the dict back with `model_copy`. The typed field cannot land in place:
  `opt_search_point.py` imports `search_point.py`, which holds the call-key rosters. And the template
  rides beside its config: `llm_call.py::run_optimizer_node` takes a filled `template` and an
  `LLMCallContext` whose `config` named it (`critique.py::distil_critique`,
  `firing.py::_run_transition`, `task_context.py::run_checkin`), so the two can disagree. Action:
  `PROMPT_FIELDS` and `OptimizerPromptTemplate` move to a leaf module both import, `prompt_edits`
  becomes a partial template, and the call context carries the running template its fill reads.
  **Re-test:** `git grep -n "prompt_edits: dict" -- promptpotter`; `git grep -n "template=filled" --
  promptpotter`.

**B. Run-data payloads are models.** **Rides with:** § Typed state "Run files read as dicts" and the
next run-data reshape.
- **`run_records.py::ResumeCheckpointRecord.inputs_ref` / `data` are `dict[str, Any]` on the ledger**,
  built by hand (`potter/race.py`, capo's `context`) and read back by string key with casts in each
  optimizer's replayer; a renamed key surfaces only at resume as `replay_error:`. Action: each
  `CheckpointKind` declares typed inputs/data. **Re-test:** `git grep -c -E 'inputs_ref\["' -- promptpotter`.
- **`RaceStandingRecord.paired_breakdown` is an untyped stat bag**: potter (`pobb/checks.py`) and capo
  write different keys, and `views/readout.py::on_race_standing` reads `.get("p_better", 1.0)` to sort
  and `.get("p_better", 0.0)` to print. Action: a typed per-prior model. **Re-test:** `git grep -n '"p_better"' -- promptpotter`.
- **`evidence/read.py` reads `campaign.json` as a tolerant dict** (`_dataset_of`, `_at`,
  `_resolve_head`), and `_dataset_name` accepts a `campaign_config.dataset_name` shape `Campaign` has no
  field for, while `subject_evidence` beside them loads `Campaign`. Action: `CampaignStore.load_campaign`.
  **Re-test:** `git grep -n "read_json_tolerant(CampaignLayout" -- promptpotter`.
- **The stale-data ladder's outcome has two owners**: `sample_measurement.py::execute_stale_data_protocol`
  returns a step string its one caller drops (`query_loop.py`, `recovered, _step =`), and stamps one of
  several exclusive bools on `MeasuredCell` that `render/sample.py` re-reads as an `elif` chain; the
  ladder is string-keyed. Action: one outcome enum on the cell, the ladder a `match`.
  **Re-test:** `git grep -n "recovered, _step" -- promptpotter`.

**C. Served reads fold the ledger.** **Rides with:** § What a cycle writes to disk (`FileSink`) and
§ One record shape for every scoring pass.
- **`hard_samples.json` is a checkout read back as served data**: `runner/output.py` writes it under
  `graceful`, and `scoring/measurement_log.py::_cycle_view` serves the leaderboard order from it, empty
  when absent (a failed write served as "no scale") and with no `at` (a replay page orders by today's
  file); the dataset scope derives its view instead (`_dataset_view`). Action: the cycle view derives
  from the fold. **Re-test:** `git grep -n "read_hard_samples(" -- promptpotter`.
- **A running verification is orphaned at two seams** (`live_dashboard/projection.py::_handle_run_wiring`,
  `served_dashboard.py::_serve`) and `orphaned=` computed a third way in `_serve`; `_round_axis` and
  `_walk` each re-decide whether `current_round` closed, by different predicates. Action: derive it
  once, from ledger plus producer lock. **Re-test:** `git grep -n 'm.state == "running"' -- promptpotter`.

**D. One LLM-call seam.** **Rides with:** § Config resolution and run scope #4 (`RunScope`) and #7.
- **The reuse cache arrives by two carriers**: `LLMCallContext.cache` for an optimizer call and
  `scoring/judges/call.py::bind_cache` (a `ContextVar`) for a grader; the protocol is
  `LLMReuseCache.answer`. Action: the cache rides `RunScope` (§ Config resolution 4).
  **Re-test:** `git grep -n "def bind_cache" -- promptpotter`.
- **`scoring/evaluators.py::Evaluator` is one class for two contracts** (`per_sample` async vs
  `per_round` sync, different kwargs) held apart by runtime guards (`_validate_evaluator`,
  `inspect.isawaitable`); its `Scope` collides with `campaign_config.Scope` and `data_root.Scope`.
  §0.5-bearing: state the case before acting. Action: two evaluator types.
  **Re-test:** `git grep -n "isawaitable\|iscoroutinefunction" -- promptpotter/application/scoring/evaluators.py`.

**E. Absent is not zero.** **Rides with:** any pass in the files named; the first item moves what the
loop decides, so it lands with an offline run.
- **Three bars for "is this θ lift real"**: `potter/escalation/state.py::_improved` reads
  `lift > current.se` (one arm's se), `potter/election.py::_lift` takes `hypot` of both arms', and
  `potter/dispatch/injections/panels.py::detectable_rows` takes `a.se * sqrt(2)` (the frontier's se
  for the parent's too). Both readings share cells, so none is the paired se. Action: one lift test on
  `AbilityReading`, read by all three, its se decided in arc D (the election's bound). It moves what
  the loop decides and shapes the optimizer prompt, so it lands with D's offline run and re-key.
  **Re-test:** `git grep -n -E "lift > current.se|sqrt\(2" -- promptpotter/application/optimizers/potter`.
- **Money text has no sub-cent rule, so two spellings stand beside `domain/spend.py::usd_text`**: a
  cell's price at five places (`preflight.py::_RoundShort.costing`,
  `cli/commands/head_to_head_price.py::_human`), which `usd_text`'s four would print as `$0.0000` for
  a cheap model, and `panels.py`'s optimizer-prompt spelling, which re-keys the digest when moved.
  `usd_text` and webapp `fmtUsd` are one rule and change together. **Open (operator):** the rule
  itself — fixed places pad a round figure (`$0.50` reads `$0.5000` today), so significant digits
  under a dollar is one candidate. Then both sites join `usd_text`; the panel rides the next accepted
  re-key. **Re-test:** `git grep -c -E '\$\{[^}]*:[,]?\.[0-9]f\}' -- promptpotter`.

**Singles.**
- **`infrastructure/identity/paths.py::DefaultClaim.renamed` is written and never read**: the marker
  `default_claim.py::maybe_claim_default` writes is read only for its `user_id`
  (`registered_user_id`). It is survivor data (its `store/data_root.py` row), so the delete is a landing migration that rewrites each marker without the
  field, run once and then deleted ([`../decisions.md`](../decisions.md) § 0011). **Rides with:** the
  next survivor-row reshape. **Re-test:** `git grep -n "\.renamed" -- promptpotter` finds no reader.

### The file layout (import-graph pass 2026-10-31)

**Rides with:** after the config/scope arc above; merges mostly cut duplicated import blocks (whole
tree ≈ −10% cross-file edges, ≈ −600 LOC), the LOC win is the root fixes inside them. Measure with an
import-graph script before and after; close each wave with `gate.py --only layering,help-imports,handler-tables,doc-claims,instruction-files`.

- **Wave A — presentation and infrastructure, no digest re-key:** CLI collapse
  (`cli/campaign_runner.py` + `parsers.py` + `commands/verbs.py` → `cli/main.py`; small read verbs →
  `commands/reads.py`, measure verbs → `commands/measure.py`; `handler-tables` names
  `campaign_runner._COMMANDS_PACKAGE`); `routers/datasets/` → one module; `infrastructure/identity/` 12 →
  3 (`oidc`, `grants`, `accounts`); `tracing/` 7 → 4 with `TracingProjection` building its own sinks
  (drops `Session.langfuse`, a second door into tracing); `projections/live_dashboard/` → one module;
  LLM wire types into `llm/base.py`.
- **Wave B — the application backbone:** `application/runner/` 18 → 7 files (loop, round, output,
  entry, bench, mint, inner/spawn) deleting the ledger-scan forwarders (`_spent`, `read_line_spend`,
  `fresh_campaign_id`); loose top-level modules regrouped (cycle reads, hierarchy, origin + `loop_start`);
  the optimizer registry (`optimizers/__init__.py`'s 146 lines), `knobs.py`, `run_callbacks.py` into
  `optimizers/nodes.py` — breaks the hidden 16-module cycle (`TYPE_CHECKING` / in-function imports hide 8 cycles).
- **Wave C — the optimizers, one re-key batch:** `potter/l1/` → `l1.py`, `validators/` 7 → 2, peer
  optimizers' `prompts` + `state` into `members.py`, `pobb/` and `escalation/` flattened, and
  `runner/inner/tasks.InnerCells` moved into `domain/l4` (optimizer code imports a runner internal).
- **Rejected:** a concept-first vertical re-cut (§0's "One rejected re-cut").

### The webapp's vanilla pass (audit 2026-10-29)

- **Wording authored in the browser**: `lib/prompt-fields.ts::PROMPT_FIELD` still labels a served
  set client-side (arc U1b's file). `sample-trajectory.ts::MOVEMENT_CLASS` maps the served
  `SampleMovement` to an ink class, and `PipelineFlow`'s `tone: "accent" | "neutral"` is chosen by each
  host: both are ink with no words, so they wait until a served word row carries its `Tone`
  (`domain/said.py`) and do not ride the Worded re-key. `shared/clock.py::duration_words` twins
  `format.ts::fmtDuration` because the browser formats clocks that tick between polls; one served
  formatter would need a served tick. **Re-test:** `git grep -n "PROMPT_FIELD\|MOVEMENT_CLASS" -- webapp/lib`.
- **The seed-once latch** hand-built at each
  `git grep -n -i "seeded" -- webapp/components webapp/lib` site (one hook is the fix, and
  `lib/hooks/` is arc W1's); `STORAGE_KEYS` mixes hook and
  non-hook keys and `side-panel:*` has no `promptpotter.` prefix.
  **Re-test:** one latch.
- **Modules still off the `lib/CLAUDE.md` § Where a module lives rule**, blocked by the run-card
  session's files and the ingest rebuild, which import them: to `state/` — `hard-samples`, `lineage`,
  `poll`, `workspace`, `ingest-flow`, `scoring-mask`, `chat/thread`, `SelectionContext` (as
  `selection.tsx`), `hooks/useConnector`'s provider; to `derivations/` — `cell-means`,
  `prompt-fields`.
  Chrome still holding a surface: `shell/searchpoint/`, `shell/scoring/` (ingest imports it),
  `shell/node-surface/`, root `PairedLift` / `SummaryBlock`; a surface holding chrome:
  `dashboard/pipeline/` (`git grep -n "dashboard/pipeline" -- webapp/components/ingest`). Hard samples and compare each spread over
  several folders (`git ls-files webapp/components | grep -i -E "hard-?sample|compare"`).
  **Re-test:** `git ls-files ':(glob)webapp/lib/*.tsx'` lists none.
- **Adapters over a flat served shape**: `useCandidatesModel.ts` joins every source it imports;
  `.facts.` opened at `git grep -c "\.facts\." -- webapp/components webapp/lib`; liveness
  re-derived at each `git grep -n "phaseWalks(" -- webapp/components` site.
- **A view family that is chrome**: `components/shell/families.tsx::FAMILIES` holds a family with a
  null pane (the phone's campaign list is the sidebar), so naming it on a wide screen opens a chip
  that draws nothing. **Re-test:** every family has a pane.
- **The `walk` tier rots unseen**: the gate runs only `cold`, so a walk assertion outlives the control
  it names. The world is built: `PP_E2E_WALK_WORLD=<offline-run>/potter` serves a copy through
  `e2e/walk_world.py`, and the host it walks as is exempt from the read bucket
  (`quota.py::refuse_request_rate`). Action: the gate's `playwright` check builds that world and adds
  `--project=walk` on it. **Re-test:** `gate.py` lists a walk check.
- **The Vite build leaves five things standing**:
  - The derivations barrel pulls the network layer into every chunk that imports it.
  - Babel (React Compiler) is 87% of build time.
  - Chunks exceed 500 kB.
  - ESLint rules dropped with `eslint-config-next`: `react/jsx-key`, `react/no-unescaped-entities`, `react/display-name`, `react/jsx-no-target-blank`, `@next/next/*`.
  - `headers.py::_WEBAPP_CSP` `style-src 'self'` may now hold, with no inline style from the framework: check the console first.
- **minimatch alert #171** (dev-scope, lint-time only): no eslint / `@eslint/eslintrc` / jsx-a11y /
  typescript-estree release takes minimatch >= 11.0.1. **Rides with:** the first eslint / ts-eslint / jsx-a11y
  release on minimatch >= 11.
- **Several doors into one state**: candidate selection minted at each
  `git grep -n "selectedCandidateOf(" -- webapp` site, the sample set written through
  `setSelectionForSampleSet` from several surfaces, `FileTree::onSelect` drilled through its parents.

### The phone pass (2026-10-31)

The launch verdict (`quota.py::launch_refusal`) landed. The rest of the 390px pass is open: re-walk the phone screens and audit the surfaces against
`webapp/CLAUDE.md` and `lib/CLAUDE.md`. Shapes the first pass kept finding, each still present
somewhere:

- a client word table or literal beside a served enum, or a served sentence where an enum belongs;
- a second door or control into one state, and client-minted state beside a served one;
- a refusal spoken only after the tap, where a served verdict could gate the control;
- `??` / `||` defaults on facts the server always sends;
- a served field whose last reader was deleted;
- a whole-object replace where a per-key merge is meant;
- two placement or layout engines for one job, and fixed geometry beside wrapping text;
- chrome that stays pinned on a phone, and figure cells that truncate sentences;
- a hand-copied fixture.

The frame, the run card, origin pairing, pipeline CSS and money format wait on the run-card session's
files.

### The candidates surface (review 2026-11-01)

Seen while removing the canvas tooltip; the panel work is [`roadmap.md`](roadmap.md) § Arrangeable workspace.

- `useCandidatesModel` writes the module store during render (the per-cycle metric and overlap seeding).
- Two sources for one question: `wonOnTheta` reads `facts.elects_on`, `electedMetric` reads `dash.display_metric`.
- `FitnessChart::parentIdx` names the standing selection, not a parent.
- Three interval formatters: `format.ts::fmtInterval`, `VerifyReading::band`, `SearchpointDrillIn::band`.
- Two `cell_means` renderers: `SubjectSignals` and `channel-parts::CellMeans`.
- Three ways into Compare (the toolbar menu, `CompareOriginAction`, the forest).
- Teaching prose in `title=`: the `FitnessLegend` hint, the toolbar, the dendrogram `<title>`, `CompareOriginAction`, `RoundVerdict`.
- `TrajectorySeriesView::SeriesHoverPopup` is a hand-built popover beside `ui/Popover`.
- `.ov-forest` is styled in `compare-evidence.css` for `OuterSignalPanel`, and collides with `candidates/Forest`.
- `EvidenceCharts` and `CostStrip` lost their hover readouts with the tooltip; a per-cell or per-round value they need opens in the DOM.

### The webapp swept by shape (debt hunt 2026-11-02)

Grouped by the arc each would ride. Came back clean: dialogs, clipboard, `EventSource`, `<table>`,
component `fetch(`, `setInterval`, `useEffect` resets, client sorts, unfiled `Record<…, string>` tables,
fixtures.

**A. One arm shape, served.**
- **A live arm and a filed arm arrive in two shapes** (`LiveCandidate` flat; `RoundView.arms[key].individual`),
  so derivations come in pairs (`candidateSearchPoint` / `liveCandidateSearchPoint`, `…Verdicts`,
  `…ObserveConfig`), `ArmRow.source: "history" | "inflight"` branches in `MeasurementRun` and
  `SteerForkPanel`, and `round-candidates.ts::roundCandidates` stitches `dash.rounds` with
  `current_round.candidates`. "Is round N filed" is asked three ways (`useRound.ts::isRoundUnfiled` by
  membership in `dash.rounds`; `roundCandidates` and `search-point.ts::roundObserveTarget` by
  `round_axis.completed`). Action: one served arm shape carrying a filed/live state.
  **Re-test:** `git grep -nE "liveCandidate(SearchPoint|Verdicts|ObserveConfig)|round_axis.completed.includes" -- webapp/lib webapp/components`.

**B. Served words own every wording.**
- **Glyph tables over served enums**: `forest-layout.ts::KIND_GLYPH` / `TRIGGER_GLYPH` /
  `DIRECTION_GLYPH`, `Criterion.tsx::TERM_GLYPHS`, `FitnessRankSummary::MOVE_MARK`, a literal "⑂" in
  `ForestRows`. Low weight. Kept out of the Worded re-key because several of the keyed enums (the
  forest's kinds and triggers) are not word tables yet. Action: the glyph rides the served word rows.
  **Re-test:** `git grep -nE "_GLYPHS?\b|MOVE_MARK" -- webapp/lib webapp/components`.
- **"Measured in N/M rounds" is counted in the browser** (`sample-set.ts::roundsCoveringSample`, read by
  `SampleSetControl`), and `fullyCovered` counts rounds with any selection. This is a count, not a
  word, so a served coverage field is a dashboard reshape outside the wording arc. Action: served
  per-sample coverage. **Re-test:** `git grep -n "roundsCoveringSample\|fullyCovered" -- webapp`.

**C. Lineage reads the served tree.**
- **A campaign's short label is parsed three ways**: `format.ts::shortId` (`{dataset}__{rand6}`),
  `ForestRows::shortOrigin` (`cycle_`), `ids.ts::shortFamilyTail`, used by `ComparePane::channelNames`,
  `EvidenceCharts::seriesLabel`, `SearchpointPanels`. Action: a served short label per subject and node.
  **Re-test:** `git grep -nE "shortId\(|shortOrigin\(|shortFamilyTail\(" -- webapp/components webapp/lib`.
- **`ids.ts::rootCycleId` regexes `_fork_` / `_diag_`** to give `useLineage::parentId` /
  `isInheritedSibling` what the served tree carries. Action: read parent and spawn off the tree.
  **Re-test:** `git grep -n "rootCycleId(" -- webapp/lib webapp/components`.
- **`SearchpointPanels.tsx::buildSpines` walks `parent_ids[0]`** beside the served `main_line`
  (`lineage-candidates.ts::mainLineOf` says never walk up). Action: spines from `main_line`.
  **Re-test:** `git grep -n "parent_ids\[0\]" -- webapp/components`.
- **`hooks/useLeafDatasetName.ts` reads a campaign pipeline for a `dataset_name`** the viewed subtree's
  entry already tags (`application/hierarchy.py::_campaign_tags`). Action: read the tag; delete the hook.
  **Re-test:** `git grep -n "useLeafDatasetName" -- webapp`.

**D. One selection and view store.** Extends § The webapp's vanilla pass "Several doors".
- **The picked candidate lives in two stores**, `workspace.tsx::viewedCandidateId` and
  `SelectionContext.candidate`: `useSelectNode.pick` writes both, `MeasurementRun.onSelectCandidate`,
  `ForestCard`'s ctx and `TimeRay` only the second. Action: one owner.
  **Re-test:** `git grep -nE "viewedCandidateId|setCandidate\(" -- webapp/lib webapp/components`.
- **`showForest` / `expandedLanes` sit in `state/candidates.ts` and `view-memory.tsx::CampaignView`**,
  written together by `hooks/useLineage.ts` and seeded during render by a latch; the hook's name
  collides with `lib/lineage.tsx`. Action: view memory alone owns them; rename the hook.
  **Re-test:** `git grep -nE "showForest|expandedLanes" -- webapp/lib`.
- **`workspace.tsx::WorkspaceState` exposes the viewed address four ways**: `viewedPath`, the root as
  unqualified `campaignId` / `cycleId`, `leafCampaignId` / `leafCycleId`, and callers running
  `pathRoot` / `pathLeaf(viewedPath)`. Action: one address value with named root and leaf.
  **Re-test:** `git grep -nE "leafCampaignId|leafCycleId|path(Root|Leaf)\(viewedPath" -- webapp/components webapp/lib`.

**E. Read discipline.**
- **`hooks/useNestedPipelines.ts` walks the nest client-side** with its own `MAX_DEPTH` and self-nest
  test, worded into `truncated`. Action: the pipeline read serves the whole nest and its depth.
  **Blocked by** K1 (`application/pipeline_resolve.py`). **Re-test:** `git grep -n "MAX_DEPTH" -- webapp/lib/hooks`.

**F. The node editor reads served row facts.**
- **Key-name tests stand in for row facts** (I9): `NodeSurface::onAuthor`, `node-config.ts::effortLadder` /
  `pickedRoute` / `permittedModels`, `NodeConfigEditor::axisMenu`, `pipeline-nodes.ts` test
  `reasoning_effort`, `provider`, `model`, `response_format`, `schema_family`. Action: the param schema
  serves each row's role. **Re-test:** `git grep -nE '\.key (===|!==) "(reasoning_effort|provider|model|response_format|schema_family)"' -- webapp/lib webapp/components`.
- **Editability is decided client-side**: `SteerForkPanel::canBabysit` tests
  `me.capabilities.includes("campaign.babysit")`; `permittedModels` re-walks what `configRows` reads;
  `NodeConfigEditor`'s `schema` is a source prop two hosts fill from different reads. Action: served
  per-row editability for the account; one schema source.
  **Re-test:** `git grep -nE "campaign.babysit|permittedModels\(" -- webapp`.
- **`JobsDock::passMark` / `passLabel` derive running/starting from `started_at === null`** (so does
  `AccountUsageTab`). Action: a served pass state. **Re-test:** `git grep -n "started_at === null" -- webapp/components`.
- Hand-built controls beside the primitives: `Criterion::DialRow` commits on pointerup/keyup/blur beside
  `CommitInput`; raw `<select>` in several surfaces with no `ui/` select; `RunLimitsControl::capInput`
  and `LimitReconcile::parseCapIn` beside `run-limits.ts::parseCap`. Low weight.
  **Re-test:** `git grep -nE "<select|parseCapIn|capInput" -- webapp/components`.

**G. Dead and ratchets.**
- **Teaching prose in `title=` across many surfaces** beyond § The candidates surface's list. Action: a
  gate ratchet. **Re-test:** `git grep -cE 'title=("[^"]{60,}|\{\`[^\`]{60,})' -- webapp/components`.
- Minor: `CandidateRow` re-derives "retired" after `splitRetired`; `reads.ts::filesRead` builds the
  cycle URL beside `cyclePathUrl`; `useActivePointer` uses `|| null` on ids.

### What a cycle writes to disk (storage survey 2026-11-01)

- **`tracing/file_sink.py::FileSink`, the local Langfuse shadow, has no reader and runs on every
  cycle**, also with no Langfuse configured and in L4 inner cycles. It writes most of a run home's
  files: one file per panel row per cycle (`langfuse/datasets/ground_truth/*.json`, a copy of
  `banks/{hash}.json`), one per trace, one per observation (written at open and again at close), and
  a scores `.jsonl` + `.lock` per trace. Action: delete it and its `data_root.py::_cycle_rows`
  langfuse rows, keeping `state.json` (`LangfuseSink`'s id map). Langfuse and MLflow keep their
  native formats, and a local Langfuse-format copy is rendered from the ledger on demand.
  **Rides with:** the next on-disk reshape (§ One record shape for every scoring pass).
  **Re-test:** `git grep -n "FileSink(" -- promptpotter`; `find <offline home> -path '*langfuse*' -type f | wc -l`.
- **A bank mint keeps exact repeated rows**: `logic-proofwriter` (`banks/1d4ed70f….json`) repeats
  whole `(query, ground_truth)` rows, a query up to 3×. Each repeat has the same
  `domain/sample.py::sample_key`, so a panel can draw one sample twice. Action: the mint keeps one
  row per `sample_key`, so the content address names distinct rows. **Rides with:** the next
  dataset ingest. **Re-test:** count rows against distinct `(query, ground_truth)` pairs in that bank.
- **An archived answer's outputs are read off today's backend**: `scoring/measurement_log.py::open_cell`
  reads the live declaration to build `outputs_of`, because an `ArchiveEntry` records only
  `node_configs` and `PipelineData.wire()` is flat, with no per-node attribution. Action: a per-node
  output-key map filed with the answer; existing rows name no campaign to join, so a backfill reads
  today's declaration per dataset hash. Operator decides. **Rides with:** the next archive reshape.
  **Re-test:** `git grep -n "dataset_pipeline(" -- promptpotter/application/scoring/measurement_log.py`.

### On the box (2026-11-01)

- Unexplained: in `justlogic.d234__e85387` / `cycle_f1591f054ad0`, R1's three candidates and R2's two
  carry no score in `dashboard.json`. Read the cycle's ledger for their arms before calling it a bug.

- One email holds two accounts on the box: the claimed host (`identity/default_claimed.json`) and the
  Google sign-in that owns every webapp campaign. The host's coupon exemption and the CLI's identity
  read the first, the webapp the second (`default_claim.py::_is_host` compares a user id, never the
  declared `HOST_ADMIN_EMAIL`).
- No verb sets an account's coupon or starts it afresh: `grant.json` is hand-written, and a reset is
  moving `charges.jsonl` aside, which also drops the account's bill. A grant that counts charges from
  its own `granted_at` would make a re-grant the reset; today `wallet_store.py::CouponGrant.granted_at`
  is written and never read.
- **The box holds the app's environment twice**: the services load `/etc/promptpotter.env`
  (`ENV_FILE`, root `0600`), and the login user's CLI reads the checkout's `.env` through
  `config/paths.py::env_file_path`. The two had drifted by 2026-11-03: `ALLOWED_ORIGINS` and the
  admin bot's token differed, and some keys were in only one file, so a CLI verb on the box read
  other keys than the app it inspected. A re-issued token pasted into the checkout file reached no
  service; `channel:telegram` caught it at deploy. One file must serve both. Either the CLI reads
  `ENV_FILE`, which needs read access for the login user, or the services read the checkout file,
  which SELinux denies under `$HOME`. That choice is the operator's.
- **A site-record file's mode is the writing process's umask**: `store/site_records.py::SiteRecordStore.keep`
  appends through `store/io.py::append_jsonl`, which sets no mode. On the box the admin bot wrote
  `app_visit_digests.jsonl` 0644 beside the API's 0600 tables. The store must create its files private
  itself, whichever service writes first.
- **A landing proves an install-shadowed manifest by its shape, not its reader**: `data_root.py`'s
  "optimizer manifest" and "check-in manifest" rows type the YAML (call keys included), but a knob or
  roster fault in an optimizer shadow, or a parse fault in the check-in (`optimizer_manifest.py::_parse`),
  passes `land-data --dry-run` and stops only the API boot (`wiring.py::complete_registries`), after the
  data root has landed: `update.sh` then reports the services down and rolls nothing back. The readers
  live in `application/`, which `infrastructure/store/` cannot import. Action: the landing proves each
  shadow present through its own reader, refused with its path as a survivor that does not load.
  **Re-test:** a shadow `checkin/pipeline.yaml` with `optimizer.param_keys: 5` under `land-data --dry-run`.
- **The first visits digest told a window nothing recorded**: `app_visits.py::due_digest` reads the last
  24 h whenever no digest is kept for today, so the deploy that began recording sent "last 24 h … Nobody"
  at 22:53. The window must start where recording did, which no record holds yet.
- **Left by the payer rule (2026-11-02)**:
  - `quota.py::sends_on` raises `NoApiKeyError` for a provider no key pays, so `change-run-limits`
    (`clamp_budget_change`) refuses a limit over a missing key. Action: `sends_on` is total (a no-key
    send's payer is `None`); `launch_refusal` refuses `NO_KEY`, and `coupon.py::keyed_client` is the one
    raise at send (today `provider_send` spells it again). **Re-test:** `git grep -n "NoApiKeyError(" -- promptpotter/application/jobs`.
  - `CouponStatus.state` / `.held` / `.left` and `LaunchRefusal.provider` are served with no reader.
    Action: delete. **Re-test:** grep each in `webapp/components webapp/lib` and `promptpotter/presentation`.
  - The in-run allowance refusal (`spend_book.py`'s `HostAllowanceExhaustedError`) names no provider
    though `admitted(…, provider=…)` carries one. Action: pass it.
  - **An in-process connector whose cells send through `keyed_client` declares a fixed payer**: the L4
    `promptpotter` connector keeps `Connector.cell_payer = "host"`, so an own-key L4 run is refused on a
    used-up allowance. Action: an in-process connector's cells are per-provider sends (the target
    nodes' providers, read through `pipeline_resolve.py::served_pipeline`), read by `sends_on`, the
    cell reservation and `application/free_tier.py::free_fence`. **Rides with:** the in-process `llm_only` connector
    (uploads that run without a backend, one node implementation shared with TermNorm's
    `_step_llm_only`), which needs the same shape and is what lets a BYOK account run an upload.

### One record shape for every scoring pass (2026-11-01, two independent reads agree)

**Rule: the ledger writes a pass the same way whatever verb sent it**; the verb and the row set are
fields. The per-cell records already hold it (`SampleScoredRecord` + `role`, for `RECORDED_ROLES`). The
records ABOUT a pass do not: origin, measure (`MeasureEnterView`), backfill (`RaceCatchUpRecord`),
overlap (`RoundCells.overlap`), repair (`RoundRestatedRecord`), verify (`VerifyEnterView`,
`VerifyGradedView`) and bench (`BenchEnterView`, `BenchGradedView`, `BenchScoredView`) each have their
own family, and `noise-floor` and `seed-screen` write nothing to the ledger.

**First arc: verify and the bench grade**, one act (an individual scored outside a round) built twice.
- Essential and kept: the row population (REPORT vs BENCH scope, never paired, `SCOPE_ROLES`), the
  subject (a candidate at a cycle vs the line), the comparator (fresh vs recorded vs origin-paired with
  `LiftCost`), the fold scope (own ledger vs the line), the bench's reserve and `reads_before`.
- Accidental and merged: `VerifyPass`/`BenchPass` (same facts, other names), the views, the phase, the
  fold (`ledger_scan.py::_Verify` vs `_Bench`), the renderers, the stop banking (only the bench banks a
  `PassStop`; a failed verify is inferred from a bare exit), the served can-run answer (only
  `BenchStatus.can_grade`), where `paid_pass` is taken, the spend bucket and source stamp, where the
  reading is joined, the chat bracket, the embedded entry (bench only).
- A skipped automatic verify (§ URGENT) lands as a refusal banked on the one pass.
- Target: one `VerificationPass` keyed by `VerificationKind`, one enter view and one graded view, one
  phase, one fold keyed `(kind, arm.key)`, a served can-run status for both. **Open (operator): one verb
  with a row choice, or two verbs over the one pass** — the comparator and subject differ, so the two
  readings stay either way.
- Names: `verify` reads as a correctness check; `bench` collides with `application/bench/` (the
  optimizer runtime); `bench` / `grade-bench` / "Grade on bench" disagree. "held-out" is the clear word.
- Ledger shape changes, so run data is wiped (after the 11-02 demo); archive and survivor rows untouched.

### Typed state — what the 2026-10-30 arc left (served words → enums + one exported table)

- **`domain/cycle_index.py::RunStatus.of` builds `mark: str` and asserts it a `StatusMark`**, so the
  literal holds at runtime only. Action: `StatusMark` typed off `RunPhase` and `StopOutcome`, whose
  values it repeats (a re-key of the exported union, so not one edit). **Re-test:** `git grep -n "mark: str" -- promptpotter/domain/cycle_index.py`.
- **`application/config_map.py::ConfigKnob.source` is an untyped `str`**, and its only reader is
  `RowHoverCard`'s declared-knobs list. Action: a `KnobSource` literal. **Re-test:** `git grep -n "source: str" -- promptpotter/application/config_map.py`.
- **The pipeline renderer's styles live in `app/styles/domains/chat.css`** (`.wf-hero-*`,
  `.connector*`, `.pipeline-zoom*`), restated by `dashboard-pipeline-nodes.css` to win on order. The
  renderer is `dashboard/pipeline/PipelineFlow`'s. Action: move the block to its own domain file in
  one commit once the run-card session's `chat.css` hunk lands. **Re-test:** `git grep -c "wf-hero" -- webapp/app/styles/domains/chat.css`.
- **`search-point.ts::OBSERVE_LABELS` is a TS-only closed set** (`ObserveState`): a UI mode, not a
  served state — admitted until a surface beside the browser names it. **Re-test:** still TS-only.
- **A landing waits on every live producer on another commit, whether or not the ledger shape
  moved** (`jobs/registry.py::JobRegistry.close_for_landing`), so a busy box may never land. Action:
  key it on a digest of the `CycleRecord` schema the code writes, not the commit. **Re-test:** a
  landing that moves no record shape refuses while a run is live.
- **Run files read as dicts outside `store/` have no shape row**: `data_root.py::KINDS` proves
  `rounds/round_*.json`, the `diagnostics/` sidecar, `rates.json` (`llm/pricing.py`), the langfuse
  mirror (`tracing/langfuse_sink.py`) and both model-capability files (`llm/capabilities.py`) by
  presence alone, because their readers walk `dict`s. A reshape there passes `land-data` and is read
  around. Action: each reader takes its model through `store/read_model.py::read_file`, its row that
  model's `TypeAdapter`. **Rides with:** § The engine swept by shape B for `evidence/read.py` (its
  `campaign.json` reads, already an entry there), D for the three `llm/` and `tracing/` readers.
  **Re-test:** `git grep -n "Fate.RUN),$" -- promptpotter/infrastructure/store/data_root.py`.
- **`draft_campaign.py::DraftCampaign` is a dataclass twin of `domain/checkin_draft.py::DraftFile`**
  plus its address, held equal by an import-time assert. Action: one frozen model that `draft.json`
  holds, the address beside it. **Re-test:** `git grep -n "class DraftCampaign" -- promptpotter`.
- **`cycle_addressing.py::ActiveSessionResponse` serves two nullable ids for `CycleHop | None`**, so
  the webapp re-tests a pair. Action: one nullable `hop`. **Rides with:** the next webapp pass.
  **Re-test:** `git grep -n "class ActiveSessionResponse" -A4 -- promptpotter`.
- **A ledger subscriber that raises after `Admission._write` lands a charge leaves it in `_unlanded`**
  (`llm/spend_book.py`), so the book counts that part twice until the run ends — conservative, never
  under. Action: the record lands before any subscriber runs. **Re-test:** the outlives journey in
  `tests/test_spend.py` with a raising projection.
- **Writers with no reader**: `dashboard.json` and `index.json` (checkouts nothing reads back), and
  `identity/blocklist_audit.jsonl` and `grants_audit.jsonl`. Action: decide whether each earns a
  reader or goes. **Re-test:** `git grep -n "blocklist_audit" -- promptpotter`.
- **One act on an arm writes two records**: `runner/arm_gate.py` and `scoring/query_loop.py` append
  an `InterventionRecord` and a `LeftComparisonRecord` for one act. Action: one act record both
  reads derive from. **Re-test:** `git grep -n "LeftComparisonRecord(" -- promptpotter`.
- **A deleted fork takes its `LeftComparisonRecord` with it**, so its arm reads `controlled` again
  while the fork's spend stays banked. Action: the exit outlives the fork. **Re-test:** delete a
  fork off an arm, then read the arm's `evidence`.
- **An unparseable job file is logged and skipped** (`jobs/registry.py::JobRegistry.list_all`), so
  occupancy undercounts a live slot; a parseable one that fails validation raises
  `FileUnreadableError` (`FileKind.JOB`). Action: one refusal for both. **Re-test:** `git grep -n "logger" -- promptpotter/application/jobs/registry.py`.
- **Potter's and capo-pobb's digests hash the review.md and log.md writers' words**: their members
  reach `generation_only.py::run_generation_only_round` → `round.py::close_round` →
  `output.py::write_review_md` / `write_log_md`, so `READING_STATE_INFO`, `STOP_REASON_INFO`,
  `HEALTH_CAUSE_INFO`, `DISPLAY_METRIC_INFO`, `UNIT_FORMATS` and the `NoApiKeyError` /
  `HostAllowanceExhaustedError` sentences (`campaign_result.py::read_cycle_bench` → the judges →
  `coupon.py::keyed_client`) key both treatments while no request byte moves. Action: the closure stops
  at the round's close, a writer of files no prompt reads. **Rides with:** the next L4 digest pass.
  **Re-test:** a reach trace over `source_scan.py::optimizer_prompt_shapers` for potter still ends a
  `READING_STATE_INFO` chain at `generation_only.py::run_generation_only_round`.

### The member backbone

- **The dev loop still serializes on Vitest:** its pool fails a worker that takes past its hard-coded 90 s to start (`WORKER_START_TIMEOUT`),
  which a host loaded from outside the gate lock (a builder's bare `pytest` or `vitest`) still trips,
  though the gate runs it `alone` on one unisolated worker per project. **Re-test:** five builders
  plus one full gate with no reap and no gate waiting longer than its own run.
- **Four `--staged` checks queue only because their regenerators always write**: `ts-types`,
  `vendor-marks`, `optimizer-schemas` and `specs` rewrite their tracked output on every run, even
  when it is unchanged, so they cannot be `gate.py::Check.light`. Two more keep a commit queued:
  eslint's `--cache` write and tsc's typegen. `ts-types`, `optimizer-schemas` and `specs` read
  `!webapp/`, so nearly every commit selects one. Action: each regenerator writes only when the
  content differs, via atomic `os.replace`; mark the four light. **Re-test:** a `--staged` gate
  selecting them starts while a sibling gate runs.
- **Two gates can still lose each other's greens**: `last_green.py::LastGreen.record` re-loads
  `greens.json` just before an atomic replace, but nothing locks the load-to-replace span, so of two
  gates finishing together the later drops the earlier's greens (a rerun, never a false green).
  Action: hold an exclusive lock across load, merge and replace. **Re-test:** two concurrent
  `record` calls on distinct checks both persist.
- **`initialization/session.py::LaunchState.ledger` is `Optional` while a round needs it**:
  `run_observers.py::build_run_observers` sets it after the state is built, so `runner/round.py`
  asserts presence and five other readers branch on `None`. Action: the run's state is built with its
  ledger. **Re-test:** `git grep -n "state.ledger" -- promptpotter/application`.
- **`run_records.py::RoundEnteredRecord.rewound` is a flag keeping two acts apart** — entering a round
  and displacing what stood past it — that `ledger_scan.py::_Standing.feed` splits again. Action:
  two records, or one whose displacement is its own field. **Rides with:** the `arc/records` arc.
  **Re-test:** `git grep -n "rewound" -- promptpotter/domain/run_records.py`.

### The origin gate (batch-2 reviews, 2026-11-05)

**Rides with:** the next pass through `bench/resume_and_fork/repair.py` or `runner/origin_gate.py`.

- **A fork cut at round 0 keeps its origin's holes**: `bench/cycle.py::Cycle.reopen_origin` leaves
  round 0 with no close, so `resume_and_fork/repair.py::rewalk_lost_origin` returns at once and
  `round.py::emit_origin_round` closes it on the dead cells; under the strict gate the fork halts
  again with the backend fixed (one extra launch). Action: what re-walks an inherited origin is
  whether its rows were measured by another launch, not whether round 0 closed. **Re-test:**
  `--fork-on-divergence` at round 0 of a gate-halted campaign, backend fixed: the fork passes on
  health in one launch.
- **The first launch pays a still-refusing backend twice**: `application/origin.py::establish_campaign_origin`
  re-scores after a transient abort, sending each refused cell again (3 cells, 10 attempts each in
  the offline `gated/` leg). Action: one walk per launch, as a resume already has. **Re-test:** the
  `gated/` leg's first launch sends each cell once.
- **The bench pass runs on a launch the origin gate halts**, sending its cells to a dead backend
  (10 in `gated/`). Action: the gate decides before the bench spends. **Re-test:** the `gated/`
  leg's halted launches send no bench cell.
- **`capo/members.py::CapoInit.refuse_origin` reads a schema fact ("no prompt node") off answered
  rows**, so it needs a guard for an origin nothing answered. Action: refuse on the pipeline
  schema at selection. **Re-test:** `git grep -n "refuse_origin" -- promptpotter/application/optimizers/capo/members.py`.
- **`tests/test_archive.py::test_a_resume_takes_the_origin_the_gate_halted_past_it_and_every_later_launch_reads_the_pass`
  walks zero cells**: `run_round_loop(cycle, [], …)` re-walks the dead origin over no dataset and
  closes an empty round 0, so the halt and override pass on an empty origin, not a holed one.
  Action: hand it the origin's cells through the `offline` fixture. **Re-test:** the test's dataset
  is non-empty.

### Words and the sample tag (batch-2 reviews, 2026-11-05)

- **Unpadded `#N` outlives the one `sample_tag`**: `TrajectorySeriesView.tsx::SeriesHoverPopup`, the
  `L1Variants.tsx` targets line and `readout.py::on_race_catch_up` spell the id themselves.
  Action: each reads `format.ts::fmtSampleTag` / `domain/sample.py::sample_tag`. **Re-test:**
  `git grep -nE "#\\$\\{|'#' \\+|f\"#\\{" -- webapp/components promptpotter/application/views`.
- **`config/settings.py::_git_head` answers `UNVERSIONED` silently** for a ref in neither the loose
  refs nor `packed-refs` (a reftable repo, corrupt refs). Action: the landing's refusal names the
  checkout it could not version. **Re-test:** a landing from a reftable clone names why.

## Open — multi-arc

- **The crowning bar tests nothing a result can quote.** `application/scoring/selection.py::elect_round_winner`
  admits with no interval and no multiplicity correction, and `RoundResult.improved` reads any selection
  past round 0 as a promotion; at panel width `exact_p_floor(6) = 0.031`, Holm across three arms 0.094, so
  no corrected exact verdict reaches α=0.05. Call any "this round improved" provisional. Action: re-read
  separability on a run that used the tenant ruler, then choose a bar — never tighten one before. **Rides
  with:** [`../../paper/README.md`](../../paper/README.md) § The sequence, stage 3. **Re-test:** a
  tenant-ruler run's `index.json::final.rounds_to_separable` beside its `rounds_to_improved`.
- **The free tier offers what it refuses.** `application/free_tier.py::with_free_tier_layer` narrows each
  model's params but never the model axis, so a free check-in offers models Start refuses. Action: the
  per-value lists name only values known to fail; the spend ceiling bounds the rest. **Rides with:**
  the next check-in pass. **Re-test:** a free account's model menu equals
  `free_tier.yaml::task_models`.
- **NEXT — the default host pin is a trial with no verdict.** `domain/pipeline_overlay.py::pin_default_routes`
  writes each model's default host (`infrastructure/llm/registry.py::_MODEL_PROFILES`) into every
  node config; `route_order: []` opts out. **Decided:** a model keeps its pin only where its realized
  cache share and effective $/M beat running uncached. **Rides with:** the next supervised campaign on
  a pinned model, beside its `route_order: []` twin. **Re-test:** `SpendRollup.by_node` in each twin's `review.md`.
- **The responsive walk takes no Lighthouse score and never sizes the candidates card.** Action: one
  Lighthouse run at 375 px, recorded here; the card swept once a spec has such a campaign. **Rides
  with:** any layout change with a browser open. **Re-test:** `grep -n candidate webapp/e2e/walk/responsive.spec.ts`.
- **Compare cards are laid out for few wide columns**, not five thin stripes on a 16:9 display
  (`components/compare/ChannelCard.tsx`). **Rides with:** the next compare-pane pass, through `/design`.
  **Re-test:** five candidates at 1920×1080 without horizontal scroll.
- **Optimizer-model repair rate on L2/L3 structured output is unmeasured.** **Rides with:** the next
  supervised campaign that escalates. **Re-test:** the share of its L2/L3 calls that needed a parse repair.
- **`tests/test_scoring.py::_peer_cycle` stands a `Cycle` up by hand**, banking a C0 report
  `application/origin.py::establish_campaign_origin` cannot produce. Action: a backend whose answers
  produce the asserted origin, started through run init. **Rides with:** any change to how run init
  scores the origin. **Re-test:** `grep -n "_peer_cycle" tests/test_scoring.py`.
- **The never / partly / always spread contradicts the headline beside it.**
  `application/scoring/measurement_log.py::_cycle_cells` pools every candidate the cycle ran, so
  `domain/cells.py::hit_spread` reads a sample only C0 saw as "never". One decision: the spread
  describes the subject (the selected searchpoint's own cells), or stays a search-coverage statistic
  labelled as such, with samples only weak candidates measured counted as *unchallenged*. **Rides
  with:** any change to `hit_spread` or the run card. **Re-test:** ask the operator which.
- **A scoring cell beats as an LLM call.** `application/scoring/sample_measurement.py::_send_cell`
  and `search_point_scorer.py::_claim_cell` append `LLMCallProgressRecord`s named
  `node="backend_scoring"` (no view has it) with a `call_id` of `scoring:{position}`, so the
  dashboard fold cannot key a beat's `inner` hop to its open cell: with look-ahead above 1,
  `CurrentRound.inner` names the latest-beating cell. Action: a cell beat carries its `position`
  and no node. **Rides with:** the next change to the heartbeat record. **Re-test:**
  `git grep -n '"backend_scoring"' promptpotter`.
- **`domain/export.py` carries no optimization spend or tokens**, so a reader cannot compute amortized lifetime cost ([`../research/external-constraints.md`](../research/external-constraints.md) § Cost reporting — binds every run from now, not only the preprint). **Rides with:** the next export shape change. **Re-test:** `grep -n "spend\|token" promptpotter/domain/export.py`.

## Open — optimizer tuning

**Rides with:** "The optimizer climbs" in [`roadmap.md`](roadmap.md) § Sequence.

- **PoBB locks in a leader the election then refuses (high-accuracy regime)**: seen on tenant
  `inbox-requires-action-v6` round 1 (parent 92.5% of 40). The round order puts the parent's misses first
  (`build_round_order`, "win-opportunities first"), so after 6 cells a candidate that fixes them reads
  `P(c>p)=93.8%` and `pobb` locks it in at `theta_election.admission: 0.85` (C1.2 locked at q6, C1.4 at
  q13, both 100%). The election then refuses both: `θ lift +0.400 logits, se 1.165`, `collapsed_band`
  caveat. The round's cells went to the three losers (38, 35, 27 cells) and the two leaders got 6 and 13,
  so the round held at the price of a full round. Two bars spelled twice: the lock-in reads P(best) on
  the cells drawn so far (front-loaded with the parent's losses), the election reads θ with its se over
  the whole ruler. Action: the lock-in asks the election's own question (would `theta_election` admit
  this arm now?), or a locked leader keeps buying cells until it does or the round's budget ends; a lock
  at q6 against a 92.5% parent needs at least the cells that could show one parent-level error.
  **Rides with:** any change to `early_stop` / `Selector.admission`. **Re-test:** rerun round 1 of
  `inbox-requires-action-v6__e1bda1`; an arm that is locked in is either elected or not locked.
- **A run reads every campaign's ledger, so one stale campaign crashes another**: `crm-extract-names__29f63f`
  crashed entering round 1 on `LedgerUnreadableError` for `inbox-requires-action-v6__e1bda1`'s ledger. The bill
  no longer reads a ledger and `land-data` refuses a tree holding one that does not load, but a desk tree is
  never landed, and `intelligence/earned_blocks.py` and `initialization/loop_start.py::_banked_instruments`
  still read every campaign's ledger after origin scoring is paid. Action: those readers refuse at mint,
  before origin. **Re-test:** with one pre-shape-change campaign on a desk tree, `new` refuses before spending.
- **A re-read arm has two names**: `runner/measurement.py::ReferencePairs.of_round` names an individual
  by the slot an EARLIER round walked it in, so a population member walked again (CAPO's
  `population_rejoin`, potter's held leader) reads `C2.1` on its pair and `C3.1` on the round card. **Rides with:** any change to
  `arm_name`. **Re-test:** a held leader's round-3 pair names its round-3 slot.
- **An operator's authored arm is refused a field they held**: `bench/children.py::carried_writes`
  refuses every child a `NodeSearchNarrowing.prompt_fields_held` field, the operator's own arm with
  them. Open: may that arm write it? **Rides with:** any change to `carried_writes`.
- **A declared bench is not the held-out count**: `CampaignConfig.dataset_split` `{bench: 100}` held out
  162 rows on the box's `hotpotqa-closedbook`. No shipped recipe adds it a `held_out` tail and the
  loader dedups rows, so the 62 are in the box's tenant definition; the one code path holding out past
  `bench` is `domain/bench.py::partition_bank` holding declared `held_out` rows beside the ranked
  ones, which L4 inner cells (`bench: 0`) and `justlogic/d234-held` are built on. Open: read the
  definition (`/potter-box`), then decide whether `bench` becomes the total. **Re-test:** a fresh
  `{bench: 100}` campaign on that definition holds out 100.
- **`noise-floor` lets its pause escape as `PassStoppedError`**, a band over fewer than `k` rescores
  being no band; the pass's address is Q1's (§ The quantum-leap pass). **Rides with:** any change to
  `paid_pass`. **Re-test:** stop a `noise-floor` from a second terminal; it banks no band.
- **A locked-in arm's catch-up cells are read by its race, never its round**: `potter/race.py`
  measures a prior on each later challenger's cells (`RaceCatchUpRecord`), but
  `runner/measurement.py::_walk_population` folds only the arm's own walk into `rows[key]`, so the
  election and scoreboard read the leader on the cells it stopped at. capo-pobb (CAPO ranks on shared
  cells) elects an arm PoBB eliminated. Action: a caught-up cell is a row of the prior's round reading.
  **Rides with:** any change to `CatchUpPool`. **Re-test:** a locked-in leader's round reading counts
  its caught-up cells.
- **A lost mirror loses its money**: `infrastructure/llm/spend_book.py::SpendBook.mirror` logs a failed
  append, yet the nested record already says `mirrored=True`, so no bill or bank counts that call. Action: a
  failed carry raises, or the nested record is written only after the carry lands. **Re-test:** a root
  append that raises leaves the call counted on the account.
- **A fork cannot narrow a dead param axis**: `ForkProposal.unlock_schema_field_rename` is the sole
  config a rebase applies (`stand_on` re-elects, it changes no config), so L3's measured ask to drop
  an axis value no arm gained on has no channel. Action: decide whether a narrowed
  `param_allowed_values` is fork-only, and if so name it as a `ForkProposal` member mapped to one
  `ConfigOverrides` edit. **Re-test:** `git grep -n "class ForkProposal" -A20 -- promptpotter`.
- **The fastest hosts refuse the `l1_generate` schema**: Cerebras and Groq (`openai/gpt-oss-*`) answer 400 to
  `L1GenerateOutput`'s inlined JSON schema — Cerebras wants `properties` on every object, Groq strict mode wants
  every property `required`. Every object on the wire comes closed and spelling its `properties` from
  the response models themselves (`dispatch/l1_wire_schema.py::build_l1_response_schema`); every optional property is still optional, since the wire sends `strict: False`
  (`infrastructure/llm/openai_compat.py`) and an all-required, nullable schema grows each request and reply.
  Action: make the wire all-required only together with sending `strict: True`. **Re-test:** one `l1_generate`
  call pinned to Cerebras parses.

## Open — from the head-to-head arc

A leading **INVESTIGATE** means nobody has shown the item is real; answer that first.

- **An arm is planned on a replicate it does not have yet**: `command_kinds.py::HeadToHeadRequest.purpose`
  declares replicate 1, and `jobs/mint.py::_numbered` overwrites it under the record hold. The number
  keys on `plan.treatment`, which `resolve_cycle_plan` builds from a whole `RunDeclaration` that already
  holds `purpose`, so the plan cannot be built without a number. The placeholder also reaches the root id
  (`measured_address`), so admission prices plans whose ids are not final. Holding the record across
  planning is no fix: the hold is try-once, and the arms mint after the dispatcher's ack. Action: a
  purpose-free run spec feeds `resolve_cycle_plan`, `effective_config` and `shared_part`; the plan carries
  `Optimize | ArmSeat(head_to_head_id, arm_key)`, and the mint builds `Compare(Arm(…, replicate))` once.
  `declare` rewrites `optimizer_narrowing` and `bench_trigger` on `isinstance(purpose, Compare)`, and that
  moves with it. **Rides with:** U1a's `RunScope` (`campaign_config.py`). **Re-test:**
  `git grep -n "replicate=1" -- promptpotter`.

- **`dashboard.json::run_limits`'s money axes still go stale on `set-limits --max-usd`**: the
  `CampaignCeilingRecord` sits on the root cycle's ledger, which no other cycle's fold reads, so
  `campaign_store/store.py::CampaignStore.armed_run_limits` lays it at the served read only. One budget rule is spelled in
  three places: the fold, `jobs/quota.py::declare_run_budget` and `infrastructure/llm/spend_book.py`.
  **Re-test:** after `set-limits --max-usd X` on a fork, `dashboard.json::run_limits.budget.usd == X`.
- **A reserved cell's bill is on no ledger** (`Connector.holds_own_sends` writes no hold), so
  `infrastructure/llm/spend_book.py::unreported_on` can re-teach a book its sends but never the cell.
  Action: a hold record per reserved cell. **Rides with:** any change to `spend_book.py::reserved`.
  **Re-test:** `grep -n "emit_spend_hold" promptpotter/infrastructure/llm/spend_book.py`.
- **INVESTIGATE — does an errored cell bias a head-to-head?** `gpt-oss-20b:nitro` can end
  `finish_reason=error` then pass one schema repair; unknown whether the row banks the answer or a
  miss. **Rides with:** the next head-to-head read. **Re-test:** per arm, the hit rate of error rows
  against the arm's other rows.
- **INVESTIGATE — CAPO's survivors are ranked on another reading than the reference ranks them on.**
  `population` keeps μ best-first by mean objective on the cells all survivors measured; promptolution's
  CAPO sorts survivors on raw score over every cell each has been evaluated on, cached across rounds,
  so an incumbent's deeper blocks from earlier races count. After a one-block race (the paper's rule,
  `candidate-elimination.md` § CAPO's race) the two can crown differently. Open: which reading the
  bench compares. **Rides with:** any change to `capo/members.py::_rank_survivors`. **Re-test:** on a CAPO
  run with offspring < μ, rank one round's survivors both ways.
- **"Calls held equal" has no axis to hold.** `domain/spend.py::Budget` meters usd, tokens, rounds and
  wall clock; the roadmap and `external-constraints.md` § 4 hold calls equal, `paper/README.md` holds
  spent tokens, and `paper/plans/` holds USD under the arm's `search_incurred` meter, so two arms of one
  point make the same calls only at one cell price and one optimizer cost. Action: one matched axis,
  decided; if calls, a cells axis on `Budget` the search meter binds. **Rides with:** any change to
  `Budget`'s axes. **Re-test:** `price-head-to-head paper/plans/comparability.yaml` — do one point's
  `search_cells` differ by arm?
- **Structured optimizer calls send the clamp's seed raw** (`bench/llm_call.py::_merged_config`)
  while `Calls.ask` draws one per call: two seed rules, so unpinned structured calls with one prompt
  replay across campaigns. Action: one seed rule, read off `domain/campaign.py::run_clamp`.
- **The $0 price opens a backend session** (`application/head_to_head_price.py::price_head_to_head`
  through `jobs/launcher/head_to_head.py::dataset_arms`) only to resolve the node models and the
  partition, so a stopped backend refuses a read that makes no call. **Rides with:** any change to
  `dataset_arms`. **Re-test:** price a plan with the backend down.
- **An optimizer's round price is its name's, not its treatment's**
  (`application/head_to_head_price.py::optimizer_round_price`): the three `escalation_ladder` arms read one potter
  price, though an `l1` round sends fewer optimizer calls than a `full` one. Action: key it on the
  treatment once enough campaigns of each have run. **Re-test:** the ablation's optimizer line names
  one price for three ladders.
- **INVESTIGATE — a CAPO arm is admitted on its round CEILING** (`preflight.py::check_cap_funds_round`
  over `round_cells_ceiling`: 4,230 cells on 310 search rows), which its race rarely spends, so a
  budget point must fund the worst case to mint CAPO at all. **Re-test:** a finished CAPO round's
  cells against its ceiling.
- **A selection is still made beside the one a read already holds.** `checkin.py::_land_draft`
  refuses each overlay before landing, then `resolve_cycle_plan` selects and refuses again;
  `config_map.py::config_map` selects off the campaign's cycle config for itself, beside
  `pipeline_resolve.py::_Merge`'s. Action: a check-in plans its cycles off the draft before landing,
  and a campaign's cycle config carries its selection. **Rides with:** U2's `RunStart`.
  **Re-test:** `git grep -n "select_optimizer(" -- promptpotter/application/config_map.py`.
- **A moved capability or backend revision alone passes a resume.** It is named as a drift cause only
  when the origin hash also mismatches (`jobs/mint.py::resolve_resume` → `ConfigDriftError`).
  **Fork:** refusing contradicts "a drift is served, never refused" (`RunWiringRecord.backend_revision`);
  serving needs a `RunWiringRecord` field like `defaults_drift` at every entry point.
- **`round_cells` has two meanings for one draw:** potter's unclamped value, read by L4, verify and the
  overlap, and the backbone's ceiling panel, clamped to the search split. Also, `RoundPlan.walk` and
  `round_walks` each decide which walk counts as a round of its own.
- **The capo offline bench turns on one seed**: lift +0.700 or none after a draw-order change, so the
  fixture cannot tell a mating-law change from a reshuffle.
- **The controlled offline leg's arm-memory count moves between identical runs** (`arm potter: memory
  holds 380` then `375` own answers on one tree): a cell coalesced with a twin in flight is answered
  from memory, not sent. A printed reading, not a decision. Action: count filed answers, not distinct
  ones — the count is `scripts/offline_run.py::run_controlled`'s (`file_answers` mints a ref per
  filing, and a coalesced cell files none). Separately, `--controlled` stops at `_variant_edit`
  ("no framing to cite") on the foreign potter campaign, on main too. **Re-test:** two `--controlled` runs of one tree print one count.

## Open — renames the operator picked (2026-10-29, low priority)

**Rides with:** a pass touching the name, or the post-2026-10-30 wipe for on-disk keys. Names are root
`CLAUDE.md`'s rule; each rename lands with its doc and `CLAUDE.md` row in one commit. **Re-test:** the old
word's `git grep -c` for that sense is 0.

- **`candidate_library` → `answer_terms`** (the target's answer-term list, beside
  `DraftCampaign.answer_space`), and `PoolSource.LIBRARY` → `PACKS`: the route
  `/datasets/draft/candidate-library`, `EditDraftPatch` and the dataset file move, and
  `shared/hashing.py::content_hash`'s key re-keys root ids.
- **"course" → "cycle" in the served and doc layer** (`HierarchyCourse`, `level: "course"`,
  `/hierarchy/{course}`, `ids.ts::courseAddressOf`); `CycleHop` stays an element of a `CyclePath`.
- **"line" in its live-cycle sense → `live_cycle`** (`command_kinds.py::LinePayload`,
  `BenchLine.on_line`, `domain/bench.py::LineRun`); "selection line" is never bare "line". A command
  payload kind, so ledger shape.
- **`nodes.py::Panel` / served `ArmPanel` → `RoundSamples` / `ArmSamples`**, the L4 roster → `inner_panel`;
  "panel" stays the evidence section an LLM reads.
- **"slot"**: prompt slot → prompt field, layout slot → region, `domain/arms.py::ArmSlot` →
  `ArmPosition` (ledger field), bank `slot_of` → `position_of`. The layout key is optimizer-prompt text.
- **`CycleSeed*` / `ForkSeed` → `CycleStart*` / `ForkStart`**, so "seed" is the RNG seed alone. A
  ledger record.
- **"member" for an individual → "individual"** (`run_standing.py`, `origin_panel.py`); "member" is the
  optimizer node; "candidate", an individual at a round address, is owed to the Vocabulary row.
- **The served hierarchy discriminator `level` → `entry_kind`**
  (`git grep -c -w level -- webapp/components webapp/lib`).
- **The READING node → reader node** in `resolved_schemas.json`'s layout prose: prompt text, so a
  re-measure.
- **draft vs check-in, by rule, no rename**: "check-in" is the resolver-and-gates process, "draft" the
  object (`DraftCampaign`); owed to root `CLAUDE.md`'s Vocabulary row.

## Open — from the structure pass

**Rides with:** any change to the file named. **Re-test:** the `file::symbol` still holding it.

- **Seen in the `lib/` regroup**: `lib/hooks/useRegistry.ts` and `lib/workspace.tsx` import `derivations/hierarchy` past the barrel
  to dodge a cycle (barrel → search-point → poll → workspace); `lib/theme.ts` mixes chart setup with
  the theme preference.

- **A scored but unhealthy origin still holds a live process**: `runner/origin_gate.py::run_origin_gate`
  halts an origin with no scored cell, but holds a degraded one, and its three answers repeat run verbs
  (`abort` ≈ leave it ended, `proceed` ≈ `resume`, `rescore` ≈ `resume --from 0` force-fresh).
  Open: halt it too, deleting the hold.
- **Two seams owed a re-read:** `webapp/lib/hooks/useCompareWithOrigin.ts` picks the best pointer;
  `presentation/cli/commands/resume_command.py::_steer_overlay` builds its own overlay.
- **`domain/phases.py` is two fifths stop-reason table**; a split was measured and declined, a
  rename re-points ~46 files. **Rides with:** a pass that moves the table.
- **A declined pause refuses with "."**: `phases.py::RUN_PHASE_INFO` gives `RUNNING` and `SCORING` a
  parked `""`, which `RunAdmission` capitalizes into the refusal. **Re-test:** each phase whose
  action can be declined parks a non-empty word.
- **A cold server folds every ledger before the first page answers**: the first
  `GET /hierarchy?depth=1` waits on a full `LEDGER_FOLDS` pass over every `projects/` ledger, and
  the browser shows a sidebar stuck on "loading…". On 2026-10-30, with 91 MB / 129k lines in
  `projects/`, it took 33 s cold beside the boot sweep and 1.1 s warm (`.scratch/cold_real.py`).
  `SpendRollup`'s private-attr factories and per-record `setdefault(k, Model())` cost a further
  70–90 s before they were cut. One shape remains (the `run_state_stamps` ETag is Q2's,
  § The quantum-leap pass): `jobs/reaper.py::periodic_sweep` runs at boot, beside the first page. Over
  58 ledgers it folds 23 s, about 10 s of it on L4 inner-sandbox ledgers that no page reads.

  The fold itself is the root: pydantic validates each screened line on every cold start. A fold
  snapshot keyed by `file_sig` would make a restart pay only the tail.
- **Seen in the parsimony pass (2026-11-01)**:
  - **An activity row is a `Said` row under other names**: `domain/activity.py::ActivityItem` carries
    label, tone and detail, with kind glyphs (★ ◆ ↻) beside the CSS tone glyphs. One `Said` row
    reaches every constructor, `chat/LiveSegment`, `TimeRay` and the event contract. That reshapes
    the event contract (AsyncAPI) and its chat readers, so it is its own arc, not a word table.
  - **Off-screen view columns stay mounted and poll**: `shell/Views.tsx` mounts every open column, so a
    phone on the Dashboard preset runs every pane's reads at once. React's `<Activity mode="hidden">`
    per column, on one IntersectionObserver, stops them and keeps their state. The fork: a column
    swiped into is blank until its reads land.
  - **Run data past `MAX_PATH` is read only where `store/io.py` reads it**: `io.py` writes through
    `_long_path`, but `store/read_model.py::file_sig` and `store/io.py::complete_lines` / `holds_mark`
    (among many: `grep -rnE "\.stat\(\)|[^_.a-z]open\(" promptpotter`) read bare, so a ledger past 260
    characters reads as absent and a deep L4 store lists no course. This desk has long paths off. Root:
    spell the data root once in its long form (`config/paths.py`) so every derived path carries it;
    the cost is every comparison with a short-spelled path (`tmp_path`, `REPO`). The Linux box is unaffected.
  - Unverified, for the next change to each file: `shell/CriticalAlertBanner` builds `Said` rows in the
    browser and re-sorts `TONES` for `pinned`; `ViewTabs.show` is many `open`/`close` calls, not one
    workspace act; `cycle_files.list_cycle_files` walks all of `.runtime/` and filters after
    (`iter_files(skip=)` exists); `domain/activity.py::ActivityFeed` falls back to `"run error"` where
    `run_said` has none; `iter_family_courses` reads each index twice; `list_hierarchy` re-calls
    `line_config` per non-holder course; `FileTree` mounts a 2 s `useActivePointer()` poll for one
    failure line; a `/hierarchy` 200 replaces every entry object `lib/hooks/useRegistry.ts::useRegistry` indexes.
- **The collapse gate cuts a hedging arm on noise**: `pobb/checks.py::check` →
  `domain/scoring.py::is_answer_collapsed` reads one repeated label over `n_min` answered cells as
  "no measurement". The panel walk runs hardest-first, so its first cells can hold one or two truth
  labels. A task model that answers `Uncertain` on about 75% of cells then trips the gate by chance in
  about 18% of arms (0.75⁶).
  - Seen 2026-10-30 in L4 inner `justlogic.d234__731eda`: C1.2 was cut at 0/6, all `Uncertain` and
    every one parsed. The round's own n_min top-up read it at 0.393 against C0's 0.5 over the 28-cell
    origin panel.
  - The bar and the sidebar lead with the 6-cell 0%; the 39% shows only as the overlap series.
  - Collapse must be judged against the truth labels the cells hold, and against the parent's answers on
    the same cells. An engine decision: prove it with an offline run.
- **The server's shutdown notice counts runs it does not host**: `main.py::_telegram_shutdown_notice`
  reads `JobRegistry.list_running()`, which holds the machine's CLI runs too. A server stopped beside a
  CLI campaign reports it "stopped … in flight" and says "Resume from the campaign once it is back up",
  but the run continues. The notice must name only the jobs this process owns. Seen 2026-10-30 against
  `promptpotter-self.panel__606cc9`; the same stop logged `ASGI callable returned without completing
  response` for its open `events:subscribe` stream.
- **Rename candidates, not yet picked** (one word, two senses each):
  `evidence/head_to_head.py::HeadToHeadRow.swapped_in` beside the manifest `swap`;
  `application/library.py::library` beside `candidate_library` (rides the `candidate_library` →
  `answer_terms` rename); `domain/pool.py::PoolOffer` / `offered` beside
  `promptpotter/application/datasets/startability.py::offered`.
- **A member knob's `NodeConfigParam` serves no estimand**, which the config map shows for every campaign
  knob (`application/knobs.py`), so the setup screen cannot say what moving one moves.
- **Seen building the setup screen**:
  - `OptimizerSetupInner` reads the optimizer from the draft and from `useConnector().optimizer`, which
    are one value through two doors.
  - An edited optimizer-prompt field cannot be reset: `OptimizerNodePrompt` serves no template text and
    `campaign_config._merge_dicts` keeps a null as a value.
  - Smaller: `title=` used as teaching prose in `NodeDetail` and `OptimizerCard`;
    `CheckinLoadingWindow` keeps its own `setInterval` outside `lib/cadence`; `.setup-step` is styled in
    `chat.css`.
- **Seen in the phone-first setup pass**:
  - A changed knob value names no source: the resolver keeps none for `optimization.nodes` layers
    (dataset block, draft override, cycle seed), so the row says only "Changed from default".
  - `locus_crossover` is listed as a maker with nothing upstream making two-parent children; an
    arm-editing maker's on/off belongs on `nodes.Proposer` as a member-declared rule.
  - The check-in picklist button's accessible name runs slug and meta together; two open check-ins
    share one slug.
- **Potter's `dispatch/` modules still hash whole** (`shapes_optimizer_prompt(__name__)`): a member
  module's reads are walked (`nodes.py::_source_digest`), so only a unit read through an instance or a
  registry needs a mark. **Re-test:**
  `git grep -n "shapes_optimizer_prompt(__name__)" -- promptpotter/application`.
- **Seen building the prompt workspace**:
  - An override cannot be removed, only overwritten: knob Reset writes the declared value and a cleared
    prompt field writes `""`, so the draft stays "edited". The overlay merge needs a delete.
  - `PipelineSetupSection` picks a default node in a `useEffect` with a ref latch: a second door into the
    shared node selection.
  - The packs copy each snippet's arXiv id and method into its `tags`, so Tags repeats Source and Method.
  - `ui/MenuCheck` takes a `title=` prop, which `CandidatesToolbar` fills with teaching prose.
- **Seen framing snippets in a prompt**:
  - `.prompt-snippet-input` re-declares the field skin's border and background inside the frame.
- **A side worktree cannot pass the full gate**: pytest's `compileall` breaks on long Temp paths,
  so a rebuilt commit series is gated without it. **Re-test:** the full gate green in a worktree.
- **Seen building the read catalogue** (2026-11-04):
  - `GET /auth/me` writes (`account.arrive`): a read catalogued as `ReadKind.ME` that opens an account.
    Action: arrival is the sign-in callback's act alone. **Re-test:** `git grep -n "arrive(" -- promptpotter/presentation`.
    Until it ships, the `me` read itself (`read_params.py::MeParams`) reads through
    `account.py::account_read`, which writes nothing; the route alone arrives, since an open box has
    no sign-in callback to arrive at.
  - Three routes are named `edit-draft-campaign`: its `/commands/` route and the two form doors in
    `routers/datasets/ingest.py` (`candidate-library`, `from-column`): N routes per command where a
    read gets one. Action: one route per kind, the form doors a
    form body on it, as `ingest-upload` is. **Re-test:** `git grep -n "name=CommandKind.EDIT_DRAFT_CAMPAIGN" -- promptpotter`.
  - `application/datasets/draft_patch.py` drops `nodes` on an optimizer switch, the rule
    `domain/campaign_config.py::merge_config_layers` already applies: one rule spelled twice. Action:
    the merge alone. **Re-test:** `git grep -n 'pop("nodes"' -- promptpotter/application/datasets`.
- **Seen building the host coupon** (2026-10-29):
  - Every connector declares its cells the box's to pay (`Connector.cell_payer`): none can send on the
    account's key, so an own-key account past its coupon is refused any run with a backend. Action: a
    connector that takes the account's key per call declares `"user"`. **Re-test:** an own-key account
    at a used-up coupon runs one TermNorm cell.
  - `api_keys.json` holds account keys in plaintext at rest, beside `user.json`. Action: encrypt under a
    box secret, or name the file in access-model's at-rest table. **Re-test:** `cat` one.
  - A resumed cycle's prior metering counts as the box's in its book's coupon reserve
    (`SpendBook.take_up` reads only unreported own-key holds), so a resumed own-key run reserves more of
    the coupon than it draws. Conservative, not a leak. **Re-test:** resume an own-key cycle and read its
    `off_coupon_usd`.
  - `TokenUsageRecord.cached` and `key_source is None` are two spellings of one fact (a replay no
    wallet paid). Action: one field. **Re-test:** `git grep -n "key_source=None" -- promptpotter`.
  - `coupon.py::keyed_client` folds the account's charges on each send the box's key pays (it reads
    the remainder fresh). Action: the run's book carries the remainder it was admitted against.
    **Re-test:** time one send on an account with 100 cycles.
  - Restamp drops `User.spend_budget_usd_total` / `token_budget_total`: a hand-raised account must be
    re-granted as `grant.json`. **Re-test:** `ls .promptpotter/projects/*/grant.json` after a restamp.
  - `LifetimeSpend` carries no token count, so the coupon's token side shows left / face, never used.
    **Re-test:** the Host allowance section with a token-bound grant.
  - `AccountUsageTab::LimitControl`'s note ("runs on their provider key") predates own keys.
- **Seen moving harbor's agent onto our client** (2026-11-04):
  - `LLMResponse.cost_usd` omits a resend `_admitted_send` made that may have billed (the book holds it
    as unreported), so `_AgentModel.cost_usd` can read a reported sum below the book's. **Re-test:** one
    5xx then an answer, read the row's `cost_usd` against the ledger.
- **Seen removing the migrations** (2026-10-30):
  - `jobs/logs/{job_id}.log` (`launcher/run_job.py`) outlives its job: nothing deletes it.

## Bypasses — held for one holistic pass

**A send made on our behalf re-derives what `_admitted_send` decides for our own.** Patched singly,
each fix is one more copy; the remedy is the upstream redesign.

- `infrastructure/llm/send_pacing.py::SendBudget` stops at the relay's door: TermNorm's
  `_MAX_ATTEMPTS`, and terminus-2's `_query_llm` thrice over `connectors/harbor.py::_AgentModel` (each
  send inside on our budget), retry on their own. **Re-test:**
  `grep -n "_MAX_ATTEMPTS = " <termnorm>/backend-api/core/llm_providers.py`.

## Blocked

- **TermNorm's live datasets (`llm_only`, `entity_profiling`, `llm_ranking`) are refused at parse**:
  TermNorm's `_enrich_with_registries` must carry `meta["fields"]`, its prompt metadata declare
  `fields`, and `direct_prompt` gain a `prompt_info`. **Blocker:** the operator's go to edit the
  TermNorm checkout. **Re-test:** `python -m promptpotter new llm_only` parses its pipeline.
- **Two readings wait on the operator:** a declared slot before `rewrites` + `field` fold into one
  knob; which ruler `ab_replay` reads.
- **`infrastructure/llm/anthropic.py::AnthropicClient` cannot run a node on a current Claude model**
  (no effort mapping, sends `temperature`, never sends the schema, stale `max_tokens`). **Blocker:** no
  Anthropic credential. **Re-test:** `settings.ANTHROPIC_API_KEY` non-empty, then `probe-reasoning`.
- **A head-to-head's arms do not share one optimizer model.** **Decided:** every arm on one model,
  each peer's swap a declared deviation. **Rides with:** the next head-to-head mint. **Re-test:**
  `grep -n "model:" promptpotter/assets/optimizers/*/pipeline.yaml`.
- **Archive hygiene — build before the next bulk delete.** **Re-test:** `compact-archive inventory`.
  (1) **Reclaim**: a dataset-scoped destructive `delete` counterpart, dry-run by default, refusing
  while a producer can append, naming what it would strand. (2) **Attribution**: an L4 outer cell
  stamps the runs its inner campaign produced when it spawns them; never re-file a backfill. (3)
  **The reach map**: the campaign family left, the archive partitions it reaches right, the
  load-bearing column what is shared outside the selection.
- **Cross-repo (TermNorm `backend-api`):** a `/version` endpoint, then bump
  `connectors/termnorm.py::_EXPECTED_REVISION` (re-test: it is `None`). A co-owned backend fix replays
  stale results because neither cache keys on backend revision: fold the revision pin into the
  measurement-cache key (re-test: grep for `--fresh` or a revision term on the key).
- **No inbound REST credential** — owned by [`../developer/stable-api.md`](../developer/stable-api.md)
  § 8; owed here: the credential and its `submit → poll → fetch` examples. **Re-test:** a bearer or
  API-key reader in `promptpotter/presentation/api/`.
- **Swapping a model means hand-editing two `pipeline.yaml` lines.** **Blocker:** a swap verb.
  **Re-test:** one in `presentation/cli/commands/`.
- **`infrastructure/llm/json_parse.py::try_groq_json_validate_repair` banks a missing count as an
  empty account.** Never estimate from content length. **Blocker:** `TokenUsageRecord` has no
  unknown-count dimension. **Re-test:** a `provider: groq` in any dataset pipeline.
- **`_rebank_on_branch`'s re-bank has never been observed.** **Re-test:** repair a fork; each
  corrected round carries its own `round_restated` record.
- **The `seed` provenance layer has never been stamped by real data.** A `resume --steer NODE.PARAM=VALUE`
  fork exercises it. **Re-test:** from the checkout root, `grep -rh cycle_seed
  .promptpotter/projects/*/campaigns/*/cycles/*/.runtime/ledger.jsonl | grep -c '"pipeline_overlay": *{'`.
