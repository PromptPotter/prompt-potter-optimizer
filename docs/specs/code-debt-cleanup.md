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
  - bench ↔ optimizers: bench still reads `nodes.py`'s `CycleRead`, member protocols and
    `NODE_MEMBERS`, because the plugin contract and the round types share that module. Moving
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
`maintenance/archive_maintenance.py`, `maintenance/built_key.py`, `store/io.py`, `store/data_root.py`

- **The feed's version spans a whole store**: the writers stamp one version per subject
  (`io.py::stamp`), but a store is the finest subject: a campaign subtree's version
  (`hierarchy_subtree.py::subtree_version`) and every evidence and cells view read their store's
  stamps (`Stores.stamps`), so any run's append or archive write re-asks every mounted tree and view;
  the feed's tick still lists each store's cycle hops to find its sandboxes
  (`cycle_reads.py::_store_stats`), and a `store:`-subject read re-runs `feed_versions` over all
  stores (`presentation/api/routers/reads.py::_endpoint`); `reads.ts::draftRead` follows bare
  `store:` beside `account`, since Start's refusal reads the wallet every ledger's bill moves, so an
  inner sandbox's append re-asks it; `cycle_reads.py::_COUNTING` is one lock over every account's
  walk. Action: a campaign is a subject its writers stamp, and a sandbox is found off its owner's
  stamp, never a walk. **Rides with:** a version chain of its own. **Re-test:** a mounted subtree
  holds its ETag while another campaign of its store appends; `draftRead`'s subject is not `store:`.
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
- **A line's fold is stated once and read by one keyed read**: `infrastructure/store/campaign_store/ledger_scan.py`
  holds 21 `scan_` functions; `for span in spans` is spelled 17/7; `scan_cell_formula` hand-merges the
  fold `scan_cycle_facts` reads on one ledger. Which ledgers answer for a hop (its cycle, its line,
  its campaign) is decided at each reader. Root: one read `(hop, scope, fold)`, the fold stating how its
  values compose. Sits under the launch-steer row and the sidebar-spend row. **Rides with:** after the measuring loop. **Re-test:**
  `git grep -c "for span in spans" -- promptpotter` falls from 16.
- **`store/read_model.py` holds the money fold inside the ledger index**: `:906-1241` (`Spent`, `Money`,
  `_Money`, `money_on`, `charge_book_fold`), imported by 54 files. Root: the money fold is a module of
  its own beside the spend book. **Rides with:** the store-root chain (wave 9, batch 2). **Re-test:**
  `git grep -n "^class Spent\|^def money_on" -- promptpotter/infrastructure/store/read_model.py` is empty.
- **A protocol with one implementation**: `read_model.py:409 Projected` exists so the index need not import
  `infrastructure/projections/base.py::Projection`, its only implementer. Root: the index takes `Projection`
  or the protocol moves to `base.py`. **Rides with:** the money-fold row above.
  **Re-test:** `git grep -n "class Projected" -- promptpotter` is empty.
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
- **A landing key re-derives per call** (tw-v, tw-v2): `shared/import_walk.py::import_closure`
  normalizes each module from its own parse (`hashing.py::normalized_tree`), beside
  `hashing.py::normalized_module`'s memo. That costs 2.16 s for the 329-module closure. On top,
  `store/data_root.py::row_schema` rebuilds pydantic `json_schema()` over 90 kinds (0.6–0.85 s), and a
  memo keyed by the model class is keyed by identity. tw-v2's fix (`arc/tw-v2` 604b03eb2) was not
  taken: +47 production lines, because a text-keyed memo held 10.5 MB, so it went sha-keyed and
  zlib-compressed, to save ~2 s on one fact. **Rides with:** after wave 6. **Re-test:** a second
  `scripts/build_ts_types.py::derived_landing_key()` in one process takes under 0.2 s, with
  production no larger.
