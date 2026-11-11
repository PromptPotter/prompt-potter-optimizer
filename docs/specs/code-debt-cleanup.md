# Code-Debt Cleanup — Backlog

**Only work that is blocked or multi-arc** — including every shape a pass noticed off its task (root
`CLAUDE.md` `<situational-awareness>`). Anything one edit closes is fixed in the pass that found it; never the root of a patch just shipped (root `CLAUDE.md` `<root-fix>`). An item ships by
being deleted. Each entry: `file::symbol` — the defect — the action — **Rides with:** the chain that
reaches it ([`roadmap.md`](roadmap.md) § The cleanup chains (wave 4)), or **Held:** why
none can yet (a digest, a decision, a mechanism, a served change, a survivor) — **Re-test:** the
command or question that says it still stands. Run the re-test before acting.

## URGENT — shapes that hide the backbone from a reshape

**Rides with:** any pass in the file named. None open.

## Open — multi-arc: wave 4's chains, in landing order

Each row sits under the wave-4 chain that closes it ([`roadmap.md`](roadmap.md) § The cleanup chains
(wave 4)); `chain N` is that chain's link N. A chain re-tests its rows in its census and deletes each
row it closes.

### batch/23 · startb

- **One word per thing.** "Arm" is the round's; a head-to-head's arms become `RunStart`s; a host task is
  a command id. **Rides with:** startb 1.
- **Each entry point builds its own answer to a launch**, though the ack's `run_records.py::LaunchedEffect`
  records the job. Action: the dispatcher computes one wire answer (the applied model, else the launched
  jobs, else the record); `checkin.py::StartCheckinResponse` and `launcher/head_to_head.py::HeadToHeadStarted`
  stop being wire models; `api/routers/commands.py:44-82` stops hand-typing two launch routes.
  **Rides with:** startb 1. **Re-test:** `git grep -n "class StartCheckinResponse" -- promptpotter`.
- **A launch refused before admission reaches a host as a task** — `host_surface.py::HostSession._open`
  opens the task once the act is filed, before it is admitted, so a refusal answers `working`, then
  `failed`, and the handle names no campaign; it polls the account log every `_FILED_POLL_S`. Action: a
  task opens for an admitted act and names the address it acts on; a refusal before admission is the
  call's answer on every door; the dispatcher hands back its admission outcome and the poll goes.
  **Rides with:** startb 1. **Re-test:** `git grep -n "_FILED_POLL_S" -- promptpotter` is empty, and
  `mint-campaign` over `promptpotter mcp` against an unreadable workspace ledger answers `isError`.
- **A Start's check-in stops at `queued` / `starting`**: the mint closes the draft, so its read 404s, and
  no served answer names the campaign a draft's Start minted. Action: one read of a Start off its job
  (`CampaignLaunch.draft_id`, its stage, then `Job.hop`). **Rides with:** startb 1. **Re-test:** Start a
  queued check-in; the view lands on the minted campaign unreloaded.
- **Five doors and three job lifecycles meet in one `HeldRun`.** `jobs/launcher/launch.py::_hold` switches
  over three request types; a head-to-head mints through a second door; `embedded_run.py::run_campaign`
  and `runner/inner/spawn.py::_open_seat` mint or resume their own way; `admission.py::embedded_share`
  and `admission.py::inner_share` copy one lifecycle beside `run_job.py::run_held_job`; the detached
  child rebuilds its plan through `run_job.py::open_held_cycle` and `mint.py::resolve_resume`. What
  varies, and stays: what starts, its admission and how it runs. Action:
  `start(RunStart × N) → [Launched(job, held or none)]`; the running process builds its `HeldRun` by one
  drift-checked `open_cycle(hop)`, which binds `RunScope`; a fork is `open_cycle` under a carried
  admission. The two `run_records.py::RunWiringRecord` writes stay. **Rides with:** startb 2.
  **Re-test:** `git grep -c "release_slot(\|job_registry.release(" -- promptpotter`.
- **Slot lifetime is a hand-rolled `except BaseException: release…` at ~20 sites** (admission, `run_job`,
  head-to-head, mint-and-start, check-in, embedded run). Action: `start` owns the slot; a context manager
  is the patch, not the root. **Rides with:** startb 2. **Re-test:** as the row above.
- **A fork's cut lands before its admission**: `jobs/launcher/admission.py::request_jobs` claims and
  `admit_and_hold` admits on the request's own hop, so `commands/launching.py::_launch_fork` cuts the
  child before asking, and a refusal leaves the parent's inert `FORK_CUT` line. Action: admission over a
  planned hop. **Rides with:** startb 2. **Re-test:** `git grep -n "cut_operator_fork(\|admit(" -- promptpotter/application/commands/launching.py`
  lists the cut before the admission.
- **A head-to-head arm holds outside its release**: `jobs/launcher/head_to_head.py` builds `HeldRun.of`
  and claims after its try. Action: `land_and_claim` inside the arm's release. **Rides with:** startb 2.
  **Re-test:** `git grep -c "claim_cycle(" -- promptpotter/application/jobs/launcher/head_to_head.py` is 0.
- **An arm is planned on a replicate it does not have yet**: `command_kinds.py::HeadToHeadRequest.purpose`
  declares replicate 1 and `jobs/mint.py::_numbered` overwrites it under the record hold; the placeholder
  reaches the root id, so admission prices plans whose ids are not final. Action: a purpose-free run spec
  feeds `resolve_cycle_plan`, `effective_config` and `shared_part`; the plan carries
  `Optimize | ArmSeat(head_to_head_id, arm_key)`, and the mint builds `Compare(Arm(…, replicate))` once.
  **Rides with:** startb 2. **Re-test:** `git grep -n "replicate=1" -- promptpotter`.
- **A mint takes no claim until `admission.py::claim_cycle`**, so the landing tells a live mint from a
  torn one by the job registry alone (`maintenance/landing.py:169`: `_live`, or a job holding spend in
  its workspace). Action: the mint claims its cycle before `adopt_ledger`, and the landing reads the
  claim. **Rides with:** startb 2. **Re-test:** `git grep -n "_held_workspaces" -- promptpotter/application/maintenance/landing.py` is empty.
- **A queued launch dies with the asking process** — the queue wait runs in the caller
  (`jobs/launcher/launch.py`). Action: the queued job on disk is promoted by whoever frees the slot.
  **Rides with:** startb 3. **Re-test:** `resume --step-rounds 1` behind a running cycle; it starts when
  the slot frees.
- **A queued launch withdrawn by an interrupt reads as a paused run**:
  `application/jobs/registry.py::JobRegistry.release` stamps `ending=run_stop_reason(exc)` for a
  never-admitted job, and `jobs/launcher/admission.py::release_slot` reads `job.ending is None` as
  `NOT_ADMITTED`. Action: a never-admitted release is the lapse `withdrawn`, and `release_slot` declares
  no stop for it. **Rides with:** startb 3. **Re-test:** release an unadmitted job with
  `asyncio.CancelledError()`; `job.lapse` is `None` and `job.ending` is a pause.
- **The daily-campaigns quota counts refused and withdrawn launches**:
  `application/jobs/quota.py::check_launch_quotas` counts `JobRegistry.list_launched_today`. Action: the
  quota counts `j.admitted or not j.released`; the listing stays whole (`machine_status.py` reads it).
  **Rides with:** startb 3. **Re-test:** `git grep -n "list_launched_today(user_id" -- promptpotter/application/jobs/quota.py`
  reads its length unfiltered.
- **The embedded launch files no command**: `application/embedded_run.py::run_campaign` admits through
  `jobs/launcher/admission.py::embedded_share` and mints itself. Action: an `Inline` without a slot whose
  held step (`jobs/launcher/mint_and_start.py::hold_fresh_mint`, `::hold_existing_cycle`) carries the
  host's `Session`; `run_campaign` dispatches `START_RUN` / `MINT_CAMPAIGN`, and `embedded_share` goes.
  **Rides with:** startb 4. **Re-test:** `git grep -n "embedded_share" -- promptpotter` names `embedded_run.py`.
- **Adapter-kept rules that belong under the dispatcher** — the leftover sweep runs before CLI launches
  and in the API's loop but never for MCP stdio or `embedded_run`; `embedded_run.__all__` hand-keeps four
  command wrappers. Action: the sweep moves under the one admission; the wrappers go. **Rides with:**
  startb 4. **Re-test:** `git grep -n "def compact_archive" -- promptpotter/application/embedded_run.py` is empty.
- **A taken name is read two ways** — `slug_exists` (the directory) against the definition's holders
  (`dataset.json`), and refused twice: `checkin.py:139` raises `LaunchError` beside
  `claim_own_definition`'s `SlugTakenError`. Action: one read of a tenant name's hold, one raiser, and
  `mint_and_start.py::LaunchError` goes. **Rides with:** startb 5. **Re-test:**
  `git grep -n "slug_exists" -- promptpotter` names one definition and its callers.
- **A selection is still made beside the one a read already holds.** `checkin.py::_land_draft` refuses
  each overlay before landing, then `resolve_cycle_plan` selects and refuses again;
  `application/knobs.py::config_map` selects for itself beside `pipeline_resolve.py::_Merge`'s. Action:
  a check-in plans its cycles off the draft before landing, and a campaign's cycle config carries its
  selection. **Rides with:** startb 5. **Re-test:** `git grep -n "select_optimizer(" -- promptpotter/application/knobs.py`.
- **A non-TTY `resume` says a config drift twice**: `resume_command.py::_pivot_to_fresh` prints it and
  re-raises it where no TTY answers. Action: print it only before the prompt a TTY answers. **Rides
  with:** startb 6. **Re-test:** a non-TTY `resume` over a drifted config prints the drift once.
- **The CLI repeats `--from`'s bound**: `presentation/cli/commands/resume_command.py` checks `>= 0` beside
  `RunMode.from_round`'s `ge=0`. Action: the CLI checks neither. **Rides with:** startb 6. **Re-test:**
  `git grep -n ">= 0" -- promptpotter/presentation/cli/commands/resume_command.py` is empty.

### batch/23 · feed

- **Q2: one change feed — the server says what moved.** The webapp guesses which served subject moved
  (a cadence per read, a re-ask per write, phase re-asks, focus and online revalidation,
  `cadence.ts::RECONNECT_POLL_MS`); the server guesses beside it with clock edges
  (`runtime_flags.py::run_state_stamps`) and one store-wide version (`application/hierarchy.py::entries_version`).
  Owner: a version per subject (the length of the ledgers it folds, plus the moves a sweep detects) on one
  account SSE stream (`routers/reads.py::_events` is the shape); the cache re-asks exactly the moved
  subject; `routers/reads.py::_conditional`'s ETag is the same version; the projection roster, never a
  hand table, says which reads an append moved. Stream lost → reconnect, re-ask every mounted read once.
  Invariants: under any reorder, drop or duplicate the cache equals the server at its last read and its
  version never regresses. **Rides with:** feed 1. **Re-test:** `git grep -nE "intervalMs|cadence" -- webapp/lib/api/reads.ts`.
- **The webapp's read state**: a `/me` failure is reported twice, by the cache and by the auth catch,
  which treats a 401 as an incident; `read-cache.ts::dropCachedRead` on a mounted read clears it with no
  re-ask; `api/reads.ts::couponRead` has no validator, and evidence computes its ETag after the build;
  `channel-hooks.ts::usePickedPoint` reads null mid-scoring; `ChannelDetail.tsx` shows its pending copy
  on a failed read; `read-cache.ts`'s `syncBeat` is latent; the round in flight is spelled in
  `round-candidates.ts`, `search-point.ts` and `round-reading.ts`. Action: the change feed deletes the
  cadences; the rest get one owner each. **Rides with:** feed 1. **Re-test:**
  `git grep -n "dropCachedRead(" -- webapp/lib webapp/components`.
- **Read rows restated by hand**: `api/deps.py::read_input`'s pattern, `cli/parsers.py`' read help,
  `complexity_ledger.py::_TERMINAL_VIEW` and the hand-written URLs of `webapp/lib/api/reads.ts` each
  restate `ReadKind`'s rows, and the webapp re-asks reads by an untyped name. Action: each derives from
  the rows, and a read's name is a type. **Rides with:** feed 2. **Re-test:**
  `git grep -c '\`${API}/' -- webapp/lib/api/reads.ts`.
- **`ReadResult` literals are minted outside `useRead`**: a draft's pipeline in `useConnector.ts:53` and
  `OptimizerSetupSection.tsx:64`. Action: one constructor beside `useRead`, or `PipelineFlow` takes the
  data and its gap. **Rides with:** feed 2. **Re-test:** `git grep -n 'status: "ready", data' -- webapp/lib/hooks webapp/components`.
- **Off-screen view columns stay mounted and poll**: `shell/Views.tsx` mounts every open column, so a
  phone on the Dashboard preset runs every pane's reads at once. React's `<Activity mode="hidden">` per
  column, on one IntersectionObserver, stops them and keeps their state; a column swiped into is blank
  until its reads land. **Rides with:** feed 3.
