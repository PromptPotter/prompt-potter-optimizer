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

### batch/26 · admit-2 (links 1-5 landed; the rows its cap left)

- **A cycle's hold is released twice on every normal exit**: `runner/entry.py`'s `finally` releases a
  hold `drain_all` already released, so `run_phase_control.py::CycleHold.release` keeps a double-release
  guard. Action: `drain_all` only drains; the release is the `finally`'s and the fork handoff's. This
  keeps the parent cycle attached through the fork mint, which moves `account_spend`'s holds, so it
  needs its own proof. **Re-test:** `CycleHold.release` holds no already-released branch.
- **A seat refused before it runs keeps its observers**: `runner/inner/spawn.py` (~:364) never releases
  the observers it opened when `admission.py::slotless_share` refuses. Action: the observers open after
  admission, or close on its refusal. **Rides with:** the slotless-doors row below. **Re-test:** a test
  refuses a seat at `slotless_share` and finds no open observer.
- **The embedded and inner runs still open their cycles their own way**: `jobs/launcher/launch.py::hold`
  opens every held launch's cycle, but `embedded_run.py::run_campaign` and
  `runner/inner/spawn.py::_open_seat` mint or resume through `admission.py::slotless_share`, and the
  embedded resume opens its cycle before it admits (its campaign's own refusals,
  `admission.py::refuse_campaign`, are already asked first there). Action: a fork is opened under a
  carried admission, and the slotless doors open through the request kind's one `open`.
  **Rides with:** admit-2, whose Owns gain `runner/inner/spawn.py::_open_seat`. **Re-test:**
  `git grep -n "slotless_share(" -- promptpotter/application` lists no opener of its own.
- **A start's release is still hand-rolled at each start site**: `admission.py::RunStart.release` is the
  one release, but 12 `except BaseException: … release(exc)` blocks across `jobs/launcher/`,
  `commands/launching.py` and `embedded_run.py` call it by hand, and `jobs/launcher/head_to_head.py::AdmittedHeadToHead.launched`
  and `embedded_run.py::run_head_to_head` each release the arms not yet run after a failure (no arm set
  owns its starts). Action: the one `open_cycle` lifecycle owns the start's release; a context manager is
  the patch, not the root. **Rides with:** admit-2. **Re-test:** `git grep -c "release(exc" -- promptpotter/application`
  falls from 12.
- **A fork's cut lands before its admission**: `commands/launching.py` cuts the child
  (`cut_operator_fork`) before `_launch_fork` admits on the request's own hop, so a refusal leaves the
  parent's inert `FORK_CUT` line, because `jobs/quota.py::_new_campaign` counts an unresolved hop as a
  new campaign. Action: the daily count reads the request's campaign; the fork is admitted on the
  parent's hop, unresolved, and lands on the child. **Rides with:** admit-2. **Re-test:**
  `git grep -n "cut_operator_fork(\|admit(" -- promptpotter/application/commands/launching.py` lists the
  admission before the cut.
- **An arm is planned on a replicate it does not have yet**: `command_kinds.py::HeadToHeadRequest.purpose`
  declares replicate 1 and `jobs/mint.py::_numbered` overwrites it under the record hold; the placeholder
  reaches the root id, so admission prices plans whose ids are not final. Action: a purpose-free run spec
  feeds `resolve_cycle_plan`, `effective_config` and `shared_part`; the plan carries
  `Optimize | ArmSeat(head_to_head_id, arm_key)`, and the mint builds `Compare(Arm(…, replicate))` once.
  **Rides with:** admit-2. **Re-test:** `git grep -n "replicate=1" -- promptpotter`.
- **A mint takes no claim until `admission.py::claim_cycle`**: `mint()` has no job, and the L4 inner and
  embedded mints hold no claim at all, so the landing tells a live mint from a torn one by the job
  registry alone (`maintenance/landing.py::plan_landing` asks `maintenance/reset.py::holder_in_reach` of
  the mint's whole workspace: any live job there keeps every torn mint in it). Action: the mint claims
  its cycle before `adopt_ledger`, and the landing reads the claim, its act too: `apply_landing` keeps
  a claimed mint (today it asks a torn mint only its manifest; once a mint claims first, that is the
  read `destroying` would otherwise raise `CycleBusyError` on). **Rides with:** admit-2. **Re-test:**
  `git grep -n "unit.parent.parent" -- promptpotter/application/maintenance/landing.py` is empty.
- **The embedded launch is not a command**: `embedded_run.py::run_campaign` and `::run_head_to_head`
  open their own session but file no `CommandRecord`; `run_campaign` admits through
  `admission.py::slotless_share` and mints itself, because a DSPy `program` is an in-memory object no
  payload carries. Action: `run_campaign` files `mint-campaign` or `start-run` with an `EmbeddedRun`
  holder, the program held by the filing process. **Rides with:** admit-2. **Re-test:**
  `git grep -n "slotless_share(" -- promptpotter/application/embedded_run.py` is empty.
- **A queued launch dies with the asking process**: the queue wait runs in the caller
  (`jobs/launcher/launch.py`). Action (the operator, 2026-11-11): a queued launch is a job on disk; the
  job file holds the principal's id, never a credential, and `jobs/registry.py::claim_next`'s promoter
  re-checks admission and spawns it when the slot frees; `jobs/CLAUDE.md`'s rule ("a queued job's intent
  never leaves the process that authenticated it") is restated in the same link. **Rides with:**
  admit-2. **Re-test:** `resume --step-rounds 1` behind a running cycle; it starts when the slot frees.
- **A steer is typed, then sent untyped**: `commands/launching.py::_typed_steer` (`:108`) validates each
  node as `NodeCall` and keeps the dict it validated, because `domain/run_records.py::CycleSeed.pipeline_overlay`
  (`:326`) is `dict[str, Any]`. Action: type it `dict[str, NodeCall]` and keep the typed carrier.
  **Rides with:** admit-2, which holds `commands/launching.py`; its Owns gain `CycleSeed.pipeline_overlay`.
  **Re-test:** read `_typed_steer`'s return and `CycleSeed.pipeline_overlay`'s type.
- **The inner door mints its seat before its admission**: `runner/inner/spawn.py::_run_inner_campaign`
  calls `_open_seat` (`mint(` at `:335`) and only then `slotless_share` (`:367`) admits, so a refusal
  leaves a minted seat. Action: admit, then mint; the reorder moves the enclosing book's room and the L4
  equals-main, so it lands with a paid L4 re-measure. **Rides with:** admit-2, whose Owns hold
  `spawn.py::_open_seat`. **Re-test:** `git grep -n "mint(\|slotless_share(" -- promptpotter/application/runner/inner/spawn.py`
  lists the admission before the mint.
- **No test pins that a raising `run_campaign` leaves no open client**: `embedded_run.py::run_campaign`
  closes its own client on every exit (a `resolve_resume` refusal included), and nothing fails if it
  stops. Action: a test where the resume refuses and one where the run raises each read the client closed.
  **Rides with:** admit-2. **Re-test:** `git grep -n "aclose\|is_closed" -- tests` names both.
- **A closed `BackendClient` reopens its pool on next use**: `infrastructure/backend.py::BackendClient.http`
  (`:187`) builds a new `httpx.AsyncClient` when `_http.is_closed`, so a send after the run's close
  leaks a pool nobody closes. Action: a closed client refuses a send. **Rides with:** admit-2, whose Owns
  gain `infrastructure/backend.py::BackendClient.http`. **Re-test:** `git grep -n "is_closed" -- promptpotter/infrastructure/backend.py`
  is empty.
- **A non-launch command filed through `host_dispatcher` sweeps nowhere**: `embedded_run.py::host_dispatcher`
  (`:84`) enters without a sweep now, and `run_campaign` alone sweeps (`:192`), so a pause or stop filed
  by an embedding host reaps no dead job. Action: the sweep is the dispatcher's, for every kind.
  **Rides with:** admit-2. **Re-test:** `git grep -n "sweep(" -- promptpotter/application/embedded_run.py`
  names the dispatcher.
- **A host may file any kind its identity's capability allows**: `commands/dispatcher.py::CommandDispatcher.open` checks
  `kind.capability` and never `kind.press`, and `CommandCall` carries no door. `embedded_run.py` hands a
  host `CommandCall` and `host_dispatcher` under `default_identity`, which holds the owner's capabilities
  (`account.keys` among them), so a host can file `set-sample-lookahead`, `set-api-key` and
  `remove-api-key`, though `Press.host` is false on each. "The absence is the boundary" holds only at the
  doors that enumerate their kinds (`host_surface.py`). Action: a call names its door, and `open` refuses
  a kind whose `press` excludes that door. **Rides with:** life (every door moves through one
  transition). **Re-test:** a `set-api-key` call filed through `host_dispatcher` refuses.
- **`acompile` lands twice per compile**: `presentation/teleprompter.py::acompile` calls `enter()` for its
  stores, then `run_campaign(…, stores=stores)` enters again (`embedded_run.py:130`); every
  `embedded_run.py::enter` re-runs `land_at_entry`, and a landing that applies completes the registries
  twice (`landing.py::land_at_entry`, then `plan_landing`). Action: one landing per process, its
  registries completed once by the entry; `plan_landing` takes them complete. **Rides with:** admit-2,
  whose Owns gain `presentation/teleprompter.py::acompile` and `landing.py::plan_landing`.
  **Re-test:** `git grep -c "enter()" -- promptpotter/presentation/teleprompter.py` is 0, and
  `git grep -c "complete_registries(" -- promptpotter/application/maintenance/landing.py` is 1.
- **A retired knob refuses at the launch, not at the landing**: `store/dataset_access.py::definition_layer`
  checks only that each section is a mapping, so a definition still naming `halt_at_accuracy` lands
  and refuses at its next launch. Action: the landing validates each layer's `campaign` section against
  `CampaignConfig`'s keys. **Rides with:** admit-2, whose Owns gain `store/dataset_access.py`.
  **Re-test:** a definition naming `accuracy_ceiling` past `fitness knobs` refuses `land-data`.
- **Four launch faults from a code review (reported; verify each at its line first)**: `jobs/head_to_head.py:178`
  leaves a partial mint's arms unstamped, so a retry refuses `arm_taken`; `mint_and_start.py:108` and
  `checkin.py:93` leak the `BackendClient` they open when the mint is refused; `jobs/launch.py:144`
  runs `admit()`'s every-ledger sweep on the event loop where `head_to_head.py` offloads it; ties between
  launches broken on a random `job_id` can refuse two head-to-head arms with one stamp each other
  `launch_contended`. Action: one mint door owns the client and the stamp, and offloads the sweep.
  **Rides with:** admit-2.
- **Framing precedence is decided twice** (✓): `--task-text` beats `task_framing: off`
  (`jobs/mint.py:670`) while `campaign_framing` lets off beat everything (`task_context.py:197`), so a
  paid check-in runs and overwrites the dataset's committed framing (`task_context.py:287`) for every
  later campaign. Action: `_description_to_decompose` asks off first. **Rides with:** admit-2.
- **A head-to-head on a bank with no `dataset_split` runs blind** (✓): `jobs/mint.py:484-524`'s
  `arm_plans` ("every refusal it owes") never checks `partition.bench`, so the forced `at_end`
  (`:207`) is skipped (`loop.py:229`) and every arm spends its full budget reading "no rows held out".
  Action: `arm_plans` refuses it. **Rides with:** admit-2.
- **A draft opened from the CLI dead-ends** (reported): `open-dataset-draft` and `open-origin-draft`
  have no verb that edits, resolves or starts them; and the upload bound is spelled twice
  (`new.py:202-205` vs `deps.py:149-152`). Action: `new --draft ID`, and one bound. **Rides with:** admit-2.

### The edge tail · passb-2 (after batch/26; `chain N` is passb's link N)

- **Verify serves no can-run answer**: `domain/bench.py::BenchStatus.can_run` is the bench's, but
  `VerifyAction.tsx` gates its button on `busy` alone, and the refusals live only in
  `passes.verify_candidate`'s admission. Action: a served verify status (a `ReadKind` row, or a field on
  the candidate's served reading) carries `can_run` and a refusal, as `BenchStatus` does. **Rides with:**
  passb-2 1, whose Owns gain `VerifyAction.tsx` and the served read it rides. **Re-test:**
  `VerifyAction.tsx` reads `can_run`.
- **A pass's running state is derived from `started_at`**: `JobsDock::passMark` / `passLabel` and
  `AccountUsageTab` test `started_at === null`; the served fact lives only in
  `application/jobs/machine_status.py::MachineSlot.started_at`. Action: `MachineSlot` serves a stage
  (`starting | running`, off `Job.stage`). **Rides with:** passb-2 1, whose Owns gain `machine_status.py`.
  **Re-test:** `git grep -n "started_at === null" -- webapp/components` is empty.
- **The held-out verb has three names**: `verify`, `bench`, `grade-bench` and "Grade on bench"; `bench`
  also collides with `application/bench/`. It is spelled in `domain/command_kinds.py` (`grade-bench`,
  `measuring:grade_bench`, `anyone("bench")`), `cli/commands/bench.py`, `webapp/lib/api/commands.ts`,
  `domain/job.py::ScoringVerb`, `domain/campaign_config.py:421`,
  `infrastructure/projections/live_dashboard/projection.py:255`, `domain/bench.py:359` (`can_run`'s
  description) and `webapp/components/shell/searchpoint/BenchGradeAction.tsx:29` ("Grade on bench"). Action: "held-out" at
  all five doors, at once. **Rides with:** passb-2 2, whose Owns gain those files. **Re-test:**
  `git grep -n "grade_bench" -- promptpotter webapp scripts` is empty (8 hits in 5 files today).
- **`restate_export_bench` rewrites `export.json` outside the roster**: `campaign_store/store.py::restate_export_bench`
  (called from `scoring/passes.py:714`) patches the file after a later held-out pass. Action: the pass's
  `RunArtifacts` drain re-renders the export off the ledger's newest bench reading
  (`ledger_scan.py::scan_closed_bench`), so `write_export` has one caller. **Rides with:** passb-2 2.
  **Re-test:** `git grep -c "restate_export_bench" -- promptpotter` is 0.
- **A stopped paid pass exits 1 at the CLI, a noise-floor stop 130**:
  `presentation/cli/campaign_runner.py::main` maps `PassStoppedError` to exit 1, while
  `cli/commands/noise_floor.py::cmd_noise_floor` catches it itself. Action: `campaign_runner.py` answers
  it with the paused exit code; noise-floor's catch goes. **Rides with:** passb-2 3. **Re-test:**
  `git grep -lE "except PassStoppedError" -- promptpotter/presentation` names `campaign_runner.py` alone.
- **`line_run` reads a paused pass as selecting**: a pass with a pause asked reads `PAUSED`
  (`infrastructure/runtime_flags.py:88`), so `runner/bench.py::line_run` takes its hold for the loop's.
  Action: the pass's hold is read by its bracket (`producer_lock.pass_held`). **Rides with:** passb-2 3.
  **Re-test:** a bench pass with a pause asked reads `selecting=False`.
- **A bench pass's served delta is always zero**: `runner/bench.py::_bench_counted` (called at `:769`, `:780`
  inside `bench_origin`) reads the one `SpendRollup` before and after the pass, but `live_dashboard/projection.py:449` replaces
  `state.spend` on each bank, so the snapshot never moves. Action: the delta is folded off the pass's own
  charges, not two reads of one snapshot. **Rides with:** passb-2 3, whose Owns gain `_bench_counted`.
  **Re-test:** a bench pass that spends serves a nonzero delta.
- **The first verify after a pause starts 38 s late, and no verify is ever `accepted`**: `measuring.py:48`
  builds `Applier(_apply_verify)` without `ack="accepted"`, unlike `launching.py:476` and
  `dispatcher.py:161`. Action: attribute the wait by phase; the command acks `accepted` at admission.
  `accepted` is the loop inbox's status (`store/command_log.py:104,318,341,408`), so a pass acked
  `accepted` would read as a standing loop command. **Rides with:** passb-2 4, whose Owns gain `store/command_log.py` and
  `command_reading.py`'s status mapping. **Re-test:** on a paused cycle, time `command` → the `verify`
  phase's `enter` in the ledger.
- **The parity file mixes a served answer into the decision set**: `scripts/offline_run.py:1584` embeds
  the served `BenchScore` dump in `decisions.json`, so a served field rename (`can_grade` → `can_run`)
  reads as "decisions MOVED". Action: `decisions.json` holds decisions alone. **Rides with:** passb-2.
  **Re-test:** rename a served `BenchStatus` field; the offline run reads decisions UNMOVED.
- **A command log row is typed by no payload model**: `domain/command_events.py::CommandRecord` holds
  its payload untyped, so a row in a retired payload shape loads and refuses only when replayed.
  Typing it by `domain/command_kinds.py::PAYLOAD_MODEL_FOR_KIND` is blocked: `IngestUploadPayload.blob`
  is excluded from the record yet required, so no recorded upload validates. Action: a recorded shape
  per kind without its excluded fields. **Rides with:** passb-2, whose Owns gain
  `domain/command_kinds.py`. **Re-test:** a command log row in a retired payload shape loads.

### batch/26 · money

- **Two carriers**: the run's `shared/run_scope.py::RunScope`, bound once where the cycle opens, carries
  the plan, the ledger and the sender; the member's node context yields a per-call context holding
  node, config, round and index only. **Rides with:** money 1.
- **The campaign store reaches into the LLM layer for a ledger read**: `campaign_store/store.py:58`
  imports `standing_budget` from `infrastructure/llm/spend_book.py:80`, and
  `CampaignStore.read_standing_budget` answers it. Root: the read lives with the store, and the book takes
  it from there. **Rides with:** money 2 (one book); its Owns gain `campaign_store/store.py`'s
  `read_standing_budget` and its `spend_book` import line. **Re-test:**
  `git grep -n "spend_book import" -- promptpotter/infrastructure/store` is empty.
- **A tombstoned keepsake writes its lifecycle twice**: `campaign_store/store.py::CampaignStore.delete_campaign`'s
  `_set_lifecycle(campaign_id, "deleted", …)` repeats what `list_campaigns` serves off
  `WorkspaceBank.deleted`. Action: the second write goes. **Rides with:** money 5, which reads the
  tombstone; its Owns gain `delete_campaign`'s `_set_lifecycle` line. **Re-test:**
  `git grep -n '_set_lifecycle(campaign_id, "deleted"' -- promptpotter` is empty.
- **The default claim moves a tree a pass may be scoring**: `identity/default_claim.py::claim_default`
  reads `producer_attached` under the accounts lock, never the admission gate, so a registered pass on a
  terminal cycle is missed. `infrastructure/` may not import `admission.py` (`gate.py::_LAYERING`).
  Root: its caller, `application/account.py:256`, holds `admission.py::destroying` of each of the
  terminal's campaigns around the claim, and `claim_default`'s `producer_attached` read goes. **Rides
  with:** money 5, which rewrites the claim; its Owns gain `account.py`'s `claim_default` call (`:256`).
  **Re-test:** `git grep -n "producer_attached" -- promptpotter/infrastructure/identity` is empty, and a
  pass registered on a terminal cycle refuses the host's claim.
- **A campaign's absence is refused in eleven spellings and three error types**:
  `jobs/launcher/admission.py::campaign_at` loads and 404s, and the same load-and-refuse stands at
  `jobs/quota.py:781,859`, `campaign_store/store.py:262,326,526`, `mask/load.py:190`,
  `scoring/measurement_log.py:210`, `evidence/head_to_head.py:567`, `scoring/passes.py:637`
  (`BenchTriggerError`, unreachable: `campaign_sends` and the slot door 404 first) and
  `initialization/scoring_scope.py:147` (`DiagnosticError`); one resume loads its campaign five times.
  Action: `CampaignStore.campaign(id) -> Campaign` raises `NotFoundError`, every site reads it, and
  `campaign_at` goes (`admission.py` sits above `quota.py`, so it cannot be the owner). **Rides with:**
  money 2, whose Owns gain `CampaignStore.campaign`, `admission.py::campaign_at` with its callers'
  lines, and each site's load line. **Re-test:** `git grep -n "def campaign_at" -- promptpotter` is empty.
- **set-limits in a launch's admission window answers 500**: `jobs/quota.py::change_budget` (`:882`)
  reads `money_on(…).holds[job.job_id]` for the job `registry.py:384`'s `running_job_for` returns, and
  that is any `holds_spend` job, a STARTING one not yet admitted included (`job.py:185`), so the lookup
  raises KeyError. Root: the run lookup answers admitted runs only. **Rides with:** money.
  **Re-test:** set-limits between a launch and its admission answers the job's limits or a 409.
- **Four quota faults from a code review (reported; verify each at its line first)**: `quota.py:944`
  set-limits rewrites an own-key run's zero hold to the coupon headroom, so every other launch on the
  account reads held; `quota.py:538` a resume's reserve counts the campaign's past host spend in its
  headroom, overdrawing the coupon by that history; `quota.py::campaigns_today` counts resumes and forks
  against the fresh-mint daily quota; `quota.py::coupon_status` folds the bill twice and reaps under the
  gate on every GET. **Rides with:** money.
- **A run's limits are one record for two facts, restated whole by three writers** (overshoot's
  census): `RunLimits` is both `RunWiringRecord`'s persisted declaration and the served limits laid over
  it, so a served-only fact (the overshoot count) rides beside it as `ServedDashboard.overshot`;
  `StandingBudgetRecord` is restated whole by `change_budget`, `stand_held_budget` and
  `declare_round_allowance`, each carrying every other field forward (`stand_held_budget` drops
  `pause_at_round` so); `RunLimitsProjection` drops a standing record landing before the launch's wiring,
  so `declare_run_wiring` copies the standing switch for the served view to hold it. Root: the declared
  limits are one record, each change a patch of the fields it names, and the projection folds in order of
  arrival. **Rides with:** money. **Re-test:** a `stand_held_budget` keeps `pause_at_round`; a standing
  record before the wiring is served.

### batch/26 · rk-opt (links 1-4 landed; the rows its cap left)

- **capo's `population_kept` replays an absent arm as confirmed**: `optimizers/capo/members.py::_rank_survivors`
  reads an arm with no shared cells as a 0.0 mean, so a replay over a round that lacks the arm confirms
  it instead of answering it absent. Action: an arm with no shared cells is absent, as in the race's
  replay. **Re-test:** `pytest tests/test_archive.py -k replays_its_decisions` covers `population_kept`.
- **`tests/test_identity.py` reaches the spawn's private `_laid_arm`** (`_l4_arm`, `_l4_laid`, the depth
  step). Action: `_laid_arm` and `_one_model` ride `cfg`, and the journey drives `_open_seat` over an
  offline inner campaign. **Rides with:** rk-dec 3. **Re-test:** `grep -n "_laid_arm" tests/test_identity.py` is empty.
- **The browser inks a served state from its own map**: `webapp/lib/derivations/sample-trajectory.ts::MOVEMENT_CLASS`
  and `PipelineFlow`'s `tone`. Action: they go once a served word row carries its `Tone`
  (`domain/said.py`). **Re-test:** `git grep -n "MOVEMENT_CLASS" -- webapp/lib`.