- **A cell file's version keys on (mtime, size)** (w8-store2, 11-27; items 32, 35):
  `promptpotter/infrastructure/store/measurement_archive.py::_CellFile.refresh` — it and
  `_live_rows` key on `(st_mtime_ns, st_size)`, so a same-size rewrite within one tick goes
  unseen — a per-file version only a writer moves (the stamp's channel) — **Rides with:**
  `area 02's chain` — **Re-test:** `git grep -n "st_mtime_ns, st_size" -- promptpotter/infrastructure/store/measurement_archive.py` is empty.
- **The landing key over-keys by module** (bkeys-4, 11-19; items 451, 456, 462):
  `scripts/build_ts_types.py::loading_modules` — keys the whole import closure of every reader,
  so a prose-free edit to any module a load could import re-lands every root, a cost not a
  wipe — key the functions a load calls, not their modules — **Rides with:**
  `area 02's chain` — **Re-test:** an edit to an unreached function in
  `promptpotter/domain/paired_reading.py` leaves `derived_landing_key()` unchanged.
- **A sandbox delete names its owner twice** (spend-tomb, 11-25):
  `promptpotter/infrastructure/store/campaign_store/store.py::CampaignStore.delete_inner_sandbox`
  — it takes `owner=` while `delete_campaign` and `reaper.py::reclaim_orphan_sandboxes` each
  read `read_sandbox_owner` first — it reads its own owner file — **Rides with:**
  `area 02's chain` — **Re-test:** one `read_sandbox_owner` per sandbox delete.
- **A replay feed spells the prefix-chain keys beside `file_keys`** (store-5a, 11-19):
  `promptpotter/infrastructure/store/measurement_archive.py::ReplayFeed.__init__` — builds
  `config_key(node_configs[:n])` per prefix, beside `file_keys(entry)` and without its
  `entry.config_key` fallback — the file-key chain is a function of `node_configs` alone, one
  spelling — **Rides with:** `area 02's chain` — **Re-test:**
  `git grep -c "config_key(.*\[:n\])" -- promptpotter/infrastructure/store/measurement_archive.py` is 1.
- **`last_landing` reads an unreadable stamp as none** (land-order, 11-18):
  `promptpotter/application/maintenance/landing.py::last_landing` — a `ValidationError` returns
  `None`, so older code reading a reshaped `LandedStamp` lands again and bypasses the
  newer-version refusal — the stamp's version is read apart from its strict model —
  **Rides with:** `area 02's chain` — **Re-test:** a stamp with an unknown field still yields its
  `version`.
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
- **The capability override's fate is RUN, though it is authored** (lite, 11-14):
  `promptpotter/infrastructure/store/data_root.py::KINDS` — row "model capability override" is
  `_RUN` (the landing drops what no model types) while its reader refuses a bad file as
  operator config (`FileKind.STORED_CONFIG`) — decide whether an authored workspace file is a
  SURVIVOR (migrated) or RUN — **Rides with:** `area 02's chain` — **Re-test:** the row's
  `Fate`.
- **An authored dataset definition has no writer** (w7-ver, 11-27):
  `promptpotter/infrastructure/store/dataset_access.py::read_dataset_definition` — an install or
  research definition edit moves no stamp (those tiers sit off the data root's subjects), so reads
  that depend on it stand until the store's next write or a restart — the definition loader stamps
  its tier root — **Rides with:** `area 02's chain` — **Re-test:** edit an install definition; the
  cells ETag moves.

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
- **The runner is imported from above** (map pass): `scoring/passes.py` and `evidence/head_to_head.py`
  import `runner/bench.py`, and `jobs/launcher/run_job.py` imports `runner/entry.py` while `runner/`
  imports `jobs/` at 11 sites. Root: what both sides read moves down. **Rides with:** area 03's chain.
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
- **A connector's two-valued term reaches no scorer** (readings-5's report): `pipeline_resolve.py::Resolution`
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
  first. The support bound is an interval, not an SE, and over a paired difference's
  support [−1, 1] it is far wider than the floor (n = 20: all ties ±0.42 against ±0.026; all wins
  [0.64, 1] against [0.97, 1]). **Decided 2026-11-27:** one rule for a constant paired side: an exact
  bound on the share of cells where the pair disagrees (0/1 grades at n = 20: about ±0.17 for all ties,
  about [0.66, 1] for all wins; its bounded-variable form for graded scores), replacing both the `1/(4n)`
  floor and the support bound; the offline run names every moved pick. **Re-test:** one rule for a constant side
  in `statistics.py`, each moved pick named.
- **The composite step has no band beside the fitness difference**: `domain/verification.py::VerifyReading.composite_increment`
  (`:152`) is a bare float while `fitness_difference` (`:148`) is an `Estimate`. Root: both columns'
  fresh − recorded are one shape. **Re-test:** one shape for both columns' fresh − recorded.
- **`domain/cell_fold.py::repairable_holes` counts a charged error** (tw-p): every errored non-HALTED cell
  counts, a charged one a re-walk pays again included, since `ErrorCategory` does not say which errors
  are fillable. With the repair gone, name each reader and what it should count. **Rides with:** wave 6.
  **Re-test:** `repairable_holes` excludes a charged error, or has no reader.
- **A resumed walk buys a charged errored cell again** (tw-j2): `infrastructure/store/measurement_archive.py::ReplayFeed.advance`
  skips `cell.errored`, so a pass that a charged error stopped is sent again on resume and paid twice.
  The crossed legs compare deduplicated `passes_graded` and read the repeat as spend. **Decided 2026-11-27:** an errored,
  charged cell on resume replays its error, never retries (§0: no retry of a (sample, candidate) pair). The root is
  shared with `repairable_holes`'s row: `ErrorCategory` does not say which errors are fillable.
  **Rides with:** wave 6. **Re-test:** `offline_run.py --charged` crossed-resumed
  `harness.backend_cells` equals crossed's, or the retry is a decision in `docs/decisions.md`.
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
  `hierarchy.py:302-308` builds the cycle row from `scan_ledger_spend` and passes `None` as
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

## 07 · Config, pipeline, datasets

**Target:** Two seams: `resolve_dataset` answers which bytes, `pipeline_resolve.py` which values a campaign
runs, the second consuming the first. Params are one fold (`ParamLayers`); an optimizer's knobs are its nodes'
`config`; a default lives in the dataset's file, never code; one typed node document every door parses. A
dataset file is a partial laid on its chain (`dataset_access.py::definition_chain`), and the overlay is the
sole route for a backend tunable.

**Unexamined:** `config_map.py`, `pipeline_responses.py`, `domain/pipeline_parsing.py`,
`domain/pipeline_schema.py`, `campaign_config.py`

- **Params are not one fold** (w10-survey): `pipeline_resolve.py::bind_resolution` lays starting prompts
  through `pipeline_resolve.py::_apply_starting_prompts` after `ParamLayers.fold`, and
  `fold_output_contract` is a second post-fold at `pipeline_resolve.py::resolved_output_schemas`,
  `application/preflight.py` and `domain/opt_search_point.py`; `datasets/draft_patch.py::plan_draft_patch`
  folds the config through `fold_layers(CAMPAIGN_RULE, ...)` and then overwrites `config["scoring"]` with
  `datasets/draft_patch.py::_folded_scoring`. Root: starting prompts, the output contract and the scoring
  block's per-key merge are `ParamLayer`s or `CAMPAIGN_RULE`, so params are final once folded. **Rides
  with:** area 07's chain. **Re-test:** `git grep -c "fold_output_contract(" -- promptpotter` is 1 (the def;
  4 now) and `git grep -c "_folded_scoring" -- promptpotter` is 0 (2 now).
