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

**Unexamined:** `domain/records/run_records.py`, `domain/results_health.py`, `domain/arm_reading.py`,
`domain/wire_record.py`, `domain/export.py`, `domain/candidate_diff.py`, `shared/errors.py`

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
  - bench ↔ the plugin contract: bench reads `optimizer_contract.py`'s `CycleRead`, member
    protocols and `NODE_MEMBERS`, which name the bench's `NodeContext` and `RoundContext`. Moving
    `CycleRead` alone re-formed runtime cycles.

  Action: each knot is cut at its root, held by `complexity_ledger.py::_type_cycles`. **Rides with:**
  layers. **Re-test:** the `type_cycles` row of `BASELINE` falls to 0.
- **Names collide across layers**: two modules each named `head_to_head.py`, `hierarchy.py`,
  `bench.py`, `power.py`, `wounds.py` and `cells.py`; `ledger_scan.py:176 CycleFacts` beside
  `evidence/read.py:360 _CycleFacts`. Root: a name is one thing; each pair renames the one that stopped
  describing its contents, with its doc and `CLAUDE.md` row. **Re-test:**
  `git ls-files promptpotter | xargs -n1 basename | sort | uniq -d | grep -v __init__` loses the six.
- **`run_records.py` is a module of 38 records and ~35 non-records**: `domain/records/run_records.py`
  declares 38 `CycleRecord` kinds beside charge lines (`Bill`, `Release`, `Lapse`), command-ack effects
  (`LaunchedEffect`, `VerifySaturated`), the user-agent parser (`device_of`), fork specs and
  `sibling_cycle_id`. Action: one module per record family under `domain/records/` (phase/stop, round,
  pass, provider, spend, LLM call, lineage, wiring, workspace), each non-record to its owner. **Rides
  with:** area 01's chain. **Re-test:** `git grep -cE "^class \w+\(.*CycleRecord" --
  promptpotter/domain/records/run_records.py` is 0.
- **A closed round banks its election twice**: `runner/round.py` appends an `ElectionRecord` from
  `result.election`, and the round's `RoundClosedRecord` carries the same `Election` through
  `results.py::RoundOutcome.election`; `ledger_scan.py` reads the first. Action: the ledger states the
  election once, the other reader reads that record. **Rides with:** area 01's chain. **Re-test:**
  `git grep -c "ElectionRecord(" -- promptpotter` is 0, or `RoundOutcome` holds no `election`.
- **`Election.deciding_pair` admits a pair that is not read**: `domain/results.py::Election.deciding_pair`
  is `PairedReading | None`, so `scoring/selection.py` asserts `ReadingState.READ` away. Action: type it
  `ReadPair | None`, narrowed where potter's election builds it (a behaviour-key move,
  named). **Rides with:** area 01's chain. **Re-test:** no `assert pair.state` in `selection.py`.
- **A round file stores a served word**: `arm_reading.py::ArmReading.vs_reference_name` is a
  `@computed_field`, dumped into each round file's scoreboard and read back by `RoundFileView.tsx`.
  Root: the round file holds facts, the round read words them. **Rides with:** area 01's chain.
  **Re-test:** a `RoundResult` dump holds no `vs_reference_name`.
- **Round health folds through three carriers**: `results_health.py::compute_degradation_health` has one
  caller (`compute_round_health`), which passes every argument, yet it defaults them and folds `or {}`,
  then copies the counts into `_RoundCounts` and again into `DegradationHealth`; `classify_sample_failure`
  and `compute_node_failure_rates` are public with no outside reader. Action: one fold, no defaults, one
  carrier. **Rides with:** area 01's chain. **Re-test:** `git grep -c "or {}" --
  promptpotter/domain/results_health.py` is 0.
- **What a child moved over its parent is computed twice**: `candidate_diff.py::candidate_delta` diffs
  flattened prompt maps (read by `l1.py`, `validators.py`, `panels.py`, `earned_blocks.py`) beside
  `opt_search_point.py::OptSearchPoint.loci_moved_from` over `loci()` (read by `export.py`,
  `hierarchy_subtree.py`); `flatten_prompts`/`flatten_sp_summary` are a third spelling of a locus, and
  `parent_param_value` falls back `or {}` twice. Action: `CandidateDelta` is built from `loci()`.
  **Rides with:** area 01's chain. **Re-test:** one of `candidate_delta`, `loci_moved_from` is left.
- **An unknown error category reads as UNKNOWN**: `shared/errors.py::error_category` catches `ValueError`
  and returns `None`, and its one reader (`domain/scoring.py:291`) maps that to `ErrorCategory.UNKNOWN`, so a
  category this build does not know is relabelled, never refused. Action: the cell reader parses
  `ErrorCategory(cat)` strictly. Same shape one layer up: `errors.py::graceful` swallows a fault into a
  log at `main.py` and `api/middleware/visits.py`. **Rides with:** area 01's chain. **Re-test:** `git grep -c "except
  ValueError" -- promptpotter/shared/errors.py` is 0.
- **Backend wire records drop undeclared keys**: `wire_record.py::typed_record` sets `extra="ignore"` on a
  record with no `rest` field, so `NodeWarning`, `Diagnostics` and `JudgeReading` silently drop a key the
  backend added. **Held:** a decision, whether backend wire is tolerant by design. **Re-test:** the
  generated `read` refuses an undeclared key, or the doc states the tolerance.

## 02 · Store, ledger, projections, landing

**Target:** One ingress (`CycleEventLog.append`), projections that are checkouts nothing reads back, the
archive one content-addressed tree filed by the dataset a row measured, `MeasurementArchive` its only gateway.
A line's fold is stated once and read by one keyed read `(hop, scope, fold)`. Survivors migrate, everything
else is wiped at a landing. The store holds no money rule and no served shape.

**Unexamined:** `store/campaign_store/store.py`, `store/stores.py`, `store/layout.py`,
`maintenance/archive_maintenance.py`, `maintenance/landing_key.py`, `store/io.py`, `store/data_root.py`

- **The feed's version spans a whole store**: a campaign is a subject its writers stamp
  (`io.py::stamp`), and a subtree reads its family's campaigns alone (`Stores.stamps`), but every
  evidence and cells view still reads its store's whole stamp; the feed's tick lists each store's cycle
  hops to find its sandboxes (`cycle_reads.py::_store_stats`), and a `store:`-subject read re-runs
  `feed_versions` over all stores (`presentation/api/routers/reads.py::_endpoint`);
  `reads.ts::draftRead` follows bare `store:` beside `account`, since Start's refusal reads the wallet
  every ledger's bill moves, so an inner sandbox's append re-asks it; `cycle_reads.py::_COUNTING` is
  one lock over every account's walk. Action: a view keys on its campaign's stamp, and a sandbox is
  found off its owner's stamp, never a walk. **Rides with:** a version chain of its own.
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
- **Which ledgers answer for a hop is decided at each reader**: a fold over a line is one
  `read_model.py::read_line` of a `Line`, but its spans are built at each caller (`CampaignStore.chain`,
  `line_spans`, a lone `LedgerSpan`, `campaign_cycle_ledgers`). Root: the read is keyed `(hop, scope,
  fold)`, the scope naming its cycle, its line or its campaign. Sits under the launch-steer row and the
  sidebar-spend row. **Rides with:** after the measuring loop. **Re-test:** `git grep -c "read_line(\[LedgerSpan" -- promptpotter` is 0.
- **`store/read_model.py` holds the money fold inside the ledger index**: `:906-1241` (`Spent`, `Money`,
  `MoneyFold`, `money_on`, `charge_book_fold`), imported by 54 files. Root: the money fold is a module of
  its own beside the spend book. **Rides with:** the store-root chain (wave 9, batch 2). **Re-test:**
  `git grep -n "^class Spent\|^def money_on" -- promptpotter/infrastructure/store/read_model.py` is empty.
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
- **A landing key's first derivation costs ~2.3 s per code version** (tw-v, tw-v2): it is derived
  once per process and filed by the package's bytes in the data root (`landing_key.py::landing_key`),
  but `shared/import_walk.py::import_closure` normalizes each module from its own parse
  (`hashing.py::normalized_tree`), beside `hashing.py::normalized_module`'s memo, and
  `store/data_root.py::row_schema` rebuilds pydantic `json_schema()` over every kind, so the first
  entry after a code edit, and a child in a fresh data root, pays it. tw-v2's memo (`arc/tw-v2`
  604b03eb2) was not taken: +47 production lines. **Rides with:** after wave 6. **Re-test:**
  `landing_key.py::derived_landing_key()` takes under 0.5 s cold, with production no larger.
