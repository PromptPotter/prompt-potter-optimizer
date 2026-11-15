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
  hold `drain_all` already released, so `run_phase_control.py::ProducerHold.release` keeps a double-release
  guard. Action: `drain_all` only drains; the release is the `finally`'s and the fork handoff's. This
  keeps the parent cycle attached through the fork mint, which moves `account_spend`'s holds, so it
  needs its own proof. **Re-test:** `ProducerHold.release` holds no already-released branch.
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
  (`cli/commands/launch.py::_ingest_checkin` vs `deps.py:149-152`). Action: `new --draft ID`, and one bound. **Rides with:** admit-2.

### The edge tail · passb-2 (after batch/26; `chain N` is passb's link N)

- **The first verify after a pause started 38 s late (2026-11-06, live)**: the click shows from admission now
  (the pass's slot serves `starting` then `running`, and an unended command reads `working`), so no
  `accepted` ack is owed: that status is a loop's standing command. Between the slot's START and the
  phase's `enter`, `scoring/passes.py::verify_candidate` opens the session (`open_diagnostic`), scans
  the archive (`measurements_for_config`) and fits the ruler (`judging_ruler`); a warm verify entered
  in 1 s. Action: time those three on a cold verify, then cut the one that holds it. **Held:** a live
  verify on a paused cycle. **Re-test:** time `command` → the `verify` phase's `enter` in the ledger.

### batch/26 · money

- **A run's ambient state rides three carriers** (money-2's report): `infrastructure/run_scope.py::RunScope`
  names no plan or sender and carries `round`, which the node context carries too, and
  `shared/measurement_context.py` holds two more ContextVars (`_MODE`, `_MEASURED`) against
  application `CLAUDE.md`'s "never a second ContextVar". Root: they ride `RunScope`, and the
  carriers' doc says what each holds. **Rides with:** a run-scope chain. **Re-test:**
  `git grep -c "ContextVar(" -- promptpotter` is 1.
- **A campaign's absence is still refused beside `CampaignStore.campaign`**: `cycle_reads.py:127`
  loads and raises its own `_not_found`; `scoring/measurement_log.py:464` refuses
  `campaign_scorer`'s `None` with its own `NotFoundError` (`campaign_scorer` should take a
  `Campaign`); a resume loads its campaign 4× (the door, `ceiling_meter_of`, `armed_budget`,
  `runner/mint.py::open_launch`). Action: those reads take the door's `Campaign`. **Rides with:**
  the next chain in `cycle_reads.py`. **Re-test:** one load per resume, by money-2's load counter.

### batch/26 · rk-opt (links 1-4 landed; the rows its cap left)

- **The browser inks a served state from its own map**: `webapp/lib/derivations/sample-trajectory.ts::MOVEMENT_CLASS`
  and `PipelineFlow`'s `tone`. Action: they go once a served word row carries its `Tone`
  (`domain/said.py`). **Re-test:** `git grep -n "MOVEMENT_CLASS" -- webapp/lib`.
- **Potter's layers keep a second member protocol**: `escalation.py::EscalationLayer` and
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

- **The live reads re-serve history per poll** (a hot-path pass, ✓ unless reported): `_cycle_cells`
  and `_campaign_edits` cost O((R·A)²) per request, `snapshot_frame` re-folds per connect, and a ray
  page re-folds per "older". Root: a value is its aggregate plus the live round, never a copy of its
  history. The same root serves the wire: the polled live dashboard re-serves every closed round on every frame (570 kB at 16
  rounds, `domain/dashboard_rows.py::served_rounds`); a browser stitch of `…/rounds/{n}` per round, or a
  new read, breaks R7, so the closed-round series' read is decided first, then every `dash.rounds` reader
  moves. **Rides with:** webd, once the closed-round read is decided (narr 2 held it as that fork);
  its Owns gain `hooks/useOriginEvidence.ts`, `hooks/useVerify.ts` and the `dash.rounds` panes.
  **Re-test:** `git grep -nE "dash\??\.rounds" -- webapp/lib webapp/components`.
