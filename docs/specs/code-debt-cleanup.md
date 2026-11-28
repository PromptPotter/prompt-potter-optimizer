# Code-Debt Cleanup — Backlog

**Only work that is blocked or multi-arc** — including every shape a pass noticed off its task (root
`CLAUDE.md` `<situational-awareness>`). Anything one edit closes is fixed in the pass that found it; never
the root of a patch just shipped (root `CLAUDE.md` `<root-fix>`). An item ships by being deleted. Rows are
grouped by area, the 14 areas of the cleanup map; each section opens with the area's Target and the modules
no row yet examines. Each entry: `file::symbol` — the defect — the action — **Rides with:** the chain that
reaches it ([`roadmap.md`](roadmap.md) § The cleanup chains (wave 4)), or **Held:** why none can yet (a
digest, a decision, a mechanism, a served change, a survivor) — **Re-test:** the command or question that
says it still stands. Run the re-test before acting. A chain re-tests its rows in its census and deletes each
row it closes. A root closes the rows it names by title without restating them; each goes when its own re-test
passes. Counts are `git grep -cE` hits/files over `promptpotter/` unless a path is named.

## 01 · Records spine and the round envelope

**Target:** A round is a neutral envelope: PROPOSE, MEASURE, SELECT, ADAPT, the headline derived, no field
naming a layer, a critique or an elimination rule (§0). Every ledger fact is a `CycleRecord` in a module of
its own under `domain/records/`, read by kind; a pass that measures is one record with an arm; no record holds
a value another derives; no field without a reader. No I/O, no async; a rename wipes run data, so shapes
settle before L4 spends.

**Unexamined:** none (w11-records2 read `results_health.py`, `arm_reading.py`, `wire_record.py`,
`export.py`, `candidate_diff.py` and `shared/errors.py`; what it found is a row below). Of the 51 models a
`RoundStatement` nests, 2 name a reader on themselves and 43 are read in several modules.

- **The data-root spec names dataclass fields, not wire keys** (w11-spend2, 11-28):
  `docs/specs/data-root.generated.json` prints `pipeline` where the file holds `pipeline_data`, and prints
  `StepUsage.tokens` where the wire lays it flat. A survivor's migration is written against that spec.
  Root: the generator reads the wire record (`WireRecord.of`). **Rides with:** area 01's chain.
  **Re-test:** each spec key appears in the file the offline run writes.

- **The layering is fictional at runtime**: the package holds no function-local import and its
  runtime file graph is acyclic, but 51 `TYPE_CHECKING` imports lie on a file-level cycle after
  w11-records2 (57 at its base), so a type-only block hides each knot rather than cuts it. Every other
  type-only import is hoisted, keeping only the two that keep `promptpotter --help` off httpx and
  rich, the layer crossings and the Harbor extras. The knots left:
  - the store hub and its parts: `stores` ↔ `session_pointer`, `account_spend`, `account_trail`,
    `archive_queries`, `dataset_access`, `spend_book`, `run_scope`, `connectors/protocol`. A
    narrower parameter cannot close these, so each part needs the protocol it reads in a module of
    its own;
  - the domain: `pipeline_params` and `search_point` ↔ `pipeline_schema` (area 07's files);
  - bench ↔ the plugin contract: bench reads `optimizer_contract.py`'s `CycleRead`, member
    protocols and `NODE_MEMBERS`, which name the bench's `NodeContext` and `RoundContext`. Moving
    `CycleRead` alone re-formed runtime cycles.

  Action: each knot is cut at its root, held by `complexity_ledger.py::_type_cycles`. **Rides with:**
  layers. **Re-test:** the `type_cycles` row of `BASELINE` falls to 0.
- **Names collide across layers**: two modules each named `head_to_head.py`, `hierarchy.py`,
  `power.py` and `cells.py` (`domain/cells.py` holds L4's `Seat` and `PinnedCell` beside the served
  cell views, a split rather than a rename); `ledger_scan.py::CycleFacts` beside
  `evidence/read.py::_CycleFacts`. Root: a name is one thing; each pair renames the one that stopped
  describing its contents, with its doc and `CLAUDE.md` row. **Re-test:**
  `git ls-files promptpotter | xargs -n1 basename | sort | uniq -d | grep -v __init__` loses the four.
- **A closed round banks its election twice**: `runner/round.py` appends an `ElectionRecord` from
  `result.election`, and the round's `RoundClosedRecord` carries the same `Election` through
  `results.py::RoundOutcome.election`; `ledger_scan.py` reads the first. Action: the ledger states the
  election once, the other reader reads that record. **Rides with:** area 01's chain. **Re-test:**
  `git grep -c "ElectionRecord(" -- promptpotter` is 0, or `RoundOutcome` holds no `election`.
- **What a child moved over its parent is computed twice**: `candidate_diff.py::candidate_delta` diffs
  flattened prompt maps (read by `l1.py`, `validators.py`, `panels.py`, `earned_blocks.py`) beside
  `opt_search_point.py::OptSearchPoint.loci_moved_from` over `loci()` (read by `export.py`,
  `hierarchy_subtree.py`); `flatten_prompts`/`flatten_sp_summary` are a third spelling of a locus, and
  `parent_param_value` falls back `or {}` twice. Action: `CandidateDelta` is built from `loci()`.
  **Rides with:** area 01's chain. **Re-test:** one of `candidate_delta`, `loci_moved_from` is left.

## 02 · Store, ledger, projections, landing

**Target:** One ingress (`CycleEventLog.append`), projections that are checkouts nothing reads back, the
archive one content-addressed tree filed by the dataset a row measured, `MeasurementArchive` its only gateway.
A line's fold is stated once and read by one keyed read `(hop, scope, fold)`. Survivors migrate, everything
else is wiped at a landing. The store holds no money rule and no served shape.

**Unexamined:** `maintenance/archive_maintenance.py` (w11-store2 read every module under `store/**`
and `landing_key.py`; what it found is a row below).

- **The feed's version spans a whole store**: a campaign is a subject its writers stamp
  (`io.py::stamp`), and a subtree reads its family's campaigns alone (`Stores.stamps`), but every
  evidence and cells view still reads its store's whole stamp; the feed's tick lists each store's cycle
  hops to find its sandboxes (`cycle_reads.py::_store_stats`), and a `store:`-subject read re-runs
  `feed_versions` over all stores (`presentation/api/routers/reads.py::_endpoint`);
  `reads.ts::draftRead` follows bare `store:` beside `account`, since Start's refusal reads the wallet
  every ledger's bill moves, so an inner sandbox's append re-asks it; `cycle_reads.py::_COUNTING` is
  one lock over every account's walk. Action: a view keys on its campaign's stamp, and a sandbox is
  found off its owner's stamp, never a walk. Beside it, an install or research definition edit
  ticks no feed (those tiers sit off every stamp); the cells ETag does move
  (`dataset_access.py::definition_version`). **Rides with:** a version chain of its own.
  **Re-test:** `draftRead`'s subject is not `store:`.
- **On Windows a replace is refused while a reader holds the file**: one thread looping `write_bytes`
  and four looping `read_bytes_optional` on one file for 20 s saw 86 of ~210 writes raise `PermissionError`
  past `store/io.py::_atomic_replace`'s three tries, at 6fa93bab0 (whose reads retry, 0 refused, where
  fdad7947d's refused 49). Live 2026-11-19: it cut an L4 outer cell on the desk (`multistep-wide__773fe8`
  round 1, C1.3 #000: `PermissionError: [WinError 32]` replacing an inner job file under
  `.promptpotter/jobs/`); 94 of 182 writes refused at `61fff035c`. Two fixes measured and reverted:
  reads sharing delete (a `CreateFileW` opener) alone refuse more (95 of 99), as `os.replace`
  (MoveFileEx) refuses any destination still open; adding a POSIX rename (`FileRenameInfoEx`,
  replace | posix) lands every write, but reads then see the file absent in bursts (0, 163, 167,
  382 of ~10k a 25-s run), an immediate re-open included, which is a silent wrong read. **Decided
  2026-11-27:** hold it and keep the retrying reads: both measured fixes were worse, and the box (Linux)
  is untouched.
  **Rides with:** rel 2. **Re-test:** the hammer, 0 refused writes and 0 absent reads.
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
- **`domain/sample.py`'s name no longer describes it**: the file holds `Sample`, `Bank`, `ArchiveConfig`,
  `ArchiveEntry` and `FiledAnswer`, the archive's filed row beside the dataset's row. Root: the archive row
  and its config move to a module named for the archive; the file keeps the dataset's row.
  **Re-test:** `git grep -n "class FiledAnswer" -- promptpotter/domain/sample.py` is empty.
- **`write_json` writes with `indent=2`** (tw-q): `infrastructure/store/io.py::write_json` indents
  every file it writes, the dashboard's on every write included, and an indent forces json's
  pure-Python encoder. A run folder is a headless UI that people read (compact JSON is
  machine-only), against the encoder's cost on every write. **Decided 2026-11-27:** price both first; the
  run folder stays indented; only `dashboard.json`, which nothing reads back, is a candidate for compact. **Rides
  with:** wave 6. **Re-test:** a cProfile of one world run ranks `json.encoder` below tw-q's
  frame list, or the format is a decision with its reason.
- **`list_campaigns` drops an unreadable manifest into a log** (hubs-2, 11-18):
  `promptpotter/infrastructure/store/campaign_store/store.py::CampaignStore.list_campaigns` —
  `logger.warning` then `continue`: the account holds a campaign no surface reports — the
  survey has a typed unreadable row — **Rides with:** `area 02's chain` — **Re-test:** a
  campaign with a corrupt manifest appears in the listing as unreadable.
- **A line's walks are a flat list every reader re-scans per individual** (w11-reads, 11-28):
  `campaign_store/ledger_scan.py::line_walks` returns `list[IndividualWalk]`. `closed_rounds.py::LineCells` keys it
  once for the reads, but `passes.py:220` and `domain/results.py:329` still filter it per individual.
  Root: `line_walks` serves the walks keyed by individual. **Rides with:** area 02's chain (with 01's
  `results.py`). **Re-test:** `git grep -n "individual_id ==" -- promptpotter/application/scoring
  promptpotter/domain/results.py` is empty.
- **A sandbox whose owner file does not parse is kept and reported nowhere** (w11-store2, 11-28):
  `promptpotter/infrastructure/store/layout.py::read_sandbox_owner` logs the parse error and reads
  `None`, which every caller takes as "keep"; the trail and the delete skip it, so its spend and its
  disk stand under no surface. Root: an unreadable owner is a typed row the storage report lists —
  **Rides with:** `area 02's chain` — **Re-test:** a sandbox with a corrupt owner file appears in
  `storage_report.py`'s report.