- **Potter's renderers read a hand-copied snapshot**: potter asks through `node_context.py::Calls.answer`
  now, but `dispatch/facade.py::build_bundle` copies run state into `dispatch/bundle.py::InjectionBundle`
  for every renderer (35 fields, then a `replace` for `silent_l1_panels`);
  `escalation.py::Layer` and `escalation.py::TransitionResult` are a second member
  protocol; `escalation.py::_first_match` runs a fixed rule table; the fitness formula has a second
  spelling (`evaluators.py::resolve_cell_formula`). Action: renderers read the node's own reads through
  a potter-owned view, and the two pure-renderer tests (`test_identity`'s rendered-prompt journey,
  `test_security`'s poisoned-panel test) drive a node context; the rules three match functions that
  keep the name `escalation.py::EscalationEvent` stamps; one formula spelling. **Rides with:**
  rk-opt's next chain. **Re-test:** `git grep -n "class InjectionBundle" -- promptpotter`.
- **`InnerCells` sits in the runner, holding application types**: `runner/inner/tasks.py::InnerCells`
  holds `optimizer_manifest.py::SelectedOptimizer` per cell and reads its schema, manifest and levers,
  so it cannot move into `domain/l4` (layering) until a cell holds a domain projection of its
  optimizer. Action: decide what a cell's optimizer is in `domain/l4` terms (the chain, the levers,
  the measurement plan), then move `InnerCells` with it. **Rides with:** the `resolve_cells` row.
- **A locked-in leader's caught-up cells reach the scoreboard only at the round's close**:
  `runner/round.py::_walk_population` merges `CatchUpPool.caught` after `run_walks`, a catch-up walk
  records no pass (`BACKFILL` is no arm role), and `scoring/query_loop.py` still types the bench's
  one pool through `domain/validators.py::CatchUps`. Action: a caught-up cell is a recorded row of
  its prior's arm (its walk continues the prior's pass, `held` = the prior's rows), and the pool
  moves down beside `run_walks` so the protocol goes. **Rides with:** rk-opt's next chain. **Re-test:** in
  `test_scoring.py::test_an_arm_is_cut_only_on_evidence_that_it_trails`'s locked block, the leader's
  last `running` before the close counts the cells `rows(leader)` holds.
- **The catch-up pool's review (reported against 1a064d901; verify each at its line first)**:
  `runner/round.py:921` a kept budget cut banks catch-up cells the race never admitted, so the election
  reads a sheet PoBB never compared; `race.py:166` `admit` merges onto an existing prior where
  `register_completed` replaced it, and `Race.admit` is two doors (a decided arm, a catch-up) told apart
  by prior membership; capo `:463` and gepa `:369` carry duplicated no-op race halves; `round.py:945`
  `_unstarted` costs walks × samples × priors × cells per scheduler turn; `round.py:848` a dead
  disjunct. Action: one admit door per arm role, beside the row above. **Rides with:** rk-opt's next chain.
- **The reply cache keys a schema by its class name** (✓): `bench/llm_call.py:195` passes
  `response_model.__name__` into `hash_call`, while `openai_compat.py:503-504` sends
  `response_model.model_json_schema()`. The check-in's `checkin_output()` builds a different schema per
  chain shape, always named `CheckinOutput` (`task_context.py:187`), sent with no seed (`:261`) and
  messages carrying only the description (`:279-282`); the cache is the archive home's, shared across
  campaigns and tenants. So two chains with one description and different prompt fields, or any
  check-in after an edit to `CheckinOutput`, replay the old reply: hidden slots read non-empty, or the
  reply fails to parse. Action: one `ChatRequest.wire_schema` (the schema sent, titles stripped), which
  the client sends and `hash_call` keys on. Beside it (reported): the cache claims nothing on a miss
  (`llm_reuse_cache.py:110-133`), so concurrent identical misses each pay; a miss claims its key and a
  second asker waits on it. **Rides with:** rk-opt's next chain. **Re-test:** two check-ins over one
  description and different prompt fields miss each other's cache.
- **L1 is told the wrong number of rows to win** (✓, a statistics pass): `panels.py:1093-1101`'s
  `detectable_rows` takes its SE from `digest.ability`, the frontier pool (each sample's latest cell from
  any individual, a pool `cell_fold.py:76` says to "never score"), and doubles it with
  `hypot(a.se, a.se)`: SE 0.256 on 80 cells vs 0.495 on 20, so L1 reads "win ≈2 more rows" where 5 is
  right, at power 0.44, not 0.80. Root: the expected information over `latest_sample_keys` at the
  parent's own θ (`parent_own_rows` exists), for both sides of the contrast. Beside it (reported):
  `SampleIndex.refresh` re-folds the bank's whole archive history at every round close for compare arms
  and L4 cells (`sample.py:195-248`), and the local archive sits at 29.96 MB of `_CellFile`'s 32 MB cap.
  **Rides with:** rk-opt's next chain (potter's prompts move: a re-key).
- **Overlap and repair keep their own pass families**: `RoundCells.overlap` and `RoundRestatedRecord`
  carry their passes beside `VerificationPass`. Action: both ride the one record. **Held (design
  fork):** (a) each overlap top-up is a `VerificationPass` of a new `overlap` kind on the round, and
  `RoundResult.overlap_results` derives from them, while `RoundOutcome.overlap` (the `OverlapReading`,
  17 files) stays; or (b) the reading and its cells are one origin-panel record the round and its
  restatement carry, so `.overlap` goes whole. Both reshape `RoundStatement` (a wipe). **Re-test:**
  `git grep -n "\.overlap\b" -- promptpotter` is empty.
- **`ab` re-fits under the current scorer on the whole ruler** (decision 21, rk-opt 5). The tenant
  ruler at a round's reach holds other campaigns' rows, so `ab` regrades those foreign rows off the
  archive too, not only the record's own cells, and with the formula unchanged its fit equals today's
  (operator, 2026-11-11: option (b)). Only `ab`'s output moves. It needs an sp_hash → archive-entry
  lookup keyed by dataset hash. **Re-test:**
  `git grep -ln "ab_replay" -- tests` names a test that edits the formula and re-grades.
- **`resolve_cells` runs twice per run init**: `runner/inner/spawn.py::_inner_cells` (through
  `_identity_config`, `:53`, `:98`) and `runner/inner/tasks.py::_outer_cells` (`:59`) each parse the
  experiment and resolve the panel's cells. Action: the cells are resolved once, off the panel the
  workload holds, and both read them. **Rides with:** the rest of rk-opt link 4, whose `InnerCells`
  move into `domain/l4` names both files. **Re-test:** `git grep -n "resolve_cells(" -- promptpotter`
  lists one call per init.

### batch/26 · stream-rk

- **`RunArtifacts` renders off a live `Session`**: `runner/output.py` reaches `session.state.scoring`
  and its partition's `demo`. Action: it takes the scorer and partition off the ledger
  (`PartitionRecord`, the declared formula), as `closed_rounds.py::campaign_scorer` does. **Held:**
  the row below; until it lands, `campaign_scorer` grades a labelless cell unlike the run, and the
  export and `log.md` would take that. **Re-test:** `git grep -n "session.state" -- promptpotter/application/runner/output.py` is empty.
- **`campaign_scorer` grades a labelless cell unlike the run**: `scoring/closed_rounds.py::campaign_scorer`
  compiles with `verifier_graded=False`, the run with `all_verifier_graded(bank)`
  (`initialization/scoring_scope.py:64`). Under `rr(ground_truth_rank)` a `ground_truth=""` cell is
  UNSCORED live and fitness 0.0 here, and a backend ranking reads every cell `not_found`; the served
  round view (`cycle_reads.py`), hard samples (`measurement_log.py`) and evidence (`evidence/read.py`)
  read it. Reachable: `wiring.py:95` admits a labelless ranker. Action: rank is a cell fact
  (`backend_rank` and the matchers answer `None` where `facts.verifier_graded`), `ground_truth_rank`
  and `n_candidates` join `ground_truth` in `compiler.py::_refuse_label_formula`, and the flag gates
  refusal only. **Held:** no chain owns `scoring/` and `connectors/` together yet. **Re-test:** on a
  `ground_truth=""` cell under `rr(ground_truth_rank)`, `campaign_scorer` and the run's scorer agree.

### batch/26 · narr (links 1-2, on stream-rk's tip)

- **U3: the ledger is narrated twice.** `views/readout.py::ReadoutProjection` (the readout log) and
  `domain/activity.py::ActivityFeed` (the chat) word the same record kinds their own way, each with its
  own hold dedupe and arm labels. Their builders sit outside the views: `run_observers.py` and
  `scoring/passes.py` build `ReadoutProjection`, `projections/event_stream.py` streams `ActivityFeed`, and
  `round_buffer.py` and `arm_reading.py` hold `AnnouncedArms`. Action: one narration table from record to
  `Said` with a lifetime, owning those construction sites: the readout log writes it as lines, the chat
  keeps the latest per lifetime, and the round tables render from fold state. **Rides with:** narr 1.
  **Re-test:** `git grep -c "AnnouncedArms" -- promptpotter` is 0.
- **An activity row is a `Said` row under other names**: `domain/activity.py::ActivityItem` carries
  label, tone and detail, with kind glyphs beside the CSS tone glyphs. It reshapes the event contract
  (AsyncAPI) and its chat readers. **Rides with:** narr 1.
- **U3: one phase act is said three ways, and render text is banked on the ledger.**
  `run_records.py::PhaseRecord` carries a phase string, an untyped event string and a view whose kind
  repeats both; `InitEnterView` banks display text; `view_models.py::ViewContext` is a mutable bag a
  fork shares, held on `initialization/session.py:82` (`LaunchState.view_context`, reset at
  `runner/mint.py:101`) and read by `runner/mint.py:358,432`, `runner/round.py:1186,1222,1367` and 9
  `view_context.anchors()` calls, whose `ArmRecord.anchors` defaults to `ViewAnchors()`, so a writer
  that forgets it stamps another metric silently. The act's 25 `PhaseRecord(` writers sit in 7
  application modules (`runner/round.py`, `bench/node_context.py`, `origin.py`, `runner/bench.py`,
  `scoring/passes.py`, `run_phase_control.py`, `runner/mint.py`), some hashed. Action: the typed act
  is the record, text is computed at render, and the anchors fold from the launch's INIT record in the
  projection, so `ViewContext` and the field's default go; a re-key link. **Rides with:** narr 2.
  **Re-test:** `git grep -n "class ViewContext" -- promptpotter` is empty.
- **The run's ledger is reached three ways**: a threaded `ledger` (`RoundContext.ledger`,
  `RunObservers.ledger`, ~25 signatures that already take `cycle`), the run scope
  (`active_cycle_ledger()`/`bound_ledger()`, e.g. `runner/round.py::hold_for_operator` beside
  `operator_arms` in one chain, `scoring/passes.py::_bracketed`) and `session.state.observers.ledger`.
  They part where a probe render mutes the scope and the threaded handle still writes. Action: writers
  read the scope (or `Cycle.ledger`) and the `ledger` parameters go. **Rides with:** narr 2, which
  rewrites the same writers. **Re-test:** `git grep -c "ledger: CycleEventLog" -- promptpotter/application/runner` is 0.
- **A round's close is composed twice**: `runner/round.py::close_round` and `tests/factories.py`'s
  `close_round` each spell `stated_line` + `RoundClosedRecord.of`; the restate has one builder,
  `bench/node_context.py::restated_round`. Action: a `closed_round` builder beside it. **Rides with:**
  narr 2. **Re-test:** `git grep -c "RoundClosedRecord.of(" -- promptpotter tests` is 1.
- **The ledger banks dashboard views**: `domain/phase_views.py` holds 13 pre-rendered view classes,
  built in `application/views/ingress.py` through `ViewContext` and taken apart again in
  `views/render/ansi.py` and `projections/live_dashboard/projection.py`; `RoundCompleteView` repeats
  `RoundClosedRecord`, and `RunSpendView` copies the spend fields one by one. Action: the ledger keeps
  domain facts only and every renderer reads the records; the view classes go with `ViewContext`
  (the largest excision of the row above; a run-data reshape, wiped). **Rides with:** narr 2.
  **Re-test:** `git grep -cE "^class \w+View\b" -- promptpotter/domain/phase_views.py` is 0.
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
- **A cycle admission refuses still offers its launch acts**: `domain/phases.py::STOP_REASON_INFO`'s
  `next_acts` are per stop reason, so an archived campaign's halted or paused cycle, or one whose
  definition now names another connector or none, serves Resume, which admission refuses
  (`admission.py::campaign_refusal`); the bench's `can_run` already reads that answer (`BenchLine.refused`).
  Action: the typed phase act is served per cycle off the same answer and offers no launch act it
  refuses. `domain/phases.py` cannot import admission, so the answer is joined where the phase is
  projected (`application/hierarchy.py`, its two `HierarchyState.of` calls) and carried on
  `domain/hierarchy.py::HierarchyState`; `webapp/lib/derivations/run-phase.ts` reads it. **Rides with:**
  narr 2, whose Owns gain those two `hierarchy.py` hunks (money's and addr's files too).
  **Re-test:** archive a paused campaign; its served acts name no `start-run`.
- **No door but the browser reads a live dashboard** (reported): the CLI has no dashboard read verb,
  though `potter-run/SKILL.md:29,53` relies on one; the disk `dashboard.json` lacks `run_phase`,
  `admission`, `run_limits` and pause; `embedded_run.py:64-73` exports no read. Action: one served read
  every door reaches. **Rides with:** narr.
- **The live projections copy history per poll** (a hot-path pass, ✓ unless reported):
  `activity.py:205-207`'s `ActivityFeed.value()` is `copy.deepcopy(self)`, per 0.5 s tail poll per
  subscriber, every arm of every past round included; reported, `_cycle_cells` and `_campaign_edits` cost
  O((R·A)²) per request, `_Standing.value()` copies every round's cells, `snapshot_frame` re-folds per
  connect, a ray page re-folds per "older", and `InRunCells` is built twice per round. Root: a
  projection's value is its aggregate plus the live round, never a copy of its history. The same root
  serves the wire: the polled live dashboard re-serves every closed round on every frame (570 kB at 16
  rounds, `domain/dashboard_rows.py::served_rounds`); a browser stitch of `…/rounds/{n}` per round, or a
  new read, breaks R7, so the closed-round series' read is decided first, then every `dash.rounds` reader
  moves. **Rides with:** narr; webb-4's Owns gain `hooks/useOriginEvidence.ts`, `hooks/useVerify.ts` and
  the `dash.rounds` panes. **Re-test:** `git grep -nE "dash\??\.rounds" -- webapp/lib webapp/components`.

### batch/26 · rk-run

- **DSPy derives its cell's priced pair beside core's**: `dspy_module.py::_in_process_run` calls
  `pricing.py::priced_pair` off the payload's `params`, while core binds each node's pair once
  (`sample_measurement.py::priced_pairs`); they agree only while the payload's `provider` is `NodeCall`'s.
  Action: the cell takes the pair core bound. Fork: `InProcessRun` takes the bound pairs (every
  in-process connector's signature, three ignoring it), or the cell's `CellBilling` carries them.
  **Rides with:** rk-run, once the fork is picked. **Re-test:**
  `git grep -n "priced_pair" -- promptpotter/connectors/dspy_module.py` is empty.
- **A check-in cannot switch an upload between its connector's pipelines**: a draft edit has no chain
  key, so a mode outside the held chain is disabled (`PipelineSetupSection.tsx`), and
  `draft_build.py::_modes` ignores a definition's own `pipelines`. Action: a mode pick sets the draft's
  `pipelines.default`, its definition half. **Re-test:** pick TermNorm's `default` on an upload; the
  draft's `active_steps` is that chain.
- **`LLMResponse.cost_usd` omits a resend `_admitted_send` made that may have billed**, so harbor's
  `_AgentModel.cost_usd` can read below the book's. **Rides with:** rk-run 3. **Re-test:** one 5xx then
  an answer; the row's `cost_usd` against the ledger.
- **`domain/scoring.py::turn_scalars` overwrites `n_turns` with the count of a step named `n`**, and
  `isidentifier()` drops a step like `my-step` silently. Action: the per-step terms take a namespace the
  totals cannot meet. **Rides with:** rk-run 4. **Re-test:** a turn list with a step `n` keeps its total.
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
- **A field cleared in the steer panel cannot drop a candidate's value**: a fork's seed only adds over
  the candidate it forks. Action: the steer sends `EditDraftPatch.reset`'s delete. **Rides with:**
  rk-run 7. **Re-test:** clear a field in `SteerForkPanel`; the fork launches without the candidate's value.
- **The knob Reset writes the declared value, so the draft stays "edited"**: `OptimizerSetupSection.tsx`
  lays `NodeKnobsPanel`'s patch as `optimization.nodes` alone, where `EditDraftPatch.reset` is the
  delete. **Rides with:** rk-run 7. **Re-test:** `git grep -n "set(k.declared)" -- webapp`.
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
- **A relabel over a mixed concept**: `domain/pipeline_manifest.py:43-52` strips `prompt_edits` from each
  pydantic `loc`, because `NodeConfig` nests the prompt fields the operator lays flat. Action: one
  layout for a node's prompt fields. **Rides with:** rk-run 7 (one node-config merge, in the re-key
  window); its Owns gain `domain/pipeline_manifest.py` and `domain/search_point.py::NodeConfig`.
  **Re-test:** `git grep -n "!= \"prompt_edits\"" -- promptpotter/domain/pipeline_manifest.py`.
- **A held prompt field fails every peer child, and `field` means two things.** Decided (decision 20):
  CAPO, GEPA and LEVI's whole-text rewrite lands in the slot the target pipeline declares, named by one
  knob `rewrites: prompt | <field>`; its preflight refuses a held slot and `rewritten` empties only open
  fields (`paper_templates.py:49`, `children.py:86-94`, `optimizers/__init__.py:169`). The L4 digests
  re-key. **Rides with:** rk-run 8. **Re-test:** `git grep -c "field: instruction" -- promptpotter/assets/optimizers`
  is 0.
- **A check-in's cycle plan selects beside its landing's refusal**: `checkin.py::_land_draft` refuses
  each overlay before landing, then `resolve_cycle_plan` selects and refuses again; planning needs the
  landed `Session`. Action: a check-in plans its cycles off the draft before landing, and a campaign's
  cycle config carries its selection. **Rides with:** rk-run. **Re-test:** one `select_optimizer` call
  between a draft's Start and its first cycle plan.
- **`application/knobs.py::config_map` selects for itself** beside `pipeline_resolve.py::_Merge`'s
  `optimizer`, which its caller holds; it does not depend on landing. Action: `config_map` takes the
  caller's selection. **Rides with:** rk-run. **Re-test:**
  `git grep -n "select_optimizer(" -- promptpotter/application/knobs.py` is empty.
- **A per-cell criterion naming a term no cell carries compiles** (`authored.py::scorer_of`, called at
  the check-in by `datasets/draft_patch.py`) and refuses per cell at the run. Root: `compiler.py::compile_scorer`
  refuses a name no owner holds, `evaluators.py::cell_name_owners` beside the campaign's own owners (the
  connector's terms and report keys, the observations); a check-in-only list in `draft_patch.py` would
  be a second spelling of that roster. **Rides with:** rk-run 3 (the compiler is a hashed unit, so the
  refusal moves digests: the re-key window); its Owns gain `scoring/formula/compiler.py` and
  `datasets/draft_patch.py` (the `scorer_of` call). **Re-test:** a scoring edit `correct - no_such_term`
  is refused.
