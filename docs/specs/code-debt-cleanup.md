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
- **Three launch faults from a code review (reported; verify each at its line first)**: `jobs/head_to_head.py:178`
  leaves a partial mint's arms unstamped, so a retry refuses `arm_taken`; `mint_and_start.py:108` and
  `checkin.py:93` leak the `BackendClient` they open when the mint is refused; ties between
  launches broken on a random `job_id` can refuse two head-to-head arms with one stamp each other
  `launch_contended`. Action: one mint door owns the client and the stamp.
  **Rides with:** admit-2.
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

- **A connector's name is looked up again where its `Connector` is held** (served-1's report):
  `free_tier.py::free_fence`, `pipeline_resolve.py::laid_config` and
  `jobs/launcher/admission.py::refuse_unreachable` take a `str`, so callers in
  `pipeline_resolve.py`, `datasets/draft_patch.py` and `jobs/launcher/` (`checkin.py`,
  `head_to_head.py`, `mint_and_start.py`) pass `.name` and `free_fence` looks it up again;
  `UploadBasis.connector` (`pipeline_resolve.py`) is a name every read resolves. Action: they take
  the `Connector`, and the campaign-name sites (`_campaign_merge`, `jobs/mint.py`, `mask/load.py`,
  `hierarchy.py`'s `is_self_optimization` call) look it up once. **Rides with:** after load-2 (it shares
  `admission.py` and `pipeline_resolve.py`). **Re-test:** `git grep -n "connector\.name" --
  promptpotter/application/pipeline_resolve.py promptpotter/application/datasets promptpotter/application/jobs` → 0.

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

- **`_peer_cycle` seats an origin reading the archive does not hold** (usage-1's report):
  `tests/test_scoring.py::_peer_cycle`'s `measured_origin` builds round 0's seated sheet from the
  bare `_measure`, never `score_search_point`, so the reading names rows no cell file backs and no
  round 0 closes; a fact reading round 0 walks it itself (`_closed_origin`, `open_origin_round`).
  Root: seat the origin through the walk that files it. **Rides with:** the chain owning
  `tests/factories.py::seat`. **Re-test:** a noise floor through `_peer_cycle` alone reads its
  round-0 rows.
- **Two precedence rules join an arm's rows** (small-13's report): `bench/cycle.py::ArmWalk.continued`
  keeps the EARLIER row (`where(_unheld)`), while `CellSheet.merged` lets the later scored row win.
  Root: one rule, `merged`'s; `continued` merges without the filter. **Re-test:** an arm re-read on a
  cell it holds reads the later row on every path.

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
- **Potter's demo axis is dead wherever a dataset holds out no demo pool**: the derived coupling
  `l1_generate.shots_without_demo_pool` (`application/config_map.py::_draw_couplings`) now badges every default potter
  campaign, because `defaults.yaml` holds out no `dataset_split.demo` and only justlogic declares one
  (`demo: 50`), while `l1_generate.k_max` is 5. The badge is true; the default is the fork. Action
  (operator's call): hold out a demo split by default (every split re-keys, search loses rows) or drop
  the axis from the default (`k_max: 0`, re-keys potter). **Re-test:** a default potter campaign on a
  shipped dataset raises no `inert` coupling.

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

- **The cycle index reads its chain beside the store's**: `projections/cycle_index.py::read_cycle_index`
  takes a bare cycle dir and spells `ledger_chain(CycleDir(...))` itself, since the store imports it
  (`CampaignStore.load`) and so it cannot read `CampaignStore.chain`. Action: the index fold is
  handed its hop's chain, `CycleIndexProjection` keyed by hop. **Rides with:** hubs.
  **Re-test:** `git grep -c "ledger_chain(CycleDir(" -- promptpotter` sums to at most 1.

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
  and the same stop now pays the held-out seat's outer bench passes (the origin's, one per adoption),
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
  not press (`from_round`, …) come out of the count first. A stream frame's
  `ProjectionEnvelope.payload` is `dict[str, Any]`, outside the contract's shape, and
  `useCycleEvents.ts` reads only its `cycle_id` and `activity` (webd-3's report). Action: a gate check holds
  every served response field to a webapp reader, or to its model naming why not (as `Verb.unspelled` /
  `terminal_only` do for the CLI), on a baseline that only falls. **Rides with:** webd. **Re-test:**
  the check's baseline is 0.
- **Sizes are literals beside the tokens**: 992 literal `px` / `rem` sizes across 45 non-token CSS files,
  and font literals in the charts (`CostStrip`, `EvidenceCharts`, `FitnessChart`), against the
  tokens rule; form fields are skinned outside `base.css` (`scoring.css:18`, `chat.css:24`,
  `onboarding.css:44`). Action: each size reads a token of
  `webapp/app/styles/foundation/tokens.css`, fields skin in `base.css` alone, and a gate check holds
  the literal count to a falling baseline. **Rides with:** webd. **Re-test:** the check's baseline is 0.
- **The webapp's live state lands on the wrong subject or the wrong version** (a live-state pass,
  reported): feed versions are unordered hashes
  (`application/cycle_reads.py::feed_versions`, a sha of `repr`), so a stale read reverts a draft
  edit, an ack shows applied before the feed moves, and a second press files a second start-run
  (root: a monotone version per subject). **Rides with:** webd.
