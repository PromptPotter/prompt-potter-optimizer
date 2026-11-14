# Code-Debt Cleanup — Backlog

**Only work that is blocked or multi-arc** — including every shape a pass noticed off its task (root
`CLAUDE.md` `<situational-awareness>`). Anything one edit closes is fixed in the pass that found it; never the root of a patch just shipped (root `CLAUDE.md` `<root-fix>`). An item ships by
being deleted. Each entry: `file::symbol` — the defect — the action — **Rides with:** the chain that
reaches it ([`roadmap.md`](roadmap.md) § The cleanup chains (wave 4)), or **Held:** why
none can yet (a digest, a decision, a mechanism, a served change, a survivor) — **Re-test:** the
command or question that says it still stands. Run the re-test before acting.

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
  admission before the cut. Measured at +60 in `promptpotter` as its own door (`wip/valid-2` d75eb7e is
  the reference build), so it rides admit-2's one admission door.
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
  its cycle before `DraftStore.become`, and the landing reads the claim, its act too: `apply_landing` keeps
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

- **`acompile` lands twice per compile**: `presentation/teleprompter.py::acompile` calls `enter()` for its
  stores, then `run_campaign(…, stores=stores)` enters again (`embedded_run.py:130`); every
  `embedded_run.py::enter` re-runs `land_at_entry`, and a landing that applies completes the registries
  twice (`landing.py::land_at_entry`, then `plan_landing`). Action: one landing per process, its
  registries completed once by the entry; `plan_landing` takes them complete. **Rides with:** admit-2,
  whose Owns gain `presentation/teleprompter.py::acompile` and `landing.py::plan_landing`.
  **Re-test:** `git grep -cE "\benter\(" -- promptpotter/presentation/teleprompter.py` is 0 (any spelling:
  `to_thread(enter)` read 0 while the double landing stood), and
  `git grep -c "complete_registries(" -- promptpotter/application/maintenance/landing.py` is 1.
- **Three launch faults from a code review (reported; verify each at its line first)**: `jobs/head_to_head.py:178`
  leaves a partial mint's arms unstamped, so a retry refuses `arm_taken`; `mint_and_start.py:108` and
  `checkin.py:93` leak the `BackendClient` they open when the mint is refused; ties between
  launches broken on a random `job_id` can refuse two head-to-head arms with one stamp each other
  `launch_contended`. Action: one mint door owns the client and the stamp.
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
  `domain/campaign_config.py:421`,
  `infrastructure/projections/live_dashboard/projection.py:255`, `domain/bench.py:359` (`can_run`'s
  description) and `webapp/components/searchpoint/BenchGradeAction.tsx:29` ("Grade on bench"). Action: "held-out" at
  all five doors, at once. **Rides with:** passb-2 2, whose Owns gain those files. **Re-test:**
  `git grep -n "grade_bench" -- promptpotter webapp scripts` is empty (8 hits in 5 files today).
- **`restate_export_bench` rewrites `export.json` outside the roster**: `campaign_store/store.py::restate_export_bench`
  (called from `scoring/passes.py:714`) patches the file after a later held-out pass. Action: the pass's
  `RunArtifacts` drain re-renders the export off the ledger's newest bench reading
  (`ledger_scan.py::scan_closed_bench`), so `write_export` has one caller. **Rides with:** passb-2 2.
  **Re-test:** `git grep -c "restate_export_bench" -- promptpotter` is 0.
- **A stopped paid pass exits 1 at the CLI**:
  `presentation/cli/campaign_runner.py::main` maps `PassStoppedError` to exit 1. Action: `campaign_runner.py`
  answers it with the paused exit code. **Rides with:** passb-2 3. **Re-test:**
  `git grep -lE "except PassStoppedError" -- promptpotter/presentation` names `campaign_runner.py` alone.
- **`line_run` reads a paused pass as selecting**: a pass with a pause asked reads `PAUSED`
  (`infrastructure/run_state.py:82`), so `runner/bench.py::line_run` takes its hold for the loop's.
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

- **Two carriers**: the run's `infrastructure/run_scope.py::RunScope`, bound once where the cycle opens, carries
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

- **The browser inks a served state from its own map**: `webapp/lib/derivations/sample-trajectory.ts::MOVEMENT_CLASS`
  and `PipelineFlow`'s `tone`. Action: they go once a served word row carries its `Tone`
  (`domain/said.py`). **Re-test:** `git grep -n "MOVEMENT_CLASS" -- webapp/lib`.
- **Potter's layers keep a second member protocol**: `escalation.py::Layer` and
  `escalation.py::TransitionResult` carry L2 and L3 beside `nodes.py`'s member protocol, so a layer
  fired by the `escalation` controller parses, lands and reports through its own door. **Re-test:**
  `git grep -n "class Layer(Protocol)" -- promptpotter`.