- **A cold server folds every ledger before the first page answers**: the first `GET /hierarchy?depth=1`
  waits on a full `LEDGER_FOLDS` pass (37 µs a line cold, 2026-11-07), and `jobs/reaper.py::periodic_sweep`
  runs at boot beside it, about 10 s of it on L4 inner-sandbox ledgers no page reads. Root: pydantic
  validates each screened line on every cold start; a fold snapshot keyed by `file_sig` makes a restart
  pay only the tail. **Rides with:** feed 3.

### batch/23 · narr

- **U3: the ledger is narrated twice.** `views/readout.py::ReadoutProjection` (the readout log) and
  `domain/activity.py::ActivityFeed` (the chat) word the same record kinds their own way, each with its
  own hold dedupe and arm labels. Action: one narration table from record to `Said` with a lifetime: the
  readout log writes it as lines, the chat keeps the latest per lifetime, and the round tables render
  from fold state. **Rides with:** narr 1. **Re-test:** `git grep -c "AnnouncedArms" -- promptpotter`.
- **An activity row is a `Said` row under other names**: `domain/activity.py::ActivityItem` carries
  label, tone and detail, with kind glyphs beside the CSS tone glyphs. It reshapes the event contract
  (AsyncAPI) and its chat readers. **Rides with:** narr 1.
- **U3: one phase act is said three ways, and render text is banked on the ledger.**
  `run_records.py::PhaseRecord` carries a phase string, an untyped event string and a view whose kind
  repeats both; `InitEnterView` banks display text; `view_models.py::ViewContext` is a mutable bag a
  fork inherits. Action: the typed act is the record, text is computed at render, and `ViewContext`
  goes. **Rides with:** narr 2. **Re-test:** `git grep -n "class ViewContext" -- promptpotter`.
- **A pause by interrupt is served as a bare "Paused"**: the `run_phase` record holds
  `cause: "interrupt"`, and no surface names it. Action: a stop's cause is narrated and served on every
  door. **Rides with:** narr 2. **Re-test:** interrupt a running `new`; the readout and `hierarchy` name
  the interrupt.
- **An exhausted stale-data ladder prints its rerun line**: `render/sample.py` reads
  `PERSISTENTLY_DEGRADED` as `RETRIED`. Decided (2026-11-10): its word is "exhausted". **Rides with:**
  narr 2. **Re-test:** a persistently degraded cell's tape line says it exhausted the ladder.
- **`domain/phases.py::RunPhaseInfo.parked` is read only for a phase with an action**: QUEUED's,
  STARTING's and GATE's words are served and read by nothing. Action: the word lives only where it is
  read. **Rides with:** narr 2. **Re-test:** every non-empty `parked` row has a reader.

### batch/24 · stopb

- **A stop has two doors**: `declare_run_stop` appends to the bound ledger while
  `CampaignStore.declare_stop` serves admission and `declare_ended`, so the void is spelled at each stop
  site: `stores.py::Stores.void_inbox` has three callers outside the store, and the launch calls
  `command_log.py::CommandLog.void(..., before=)` itself (`run_observers.py::build_run_observers`).
  Action: `declare_stop` is the one stop door; it voids the inbox and takes the cut-off. **Rides with:**
  stopb 1. **Re-test:** `git grep -nE "void_inbox\(|commands\.void\(" -- promptpotter/application` lists four sites.
- **The store door says by a flag whether a record rides with `dashboard.json`**:
  `campaign_store/store.py::CampaignStore.append` takes `dashboard=True` from `mint_cycle` and
  `declare_stop`. Action: `append` derives it from the records it lands. **Rides with:** stopb 1.
  **Re-test:** `git grep -n "dashboard=True" -- promptpotter` is empty.
- **The reaper reads `SILENT` outside the producer lock**: a pause landing between
  `Stores.mark_producer_vanished`'s read and `declare_ended`'s lock is ended vanished. Action:
  `declare_ended` reads the owed run under the lock it holds. **Rides with:** stopb 2. **Re-test:** a
  pause filed between the read and the lock leaves the cycle paused.
- **A command whose filer died stays `filed`**: a killed verify reads `working` to every door and its
  task's result polls forever. Action: a command names its filer's OS lock (§0.5), and a dead filer's
  command reads ended. **Rides with:** stopb 3. **Re-test:** kill a verify mid-pass; its command reads ended.
- **`CommandDispatcher.run_state` delegates to `Stores.run_state`** (8 callers, one in `new.py`).
  Action: the callers read the stores'. **Rides with:** stopb 3. **Re-test:**
  `git grep -n "def run_state" -- promptpotter/application/commands/dispatcher.py` is empty.
- **`pause_superseded` is a second pause filer**: `commands/dispatcher.py::CommandDispatcher.pause_superseded`
  appends a PAUSE command and its ack beside `dispatch`. Action: the fork dispatches
  `PauseCyclePayload`. **Rides with:** stopb 3. **Re-test:** `git grep -c "def pause_superseded" -- promptpotter` is 0.
- **A destroyer takes no cycle claim** (bl2.B7): `campaign_store/store.py::CampaignStore.delete_campaign`
  checks for no attached producer, banks, then removes, with no admission gate or claim held, so a
  `start-run` admitted between the guard and the bank dies writing into a missing tree, unreported.
  Root: the destroyer takes `claim_launch` on each cycle under `JobRegistry.admission_gate`, in
  `application/commands/workspace_edits.py::campaign_lifecycle`, never `infrastructure/`. **Rides with:**
  stopb 4. **Re-test:** `git grep -n "claim_launch" -- promptpotter/application/commands/workspace_edits.py` names the destroyer's claim.
- **A tombstoned keepsake writes its lifecycle twice**: `CampaignStore.delete_campaign`'s
  `_set_lifecycle(campaign_id, "deleted", …)` repeats what `list_campaigns` serves off
  `WorkspaceBank.deleted`. Action: the second write goes. **Rides with:** stopb 4. **Re-test:**
  `git grep -n '_set_lifecycle(campaign_id, "deleted"' -- promptpotter` is empty.
- **A landing waits on every live producer on another commit, whether or not the ledger shape moved**,
  and on nothing else (`jobs/registry.py::JobRegistry.close_for_landing` reads the jobs holding spend):
  a server or read verb on other code holds no job, so `jobs/interlock.py` guards nothing against it,
  and a busy box may never land. Action: key it on a digest of the `CycleRecord` schema the code writes,
  held by every live process. **Rides with:** stopb 4. **Re-test:** a landing that moves no record shape
  refuses while a run is live.

### batch/24 · conn

- **An unregistered `backend_type` is a bare `KeyError` at thirteen `CONNECTORS.get` sites**; only
  `dataset_access.py::dataset_connector` refuses it typed, and lowercase is documented on
  `Connector.name`, enforced nowhere. Action: `CONNECTORS.get` / `Registry.get` refuses typed, naming
  the registered set; `_validate` refuses a non-lowercase name; callers take the Connector. **Rides
  with:** conn 1. **Re-test:** a dataset naming `backend_type: nope` answers 422 on `new`, `start-run`
  and the pipeline read.
- **A connector that reports no price cannot run under the default dollar cap**:
  `domain/campaign_config.py::_DEFAULT_BUDGET` gives every campaign a dollar ceiling, an n8n cell is
  unpriced (`connectors/n8n.py::_pipeline_declaration` serves no `spend_bound`), so
  `spend_book.py::SpendBook.refuse_unpriced` halts the first send after the mint. Action: a connector
  declares how its sends are bounded and reported; an unenforceable dollar axis is refused at the mint.
  **Rides with:** conn 2. **Re-test:** mint a `backend_type: n8n` dataset that does not declare
  `budget.usd: null`; it halts at round 0.
- **Every connector declares its cells the box's to pay** (`connectors/protocol.py:158 cell_payer = "host"`),
  so an own-key account past its coupon is refused any run with a backend, L4's in-process connector
  included. Action: `cell_payer` loses its `"host"` default; a connector that takes the account's key
  per call declares `"user"`. **Rides with:** conn 2. **Re-test:** an own-key account at a used-up
  coupon runs one TermNorm cell.
- **The payer rule: `quota.py::sends_on` raises `NoApiKeyError`** for a provider no key pays, so
  `set-limits` (`quota.py::change_budget`) refuses a limit over a missing key, and `provider_send` and
  harbor's `_AgentModel` raise their own. Action: `sends_on` is total (a no-key send's payer is `None`);
  `launch_refusal` refuses `NO_KEY`; `coupon.py::keyed_client` is the one raise at send. **Rides with:**
  conn 2. **Re-test:** `git grep -n "NoApiKeyError(" -- promptpotter/application/jobs`.
- **`connectors/protocol.py::InProcessWorkload.experiment` is a `Mapping`** that
  `runner/inner/tasks.py::parse_resolved` re-parses at each reader. Action: each connector declares its
  experiment model; the workload carries it parsed. **Rides with:** conn 2. **Re-test:**
  `git grep -n "parse_resolved(" -- promptpotter`.
- **A per-cell criterion naming a term no cell carries compiles** (`authored.py::scorer_of`) and refuses
  per cell at the run, though the draft holds its backend's terms (`compiler.py::campaign_terms`).
  Action: the check-in compiles it against them. **Rides with:** conn 3. **Re-test:** a scoring edit
  `correct - no_such_term` is refused.
- **Resuming a campaign takes its connector from today's dataset file**: `jobs/launcher/mint_and_start.py`
  (`ExistingCycle.backend_type`) and `wiring.py::bind_cycle_session` read the definition again, while
  the sends read `Campaign.connector`. Action: `init_services` takes the campaign's Connector; a
  mismatch with the definition is refused, naming both. **Rides with:** conn 4. **Re-test:** re-point a
  minted slug's `backend_type`; `start-run` refuses.

### batch/24 · doors

- **"Run the resolver now" lives three times**: webapp `useIngestFlow` (`advance`, `land`,
  `submitContext`), CLI `new.py::_hold_checkin`, `DraftFile.resolution_pending`. Action: the served flag
  alone. **Rides with:** doors 1. **Re-test:** `git grep -n "void runCheckin" -- webapp/lib/hooks/useIngestFlow.ts`
  is one line, under `resolution_pending`.
- **An upload's two halves are re-derived**: `mint_and_start.py::commit_upload_definition` re-derives
  `DraftPipeline.local` and `pipeline_resolve.py::draft_declared`, which `pipeline_resolve.py` already
  split. Action: `DraftPipeline` carries the campaign half beside `local`. **Rides with:** doors 2.
  **Re-test:** `git grep -n "draft_declared" -- promptpotter/application/jobs`.
- **`new --set` hand-maps its names onto `EditDraftPatch.config` paths** (`_SET_SCORING`,
  `_PATCH_KEY_OF`); `--set scoring.*` is held by no test. Action: `--set` takes the patch's dotted path,
  parsed over its schema. **Rides with:** doors 2. **Re-test:** `git grep -nE "_SET_SCORING|_PATCH_KEY_OF" -- promptpotter`.
- **A scoring edit takes `matcher` and `dials`, which the compiler's block refuses**, converted by
  `draft_patch.py::_folded_scoring` and `draft_campaign.py::draft_criterion`. Action: the compiler owns
  the pick's spelling, or the pick leaves `config`. **Rides with:** doors 2. **Re-test:**
  `git grep -n '"matcher"' -- promptpotter/application/datasets`.
- **The sign-in exchange-failure refusal stays in the router**: `presentation/api/routers/auth.py::callback`
  calls `account.record_refusal` and `health.is_broken` itself, and logs a warning beside the act.
  Action: split `health`'s account dependency; the callback calls one function and the log goes.
  **Rides with:** doors 3. **Re-test:** `git grep -n "record_refusal" -- promptpotter/presentation` is empty.
- **`GET /auth/me` writes**: `presentation/api/routers/reads.py:129` calls `routers/auth.py::arrive`, so a
  read opens an account. Action: the open box's principal arrives once, where identity resolves
  (`api/deps.py`), and no read writes. **Rides with:** doors 3. **Re-test:**
  `git grep -n "arrive(" -- promptpotter/presentation/api/routers/reads.py` is empty.
- **`application/notices.py::answer_operator` is a one-line ring wrapper** the layering rule forced.
  Decided (2026-11-10): it goes. **Rides with:** doors 3. **Re-test:** `git grep -n "answer_operator" -- promptpotter`
  is empty.

### batch/25 · init

- **Run init is five modules in the wrong order**: `runner/entry.py::run_optimization`'s preamble,
  `runner/run_prep.py::prepare_run`, `origin.py::establish_campaign_origin` and
  `initialization/loop_start.py::finalize_loop_state` write `session.state` from eight modules, and
  ORIGIN starts before INIT. Action: `open_cycle(hop)` in `runner/`, INIT then ORIGIN. **Rides with:**
  init 1. **Re-test:** `git grep -c "session.state.[a-z_]* =" -- promptpotter/application`.