- **An inner campaign serves a Delete no door presses** (webd-3's report): `domain/hierarchy.py::SubtreeCampaign`
  subclasses `HierarchyCampaign`, so an inner campaign in the deep read inherits its
  `delete: DeleteAct` (`delete-campaign`), which nothing may file for it. Root: the subtree entry is
  not a listed entry; it shares a base, not the listed one's acts. **Re-test:** the deep read's
  inner campaign serves no `delete`.
- **A round's own arms are spelled beside `MeasurementRole.is_proposal`** (live-3f's report):
  `scoring/passes.py:266`, `domain/run_standing.py:444`, `store/lineage_queries.py:184,203` still
  write `role != MeasurementRole.PARENT`. Action: each asks `is_proposal`. **Re-test:**
  `git grep -c "!= MeasurementRole.PARENT" -- promptpotter` is 0.

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
- **A test seat binds a campaign disk does not hold** (load-2's review, cut by load-3):
  `factories.py::loop_session` seats a synthetic `minted_campaign(hop)`; reading the stored one
  breaks 19 facts whose fixtures seat a hop (`test_scoring.py::_PEER_HOP`) and store its campaign
  after, across `test_scoring.py` / `test_security.py`. Action: each seat stores its campaign first,
  and `loop_session` reads it. **Re-test:** `git grep -c "minted_campaign(hop)" -- tests/factories.py`
  drops by the seat's one.

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

- **Vitest's start still meets load from outside the gate lock**: inside a gate it starts beside no
  check (`gate.py::Check`'s `started`), but a builder outside the lock still shares its hard-coded 90 s
  worker start; five looping builders plus one full gate reaped none on a 4-core Linux box (127 runs).
  **Rides with:** rel 2. **Re-test:** the same on the Windows desk, no reap.
- **`Check.slots` is a guess beside `mb`'s measured peak**: Playwright claimed 3 slots and used 0.5;
  offline-run claims `min(8, cores)`. Action: derive slots from measured CPU-s ÷ wall as `mb` is.
  **Held:** none claims it. **Re-test:** each `slots` within a quarter of its measured ratio.
- **On Windows a replace is refused while a reader holds the file**: one thread looping `write_bytes`
  and four looping `read_bytes_optional` on one file for 20 s saw 86 of ~210 writes raise `PermissionError`
  past `store/io.py::_atomic_replace`'s three tries, at 6fa93bab0 (whose reads retry, 0 refused, where
  fdad7947d's refused 49). Live 2026-11-19: it cut an L4 outer cell on the desk (`multistep-wide__773fe8`
  round 1, C1.3 #000: `PermissionError: [WinError 32]` replacing an inner job file under
  `.promptpotter/jobs/`); 94 of 182 writes refused at `61fff035c`. Two fixes measured and reverted:
  reads sharing delete (a `CreateFileW` opener) alone refuse more (95 of 99), as `os.replace`
  (MoveFileEx) refuses any destination still open; adding a POSIX rename (`FileRenameInfoEx`,
  replace | posix) lands every write, but reads then see the file absent in bursts (0, 163, 167,
  382 of ~10k a 25-s run), an immediate re-open included, which is a silent wrong read. **Held:** a
  design whose reader never reads a present file as absent; the box (Linux) is untouched.
  **Rides with:** rel 2. **Re-test:** the hammer, 0 refused writes and 0 absent reads.
- **Run data is stat'ed bare outside `store/io.py`**: six
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
- **A landing waits on every live producer on another commit, whether or not the ledger shape moved**,
  and on nothing else (`jobs/registry.py::JobRegistry.close_for_landing` reads the jobs holding spend):
  a server or read verb on other code holds no job, so `jobs/interlock.py` guards nothing against it,
  and a busy box may never land. Beside it (reported), a deploy dying mid-landing wedges every launch:
  `update.sh` dying after close-admission leaves the landing marker (`interlock.py:36-47`), so the old
  code refuses every launch with `landing`. Action: key the wait on a digest of the `CycleRecord`
  schema the code writes, held by every live process, in place of `config/settings.py::CODE_REVISION`
  through `jobs/interlock.py` and `deploy-linux/update.sh`, and back the marker by the deployer's OS
  lock. **Rides with:** rel (the landing stamp now orders by version, `landing.py::LandedStamp`); its Owns gain
  `jobs/interlock.py`, `jobs/registry.py::close_for_landing` and `deploy-linux/update.sh` (a box change:
  that hunk asks). **Re-test:** a landing that moves no record shape refuses while a run is live; a
  deploy killed after close-admission leaves no marker the next launch refuses on.
- **A reader's helper body is not keyed** (land-order's report): `landing.py::landing_key` hashes
  each reader's and migration's own source, so a helper a reader calls (the record validators behind
  `ledger_line`) or a pydantic validator on a row's model can change what loads without moving the
  key. Root: a shape's loading behaviour has no digest beyond its JSON schema. **Rides with:** rel.
  **Re-test:** edit a validator a survivor's model runs; `landing_key()` moves.