- **The `Stores` hub holds a cycle's stop machine** (w11-store, 11-28):
  `promptpotter/infrastructure/store/stores.py::Stores.declare_ended` (with `declare_stop`,
  `mark_superseded`, `mark_producer_vanished`) runs the producer-lock ending on the hub every
  bench import reaches; the ending belongs to the campaign store's append door, which cannot void
  the inbox yet: a `CampaignStore` carries no subject chain (`CampaignStore.in_sandbox` builds one
  off a sandbox whose `store/layout.py::SandboxOwner` names one hop). Root: the campaign store carries its
  `inside` and the account's `CommandLog` — **Rides with:** `area 02's chain` — **Re-test:**
  `git grep -cE "def declare_ended" -- promptpotter/infrastructure/store/stores.py` is 0.

## 03 · Runner, round, bench

**Target:** The bench walks a manifest's `default` once per round and `initial_population` in a round entered
with no population; it decides nothing an optimizer owns and imports none. Init writes a frozen `Session`, the
runner only `Session.state`; one `RunScope`; a stop is a `StopReason` declared at one door; resume and fork
read standing rounds off the ledger.

**Unexamined:** `runner/round.py`, `initialization/scoring_scope.py`, `embedded_run.py`,
`initialization/session.py`, `bench/export.py`, `domain/activity.py`,
`resume_and_fork/resume.py`, `origin.py`, `bench/node_context.py`, `run_observers.py`, `bench/cycle.py`,
`bench/line.py`

- **A resume's wait is unattributed**: on the box a resume takes up to 72 s to be applied, while
  offline its `open_launch`, `open_cycle` and `close_init` total about 0.3 s and the landing stamps once
  per schema, so the wait sits on the backend's network edges (every symbol is present, but the box
  half is unchecked; bac873699, landing 41, changed the TermNorm term push, so that candidate's cost may
  have moved). A detached resume opens its session once now, in its own process (w8-launch4,
  `held.py::HeldLaunch`). Action: time `probe_backend`,
  `served_pipeline`, `ensure_model_capabilities` and the term push on the box for one paused cycle, then
  drop whichever one a resume re-does. **Rides with:** box (it needs the box measured), whose Owns gain
  `runner/run_init.py` and `initialization/wiring.py`. **Re-test:** time `start-run` → its `applied`
  ack on a paused cycle.
- **A max-press resume replays on timing** (ml-7): at press max the offline run's `capo/resumed` and
  `gepa/resumed` legs differ run to run on the base itself (capo-only rerun 3×: 3 of 3 moved), so
  equals-main at max cannot pin them; at max a 1-SIGINT `new` can also finish before the signal lands
  (`offline_run.py`'s capo signal leg exits 0, not 130). Root: a resume replays the reads the run filed,
  whatever the press. **Held:** unreproduced at ml-8 (4-core cloud: 0 of 3 moved at press 16, 0 of 3
  at 64); reproduce it first. **Re-test:** three runs at press max leave `capo/resumed` and
  `gepa/resumed` UNMOVED against each other.
- **`BenchStatus.state` spans `read`** (spine4, 11-27):
  `promptpotter/domain/bench.py::BenchStatus` — `state: ReadingState` includes `READ`, so
  `bench/line.py`'s unread branch asserts it away (`a read bench carries its pair`) — the
  unread status is its own type without `READ` — **Rides with:** `area 03's chain` —
  **Re-test:** `git grep -n "a read bench carries its pair" -- promptpotter` is empty.
- **A block race files an `ArmRecord` per arm per turn** (rk-dec, 11-13):
  `promptpotter/application/runner/measure.py::_walk_population` — its `on_turn` appends an
  `ArmRecord` per arm per cell taken (block_size=1), so a block's ledger grows with cells — the
  root: an arm's state is filed at the decision that moves it, not at every turn — **Rides with:**
  `area 03's chain` — **Re-test:** `git grep -n "ArmRecord(" -- promptpotter/application/runner/measure.py`
  shows `on_turn` filing one; a block_size=1 race's ledger holds one per arm per cell.
- **A refused send's stop and a banked hole's stop share one field** (l4-words, 11-18):
  `promptpotter/application/run_stop.py::panel_gate_tripped` — spells SERVER to `BACKEND_HOLE`
  beside `domain/phases.py::ERROR_CATEGORY_INFO`, whose `stops` says `None` for SERVER — the root:
  `stops` carries two meanings (a refused send's stop, a banked hole's stop); split them so one
  table owns each — **Rides with:** `area 03's chain` — **Re-test:** `git grep -n "BACKEND_HOLE" --
  promptpotter/application` names `panel_gate_tripped` alone, and `stops` for SERVER agrees.
  (Item 522.)
- **A resume's divergence check and its cut are one off-loop function** (w8-fork, 11-27):
  `promptpotter/application/bench/resume_and_fork/resume.py::resume_with_divergence_check` — runs
  whole under `asyncio.to_thread` (`runner/run_init.py`) only because its cut takes the gate —
  the check and the cut are one function; the decision runs on the loop, the cut off it —
  **Rides with:** `area 03's chain` — **Re-test:** `git grep -n "to_thread, resume_with_divergence_check" --
  promptpotter/application/runner/run_init.py` is empty. (item 79)

- **An inner run's spawn is read off the ambient scope, so bench and init import `runner/inner/`**
  (w11-runner census, 11-28): `promptpotter/application/bench/node_context.py::Target._inner_run`
  finds a cell's sandbox through `run_scope().spawn`, answering `(None, None)` off an
  `InnerSpawnContext`, and `initialization/scoring_scope.py::session_scope` builds `inner_spawn_of`
  itself; `scoring_scope.py::run_memory` likewise prefers the ambient `ArmOf` — the root: the spawn
  is a keyed field the runner hands its session, never a global read below it — **Rides with:**
  `area 03's chain` — **Re-test:** `git grep -n "^from promptpotter.application.runner" --
  promptpotter/application/bench promptpotter/application/initialization` is empty.
- **A cycle's export skips a lost bank in silence** (w11-runner census, 11-28):
  `promptpotter/application/bench/export.py::_render_export` returns when `bank(drawn.dataset_hash)`
  is `None`, while `initialization/session.py` raises `ConflictError` on the same fact — the root:
  one reader of a cycle's drawn bank, which refuses — **Rides with:** `area 03's chain` —
  **Re-test:** `git grep -n "if bank is None" -- promptpotter/application/bench/export.py` is empty.
- **`declare_line_bench` re-derives its scorer and patches the live reading** (w11-runner census,
  11-28): `promptpotter/application/bench/line.py::declare_line_bench` builds
  `Resolution(config, …).scorer` beside the seated `session.state.scoring.scorer`, falls back from
  `passes` to `line_passes`, and `_replace`s `live_cycle_run`'s `selecting` / `ending` — the root:
  the launch's word is a field of the run it reads, and the scorer is the session's — **Rides
  with:** `area 03's chain` — **Re-test:** `git grep -n "Resolution(config" --
  promptpotter/application/bench/line.py` is empty.
- **An unbound session's address is two empty strings** (w11-runner census, 11-28):
  `promptpotter/application/initialization/session.py::Session` defaults `cycle_id` to `""` and
  `campaign_id` answers `""` unbound, so `hop` builds `CycleHop("", "")` where `bound_campaign`
  raises — the root: an unbound session has no hop — **Rides with:** `area 03's chain` —
  **Re-test:** `git grep -n 'cycle_id: str = ""' -- promptpotter/application/initialization/session.py`
  is empty.
- **A line's spent rounds are spelled at three sites** (w11-store, 11-28):
  `promptpotter/application/bench/line.py::line_round_budget`, `runner/entry.py` and
  `runner/inner/spawn.py` each sum or flatten `LineSpan.own` over a line — one function of the
  line's spans — **Rides with:** `area 03's chain` — **Re-test:**
  `git grep -c "span.own" -- promptpotter` is 1.
## 04 · Scoring, fitness, statistics

**Target:** One scoring gateway (`search_point_scorer.py`); a cell is a fact, never a grade; fitness is
formula- and mode-relative and served, never recomputed; one tenant δ ruler; two θ differenced only through
`comparable_to` / `lift_over`; no retry of a (sample, candidate) pair; the bench set is held out from every
optimizer node.

**Unexamined:** `scoring/sample_measurement.py`, `scoring/search_point_scorer.py`,
`scoring/formula/compiler.py`, `intelligence/indexes/axis.py`, `scoring/row_policy.py`,
`domain/fitness_fold.py`, `scoring/query_loop.py`, `domain/scoring.py`, `scoring/measurement_log.py`

- **A walk ended outside `land` names nothing it bought**: `query_loop.py::_ScoringPhase.discard` (a
  cancel) and a crash write no `UntakenBoughtRecord` (only the land path, `query_loop.py:1271`), so those
  paid cells stay the campaign's memory. Root: name the cell at its filing in `_acquire`. **Rides with:**
  area 04's chain. **Re-test:** a cancelled pressed walk's untaken cells are left out of its campaign's
  `SampleIndex`.
- **A freed slot idles up to `HELD_POLL_S`**: `query_loop.py::_ScoringPhase.freed` polls a run waiting on
  another run's landing (`asyncio.sleep(HELD_POLL_S)`, two sites). Root: no wake signal crosses phases.
  **Rides with:** area 04's chain. **Re-test:** `git grep -c "sleep(HELD_POLL_S)" --
  promptpotter/application/scoring/query_loop.py` is 0.

- **The first verify after a pause started 38 s late (2026-11-06, live)**: the click shows from admission now
  (the pass's slot serves `starting` then `running`, and an unended command reads `working`), so no
  `accepted` ack is owed: that status is a loop's standing command. Between the slot's START and the
  phase's `enter`, `scoring/passes.py::verify_candidate` opens the session (`open_diagnostic`), scans
  the archive (`measurements_for_config`) and fits the ruler (`judging_ruler`); a warm verify entered
  in 1 s. Action: time those three on a cold verify, then cut the one that holds it. **Held:** a live
  verify on a paused cycle. **Re-test:** time `command` → the `verify` phase's `enter` in the ledger.
- **The warm gate counts no se_δ**: θ now reads each δ under its SE (`DeltaRuler.entries`), but the
  young-ruler joint MAP still shrinks δ̂ inward (slope 0.30 at 3 individuals per cell) and the warm
  gate warms on row counts alone.
 Root: δ by MML, or the warm gate reads se_δ.
  **Rides with:** readings' successor.
- **A connector's two-valued term reaches no scorer** (readings-5's report): `pipeline_resolve.py::Resolution`
  compiles from `CampaignConfig` alone, which names no connector, so a `CellTerm` or observation
  declaration cannot reach `matchers.py::fitness_scale_of`; harbor's `env_reward` is
  `float(reward)` of a panel's `reward_key`, two-valued only per task, so
  `max(0.0, min(1.0, env_reward))` still fits φ. Root: a connector declares the shape of its
  observation keys (per panel where it varies), and the scorer compiles with the campaign's
  connector. **Re-test:** the clamp under harbor reads two-valued in `Scorer.two_valued`.
- **A constant paired side reads the `1/(4n)` floor, not the decided exact bound** (w11-scoring):
  `statistics.py::floored_se` is now the one constant-side rule (`_normal_posterior` and
  `difference_interval`'s graded side read it; the support bound is gone from differences), as the
  w11-scoring brief states decision 9. `briefs/decisions.md`'s 2026-11-27 header instead decides an
  exact bound on the share of cells where the pair disagrees (0/1 grades at n = 20: about ±0.17 for
  all ties, about [0.66, 1] for all wins), replacing the floor. Action: the operator names which;
  if the bound, it replaces `floored_se`'s body and the offline run names every moved pick.
  **Rides with:** area 04's chain. **Re-test:** `statistics.py` holds one constant-side rule, the one decided.
- **"Which benchmark a row measures" is spelled twice**: `BenchScope` partitions by benchmark under
  the word `RulerScope` owns. Action: `BenchScope` takes a benchmark name. **Held:** l4-runs owns
  `bench/line.py`. **Re-test:** `git grep -n "BenchScope" -- promptpotter` is empty.
- **A hard-sample page resolves its own config** (tw-q; narrowed by w11-scoring2, 11-28): the round
  close now hands `cycle_hard_samples` its config. The one `cycle_config` call left in
  `scoring/measurement_log.py` is the page's own resolve. Root: the view is handed what its caller
  resolved. **Rides with:** area 04's chain. **Re-test:** the hard-sample view resolves nothing.
- **An L4 panel averages θ over its cells' own rulers** (w11-scoring2, 11-28):
  `evidence/metric_catalogue.py::merge_cells` and the `promptpotter` connector's θ terms
  (`domain/l4/proxies.py::INNER_CELL_TERMS`: `origin`, `final_lift`, `peak_lift`) pool θ across inner
  cycles, though each cycle graduates 1PL or 2PL on its own ruler (`rasch.py::graduate_ruler_model`).
  Root: `InnerCellFacts` carries its series' `calibration_model`, and a θ-unit merge or formula read
  over cells of two models refuses. **Rides with:** area 10's chain. **Re-test:** a panel of one 1PL
  and one 2PL inner cell serves no merged `final_lift`.
- **`SampleIndex`'s fold row carries a `degraded` nobody reads** (w11-scoring2, 11-28):
  `intelligence/indexes/sample.py::SampleIndex._derive` still writes it after the stale-data ladder
  went. Root: the element leaves `SampleFoldRow.cells`, which wipes the persisted fold. **Rides with:**
  area 04's chain. **Re-test:** `git grep -n "_degraded" -- promptpotter` is empty.
- **An estimator spec every caller fixes** (w11-scoring2, 11-28): `ROUND_LIFT_SPEC` is passed at 12
  sites and no other spec exists. `read_arms` now owns its own, and the other sites still pass it in.
  Root: each reading owns its estimator. **Rides with:** area 04's chain. **Re-test:**
  `git grep -c "ROUND_LIFT_SPEC" -- promptpotter` names only its definition and its readers' own use.
- **Held re-tests need world legs** (w11-scoring2, 11-28):
  - The PoBB false-cut rate (RM592) needs an A/A leg beside capo-pobb, with identical arms and the
    cut rate read against ε.
  - The origin panel under `vote: overlap` needs a leg asserting training rows ∩ measured rows = ∅.
  - The edited-scorer refit needs a world resume after `world.family()` edits the formula.

  **Rides with:** area 14's chain. **Re-test:** each leg exists and reads its claim.
- **A freed slot waits on a poll** (flight-share, 11-18):
  `promptpotter/application/scoring/query_loop.py::_ScoringPhase` — `freed` polls at `HELD_POLL_S`
  while a run waits on another run's landing, so a freed slot can sit idle up to 0.25 s — the root:
  no wake signal crosses phases — **Rides with:** `area 04's chain` — **Re-test:**
  `git grep -n "HELD_POLL_S" -- promptpotter/application/scoring/query_loop.py` shows only sleeps,
  no event wait.
