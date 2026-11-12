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
- **`acompile` lands twice per compile**: `presentation/teleprompter.py::acompile` calls `enter()` for its
  stores, then `run_campaign(…, stores=stores)` enters again (`embedded_run.py:130`). Action: one landing
  per embedded compile. **Rides with:** admit-2, whose Owns gain `presentation/teleprompter.py::acompile`.
  **Re-test:** `git grep -c "enter()" -- promptpotter/presentation/teleprompter.py` is 0.
- **A retired knob refuses at the launch, not at the landing**: `store/dataset_access.py::definition_layer`
  checks only that each section is a mapping, so a definition still naming `halt_at_accuracy` lands
  and refuses at its next launch. Action: the landing validates each layer's `campaign` section against
  `CampaignConfig`'s keys. **Rides with:** admit-2, whose Owns gain `store/dataset_access.py`.
  **Re-test:** a definition naming `accuracy_ceiling` past `fitness knobs` refuses `land-data`.

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
  `domain/job.py::ScoringVerb`, `domain/campaign_config.py:414`, `live_dashboard/projection.py:261` and
  `domain/bench.py:349` (`can_run`'s description). Action: "held-out" at all five doors, at once.
  **Rides with:** passb-2 2, whose Owns gain those files. **Re-test:**
  `git grep -n "grade_bench" -- promptpotter webapp scripts` is empty.
- **`restate_export_bench` rewrites `export.json` outside the roster**: `campaign_store/store.py::restate_export_bench`
  (called from `scoring/passes.py:715`) patches the file after a later held-out pass. Action: the pass's
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
- **A bench pass's served delta is always zero**: `runner/bench.py::_bench_counted` (`:138`, `:149`)
  reads the one `SpendRollup` before and after the pass, but `live_dashboard/projection.py:456` replaces
  `state.spend` on each bank, so the snapshot never moves. Action: the delta is folded off the pass's own
  charges, not two reads of one snapshot. **Rides with:** passb-2 3, whose Owns gain `_bench_counted`.
  **Re-test:** a bench pass that spends serves a nonzero delta.
- **The first verify after a pause starts 38 s late, and no verify is ever `accepted`.** Action:
  attribute the wait by phase; the command acks `accepted` at admission. `accepted` is the loop inbox's
  status (`store/command_log.py:107,327,350,417`), so a pass acked `accepted` would read as a standing
  loop command. **Rides with:** passb-2 4, whose Owns gain `store/command_log.py` and
  `command_reading.py`'s status mapping. **Re-test:** on a paused cycle, time `command` → the `verify`
  phase's `enter` in the ledger.
- **A command whose filer died stays `filed`**: a killed verify reads `working` to every door and its
  task's result polls forever. Action: a command names its filer's OS lock (§0.5), a field on
  `domain/command_events.py::CommandRecord`, and a dead filer's command reads ended. **Rides with:**
  passb-2 3, whose subject is a stopped pass; its Owns gain `domain/command_events.py` and
  `store/command_log.py`. **Re-test:** kill a verify mid-pass; its command reads ended.
- **The parity file mixes a served answer into the decision set**: `scripts/offline_run.py:1347` embeds
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
- **Thirteen `ContextVar`s still stand beside `RunScope`**: `jobs/coupon.py` 1, `llm/send_pacing.py` 4,
  `llm/spend_book.py` 3, `llm/telemetry.py` 3, `shared/measurement_context.py` 2. Action: the eight
  sender vars and `bound_spend_book()` move onto `RunScope`, and an inner run's `slotless_share` takes
  its book from its scope; the per-call round and measured vars stay; telemetry's ride rec. **Rides with:** money 1.
  **Re-test:** `git grep -c "ContextVar(" -- promptpotter` over `coupon.py`, `send_pacing.py` and
  `spend_book.py` is 0.
- **S: a live book counts apart from the one fold** (every file's money is
  `store/read_model.py::money_on`, money-g): `spend_book.py::SpendBook` counts through its ledger
  subscription and through admission (`Admission._unseen`, `_unlanded`), and the live dashboard banks
  its own rollup. Action: the live readers ride `_Money` as a projection. **Rides with:** holds nest.
- **Money holds once per level, three ways** (operator, 2026-11-12: a backbone root, not a row):
  - the run reserves in quota's job registry (`job.reserve`, `_outstanding_reservations`), handed to
    its book as a `Hold`;
  - a cell reserves in memory only (`spend_book.py::CellReservation`, with its own `draw` and the
    chain index in `SpendBook.hold`), so a reserved cell's hold is on no ledger
    (`Connector.holds_own_sends`);
  - a call holds a `Charge`.

  Action: one hold at every depth, a `Charge` with a bound and a parent hold. A draw goes against the
  nearest open parent, and the open part is the bound less its children's holds and bills, read by
  `read_model.py::money_on`. `CellReservation` and the job reserve go. **Rides with:** holds nest
  (roadmap § Sequence, Phase 2). **Re-test:** `git grep -n "class CellReservation" -- promptpotter`
  is empty.
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

### batch/26 · rk-opt (links 1-4 landed; the rows its cap left)

- **capo's `population_kept` replays an absent arm as confirmed**: `optimizers/capo/members.py::_rank_survivors`
  reads an arm with no shared cells as a 0.0 mean, so a replay over a round that lacks the arm confirms
  it instead of answering it absent. Action: an arm with no shared cells is absent, as in the race's
  replay. **Re-test:** `pytest tests/test_archive.py -k replays_its_decisions` covers `population_kept`.
- **An optimizer node's template rides beside its config**: `llm_call.py::run_optimizer_node` takes a
  filled `template` and an `LLMCallContext` whose `config` named it (`critique.py::distil_critique`,
  `firing.py::_run_transition`, `task_context.py::run_checkin`, `generate.py:133`), so the two can
  disagree. Action: the call context carries the running template its fill reads. **Rides with:**
  the rest of rk-opt link 4. **Re-test:** `git grep -n "template=filled" -- promptpotter`.
- **An edited template's unknown slot is refused at dispatch, not at selection, and names no address**:
  `dispatch/injections/registry.py` validates what `dispatch/prompts.py::load_optimizer_prompt` loads,
  and `optimizer_manifest.py::_select` cannot reach potter's `injection_table()`. Action: a member hook
  names its known slots and selection calls it, refusing by config address. **Rides with:** the rest
  of rk-opt link 4. **Re-test:** an overlay holding `{{typo}}` is refused at `new`, naming
  `optimization.nodes.l1_generate.config.instruction`.
- **`tests/test_identity.py` reaches the spawn's private `_laid_arm`** (`_l4_arm`, `_l4_laid`, the depth
  step). Action: the journey drives `_open_seat` over an offline inner campaign. **Rides with:**
  the rest of rk-opt link 4. **Re-test:** `grep -n "_laid_arm" tests/test_identity.py` is empty.