- **`Session` / `LaunchState` is a staged builder modelled as a bag of Optionals**
  (`initialization/session.py`; ~62 guard or sentinel sites). Action: each init step returns the next
  typed stage. **Rides with:** init 1. **Re-test:** `git grep -c "require_scorer\|require_partition" -- promptpotter`.
- **A resume takes up to 72 s to be applied**, and each resume prints a fresh `✓ Initialized`. Action:
  attribute the wait by phase; a resume does not re-initialize. **Rides with:** init 1. **Re-test:** time
  `start-run` → its `applied` ack on a paused cycle.
- **Earned blocks re-survey every round**: `bench/node_context.py:461` calls `earned_blocks_for`, which
  builds its own `CampaignSurvey` (`intelligence/earned_blocks.py:148`). Action: run init hands it the
  one survey. **Rides with:** init 1. **Re-test:** `git grep -c "CampaignSurvey(" -- promptpotter` is 1.
- **Round 0 has two doors**: `origin.py::establish_campaign_origin` walks it before the cycle exists, and
  `bench/node_context.py::rewalk_origin` again for the gate and the repair; `origin.py::lock_criterion`
  changes the scorer's id, which keys the difficulty ruler. Action: round 0 is seated unmeasured,
  inherited (`origin.py::try_inherit_fork_origin`) or standing, and walked only inside the round loop;
  its close locks the criterion and re-opens the scorer-keyed state; the bench pass runs after the gate.
  **Rides with:** init 2. **Re-test:** `git grep -n "def establish_campaign_origin" -- promptpotter`.
- **The first launch pays a still-refusing backend twice**: `application/origin.py::establish_campaign_origin`
  re-scores after a transient abort. Action: one walk per launch. **Rides with:** init 2. **Re-test:**
  the `gated/` leg's first launch sends each cell once.
- **The bench pass runs on a launch the origin gate halts.** Action: the gate decides before the bench
  spends. **Rides with:** init 2. **Re-test:** the `gated/` leg's halted launches send no bench cell.
- **A scored but unhealthy origin still holds a live process**: `runner/origin_gate.py::run_origin_gate`
  holds a degraded origin, and its three answers repeat run verbs. Action: a degraded origin halts and
  the hold goes. **Rides with:** init 3. **Re-test:** a degraded origin's launch ends halted.
- **`capo/members.py::CapoInit.refuse_origin` reads a schema fact off answered rows.** Action: refuse on
  the pipeline schema at selection. **Rides with:** init 3. **Re-test:**
  `git grep -n "refuse_origin" -- promptpotter/application/optimizers/capo/members.py`.
- **A fork cut at round 0 keeps its origin's holes**: `bench/cycle.py::Cycle.reopen_origin` leaves round
  0 with no close, so `resume_and_fork/repair.py::rewalk_lost_origin` returns at once. Action: an
  inherited origin is re-walked when another launch measured its rows. **Rides with:** init 3.
  **Re-test:** `--fork-on-divergence` at round 0 of a gate-halted campaign, backend fixed, passes in one launch.
- **Core computes TermNorm's term index for every connector**, pushed by two calls (`Session.index_terms`,
  `loop_start.py`, `origin.py`). Action: one call where the session opens, handing the connector's
  session the bank. **Rides with:** init 3. **Re-test:** `git grep -n "index_terms" -- promptpotter`.
- **`--diag` mints a sibling cycle** (`commands/launching.py::_diag_target`) and
  `potter/generation_only.py` drives its rounds a second way. Action: an unmeasured mode on `RunStart`
  the round loop reads; both go. **Rides with:** init 4. **Re-test:** `git grep -n "def cut_diag_sibling" -- promptpotter`.
- **A diag sibling never leaves the comparison**: `commands/launching.py::start_run` launches it with
  `leaving=None`. Action: `--diag` as a mode, whose launch leaves. **Rides with:** init 4. **Re-test:**
  `git grep -n "leaving=None" -- promptpotter` is empty.
- **The test builders replay production by hand**: `tests/factories.py::seat` builds a cycle as run init
  does, and `tests/factories.py::close_round` replays the runner's append order. Action: they call
  `open_cycle` and the mint. **Rides with:** init 5. **Re-test:** `git grep -c "def seat(" -- tests/factories.py`.
- **`tests/test_scoring.py::_peer_cycle` stands a `Cycle` up by hand**, banking a C0 report run init
  cannot produce. Action: a backend whose answers produce the asserted origin, started through run init.
  **Rides with:** init 5. **Re-test:** `grep -n "_peer_cycle" tests/test_scoring.py`.
- **`tests/test_archive.py::test_a_resume_takes_the_origin_the_gate_halted_past_it_and_every_later_launch_reads_the_pass`
  walks zero cells**, so the halt passes on an empty origin. Action: hand it the origin's cells through
  the `offline` fixture. **Rides with:** init 5. **Re-test:** the test's dataset is non-empty.

### batch/25 · passb

- **Only the bench serves a can-run answer**: `domain/bench.py::BenchStatus.can_grade`, read by name in
  `BenchGradeAction.tsx` and `scripts/offline_run.py`; verify serves none. Action: one served `can_run`
  per pass kind. **Rides with:** passb 1. **Re-test:** `git grep -n "can_grade" -- promptpotter webapp scripts` is empty.
- **`JobsDock::passMark` / `passLabel` derive running/starting from `started_at === null`** (so does
  `AccountUsageTab`). Action: a served pass state. **Rides with:** passb 1. **Re-test:**
  `git grep -n "started_at === null" -- webapp/components`.
- **The held-out verb has three names**: `verify`, `bench`, `grade-bench` and "Grade on bench"; `bench`
  also collides with `application/bench/`. Action: "held-out" at all five doors. **Rides with:** passb 2.
  **Re-test:** `git grep -n "grade_bench" -- promptpotter webapp scripts` is empty.
- **`restate_export_bench` rewrites `export.json` outside the roster**: `campaign_store/store.py::restate_export_bench`
  (called from `scoring/passes.py:715`) patches the file after a later held-out pass. Action: the pass's
  `RunArtifacts` drain re-renders the export off the ledger's newest bench reading
  (`ledger_scan.py::scan_closed_bench`), so `write_export` has one caller. **Rides with:** passb 2.
  **Re-test:** `git grep -c "restate_export_bench" -- promptpotter` is 0.
- **A stopped paid pass exits 1 at the CLI, a noise-floor stop 130**:
  `presentation/cli/campaign_runner.py::main` maps `PassStoppedError` to exit 1, while
  `cli/commands/noise_floor.py::cmd_noise_floor` catches it itself. Action: `campaign_runner.py` answers
  it with the paused exit code; noise-floor's catch goes. **Rides with:** passb 3. **Re-test:**
  `git grep -cE "except PassStoppedError" -- promptpotter/presentation` is 1.
- **`line_run` reads a paused pass as selecting**: a pass with a pause asked reads `PAUSED`, so
  `campaign_result.py::line_run` takes its hold for the loop's. Action: the pass's hold is read by its
  bracket. **Rides with:** passb 3. **Re-test:** a bench pass with a pause asked reads `selecting=False`.
- **The first verify after a pause starts 38 s late, and no verify is ever `accepted`.** Action:
  attribute the wait by phase; the command acks `accepted` at admission. **Rides with:** passb 4.
  **Re-test:** on a paused cycle, time `command` → the `verify` phase's `enter` in the ledger.

### batch/25 · webb

- **A live arm and a filed arm arrive in two shapes** (`LiveCandidate` flat; `RoundView.arms[key].individual`),
  so derivations come in pairs, and "is round N filed" is asked three ways (`useRound.ts::isRoundUnfiled`,
  `round-candidates.ts::roundCandidates`, `search-point.ts::roundObserveTarget`). Action: one served arm
  shape carrying a filed/live state. **Rides with:** webb 1. **Re-test:**
  `git grep -nE "liveCandidate(SearchPoint|Verdicts|ObserveConfig)|round_axis.completed.includes" -- webapp/lib webapp/components`.
- **Adapters over a flat served shape**: `useCandidatesModel.ts` joins every source it imports; `.facts.`
  is opened at many sites; liveness is re-derived at each `phaseWalks(` site. Action: the served rows
  carry what the adapters join. **Rides with:** webb 1. **Re-test:** `git grep -c "\.facts\." -- webapp/components webapp/lib`.
- **A campaign's short label is parsed three ways**: `format.ts::shortId`, `ForestRows::shortOrigin`,
  `ids.ts::shortFamilyTail`; `EvidenceCharts.tsx:77` labels cells with `c.split("/").pop()`, so two
  datasets' cells share one label. Action: a served short label per subject and node. **Rides with:**
  webb 1. **Re-test:** `git grep -nE "shortId\(|shortOrigin\(|shortFamilyTail\(" -- webapp/components webapp/lib`.
- **The polled live dashboard re-serves every closed round every 2 s** (570 kB at 16 rounds,
  `domain/dashboard_rows.py::served_rounds`). Action: the live body serves what moves; a closed round is
  `…/rounds/{n}` fetched once; the per-round series stays merged server-side. **Rides with:** webb 1.
- **One subject**: the webapp's hash is not a `domain/cycle_paths.py::Subject` yet. Action: the hash
  takes the same grammar. **Rides with:** webb 2.
- **View state**: whatever names the viewed thing (subject, candidate, at, view keys, round pick, sample
  set) lives in the hash; a module store holds only transient state. **Rides with:** webb 2.
- **U5: one viewed searchpoint.** Three hosts (`searchpointCopyChoices(`) assemble a picked arm from
  several reads each, and the Leader and the inspector show one pick at once. Action (operator,
  2026-11-05): one searchpoint read and one rendering that hosts give identity only; the Leader always
  shows the leader; the pick (`workspace.tsx::pick`) renders once, where
  `ScoringInspector.tsx::ScoringInspector` stands; `useCandidatesModel`'s bag and
  `workspace.tsx::ViewedCycle` become the `Subject` and the served arm row; compare channels and their
  picks are hash state too. **Rides with:** webb 2. **Re-test:** `git grep -n "searchpointCopyChoices(" -- webapp`.
- **Several doors into one state**: the sample set is written through `setSelectionForSampleSet`
  (`state/selection.tsx`) from several surfaces, and `FileTree::onSelect` is drilled through its
  parents. Action: one sample-set door. **Rides with:** webb 2. **Re-test:**
  `git grep -n "setSelectionForSampleSet" -- webapp/components` lists three files.
- **Two sources for one question**: `wonOnTheta` reads `facts.elects_on`, `electedMetric` reads
  `dash.display_metric`. Action: one source for "elected on". **Rides with:** webb 2.
- **`showForest` / `expandedLanes` sit in `state/candidates.ts` and `view-memory.tsx::CampaignView`**,
  written together by `hooks/useLineage.ts`, whose name collides with `lib/lineage.tsx`. Decided: view
  memory alone owns both; the hook is renamed. **Rides with:** webb 2. **Re-test:**
  `git grep -nE "showForest|expandedLanes" -- webapp/lib`.
- **A channel's seed is a store latch written in render**: `compare/ChannelCard.tsx:35`'s
  `view.select(seed)` sets `lib/state/compare-selection.ts::ChannelView.seeded` during render. Action: the
  pick reads `picked ?? seed` until the operator picks; the flag and the render-phase write go. **Rides
  with:** webb 2. **Re-test:** `git grep -n "seeded" -- webapp/lib/state/compare-selection.ts webapp/components/compare/ChannelCard.tsx` is empty.
- **"Measured in N/M rounds" is counted in the browser** (`sample-set.ts::roundsCoveringSample`), and
  `fullyCovered` counts rounds with any selection. Action: served per-sample coverage. **Rides with:**
  webb 3. **Re-test:** `git grep -n "roundsCoveringSample\|fullyCovered" -- webapp`.
- **`hooks/useNestedPipelines.ts` walks the nest client-side** with its own `MAX_DEPTH`. Action: the
  pipeline read serves the whole nest and its depth. **Rides with:** webb 3. **Re-test:**
  `git grep -n "MAX_DEPTH" -- webapp/lib/hooks`.
- **`cycle_addressing.py::ActiveSessionResponse` serves two nullable ids for `CycleHop | None`.** Action:
  one nullable `hop`. **Rides with:** webb 3. **Re-test:** `git grep -n "class ActiveSessionResponse" -A4 -- promptpotter`.
- **`NodeConfigEditor`'s `schema` is a source prop two hosts fill from different reads**:
  `lib/types/connector.ts::NodeSchemaReading` is threaded through 11 files. Action: the editor reads
  `useConnector` at an identity prop. **Rides with:** webb 3. **Re-test:** `git grep -c "NodeSchemaReading" -- webapp`.

### batch/26 · money

- **Two carriers**: the run's `shared/run_scope.py::RunScope`, bound once where the cycle opens, carries
  the plan, the ledger and the sender; the member's node context yields a per-call context holding
  node, config, round and index only. **Rides with:** money 1.