### wave-5 · store (what store-2f, store-2g and store-3b left at their landing)

- **A launch's steer verdict reads the root's narrowing**: `commands/launching.py` reads a launch's
  node rows at its hop ("a row's menu is its cycle's own declaration") but judges its steer with
  `permitted_narrowing(stores, of)`, the campaign root's, where a fork's start judges against its
  parent cycle's (`fork_siblings.py::operator_start`). A steer outside a forked line's narrowing and
  inside the root's reads `clean`. Action: decide which narrowing a launch at a hop answers to (the
  babysit act, decisions.md § 0005) and read it there. **Re-test:** a launch at a fork whose line
  narrowed a model steers to the root-only model and reads `babysat`.
- **A store under the data root off its workspace shapes** (tests-x's report): `build_stores` refuses
  only a root outside `user_data_root()`; one under it matching no `io.py::WORKSPACE_SHAPES` (say
  `<home>/projects/x/projects`) still builds, and io's anchor never binds there. The one holder is
  `scripts/offline_run.py::interrupted`, which builds Stores over a child leg's data root from the
  parent. Root: the parent reads a leg through the leg's own process, then `build_stores` matches the
  shapes. **Re-test:** `build_stores` on `<home>/projects/x/projects` raises.
- **io's path spelling is private** (tests-x's report): `stores.py::build_stores` imports
  `io.py::_plain`, the one spelling of a path for the anchor's compare. Root: io names it publicly.
  **Re-test:** `git grep -c "import.*_plain" -- promptpotter` is 0 outside io.

