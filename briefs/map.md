# The cleanup map (base `release/next` 176ed7b4c, 2026-11-27)

Areas are §0 buckets laid on packages. Every one of the 434 production modules sits in exactly one area
(sum 114,219 lines = `git ls-files promptpotter | grep '\.py$' | grep -v /assets/ | xargs wc -l`; the two
empty `__init__.py` aside). A row is `**title** (L<line in docs/specs/code-debt-cleanup.md at HEAD>)`.
`=` joins rows that are one defect. `[w9]` = closed by an arc built but not landed (`arc/w9-entry`,
`arc/w9-pass`, `arc/w9-trace`). `RM<n>` = `docs/specs/roadmap.md` line n. `HO<n>` = `.scratch/wave-2/HANDOFF.md` line n.

How the numbers were made: **Size** = lines / modules by the path rule in each area's *Files*. **Δ** =
`git diff --no-renames --numstat v0.8.21 HEAD -- promptpotter` summed per area (all areas: +6,193 / −5,672,
net +521). **Churn** = commits touching a file since 2026-10-15, renames folded
(`git log --since=2026-10-15 -M --name-status`). **Unexamined** = a module ≥250 lines or churn ≥25 whose
`parent/name.py`, unique basename and class names all appear in neither the debt doc nor the roadmap; listed
as `file lines/churn`. Δ counts code moved across an area line as growth on one side and a fall on the other
(03's fall is partly 01's and 06's gain), so read it as where lines arrived, not as who wrote more. Not read
for this map: `scoring/judges/CLAUDE.md`, roadmap 70–195 and 546–586 (landed history, variation, survey, turns).
Folded churn, top five: `domain/records/run_records.py` 97, `pipeline_resolve.py` 84, `runner/round.py` 80,
`jobs/mint.py` 77, `store/campaign_store/store.py` 76.

| # | Area | Lines / mods | Δ since v0.8.21 | Churn | Open rows (+in flight) | Other deviations | Unexamined |
|---|---|---|---|---|---|---|---|
| 01 | Records spine & envelope | 8,772 / 49 | +179 / −189 | 514 | 3 (+2) | 2 | 5 |
| 02 | Store, ledger, projections, landing | 11,685 / 42 | +510 / −390 | 829 | 9 (+1) | 2 | 5 |
| 03 | Runner, round, bench | 10,780 / 36 | +802 / −1,560 | 1,168 | 3 (+3) | 7 | 9 |
| 04 | Scoring, fitness, statistics | 13,817 / 50 | +692 / −629 | 709 | 10 | 6 | 6 |
| 05 | Spend, wallet, quota | 5,493 / 14 | +532 / −410 | 312 | 9 | 4 | 2 |
| 06 | Jobs, launch, commands | 8,809 / 37 | +1,473 / −1,012 | 843 | 6 | 1 | 6 |
| 07 | Config, pipeline, datasets | 11,071 / 36 | +450 / −416 | 694 | 4 | 4 | 11 |
| 08 | Connectors, backends, LLM clients | 6,381 / 24 | +91 / −75 | 360 | 5 | 2 | 4 |
| 09 | Optimizers (potter, capo, gepa, levi) | 12,749 / 45 | +84 / −64 | 833 | 5 | 6 | 11 |
| 10 | L4 | 1,710 / 9 | +57 / −71 | 153 | 4 (+1) | 4 | 2 |
| 11 | Served reads, views, evidence | 13,640 / 36 | +874 / −637 | 709 | 9 | 2 | 10 |
| 12 | Surfaces: CLI, API, MCP, webapp | 6,224 / 33 (+ webapp 37,579) | +247 / −195 | 370 | 11 | 3 | 5 |
| 13 | Identity, accounts, access | 3,088 / 21 | +202 / −24 | 133 | 2 | 4 | 5 |
| 14 | Gate, offline world, tests | scripts 9,862 / tests 27,173 | n/a | n/a | 13 (+2) | 4 | 4 |

Rows: 93 open + 9 in flight = 102, each placed once (a row touching two areas sits in the area of its root).
Same-defect groups fold 15 of them into 5 roots (C1 ×3, root E ×5, the nested send ×3, the charged error ×2, the
node document ×2). Unfiled symptoms named below: 22; roadmap items placed: about 30. The recovered chain
reports add several hundred candidates, counted per area in the last section.

Where the +521 came from: 06 (+461), 11 (+237), 13 (+178), 05 (+122), 02 (+120), 04 (+63); 03 fell 758. The
growth sits where the fewest rows are per changed line, and 13 grew with almost none.

## 01 · Records spine and the round envelope
**Files.** `domain/**` and `shared/**` not claimed below (results, records/, arms, arm_reading, cells, run_standing,
origin_panel, pass_kinds, export, search points, wounds, strict_model, errors, hashing, plugin_registry).
**Target.** A round is a neutral envelope: PROPOSE, MEASURE, SELECT, ADAPT, the headline derived, no field naming a
layer, a critique or an elimination rule (§0). Every ledger fact is a `CycleRecord` in a module of its own under
`domain/records/`, read by kind; a pass that measures is one record with an arm; no record holds a value another
derives; no field without a reader. No I/O, no async; a rename wipes run data, so shapes settle before L4 spends.
`domain/` stays one flat package (76 modules, 21,037 lines): §0 rules out a concept-first re-hierarchy, so the
grouping is this map's file lists, not directories.
**Deviations.**
- **Every pass is a recorded pass (root C1)** (L521) [w9: half; `CatchUpPool` stays, 7 hits/3 files] = **A locked-in
  leader's caught-up cells…** (L41) [w9] = **Overlap and repair keep their own pass families** (L48) [w9].
- **The layering is fictional at runtime** (L138): `type_cycles` baseline 81; knots: the store hub, `spend` ↔
  `run_records`/`sample`/`pipeline_schema`/`pass_kinds`, bench ↔ optimizers (see 09).
- **Names collide across layers** (L557): six basename pairs, `CycleFacts` twice.
- RM283, not a row: θ still rides the envelope (`RoundResult.ranked`, `Election.ruler`, `ArmFit.theta*`), against
  §0's "no envelope field names an elimination rule". Operator-ordered before L4 banks a result; reshapes run data.
- 486 `dict[str, Any]` in `promptpotter/*.py` (`git grep -c`); L107 names ~58 of them as node-config bags.
**Unexamined.** `domain/records/run_records.py` 1,428/97 (the top hotspot once its rename is folded; three rows cite it,
none says what it should be), `domain/results_health.py` 625/15, `domain/arm_reading.py` 467/19, `domain/wire_record.py`
351/4, `domain/export.py` 257/15, `domain/candidate_diff.py` 261/5; `shared/errors.py` 674/41 is mentioned only by class.
**Done when.** `git grep -c "CatchUpPool\|BACKFILL\|\.overlap\b" -- promptpotter` is 0 · no `theta` field under
`RoundStatement` · `complexity_ledger.py::BASELINE["type_cycles"]` is 0 · does every model nested in `RoundStatement`
name its one reader?
**Depends on.** Nothing; everything else rebases on it.

## 02 · Store, ledger, projections, landing
**Files.** `infrastructure/store/**` (less wallet, account, dataset stores), `infrastructure/projections/**`, `ledger.py`,
`run_state.py`, `run_scope.py`, `holders.py`, `producer_lock.py`, `application/maintenance/**`, `domain/sample.py`,
`cycle_index.py`, `cycle_paths.py`, `config/paths.py`.
**Target.** One ingress (`CycleEventLog.append`), projections that are checkouts nothing reads back, the archive one
content-addressed tree filed by the dataset a row measured, `MeasurementArchive` its only gateway. A line's fold is
stated once and read by one keyed read `(hop, scope, fold)`. Survivors migrate, everything else is wiped at a landing.
The store holds no money rule and no served shape.
**Deviations.**
- **A line's fold is stated once and read by one keyed read** (L542): 21 `scan_` functions in `ledger_scan.py`
  (1,082 lines); sits under L127 [w9] and L464.
- **`store/read_model.py` holds the money fold inside the ledger index** (L549), **A protocol with one
  implementation** (L553): one chain; `read_model.py` is 1,241 lines, 54 importers.
- **The feed's version spans a whole store** (L88) · **`domain/sample.py`'s name no longer describes it** (L587).
- **On Windows a replace is refused while a reader holds the file** (L394, Held: design) · **`write_json` writes with
  `indent=2`** (L671, fork) · **A landing key re-derives per call** (L683).
- **A landing waits on every live producer on another commit** (L411; `jobs/interlock.py`, `update.sh` asks).
- Unfiled, HO1064: stamp compaction and the `built_key` rewrite, "left open, not briefed". (HO1061's other store
  symptoms, two placers, mtime as a version and epoch growth, were closed by d525d14f2.)
- Unfiled, w9-trace report: L542's `ledger_scan.py:823/:827` citations are stale.
**Unexamined.** `store/campaign_store/store.py` 734/76 (5th-highest churn, no row; w9-trace moves the index fold into
it), `store/stores.py` 338/37 (the hub of L138's first knot), `store/layout.py` 259/28,
`maintenance/archive_maintenance.py` 580/22, `maintenance/built_key.py` 5 lines/31 commits (a generated constant
committed 31 times). Mentioned once each but never as a shape: `store/io.py` 1,019/35, `store/data_root.py` 825/44.
**Done when.** `git grep -c "for span in spans" -- promptpotter` is 1 · `git grep -n "^class Spent\|^def money_on\|class
Projected" -- promptpotter/infrastructure/store/read_model.py` is empty · a mounted subtree keeps its ETag while another
campaign of its store appends · does `store.py` hold anything a fold should own?
**Depends on.** 01 (the record shapes it folds).

## 03 · Runner, round, bench
**Files.** `application/runner/**` (less `inner/`), `application/bench/**`, `application/initialization/**`,
`run_observers.py`, `run_phase_control.py`, `origin.py`, `embedded_run.py`, `preflight.py`, `domain/phases.py`,
`phase_acts.py`, `activity.py`.
**Target.** The bench walks a manifest's `default` once per round and `initial_population` in a round entered with no
population; it decides nothing an optimizer owns and imports none. Init writes a frozen `Session`, the runner only
`Session.state`; one `RunScope`; a stop is a `StopReason` declared at one door; resume and fork read standing rounds
off the ledger. UNDECIDED: "8 modules" (L252) is a count, not a shape; the target should say which concepts own a
module.
**Deviations.**
- **Wave B — `application/runner/` is 10 modules, not 8** (L252; 9 after w9-entry) · **`runner/termination.py`…** (L256)
  [w9] · **"Rebased to fork" names no fork** (L707) [w9] · **The never-closed-origin edge of `fork_edge`…** (L624) [w9].
- **A max-press resume replays on timing** (L763, Held: unreproduced) · **A resume's wait is unattributed** (L352,
  needs the box timed).
- Unfiled, from l4-words (2026-11-18) and again w9-entry (2026-11-27): `run_observers.py::declare_run_stop` takes a
  paused stop's words from `take_pause() or (interrupted_by, "")` (`:71` at HEAD), so a self-pause cannot say why.
  Reported twice, nine days apart, never a row.
- Unfiled, from w7-open: the embedded fresh door probes its backend after its session opens (not re-tested).
- Unfiled, w9-entry report: `declare_run_stop` requires `interrupted_by` that non-paused stops
  ignore; a stop's error has two doors (`end_run_on` and `Stores.declare_stop(error=)`, the second unused by the run);
  a cancel during `asyncio.to_thread(_cut_rebase_fork)` mints a fork the parent never names.
- Upward imports, no row: `scoring/passes.py` and `evidence/head_to_head.py` import `runner/bench.py`; `jobs/launcher/
  run_job.py` imports `runner/entry.py` while `runner/` imports `jobs/` at 11 sites.
- 13 modules outside `optimizers/` import `optimizers/nodes.py` (`bench/cycle.py`, `bench/node_context.py`,
  `runner/round.py`…): §0 says the bench never imports an optimizer; L138 names it as a type knot only (see 09).
**Unexamined.** `runner/round.py` 1,402/80 (largest runner module, 3rd-highest churn, one passing mention),
`initialization/scoring_scope.py` 199/54, `embedded_run.py` 175/50 (one of the five entry points), `initialization/
session.py` 237/43, `runner/output.py` 82/41, `domain/activity.py` 160/38, `bench/resume_and_fork/repair.py` 13 lines/35
commits (a stub of a retired mechanism), `resume_and_fork/resume.py` 118/34, `origin.py` 253/34. Also hot with one
mention: `bench/node_context.py` 866/71, `run_observers.py` 217/70, `bench/cycle.py` 533/55, `runner/bench.py` 944/51.
**Done when.** `git grep -n "^from promptpotter.application.runner" -- promptpotter/application/scoring
promptpotter/application/evidence promptpotter/application/jobs` is empty or each edge is declared · no module under
`runner/` or `bench/` imports `optimizers/` · what is `repair.py` for? · does `round.py` hold one phase per function?
**Depends on.** 01; 09's contract seam.

## 04 · Scoring, fitness, statistics
**Files.** `application/scoring/**`, `application/intelligence/**`, `application/mask/**`, `domain/scoring.py`, `ruler.py`,
`paired_reading.py`, `fitness_fold.py`, `crowning_bar.py`, `bench.py`, `shared/statistics.py`, `composite.py`.
**Target.** One scoring gateway (`search_point_scorer.py`); a cell is a fact, never a grade; fitness is formula- and
mode-relative and served, never recomputed; one tenant δ ruler; two θ differenced only through `comparable_to` /
`lift_over`; no retry of a (sample, candidate) pair; the bench set is held out from every optimizer node.
**Deviations.**
- **`domain/cell_fold.py::repairable_holes` counts a charged error** (L635) = **A resumed walk buys a charged errored
  cell again** (L642): one root, `ErrorCategory` does not say which errors are fillable (fork: replay or retry).
- **A lift is read two ways** (L175) · **The warm gate counts no se_δ** (L184) · **The paired SE floor is its own rule**
  (L433, Held: decision) · **The composite step has no band…** (L570).
- **A connector's two-valued term reaches no scorer** (L426) · **`ab` under an edited scorer refits what the archive
  still holds** (L118, design call) · **"Which benchmark a row measures" is spelled twice** (L743, Held).
- **The first verify after a pause started 38 s late** (L27, needs a live verify).
- RM348 INVESTIGATE: does an errored cell bias a head-to-head · RM366: a declared `bench: 100` held out 162 rows.
- RM592–614, mechanism not feature: PoBB false cuts 33.5% against its stated level; the pick's evidence does not pool
  across rounds; under `vote: overlap` the origin panel trains and measures at once.
- RM456 (tuning board 1): 50 promotions claimed +0.63 θ and read −0.14 ± 0.16 on fresh rows.
- Unfiled, from tw-q: a round close re-resolves the config it already holds (`measurement_log.py:437` still calls
  `cycle_config` inside the hard-sample view at HEAD).
**Unexamined.** `scoring/sample_measurement.py` 760/41, `scoring/search_point_scorer.py` 248/35 (the sole gateway),
`scoring/formula/compiler.py` 534/20, `intelligence/indexes/axis.py` 258/14, `scoring/row_policy.py` 279/2,
`domain/fitness_fold.py` 381/1 (new in spine4). One mention each: `scoring/query_loop.py` 1,249/51, `domain/scoring.py`
968/32, `scoring/measurement_log.py` 634/44.
**Done when.** `offline_run.py --charged` crossed-resumed `harness.backend_cells` equals crossed's, or the retry is in
`docs/decisions.md` · one constant-side rule in `statistics.py` · an offline replay elects no pick its later reads as
parent contradict (RM616) · is every score a consumer shows served?
**Depends on.** 01 (θ off the envelope), 02 (the keyed read).

## 05 · Spend, wallet, quota
**Files.** `infrastructure/llm/spend_book.py`, `wallet.py`, `pricing.py`, `send_pacing.py`, `store/wallet_store.py`,
`account_spend.py`, `application/jobs/quota.py`, `shares.py`, `install_spend.py`, `capacity.py`, `free_tier.py`,
`head_to_head_price.py`, `domain/spend.py`.
**Target.** One `Budget` at four positions (asked, admitted, standing, spent); spent is a fold of spend facts; only the
run's spend book halts a run; an L4 inner cycle spends under its root's book; a send is filed once, on the run that
sent it; the payer is resolved per cell by `wallet.py::sending_key`; spend is the provider's bill.
**Deviations.**
- **A nested run's send is filed once…** (L535; `mirrored` 14 hits/6 files) = **A nested spend book is wired beside the
  cell that nests it** (L453, Held: what names `_ENCLOSED`) = **A cycle's sidebar spend reads its ledger alone, so a
  cut send reads $0.000** (L464): three rows, one root (an outer run's cost is a fold over its inner ledgers).
- **A cycle's running job is found two ways** (L562) · **`spend_counts` builds a full `SpendBook` per cycle…** (L566).
- **The wallet read under the admission gate grows…** (L476, unmeasured) · **Money text has no sub-cent rule** (L317,
  operator) · **dbllmbench's cells send on the box's key…** (L778) · **The default host pin is a trial with no
  verdict** (L331, paid twins).
- RM357: an optimizer's round price is its name's, not its treatment's · RM362 INVESTIGATE: a CAPO arm is admitted on
  its round ceiling (4,230 cells on 310 rows) · RM725: look-ahead's ceiling is a constant per connector · RM316 fork:
  whether an inner L4 run inherits its outer's overshoot switch.
**Unexamined.** `infrastructure/llm/pricing.py` 587/18, `jobs/shares.py` 292/2. Hot with rows that name only a line:
`jobs/quota.py` 775/71, `spend_book.py` 837/46, `domain/spend.py` 1,111/40.
**Done when.** `git grep -c mirrored -- promptpotter` is 0 · `git grep -n "SpendBook(" -- promptpotter/application` names
no zero-budget book · pause an outer mid inner-round: the inner row reads its cut sends as unreported · can every
dollar a surface shows be traced to one charge record?
**Depends on.** 02 (the money fold leaves `read_model.py`), 01 (`run_records.py`).

## 06 · Jobs, launch, commands
**Files.** `application/jobs/**` (less the spend modules), `application/commands/**`, `command_reading.py`, `domain/job.py`,
`command_kinds.py`, `command_events.py`, `refusal.py`, `store/command_log.py`.
**Target.** Every loop start admits through one seam (`admission.py::request_jobs`); a full box queues; a launch moves
only by a `LAUNCH_MOVES` row through `JobRegistry.move`; an entry point hands data, never a callable; every command
is a `CommandRecord` filed by the sole `CommandDispatcher`; a job file is a survivor, written under the gate.
**Deviations.**
- **A queued launch dies with the asking process** (L18) · **Queue and task-result waits re-read on a clock** (L591)
  · **A first landing leaves `jobs/.live-jobs` unwritten** (L698): three rows, one chain over `jobs/registry.py`.
- Unfiled, from w7-open: a fork's cut is opened beside its admission (`commands/launching.py::fork_cycle`), not
  through a request's `open` (not re-tested).
- **A check-in's cycle plan selects beside its landing's refusal** (L69, Held) · **A fork cannot narrow a dead param
  axis** (L327, decision) · **Child-process facts idle the parent ~8 s** (L692; `command_kinds.py` costs 0.4–0.58 s of
  every CLI start).
- No row: this area grew most since v0.8.21 (+1,473 / −1,012) while holding six rows.
**Unexamined.** `jobs/mint.py` 794/77 (4th-highest churn in the package, zero mentions), `commands/launching.py` 517/65,
`commands/dispatcher.py` 617/46, `jobs/launcher/head_to_head.py` 256/32, `jobs/reaper.py` 136/30, `store/command_log.py`
482/19. One mention each: `domain/command_kinds.py` 1,519/72, `jobs/launcher/admission.py` 713/67.
**Done when.** `resume --step-rounds 1` behind a running cycle starts when the slot frees, the asker gone · no poll
constant waits on another process's write · a first landing on an empty root writes the index · does `mint.py` hold
one rule per kind of mint?
**Depends on.** 01. Only its admission rows wait on 05 (the hold nest) and 07 (the campaign holds its `Connector`);
the registry, queue and mint work does not.

## 07 · Config, pipeline, datasets (the two resolution seams)
**Files.** `application/pipeline_resolve.py`, `pipeline_responses.py`, `config_map.py`, `application/datasets/**`, `domain/
pipeline_*.py`, `campaign.py`, `campaign_config.py`, `layer_fold.py`, `checkin_draft.py`, `config/**`, the dataset stores.
**Target.** Two seams: `resolve_dataset` answers which bytes, `pipeline_resolve.py` which values a campaign runs, the
second consuming the first. Params are one fold (`ParamLayers`); an optimizer's knobs are its nodes' `config`; a
default lives in the dataset's file, never code; one typed node document every door parses. A dataset file is a
partial laid on its chain (`dataset_access.py::definition_chain`), and the overlay is the sole route for a backend
tunable.
**Deviations.**
- Unfiled, from w7-res3 and again w7-rows6: a dataset read is a third resolution;
  `pipeline_resolve.py::resolve_pipeline_for_dataset` (`:1107` at HEAD) parses and narrows a schema beside
  `RunResolution`. Reported twice, "design pick open", never a row.
- **The node document is typed two ways and the L2 steer is a side memory** (L107; ~11 modules hold a view) = closes
  **A check-in cannot switch an upload between its connector's pipelines** (L64).
- **The pipeline carrier is typed only at its door** (L193) · **A campaign names its connector, and five readers look
  it up again** (L786).
- RM735 C10 prompt-field order (open) · RM313 forks: whether an upload's bytes are kept before the parse; a bench's
  total · RM315 fork: whether `enable_2pl_graduation`, `degradation_fatal_fastpath`, `lift_reference`, `fitness_ceiling`
  / `rounds_to_ceiling` stay (asked 2026-11-12, undecided: four config knobs with no verdict).
**Unexamined.** The whole of `application/datasets/` (13 modules, 3,332 lines, one row): `draft_patch.py` 342/40,
`startability.py` 231/28, `ingest.py` 183/26, `draft_campaign.py` 156/26, `origin_resolve.py` 264/21, `loaders.py` 502/20,
`origin_readiness.py` 255/16, `tabular_ingest.py` 557/10. Also `config_map.py` 415/36 (imports `optimizers/nodes.py`),
`pipeline_responses.py` 449/21, `domain/pipeline_parsing.py` 528/19. Hot with thin coverage: `pipeline_resolve.py`
1,292/84 (the most-changed file in the package), `domain/pipeline_schema.py` 1,348/33, `campaign_config.py` 598/39.
**Done when.** `git grep -cE "cfg\.get\(\"(model|provider)\"" -- promptpotter` is 0 · `git grep -c
"CONNECTORS.get(.*campaign.connector)" -- promptpotter` is 1 · pick TermNorm's `default` on an upload: the draft's
`active_steps` is that chain · how many modules hold a view of the node document (target 1 + views)?
**Depends on.** 01.

## 08 · Connectors, backends, LLM clients
**Files.** `connectors/**`, `infrastructure/backend.py`, `docker_host.py`, `outbound.py`, `infrastructure/llm/**` (less spend).
**Target.** Connectors are the only place backend identity is named; a built-in is one module binding `CONNECTOR`;
core never infers what a connector should declare; clients are `ProviderSpec` instances; provider selection explicit.
UNDECIDED: whether a 1,545-line connector is inside "one module here".
**Deviations.**
- **TermNorm serves no rewrite field** (L76) · **PromptPotter does not read TermNorm's served wire contract** (L264) ·
  **TermNorm words a warning's step into its message** (L272) · **`SendBudget` stops at the relay's door** (L277): four
  rows waiting on TermNorm's `xconn` branch reaching the box.
- **A machine's cell ceiling is counted twice** (L754, decided 2026-11-19, unbuilt) · L778 (see 05).
- RM677: the connector as a unit · RM666 R4: host-declared providers.
- Unfiled, from w7-small: the sweep unlinks a dead producer's lock before `docker_host.py::reap_dead_producers` reads
  it, so that producer's containers are never reaped (not re-tested).
**Unexamined.** `connectors/harbor.py` 1,545/38 (the largest module in the package; one mention, for a retry count),
`connectors/dspy_module.py` 475/33, `infrastructure/llm/openai_compat.py` 734/25, `infrastructure/llm/base.py` 290/13.
**Done when.** no machine-held connector's arming reads `_SENDING` · `connectors/termnorm.py` probes `/version` · what
in `harbor.py` is core's work done in a connector?
**Depends on.** 05 (the payer).

## 09 · Optimizers (potter, capo, gepa, levi)
**Files.** `application/optimizers/**`, `application/optimizer_manifest.py`, `domain/optimizer_state.py`, `optimizer_template.py`, `pool.py`.
**Target.** Every optimizer is a manifest whose knobs are its nodes' `config`, registered in the one entry-point
registry, so a third party ships one with no edit here; its working state rides the round as `optimizer_state`;
potter's bad prompt is fixed in the dispatch hub; no fourth layer. UNDECIDED: where the plugin contract lives.
`optimizers/nodes.py` (854 lines, churn 64) holds the member protocols, the round types, the review readings, the
knob types and `BehaviourKeys`, and 13 modules outside `optimizers/` import it.
**Deviations.**
- **Potter's layers keep a second member protocol** (L37; `class EscalationLayer` still 1 hit).
- **`locus_crossover` re-crosses a measured or hosted child** (L746) · **An over-budget crossover child books its
  verdict under axis `variant`** (L750) · **"slot" survives in the text an LLM reads** (L336, paid comparison) ·
  **`l1_critique` on minimax runs a cluster's id list to `max_tokens`** (L480, paid proof).
- RM351 INVESTIGATE: `capo/members.py::_rank_survivors` ranks on another reading than the reference CAPO.
- RM596–610: the early-winner stop on an anytime-valid bound; one crown ends the round; evidence pooled across rounds.
- Unfiled, w9-pass report: no offline leg exercises CAPO's `locked_in` path.
- Kept on the operator's word (RM319): the three recombination routes, `halt_at_fitness`, `block_access` modes.
**Unexamined.** The three peers have no debt row: `capo/members.py` 724/36, `levi/members.py` 675/25, `gepa/members.py`
514/30. Potter: `dispatch/facade.py` 572/43, `validators.py` 335/28, `dispatch/bundle.py` 250/27, `dispatch/layout.py`
366/25, `knobs.py` 334/25, `dispatch/injections/layer_state.py` 374/22, `l1.py` 569/17, `behavior_checks.py` 365/12. Hot
with thin coverage: `optimizer_manifest.py` 1,123/62, `potter/members.py` 652/59, `dispatch/injections/panels.py` 1,068/35.
**Done when.** `git grep -n "^from promptpotter.application.optimizers" -- 'promptpotter/*.py'
':!promptpotter/application/optimizers'` lists only the registry's loader · `git grep -n "class EscalationLayer" --
promptpotter` is empty · a hosted recombination keeps its id · does each peer's module state its paper's rule once?
**Depends on.** 01, 03.

## 10 · L4
**Files.** `application/runner/inner/**`, `domain/l4/**`.
**Target.** L4 is PromptPotter applied to itself through the `promptpotter` connector, one level deep; the law is
`domain/l4/`, the machinery `runner/inner/`; sandboxes a flat per-cycle registry; identity is a built-in's behaviour
key on the offline world, so a rename on a run line re-keys no cell. Goal (operator): early-round AUC.
**Deviations.**
- **The outer's edit menu offers fields of inner nodes that never fire** (L445, Held: who clocks an inner node).
- **No L4 fixture buys an outer cell ahead…** (L607) · **A pressed L4 outer run does not repeat** (L599) [w9] · **The
  paid L4 browser walk's stop was sized for one inner round** (L167, paid) · **The estimator has no golden corpus**
  (L738, Held: a corpus under 1 MB).
- Unfiled, w9-trace report: `runner/inner/spawn.py:119` `inner_campaign_id` hashes the cell key, which carries the held
  estimator, so a run that rewrites `behaviour_keys.json` reads `potter/l4` requests MOVED once more.
- RM523–544 L4 closure: 8 owed items, 3 and 4 measurements (the outer election is unmeasured; arms differ less than
  their intervals) · RM499 open: can a compare campaign seat optimizer-prompt arms?
- `RunScope.depth` / `InstrumentMode.depth` restate `instrument is not None` (inside L453).
**Unexamined.** `runner/inner/tasks.py` 428/40, `domain/l4/proxies.py` 349/21. Hot: `runner/inner/spawn.py` 491/66.
**Done when.** two pressed runs of `offline_run.py --l4` read the same outer requests · an L4 leg pressed past one outer
cell discards one and names it untaken · `InstrumentMode` carries no `depth`.
**Depends on.** 01 and 05 first (each re-key or wipe voids paid L4 runs), then 09.

## 11 · Served reads, views, evidence
**Files.** `application/served_dashboard.py`, `cycle_reads.py`, `hierarchy*.py`, `read_params.py`, `library.py`, `health.py`,
`application/views/**`, `application/evidence/**`, `domain/dashboard_rows.py`, `hierarchy.py`, `narration.py`.
**Target.** A served GET mutates nothing and resolves in `application/`; a read is a `ReadKind` row plus its params
model; run state is derived, never a freshness guess; every score is served, never recomputed in the consumer; the
lineage tree is a read model that decides nothing; a value is its aggregate plus the live round, never a copy of history.
**Deviations.**
- **The live reads re-serve history per poll** (L58) · **The lineage subtree sorts every candidate per candidate**
  (L83) · **`cycle_reads.py` copies the live round's candidates into a second shape** (L715, blocked on `LineArm`).
- **An inner campaign serves a Delete no door presses** (L244) · **A wiped campaign answers two failure kinds** (L770,
  Held: unreproduced).
- Exhibit, one access chain: **The account feed never re-asks an exhibit's reads…** (L296) · **`ActiveSessionResponse`
  carries `hop` and `said` together…** (L299) · **A follower reads with the host's whole `Stores`** (L302) · **A
  follower's live tail outlives its switch** (L310).
- L562 and L566 sit in `served_dashboard.py` (see 05).
- Unfiled, w9-pass report: narration still emits a "Round N" line for a restatement; a fork child's readout may print
  a full block for the restated branch round.
- Unfiled, from w7-rows2: the masthead's Candidates figure is `RunStanding.candidates`, a second count beside
  `ServedDashboard.line` (not re-tested).
**Unexamined.** `application/evidence/` holds 3,038 lines and one row: `head_to_head.py` 777/39, `comparison.py` 543/9,
`metric_catalogue.py` 276/10, `grid.py` 257/5; `views/evidence.py` 913/6. Also `read_params.py` 928/29, `views/render/
phase.py` 362/35, `render/export.py` 389/18, `render/primitives.py` 351/18, `domain/narration.py` 452/9. Hot:
`served_dashboard.py` 1,091/60, `hierarchy.py` 654/56, `hierarchy_subtree.py` 793/40.
**Done when.** `_cycle_cells` per request is O(R·A) · `git grep -n "def earlier_measured" --
promptpotter/application/hierarchy_subtree.py` is empty · the exhibit's hierarchy ETag holds while the host edits a
draft · on a running campaign, does every surface name the same active node and arm?
**Depends on.** 01, 02, 05.

## 12 · Surfaces: CLI, REST API, MCP, webapp
**Files.** `presentation/**`, `main.py`, `application/host_surface.py`, `browser_sign_in.py`, `caller.py`; `webapp/**`.
**Target.** Five entry points share one orchestration layer; no business logic in an adapter; a CLI verb is one `Verb`
row; every write goes through `CommandDispatcher.dispatch`; everything on stdout is also on disk; the served contract
is generated and the browser derives nothing; mobile first.
**Deviations.**
- **The served contract is generated and the browser derives nothing (root E)** (L528) = **Adapters over a flat served
  shape** (L206) = **The browser recomputes engine facts** (L210) = **A hand-kept mirror of the served shapes** (L217) =
  **Served fields the browser never reads** (L229): five rows, one root; `webapp/lib/derivations` is 1,758 lines / 23
  files at HEAD; root E still names a row the doc no longer holds.
- **An open panel lags its trigger by a frame** (L490, Held: iOS) · **A Candidates card draws an empty chart over a
  served lineage** (L495, cause untraced) · **A focus ring is re-skinned outside `base.css`** (L573) · **A pane's data
  read in flight reports nothing** (L578) · **Unverified, for the next change to each file** (L406: five smells in one row).
- **The MCP door's claude.ai connector is unproven on the box** (L288, operator step).
- Unfiled, from feed-version (2026-11-19): a pool or pack chip pressed twice inside one write's ack builds its second
  list from the first's pre-write read (`NodeKnobsPanel.tsx::PoolPicker`; not re-tested).
- RM632 host assistant: deletes `CheckinOutput`, `RaisedCommand`, `shell/families.tsx::familiesNamed`; four operator
  questions open · RM738 A4 · RM743 the lineage strip at 300 rounds.
**Unexamined.** `presentation/cli/main.py` 1,168/64 (the verb table; no row), `cli/commands/launch.py` 757/43, `main.py`
523/40, `presentation/admin_bot.py` 368/22, `cli/commands/writes.py` 308/6. Webapp: `components/candidates` 3,407 lines /
16 files and `components/node-surface` 2,746 / 10 have no row beside L495.
**Done when.** `git ls-files webapp/lib/derivations | grep -v __tests__ | xargs wc -l` falls by the served facts ·
`webapp/lib/test-fixtures.ts` holds no hand-written builder of a served type · the unread-field baseline is 0 · one
capability walked through all five doors behaves the same.
**Depends on.** 11 (what is served), then 14 (generated fixtures need `offline_world.py`).

## 13 · Identity, accounts, access
**Files.** `infrastructure/identity/**`, the user, trail, pack, visit and pointer stores, `application/account.py`,
`app_visits.py`, `bulletins.py`, `exhibit.py`, `shared/identity.py`.
**Target.** OIDC verified at the API trust boundary, tokens never past it; privileged identity changes ride the
operator-admin channel; a cross-user campaign is a 404; who an account is (user, API keys, tokens) is a survivor.
**Deviations.**
- **`api_keys.json` holds account keys in plaintext at rest** (L322, survivor migration) · **A bulletin dismissal has
  no account deleter to ride** (L722: no code deletes an account) · L302 (see 11).
- RM727 delegation (decisions § 0005): who edits grants; whether a delegate is its own tenant · RM309 fork: whether
  the visits analytics move to the site repo · RM374 anon: `shared/identity.py::IdentityContext.host` reads a missing
  issuer as the box operator · RM659 R1 the machine principal.
**Unexamined.** `application/account.py` 628/30, `infrastructure/identity/oidc.py` 521/2, `identity/grants.py` 288/12,
`identity/accounts.py` 264/10, `application/app_visits.py` 273/4. The area grew +202 / −24 since v0.8.21 with three rows.
**Done when.** `cat` of one `api_keys.json` shows no key · an account deletion drops its dismissals in the same write ·
has an access review read `account.py` and `grants.py` since mkey (landing 84)?
**Depends on.** None for the review; 06 for the deleter.

## 14 · Gate, offline world, tests
**Files.** `scripts/**` (`offline_run.py` 3,238, `gate.py` 2,573, `complexity_ledger.py` 1,178, `offline_world.py` 761),
`tests/**` (27,173 lines; four files over 5,000: `test_spend.py`, `test_scoring.py`, `test_security.py`, `test_identity.py`).
**Target.** A fact is proven through a door of the offline world, never a hand-seated record; a guard at one choke
point is tested once; the behaviour keys name decisions; the gate answers from what changed and fits its budgets.
**Deviations.**
- World doors: **19 parked facts wait for a world door** (L649) · **No door yields a recorded capability menu or a
  dataset with a panel** (L629) · **A knob layer's tiers have no world example** (L639) · **An operator's act is atomic
  in the offline world** (L614) · **`tests/test_spend.py` holds the walk's tests** (L583).
- Determinism: **Two wounded legs move under load** (L365) · **capo's skip twin moved once on the Windows desk** (L730) ·
  **The capo offline bench turns on one seed** (L389) · [w9] **The trace hashes a field's spelling** (L157), **A rename
  on a run line re-keys potter** (L619), **The cycle index reads its chain beside the store's** (L127).
- Cost: **Smells the test-shape split left** (L372) · **One guard is tested once per door** (L382) · **The suite runs
  ~3.6 s over `7dce00a96`…** (L677) · **Vitest's start meets load from outside the gate lock** (L725) · RM273: the gate
  and CI quarter hour (three tests over budget, CI's unread `walk-world`, the gate metering CPU).
- **The paper harness tracks no named tree** (L345).
- Unfiled: HO1063 `--changed` specs go red when their generator rewrites; w9-pass: `BASELINE["package_lines"]` stands
  above the actual count; w9-entry: `tests/test_security.py:2650` hand-seats a `ForkCut`; from w7-ver2: `gate.py
  --changed` on a clean tree runs 0 checks, so it proves nothing for a committed link; from w8-tests3:
  `offline_run.py::decided`'s canonical labels move on a resume into the end pass (C0 reads as `parent@R1`); from
  w7-open: walk-world fails with `FileNotFoundError` while ts-types regenerates `built_key.py` mid-gate.
**Unexamined.** `scripts/offline_run.py` at 3,238 lines is the largest script and the proof every chain leans on;
`gate.py` 2,573; the four 5,000-line test files are named in rows only for single tests.
**Done when.** `git grep -c "write_resolved_run(" -- tests/` is 0 · no test in `tests/test_spend.py` patches
`execute_round` · the proof run beside a busy pytest reads every leg UNMOVED · `CI=1 gate.py` wall against 277 s.
**Depends on.** 01–05 settle first (each reshape re-keys the world); w9-trace lands before any chain here.

## Recovered chain reports: unfiled candidates per area
Source: `C:\Users\dsacc\.claude\jobs\2f9e3f92\tmp\recovered_symptoms.md` (235 report sections, 2026-11-13 to 11-27;
781 bullets, 746 distinct). Method: a mechanical pass over every item, then 26 of the unfiled candidates drawn at
random (seed 7) and re-tested by grep at HEAD. The mechanical classes: the item's bold title is a debt row at HEAD
(44); a symbol it names appears in a debt row (167, a loose match); its symbols are gone from the code (42); it
names no symbol or path (88); its symbols are alive and no row names them (405, the candidates). Of the 26 sampled
candidates: 9 fixed since, 5 not a defect (a cut note, a stale-claim note), 1 a duplicate, 11 still plausible, 4 of
those confirmed by grep. Scaled, the 405 hold about 170 still-plausible unfiled items (roughly 95 to 250 at n = 26),
against 93 open rows. The items placed in the areas above come from the sample and the file's first blocks; the
rest are counted here, as all dates / since 2026-11-25:

| 01 | 02 | 03 | 04 | 05 | 06 | 07 | 08 | 09 | 10 | 11 | 12 | 13 | 14 | no path |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 12 / 5 | 39 / 15 | 34 / 11 | 21 / 6 | 23 / 4 | 24 / 7 | 15 / 6 | 7 / 1 | 15 / 6 | 5 / 1 | 14 / 5 | 60 / 8 | 3 / 2 | 69 / 37 | 64 / 14 |

Each area chain's census re-tests its own candidates (about 0.4 of them survive) before it edits.