- **`Election.deciding_pair` is typed wider than it is** (w8-spine4, 11-27):
  `promptpotter/domain/results.py::Election` — `deciding_pair: PairedReading | None` though it is
  always a read pair, so `scoring/selection.py` asserts `pair.state is READ` — the root: the field
  is typed `ReadPair | None`, and the assert goes — **Rides with:** `area 04's chain` —
  **Re-test:** `git grep -n "a crown is decided on a read pair" -- promptpotter` is empty.

## 05 · Spend, wallet, quota

**Target:** One `Budget` at four positions (asked, admitted, standing, spent); spent is a fold of spend facts;
only the run's spend book halts a run; an L4 inner cycle spends under its root's book; a send is filed once,
on the run that sent it; the payer is resolved per cell by `wallet.py::sending_key`; spend is the provider's
bill.

**Unexamined:** `infrastructure/llm/pricing.py`, `jobs/shares.py`, `jobs/quota.py`, `spend_book.py`,
`domain/spend.py`

- **The default host pin is a trial with no verdict**: `domain/pipeline_overlay.py::pin_default_routes`
  writes each model's default host into every node config, and `bench/llm_call.py:231` pins the optimizer's
  own calls too. Decided: a model keeps its pin only where its
  realized cache share and effective $/M beat running uncached. **Rides with:** tune 3. **Re-test:**
  the `spend by node:` block at each twin's `readout.log` stop (`render/ansi.py::render_spend_breakdown`).
- **A nested spend book is wired beside the cell that nests it** (inner-1's report):
  `run_observers.py::RunObservers.arm_spend_book` reads none of `self`, so a fact repeats its
  `enclosed_as` line instead of calling it; `tests/factories.py::spend_book(enclosing=…)` nests a book a
  second way; `spend_book.py::_ENCLOSED = "backend"` is what a nested book falls back to where its
  spawner's pass names no kind. Root: `enter_inner_cell` nests the book (enclosing book and
  kind together, every spawning pass naming its kind). `_ENCLOSED` is also
  the hold tree's kind for a nested cell's hold with no resource (`SpendBook._filed`, `_out`), where
  an outer search's inner cell IS its backend. **Decided 2026-11-27:** the spawning pass names a nested
  cell's hold kind; an inner cell's is its connector's name, so no fallback is needed.
  **Re-test:** `factories.spend_book` takes no `enclosing`; the nested-replay fact calls the
  production nesting.
- **A landing that drops other accounts' run data refunds nobody** (w11-spend, 11-28). On 11-26 a
  deploy wiped a stranger's campaign, and the operator granted their coupon again by hand. The landing's
  run-data wipe (`store/data_root.py`) folds the dropped campaigns' charges nowhere. **Operator's
  choice, pending:**
  - (a) Refund and notify at the landing: release each dropped account's charges for those campaigns,
    and post the notice through `outbound.py`.
  - (b) Migrate their runs, which makes run data a SURVIVOR.

  **Rides with:** area 05's chain, once decided. **Re-test:** a landing that drops an account's campaign
  restores its coupon.
- **dbllmbench's cells send on the box's key read off `settings`, not `sending_key`** (w9-payer):
  `connectors/dbllmbench.py:182,205` hand the container `settings.<KEY>`, so an account's own key never
  pays a dbllmbench cell, and `holds_own_sends=False` is what holds its payer at the box. Root: the
  wallet's payer rule. Door: the container takes `sending_key(provider)`'s secret, it keys its own
  sends, and `cells_paid_by` reads it as a library's. Symptom under it:
  `infrastructure/backend.py::BackendClient.cells_paid_by` asks for the bound account even for a remote backend,
  which never reads the keys. **Re-test:** `git grep -n "api_key_attr).get_secret_value\|ANTHROPIC_API_KEY.get_secret_value" -- promptpotter/connectors`
  is empty. w11-connectors3 found the door blocked upstream: `llm/registry.py::providers()` holds no
  `anthropic`, so `host_key_attr("anthropic")` raises a `KeyError` and `wallet.py::cells_paid_by` would
  raise on an anthropic cell. Root: the wallet's providers include every provider a connector's container
  sends on, and the send is admitted in-module as `harbor.py::_installed_run` does.
- **A landed job's cycle is asked twice** (w11-jobs3, 11-28): `shares.py::_refuse_unshared` tests
  `other.hop is None` beside `Job.on_campaign`, which already implies it, only so the type narrows. Root:
  `Job.on_campaign` answers the landed hop. **Rides with:** area 05's chain. **Re-test:** no `hop is None`
  guard beside an `on_campaign` call.
- **A free account's refusal reads as a raw sentence** (w11-walk2, 11-28): on an auth-on offline world,
  a free account's `not_on_free_tier` refusal
  (`promptpotter/application/free_tier.py::outside_free_tier_refusal`) opens in lower case and spells
  both links as raw URLs. Without a box key, the same draft says only "No key for a provider", with its
  sentence behind a tap. Expected: a refusal written as a person reads it, its doors as links. Seen:
  a joined string. **Rides with:** `area 05's chain`. **Re-test:** on the walk world, a free account's
  off-tier pick reads one capitalised sentence per refusal, and both doors render as links.
- **A provider hold is filed twice under a nested run** (w11-spend2, 11-28):
  `infrastructure/llm/send_pacing.py`'s `_announce` appends a `ProviderHoldRecord` onto the sending ledger
  and again onto the root's. Now that the outer folds its inner ledgers (`read_model.py::enclosing`), the
  root's copy is a second filing of one fact. Root: it is filed once, on the sender, and the outer reads
  it through the fold. **Rides with:** area 05's chain. **Re-test:** one `ProviderHoldRecord` per hold
  across the L4 leg's ledgers.
- **An outer run's overshoot may read its own ledger alone** (w11-spend2, 11-28; unverified): the outer's
  overshoot check reads `LEDGER_SPEND` over its own ledger, not the `enclosing` fold its money lines now
  read, so an inner round's sends may not count toward it. **Rides with:** area 05's chain. **Re-test:**
  an outer whose inner rounds pass its ceiling halts at it on the L4 leg.

## 06 · Jobs, launch, commands

**Target:** Every loop start admits through one seam (`admission.py::request_jobs`); a full box queues; a
launch moves only by a `LAUNCH_MOVES` row through `JobRegistry.move`; an entry point hands data, never a
callable; every command is a `CommandRecord` filed by the sole `CommandDispatcher`; a job file is a survivor,
written under the gate.

**Unexamined:** `jobs/mint.py`, `commands/launching.py`, `commands/dispatcher.py`,
`jobs/launcher/head_to_head.py`, `jobs/reaper.py`, `store/command_log.py`, `domain/command_kinds.py`,
`jobs/launcher/admission.py`

- **An arm's admission is held twice** (w11-jobs3, 11-28): `jobs/launcher/head_to_head.py::AdmittedHeadToHead.admitted`
  pairs each `Opening` with its `HeldBudget`, while `Opening.share` already holds the share that budget
  names; `opening.py::Opening.land_cut` is a two-line wrapper with one caller (`ForkLaunch.stand`). Root:
  the opening carries what its admission granted, so the pair and the wrapper go. **Rides with:** area
  06's chain. **Re-test:** `git grep -c "tuple\[Opening, HeldBudget\]" -- promptpotter` is 0.
- **A check-in's cycle plan selects beside its landing's refusal**: `checkin.py::_land_draft` refuses
  each overlay before landing, then `resolve_cycle_plan` selects and refuses again; planning needs the
  landed `Session`. Action: a check-in plans its cycles off the draft before landing, and a campaign's
  cycle config carries its selection. **Held:** a `Session` over a draft-held `ResolvedDataset` (rk-rest
  cut it: `wiring.py::init_services`, `session_bank` and `dataset_experiment` read the landed
  definition off disk). **Re-test:** one `select_optimizer` call
  between a draft's Start and its first cycle plan.