- **Thirteen `ContextVar`s still stand beside `RunScope`**: `jobs/coupon.py` 1, `llm/send_pacing.py` 4,
  `llm/spend_book.py` 3, `llm/telemetry.py` 3, `shared/measurement_context.py` 2. Action: the eight
  sender vars and `bound_spend_book()` move onto `RunScope`, and `inner_share` takes its book from its
  scope; the per-call round and measured vars stay; telemetry's ride rec. **Rides with:** money 1.
  **Re-test:** `git grep -c "ContextVar(" -- promptpotter` over `coupon.py`, `send_pacing.py` and
  `spend_book.py` is 0.
- **S: spend is booked twice and folded many times.** `wallet_store.py::HeldCharge` and
  `wallet_store.py::BilledCharge` repeat `run_records.py::SpendHoldRecord` and
  `run_records.py::TokenUsageRecord`; `spend_book.py::SpendBook` counts through its ledger subscription
  and through admission; `spend_book.py::Admission` writes each home differently. Action: one charge
  value (hold id, key source, kind, model, provider, cost, rate, tokens, ledger, timestamp) the ledger
  records embed; the account takes each send once, at the root; one fold parameterized by subject and
  meter. `charges.jsonl` is a survivor: its migration maps each line one to one and keeps its timestamp.
  **Rides with:** money 2. **Re-test:** `git grep -n "class HeldCharge" -- promptpotter`.
- **The cycle share has two readers**: `last` reads `CycleShareRecord` from the cycle alone, while
  `spend_book` folds it through `scan_cycle_share`. Action: the book reads the share through `last`.
  **Rides with:** money 2. **Re-test:** `git grep -n "scan_cycle_share" -- promptpotter` is empty.
- **A reserved or failed send writes a bill nobody counts** (bl2.B8, B9, B11): `paid_call` admits off the
  gate and registers no hold; a failed part write leaves later parts uncharged; a never-sent attempt
  writes a zero bill on two homes. Action: the call's hold is an open charge, one charge record per
  send, and a never-sent close writes the closing line only. **Rides with:** money 2.
- **A lost mirror loses its money**: `infrastructure/llm/spend_book.py::Admission._write` stamps the
  nested record `mirrored=True` before carrying it to the root. Action: the root copy lands first.
  **Rides with:** money 2. **Re-test:** a root append that raises leaves the call counted on the account.
- **A cycle's fold does not read its line's root ledger**: the `CampaignCeilingRecord` sits on the root
  cycle's ledger, so `campaign_store/store.py::CampaignStore.armed_run_limits` joins it in at the served
  read and `run_limits` goes stale on `set-limits --max-usd`; one budget rule is spelled three times
  (the fold, `jobs/quota.py::declare_run_budget`, `llm/spend_book.py`). Action: a cycle's fold reads its
  line's root ledger; `armed_run_limits` goes. **Rides with:** money 2. **Re-test:**
  `git grep -n "armed_run_limits" -- promptpotter` is empty.
- **The listing folds the whole dashboard for `run_limits`**: `served_dashboard.py::binding_run_limits`
  takes one field off `fold_at`. Action: `run_limits` is its own small fold in `live_dashboard/projection.py`
  (the last `RunWiringRecord.run_limits`, each later `StandingBudgetRecord` laid over, seeded across a
  fork's prefix), and the row above's root-ledger read lands on it. **Rides with:** money 2. **Re-test:**
  `git grep -n -A6 "def binding_run_limits" -- promptpotter | grep -c fold_at` is 0.
- **A reserved cell's bill is on no ledger** (`Connector.holds_own_sends` writes no hold), so
  `infrastructure/llm/spend_book.py::unreported_on` can re-teach a book its sends but never the cell.
  Action: a hold record per reserved cell. **Rides with:** money 2. **Re-test:**
  `git grep -n -A28 "def reserved" -- promptpotter/infrastructure/llm/spend_book.py | grep -c emit_spend_hold` is 1.
- **A live run's billed spend has no carrier**: `quota.py::_billed_by` re-folds the cycle ledger on every
  wallet read. Action: the job carries it. **Rides with:** money 3. **Re-test:** `git grep -n "_billed_by" -- promptpotter`.
- **`coupon.py::keyed_client` folds the account's charges on each send** the box's key pays. Action: the
  run's book carries the remainder it was admitted against, folded once per run and advanced by
  appended lines. **Rides with:** money 3. **Re-test:** time one send on an account with 100 cycles.
- **A resumed cycle loses its unmetered coupon spend**: `spend_book.py::SpendBook.take_up` restores
  `off_coupon_*` but not `beside_*`. Action: `unreported_on` folds it. **Rides with:** money 3.
  **Re-test:** `git grep -n "beside_usd" -- promptpotter/infrastructure/llm/spend_book.py`.
- **A resumed cycle's prior metering counts as the box's** in its book's coupon reserve
  (`SpendBook.take_up` reads only unreported own-key holds): conservative, not a leak. Action: it
  survives a resume as the run's own. **Rides with:** money 3. **Re-test:** resume an own-key cycle and
  read its `off_coupon_usd`.
- **`LifetimeSpend` carries no token count**, so the coupon's token side shows left / face, never used.
  **Rides with:** money 3. **Re-test:** the Host allowance section with a token-bound grant.
- **The dashboard fold deep-copies a spend rollup per token record**: `live_dashboard/projection.py::_handle_token_usage`
  copies `SpendRollup` (`model_copy(deep=True)`) to stay copy-on-write, because
  `domain/spend.py::SpendRollup.bank` mutates in place. Action: a non-mutating `SpendRollup.banked(record)`
  the fold assigns. **Rides with:** money 3. **Re-test:**
  `git grep -c "deep=True" -- promptpotter/infrastructure/projections/live_dashboard` is 0.
- **The free tier's budget is both a config default and an admission bound**: `free_tier.yaml`
  `campaign.optimization.budget.usd` is laid by `application/free_tier.py::with_free_tier_layer` and read
  by `application/free_tier.py::tier_ceiling`. Action: one owner. **Rides with:** money 4. **Re-test:**
  `git grep -n "tier_ceiling\|with_free_tier_layer" -- promptpotter`.
- **A bench cell's plan reads the campaign ceiling it is not metered by**:
  `application/scoring/query_loop.py:639` calls `fits(..., beside=label.kind)`, which counts every side
  of `_room`. Action: `fits` skips the share's sides where the book does not bind the kind, as
  `refuse_unless_room` does (a re-key). **Rides with:** money 4. **Re-test:**
  `git grep -n "\.fits(" -- promptpotter/application/scoring/query_loop.py`.
- **The $0 price opens a backend session** (`application/head_to_head_price.py::price_head_to_head`
  through `jobs/launcher/head_to_head.py::dataset_arms`) only to resolve the node models and the
  partition. Action: a $0 price opens nothing. **Rides with:** money 4. **Re-test:** price a plan with
  the backend down.
- **The host is a claim marker, not the declared account**: `infrastructure/identity/default_claim.py::is_host_tenant_dir`
  compares a user id to the claim marker, while `HOST_ADMIN_EMAIL` is read only in `routers/auth.py`,
  so one email holds two accounts on the box. Action: the host is the account `HOST_ADMIN_EMAIL` names;
  what the marker owns is migrated to it at the landing (a SURVIVOR, decisions § 0011). **Rides with:**
  money 5. **Re-test:** `git grep -n "default_claim_marker" -- promptpotter/infrastructure/identity/default_claim.py` is empty.
- **`default_claim.py::is_host_tenant_dir` is named for a directory but is the user-id check.** Action:
  rename it to what it decides. **Rides with:** money 5. **Re-test:** `git grep -n "is_host_tenant_dir(" -- promptpotter`.
- **A campaign id the bank holds as deleted can be minted again**: `store/account_spend.py::WorkspaceBank.deleted`
  is permanent and keyed on the id, and `runner/campaign_ids.py::mint_campaign_id` draws
  `prefix__token_hex(3)` without asking it, so a campaign on a tombstoned id meters none of its own
  ledgers. Action: the mint re-draws an id the bank holds as deleted. **Rides with:** money 5.
  **Re-test:** `git grep -n "token_hex(3)" -- promptpotter/application/runner/campaign_ids.py` names the
  draw with no `WorkspaceBank.deleted` read beside it.
- **`launch_contended` is retryable but answers 409** (`quota.py::_contended`). Decided (2026-11-10): it
  serves `retry_after`. **Rides with:** money 5. **Re-test:** `git grep -n "launch_contended" -- promptpotter`.

### batch/26 · rk-opt

- **The L4 task prompt points at a deleted doc**: `research/datasets/promptpotter-self/task.md:102`
  names `docs/specs/l4-outer-loop.md` (deleted at batch/21), kept byte-equal so the next paid L4 run
  replays. Action: the operator notes leave the prompt body; what stays points at
  `docs/concepts/l4.md` § Cost and `roadmap.md` § L4 closure. **Rides with:** rk-opt (the re-key
  window). **Re-test:** `git grep -n "l4-outer-loop" -- research/datasets/promptpotter-self/task.md`
  is empty.
- **A checkpoint's inputs are untyped dicts**: `run_records.py::ResumeCheckpointRecord.inputs_ref` /
  `data` are built by hand and read back by string key in each replayer, so a renamed key surfaces only
  at resume as `replay_error:` (N1: `KeyError: 'crowned'`). Action: each checkpoint kind declares a typed
  inputs model (`RoundWinnerInputs`), parsed at the ledger read, so a missing key is the one
  unreadable-file refusal and an unknown kind refuses at load; the replayer's catch-all, its log and the
  `replay_error:` state go; the test's record writes through the production writer. **Rides with:**
  rk-opt 1. **Re-test:** `git grep -c -E 'inputs_ref\["' -- promptpotter`.
- **Each resume decision is spelled twice**, live and by a per-member replayer over untyped dicts
  (`potter/resume.py::_replay_round_winner`). Action: the live election and its replayer share one
  input assembly. **Rides with:** rk-opt 1. **Re-test:** `git grep -n "def _replay_round_winner" -- promptpotter`.
- **`RaceStandingRecord.paired_breakdown` is an untyped stat bag**: potter (`pobb/checks.py`) and capo
  write different keys, and `views/readout.py::on_race_standing` reads `"p_better"` with two defaults.
  Action: a typed per-prior model. **Rides with:** rk-opt 1. **Re-test:** `git grep -n '"p_better"' -- promptpotter`.
- **PoBB re-decides a round a pause cut, and decides it differently**: a resumed capo-pobb banks two
  eliminator decisions beside the first launch's, and its `p_best` moves on the same paired breakdown.
  Action: an interrupted round's eliminator decisions retract on re-entry, and its reading is the one
  the round opened on. **Rides with:** rk-opt 2. **Re-test:** drop `capo-pobb` from
  `scripts/offline_run.py::BOUNDARY_RESUMED`; its `resumed/` leg reads UNMOVED.
- **A re-entered round draws its order on the current ruler**: `optimizers/potter/members.py:212-217`
  orders the panel by `build_round_order` on `view.ruler`, which `bench/difficulty.py::ruler_clock` reads
  at its current version, so any fold between the opening and the re-entry moves its order, walk and θ.
  Root: the ruler stamp is banked at the round's opening (beside `RoundProposedRecord`), and a re-entered
  round seats it, as `DifficultyView.seat` does a banked round's. **Rides with:** rk-opt 2. **Re-test:**
  pause a lock-in round after its opening, fold a row into the ruler, resume: `build_round_order` and
  `rows(leader)` equal the live round's.
- **The optimizer prompt spells slots its own way**: `[#N]` / `#N` (`potter/dispatch/injections/`), while
  every surface prints `domain/sample.py::sample_tag`'s `#007`. Action: the prompt renders its slots
  through `sample_tag`. **Rides with:** rk-opt 3. **Re-test:**
  `git grep -nE "\[#\{|#\{" -- promptpotter/application/optimizers/potter` names no slot.
- **A refused L4 arm's identity is the floor's**: `potter/dispatch/layout.py::layout_levers` folds a
  breach into the floor, so `spawn.py::inner_cell_hop` reads the floor arm's campaign. Action: the
  identity reads the refusal. **Rides with:** rk-opt 3. **Re-test:** a breaching and a floor arm get
  different inner campaign ids.
- **An optimizer node's template rides beside its config**: `llm_call.py::run_optimizer_node` takes a
  filled `template` and an `LLMCallContext` whose `config` named it (`critique.py::distil_critique`,
  `firing.py::_run_transition`, `task_context.py::run_checkin`, `generate.py:133`), so the two can
  disagree. Action: the call context carries the running template its fill reads. **Rides with:**
  rk-opt 3. **Re-test:** `git grep -n "template=filled" -- promptpotter`.
- **An edited template's unknown slot is refused at dispatch, not at selection, and names no address**:
  `dispatch/injections/registry.py` validates what `dispatch/prompts.py::load_optimizer_prompt` loads,
  and `optimizer_manifest.py::_select` cannot reach potter's `injection_table()`. Action: a member hook
  names its known slots and selection calls it, refusing by config address. **Rides with:** rk-opt 3.
  **Re-test:** an overlay holding `{{typo}}` is refused at `new`, naming
  `optimization.nodes.l1_generate.config.instruction`.
- **Structured optimizer calls send the clamp's seed raw** (`bench/llm_call.py::_merged_config`) while
  `Calls.ask` draws one per call. Action: one seed rule, read off `domain/campaign.py::run_clamp`.
  **Rides with:** rk-opt 3.
- **`tests/test_identity.py` reaches the spawn's private `_laid_arm`** (`_l4_arm`, `_l4_laid`, the depth
  step). Action: the journey drives `_open_seat` over an offline inner campaign. **Rides with:** rk-opt 3.
  **Re-test:** `grep -n "_laid_arm" tests/test_identity.py` is empty.