- **A locked-in leader's caught-up cells reach the scoreboard only at the round's close**:
  `runner/round.py::_walk_population` merges `CatchUpPool.caught` after `run_walks`, and a catch-up
  walk records no pass (`BACKFILL` is no arm role). Action: a caught-up cell is a recorded row of its
  prior's arm (its walk continues the prior's pass, `held` = the prior's rows). **Rides with:** rk-opt's
  next chain. **Re-test:** a CAPO race settled `locked_in`: the leader's last `running` before the close
  counts the cells `rows(leader)` holds.
- **A round close re-folds the bank's whole archive**: `SampleIndex.refresh` re-folds the bank's
  whole archive history at every round close for compare arms and L4 cells (`sample.py:195-248`), and
  the local archive sits at 29.96 MB of `_CellFile`'s 32 MB cap. **Rides with:** rk-opt's next chain.
- **Overlap and repair keep their own pass families**: `RoundCells.overlap` and `RoundRestatedRecord`
  carry their passes beside `VerificationPass`. Action: both ride the one record. **Held (design
  fork):** (a) each overlap top-up is a `VerificationPass` of a new `overlap` kind on the round, and
  `RoundResult.overlap_results` derives from them, while `RoundOutcome.overlap` (the `OverlapReading`,
  17 files) stays; or (b) the reading and its cells are one origin-panel record the round and its
  restatement carry, so `.overlap` goes whole. Both reshape `RoundStatement` (a wipe). **Re-test:**
  `git grep -n "\.overlap\b" -- promptpotter` is empty.
- **`resolve_cells` runs twice per run init**: `runner/inner/spawn.py::_inner_cells` (through
  `_identity_config`, `:53`, `:98`) and `runner/inner/tasks.py::_outer_cells` (`:59`) each parse the
  experiment and resolve the panel's cells. Action: the cells are resolved once, off the panel the
  workload holds, and both read them; `identity_config` takes `(stores, experiment)`, so the connector
  protocol moves with it. **Rides with:** rk-opt's next chain. **Re-test:** `git grep -n "resolve_cells(" -- promptpotter`
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
- **The live reads re-serve history per poll** (a hot-path pass, ✓ unless reported): `_cycle_cells`
  and `_campaign_edits` cost O((R·A)²) per request, `snapshot_frame` re-folds per connect, and a ray
  page re-folds per "older". Root: a value is its aggregate plus the live round, never a copy of its
  history. The same root serves the wire: the polled live dashboard re-serves every closed round on every frame (570 kB at 16
  rounds, `domain/dashboard_rows.py::served_rounds`); a browser stitch of `…/rounds/{n}` per round, or a
  new read, breaks R7, so the closed-round series' read is decided first, then every `dash.rounds` reader
  moves. **Rides with:** narr 2; webb-5's Owns gain `hooks/useOriginEvidence.ts`, `hooks/useVerify.ts` and
  the `dash.rounds` panes. **Re-test:** `git grep -nE "dash\??\.rounds" -- webapp/lib webapp/components`.