- **A served prompt-field set is labelled in the browser**: `webapp/lib/prompt-fields.ts::PROMPT_FIELD`.
  Action: the served prompt-field row carries its label, and the constant goes; the ink maps
  (`sample-trajectory.ts::MOVEMENT_CLASS`, `PipelineFlow`'s `tone`) wait until a served word row carries
  its `Tone` (`domain/said.py`). **Rides with:** the rest of rk-opt link 4. **Re-test:**
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
  the rest of rk-opt link 4. **Re-test:** `git grep -n "class InjectionBundle" -- promptpotter`.
- **Wave C, the optimizers in one re-key batch**: `potter/l1/` → `l1.py`, `validators/` 7 → 2, peer
  optimizers' `prompts` + `state` into `members.py`, `pobb/` and `escalation/` flattened, and
  `runner/inner/tasks.InnerCells` moved into `domain/l4`. **Rides with:** the rest of rk-opt link 4.
- **The catch-up pool**: `validators.py::CatchUps` and `nodes.py::NoCatchUps` declare one protocol with
  one implementation, and the round reading takes a lock-in's backfills at
  `runner/round.py::_catch_up`'s commit, a second door. Action: the bench owns the pool and the
  race exposes only its priors. **Rides with:** the rest of rk-opt link 4. **Re-test:**
  `git grep -c "NoCatchUps" -- promptpotter`.
- **A locked-in leader's caught-up cells reach the scoreboard only at the round's close**:
  `runner/round.py::_walk_population` merges a prior's caught-up cells after `run_walks`, and
  `BACKFILL` is no arm role. Action: a caught-up cell is a recorded row of its prior's arm. **Rides
  with:** the rest of rk-opt link 4. **Re-test:** in
  `test_scoring.py::test_an_arm_is_cut_only_on_evidence_that_it_trails`'s locked block, the leader's
  last `running` before the close counts the cells `rows(leader)` holds.
- **Two word tables over one enum, three times** (`evidence/head_to_head.py::GUARD_STATE_INFO` beside its
  labels, `paired_reading.py::READING_STATE_KIND` beside its info, `origin_panel.py::ROUND_ADVANCE_STALL`
  beside its info), and `library.py::LibrarySnippet` flattens a cited-or-authored snippet into nullable
  fields. Action: one table each, its words outside the marked decision units; the snippet keeps its
  union. **Rides with:** the rest of rk-opt link 4.
- **Overlap and repair keep their own pass families**: `RoundCells.overlap` and `RoundRestatedRecord`
  carry their passes beside `VerificationPass`. Action: both ride the one record. **Rides with:**
  the rest of rk-opt link 4. **Re-test:** `git grep -n "\.overlap\b" -- promptpotter` is empty.
- **Three bars for "is this θ lift real"**: `potter/escalation/state.py::_improved` reads
  `lift > current.se`, `potter/election.py::_lift` takes `hypot` of both arms', and
  `potter/dispatch/injections/panels.py::detectable_rows` takes `a.se * sqrt(2)`. Action: all three fold
  onto the predicate `scoring/selection.py::elect_round_winner` reads (one lift test on
  `AbilityReading`). It moves decisions and the optimizer prompt: an offline run and the re-key.
  **Rides with:** the rest of rk-opt link 4. **Re-test:**
  `git grep -n -E "lift > current.se|sqrt\(2" -- promptpotter/application/optimizers/potter`.
- **The collapse gate cuts a hedging arm on noise**: `pobb/checks.py::check` →
  `domain/scoring.py::is_answer_collapsed` reads one repeated label over `n_min` answered cells as "no
  measurement". Action: collapse is judged against the parent's answers on the same cells, proven by an
  offline run. **Rides with:** the rest of rk-opt link 4.
- **`ab` re-fits under the current scorer on the whole ruler** (decision 21, rk-opt 5). The tenant
  ruler at a round's reach holds other campaigns' rows, so `ab` regrades those foreign rows off the
  archive too, not only the record's own cells, and with the formula unchanged its fit equals today's
  (operator, 2026-11-11: option (b)). Only `ab`'s output moves. **Re-test:**
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
- **scoring/evaluators.py::Evaluator is one class for two contracts** (`per_sample` async vs
  `per_round` sync) held apart by runtime guards; its `Scope` collides with `data_root.Scope`. Action:
  two evaluator types. **Rides with:** rk-run 4. **Re-test:**
  `git grep -n "isawaitable\|iscoroutinefunction" -- promptpotter/application/scoring/evaluators.py`.
- **`domain/scoring.py::turn_scalars` overwrites `n_turns` with the count of a step named `n`**, and
  `isidentifier()` drops a step like `my-step` silently. Action: the per-step terms take a namespace the
  totals cannot meet. **Rides with:** rk-run 4. **Re-test:** a turn list with a step `n` keeps its total.
- **infrastructure/llm/anthropic.py::AnthropicClient cannot run a node on a current Claude model.**
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
  / fork / diag off the cycle id, read in `runner/output.py`, `hierarchy.py` and `views/view_models.py`.
  Action: every reader takes the kind the mint recorded; `sibling_kind` goes. **Rides with:** addr 1.
  **Re-test:** `git grep -n "sibling_kind" -- promptpotter` is empty.
- **`ids.ts::rootCycleId` regexes `_fork_`** to give `useCampaignForest.ts::parentId` /
  `isInheritedSibling` what the served tree carries. Action: read parent and spawn off the tree. **Rides
  with:** addr 1. **Re-test:** `git grep -n "rootCycleId(" -- webapp/lib webapp/components`.
- **A workspace and a hop are re-derived from a path by parent counting**: `campaign_store/store.py`
  (the cycle's hop beside `cycle_dir.parent.parent`) and `store/account_trail.py`. Action: read the
  address from `Subject` / `CycleHop`. **Rides with:** addr 2. **Re-test:**
  `git grep -nE "\.parents\[[0-9]\]|\.parent\.parent" -- promptpotter` names none beside the data-layout
  sites (`account_trail.py:110`, `campaign_store/store.py:378,797`, `read_model.py:159`, `layout.py:236`,
  `archive_maintenance.py:57`) and the landing's two (`maintenance/landing.py:255,379`).
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
- **The feed's version spans a whole store**: `GET /feed`'s tick (`application/cycle_reads.py::feed_frames`) runs
  `application/hierarchy.py::entries_version` over every store, per connected tab, and a campaign
  subtree's version (`hierarchy_subtree.py`) covers its whole store, so any run's append re-asks every
  mounted tree. Action: one version per subject, kept by the append door and the sweep, so no tick walks
  a store. **Rides with:** addr 2. **Re-test:** `git grep -n "entries_version(" -- promptpotter/application/cycle_reads.py` is empty.
- **A campaign's short label is parsed three ways**: `format.ts::shortId`, `ForestRows::shortOrigin`,
  `ids.ts::shortFamilyTail`; `EvidenceCharts.tsx:77` labels cells with `c.split("/").pop()`, so two
  datasets' cells share one label. Action: a served short label per subject and node, on
  `application/hierarchy.py` and `application/evidence/read.py`. **Rides with:** addr, whose Owns gain
  `format.ts::shortId`, `compare/EvidenceCharts.tsx` and `evidence/read.py`. **Re-test:**
  `git grep -nE "shortId\(|shortOrigin\(|shortFamilyTail\(" -- webapp/components webapp/lib`.

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
  inherited origin is re-walked when another launch measured its rows. **Rides with:** rk-dec, with the
  walk; its Owns gain `bench/cycle.py` and `resume_and_fork/repair.py`. **Re-test:**
  `--fork-on-divergence` at round 0 of a gate-halted campaign, backend fixed, passes in one launch.
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

### webb-4 (a webb re-plan on addr's tip; its landing is not yet placed)

- **The polled live dashboard re-serves every closed round on every frame** (570 kB at 16 rounds,
  `domain/dashboard_rows.py::served_rounds`). A browser stitch of `…/rounds/{n}` per round, or a new
  read, breaks R7. Action: decide the closed-round series' read, then move every `dash.rounds` reader.
  **Rides with:** webb-4, whose Owns gain `hooks/useOriginEvidence.ts`, `hooks/useVerify.ts` and the
  `dash.rounds` panes. **Re-test:** `git grep -nE "dash\??\.rounds" -- webapp/lib webapp/components`.
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
  `useConnector` at an identity prop. **Rides with:** webb-4. **Re-test:** `git grep -c "NodeSchemaReading" -- webapp`.
- **Teaching prose in `title=` outlived its shipped lint**: `webapp/eslint.config.mjs::titleIsALabel`
  counts a title's static characters only, so 8 hits stand (`AccountActivityTab`, `CleanupConfirmModal`,
  `ForestCard`, `SampleSetControl` ×2, `ConnectorInspector`, `NodeConfigEditor`, `RemoteControl`), and an
  instruction under 60 characters ("Click to toggle.", "Open its dashboard.") passes. Action: the lint
  reads a template title's words too, and each hit says its prose through `ui/Term` or `ui/HoverCard`.
  **Rides with:** webb-4. **Re-test:** `git grep -cE 'title=("[^"]{60,}|\{\`[^\`]{60,})' -- webapp/components`.
- **The webapp's read state, past the feed**: the feed took the cadences and `syncBeat`; what it did not
  touch still stands to re-test: a `/me` failure reported by the cache and by `state/auth.tsx`;
  `api/reads.ts::couponRead` without a validator, and evidence computing its ETag after the build;
  `channel-hooks.ts::usePickedPoint` reading null mid-scoring; `ChannelDetail.tsx`'s pending copy on a
  failed read; "has measurements" asked through `round-reading.ts`'s `completed.includes` beside the
  served `round_axis.unfiled`. Action: one owner each. **Rides with:** webb-4. **Re-test:** each clause, first.
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
  (`oidc`, `grants`, `accounts`); `projections/live_dashboard/` → one module; LLM wire types into
  `llm/base.py`. Close with `gate.py --only layering,help-imports,handler-tables,doc-claims,instruction-files`.
  **Rides with:** la 1-2.
- **`Verb.needs_registries` carries two decisions**: a verb that declares it `False` (`trail`, `reindex`,
  which GCs, `close-admission`, `offer-extras`, `pin-roster`) skips the landing with the registries, so it
  reads and writes the data root unlanded (`cli/campaign_runner.py::main`). Action: every registry
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
- **The run state is derived in a module named for something else**: `infrastructure/runtime_flags.py::derive_run_state`,
  `::run_owed`, `::read_run_state` and `::run_state_stamps` read the ledger and the producer lock that
  `store/stores.py::Stores.declare_stop` and `::declare_ended` write. They cannot move into `stores.py`:
  `stores.py` imports `CampaignStore`, and both `campaign_store/store.py` (`run_state_stamps`) and
  `store/account_spend.py` read the run state, so the move closes an import loop. Root: one run-state
  module below all three, `infrastructure/store/run_state.py`, holding the derivation, `run_owed`, the
  stamps and `RECENT_STEP_S`; the stop door in `stores.py` reads it, and `runtime_flags.py` goes (stopb
  5's rest). **Rides with:** la 2, a regroup by subject; its Owns gain `infrastructure/runtime_flags.py`
  (moved), `store/run_state.py` (new), the import lines la already takes (`store/stores.py`,
  `maintenance/archive_maintenance.py`, `identity/default_claim.py`, `store/account_spend.py`,
  `campaign_store/store.py`, `scripts/{build_ts_types,offline_run}.py`, `tests/test_security.py`) and
  `llm/heartbeat.py`'s doc line (`cycle_reads.py` imports none of it). **Re-test:**
  `git ls-files promptpotter/infrastructure/runtime_flags.py` is empty and `python scripts/gate.py --only layering` is green.
- **`run_owed` re-derives `SILENT`'s fact beside `derive_run_state`**: `runtime_flags.py::run_owed`
  reads the last phase and the launch claim again, because `derive_run_state` cannot tell a lock
  holder's own hold from a producer's. Root: the derivation takes the reader's hold as an input.
  **Rides with:** la 2, with the move above, in the same files. **Re-test:**
  `git grep -c "def run_owed" -- promptpotter` is 0.
- **Two spellings of "a producer holds it"**: `runtime_flags.py::producer_attached` derives the whole run
  state, ledger and inbox included, to answer a lock probe, while `campaign_store/store.py::run_held_cycles`
  asks `producer_lock.cycle_held` directly. Root: `producer_attached` goes, and its readers ask
  `producer_lock.cycle_held` (`store/account_spend.py:164,284`, `maintenance/archive_maintenance.py:57`,
  `scripts/offline_run.py`, `tests/test_security.py`; `identity/default_claim.py:39`'s read goes with
  money 5). **Rides with:** la 2, with the move above, in the same files. **Re-test:**
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
  `_members` out of every treatment digest. With the `knobs.py` fold, it **rides with:** rk-run 1.
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

### batch/29 · lite

- **Three data-root rows are proven by presence alone** (`store/data_root.py::_WALKED`): `rates.json`, the
  model capability override and the capabilities cache, whose readers (`llm/pricing.py`,
  `llm/capabilities.py`) walk a raw dict. Action: a model per file, named by its row. **Rides with:**
  lite 2 (core's model vocabulary is its own, `rates.json` drops `litellm_provider`); its Owns gain
  `llm/capabilities.py` and `store/data_root.py`'s three rows (mkey's file too, § 5). **Re-test:**
  `git grep -c "_WALKED" -- promptpotter/infrastructure/store/data_root.py` is 0.

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
  cookie alone and reads no bearer, so a third party needs `PROMPTPOTTER_AUTH=off`.
  Action: an account mints a named, scoped, revocable token (hash at rest, a SURVIVOR row of
  `store/data_root.py::KINDS`); a bearer resolves through the one caller dependency, a new auth door
  reviewed in `access-model.md` (`identity/bundle.py::identity_of_session` per carrier; `deps.py::open_caller`
  splits the rate draw from the `Caller` build, the seam mcp 2 rides); stable-api § 8 shows
  submit → poll → fetch with it. **Rides with:** mkey 1, 2. **Re-test:** a bearer reader in
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
  on the box's trail. Action: the provider's error is the same refusal, filed once. **Rides with:**
  mkey 1, beside the row above, in the same hunk. **Re-test:** a callback with `?error=access_denied`
  adds one refusal to the box's trail.
- **A reopen serves a Start its reader may not follow**: `datasets/draft_build.py::HeldStart.of` serves
  the last `start-checkin` on the draft whoever filed it, while `command_reading.py::owned_command` hides
  a command another principal issued with a 404, so an owner reopening a delegate's Start sees
  `useQueuedStart` wait past the mint. Root: one rule for who may follow a Start, whoever may reopen its
  draft: the reopen serves the Start's own reading, or the command read lets an account in on a command
  filed on a subject it owns, never two rules. **Rides with:** mkey 4, the access review; its Owns gain
  `command_reading.py::owned_command` (mcp's file after, on mkey's tip), `draft_build.py::HeldStart`
  (rk-run's file before) and `webapp/lib/hooks/useQueuedStart.ts`. **Re-test:** a delegate Starts a
  check-in, the owner reopens it, and the page follows the Start to its mint.

### batch/29 · mcp

- **An arms gate's hold claims `input_required` and sends no input request**: `command_reading.py::job_status`
  sets the status (the arms gate is its one holder since the origin gate's hold went); `tasks/result`
  blocks until the run ends. Action: the hold becomes the task's input request (the arms shape for
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
  Action: the wire goes all-required only together with `strict: True`; the flag is
  `infrastructure/llm/openai_compat.py:418` (`"strict": False`). **Rides with:** tune 2, whose Owns gain
  `openai_compat.py`'s flag (rk-opt's Owns held `l1_wire_schema.py` alone, and the proof is a paid call).
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
- **`promptpotter/__init__.py` writes the data root at import**: `hold_private(user_data_root())` runs
  before any process's landing. Action: the entry that lands holds the root private, as the unit and the
  launcher set the process's environment. **Rides with:** box 1. **Re-test:**
  `git grep -n "hold_private" -- promptpotter/__init__.py` is empty.
- **The box runbook skips the move `site_records/` owes**: `deploy-linux/README.md` has no step that moves
  it out before `update.sh`; only `CHANGELOG.md` and `cd8593459`'s body say it. `update.sh`'s dry-run
  refuses and rolls back, so nothing is wiped. Action: a step that moves `site_records/` out before
  `update.sh`. **Rides with:** box 1. **Re-test:**
  `git grep -n "site_records" -- deploy-linux/README.md` names the move.
- **A resume's wait is unattributed**: on the box a resume takes up to 72 s to be applied, while
  offline its `open_launch`, `open_cycle` and `close_init` total about 0.3 s and the landing stamps once
  per schema, so the wait sits on the backend's network edges. Action: time `probe_backend`,
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
- **The git hooks call bare `python`**: `.githooks/pre-commit`, `commit-msg` and `pre-push` each
  `exec python scripts/gate.py …`, and the repo refuses bare `python` at import. Action: the hooks call the
  pinned venv's interpreter, the one `gate.py::_reexec_pinned` names. **Rides with:** rel 2, which holds
  `scripts/gate.py`; its Owns gain `.githooks/`. **Re-test:** `git grep -n "exec python" -- .githooks` is empty.
- **`paper/bbeh-comparison/bbeh_potter_runner.py` closes a client the run already closed**:
  `run_bbeh_campaign` calls `session.backend_client.aclose()` (`:229`) after `run_campaign`, which closes
  its own. Action: the line and its `suppress` go. **Rides with:** rel 1, which holds this script.
  **Re-test:** `git grep -n "aclose" -- paper/bbeh-comparison/bbeh_potter_runner.py` is empty.
- **The pinned venv is keyed on the raw `uv.lock`**: `scripts/gate.py::_install_digest` hashes the lock's
  bytes, so a worktree whose lock moved only outside the pinned extras (batch/22's mlflow exit) is
  refused a venv that `uv export --frozen` proves identical. Action: hash the resolved pinned set.
  **Rides with:** rel 2. **Re-test:** a lock edit outside `_PINNED_EXTRAS` leaves the digest unmoved.
- **The walk world has no outer/inner run, so the seat-cell drill never runs**: `scripts/gate.py::_walk_world`
  builds `offline_run.py --optimizer=potter --straight` alone, so `webapp/e2e/walk/cells.spec.ts:41`
  always skips. Action: the walk world carries an offline L4 leg, and the spec's skip becomes a
  failure. **Rides with:** rel 2. **Re-test:** `npx playwright test --project=walk e2e/walk/cells.spec.ts`
  passes 1, skips 0.
- **The dev loop still serializes on Vitest**: its pool fails a worker past its hard-coded 90 s start,
  which a host loaded from outside the gate lock still trips. **Rides with:** rel 2. **Re-test:** five
  builders plus one full gate, no reap.
- **`config/settings.py::_git_head` answers `UNVERSIONED` silently** for a ref in neither the loose refs
  nor `packed-refs`. Action: it refuses, naming the checkout it could not version. **Rides with:** rel 2.
  **Re-test:** a landing from a reftable clone names why.
- **Run data is stat'ed bare outside `store/io.py`**: `store/read_model.py::file_sig`, eight more
  `.stat()` sites and `measurement_archive.py::_named`'s `os.scandir` read a long path bare, so a ledger
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
- **`webapp/e2e/README.md` tells a reader to wait for a sibling gate**, a rule the gate itself should
  hold. Action: the gate holds it, and the sentence goes. **Rides with:** rel 2. **Re-test:** the README
  names no wait.
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
- **`landing_key` misses a body change**: `store/data_root.py::_schema` keys a reader by `module::qualname`
  and `landing.py::landing_key` keys migrations by name (`sorted(MIGRATIONS)`), so a reader's or a
  migration's body change never lands again. **Rides with:** rel. **Re-test:** edit a migration's body;
  `landing_key()` moves.
- **Survivor migrations are unchained**: `maintenance/migrations/consent_by_principal.py` reads
  `consent.jsonl` from disk, blind to the lines `consent_trail` stages into it in the same landing
  (`landing.py::_staged`), so an owner-plus-delegate account last landed before v0.8.19 gets its
  delegate's acceptance, and nothing runs it again. Root: each migration reads the root as staged so
  far, as `JobRegistry.as_staged` does. **Rides with:** rel. **Re-test:** a landing over a pre-v0.8.19
  consent trail keys each acceptance by its own principal.
- **A landing waits on every live producer on another commit, whether or not the ledger shape moved**,
  and on nothing else (`jobs/registry.py::JobRegistry.close_for_landing` reads the jobs holding spend):
  a server or read verb on other code holds no job, so `jobs/interlock.py` guards nothing against it,
  and a busy box may never land. Action: key it on a digest of the `CycleRecord` schema the code writes,
  held by every live process, in place of `config/settings.py::CODE_REVISION` through `jobs/interlock.py`
  and `deploy-linux/update.sh`. **Rides with:** rel, beside the stamp row above (one landing order); its
  Owns gain `jobs/interlock.py`, `jobs/registry.py::close_for_landing` and `deploy-linux/update.sh`
  (a box change: that hunk asks). **Re-test:** a landing that moves no record shape refuses while a run
  is live.

## Held — no wave-4 chain yet

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
  `runner/inner/spawn.py::sandbox_stores`. Action: one builder from (stores, hop). **Held:** none
  claims it. **Re-test:** `git grep -n "def inner_sandbox_store\|def sandbox_stores" -- promptpotter`.
- **Two fits of one reading**: `runner/round.py::measure_population` and
  `scoring/selection.py` both fit `candidate_abilities` over the same rows. Action: the election takes
  the measured abilities. **Held:** none claims it. **Re-test:** `git grep -n "candidate_abilities(" -- promptpotter`.
- **A sandbox writes account labels**: an inner mint relabels the account's dataset label from inside
  a sandbox (`jobs/mint.py::mint`, `tenant_datasets.relabel`); benign only while `dataset_hash` covers
  the whole dataset. Action: a sandboxed mint writes no account label. **Held:** none claims it.
  **Re-test:** an inner mint leaves the account's label file untouched.
- **A ± that measures nothing**: `seed-screen`'s `margin_se` reads 0.000 over identical temperature-0
  passes, which carry no row-sampling variance, and the panel quotes it as a precision. Action: an SE
  over row resamples, or none served. **Held:** none claims it. **Re-test:** a temperature-0 screen's
  `margin_se`.
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