- **A fork cannot narrow a dead param axis**: `ForkProposal.unlock_schema_field_rename` is the sole config
  a rebase applies. **Decided 2026-11-27:** a narrowed `param_allowed_values` is fork-only: one
  `ForkProposal` member mapped to one `ConfigOverrides` edit. **Rides with:** tune 1. **Re-test:**
  `git grep -n "class ForkProposal" -A20 -- promptpotter`.
- **A command's settle and a retry's wait re-read on a clock** (tw-m): `host_surface.py::_SETTLE_POLL_S`
  and `commands/dispatcher.py::_ACTING_POLL_S` each wait on a command end another process may file, and
  no signal carries it, so `test_security.py::_held_at_arms_gate` patches the first to 0. Root: a
  cross-process wake on the command log's end write (a lock handoff or a file watch). **Rides with:**
  area 06's chain. **Re-test:** neither constant exists and no test patches a poll.
- **A promoted launch's stderr is discarded until its cycle exists** (w10-jobs): `JobRegistry.promote`
  spawns its child with `log=None`, so a child dying before `held.py::spawn_job` opens the cycle's job log
  says nothing. Root: the child's output has a home from its spawn, named by its job. **Rides with:**
  area 06's chain. **Re-test:** `git grep -n "log=None" -- promptpotter/application/jobs` is empty.
- **A queued launch's ending files where an unasked one does** (w10-jobs): a released queued launch
  files at `jobs/refused/` though its ask carries its command id, and `commands/opening.py` stamps a fork
  child it then withdraws on `__exit__`. Root: a launch's ending is its command's, filed once by kind.
  **Rides with:** area 06's chain. **Re-test:** a released queued launch's ending is read off its
  command, and an opened-then-withdrawn fork leaves no child stamp.
- **Child-process facts idle the parent ~8 s** (tw-v, tw-v2): `conftest.py::dspy_child` (2.9 s), the
  API-start child (2.7 s) and the stdio host (2.4 s) run serially. Each peaks at 160–165 MB, so
  overlapping them breaks pytest's 435 MB cap. `-X importtime` finds no extra loaded by a boot that
  never calls it: `promptpotter.main` takes 2.6 s (`application/health.py` 0.82 s, `mcp` mounted at
  `/mcp` 0.64 s), and the CLI 0.45–0.68 s (`domain/command_kinds.py` 0.40–0.58 s). Door: fewer models
  built at import. **Rides with:** wave 6. **Re-test:** each child's wall is under 1.5 s.
- **A fork's preview does not serve the refusal its launch meets** (w11-walk, 11-27): on the walk world
  with no key, Steer & fork's "Confirm steered fork" is enabled and its press answers 422
  `no_api_key`; the cycle's own Start serves it (`admission.py::launch_preflight`), the fork preview's
  `steer` does not. Root: the preview asks `launch_preflight`'s send half of the fork's config
  (`mint_and_start.py::ForkLaunch.sends`). **Rides with:** `area 06's chain` — **Re-test:** on the walk
  world with no key, the fork preview serves `no_api_key` and Confirm is disabled.
  Walk #2 (11-28, 390×844): still. On `justlogic.d234`, tap C1.3, then Steer & fork. `fork-preview`
  answers `{"steer":"clean"}`, Confirm is enabled, and pressing it answers 422 `no_api_key`.
- **A launch released refused reads as Detached, Start still offered** (w11-walk, 11-27): Start on
  the auth-off offline world (key added) mints the cycle, its job is released refused
  (`jobs/refused/<id>.json`: `backend_unreachable`), and `GET /hierarchy/<campaign>::<cycle>` serves
  `run_phase` detached, `stop` null, `admission.refusal` null, offers `start`; the masthead says
  "Detached · 0 of 5 · pending — The pass this reading waits on has not finished". On the walk world
  "Run one round, then pause" shows "Starting" then falls back to "Rounds budget reached" with no word
  of the same refusal. Expected: the cycle says how its launch ended. Root: the cycle's run state
  (`promptpotter/domain/phases.py::ProducerReading`) reads the release's refusal as
  `promptpotter/application/command_reading.py::job_status` does; a state the system enters but does not
  report. One line with **A queued launch's ending files where an unasked one does**, whose root (a
  launch's ending is its command's) serves it. **Rides with:** `area 06's chain` — **Re-test:** after
  a refused launch the cycle's served state names the refusal's code.
  Walk #2 (11-28): still. On the cold world, a check-in's Start mints `email-tagging__ed682a`, and its
  job is released `backend_unreachable`. The cycle serves `detached` with `admission.refusal` null.
  Once the key is removed, the dock shows the next launch's `no_api_key`, never the refusal the last
  launch met.
- **The run dock says a launch's refusal as a send that happened** (w11-walk2, 11-28): on the walk
  world, open `justlogic.d234`, a finished run where nothing was pressed. Every view's run dock pins
  "No openrouter key is held by this account or this box, so nothing was sent. Add your own openrouter
  key …". That is the cycle's served `admission.refusal` for its next act (more rounds), worded by
  `promptpotter/shared/errors.py::NoApiKeyError`, the error a send raises. At 390×844 the pinned line
  takes about a sixth of the screen under a 4-row masthead. Expected: the refusal names the act it
  bars ("More rounds need an openrouter key"), in the dock's collapsed line. Seen: a past-tense send
  outcome on every view. Root: a preflight refusal carries its act's own sentence, and the send-time
  error keeps "nothing was sent". **Rides with:** `area 06's chain`. **Re-test:** the walk world's
  finished campaign shows no "nothing was sent" before any press.