- **The live dashboard keeps a third hold tracker** (narr-1's landing): `live_dashboard/projection.py::_holds`
  keeps the last `ProviderHoldRecord` per sender and, unlike `narration.py::Narration`'s, is never
  cleared at a run-phase declaration. Action: the dashboard reads the narration's holds. **Rides with:**
  narr 2. **Re-test:** `git grep -c "_holds" -- promptpotter/infrastructure/projections/live_dashboard` is 0.
- **`_Standing.value` builds a `StandingArm` per arm of every round on each call** (narr-1's landing):
  closed rounds' cells are shared, but the tuples and outer dicts stay O(R·A) per poll. Action: a closed
  round's `StandingArm`s freeze at its close. **Rides with:** narr 2. **Re-test:** a value at round R
  builds tuples for the live round only.
- **`RoundBuffer` wraps `Narration`'s arm API one call deep** (narr-1's landing,
  `live_dashboard/round_buffer.py:28`: `announce`, `take`, `enter`): the buffer is a view of the
  narration's live round, not a holder of its own. Action: its readers read the narration and the
  buffer goes. **Rides with:** narr 2. **Re-test:** `git ls-files promptpotter/infrastructure/projections/live_dashboard/round_buffer.py` is empty.
- **The arms gate is worded twice** (narr-1's landing): `views/readout.py`'s `[ARM GATE]` block and
  `activity.py::_arms_gate` say one hold, because the gate is no narration row. Action: the gate is the
  typed act's narration row (the U3 row above). **Rides with:** narr 2.

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
- **The knob Reset writes the declared value, so the draft stays "edited"**: `OptimizerSetupSection.tsx`
  lays `NodeKnobsPanel`'s patch as `optimization.nodes` alone, where a `null` is the clear
  (`layer_fold.py::wire_patch`). **Rides with:** rk-run 7. **Re-test:** `git grep -n "set(k.declared)" -- webapp`.
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
  `config/paths.py:9`) and the landing's two (`maintenance/landing.py:256,380`);
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

### passes-6 (what passes-5 and holders-3 left at landing 54)

passes-5 landed a pause standing for its hold and one reader of a reply's usage; holders-3 `reindex`'s
plan/apply pair and a temporary locked through its replace. Left:

- **A send's error category is decided twice**: `spend_book.py::_status_failure` classifies at the
  send's close, and the cell classifies the raised exception again (`sample_measurement.py::_known_failure`,
  `measure_sample`'s except, `harbor.py::_AgentModel.call`). Root: the send raises a typed failure
  carrying its `SendOutcome`, which changes what `openai_compat`'s client raises for every caller
  (`judges/call.py` catches `httpx.HTTPError`; `infrastructure/backend.py::run_query`'s `raise_for_status` builds a
  fresh exception after the send closed). **Rides with:** the chain owning the client's raise contract.
- **A cell's step usage drops reasoning**: `connectors/dspy_module.py::step_tokens` and
  `connectors/harbor.py::_step_tokens` build `StepUsage` without `reasoning`, though the field exists;
  and `openai_compat.py::ReportedUsage.tokens` writes `cache_read=… or 0` where `TokenAccount` reads
  `None` as no breakdown reported. Root: `StepUsage` is built from `ReportedUsage` alone.
  **Rides with:** passes-6.
- **Two tests fail under load, not under their claim**: `test_concurrent_account_edits_and_an_acceptance_all_survive`
  raises `PermissionError` on `user.json` on Windows beside a second gate, and
  `tests/test_security.py` is not collectable in a venv lacking the `mcp` extra; `tests/test_scoring.py::_peer_cycle`
  files no round-0 cells, so a noise floor through it reads nothing. **Rides with:** passes-6.

### cfg-2 (what cfg left at landing 50; the re-key window)

cfg landed the one layered-document fold (`domain/layer_fold.py`) and every door laying through it;
cfg-2 laid every door's patch through one wire rule and every campaign default into `defaults.yaml`,
and derived the knob couplings from each member's draws and the escalation ladder.

- **The node document is typed two ways and the L2 steer is a side memory**: every door now parses a
  patch through `layer_fold.py::wire_patch`, but L2's steer is read through `L2Memory.steered` /
  `steered_layout` beside the fold, not as a named layer of `l1_generate`'s config, and about 58 of
  the engine's `dict[str, Any]` sites are node-config bags (`dict[str, NodeCall]` on `CycleSeed`,
  `dict[str, dict[str, Any]]` on `ForkSeed`, which carries a null clear and text a `NodeCall` refuses
  before the schema types it). Action: one typed node document with knobs a view on it; the steer a
  named layer. It closes ren 8's node-config census and the pipeline-switch row. **Rides with:** cfg.
- **A sub-model's knobs still default in code**: every `CampaignConfig` / `OptimizationConfig` knob's
  default lives in `assets/datasets/defaults.yaml` (`authored.py::defaults_config`), but a knob inside
  a nested model keeps a code default the file never shows: `LivesConfig` start 2 / cap 4,
  `JudgeSpec.temperature` 0.0, and `campaign_config.py::over_the_default`'s budget (usd 0.025, rounds
  50) laid under every budget a layer names. Action: each into `defaults.yaml`, its field required.
  **Rides with:** cfg. **Re-test:** no field of a model `CampaignConfig` nests carries a valued default.
- **Potter's demo axis is dead wherever a dataset holds out no demo pool**: the derived coupling
  `l1_generate.shots_without_demo_pool` (`application/knobs.py::_draw_couplings`) now badges every default potter
  campaign, because `defaults.yaml` holds out no `dataset_split.demo` and only justlogic declares one
  (`demo: 50`), while `l1_generate.k_max` is 5. The badge is true; the default is the fork. Action
  (operator's call): hold out a demo split by default (every split re-keys, search loses rows) or drop
  the axis from the default (`k_max: 0`, re-keys potter). **Re-test:** a default potter campaign on a
  shipped dataset raises no `inert` coupling.

### batch/26 · rk-dec

- **The never / partly / always spread contradicts the headline beside it**:
  `application/scoring/measurement_log.py::_cycle_cells` pools every candidate the cycle ran, so
  `domain/cells.py::hit_spread` reads a sample only C0 saw as "never". Action: the spread reads the
  headline's own cells. **Rides with:** readings.
- **No door but the browser reads the noise-walk readout**: rk-dec-2 serves `lift_prior` and
  `hold_up` on the round channel, but no test reads them on the CLI, MCP or embedded doors. Action: each
  door renders them off the same served round. **Rides with:** narr. **Re-test:** the offline world's
  CLI round readout names μ0 and τ.
- **`hold_up` is withheld on a flat ruler**, so the offline world never shows it: the readout
  cannot be seen in the harness that proves the doors. Action: the offline world banks one round whose
  ruler separates arms. **Rides with:** tests-w.

### valid (after rk-dec, before the behaviour keys)

- **`HeldRun.of`'s second config read**: `jobs/launcher/run_job.py::HeldRun.of` reloads the campaign
  and `cycle_config` after `open` and the admission already read them. Action: the opened cycle carries
  the config it read. **Rides with:** valid 4.
- **A fork's share lands at its landing**: a running cycle's job moves onto its fork through
  `jobs/launcher/admission.py::land_job`, which writes the fork's `CycleShareRecord` there, so a
  `set-limits` filed between the cut and the landing re-shares the campaign without the fork. Action:
  the share is taken against the campaign with the cut. **Rides with:** admit-2.

### hubs (after valid, before layers)

- **A cycle's outcome is two types**: `domain/cycle_result.py::CycleResult` (what `run_cycle` returns,
  its rounds with their cells) and `domain/records/run_records.py::CycleFinal` (what `CycleFinalRecord`
  banks), which `runner/entry.py` fills from the other field by field. Banking `CycleResult` whole
  would put every round's cells on the ledger beside `RoundClosedRecord`, against the chronology rule
  (`infrastructure/CLAUDE.md`). Action: one type, the rounds read back off the ledger by its
  readers (`l4/proxies.py`, the narrative); the L4 estimator digest re-keys. **Rides with:** hubs.
  **Re-test:** `git grep -c "class CycleFinal(" -- promptpotter` is 0.
