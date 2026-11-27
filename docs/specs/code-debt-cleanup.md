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

- **A fork is not opened under a carried admission**: `jobs/launcher/launch.py::hold` opens every held
  launch's cycle, and the slotless doors (`embedded_run.py::run_campaign`,
  `runner/inner/spawn.py::_open_seat`) admit through `admission.py::admit_slotless` before they mint
  and run through `run_job.py::run_held_job`, but mint or resume beside it. Action: a fork is opened
  under a carried admission, and the slotless doors open through the request kind's one `open`.
  **Rides with:** admit-2. **Re-test:** `git grep -n "mint(" -- promptpotter/application/runner/inner/spawn.py promptpotter/application/embedded_run.py` is empty.
- **A start's release is still hand-rolled at each start site**: `admission.py::RunStart.release` is the
  one release, but 8 `except BaseException: … release(exc)` blocks across `jobs/launcher/` and
  `commands/launching.py`, and `embedded_run.py`'s `to_thread(start.release, exc`, call it by hand, and `jobs/launcher/head_to_head.py::AdmittedHeadToHead.launched`
  and `embedded_run.py::run_head_to_head` each release the arms not yet run after a failure (no arm set
  owns its starts). Action: the one `open_cycle` lifecycle owns the start's release; a context manager is
  the patch, not the root. **Rides with:** admit-2. **Re-test:** `git grep -c "release(exc" -- promptpotter/application`
  falls from 8.
- **A fork's cut lands before its admission**: `commands/launching.py` cuts the child
  (`cut_operator_fork`) before `_launch_fork` admits on the request's own hop, so a refusal leaves the
  parent's inert `FORK_CUT` line, because `jobs/quota.py::_new_campaign` counts an unresolved hop as a
  new campaign. Action: the daily count reads the request's campaign; the fork is admitted on the
  parent's hop, unresolved, and lands on the child. **Rides with:** admit-2. **Re-test:**
  `git grep -n "cut_operator_fork(\|admit(" -- promptpotter/application/commands/launching.py` lists the
  admission before the cut. Measured at +60 in `promptpotter` as its own door (`wip/valid-2` d75eb7e is
  the reference build), so it rides admit-2's one admission door.
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
  `admission.py::admit_slotless` and mints itself, because a DSPy `program` is an in-memory object no
  payload carries. Action: `run_campaign` files `mint-campaign` or `start-run` with an `EmbeddedRun`
  holder, the program held by the filing process. **Rides with:** admit-2. **Re-test:** an embedded
  `run_campaign` leaves a `CommandRecord` in the commands log (today `commands/dispatcher.py` builds the
  only one, and `run_campaign` and `run_head_to_head` file none).
- **A queued launch dies with the asking process**: the queue wait runs in the caller
  (`jobs/launcher/launch.py`). Action (the operator, 2026-11-11): a queued launch is a job on disk; the
  job file holds the principal's id, never a credential, and `jobs/registry.py::claim_next`'s promoter
  re-checks admission and spawns it when the slot frees; `application/CLAUDE.md` § Run admission's rule ("a queued job's intent
  never leaves the process that authenticated it") is restated in the same link. **Rides with:**
  admit-2. **Re-test:** `resume --step-rounds 1` behind a running cycle; it starts when the slot frees.
- **Three launch faults from a code review (reported; verify each at its line first)**: `jobs/launcher/head_to_head.py:178`
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
  `git grep -n "class EscalationLayer" -- promptpotter` is empty.
- **A locked-in leader's caught-up cells reach the scoreboard only at the round's close**:
  `runner/round.py::_walk_population` merges `CatchUpPool.caught` after `run_walks`, and a catch-up
  walk records no pass (`BACKFILL` is no arm role). Action: a caught-up cell is a recorded row of its
  prior's arm (its walk continues the prior's pass, `held` = the prior's rows). **Rides with:** rk-opt's
  next chain. **Re-test:** a CAPO race settled `locked_in`: the leader's last `running` before the close
  counts the cells `rows(leader)` holds.
- **Overlap and repair keep their own pass families**: `RoundCells.overlap` and `RoundRestatedRecord`
  carry their passes beside `VerificationPass`. Action: both ride the one record. **Held (design
  fork):** (a) each overlap top-up is a `VerificationPass` of a new `overlap` kind on the round, and
  `RoundResult.overlap_results` derives from them, while `RoundOutcome.overlap` (the `OverlapReading`,
  17 files) stays; or (b) the reading and its cells are one origin-panel record the round and its
  restatement carry, so `.overlap` goes whole. Both reshape `RoundStatement` (a wipe). **Re-test:**
  `git grep -n "\.overlap\b" -- promptpotter` is empty.

### batch/26 · stream-rk

- **`ExportProjection` renders off a live `Session`**: `runner/output.py` reaches `session.state.scoring`
  and its partition's `demo`. Action: it takes the scorer and partition off the ledger
  (`PartitionRecord`, the declared formula), as `closed_rounds.py::campaign_scorer` does. **Held:**
  the row below; until it lands, `campaign_scorer` grades a labelless cell unlike the run, and the
  export would take that. **Re-test:** `git grep -n "session.state" -- promptpotter/application/runner/output.py` is empty.
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

- **The lineage subtree sorts every candidate per candidate**: `application/hierarchy_subtree.py::earlier_measured`,
  O(n² log n) per read; reported beside it, the subtree read builds the whole top family per request,
  and `_descent` compares O(n²) pairs with `SequenceMatcher` behind an lru of 4096 that thrashes from
  ~90 candidates (`hierarchy_subtree.py:242-318`). Action: "closer to" and descent are folded once per
  candidate on the ledger: a record kind. **Rides with:** a lineage-fold chain of its own. **Re-test:** `git grep -n "def earlier_measured" -- promptpotter/application/hierarchy_subtree.py` is empty.
- **The feed's version spans a whole store**: `GET /feed`'s tick (`application/cycle_reads.py::feed_frames`) runs
  `application/hierarchy.py::entries_version` over every store, per connected tab, and a campaign
  subtree's version (`hierarchy_subtree.py`) covers its whole store, so any run's append re-asks every
  mounted tree. Action: one version per subject, kept by the append door and the sweep, so no tick walks
  a store; every writer stamps it (the ledger ingress, drafts, archive, ruler, commands,
  definitions). With it, a command's ack names only what its write moved, where it now walks every
  subject (`commands.py`'s stamp calls `feed_versions_said` with no subject named), so under a live
  run every press waits on a re-read of every store read; and `reads.ts::draftRead` follows its own
  draft, where it follows bare `store:`, so an inner sandbox's append re-asks it. **Rides with:** a
  version chain of its own. **Re-test:** `git grep -n "entries_version(" -- promptpotter/application/cycle_reads.py`
  is empty; an `edit-draft-campaign` ack's `X-Feed-Versions` names `account` alone; `draftRead`'s
  subject is not `store:`.
  Beside it (a hot-path pass, ✓ unless reported): `read_model.py:246`'s `LEDGER_INDEX_MAX` = 256, so past
  256 cycles (one 20-round L4 campaign is ~240 inner campaigns) the feed's `cycle_reads.py::feed_versions` walk misses
  every tick and each miss re-folds with `model_validate_json` per line; reported, every `store:`-subject
  read re-runs `feed_versions` over all stores (`presentation/api/routers/reads.py::_endpoint`), `read-cache` re-asks ~9 reads per
  sandbox frame, the machine's version stats every job file and reads the live index per tick
  (`machine_status.py::machine_version`), `entries_version` parses every
  manifest, and archive-wide signatures serve as read versions (`evidence/read.py:551-554`,
  `measurement_log.py:271-280`), so any run's archive write invalidates every evidence and cells view.
  Seen at feed-version's landing: a held claim versions its cycle by the clock
  (`infrastructure/run_state.py:201`, `time.time_ns()`: "it never 304s"), so under a claim every tick
  moves `store:` and an idle walk re-asks ~6 reads per ~7 s (the producer's end is a write its owner
  stamps); and `cycle_reads.py::_COUNTING` is one lock over every account's walk.
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
  patch through `layer_fold.py::fold_layers` (a null clears), but L2's steer is read through `L2Memory.steered` /
  `steered_layout` beside the fold, not as a named layer of `l1_generate`'s config, and about 58 of
  the engine's `dict[str, Any]` sites are node-config bags (`dict[str, NodeCall]` on `CycleStart`,
  `dict[str, dict[str, Any]]` on `ForkStart`, which carries a null clear and text a `NodeCall` refuses
  before the schema types it), and schema, parsing, overlay and knobs each hold a view of the document
  (~11 modules). Action: one typed node document with knobs a view on it; the steer a named layer. It
  closes the pipeline-switch row. **Rides with:** cfg.

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
  handed its hop's chain, `CycleIndexProjection` keyed by hop. Built and reverted by store-5a:
  `fold_cycle_index(hop, chain)` with `CampaignStore.load` = it over `self.chain(hop, moment=…)`,
  blocked only by `scripts/offline_run.py`'s two `read_cycle_index(<dir>)` calls. **Rides with:**
  the next chain owning `scripts/offline_run.py`.
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