- **The second seam re-resolves the dataset by name** (w10-survey): `pipeline_resolve.py` calls
  `resolve_dataset(` five times (`_draft_dataset`, `_draft_local`, `draft_backend`, `draft_layers`,
  `resolve_pipeline_for_dataset`) and `datasets/startability.py` four more, so one request resolves one
  dataset several times instead of consuming the first seam's `ResolvedDataset`. Root: one resolved dataset
  per request, passed in. **Rides with:** **A dataset read is a third resolution**. **Re-test:**
  `git grep -c "resolve_dataset(" -- promptpotter/application/pipeline_resolve.py` is at most 1 (5 now).
- **A definition that cannot be read becomes "no extra needed"** (w10-survey):
  `datasets/startability.py::_connector` turns a `DEFINITION_READ_ERRORS` fault into `None`, so
  `dataset_extra` reads it as "needs none" and `offer_connectors` drops the dataset. Root: an unreadable
  definition is `StartBlock.UNREADABLE` or raises. `datasets/loaders.py::_bank` also keeps a source default
  in code (`src.get("shuffled", True)`), which belongs in the dataset file or `defaults.yaml`. **Re-test:**
  `git grep -c "except DEFINITION_READ_ERRORS" -- promptpotter/application` is 1 (2 now) and
  `git grep -c '"shuffled", True' -- promptpotter/application/datasets/loaders.py` is 0.