- **A moved panel is warned into a log**: `campaign_store/store.py::write_resolved_run` logs it as a
  `logger.warning` and appends anyway, a state the run enters and reports nowhere. Action: the moved
  panel lands as a field of `ResolvedRunRecord`, read by the readout. **Rides with:** hubs.
  **Re-test:** no `logger.warning` in `write_resolved_run`.
- **A hop's chain is spelled 16 times**: `ledger_chain(CycleDir(...), moment=None)` across the
  package. Action: one store read of a hop's chain (`CampaignStore.chain`). **Rides with:** hubs.
  **Re-test:** `git grep -c "ledger_chain(CycleDir(" -- promptpotter` sums to at most 1.
- **`p_record_drift` counts handler tables as mirrors**: its 33 is `ledger_scan.py` (12) and
  `live_dashboard/projection.py` (21), folds and `_handle_*` tables that each read their own subset.
  Action: the count skips a `_handle_*` table and a fold's declared kinds (the row above's
  declaration). **Rides with:** hubs. **Re-test:** drift is 0 and its `BASELINE` row says so.

### layers (alone, after valid, before the behaviour keys)

- **The layering is fictional at runtime**: the package holds no function-local import and its
  runtime file graph is acyclic, but 81 `TYPE_CHECKING` imports lie on a file-level cycle after
  layers (122 at its base), so a type-only block hides each knot rather than cuts it. Every other
  type-only import is hoisted, keeping only the two that keep `promptpotter --help` off httpx and
  rich, the layer crossings and the Harbor extras. The knots left:
  - the store hub and its parts: `stores` ↔ `session_pointer`, `account_spend`, `account_trail`,
    `archive_queries`, `dataset_access`, `spend_book`, `run_scope`, `connectors/protocol`. A
    narrower parameter cannot close these, so each part needs the protocol it reads in a module of
    its own;
  - the domain: `spend` ↔ `run_records`, `sample`, `pipeline_schema`, `pass_kinds`. Moving the
    scoring fold re-keys the estimator;
  - bench ↔ optimizers: bench still reads `nodes.py`'s `CycleRead`, member protocols and
    `NODE_MEMBERS`, because the plugin contract and the round types share that module. Moving
    `CycleRead` alone re-formed runtime cycles.

  Action: each knot is cut at its root, held by `complexity_ledger.py::_type_cycles`. **Rides with:**
  layers. **Re-test:** the `type_cycles` row of `BASELINE` falls to 0.

### docs (beside any chain: `docs/concepts/` and `docs/methods/` rewritten to the code)

- **Concept and method docs state what the code does not** (the rest of a docs-vs-code pass; the docs
  chain rewrote the others): `the-loop.md:38-40`'s `terminate_proposal` line waits on the faults row that
  makes the abort report; the race-vs-election and joint-fit lines wait on readings 2; the L4
  fingerprint line on bkeys. **Rides with:** each named chain. **Re-test:** each claim re-read against
  its code line.

### bkeys-2 (what bkeys left at its landing)

- **A rename re-keys every banked L4 cell**: the estimator id hashes its modules' source, so rn-conn's
  `is_self_optimization(connector)` rename in `runner/inner/tasks.py` moved it `ff29c7fc` → `75e905b2`
  and filed every banked L4 cell under an id no read finds, while `behaviour_keys.json` held. Root: the
  estimator is keyed by its behaviour key, as the treatments are since bkeys. **Rides with:** bkeys-2.
  **Re-test:** a rename in `runner/inner/tasks.py` leaves the estimator id unchanged.