- **The live dashboard tracks the race beside the narration** (narr-2's report):
  `live_dashboard.py`'s `_race` / `_race_standings` (`:440-441`, filled `:712-713`) fold
  `RaceStandingRecord`s apart from the `Narration` that reads the same ledger. Root: the race standing
  is one fold every door reads. **Rides with:** the row above. **Re-test:**
  `git grep -n "_race_standings" -- promptpotter` is empty.
- **A check-in cannot switch an upload between its connector's pipelines**: a draft edit has no chain
  key, so a mode outside the held chain is disabled (`PipelineSetupSection.tsx`), and
  `draft_build.py::_modes` ignores a definition's own `pipelines`. Action: a mode pick sets the draft's
  `pipelines.default`, its definition half. **Re-test:** pick TermNorm's `default` on an upload; the
  draft's `active_steps` is that chain.
- **A check-in's cycle plan selects beside its landing's refusal**: `checkin.py::_land_draft` refuses
  each overlay before landing, then `resolve_cycle_plan` selects and refuses again; planning needs the
  landed `Session`. Action: a check-in plans its cycles off the draft before landing, and a campaign's
  cycle config carries its selection. **Held:** a `Session` over a draft-held `ResolvedDataset` (rk-rest
  cut it: `wiring.py::init_services`, `session_bank` and `dataset_experiment` read the landed
  definition off disk). **Re-test:** one `select_optimizer` call
  between a draft's Start and its first cycle plan.
- **A per-cell criterion naming a term no cell carries compiles** (`authored.py::scorer_of`, called at
  the check-in by `datasets/draft_patch.py`) and refuses per cell at the run. Root: `compiler.py::compile_scorer`
  refuses a name no owner holds, `evaluators.py::cell_name_owners` beside the campaign's own owners (the
  connector's terms and report keys, the observations); a check-in-only list in `draft_patch.py` would
  be a second spelling of that roster. **Held:** the owners ride the scoring spec first (rk-rest cut
  it: no `scorer_of` reader outside the check-in holds them, and an optional owners argument is a
  flag); the compiler is a hashed unit, so the refusal moves digests. **Re-test:** a scoring edit `correct - no_such_term`
  is refused.
- **TermNorm serves no rewrite field**: TermNorm's `GET /pipeline` `llm_only.prompt_info` lacks
  `rewrite_field`. Action (TermNorm repo): serve `rewrite_field: instruction`, then
  `connectors/termnorm.py`'s `prompt_info` template line goes. **Re-test:**
  `git grep -n "rewrite_field" -- promptpotter/connectors/termnorm.py` is empty.

### batch/26 · addr

- **The lineage subtree sorts every candidate per candidate**: `application/hierarchy_subtree.py::_earlier`,
  O(n² log n) per read; reported beside it, the subtree read builds the whole top family per request,
  and `_descent` compares O(n²) pairs with `SequenceMatcher` behind an lru of 4096 that thrashes from
  ~90 candidates (`hierarchy_subtree.py:154-231`). Action: "closer to" and descent are folded once per
  candidate on the ledger: a record kind. **Rides with:** a lineage-fold chain of its own. **Re-test:** `git grep -n "def _earlier" -- promptpotter/application/hierarchy_subtree.py` is empty.
- **The feed's version spans a whole store**: `GET /feed`'s tick (`application/cycle_reads.py::feed_frames`) runs
  `application/hierarchy.py::entries_version` over every store, per connected tab, and a campaign
  subtree's version (`hierarchy_subtree.py`) covers its whole store, so any run's append re-asks every
  mounted tree. Action: one version per subject, kept by the append door and the sweep, so no tick walks
  a store; every writer stamps it (the ledger ingress, drafts, archive, ruler, commands,
  definitions). **Rides with:** a version chain of its own. **Re-test:** `git grep -n "entries_version(" -- promptpotter/application/cycle_reads.py` is empty.
  Beside it (a hot-path pass, ✓ unless reported): `read_model.py:224`'s `LEDGER_INDEX_MAX` = 256, so past
  256 cycles (one 20-round L4 campaign is ~240 inner campaigns) the feed's `_store_subjects` walk misses
  every tick and each miss re-folds with `model_validate_json` per line; reported, every `store:`-subject
  read re-runs `feed_versions` over all stores (`reads.py:214-232`), `read-cache` re-asks ~9 reads per
  sandbox frame, `machine_status` re-reads the job files four ways per tick (`list_running`, `queue_order` twice,
  `list_launched_today`; `machine_status.py:220,225,262`), `entries_version` parses every
  manifest, and archive-wide signatures serve as read versions (`evidence/read.py:551-554`,
  `measurement_log.py:271-280`), so any run's archive write invalidates every evidence and cells view.
  The same root: a writer-stamped version per subject.

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
- **Two tests fail beside their claim**: `tests/test_security.py` is not collectable in a venv lacking
  the `mcp` extra; `tests/test_scoring.py::_peer_cycle` files no round-0 cells, so a noise floor
  through it reads nothing. **Rides with:** passes-6.

### cfg-2 (what cfg left at landing 50; the re-key window)

cfg landed the one layered-document fold (`domain/layer_fold.py`) and every door laying through it;
cfg-2 laid every door's patch through one wire rule and every campaign default into `defaults.yaml`,
and derived the knob couplings from each member's draws and the escalation ladder.

- **The node document is typed two ways and the L2 steer is a side memory**: every door now parses a
  patch through `layer_fold.py::wire_patch`, but L2's steer is read through `L2Memory.steered` /
  `steered_layout` beside the fold, not as a named layer of `l1_generate`'s config, and about 58 of
  the engine's `dict[str, Any]` sites are node-config bags (`dict[str, NodeCall]` on `CycleStart`,
  `dict[str, dict[str, Any]]` on `ForkStart`, which carries a null clear and text a `NodeCall` refuses
  before the schema types it). Action: one typed node document with knobs a view on it; the steer a
  named layer. It closes ren 8's node-config census and the pipeline-switch row. **Rides with:** cfg.
- **A sub-model's knobs still default in code**: every `CampaignConfig` / `OptimizationConfig` knob's
  default lives in `assets/datasets/defaults.yaml` (`authored.py::defaults_config`), but a knob inside
  a nested model keeps a code default the file never shows: `LivesConfig` start 2 / cap 4,
  `JudgeSpec.temperature` 0.0, and `campaign_config.py::over_the_default`'s budget (usd 0.025, rounds
  50) laid under every budget a layer names. Action: each into `defaults.yaml`, its field required.
  **Rides with:** cfg. **Re-test:** no field of a model `CampaignConfig` nests carries a valued default.