- **The trace hashes a field's spelling** (rec's landing, reported; bkeys-3 took `Estimate`'s words;
  `MemberAddress.pass_role` is no display field: `paired.py::read_pair`'s address equality reads it,
  and capo-pobb's `same_individual` moved without it): `offline_run.py::behaviour_keys` hashes the
  trace with its keys, and the trace keeps whole
  sub-documents (`ELECTION_KEYS`' `fit`, `LEDGER_KEYS`' `election.fit`, `run.bench`, `round_index`),
  so ren's renames re-keyed `behaviour_keys.json` by field names alone (decisions identical once
  normalized). Root: the trace names each decision it reads, so a model rename moves only its pick.
  **Rides with:** bkeys-2. **Re-test:** renaming a field the trace reads leaves `behaviour_keys.json`
  unchanged.

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
  `p_unread_fields` ceiling). Action: each goes, or names its reader. **Re-test:** the ceiling is 0.
- **`SampleScoredRecord.running` stamps a running fold on every cell** (9.3 % of a 1.5 MB ledger). Its
  readers (`Narration.fold`, `StandingArm.fold`, the live board's rows) need the scorer's evaluators,
  which no domain fold holds. Action: the readers fold `StandingArm.cells` through
  `metrics.py::compute_composite_fitness` in `application/`, and the field goes (run data wiped).

### webd (the edge tail, after lw)

- **Adapters over a flat served shape** (webb-5's): `useCandidatesModel.ts` joins about twelve sources;
  `.facts.` is opened at many sites; liveness is re-derived at each `phaseWalks(` site. Action: the
  served rows carry what the adapters join. **Rides with:** webd. **Re-test:**
  `git grep -c "\.facts\." -- webapp/components webapp/lib`.
- **The browser recomputes engine facts**: `webapp/lib/derivations/` (~2.2k lines, plus tests) derives
  node config, spend, run phase and the search point the engine already computes. Action: each fact is
  served on its existing read and the TypeScript copy and its tests go; so do `wonOnTheta` (9 hits in 5 files; its filed row and `electedMetric` went, ec96b1cda) and
  `ConfigMode` (`lib/derivations/node-config.ts:11`; no row files it). So does `run-phase.ts:51`'s copy
  of a cycle's acts, which drops only `start-run` where `phases.py:447` drops every act of a refused
  admission (narr-2's review): the acts are served once faults 1 serves `next_acts`. **Rides with:** webd. **Re-test:**
  `git ls-files webapp/lib/derivations | grep -v __tests__ | xargs wc -l` falls by the served facts.
- **A hand-kept mirror of the served shapes**: `webapp/lib/test-fixtures.ts` (666 lines, imported by 23
  webapp files) hand-writes default builders for ~30 Python-served types (`configParam`,
  `pairedReading`, `armReading`, `runStanding`, `dash`, `liveRow`, `roundDoc`, …). Since 2026-08-15 it
  changed with `infrastructure/projections/live_dashboard.py` 53 times and `domain/dashboard_rows.py` 38 (`view.py` went,
  14ee22b61): the repo's most frequent cross-language ripple. Action: the fixtures
  are generated from the Python side as `scripts/build_ts_types.py` generates `types.generated.ts`,
  their values read off the offline world's served run (`scripts/offline_world.py`), drift-checked by
  the gate; the hand builders go. `tests/factories.py` (1,571 lines), its Python twin, co-changed with
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
- **An inner campaign serves a Delete no door presses** (webd-3's report): `domain/hierarchy.py::SubtreeCampaign`
  subclasses `HierarchyCampaign`, so an inner campaign in the deep read inherits its
  `delete: DeleteAct` (`delete-campaign`), which nothing may file for it. Root: the subtree entry is
  not a listed entry; it shares a base, not the listed one's acts. **Re-test:** the deep read's
  inner campaign serves no `delete`.
- **A round's own arms are spelled beside `MeasurementRole.is_proposal`** (live-3f's report):
  `scoring/passes.py:268`, `domain/arms.py:139`, `domain/run_standing.py:444` and
  `store/lineage_queries.py:184` still write `role != MeasurementRole.PARENT`, and
  `resume_and_fork/fork_siblings.py:123` `is not`. Action: each asks `is_proposal`. **Re-test:**
  `git grep -lE "(!=|is not) MeasurementRole.PARENT" -- promptpotter` names `shared/measurement_context.py` alone.

### batch/28 · lb

- **Wave B — `application/runner/` is 10 modules, not 8.** lb collapsed it by phase (21 modules to
  10) and deleted the ledger-scan forwarders. `inner/narrative.py` has
  since gone and the count is still 10; `termination.py` is the row below.
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

### batch/29 · mcp

- **The MCP door's claude.ai connector is unproven on the box**: the door serves Streamable HTTP at
  `/mcp/` on the account's bearer (`promptpotter/presentation/mcp/server.py::HttpDoor`). Action: the
  operator's step after the box update, a connector with the token as its `Authorization` request
  header. **Rides with:** the next box update. **Re-test:** in claude.ai, a custom connector at the
  box's MCP URL answers `tools/list`.

### batch/29 · tv-2 (what tv-2 left at its landing)

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
  `cli/commands/reads.py::_human`, and `potter/dispatch/injections/panels.py:1051-1052`) beside `domain/spend.py::usd_text`, which prints `$0.0000`
  for a cheap model; `usd_text` and webapp `fmtUsd` are one rule; `run_standing.py` spells a third.
  Open (operator): the rule (significant digits under a dollar is one candidate). **Rides with:** forks 1.
  **Re-test:** `git grep -c -E '\$\{[^}]*:[,]?\.[0-9]f\}' -- promptpotter`.
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
  the `spend by node:` block at each twin's `readout.log` stop (`render/ansi.py::render_spend_breakdown`).
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
- **Run data is stat'ed bare outside `store/io.py`**: five
  `.stat()` sites (`optimizer_manifest.py:91`, `event_stream.py:53`,
  `run_state.py:74`, `measurement_archive.py:475`, `private_root.py:32`) and `measurement_archive.py::_named`'s `os.scandir` read a long path bare, so a ledger
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

### wave-5 · store (what store-2f, store-2g and store-3b left at their landing)

- **A launch's steer verdict reads the root's narrowing**: `commands/launching.py` reads a launch's
  node rows at its hop ("a row's menu is its cycle's own declaration") but judges its steer with
  `permitted_narrowing(stores, of)`, the campaign root's, where a fork's start judges against its
  parent cycle's (`fork_siblings.py::operator_start`). A steer outside a forked line's narrowing and
  inside the root's reads `clean`. Action: decide which narrowing a launch at a hop answers to (the
  babysit act, decisions.md § 0005) and read it there. **Re-test:** a launch at a fork whose line
  narrowed a model steers to the root-only model and reads `babysat`.


### wave-5 · readings (what readings-4, readings-5 and readings-6 left at their landing)

- **A connector's two-valued term reaches no scorer** (readings-5's report): `authored.py::scorer_of`
  compiles from `CampaignConfig` alone, which names no connector, so a `CellTerm` or observation
  declaration cannot reach `matchers.py::fitness_scale_of`; harbor's `env_reward` is
  `float(reward)` of a panel's `reward_key`, two-valued only per task, so
  `max(0.0, min(1.0, env_reward))` still fits φ. Root: a connector declares the shape of its
  observation keys (per panel where it varies), and the scorer compiles with the campaign's
  connector. **Re-test:** the clamp under harbor reads two-valued in `Scorer.two_valued`.
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
  None` (L4 is one level deep, `docs/concepts/l4.md` § Depth). Root: `enter_inner_cell` nests the book (enclosing book and
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
- **`l1_critique` on minimax runs a cluster's id list to `max_tokens`** (three sightings 2026-11-18,
  `finish=length`, healed by `clean_reask` at a full call's price): ANY cluster's `sample_ids`
  repeats ids without end (outer `908474` round 1 cycles 12 ids in cluster 0; inner `1544dc`'s
  cluster 2 re-lists cluster 0's 15). potter-w1 closed both prompt facts feeding it: a cluster now
  names `mechanism` and `claim` before `sample_ids` in the layout and `schemas.py::FailureCluster`'s
  order, and each id appears once in the whole reply. Open: the paid proof. **Re-test:** the banked
  inputs (`.scratch/wave-2/bank/row111/`) re-asked under `l1_critique/1` as it now renders finish `stop`.

### wave-5 · webapp (what webapp-1 left)

- **An open panel lags its trigger by a frame**: `ui/overlay.ts::useAnchored` re-places a popover
  from `requestAnimationFrame`, and follows a trigger scrolled out of its pane over the masthead;
  `e2e/harness.ts::everyTapLands` re-reads a cover two frames on for it. Root: CSS anchor
  positioning, no JS follow. **Held:** iOS before 26 lacks it. **Re-test:**
  `git grep -c requestAnimationFrame -- webapp/components/ui/overlay.ts webapp/e2e/harness.ts` is 0.
- **A Candidates card draws an empty chart over a served lineage**: campaign
  `bbh-multistep-arithmetic-8b__244ba5` (history 2, rounds 2) renders `.cand-card`'s canvas blank
  with no console error, while its hierarchy and dashboard reads serve rows. A state the card enters
  but does not report; cause not traced. **Re-test:** the card for a campaign with a closed round
  draws at least one bar or point, or says why it cannot.

### wave-5 · drift (the 2026-11-19 backbone hunt)

Each row names the chain it rides with.

- **A decision record is named three ways**: `ResumeCheckpointRecord`, its ledger kind `"decision"`,
  and an `inputs_ref` holding the inputs whole (since potter-w1 a decision's inputs are addresses,
  `race.py::EliminationCut.cells`). Root: one name for the record and its field, held in
  `domain/records/run_records.py`, `mask/`, `store/campaign_store/ledger_scan.py` and
  `bench/cycle.py`; a run-data wipe. **Rides with:** root C2 (wave-6). **Re-test:** `git grep -c ResumeCheckpointRecord -- promptpotter` is 0.
- **The neutral round envelope stores one backend's ranking report and a second copy of its cells**:
  `domain/results.py::RoundOutcome.diagnostics` is a `round_diagnostics.py::RoundDiagnostics`
  (`rank_buckets`, `top_k_accuracy`, `near_misses`, per-sample `rank` / `gt_in_*`) that
  `bench/round_analysis.py` writes and `bench/node_context.py:319` copies across a restatement for one
  reader, `potter/dispatch/facade.py:304` → `injections/panels.py:147`. On the desk's 40 `round_closed`
  records the first three are empty and `samples` repeats the head arm's 2,131 cells (483 KB); it is
  inside `round_document_digest`, so a diagnostic edit moves `consumed`. Root: a report derived from
  cells is computed by the optimizer that reads it; potter folds it at render, its text byte-identical.
  **Rides with:** rec-2, after potter-w1 (it edits `optimizers/potter/`). **Re-test:** `git grep -n "RoundDiagnostics" -- promptpotter/domain/results.py` is empty.
- **A job is a survivor that carries how its run ended**: `store/data_root.py::INSTALL_KINDS` keeps
  `Kind("job", …, _SURVIVOR, …)` for the daily quota, but `domain/job.py::Job` holds `ending:
  StopReason | None`, `refusal: Refusal | None` (a served message, `details`, `next_acts`), `lapse`,
  `stepped` and a five-arm `holder`, so a `StopReason`, `Refusal`, `NextAct` or holder rename is a
  survivor migration or a refused landing (two of the five migrations are jobs': `job_endings.py`,
  `live_jobs_index.py`; 5cf502c7b dropped `converged` with none, found at the v0.8.21 cut); `ending` and `refusal`'s readers (`admission.py:144`,
  `machine_status.py:297-301`) read a released job. Root: a survivor's model imports no run-data enum
  and no served-words model; the ending, refusal and lapse go to run data, in a one-shot migration
  (decisions.md § 0011). **Rides with:** surv, before "A queued launch dies with the asking process". **Re-test:** the model `Kind("job", …)` loads holds no `StopReason`, `Refusal` or `JobLapse` field.
- **An invalid arm is recorded twice**: `domain/arms.py::Invalid`, the `Cut` reason
  `scoring/arm_states.py::cut_invalid` mints, re-states what `_Arm.invalid` derives from the arm's own
  fatal `validation_failures`, and readers split between them (`arm_end`, `walked_short`,
  `arm_reading.py::ArmReading`'s `end` read the reason; the hierarchy, measurement log, folds, mask,
  transcript and L4 proxies read `.invalid`). They agree on all 42 invalid arms of 1,381 concluded
  in a 21-campaign home (2026-11-25), held only because `cut_invalid` is the one minter and the
  fatal roster has not moved. Root: the reason goes, and `arm_end` reads `.invalid`; a
  `CutKind` and on-disk reshape, so a run-data wipe. **Rides with:** root C2 of wave-6 below. **Re-test:**
  `git grep -n "class Invalid" -- promptpotter/domain/arms.py` is empty.

### wave-6 · arch review 2 (2026-11-25)

Order. A landing may wipe run data, so none is ordered by a wipe; each root names its reshape.
1. Land what is in flight: l4-key-b → l4-key-c; spend-tomb; ml-6 → ml-7; web-tail → web-land.
2. A, the run opening, after l4-key-b, spend-tomb and admit-3. Its rows `runner/mint.py` and `jobs/quota.py` ride it.
3. B, the resolved run: its core is free now and parallel with A; the check-in link goes after A. Decide G first.
4. D, versions, after spend-tomb; parallel with A and B.
5. C1 → C2 → the nested send, one chain over `domain/records/run_records.py`, after l4-key-b, spend-tomb and the measuring loop; C1 after ml-7; the scanner fold after the loop.
6. F, the test world, after A; parallel with the rest.
7. E, the browser, after D and spend-tomb; a link per served fact.
8. Every other row stays under its own chain.

Each root closes the rows it names by title; it does not restate them, and each of those rows goes when
its own re-test passes. Counts are `git grep -cE` hits/files over `promptpotter/` unless a path is named.

- **One `Opening` per run: admit, claim, mint or open, observers, book, entered once and released by the object (root A)**:
  `jobs/launcher/launch.py:30` `LaunchRequest = FreshMint | ExistingCycle | CheckinStart` repeats eight
  members (`at`, `step_rounds`, `holder`, `connector`, `endpoint`, `declared_budget`, `sends`, `open`)
  in three classes (`mint_and_start.py:58,117`, `checkin.py:52`) with no declared protocol, and the
  embedded and inner doors stand outside it. The opening is spelled across `admission.py::RunStart`,
  `jobs/launcher/launch.py::hold`, `runner/mint.py::open_launch` and `open_cycle`, `run_observers.py::build_run_observers`,
  `runner/entry.py::launch_scope`, `embedded_run.py::enter` and `runner/inner/spawn.py::_open_seat`;
  `release(exc` stands 8/3; `run_observers.py:154` appends `RunPhaseRecord(Running)`, voids the inbox,
  saves the active pointer and closes a dead bench, so the run boundary sits in an observer builder.
  Root: a request kind states only its data; embedded, inner and fork are request kinds, not side doors.
  **Closes:** **A start's release is still hand-rolled at each start site**, **A fork's cut lands before its admission**,
  **A mint takes no claim until `admission.py::claim_cycle`**, **The embedded launch is not a command**,
  **Three launch faults from a code review**, **A fork's share lands at its landing**, **A fork is not opened under a carried admission**;
  adjacent: **A queued launch dies with the asking process**, **A job is a survivor that carries how its run ended**.
  **Run data:** none. **Size:** large, about 10 files, a chain of 5–6 links. **Rides with:** admit-3.
  **Re-test:** `git grep -c "release(exc" -- promptpotter/application` is 0.
- **`resolved(subject)` returns one run's schema, params, config, optimizer selection and scorer, built from records (root B)**:
  two doors fold a cycle's pipeline: `pipeline_resolve.py:750 _campaign_merge` (the read, off records)
  and `:1183 configure_and_apply_pipeline` (the run, off a live `Session`) each call
  `_resolve_active_schema` (5/1) and build `ParamLayers(` (3/1) with the same six arguments, and the
  run's side branches on `if session.cycle_id:` (`:1196`) to pick recorded or fresh layers. The scorer is
  compiled at 10 `scorer_of(` call sites in 7 files; 9 pass `verifier_graded=False` by hand (9/6) and
  only `initialization/scoring_scope.py:58` passes the bank's answer. `run_observers.py::_bind_observers`
  builds three projections on a live `Session`, and `runner/output.py` reads `session.` 23 times.
  Root: a `Session` binds the value to a live client and builds none of it. **Closes:**
  **`ExportProjection` renders off a live `Session`**, **`campaign_scorer` grades a labelless cell unlike the run**,
  **A check-in's cycle plan selects beside its landing's refusal**, **A per-cell criterion naming a term no cell carries compiles**,
  **A connector's two-valued term reaches no scorer**; adjacent: **The node document is typed two ways**,
  **The pipeline carrier is typed only at its door**, **A connector's name is looked up again**,
  **A launch's steer verdict reads the root's narrowing**, the reader half of **`SampleScoredRecord.running`**.
  **Run data:** `verifier_graded` and the scorer's owners become a recorded fact of the mint if they are not
  one already; the compiler is a hashed unit, so digests move. **Size:** large: `pipeline_resolve.py`,
  `datasets/authored.py`, `scoring/closed_rounds.py`, `initialization/**`, six scorer callers.
  **Rides with:** a chain after ml-7 for `optimizer_manifest.py` and `config_map.py`.
  **Re-test:** `git grep -c "verifier_graded=False" -- promptpotter` is 0.
- **A writer stamps one version per subject (root D)**: 11 `version` functions in 10 files
  (`campaign_store/store.py:243`, `draft_store.py:59`, `measurement_archive.py:493`, `ruler_store.py:42`,
  `tenant_dataset_store.py:136`, `dataset_access.py:397`, `machine_status.py:249`, `hierarchy.py:554,585`,
  `hierarchy_subtree.py:750`, `measurement_log.py:259`) and `file_sig(` 34/15, each re-deriving a
  subject's change from the files it reads. **Closes:** **The feed's version spans a whole store**,
  **The account feed never re-asks an exhibit's reads when the host's store moves**; adjacent:
  **The live reads re-serve history per poll**, **The lineage subtree sorts every candidate per candidate**,
  the ETag half of **A follower reads with the host's whole `Stores`**. **Run data:** none. **Size:** medium.
  **Rides with:** a chain after spend-tomb. **Re-test:** `git grep -c "file_sig(" -- promptpotter` falls from 34.
- **Every pass is a recorded pass (root C1)**: `domain/pass_kinds.py::PASSES` leaves `recorded` false on
  `BACKFILL`, `REPAIR`, `OVERLAP` and `NOISE_FLOOR`, whose cells reach the ledger not one by one;
  overlap and repair keep records of their own (`.overlap` 27/15, `RoundRestatedRecord` 19/6,
  `CatchUpPool` 7/3), and `MeasurementRole.PARENT` is compared 18/11 beside `is_proposal` 5/5.
  Root: a pass that measures is a record. **Closes:** **Overlap and repair keep their own pass families**,
  **A locked-in leader's caught-up cells reach the scoreboard only at the round's close**,
  **Two precedence rules join an arm's rows**, **A round's own arms are spelled beside `MeasurementRole.is_proposal`**.
  **Run data:** `RoundStatement` reshapes. **Size:** medium; it edits `runner/round.py`. **Rides with:** a chain after ml-7.
  **Re-test:** `PASSES` lists no pass with `recorded` false.
- **A record holds no value another record derives (root C2)**: `domain/arms.py::Invalid` beside `.invalid`
  (12/9), `RoundDiagnostics` (8/4) on the envelope, `.running` (31/11) stamped on every cell.
  **Closes:** **An invalid arm is recorded twice**, **The neutral round envelope stores one backend's ranking report and a second copy of its cells**,
  **`SampleScoredRecord.running` stamps a running fold on every cell** (its field; B gives the readers the scorer);
  adjacent: **`PairedReading` is a tagged union**, **Fields written, never read: 27 left**, and the decision record's three names
  (**A decision record is named three ways**). **Run data:** every link; behaviour keys move.
  **Size:** medium. **Rides with:** after C1. **Re-test:** `git grep -n "class Invalid" -- promptpotter/domain/arms.py` is empty.
- **The served contract is generated and the browser derives nothing (root E)**: `webapp/lib/derivations`
  holds 44 files and 3,827 lines, about 1,780 of tests; `webapp/lib/test-fixtures.ts` 666 lines;
  `.facts.` 121/47 over `webapp/`. **Closes:** **The browser inks a served state from its own map**,
  **Adapters over a flat served shape**, **The browser recomputes engine facts**,
  **A hand-kept mirror of the served shapes**, **Served fields the browser never reads**. About 4,500
  TypeScript lines go. **Run data:** none. **Size:** large, a link per served fact. **Rides with:** webd.
  **Re-test:** `git ls-files webapp/lib/derivations | grep -v __tests__ | xargs wc -l` falls by the served facts.
- **One test world (root F)**: `seat(` 36/5, `_peer_cycle(` 16/1, `close_round(` 17/4, `loop_session(` 25/7
  and `minted_campaign(` 82/6 over `tests/`; `tests/factories.py` holds 57 builders. **Closes:**
  **`seat`, `_peer_cycle` and `close_round` replay production by hand**, **`_peer_cycle` seats an origin reading the archive does not hold**,
  **A test seat binds a campaign disk does not hold**, **Smells the test-shape split left**, **A test stands outside the harness**.
  **Run data:** none. **Size:** large, tests only; cheaper after A, the harness then opens through the one opening.
  **Rides with:** tests-w's successor. **Re-test:** `git grep -c "def _peer_cycle" -- tests` is 0.
- **A nested run's send is filed once, on the run that sent it**: `domain/records/run_records.py:572`
  `mirrored: bool = False`, `infrastructure/llm/spend_book.py:751` writes `mirrored=self._book.nested`,
  `infrastructure/store/read_model.py:1132-1136` branches on it, and `store/CLAUDE.md` warns that money
  "banks twice" where a reader forgets (`mirrored` 14/6). Root: the outer run's cost is a fold over its
  inner ledgers. Under **A nested spend book is wired beside the cell that nests it** and
  **A cycle's sidebar spend reads its ledger alone, so a cut send reads $0.000**. Reshapes run data.
  **Rides with:** root C2's chain. **Re-test:** `git grep -c "mirrored" -- promptpotter` is 0.
- **A line's fold is stated once and read by one keyed read**: `infrastructure/store/campaign_store/ledger_scan.py`
  holds 21 `scan_` functions; `for span in spans` is spelled 16/6; `scan_cell_formula` (`:827`) hand-merges the
  fold `scan_cycle_facts` (`:823`) reads on one ledger. Which ledgers answer for a hop (its cycle, its line,
  its campaign) is decided at each reader. Root: one read `(hop, scope, fold)`, the fold stating how its
  values compose. Sits under **The cycle index reads its chain beside the store's**, the launch-steer row
  and the sidebar-spend row. **Rides with:** after the measuring loop. **Re-test:**
  `git grep -c "for span in spans" -- promptpotter` falls from 16.
- **`jobs/quota.py` holds counts and rates, and the money shares sit beside the spend book**: `quota.py`
  is 1,015 lines; `:608` onward holds `share_campaign_ceiling`, `cycle_share`, `move_campaign_money`,
  `change_budget`, `hold_run` and `release_run`; 16 files import it, `pipeline_resolve.py` among them,
  the cycle **`runner/termination.py` is a phase module that `jobs/` reads** names. Root: shares and holds
  move to the spend book's side, and `quota` keeps counts and rates. **Rides with:** root A.
  **Re-test:** `git grep -c "^def " -- promptpotter/application/jobs/quota.py` falls by the six.
- **`runner/mint.py` opens a run and mints nothing**: it holds `open_launch`, `open_cycle`, `close_init`
  and `onto_fork`; the mint is `jobs/mint.py`. Root: the opening of root A takes the four. **Rides with:** root A.
  **Re-test:** `git ls-files promptpotter/application/runner/mint.py` is empty.
- **`store/read_model.py` holds the money fold inside the ledger index**: `:906-1241` (`Spent`, `Money`,
  `_Money`, `money_on`, `charge_book_fold`), imported by 54 files. Root: the money fold is a module of
  its own beside the spend book. **Rides with:** root C2's chain. **Re-test:**
  `git grep -n "^class Spent\|^def money_on" -- promptpotter/infrastructure/store/read_model.py` is empty.
- **A protocol with one implementation**: `read_model.py:409 Projected` exists so the index need not import
  `infrastructure/projections/base.py::Projection`, its only implementer. Root: the index takes `Projection`
  or the protocol moves to `base.py`. **Rides with:** the money-fold row above.
  **Re-test:** `git grep -n "class Projected" -- promptpotter` is empty.
- **Names collide across layers**: two modules each named `mint.py`, `head_to_head.py`, `hierarchy.py`,
  `bench.py`, `power.py`, `wounds.py` and `cells.py`; `ledger_scan.py:176 CycleFacts` beside
  `evidence/read.py:362 _CycleFacts`. Root: a name is one thing; each pair renames the one that stopped
  describing its contents, with its doc and `CLAUDE.md` row. **Re-test:**
  `git ls-files promptpotter | xargs -n1 basename | sort | uniq -d | grep -v __init__` loses the seven.
- **The `--set` path grammar lives in the CLI**: `presentation/cli/commands/launch.py:275-328` (`_laid`,
  `_lay`, `_set_body`) walks a model's fields to build a patch, so no other door can spell a dotted path.
  Root: the grammar moves to `application/`, and the CLI, the API and `/potter-run` call it.
  **Re-test:** `git grep -n "def _set_body" -- promptpotter/presentation` is empty.
- **A cycle's running job is found two ways**: `served_dashboard.py::spend_counts` (`:611`) takes this
  cycle's `hold_id` from its caller (`run_scope().book` in `run_observers.py:59`, the held run's job in
  `armed_caps`) and reads the siblings' through `_job_holding` (`:636`). Root: one reader of a cycle's
  live job. **Re-test:** `spend_counts` takes no `hold_id`.
- **`spend_counts` builds a full `SpendBook` per cycle to call `count()`**: `served_dashboard.py:617` fills
  a zero-budget book per cycle; `jobs/quota.py:789` and `jobs/launcher/admission.py:595` build similar
  zero-budget books. Root: `SpendBook` has no count-only constructor. **Re-test:**
  `git grep -n "SpendBook(" -- promptpotter/application` names no zero-budget book.
- **The composite step has no band beside the fitness difference**: `domain/verification.py::VerifyReading.composite_increment`
  (`:152`) is a bare float while `fitness_difference` (`:148`) is an `Estimate`. Root: both columns'
  fresh − recorded are one shape. **Re-test:** one shape for both columns' fresh − recorded.
- **A build stamp names HEAD while the build reads the worktree**: `webapp/vite.config.ts:81` stamps
  `git rev-parse HEAD:./` and `application/health.py::webapp_bundle` (`:184`) compares `HEAD:webapp`, so a
  dirty-tree build is stamped as HEAD's and a commit after a build turns a byte-identical page "out of date".
  `scripts/gate.py::Check.stamps_head` (`:1414`) keys `vite-build` on `HEAD:webapp`. Root: the stamp names
  the bytes it built (a worktree tree hash) on both sides. **Re-test:** `stamps_head` is deleted and a
  build-then-commit walk stays green.
- **A focus ring is re-skinned outside `base.css`**: `webapp/app/styles/domains/shell.css:64`
  `.sidebar-filter-search:focus-visible{outline:none;border-color:…}` and `compare.css:477`'s
  `.cmp-expr-bad{border-color}` state a field's look beside the skin. Root: the focus and invalid states of
  a field skin in `foundation/base.css` (`:focus-visible`, `[aria-invalid=true]`). **Re-test:** no `outline`
  or `border-color` on a field class outside `foundation/base.css`.
- **A pane's data read in flight reports nothing**: `webapp/components/chat/HardSamplesPreview.tsx` (and
  every `useRead` consumer but `AccountSpend` and `PipelineFlow`) draws its rows when the read lands but sets
  no `aria-busy` while it is in flight, so the walk counts fetches itself (`webapp/e2e/harness.ts::drawn`).
  Root: `useRead`'s `loading` state marks its pane busy, one rule in `webapp/lib/hooks/useRead.ts`.
  **Re-test:** `drawn` keeps only its `aria-busy` check and `landscape-844 › every view` stays red 3/3 at base.
- **A max-press resume replays on timing** (ml-7): at press max the offline run's `capo/resumed` and
  `gepa/resumed` legs differ run to run on the base itself (capo-only rerun 3×: 3 of 3 moved), so
  equals-main at max cannot pin them; at max a 1-SIGINT `new` can also finish before the signal lands
  (`offline_run.py`'s capo signal leg exits 0, not 130). Root: a resume replays the reads the run filed,
  whatever the press. Unreproduced at ml-8 (4-core cloud: 0 of 3 moved at press 16, 0 of 3 at 64), so
  no replayed record is named yet; reproduce it first. **Re-test:** three runs at press max leave
  `capo/resumed` and `gepa/resumed` UNMOVED against each other.
- **`tests/test_spend.py` holds the walk's tests**: 5,522 lines, 32 lines naming a walk (tw-j deleted
  `_round` and the pause and catch-up tests by mutation; the hand-driven `_walk` stays under the spend
  ceiling, skip and last-skip facts, and the inner stop patches `execute_round`). Root: each becomes an
  offline leg (`run_one`'s knobs) or a rule exposed as a pure function, and `_walk` goes
  (`tests/CLAUDE.md` § Three tiers). **Re-test:** `git grep -ci "walk" -- tests/test_spend.py` is 0.
- **`domain/sample.py`'s name no longer describes it**: the file holds `Sample`, `Bank`, `ArchiveConfig`,
  `ArchiveEntry` and `FiledAnswer`, the archive's filed row beside the dataset's row. Root: the archive row
  and its config move to a module named for the archive; the file keeps the dataset's row.
  **Re-test:** `git grep -n "class FiledAnswer" -- promptpotter/domain/sample.py` is empty.
- **Queue and task-result waits re-read on a clock** (tw-m): `application/jobs/registry.py::_QUEUE_POLL_S`
  and `host_surface.py::_SETTLE_POLL_S` each wait on a write another process makes, and no signal in
  this process carries it, so tests patch both to 0 (`test_security.py::_held_at_arms_gate` and the
  reaped-queued test). Root: a cross-process wake on the jobs dir or ledger write (a lock handoff or a
  file watch). **Re-test:** neither constant exists and no test patches a poll.
- **`config/` has no layering row**: `scripts/gate.py::_LAYERING` (`:189`) lists application, shared,
  domain, infrastructure and connectors but not `config/`, so nothing bars it from `application/` or
  `presentation/`. It imports `infrastructure/` once (`config/first_run.py:9`, `store.io.write_text`)
  while 17 infrastructure files import `config/`. Root: `config/` gets its row, and first-run's write
  moves to the side the row puts below it. **Re-test:** `_LAYERING` names `config/` and the gate's
  `layering` check is green.

### wave-6 · what the test rework's world could not seat (tw-x, tw-j at their landing)

- **A pressed L4 outer run does not repeat** (tw-j): two pressed runs of the e2e L4 fixture differ in the
  outer `L1Critique/Generate_0,_1` requests and the inner decisions, while unpressed runs repeat, so the
  outer's record hangs on which concurrent inner campaign files first. Root: an outer phase reads its
  inners in an order the outer owns, not their completion order. **Rides with:** wave 6 (L4).
  **Re-test:** two pressed runs of `offline_run.py --l4` read the same outer requests.
- **No L4 fixture buys an outer cell ahead, so an L4 outer ending is unread** (tw-j link 1):
  `query_loop.py`'s endings skip `land()` when `phase.cancels`, the connector's `cancel_stops_billing`,
  which only the inner connector sets (`runner/inner/spawn.py::CONNECTOR`). So an inner cell that
  arrived, was filed under its claim and was cut may stay the outer campaign's own memory, with no
  `UntakenBoughtRecord`. tw-j's e2e L4 fixture discarded 0 outer cells at press depth 4 and at 6
  seats: the path is unreached, not refuted. **Rides with:** wave 6 (L4). **Re-test:** an L4 leg
  pressed past one outer cell discards one, and its ledger names it untaken.
- **An operator's act is atomic in the offline world** (tw-j link 2): `offline_world.py::_acted` runs
  a press or a pause whole while the run's loop waits, so its legs repeat, and no leg lands a cell
  between a command's own steps, where tw-r2 and tw-r3 found their bugs. Root: the world steps the
  command and picks the cell that lands between two steps, by an event, never the clock. **Rides
  with:** wave 6. **Re-test:** a leg lands a cell between a pause's two steps, on every run.
- **A rename on a run line re-keys potter** (tw-j): renaming `application/optimizers/potter/dispatch/injections/wounds.py::_guard_evidence` re-keyed potter
  and capo-pobb, while `optimizers/potter/CLAUDE.md` says a rename on a run line re-keys nothing. The
  fingerprint carries a name it should not (`optimizer_manifest.py::behaviour_key`), or the doc is
  stale. Root: whichever side is wrong. **Rides with:** wave 6. **Re-test:** rename `_guard_evidence`
  and read `offline_run.py --digests`.
- **The world has no in-process connector door** (tw-x row 3): test_spend's ungraded-cell fact stays on
  `factories.py::loop_session`. **Rides with:** wave 6. **Re-test:** `git grep -c "loop_session(" --
  tests/` is 0.
- **The never-closed-origin edge of `fork_edge` has no door** (tw-x row 5):
  `application/fork_siblings.py:131-139` is reached by no fork, since `cut_operator_fork` refuses an
  unmeasured origin, so test_identity's sibling stop-record stays on `mint_cycle`. Root: the edge is
  dead (delete it) or a door reaches it. **Rides with:** wave 6. **Re-test:** the coverage run reaches
  `fork_siblings.py:131`, or the lines are gone.
- **No door yields a recorded capability menu or a dataset with a panel** (tw-x row 7): two
  `write_resolved_run` seats stay in test_identity. **Rides with:** wave 6. **Re-test:**
  `git grep -c "write_resolved_run(" -- tests/` is 0.
- **The repair's readers have no producer** (tw-p): `application/bench/round_analysis.py::mended_round`,
  `domain/records/run_records.py::ForkGradedRecord` (with `ledger_scan.py`'s case, `CycleFacts.direction`
  and the `fork` override it feeds, and `fork_graded` in the generated specs and webapp types) and
  `shared/measurement_context.py::MeasurementRole.REPAIR` (`domain/pass_kinds.py`'s row). Their one
  writer was the archive repair's `apply_correction`, deleted as dead by tw-p: a charged hole cuts its arm
  and an uncharged one ends the run before its round closes. Action: delete them with their readers and
  regenerate the specs. **Rides with:** wave 6. **Re-test:** `git grep -n "mended_round(\|ForkGradedRecord("
  -- promptpotter` names only definitions.
- **`domain/cell_fold.py::repairable_holes` counts a charged error** (tw-p): every errored non-HALTED cell
  counts, a charged one a re-walk pays again included, since `ErrorCategory` does not say which errors
  are fillable. With the repair gone, name each reader and what it should count. **Rides with:** wave 6.
  **Re-test:** `repairable_holes` excludes a charged error, or has no reader.
- **A knob layer's tiers have no world example** (tw-x row 10): the knob-layer fact reads only
  "campaign"-level layers; dataset vs free_tier needs a `campaign(knobs=)` layer per tier. **Rides
  with:** wave 6. **Re-test:** a knob fact names a layer of each tier.
- **A resumed walk buys a charged errored cell again** (tw-j2): `infrastructure/store/measurement_archive.py::ReplayFeed.advance`
  skips `cell.errored`, so a pass that a charged error stopped is sent again on resume and paid twice.
  The crossed legs compare deduplicated `passes_graded` and read the repeat as spend. Fork: an errored
  cell replays as the error it was, or the retry is the point and the bill states it. The root is
  shared with `repairable_holes`'s row: `ErrorCategory` does not say which errors are fillable.
  **Rides with:** wave 6. **Re-test:** `offline_run.py --charged` crossed-resumed
  `harness.backend_cells` equals crossed's, or the retry is a decision in `docs/decisions.md`.
- **No leg's L2 carries a steer** (tw-j2): `optimizers/potter/records.py::L2Memory.steered_onto`, which
  `potter/members.py:442` calls when an L2 result names a steer, runs in no leg. At the base only a test
  that seated the steer itself called it, and that test was deleted. Door: the world's L2 reply names a
  `steer`. **Rides with:** wave 6. **Re-test:** offline `--coverage` reaches `records.py:79`.
- **23 parked facts wait for a world door** (tw-j2; each is kept as a test until its door exists):
  - test_identity I2: a dataset with two prompt nodes.
  - I3, I4: a parent on fewer rows than its frontier.
  - I6: an L4 origin past 16k chars.
  - mixed ×2: an L4 leg mixing potter and capo.
  - test_scoring S3, S6, S13: a leg that binds a budget (backend cells cost $0).
  - S8, S14, S15: a pause during a bench pass.
  - S9–S12: a leg setting `origin_replicates`.
  - S16–S18: a gate leg running `every_adoption`.
  - S19: an `arm_gate=author` leg.
  - S20: a GEPA round proposing more than one arm.
  - S25: a world LLM naming `stand_on`.
  - test_spend's skip journey: a `run_one` act that sends `SkipSearchpoint` mid-walk, with a resumed twin.
  - test_spend's inner-stop: an L4 leg whose inner run rebases and whose provider refuses credit.
  - The catch-up fact deleted at `af224d3bd` line 1222: a pause while a prior's catch-up is out past
    the sure rows.
  - The priced-replay facts at lines 1324 and 1349: a Compare leg paused mid-walk and resumed
    (`search_incurred`). Mutating `claim_price` leaves every leg green.

  **Rides with:** wave 6. **Re-test:** each fact moves to a leg on its door, or is deleted by a named
  mutation.
- **Hand-seats with no world door** (tw-j2 link 5): `world.opened(rows)` takes no connector, and
  the seats below have not moved onto `world.minted` (tw-v's mint door). test_security seats `minted_campaign` ×6,
  `stored_campaign` ×3 and `loop_session` ×1; test_scoring `loop_session` ×2 and `minted_campaign` ×2;
  test_spend `loop_session` ×2 (one is tw-x row 3's). **Rides with:** wave 6. **Re-test:**
  `git grep -cE "\b(minted_campaign|stored_campaign|loop_session)\(" -- tests/test_*.py` is 0.
- **test_security's stdio-host test is 78 lines** (tw-c, tw-j2), against the 60-line budget: it is one
  function, so that one spawned host answers every act. Door: a host spawned once for the module.
  **Rides with:** wave 6. **Re-test:** the test is at most 60 lines and spawns no host of its own.
- **`write_json` writes with `indent=2`** (tw-q): `infrastructure/store/io.py::write_json` indents
  every file it writes, the dashboard's on every write included, and an indent forces json's
  pure-Python encoder. Fork: a run folder is a headless UI that people read (compact JSON is
  machine-only), against the encoder's cost on every write. Price both before choosing. **Rides
  with:** wave 6. **Re-test:** a cProfile of one world run ranks `json.encoder` below tw-q's
  frame list, or the format is a decision with its reason.
- **The suite runs ~3.6 s over `7dce00a96`, all of it test_archive's world runs** (tw-v, tw-v2): in one
  session, `7dce00a96` 62.0–67.0 s and tw-v's tip 65.2–67.9 s. `test_archive.py` takes 16.4–19.4 s
  against 3.4–3.5 s hand-seated. `line`, `displaced`, `family_tree`, `origin_forks` and `gated` have
  distinct forks, so no shape merges. Operator, 2026-11-26: close it once the indirections are removed,
  never by adding complexity for seconds. Door: a cheaper run, not fewer runs. **Rides with:** after
  wave 6. **Re-test:** archive's file wall is under 8 s, and the suite's is below `7dce00a96`'s.
- **A landing key re-derives per call** (tw-v, tw-v2): `shared/import_walk.py::import_closure`
  normalizes each module from its own parse (`hashing.py::normalized_tree`), beside
  `hashing.py::normalized_module`'s memo. That costs 2.16 s for the 329-module closure. On top,
  `store/data_root.py::row_schema` rebuilds pydantic `json_schema()` over 90 kinds (0.6–0.85 s), and a
  memo keyed by the model class is keyed by identity. tw-v2's fix (`arc/tw-v2` 604b03eb2) was not
  taken: +47 production lines, because a text-keyed memo held 10.5 MB, so it went sha-keyed and
  zlib-compressed, to save ~2 s on one fact. **Rides with:** after wave 6. **Re-test:** a second
  `scripts/build_ts_types.py::derived_landing_key()` in one process takes under 0.2 s, with
  production no larger.
- **`durable=` is a caller's flag restating `Kind.durable`** (tw-v2): `store/io.py::write_bytes`,
  `write_json` and `appending` fsync on a flag that each writer passes (`jobs/registry.py`,
  `maintenance/landing.py`, `identity/grants.py`, `identity/tokens.py`, `store/user_store.py`,
  `store/wallet_store.py`). That sits beside `data_root.py::Kind.durable`, the row that decides it.
  Priced: 5,087 fsyncs a suite, 2.5 s, each on a truth no fold rebuilds (`charges.jsonl` 3,087, live
  job files, `.live-jobs`, accounts). `dashboard.json` is never fsynced. Door: io's doors read
  durability off the file's kind. **Rides with:** wave 6. **Re-test:**
  `git grep -cE "durable=(True|not )" -- promptpotter/*.py` is 0.
- **Child-process facts idle the parent ~8 s** (tw-v, tw-v2): `conftest.py::dspy_child` (2.9 s), the
  API-start child (2.7 s) and the stdio host (2.4 s) run serially. Each peaks at 160–165 MB, so
  overlapping them breaks pytest's 393 MB cap. `-X importtime` finds no extra loaded by a boot that
  never calls it: `promptpotter.main` takes 2.6 s (`application/health.py` 0.82 s, `mcp` mounted at
  `/mcp` 0.64 s), and the CLI 0.45–0.68 s (`domain/command_kinds.py` 0.40–0.58 s). Door: fewer models
  built at import. **Rides with:** wave 6. **Re-test:** each child's wall is under 1.5 s.
- **A child process has no offline-world door** (tw-v): `refused_network` stops at the process, so
  test_security's API-start child fetched upstream's real price list until `d2fce21` gave it a
  `-c` preamble, `tests/test_security.py::_OFFLINE_CHILD`, that installs the world's `Router`. Door:
  one child entry in `scripts/offline_world.py` that every spawned child runs under. **Rides with:**
  wave 6. **Re-test:** `git grep -c "_OFFLINE_CHILD" -- tests` is 0 and each child asserts nothing
  went unrouted.
- **A first landing leaves `jobs/.live-jobs` unwritten** (tw-v, `tests/test_security.py` `moved`
  fixture): on a tree that never ran, a first landing writes no live-jobs index, and a second landing
  stages it, so the fixture keeps `campaign(rounds=0)` rather than a mint. This is a state the system
  enters but does not report. Suspected root: the landing's index row is written only once a run has
  started. **Rides with:** wave 6. **Re-test:** a first landing on an empty root writes the index, and
  the fixture mints.

### wave-6 · what the v0.8.21 box test showed (2026-11-26)

- **A wiped campaign answers two failure kinds**: after the landing, a still-open tab asked
  `/campaigns/<wiped>/pipeline` (400) beside its `/hierarchy` and `/dashboard` (404). I7's GONE
  stops on a 404 alone, so that read keeps asking. Door: an absent campaign is a 404 at every read.
  **Rides with:** wave 6. **Re-test:** each campaign read of a deleted campaign answers 404. Not
  reproduced at `2c7dff34e` (w7-small: `/campaigns/gone/pipeline` answers 404 with and without `at=`,
  over every hop shape); if the 400 recurs, capture the exact `at` the stale tab sent.
- **"Rebased to fork" names no fork**: the readout's stop line names neither the round the fork was
  cut at nor the layer's reason, so the run that continues reads as unexplained. The stop renders in
  `views/readout.py`, but no record carries either fact: `escalation.py`'s
  `StopLoop(StopReason.REBASED, fork=…)` and `runner/entry.py::open_line`'s stop pass no detail.
  Door: the stop's record carries the cut round and the `RebaseRequest.reason`, and its words read
  them. **Rides with:** wave 6.
- **A host-allowance halt below the face, unexplained**: a visitor's first run ended `host_allowance`
  at $0.041 spent of a $0.30 face (hold bound $0.06). Unchecked whether the run's own ceiling was
  worded as the host's allowance. **Rides with:** money. **Re-test:** a run halted on its own
  ceiling names that ceiling, never the allowance.
- **A dead producer's lock and its containers are reclaimed by two owners**: the box's `jobs/producers/`
  holds 15 `.lock` files, one per past producer pid, with nothing listed live: only
  `docker_host.py::reap_dead_producers` forgets a jobless producer's lock, and it runs only on a machine
  step. Yet `application/jobs/registry.py::JobRegistry.reap_orphans` unlinks a dead producer's lock when it releases its
  job, and `reap_dead_producers` finds a dead producer's containers BY that lock, so a producer that
  died holding a job leaves its containers unreaped. A sweep that unlinks every unheld lock (w7-small
  link 3, not landed) widens that leak to every dead producer. Root: one reclaim of a dead producer
  removes its containers and then its lock, and every sweep calls it. **Re-test:** a producer killed with
  a job and a labelled container leaves neither its lock nor its container after one sweep.
- **`promptpotter-self`'s ε 0.30 is priced on the retired pairwise statistic**: its comment weighs a
  false cut against a false survival on the pairwise p_best (and a 0.15 default), but since
  5cf502c7b ε reads a field share that a 3-variant round ties at 0.25, so 0.30 cuts a tied leader
  (`epsilon_at_a_tied_share` names it). Door: a value measured on the field-wise p_best stream of an
  outer campaign. **Rides with:** L4. **Re-test:** the outer round's check-in names no collision.

### wave-6 · what the round-decision restructure left (2026-11-26)

- **Round 0 is CROWNED on its `Election` and holds no election as served**: `Election.ending` says
  `crowned` where the origin is adopted, `RoundResult.improved` and the readout's hold exclude it by
  `round > 0`, and `run_records.py` serves `ending: null` and no `verdict_reason`. Door: round 0's
  ending is its own state (`adopted`), so no reader tests the round number. **Rides with:** θ off the
  round's statement (roadmap § Sequence).
- **`RoundOutcome.reference` and `Election.parent` name one arm twice** (`results.py`,
  `layer_state.py` reads the first): the parent the round re-read is the election's input. Door: one
  field, the round's `RoundArm`. **Rides with:** θ off the round's statement.
- **The race a round closes on can differ from the one its election read**
  (`round.py::measure_overlap`): a pick cut short, a line gap or a trigger buys after the election,
  and the closed reading is the later one. Door: the election reads the round's last race, or the
  closed record names which it read. **Rides with:** θ off the round's statement.

### wave-7 · what the w7 chains filed (2026-11-27)

- **The masthead's candidate count names no count** (w7-line): the masthead still recounts candidates
  beside `ServedDashboard.line`, so its number and the bars' can disagree on what they count (arms
  scored over the line, or C0 plus every candidate). Door: a served count beside the line, named for
  what it counts, read by the masthead. **Re-test:** the masthead derives no count of its own.
- **`ActiveNode.scope` is never `target`** (w7-line): `live_dashboard.py::ActiveNode` declares
  `optimizer | target`, but `_active_node` serves `optimizer` alone (a scoring pass runs as the
  measurement node, an optimizer node), so the target layer never lights and the client's target
  branch is dead. Door: under pp-self the outer fold serves the inner run's active node (it already
  folds `record.inner`) as `target`; else the variant and the client's branch go. **Re-test:** a
  `target` scope is produced, or `ActiveNode` has no `scope`.
- **`cycle_reads.py` copies the live round's candidates into a second shape** (w7-line):
  `application/cycle_reads.py:136` copies `current_round.candidates` whole. Door: its reader takes
  the served line. **Re-test:** no copy of `current_round.candidates` outside the fold.
- **A superseded cycle's successor is worded twice** (w7-line): `domain/phases.py:291`'s
  `start_refusal` and `ServedDashboard.successor` each word the same fact. Root: one `Said` on
  `RunState`, read by both. **Re-test:** one sentence names a cycle's successor.
- **The readout folds the closed rounds three times at a stop** (w7-text): `views/readout.py`'s
  `_reviewed` and `cycle_hard_samples` each fold `closed_rounds`, and the review folds again at every
  close. Root: the projection keeps one round fold. **Re-test:** one `closed_rounds` fold per stop.
- **`ReviewReading.feedback` has no reader** (w7-text): `potter/l1.py::review_reading` fills it, but the
  critique reaches the readout as the round block's note. Deleting it moves capo-pobb's behaviour key,
  so it waits for a planned re-key. **Re-test:** `git grep -n "feedback=\[" -- promptpotter/application/optimizers/potter/l1.py` is empty.
- **`optimized.md` is a markdown artifact root G did not name** (w7-text):
  `infrastructure/store/layout.py::optimized_surface`. Root G's question, the operator's to decide:
  the winner's text as plain text in the folder, or a markdown file.
- **`identity/accounts.py::is_host` wraps `identity.host`** (w7-host): a one-line wrapper; the roadmap
  cites it. Door: callers read `identity.host`. **Re-test:** `git grep -c "def is_host" -- promptpotter` is 0.
- **A bulletin dismissal has no account deleter to ride** (w7-host): no code deletes an account, so
  "an account's deletion drops its dismissals in the same write" has no writer. It lands with the
  account deleter (`bulletin_board.py` drops `dismissed[account_id]`).
- **`claim_default` orphans the terminal's dismissals** (w7-host): `identity/accounts.py::claim_default`
  renames the terminal's `default` account to the host id; dismissals filed as `default` stay there, so
  the host sees those bulletins again. Door: the claim re-keys the account's dismissals in its write.
- **The CLI's `bulletins` read hides which pop up** (w7-host): only the webapp shows the mark. Door:
  the read's line names it.
- **The admin bot's account commands bypass the dispatcher** (w7-host): its other admin commands call
  `application.account` directly (decisions § 0004); `/bulletin` is the first to dispatch a
  `CommandKind`. Door: each bot verb is a command kind. **Re-test:** the bot imports no
  `application.account` writer.
- **The gate rewrites `maintenance/built_key.py` on every code change** (w7-small; the wave-7 landing
  hit it at each squash): a committed key of the code tree is stale after any edit, so every commit
  regenerates it first. Root: the key is computed where it is read, or the gate's write is the
  commit's own step. **Re-test:** a code-only commit needs no regenerated file.

## Held — no wave-4 chain yet

- **The estimator has no golden corpus**: its key (`nodes.py::BehaviourKeys.cell_estimator`) reads the
  L4 leg's trace on the offline world, not its outputs on fixed inputs. A corpus would read each inner cell's `CycleResult` (~255 KB each) and the
  tenant ruler's response rows: over 1 MB, and its only source is account data on the box, which the
  operator may wipe. **Held:** a corpus source the repo can commit under 1 MB. **Re-test:** the
  estimator's entry in the behaviour-key file names its key's inputs.
- **"Which benchmark a row measures" is spelled twice**: Shape: `potter/members.py::_strata` reads
  `measured_dataset or ""` where every other reader takes `Sample.measured_dataset` raw (None = the
  filed one), and `BenchScope` partitions by benchmark under the word `RulerScope` owns. Action:
  `_strata` keys on the field raw; `BenchScope` takes a benchmark name. **Held:** l4-outer owns
  `potter/`, l4-runs `runner/bench.py`. **Re-test:** `git grep -n "measured_dataset or\|BenchScope" -- promptpotter`.
- **`locus_crossover` re-crosses a measured or hosted child**: it edits every two-parent proposal, so
  a hosted recombination and a rejoined crossover child are crossed again (one call: the re-crossed
  id differs; l4-outer-5's report). Action: a crossover applies to a proposal it drew, never one it
  receives. **Rides with:** the next variation chain. **Re-test:** a hosted recombination keeps its id.
- **An over-budget crossover child books its verdict under axis `variant`**: `bench/children.py`'s
  one length check cannot tell which mutation minted the child. Action: the verdict names the
  mutation kind. **Rides with:** the row above. **Re-test:** a crossover child's OVER_CEILING names
  crossover.
- **A machine's cell ceiling is counted twice**: `infrastructure/backend.py::MachineSlots` (a
  file-lock pool across processes, for `cells_hold_the_machine` alone) beside flight-share's
  in-process count per backend. The two count different subjects: `infrastructure/backend.py::_SENDING` this
  process's calls by `(connector, base_url)`, which `query_loop.py`'s `room()`/`freed()` schedule
  from; `MachineSlots` containers across processes by `connector.name`, by file locks a probe
  cannot read machine-wide (POSIX `fcntl` locks are per-process). Decided (2026-11-19): for a
  machine-held connector the in-process count gives way and arming waits on the pool, one count of
  one fact. **Rides with:** a store or spend lane. **Re-test:** no machine-held connector's arming
  reads `_SENDING`.