- **A served prompt-field set is labelled in the browser**: `webapp/lib/prompt-fields.ts::PROMPT_FIELD`.
  Action: the served prompt-field row carries its label, and the constant goes; the ink maps
  (`sample-trajectory.ts::MOVEMENT_CLASS`, `PipelineFlow`'s `tone`) wait until a served word row carries
  its `Tone` (`domain/said.py`). **Rides with:** rk-opt 3. **Re-test:**
  `git grep -n "PROMPT_FIELD\|MOVEMENT_CLASS" -- webapp/lib`.
- **Potter's calls bypass the bench's call door**: the peers ask through `node_context.py::Calls`;
  potter builds each call by hand (`dispatch/facade.py::build_bundle`, then
  `llm_call.py::run_optimizer_node`) and handles one fault four ways, one silently under `graceful`;
  `escalation/firing.py::Layer` and `escalation/firing.py::TransitionResult` are a second member
  protocol; `dispatch/bundle.py::InjectionBundle` hand-copies run state for every renderer;
  `escalation/rules.py::_first_match` runs a fixed rule table; the fitness formula has a second spelling
  (`evaluators.py::resolve_cell_formula`). Action: `Calls.ask(prompt, reply=Model)` with one fault
  policy; renderers read a potter-owned view in a hashed module; the rules three match functions that
  keep the name `escalation/rules.py::EscalationEvent` stamps; one formula spelling. **Rides with:**
  rk-opt 4. **Re-test:** `git grep -n "class InjectionBundle" -- promptpotter`.
- **Wave C, the optimizers in one re-key batch**: `potter/l1/` → `l1.py`, `validators/` 7 → 2, peer
  optimizers' `prompts` + `state` into `members.py`, `pobb/` and `escalation/` flattened, and
  `runner/inner/tasks.InnerCells` moved into `domain/l4`. **Rides with:** rk-opt 4.
- **The catch-up pool**: `validators.py::CatchUps` and `nodes.py::NoCatchUps` declare one protocol with
  one implementation, and the round reading takes a lock-in's backfills at
  `runner/measurement.py::_catch_up`'s commit, a second door. Action: the bench owns the pool and the
  race exposes only its priors. **Rides with:** rk-opt 4. **Re-test:** `git grep -c "NoCatchUps" -- promptpotter`.
- **A locked-in leader's caught-up cells reach the scoreboard only at the round's close**:
  `runner/measurement.py::_walk_population` merges a prior's caught-up cells after `run_walks`, and
  `BACKFILL` is no arm role. Action: a caught-up cell is a recorded row of its prior's arm. **Rides
  with:** rk-opt 4. **Re-test:** in `test_scoring.py::test_an_arm_is_cut_only_on_evidence_that_it_trails`'s
  locked block, the leader's last `running` before the close counts the cells `rows(leader)` holds.
- **Two functions named `election_rows`**: `domain/origin_panel.py:242` returns the `CellSheet` an
  election reads; `optimizers/potter/dispatch/bundle.py:126` returns the ELECTIONS table's rows. Action:
  the dispatch one becomes `election_table_rows` (prompt-shaping). **Rides with:** rk-opt 4.
  **Re-test:** `git grep -n "def election_rows" -- promptpotter` names one.
- **Two word tables over one enum, three times** (`evidence/head_to_head.py::GUARD_STATE_INFO` beside its
  labels, `paired_reading.py::READING_STATE_KIND` beside its info, `origin_panel.py::ROUND_ADVANCE_STALL`
  beside its info), and `library.py::LibrarySnippet` flattens a cited-or-authored snippet into nullable
  fields. Action: one table each, its words outside the marked decision units; the snippet keeps its
  union. **Rides with:** rk-opt 4.
- **A re-read arm has two names**: `runner/measurement.py::ReferencePairs.of_round` names an individual by
  the slot an earlier round walked it in. **Rides with:** rk-opt 4. **Re-test:** a held leader's round-3
  pair names its round-3 slot.
- **The READING node → reader node** in `resolved_schemas.json`'s layout prose: prompt text, so a
  re-measure. **Rides with:** rk-opt 4.
- **Overlap and repair keep their own pass families**: `RoundCells.overlap` and `RoundRestatedRecord`
  carry their passes beside `VerificationPass`. Action: both ride the one record. **Rides with:** rk-opt 4.
  **Re-test:** `git grep -n "\.overlap\b" -- promptpotter` is empty.
- **Three bars for "is this θ lift real"**: `potter/escalation/state.py::_improved` reads
  `lift > current.se`, `potter/election.py::_lift` takes `hypot` of both arms', and
  `potter/dispatch/injections/panels.py::detectable_rows` takes `a.se * sqrt(2)`. Action: all three fold
  onto the predicate `scoring/selection.py::elect_round_winner` reads (one lift test on
  `AbilityReading`). It moves decisions and the optimizer prompt: an offline run and the re-key.
  **Rides with:** rk-opt 4. **Re-test:** `git grep -n -E "lift > current.se|sqrt\(2" -- promptpotter/application/optimizers/potter`.
- **The collapse gate cuts a hedging arm on noise**: `pobb/checks.py::check` →
  `domain/scoring.py::is_answer_collapsed` reads one repeated label over `n_min` answered cells as "no
  measurement". Action: collapse is judged against the parent's answers on the same cells, proven by an
  offline run. **Rides with:** rk-opt 4.
- **Which ruler `ab_replay` reads.** Decided (decision 21): after a formula edit, `ab` re-grades the
  record's own cells under the current scorer and folds δ in memory at each round's link; with the
  formula unchanged it equals today's fit. **Rides with:** rk-opt 5. **Re-test:**
  `git grep -ln "ab_replay" -- tests` names a test that edits the formula and re-grades.

### batch/26 · stream-rk

- **U3: the ledger read stack.** `RunCallbacks` is a second producer door beside `RunScope`'s ledger,
  and `live_dashboard/projection.py::DashboardCheckout` writes a `dashboard.json` only
  `scripts/offline_run.py` reads, folding a second, eager dashboard on every append. Action: the ledger
  is the one producer door (`RunCallbacks` goes); the checkout writes the `LedgerIndex` fold's value at
  `_AT_ONCE` and the debounce. **Rides with:** stream-rk 1: `optimizers/potter/generation_only.py` holds
  `ctx.callbacks`, a hashed unit, so the cut moves potter and capo-pobb. **Re-test:**
  `git grep -c "RunCallbacks" -- promptpotter` is 0.
- **The mint's wiring reads its own commit back**: `run_observers.py::declare_run_wiring` loads
  `campaign.json` for `defaults_drift`, so `jobs/mint.py::_mint_campaign` cannot write the campaign
  last. Action: the caller hands the `Campaign` it declares. **Rides with:** stream-rk 1. **Re-test:**
  `git grep -n "load_campaign(" -- promptpotter/application/run_observers.py` is empty.
- **`RunArtifacts` renders off a live `Session`**: `runner/output.py` reaches `session.state.scoring`
  and its partition's `demo`. Action: it takes the scorer and partition off the ledger
  (`PartitionRecord`, the declared formula), as `closed_rounds.py::campaign_scorer` does. **Rides
  with:** stream-rk 1. **Re-test:** `git grep -n "session.state" -- promptpotter/application/runner/output.py` is empty.

### batch/26 · rk-run

- **RS: one running set — which nodes a run reaches.** A which-nodes question reads the running set (the
  chain, its phase pipelines and the declared nodes off the chain, less the excluded); an order
  question reads the chain. Most readers ask the chain
  (`pipeline_schema.py::PipelineSchema.active_steps`), so a phase or off-chain node is missing from run
  init, the Start gate, potter's view, the price and fence and the scoring role questions. Owner: one
  `running` set, derived once on the resolved schema, the phases laid once by the optimizer's manifest,
  each door held by a test that fails when its fix is reverted. It moves what L4 decides (`l2_context`,
  `l3_plan` become L1 levers). **Rides with:** rk-run 1. **Re-test:** `git grep -c "active_steps" -- promptpotter`.
- **The check-in's pipeline step is TermNorm's for every connector**:
  `webapp/components/ingest/PipelineSetupSection.tsx` hard-codes `llm_only` and "Research + Match".
  Action: serve the declaration's named `pipelines` map and render the select from it; delete
  `LLM_ONLY`. **Rides with:** rk-run 1. **Re-test:** `git grep -n "LLM_ONLY" -- webapp/components` is empty.
- **`PipelineSetupSection.tsx` derives Mode's exclusions client-side** (`active_steps` plus
  `config.exclude_nodes`). Action: the running set serves each mode's exclusions. **Rides with:** rk-run 1.
  **Re-test:** `git grep -n "exclude_nodes" -- webapp/components/ingest`.
- **Two per-category tables in two modules**: `domain/phases.py::REFUSAL_STOPS` and
  `shared/errors.py::ERROR_IS_CHARGED` each key `ErrorCategory`. Action: one row per category holds
  every column (a re-key). **Rides with:** rk-run 2. **Re-test:**
  `git grep -nE "^(REFUSAL_STOPS|ERROR_IS_CHARGED)\b" -- promptpotter` names two tables.
- **Two owners, two values, for what a definition leaves unsaid**: `assets/datasets/defaults.yaml` against
  `connectors/termnorm.py` `available_models` / `default_node_config` and
  `draft_campaign.py::UPLOAD_BUDGET_ROUNDS` / `Connector.default_optimization`. Action: a connector's node
  template is laid under every definition naming it; connector-neutral campaign defaults are one file;
  the four upload-seeding `Connector` fields become one optional upload-defaults declaration whose
  presence is `serves_uploads`. **Rides with:** rk-run 3. **Re-test:**
  `git grep -nE "llm_only|param_keys" -- promptpotter/assets/datasets/defaults.yaml` is empty.
- **Three TermNorm defaults in core**: `connectors/__init__.py` `DEFAULT_CONNECTOR` and `shipped`, and
  `Settings.TERMNORM_URL` defaulting to loopback, so a box with no TermNorm lists it configured. Action:
  the upload connector derives from its declaration; no endpoint default; each connector declares its
  own config model. **Rides with:** rk-run 3. **Re-test:** `git grep -n "DEFAULT_CONNECTOR" -- promptpotter`.
- **A pipeline's `name` and `version` are parsed and read by nothing**:
  `pipeline_parsing.py::parse_pipeline_response` fills them off a declaration and only a debug log reads
  them. Action: drop both fields and the keys that feed them. **Rides with:** rk-run 3. **Re-test:**
  `git grep -nE "\"(name|version)\":" -- 'promptpotter/connectors/*.py'` is empty.
- **DSPy splits the model string for pricing twice**: `dspy_module.py` meters `inline_route(model)` while
  core's `BackendClient.priced_as` splits the same string; they disagree when a dataset sets `provider`.
  Action: the cell takes the pair core bound. **Rides with:** rk-run 3. **Re-test:**
  `git grep -n "inline_route" -- promptpotter/connectors/dspy_module.py` is empty.
- **`ConnectorInspector.tsx::SECURITY_DOC_URL` links § Connection security for every connector**, an
  in-process one too. Action: the link rides the served connector's wire kind. **Rides with:** rk-run 3.
  **Re-test:** `git grep -n "SECURITY_DOC_URL" -- webapp/components`.
- **`LLMResponse.cost_usd` omits a resend `_admitted_send` made that may have billed**, so harbor's
  `_AgentModel.cost_usd` can read below the book's. **Rides with:** rk-run 3. **Re-test:** one 5xx then
  an answer; the row's `cost_usd` against the ledger.
- **`scoring/evaluators.py::Evaluator` is one class for two contracts** (`per_sample` async vs
  `per_round` sync) held apart by runtime guards; its `Scope` collides with `data_root.Scope`. Action:
  two evaluator types. **Rides with:** rk-run 4. **Re-test:**
  `git grep -n "isawaitable\|iscoroutinefunction" -- promptpotter/application/scoring/evaluators.py`.
- **`domain/scoring.py::turn_scalars` overwrites `n_turns` with the count of a step named `n`**, and
  `isidentifier()` drops a step like `my-step` silently. Action: the per-step terms take a namespace the
  totals cannot meet. **Rides with:** rk-run 4. **Re-test:** a turn list with a step `n` keeps its total.