- **A check-in offers Propose on a draft it would refuse, and a failed propose speaks the parser**
  (w11-walk2, 11-28): on the cold world, upload a 10-row CSV, then press "Propose it from the rows".
  - With a key held, the offline world answers 503 `resolver_failed`: "origin resolver turn failed:
    Optimizer prompt response failed Pydantic validation after 2 attempt(s): 1 errors".
    `application/commands/origin_resolving.py` wraps the exception's text
    (`infrastructure/llm/json_parse.py`).
  - With no key, the button is still offered, and pressing it answers 422 `no_api_key`. Start on the
    same draft is disabled with that refusal.

  Expected: Propose is disabled with the refusal the draft serves, and a failed turn says one operator
  sentence. Root: a paid act on a draft reads its refusal off the draft read, as Start does, and
  `resolver_failed` carries a typed cause, never the exception's text. **Rides with:** `area 06's
  chain`. **Re-test:** a keyless upload's Propose is disabled, and no served message holds "Pydantic".

## 07 · Config, pipeline, datasets

**Target:** Two seams: `resolve_dataset` answers which bytes, `pipeline_resolve.py` which values a campaign
runs, the second consuming the first. Params are one fold (`ParamLayers`); an optimizer's knobs are its nodes'
`config`; a default lives in the dataset's file, never code; one typed node document every door parses. A
dataset file is a partial laid on its chain (`dataset_access.py::definition_chain`), and the overlay is the
sole route for a backend tunable.

- **Params are not one fold** (w10-survey): `pipeline_resolve.py::bind_resolution` lays starting prompts
  through `pipeline_resolve.py::_apply_starting_prompts` after `ParamLayers.fold`, and
  `fold_output_contract` is a second post-fold at `pipeline_resolve.py::resolved_output_schemas`,
  `application/preflight.py` and `domain/opt_search_point.py`. Root: starting prompts and the output
  contract are `ParamLayer`s, so params are final once folded. **Rides with:** area 07's chain.
  **Re-test:** `git grep -c "fold_output_contract(" -- promptpotter` is 1 (the def; 4 now). w11-config3
  cut it: `fold_output_contract` also runs in `application/preflight.py::refuse_off_contract`'s loop, and
  `application/origin.py` calls the domain's `OptSearchPoint.configured`, so folding through `ParamLayers`
  moves `configured` into `application/` (domain cannot import `pipeline_resolve`). The chain's Owns hold
  `preflight.py`, `origin.py` and the candidate path's caller in `bench/node_context.py`.
- **A resolved dataset is resolved again past the seam** (w11-config): `datasets/loaders.py::resolve_bank`,
  `held_bank` and `fetch_bank` each re-read the definition (`definition_source`) a `ResolvedDataset` does not
  carry; `UploadBasis.connector` is a `str` that `pipeline_resolve.py::basis_source` looks up again. Root:
  the seam's answer (`ResolvedDataset` holding its read definition, `DraftSource`) is handed down, never a
  name. **Rides with:** area 07's chain, with area 02's (`dataset_access.py::ResolvedDataset`). **Re-test:**
  `git grep -c "definition_source(dataset)" -- promptpotter/application/datasets/loaders.py` is 0 (5 now).
- **A knob's provenance is diffed, not read off the fold** (w11-config): `config_map.py::_knob_rows` calls a
  bench knob `default` where its value equals `defaults.yaml`'s, so a campaign that states the default reads as
  unstated. Root: the fold stamps each key's layer (`layer_fold.py::Laid`) and the map reads the stamp,
  which needs the definition chain to stamp its `defaults.yaml` layer apart (`authored.py::_campaign_block`
  lays every file as `dataset`) and the mint to record a bench key's stamp as it does a node key's
  (`pipeline_resolve.py::LaidConfig`), a new mint field and so a wipe. **Rides with:** area 07's chain, with
  area 02's (`application/datasets/authored.py`, `store/dataset_access.py::DatasetDefinition`).
  **Re-test:** `git grep -c "campaign_defaults()" -- promptpotter/application/config_map.py` is 0 (2 now).
- **An origin finding with an unknown row is dropped unserved** (w11-config2, 11-28):
  `datasets/origin_resolve.py::raised_commands` drops through `_coerce` a finding whose row is unknown,
  whose evidence is empty or that names an absent header, and serves none of them, while a finding
  `EditDraftPatch` refuses is now served in `degraded_cause`. Root: a dropped finding is served with its
  reason, as a refused one is. **Rides with:** area 07's chain. **Re-test:** a finding naming an absent
  header reads in `degraded_cause`.
- **The node document is typed two ways and the L2 steer is a side memory**: every door now parses a
  patch through `layer_fold.py::fold_layers` (a null clears), but L2's steer is read through `L2Memory.steered` /
  `steered_layout` beside the fold, not as a named layer of `l1_generate`'s config, and about 58 of
  the engine's `dict[str, Any]` sites are node-config bags (`dict[str, NodeCall]` on `CycleStart`,
  `dict[str, dict[str, Any]]` on `ForkStart`, which carries a null clear and text a `NodeCall` refuses
  before the schema types it), and schema, parsing, overlay and knobs each hold a view of the document
  (6 modules define one: `potter/records.py`, `pipeline_overlay`, `pipeline_params`, `pipeline_parsing`,
  `pipeline_schema`, `search_point`; 22 name `NodeCall`, 31 carry the bag). The untyped edges show it:
  `datasets/prompts.py::pipeline_node_overlay` skips a non-dict node or config silently,
  `pipeline_parsing.py::_registered` answers `None` for a family no registry holds,
  `ManifestNode.optimizer` is a `dict[str, Any]`, `pipeline_schema.py::node_config_schema` takes optional
  `values` / `sources` that act as fallbacks, and `layer_fold.py::_laid` ignores a non-list SUBSET value.
  Action: one typed node document with knobs a view on it; the steer a named layer.
  **Rides with:** area 07's chain. w11-config2 counts 24 files naming `NodeCall`, and finds an edge
  of it: `datasets/ingest.py::_open_checkin_of` cannot tell an origin-opened draft from a plain pick
  (same `DatasetBasis`), so a plain pick may reopen it; the draft does not record its opener.
  w11-config3 typed the archive carrier (`NodeChain`) and cut the rest. `NodeConfig`'s typed readers
  are `bench/llm_call.py` (5: `provider`/`model` required, `temperature` a float), `optimizer_manifest.py`
  (5) and `free_tier.py` (1); the steer's readers are potter's `l1.py`, `members.py`,
  `dispatch/injections/layer_state.py` and `dispatch/prompts.py`, and its layer would ride
  `NodeContext.knobs`. The chain's Owns hold those. Two edges of it:
  `domain/pipeline_schema.py::PipelineSchema.sp_hash` answers `""` for a chain-less schema the archive
  now refuses (the refusal belongs at the schema's read), and `ArchiveConfig.config_key` is stored beside
  the `node_configs` it derives from.

## 08 · Connectors, backends, LLM clients

**Target:** Connectors are the only place backend identity is named; a built-in is one module binding
`CONNECTOR`; core never infers what a connector should declare; clients are `ProviderSpec` instances; provider
selection explicit.

**Unexamined:** none (w11-connectors read `connectors/harbor.py`, `connectors/dspy_module.py`,
`infrastructure/llm/openai_compat.py` and `infrastructure/llm/base.py` against the Target).

- **A remote backend's cells ask the bound account** (recovered): `infrastructure/backend.py::BackendClient.cells_paid_by`
  reads `bound_account` for a backend that sends on its own keys, harmless while every sending entry
  binds one. Root: the payer is asked only of a connector that `holds_own_sends` or is host-paid
  (`wallet.py::cells_paid_by`). **Rides with:** area 05's chain. **Re-test:** a remote cell scored under
  no bound account is admitted. w11-connectors3 cut it there: `cells_paid_by` takes `stores` eagerly,
  where it should read `bound_account` itself, only for a `holds_own_sends` connector, as `sending_key` does.
- **`Endpoint.setting` restates its env block's key** (w11-connectors3, 11-28): `connectors/protocol.py::Endpoint`
  names `"TERMNORM_ENDPOINTS"` beside `TermNormEnv.ENDPOINTS`. Root: the refusal derives the name from the
  block's prefix and field. **Rides with:** area 08's chain. **Re-test:** `git grep -c 'Endpoint("' --
  promptpotter/connectors` is 0.
- **The docker host's producer claim is a process global** (w11-connectors3, 11-28):
  `infrastructure/docker_host.py::_claimed` stands in for a producer that `producer_lock.py::_tokens` keys
  per `jobs_dir`, so two data roots in one process share one claim. Root: the claim is keyed by its jobs
  dir. **Rides with:** area 08's chain. **Re-test:** no module-level claim flag in `docker_host.py`.

- **TermNorm serves no rewrite field**: TermNorm's `GET /pipeline` `llm_only.prompt_info` lacks
  `rewrite_field`. Action (TermNorm repo): serve `rewrite_field: instruction`, then
  `connectors/termnorm.py`'s `prompt_info` template line goes. **Re-test:**
  `git grep -n "rewrite_field" -- promptpotter/connectors/termnorm.py` is empty.
- **PromptPotter does not read TermNorm's served wire contract**: the probe reads `/status` while
  TermNorm's `xconn` branch serves `GET /version` (`{revision, contract}`); `connectors/termnorm.py`
  probes `/status` and `::_EXPECTED_REVISION` is `None`, so a co-owned backend fix replays stale results;
  on the `xconn` branch `/status` loses its version word, so `_termnorm_version_check` reads None there.
  Action: the probe reads `/version` and refuses another contract; the revision folds into TermNorm's
  answer identity. **Rides with:** xconn 3, after TermNorm's `xconn` reaches the box. **Re-test:** a
  launch against a backend one contract behind fails at the probe, naming both numbers;
  `connectors/termnorm.py` probes `/version`. The probe half is built on `hold/w11-termnorm-version`
  (87e964e05, w11-connectors). It lands once TermNorm's master serves `GET /version`: until then it
  refuses every termnorm launch.
- **TermNorm words a warning's step into its message** (`research_pipeline.py`), though `StepWarning`
  carries the step as a field. Fixed only on TermNorm's `xconn` branch (11b1dbe); master (ce4f15b) and
  the box still word it (master `research_pipeline.py:278`). Action: the message is the fault alone.
  **Rides with:** xconn 4. **Re-test:** `git grep -n "failed: {" -- backend-api/api` in the TermNorm
  checkout, on master.
- **`infrastructure/llm/send_pacing.py::SendBudget` stops at the relay's door**: TermNorm's
  `_MAX_ATTEMPTS` retries on its own (terminus-2's `_query_llm` no longer does: `connectors/harbor.py::_AgentModel`
  raises a send the budget closed again, unsent). The remedy is the upstream redesign. **Rides
  with:** xconn 4. **Re-test:** the relay's retries, not one constant: on TermNorm's `xconn` branch
  `_MAX_ATTEMPTS` becomes per-node `max_attempts` (`pipeline.json:156,232,287,325`), so a grep of
  `_MAX_ATTEMPTS` reads closed after that merge while the relay still resends; count the relay's
  resends for one 5xx instead (`grep -n "_MAX_ATTEMPTS = " <termnorm>/backend-api/core/llm_providers.py`
  on master, `max_attempts` in `pipeline.json` on `xconn`).
- **The box keeps 21 dead producers' locks** (`jobs/producers/`), though one sweep reaps a dead
  producer's containers and then its lock (re-tested 2026-11-27 off a dead lock and a scripted
  `docker ps`; the w7-small symptom, a lock unlinked before the reaper reads it, holds no more). What
  keeps them is a sweep that never reaches `docker_host.py::reap_dead_producers` or raises in it: a
  raise in `application/jobs/reaper.py::sweep`'s earlier steps, or a `docker ps` the box refuses. **Rides with:** the
  next box read. **Re-test:** `machine-status`'s orphans step on the box names no fault, and
  `ls jobs/producers | wc -l` there falls to the live producers after one sweep.

## 09 · Optimizers (potter, capo, gepa, levi)

**Target:** Every optimizer is a manifest whose knobs are its nodes' `config`, registered in the one
entry-point registry, so a third party ships one with no edit here; its working state rides the round as
`optimizer_state`; potter's bad prompt is fixed in the dispatch hub; no fourth layer. The plugin contract
(`application/optimizer_contract.py`) sits below the bench; outside `optimizers/` only the landing's loader
imports an optimizer package.

- **A whole-candidate verdict books a placeholder axis** (w11-plugin census; w11-optim2 cut it to
  this): `potter/validators.py::_collapse` books the no-op, duplicate and repeat verdicts under axis
  `variant`, because `domain/failures.py::ValidationFailure`'s `axis` is a required `str`. Root: a
  whole-candidate verdict names no axis, so `axis` is optional (a scoring-tolerance reshape: run data
  wiped, L2's rendered failure JSON moves). **Rides with:** area 09's chain, with a domain/ owner.
  **Re-test:** `git grep -c 'axis="variant"' -- promptpotter` is 0.
- **The plugin contract is spread over seven modules** (w11-optim2, 11-28): `examples/optimizer-plugin`
  imports `optimizer_contract`, `bench/node_context`, `optimizers/paper_templates` (a built-in's),
  `domain/campaign_config`, `domain/paired_reading`, `domain/pipeline_schema` and `domain/records/decision`.
  A third party edits nothing under `application/optimizers/`, but it reaches seven homes. Root: the
  contract module owns what a plugin reads, so the example imports one module. **Rides with:** area 09's
  chain (map § 09 UNDECIDED). **Re-test:** the example's `promptpotter` imports name one module.
- **"wound" survives in potter's dispatch hub** (w11-optim2, 11-28): `domain/wounds.py` became
  `failures.py`, but `potter/dispatch/injections/wounds.py` and its `l1_wounds` injection keep the word.
  Renaming the injection key re-keys potter, a measured move. **Rides with:** area 09's chain. **Re-test:**
  `git grep -c wound -- promptpotter/application/optimizers` is 0.
- **CAPO's settled race path runs in no offline leg** (w9-pass; w11-optim2, 11-28):
  `capo/members.py::PairedTRace.cut`'s `settled` signal is reached by no leg, so a change to it moves no
  key. **Rides with:** area 09's chain, with area 14's offline legs. **Re-test:** `offline_run.py
  --coverage` reaches it.
- **"slot" survives in the text an LLM reads** (ren's report): prompt prose, `{{slot}}` tokens and the
  `not_a_slot` / `stable_slot` names keep the word ren retired in code (arm position, prompt field,
  region). Root: the optimizer prompts say what the code says; a prompt edit is a measured change,
  so it lands with its paid comparison. **Rides with:** tune 1. **Re-test:**
  `git grep -c "slot" -- 'promptpotter/assets/optimizers/*/pipeline.yaml'` is 0. "panel" likewise
  (small-3): the prompts' "panel" means only a set of cells, never a round's arms (`RoundOutcome.proposed`).
- **`l1_critique` on minimax runs a cluster's id list to `max_tokens`** (three sightings 2026-11-18,
  `finish=length`, healed by `clean_reask` at a full call's price): ANY cluster's `sample_ids`
  repeats ids without end (outer `908474` round 1 cycles 12 ids in cluster 0; inner `1544dc`'s
  cluster 2 re-lists cluster 0's 15). potter-w1 closed both prompt facts feeding it: a cluster now
  names `mechanism` and `claim` before `sample_ids` in the layout and `schemas.py::FailureCluster`'s
  order, and each id appears once in the whole reply. Open: the paid proof. **Re-test:** the banked
  inputs (`.scratch/wave-2/bank/row111/`) re-asked under `l1_critique/1` as it now renders finish `stop`.

## 10 · L4

**Target:** L4 is PromptPotter applied to itself through the `promptpotter` connector, one level deep; the law
is `domain/l4/`, the machinery `runner/inner/`; sandboxes a flat per-cycle registry; identity is a built-in's
behaviour key on the offline world, so a rename on a run line re-keys no cell. Goal (operator): early-round
AUC.

**Unexamined:** `runner/inner/tasks.py`, `domain/l4/proxies.py`, `runner/inner/spawn.py`

- **The paid L4 browser walk's stop was sized for one inner round** (reported):
  `research/datasets/promptpotter-self/e2e/dataset.yaml:30`'s `usd: 0.05` predates the depth-2 floor,
  and the same stop now pays the held-out seat's outer bench passes (the origin's, one per adoption),
  so `webapp/e2e/spend/l4.spec.ts` may halt on its ceiling. Action: size it from one measured depth-2
  run. **Rides with:** the next paid walk.
- **The outer's edit menu offers fields of inner nodes that never fire**: inner `l3_plan` made 0
  calls and `l2_context` 1 across six inner runs (L4 audit C, 2026-11-15), yet both stay editable.
  Action: a field is offered only where its node ran on the measured samples, for every campaign.
  A node ran where it holds a `step_timings` key
  (`scoring/evaluators.py`), which core stamps with the terminal alone for an in-process run
  (`infrastructure/backend.py`); an inner node's calls sit on its run's ledger only
  (`runner/inner/transcript.py`). **Decided 2026-11-27:** a node ran where its run's ledger holds a call
  of it: the ledger is the trace. **Re-test:** a C0 on `promptpotter-self/run` offers no
  `l3_plan` field until one inner run calls it.
- **No L4 fixture buys an outer cell ahead, so an L4 outer ending is unread** (tw-j link 1):
  `query_loop.py`'s endings skip `land()` when `phase.cancels`, the connector's `cancel_stops_billing`,
  which only the inner connector sets (`runner/inner/spawn.py::CONNECTOR`). So an inner cell that
  arrived, was filed under its claim and was cut may stay the outer campaign's own memory, with no
  `UntakenBoughtRecord`. tw-j's e2e L4 fixture discarded 0 outer cells at press depth 4 and at 6
  seats: the path is unreached, not refuted. **Rides with:** wave 6 (L4). **Re-test:** an L4 leg
  pressed past one outer cell discards one, and its ledger names it untaken.
- **The estimator has no golden corpus**: its key (`optimizer_contract.py::BehaviourKeys.cell_estimator`) reads the
  L4 leg's trace on the offline world, not its outputs on fixed inputs. A corpus would read each inner cell's `CycleResult` (~255 KB each) and the
  tenant ruler's response rows: over 1 MB, and its only source is account data on the box, which the
  operator may wipe. **Decided 2026-11-27:** the corpus is the offline world's L4 leg outputs, committed under 1 MB. **Re-test:** the
  estimator's entry in the behaviour-key file names its key's inputs.
- **An inner campaign's id is keyed by the instrument** (w9-trace): `runner/inner/spawn.py`'s
  `inner_campaign_id` hashes the cell key, which carries the held estimator, and an L4 outer request
  quotes it, so a run that rewrites `behaviour_keys.json` is not at its fixed point and the next reads
  `potter/l4` requests MOVED once more. Root: the id is keyed by the cell's content. **Rides with:**
  area 10's chain. **Re-test:** two offline runs in a row read `potter/l4` UNMOVED.

## 11 · Served reads, views, evidence

**Target:** A served GET mutates nothing and resolves in `application/`; a read is a `ReadKind` row plus its
params model; run state is derived, never a freshness guess; every score is served, never recomputed in the
consumer; the lineage tree is a read model that decides nothing; a value is its aggregate plus the live round,
never a copy of history.

**Unexamined:** `views/render/phase.py`, `render/export.py`,
`render/primitives.py`, `domain/narration.py`, `served_dashboard.py`, `hierarchy.py`, `hierarchy_subtree.py`

- **Head-to-head re-reads every pair's bench per GET** (w10-survey): `evidence/head_to_head.py::_read_apart`
  reads each pair of graded rows once per request (every guard looks its pairs up), still a full
  `bench/line.py::pair_on_bench` per pair per GET, and `_read` re-folds `standing_rounds` and the spend
  scan per campaign. Root: "the same bench set read alike" is a banked fact of two passes, served from the
  line's ledger: a record kind. **Rides with:** a lineage-fold chain after w11-records2.
  **Re-test:** `git grep -c "pair_on_bench" -- promptpotter/application/evidence/head_to_head.py` is 0 (1 now).
- **The live reads re-serve history per poll** (a hot-path pass, ✓ unless reported): `snapshot_frame`
  re-folds per connect, a ray page re-folds per "older", and the server folds and hashes every closed
  round per poll to decide `served_dashboard.py::ServedDashboard.series_held` (the wire half is closed:
  a held series re-sends no closed round). `_cycle_cells` and `_campaign_edits` read each walk once
  (`scoring/closed_rounds.py::LineCells`). Root: a value is its aggregate plus the live round, never a
  copy of its history. **Re-test:** a poll naming the series it holds reads no closed round.
- **The lineage subtree sorts every candidate per candidate**: `application/hierarchy_subtree.py::earlier_measured`,
  O(n² log n) per read; reported beside it, the subtree read builds the whole top family per request,
  and `_descent` compares O(n²) pairs with `SequenceMatcher` behind an lru of 4096 that thrashes from
  ~90 candidates (`hierarchy_subtree.py:242-318`). Action: "closer to" and descent are folded once per
  candidate on the ledger: a record kind. **Rides with:** a lineage-fold chain of its own. **Re-test:** `git grep -n "def earlier_measured" -- promptpotter/application/hierarchy_subtree.py` is empty.
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
  **Re-test:** the exhibit's hierarchy ETag holds while the host edits a draft. w11-reads (11-28): this
  row is the root of the two above it. `exhibit.py::read_stores` hands the follower the host's
  `Stores`, so the feed, the session's shape and the follow check are each spelled per reader. The
  narrowed `Stores` lives in `infrastructure/store/stores.py`, and the three rows ride it (areas 13
  and 02).
- **`cycle_reads.py` copies the live round's candidates into a second shape** (w7-line):
  `application/cycle_reads.py:136` copies `current_round.candidates` whole. Door: its reader takes
  the served line. **Re-test:** no copy of `current_round.candidates` outside the fold. Blocked
  (w7-rows2): `LineArm` lacks `prompts`, `resolved_params`, `validation_failures` and
  `evidence_grounding`, which `ServedArm` carries, so the line carries `ServedArm` first. w8-web: the
  closed line is banked as `DashboardCandidate` with no prompts, so serving `ServedArm` there banks
  every arm's prompts. The door is the round read serving its unfiled arms from the fold's own method.
- **A wiped campaign answers two failure kinds**: after the landing, a still-open tab asked
  `/campaigns/<wiped>/pipeline` (400) beside its `/hierarchy` and `/dashboard` (404). I7's GONE
  stops on a 404 alone, so that read keeps asking. Door: an absent campaign is a 404 at every read.
  **Held:** unreproduced at `2c7dff34e` (w7-small: `/campaigns/gone/pipeline` answers 404 with and
  without `at=`, over every hop shape); if the 400 recurs, capture the exact `at` the stale tab sent.
  **Re-test:** each campaign read of a deleted campaign answers 404.
- **Readout words every look-ahead press as expiring** (readings-4/flight-ux, 11-18): `promptpotter/application/views/readout.py::_handle_sample_started`
  — an every-round depth is narrated "(armed — expires when this round finishes scoring)" because
  `SampleStartedRecord` carries `sample_lookahead`/`sample_abreast` but no `auto` — the record
  should carry whether the press expires, so the narration reads a fact — **Rides with:** `area 11's
  chain` — **Re-test:** `git grep -n "expires when this round" -- promptpotter`; still one hit means
  it stands.
- **The node surface re-words what `domain/` serves** (w11-web3 census, 11-28): `MeasurementRun.tsx`
  hand-writes "rejected" and its teaching sentences for an `invalid` arm, beside the served
  `domain/arms.py::ARM_END_INFO`; `SchemaTreeEditor.tsx::objectOf` forces `required` on every field and
  `additionalProperties: false` over a local closed `TYPES`, beside `bench/llm_call.py::wire_closed`, which
  closes every schema at send. Root: the browser renders `ARM_END_INFO.invalid`, the served param says
  the editable types, and the server closes the schema. **Rides with:** area 11's chain. **Re-test:**
  `git grep -ciE "rejected|additionalProperties: false" -- webapp/components/node-surface` is 0.

## 12 · Surfaces: CLI, REST API, MCP, webapp

**Target:** Five entry points share one orchestration layer; no business logic in an adapter; a CLI verb is
one `Verb` row; every write goes through `CommandDispatcher.dispatch`; everything on stdout is also on disk;
the served contract is generated and the browser derives nothing; mobile first.

- **The host door's import closure loads the LLM client** (w11-web3 census, 11-28):
  `application/host_surface.py` imports `bench/task_context.py::checkin_template`, which imports
  `bench/llm_call.py`, so the stdio host fact in `tests/test_security.py` runs 2.05–2.44 s against the 2 s
  budget. The template reads only `optimizer_manifest.py`. Root: `checkin_template` moves into
  `optimizer_manifest.py`, with no lazy import. **Rides with:** area 12's chain. **Re-test:**
  `git grep -c "bench.task_context" -- promptpotter/application/host_surface.py` is 0.
- **A locked act's reason is written in the browser** (w11-web3 census, 11-28):
  `RunLimitsControl.tsx:190` and `AccountPreferencesTab.tsx:34` both spell "not yours to set", because
  `lib/hooks/useFiles.ts::useFiles` answers only a boolean from `filable`. Root: the served filable answer
  carries its refusal as a `Said` row, and `useFiles` returns it. **Rides with:** area 12's chain.
  **Re-test:** `git grep -c "not yours to set" -- webapp/components` is 0.
- **No walk spec opens the remote panel** (w11-web3 census, 11-28): `RemoteControl.tsx:31` mounts
  `RemotePanel` (provenance, `RunLimitsControl`) only when open, and no file under `webapp/e2e` names the
  remote control, so the panel's render against a served cycle rides RTL tests alone. Root: a walk spec
  opens the remote on a served run and reads its limits. **Rides with:** area 12's chain. **Re-test:**
  `git grep -c "Campaign remote control" -- webapp/e2e/walk` is at least 1.
- **The CLI's welcome and the archive modes are spelled beside their rows** (w11-web3 census, 11-28):
  `cli/main.py::main` hand-writes five verbs and their help beside the `VERBS` rows that carry it;
  `cli/main.py`'s `compact-archive` choices restate `command_kinds.py::ArchiveMode`, and
  `cli/commands/writes.py::_render` and `ArchiveCompactionControl.tsx`'s `DELETES` each re-spell
  `("purge-cold", "reclaim")` as the irreversible pair. Root: the welcome draws from `VERBS` (a `Verb` flag
  names the listed ones), `ArchiveModeInfo` serves `irreversible`, and the choices come from
  `get_args(ArchiveMode)`. **Rides with:** area 12's chain. **Re-test:**
  `git grep -cE '"purge-cold",? "reclaim"' -- promptpotter webapp/components` is 1 (4 now).
- **A refused Ctrl+C pause is swallowed** (w11-web3 census, 11-28): `cli/commands/launch.py`'s
  `ctrl_c_pauses._pause_each` and `ctrl_c_stops_pass._stop` wrap the dispatch in
  `contextlib.suppress(PotterError)`, so a refused pause or cancel says nothing while the run goes on;
  `_ctrl_c.send` in the same file says its refusal. Root: both go through `send`, said with its
  `next_acts`. **Rides with:** area 12's chain. **Re-test:** `git grep -c "suppress(PotterError)" --
  promptpotter/presentation/cli/commands/launch.py` is 0 (2 now).
- **A terminal launch's start-up summary is said on stderr only** (w11-web3 census, 11-28):
  `cli/commands/launch.py::_run_origin` and `_resume` say the reached backend URL, the dataset and its query count,
  the node list and the minted campaign through `_checkin_line` and `log_startup_summary`, a logger on
  stderr; no `readout.log` holds them and no other door says them. Root: the readout's header renders
  them, and the CLI hands only its line sink. **Rides with:** area 12's chain. **Re-test:**
  `git grep -cE "_checkin_line\(|log_startup_summary\(" -- promptpotter/presentation/cli/commands/launch.py`
  is 0 (14 now).
- **The server's adapter words health and shutdown itself** (w11-web3 census, 11-28):
  `promptpotter/main.py::health_check` builds the stale-bundle `Said` row beside `health.py::WEBAPP_BUNDLE`,
  and `promptpotter/main.py::_telegram_shutdown_notice` decides which jobs are this producer's and words the notice. Root:
  `health.py` serves the bundle row and `notices.py` owns the notice. **Rides with:** area 12's chain.
  **Re-test:** `git grep -cE "This page is out of date|stopped with" -- promptpotter/main.py` is 0.
- **The admin bot keeps its own verb table, grammar and spend sums** (w11-web3 census, 11-28):
  `admin_bot.py::handle_command` is a 13-branch if-chain with `_USAGE`, parsing each kind by hand
  (`_set_coupon` spells unbounded `-` where the CLI spells `unbounded`), and `_render_install_spend`
  totals and flags the install's spend outside `application/views/`. Root: the bot's commands derive from
  `CommandKind` rows and their payload spellings as `cli/main.py::command_verb` does, and
  `jobs/install_spend.py` serves the totals the views render. **Rides with:** area 12's chain.
  **Re-test:** `git grep -cE "if command (==|in)|def _render" -- promptpotter/presentation/admin_bot.py` is 0.
- **The maintenance verbs' renderers live in the CLI adapter** (w11-web3 census, 11-28):
  `cli/commands/writes.py::_render_summary`, `_render_landing`, `_render` and `_render_inventory` format
  the reset, landing and archive reports in `cli/`, where every renderer is `application/views/`'s. Root:
  they move to `application/views/render`, and the verbs hand back the text. **Rides with:** area 12's
  chain. **Re-test:** `git grep -c "def _render" -- promptpotter/presentation/cli` is 0 (4 now).
- **Adapters over a flat served shape** (webb-5's): `useCandidatesModel.ts` joins about twelve sources;
  `.facts.` is opened at many sites; liveness is re-derived at each `phaseWalks(` site. Action: the
  served rows carry what the adapters join. **Rides with:** webd. **Re-test:**
  `git grep -c "\.facts\." -- webapp/components webapp/lib`. w11-web3: `HardSamplesPreview.tsx:27`
  re-checks `walk.ids.length > 0`, though `served_dashboard.py::_walk` serves `None` when no arm walks.
- **The browser recomputes engine facts**: `webapp/lib/derivations/` (~2.2k lines, plus tests) derives
  node config, spend, run phase and the search point the engine already computes. Action: each fact is
  served on its existing read and the TypeScript copy and its tests go; so do `wonOnTheta` (9 hits in 5 files; its filed row and `electedMetric` went, ec96b1cda) and
  `ConfigMode` (`lib/derivations/node-config.ts:11`; no row files it). So does `run-phase.ts:51`'s copy
  of a cycle's acts, which drops only `start-run` where `phases.py:447` drops every act of a refused
  admission (narr-2's review): the acts are served once faults 1 serves `next_acts`. **Rides with:** webd. **Re-test:**
  `git ls-files webapp/lib/derivations | grep -v __tests__ | xargs wc -l` falls by the served facts.
  w11-web3: `candidate-search-point.ts::nodeConfigs` still filters non-dict nodes out of
  `resolved_params`, and `node-config.ts::knobGroups` joins `knobs` to `node_config_schema` by key.
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
  hand-written builder of a served type. w11-web3: `press-subject.test.tsx:36` still writes
  `bench: {...} as never`.
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
- **The MCP door's claude.ai connector is unproven on the box**: the door serves Streamable HTTP at
  `/mcp/` on the account's bearer (`promptpotter/presentation/mcp/server.py::HttpDoor`). Action: the
  operator's step after the box update, a connector with the token as its `Authorization` request
  header. **Rides with:** the next box update. **Re-test:** in claude.ai, a custom connector at the
  box's MCP URL answers `tools/list`.
- **Unverified, for the next change to each file**: `shell/CriticalAlertBanner` builds `Said` rows in the
  browser from `BACKEND_REACHABILITY_INFO` and orders them by `TONES`;
  `cycle_files.list_cycle_files` walks all of `.runtime/` and filters after; a `/hierarchy` 200
  replaces every entry object `lib/hooks/useRegistry.ts::useRegistry` indexes. The anti-rot pass
  re-finds them. **Rides with:** rel 3.
- **The served contract is generated and the browser derives nothing (root E)**: `webapp/lib/derivations`
  holds 44 files and 3,827 lines, about 1,780 of tests; `webapp/lib/test-fixtures.ts` 666 lines;
  `.facts.` 121/47 over `webapp/`. **Closes:** **The browser inks a served state from its own map**,
  **Adapters over a flat served shape**, **The browser recomputes engine facts**,
  **A hand-kept mirror of the served shapes**, **Served fields the browser never reads**. About 4,500
  TypeScript lines go. **Run data:** none. **Size:** large, a link per served fact. **Rides with:** webd.
  **Re-test:** `git ls-files webapp/lib/derivations | grep -v __tests__ | xargs wc -l` falls by the served facts.
- **The Candidates card derives what its reads should serve** (w11-web census, 11-27):
  `webapp/components/candidates/useCandidatesModel.ts` takes `divergentRound` as a min over views
  (:103), counts replayed cells that `CandidatesToolbar.tsx:152` shows "of" `views.length` (:170), and
  sets the in-flight arms from `openCells` (:122); `ApplyScenarioPanel.tsx:30` falls back to
  `history.length` for the fork round; `SampleSetControl.tsx:37,47` derives the measuring rounds and
  `everywhere`; `lineage-tree.ts:89` (`standingElection`) re-decides a candidate's standing from its
  election at three sites. Action: the dashboard and subtree reads serve the fork round, the replay
  count, the in-flight arms, per-sample coverage and the standing; the browser copies go. **Rides with:**
  root E (webd). **Re-test:** `git grep -cE "standingElection|divergentRound" -- webapp/components/candidates` is 0.
- **The Candidates charts re-decide what their reads serve** (w11-web3 census, 11-28):
  `FitnessChart.tsx::shortPanels` tests `scored < expected` beside the served `arm_reading.py::ArmSamples.cut`,
  and the two rules disagree on a proposed arm; `Forest.tsx` counts a lane's rounds beside the served
  `rounds` figure, hand-words the `trigger` and `direction` sets and prints "undefined" on a missing
  `state` figure; `useCandidateBars.ts` finds `dash.overlap_line_round` in `rounds[]`, and its `?? null`
  reads a missing round as no overlap. Root: each reads its served field (`samples.cut`, the figures and
  `Worded` spawn rows, an overlap `panel_reading` on `dashboard.json`). **Rides with:** root E (webd).
  **Re-test:** `git grep -cE "p\.scored < p\.expected|roundCount|r\.round === lineRoundNum" --
  webapp/components` is 0.
- **The node surface decides a run's chips and a snippet's version itself** (w11-web3 census, 11-28):
  `NodeDetail.tsx` picks the model chip from the untyped `block.config["model"]` over `block.model`, adds
  `usage.input + usage.output` and hand-words "not reported"; `PromptFieldsEditor.tsx::bankEdits` matches
  typed text to library snippets (`segmentPrompt`, `snippetSupersedes`) and files one `bank-snippet` per
  edit in a loop. Root: the audit read serves the asked model, the total and the word, and the
  prompt-edit command banks its superseding versions on the server. **Rides with:** root E (webd).
  **Re-test:** `git grep -cE 'config\["model"\]|usage\.input \+ usage\.output|segmentPrompt\(' --
  webapp/components` is 0.
- **Candidates and node-surface keep words and fallbacks for served sets** (w11-web census, 11-27):
  `SnippetLibrary.tsx:40`'s `FACET_LABEL` Record, `lineage-tree.ts`'s hand-written link-note words,
  and `FitnessRankSummary.tsx:37`'s `?? PLACEHOLDER` on an index miss, because the served `lens_shift`
  holds addresses, not labels. Action: the words are `Worded` rows; a lens shift serves its labels.
  **Rides with:** area 11's chain. **Re-test:** `git grep -cE "FACET_LABEL|\?\? PLACEHOLDER" -- webapp/components` is 0.
- **The node surface diffs config in the browser** (I9; w11-web census, 11-27):
  `NodeConfigEditor.tsx:256` (`axisMenu`) marks values inert or user-added by a client diff and :110
  derives `nodeLocked`; `NodeSurface.tsx:245` counts tunables; `L1Variants.tsx:62` casts an untyped
  value to a hand-declared `Variant[]`. Action: `/pipeline?at=` serves each flag and count, and the
  variant shape is generated. **Rides with:** area 11's chain. **Re-test:** `git grep -cE "axisMenu|as Variant" -- webapp/components` is 0.

## 13 · Identity, accounts, access

**Target:** OIDC verified at the API trust boundary, tokens never past it; privileged identity changes ride
the operator-admin channel; a cross-user campaign is a 404; who an account is (user, API keys, tokens) is a
survivor.

**Unexamined:** none (w10-identity read `application/account.py`, `app_visits.py` and
`infrastructure/identity/` against the Target).

- **An operator notice with no account behind it leaves no readable outcome** (w11-access): a sign-up's
  notice files its outcome on the new account's trail (`account.py::record_operator_notice`), but
  `browser_sign_in.py::ring`'s refusal lines (a sign-up refused at the ceiling, a sign-in that could
  not be recorded) and `notices.py`'s health and landing notices drop what `notify_operator` returns,
  so a skipped or refused one is a log line alone. Root: a box-level notice log (one row per notice:
  its text, outcome and time) that the admin bot's `/trail` or `health` reads, every caller filing
  through it. **Rides with:** area 13's next chain. **Re-test:** with no admin bot configured, a
  sign-up refused `accounts_full` leaves a `skipped` outcome an operator read serves.
- **An account cannot find how to delete itself** (w11-walk2, 11-28): on an auth-on world, signed in
  as a non-host account, Account → Profile offers Sign out and nothing else. `delete-account` is
  `promptpotter/domain/command_kinds.py::_HOST_ADMINISTERS` ("the host administers from its terminal
  or its bot"), and no surface tells the account so. Expected: Profile says who deletes an account and
  how to ask, from the served press's reason. Erasure on request is a user's right (GDPR/FADP). Seen:
  silence. **Held:** whether an account may file its own deletion is an operator decision. Until it
  is made, the served reason renders. **Re-test:** Profile names the door to deletion.
- **An empty visits digest says counting began at its own minute** (w11-walk2, 11-28): with no
  outside request kept, `promptpotter/application/app_visits.py::visits_digest` takes `began` from the
  records it is handed, so with none, `began` = `now`. It reads "People per day, oldest to today: — ·
  — · — · — · — · — · — (counting began Sat 28 Nov 02:56)", where today reads "—" (unknown), not 0.
  Expected: today reads 0, and "counting began" is the store's first day. Root: the digest reads when
  the visit store began counting, never the earliest record. **Rides with:** `area 13's chain`.
  **Re-test:** `visits_digest([], day=…, now=…)` reads today as 0.

## 14 · Gate, offline world, tests

**Target:** A fact is proven through a door of the offline world, never a hand-seated record; a guard at one
choke point is tested once; the behaviour keys name decisions; the gate answers from what changed and fits its
budgets.

**Unexamined:** none (w10-tests read each against the Target; what it found is a row below).

- **The paper harness tracks no named tree** (small-1's landing): `paper/bbeh-comparison/bbeh_potter_runner.py:1`
  pins `promptpotter==0.8.20` while the ledger fixes it against the current API. Action: the
  harness names the release it runs against, and a fix to it targets that release. **Rides with:** the
  next chain touching `paper/bbeh-comparison/`.
- **Two wounded legs move under load** (narr-3's landing): `offline_run.py --against` read
  gepa/wounded and levi/wounded MOVED on `decisions.ledger.round_warnings` while `gate.py --changed`
  ran its pytest beside it on 16 cores, and 20/20 UNMOVED alone on the same tree. A round warning in
  a scripted world depends on wall time or scheduling. Root: the offline world's clock and order are
  its own, so no decision or warning reads the desk's load. **Rides with:** tests-w. **Re-test:** the
  proof run beside a busy pytest reads every leg UNMOVED (keep its `eq_*` home to diff the warning).
- **Smells the test-shape split left** (reported by its builders): w7-tests opened the peer facts
  through real run init, and the pytest wall went from 77.8 s to 87 s; w7-tests2 shares the bench
  line (`tests/test_scoring.py::banked_line`) and binds each fact's account in
  `tests/conftest.py::own_home`, buying back about half (65.8 s on 4-core Linux at w10-tests). Left: a
  CAPO round on 16 rows walks in ~0.7 s, a tenth of it one `fsync` of `charges.jsonl` per send (the
  budget-cut table's walk is in **A rule the suite reaches only through a round…**). Action: a round costs what its cells cost, found in the
  run's write path, never by the test. **Rides with:** the chain owning `application/runner/`.
  **Re-test:** `pytest --durations=10` lists no peer fact over 2 s.
- **One guard is tested once per door**: tests grew +2.3k lines in three days, mostly security and
  identity, a test per door of one guard; the test-shape split (landing 43) kept every assert. Action:
  a guard living at one choke point is tested once, over the table of its doors; a guard spelled at
  more than one door is a row for the chain owning it. **Rides with:** tests-w's successor.
- **The capo offline bench turns on one seed**: lift +0.700 or none after a draw-order change. Action: it
  reads two seeds. **Rides with:** ts-score 3.
- **`tests/test_spend.py` holds the walk's tests**: the hand-driven walk went to the `skipped` legs
  (w8-tests3); the inner stop still patches `execute_round`. Root: it becomes an offline leg
  (`run_one`'s knobs) or a rule exposed as a pure function (`tests/CLAUDE.md` § Three tiers).
  **Re-test:** no test in `tests/test_spend.py` patches `execute_round`.
- **An operator's act is atomic in the offline world** (tw-j link 2): `offline_world.py::_acted` runs
  a press or a pause whole while the run's loop waits, so its legs repeat, and no leg lands a cell
  between a command's own steps, where tw-r2 and tw-r3 found their bugs. Root: the world steps the
  command and picks the cell that lands between two steps, by an event, never the clock. **Rides
  with:** wave 6. **Re-test:** a leg lands a cell between a pause's two steps, on every run.
- **No door yields a dataset with a moved panel** (tw-x row 7; the capability half closed at
  w11-tests2): `tests/test_identity.py::test_a_moved_panel_is_banked_as_moved_while_the_measured_roster_stands`
  sets the workload's panel before `Session.record_resolved_run`, since no world connector owns an
  unpinned panel (L4's is pinned) and a moved roster is a moved bank, so the resume refuses on the
  origin before run init restates it. Door: `world.campaign` on an owning connector whose panel
  moves without its rows. **Rides with:** area 14's chain. **Re-test:** the fact sets no workload.
- **A knob layer's tiers have no world example** (tw-x row 10): the knob-layer fact reads only
  "campaign"-level layers; dataset vs free_tier needs a `campaign(knobs=)` layer per tier. **Rides
  with:** wave 6. **Re-test:** a knob fact names a layer of each tier.
- **19 parked facts wait for a world door** (tw-j2; each is kept as a test until its door exists):
  - test_identity I2: a dataset with two prompt nodes.
  - I3, I4: a parent on fewer rows than its frontier.
  - I6: an L4 origin past 16k chars.
  - mixed ×2: an L4 leg mixing potter and capo.
  - test_scoring S3, S6, S13: a leg that binds a budget (backend cells cost $0).
  - S8, S14: a pause during a scoring pass's bench pass (the cycle reading as scoring), and a
    refusal after it; the `bench-paused` leg pauses the run's own end pass.
  - S9, S10, S12: the `replicated` leg with a row gone from its bank, and a replicate that stops or
    raises.
  - S17, S18: the `every-adoption` leg with a member whose point moved, and reading its set-aside.
  - S19: an `arm_gate=author` leg.
  - S20: a GEPA round proposing more than one arm.
  - S25: a world LLM naming `stand_on`.
  - test_spend's inner-stop: an L4 leg whose inner run rebases and whose provider refuses credit.
  - The catch-up fact deleted at `af224d3bd` line 1222: a pause while a prior's catch-up is out past
    the sure rows.
  - The priced-replay facts at lines 1324 and 1349: a Compare leg paused mid-walk and resumed
    (`search_incurred`). Mutating `claim_price` leaves every leg green.

  **Rides with:** wave 6. **Re-test:** each fact moves to a leg on its door, or is deleted by a named
  mutation.
- **The suite runs ~3.6 s over `7dce00a96`, all of it test_archive's world runs** (tw-v, tw-v2): in one
  session, `7dce00a96` 62.0–67.0 s and tw-v's tip 65.2–67.9 s. `test_archive.py` takes 16.4–19.4 s
  against 3.4–3.5 s hand-seated. `line`, `displaced`, `family_tree`, `origin_forks` and `gated` have
  distinct forks, so no shape merges. Operator, 2026-11-26: close it once the indirections are removed,
  never by adding complexity for seconds. Door: a cheaper run, not fewer runs. **Rides with:** after
  wave 6. **Re-test:** archive's file wall is under 8 s, and the suite's is below `7dce00a96`'s.
- **Vitest's start meets load from outside the gate lock** (w7-gate, built, Windows re-test owed):
  inside a gate it starts beside no check (`gate.py::Check`'s `started`), and `npm test` outside one
  queues behind the clone's lock (`scripts/queue_then.py`) rather than sharing its hard-coded 90 s
  worker start; five looping builders plus one full gate reaped none on a 4-core Linux box (127
  runs). **Re-test:** five looping builders plus one gate on the Windows desk reap none.
- **capo's skip twin moved once on the Windows desk** (w8-tests3's leg, 2026-11-27): the full gate
  on 6b3f691b3 read "a skip stands at [3] cells, its twin paused inside round 1 and resumed, skip,
  decisions and cells MOVED" with every count equal; `--only offline-run` on the same tree was green.
  A green rerun is a gate or desk defect, never a pass: a twin's pause point or its replay depends on
  something the leg does not pin. **Re-test:** five desk runs of the leg read UNMOVED.
- **No door holds a world cycle mid-walk, a refused inner rebase or a raised mid-run ceiling**
  (w11-tests2). Each is hand-seated:
  - A run phase seated `state=Running()`, 9 times: `factories.walking_cycle`, test_security,
    test_scoring and test_spend.
  - test_spend's `execute_round` patch: no offline leg drives an L4 inner run to a refused rebase,
    because potter declines inner forks with no θ in the sandbox.
  - test_spend's mid-run ceiling, through `StandingBudgetRecord` and `hold_moved`: set-limits clamps
    to the account allowance.

  Root: the world holds a cycle mid-walk. The inner stop rule becomes a pure function in
  `application/runner/inner/spawn.py`, and a ceiling raise has a door. **Rides with:** area 14's chain,
  with w11-runner (the inner stop rule). **Re-test:** `git grep -c "state=Running()" -- tests` is 0,
  and test_spend patches no `execute_round`.
- **A torn-bill fact takes 3.08 s** (w11-tests2):
  `test_a_ledger_index_read_in_a_race_with_its_writer_sees_no_torn_bill` runs over the 2 s budget.
  **Rides with:** area 14's chain. **Re-test:** `pytest --durations=10` does not list it.
- **No offline leg draws a mixed-benchmark round** (draw-strata, 11-18):
  `scripts/offline_run.py::run_l4` — the L4 leg (`L4_DATASET = "promptpotter-self/e2e"`) seats one
  search and one held-out cell on one benchmark, so `build_round_order`'s interleave and
  `origin_panel`'s per-benchmark seats are proved by pytest alone and a regression moves no leg — the
  e2e L4 panel seats two benchmarks — **Rides with:** `area 14's chain` — **Re-test:** `potter/l4`
  moves against a base that draws by δ alone. (item 541)
- **A shipped-data scan lives in test_identity** (tw-3, 11-25):
  `tests/test_identity.py::test_every_committed_datasets_framing_reads_within_its_budget` — a repo-wide
  scan of shipped data run as a test — the check runs at import in the registry that owns it
  (`application/CLAUDE.md` § Registries complete at import), then the test goes — **Rides with:**
  `area 14's chain` — **Re-test:** the test is gone and an over-budget framing refuses at import.
  (item 200)
- **A rule the suite reaches only through a round is decided where no table can reach it** (w10-tests
  link 1): the slowest `offline` facts walk a campaign to reach one decision, e.g.
  `tests/test_scoring.py::test_a_round_the_budget_cuts_elects_only_past_the_panel_gate_and_stands_once_closed`
  (2.58 s, a CAPO campaign per case, `execute_round` / `post_round` driven by hand) and
  `tests/test_scoring.py::test_a_round_file_the_disk_refuses_is_one_warning_and_the_next_round_files_its_own`
  (1.55 s); the cut's election is now `runner/round.py::panel_gate_on`, with its table, so the first
  fact's walk is the tests chain's to delete; the round file's refusal (`scoring/closed_rounds.py`) and
  the formula re-read expose no pure function, and test_scoring's
  hand-driven rounds (8 lines, 4681–5421 at `42bd9ca42`) are the same root. Root: each rule becomes a
  pure function in the module that decides it (`scoring/row_policy.py::send_rows` is the model), its
  facts a table, the walk left to a leg. **Rides with:** the chain owning `application/runner/` beside
  area 14's chain. **Re-test:** `pytest --durations=10` lists no `offline` fact over 1 s on Linux, and
  `git grep -c "execute_round(" -- tests/test_scoring.py` is 0.