- **A scoring edit takes `matcher` and `dials`, which the compiler's block refuses**, converted by
  `draft_patch.py::_folded_scoring` and `draft_campaign.py::draft_criterion`. Action: the compiler owns
  the pick's spelling (`scoring/formula/`, a hashed unit, so the re-key window), or the pick leaves
  `config` (`domain/draft_edit.py`, `draft_build.py`, `IngestConversation.tsx`). **Rides with:** rk-run 4
  (the evaluator's declaration), beside the row above; its Owns gain `scoring/formula/`,
  `draft_patch.py::_folded_scoring`, `draft_campaign.py::draft_criterion` and
  `webapp/components/ingest/IngestConversation.tsx` (the pick's field). **Re-test:**
  `git grep -n '"matcher"' -- promptpotter/application/datasets`.
- **`connectors/protocol.py::InProcessWorkload.experiment` is a `Mapping`** that
  `runner/inner/tasks.py::parse_resolved` re-parses at each reader. Action: each connector declares its
  experiment model; the workload carries it parsed. **Rides with:** rk-run 3 (each connector declares
  what it runs); its Owns gain `connectors/protocol.py::InProcessWorkload` (xconn's file, named shared)
  and `runner/inner/tasks.py::parse_resolved` with its readers. **Re-test:**
  `git grep -n "parse_resolved(" -- promptpotter`.
- **`GateKind` keeps an `"origin"` no writer declares**: `domain/phases.py::GateKind`, since the origin
  gate's hold went. Action: `Literal["arms"]`. **Rides with:** the next run-data wipe (rk-run holds
  `domain/phases.py`). **Re-test:** `git grep -n "GateKind" -- promptpotter/domain/phases.py` names arms alone.
- **`STOP_REASON_INFO[ORIGIN_GATE].grades_selection` is `True` on a stop that grades nothing**: the gate
  decides before the bench spends, so a halted launch returns before `bench_line_origin`,
  `cycle.bench_passes` is `None` and `runner/entry.py:119-123` never grades; the served row
  (`types.generated.ts`) says it does. Its `next_step` is silent that the bench pass is owed to the resume
  that proceeds (the halted readout says only that nobody has asked for it; the design sentence lives in
  the CHANGELOG and `offline-run.md`). Action: `False`, and the `next_step` names the owed pass.
  **Rides with:** rk-run, which holds `domain/phases.py`. **Re-test:** `git grep -n "\"origin_gate\".*grades_selection: true" -- webapp/lib/api/types.generated.ts` is empty.
- **TermNorm serves no rewrite slot**: TermNorm's `GET /pipeline` `llm_only.prompt_info` lacks
  `rewrite_slot`. Action (TermNorm repo): serve `rewrite_slot: instruction`, then
  `connectors/termnorm.py`'s `prompt_info` template line goes. **Re-test:**
  `git grep -n "rewrite_slot" -- promptpotter/connectors/termnorm.py` is empty.

### batch/26 · payer (on rk-run's tip: the assembled one, beside rk-dec)

- **Every connector declares its cells the box's to pay**, the inner (L4) one included
  (`runner/inner/spawn.py:138`, `cell_payer="host"`), though each inner send is keyed per call
  (`bench/llm_call.py` → `coupon.py::keyed_client` → `paying_key`, the account's own key first). So
  `quota.py::sends_on` prices `BackendCells(…, "host")`, which draws the coupon, and an own-key account
  past its coupon is refused `host_allowance_exhausted` on `promptpotter-self`. Root: a connector whose
  every send is admitted at the send under `paying_key` declares no cell payer
  (`connectors/protocol.py::Connector.cell_payer: KeySource | None`), and `sends_on` lists the
  providers its sends draw. **Rides with:** payer 1 (who pays a connector's send), with harbor's raise
  below. Its Owns are every holder of the widened type: `connectors/protocol.py::Connector.cell_payer`
  (xconn's file after), `runner/inner/spawn.py:138`, `runner/mint.py:129`,
  `application/free_tier.py:75` (`free_fence`'s `task=` read),
  `infrastructure/backend.py`'s `cell_payer` reads (`:312,422,426`), the `paid_by=CONNECTOR.cell_payer`
  lines of `connectors/{harbor,dspy_module}.py`, `scoring/query_loop.py:627` (`self.payer`), the
  `paid_by: KeySource` consumers `llm/base.py:130` and `llm/registry.py:126,145`, and
  `jobs/quota.py::sends_on`. **Re-test:** an own-key account at a used-up coupon runs one
  `promptpotter-self` cell.
- **A connector raises its own `NoApiKeyError`**: `connectors/harbor.py::_AgentModel` checks its own
  `cell_payer` literal and is the third raiser beside `coupon.py::keyed_client`, because `keyed_client`
  lives in `application/`, where a connector cannot reach it. Action: a connector's send takes its key
  through the one door, and the raise is `keyed_client`'s alone. **Rides with:** payer 1, the row
  above's root; its Owns hold `connectors/harbor.py::_AgentModel`. **Re-test:**
  `git grep -n "NoApiKeyError(" -- promptpotter/connectors` is empty.

### batch/26 · addr

- **A sibling's kind is parsed from its id**: `infrastructure/store/layout.py::sibling_kind` regexes root
  / fork off the cycle id (`diag` is gone, a85862db3), read in `runner/output.py` and `hierarchy.py`;
  `views/view_models.py:87-88` no longer reads it. Action: every reader takes the kind the mint
  recorded; `sibling_kind` goes. **Rides with:** addr 1. **Re-test:**
  `git grep -n "sibling_kind" -- promptpotter` is empty (7 hits today).
- **`ids.ts::rootCycleId` regexes `_fork_`** to give `useCampaignForest.ts::parentId` /
  `isInheritedSibling` what the served tree carries. Action: read parent and spawn off the tree. **Rides
  with:** addr 1. **Re-test:** `git grep -n "rootCycleId(" -- webapp/lib webapp/components`.
- **A workspace and a hop are re-derived from a path by parent counting**: `campaign_store/store.py`
  (the cycle's hop beside `cycle_dir.parent.parent`) and `store/account_trail.py`. Action: read the
  address from `Subject` / `CycleHop`: the defect sites are `store/account_trail.py:114` and
  `campaign_store/store.py:407,835`. **Rides with:** addr 2. **Re-test:**
  `git grep -nE "\.parents\[[0-9]\]|\.parent\.parent" -- promptpotter` names none beside the
  data-layout and package-root sites (`read_model.py:181`, `layout.py:232`, `archive_maintenance.py:57`,
  `config/paths.py:9`, `store/source_scan.py:29`) and the landing's two (`maintenance/landing.py:256,380`);
  the allowlist holds none of the three defect sites. `landing.py:256`'s `unit.parent.parent` is also
  admit-2's re-test (`:64-66`), which wants it gone, so the two rows' allowlists conflict until admit-2
  lands.
- **Lineage spines key on `facts.individual` and on the origin**: the compare pane's spine builder
  resolves a subject by individual id across the whole tree (`hierarchy_subtree.py::_answering` picks
  one row per id; keys at `hierarchy_subtree.py:610-631`, `:620-622`) and merges a campaign's subjects
  into one spine, so a diverging branch drops out; `SearchpointPanels.tsx::buildSpines` walks
  `parent_ids[0]` the same way (`:238`, `:257`). The browser folds genealogy beside it, against
  `webapp/lib/CLAUDE.md`'s "key entries on `address`, never `facts.individual`":
  `lib/derivations/lineage-descendants.ts:3,11` keys the invalidation closure by `facts.individual`,
  which repeats across cycles, `lib/derivations/lineage-candidates.ts:107,123` keys on it too, and
  `components/compare/channel-hooks.ts:53,103` falls back to `mine[0]`. `webapp/CLAUDE.md` `:9` and `:33` now
  endorse `descendantsOf` (51231a4b5, which predates served lineage 52b76f719 and "the browser reads
  served facts" 0d8011635); the server now serves descent (`hierarchy_subtree.py::_descent`). Action:
  resolve by address, spines from the served `main_line`, invalidation from served descent;
  `descendantsOf` and its test go, and `webapp/CLAUDE.md` `:9` and `:33` stop endorsing it. **Rides
  with:** addr 3, whose Owns gain `webapp/CLAUDE.md` and `lineage-candidates.ts`. **Re-test:** compare
  two branches of one campaign; both draw, and `git grep -n "facts.individual" -- webapp/lib/derivations` is empty.
- **Drilled into an inner run, the masthead names the outer dataset**: `RunMasthead.tsx` reads the
  outer campaign's, and `useLeafDatasetName` reads a pipeline for a tag the tree holds. Action: the
  viewed dataset is the served subtree tag everywhere; the hook goes. **Rides with:** addr 3.
  **Re-test:** open an L4 inner run; the masthead and chat name one dataset.
- **The tree rewrites a retired arm's election**: `hierarchy_subtree.py` (`:551-555`) copies a retired
  candidate's reading with `selected=False`, `crown=None` and its `panel.cut`, against
  `store/CLAUDE.md`'s "the tree is a read model and decides nothing", though `arm_reading.py::arm_verdict`
  already takes `retired=`. Action: retirement and the cut land on the record that owns them and the
  served reading carries them; the copies go. **Rides with:** addr 3. **Re-test:**
  `git grep -c "model_copy" -- promptpotter/application/hierarchy_subtree.py` falls by three.
- **The lineage subtree sorts every candidate per candidate**: `application/hierarchy_subtree.py::_earlier`,
  O(n² log n) per read; reported beside it, the subtree read builds the whole top family per request,
  and `_descent` compares O(n²) pairs with `SequenceMatcher` behind an lru of 4096 that thrashes from
  ~90 candidates (`hierarchy_subtree.py:154-231`). Action: "closer to" and descent are folded once per
  candidate on the ledger. **Rides with:** addr 3. **Re-test:** `git grep -n "def _earlier" -- promptpotter/application/hierarchy_subtree.py` is empty.
- **The feed's version spans a whole store**: `GET /feed`'s tick (`application/cycle_reads.py::feed_frames`) runs
  `application/hierarchy.py::entries_version` over every store, per connected tab, and a campaign
  subtree's version (`hierarchy_subtree.py`) covers its whole store, so any run's append re-asks every
  mounted tree. Action: one version per subject, kept by the append door and the sweep, so no tick walks
  a store. **Rides with:** addr 2. **Re-test:** `git grep -n "entries_version(" -- promptpotter/application/cycle_reads.py` is empty.
  Beside it (a hot-path pass, ✓ unless reported): `read_model.py:224`'s `LEDGER_INDEX_MAX` = 256, so past
  256 cycles (one 20-round L4 campaign is ~240 inner campaigns) the feed's `_store_subjects` walk misses
  every tick and each miss re-folds with `model_validate_json` per line; reported, every `store:`-subject
  read re-runs `feed_versions` over all stores (`reads.py:214-232`), `read-cache` re-asks ~9 reads per
  sandbox frame, `machine_status` re-reads the job files four ways per tick (`list_running`, `queue_order` twice,
  `list_launched_today`; `machine_status.py:220,225,262`), `entries_version` parses every
  manifest, and archive-wide signatures serve as read versions (`evidence/read.py:551-554`,
  `measurement_log.py:271-280`), so any run's archive write invalidates every evidence and cells view.
  The same root: a writer-stamped version per subject.
- **A campaign's short label is parsed three ways**: `format.ts::shortId`, `ForestRows::shortOrigin`,
  `ids.ts::shortFamilyTail`; `EvidenceCharts.tsx:77` labels cells with `c.split("/").pop()`, so two
  datasets' cells share one label. Action: a served short label per subject and node, on
  `application/hierarchy.py` and `application/evidence/read.py`. **Rides with:** addr, whose Owns gain
  `format.ts::shortId`, `compare/EvidenceCharts.tsx` and `evidence/read.py`. **Re-test:**
  `git grep -nE "shortId\(|shortOrigin\(|shortFamilyTail\(" -- webapp/components webapp/lib`.

### holders (after the holds nest, beside passes)

- **Every hold names its holder its own way**: `Job.producer_id`, `LaunchClaimRecord.claimant_lock`,
  the cycle lock, a bill's ledger path (whose liveness is asked of the cycle lock), the send set and
  object identity; six readers ask "who holds X" (`admission.py::refuse_taken_cycle`,
  `maintenance/reset.py::holder_in_reach` and `::_claimed`, `identity/default_claim.py`,
  `runtime_flags.py::derive_run_state`, the shared-cycle refusals), and the bugs sit at the joins. The
  holds nest covers money's run, cell and call; the campaign share and the slot, claim and cycle side
  stay apart, though machine → account → campaign → cycle is one nest for slots and money alike.
  Action: one `Hold` (id, resource, parent, holder = the job id, bound, state asked | held | settled)
  and one `holder_of(resource)` fold with a death rule per kind; the six readers read it. The hot-path
  OS locks (archive cell claims, machine slots, draft claims, the gate) and the rate meters stay
  native; the cycle lock stays as the hold's kernel-backed index. ≈−0.9k lines. It closes, by their
  Re-tests, admit-2's rows on the double release, the refused seat's observers, the embedded and inner
  opens, the hand-rolled start releases, the fork cut before admission, the mint without a claim, the
  queued launch dying with its asker and the inner seat minted before admission; money's default claim
  moving a scored tree; passb-2's paused pass read as selecting and the command whose filer died.
  **Rides with:** holders.
- **Data the operator wipes is migrated**: `maintenance/migrations/` reshapes job files
  (`job_ending`, `job_refusal`, `retired_ending`: run data §0011 never migrates), authored data
  (`fitness_knobs`, `pack_tags`) and consent (`consent_trail`, `consent_by_principal`), and
  `store/data_root.py` keeps an account's consent trail, coupon grant, charges, uploads, authored
  datasets and block packs as SURVIVOR. Until the operator is no longer a solo developer, only who an
  account is survives: the user and its API keys (operator, 2026-11-12); the install's identity and
  configuration rows (`.env`, provider config, grants, the host) are the machine's and stay. Action:
  those account rows' fate is RUN, so a landing drops what no longer loads (each reader taking an
  absent file as a fresh account's); the seven migrations go with their `migrate.py` rows and
  `test_security.py`'s legs; §0011 names the narrowing and when it ends. **Rides with:** holders.
  **Re-test:** `migrate.py::MIGRATIONS` holds only `grant ceiling` and `host account`.
- **Nothing is ever fsynced**: `store/io.py`'s atomic write and `write_bytes` run mkstemp → write →
  `os.replace` with no fsync of the file or its directory, and the ledger and bill appends flush
  without one. A process crash is covered (the replace is atomic; a torn line is cut); a kernel crash
  or power loss is not: a replaced file can come back empty and the last appended lines vanish. An
  empty grants file refuses the server's start as corrupt; a lost bill line under-charges. Action:
  durability is a column on `data_root.py`'s kinds: a SURVIVOR write fsyncs the file before the
  replace and the directory after it, and the bill appends fsync; run data stays fast. **Rides with:**
  holders. **Re-test:** a SURVIVOR kind's write path calls fsync on file and directory.
- **A crash between a gate and its fact wedges or loses it** (a static crash-point pass, ✓ where
  re-read at the line): ✓ `jobs/launcher/admission.py:121-125` stamps the job released before
  `release_run` (`:129`) closes the account hold, and the reaper skips released jobs (`registry.py:448`),
  so a crash between them holds headroom open for good; ✓ `identity/default_claim.py:40` renames the
  account directory before `:44-47` rewrites its `user.json`, so a crash leaves `user_id="default"`
  and every later host sign-in refuses "both exist"; ✓ `spend_book.py:713` books a settle's Bills one at
  a time before the ledger (`:718-719`), so a crash leaves parts unbilled (reported: the first Bill
  already closed the hold, `read_model.py:945`) or the campaign under-metered; ✓ `grants.py:109→120`,
  `:136→137` (reported: `blocklist.py:97→98`, `:111→112`) write the effect before its audit line.
  Action: append the idempotent fact first and stamp the gate last (the `Release` before the job's
  released stamp); `user.json` rewritten inside the directory before the rename, so the rename commits;
  one `append_jsonl(path, *rows)` per fact (`io.py:533`) and billed money metered off the charge book;
  the intent line first, as consent does (`account.py:173`). **Rides with:** holders.
- **A cell claim's release deletes the next holder's row** (✓, model-checked):
  `measurement_archive.py:609-614` drops the claim row with no held-check, so `publish` releasing an
  errored cell early and the walk's `finally` releasing again deletes the next holder's live row, and a
  waiter buys the cell again. Beside it (reported): a waiter files a look-ahead row its holder later
  discarded at a cut, and a third walk pays the cell twice. Decided (orchestrator, the model's option
  (a)): the holder files the row at publish, and `CLAIM_ROW` / `claimed_cell` go, so every modelled
  invariant holds and the first fault goes with them; a cell past a cut is banked, already paid.
  **Rides with:** holders. **Re-test:** a cell claim has no row a release deletes;
  `git grep -c "CLAIM_ROW" -- promptpotter` is 0.
- **The money fold copies the account's lifetime book on every send** (✓, a hot-path pass):
  `read_model.py:969-985`'s `_Money.value()` copies holds, drawn, bills and usage and deep-copies
  `by_round`, `_view` resets on every feed (`:912`), each send appends a Hold, and `spend_book` reads it
  two or more times per send (`:252`, `:394-421`): O(holds ever) per send, quadratic over the account's
  life, on a SURVIVOR file never compacted. Root: the fold keeps aggregates and the open holds only;
  a settled bill leaves the fold state. Beside it (reported): inner and outer L4 replays reach
  `search_incurred` at $0 because the root mirror runs only in `Admission._write` (`spend_book.py:522-535`,
  `:686-689`), so a compare's metered price depends on run order; and a fork leaves partial inner
  campaigns in the parent's sandbox that only the server sweep reclaims (`reaper.py:30-54`), never a
  CLI-only run. **Rides with:** holders.
- **A live depth-2 L4 sandbox is reclaimed as an orphan** (✓, a two-process pass): `spawn.py:185` records
  `owned_by = ctx.inside[-1]`, sandboxes sit flat under the shared root (`tasks.py:604-610`,
  `layout.py:122-140`), and a depth-1 inner cycle spawns depth-2 cells (`MAX_INSTRUMENT_DEPTH` 2,
  `spawn.py:422-423`); `reaper.py:43-48` loads the owner from `tenant_workspace(projects_root)`, which
  answers None for an owner living inside a sandbox, so it calls `delete_inner_sandbox` with no producer
  lock and no `destroying`. Root: the owner record carries the whole `inside` chain, and the reclaim asks
  the owner's producer lock, never a failed load. **Rides with:** holders (the reaper's holder read).
  **Re-test:** a depth-2 cell's sandbox survives a sweep while its owner runs.
- **Three files are rewritten whole with no lock** (✓ the first): `user_store.py:71-72` `save` rewrites the
  user list (a SURVIVOR) while `account.py::accept_terms` (`:362-377`), `set_user_settings` (`:349-352`)
  and `quota.py:1049-1050`'s `set_concurrent_cycles` interleave, so a lost acceptance re-asks consent;
  reported beside it: a label rename (`workspace_edits.py:82-90` → `store.py:355-361`) rewrites
  `campaign.json` outside the gate, racing archive / unarchive against `persistence-and-state.md`'s "one
  editor", and stub-fork cleanup restores the session pointer over a launch's write
  (`session_pointer.py:43-71`, `run_observers.py:193`). Root: a terms acceptance is a fold of
  `consent.jsonl`, and every whole-file rewrite is a locked rewrite (`wallet_store.py:97-113`'s shape) or
  an appended last-wins record; the user list's reshape ships its migration. **Rides with:** holders
  (survivor writes). **Re-test:** two concurrent settings writes and an acceptance all survive.
- **Maintenance verbs race the writers they do not ask** (reported): `reindex`
  (`archive_maintenance.py:535-536`) checks no writer and has no dry run; `measurement_archive.py::_register`
  (`:419-430`) checks then appends outside `appending`, so duplicate fold keys land (`_live_rows` keeps
  the last, `_built_index` the first); `reset` unlinks `.*.tmp` (`data_root.py:567`, `reset.py:193-196`)
  without the file locks, and a server with no jobs passes `holder_in_reach`; the reaper holds the
  producer lock across a whole ledger scan (`stores.py:121-138`) while `hold_cycle` waits 1 s
  (`producer_lock.py:13,24`), so a resume racing the sweep is refused `producer_live`. Root: each verb asks
  `holder_of` for the writers it races, `_register` appends under `appending`, and the reaper takes the
  lock per cycle. **Rides with:** holders. **Re-test:** a reindex beside a live writer refuses, naming it.
- **Per-process memos stand in for on-disk money facts**: the API server prices on the rates it read at
  boot (`pricing.py:211`'s `lru_cache` by path; the only refresh is `runner/mint.py:123`, in the run's own
  process; server readers `preflight.py:415`, `pipeline_resolve.py:886`, `telemetry.py:126`,
  `capabilities.py:78`) (✓); reported: the Compare priced-key set is a `ContextVar` snapshot
  (`telemetry.py:253-280`, bound at `mint.py:122`), so two processes holding cycles of one campaign each
  meter one replay and `search_incurred` drains twice, against "metered once per campaign"; rate buckets
  are per process (`quota.py:107-130`, `:175-210`), so two servers give twice the stranger rate its
  docstring promises; `ReplayFeed._seen` is a count cursor (`measurement_archive.py:634-656`) where
  `_LedgerTail` keys a `FileMark`. Root: the rates key on `file_sig` through `read_model.derived`, as
  `capabilities.py:91-114` does, and `cache_clear` goes; the priced-key check-then-append is
  campaign-scoped under one write lock; the bucket's docstring states per process or the bucket moves on
  disk; the feed keys a `FileMark`. **Rides with:** holders (the money files). **Re-test:** a rates file
  edited under a live server re-prices its next preflight.
- **Two per-process liveness locks share one jobs dir** (filed by paths): `jobs/interlock.py::this_producer`
  (`jobs/producers/<pid>-<hex>.lock`) and `docker_host.py::CONTAINER_PRODUCER`
  (`jobs/machine/producers/<hex>.lock`) each mint and hold a token per process. Root: one producer token
  per process and home, held in `infrastructure/producer_lock.py`, which both labels containers and
  admits jobs; `CONTAINER_PRODUCER`'s import-time readers (`connectors/harbor.py:1129`, the compose
  overlay's env) take it from the claim. **Rides with:** holders (one holder identity). **Re-test:**
  `git grep -c "CONTAINER_PRODUCER" -- promptpotter` is 0.

### life (after holders, beside cfg)

- **The launch lifecycle is an implicit state machine**: fix-commit touches since 2026-09-15 put
  `campaign_store/store.py` (15), `jobs/quota.py` (13), `domain/run_records.py` (12), `jobs/mint.py`
  (12), `runner/entry.py` (10), `launcher/mint_and_start.py` (10), `launcher/checkin.py` (9),
  `commands/launching.py` (9), `commands/dispatcher.py` (9), `domain/command_kinds.py` (8),
  `runner/inner/spawn.py` (8), `run_observers.py` (8) and `launcher/admission.py` (8) on top; about ten
  of the thirteen are mint → admit → hold → start → release, spread over ~10 files with no transition
  table. holders fixes who holds; the fix density is the missing lifecycle. Action: one explicit
  lifecycle (asked → admitted → held → running → ended), each exit a declared transition, `Hold` its
  data; every door moves a launch only through it. The reconciler it replaces:
  `infrastructure/runtime_flags.py` holds no flags; `read_run_state` takes 11 inputs (6 bools, 2
  clocks, the claim, the gate, the drafts) through an 8-rule precedence ladder over five sources (the
  ledger's last `RunPhaseRecord`, the producer lock, the pass lock, the inbox's pause, the launch
  claim's lock) and heartbeat mtimes, on every read; with the table its inputs are the declared state
  and lock liveness, and the file is renamed to what it holds (`run_state.py`). The contracts are
  hidden: since 2026-05-01, in commits of 25 files or fewer, with no import edge either way,
  `runner/entry.py` co-changes with `live_dashboard/state.py` 34 times (47% of its changes) and with
  `runtime_flags.py` 21 (66%), `launcher/checkin.py` with `cli/commands/new.py` 20,
  `campaign_store/store.py` with `cli/commands/reset.py` 13 (62%), `resume_and_fork/repair.py` with
  `runner/round.py` 7 of 7; `embedded_run.py` spells its own launch beside `launcher/head_to_head.py`'s
  admitted one and co-changes with `cli/commands/new.py`, which neither imports. **Rides with:** life.
  **Re-test:** the chain's census lists every write of a launch's phase; one transition function
  remains.
- **A blocking lock runs on the event loop** (✓): the admission gate's RLock is taken on worker threads
  that scan every ledger and on the loop (`admit()` at `launch.py:134-162`, from async launch `:173` and
  `launching.py:348`; the registry's `mark_started`, `release`, `cancel_queued`), and the file gate
  `io.py::take_lock` polls with `time.sleep` up to `LOCK_TIMEOUT` = 5 s (`interlock.py:50-58`); also on
  the loop: the dispatcher's `_applied` ack (a locked command-log append), a grants read in the OIDC
  middleware per request, `headers.py:56-61,98` reading `build-id.txt` per response, the auth callback's
  `claim_default` lock. One admission scan or file-gate holder stalls every request for up to 5 s. Root:
  a blocking lock (`take_lock` with wait > 0, the admission gate) refuses on the event-loop thread, and
  each async door offloads once. Beside it (filed by paths, whose landing moved the upload parse off the
  loop): `loaders.py:433` and `startability.py:195` re-read a kept upload on the loop at every bank read
  (`read_kept_table`). **Rides with:** life (every door moves through one transition).
- **The CLI composes answers the server should own** (reported unless ✓): `machine-status` joins
  `account_bound`; a pass's second Ctrl+C hard-codes "resume" (`campaign_runner.py:91-98`); the drift
  pivot (`resume_command.py:175-194`) should be `ConfigDriftError` carrying `NextAct("mint-campaign")`;
  readiness is checked twice (`new.py:248-254` vs `launching.py:444-446`) with two `draft_served`
  fetches. **Rides with:** life.
- **The job registry globs every job file per question** (✓): `JobRegistry.list_all` globs and
  validates every `jobs/*.json` (`registry.py:340-348`), and `list_running`, `list_queued`, `pass_on` each
  call it again, some under the admission gate (`reap_orphans`, `share_campaign_ceiling`); every L4 inner
  cell mints a job, and wallet reads on each store frame run `reap_orphans` under the gate. Root: a
  writer-maintained live-jobs index; reaping moves to the sweep. **Rides with:** life.
- **Nothing owns the backend HTTP client's lifetime** (✓, a leak pass): `BackendClient.http` opens an
  `httpx.AsyncClient` lazily (`backend.py:186-195`), and `wiring.py:284`'s revision read opens it at every
  termnorm and harbor session (`termnorm.py:151-152`, `harbor.py:1538-1539`); seven openers meet three
  closes of three shapes (`embedded_run.py:147,157` an exit stack, `spawn.py:396-399` a finally,
  `mint_and_start.py:149-150` on refusal only). Never closed: the server launch (`launch.py:62-87` →
  `HeldRun` → detach, `run_job.py:265`), `NewCycle.open` when `mint_framed_cycle` raises
  (`mint_and_start.py:104-114`), `open_checkin` (`checkin.py:180`; its finally releases the claim alone),
  `head_to_head.py:87,99`, `scoring_scope.py:139-162`'s `open_diagnostic` (already an async context
  manager, never acloses), and the L4 seat (`spawn.py:371` → `init_services`, raising at `:316`, `:323`,
  `:358` or in mint before the try at `:376`), so every unscoreable cell leaks one client per round. Root:
  the opener owns the lifetime: `init_services` / `bind_cycle_session` become async context managers,
  `HeldRun` closes after `spawn_job` hands off, every door enters one, and the ad-hoc closes go.
  **Rides with:** life (every door through one transition). **Re-test:** a refused, an unscoreable and a
  handed-off launch each leave no open client.
- **The run-state stamp misses a quiet producer's death** (✓): `runtime_flags.py:174` relies on "a
  directory's mtime sees a child's create AND unlink", but `producer_lock.py:35-39` `release` never
  unlinks, so the dashboard answers 304 on attached or WEDGED until the sweep writes a stop record. Root:
  the stamp carries `held(cycle)` and `held(pass)`, the OS facts `derive_run_state` already reads, and the
  comment goes. **Rides with:** life 2 (the reconciler). **Re-test:** a producer killed between ticks
  reads ended at the next poll.

### passes (links 1-2 landed at 47; link 3 beside holders, link 4 after it)

- **A pass is still named by its job's verb** (landing 47 left it): `domain/pass_kinds.py::PASSES` is the
  one row per kind, but `ScoringVerb` still names the pass a scoring job runs, and the seed screen keeps
  its own opener beside `scoring_scope.py`'s. Action: `ScoringVerb` is retyped off the pass kind with the
  verbs' deletion (one job migration: job files are SURVIVOR), and the seed screen's opener goes with its
  verb. **Rides with:** passes 3. **Re-test:** `git grep -c "class ScoringVerb" -- promptpotter` is 0.
- **A cell spawned outside any pass reads PANEL** (passes' census): `MeasuredPass.role` defaults to
  PANEL, so `runner/inner/spawn.py:165` names a cell no pass measured as a panel cell. Root: a default
  standing in for absence; the spawn names its pass or none. **Rides with:** passes 3.
- **Diagnostics are verbs where they should be settings**: `noise-floor`, `seed-screen` and
  `probe-reasoning` are each a verb, and each prints or files where the dashboard should read
  (`seed-screen` a dated JSON under `DIAGNOSTICS_DIR`, the noise floor a pass nothing in `webapp/`
  serves, `probe-reasoning` a `capabilities.py` row). Action (operator, 2026-11-12: no explicit verbs,
  one door with different settings): one campaign started from a LIST (`arm_gate` review or author,
  `submit-arms`), set by its arms (any knob, `reasoning_effort` included) and the `Pass` row's
  `replicates` and `fresh` (the answers or the bank draw; a content-identical arm replays from the
  archive, §0008), its readings on the served dashboard. The noise floor is its origin at k fresh
  replicates. The seed screen and probe-reasoning go outright (orchestrator): a bank-draw spread is the
  same replicates with the bank draw fresh, and the seed screen's verdict (the constant-answer floor
  over the origin) is the row "A ± that measures nothing", served on every origin round in this same
  link. A `registry._MODEL_PROFILES` row is earned by a round
  whose arms differ in `reasoning_effort`: a rung the endpoint refuses is served as a REFUSAL on its
  arm, never a generic errored cell, and each call's usage carries its reasoning tokens;
  `docs/operations/datasets.md`'s "earned with `probe-reasoning`" line moves to that door. The noise
  floor today appends a VERIFY `PhaseRecord` (`scoring/passes.py::_rescore_origin`) with no command
  filed, so `presentation/CLAUDE.md`'s two rules on it (:9, :13) contradict; the campaign door files one,
  and the contradiction goes with the verb. Price (one
  budget): the probe was one call per rung on no dataset; the round is cells × rungs, cents on the
  flash models, and buys a score per rung. The three verbs, `DIAGNOSTICS_DIR` and `probe_reasoning.py`
  go; `ab` stays (it replays a code tree over recorded rounds, a developer's check, not a measurement of
  a prompt). **Rides with:** passes 3. **Re-test:** `git ls-files promptpotter/presentation/cli/commands`
  names no `noise_floor`, `seed_screen` or `probe_reasoning`; an origin round serves the paired
  constant-answer floor; a refused rung reads REFUSED on its arm.
- **`--diag` skips round 0's door** (✓): `runner/loop.py:188` skips the lost-cell rewalk, the origin gate
  and `emit_origin_round` when diagnosing; reported, diag with a controller-less optimizer refuses only
  after the mint (`entry.py:476`). Action: the `diagnosing or` goes with the diagnostic verbs (passes 3),
  and a controller-less optimizer is refused at the payload. **Rides with:** passes 3.
- **An idempotent retry is acted twice** (✓, model-checked): `store/command_log.py:341`'s `keyed()`
  matches only accepted or applied events and `file()` (`:392`) refuses only a reused `command_id`, so a
  same-key retry during the first act is filed and acted again (a second draft from ingest-upload, a 409
  on a launch retry). Reported: a pause filed while run R0 releases is applied by the NEXT run, though it
  predates that run's `held_since`, or reads "working: accepted" forever. Action (both verified in the
  model): lookup, file and act serialize per (subject, key), a filing with no end whose filer is dead
  folding as cancelled, and each loop filing stamped with the hold it was admitted against. The same
  filing record serves the dead filer: a killed verify reads `working` to every door and its task's
  result polls forever, because a command whose filer died stays `filed`; the filing names its filer
  (a field on `domain/command_events.py::CommandRecord`), judged by holders' one death rule (the
  producer token), and a dead filer's command reads ended. A build on `claimant_lock` (`a4c5f7948` on
  `arc/passes`, unlanded) was held back because holders deletes that lock: it is re-cut on holders' rule.
  **Rides with:** passes 4, after holders; its Owns gain `domain/command_events.py` (passb-2 3's stopped
  pass rides this row). **Re-test:** kill a verify mid-pass; its command reads ended.

### cfg (the re-key window, beside payer)

- **The merge rule is chosen by the door, not declared by the document**: `runner/inner/spawn.py`'s
  `_laid_arm` and `potter/records.py`'s `steered_onto` merge by dict spread, skipping
  `pipeline_params.py`'s rule that a new model drops `route_order`; `pipeline_manifest.py`'s `laid_over`
  passes no types, so an object knob is replaced whole; "" means unchanged, emptied, no write, reset or
  inherit by site; reset has two path grammars (`draft_patch.py`, `command_kinds.py`); the patch check
  is spelled about five times, healing in some and raising in others. Action: a layer is (name, a patch
  or a function of the fold so far); a patch sets, clears or merges per path, by the rule the
  document's schema declares for that key; one fold returns the document and a provenance stamp per key
  (`pipeline_resolve.py::ParamLayers` already is this for target params). `merge_config_layers`, the
  knob-layer projections, `apply_config_overrides`, `_laid_arm` / `_one_model`, the `steered*` memory,
  both reset grammars and the browser's "" handling go; ≈−0.9k lines. About 58 of the engine's 611
  `dict[str, Any]` sites are node-config bags, the same overlay typed two ways (`dict[str, NodeCall]`
  on `CycleSeed`, `dict[str, dict[str, Any]]` on `ForkSeed`): each becomes the one typed document. It closes rk-run's two-merges,
  cleared-steer-field, relabel and `config_map` rows, admit-2's untyped steer and retired-knob rows,
  ren 8's node-config census, the pipeline-switch row by explicit layer names, and rk-dec 3 (a model
  per inner node rides it). One L4 re-key and its wipe. **Rides with:** cfg.
- **Target-prompt field order is a shared constant**: `domain/CLAUDE.md` rules that field order is
  the node's (`NodePromptInfo.fields`), never a shared constant, yet `optimizer_template.py::PROMPT_FIELDS`
  orders a target's parts (`pipeline_responses.py:341`, the library's `suits`) and seeds a connector's
  fields (`connectors/dspy_module.py:413`). Action: each reader takes the node's own field list off the
  one document. **Rides with:** cfg. **Re-test:** `git grep -c PROMPT_FIELDS -- promptpotter/connectors
  promptpotter/application/pipeline_responses.py` is 0.
- **Knob couplings are hand-written, and `CampaignConfig` has no cross-validator** (a feature-interaction
  pass; reported unless ✓): `shots_without_demo_pool` is declared on capo `few_shot` only, not `capo_init`
  or potter `l1_generate`; `couplings.py` has no escalation row (ladder `l1` with rebase, reelect or
  patiences set is inert); `recombine` is priced into `_walk_ceiling` whatever its host, refusing an arm
  over children that cannot exist (`potter/members.py:273`, `preflight.py:312`); the degradation
  coupling tests `== 0` where the code branches on `> 0` and the field is unbounded (`knobs.py:214`,
  `classification.py:109`, `campaign_config.py:212`); `exclude_nodes` silently drops a narrowing or
  overlay on the excluded node; `DeterminismClamp.route_order` is laid on every optimizer call
  (`llm_call.py:167`), skipping the rule that a route names one model's hosts (`pipeline_params.py:215`).
  Action: couplings derived from each member's `draws` and the ladder enum; `degradation_threshold`
  bounded 0..1; one `CampaignConfig` validator refusing a narrowing or overlay on an excluded node; the
  clamp's route keyed by model. **Rides with:** cfg.
- **A `--config` budget means three things, and a value is typed two ways** (✓): `new.py:277-279` pops
  the declaration's budget to bound only this launch, REST answers 422 (`draft_edit.py:31-43`), and
  embedded makes it the standing budget (`embedded_run.py:149`) (root: `ConfigLayers.of` alone decides,
  the CLI's pop goes); reported, `--set` types values as YAML (`new.py:93-96`) and `--steer` as JSON
  (`launching.py:128-135`), so `--set k=yes` stays the string "yes" while `--steer` refuses it.
  **Rides with:** cfg.
- **The campaign default layer has two homes** (✓): `assets/datasets/defaults.yaml` holds three keys
  (`scoring`, `degradation_threshold`, `budget.rounds`), and `degradation_threshold` is `Field(...)`,
  required, at `campaign_config.py:212`; about twenty knobs default in `CampaignConfig` field defaults:
  `sp_budget_origin=40` (`:308`), `elimination_n_min=6` (`:223`), lives 2/4, judges' temperature 0.0,
  `hard_sample_order`, `panel_gate` / `origin_gate` strict, `lift_reference`, `arm_gate` off,
  `enable_2pl_graduation`, `bench_trigger`, `task_framing`, `display_metric`, `dataset_split`. No rule
  says which goes where, against `architecture.md` §0.5 ("never code", the contract the code is fixed to)
  and `assets/datasets/CLAUDE.md:7` (the definition chain shows every default). Action: every campaign
  default lives in `assets/datasets/defaults.yaml`, and each `CampaignConfig` field it covers becomes
  required, as `degradation_threshold` is. **Rides with:** cfg (a re-key when a cell hashes the merged
  document). **Re-test:** no `CampaignConfig` field outside the per-run inputs carries a default.

### batch/26 · rk-dec

- **The crowning bar tests nothing a result can quote**: `application/scoring/selection.py::elect_round_winner`
  admits with no interval and no multiplicity correction, and `RoundResult.improved` reads any selection
  past round 0 as a promotion; at panel width no corrected exact verdict reaches α=0.05. Action
  (operator, 2026-11-12): ONE predicate whose statistic is a switch, every reading built: today's
  posterior bar; an exact paired sign test on the discordant cells, Holm-corrected across the round's
  arms; an anytime-valid e-process on the paired discordance, valid wherever the race stops. Each
  reading names what a result quotes, and `RoundResult.improved` reads the bar. Today's stays the
  default until separability is read on banked runs. **Rides with:** rk-dec 2. **Re-test:** a tenant-ruler run's `index.json::final.rounds_to_separable`
  beside its `rounds_to_improved`.
- **The never / partly / always spread contradicts the headline beside it**:
  `application/scoring/measurement_log.py::_cycle_cells` pools every candidate the cycle ran, so
  `domain/cells.py::hit_spread` reads a sample only C0 saw as "never". Action: the spread reads the
  headline's own cells. **Rides with:** rk-dec 2.
- **"Calls held equal" has no axis to hold**: `domain/spend.py::Budget` meters usd, tokens, rounds and
  wall clock, while the roadmap, `paper/README.md` and `paper/plans/` each hold a different axis
  equal. Action: USD is the one matched axis, named on `Budget` (operator, 2026-11-12). **Rides with:** rk-dec 2. **Re-test:**
  `price-head-to-head paper/plans/comparability.yaml` — do one point's `search_cells` differ by arm?
- **The election has no one-arm read**: `scoring/selection.py:177` fits a prior through
  `candidate_abilities({"read": rows}, …)`, a fake arm key. Action: a one-arm read on the election's fit.
  **Rides with:** rk-dec 2. **Re-test:** `git grep -n '{"read": ' -- promptpotter/application/scoring/selection.py`.
- **An L4 arm's model answers every inner node**: `runner/inner/spawn.py::_one_model` lays the first
  edit's model on every llm node. Action: an L4 arm can name a model per inner node. **Rides with:**
  rk-dec 3. **Re-test:** an arm naming a model on `l1_critique` alone answers it on that node only.
- **Round 0 is walked before the cycle's init closes**: `origin.py::establish_campaign_origin` walks it
  outside its door, `runner/loop.py::open_origin_round` holds its close and gate, and
  `bench/node_context.py::rewalk_origin` walks it again for the repair; `origin.py::lock_criterion`
  changes the scorer's id, which keys the difficulty ruler. Walking round 0 only inside its door puts
  INIT before ORIGIN, which reorders every leg's phases (`offline_run.py::_ledger` keeps phase order), so
  decisions move on every leg. Action: seat the origin unmeasured, inherited
  (`origin.py::try_inherit_fork_origin`) or standing, walk it in its door, and lock the criterion at the
  close, named as a decision move. **Rides with:** rk-dec, whose Owns gain `origin.py`,
  `runner/{open_cycle,origin_gate}.py` and `bench/node_context.py::rewalk_origin`. **Re-test:**
  `git grep -n "def establish_campaign_origin" -- promptpotter` is empty.
- **A fork cut at round 0 keeps its origin's holes**: `bench/cycle.py::Cycle.reopen_origin` leaves round
  0 with no close, so `resume_and_fork/repair.py::rewalk_lost_origin` returns at once. Telling a
  just-walked origin from an inherited one needs the one walk above; a flag would be the patch. Action: an
  inherited origin is re-walked when another launch measured its rows. Also: `runner/loop.py:205`'s
  `resumed_ending` reads the fork's hop, so the operator's override is lost. **Rides with:** rk-dec, with the
  walk; its Owns gain `bench/cycle.py` and `resume_and_fork/repair.py`. **Re-test:**
  `--fork-on-divergence` at round 0 of a gate-halted campaign, backend fixed, passes in one launch.
- **The last round is decided in three places** (✓): `runner/loop.py:129`'s `target_tripped` runs after
  `post_round` (`:119`), so with `halt_at_fitness` set the escalation pays for L2/L3 and a
  `fork_proposal` can rebase past TARGET_HIT. Action: `target_tripped` joins `standing_tripped` in
  `termination.py`, and `post_round` and the adapter skip (`round.py:1464`) read only that. **Rides
  with:** rk-dec.
- **The ceiling stop and the advance read without a guarantee** (a statistics pass, ✓ for the stop):
  `termination.py:124`'s `rate_a + estimate.ci_lo >= target` treats C0's rate as known, inherits the
  level interval's undercoverage, and re-reads every round with no α spent: coverage 0.77–0.93 against a
  nominal 0.975, and a 20/20 pick fires TARGET_HIT at 0.95 in 8% of looks when the true level is 0.9.
  ADVANCED (`origin_panel.py:95-121`) is an uncorrected one-sided 2.5% test repeated every round on a
  reused panel: under the null 12.8% of 5-arm × 10-round runs advance, so `rounds_to_separable ≤ 5` can
  pass on noise. The crown row's numbers: per-round false admission 0.30 at 3 arms, 0.34–0.39 at 5.
  Decided (orchestrator, on the crowning bar's pattern): the stop reads the pick's own one-sided level
  bound, anytime-valid; the advance reads the bar's statistic switch (today's the default), so one
  switch governs crown, advance and stop. **Rides with:** rk-dec 2.
- **`refuse_origin` reads a schema fact off answered rows**: `capo/members.py::CapoInit.refuse_origin`,
  `optimizer_manifest.py::refuse_origin` and `optimizers/nodes.py:211`, called at `bench/cycle.py:264`.
  Every one is hashed (every treatment, both capos). Action: `refuse_pipeline(schema)` at `Cycle.start`. **Rides with:** rk-dec (the
  re-key window), whose Owns gain those three hunks. **Re-test:**
  `git grep -n "refuse_origin" -- promptpotter/application` is empty.
- **`bench/difficulty.py` still guards a scorer run init guarantees**: `require_scorer` /
  `require_partition` (`:167`, `:272`, `:337`), a hashed unit (the estimator digest), keep the two
  accessors in `initialization/session.py`. Action: read the fields; the accessors go. **Rides with:**
  rk-dec (the re-key window), whose Owns gain `bench/difficulty.py` and `session.py`'s accessors.
  **Re-test:** `git grep -c "require_scorer\|require_partition" -- promptpotter` is 0.
- **Three shipped-off behaviours stay as knobs**: potter's `pobb.lock_in` (off; 7 tests),
  `origin_gate: critical_only` (`strict` and `off` stay) and `convergence_patience` (`lives` stays: the
  round standing and the UI's hearts read the same `RoundAdvance` signal). Action (operator,
  2026-11-12): each goes, its shipped behaviour hard-wired; `pobb.lock_in` sits in potter's hashed
  files, a re-key. **Rides with:** rk-dec 4. **Re-test:**
  `git grep -cE "lock_in|critical_only|convergence_patience" -- promptpotter` is 0.

### valid (after rk-dec, before the behaviour keys)

- **A resume asks "is this still valid" five ways**: `resume.py`'s consumed set and prompt hashes,
  `jobs/mint.py`'s defaults drift, seven members' `replay()` (capo, gepa, levi, the election, the race)
  and `round_packages` / `rederive`. Action: one validity check: `resume.py::_divergence` becomes
  `ab_replay`'s verdict, walked by `find_divergences`; ≈−250 lines. The round a fork is cut at can
  move: name it. **Rides with:** valid 1.
- **A cycle's mint is written before it is minted**: the root mint writes its partition, seed and
  resolved run before `CycleMintedRecord`, against `run_records.py`'s own docstring. Action: one
  `mint_cycle` writer. **Rides with:** valid 2.
- **Three ways onto a fork**: the mid-INIT rebind, `_run_line`'s rebase and an operator relaunch.
  Action: one. **Rides with:** valid 2.
- **A copy skips validation**: `model_copy(update=…)` stands at 114 sites in 60 files, and pydantic
  validates nothing in `update`; `domain/strict_model.py::StrictModel` does not override it. So a copy
  bypasses the 49 classes' `model_validator`s (`OptimizerState`'s slices in `runner/entry.py`,
  `CycleSeed` in `fork_siblings.py`, the command payloads `commands/dispatcher.py` rewrites); about
  100 update keys are string literals no rename or mypy sees, so a stale key lands unrefused while the
  renamed field keeps its old value; and `CampaignConfig` has a validating constructor
  (`effective_config`) beside a copying one (`campaign_config.py::apply_config_overrides`). No live
  breach found in the sites sampled. Action: `StrictModel.model_copy` revalidates whenever `update` is
  given, and a probe keeps the count from rising. **Rides with:** valid 3. **Re-test:** a copy with a
  stale key or a state its validator refuses raises.
- **`RunMode` is four independent bools** (✓): `--no-check` makes `--fork-on-divergence` inert
  (`resume.py:120`); `--from` with a fork rewinds the parent in place (`:131`) and then branches (`:138`);
  reported, `resume --rewind N --from M` cuts the fork and then dies (`fork_cycle` never checks
  `from_round`, `launching.py:320-334`). Action: one `on_divergence: halt | accept | fork`, and
  `ForkCyclePayload` refuses `mode.from_round`. **Rides with:** valid 1.
- **Two seeds** (✓): `Cycle.rng` seeds on the campaign id or the clamp seed (`cycle.py:204`), never
  `run_seed`; L4 bridges the two only when a temperature is set (`tasks.py:523`). Action: `Cycle.rng`
  keyed on `run_seed`. **Rides with:** valid.
- **A mint or a fork lands as several appends** (reported): `CycleMintedRecord`, the rewound
  `RoundEnteredRecord`, `CycleSeedRecord` and `InterventionRecord` are separate appends (`store.py:215`
  loops single appends; `:535`, `:544`, `fork_siblings.py:93,96`), so a resumed torn fork stands on all
  its parent's rounds, loses the steer, or grades steered rows reusable; a crash after the manifest
  (`mint.py:592`) and before `drafts.close` (`checkin.py:102`) leaves the draft listed for a duplicate
  mint, and one after `adopt_ledger` and before the manifest leaves a torn mint whose draft lost its
  check-in ledger; `draft.json` is written before `resolution.json` (`origin_readiness.py:263-264`) and
  `write_own_definition` is torn across framing, origin and definition (`stores.py:167-172`). Action:
  one append per fact; the manifest names the adopted draft and `draft_ids` skips it; a companion file
  carries the digest it was computed against. **Rides with:** valid 2 (one mint writer).
- **Each door forks its own way** (an entry-point parity pass; ✓ unless reported): a REST fork-cycle
  with `candidate_id` seeds only the candidate's params (`fork_siblings.py:263-267`), so it runs X's
  params under C0's prompts, while the webapp sends X's prompts itself (`SteerForkPanel.tsx:77`) (root:
  `cut_operator_fork` seeds the origin prompts from the named candidate beside `_candidate_params`); the
  CLI refuses rewind + steer (`resume_command.py:209-210`), which the payload accepts, sets its own
  default reason (`:115` vs `fork_siblings.py:279-284`), and its `--steer` seed covers only the overlay
  (`verbs.py:192`; `_unsaid` checks top-level fields only, `:893-903`) (root: both CLI rules go, one
  `ForkCyclePayload` laid); reported, the CLI re-derives `RunMode` for an inline run (`new.py:481`,
  `resume_command.py:242-244`) where `HeldRun` should carry the admitted mode; the browser's "Resume run"
  sends start-run with no mode (`useRunControl.ts:54,59`) while `phases.py:793` serves
  `NextAct("start-run", "mode.fork_on_divergence")`, so a diverged cycle diverges again; the
  `arm_gate: author` hold offers keep-only buttons (`LiveSegment.tsx:32`, `activity.py:134-157`) though
  `command_kinds.py:140-145` says the browser presses submit-arms. Root for the browser: a served act
  carries the payload it fills. **Rides with:** valid 2 (one way onto a fork) with the `RunMode` row.

### hubs (after valid, before layers)

- **Five hub files serialize the arcs**: 434 of the 989 engine commits since 2026-06-01 (44%) touch
  `runner/entry.py` (180), `domain/results.py` (174; `RoundResult` holds 34 fields),
  `domain/run_records.py` (169; 84 classes), `campaign_store/store.py` (146) or `runner/round.py`
  (125). Since an arc owns its files, two arcs that each add a record kind or a round fact run as a
  chain. Action: open registries. Record kinds live in family modules found by walking the package, as
  injections are; a round fact is a kind-keyed typed slice, as `StateSlice` / `RoundOutput` are; a
  feature adds a file instead of editing the spine. Members' imports move (a re-key) and a round's
  on-disk form changes (a wipe). It follows life, whose spine is `runner/entry.py`. **Rides with:**
  hubs. **Re-test:** a new record kind or round fact lands in a file of its own, no hub edited.

### layers (alone, after valid, before the behaviour keys)

- **The layering is fictional at runtime**: 659 function-level `from promptpotter` imports in the
  engine, about 100 closing a package cycle against module-level imports the other way. Optimizer
  members import `bench/node_context.py` lazily 18 times (and `bench/cycle.py` once) while `bench/`
  imports optimizers at module level 3 times (`optimizers/nodes.py` holds 24 function-level imports,
  `node_context.py` 23); `infrastructure/llm` and `infrastructure/store` import each other (3 and 1
  lazy, module-level back); `application/jobs` → `application` (3 against 26);
  `application/datasets` → `application` (2 against 13). `application/CLAUDE.md` already rules that a
  back-import moved into a function hides the cycle. Action: a gate check counts the package pairs a
  lazy import closes into a cycle, held to a falling baseline as `surface-ledger` is; then each knot is
  cut at its root. Members' imports move, a re-key, hence before the behaviour keys. **Rides with:**
  layers. **Re-test:** the check's baseline is 0.
- **The bottom layer types an application object**: `shared/run_scope.py::RunScope` types its fields
  under `TYPE_CHECKING` with `SelectedOptimizer`, `InnerSpawnContext`, `CycleEventLog`, `SendBudget`,
  `SpendBook`, `MemoryScope`, `LLMReuseCache` and `Stores`. Action: the carrier moves into
  `application/`, and the three `ContextVar`s of `llm/telemetry.py` beside it land on it in the same move
  (money's sender and spend-book vars already did; `shared/measurement_context.py`'s two per-call vars
  stay). **Rides with:** layers. **Re-test:** `promptpotter/shared/` holds no `RunScope`, and
  `git grep -c "ContextVar(" -- promptpotter/infrastructure/llm/telemetry.py` is 0.

### docs (beside any chain: `docs/concepts/` and `docs/methods/` rewritten to the code)

- **Concept and method docs state what the code does not** (the rest of a docs-vs-code pass; the docs
  chain rewrote the others): `the-loop.md:38-40`'s `terminate_proposal` line waits on the faults row that
  makes the abort report; the race-vs-election and joint-fit lines wait on readings 2; the L4
  fingerprint line on bkeys. **Rides with:** each named chain. **Re-test:** each claim re-read against
  its code line.

### bkeys (the last re-key; L4's cell identity rides it)

- **An L4 cell's depth defines its measurand but enters no key** (✓, an L4 pass): the panel says
  "`max_inner_rounds` IS the estimand's budget" (`research/…/panel/dataset.yaml:29-32`), yet
  `DEPTH_FIELDS` stay out of every key (`cells.py:24-28`, `Seat.pin` `:48-52`, the treatment
  `tasks.py:321-324`, `_instrument` `spawn.py:507-517`) and the outer `ReplayFeed` serves banked rows
  (`search_point_scorer.py:303-316`): raising `max_inner_rounds` or `n_samples` replays every banked cell
  at the old depth, C0 included, while new arms read deep, so `held_out_lift` compares two measurands
  silently (`n_samples` also moves `draw_bench`'s rows); `tasks.py:136-141`'s "every cell deepens in
  place" is false (a continued inner campaign runs its stored declaration, `spawn.py:217-250` →
  `mint.py:352-375`). Decided (orchestrator; the operator may reverse it): the depth fields that define
  the measurand join the outer key; only the inner continuation stays depth-free.
- **One L4 cell has two keys, and the instrument hashes a hand-picked subset** (✓ for the keys):
  `inner_campaign_id` (`spawn.py:130-142`: treatment, edits, model, `_purpose(role)`, estimator) vs
  `_instrument` (`:507-517`: treatment, node config, campaign identity), so an inner dataset's node-config
  edit moves the outer pin but not the inner id, the old campaign continues into `ConfigDriftError` and
  `CellUnscoreableError(spent={})`, and that (arm, cell) is unscoreable for good (test gap:
  `test_identity.py:1065-1118`); reported, the instrument skips what the inner session loads live
  (`wiring.py:228-307`: `origin.yaml`, the `source:` cut and recipe, `task.md` framing, the pipelines
  walk, the backend revision), and pins freeze at outer run init while the session loads live
  (`tasks.py:586-596` vs `spawn.py:306`); over-keying: `_purpose(role)` inside a content id
  (`spawn.py:139`), `Seat.id` a label inside the pin, axes key order and mixed-manifest chain order.
  Root: the instrument hashes the inner origin's own content identity (origin hash, cut hash, framing)
  off the object the session loads, and the inner id derives from the outer cell key plus the edits.
- **The estimator digest misses what decides a cell's number** (✓): `measurement_modules`
  (`tasks.py:330-333`) omits `inner_instrument_config` (`tasks.py:501-562`), `draw_bank` / `draw_bench`
  and the n rule (`spawn.py:317`). Root: they move into `domain/l4/` or their modules are digested.
  Also: `l4.md` says `max_inner_rounds ≥ 2` while the code takes `ge=1`; the code is fixed.

### readings (after the L4 C0 re-measure; behaviour keys make it key-neutral)

- **One estimate is read ~10 ways**: of the 31 `*Reading` classes, about 10 are estimates off cells
  (`ArmReading`, `PairedReading`, `MetricReading`, `FactorReading`, …); the other ~22 carry a status
  (`CommandReading`, `PauseReading`, …) and stay. Action: one reading (estimate, interval, n, state) that
  every estimate returns and every surface renders, and `paired.py::read_pair`'s estimator spec gains a
  kind (paired t, θ, exact sign), each returning `MeasuredLift`; rk-dec's bar and rk-opt's lift test read
  it. θ and paired t stay distinct measurands (θ is unpaired and model-based, which PoBB's truncation
  needs); CAPO, GEPA and LEVI keep their papers' bars. **Rides with:** readings. **Re-test:** the
  chain's census lists the estimate readings; one remains.
- **A finished arm meets a harder bar than a cut one**: `lost_ideas` asks a finished arm's interval to
  sit wholly below, a cut arm only the ε posterior. Action: one bar for both. **Rides with:** readings.
- **A round's advance mixes a point and an interval**: `origin_panel.py::RoundAdvance`'s "tops" half is
  a point compare beside an interval read. Action: one interval read. **Rides with:** readings.
- **The level interval undercovers at the ends** (✓, a statistics pass): the one level interval is
  Wald-t with an SE floor of 1/(4n) (`shared/statistics.py:110-125`, `:143-149`), read by
  `selection.py:29-41`'s `level_band`, which clips (`:40`): 20/20 gives [0.974, 1.0] against an exact
  Clopper-Pearson lower bound of 0.832, 0/6 an upper bound of 0.107 against 0.459, and true coverage is
  64% at p = 0.95, n = 20. Readers: the bench headline, the round fitness CI, the noise-floor band. Root:
  one bounded-mean interval in `shared/statistics.py`, chosen by the declared `Measurand.binary` (exact
  or Wilson for binary, a variance-bounded t for graded); the clip goes. **Rides with:** readings 1.
- **The Rasch fit's readings break their stated invariants** (reported unless ✓): the young-ruler joint
  MAP shrinks δ̂ inward (slope 0.30 at 3 individuals per cell), so θ is not subset-invariant against
  `verdict-resolution.md:39-40` (root: θ read under δ ~ N(δ̂, se_δ²), or δ by MML, the warm gate counting
  se_δ); `fit_theta`'s fixed N(0, 1.5²) prior per arm (`rasch.py:238`) shrinks arm and parent apart, so
  an equal arm on 8 cells against a parent at θ = +1.5 on 40 reads −0.28 (`DeltaRuler.sigma_theta` is
  carried, unused; root: the prior on the difference); ✓ `comparable_to` compares `ruler.id`
  (`ruler.py:387-388`) and a cold id is `flat:<objective>` (`:84-85`), so 8/10 on easy cells vs 4/10 on
  hard reads +1.45 logits stamped "directly comparable" (root: a cold identity carries its cell set, or
  cold readings refuse); φ's binariness is inferred from the values (`rasch.py:271-272`) against
  `paired_reading.py:378-381`'s DECLARED rule (one 0.999 for 1.0 cuts θ SE 0.546 → 0.324); FLOOR_PINNED's
  wording is false (the all-miss θ is −1.84 at n = 6, −2.74 at n = 20; a 1/20 arm over it reads +0.529,
  not 0.000; `verdict-resolution.md:93-95`, `ruler.py:160-164`), with no ceiling twin (root:
  "prior-dominated", one rule for both ends); the paired binary lift is a t-test plus a floor, not exact
  McNemar (`paired.py:367-397`; 7/1/12 gives p = 0.030 vs 0.070); the head-to-head MDE ignores Holm
  (`comparison.py:433` vs `:269`); `rasch.py:451-475`'s "with hysteresis" is stale (the margin is
  0.002/obs). **Rides with:** readings (re-keys the estimator: name it).
- **The race and the election read different rulers** (✓): `race.py:144-153` builds `PoBBCheck` on
  `ctx.read(Ruler)` as of round open with `NO_CELLS` for the origin panel, while the election re-fits
  (`round.py:611-654`, `link`) and reads rows merged with `on_panel`, so `candidate-elimination.md:35-37`
  and `pobb.py:151`'s "cannot disagree" is false; the ε-cut half stands once `lock_in` goes; and
  `verdict-resolution.md:34-36`'s "the joint fit runs once, at ruler lock" is false (re-fit every round,
  `difficulty.py:223-256`). Decided (orchestrator; the operator may reverse it): the code, not the doc,
  is fixed: both read one ruler and one row set, and the doc then states it. **Rides with:** readings 2.

### recipes (beside readings)

- **Each benchmark's loader is code**: `application/datasets/loaders.py` spells a bank recipe per
  benchmark (`hotpotqa`, `trec`, `naturalplan_meeting`, `planbench_blocksworld`, `arc`,
  `instruction_induction`, `screen_taste`, `justlogic`) and the panel recipes `sealqa_longseal` and
  `dbllmbench_questions`; `sealqa_panel.py`'s Dockerfile, `solve.sh`, `task.toml`, `test.sh` and
  instruction templates are Python string constants. The first seven back no shipped or research
  dataset (read the box's tenant datasets first, free). Action (operator, 2026-11-12): each becomes a
  field mapping in `source:` on the generic columns / table recipe (blocksworld's query parse may need
  one hook; justlogic follows where a mapping fits), the panel templates asset files. Recipe code is
  hashed into the cut (`loaders.py::_recipe_digest`), so every moved recipe re-keys its cut. **Rides
  with:** recipes. **Re-test:** `loaders.py` names no benchmark.

### batch/26 · rn-conn

- **One fact, two words: `backend_type` and `connector`**: the definition key `pipeline.backend_type`,
  `pipeline_resolve.py::draft_backend_type` and `DraftPipeline.backend_type` name what
  `Campaign.connector` names. Action: one word, `connector`, the definition key included; tenant
  definitions rewritten by a one-shot migration. **Rides with:** rn-conn 1. **Re-test:**
  `git grep -nw backend_type -- promptpotter` is empty.
- **An unregistered `backend_type` is a bare `KeyError` at thirteen `CONNECTORS.get` sites**; only
  `dataset_access.py::dataset_connector` refuses it typed, and lowercase is documented on
  `Connector.name`, enforced nowhere. `shared/plugin_registry.py::Registry` is a hashed unit: every
  member's digest reaches it (`store/source_scan.py::optimizer_prompt_shapers`), so an edit to
  `Registry.get` re-keys every treatment (conn 1 stopped on it). Action: inside the re-key,
  `shared.plugin_registry` joins `source_scan.py::PLUMBING_MODULES`; then `Registry.get` refuses typed,
  naming the registered set (a `CONNECTORS.resolve` wrapper would be a second door), `_validate` refuses
  a non-lowercase name, and callers take the Connector. **Rides with:** rn-conn 1, alone and last on the
  assembled tip, in the re-key window; its Owns (every `backend_type` holder, the `CONNECTORS.get` lines
  of conn's nine holders among them: `datasets/draft_campaign.py`, `free_tier.py`, `health.py`,
  `initialization/wiring.py`, `jobs/launcher/admission.py`, `jobs/quota.py`, `pipeline_resolve.py`,
  `scoring/formula/compiler.py`, `cli/commands/launch.py`) gain `shared/plugin_registry.py` and
  `store/source_scan.py::PLUMBING_MODULES`. **Re-test:** a dataset naming `backend_type: nope` answers
  422 on `new`, `start-run` and the pipeline read; an edit to `Registry.get` moves no `--digests` value.
- **What a definition's read raises is spelled three times**: `jobs/launcher/admission.py::_definition_read`
  catches `(DatasetAccessError, FileUnreadableError, PayloadInvalidError)`,
  `pipeline_resolve.py::_ROOT_UNRESOLVED` a superset and `datasets/startability.py::_connector_extra` a
  pair, each around `runnable_definition`. Action: `dataset_access.py` names the set beside
  `runnable_definition`, and each site reads it. **Rides with:** rn-conn 1 (every `backend_type`
  holder, these four files among them); its Owns gain `pipeline_resolve.py::_ROOT_UNRESOLVED`.
  **Re-test:** `git grep -n "FileUnreadableError, PayloadInvalidError)" -- promptpotter/application` is empty.
- **The pipeline carrier is typed only at its door**: `NodeCall`'s readers go back through
  `.laid_keys()` (21 sites) and `.laid_nodes` (26) and read its own fields by string, among them
  `connectors/dbllmbench.py:158-249` and `connectors/harbor.py:431-437`. Action: read `NodeCall`'s
  fields. **Rides with:** rn-conn 2: the reads span 14 files in seven chains' Owns, hashed units among
  them, so they move at once, alone on the assembled tip, in the re-key window; its Owns gain the string
  reads of the 14 files the re-test names (`connectors/dbllmbench.py`, `infrastructure/llm/openai_compat.py`
  and `projections/audit_trail.py` among them). **Re-test:**
  `git grep -cE "\.get\(\"(model|provider|route_order|temperature|max_tokens|reasoning_effort|top_p|seed)\"" -- promptpotter`.
- **A model's context window is node config, refused or dropped three ways**: three connectors'
  `_sent_spend_bound` hooks demand `max_input_tokens` from the node overlay, though
  `infrastructure/llm/capabilities.py::_card` serves the model's `context_length`; `harbor.py:474`
  refuses its absence, while `dbllmbench.py:145` and `dspy_module.py:323` return None, so
  `sample_measurement.py::_calls_bound` serves no bound and the cell's spend runs unbounded. Beside it,
  `--add-host host.docker.internal:host-gateway` is spelled at `dbllmbench.py:423`, `harbor.py:620` and
  `resources/harbor-docker-compose.yaml:12`, though `infrastructure/docker_host.py` owns the docker call.
  The two connectors co-change 82% of the time with no import between them. The bound and the send
  also disagree on one absent value: `dbllmbench.py:143` returns no bound without `max_tokens`, while
  its send (`:163`) takes `cfg.get("max_tokens") or 8192`; more code defaults sit on node config
  (`db or "typedb"` `:209`, `examples or 0` `:225`, harbor's `environment or "docker"` `:1112`, and the
  default DB credentials, `dbllmbench.py:217-221`: `_DB_DEFAULT_USERS` and `password or "password"`,
  beside `_DB_DEFAULT_URLS`), against
  §0.5's "a default goes in the dataset's file, never code" and "an unreported call binds at the most it
  may cost". Action: the window is read off the model card, one `LLMSpendBound` builder serves the three
  hooks, and `max_input_tokens` leaves `harbor.py::_SEND_KEYS`; a missing node value refuses at
  validation, as harbor's does now; the gateway route moves into `docker_host.py::run_cell_container`.
  **Rides with:** rn-conn 2 (its Owns hold both connectors' string reads); its Owns gain
  `dspy_module.py`, `docker_host.py` and the compose file. **Re-test:** `git grep -c max_input_tokens --
  promptpotter` is 0, `git grep -c host-gateway -- promptpotter` is 1, and
  `git grep -cE "cfg\.get\(\"[a-z_]+\"\) or " -- promptpotter/connectors` is 0, and
  `git grep -c "_DB_DEFAULT_USERS" -- promptpotter/connectors` is 0.

### mob (links 1-3 landed at 46; mob 2 builds the header fold)

The touch project (390 px, `pointer: coarse`) skips with its reason the views these rows still fail:
phone-375/393/412 × "every view fits" and × "lineage forest fits" (the Candidates header, +10 px), and
"pinned cycle" (the masthead id). Each row turns its skip green.

- **A one-row primitive has no overflow strategy** (✓): `Toolbar.module.css:6` `flex-wrap: nowrap`,
  `Chip` `flex: 0 0 auto`, `.card-title` and `.cand-title` nowrap: on touch the Candidates header
  overflows its card by ≈10 px now that the touch floor is a `::before` hit area (landing 46), Compare's
  Levels and the account tabs likewise; `components/CLAUDE.md`'s "a card header is ONE row" states no
  overflow. Action (operator, 2026-11-13): on a phone the primitive folds its trailing actions into ⋯,
  so a card header stays one row. **Rides with:** mob 2.
- **A phone never shows a run's read failures** (✓): `masthead.css:34-35` hide `.run-id` and
  `.run-setup` at ≤880 px, and `RunMasthead.tsx:49-51` holds the only cycle-id CopyButton while
  `.run-setup` (`:115-140`) holds the served `lines` ("what could not be read" included), the criterion,
  the inner breadcrumb and the vendors. Action: the setup line folds under the title, clamped and
  tap-expandable, and the id moves where the ⋯ strategy above puts actions. **Rides with:** mob 2.
- **The phone walk never walks back from the list screen** (mob's census): one column shows the opened
  Compare alone, so `webapp/e2e/walk/views.spec.ts`'s Compare tick skips on a phone, and no spec walks
  from a pane back to its list. Action: the walk takes the back step the phone layout offers.
  **Rides with:** mob 2. **Re-test:** the views spec runs its Compare tick on the touch project.

### webb-4 (a webb re-plan on addr's tip; its landing is not yet placed)

- **U5: one viewed searchpoint.** Three hosts (`searchpointCopyChoices(`) assemble a picked arm from
  several reads each, and the Leader and the inspector show one pick at once. Action (operator,
  2026-11-05): one rendering that hosts give identity only; the Leader always shows the leader; the pick
  (`workspace.tsx::pick`) renders once, where `ScoringInspector.tsx::ScoringInspector` stands. Which
  existing read serves the picked arm's spec is decided first: a new read breaks R7. **Rides with:**
  webb-4. **Re-test:** `git grep -n "searchpointCopyChoices(" -- webapp` is 1.
- **The sample set has one store and three writing intents**: the hash holds it and
  `workspace.tsx::setViewKeys` writes it, but the picker, the overlap toggle
  (`useCandidatesModel.ts::stepOverlap`) and the trajectory seed (`TrajectorySeriesView.tsx`) each write
  through `setSelectionForSampleSet`, and `FileTree::onSelect` is drilled through its parents. Action:
  decide whether "open the picker seeded with X" is one intent, then one door. **Rides with:** webb-4.
  **Re-test:** `git grep -l "setSelectionForSampleSet" -- webapp/components` lists 1 file.
- **The output contract is flattened in the browser**: `lib/derivations/output-contract.ts::outputContract`
  walks a JSON Schema with its own `MAX_DEPTH = 3`. Action: `domain/pipeline_schema.py::NodeOutputSchema`
  serves the flattened contract rows and `shell/node-surface/NodeSurface.tsx` draws them. **Rides with:**
  webb-4, after rk-run (which holds `pipeline_schema.py`); its Owns gain both files. **Re-test:**
  `git grep -n "MAX_DEPTH" -- webapp` is empty.
- **Adapters over a flat served shape**: `useCandidatesModel.ts` joins about twelve sources; `.facts.` is
  opened at many sites; liveness is re-derived at each `phaseWalks(` site. Action: the served rows carry
  what the adapters join. **Rides with:** webb-4. **Re-test:** `git grep -c "\.facts\." -- webapp/components webapp/lib`.
- **A channel's seed is a store latch written in render**: `compare/ChannelCard.tsx:35`'s
  `view.select(seed)` sets `lib/state/compare-selection.ts::ChannelView.seeded` during render. Action: the
  pick reads `picked ?? seed` until the operator picks; the flag and the render-phase write go. **Rides
  with:** webb-4. **Re-test:** `git grep -n "seeded" -- webapp/lib/state/compare-selection.ts webapp/components/compare/ChannelCard.tsx` is empty.
- **`NodeConfigEditor`'s `schema` is a source prop two hosts fill from different reads**:
  `lib/types/connector.ts::NodeSchemaReading` is threaded through 8 files. Action: the editor reads
  `useConnector` at an identity prop. **Rides with:** webb-4, which lands first:
  `lib/derivations/connector-state.ts::nodeSchemaReading` is shared with lw's barrel row. **Re-test:**
  `git grep -c "NodeSchemaReading" -- webapp`.
- **Teaching prose in `title=` outlived its shipped lint**: `webapp/eslint.config.mjs::titleIsALabel`
  counts a title's static characters only, so 8 hits stand (`AccountActivityTab`, `CleanupConfirmModal`,
  `ForestCard`, `SampleSetControl` ×2, `ConnectorInspector`, `NodeConfigEditor`, `RemoteControl`), and an
  instruction under 60 characters ("Click to toggle.", "Open its dashboard.") passes. Action: the lint
  reads a template title's words too, and each hit says its prose through `ui/Term` or `ui/HoverCard`.
  **Rides with:** webb-4. **Re-test:** `git grep -cE 'title=("[^"]{60,}|\{\`[^\`]{60,})' -- webapp/components`.
- **The webapp's read state, past the feed**: the feed took the cadences and `syncBeat`; what it did not
  touch still stands to re-test (c714ec217 fixed `usePickedPoint`'s and `ChannelDetail.tsx`'s failure
  clauses): a `/me` failure reported twice, by the cache (`read-cache.ts:196`) and by `state/auth.tsx:72`,
  which also fires on a 401; `api/reads.ts::couponRead` through `once()` without a validator
  (`reads.ts:77-86,131-133`); evidence hashed after the build (`routers/reads.py:164-165` via `_hashed`,
  `:97-107`); "has measurements" asked through `round-reading.ts:12`'s `completed.includes` beside the
  served `round_axis.unfiled`. Open question: `channel-hooks.ts:164-169`'s `spentTo` may still read null
  mid-round. Action: one owner each. **Rides with:** webb-4. **Re-test:** each clause, first.
- **`useEvidence`'s pulse re-derives what the feed says**: `compare/ComparePane.tsx:73` and
  `hooks/useOriginEvidence.ts` build the pulse that re-asks a settled course's evidence. Action: the pulse
  goes, and the evidence read names its subject. **Rides with:** webb-4. **Re-test:**
  `git grep -n "pulse" -- webapp/lib/hooks/useEvidence.ts` is empty.
- **A lift's name is composed twice**: `webapp/lib/derivations/paired-reading.ts::liftVersus` re-spells
  `paired_reading.py::lift_versus`'s "vs {over} {words}" (the words are generated, the composition is
  not), because the round read serves no label (`RoundView.candidates[].reading.vs_reference`). In
  `components/files/RoundFileView.tsx` the lift column's name comes from the first served arm's cell
  set alone, and its hint reads "on the cells both measured" whatever that set is. Action: the reading
  serves its lift's name, which `RoundFileView` and `shell/searchpoint/SearchpointDrillIn.tsx` read;
  `liftVersus` and the hint's fixed wording go. **Rides with:** webb-4. **Re-test:**
  `git grep -n "liftVersus" -- webapp` is empty.

### batch/27 · rec

- **Tagged unions spelled as optional fields and a validator**: the engine holds 46
  `model_validator(mode="after")`, many guarding "field X is set exactly when the state is Y"
  (`RunPhaseRecord`'s `_paused_says_why` and `VerificationGradedView`'s `_refused_reads_nothing`, read in
  code; `_gate_says_which`, `_state_fits`, `_verdict_or_why_not`, `_held_or_why_not`, `_refusal_fits`,
  `_a_floor_measured_no_lift`, `_only_a_parent_is_unwalked` by name), and every reader re-checks None.
  Action: a census first; then a discriminated union per such record, deleting the validator and the
  readers' None branches, so the illegal state cannot be written. A renamed record field wipes run
  data, the landing's normal path. **Rides with:** rec. **Re-test:** the census's records carry no
  state-fitting `model_validator`.
- **Fields written, never read**: no engine, webapp or test code reads `Campaign.lifecycle_changed_at`
  / `lifecycle_reason` (written by `jobs/mint.py` and `campaign_store/store.py`),
  `PriorStanding.n_paired` / `n_discordant` (every reader takes `p_better` alone), `CycleIndex.open_rounds`,
  `HardSamples.round_order`, `InitExitView.origin_samples`, `round_health.py`'s `transient_count` /
  `output_ceiling_count`, `DraftFile.source_file`, `BankLabel.labelled_at` or
  `InnerCellFacts.origin_headroom` (unless a panel dumps the object). Found by name, so a
  `case X(field=…)` read escapes it: confirm each before deleting. Action: they go, and a gate check
  holds every declared field to a reader, with an allowlist for timestamp stamps and a baseline that
  only falls. **Rides with:** rec. **Re-test:** the check's baseline is 0.
- **An absent round fitness reads as zero**: `domain/round_diagnostics.py::RoundDiagnostics`'s
  `fitness` defaults to `0.0`, against `docs/developer/conventions.md`'s "Absent is not zero"; `domain/CLAUDE.md`
  licenses it ("reporting … every field defaulted", so a file written before a rename still reads),
  which is a reader of two shapes the no-backcompat rule already forbids, since a rename wipes run data.
  Action: the reporting fields are required or `None` where absent, and the license line goes. **Rides
  with:** rec. **Re-test:** `git grep -cE ": float = 0\.0" -- promptpotter/domain/round_diagnostics.py` is 0.
- **One act on an arm writes two records**: `runner/round.py` and `scoring/query_loop.py` append an
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
- **A cold server folds every ledger before the first page answers**: the first `GET /hierarchy?depth=1`
  waits on a full `LEDGER_FOLDS` pass (37 µs a line cold, 2026-11-07); the boot sweep no longer runs
  beside it. Root: pydantic validates each screened line on every cold start; a fold snapshot keyed by
  `file_sig` makes a restart pay only the tail. **Rides with:** rec 4, the records' fold; its Owns gain
  `store/read_model.py::LedgerIndex` (the snapshot) and `store/data_root.py` (its `CACHE` row).
  **Re-test:** time a cold `GET /hierarchy?depth=1` on the desk's data root.

### batch/27 · la

- **CLI `--json` says a refusal on both streams**: `campaign_runner.py::main` prints the `Refusal` JSON to
  stdout and `Error: …` to stderr. Decided (2026-11-10): one stream. **Rides with:** la 1. **Re-test:**
  `python -m promptpotter resume --json --campaign nope 2>&1 >/dev/null` prints nothing.
- **Wave A — presentation and infrastructure, no digest re-key**: the CLI as five modules
  (`cli/campaign_runner.py` + `parsers.py` + `commands/verbs.py` → `cli/main.py`; small read verbs →
  `commands/reads.py`, measure verbs → `commands/measure.py`); `infrastructure/identity/` 12 → 3
  (`oidc`, `grants`, `accounts`); `infrastructure/projections/live_dashboard/` → one module; LLM wire types into
  `llm/base.py`. Close with `gate.py --only layering,help-imports,handler-tables,doc-claims,instruction-files`.
  **Rides with:** la 1-2.
- **`Verb.needs_registries` carries two decisions**: seven verbs declare it `False` (`reset`, `reindex`,
  `close-admission`, `land-data`, `trail`, `offer-extras`, `pin-roster`; `verbs.py:337,391,748,774,784,792,824`),
  and skip the landing with the registries, so they read and write the data root unlanded (`cli/campaign_runner.py::main`). Action: every registry
  completes at import, the flag goes, and every verb lands. **Rides with:** la 3. **Re-test:**
  `git grep -n "needs_registries" -- promptpotter` is empty.
- **The fold roster rests on side-effect imports**: `initialization/wiring.py` joins `health` and
  `event_stream` to `LedgerIndex`'s roster by `noqa: F401` imports, and
  `read_model.py::LedgerIndex.check_every_arm_read` runs only under `every_treatment=True`, so no CLI
  process checks it. Action: the roster completes at import with the registries, and every entry checks
  it. **Rides with:** la 3. **Re-test:** `git grep -n "joins the roster" -- promptpotter/application/initialization/wiring.py` is empty.
- **`main.py`'s CORS still allows `If-Modified-Since`**, which no client sends since the dashboard reads
  by ETag. Action: the header leaves `allow_headers`. **Rides with:** la 2. **Re-test:**
  `git grep -n "If-Modified-Since" -- promptpotter/main.py` is empty.
- **The run-state derivation cannot move into `stores.py`**: `infrastructure/runtime_flags.py`'s
  `derive_run_state`, `run_owed`, `read_run_state` and `run_state_stamps` read the ledger and the producer
  lock that `store/stores.py::Stores.declare_stop` and `::declare_ended` write, but `stores.py` imports
  `CampaignStore`, and `campaign_store/store.py:68,254` (`run_state_stamps`) reads the run state, so
  the move closes an import loop. life renames the file (`runtime_flags.py` → `run_state.py`), so this
  row keeps only the leg: life's `run_state.py` (the derivation, `run_owed`, the stamps and
  `RECENT_STEP_S`) imports neither `stores.py` nor `campaign_store/`, and both read it.
  **Rides with:** la 2, a regroup by subject, after life's rename; its Owns gain the import lines la
  already takes (`store/stores.py`, `maintenance/archive_maintenance.py`, `identity/default_claim.py`,
  `campaign_store/store.py`, `scripts/{build_ts_types,offline_run}.py`, `tests/test_security.py`) and
  `llm/heartbeat.py`'s doc line (`cycle_reads.py` imports none of it). **Re-test:**
  `python scripts/gate.py --only layering` is green with `stores.py` reading life's `run_state.py`.
- **`run_owed` re-derives `SILENT`'s fact beside `derive_run_state`**: `runtime_flags.py::run_owed`
  reads the last phase and the launch claim again, because `derive_run_state` cannot tell a lock
  holder's own hold from a producer's. Root: the derivation takes the reader's hold as an input.
  **Rides with:** la 2, with life's rename above, in the same files. **Re-test:**
  `git grep -c "def run_owed" -- promptpotter` is 0.
- **Two spellings of "a producer holds it"**: `runtime_flags.py::producer_attached` derives the whole run
  state, ledger and inbox included, to answer a lock probe, while `campaign_store/store.py::run_held_cycles`
  asks `producer_lock.cycle_held` directly. The `account_spend` readers are gone (df701cd20). Root:
  `producer_attached` (`runtime_flags.py:162-164`) goes, and its readers ask `producer_lock.cycle_held`
  (`maintenance/archive_maintenance.py:57`, `scripts/offline_run.py:714`, `tests/test_security.py:1511`;
  `identity/default_claim.py:35`'s read goes with money 5). **Rides with:** la 2, with life's rename
  above, in the same files. **Re-test:**
  `git grep -n "producer_attached" -- promptpotter scripts tests` is empty.
- **The connector registry names an application module by string**: `connectors/__init__.py::_APPLICATION_BUILT_INS`
  loads the recursion's connector from `application/` by name. Root: it registers where the application
  completes its registries. **Rides with:** la 3 (every registry completes at import); its Owns gain
  `connectors/__init__.py`'s `_APPLICATION_BUILT_INS` (rk-run's and xconn's file, named shared).
  **Re-test:** `git grep -n "_APPLICATION_BUILT_INS" -- promptpotter` is empty.
- **`application/notices.py::answer_operator` is a one-line ring wrapper** the layering rule forced
  (`presentation/admin_bot.py` may not reach `infrastructure/`). Decided (2026-11-10): it goes, once the
  admin bot has a sanctioned infrastructure seam (`scripts/gate.py::_PRESENTATION_IMPORTS_INFRASTRUCTURE`).
  **Rides with:** la 1, the presentation's reshape; its Owns gain `application/notices.py`,
  `presentation/admin_bot.py` and that `scripts/gate.py` line (rel's file, named shared). **Re-test:**
  `git grep -n "answer_operator" -- promptpotter` is empty.

### batch/27 · lw

- **Modules off `webapp/lib/CLAUDE.md`'s "a module lives by what it IS" rule**: to `state/` — `lineage`, `poll`,
  `workspace`, `scoring-mask`, `chat/thread` (`hard-samples` and `useConnector`'s provider are gone,
  c714ec217); to `derivations/` — `cell-means`, `prompt-fields`. Chrome holding a surface: `shell/searchpoint/`,
  `shell/scoring/`, `shell/node-surface/`, root `PairedLift` / `SummaryBlock`; a surface holding chrome:
  `dashboard/pipeline/`. **Rides with:** lw 1. **Re-test:** `git ls-files ':(glob)webapp/lib/*.tsx'` lists none.
- **The derivations barrel reaches the network layer**: `derivations/connector-state.ts::nodeSchemaReading`
  imports `hooks/useRead::shownData`. Action: it takes the read's body, not the `ReadResult`. **Rides
  with:** lw 1, after webb-4's `NodeSchemaReading` row (same function, webb-4 first). **Re-test:** `git grep -n "shownData\|read-cache" -- webapp/lib/derivations ':!*__tests__*'` is empty.
- **`lib/hooks/useRegistry.ts:13` and `lib/workspace.tsx:41` import `derivations/hierarchy` past the
  barrel**; the cycle that justified it (barrel → search-point → poll → workspace) is gone (a96880754), so
  the deep imports dodge nothing. Action: both re-point at the barrel. **Rides with:** lw 1.
- **`hooks/useLocalStorage.ts::STORAGE_KEYS` mixes hook and non-hook keys**, and `sidePanelKey`'s
  `side-panel:*` has no `promptpotter.` prefix. Action: one key table, every key prefixed. **Rides with:**
  lw 1. **Re-test:** `` git grep -n '`side-panel:' -- webapp/lib `` is empty.

### webd (the edge tail, after lw)

- **The browser recomputes engine facts**: `webapp/lib/derivations/` (~2.2k lines, plus tests) derives
  node config, spend, run phase and the search point the engine already computes. Action: each fact is
  served on its existing read and the TypeScript copy and its tests go; the derivation rows filed
  elsewhere (`output-contract.ts`, `paired-reading.ts::liftVersus`) close inside it where their chain
  has not; so do `wonOnTheta` (9 hits in 5 files; its filed row and `electedMetric` went, ec96b1cda) and
  `ConfigMode` (`lib/derivations/node-config.ts:11`; no row files it). **Rides with:** webd. **Re-test:**
  `git ls-files webapp/lib/derivations | grep -v __tests__ | xargs wc -l` falls by the served facts.
- **A hand-kept mirror of the served shapes**: `webapp/lib/test-fixtures.ts` (616 lines, imported by 23
  webapp files) hand-writes default builders for ~30 Python-served types (`configParam`,
  `pairedReading`, `armReading`, `runStanding`, `dash`, `liveRow`, `roundDoc`, …). Since 2026-08-15 it
  changed with `live_dashboard/state.py` 53 times and `domain/dashboard_rows.py` 38 (`view.py` went,
  14ee22b61): the repo's most frequent cross-language ripple. Action: the fixtures
  are generated from the Python side as `scripts/build_ts_types.py` generates `types.generated.ts`,
  their values read off the offline world's served run (`scripts/offline_world.py`), drift-checked by
  the gate; the hand builders go. `tests/factories.py` (1360 lines), its Python twin, co-changed with
  `domain/results.py` 31 times and `domain/run_records.py` 33 times since 2026-06-01, and draws from the
  same snapshot. **Rides with:** webd, whose Owns gain `scripts/offline_world.py`, the file shared with
  tests-w, which owns it. **Re-test:** `webapp/lib/test-fixtures.ts` holds no
  hand-written builder of a served type.
- **Served fields the browser never reads**: 157 of the 926 field names in
  `webapp/lib/api/types.generated.ts` (17%) appear in no webapp source outside the generated types,
  fixtures and tests: the whole `campaign_config` (`halt_at_fitness`, `origin_gate`, `panel_gate`,
  `run_seed`, `task_framing`, `bench_trigger`, …), statistics (`se_clustered`, `kendall_w`,
  `rank_agreement`, `snr`, `theta_se`, `delta_se`, `edit_effect_sd`, …), spend detail (`cache_read`,
  `reserve_usd`, `incurred_usd_ratio`, `search_usd`, …) and refusals (`lookahead_refusal`,
  `refused_efforts`, `indistinct_efforts`); `ReadKind.HEAD_TO_HEAD_PRICE` is the one read no webapp
  source calls, so a head-to-head launch never shows its price. Accumulated data reaches the dashboard
  (operator), so each field is shown or deleted server-side; request fields of kinds the browser does
  not press (`from_round`, `keep_results`, …) come out of the count first. Action: a gate check holds
  every served response field to a webapp reader, or to its model naming why not (as `Verb.unspelled` /
  `terminal_only` do for the CLI), on a baseline that only falls. **Rides with:** webd. **Re-test:**
  the check's baseline is 0.
- **Sizes are literals beside the tokens**: 992 literal `px` / `rem` sizes across 45 non-token CSS files,
  and font literals in the charts (`CostStrip`, `EvidenceCharts`, `FitnessChart`), against the
  tokens rule; form fields are skinned outside `base.css` (`scoring.css:18`, `chat.css:24`,
  `onboarding.css:44`). Action: each size reads a token of
  `webapp/app/styles/foundation/tokens.css`, fields skin in `base.css` alone, and a gate check holds
  the literal count to a falling baseline. **Rides with:** webd. **Re-test:** the check's baseline is 0.
- **The webapp's live state lands on the wrong subject or the wrong version** (a live-state pass;
  reported unless ✓): ✓ `useCommand` lands its result on whatever subject is in view (20 of 30 slots
  unscoped): scope clears the failure only at `useCommand.ts:44-48`, and `then` / `setFailure` /
  `setPending` land after the scope moved (`:62-81`); feed versions are unordered hashes
  (`cycle_reads.py:221-224`, a sha of `repr`), so a stale read reverts a draft edit, an ack shows applied
  before the feed moves, and a second press files a second start-run (root: a monotone version per
  subject); ✓ the cycle-events `EventSource` never reopens after a non-200 reconnect
  (`useCycleEvents.ts:43-44`'s `onerror` only sets `connected=false`) while `read-cache.ts:400-404`
  handles it, so the rule is spelled twice; time-ray pages are not contiguous with the head (earlier
  pages ride a fixed cursor, `reads.ts:603-626`); reads with no subject break I10 (`useRound` mixes
  sources, `useRound.ts:40-46`); the feed opens on any subscription, auth or not (`read-cache.ts:415` →
  `:383-385`); `dropCachedRead` runs while the read has subscribers (`read-cache.ts:300-310`, caller
  `useIngestFlow.ts:374`). **Rides with:** webd.
- **The served contract has three hand copies** (✓): `commands.ts:103-108` hand-writes `Partial`
  `ConfigOverrides` / `ForkSeed` beside the generated `CommandSent`; SSE frames are cast with `as`
  (`useCycleEvents.ts:49`, `read-cache.ts:390`); the build handshake covers fetches, not streams; and
  the client decides what Delete files (`CampaignMenu.tsx:44-54` vs `workspace_edits.py:62-77`).
  **Rides with:** webd.

### batch/28 · lb

- **Wave B — `application/runner/` is 10 modules, not 8.** lb collapsed it by phase (21 modules to
  10) and deleted the ledger-scan forwarders. The two left over the target: `termination.py` (row
  below) and `inner/narrative.py`, whose whole text is the L4 estimator digest
  (`inner/tasks.py::measurement_modules`). The registry and `knobs.py` fold is the rk-run row below.
  **Held:** the termination row. **Re-test:** `git ls-files 'promptpotter/application/runner/*.py' | grep -vc __init__` is 8 or fewer.
- **`runner/termination.py` is a phase module that `jobs/` reads**: `jobs/registry.py` imports
  `run_stop_reason`, so the stop vocabulary cannot join a phase module without a cycle
  (`scoring/closed_rounds.py` → `pipeline_resolve.py` → `jobs/quota.py` → `jobs/registry.py`). Action:
  the stop vocabulary moves to `domain/phases`. **Held:** no chain reaches `domain/phases` yet.
  **Re-test:** `git ls-files promptpotter/application/runner/termination.py` is non-empty.
- **A line's ledger spans are spelled at every reader**: `LedgerSpan(stores.campaigns.ledger(h)) for h
  in stores.campaigns.line(...)` sits in `runner/bench.py` (×4), `evidence/head_to_head.py` and
  `head_to_head_price.py`. Action: the campaign store serves a line's spans. **Held:** no chain owns
  `campaign_store/` reads yet. **Re-test:** `git grep -c "stores.campaigns.line(" -- promptpotter/application` sums above 1.
- **The optimizer registry is outside `optimizers/nodes.py`.** The registry fold waits until optimizer-unit
  coverage no longer rides `optimizers/__init__.py` (`source_scan.py::optimizer_prompt_shapers`):
  `nodes.py` is a `PLUMBING_MODULES` stop, so the fold drops `llm_nodes`, `unfit_pipeline` and
  `_members` out of every treatment digest, so it waits for the behaviour keys' digest walk. With the
  `knobs.py` fold, it **rides with:** rk-run 1.
  **Re-test:** `promptpotter/application/optimizers/__init__.py` defines `NODE_MEMBERS`.

### batch/28 · faults

- **Q3: one fault channel — a fault is a value.** A failed or skipped step is a log line at many sites
  (engine decisions narrated into `logger.warning`, `origin_listing.py::list_origins` skipping an origin
  silently, `campaign_store/store.py::CampaignStore.list_campaigns` and
  `tenant_dataset_store.py::banks_sharing` logging an unreadable entry out of every list,
  `intelligence/earned_blocks.py::CampaignSurvey.faulted` written and never read, `llm/heartbeat.py`
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
- **A `derived_file_stale` warning names the projection's class, never the file it could not write**:
  `read_model.py::LedgerIndex.shielded` has only the `Projected` protocol, which carries no path. Action:
  a `derives` ClassVar on each `Projected`, which the warning names. **Rides with:** faults 1, a fault
  the readout serves; after narr, money and rec, whose derivers it reads, so its Owns gain one line each in
  `views/readout.py::ReadoutProjection`, `projections/cycle_index.py::CycleIndexProjection`,
  `scoring/closed_rounds.py::RoundFileProjection` and `llm/spend_book.py::SpendBook`, with
  `domain/run_records.py::stale_file_warning` and `read_model.py`'s `Projected`. **Re-test:** a
  `dashboard.json` write fault's warning detail names `dashboard.json`.
- **A text node's form failure has three answers**: capo's init out of form ends the cycle `crashed`,
  gepa's `fenced` reads an unfenced reply as the instruction (so its form never fails), and every other
  text node (capo's crossover, levi's seed and refine, potter's L1 and L2) refuses its child. det's
  `wounded/` legs drive the third; the first cannot sit in a green leg. Action: one answer, the child
  refused as a fault value; capo's init and gepa's `fenced` read it. Decisions move on the wounded legs
  only: name them. **Rides with:** faults 1. **Re-test:** a capo init and a gepa reflect out of form each
  read as a refused child on `wounded/`.
- **`ValidationFailure.reason`'s description lists its codes by hand**: it named the dead
  `PARSE_FAILURE_WRONG_TYPE` and omits `l1_all_no_op_variants`; the text is hashed into every treatment
  digest (det 2 moved all five). Action: the codes are an enum the field types, and the description
  goes. **Rides with:** faults 1. **Re-test:** the field's type is the enum.
- **A refusal carries prose, a stop carries acts** (✓, a refusal census: 337 `PotterError` raises): a
  refusal is `domain/refusal.py::Refusal` {code, message, details} while a run stop carries
  `next_acts: tuple[NextAct]` (`phases.py:495-522`) each door spells, so 20 served refusals spell CLI flags
  or verbs (`repair.py:230` `resume --from N`, which the browser lacks; `passes.py:333` `--samples`;
  `store.py:592` `--from`; `entry.py:477` `--diag`; `cycle_addressing.py:134`, `loaders.py:575`,
  `preflight.py:212-213` and `:308` say `mint-campaign`, wrong at the terminal too, where the CLI verb is `new`), and ~12 of 74 409s name no next step (`session.py:154`,
  `fork_siblings.py:141`, `store.py:523`, `passes.py:512-531`, `passes.py:639`, `scoring_scope.py:148`).
  Root: `Refusal` carries `next_acts`, each door spells the act, and no message names a verb or flag
  (`phases.py:522`'s rule extended); this subsumes the `ConfigDriftError` row. Beside it (✓):
  `repair.py:226`'s `except Exception` wraps any rederive failure, bugs included, as a 409 telling the
  operator to `resume --from` (root: catch the declared rederive refusal only); a definition whose
  `source:` lacks recipe or rev raises a bare `ValueError` (`loaders.py:87,104`), swallowed by the listing
  into "Its samples could not be read on this install" (`startability.py:143-145`) and a 500 at start
  (`main.py:270-279`) (root: `Source.of` raises a refusal naming the field); ✓ `OPTIMIZER_ABORT` has an
  empty `next_step` and no acts (`phases.py:614-616`) and its reason is only logged (`escalation.py:516-531`),
  a state the system enters but cannot report. **Rides with:** faults 1.
- **The LLM layer's wire faults**: `infrastructure/llm/openai_compat.py::_strip_titles` drops every key
  named `title`, so a property called `title` leaves `properties` while `required` still names it
  (latent: no wire schema has one yet); `send_pacing.py:302` reads `Retry-After: 0` as absent and takes
  the 15–300 s cooldown. Reported, verify first: `json_parse.py:178` a ValidationError from Groq's
  `failed_generation` salvage escapes the repair ladder as UNKNOWN; `openai_compat.py:648` a 200 reply
  with an error envelope classifies UNKNOWN and drops its usage; `send_pacing.py`'s `RateLimiter._acquire`
  sleeps the whole window, so the send deadline fires mid-wait and a pause goes unseen; `:426` Groq's
  per-day `x-ratelimit-limit-requests` is read as RPM; `:483` OpenAI reasoning models take
  `max_completion_tokens`; `:612` the `first_*` tokens fold in the repair rung's; `:485` `hold_ceiling`
  reads disk twice per attempt on the loop; `base.py:39` `wire_client`'s cached httpx client crosses event
  loops (the teleprompter runs `asyncio.run` per compile). Action: `title` stripped from schema keywords
  only; a Retry-After present is honoured at 0. **Rides with:** faults 1.
- **A depth bomb is a 500 the webapp retries** (an untrusted-input pass): `tabular_ingest.py:202-206`
  catches only `JSONDecodeError`, so `"["*200000` raises `RecursionError`; `scoring/formula/compiler.py:184`
  catches only `(SyntaxError, ValueError)`, so `"-"*100000+"1"` and `"not "*50000+"1"` raise
  `MemoryError` and `"1"+"+1"*200000` `RecursionError`; `CampaignConfig.scoring` (`campaign_config.py:349`)
  and the hierarchy read's `lens=score:` (`read_params.py:374-406`) have no length bound, and a draft
  patch's `origin_prompts`, `candidate_library` and `output_schema` only the body cap. Reported beside it:
  a non-integer ODS repeat attribute is an `int()` `ValueError` 500 (`tabular_ingest.py:431-440`). Root:
  each parser seam maps `RecursionError` and `MemoryError` (and the ODS cast) to its typed refusal, on
  the refusal root above, and each tenant-authored text field carries its own bound. **Rides with:**
  faults. **Re-test:** each input above answers a typed refusal, never a 500.

### batch/29 · lite

- **Three data-root rows are proven by presence alone** (`store/data_root.py::_WALKED`): `rates.json`, the
  model capability override and the capabilities cache, whose readers (`llm/pricing.py`,
  `llm/capabilities.py`) walk a raw dict. Action: a model per file, named by its row. **Rides with:**
  lite 2 (core's model vocabulary is its own, `rates.json` drops `litellm_provider`); its Owns gain
  `llm/capabilities.py` and `store/data_root.py`'s three rows (mkey's file too: its `KINDS`). **Re-test:**
  `git grep -c "_WALKED" -- promptpotter/infrastructure/store/data_root.py` is 0.

### batch/29 · xconn

- **PromptPotter does not read TermNorm's served contract, so a wire skew surfaces as a 100% degraded
  origin, not a refusal at check-in**: the box's TermNorm read `answer_field` while PromptPotter sent
  `answer_fields` (2026-10-31). A TermNorm schema edit lays keys only `llm_only` accepts:
  `datasets/draft_patch.py::_authored_contract` writes `answer_fields` and `response_format: json` on
  whichever node the edit names, and `domain/pipeline_parsing.py::parse_pipeline_response` offers the
  `response_format` axis on every `tunes_llm` node; TermNorm's `xconn` branch refuses both on
  `entity_profiling` and `llm_ranking` (400 `wire_key_unknown`), and `top_p` on any node. And the probe
  reads `/status` while that branch serves `GET /version` (`{revision, contract}`): `connectors/termnorm.py`
  probes `/status` and `::_EXPECTED_REVISION` is `None`, so a co-owned backend fix replays stale results;
  on the `xconn` branch `/status` loses its version word, so `_termnorm_version_check`
  (`termnorm.py:120-127`) reads None there. Action: the probe reads `/version`; the connector serves the
  wire contract and each node's accepted keys; the check-in refuses a node config that contract rejects,
  a node's accepted keys bound what an edit lays and what an axis offers (refused at the mint); and the
  revision folds into the measurement-cache key. **Rides with:** xconn 1 and 3, before TermNorm's
  `xconn` reaches the box. **Re-test:** a launch against a backend one wire rename behind fails at the
  probe, naming the key; a check-in schema edit on `llm_ranking` mints and its first cell answers;
  `connectors/termnorm.py` probes `/version` and `_EXPECTED_REVISION` is not `None`.
- **A connector's endpoint is one per install**: `config/settings.py` `TERMNORM_URL`, `N8N_HARNESS_URL`;
  a second workflow edits `.env` and re-keys every banked cell of the first (`n8n.py::_identity_config`,
  `:94`, hashes `CONNECTOR.base_url()`). Action: operator config holds named endpoints per connector, and a dataset names one;
  the credential never leaves operator config ([`connectors/CLAUDE.md`](../../promptpotter/connectors/CLAUDE.md)
  § The credential rides the connector). **Rides with:** xconn 2. **Re-test:** two n8n datasets on two
  workflows both run, no `.env` edit between them.
- **A moved capability or backend revision alone passes a resume**: it is a drift cause only when the
  origin hash also mismatches (`jobs/mint.py::resolve_resume`, `::_revision_moved`). Action: a resume
  refuses a moved capability or backend revision. **Rides with:** xconn 3. **Re-test:** a resume after a
  backend revision bump is refused naming it.
- **TermNorm words a warning's step into its message** (`research_pipeline.py`), though `StepWarning`
  carries the step as a field. Fixed only on TermNorm's `xconn` branch (11b1dbe); master (ce4f15b) and
  the box still word it (master `research_pipeline.py:278`). Action: the message is the fault alone.
  **Rides with:** xconn 4. **Re-test:** `git grep -n "failed: {" -- backend-api/api` in the TermNorm
  checkout, on master.
- **TermNorm serves `direct_prompt` as a node no step runs**: it parses, but `/pipeline` lists it as an
  llm `ranker` absent from `STEP_REGISTRY`, so PP reads its prompt fields as config axes. Fixed only on
  TermNorm's `xconn` branch (11b1dbe); master (ce4f15b) and the box still serve it (master
  `pipeline.json:246`). Action: in the TermNorm checkout, the block leaves `nodes` for a top-level
  `prompts` section. **Rides with:** xconn 4. **Re-test:** TermNorm's `pipeline.json` on master holds no
  `direct_prompt` under `nodes`.
- **`infrastructure/llm/send_pacing.py::SendBudget` stops at the relay's door**: TermNorm's
  `_MAX_ATTEMPTS`, and terminus-2's `_query_llm` thrice over `connectors/harbor.py::_AgentModel`, retry
  on their own. Patched singly, each fix is one more copy; the remedy is the upstream redesign. **Rides
  with:** xconn 4. **Re-test:** the relay's retries, not one constant: on TermNorm's `xconn` branch
  `_MAX_ATTEMPTS` becomes per-node `max_attempts` (`pipeline.json:156,232,287,325`), so a grep of
  `_MAX_ATTEMPTS` reads closed after that merge while the relay still resends; count the relay's
  resends for one 5xx instead (`grep -n "_MAX_ATTEMPTS = " <termnorm>/backend-api/core/llm_providers.py`
  on master, `max_attempts` in `pipeline.json` on `xconn`).

### batch/29 · mkey

- **No inbound credential for the campaign surface**: `presentation/api/deps.py` resolves a session
  cookie alone and reads no bearer, so a third party needs `PROMPTPOTTER_AUTH=off`.
  Action: an account mints a named, scoped, revocable token (hash at rest, a SURVIVOR row of
  `store/data_root.py::KINDS`); a bearer resolves through the one caller dependency, a new auth door
  reviewed in `access-model.md` (`identity/bundle.py::identity_of_session` per carrier; `deps.py::open_caller`
  splits the rate draw from the `Caller` build, the seam mcp 2 rides); a new section of
  `docs/developer/stable-api.md` (its § 8 is "What is NOT stable") shows submit → poll → fetch with it. **Rides with:** mkey 1, 2. **Re-test:** a bearer reader in
  `promptpotter/presentation/api/`.
- **The host's exemption ignores the door**: `jobs/quota.py::refuse_request_rate`
  exempts the host from every bucket, and auth-off makes every caller the host (`deps.py::resolve_identity`),
  so a leaked host bearer spends the box's keys unbounded. Action: the host is refused on a bearer door.
  **Rides with:** mkey 2. **Re-test:** a host-account token minted at mkey 1 is refused.
- **A new account's announcement is adapter code**: `presentation/api/routers/auth.py::_announce_new_account`
  rings and forwards the arrival to the CRM, though `account.py::sign_in_oidc` already returns its
  `rings`. Action: the announcement is an `account.py::Arrival` field the application acts on.
  **Rides with:** mkey 1, whose acts are the account's; its Owns gain `presentation/api/routers/auth.py`
  (the callback's announcement and refusal hunks). **Re-test:**
  `git grep -n "def _announce_new_account" -- promptpotter/presentation` is empty.
- **A provider's sign-in error is said only in a log**: `routers/auth.py::callback`'s `error` branch
  (`:158`) logs a warning and files nothing, where the exchange failure files `account.record_refusal`
  on the box's trail. Action: the provider's error is the same refusal, filed once in `application/`
  by the one function the Held row "The sign-in exchange-failure refusal still reads `health`" asks
  for, which both branches call; the router gains no `record_refusal` of its own. **Rides with:**
  mkey 1, beside the row above, in the same hunk. **Re-test:** a callback with `?error=access_denied`
  adds one refusal to the box's trail, and `git grep -c "record_refusal" -- promptpotter/presentation`
  does not rise.
- **A reopen serves a Start its reader may not follow**: `datasets/draft_build.py::HeldStart.of` serves
  the last `start-checkin` on the draft whoever filed it, while `command_reading.py::owned_command` hides
  a command another principal issued with a 404, so an owner reopening a delegate's Start sees
  `useQueuedStart` wait past the mint. Root: one rule for who may follow a Start, whoever may reopen its
  draft: the reopen serves the Start's own reading, or the command read lets an account in on a command
  filed on a subject it owns, never two rules. **Rides with:** mkey 4, the access review; its Owns gain
  `command_reading.py::owned_command` (mcp's file after, on mkey's tip), `draft_build.py::HeldStart`
  (rk-run's file before) and `webapp/lib/hooks/useQueuedStart.ts`. **Re-test:** a delegate Starts a
  check-in, the owner reopens it, and the page follows the Start to its mint.
- **An SSE stream keeps the identity it opened with** (✓): `reads.py:158-161` calls
  `read.answer(asked.caller)` once, `cycle_reads.py:178-181` and `:230-235` loop forever, and identity is
  resolved only in the OIDC middleware (`oidc.py:18`) with no re-check and no lifetime cap, so a revoke,
  logout or expiry leaves the stream open: a revoked delegate keeps streaming the host's ledger, prompts
  included. Root: each tick re-reads the session (`session_store.read` + `identity_of_session`) and ends the
  stream on a mismatch; `_events` (`reads.py:158`) runs once, so the check belongs in
  `cycle_reads.py::cycle_event_frames` (`:170`) and `::feed_frames` (`:227`), or a wrapper around both. Beside it (reported, sub-threshold): one-level delegation is
  enforced one way only (`grants.py:104` vs `bundle.py:91-105`); `capability=None` kinds
  (set-user-settings, accept-terms) act for blocked accounts and delegates; `dispatcher.py:354` resolves
  the prefix before the capability check (`:358`). **Rides with:** mkey (the access review), ahead of
  the edge tail's order if a delegate is ever granted on the box.

### batch/29 · mcp

- **An arms gate's hold claims `input_required` and sends no input request**: `command_reading.py::job_status`
  sets the status (the arms gate is its one holder since the origin gate's hold went); `tasks/result`
  blocks until the run ends, in `host_surface.py::_task_result` (`:398-405`), not only `job_status`. Action: the hold becomes the task's input request (the arms shape for
  `submit-arms`), answered as the existing command. **Rides with:** mcp 1. **Re-test:** a host calling
  `tasks/result` on a held arms gate receives an input request.
- **The MCP door speaks stdio only, so a browser chat cannot reach it**:
  `promptpotter/presentation/mcp/server.py::serve_stdio` is the one transport. Action: serve
  `promptpotter/presentation/mcp/server.py::host_server` over Streamable HTTP, mounted on the API behind
  its sign-in, a second transport of the one adapter, on mkey's bearer; its auth follows
  [`access-model.md`](../operations/access-model.md). **Rides with:** mcp 2. **Re-test:** in claude.ai,
  a custom connector at the box's MCP URL answers `tools/list`.
- **`main.py::API_ROUTERS` declares `APIRouter`s only**, so an ASGI mount (the webapp's `StaticFiles`,
  MCP next) sits outside the declared gate and `_off_catalogue`. Action: a mount is a declared row.
  **Rides with:** mcp 2. **Re-test:** `git grep -n "app.mount(" -- promptpotter/main.py` is empty.

### batch/29 · tv-2

The operator, 2026-11-11: the exhibit root lives in the host account and is served read-only to any
signed-in follower; `demo_mode_enabled` is its reader; with no exhibit named the route says so in a
`Said`. One access review with mkey and mcp.

- **The exhibit's served fact has no home**: what a follower follows (the running campaign, else the
  latest launch, else the host's exhibit root, else a `Said`) belongs on
  `cycle_addressing.py::ActiveSessionResponse`, whose pick is `followed_cycle`. Action: the read falls
  through to the exhibit, then to a `Said`. **Rides with:** tv-2. **Re-test:** `/stage/` with no launch
  draws the exhibit.
- **The install names no exhibit campaign**: one setting names the exhibit campaign root in the host
  account. **Rides with:** tv-2. **Re-test:** `git grep -n -i "exhibit" -- promptpotter/config` names the
  setting.
- **No read reaches the host's exhibit**: every hierarchy, lineage and dashboard read is scoped to the
  caller's tenant (`stores.py::Stores`). Action: the exhibit root is served read-only to a signed-in
  follower, the one cross-tenant read, reviewed in
  [`../operations/access-model.md`](../operations/access-model.md). **Rides with:** tv-2. **Re-test:** an
  account draws the exhibit root's hierarchy and can act on none of it.
- **`demo_mode_enabled` has no reader**: `store/user_store.py` → `account.py::user_settings`, the
  `set-user-settings` kind and the "Try & learn" switch, whose lede promises a support-ticket dataset no
  code places (`AccountPreferencesTab.tsx`). Action: it becomes the exhibit's reader. **Rides with:**
  tv-2. **Re-test:** `git grep -n "demo_mode_enabled" -- promptpotter webapp` names a reader.

### batch/30 · ws

- **`EvidenceCharts` and `CostStrip` lost their hover readouts** with the tooltip (d36f9af7e).
  Action: a DOM reading under each chart, since `webapp/components/CLAUDE.md:8` forbids painting detail
  over a canvas's own marks. **Rides with:** ws 1.
- **Two lineage layouts are derived side by side**: `webapp/components/candidates/dendrogram.ts::dendrogram`
  lays `DendrogramStrip`, and `webapp/components/candidates/forest-layout.ts::layout` lays `Forest`. The
  strip stays (operator, 2026-11-10). Action: one tree, two layouts, PAIRED and FREED, one button
  detaching and re-pairing it; one pick keyed on its `address` (`workspace.tsx::pick`) highlights both.
  **Rides with:** ws 2. **Re-test:** `git grep -hE "^export function (dendrogram|layout)\(" -- webapp/components/candidates`
  prints one line.
- **Compare cards are laid out for few wide columns**, not five thin stripes on a 16:9 display (the
  column rule is `webapp/app/styles/domains/compare.css:40-42`). **Rides with:** ws 3. **Re-test:** five
  candidates at 1920×1080 sit in a row, each card at its full content (a wrapped second row fails).

### batch/30 · forks

- **Money text has no sub-cent rule**: a cell's price at six places (`preflight.py::_RoundShort.costing`,
  `cli/commands/head_to_head_price.py::_human`, and `potter/dispatch/injections/panels.py:1179-1180`) beside `domain/spend.py::usd_text`, which prints `$0.0000`
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
- **The default host pin is a trial with no verdict**: `domain/pipeline_overlay.py::pin_default_routes`
  writes each model's default host into every node config, and `bench/llm_call.py:231` pins the optimizer's
  own calls too. Decided: a model keeps its pin only where its
  realized cache share and effective $/M beat running uncached. **Rides with:** tune 3. **Re-test:**
  `SpendRollup.by_node` in each twin's `review.md`.

### batch/31 · small

- **An extra the package no longer declares**: `shared/extras.py::Extra.OBSERVABILITY` outlived the
  Langfuse and MLflow sinks; nothing reads it and `missing()` would raise on it. Action: it goes, and a
  check holds `Extra` equal to `pyproject.toml`'s extras less `api`, `jupyter`, `all` and `dev`.
  **Rides with:** small. **Re-test:** `git grep -c OBSERVABILITY -- promptpotter` is 0.
- **Two selectors override one pair identically**: `levi/members.py` and potter's `ThetaElection`
  (`potter/members.py`) each define `parent_cells` and `parent_read` as the panel's cells. Action: the
  `Selector` default. **Rides with:** small.
- **Four settings no operator can discover**: `QUEUE_MAX_WAIT_S`, `UNPRICED_GRACE_USD`,
  `USER_RATE_BURST` and `USER_RATE_PER_MIN` appear in no `.env.example` or deploy config. Action: each
  is listed where the box's settings are. **Rides with:** small.
- **No verb sets an account's coupon or starts it afresh**: `grant.json` is hand-written, and a reset is
  moving `charges.jsonl` aside; `wallet_store.py::CouponGrant.granted_at` is written and never read.
  **Rides with:** small 1.
- **Swapping a model means hand-editing eleven `model:` lines** (4+3+3+1: potter, levi, capo, gepa;
  recounted on the current tree by `grep -c "model:" promptpotter/assets/optimizers/*/pipeline.yaml`,
  the same lines as rk-run 5's re-test). Action: a swap verb. **Rides with:** small 2. **Re-test:** one
  in `presentation/cli/commands/`.
- **Archive hygiene before the next bulk delete**: (1) reclaim, a dataset-scoped destructive `delete`,
  dry-run by default, refusing while a producer can append; (2) attribution, an L4 outer cell stamps the
  runs its inner campaign produced; (3) the entry-to-run lookup (which runs an archive entry serves,
  so a delete names what it breaks; not `pipeline_schema.py::reach_map`, a node's reach); (4) `reindex`, a maintenance write with no
  dry-run and no live-writer refusal, whose help promises "GC orphaned runs" that
  `archive_maintenance.py::reindex_measurement_archive` does not do, takes reclaim's refusal and its help
  says what it does. **Rides with:** small 3. **Re-test:** `compact-archive inventory`; `reindex`
  under a live producer refuses.
- **An immutable record is written four ways**: `FrozenModel`, `StrictModel`, `NamedTuple` and frozen
  `dataclass`. Action: `docs/developer/conventions.md` names one per role (a record crossing a seam, a
  local value); a file converts when a chain touches it, never a sweep. **Rides with:** small 5.
- **ISO stamps sort as strings at ~12 sites against `shared/clock.py::epoch_seconds`'s rule**:
  `jobs/registry.py:191,347,363,369,374`, `jobs/quota.py:711`, `evidence/read.py:404,451`,
  `head_to_head_price.py:118`, `origin_listing.py:98,142`, `application/hierarchy.py:426,496,603,612` and `registry.py:443-445`; the formatter at the root, `shared/clock.py::iso_z` (`:23-24`, also called
  by `cycle_files.py:60` and `registry.py:439`), omits a zero fractional part, so string order is not
  time order. Root: `iso_z` emits fixed-width microseconds, and the "never as strings" rule goes.
  **Rides with:** small 6. **Re-test:** `iso_z` at a whole second ends `.000000Z`.
- **Three torn-write smells from a crash-point pass (the first and third confirmed; confirm the second)**:
  `stores.py:168-169` leaves an old `origin.yaml` when the origin is empty, and is reachable
  (`teleprompter.py:157` always passes `origin={}`); `ledger_scan.py:232-244` clears `superseded_by`
  on PAUSED; `account_spend.py:152` writes no tombstone for a zero-spend delete, worse than filed: the
  tombstone is the delete's commit (`account_spend.py:73-75`), so a torn zero-spend delete reads as
  live (`store.py:229`) and its id can be minted again (`store.py:181`). **Rides with:** small.
- **Two wrong names at the doors** (reported): `--steer`'s help calls itself the web steer-fork's twin;
  `bench.py:21-27` keeps its own active-campaign default (the `mint-campaign` names close in faults).
  **Rides with:** small.
- **Three name checks stand beside the one validator** (filed by paths): `shared/identity.py::safe_name`
  matches with `$`, so `abc\n` passes as an identity slug until the store join refuses it;
  `application/cycle_files.py:68` prechecks `..`, `\` and `/` beside the `resolve` containment that owns
  it; `draft_campaign.py:159`'s `c.isalnum()` admits a Unicode letter, and `safe_name` then raises a bare
  `ValueError` out of `ingest_draft` (a 500 for `café.csv`). Root: `fullmatch` in `safe_name`, one
  containment rule, and a derived slug built in the dataset-name charset. **Rides with:** small.
  **Re-test:** `café.csv` uploads under a refused-or-derived slug, never a 500.

### batch/31 · box

- **The box holds the app's environment twice**: the services load `/etc/promptpotter.env`, and the login
  user's CLI reads the checkout's `.env` through `config/paths.py::env_file_path`, so they drift. One
  file serves both; which one is the operator's (read access for the login user, or SELinux under
  `$HOME`). The repo half stands (`config/paths.py:126-132` falls back to the checkout `.env` unless
  `PROMPTPOTTER_ENV_FILE` is set, and no deploy script sets it for the login shell); the live half is
  unchecked, since it needs the live box. **Rides with:** box 1.
- **`promptpotter/__init__.py` acts at import, on the process and on the data root**: `:4` sets
  `OPENBLAS_NUM_THREADS`, a fact of the process, not the package, and `hold_private(user_data_root())`
  writes the data root before any process's landing. Action: the unit and the process launcher set the
  environment, and the entry that lands holds the root private. **Rides with:** box 1. **Re-test:**
  `git grep -n "OPENBLAS" -- promptpotter` is empty, and
  `git grep -n "hold_private" -- promptpotter/__init__.py` is empty.
- **The box runbook skips the move `site_records/` owes**: `deploy-linux/README.md` has no step that moves
  it out before `update.sh`; only `CHANGELOG.md` and `cd8593459`'s body say it. `update.sh`'s dry-run
  refuses and rolls back, so nothing is wiped. Action: a step that moves `site_records/` out before
  `update.sh`. **Rides with:** box 1. **Re-test:**
  `git grep -n "site_records" -- deploy-linux/README.md` names the move.
- **A resume's wait is unattributed**: on the box a resume takes up to 72 s to be applied, while
  offline its `open_launch`, `open_cycle` and `close_init` total about 0.3 s and the landing stamps once
  per schema, so the wait sits on the backend's network edges (every symbol is present, but the box
  half is unchecked; bac873699, landing 41, changed the TermNorm term push, so that candidate's cost may
  have moved). Action: time `probe_backend`,
  `served_pipeline`, `ensure_model_capabilities` and the term push on the box for one paused cycle, then
  drop whichever one a resume re-does. **Rides with:** box (it needs the box measured), whose Owns gain
  `runner/mint.py` and `initialization/wiring.py`. **Re-test:** time `start-run` → its `applied`
  ack on a paused cycle.

### batch/32 · tests-w (a tests chain named at batch/25's fold; it owns `scripts/offline_world.py`)

- **`run_init_round`, `seat`, `_peer_cycle` and `close_round` replay production by hand**:
  `tests/factories.py::run_init_round` re-composes `runner/entry.py::run_optimization`'s preamble and
  `_run_single_cycle` (`mint_framed_cycle`, `land_job`, `build_run_observers`, `start_launch`,
  `at_round(0)`, `scoped`, `session_scope`, `open_launch`, `open_cycle`, `close_init`) less the fork
  branch, a third copy of the embedded mint; 14 `seat` sites (a custom `schema=`, `scorer=` or
  `origin=`), 14 `close_round` sites and 12 `tests/test_scoring.py::_peer_cycle` sites (a C0 report run
  init cannot produce) need answers `offline_world.py::FakeBackend` cannot give: it serves one
  `llm_only` pipeline. Action: one production line opener that `run_optimization` and the harness both
  call; `FakeBackend` serves the pipeline and answers a test declares; the sites move onto it; `seat`,
  `_peer_cycle`, `close_round` and `run_init_round`'s composition go. **Rides with:** tests-w, whose
  Owns gain `scripts/offline_world.py`, `tests/factories.py` (asked of ts-arch), `runner/entry.py` (the
  opener, after lb) and each site's hunk in the four test files that hold one: `tests/test_scoring.py`
  (ts-score's), `tests/test_archive.py` and `tests/test_identity.py` (ts-arch's) and
  `tests/test_security.py` (ts-spend's), each named by plan § 4 and paired in § 5. **Re-test:** `git grep -c "def seat(\|close_init(" -- tests/factories.py` is 0.
- **`tests/test_archive.py::test_a_resume_takes_the_origin_the_gate_halted_past_it_and_every_later_launch_reads_the_pass`
  walks zero cells**, so the halt passes on an empty origin. Action: hand it the origin's cells through
  the `offline` fixture. **Rides with:** tests-w. **Re-test:** the test's dataset is non-empty.
- **The offline run imports MAIN's package from a worktree**: `PYTHONPATH=.` falls through to MAIN's
  `promptpotter`, so an equals-main leg run in a worktree measures the wrong tree unless the path is
  absolute. Action: `scripts/offline_run.py` puts its own repo root first on `sys.path`. **Rides with:**
  tests-w, whose Owns gain `scripts/offline_run.py`'s path setup. **Re-test:** from a worktree,
  `PYTHONPATH=. <venv python> scripts/offline_run.py --help` imports the worktree's `promptpotter.__file__`.
- **Smells the test-shape split left** (reported by its builders): `tests/test_security.py` writes into
  the session-wide test home (owner-9's user file, the `host_tenant` accounts); `tests/test_spend.py`'s
  `_paid_door` dispatches by string, its `_doors` rows are untyped 9-tuples, `hold_usd = 0.0036` is
  spelled four times, and facts that never touch the network take the `offline` fixture only because
  `spend_book` needs the account it binds; seven `tests/test_scoring.py` bench facts re-run the CAPO round
  for a live cycle (the `_peer_cycle` row above), the command-door facts among them needing no bench
  round, and `capo_says` is spelled twice; `RunWiringRecord(...)` is built by hand in
  `tests/test_security.py::_running` and `tests/test_scoring.py` beside `factories.run_wiring`
  (overshoot's census). Action: each fact builds into its own `tmp_path` home; the
  doors are a typed row with one hold constant; a spend book binds an account without the offline
  world; the bench facts share one module-scoped round. **Rides with:** tests-w. **Re-test:** the
  session home holds no file after `tests/test_security.py` runs alone.
- **One guard is tested once per door**: tests grew +2.3k lines in three days, mostly security and
  identity, a test per door of one guard; the test-shape split (landing 43) kept every assert. Action:
  a guard living at one choke point is tested once, over the table of its doors; a guard spelled at
  more than one door is a row for the chain owning it. **Rides with:** tests-w.
- **Two tests stand outside the harness**: `tests/test_archive.py` fakes a `HeldRun` with a
  `SimpleNamespace`, so a renamed field passes there, and `tests/test_identity.py` `rglob`s both shipped
  dataset tiers, a repo-wide scan per run, beside the suite's dataset reader
  (`bench/task_context.py::committed_task_context`). Action: the real record, and the committed datasets
  read once through that reader. **Rides with:** tests-w. **Re-test:**
  `git grep -c "SimpleNamespace\|rglob(" -- tests/test_archive.py tests/test_identity.py` is 0.

### batch/32 · ts-arch

- **The treatment digest's edit probe patches `Path.read_text`**:
  `tests/test_identity.py::test_a_prompt_shaping_classs_method_rekeys_the_run`
  blocks keying `source_scan.py::prompt_source_digest` on a once-per-process tree hash (~250 ms per
  boot). Action: the probe writes the edit to a tree copy under a patched `_PACKAGE_ROOT`, then the tree
  key is process-cached; the test's assertion at `test_identity.py:504-512` forbids that cache and changes
  with the action (`nodes.py::_source_digest` is already `functools.cache`, `nodes.py:395-405`).
  **Rides with:** ts-arch 3. **Re-test:** `git grep -n "read_text" -- tests/test_identity.py`.

### batch/32 · ts-score

- **The capo offline bench turns on one seed**: lift +0.700 or none after a draw-order change. Action: it
  reads two seeds. **Rides with:** ts-score 3.

### batch/32 · ren

Each rename lands with its doc and `CLAUDE.md` row in one commit. **Re-test:** the old word's
`git grep -c` for that sense is 0.

- **`overshoot` names a sleep's excess** (overshoot's census): `heartbeat.py`'s local `overshoot` is a
  timer's lateness, while the word now names a bill past its hold. Action: `lateness`.

- **Six class names name two things each**: `Cut` (an arm's cut state in `arms.py`, a fork cut in
  `cycle_paths.py`), `PanelCoverage` (`dispatch/compose.py`'s produced / placed / chars, a checkpoint's
  holes by arm in `run_records.py`), `FailureCluster` (`intelligence/indexes/sample.py`, the
  `dispatch/schemas.py` wire model), `Provenance` (`views/render/export.py`'s export header,
  `origin_provenance.py`'s StrEnum), `Progress` (`bench/node_context.py`'s `CycleRead`,
  `ledger_scan.py`'s liveness tuple beside its own `_Progress`) and `Reported` (`prompt_block.py`'s
  published metrics, `spend_book.py`'s billing parts). Action: each is renamed to what it holds, and a
  gate check refuses a top-level class name defined twice. **Rides with:** ren. **Re-test:** the
  check passes with an empty allowlist.
- **Two functions named for another act**: `initialization/wiring.py::read_treatment` returns None and
  raises (a check, named as a read); `views/render/export.py`'s local `needs_export` returns a
  `PromptExport` (a getter, named as a predicate). Action: each named for its act. **Rides with:** ren.

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
- **Two `knobs.py` and two `free_tier.py` modules**: `application/knobs.py` (the served knob
  projection) beside `optimizers/potter/knobs.py`, and `domain/free_tier.py` beside
  `application/free_tier.py`. Action: each named for what it holds, or the pair folds where one fact
  has two homes (payer's census reads the free-tier pair first). **Rides with:** ren 8.
- **Node config is one document read through ~11 modules**: schema, parsing, overlay and knobs each
  hold a view of it after landing 30's one layered read. Action: one model of the node document with
  knobs a view on it. **Rides with:** cfg (its declared merge rule).
- **`_DETACHED` names two things**: `jobs/launch.py:29` and `run_job.py:56`. **Rides with:** ren.

### batch/32 · rel

- **A renderer outside `views/` keeps an extra for one paper script**:
  `presentation/terminal/completion.py::report_completion` has one caller,
  `paper/bbeh-comparison/bbeh_potter_runner.py:38`, and alone keeps the `jupyter` extra. Action: pin the
  paper script to its release, then delete the module and the extra. **Rides with:** rel 1. **Re-test:**
  `git grep -n "terminal.completion" -- paper promptpotter` is empty.
- **The wheel's "install it elsewhere and run it" is written twice** (`.github/workflows/ci.yml:78-87`,
  `publish.yml:89-100`). Action: `scripts/smoke_wheel.py`, which already exists and runs inside the
  venv, is extended to take the wheel and make its own venv; each workflow calls it once. **Rides with:** rel 1. **Re-test:** `git grep -c 'RUNNER_TEMP/elsewhere' -- .github` is 0.
- **The example plugin's `resolved_schemas.json` is tracked, and no gate run writes it.** **Rides with:**
  rel 1. **Re-test:** edit it by hand; a gate is red.
- **`scripts/build_optimizer_schemas.py` names its outputs by absolute path.** Action: repo-relative
  wherever the output is in the checkout. **Rides with:** rel 1. **Re-test:** every regenerator prints
  `wrote|unchanged <repo-relative path>`.
- **The gate's `py` lane and "needs the pinned venv" are one flag**: `scripts/gate.py::main` (`:1868`)
  keys on `kind == "py"`, not `_reexec_pinned`. Action: one fact for the venv need, apart from the lane. **Rides with:** rel 2.
  **Re-test:** `python scripts/gate.py --py` lists `walk-world`.
- **The git hooks call bare `python`**: `.githooks/pre-commit`, `commit-msg` and `pre-push` each
  `exec python scripts/gate.py …` (`commit-msg:5`, `pre-commit:10`, `pre-push:8`); `gate.py` imports no
  `promptpotter` at module level, so the real failure is a `python` without PyYAML (`gate.py:30`), though
  the repo refuses bare `python` at import. Action: the hooks call the
  pinned venv's interpreter, the one `gate.py::_reexec_pinned` names. **Rides with:** rel 2, which holds
  `scripts/gate.py`; its Owns gain `.githooks/`. **Re-test:** `git grep -n "exec python" -- .githooks` is empty.
- **`paper/bbeh-comparison/bbeh_potter_runner.py` closes a client the run already closed**:
  `run_bbeh_campaign` calls `session.backend_client.aclose()` (`:232-233`) after `run_campaign`, which closes
  its own. Action: the line and its `suppress` go. **Rides with:** rel 1, which holds this script.
  **Re-test:** `git grep -n "aclose" -- paper/bbeh-comparison/bbeh_potter_runner.py` is empty.
- **The pinned venv is keyed on the raw `uv.lock`**: `scripts/gate.py::_install_digest` hashes the lock's
  bytes, so a worktree whose lock moved only outside the pinned extras (batch/22's mlflow exit) is
  refused a venv that `uv export --frozen` proves identical. Action: hash the resolved pinned set.
  **Rides with:** rel 2. **Re-test:** a lock edit outside `_PINNED_EXTRAS` leaves the digest unmoved.
- **The walk world has no outer/inner run, so the seat-cell drill never runs**: `scripts/gate.py::_walk_world`
  builds `offline_run.py --optimizer=potter --straight` alone, so `webapp/e2e/walk/cells.spec.ts:40`
  always skips. Action: the walk world carries an offline L4 leg (`offline_run.py --l4`, `:2123`,
  1ba7ab27a, now exists), and the spec's skip becomes a failure. **Rides with:** rel 2. **Re-test:** `npx playwright test --project=walk e2e/walk/cells.spec.ts`
  passes 1, skips 0.
- **The dev loop still serializes on Vitest**: its pool fails a worker past its hard-coded 90 s start,
  which a host loaded from outside the gate lock still trips. **Rides with:** rel 2. **Re-test:** five
  builders plus one full gate, no reap.
- **`config/settings.py::_git_head` answers `UNVERSIONED` silently** for a ref in neither the loose refs
  nor `packed-refs`. Action: it refuses, naming the checkout it could not version. **Rides with:** rel 2.
  **Re-test:** a landing from a reftable clone names why.
- **Run data is stat'ed bare outside `store/io.py`**: `store/read_model.py::file_sig`, six more
  `.stat()` sites (`optimizer_manifest.py:96`, `pricing.py:114`, `event_stream.py:49`,
  `runtime_flags.py:38`, `measurement_archive.py:480`, `private_root.py:32`) and `measurement_archive.py::_named`'s `os.scandir` read a long path bare, so a ledger
  past `MAX_PATH` reads as absent. Root: the data root spelled once in its long form (`config/paths.py`),
  so every derived path carries it; `scripts/processes.py::long_path` (`a46a02722`, the gate's cache root
  alone) is a second speller beside `store/io.py::_long_path`, and goes with it, or its definition says
  why the gate spells before the venv exists. **Rides with:** rel 2, local and last, so the long form
  moves every derived path on the assembled tip; its Owns gain `config/paths.py` (box's file before, § 5),
  `store/io.py::_long_path`, `scripts/processes.py::long_path` and the `.stat()` and `os.scandir` reads
  of the files the re-test names. **Re-test:** `git grep -lE "\.stat\(\)" -- promptpotter` names no file
  outside `store/io.py`.
- **The walk world keyed by its check's key has no test for the key-is-None path** (`scripts/gate.py`,
  `6381c24b9`: a world is never built or pruned under no key). Action: a test holds that path. **Rides
  with:** rel 2. **Re-test:** a gate test runs the `walk-world` check with no key.
- **Unverified, for the next change to each file**: `shell/CriticalAlertBanner` builds `Said` rows in the
  browser; `ViewTabs.show` is many `open`/`close` calls, not one workspace act;
  `cycle_files.list_cycle_files` walks all of `.runtime/` and filters after; `FileTree` mounts a 2 s
  `useActivePointer()` poll for one failure line; a `/hierarchy` 200 replaces every entry object
  `lib/hooks/useRegistry.ts::useRegistry` indexes. The anti-rot pass re-finds them. **Rides with:** rel 3.
- **The landing stamp is unordered**: `maintenance/landing.py::LandedStamp` holds a schema hash, so a
  process on older code (an earlier desk checkout, or two checkouts on one `PROMPTPOTTER_HOME`) lands
  again and drops what newer code wrote, the paid archive's caches included, against the module's own
  "nothing may roll the code back". Action (the operator, 2026-11-11: the package version is the
  ordering signal): the stamp carries the package version that landed, and an entry from an older
  version refuses instead of dropping. **Rides with:** rel. **Re-test:** an entry from an older version
  on a root a newer one landed refuses.
- **A landing waits on every live producer on another commit, whether or not the ledger shape moved**,
  and on nothing else (`jobs/registry.py::JobRegistry.close_for_landing` reads the jobs holding spend):
  a server or read verb on other code holds no job, so `jobs/interlock.py` guards nothing against it,
  and a busy box may never land. Beside it (reported), a deploy dying mid-landing wedges every launch:
  `update.sh` dying after close-admission leaves the landing marker (`interlock.py:36-47`), so the old
  code refuses every launch with `landing`. Action: key the wait on a digest of the `CycleRecord`
  schema the code writes, held by every live process, in place of `config/settings.py::CODE_REVISION`
  through `jobs/interlock.py` and `deploy-linux/update.sh`, and back the marker by the deployer's OS
  lock. **Rides with:** rel, beside the stamp row above (one landing order); its Owns gain
  `jobs/interlock.py`, `jobs/registry.py::close_for_landing` and `deploy-linux/update.sh` (a box change:
  that hunk asks). **Re-test:** a landing that moves no record shape refuses while a run is live; a
  deploy killed after close-admission leaves no marker the next launch refuses on.
- **`landing_key` misses a body change**: `store/data_root.py::_schema` keys a reader by `module::qualname`
  and `landing.py::landing_key` keys migrations by name (`sorted(MIGRATIONS)`), so a reader's or a
  migration's body change never lands again. **Rides with:** rel. **Re-test:** edit a migration's body;
  `landing_key()` moves.
- **Survivor migrations are unchained**: `maintenance/migrations/consent_by_principal.py` reads
  `consent.jsonl` from disk, blind to the lines `consent_trail` stages into it in the same landing
  (`landing.py::_staged`), so an owner-plus-delegate account last landed before v0.8.19 gets its
  delegate's acceptance, and nothing runs it again. Root: each migration reads the root as staged so
  far, as `JobRegistry.as_staged` does. Beside it (reported by the test split, unverified): a landing
  in a tree with no account user file stages no respell of an authored definition's retired knobs.
  **Rides with:** rel. **Re-test:** a landing over a pre-v0.8.19 consent trail keys each acceptance by
  its own principal, and a landing with no user file respells a definition's retired knob.

## Held — no wave-4 chain yet

- **The estimator has no golden corpus**: its key reads `measurement_modules()` over its source, not
  its outputs on fixed inputs. A corpus would read each inner cell's `CycleResult` (~255 KB each) and the
  tenant ruler's response rows: over 1 MB, and its only source is account data on the box, which the
  operator may wipe. **Held:** a corpus source the repo can commit under 1 MB. **Re-test:** the
  estimator's entry in the behaviour-key file names its key's inputs.
- **The sign-in exchange-failure refusal still reads `health` in the router**:
  `presentation/api/routers/auth.py::callback` calls `account.record_refusal` and `health.is_broken`
  itself (its log line beside the act went). Action: split `health`'s account dependency; the callback
  calls one function. **Held:** `health`'s account split (M22, [`roadmap.md`](roadmap.md) § Host assistant).
  **Re-test:** `git grep -n "record_refusal" -- promptpotter/presentation` is empty.
- **The committed L4 panel resolves only on this account**: `research/datasets/promptpotter-self/panel/dataset.yaml`
  seats `hotpotqa-distractor` and `legalbench-fods`, account definitions no committed tier holds, so
  the box refuses the panel at init. Action: commit both to the research tier, or keep the README's
  prerequisite. **Held:** the operator's call (a research-tier definition gets `defaults.yaml` laid
  under it, a tenant one does not). **Re-test:** `promptpotter-self/panel` inits on a fresh account.
- **An outer early cut can skip a benchmark**: `intelligence/rasch.py::build_round_order` orders a round's cells by
  δ, not by benchmark, so an arm PoBB cuts at 4 of 6 cells may read none of one benchmark's seats.
  Action: draw the outer round stratified by `Sample.measured_dataset` (potter's `_strata` exists).
  **Held:** a decision on the outer draw. **Re-test:** a cut arm's cells span every benchmark.
- **The inner measurand differs by benchmark**: justlogic seats inherit `justlogic/family.yaml`'s
  `per_cell` token discount while hotpotqa and legalbench score raw `em_match` / `label_match`, and
  hotpotqa's `escalation.l1_patience: 5` differs from the others; `runner/inner/tasks.py::_lever`
  leaves knobs out, so nothing flags it. Action: one inner measurand and one ladder across seats, or a
  refusal. **Held:** a decision. **Re-test:** the three definitions' `scoring` and `escalation` blocks.
- **Merge hazard at landing**: MAIN's uncommitted `promptpotter-self/panel/dataset.yaml`,
  `openapi.generated.json` and `types.generated.ts` overlap `arc/l4-robust2`. Action: regenerate the
  specs and types after the merge. **Held:** the landing. **Re-test:** `git status` on MAIN.
- **Two builders of one sandbox store**: `infrastructure/store/stores.py::inner_sandbox_store` and
  `runner/inner/spawn.py::sandbox_stores`; the first takes `(stores, campaign_id, cycle_id)`, so its
  callers split a `CycleHop` to call it (`hierarchy_subtree.py:380`, `stores.py:268`,
  `lineage_queries.py:80`). Action: one builder from (stores, hop). **Held:** none claims it. **Re-test:** `git grep -n "def inner_sandbox_store\|def sandbox_stores" -- promptpotter`.
- **Two fits of one reading**: `runner/round.py::measure_population` and
  `scoring/selection.py` both fit `candidate_abilities` over the same rows. Action: the election takes
  the measured abilities. **Held:** none claims it. **Re-test:** `git grep -n "candidate_abilities(" -- promptpotter`.
- **A sandbox writes account labels**: an inner mint relabels the account's dataset label from inside
  a sandbox (`jobs/mint.py::mint`, `tenant_datasets.relabel`); benign only while `dataset_hash` covers
  the whole dataset. Action: a sandboxed mint writes no account label. **Held:** none claims it.
  **Re-test:** an inner mint leaves the account's label file untouched.
- **A ± that measures nothing**: a replicate pass's ± (today `seed-screen`'s `margin_se`) reads 0.000
  over identical temperature-0 passes, which carry no row-sampling variance, and the panel quotes it as
  a precision. Action: the
  floor is an arm whose per-cell score is known for free (truth equals the majority), so the origin is
  read PAIRED against it, a served reading on EVERY campaign's origin round: a bank whose
  constant-answer floor exceeds the origin pays a candidate for collapsing to one label, which is the
  seed screen's verdict (`seed_screen.py`'s `rewards_collapse`) and dataset election's disqualifier.
  The floor has one owner (`potter/dispatch/injections/panels.py::_constant_answer` reads it).
  **Rides with:** passes 3, which deletes the seed screen (today's paired reading serves it; readings
  folds it later). **Re-test:** an origin round's served readings carry the paired floor and its
  collapse verdict.
- **The outer prompt ranks on θ, not the scored term**: Shape: `potter/dispatch/injections/panels.py`'s
  "weakest first" and "worse than the origin beyond their own error bar" read `mean_round_delta`, which
  is in-sample and scores nothing, because `held_out_lift` carries no SE. Action: serve the held-out
  lift's SE (a binomial 20-row bench a pass) and rank and separate on it; θ stays a column.
  **Held:** a served SE. **Re-test:** `git grep -n "mean_round_delta" -- promptpotter/application/optimizers/potter/dispatch/injections/panels.py`.
- **"Which benchmark a row measures" is spelled three ways**: Shape: `runner/round.py` (`or filed`),
  `potter/members.py::_strata` (`or ""`), `domain/verification.py` (`.get`, None = filed); and
  `origin_panel.py::origin_panel(scope_of=…)` / `BenchScope` partition by benchmark under the word
  `RulerScope` owns. Action: one `Sample` reader of its benchmark; `scope_of` → `benchmark_of`.
  **Held:** the rename's digest. **Re-test:** `git grep -n "measured_dataset or\|scope_of=" -- promptpotter`.
- **`AbilityScale` wraps one field**: Shape: `bench/difficulty.py::AbilityScale` holds `judging` alone,
  and its `caveat` / `frontier` belong on `JudgingRuler`. Action: fold the wrapper. **Held:** none
  claims it. **Re-test:** `git grep -n "class AbilityScale" -- promptpotter`.
- **Nothing keeps an L4 edit L4-only**: Shape: nothing refuses a mark in `runner/inner/narrative.py` or
  an optimizer import of it, and potter's `panels.py` reaches `OuterSampleProxies`, `outer_proxies_of`,
  `inner_facts_of` and `InnerCellFacts`, so an edit to the scored term re-keys every potter campaign.
  Action: an import-time check in `runner/inner/tasks.py::measurement_modules`' module that no estimator
  module is a shaper; the injection reads L4 cells through the connector's served terms. **Held:** the
  re-key. **Re-test:** `git grep -n "domain.l4" -- promptpotter/application/optimizers`.
- **`floor_reason` and the bench read different rounds**: Shape: `domain/l4/proxies.py::floor_reason`
  reads `result.rounds`, the last cycle only, while the bench and `n_rounds_after_origin` read the line
  (`runner/entry.py`), so after a fork "no proposal reached a cell" is judged without the rounds
  the rebase discarded. Action: `CycleResult` carries the line's spent rounds (`LineSpan.own`).
  **Held:** a `CycleResult` reshape. **Re-test:** a forked inner line whose pre-fork rounds all spoiled.
- **The abort knob and the floor disagree**: Shape: `potter/knobs.py`'s `terminate_capability` names
  an unrecoverable upstream fault as an abort's intended use, while `domain/l4/proxies.py::floor_reason`
  floors every `OPTIMIZER_ABORT` as prompt-owned, one after a real held-out gain included. Action: an
  abort carries who owns it, and only a prompt-owned one floors. **Held:** a decision. **Re-test:** an
  inner run aborted on a backend fault reads `floored` 1.
- **An archived L4 outer cell carries no `floored`**: rows banked before `floored` became a row term
  leave the `per_sample` of `research/datasets/promptpotter-self/family.yaml` unscored; no reader reads both
  shapes. Action: none past the landing that ships it, which re-measures C0. **Held:** that landing.
  **Re-test:** after it, `promptpotter-self` scores every outer cell it reads.