- **`infrastructure/llm/anthropic.py::AnthropicClient` cannot run a node on a current Claude model.**
  Decided (decision 10): it is deleted with its two `provider:` entries; Claude routes through
  OpenRouter. **Rides with:** rk-run 5. **Re-test:** `git grep -n "class AnthropicClient" -- promptpotter` is empty.
- **A head-to-head's arms do not share one optimizer model.** Decided: every arm on one model, each
  peer's swap a declared deviation. **Rides with:** rk-run 5. **Re-test:**
  `grep -n "model:" promptpotter/assets/optimizers/*/pipeline.yaml`.
- **An operator's authored arm is refused a field they held**: `bench/children.py::carried_writes`
  refuses every child a `prompt_fields_held` field. Decided (2026-11-10): the operator's arm writes it;
  `carried_writes` refuses children only. **Rides with:** rk-run 5.
- **Two node-config merges disagree**: `campaign_config.py::merge_config_layers` deep-merges campaign
  documents, while `pipeline_params.py::apply_node_overlay` merges one level, typed by schema. Decided
  (decision 19): the overlay rule — a new value replaces the old, only object-typed params merge their
  keys, a model changed without a route drops the old route — folded into `ParamLayers`. **Rides with:**
  rk-run 7. **Re-test:** `git grep -c "merge_config_layers" -- promptpotter` is 0 or holds no node rule.
- **A knob's layer below the edit is read by value, and a draft's names none**: `optimizer_setup` rows
  carry `unset`, and `pipeline_resolve.py::_knob_layers` names the dataset where the edit set the
  dataset's own value. Action: the edit layer reaches the knob-layer read, which names the last layer
  laid; `optimizer_setup` serves it. **Rides with:** rk-run 7. **Re-test:** an edit restating the
  dataset's value reads `campaign`.
- **A field cleared in the steer panel cannot drop a candidate's value**: a fork's seed only adds over
  the candidate it forks. Action: the steer sends `EditDraftPatch.reset`'s delete. **Rides with:**
  rk-run 7. **Re-test:** clear a field in `SteerForkPanel`; the fork launches without the candidate's value.
- **The knob Reset writes the declared value, so the draft stays "edited"**: `OptimizerSetupSection.tsx`
  lays `NodeKnobsPanel`'s patch as `optimization.nodes` alone, where `EditDraftPatch.reset` is the
  delete. **Rides with:** rk-run 7. **Re-test:** `git grep -n "set(k.declared)" -- webapp`.
- **An orphan `NodeCall` component sits in the openapi contract**: pydantic hoists it out of
  `PipelineParams`' custom schema, and the `p_served` baseline was raised for it. Action: the carrier's
  JSON schema names no `NodeCall`. **Rides with:** rk-run 7. **Re-test:**
  `git grep -c '"NodeCall"' -- docs/specs/openapi.generated.json` is 0.
- **The domain carrier raises a door's error**: `pipeline_params.py::PipelineParams.with_nodes` raises
  `PayloadInvalidError` (a 422) from internal calls. Action: the carrier raises its validation error and
  each door words it. **Rides with:** rk-run 7. **Re-test:**
  `git grep -n "PayloadInvalidError" -- promptpotter/domain/pipeline_params.py`.
- **The call-key rosters are read through a private name**: `domain/search_point.py:10` imports
  `pipeline_params._Roster` to derive `WHO_ANSWERS_KEYS`, `PARAM_FORBIDDEN_KEYS`, `PARAM_SCOPE_KEYS`
  and `CALL_CONFIG_KEYS`. Action: the four live beside `NodeCall`, their nine importers re-pointed.
  **Rides with:** rk-run 7. **Re-test:** `git grep -n "_Roster" -- promptpotter/domain/search_point.py`.
- **A `NodeCall` laid with `seed: null` equals one laid without it, and hashes apart**: pydantic equality
  reads the fields, while the digest dumps the laid keys with `exclude_unset`
  (`domain/pipeline_params.py:78`, `:95`). Root: a None-valued call key is unset at validation, so
  equality and the digest read one shape. A re-key if any shipped yaml lays a null key; the census
  greps. **Rides with:** rk-run 7. **Re-test:** a node laid `seed: null` and one laid without it hash alike.
- **`editable` cannot say "frozen" apart from "inside `permitted`"**: a non-editable enum cost lever
  (`provider`) still offers its permitted values, which fork-preview then refuses. Action: a served
  per-row pickable set for the account. **Rides with:** rk-run 7. **Re-test:** in the offline world, the
  `provider` row's `kind` is `enum` with `never_axis: cost_lever`.
- **A held prompt field fails every peer child, and `field` means two things.** Decided (decision 20):
  CAPO, GEPA and LEVI's whole-text rewrite lands in the slot the target pipeline declares, named by one
  knob `rewrites: prompt | <field>`; its preflight refuses a held slot and `rewritten` empties only open
  fields (`paper_templates.py:49`, `children.py:86-94`, `optimizers/__init__.py:169`). The L4 digests
  re-key. **Rides with:** rk-run 8. **Re-test:** `git grep -c "field: instruction" -- promptpotter/assets/optimizers`
  is 0.

### batch/26 · addr

- **A sibling's kind is parsed from its id**: `infrastructure/store/layout.py::sibling_kind` regexes root
  / fork / diag off the cycle id, read in `runner/output.py`, `hierarchy.py` and `views/view_models.py`.
  Action: every reader takes the kind the mint recorded; `sibling_kind` goes. **Rides with:** addr 1.
  **Re-test:** `git grep -n "sibling_kind" -- promptpotter` is empty.
- **`ids.ts::rootCycleId` regexes `_fork_` / `_diag_`** to give `useLineage::parentId` /
  `isInheritedSibling` what the served tree carries. Action: read parent and spawn off the tree. **Rides
  with:** addr 1. **Re-test:** `git grep -n "rootCycleId(" -- webapp/lib webapp/components`.