### wave-5 · rates (what rates-4 left at its landing)

- **A sweep's reap is observed by its count** (rates-4's report): `reaper.py::sweep_dead_cycles`
  returns a reaped count no production code reads, only tests (`tests/test_security.py`), so it keeps
  a `nonlocal` closure to hand `_recorded` its faults. Root: the facts read the reaped cycles'
  `stop_reason`, and the sweep returns its faults. **Re-test:** `git grep -c "nonlocal" --
  promptpotter/application/jobs/reaper.py` == 0.
- **A route outage is remembered per process** (rates-4's report): `pricing.py::_ROUTE_OUTAGE` is a
  process-global memo keyed by path, so only the process that met an outage holds back; another
  re-fetches within `_OUTAGE_MEMO_S`. Root: the outage is a fact of the route answer on disk (its
  `at` already dates the try), read by every process. **Re-test:** `git grep -c "_ROUTE_OUTAGE" --
  promptpotter` == 0.

### wave-5 · readings (what readings-4, readings-5 and readings-6 left at their landing)

- **A connector's two-valued term reaches no scorer** (readings-5's report): `authored.py::scorer_of`
  compiles from `CampaignConfig` alone, which names no connector, so a `CellTerm` or observation
  declaration cannot reach `matchers.py::fitness_scale_of`; harbor's `env_reward` is
  `float(reward)` of a panel's `reward_key`, two-valued only per task, so
  `max(0.0, min(1.0, env_reward))` still fits φ. Root: a connector declares the shape of its
  observation keys (per panel where it varies), and the scorer compiles with the campaign's
  connector. **Re-test:** the clamp under harbor reads two-valued in `Scorer.two_valued`.