- **Potter's demo axis is dead wherever a dataset holds out no demo pool**: the derived coupling
  `l1_generate.shots_without_demo_pool` (`application/config_map.py::_draw_couplings`) now badges every default potter
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

- **A fork's share lands at its landing**: a running cycle's job moves onto its fork through
  `jobs/launcher/admission.py::land_job`, which writes the fork's `CycleShareRecord` there, so a
  `set-limits` filed between the cut and the landing re-shares the campaign without the fork. Action:
  the share is taken against the campaign with the cut. **Rides with:** admit-2.
- **`ab` under an edited scorer refits what the archive still holds**: `difficulty.py::regraded_ruler`
  re-grades a ruler row off its address's archive answer, but `ruler.jsonl` is never rewritten and
  outlives those answers (hotpotqa's tenant scale, 2026-11-15: 305 of 372 addresses, 5,840 of 7,874
  rows, have none), so the edited-formula counterfactual reads a quarter of the scale and says
  nothing. Action: decide what scale an edited formula replays on (the filed rows' share it
  re-graded, served beside the verdict; or refuse below a coverage). **Rides with:** a design call.

### hubs (after valid, before layers)

- **A cycle's outcome is two types**: `domain/cycle_result.py::CycleResult` (what `run_cycle` returns,
  its rounds with their cells) and `domain/records/run_records.py::CycleFinal` (what `CycleFinalRecord`
  banks), which `runner/entry.py` fills from the other field by field. Banking `CycleResult` whole
  would put every round's cells on the ledger beside `RoundClosedRecord`, against the chronology rule
  (`infrastructure/CLAUDE.md`). Action: one type, the rounds read back off the ledger by its
  readers (`l4/proxies.py`, the narrative). **Rides with:** hubs.
  **Re-test:** `git grep -c "class CycleFinal(" -- promptpotter` is 0.
- **A moved panel is warned into a log**: `campaign_store/store.py::write_resolved_run` logs it as a
  `logger.warning` and appends anyway, a state the run enters and reports nowhere. Action: the moved
  panel lands as a field of `ResolvedRunRecord`, read by the readout. **Rides with:** hubs.
  **Re-test:** no `logger.warning` in `write_resolved_run`.
- **A hop's chain is spelled 16 times**: `ledger_chain(CycleDir(...), moment=None)` across the
  package. Action: one store read of a hop's chain (`CampaignStore.chain`). **Rides with:** hubs.
  **Re-test:** `git grep -c "ledger_chain(CycleDir(" -- promptpotter` sums to at most 1.
- **`p_record_drift` counts handler tables as mirrors**: its 33 is `ledger_scan.py` (12) and
  `live_dashboard.py` (21), folds and `_handle_*` tables that each read their own subset.
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
  - the domain: `spend` ↔ `run_records`, `sample`, `pipeline_schema`, `pass_kinds`;
  - bench ↔ optimizers: bench still reads `nodes.py`'s `CycleRead`, member protocols and
    `NODE_MEMBERS`, because the plugin contract and the round types share that module. Moving
    `CycleRead` alone re-formed runtime cycles.

  Action: each knot is cut at its root, held by `complexity_ledger.py::_type_cycles`. **Rides with:**
  layers. **Re-test:** the `type_cycles` row of `BASELINE` falls to 0.

### bkeys-2 (what bkeys left at its landing)

- **A rename re-keys every banked L4 cell's treatment**: the cell estimator is the L4 leg's behaviour
  (`nodes.py::BehaviourKeys.cell_estimator`), but `nodes.py::behaviour_key` still hashes each
  optimizer package's source into `InnerTaskSpec.optimizer_treatment`, so ren's renames moved all five
  treatment digests, every request byte-identical. Root: a treatment keyed by its fingerprint alone.
  **Rides with:** bkeys-2. **Re-test:** a rename in `optimizers/potter/` leaves `treatment:potter`
  (`offline_run.py --digests`) unchanged.
- **The trace hashes a display field** (rec's landing, reported): `MemberAddress.pass_role` has no
  reader but rides the round-file readings the trace hashes, so deleting it moves the behaviour keys.
  Root: the behaviour key reads the decisions, not the display fields nor their spelling: ren's renames
  re-keyed `behaviour_keys.json` by field names alone (decisions identical once normalized), and its
  landing wiped the banked origins with them. **Rides with:** bkeys-2. **Re-test:** deleting
  `pass_role`, or renaming a trace field, leaves `behaviour_keys.json` unchanged.

- **An L4 cell's key holds its config, not what its session loads** (✓): `InnerTaskSpec`
  (`runner/inner/tasks.py::InnerTasks`: node config, laid campaign identity, treatment, estimator)
  skips what the inner session loads live (`wiring.py:228-307`: `origin.yaml`, the `source:`
  cut and recipe, `task.md` framing, the pipelines walk, the backend revision), and pins freeze at outer
  run init while the session loads at each spawn; over-keying: `Seat.id` a label inside the pin, axes
  key order and mixed-manifest chain order. Root: the instrument hashes the seat's
  `MintPlan.origin_hash` plus its `measured_address`, resolved at the outer run's init (one async
  resolve and one backend read per seat), which `dataset_access.py::dataset_experiment`'s synchronous
  resolve cannot host. **Rides with:** bkeys-2. **Re-test:** an edit to an inner `task.md` moves the
  pin of every seat on that dataset.