- **The trace hashes a display field** (rec's landing, reported): `MemberAddress.pass_role` has no
  reader but rides the round-file readings the trace hashes, so deleting it moves the behaviour keys.
  Root: the behaviour key reads the decisions, not the display fields. **Rides with:** bkeys-2.
  **Re-test:** deleting `pass_role` leaves `behaviour_keys.json` unchanged.

- **An L4 cell's instrument hashes a hand-picked subset, not what its session loads** (✓): the pin's
  `InnerTaskSpec.instrument` (`runner/inner/tasks.py::InnerTasks`: node config, campaign identity,
  estimator) skips what the inner session loads live (`wiring.py:228-307`: `origin.yaml`, the `source:`
  cut and recipe, `task.md` framing, the pipelines walk, the backend revision), and pins freeze at outer
  run init while the session loads at each spawn; over-keying: `Seat.id` a label inside the pin, axes
  key order and mixed-manifest chain order. Root: the instrument hashes the seat's
  `MintPlan.origin_hash` plus its `measured_address`, resolved at the outer run's init (one async
  resolve and one backend read per seat), which `dataset_access.py::dataset_experiment`'s synchronous
  resolve cannot host. **Rides with:** bkeys-2. **Re-test:** an edit to an inner `task.md` moves the
  pin of every seat on that dataset.
- **A seat's `n_rounds` takes `ge=1` under a panel floor of 2**: `domain/cells.py::Seat.n_rounds`
  overrides `max_inner_rounds` (`ge=2`) per seat, so one seat still runs a single inner round, a
  difference with no series behind it. Action: `ge=2`. **Rides with:** bkeys-2. **Re-test:** a seat
  declaring `n_rounds: 1` is refused.
- **A deepened inner campaign may stop at its old depth** (reported): depth leaves the inner id, so
  raising `max_inner_rounds` continues the inner campaign, but `runner/inner/spawn.py:217-250` resumes
  its stored declaration, whose round budget is the old depth. Action: verify first; if it
  reproduces, the continued campaign takes the spec's depth as its round budget. **Rides with:**
  bkeys-2. **Re-test:** a 2-round inner campaign, raised to 3, closes a third round.
- **The paid L4 browser walk's stop was sized for one inner round** (reported):
  `research/datasets/promptpotter-self/e2e/dataset.yaml:30`'s `usd: 0.05` predates the depth-2 floor,
  so `webapp/e2e/spend/l4.spec.ts` may halt on its ceiling. Action: size it from one measured depth-2
  run. **Rides with:** the next paid walk.

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
  and `pobb.py:151`'s "cannot disagree" is false; the ε cut is the half that stands; and
  `verdict-resolution.md:34-36`'s "the joint fit runs once, at ruler lock" is false (re-fit every round,
  `difficulty.py:223-256`). Decided (orchestrator; the operator may reverse it): the code, not the doc,
  is fixed: both read one ruler and one row set, and the doc then states it. **Rides with:** readings 2.

### batch/26 · rn-conn