- **A workspace and a hop are re-derived from a path by parent counting**: `campaign_store/store.py`
  (the cycle's hop beside `cycle_dir.parent.parent`) and `store/account_trail.py`. Action: read the
  address from `Subject` / `CycleHop`. **Rides with:** addr 2. **Re-test:**
  `git grep -nE "\.parents\[[0-9]\]|\.parent\.parent" -- promptpotter` names none beside the data-layout
  sites (`account_trail.py:110`, `campaign_store/store.py:830`, `read_model.py:162`, `layout.py:240`,
  `archive_maintenance.py:57`).
- **Lineage spines key on `facts.individual` and on the origin**: the compare pane's spine builder
  resolves a subject by individual id across the whole tree (`hierarchy_subtree.py::_answering` picks
  one row per id) and merges a campaign's subjects into one spine, so a diverging branch drops out;
  `SearchpointPanels.tsx::buildSpines` walks `parent_ids[0]` the same way. Action: resolve by address,
  spines from the served `main_line`. **Rides with:** addr 3. **Re-test:** compare two branches of one
  campaign; both draw.
- **Drilled into an inner run, the masthead names the outer dataset**: `RunMasthead.tsx` reads the
  outer campaign's, and `useLeafDatasetName` reads a pipeline for a tag the tree holds. Action: the
  viewed dataset is the served subtree tag everywhere; the hook goes. **Rides with:** addr 3.
  **Re-test:** open an L4 inner run; the masthead and chat name one dataset.
- **The lineage subtree sorts every candidate per candidate**: `application/hierarchy_subtree.py::_earlier`,
  O(n² log n) per read. Action: "closer to" is folded once per candidate on the ledger. **Rides with:**
  addr 3. **Re-test:** `git grep -n "def _earlier" -- promptpotter/application/hierarchy_subtree.py` is empty.

### batch/26 · rk-dec

- **The crowning bar tests nothing a result can quote**: `application/scoring/selection.py::elect_round_winner`
  admits with no interval and no multiplicity correction, and `RoundResult.improved` reads any selection
  past round 0 as a promotion; at panel width no corrected exact verdict reaches α=0.05. Action: the bar
  tests a hypothesis a result can quote, chosen after separability is re-read on a tenant-ruler run.
  **Rides with:** rk-dec 2. **Re-test:** a tenant-ruler run's `index.json::final.rounds_to_separable`
  beside its `rounds_to_improved`.
- **The never / partly / always spread contradicts the headline beside it**:
  `application/scoring/measurement_log.py::_cycle_cells` pools every candidate the cycle ran, so
  `domain/cells.py::hit_spread` reads a sample only C0 saw as "never". Action: the spread reads the
  headline's own cells. **Rides with:** rk-dec 2.
- **"Calls held equal" has no axis to hold**: `domain/spend.py::Budget` meters usd, tokens, rounds and
  wall clock, while the roadmap, `paper/README.md` and `paper/plans/` each hold a different axis
  equal. Action: one matched axis, named on `Budget`. **Rides with:** rk-dec 2. **Re-test:**
  `price-head-to-head paper/plans/comparability.yaml` — do one point's `search_cells` differ by arm?
- **The election has no one-arm read**: `scoring/selection.py:177` fits a prior through
  `candidate_abilities({"read": rows}, …)`, a fake arm key. Action: a one-arm read on the election's fit.
  **Rides with:** rk-dec 2. **Re-test:** `git grep -n '{"read": ' -- promptpotter/application/scoring/selection.py`.
- **An L4 arm's model answers every inner node**: `runner/inner/spawn.py::_one_model` lays the first
  edit's model on every llm node. Action: an L4 arm can name a model per inner node. **Rides with:**
  rk-dec 3. **Re-test:** an arm naming a model on `l1_critique` alone answers it on that node only.

### batch/26 · rn-conn

- **One fact, two words: `backend_type` and `connector`**: the definition key `pipeline.backend_type`,
  `pipeline_resolve.py::draft_backend_type` and `DraftPipeline.backend_type` name what
  `Campaign.connector` names. Action: one word, `connector`, the definition key included; tenant
  definitions rewritten by a one-shot migration. **Rides with:** rn-conn 1. **Re-test:**
  `git grep -nw backend_type -- promptpotter` is empty.

### batch/27 · rec

- **One act on an arm writes two records**: `runner/arm_gate.py` and `scoring/query_loop.py` append an
  `InterventionRecord` and a `LeftComparisonRecord` for one act. Action: one act record both reads derive
  from. **Rides with:** rec 1. **Re-test:** `git grep -n "LeftComparisonRecord(" -- promptpotter`.
- **A deleted fork takes its `LeftComparisonRecord` with it**, so its arm reads `controlled` again while
  the fork's spend stays banked. Action: the exit outlives the fork. **Rides with:** rec 1. **Re-test:**
  delete a fork off an arm, then read the arm's `evidence`.
- **`record_decision` discards its offset**: `bench/resume_and_fork/decisions.py::record_decision`
  returns the outcome, so the fork's cut appends its own record. Action: it returns the offset the cut
  takes. **Rides with:** rec 1. **Re-test:**
  `git grep -c "ResumeCheckpointRecord(" -- promptpotter/application/bench/resume_and_fork/fork_siblings.py` is 0.
- **`domain/cycle_index.py::RunStatus.of` builds `mark: str` and asserts it a `StatusMark`.** Action:
  `StatusMark` typed off `RunPhase` and `StopOutcome`. **Rides with:** rec 2. **Re-test:**
  `git grep -n "mark: str" -- promptpotter/domain/cycle_index.py`.
- **`last` reads `forks_inherit` by `getattr` with a default**: three records declare it as a `ClassVar`,
  no record base does. Action: the ledger-record base declares it. **Rides with:** rec 2. **Re-test:**
  `git grep -n "getattr(record, \"forks_inherit\"" -- promptpotter` is empty.
- **`SampleScoredRecord.running` stamps a running fold on every cell** (9.3 % of a 1.5 MB ledger). Action:
  a fold beside `AnnouncedArms`, and the field goes (run data wiped). **Rides with:** rec 2.
- **A scoring cell beats as an LLM call**: `scoring/sample_measurement.py::_send_cell` and
  `search_point_scorer.py::_claim_cell` append `LLMCallProgressRecord`s with `node="backend_scoring"`,
  naming no arm. Action: a cell beat is its own record, keyed (arm, sample), with no node. **Rides
  with:** rec 2. **Re-test:** `git grep -n '"backend_scoring"' -- promptpotter` is empty.
- **`TokenUsageRecord.cached` and `key_source is None` are two spellings of one fact** (a replay no
  wallet paid). Action: one field. **Rides with:** rec 2. **Re-test:** `git grep -n "key_source=None" -- promptpotter`.
- **`domain/export.py` carries no optimization spend or tokens**, so a reader cannot compute amortized
  lifetime cost ([`../research/external-constraints.md`](../research/external-constraints.md) § Cost
  reporting). **Rides with:** rec 2. **Re-test:** `grep -n "spend\|token" promptpotter/domain/export.py`.
- **`Sample.key` re-hashes on every read** (16,781 calls per potter leg, `domain/sample.py::Sample.key`).
  Action: computed once at validation. **Rides with:** rec 2.
- **`infrastructure/llm/json_parse.py::try_groq_json_validate_repair` banks a missing count as an empty
  account.** Never estimate from content length. Action: `TokenUsageRecord` carries an unknown count.
  **Rides with:** rec 3. **Re-test:** a `provider: groq` in any dataset pipeline.

### batch/27 · la

- **CLI `--json` says a refusal on both streams**: `campaign_runner.py::main` prints the `Refusal` JSON to
  stdout and `Error: …` to stderr. Decided (2026-11-10): one stream. **Rides with:** la 1. **Re-test:**
  `python -m promptpotter resume --json --campaign nope 2>&1 >/dev/null` prints nothing.
- **Wave A — presentation and infrastructure, no digest re-key**: the CLI as five modules
  (`cli/campaign_runner.py` + `parsers.py` + `commands/verbs.py` → `cli/main.py`; small read verbs →
  `commands/reads.py`, measure verbs → `commands/measure.py`); `infrastructure/identity/` 12 → 3
  (`oidc`, `grants`, `accounts`); `projections/live_dashboard/` → one module; LLM wire types into
  `llm/base.py`. Close with `gate.py --only layering,help-imports,handler-tables,doc-claims,instruction-files`.
  **Rides with:** la 1-2.

### batch/27 · lw

- **Modules off `webapp/lib/CLAUDE.md`'s "a module lives by what it IS" rule**: to `state/` — `hard-samples`,
  `lineage`, `poll`, `workspace`, `scoring-mask`, `chat/thread`, `hooks/useConnector`'s provider; to
  `derivations/` — `cell-means`, `prompt-fields`. Chrome holding a surface: `shell/searchpoint/`,
  `shell/scoring/`, `shell/node-surface/`, root `PairedLift` / `SummaryBlock`; a surface holding chrome:
  `dashboard/pipeline/`. **Rides with:** lw 1. **Re-test:** `git ls-files ':(glob)webapp/lib/*.tsx'` lists none.
- **The derivations barrel reaches the network layer**: `derivations/connector-state.ts::nodeSchemaReading`
  imports `hooks/useRead::shownData`. Action: it takes the read's body, not the `ReadResult`. **Rides
  with:** lw 1. **Re-test:** `git grep -n "shownData\|read-cache" -- webapp/lib/derivations ':!*__tests__*'` is empty.
- **`lib/hooks/useRegistry.ts` and `lib/workspace.tsx` import `derivations/hierarchy` past the barrel** to
  dodge a cycle (barrel → search-point → poll → workspace). **Rides with:** lw 1.
- **`hooks/useLocalStorage.ts::STORAGE_KEYS` mixes hook and non-hook keys**, and `sidePanelKey`'s
  `side-panel:*` has no `promptpotter.` prefix. Action: one key table, every key prefixed. **Rides with:**
  lw 1. **Re-test:** `` git grep -n '`side-panel:' -- webapp/lib `` is empty.

### batch/28 · lb

- **Wave B — `application/runner/` collapses** to at most 8 files: loop, round, output, entry, bench,
  mint and inner/spawn. Delete the ledger-scan forwarders (`_spent`, `read_line_spend`); fold the
  optimizer registry (`optimizers/__init__.py`) and `knobs.py` into `optimizers/nodes.py`. **Rides
  with:** lb 1. **Re-test:** the `runner/` file count is 8 or fewer.

### batch/28 · faults

- **Q3: one fault channel — a fault is a value.** A failed or skipped step is a log line at many sites
  (engine decisions narrated into `logger.warning`, `origin_listing.py::list_origins` skipping an origin
  silently, `campaign_store/store.py::CampaignStore.list_campaigns` and
  `tenant_dataset_store.py::banks_sharing` logging an unreadable entry out of every list, `llm/heartbeat.py`
  a failed beat); until this lands, `application/`'s `logger.(warning|error|exception)(`
  count may not rise. Owner: inside a run, a typed fault record through the one producer door, read by a
  projection; inside a read, a field of the row it failed, served as its `Said`; `logging` leaves
  `application/` and `domain/`, banned at the gate as `gate.py::_IMPORTS_OPTIMIZER` bans an optimizer
  import. Invariant: the gate lists every `except` there that neither raises nor hands its fault on, and
  the list is empty.
  **Rides with:** faults 1. **Re-test:** `git grep -l "^import logging" -- promptpotter/application`.
- **`shared/errors.py::LedgerUnreadableError` stands beside `FileUnreadableError`.** Action: it folds into
  `FileUnreadableError` at every raise, catch and import (13 files). **Rides with:** faults 2.
- **The wound map has no home** (`docs/developer/self-healing-internals.md`, deleted at batch/21). Action:
  its two-axis table and `classify_result`'s two row rules become `domain/results_health.py::classify_result`'s
  docstring, from `git show d9012dc0e:docs/developer/self-healing-internals.md`. **Rides with:** faults.
  **Re-test:** `git grep -n "NurseOwner" -- promptpotter/domain/results_health.py` is non-empty.

### batch/29 · xconn

- **A backend wire skew is discovered as a 100% degraded origin, not refused at launch**: the box's
  TermNorm read `answer_field` while PromptPotter sent `answer_fields` (2026-10-31). Action: the
  connector's probe serves the wire contract it speaks, and the check-in refuses a node config that
  contract rejects. **Rides with:** xconn 1. **Re-test:** a launch against a backend one wire rename
  behind fails at the probe, naming the key.
- **A TermNorm schema edit lays keys only `llm_only` accepts**: `datasets/draft_patch.py::_authored_contract`
  writes `answer_fields` and `response_format: json` on whichever node the edit names, and
  `domain/pipeline_parsing.py::parse_pipeline_response` offers the `response_format` axis on every
  `tunes_llm` node; TermNorm's `xconn` branch refuses both on `entity_profiling` and `llm_ranking`
  (400 `wire_key_unknown`), and `top_p` on any node. Action: a node's served accepted keys bound what an
  edit lays and what an axis offers, refused at the mint. **Rides with:** xconn 1, before TermNorm's
  `xconn` reaches the box. **Re-test:** a check-in schema edit on `llm_ranking` mints and its first cell answers.
- **A connector's endpoint is one per install**: `config/settings.py` `TERMNORM_URL`, `N8N_HARNESS_URL`;
  a second workflow edits `.env` and re-keys every banked cell of the first (`n8n.py::INSTRUMENT_KEY`
  hashes the URL). Action: operator config holds named endpoints per connector, and a dataset names one;
  the credential never leaves operator config ([`connectors/CLAUDE.md`](../../promptpotter/connectors/CLAUDE.md)
  § The credential rides the connector). **Rides with:** xconn 2. **Re-test:** two n8n datasets on two
  workflows both run, no `.env` edit between them.
- **The TermNorm probe reads `/status`, and no cache keys on the backend revision**: TermNorm's `xconn`
  branch serves `GET /version` (`{revision, contract}`), while `connectors/termnorm.py` probes `/status`
  and `::_EXPECTED_REVISION` is `None`, so a co-owned backend fix replays stale results. Action: the
  probe reads `/version`, and the revision folds into the measurement-cache key. **Rides with:** xconn 3.
  **Re-test:** `connectors/termnorm.py` probes `/version` and `_EXPECTED_REVISION` is not `None`.
- **A moved capability or backend revision alone passes a resume**: it is a drift cause only when the
  origin hash also mismatches (`jobs/mint.py::resolve_resume`, `::_revision_moved`). Action: a resume
  refuses a moved capability or backend revision. **Rides with:** xconn 3. **Re-test:** a resume after a
  backend revision bump is refused naming it.
- **TermNorm words a warning's step into its message** (`research_pipeline.py`), though `StepWarning`
  carries the step as a field. Action: the message is the fault alone. **Rides with:** xconn 4.
  **Re-test:** `git grep -n "failed: {" -- backend-api/api` in the TermNorm checkout.
- **TermNorm serves `direct_prompt` as a node no step runs**: it parses, but `/pipeline` lists it as an
  llm `ranker` absent from `STEP_REGISTRY`, so PP reads its prompt fields as config axes. Action: in the
  TermNorm checkout, the block leaves `nodes` for a top-level `prompts` section. **Rides with:** xconn 4.
  **Re-test:** TermNorm's `pipeline.json` holds no `direct_prompt` under `nodes`.
- **`infrastructure/llm/send_pacing.py::SendBudget` stops at the relay's door**: TermNorm's
  `_MAX_ATTEMPTS`, and terminus-2's `_query_llm` thrice over `connectors/harbor.py::_AgentModel`, retry
  on their own. Patched singly, each fix is one more copy; the remedy is the upstream redesign. **Rides
  with:** xconn 4. **Re-test:** `grep -n "_MAX_ATTEMPTS = " <termnorm>/backend-api/core/llm_providers.py`.

### batch/29 · mkey

- **No inbound credential for the campaign surface**: `presentation/api/deps.py` resolves a session
  cookie alone; the one bearer opens only `/site-records`, so a third party needs `PROMPTPOTTER_AUTH=off`.
  Action: an account mints a named, scoped, revocable token (hash at rest, a SURVIVOR row of
  `store/data_root.py::KINDS`); a bearer resolves through the one caller dependency, a new auth door
  reviewed in `access-model.md` (`identity/bundle.py::identity_of_session` per carrier; `deps.py::open_caller`
  splits the rate draw from the `Caller` build, the seam mcp 2 rides); stable-api § 8 shows
  submit → poll → fetch with it. **Rides with:** mkey 1, 2. **Re-test:** a bearer reader besides
  `require_site_records_token` in `promptpotter/presentation/api/`.
- **The host's exemption ignores the door**: `jobs/quota.py::refuse_request_rate`
  exempts the host from every bucket, and auth-off makes every caller the host (`deps.py::resolve_identity`),
  so a leaked host bearer spends the box's keys unbounded. Action: the host is refused on a bearer door.
  **Rides with:** mkey 2. **Re-test:** a host-account token minted at mkey 1 is refused.

### batch/29 · mcp

- **A gate hold claims `input_required` and sends no input request**: `command_reading.py::job_status`
  sets the status; `tasks/result` blocks until the run ends. Action: the hold becomes the task's input
  request (the gate's `GateDecision`, the arms shape for `submit-arms`), answered as the existing
  command. **Rides with:** mcp 1. **Re-test:** a host calling `tasks/result` on a held origin gate
  receives an input request.
- **The MCP door speaks stdio only, so a browser chat cannot reach it**:
  `promptpotter/presentation/mcp/server.py::serve_stdio` is the one transport. Action: serve
  `promptpotter/presentation/mcp/server.py::host_server` over Streamable HTTP, mounted on the API behind
  its sign-in, a second transport of the one adapter, on mkey's bearer; its auth follows
  [`access-model.md`](../operations/access-model.md). **Rides with:** mcp 2. **Re-test:** in claude.ai,
  a custom connector at the box's MCP URL answers `tools/list`.
- **`main.py::API_ROUTERS` declares `APIRouter`s only**, so an ASGI mount (the webapp's `StaticFiles`,
  MCP next) sits outside the declared gate and `_off_catalogue`. Action: a mount is a declared row.
  **Rides with:** mcp 2. **Re-test:** `git grep -n "app.mount(" -- promptpotter/main.py` is empty.

### batch/30 · ws

- **`EvidenceCharts` and `CostStrip` lost their hover readouts** with the tooltip. Action: back through
  the chart tooltip primitive. **Rides with:** ws 1.
- **Two lineage layouts are derived side by side**: `webapp/components/candidates/dendrogram.ts::dendrogram`
  lays `DendrogramStrip`, and `webapp/components/candidates/forest-layout.ts::layout` lays `Forest`. The
  strip stays (operator, 2026-11-10). Action: one tree, two layouts, PAIRED and FREED, one button
  detaching and re-pairing it; one pick keyed on its `address` (`workspace.tsx::pick`) highlights both.
  **Rides with:** ws 2. **Re-test:** `git grep -hE "^export function (dendrogram|layout)\(" -- webapp/components/candidates`
  prints one line.
- **Compare cards are laid out for few wide columns**, not five thin stripes on a 16:9 display
  (`components/compare/ChannelCard.tsx`). **Rides with:** ws 3. **Re-test:** five candidates at
  1920×1080 without horizontal scroll.

### batch/30 · forks

- **Money text has no sub-cent rule**: a cell's price at five places (`preflight.py::_RoundShort.costing`,
  `cli/commands/head_to_head_price.py::_human`) beside `domain/spend.py::usd_text`, which prints `$0.0000`
  for a cheap model; `usd_text` and webapp `fmtUsd` are one rule; `run_standing.py` spells a third.
  Open (operator): the rule (significant digits under a dollar is one candidate). **Rides with:** forks 1.
  **Re-test:** `git grep -c -E '\$\{[^}]*:[,]?\.[0-9]f\}' -- promptpotter`.
- **An archived answer's outputs are read off today's backend**: `scoring/measurement_log.py::open_cell`
  reads the live declaration, because an `ArchiveEntry` records only `node_configs`. Action: a per-node
  output-key map filed with the answer; a replay reads the bank. **Rides with:** forks 2. **Re-test:**
  `git grep -n "dataset_pipeline(" -- promptpotter/application/scoring/measurement_log.py`.
- **`api_keys.json` holds account keys in plaintext at rest.** Action: encrypt under a box key, with its
  survivor migration. **Rides with:** forks 4. **Re-test:** `cat` one.

### batch/31 · tune

- **A fork cannot narrow a dead param axis**: `ForkProposal.unlock_schema_field_rename` is the sole config
  a rebase applies. Action: decide whether a narrowed `param_allowed_values` is fork-only, and if so a
  `ForkProposal` member mapped to one `ConfigOverrides` edit. **Rides with:** tune 1. **Re-test:**
  `git grep -n "class ForkProposal" -A20 -- promptpotter`.
- **The fastest hosts refuse the `l1_generate` schema**: Cerebras wants `properties` on every object, Groq
  strict mode wants every property `required` (`dispatch/l1_wire_schema.py::build_l1_response_schema`).
  Action: the wire goes all-required only together with `strict: True`. **Rides with:** tune 2.
  **Re-test:** one `l1_generate` call pinned to Cerebras parses.
- **The default host pin is a trial with no verdict**: `domain/pipeline_overlay.py::pin_default_routes`
  writes each model's default host into every node config. Decided: a model keeps its pin only where its
  realized cache share and effective $/M beat running uncached. **Rides with:** tune 3. **Re-test:**
  `SpendRollup.by_node` in each twin's `review.md`.

### batch/31 · small

- **No verb sets an account's coupon or starts it afresh**: `grant.json` is hand-written, and a reset is
  moving `charges.jsonl` aside; `wallet_store.py::CouponGrant.granted_at` is written and never read.
  **Rides with:** small 1.
- **Swapping a model means hand-editing eleven `model:` lines** (4+3+3+1). Action: a swap verb.
  **Rides with:** small 2. **Re-test:** one in `presentation/cli/commands/`.
- **Archive hygiene before the next bulk delete**: (1) reclaim, a dataset-scoped destructive `delete`,
  dry-run by default, refusing while a producer can append; (2) attribution, an L4 outer cell stamps the
  runs its inner campaign produced; (3) the reach map. **Rides with:** small 3. **Re-test:**
  `compact-archive inventory`.

### batch/31 · box

- **The box holds the app's environment twice**: the services load `/etc/promptpotter.env`, and the login
  user's CLI reads the checkout's `.env` through `config/paths.py::env_file_path`, so they drift. One
  file serves both; which one is the operator's (read access for the login user, or SELinux under
  `$HOME`). **Rides with:** box 1.