- **The landing key over-keys by module** (bkeys-4, 11-19; items 451, 456, 462):
  `promptpotter/application/maintenance/landing_key.py::loading_modules` — keys the whole import closure of every reader,
  so a prose-free edit to any module a load could import re-lands every root, a cost not a
  wipe — key the functions a load calls, not their modules — **Rides with:**
  `area 02's chain` — **Re-test:** an edit to an unreached function in
  `promptpotter/domain/paired_reading.py` leaves `derived_landing_key()` unchanged.
- **A replay feed spells the prefix-chain keys beside `file_keys`** (store-5a, 11-19):
  `promptpotter/infrastructure/store/measurement_archive.py::ReplayFeed.__init__` — builds
  `config_key(node_configs[:n])` per prefix, beside `file_keys(entry)` and without its
  `entry.config_key` fallback — the file-key chain is a function of `node_configs` alone, one
  spelling — **Rides with:** `area 02's chain` — **Re-test:**
  `git grep -c "config_key(.*\[:n\])" -- promptpotter/infrastructure/store/measurement_archive.py` is 1.
- **`list_campaigns` drops an unreadable manifest into a log** (hubs-2, 11-18):
  `promptpotter/infrastructure/store/campaign_store/store.py::CampaignStore.list_campaigns` —
  `logger.warning` then `continue`: the account holds a campaign no surface reports — the
  survey has a typed unreadable row — **Rides with:** `area 02's chain` — **Re-test:** a
  campaign with a corrupt manifest appears in the listing as unreadable.
- **The dataset definition's `source` merges one level** (cite-fold, 11-18):
  `promptpotter/infrastructure/store/dataset_access.py::read_dataset_definition` — `source |=`
  is a shallow merge beside `merge_node_blocks` — `source` folds through `fold_layers` —
  **Rides with:** `area 02's chain` — **Re-test:**
  `git grep -c "source |=" -- promptpotter/infrastructure/store/dataset_access.py` is 0.
- **An authored dataset definition has no writer** (w7-ver, 11-27):
  `promptpotter/infrastructure/store/dataset_access.py::read_dataset_definition` — an install or
  research definition edit moves no stamp (those tiers sit off the data root's subjects), so reads
  that depend on it stand until the store's next write or a restart — the definition loader stamps
  its tier root — **Rides with:** `area 02's chain` — **Re-test:** edit an install definition; the
  cells ETag moves.
- **`store.py` holds two folds a `Line` should state** (w11-store, 11-28):
  `promptpotter/infrastructure/store/campaign_store/store.py::fold_cycle_index` composes a cycle's
  index off its chain beside `ledger_scan.py`'s lines, and `store.py::_ledger_meters` sums each
  ledger's `MoneyFold` meters in a loop of its own — each is a `read_model.py::Line` over its fold —
  **Rides with:** `area 02's chain` — **Re-test:**
  `git grep -cE "def (fold_cycle_index|_ledger_meters)" -- promptpotter/infrastructure/store/campaign_store/store.py` is 0.
- **The `Stores` hub holds a cycle's stop machine and the definition writer** (w11-store, 11-28):
  `promptpotter/infrastructure/store/stores.py::Stores.declare_ended` (with `declare_stop`,
  `mark_superseded`, `mark_producer_vanished`) runs the producer-lock ending on the hub every
  bench import reaches, and `Stores.write_own_definition` is the dataset tier's writer — the
  ending belongs to the campaign store's append door, the writer to the dataset store — **Rides
  with:** `area 02's chain` — **Re-test:**
  `git grep -cE "def (declare_ended|write_own_definition)" -- promptpotter/infrastructure/store/stores.py` is 0.

## 03 · Runner, round, bench

**Target:** The bench walks a manifest's `default` once per round and `initial_population` in a round entered
with no population; it decides nothing an optimizer owns and imports none. Init writes a frozen `Session`, the
runner only `Session.state`; one `RunScope`; a stop is a `StopReason` declared at one door; resume and fork
read standing rounds off the ledger.

**Unexamined:** `runner/round.py`, `initialization/scoring_scope.py`, `embedded_run.py`,
`initialization/session.py`, `runner/output.py`, `domain/activity.py`, `bench/resume_and_fork/repair.py`,
`resume_and_fork/resume.py`, `origin.py`, `bench/node_context.py`, `run_observers.py`, `bench/cycle.py`,
`runner/bench.py`

- **Wave B — `application/runner/` names no concept per module.** lb collapsed it by phase (21 modules to
  10) and deleted the ledger-scan forwarders. `inner/narrative.py` has since gone, and the stop
  vocabulary moved to `domain/phases.py`; the count is 9. **Decided 2026-11-27:** the target names the
  concepts that own a module, not a module count.
  **Re-test:** each `runner/` module's docstring names the one concept it owns, and no two name the same.
- **A resume's wait is unattributed**: on the box a resume takes up to 72 s to be applied, while
  offline its `open_launch`, `open_cycle` and `close_init` total about 0.3 s and the landing stamps once
  per schema, so the wait sits on the backend's network edges (every symbol is present, but the box
  half is unchecked; bac873699, landing 41, changed the TermNorm term push, so that candidate's cost may
  have moved). A detached resume opens its session once now, in its own process (w8-launch4,
  `run_job.py::HeldLaunch`). Action: time `probe_backend`,
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
- **A stop's cause and error each have a second door** (l4-words 2026-11-18, w9-entry):
  `run_observers.py::declare_run_stop` takes a paused stop's words from `take_pause() or
  (interrupted_by, "")`, so a self-pause cannot say why, and requires an `interrupted_by` every
  non-paused stop ignores; `run_observers.py::end_run_on` appends the stop's `ErrorRecord` through
  `append_record` while `store/stores.py::Stores.declare_stop` takes an `error=` the run never passes.
  Root: a stop carries its cause and its error through one door. **Rides with:** area 03's chain.
  **Re-test:** `interrupted_by` reaches only the paused branch, and one door files a stop's error.
- **A cancel during a rebase cut mints a fork its parent never names** (w9-entry):
  `runner/entry.py::_run_single_cycle` and `_run_line` await `asyncio.to_thread(_cut_rebase_fork)`, and a
  cancel leaves the thread minting. Root: the cut completes or names its child before the await can be
  cancelled. **Rides with:** area 03's chain. **Re-test:** a cancel injected during the cut leaves no
  child cycle its parent's stop does not name.
- **The runner is imported from above** (map pass, w11-plugin): `scoring/passes.py` imports nine
  `runner/bench.py` functions (`bench_origin`, `line_passes`, `live_cycle_run`, `read_cycle_bench`…) and
  `runner/output.py::ExportProjection`, `evidence/head_to_head.py` seven (`bench_line`, `pair_on_bench`,
  `read_bench`…), and `jobs/launcher/run_job.py` imports `runner/entry.py::run_optimization` and
  `runner/run_init.py::enter_cycle` while `runner/` imports `jobs/` at 11 sites (bench 1, entry 1,
  `inner/spawn.py` 6, run_init 3); the contract's move made none trivial. Root: the bench pass's lines
  move below scoring and evidence, and the launch's seam below both runner and jobs. **Rides with:** area 03's chain.
  **Re-test:** `git grep -n "^from promptpotter.application.runner" -- promptpotter/application/scoring
  promptpotter/application/evidence promptpotter/application/jobs` is empty.
- **The embedded fresh door probes its backend after its session opens** (w7-open, not re-tested):
  `application/embedded_run.py`. **Rides with:** area 03's chain. **Re-test:** the probe precedes the
  session's open, or the order is the design.
- **`BenchStatus.state` spans `read`** (spine4, 11-27):
  `promptpotter/domain/bench.py::BenchStatus` — `state: ReadingState` includes `READ`, so
  `runner/bench.py`'s unread branch asserts it away (`a read bench carries its pair`) — the
  unread status is its own type without `READ` — **Rides with:** `area 03's chain` —
  **Re-test:** `git grep -n "a read bench carries its pair" -- promptpotter` is empty.
- **A block race files an `ArmRecord` per arm per turn** (rk-dec, 11-13):
  `promptpotter/application/runner/round.py::_walk_population` — its `on_turn` appends an
  `ArmRecord` per arm per cell taken (block_size=1), so a block's ledger grows with cells — the
  root: an arm's state is filed at the decision that moves it, not at every turn — **Rides with:**
  `area 03's chain` — **Re-test:** `git grep -n "ArmRecord(" -- promptpotter/application/runner/round.py`
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

- **A line's spent rounds are spelled at three sites** (w11-store, 11-28):
  `promptpotter/application/runner/bench.py::line_round_budget`, `runner/entry.py` and
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
  `runner/bench.py`. **Re-test:** `git grep -n "BenchScope" -- promptpotter` is empty.