- **A check-in cannot switch an upload between its connector's pipelines**: a draft edit has no chain
  key, so a mode outside the held chain is disabled (`PipelineSetupSection.tsx`), and
  `draft_build.py::_modes` ignores a definition's own `pipelines`. Action: a mode pick sets the draft's
  `pipelines.default`, its definition half. **Re-test:** pick TermNorm's `default` on an upload; the
  draft's `active_steps` is that chain.
- **The node document is typed two ways and the L2 steer is a side memory**: every door now parses a
  patch through `layer_fold.py::fold_layers` (a null clears), but L2's steer is read through `L2Memory.steered` /
  `steered_layout` beside the fold, not as a named layer of `l1_generate`'s config, and about 58 of
  the engine's `dict[str, Any]` sites are node-config bags (`dict[str, NodeCall]` on `CycleStart`,
  `dict[str, dict[str, Any]]` on `ForkStart`, which carries a null clear and text a `NodeCall` refuses
  before the schema types it), and schema, parsing, overlay and knobs each hold a view of the document
  (~11 modules). Action: one typed node document with knobs a view on it; the steer a named layer. It
  closes the pipeline-switch row. **Rides with:** cfg.
- **The pipeline carrier is typed only at its door**: `PipelineNode.current_config` is a `NodeCall`
  now, but two laid dumps still read the model by string: `measurement_log.py::_span` reads a cell's
  `node_configs` (the archive address, `measurement_archive.py::config_key`'s `list[tuple[str, dict]]`)
  and `injections/wounds.py::_format_runtime_failure_group` reads `RuntimeFailure.observed_config`,
  laid as a dict by `scoring/arm_states.py`. Action: the archive address and `observed_config` carry
  a `NodeCall` per node, each hash reading its laid dump. **Re-test:**
  `git grep -cE "cfg\.get\(\"(model|provider)\"" -- promptpotter` is 0.
- **A campaign names its connector, and five readers look it up again** (w9-payer):
  `Campaign.connector` / `Environment.connector` are `str`, so `wiring.py:258`,
  `admission.py::campaign_sends`, `mint_and_start.py:123`, `mask/load.py:206` and
  `pipeline_resolve.py:1043` each call `CONNECTORS.get`. Root: the campaign domain
  (`domain/campaign.py`) holds the `Connector`. **Re-test:**
  `git grep -c "CONNECTORS.get(.*campaign.connector)" -- promptpotter` is 1.
- **A dataset read is a third resolution** (w7-res3, w7-rows6):
  `pipeline_resolve.py::resolve_pipeline_for_dataset` parses and narrows a schema beside `RunResolution`.
  Root: one resolution, the dataset read a projection of it. **Rides with:** area 07's chain (one typed
  node document). **Re-test:** the function is gone or reads `RunResolution`.
- **A call's plain serializer drops `include=`** (valid-3, 11-14): `promptpotter/domain/pipeline_params.py::NodeCall` —
  `_dump` is a plain model serializer taking no serialization info, so `model_dump(include=...)` returns
  every key; `promptpotter/domain/search_point.py::prompt_edits` read by attribute as a workaround
  (items 631) — the model dump honours `include`/`exclude` like every other model, so no caller reads
  by attribute to dodge it — **Rides with:** `area 07's chain` — **Re-test:** `NodeCall(...).model_dump(include={"model"})`
  returns only `model`.

## 08 · Connectors, backends, LLM clients

**Target:** Connectors are the only place backend identity is named; a built-in is one module binding
`CONNECTOR`; core never infers what a connector should declare; clients are `ProviderSpec` instances; provider
selection explicit.