- **The pipeline carrier is typed only at its door**: both connectors now read `NodeCall`'s fields,
  but `PipelineNode.current_config` is a `dict` (`domain/pipeline_parsing.py:381` builds it from the
  validated call), so `pipeline_schema.py` (7 reads), `pipeline_resolve.py:297` and `free_tier.py:137`
  read the model by string; `measurement_log.py::_span` and `wounds.py:184` read a laid dump the same
  way. The re-test also counts carriers that are no `NodeCall` (an OpenAI request body in
  `openai_compat.py`, a record payload in `audit_trail.py`, a model menu, dspy's `LM.kwargs`), which
  the action leaves alone. Action: type `current_config` as `NodeCall` and read its fields.
  **Re-test:** `git grep -cE "current_config\.get\(" -- promptpotter` is 0.
- **Two connectors still default in code** (rn-conn's landing): `connectors/dbllmbench.py::_DB_DEFAULT_URLS`
  maps a database to upstream's compose address when `DBLLMBENCH_DB_URL` is unset, and
  `connectors/harbor.py:229` defaults `skill_delivery` to `agent_skill`, against §0.5. Deleting the map
  breaks a TypeQL run on an `.env` without the URL, so the address's home is the decision. Action: the
  address is operator config (the install's `.env.example` names it) and `skill_delivery` sits in the
  dataset's file; both refuse when absent. **Rides with:** xconn 2 (named endpoints per connector).
  **Re-test:** `git grep -cE "_DB_DEFAULT_URLS|skill_delivery.*or " -- promptpotter/connectors` is 0.

### mob-3 (what mob 2 left at landing 53)

mob 2 landed the phone's ⋯ fold for a one-row toolbar, the masthead setup line as a tap-expandable
Toolbar, and the phone walk's back step. Left:

- **The walk server takes its world by a hand-set hash**: `webapp/e2e/serve.mjs` reads
  `PP_E2E_WORLD`, a `.gate_cache/walk-world/<hash>` path a person copies from the gate, so a gate
  rebuild removes the hash and the server exits "no workspace". Root: the server asks the gate for
  the current world. **Rides with:** mob-3.

### webb-5 (what webb-4 left at its landing)

- **The output contract is flattened in the browser**: `lib/derivations/output-contract.ts::outputContract`
  walks a JSON Schema with its own `MAX_DEPTH = 3`. Action: `domain/pipeline_schema.py::NodeOutputSchema`
  serves the flattened contract rows and `node-surface/NodeSurface.tsx` draws them. **Rides with:**
  webb-5, after rk-run (which holds `pipeline_schema.py`); its Owns gain both files. **Re-test:**
  `git grep -n "MAX_DEPTH" -- webapp` is empty.
- **Adapters over a flat served shape**: `useCandidatesModel.ts` joins about twelve sources; `.facts.` is
  opened at many sites; liveness is re-derived at each `phaseWalks(` site. Action: the served rows carry
  what the adapters join. **Rides with:** webb-5. **Re-test:** `git grep -c "\.facts\." -- webapp/components webapp/lib`.
- **A lift's name is composed twice**: `webapp/lib/derivations/paired-reading.ts::liftVersus` re-spells
  `paired_reading.py::lift_versus`'s "vs {over} {words}" (the words are generated, the composition is
  not), because the round read serves no label (`RoundView.candidates[].reading.vs_reference`). In
  `components/files/RoundFileView.tsx` the lift column's name comes from the first served arm's cell
  set alone, and its hint reads "on the cells both measured" whatever that set is. Action: the reading
  serves its lift's name, which `RoundFileView` and `searchpoint/SearchpointDrillIn.tsx` read;
  `liftVersus` and the hint's fixed wording go. **Rides with:** webb-5. **Re-test:**
  `git grep -n "liftVersus" -- webapp` is empty.
- **An L4 panel draws its inner datasets as identical cards** (operator, 2026-11-14):
  `pipeline/PipelineStack.tsx` lays one layer per dataset the panel measures
  (`pipeline_resolve.py::_nests`), but `pipeline/PipelineFlow.tsx` tags a card with its `connector`
  alone, so hotpotqa-distractor, justlogic/d234 and legalbench-fods each read "TERMNORM · LLM_ONLY";
  the served `NestedPipelineRef.dataset` reaches only the zoom button's title. Action: a nested card
  names its dataset, the connector beneath it. **Rides with:** webb-5. **Re-test:**
  promptpotter-self/panel's stack names three datasets.
- **The theme key is spelled twice** (lw's landing): `webapp/public/theme-init.js:4` reads
  `"promptpotter.theme"` beside `lib/storage-keys.ts::STORAGE_KEYS.theme`, since the pre-paint script
  cannot import the table. Action: the build generates the script's key from the table. **Rides with:**
  webb-5. **Re-test:** `git grep -c '"promptpotter.theme"' -- webapp ':!webapp/lib/storage-keys.ts'` is 0.
- **`lib/address.ts:6` imports `./derivations/search-point` past the barrel** (lw's landing; type-only):
  the cycle the hierarchy import's row closed, met again. **Rides with:** webb-5. **Re-test:**
  `git grep -n "derivations/" -- webapp/lib/address.ts` is empty.

### batch/27 · rec

- **`PairedReading` is a tagged union spelled as optional fields and a validator**:
  `domain/paired_reading.py::PairedReading`'s `_state_fits_content` guards "a lift, its members,
  cell set and coverage exist exactly where `state` is `read`", and 138 reads across 27 files re-check
  `headline` / `coverage` for None. Action: a read arm and an unread arm discriminated on `state`, the
  validator and the readers' None branches deleted. A reshape wipes run data. **Re-test:**
  `git grep -c "_state_fits_content" -- promptpotter/domain/paired_reading.py` is 0.
- **Fields written, never read: 30 left** (`scripts/complexity_ledger.py::_unread_fields`, the
  `p_unread_fields` ceiling). `PriorStanding.n_paired` / `n_discordant` are written in the capo and potter
  packages, so deleting them re-keys behaviour; `InnerCellFacts.origin_headroom` sits in the hashed
  `l4/proxies.py`. Action: each goes, or names its reader. **Re-test:** the ceiling is 0.
- **`SampleScoredRecord.running` stamps a running fold on every cell** (9.3 % of a 1.5 MB ledger). Its
  readers (`Narration.fold`, `StandingArm.fold`, the live board's rows) need the scorer's evaluators,
  which no domain fold holds. Action: the readers fold `StandingArm.cells` through
  `metrics.py::compute_composite_fitness` in `application/`, and the field goes (run data wiped).
- **Generated `last` record classes live in module globals so a snapshot can pickle them** (rec's
  landing): `store/campaign_store/ledger_scan.py::_last` builds a class at runtime and registers it with
  `globals()[made.__name__] = made`. Action: the last-wins records become declared classes, and the
  registration goes. **Re-test:** `git grep -c "globals()\[" -- promptpotter/infrastructure/store/campaign_store/ledger_scan.py` is 0.
- **An empty round's error rate reads 0.0** (rec's landing, reported):
  `domain/round_diagnostics.py::RoundDiagnostics.error_rate` stays a `float` where no cell ran, the
  absent-as-zero shape rec closed for a round's fitness. Action: `None` where no cells ran.
  **Re-test:** a round with no scored cell serves `error_rate: null`.

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
- **The run-state derivation cannot move into `stores.py`**: `infrastructure/run_state.py`'s
  `derive_run_state`, `run_owed`, `read_run_state` and `run_state_stamps` read the ledger and the producer
  lock that `store/stores.py::Stores.declare_stop` and `::declare_ended` write, but `stores.py` imports
  `CampaignStore`, and `campaign_store/store.py:67` (`run_state_stamps`) reads the run state, so the
  move closes an import loop. The row keeps only the leg: `run_state.py` (the derivation, `run_owed`,
  the stamps and `RECENT_STEP_S`) imports neither `stores.py` nor `campaign_store/`, and both read it.
  **Rides with:** la 2, a regroup by subject; its Owns gain the import lines la already takes
  (`store/stores.py`, `campaign_store/store.py`, `scripts/{build_ts_types,offline_run}.py`,
  `tests/test_security.py`) and `llm/heartbeat.py`'s doc line (`cycle_reads.py` imports none of it).
  **Re-test:** `python scripts/gate.py --only layering` is green with `stores.py` reading `run_state.py`.
- **`run_owed` re-derives `SILENT`'s fact beside `derive_run_state`**: `run_state.py::run_owed`
  reads the last phase and the launch claim again, because `derive_run_state` cannot tell a lock
  holder's own hold from a producer's. Root: the derivation takes the reader's hold as an input.
  **Rides with:** la 2, in the same files as the row above. **Re-test:**
  `git grep -c "def run_owed" -- promptpotter` is 0.
- **Two spellings of "a producer holds it"**: `run_state.py::producer_attached` derives the whole run
  state, ledger and inbox included, to answer a lock probe, while `campaign_store/store.py::run_held_cycles`
  asks `producer_lock.cycle_held` directly. The `account_spend` readers are gone (df701cd20). Root:
  `producer_attached` (`run_state.py:156-158`) goes, and its readers ask `producer_lock.cycle_held`
  (`scripts/offline_run.py:714`, `tests/test_security.py:2052`). **Rides with:** la 2, in the same
  files as the rows above. **Re-test:**
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
  `domain/results.py` 31 times and `domain/records/run_records.py` 33 times since 2026-06-01, and draws from the
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
  (`inner/tasks.py::estimator`). The registry and `knobs.py` fold is the rk-run row below.
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
- **The optimizer registry is outside `optimizers/nodes.py`.** No digest walk holds it since the
  behaviour keys: the fold moves `llm_nodes`, `unfit_pipeline` and `_members` with no treatment re-key.
  With the `knobs.py` fold, it **rides with:** rk-run 1.
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
  `domain/records/run_records.py::stale_file_warning` and `read_model.py`'s `Projected`. **Re-test:** a
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
  or verbs (`passes.py:333` `--samples`;
  `store.py:592` `--from`; `entry.py:477` `--diag`; `cycle_addressing.py:134`, `loaders.py:575`,
  `preflight.py:212-213` and `:308` say `mint-campaign`, wrong at the terminal too, where the CLI verb is `new`), and ~12 of 74 409s name no next step (`session.py:154`,
  `fork_siblings.py:141`, `store.py:523`, `passes.py:512-531`, `passes.py:639`, `scoring_scope.py:148`).
  Root: `Refusal` carries `next_acts`, each door spells the act, and no message names a verb or flag
  (`phases.py:522`'s rule extended); this subsumes the `ConfigDriftError` row. Beside it (✓): a definition whose
  `source:` lacks recipe or rev raises a bare `ValueError` (`loaders.py:87,104`), swallowed by the listing
  into "Its samples could not be read on this install" (`startability.py:143-145`) and a 500 at start
  (`main.py:270-279`) (root: `Source.of` raises a refusal naming the field); ✓ `OPTIMIZER_ABORT` has an
  empty `next_step` and no acts (`phases.py:614-616`) and its reason is only logged (`escalation.py:516-531`),
  a state the system enters but cannot report. **Rides with:** faults 1.
- **A deep YAML ends the process, never a refusal** (faults-w's landing): `store/io.py::parse_yaml` loads
  with `CSafeLoader`, so `"["*5000+"1"+"]"*5000` exits the interpreter with no exception (depth 2000
  parses). The server writes those files, so no tenant path is shown yet. Root: one depth bound where
  `parse_yaml` reads. **Rides with:** faults. **Re-test:** that text raises `ValueError`.
- **Reported beside faults-w** (verify first): `commands/launching.py::launch_cancels`' refusal names
  "the launch's ledger", not a path; `llm/registry.py`'s cached `RateLimiter._lock` stays bound to its
  event loop across `asyncio.run`; `scoring/formula/compiler.py`'s refusal message carries the whole
  formula. **Rides with:** faults.

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

- **Two selectors override one pair identically**: `levi/members.py` and potter's `ThetaElection`
  (`potter/members.py`) each define `parent_cells` and `parent_read` as the panel's cells. Action: the
  `Selector` default. **Rides with:** small.
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
  so a delete names what it breaks; not `pipeline_schema.py::reach_map`, a node's reach).
  **Rides with:** small 3. **Re-test:**
  `compact-archive inventory`.
- **An overdrawn remainder has no type** (small-1's landing): `infrastructure/llm/spend_book.py::_left`
  (`:351`) is a signed `(usd, tokens)` tuple beside `domain/spend.py::Budget.less`, which clamps at
  zero, and `_at_least_nothing` (`:602`) clamps it again; `_times` admits a free send at exactly zero
  left but nothing once overdrawn, so it cannot fold into `less` unchanged. Action: one typed remainder
  that carries the overdraft, read by both. **Rides with:** small 2. **Re-test:**
  `git grep -c "_at_least_nothing" -- promptpotter` is 0.
- **A read answers for any inode kind** (small-1's landing): `cycle_files.py:116`'s
  `stat_optional` treats a directory as a file, so a directory path reached `read_bytes_optional`
  until small-1 refused it at the read. Action: `stat_optional` answers for regular files only.
  **Rides with:** small 2.
- **The paper harness tracks no named tree** (small-1's landing): `paper/bbeh-comparison/bbeh_potter_runner.py:1`
  pins `promptpotter==0.8.20` while the ledger fixes it against the current API. Action: the
  harness names the release it runs against, and a fix to it targets that release. **Rides with:** the
  next chain touching `paper/bbeh-comparison/`.

### batch/31 · box

- **The box holds the app's environment twice**: the services load `/etc/promptpotter.env`, and both
  they and the login user's CLI read the checkout's `.env` under it through `config/paths.py::env_file_path`,
  since no unit or shell sets `PROMPTPOTTER_ENV_FILE`. Checked live 2026-11-13: the app resolves the checkout
  `.env`, and `TERMNORM_TOKEN` is held only there. One file serves both; which one is the operator's (read
  access for the login user, or SELinux under `$HOME`), and the token moves into it first. **Rides with:**
  box 1.
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
  `scoped`, `session_scope`, `open_launch`, `open_cycle`, `close_init`) less the fork
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
- **The offline world asks the internet for LiteLLM's rates on every leg** (bkeys' landing, ✓ on
  Windows, WSL and the cloud): each trace's `harness.unrouted` holds
  `urlopen …/model_prices_and_context_window_backup.json`, because the run's home has no fresh
  `rates.json` and `pricing.py::refresh_rates_in_background` fires a thread the world refuses
  (`offline_world.py::install_network`, `router.rates` is `None`). A refused thread is a race the
  behaviour key happens to read alike. Action: the world answers the rates URL from the bundled
  table, so every leg's `unrouted` is empty. **Rides with:** tests-w. **Re-test:** no trace's
  `harness.unrouted` names the rates URL.
- **A failing test can hang the gate's pytest**: in a cloud run (hubs, 2026-11-14) a full-gate pytest
  sat at 2% CPU for 20 minutes on a failing connector test instead of failing. Not yet reproduced.
  Action: reproduce it, name what the test waits on, and make that wait end with the failure.
  **Rides with:** tests-w. **Re-test:** the reproduced failure fails within its test's own timeout.

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

- **The dev loop still serializes on Vitest**: its pool fails a worker past its hard-coded 90 s start,
  which a host loaded from outside the gate lock still trips. **Rides with:** rel 2. **Re-test:** five
  builders plus one full gate, no reap.
- **Run data is stat'ed bare outside `store/io.py`**: `store/read_model.py::file_sig`, six more
  `.stat()` sites (`optimizer_manifest.py:96`, `pricing.py:114`, `event_stream.py:49`,
  `run_state.py:39`, `measurement_archive.py:480`, `private_root.py:32`) and `measurement_archive.py::_named`'s `os.scandir` read a long path bare, so a ledger
  past `MAX_PATH` reads as absent. Root: the data root spelled once in its long form (`config/paths.py`),
  so every derived path carries it; `scripts/processes.py::long_path` (`a46a02722`, the gate's cache root
  alone) is a second speller beside `store/io.py::_long_path`, and goes with it, or its definition says
  why the gate spells before the venv exists. **Rides with:** rel 2, local and last, so the long form
  moves every derived path on the assembled tip; its Owns gain `config/paths.py` (box's file before, § 5),
  `store/io.py::_long_path`, `scripts/processes.py::long_path` and the `.stat()` and `os.scandir` reads
  of the files the re-test names. **Re-test:** `git grep -lE "\.stat\(\)" -- promptpotter` names no file
  outside `store/io.py`.
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

## Held — no wave-4 chain yet

- **The estimator has no golden corpus**: its key reads `estimator()` over its source and the L4 leg, not
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
  Action: an import-time check in `runner/inner/tasks.py::estimator`' module that no estimator
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