- **A round close re-resolves the config it holds** (tw-q): `scoring/measurement_log.py` calls
  `cycle_config` inside the hard-sample view though its caller holds the cycle's config. Root: the
  view is handed the config. **Rides with:** area 04's chain. **Re-test:**
  `git grep -c cycle_config -- promptpotter/application/scoring/measurement_log.py` is 0.
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
- **`read_arms` takes a spec its one caller fixes** (readings-3, 11-19):
  `promptpotter/application/scoring/paired.py::read_arms` — `spec` is always `ROUND_LIFT_SPEC` from
  `evidence/head_to_head.py` — the root: the one rule is the function's own, not a parameter —
  **Rides with:** `area 04's chain` — **Re-test:** `git grep -n "read_arms(" -- promptpotter` shows
  a caller passing any other spec, or the parameter goes.
- **A noise-floor reading repeats `unread`'s condition** (small-7, 11-18):
  `promptpotter/application/scoring/passes.py::measure_noise_floor` — `reading=` re-spells
  `band is None or recorded.composite is None` beside `unread` since mypy narrows only the inline
  test — the root: one narrowing, a helper returning the band or `None` — **Rides with:** `area
  04's chain` — **Re-test:** the condition appears once in the function.

## 05 · Spend, wallet, quota

**Target:** One `Budget` at four positions (asked, admitted, standing, spent); spent is a fold of spend facts;
only the run's spend book halts a run; an L4 inner cycle spends under its root's book; a send is filed once,
on the run that sent it; the payer is resolved per cell by `wallet.py::sending_key`; spend is the provider's
bill.

**Unexamined:** `infrastructure/llm/pricing.py`, `jobs/shares.py`, `jobs/quota.py`, `spend_book.py`,
`domain/spend.py`

- **Money text has no sub-cent rule**: a cell's price at six places (`phase_acts.py::CapUnderRound.costing`,
  `cli/commands/reads.py::_human`, and `potter/dispatch/injections/panels.py:1051-1052`) beside `domain/spend.py::usd_text`, which prints `$0.0000`
  for a cheap model; `usd_text` and webapp `fmtUsd` are one rule; `run_standing.py` spells a third.
  **Decided 2026-11-27:** three significant digits under a dollar in `usd_text`, which feeds the webapp's `fmtUsd`. **Rides with:** forks 1.
  **Re-test:** `git grep -c -E '\$\{[^}]*:[,]?\.[0-9]f\}' -- promptpotter`.
- **The default host pin is a trial with no verdict**: `domain/pipeline_overlay.py::pin_default_routes`
  writes each model's default host into every node config, and `bench/llm_call.py:231` pins the optimizer's
  own calls too. Decided: a model keeps its pin only where its
  realized cache share and effective $/M beat running uncached. **Rides with:** tune 3. **Re-test:**
  the `spend by node:` block at each twin's `readout.log` stop (`render/ansi.py::render_spend_breakdown`).