**Unexamined:** `connectors/dspy_module.py`, `infrastructure/llm/openai_compat.py`,
`infrastructure/llm/base.py`

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
`optimizer_state`; potter's bad prompt is fixed in the dispatch hub; no fourth layer. `optimizers/nodes.py`
holds the member protocols, the round types, the review readings, the knob types and `BehaviourKeys`, and 13
modules outside `optimizers/` import it.

**Unexamined:** `dispatch/facade.py`,
`validators.py`, `dispatch/bundle.py`, `dispatch/layout.py`, `knobs.py`, `dispatch/injections/layer_state.py`,
`l1.py`, `behavior_checks.py`, `optimizer_manifest.py`, `potter/members.py`, `dispatch/injections/panels.py`

- **Selection and the race are written once per peer, not in the backbone** (w10-survey):
  `capo/members.py::PopulationSelector.select`, `gepa/members.py::Pareto.select` and
  `levi/members.py::MapElites.select` each re-derive the leader, a tie rule and the `RoundEnding` /
  `Selection`; `capo/members.py::PairedTRace.decide` and `gepa/members.py::_GateRace.decide` are one
  `scoring/race_protocol.py::next_block` body and both build the `eliminated` `StopSignal`;
  `kind == "eliminated"` is counted in `capo/members.py`, `gepa/members.py` and
  `potter/dispatch/injections/panels.py`; the shared-cell mean is written four times
  (`capo/members.py::_rank_survivors`, `capo/members.py::_readings`, `gepa/members.py::minibatch_improves`,
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
- **Potter's layers keep a second member protocol**: `escalation.py::EscalationLayer` and
  `escalation.py::TransitionResult` carry L2 and L3 beside `nodes.py`'s member protocol, so a layer
  fired by the `escalation` controller parses, lands and reports through its own door. **Re-test:**
  `git grep -n "class EscalationLayer" -- promptpotter` is empty.
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
- **`locus_crossover` re-crosses a measured or hosted child**: it edits every two-parent proposal, so
  a hosted recombination and a rejoined crossover child are crossed again (one call: the re-crossed
  id differs; l4-outer-5's report). Action: a crossover applies to a proposal it drew, never one it
  receives. **Rides with:** the next variation chain. **Re-test:** a hosted recombination keeps its id.
- **An over-budget crossover child books its verdict under axis `variant`**: `bench/children.py`'s
  one length check cannot tell which mutation minted the child. Action: the verdict names the
  mutation kind. **Rides with:** the row above. **Re-test:** a crossover child's OVER_CEILING names
  crossover.
- **The plugin contract lives inside `optimizers/`** (map pass): `optimizers/nodes.py` holds the member
  protocols, the round types, the review readings, the knob types and `BehaviourKeys`, and 13 modules
  outside `optimizers/` import it (`bench/cycle.py`, `bench/node_context.py`, `runner/round.py`…), against
  §0's "the bench never imports an optimizer". **Decided 2026-11-27:** the contract moves below the
  bench; the bench ↔ optimizers knot of **The layering is fictional at runtime** closes with it.
  **Rides with:** the 09 + 03 chain. **Re-test:** no module under `runner/` or `bench/` imports
  `application/optimizers/`.
- **A held round's words are the election match's fall-through** (w8-round, 11-27):
  `promptpotter/application/optimizers/potter/election.py::_verdict_reason` — a HELD round (and
  ADOPTED) reaches the closing `return` of the `match`, since `RoundDecision.ending` is typed
  `RoundEnding`, which admits `adopted` — a selector's ending type is the endings it can decide
  (crowned, moved, held), so each reads its own arm — **Rides with:** `area 09's chain` —
  **Re-test:** `_verdict_reason` has a `case RoundEnding.HELD` and `RoundDecision.ending` excludes
  `ADOPTED`.

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
- **The estimator has no golden corpus**: its key (`nodes.py::BehaviourKeys.cell_estimator`) reads the
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

## 13 · Identity, accounts, access

**Target:** OIDC verified at the API trust boundary, tokens never past it; privileged identity changes ride
the operator-admin channel; a cross-user campaign is a 404; who an account is (user, API keys, tokens) is a
survivor.

**Unexamined:** none (w10-identity read `application/account.py`, `app_visits.py` and
`infrastructure/identity/` against the Target).

- **A grants file that does not load reads as no grants, which fails OPEN for a denied one**
  (`identity/grants.py::read_grant`): its `FileUnreadableError` is logged and read as no grant, so a
  sub-principal whose grant was DENIED (empty `delegated_by`: own tenant, no command rungs) resolves in
  `accounts.py::account_identity` as an ordinary account holding the whole owner set, while every live
  delegate drops to its own tenant. The same reader takes a grant whose delegator holds no `user.json`
  as active (`_delegated_identity` reads the blocklist with no email), and the delegate's first act
  opens the delegator's account (`account.py::account_of`). Action: a grants file that does not load
  refuses the identity it was asked for (503, the box's config broken), never reads as none; a grant
  resolves only onto a delegator whose account stands. Both halves are reads that fail open, and
  neither waits on the operator's open delegation questions (who edits grants, whether a delegate is
  its own tenant), which govern only how a grant is written. **Rides with:** area 13's next chain,
  first. **Re-test:** with `grants.json` truncated, a denied sub-principal's `/auth/me` lists no
  `campaign.*` rung.
- **One token row that fails validation blocks every bearer on the install** (w10-identity):
  `identity/tokens.py::_tokens` refuses the whole file. Root: a per-row load where an unloadable row
  reads as unknown (401 for that bearer alone), and a rewrite keeps the unloadable rows' bytes.
  **Rides with:** area 13's next chain. **Re-test:** one corrupted row in the tokens file leaves
  every other bearer's `/auth/me` answering.
- **An ID token naming several audiences is accepted without its authorized party** (w10-identity):
  `identity/oidc.py::verify_id_token` takes an `aud` list holding the client id without checking
  `azp`, so a multi-audience token issued to another client passes. Root: when `aud` names several,
  `azp` must equal the client id. **Rides with:** area 13's next chain, first. **Re-test:** a test
  token with `aud` [client, other] and `azp` other is refused.
- **The delegated identity reads the default data root, not the install's** (w10-identity):
  `accounts.py::_delegated_identity` and `_token_identity` read `default_projects_root()` while the
  bundle carries the install's paths: a global standing in for a keyed subject. **Rides with:** area
  13's next chain. **Re-test:** `git grep -c default_projects_root -- promptpotter/infrastructure/identity/accounts.py` is 0.
- **The box's own terminal opens no account's key** (w10-seal): the unit and `update.sh::as_unit` are
  handed the box key as a systemd credential, but a verb the login user runs by hand names no
  `BOX_KEY_FILE`, so one reading an account's keys (`wallet_store.py::TenantApiKeyStore.load`) refuses
  `sealed_keys_unopened`. Root: the box's terminal is handed the same credential (one door every
  hand-run verb takes, as `as_unit` is), never a copy of the key. **Rides with:** area 13's next
  chain. **Re-test:** on the box, a hand-run verb reading an account that holds a key opens it.


## 14 · Gate, offline world, tests

**Target:** A fact is proven through a door of the offline world, never a hand-seated record; a guard at one
choke point is tested once; the behaviour keys name decisions; the gate answers from what changed and fits its
budgets.

**Unexamined:** `scripts/offline_run.py`, `scripts/gate.py`, `tests/test_spend.py`, `tests/test_scoring.py`,
`tests/test_security.py`, `tests/test_identity.py`

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
- **Smells the test-shape split left** (reported by its builders): w7-tests opened the peer facts
  through real run init, and the pytest wall went from 77.8 s to 87 s; w7-tests2 shares the bench
  line (`tests/test_scoring.py::banked_line`) and binds each fact's account in
  `tests/conftest.py::own_home`, buying back about half. Left: the budget-cut table
  (`test_a_round_the_budget_cuts_elects_only_past_the_panel_gate_and_stands_once_closed`, ~3 s) opens
  a campaign per case, each writing its own cut under its own panel gate, and a CAPO round on 16 rows
  walks in ~0.7 s, a tenth of it one `fsync` of `charges.jsonl` per send. Action: a round costs what
  its cells cost, found in the run's write path, never by the test. **Rides with:** tests-w's
  successor. **Re-test:** the wall is back under 77.8 s, and `pytest --durations=10` lists no peer
  fact over 2 s.
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
- **Tests seat what a world door should make** (w9-entry, w9-pass): `tests/test_security.py` hand-seats a
  `ForkCut`, and no offline leg exercises CAPO's `locked_in` path, so w9-pass's catch-up is proven by
  unit tests alone. **Rides with:** area 14's chain. **Re-test:** a world leg settles a CAPO race
  `locked_in`; `git grep -c "ForkCut(" -- tests/test_security.py` is 0.
- **`gate.py --changed` proves nothing for a committed link** (w7-ver2, HO1063): on a clean tree it runs 0
  checks, and its `specs` go red when their generator rewrites. **Rides with:** area 14's chain.
  **Re-test:** `--changed` after a commit checks that commit's files, and a regenerated spec reads green.
- **walk-world reads the built key absent while ts-types rewrites it** (w7-open): a gate's walk-world fails
  with `FileNotFoundError` on `application/maintenance/built_key.py` when ts-types regenerates it
  mid-gate. The key stays committed: computed where read, it would cost every process start ~2.8 s
  (`landing.py::_landed` reads it at entry; the closure 2.16 s, `row_schema` 0.6–0.85 s), and the other
  generated files conflict at a merge the same way (orchestrator, 2026-11-27, withdrawing map D-0).
  Root: a generated file is replaced whole, never absent between two writes. **Rides with:** area 14's
  chain. **Re-test:** a gate whose ts-types rewrites the key beside walk-world reads it present.
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
- **The gate stages the landing key in two places** (w8-gate, 11-27): `scripts/gate.py::_ts_types`
  and `scripts/gate.py::_proven_in` both `git add` the landing key, one in the index's own tree and
  one copying back from its checkout, following `--staged`'s two modes — one mode, one stage —
  **Rides with:** `area 14's chain` — **Re-test:** `git grep -c "_LANDING_KEY" -- scripts/gate.py`
  names one `git add`.
- **The gate reports only after every check ends** (tw-x, 11-26): `scripts/gate.py::main` — the
  per-check table and failures print once `_schedule` returns, so a long run cannot be watched — each
  check's verdict line prints when it ends — **Rides with:** `area 14's chain` — **Re-test:**
  `python scripts/gate.py --py` prints a check's line before the last check starts.
- **The idle world's Router is built at three sites** (web-tail, 11-25):
  `scripts/offline_world.py::install_network`'s callers spell `Router(FakeLLM(lambda _body: "ok"),
  FakeBackend([]))` in `tests/world.py`, `scripts/offline_world.py` and `webapp/e2e/serve_app.py` —
  `offline_world.py` owns the idle world's constructor and the other two call it — **Rides with:**
  `area 14's chain` — **Re-test:** `git grep -c "FakeLLM(lambda _body" -- tests scripts webapp/e2e`
  sums to 1.
- **The offline world reports no cached_tokens** (usage-2, 11-19): `scripts/offline_world.py::USAGE`
  — `prompt_tokens_details` holds only `cache_write_tokens`, so every offline prefix reads
  "unreported" where OpenRouter reports a cold 0 — the world's usage carries `cached_tokens` as the
  provider does — **Rides with:** `area 14's chain` — **Re-test:** `git grep -c cached_tokens --
  scripts/offline_world.py` is above 0.
- **test_spend's `_running` hard-codes an identity and a jobs dir as workspace** (tests-x, 11-19):
  `tests/test_spend.py::_running` — it passes `user_id="sub-9"` and
  `workspace=WorkspaceDir(registry.jobs_dir)`, a jobs dir standing in for a workspace — the helper
  takes both from `stores.identity` and the stores' workspace — **Rides with:** `area 14's chain` —
  **Re-test:** `git grep -c 'sub-9' -- tests/test_spend.py` falls with `_running` taking its user from
  its `stores`.
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