- **The paid L4 browser walk's stop was sized for one inner round** (reported):
  `research/datasets/promptpotter-self/e2e/dataset.yaml:30`'s `usd: 0.05` predates the depth-2 floor,
  so `webapp/e2e/spend/l4.spec.ts` may halt on its ceiling. Action: size it from one measured depth-2
  run. **Rides with:** the next paid walk.

### readings-2 (what readings left at its landing)

- **A lift is read two ways**: `paired_reading.py::Estimate` is the one level and lift every surface
  renders, and `paired.py::_lift`'s test is the measurand's (exact sign where it is declared binary,
  paired t where graded), but a θ lift is a bare `Estimate` (`ruler.py::theta_lift`), never a
  `MeasuredLift`, so rk-dec's bar and rk-opt's lift test read two shapes. Action: θ returns
  `MeasuredLift` beside the paired kinds; θ and paired t stay distinct measurands (θ is unpaired and
  model-based, which PoBB's truncation needs); CAPO, GEPA and LEVI keep their papers' bars. No
  `EstimatorSpec.kind`: the measurand's declaration already picks the paired test, and a kind would be
  a second door to it. **Rides with:** rk-dec. **Re-test:** the chain's census lists the estimate
  readings; one remains.
- **φ's binariness is inferred from the values** (`rasch.py::_dispersion`) against the DECLARED rule
  (`paired_reading.py::Measurand.binary`; one 0.999 for 1.0 cuts θ SE 0.546 → 0.324), but the response
  θ reads (`objective`) is declared graded on every scorer (`scoring.py::ROW_GRADES`), so reading the
  declaration fits φ on every binary dataset, whose δ-on-the-same-rows residuals read φ = 0.10 (θ_se
  0.28 where the information gives 0.89: the election crowns on noise). Root: the scorer declares
  whether its objective is two-valued (no `per_cell` cost term on a hit), and `fit_theta` takes that
  declaration. The same missing declaration has two more holders:
  - `evidence/comparison.py::metric_measurand` hard-codes `binary=False` (`MetricSpec` declares none);
  - `crowning_bar.py::CrowningBar.separates` never separates a graded measurand.

  **Rides with:** readings' successor. **Re-test:** a 0.999 for a 1.0 moves θ_se < 1%, and
  `git grep -c "binary=False" -- promptpotter/application/evidence/comparison.py` is 0.
- **A verify compares against a point**: `VerifyReading.held` reads the recorded level as known, but
  the recorded level carries no band. Root: the record keeps the level's `Estimate`, and the verify
  reads both bands. **Re-test:** a verify whose recorded level has a wide band is never "held" on a
  point comparison.
- **`EliminationGate` is a one-member enum** (`potter/pobb.py:30`). It is wired through the lens and
  `mask/record.py:31` and stands in for a concept with one value. Action: delete it and its wiring.
  **Re-test:** `git grep -c EliminationGate -- promptpotter` is 0.
- **The warm gate counts no se_δ**: θ now reads each δ under its SE (`DeltaRuler.entries`), but the
  young-ruler joint MAP still shrinks δ̂ inward (slope 0.30 at 3 individuals per cell) and the warm
  gate warms on row counts alone.
 Root: δ by MML, or the warm gate reads se_δ.
  **Rides with:** readings' successor.


### batch/26 · rn-conn

- **The pipeline carrier is typed only at its door**: both connectors now read `NodeCall`'s fields,
  but `PipelineNode.current_config` is a `dict` (`domain/pipeline_parsing.py:381` builds it from the
  validated call), so `pipeline_schema.py` (7 reads), `pipeline_resolve.py:297` and `free_tier.py:137`
  read the model by string; `measurement_log.py::_span` and `wounds.py:184` read a laid dump the same
  way. The re-test also counts carriers that are no `NodeCall` (an OpenAI request body in
  `openai_compat.py`, a record payload in `audit_trail.py`, a model menu, dspy's `LM.kwargs`), which
  the action leaves alone. Action: type `current_config` as `NodeCall` and read its fields.
  **Re-test:** `git grep -cE "current_config\.get\(" -- promptpotter` is 0.

### batch/27 · rec

- **`PairedReading` is a tagged union spelled as optional fields and a validator**:
  `domain/paired_reading.py::PairedReading`'s `_state_fits_content` guards "a lift, its members,
  cell set and coverage exist exactly where `state` is `read`", and 138 reads across 27 files re-check
  `headline` / `coverage` for None. Action: a read arm and an unread arm discriminated on `state`, the
  validator and the readers' None branches deleted. A reshape wipes run data. **Re-test:**
  `git grep -c "_state_fits_content" -- promptpotter/domain/paired_reading.py` is 0.
- **Fields written, never read: 27 left** (`scripts/complexity_ledger.py::_unread_fields`, the
  `p_unread_fields` ceiling). `InnerCellFacts.origin_headroom` sits in the hashed `l4/proxies.py`. Action: each goes, or names its reader. **Re-test:** the ceiling is 0.
- **`SampleScoredRecord.running` stamps a running fold on every cell** (9.3 % of a 1.5 MB ledger). Its
  readers (`Narration.fold`, `StandingArm.fold`, the live board's rows) need the scorer's evaluators,
  which no domain fold holds. Action: the readers fold `StandingArm.cells` through
  `metrics.py::compute_composite_fitness` in `application/`, and the field goes (run data wiped).
- **Generated `last` record classes live in module globals so a snapshot can pickle them** (rec's
  landing): `store/read_model.py::last_fold` builds a class at runtime and registers it with
  `globals()[made.__name__] = made`. Action: the last-wins records become declared classes, and the
  registration goes. **Re-test:** `git grep -c "globals()\[" -- promptpotter/infrastructure/store/read_model.py` is 0.
- **An empty round's error rate reads 0.0** (rec's landing, reported):
  `domain/round_diagnostics.py::RoundDiagnostics.error_rate` stays a `float` where no cell ran, the
  absent-as-zero shape rec closed for a round's fitness. Action: `None` where no cells ran.
  **Re-test:** a round with no scored cell serves `error_rate: null`.

### webd (the edge tail, after lw)

- **Adapters over a flat served shape** (webb-5's): `useCandidatesModel.ts` joins about twelve sources;
  `.facts.` is opened at many sites; liveness is re-derived at each `phaseWalks(` site. Action: the
  served rows carry what the adapters join. **Rides with:** webd. **Re-test:**
  `git grep -c "\.facts\." -- webapp/components webapp/lib`.
- **A round's markdown names its lift column from the first arm** (webb-5b's report):
  `runner/output.py::_render_variants_table` reads the first fit's `vs_reference.cell_set`, the
  first-arm shape `RoundFileView` dropped, because the served name sits on `ArmReading` and the table
  reads `ArmFit`. Root: the name is the pair's, served wherever the pair is, so every reader takes each
  arm's own. **Rides with:** webd. **Re-test:** `git grep -n "lift_versus('parent', sheet)" -- promptpotter` is empty.
- **The browser recomputes engine facts**: `webapp/lib/derivations/` (~2.2k lines, plus tests) derives
  node config, spend, run phase and the search point the engine already computes. Action: each fact is
  served on its existing read and the TypeScript copy and its tests go; so do `wonOnTheta` (9 hits in 5 files; its filed row and `electedMetric` went, ec96b1cda) and
  `ConfigMode` (`lib/derivations/node-config.ts:11`; no row files it). So does `run-phase.ts:51`'s copy
  of a cycle's acts, which drops only `start-run` where `phases.py:447` drops every act of a refused
  admission (narr-2's review): the acts are served once faults 1 serves `next_acts`. **Rides with:** webd. **Re-test:**
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
  `reserve_usd`, `incurred_usd_ratio`, `search_usd`, …) and refusals (`refused_efforts`,
  `indistinct_efforts`); `ReadKind.HEAD_TO_HEAD_PRICE` is the one read no webapp
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
  `ConfigOverrides` / `ForkStart` beside the generated `CommandSent`; SSE frames are cast with `as`
  (`useCycleEvents.ts:49`, `read-cache.ts:390`); the build handshake covers fetches, not streams; and
  the client decides what Delete files (`CampaignMenu.tsx:44-54` vs `workspace_edits.py:62-77`).
  **Rides with:** webd.
- **An L4 panel's Leader card is empty** (C0 on a3312e3e1, 390 and 1280): `panes.tsx::LeaderPane`'s
  `SubjectBox` draws nothing for an origin-only outer campaign and no line says why (I1). **Rides
  with:** webd. **Re-test:** that card names the origin or says why it has no leader.

### batch/28 · lb

- **Wave B — `application/runner/` is 10 modules, not 8.** lb collapsed it by phase (21 modules to
  10) and deleted the ledger-scan forwarders. The two left over the target: `termination.py` (row
  below) and `inner/narrative.py`, which no digest hashes apart any more. The registry fold is the
  row below.
  **Held:** the termination row. **Re-test:** `git ls-files 'promptpotter/application/runner/*.py' | grep -vc __init__` is 8 or fewer.
- **`runner/termination.py` is a phase module that `jobs/` reads**: `jobs/registry.py` imports
  `run_stop_reason`, so the stop vocabulary cannot join a phase module without a cycle
  (`scoring/closed_rounds.py` → `pipeline_resolve.py` → `jobs/quota.py` → `jobs/registry.py`). Action:
  the stop vocabulary moves to `domain/phases`. **Held:** no chain reaches `domain/phases` yet.
  **Re-test:** `git ls-files promptpotter/application/runner/termination.py` is non-empty.

### batch/28 · faults

- **An inline launch queued on a full machine prints nothing** (faults-2b's report; its `unloadable`
  half closed at faults 3): `launcher/launch.py::Admitted.hand_over` awaits the hold, whose
  `admit_and_hold` waits in `JobRegistry.await_claim`, before the inline door holds any answer.
  Action: the inline `Launched` answers before its wait, carrying the queued `LaunchedJob` as the
  detached one does, and the terminal words it with `machine_notice`'s words (nothing on stdout
  under `--json`), then awaits the hold; no sink on `Inline`. **Rides with:** the next chain in
  `application/commands/launching.py`. **Re-test:** `new` inline with `MACHINE_RUN_CAPACITY=0`
  prints its queue place.

### batch/29 · xconn

- **PromptPotter does not read TermNorm's served wire contract**: the probe reads `/status` while
  TermNorm's `xconn` branch serves `GET /version` (`{revision, contract}`); `connectors/termnorm.py`
  probes `/status` and `::_EXPECTED_REVISION` is `None`, so a co-owned backend fix replays stale results;
  on the `xconn` branch `/status` loses its version word, so `_termnorm_version_check` reads None there.
  Action: the probe reads `/version` and refuses another contract; the revision folds into TermNorm's
  answer identity. **Rides with:** xconn 3, after TermNorm's `xconn` reaches the box. **Re-test:** a
  launch against a backend one contract behind fails at the probe, naming both numbers;
  `connectors/termnorm.py` probes `/version`.
- **An identity re-key moves offline decisions**: an arm's content id carries identity-only keys
  (`pipeline_resolve.py::identity_layer`). A pure re-key therefore changes which cells are drawn and
  raced. xconn 3's revision leaf alone moved potter's crossed leader (C2.6 → C2.7) and p_best/θ in
  capo-pobb, levi and potter (`wip/xconn` f70980fe7). Action: an arm's id derives from what the arm
  sends, never from its identity keys. **Rides with:** xconn 3, before it lands. **Re-test:**
  `wip/xconn`'s run against its base moves only `backend_revision` leaves.
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
- **An L4 launch never probes the connectors its inner cells name** (the C0 re-measure at landing 72
  halted on `TERMNORM_ENDPOINTS is unset` after its mint): `admission.py::probe_backend` probes the outer
  backend alone, so an inner cell's connector fails per cell inside `runner/inner/spawn.py`, once the
  cycle is minted and billed. Root: the outer preflight probes every connector its inner cells
  resolve. **Rides with:** xconn 3, the probe's link. **Re-test:** an L4 launch with an inner
  connector's endpoints unset is refused before its mint, naming the connector.

### batch/29 · mcp

- **The MCP door's claude.ai connector is unproven on the box**: the door serves Streamable HTTP at
  `/mcp/` on the account's bearer (`promptpotter/presentation/mcp/server.py::HttpDoor`). Action: the
  operator's step after the box update, a connector with the token as its `Authorization` request
  header. **Rides with:** the next box update. **Re-test:** in claude.ai, a custom connector at the
  box's MCP URL answers `tools/list`.
- **The HTTP door's sweep reads the SDK's private `_server_instances`** (tv-2's report): a session
  that expires idle leaves only at the door's next request. Action: the sweep rides the SDK's own
  session-end hook, or the door owns its session table. **Rides with:** the next mcp chain.
  **Re-test:** `git grep -n "_server_instances" -- promptpotter` is empty.

### batch/29 · tv-2 (what tv-2 left at its landing)

- **The main workspace follows the exhibit too, and its run controls 404 there** (tv-2's report): a
  follower's pause and stop reach a run they may not act on (I3). Action: the controls read the
  follow's `said.key` and offer no act on an exhibit. **Re-test:** follow the exhibit as a
  non-host; the workspace offers no run control.
- **The account feed never re-asks an exhibit's reads when the host's store moves**: a follower's
  view stands still. Action: the feed's subject includes the exhibit's. **Re-test:** the host's
  round closes; the follower's dashboard moves without a reload.
- **`ActiveSessionResponse` carries `hop` and `said` together only for an exhibit**: two shapes in one
  response. Action: one shape, the follow read as its own row. **Re-test:** every
  `/sessions/active` answer has one shape.
- **A follower reads with the host's whole `Stores`** (tv-2's access review): `exhibit.py::exhibit_stores`
  serves every host store, so the exhibit's ETag folds the host's command log, drafts and every
  campaign (`application/hierarchy.py::entries_version`), an activity side channel; the raw cycle and campaign
  files a follower reads carry the host's `issued_by` principal (`ForkSpec`, `RebaseRequest`). The
  descent, the moment and the root are refused off the exhibit (landing 89's fix pass), each a
  check. Action: a `Stores` narrowed to the exhibit's campaign, so no read can name another, and the
  served files carry no principal a follower may not see. **Rides with:** the next access chain.
  **Re-test:** the exhibit's hierarchy ETag holds while the host edits a draft.
- **A follower's live tail outlives its switch**: `cycle_reads.py`'s SSE stream re-checks the carrier
  alone, so turning "Try & learn" off keeps the exhibit's tail open until a reconnect (a block ends it).
  Action: the stream re-reads `exhibit_stores` per frame batch. **Re-test:** switch off mid-stream;
  the stream ends.

### batch/30 · ws

- **Compare cards are laid out for few wide columns**, not five thin stripes on a 16:9 display (the
  column rule is `webapp/app/styles/domains/compare.css:40-42`). **Rides with:** ws 3. **Re-test:** five
  candidates at 1920×1080 sit in a row, each card at its full content (a wrapped second row fails).
- **A `candidate:` subject names no round** (ws-2's report): `cycle_paths.py::Subject.key` spells
  `candidate:<campaign>/<cycle>/<arm>` and `evidence/read.py::_candidate_point` resolves the newest
  scored round, so in the walk world "Compare C1.1 with C0" reads C2.1 · round 2 (one arm key,
  `4fe6d83270e5bef5`, at both). The cells filter (`candidate_id`), `ScenarioEdits` and
  `mask/tree_lens.py::_Overlay.readings` (cycle, arm) share the gap. Action: the subject carries the
  round, keyed as the hierarchy address. **Rides with:** a chain after tests-w (`tests/` spells the
  grammar). **Re-test:** comparing a same-key round-1 arm, its channel reads round 1.
- **Compare resolves an inner campaign by its bare id** (ws-2's report): a channel on an inner
  campaign asks `GET /hierarchy/<inner>` and `/campaigns/<inner>/pipeline` with no chain, a 404 the
  walk's problem check reads red. Suspected root: `ChannelCards.tsx:47` and `ComparePane.tsx:70`
  resolve a channel's run by `lib/derivations::campaignAt` over a bare `rootCampaignId`. **Rides with:** the candidate-subject chain. **Re-test:** comparing an inner
  candidate, no request 404s.
- **The freed tree crowds itself** (ws-2's report): its labels collide around an inner lane
  (`R2 justlogic/d234` over `C2.1`, the `θ` labels overlapping), and once it is freed the Candidates
  card sits at half width beside empty space (`dashboard-layout.css:8`'s auto-fit tracks). **Rides
  with:** ws 3. **Re-test:** the freed tree's walk shot at 1280×800, no overlap, the card full width.

### batch/30 · forks

- **Money text has no sub-cent rule**: a cell's price at six places (`phase_acts.py::CapUnderRound.costing`,
  `cli/commands/reads.py::_human`, and `potter/dispatch/injections/panels.py:1179-1180`) beside `domain/spend.py::usd_text`, which prints `$0.0000`
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
- **"slot" survives in the text an LLM reads** (ren's report): prompt prose, `{{slot}}` tokens and the
  `not_a_slot` / `stable_slot` names keep the word ren retired in code (arm position, prompt field,
  region). Root: the optimizer prompts say what the code says; a prompt edit is a measured change,
  so it lands with its paid comparison. **Rides with:** tune 1. **Re-test:**
  `git grep -c "slot" -- 'promptpotter/assets/optimizers/*/pipeline.yaml'` is 0. "panel" likewise
  (small-3): the prompts' "panel" means only a set of cells, never a round's arms (`RoundOutcome.proposed`).

### batch/31 · small

- **The paper harness tracks no named tree** (small-1's landing): `paper/bbeh-comparison/bbeh_potter_runner.py:1`
  pins `promptpotter==0.8.20` while the ledger fixes it against the current API. Action: the
  harness names the release it runs against, and a fix to it targets that release. **Rides with:** the
  next chain touching `paper/bbeh-comparison/`.
- **A reclaim that empties a cell file leaves it** (small-4's report): `measurement_archive.py::rewrite_cell_file`
  writes a 0-byte file, which `keys(ArchiveFile.CELLS)` still lists. Root: `rewrite_pair` has no
  "delete when empty" outcome. **Rides with:** the next archive chain. **Re-test:** after a reclaim
  of a dataset's every row, `keys(CELLS)` names none of its files.
- **The reclaim control takes its dataset as free text** (small-4's report):
  `webapp/components/account/ArchiveCompactionControl.tsx` has no served list of the account's
  datasets to pick from. Root: no `ReadKind` lists an account's datasets. **Rides with:** the next
  chain in `application/read_params.py`. **Re-test:** the control offers only datasets the archive holds.

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

- **Two wounded legs move under load** (narr-3's landing): `offline_run.py --against` read
  gepa/wounded and levi/wounded MOVED on `decisions.ledger.round_warnings` while `gate.py --changed`
  ran its pytest beside it on 16 cores, and 20/20 UNMOVED alone on the same tree. A round warning in
  a scripted world depends on wall time or scheduling. Root: the offline world's clock and order are
  its own, so no decision or warning reads the desk's load. **Rides with:** tests-w. **Re-test:** the
  proof run beside a busy pytest reads every leg UNMOVED (keep its `eq_*` home to diff the warning).

- **`seat`, `_peer_cycle` and `close_round` replay production by hand**: the harness opens its line
  through `runner/entry.py::launch_scope` and `runner/entry.py::open_line`, as `run_optimization`
  does, but 30 `seat` sites (a custom `schema=`, `scorer=` or `origin=`), 16 `close_round` sites and
  14 `tests/test_scoring.py::_peer_cycle` sites build a `Cycle` by hand on `factories.py::loop_session`,
  whose connector holds its own sends, so no cell it sends gets a bound and the world cannot measure
  it: `_peer_cycle` patches `query_loop.measure_sample` instead. Action: `FakeBackend` serves the
  pipeline and answers a test declares, the sites open through `run_init_round` on it, and `seat`,
  `_peer_cycle` and `close_round` go. **Rides with:** tests-w's successor, whose Owns are tests-w's.
  **Re-test:** `git grep -c "def seat(\|def close_round(" -- tests/factories.py` is 0.
- **Smells the test-shape split left** (reported by its builders): facts that never touch the
  network take the `offline` fixture only because `factories.py::spend_book` needs the account it
  binds; seven `tests/test_scoring.py` bench facts re-run the CAPO round for a live cycle (the
  `_peer_cycle` row above), the command-door facts among them needing no bench round. Action: a spend
  book binds an account without the offline world; the bench facts share one module-scoped round.
  **Rides with:** tests-w's successor.
- **One guard is tested once per door**: tests grew +2.3k lines in three days, mostly security and
  identity, a test per door of one guard; the test-shape split (landing 43) kept every assert. Action:
  a guard living at one choke point is tested once, over the table of its doors; a guard spelled at
  more than one door is a row for the chain owning it. **Rides with:** tests-w's successor.
- **A test stands outside the harness**: `tests/test_archive.py` fakes a `HeldRun` with a
  `SimpleNamespace` (`test_a_fork_by_another_issuer_withdraws_the_launch_queued_on_its_parent`), so a
  renamed field passes there; the real `Admitted.held` needs a runnable bank under the hand-built
  scenario line (the `seat` row's root). Action: the real record. **Rides with:** tests-w's successor.
  **Re-test:** `git grep -c "SimpleNamespace" -- tests/test_archive.py` is 0.
- **A fact's home outlives it** (tests-w's report): `runner/mint.py` calls
  `pricing.py::refresh_rates_in_background` per mint, a daemon thread that can outlive its fact's
  `tmp_path` home, and pytest's `tmp_path`
  retention keeps every home on disk. Root first: whether a refresh belongs to a mint at all, or to
  the process that prices. **Rides with:** tests-w's successor. **Re-test:** after the suite, no
  refresh thread alive past its fact, and no home retained beyond pytest's last run.

### batch/32 · ts-score

- **The capo offline bench turns on one seed**: lift +0.700 or none after a draw-order change. Action: it
  reads two seeds. **Rides with:** ts-score 3.

### batch/32 · ren

Each rename lands with its doc and `CLAUDE.md` row in one commit. **Re-test:** the old word's
`git grep -c` for that sense is 0.

- **Node config is one document read through ~11 modules**: schema, parsing, overlay and knobs each
  hold a view of it after landing 30's one layered read. Action: one model of the node document with
  knobs a view on it. **Rides with:** cfg (its declared merge rule).

### batch/32 · rel

- **The dev loop still serializes on Vitest**: its pool fails a worker past its hard-coded 90 s start,
  which a host loaded from outside the gate lock still trips. **Rides with:** rel 2. **Re-test:** five
  builders plus one full gate, no reap.
- **Playwright's server starts beside the gate's heaviest checks**: the push gate of 6fa93bab0 read
  `Timed out waiting 120000ms from config.webServer` (`webapp/playwright.config.ts::server`'s
  `timeout`) with its server ready ~5 s late, beside mypy (428 s, cold cache), pytest and offline-run;
  `gate.py --only playwright` alone was green in 276 s. The rail measures the desk's load, as Vitest's
  90 s start does. Root: the gate admits a check by memory alone, so a timed start runs beside the
  CPU-bound checks. **Rides with:** rel 2. **Re-test:** the push gate with a cold mypy cache, playwright green.
- **On Windows a replace is refused while a reader holds the file**: one thread looping `write_bytes`
  and four looping `read_bytes_optional` on one file for 20 s saw 86 of ~210 writes raise `PermissionError`
  past `store/io.py::_atomic_replace`'s three tries, at 6fa93bab0 (whose reads retry, 0 refused, where
  fdad7947d's refused 49). Root: a read opens without `FILE_SHARE_DELETE`, so the swap waits on every
  open reader; the box (Linux) is untouched. **Rides with:** rel 2. **Re-test:** the hammer, 0 refused writes.
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

### wave-5 · l4 (what l4-measure and l1-cite left at their landing)

- **No test runs a detached job**: `test_spend.py` monkeypatches `run_job.py::spawn_job`, so a sync lock
  on `run_job`'s loop killed every server launch and head-to-head arm for two days with the gate green
  (fixed `d17fb2f83`). Action: one test drives `run_job` on a loop against a real registry (adopt,
  refuse, release). **Re-test:** reintroduce a sync `job_registry.adopt` in `run_job`; a test fails.
- **A moved search point escapes the end pass unreported**: `domain/arms.py::BankedSearchPointError` is
  not in `runner/termination.py::RUN_STOPS`, so `runner/entry.py::_run_single_cycle`'s end-pass `except`
  lets it past `close_cycle`; under `every_adoption` every graded roster member can raise it. Action:
  the end pass's refusal is a stop the cycle closes on. **Re-test:** a roster member whose point no
  longer rebuilds ends the cycle with a stop reason, not a traceback.
- **A citation's panel is matched by substring, not by word**: `potter/validators.py::l1_evidence_grounding`
  tests `words in _words(panel)`, so a one-word quote is held by whichever panel sorts first.
  **Re-test:** a one-word citation that only one panel holds as a word names that panel.
- **The derived citation panel is not served**: `L1Variants.tsx` reads the raw reply, which no longer
  names a panel. Action: serve the child's `EvidenceGrounding.field` on its variant row.
- **The offline run refuses outside a git clone**: `processes.py::clone_root`, so a base copy for
  `gate.py --only equals-main --against` needs a detached worktree. **Re-test:** run
  `scripts/offline_run.py` in a `git archive` copy.

## Held — no wave-4 chain yet

- **The estimator has no golden corpus**: its key (`nodes.py::BehaviourKeys.cell_estimator`) reads the
  L4 leg's trace on the offline world, not its outputs on fixed inputs. A corpus would read each inner cell's `CycleResult` (~255 KB each) and the
  tenant ruler's response rows: over 1 MB, and its only source is account data on the box, which the
  operator may wipe. **Held:** a corpus source the repo can commit under 1 MB. **Re-test:** the
  estimator's entry in the behaviour-key file names its key's inputs.
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
- **Nothing keeps an L4 edit L4-only**: Shape: nothing refuses a mark in `runner/inner/narrative.py` or
  an optimizer import of it, and potter's `panels.py` reaches `OuterSampleProxies`, `outer_proxies_of`,
  `inner_facts_of` and `InnerCellFacts`, so an edit to the scored term re-keys every potter campaign.
  Action: the injection reads L4 cells through the connector's served terms (D6). **Held:** the
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