- **A nested spend book is wired beside the cell that nests it** (inner-1's report):
  `run_observers.py::RunObservers.arm_spend_book` reads none of `self`, so a fact repeats its
  `enclosed_as` line instead of calling it; `tests/factories.py::spend_book(enclosing=…)` nests a book a
  second way; `spend_book.py::_ENCLOSED = "backend"` is what a nested book falls back to where its
  spawner's pass names no kind; `RunScope.depth` / `InstrumentMode.depth` restate `instrument is not
  None` (L4 is one level deep, `docs/concepts/l4.md` § Depth). Root: `enter_inner_cell` nests the book (enclosing book and
  kind together, every spawning pass naming its kind), and depth goes. `_ENCLOSED` is also
  the hold tree's kind for a nested cell's hold with no resource (`SpendBook._filed`, `_out`), where
  an outer search's inner cell IS its backend. **Decided 2026-11-27:** the spawning pass names a nested
  cell's hold kind; an inner cell's is its connector's name, so no fallback is needed.
  **Re-test:** `factories.spend_book` takes no `enclosing`; `InstrumentMode` carries no `depth`; the
  nested-replay fact calls the production nesting.
- **A cycle's sidebar spend reads its ledger alone, so a cut send reads $0.000**:
  `hierarchy.py:302-308` builds the cycle row from `ledger_scan.py::LEDGER_SPEND` and passes `None` as
  unreported (`_spend_row("spend", …, None)`). A send a pause cuts is already filed at the cut as a
  `Lapse` (`spend_book.py::admitted`), but on the charge book, and for a nested run under the ROOT's
  ledger (`Admission`: `held_on((root.ledger or ledger).path)`), so neither the inner row nor the
  inner ledger can see it. `MeteredSpend` carries no `sends_unreported` (the paused-inner row's text
  says it does). Action: the cycle row reads its unreported off the hold tree
  (`Money.unreported_holds` under the cycle's jobs), not by ledger. **Decided 2026-11-27:** a cut send's
  late usage is not collected: it stays an unreported `Lapse`, since the cancelled request never returns. **Rides with:** hierarchy.py
  and the inner chain. **Re-test:** pause an outer mid inner-round (operator 2026-11-18: seat #001
  of `promptpotter-self.run__54ccef`); the inner row reads its cut sends as unreported, never
  `$0.000`, and the outer's mirror matches.
- **The wallet read under the admission gate grows with the account's charges** (gate-hold's
  report): `jobs/quota.py::read_account_wallet` folds `money_on(stores.charges.path)` and
  `campaign_spend` under the gate, admission's last scan that grows with history. Unmeasured.
  **Re-test:** hold time of `admit_and_share` at 10× today's charges file stays within 2× today's.
- **A nested run's send is filed once, on the run that sent it**: `domain/records/run_records.py:572`
  `mirrored: bool = False`, `infrastructure/llm/spend_book.py:751` writes `mirrored=self._book.nested`,
  `infrastructure/store/read_model.py:1132-1136` branches on it, and `store/CLAUDE.md` warns that money
  "banks twice" where a reader forgets (`mirrored` 14/6). Root: the outer run's cost is a fold over its
  inner ledgers. Under **A nested spend book is wired beside the cell that nests it** and
  **A cycle's sidebar spend reads its ledger alone, so a cut send reads $0.000**. Reshapes run data.
  **Rides with:** the store-root chain (wave 9, batch 2). **Re-test:** `git grep -c "mirrored" -- promptpotter` is 0.
- **A cycle's running job is found two ways**: `served_dashboard.py::spend_counts` (`:611`) takes this
  cycle's `hold_id` from its caller (`run_scope().book` in `run_observers.py:59`, the held run's job in
  `armed_caps`) and reads the siblings' through `_job_holding` (`:636`). Root: one reader of a cycle's
  live job. **Re-test:** `spend_counts` takes no `hold_id`.
- **`spend_counts` builds a full `SpendBook` per cycle to call `count()`**: `served_dashboard.py:617` fills
  a zero-budget book per cycle; `jobs/quota.py:789` and `jobs/launcher/admission.py:595` build similar
  zero-budget books. Root: `SpendBook` has no count-only constructor. **Re-test:**
  `git grep -n "SpendBook(" -- promptpotter/application` names no zero-budget book.
- **dbllmbench's cells send on the box's key read off `settings`, not `sending_key`** (w9-payer):
  `connectors/dbllmbench.py:182,205` hand the container `settings.<KEY>`, so an account's own key never
  pays a dbllmbench cell, and `holds_own_sends=False` is what holds its payer at the box. Root: the
  wallet's payer rule. Door: the container takes `sending_key(provider)`'s secret, it keys its own
  sends, and `cells_paid_by` reads it as a library's. Symptom under it:
  `infrastructure/backend.py::BackendClient.cells_paid_by` asks for the bound account even for a remote backend,
  which never reads the keys. **Re-test:** `git grep -n "api_key_attr).get_secret_value\|ANTHROPIC_API_KEY.get_secret_value" -- promptpotter/connectors`
  is empty.
- **`StepUsage` re-declares `TokenAccount`'s parts** (usage-2, 11-19):
  `promptpotter/domain/spend.py::StepUsage` — its flat `reasoning`, `cache_read`, `cache_write`
  fields repeat the names `TokenAccount.model_fields` already rosters (`from_account` and `account`
  go through that roster) — the root: `step_tokens`' flat wire shape; `StepUsage` holds a
  `TokenAccount` and the wire flattens it — **Rides with:** `area 05's chain` — **Re-test:** a new
  `TokenAccount` part reaches `StepUsage` without a second field edit.
- **A mint's job names its hop before its cycle share lands** (w8-launch4, 11-27; trim, 11-27):
  `promptpotter/application/jobs/shares.py::cycle_share` — raises "no share landed there" for a live
  job whose cycle has no `CycleShareRecord` yet; unreachable today only because a fresh campaign id
  is unknown before its manifest, and nothing enforces it — a job holds its hop only once its share
  is filed (one write, or a job without a share reads as zero) — **Rides with:** `area 05's chain` —
  **Re-test:** a sibling admitted on the campaign during a mint window reads a share, never raises.
  (items 68, 134)
- **A launch refused after admission counts toward the day's campaigns** (w11-walk, 11-27):
  `promptpotter/application/jobs/quota.py::campaigns_today` keeps a job that was admitted, so a Start
  released refused (no campaign minted) counts its job id, against the function's own "a refused launch
  spent nothing". Route: a signed-in account on the offline world (`serve.mjs --auth=on`), dataset
  email-tagging, Start (refused 422 `not_on_free_tier`), then Account → Usage & limits. Expected
  "Campaigns today 0 of 1000" (Storage: 0 campaigns); seen "1 of 1000". Root: the count reads the
  release's refusal (`promptpotter/application/jobs/registry.py::JobRegistry`, its `refusal`) as no
  mint. **Rides with:** `area 05's chain` — **Re-test:** after a refused Start, `GET
  /auth/quota-status` serves `campaigns_today` 0.
- **An unbilled run's spend reads three ways** (w11-walk, 11-27): on the walk world's
  `justlogic.d234` campaign the sidebar panel says "SPEND unmetered", the masthead "$0.0000", and
  `webapp/components/dashboard/scoring/CostStrip.tsx` ("Spend by round") draws an empty 0–1.0 axis with
  no unit and no empty-state line. Expected one served state ("nothing billed: unmetered") on all
  three; seen three. Root: one served spend headline the surfaces render, an empty series said, not
  drawn. **Rides with:** `area 05's chain` — **Re-test:** on the walk world the three name one state.

## 06 · Jobs, launch, commands

**Target:** Every loop start admits through one seam (`admission.py::request_jobs`); a full box queues; a
launch moves only by a `LAUNCH_MOVES` row through `JobRegistry.move`; an entry point hands data, never a
callable; every command is a `CommandRecord` filed by the sole `CommandDispatcher`; a job file is a survivor,
written under the gate.

**Unexamined:** `jobs/mint.py`, `commands/launching.py`, `commands/dispatcher.py`,
`jobs/launcher/head_to_head.py`, `jobs/reaper.py`, `store/command_log.py`, `domain/command_kinds.py`,
`jobs/launcher/admission.py`

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
- **A queued check-in Start leaves its draft unguarded** (w10-jobs): the draft claim is re-taken only in
  the promoted child, so a second Start or an edit can land while the Start waits in `jobs/queued/`;
  and a queued child's stderr is discarded until its cycle exists. Root: the queued ask holds its draft
  claim on disk, and the child's output has a home from its spawn. **Rides with:** area 06's chain.
  **Re-test:** a second Start on a draft whose Start is queued is refused.
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
- **A refusal Start meets is not the refusal the read serves** (w11-walk, 11-27): a run act is offered
  and refused only at the press. (1) Walk world, `justlogic.d234` campaign: "Start run", "Run one
  round, then pause" and Steer & fork's "Confirm steered fork" are enabled; each press answers 422
  `no_api_key`, while a check-in draft on the same world serves it as `launch_refusal` and disables
  Start ("No key for a provider"). (2) A signed-in free-tier account, dataset email-tagging with its own
  openrouter key: the draft serves `launch_refusal` null and an enabled Start, the press answers 422
  `not_on_free_tier` ("the task model openai/gpt-oss-20b is not part of the free tier yet", the
  dataset's own default). Expected (I3): every refusal the launch will meet is served on the read that
  offers the act, the cycle's `state.admission` as the draft's. Root: the key check and
  `promptpotter/application/free_tier.py::outside_free_tier` run at the press alone; one admission
  preflight serves both reads. **Rides with:** `area 06's chain` — **Re-test:** on the walk world with
  no key, the cycle's `state.admission.refusal` is `no_api_key` and the dock's Start is disabled.
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

## 07 · Config, pipeline, datasets

**Target:** Two seams: `resolve_dataset` answers which bytes, `pipeline_resolve.py` which values a campaign
runs, the second consuming the first. Params are one fold (`ParamLayers`); an optimizer's knobs are its nodes'
`config`; a default lives in the dataset's file, never code; one typed node document every door parses. A
dataset file is a partial laid on its chain (`dataset_access.py::definition_chain`), and the overlay is the
sole route for a backend tunable.

- **Params are not one fold** (w10-survey): `pipeline_resolve.py::bind_resolution` lays starting prompts
  through `pipeline_resolve.py::_apply_starting_prompts` after `ParamLayers.fold`, and
  `fold_output_contract` is a second post-fold at `pipeline_resolve.py::resolved_output_schemas`,
  `application/preflight.py` and `domain/opt_search_point.py`; `datasets/draft_patch.py::plan_draft_patch`
  folds the config through `fold_layers(CAMPAIGN_RULE, ...)` and then overwrites `config["scoring"]` with
  `datasets/draft_patch.py::_folded_scoring`. Root: starting prompts, the output contract and the scoring
  block's per-key merge are `ParamLayer`s or `CAMPAIGN_RULE`, so params are final once folded. **Rides
  with:** area 07's chain. **Re-test:** `git grep -c "fold_output_contract(" -- promptpotter` is 1 (the def;
  4 now) and `git grep -c "_folded_scoring" -- promptpotter` is 0 (2 now).
- **A source default lives in code** (w10-survey): `datasets/loaders.py::_bank` keeps
  `src.get("shuffled", True)`, which belongs in the dataset file or `defaults.yaml`. **Rides with:** area
  07's chain. **Re-test:** `git grep -c '"shuffled", True' -- promptpotter/application/datasets/loaders.py`
  is 0 (1 now).
- **A resolved dataset is resolved again past the seam** (w11-config): `datasets/ingest.py::draft_from_dataset`
  resolves the dataset, then `datasets/startability.py::checkin_bank` resolves it again by name;
  `datasets/loaders.py::resolve_bank`, `held_bank` and `fetch_bank` each read the definition the
  `ResolvedDataset` already carries; `UploadBasis.connector` is a `str` that `pipeline_resolve.py::basis_source`
  looks up again. Root: the seam's answer (`ResolvedDataset`, `DraftSource`) is handed down, never a name.
  **Rides with:** area 07's chain. **Re-test:** `git grep -c "checkin_bank(" --
  promptpotter/application/datasets/ingest.py` is 0 (1 now).
- **A knob's provenance is diffed, not read off the fold** (w11-config): `config_map.py::_knob_rows` calls a
  bench knob `default` where its value equals `defaults.yaml`'s, so a campaign that states the default reads as
  unstated; `campaign_config.py::OptimizerChoice.arm_gate` keeps a code default (`"off"`) the
  `defaults.yaml` guard never sees. Root: the fold stamps each key's layer (`layer_fold.py::Laid`) and the
  map reads the stamp; every bench default lives in `defaults.yaml`. **Rides with:** area 07's chain.
  **Re-test:** `git grep -c "campaign_defaults()" -- promptpotter/application/config_map.py` is 0 (2 now).
- **A refusal is swallowed into nothing** (w11-config): `datasets/origin_resolve.py::raised_commands` drops a
  finding whose patch fails `EditDraftPatch` with `continue`, and `datasets/harbor_panel.py::write_task_panel`
  drops a row with no documents; neither files what it dropped, and `harbor_panel.py::_panel_yaml` hardcodes
  `terminus-2` / `docker` the dataset file should name. Root: a dropped finding or row is a reported
  refusal; the panel's agent and environment are the dataset's. **Rides with:** area 07's chain.
  **Re-test:** `git grep -c "except ValidationError" -- promptpotter/application/datasets/origin_resolve.py`
  is 0 (1 now) and `git grep -c "name: terminus-2" -- promptpotter/application/datasets/harbor_panel.py` is
  0 (1 now).
- **Per-connector tunables are install globals** (w11-config): `config/settings.py::Settings` holds
  `TERMNORM_ENDPOINTS`, `N8N_HARNESS_AUTH_*` and `DBLLMBENCH_DB_TLS` beside the install's own settings, so a
  connector's reach is configured outside its definition. Root: each is its connector's extra, read from its
  definition or its own env block. **Rides with:** area 08's chain. **Re-test:**
  `git grep -cE "^ +(TERMNORM_|N8N_|DBLLMBENCH_)" -- promptpotter/config/settings.py` is 0 (13 now).
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
  **Rides with:** area 07's chain.
- **The pipeline carrier is typed only at its door**: the archive address
  (`measurement_archive.py::config_key`'s `list[tuple[str, dict]]`) and `RuntimeFailure.observed_config`
  (laid as a dict by `scoring/arm_states.py`; an abort's holds the whole document) stay dicts, typed at
  their reads (`RuntimeFailure.observed_call`, `measurement_log.py::_span`). Action: each carries a
  `NodeCall` per node, each hash reading its laid dump. **Rides with:** area 02's chain (the archive),
  area 04's (`observed_config`). **Re-test:** `git grep -c "NodeCall.model_validate(" --
  promptpotter/application/scoring/measurement_log.py promptpotter/domain/wounds.py` is 0 (2 now).
- **A cap's help is its developer docstring** (w11-walk, 11-27): check-in → Run bounds renders each
  field's served `description`: "Halt at fitness: Stop target_hit once … None → no such stop", each name in RST double backticks
  (`promptpotter/domain/campaign_config.py`) and, under "Wall-clock cap (minutes)", "Seconds ONE launch
  runs" (`promptpotter/domain/spend.py`). Expected operator words in the unit the field asks for; seen
  RST backticks and a seconds/minutes contradiction. Root: a served field's operator line is its own
  served text, never the model's docstring. **Rides with:** `area 07's chain` — **Re-test:** no served field
  `description` the Run bounds form renders holds a double backtick or names a unit its label does not.
- **Picking a dataset twice opens two identical check-ins** (w11-walk, 11-27): each press of a dataset
  in `webapp/components/ingest/DatasetPickList.tsx::DatasetPickList` files `open-dataset-draft`, so a
  second press mints a second draft; the pick list shows two "email-tagging · just now" rows and the
  sidebar two "email-tagging unstarted" campaigns, nothing telling them apart. Expected: a dataset with
  an open check-in reopens it (or the rows say what differs). **Rides with:** `area 07's chain` —
  **Re-test:** two presses of one dataset leave one open check-in.

## 08 · Connectors, backends, LLM clients

**Target:** Connectors are the only place backend identity is named; a built-in is one module binding
`CONNECTOR`; core never infers what a connector should declare; clients are `ProviderSpec` instances; provider
selection explicit.

**Unexamined:** none (w11-connectors read `connectors/harbor.py`, `connectors/dspy_module.py`,
`infrastructure/llm/openai_compat.py` and `infrastructure/llm/base.py` against the Target).

- **A harbor trial's harness fault escapes raw** (recovered, 2026-11): `connectors/harbor.py::_attempt`
  wraps only the image reuse and the package cache in `docker_host.py::machine_step`; a fault from
  `Trial.create` or `trial.run` that is no `OSError`/`TimeoutError` reaches the cell as UNKNOWN, charged as
  the prompt's. Root: the connector declares Harbor's harness faults as `CONNECTION`, never core reading
  them. **Rides with:** area 08's chain, with one measured fault class. **Re-test:** a `Trial.create`
  raising Harbor's own docker error ends the cell `CONNECTION`, retried.
- **dspy's send raises its raw failure** (recovered, w8): `connectors/dspy_module.py::_ProgramCalls._settle`
  closes a status-less failure outside `send_failure.py::NOT_THE_PROMPT` and returns, so the program
  re-raises litellm's exception, which `application/scoring/sample_measurement.py` classifies again
  through `failed_send`. Root: raise `FailedSendError` carrying the closed outcome. **Rides with:** area
  04's chain (the second reader). **Re-test:** `git grep -c "failed_send" -- promptpotter/application` == 0.
- **The offline world sleeps an LLM resend on the wall clock** (recovered, w7): `scripts/offline_world.py`'s
  `unslept` replaces only `infrastructure/backend.py`'s binding of `send_pacing.py::held_wait`, so
  `openai_compat.py`'s resend waits real seconds offline; and a fault raised inside the world's LLM reply
  is closed as a failed send before it raises as itself (`OpenAICompatibleClient._admitted_send`).
  Root: the world answers the sleep at `send_pacing.py`, the one owner. **Rides with:** area 14's chain.
  **Re-test:** a 5xx scripted on the offline LLM resends in no wall time.
- **A remote backend's cells ask the bound account** (recovered): `infrastructure/backend.py::BackendClient.cells_paid_by`
  reads `bound_account` for a backend that sends on its own keys, harmless while every sending entry
  binds one. Root: the payer is asked only of a connector that `holds_own_sends` or is host-paid
  (`wallet.py::cells_paid_by`). **Rides with:** area 05's chain. **Re-test:** a remote cell scored under
  no bound account is admitted.

- **Harbor's identity is named in core** (w10-survey): `application/datasets/harbor_panel.py` writes
  `terminus-2`, `application/datasets/loaders.py::_harbor_tasks` loads the roster,
  `presentation/cli/commands/writes.py` imports `pin_roster`, and `domain/scoring.py` and
  `application/scoring/formula/compiler.py` spell `env_reward`, which `connectors/harbor.py::REWARD_KEY`
  owns; `application/pipeline_resolve.py` keeps a "twin" of `connectors/harbor.py::INSTRUMENT_KEY`. Root: a
  connector declares its panel, dataset loader and reward key; core reads the declaration. **Re-test:**
  `git grep -nE "env_reward" -- 'promptpotter/*.py' ':!promptpotter/connectors/harbor.py'` is empty.
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
  `_MAX_ATTEMPTS`, and terminus-2's `_query_llm` thrice over `connectors/harbor.py::_AgentModel`, retry
  on their own. Patched singly, each fix is one more copy; the remedy is the upstream redesign. **Rides
  with:** xconn 4. **Re-test:** the relay's retries, not one constant: on TermNorm's `xconn` branch
  `_MAX_ATTEMPTS` becomes per-node `max_attempts` (`pipeline.json:156,232,287,325`), so a grep of
  `_MAX_ATTEMPTS` reads closed after that merge while the relay still resends; count the relay's
  resends for one 5xx instead (`grep -n "_MAX_ATTEMPTS = " <termnorm>/backend-api/core/llm_providers.py`
  on master, `max_attempts` in `pipeline.json` on `xconn`).
- **A draft serves every optimizer model twice, once under an empty provider** (w11-walk, 11-27):
  `GET /drafts/{id}`'s `draft.pipeline.optimizer_setup.model_capabilities` carries each model under
  `openrouter` and again under the empty string. Expected one entry per (provider, model). **Rides with:**
  `area 08's chain` — **Re-test:** no `model_capabilities` key is the empty string.
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

- **Potter's dispatch spells a rule twice and a cap inline** (w11-plugin census):
  `potter/dispatch/layout.py::unplaceable_edit` refuses an edit placed twice and
  `potter/dispatch/layout.py::coerce_l1_layout` asserts it again; `potter/behavior_checks.py`'s
  `_REBUT_SIGNALS` must match the words `potter/dispatch/injections/panels.py` tells the model to write;
  `panels.py` and `potter/dispatch/injections/layer_state.py` cut text at inline literals (`[:70]`,
  `char_cap=400`, `char_cap=2400`) beside `potter/dispatch/bundle.py`'s caps, and `layer_state.py`'s
  `_r_l1_layout` re-derives `potter/dispatch/prompts.py::node_layout`; `potter/validators.py` books its
  no-op, duplicate and repeat verdicts under the placeholder axis `variant`, no locus, which
  `domain/wounds.py`'s docstring lists for two of the three. Root: one owner per rule and per cap; a
  whole-candidate verdict names no axis. **Rides with:** area 09's chain. **Re-test:**
  `git grep -c 'axis="variant"' -- promptpotter` is 0 and `layer_state.py` names no `char_cap=` literal.
- **Selection and the race are written once per peer, not in the backbone** (w10-survey):
  `capo/members.py::PopulationSelector.select`, `gepa/members.py::Pareto.select` and
  `levi/members.py::MapElites.select` each re-derive the leader, a tie rule and the `RoundEnding` /
  `Selection`; `capo/members.py::PairedTRace.decide` and `gepa/members.py::_GateRace.decide` are one
  `scoring/race_protocol.py::next_block` body and both build the `eliminated` `StopSignal`;
  `kind == "eliminated"` is counted in `capo/members.py`, `gepa/members.py` and
  `potter/dispatch/injections/panels.py`; the shared-cell mean is written three times
  (`capo/members.py::_readings`, `gepa/members.py::minibatch_improves`,
  `gepa/members.py::_aggregate`) beside `optimizers/descriptors.py::cell_objectives`. Root: the backbone
  takes a score map and owns crown, hold and the stop signal; a peer supplies the ranking. **Rides with:**
  area 09's chain. **Re-test:** `git grep -n "next_block(" -- promptpotter/application/optimizers` is 0 in
  the peers (2 now).
- **Peers hold defaults and an Optional the manifest should own** (w10-survey):
  `capo/members.py::mutate_shots` draws `rng.randrange(3)`; `levi/members.py::_LLOYD_ITERATIONS` (100), the
  `< 1.0` threshold in `levi/members.py::_failures` and an always-empty `meta_advice_section` in
  `levi/members.py::refine_prompt` are code constants; `levi/members.py::LeviRefine._refinement` takes
  `LeviCalibration | None` and asserts it; `elects_on` returns a constant in all three peers while
  `potter/members.py` takes `selected`; `gepa/members.py::_GateRace._tested` is race-local state beside
  `optimizer_state`. Root: a knob is a node `config` field; the round carries what exists. **Rides with:**
  area 09's chain. **Re-test:**
  `git grep -nE "_LLOYD_ITERATIONS|randrange\(3\)" -- promptpotter/application/optimizers` is empty.
- **`theta_election` names a θ it no longer reads**: since θ left the round's statement every crown elects
  on the fitness column, yet the pipeline node, its knobs and `row_policy.py` still spell
  `theta_election`. Action: rename it to what it decides (with its doc row; a behaviour-key move).
  **Rides with:** area 09's chain. **Re-test:** `git grep -c theta_election -- promptpotter` is 0.
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
- **A held round's words are the election match's fall-through** (w8-round, 11-27):
  `promptpotter/application/optimizers/potter/election.py::_verdict_reason` — a HELD round (and
  ADOPTED) reaches the closing `return` of the `match`, since `RoundDecision.ending` is typed
  `RoundEnding`, which admits `adopted` — a selector's ending type is the endings it can decide
  (crowned, moved, held), so each reads its own arm — **Rides with:** `area 09's chain` —
  **Re-test:** `_verdict_reason` has a `case RoundEnding.HELD` and `RoundDecision.ending` excludes
  `ADOPTED`.
- **A coupling note is developer prose on every potter campaign** (w11-walk, 11-27):
  `promptpotter/application/optimizers/potter/couplings.py`'s `per_round_resubset` text ("ONE anchored
  δ ruler in θ — the stall replayer, c0_ok and PoBB elimination all via fit_theta on
  cycle.difficulty …") is served through `promptpotter/domain/phase_acts.py` as a "config coupling"
  info line naming the two knobs' dotted keys, above every potter
  campaign's chat. Expected: an info a run keeps is no news (`KnobClashes.tsx`'s own rule), or one
  operator sentence. **Rides with:** `area 09's chain` — **Re-test:** the walk world's campaign chat
  shows no coupling the config keeps.

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