- **A verify reads its comparison twice** (readings-5's report): `passes.py::verify_reading` computes
  the levels (`own_level`) and `_held` off the fitness column separately. Root: `VerifyReading`
  carries the difference band as its own `Estimate`, which `held` reads. **Held:** `VerifyReading`
  rides `PassGradedRecord` in the ledger (`extra="forbid"`), so a new field reshapes run data: it
  lands with the next wipe the operator calls. **Re-test:** one reader of the fitness column in
  `verify_reading`.
- **The paired SE floor is its own rule** (readings-6's report): `statistics.py::_normal_posterior`
  floors the SE at `1/(4n)` beside `bounded_mean_interval`'s support bound, the rule a constant
  graded side now reads in `difference_interval`. Folding it moves CAPO's paired t race (via
  `paired_mean_t`) and `read_arms`' SEs, so it needs an offline-run attribution of every moved pick
  first. **Held:** the support bound is an interval, not an SE, and over a paired difference's
  support [−1, 1] it is far wider than the floor (n = 20: all ties ±0.42 against ±0.026; all wins
  [0.64, 1] against [0.97, 1]), so the fold would cost the race most of its power: decide what a
  constant paired side should claim before cutting either. **Re-test:** one rule for a constant side
  in `statistics.py`, each moved pick named.

### wave-5 · l4 (what l4-measure, l1-cite and small-12b left at their landing)

- **The outer's edit menu offers fields of inner nodes that never fire**: inner `l3_plan` made 0
  calls and `l2_context` 1 across six inner runs (L4 audit C, 2026-11-15), yet both stay editable.
  Action: a field is offered only where its node ran on the measured samples, for every campaign.
  **Held:** who clocks an inner node. A node ran where it holds a `step_timings` key
  (`scoring/evaluators.py`), which core stamps with the terminal alone for an in-process run
  (`infrastructure/backend.py`); an inner node's calls sit on its run's ledger only
  (`runner/inner/transcript.py`). **Re-test:** a C0 on `promptpotter-self/run` offers no
  `l3_plan` field until one inner run calls it.
- **A nested spend book is wired beside the cell that nests it** (inner-1's report):
  `run_observers.py::RunObservers.arm_spend_book` reads none of `self`, so a fact repeats its
  `enclosed_as` line instead of calling it; `tests/factories.py::spend_book(enclosing=…)` nests a book a
  second way; `spend_book.py::_ENCLOSED = "backend"` is what a nested book falls back to where its
  spawner's pass names no kind; `RunScope.depth` / `InstrumentMode.depth` restate `instrument is not
  None` at `MAX_INSTRUMENT_DEPTH = 1`. Root: `enter_inner_cell` nests the book (enclosing book and
  kind together, every spawning pass naming its kind), and depth goes. **Held:** `_ENCLOSED` is also
  the hold tree's kind for a nested cell's hold with no resource (`SpendBook._filed`, `_out`), where
  an outer search's inner cell IS its backend: decide what names that kind before cutting it.
  **Re-test:** `factories.spend_book` takes no `enclosing`; `InstrumentMode` carries no `depth`; the
  nested-replay fact calls the production nesting.
- **A cycle's sidebar spend reads its ledger alone, so a cut send reads $0.000**:
  `hierarchy.py:302-308` builds the cycle row from `scan_ledger_spend` and passes `None` as
  unreported (`_spend_row("spend", …, None)`). A send a pause cuts is already filed at the cut as a
  `Lapse` (`spend_book.py::admitted`), but on the charge book, and for a nested run under the ROOT's
  ledger (`Admission`: `held_on((root.ledger or ledger).path)`), so neither the inner row nor the
  inner ledger can see it. `MeteredSpend` carries no `sends_unreported` (the paused-inner row's text
  says it does). Action: the cycle row reads its unreported off the hold tree
  (`Money.unreported_holds` under the cycle's jobs), not by ledger; and decide whether a cut send's
  late usage is collected at all (the cancelled request never returns). **Rides with:** hierarchy.py
  and the inner chain. **Re-test:** pause an outer mid inner-round (operator 2026-11-18: seat #001
  of `promptpotter-self.run__54ccef`); the inner row reads its cut sends as unreported, never
  `$0.000`, and the outer's mirror matches.
- **The wallet read under the admission gate grows with the account's charges** (gate-hold's
  report): `jobs/quota.py::read_account_wallet` folds `money_on(stores.charges.path)` and
  `campaign_spend` under the gate, admission's last scan that grows with history. Unmeasured.
  **Re-test:** hold time of `admit_and_share` at 10× today's charges file stays within 2× today's.
- **`content_filtered` is fatal on one sighting, though a filter trips on one row's text**
  (small-6's report): `domain/results_health.py::classify_result` keeps it on the fast path, against
  its own law (a fatal code is deterministic for the whole config). **Held:** a measured case.
  **Re-test:** an arm filtered on 1 of 18 rows is graded, never cut.
- **A campaign's cap count joins every cycle's bills to one cycle's other parts** (spend-served-fix's
  report): `served_dashboard.py::campaign_count` sets `metered_spend` (every cycle) beside this
  cycle's book's unreported, set-aside and calls out, so a sibling cycle's lapsed cells drop out of
  the campaign line the cap is said against. It cannot close in `campaign_count` alone (spend-4
  built it, then cut it): "this cycle" is that same count relabelled, `campaign.beside(cycle bills)`
  in `domain/spend.py::StopSpend.meter_lines` and `served_dashboard.py`'s metered line, so folding
  the siblings' holds there moves them into the cycle line. Action: `StopSpend` carries `cycle:
  SpendCount` beside `campaign` (a run-data wipe: the `RunPhaseRecord` stop shape),
  `campaign_count` folds every cycle's unreported, and `SpendCount.beside` goes. **Rides with:** a
  chain owning `domain/spend.py`, `run_observers.py`, `served_dashboard.py`. **Re-test:** a lapsed
  cell on a sibling cycle reads in the campaign cap line and not in this cycle's.
- **`l1_critique` on minimax runs its first cluster to `max_tokens`** (three sightings 2026-11-18:
  two dyck inner cells, `bbh-multistep-arithmetic-8b__908474` round 0, 26.7k chars, `finish=length`):
  the reply fails inside `failure_highlights.0` (`mechanism`, `claim` and `priority_fix` missing), so
  the run-on is one cluster's body, not the count `schemas.py::L1CritiqueOutput` caps at 3; the
  prompt's "put each id in the ONE cluster" (`pipeline.yaml`'s `l1_critique/1`) likely enumerates
  ids without end. `clean_reask` heals it at a full call's price. Action: attribute one banked
  payload by block, then a tighter prompt bounds the cluster body (CLAUDE.md: the prompt, not a cap).
  **Re-test:** the three banked inputs re-asked under the new prompt finish `stop`.
- **`l1_generate` re-proposes an arm the race already measured, and its re-race buys no evidence**
  (census 2026-11-19 over 113 inner cells): 94 arms raced in round r+1 on round r's identical rows,
  every row cached, 82 of them with a positive in-race lift. Potter keeps no survivors (a round's
  population is its parent), so each is `l1_generate` at temperature 0 re-emitting a measured arm
  verbatim (an id is its content) after a round that crowned nothing: a variant slot whose verdict
  cannot move. Case: `bbh-multistep-arithmetic-8b__2d64a7` round 3 proposal 0 is round 2's
  `e35a6671e62a0fb4`, +13 over its crown on 150 fixed rows, never crowned. **Held:** the fork, refuse
  a measured id at the child door (the slot goes to a new idea) or read a re-proposed arm on fresh
  rows (the re-proposal becomes evidence). Free first: replay that arm against its crown through
  `candidate_abilities` and `elect_round_winner` at 40, 80 and 120 rows, which prices the second.
  **Re-test:** no inner round races an arm on rows an earlier round already read it on.

## Held — no wave-4 chain yet

- **The estimator has no golden corpus**: its key (`nodes.py::BehaviourKeys.cell_estimator`) reads the
  L4 leg's trace on the offline world, not its outputs on fixed inputs. A corpus would read each inner cell's `CycleResult` (~255 KB each) and the
  tenant ruler's response rows: over 1 MB, and its only source is account data on the box, which the
  operator may wipe. **Held:** a corpus source the repo can commit under 1 MB. **Re-test:** the
  estimator's entry in the behaviour-key file names its key's inputs.
- **The inner measurand differs by benchmark**: justlogic seats inherit `justlogic/family.yaml`'s
  `per_cell` token discount while hotpotqa and legalbench score raw `em_match` / `label_match`, and
  hotpotqa's `escalation.l1_patience: 5` differs from the others; `runner/inner/tasks.py::_lever`
  leaves knobs out, so nothing flags it. Action: one inner measurand and one ladder across seats, or a
  refusal. **Held:** a decision. **Re-test:** the three definitions' `scoring` and `escalation` blocks.
- **Two builders of one sandbox store**: `infrastructure/store/stores.py::inner_sandbox_store` (the
  reader, from `(stores, hop)`) and `runner/inner/spawn.py::sandbox_stores` (the writer, from an
  `infrastructure/run_scope.py::InnerSpawnContext`, which carries the spawner's fields but not its
  `Stores`, so it cannot call the reader). Action: the spawn context carries the spawner's `Stores`,
  and the writer builds through the one builder, its existence checks off. **Held:** none claims it. **Re-test:** `git grep -n "def inner_sandbox_store\|def sandbox_stores" -- promptpotter`.
- **The outer prompt ranks on θ, not the scored term**: Shape: `potter/dispatch/injections/panels.py`'s
  "weakest first" and "worse than the origin beyond their own error bar" read `mean_round_delta`, which
  is in-sample and scores nothing, because `held_out_lift` carries no SE. Action: serve the held-out
  lift's SE (a binomial 20-row bench a pass) and rank and separate on it; θ stays a column.
  **Held:** a served SE. **Re-test:** `git grep -n "mean_round_delta" -- promptpotter/application/optimizers/potter/dispatch/injections/panels.py`.
- **"Which benchmark a row measures" is spelled twice**: Shape: `potter/members.py::_strata` reads
  `measured_dataset or ""` where every other reader takes `Sample.measured_dataset` raw (None = the
  filed one), and `BenchScope` partitions by benchmark under the word `RulerScope` owns. Action:
  `_strata` keys on the field raw; `BenchScope` takes a benchmark name. **Held:** l4-outer owns
  `potter/`, l4-runs `runner/bench.py`. **Re-test:** `git grep -n "measured_dataset or\|BenchScope" -- promptpotter`.
- **`floor_reason` and the bench read different rounds**: Shape: `domain/l4/proxies.py::floor_reason`
  reads the ended cycle's standing closes (`runner/inner/spawn.py::_run_inner_campaign`), while the
  bench and `n_rounds_after_origin` read the line (`runner/entry.py`), so after a fork "no proposal
  reached a cell" is judged without the rounds the rebase discarded. Action: the spawn reads the
  line's spent rounds (`LineSpan.own`). **Re-test:** a forked inner line whose pre-fork rounds all spoiled.
- **The abort knob and the floor disagree**: Shape: `potter/knobs.py`'s `terminate_capability` names
  an unrecoverable upstream fault as an abort's intended use, while `domain/l4/proxies.py::floor_reason`
  floors every `OPTIMIZER_ABORT` as prompt-owned, one after a real held-out gain included. Action: an
  abort carries who owns it, and only a prompt-owned one floors. **Held:** a decision. **Re-test:** an
  inner run aborted on a backend fault reads `floored` 1.
- **An archived L4 outer cell carries no `floored`**: rows banked before `floored` became a row term
  leave the `per_sample` of `research/datasets/promptpotter-self/family.yaml` unscored; no reader reads both
  shapes. Action: none past the landing that ships it, which re-measures C0. **Held:** that landing.
  **Re-test:** after it, `promptpotter-self` scores every outer cell it reads.
- **`locus_crossover` re-crosses a measured or hosted child**: it edits every two-parent proposal, so
  a hosted recombination and a rejoined crossover child are crossed again (one call: the re-crossed
  id differs; l4-outer-5's report). Action: a crossover applies to a proposal it drew, never one it
  receives. **Rides with:** the next variation chain. **Re-test:** a hosted recombination keeps its id.
- **An over-budget crossover child books its verdict under axis `variant`**: `bench/children.py`'s
  one length check cannot tell which mutation minted the child. Action: the verdict names the
  mutation kind. **Rides with:** the row above. **Re-test:** a crossover child's OVER_CEILING names
  crossover.
- **`l2_context`'s subject is discretionary, so its share is sized against a growing prompt**: L1's
  subject is mandatory, L2's `rendered_prompt` rides the share (`family.yaml` sizes it at 21,000 for
  a subject that grows `length_budget` a round). Action: decide whether the subject joins L2's
  mandatory set. **Rides with:** the next l4-outer link. **Re-test:** L2's subject survives any share.
- **A member's treatment digest re-keys on a rename**: it hashes the member's source files, so a
  refactor that moves no decision (l4-outer-6's `Frontier` → `Parent`) re-keys every banked L4 cell
  for potter, capo-pobb and gepa. Action: the digest hashes what the member decides, not its text.
  **Re-test:** a pure rename in `potter/members.py` leaves `--digests` unmoved.
- **A machine's cell ceiling is counted twice**: `infrastructure/backend.py::MachineSlots` (a
  file-lock pool across processes, for `cells_hold_the_machine` alone) beside flight-share's
  in-process count per backend. Action: one count of one machine fact, reaching both. **Rides
  with:** the rows above. **Re-test:** `git grep -c "class MachineSlots" -- promptpotter` is 0.