- **`promptpotter/__init__.py:4` sets `OPENBLAS_NUM_THREADS` at import**, a fact of the process, not the
  package. Action: the unit and the process launcher set it. **Rides with:** box 1. **Re-test:**
  `git grep -n "OPENBLAS" -- promptpotter` is empty.

### batch/32 · ts-arch

- **The treatment digest's edit probe patches `Path.read_text`**:
  `tests/test_identity.py::test_a_measurement_is_filed_under_every_input_that_moves_its_number_and_no_other`
  blocks keying `source_scan.py::prompt_source_digest` on a once-per-process tree hash (~250 ms per
  boot). Action: the probe writes the edit to a tree copy under a patched `_PACKAGE_ROOT`, then the tree
  key is process-cached. **Rides with:** ts-arch 3. **Re-test:** `git grep -n "read_text" -- tests/test_identity.py`.

### batch/32 · ts-score

- **The capo offline bench turns on one seed**: lift +0.700 or none after a draw-order change. Action: it
  reads two seeds. **Rides with:** ts-score 3.

### batch/32 · ren

Each rename lands with its doc and `CLAUDE.md` row in one commit. **Re-test:** the old word's
`git grep -c` for that sense is 0.

- **`candidate_library` → `answer_terms`**, and `PoolSource.LIBRARY` → `PACKS`: `EditDraftPatch`'s three
  `candidate_library*` fields and the dataset file move, and `shared/hashing.py::content_hash`'s key
  re-keys root ids. **Rides with:** ren 1.
- **"course" → "cycle" in the served and doc layer** (`HierarchyCourse`, `level: "course"`,
  `/hierarchy/{course}`, `ids.ts::courseAddressOf`). **Rides with:** ren 2.
- **"line" in its live-cycle sense → `live_cycle`** (`command_kinds.py::LinePayload`, `BenchLine.on_line`,
  `domain/bench.py::LineRun`): ledger shape. **Rides with:** ren 3.
- **`nodes.py::Panel` / served `ArmPanel` → `RoundSamples` / `ArmSamples`**, the L4 roster →
  `inner_panel`. **Rides with:** ren 4.
- **"slot"**: prompt slot → prompt field, layout slot → region, `domain/arms.py::ArmSlot` → `ArmPosition`,
  bank `slot_of` → `position_of`. **Rides with:** ren 5.
- **`CycleSeed*` / `ForkSeed` → `CycleStart*` / `ForkStart`**, so "seed" is the RNG seed alone.
  **Rides with:** ren 6.
- **The served hierarchy discriminator `level` → `entry_kind`**. **Rides with:** ren 7.

### batch/32 · rel

- **A renderer outside `views/` keeps an extra for one paper script**:
  `presentation/terminal/completion.py::report_completion` has one caller,
  `paper/bbeh-comparison/bbeh_potter_runner.py:38`, and alone keeps the `jupyter` extra. Action: pin the
  paper script to its release, then delete the module and the extra. **Rides with:** rel 1. **Re-test:**
  `git grep -n "terminal.completion" -- paper promptpotter` is empty.
- **The wheel's "install it elsewhere and run it" is written twice** (`.github/workflows/ci.yml:75-84`,
  `publish.yml:86-97`). Action: `scripts/smoke_wheel.py` takes the wheel and makes its own venv; each
  workflow calls it once. **Rides with:** rel 1. **Re-test:** `git grep -c 'RUNNER_TEMP/elsewhere' -- .github` is 0.
- **The example plugin's `resolved_schemas.json` is tracked, and no gate run writes it.** **Rides with:**
  rel 1. **Re-test:** edit it by hand; a gate is red.
- **`scripts/build_optimizer_schemas.py` names its outputs by absolute path.** Action: repo-relative
  wherever the output is in the checkout. **Rides with:** rel 1. **Re-test:** every regenerator prints
  `wrote|unchanged <repo-relative path>`.
- **The gate's `py` lane and "needs the pinned venv" are one flag**: `scripts/gate.py::_reexec_pinned`
  keys on `kind == "py"`. Action: one fact for the venv need, apart from the lane. **Rides with:** rel 2.
  **Re-test:** `python scripts/gate.py --py` lists `walk-world`.
- **The pinned venv is keyed on the raw `uv.lock`**: `scripts/gate.py::_install_digest` hashes the lock's
  bytes, so a worktree whose lock moved only outside the pinned extras (batch/22's mlflow exit) is
  refused a venv that `uv export --frozen` proves identical. Action: hash the resolved pinned set.
  **Rides with:** rel 2. **Re-test:** a lock edit outside `_PINNED_EXTRAS` leaves the digest unmoved.
- **The walk world has no outer/inner run, so the seat-cell drill never runs**: `scripts/gate.py::_walk_world`
  builds `offline_run.py --optimizer=potter --straight` alone, so `webapp/e2e/walk/cells.spec.ts:41`
  always skips. Action: the walk world carries an offline L4 leg, and the spec's skip becomes a
  failure. **Rides with:** rel 2. **Re-test:** `npx playwright test --project=walk e2e/walk/cells.spec.ts`
  passes 1, skips 0.
- **A stale walk world past `MAX_PATH` fails the gate**: `scripts/gate.py::_walk_world` prunes another
  key's world with a bare `shutil.rmtree`, and a world's `.runtime/.ledger.jsonl.lock` under the leg's
  `data/` root runs past 260 characters from a desk checkout, so the check raises `WinError 145`
  (batch/22's gate). Action: the gate spells its cache root in the long form `store/io.py` writes with.
  **Rides with:** rel 2. **Re-test:** prune a built world from a desk checkout; the check runs.
- **The dev loop still serializes on Vitest**: its pool fails a worker past its hard-coded 90 s start,
  which a host loaded from outside the gate lock still trips. **Rides with:** rel 2. **Re-test:** five
  builders plus one full gate, no reap.
- **`config/settings.py::_git_head` answers `UNVERSIONED` silently** for a ref in neither the loose refs
  nor `packed-refs`. Action: it refuses, naming the checkout it could not version. **Rides with:** rel 2.
  **Re-test:** a landing from a reftable clone names why.
- **The walk world keyed by its check's key has no test for the key-is-None path** (`scripts/gate.py`,
  `6381c24b9`: a world is never built or pruned under no key). Action: a test holds that path. **Rides
  with:** rel 2. **Re-test:** a gate test runs the `walk-world` check with no key.
- **`webapp/e2e/README.md` tells a reader to wait for a sibling gate**, a rule the gate itself should
  hold. Action: the gate holds it, and the sentence goes. **Rides with:** rel 2. **Re-test:** the README
  names no wait.
- **Unverified, for the next change to each file**: `shell/CriticalAlertBanner` builds `Said` rows in the
  browser; `ViewTabs.show` is many `open`/`close` calls, not one workspace act;
  `cycle_files.list_cycle_files` walks all of `.runtime/` and filters after; `FileTree` mounts a 2 s
  `useActivePointer()` poll for one failure line; a `/hierarchy` 200 replaces every entry object
  `lib/hooks/useRegistry.ts::useRegistry` indexes. The anti-rot pass re-finds them. **Rides with:** rel 3.

## Held — no wave-4 chain yet

- **The pipeline carrier is typed only at its door**: `NodeCall`'s readers go back through
  `.laid_keys()` (21 sites) and `.laid_nodes` (26) and read its own fields by string, among them
  `connectors/dbllmbench.py:158-249` and `connectors/harbor.py:431-437`. Action: read `NodeCall`'s
  fields. **Held:** the string reads span 14 files in seven chains' Owns, and none holds
  `dbllmbench.py`, `infrastructure/llm/openai_compat.py` or `projections/audit_trail.py`. **Re-test:**
  `git grep -cE "\.get\(\"(model|provider|route_order|temperature|max_tokens|reasoning_effort|top_p|seed)\"" -- promptpotter`.
- **A relabel over a mixed concept**: `domain/pipeline_manifest.py:43-52` strips `prompt_edits` from each
  pydantic `loc`, because `NodeConfig` nests the prompt fields the operator lays flat. Action: one
  layout for a node's prompt fields. **Held:** no chain's Owns holds `domain/pipeline_manifest.py` or
  `search_point.py::NodeConfig`. **Re-test:** `git grep -n "!= \"prompt_edits\"" -- promptpotter/domain/pipeline_manifest.py`.
- **A steer is typed, then sent untyped**: `commands/launching.py::_typed_steer` (`:133`) validates each
  node as `NodeCall` and keeps the dict it validated. Action: keep the typed carrier. **Held:** of
  `launching.py`, startb holds only the release lines and init only the diag; no chain holds
  `_typed_steer`. **Re-test:** read `_typed_steer`'s return.
- **A `derived_file_stale` warning names the projection's class, never the file it could not write**:
  `read_model.py::LedgerIndex.shielded` has only the `Projected` protocol, which carries no path. Action:
  a `derives` ClassVar on each `Projected`, which the warning names. **Held:** the derivers sit across
  narr, money, rec and no chain: `views/readout.py::ReadoutProjection`, `projections/cycle_index.py::CycleIndexProjection`,
  `scoring/closed_rounds.py::RoundFileProjection`, `llm/spend_book.py::SpendBook`, with
  `domain/run_records.py::stale_file_warning`. **Re-test:** a `dashboard.json` write fault's warning
  detail names `dashboard.json`.
- **Three data-root rows are proven by presence alone** (`store/data_root.py::_WALKED`): `rates.json`, the
  model capability override and the capabilities cache, whose readers (`llm/pricing.py`,
  `llm/capabilities.py`) walk a raw dict. Action: a model per file, named by its row. **Held:** lite holds
  `pricing.py` for LiteLLM's exit alone, and no chain holds `capabilities.py`. **Re-test:**
  `git grep -c "_WALKED" -- promptpotter/infrastructure/store/data_root.py` is 0.
- **Run data is stat'ed bare outside `store/io.py`**: `store/read_model.py::file_sig`, eight more
  `.stat()` sites and `measurement_archive.py::_named`'s `os.scandir` read a long path bare, so a ledger
  past `MAX_PATH` reads as absent. Root: the data root spelled once in its long form (`config/paths.py`),
  so every derived path carries it. **Held:** box holds `config/paths.py` for `env_file_path` alone, and
  the long form moves every derived path. **Re-test:** `git grep -lE "\.stat\(\)" -- promptpotter` names
  no file outside `store/io.py`.