- **Head-to-head re-reads every pair's bench per GET** (w10-survey): `evidence/head_to_head.py::head_to_head`
  picks the reference by `_agree` over every pair of graded rows and re-guards each row, and `_contrasts`
  guards every arm pair; each `_guard` reaches `runner/bench.py::pair_on_bench`, a full `read_pair`, so a
  request costs O(n²) reads (O(k²·m²) in `_contrasts`), and `_read` re-folds `standing_rounds` and the spend
  scan per campaign. Root: "the same bench set read alike" is a banked fact of two passes, served from the
  line's ledger. **Rides with:** area 11's chain, beside **The live reads re-serve history per poll**.
  **Re-test:** `git grep -c "pair_on_bench" -- promptpotter/application/evidence/head_to_head.py` is 0 (2 now).
- **The consumer spells the lift's verdict** (w10-survey): `views/evidence.py::_contrast_row` and
  `views/evidence.py::_pair_row` compute `lift.p_value <= lift.p_floor`; `views/evidence.py::_ranking_lines`,
  `evidence/head_to_head.py::_read_alike` and `views/render/phase.py` each spell `side == "spans"`.
  `domain/paired_reading.py::MeasuredLift` serves the floor and the side but not "the floor binds" or "the
  interval excludes zero". Root: every score is served; the verdict is a field of the lift. Also
  `evidence/metric_catalogue.py::CHANNELS` is a second word for `domain/fitness_fold.py::CELL_CHANNELS`:
  delete it. **Rides with:** area 11's chain. **Re-test:**
  `git grep -cE "p_floor|\.side == \"spans\"" -- promptpotter/application/views promptpotter/application/evidence`
  is 0 (5 now).
- **The live reads re-serve history per poll** (a hot-path pass, ✓ unless reported): `_cycle_cells`
  and `_campaign_edits` cost O((R·A)²) per request, `snapshot_frame` re-folds per connect, and a ray
  page re-folds per "older". Root: a value is its aggregate plus the live round, never a copy of its
  history. The wire half is closed (a frame naming the closed series' `series_version` it holds re-sends
  no closed round, `served_dashboard.py::ServedDashboard.series_held`); the server still folds and
  hashes every closed round per poll to decide it. **Re-test:** `_cycle_cells` per request is O(R·A).
- **The lineage subtree sorts every candidate per candidate**: `application/hierarchy_subtree.py::earlier_measured`,
  O(n² log n) per read; reported beside it, the subtree read builds the whole top family per request,
  and `_descent` compares O(n²) pairs with `SequenceMatcher` behind an lru of 4096 that thrashes from
  ~90 candidates (`hierarchy_subtree.py:242-318`). Action: "closer to" and descent are folded once per
  candidate on the ledger: a record kind. **Rides with:** a lineage-fold chain of its own. **Re-test:** `git grep -n "def earlier_measured" -- promptpotter/application/hierarchy_subtree.py` is empty.
- **An inner campaign serves a Delete no door presses** (webd-3's report): `domain/hierarchy.py::SubtreeCampaign`
  subclasses `HierarchyCampaign`, so an inner campaign in the deep read inherits its
  `delete: DeleteAct` (`delete-campaign`), which nothing may file for it. Root: the subtree entry is
  not a listed entry; it shares a base, not the listed one's acts. **Re-test:** the deep read's
  inner campaign serves no `delete`.
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
- **A restatement reads as a round** (w9-pass): the narration emits a "Round N" line for a restatement,
  and a fork child's readout may print a full block for the restated branch round. Root: a restatement
  is a close of the round it restates, worded as one. **Rides with:** area 11's chain. **Re-test:** a
  resumed offline leg's narration and readout name the restatement as one.
- **The served walk axis is a declared order** (ml-5, 11-25):
  `promptpotter/application/served_dashboard.py::_walk` — the walk's axis is
  `LiveDashboardState.walk_orders`, the declared row order a walk re-files at each decision, not the
  cells taken — the root: the axis derives from the cells taken — **Rides with:** `area 11's chain`
  — **Re-test:** `git grep -n "walk_orders" -- promptpotter` reads no declared list in `_walk`.
- **Readout words every look-ahead press as expiring** (readings-4/flight-ux, 11-18): `promptpotter/application/views/readout.py::_handle_sample_started`
  — an every-round depth is narrated "(armed — expires when this round finishes scoring)" because
  `SampleStartedRecord` carries `sample_lookahead`/`sample_abreast` but no `auto` — the record
  should carry whether the press expires, so the narration reads a fact — **Rides with:** `area 11's
  chain` — **Re-test:** `git grep -n "expires when this round" -- promptpotter`; still one hit means
  it stands.
- **Closed rounds folded twice per readout** (w7-text, 11-26): `promptpotter/application/scoring/measurement_log.py::cycle_hard_samples`
  — `closed_rounds` is folded in `cycle_hard_samples` and again in the readout's own fold
  (`views/readout.py` line ~348) — the projection has no round fold of its own to share — **Rides
  with:** `area 11's chain` — **Re-test:** `git grep -n "closed_rounds(" -- promptpotter/application/views/readout.py
  promptpotter/application/scoring/measurement_log.py` shows two folds per readout.

## 12 · Surfaces: CLI, REST API, MCP, webapp

**Target:** Five entry points share one orchestration layer; no business logic in an adapter; a CLI verb is
one `Verb` row; every write goes through `CommandDispatcher.dispatch`; everything on stdout is also on disk;
the served contract is generated and the browser derives nothing; mobile first.

**Unexamined:** `presentation/cli/main.py`, `cli/commands/launch.py`, `main.py`, `presentation/admin_bot.py`,
`cli/commands/writes.py`, `webapp/components/candidates`, `webapp/components/node-surface`

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
- **The MCP door's claude.ai connector is unproven on the box**: the door serves Streamable HTTP at
  `/mcp/` on the account's bearer (`promptpotter/presentation/mcp/server.py::HttpDoor`). Action: the
  operator's step after the box update, a connector with the token as its `Authorization` request
  header. **Rides with:** the next box update. **Re-test:** in claude.ai, a custom connector at the
  box's MCP URL answers `tools/list`.
- **Unverified, for the next change to each file**: `shell/CriticalAlertBanner` builds `Said` rows in the
  browser; `ViewTabs.show` is many `open`/`close` calls, not one workspace act;
  `cycle_files.list_cycle_files` walks all of `.runtime/` and filters after; `FileTree` mounts a 2 s
  `useActivePointer()` poll for one failure line; a `/hierarchy` 200 replaces every entry object
  `lib/hooks/useRegistry.ts::useRegistry` indexes. The anti-rot pass re-finds them. **Rides with:** rel 3.
- **A Candidates card draws an empty chart over a served lineage**: campaign
  `bbh-multistep-arithmetic-8b__244ba5` (history 2, rounds 2) renders `.cand-card`'s canvas blank
  with no console error, while its hierarchy and dashboard reads serve rows. A state the card enters
  but does not report; cause not traced. **Re-test:** the card for a campaign with a closed round
  draws at least one bar or point, or says why it cannot.
- **The served contract is generated and the browser derives nothing (root E)**: `webapp/lib/derivations`
  holds 44 files and 3,827 lines, about 1,780 of tests; `webapp/lib/test-fixtures.ts` 666 lines;
  `.facts.` 121/47 over `webapp/`. **Closes:** **The browser inks a served state from its own map**,
  **Adapters over a flat served shape**, **The browser recomputes engine facts**,
  **A hand-kept mirror of the served shapes**, **Served fields the browser never reads**. About 4,500
  TypeScript lines go. **Run data:** none. **Size:** large, a link per served fact. **Rides with:** webd.
  **Re-test:** `git ls-files webapp/lib/derivations | grep -v __tests__ | xargs wc -l` falls by the served facts.
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
- **A pool chip pressed twice inside one write's ack builds from the pre-write read** (feed-version, not
  re-tested): `NodeKnobsPanel.tsx::PoolPicker`. **Rides with:** area 12's chain. **Re-test:** two presses
  inside one ack keep both picks.
- **A pane's phone layout keys on the screen, not its column** (webapp-1, 11-19): measurements.css,
  scoring.css, dashboard-pipeline.css, remote.css and ui/IntervalRow.module.css still switch at
  `@media (max-width: 640px)` though they draw in a narrower `.view` — the root: the `column`
  container the fold reads — **Rides with:** `area 12's chain` — **Re-test:** `git grep -c "@media
  (max-width: *640px)" -- webapp/components webapp/app/styles/domains` reaches the screen-only rules
  alone.
- **App lifespan state outlives shutdown** (tw-2, 11-25): `promptpotter/main.py::lifespan` — about 23 MB
  stays held in-process after `TestClient(app)` exits — what lifespan opens (registry, MCP door,
  identity bundle) should be released or owned per app instance — **Rides with:** `area 12's chain`
  — **Re-test:** tracemalloc a `with TestClient(app)` enter/exit; retained bytes near 0 closes it.
- **Vitest isolate:false makes vi.mock silently miss** (webd-1, live-3, 11-19): `webapp/vitest.config.ts` (`isolate`)
  — a `vi.mock` of a module another file already loaded is ignored in the shared module graph — mocks
  are unsafe there, so tests mount real providers or the gate refuses `vi.mock` of `@/lib/hooks/*`
  (items 299, 317) — **Rides with:** `area 12's chain` — **Re-test:** `git grep -n "isolate" -- webapp/vitest.config.ts`
  reads false.
- **REST 422 finds its model through request.scope** (refuse-1, 11-19): `promptpotter/main.py::validation_error_handler`
  — digs `request.scope["route"].body_field` because a `RequestValidationError` carries no model —
  command routes should take the body untyped and run `validating`, keeping the schema through
  `openapi_extra` — **Rides with:** `area 12's chain` — **Re-test:** `git grep -n "body_field" -- promptpotter/main.py`.
- **Sign-in clears the read cache it just filled** (exhibit-acts-fix, 11-19): `webapp/lib/state/auth.tsx` (`clearReadCache`)
  — adopting an identity remounts every read, so `/auth/me` is probed twice — the cache should be keyed
  by identity, not cleared on anon-to-authed — **Rides with:** `area 12's chain` — **Re-test:** open
  the page signed-in; count `/auth/me` requests, 2 means it stands.
- **A pane's acts gate on the dashboard read, not the entry they act on** (exhibit-acts, 11-19):
  `webapp/components/node-surface/PromptFieldsEditor.tsx::PromptFieldsEditor` — it picks where a
  snippet banks by order (`viewed?.path ?? checkin`), so a viewed campaign wins over an open
  check-in — the editor's host names the subject an edit is written in, never a fallback by order —
  **Rides with:** `area 12's chain` — **Re-test:** follow the exhibit, open a check-in, edit a
  snippet; it banks into the check-in.
- **The login page promises a grant no read serves** (w11-walk, 11-27):
  `webapp/components/login/AuthCore.tsx::AuthCore` hard-codes "10 optimization runs on your own data,
  up to 10 rounds each, on my key"; the served grant is "$0.0000 of $0.3000" (`/auth/coupon`), 1000
  campaigns a day (`/auth/quota-status`) and a check-in's "up to 5 rounds". Root: the grant's words are
  served. **Rides with:** `area 12's chain` — **Re-test:**
  `git grep -c "10 optimization runs" -- webapp` is 0.
- **A phone clips the run's masthead and three rows** (w11-walk, 11-27), at 390×844, each fine at
  1440: `webapp/components/shell/masthead/RunMasthead.tsx` cuts the stat strip ("BEN…", "10(") and
  the Follow button, over a ~100px empty band; `webapp/components/account/ApiTokens.tsx`'s scope chips
  run off the card ("step" at x=399); `webapp/components/pipeline/KnobClashes.tsx` lays a dotted key in
  a ~50px column, one to three letters a line; the Account view opens under an empty ~150px band
  below its CHAT/ACCOUNT tabs. No page scrolls sideways, so `noSidewaysScroll` passes all four.
  Root: a row of unwrapped items owns its overflow at column width. **Rides with:** `area 12's chain`
  — **Re-test:** at 390px every element of these four lies inside its card.
- **A reopened check-in tells itself as the dataset's whole task file** (w11-walk, 11-27):
  `webapp/lib/hooks/useIngestFlow.ts` (`retell`) echoes `draft.raw_task_description` as the user's
  message: the dataset's markdown task, newlines collapsed into one block, pushing the draft card off
  the first screen; a fresh pick echoes "Use dataset “Email Tagging”". Expected one telling of a
  draft, the same fresh or reopened. **Rides with:** `area 12's chain` — **Re-test:** reopen a
  dataset check-in; the thread holds no line the operator did not type.
- **A campaign's served address opens nothing and draws another campaign** (w11-walk, 11-27): a deep
  link to `#/justlogic.d234__3ddd32`, the walk world's hierarchy `address` for that campaign, shows
  `webapp/lib/state/poll.tsx`'s "This link opens nothing" over promptpotter-self/e2e's masthead and
  pipeline. Expected: a campaign address opens its line cycle, or the gone state alone, never
  another run beneath it. **Rides with:** `area 12's chain` — **Re-test:** each `/hierarchy` entry's
  `#/<address>` opens that entry.
- **The Leader card heads one round with another's line** (w11-walk, 11-27): on the walk world's
  `justlogic.d234` (desktop and phone), Best reads "round 1 Critique · best · C1.1" over "round 4: no
  arm cleared the parent; the line moves on to C4.1, uncrowned …"
  (`webapp/components/searchpoint/WinnerStanding.tsx`'s card). Expected the line of the round it
  heads. **Rides with:** `area 12's chain` — **Re-test:** the card's round label and its line name one
  round.
- **The Candidates chart's lift names no floor** (w11-walk, 11-27):
  `webapp/components/candidates/FitnessChart.tsx` captions "+80.0 pts" beside the crown, drawn over
  the crown glyph and the interval cap, while the masthead serves "+71.4 pts [+44.4, +98.5]" on the
  origin panel. Expected (I8) the caption names its floor and panel, legible. **Rides with:**
  `area 12's chain` — **Re-test:** the caption reads its floor and overlaps no mark.
- **Lineage counts "0 descendants" over a drawn tree** (w11-walk, 11-27): the freed tree of 13
  candidates is headed "0 descendants", `webapp/lib/hooks/useCampaignForest.ts`'s `cycles.length - 1`,
  a browser count of forked cycles under a candidate tree. Expected a served count of what is drawn,
  named for it. **Rides with:** `area 12's chain` — **Re-test:** the header's count names the drawn
  nodes' kind and matches them.
- **Compare promises a read it refuses** (w11-walk, 11-27): `webapp/components/compare/ComparePane.tsx`
  says subjects "may come from different datasets — the read says what that costs rather than refusing
  it"; ticking the walk world's two campaigns answers 400 "Could not read the selection … graded under
  2 scorers". Expected the empty state names the one-scorer rule, or the second tick says it before
  the read. **Rides with:** `area 12's chain` — **Re-test:** the help text and the refusal agree.
- **Uncap is refused, in the validator's own words** (w11-walk, 11-27):
  `webapp/components/shell/remote/RunLimitsControl.tsx`'s Uncap sends `{budget: {rounds: null}}`, but
  `promptpotter/domain/command_kinds.py::ChangeRunLimitsPayload` lifts an axis on `unbounded` and
  reads null as unnamed, so every Uncap answers 422 and shows "a budget naming at least one of usd /
  tokens / rounds / wall_clock_s, or `overshoot`, is required". Route: walk world campaign → Job
  status and configuration → Rounds → Uncap. Root: one spelling of a lift, the generated payload type
  refusing null. **Rides with:** `area 12's chain` — **Re-test:** Uncap on a capped axis applies.
- **A signed-out phone lands on an empty pane that offers an upload** (w11-walk, 11-27): `/` at 390px
  without a session draws a blank pane over "Drop a dataset file…" with the attach control; sign-in
  sits only behind the "<" sidebar. An account deleted by the host drops to the same with no word.
  Expected (I4) anon shows anon chrome and its sign-in CTA on the first screen. **Rides with:**
  `area 12's chain` — **Re-test:** a cookieless phone load of `/` shows a sign-in link and no
  attach control.
- **Account controls render disabled with no reason** (w11-walk, 11-27):
  `webapp/components/account/AccountProfileTab.tsx` shows "Update profile" and the sole sign-in's
  "Remove" disabled with no `aria-describedby`; Permissions lists `account.keys` beside bare
  `babysit`, `budget` … (the `campaign.` prefix dropped for one family only); `DatasetPickList.tsx`'s
  Discard is a native unstyled button on its own line. Expected (I3) a control no read offers is not
  drawn, or says why; one word per capability. **Rides with:** `area 12's chain` — **Re-test:** every
  disabled control on Account → Profile carries its reason.

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
- **The bundle check times out under the gate's load** (w10-records' landing): the full gate's
  playwright went red in 37 s, because the e2e serve refused: `application/health.py::webapp_bundle`
  ran `git add -A -- webapp` into an empty scratch index with `timeout=10` and timed out; alone on
  the same tree it was green (449.9 s). An empty index stats and hashes every `webapp/` file on every
  check, so its cost grows with the tree and the desk's load. Root: the scratch index starts as a
  copy of the checkout's own, so only changed files are hashed, stamped as
  `webapp/vite.config.ts::webappTree` stamps (check that both name one tree). **Rides with:** the next
  chain touching `application/health.py`. **Re-test:** the full gate's playwright is green on the
  desk beside its pytest.
  Second case (w10-tests' landing): `touch` phone-412 "every view fits its viewport" failed in
  `webapp/e2e/harness.ts::drawn`, with a read still open past `expect.timeout` (15 s), while
  offline-run (903 s) and pytest (638 s) ran beside it; alone it was green (252 s). Third case
  (w11-store/plugin/scoring): `touch` landscape-844 the same, plus `cold` onboarding-signed-in's
  `fake_issuer.ts::waitForHealth` past its 60 s. Playwright took 962 s against 290 s alone, and pytest
  1194 s against 638 s, yet the desk was not CPU-bound: playwright ran at 0.74 cpu/wall against 1.83
  alone, pytest at 0.49, and the checks running together summed about 5 of 16 cores. The schedule
  packed them up to `free_mb` (3817 MB free; playwright 1680 + offline-run 1240 + pytest 435 MB
  declared). Suspected root: memory or disk, not cores. Measure first: page faults and disk queue
  sampled across one full gate. Then budget the schedule against the resource that binds, or make
  `drawn` and `waitForHealth` wait on what the server says rather than on a clock. **Rides with:**
  area 14's chain, as its first link.
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
- **No door yields a recorded capability menu or a dataset with a panel** (tw-x row 7): two
  `write_resolved_run` seats stay in test_identity. Door: the offline world's OpenRouter catalogue
  (`offline_world.py`, `openrouter.ai/api/v1/models`) moved between a world campaign and its resume,
  the workspace's snapshot refreshed (`capabilities.py::refresh_model_capabilities`); a panel through
  a dbllmbench dataset on `world.opened(connector=…)`. **Rides with:** wave 6. **Re-test:**
  `git grep -c "write_resolved_run(" -- tests/` is 0.
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
- **Tests seat what a world door should make** (w9-entry): `tests/test_security.py` hand-seats a
  `ForkCut`. **Rides with:** area 14's chain. **Re-test:** `git grep -c "ForkCut(" -- tests/test_security.py` is 0.
- **`gate.py --changed` proves nothing for a committed link** (w7-ver2, HO1063): on a clean tree it runs 0
  checks, and its `specs` go red when their generator rewrites. **Rides with:** area 14's chain.
  **Re-test:** `--changed` after a commit checks that commit's files, and a regenerated spec reads green.
- **The trace's canonical labels move on a resume into the end pass** (w8-tests3): `offline_run.py::decided`
  reads C0 as `parent@R1` there. **Rides with:** area 14's chain. **Re-test:** a resumed leg's labels
  match its straight twin's.
- **The surface ledger measures against origin/main, not the chain's base** (w8-web2, 11-27; w8-web,
  w8-resolve2, w8-l4): `scripts/complexity_ledger.py::_MAIN` — the `surface-ledger` check reads each
  row at the merge-base with `origin/main`, so a `release/next` base starts red with its inherited
  rise, and `BASELINE`'s exact `package_lines` leaves a chain growing one line red until another
  deletes (item 71) — the gate measures against the base the chain forked from, as
  `gate.py::_branch_base` already names it for `--changed` — **Rides with:** `area 14's chain` —
  **Re-test:** `python scripts/gate.py --only surface-ledger` on a branch off an unlanded
  `release/next` reads green when the chain raised nothing.
- **The offline world reports no cached_tokens** (usage-2, 11-19): `scripts/offline_world.py::USAGE`
  — `prompt_tokens_details` holds only `cache_write_tokens`, so every offline prefix reads
  "unreported" where OpenRouter reports a cold 0 — the world's usage carries `cached_tokens` as the
  provider does — **Rides with:** `area 14's chain` — **Re-test:** `git grep -c cached_tokens --
  scripts/offline_world.py` is above 0.
- **The offline-run check is landing tier, so `--changed` misses a script break** (bkeys-4, 11-19):
  `scripts/gate.py::_offline_leg` — `gate.py --changed` skips the offline-run check, so a break in
  `scripts/offline_run.py` itself (e.g. a `CellSheet` that cannot parse from JSON in the trace) shows
  only at equals-main — a `--changed` pass runs the offline run whenever `scripts/offline_run.py` or
  the world it reads changes — **Rides with:** `area 14's chain` — **Re-test:** edit
  `scripts/offline_run.py`, run `gate.py --changed`; the offline-run check runs. (items 454, 459)
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
- **The offline world runs no launch a browser starts** (w11-walk, 11-27): `webapp/e2e/serve_app.py`
  routes the server's own sends through `scripts/offline_world.py`'s `Router`, but a launch runs in
  `promptpotter/application/jobs/launcher/run_job.py`'s child, which reads no `TERMNORM_ENDPOINTS` and
  is released `backend_unreachable`; the world also holds no provider key (every run act 422
  `no_api_key`) and its pricing and capability refreshes dial out (403 through a refusing proxy). So
  no walk can watch a round live, pause, resume or fork on divergence. Root: the child inherits the
  world's backend posture from the server that spawned it. **Rides with:** `area 14's chain` —
  **Re-test:** Start on the cold world's email-tagging check-in closes round 0.
- **A rule the suite reaches only through a round is decided where no table can reach it** (w10-tests
  link 1): the slowest `offline` facts walk a campaign to reach one decision, e.g.
  `tests/test_scoring.py::test_a_round_the_budget_cuts_elects_only_past_the_panel_gate_and_stands_once_closed`
  (2.58 s, a CAPO campaign per case, `execute_round` / `post_round` driven by hand) and
  `tests/test_scoring.py::test_a_round_file_the_disk_refuses_is_one_warning_and_the_next_round_files_its_own`
  (1.55 s); the cut's election, the round file's refusal and the formula re-read are decided inside
  `promptpotter/application/runner/`, which exposes no pure function for them, and test_scoring's
  hand-driven rounds (8 lines, 4681–5421 at `42bd9ca42`) are the same root. Root: each rule becomes a
  pure function in the module that decides it (`scoring/row_policy.py::send_rows` is the model), its
  facts a table, the walk left to a leg. **Rides with:** the chain owning `application/runner/` beside
  area 14's chain. **Re-test:** `pytest --durations=10` lists no `offline` fact over 1 s on Linux, and
  `git grep -c "execute_round(" -- tests/test_scoring.py` is 0.

