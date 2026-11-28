# Backbone end state, bottom-up

Tip `ff429d376`, worktree `wt-land-w7`. Paths are under `promptpotter/` unless they start with `scripts/`.

**How this was built.** I read the run-context and store knots myself (`initialization/session.py`, `bench/cycle.py`, `bench/node_context.py`, `runner/{round,entry,loop}.py`, `run_scope.py`, `campaign_store/store.py`, `domain/campaign.py`, `scripts/offline_run.py::_unvouched`). Six readers read the other areas in full and returned inventories; I used their counts as leads. Counts marked (g) are my own `git grep` at the tip. Line numbers are at the tip. Size estimates for the end state are estimates, not measurements. The debt doc, `.scratch/map` and `.scratch/wave-*` were not opened before sections 1–6 were written.

**Correction to the fact sheet.** Four hub paths no longer exist at the tip. `runner/bench.py` is `bench/line.py`, `optimizers/nodes.py` is `optimizer_contract.py`, `runner/output.py` is `bench/export.py`, and `domain/records/run_records.py` (32 commits) was split into ten record families on 2026-11-28. `application/layer_fold.py` is `domain/layer_fold.py`. `maintenance/built_key.py` (39 commits) is gone; `maintenance/landing_key.py` (53 lines) hashes store row schemas and is unrelated to optimizers. Section 4 uses commit counts that follow renames.

---

## 0. The picture in one page

The backbone carries about sixteen concepts. Most of them have one good core already. The weight is in what surrounds each core: a second carrier, a second fold, a second spelling. Five findings shape everything below.

1. **One run, six context carriers.** A run is described by `Session` (14 fields), `LaunchState` (9 mutable fields hung on the frozen `Session`), `PreparedRun`, `RunObservers`, `RunScope` (a context variable with 14 fields) and `Cycle` (20 fields). `session: Session` is a parameter at 116 sites in 25 files (g); `session.state.` is read 112 times (g). 17 of the 51 type-only import cycles point into this knot.
2. **One cycle's history, about eight folds.** `_StandingFold`, `CycleFactsFold`, `LiveDashboardProjection` (24 handlers), `ReadoutProjection` (24 handlers), `RoundAudits`, `_RayTail`, `ActivityFeed` and `RoundFileProjection` each walk the ledger and each rebuild "the rounds of this cycle". `RoundEnteredRecord` is handled in five of them. The result is six round shapes and six wrappers around one `ArmReading`.
3. **One pass, five drivers.** A pass (measure some individuals on some cells under one role) is driven from `scoring/passes.py`, `runner/measure.py`, `bench/line.py`, `bench/node_context.py` and `runner/loop.py`. One cell travels through 12 carrier types to become a pass result.
4. **One bound on spend, twelve carriers.** `Budget` is used four ways (bound, amount, coupon face, share). The chain from account coupon to one send is spelled across `quota.py`, `shares.py`, `admission.py`, `spend_book.py` and three record kinds.
5. **The optimizer boundary is not where the contract says.** Members import `bench.node_context` (13 sites), six `scoring` modules (13), `bench.resume_and_fork.replayers` (5), `bench.llm_call` (3) and 38 distinct `domain` modules. And the behaviour key hashes every module-level import statement of a member file by its module path (`scripts/offline_run.py:1910-1935`). So renaming any of those modules re-keys every built-in optimizer. `assets/behaviour_keys.json` changed in 43 commits since 2026-11-14; most of those changed behaviour on purpose, and this is one cause of the ones that did not.

Finding 5 sets the order. The first structural move fences the member import surface and makes module renames free. Later moves then re-key nothing, except the one landing that renames classes members bind (`RoundResult`, `RoundEnding`), which is also the wipe.

**End-state totals (estimates).** About 4,300 lines deleted from the backbone, about 1,500 moved out of it into the read surfaces (`views/`), 51 type cycles to about 3, one wipe of run data, two re-keys (M2, and M10 on the wipe landing). Without fork F1, every move that renames a module a member imports re-keys as well. The retune capability (section 8) adds about 700 lines.

---

## 1. The concepts the backbone should have

Each row: owning module in the end state, what it is, and where it lives today.

### 1.1 Record
- **Owner:** `domain/records/**`, registry `domain/record_kinds.py`.
- **Is:** one fact on a cycle's ledger, stated once.
- **Today:** 47 kinds in 18 files (1,566 lines). The home is right. The facts are not stated once:
  - `election` equals `round_closed.election` (2 kinds). This one stays: the election is appended before adapt and the close, and the readout, the live dashboard and the standing fold read it in between (`readout.py:433`, `live_dashboard.py:602`, `ledger_scan.py:477`). It is the live event of a fact the close settles.
  - `cycle_final.final` restates `bench_scored`, `level_series`, `launch_clock`, `run_phase.spend`, a whole nested `ErrorRecord` and the stop reason (6 facts restated).
  - `StopReason` is a field on 3 kinds; `CycleIndex` holds `stop_reason`, `finished_at` and `error` beside the same three inside its `final`.
  - `round_proposed.walked` and `round_closed.optimizer_state` are both an `OptimizerState`.
  - An individual with its overlay and validation failures sits in 4 kinds (`round_proposed.proposals`, `arm.state`, `round_closed.arms`, `run_phase` `ArmsGate.drafts`).
  - `token_usage` spells `TokenAccount`'s five fields under other names; `spend_tombstone` and `trail_tombstone` share three fields and a verbatim property.
- Two append doors: `infrastructure/llm/telemetry.py::append_record` (58 calls in 15 files (g), ambient) and `CampaignStore.append(hop, …)` (`store.py:212`).

### 1.2 Round (the envelope)
- **Owner:** `domain/round.py` (today `domain/results.py`).
- **Is:** what one round proposed, measured, selected and left as state, neutral to the optimizer.
- **Today:** 4 classes for the write side and 6 for the read side.
  - Write: `RoundOutcome` (`results.py:146`), `RoundResult` (`:253`, adds `at_offset, cells, panel_rows`), `RoundStatement` (`records/rounds.py:36`, adds `cells, panel_cells, positions, run_standing, panel_precision`), `RoundClosedRecord` (`:116`, empty body).
  - Read: `StandingRound`, `IndexRound`, `RoundSummary`, `ServedRound`, `HierarchyRound`, `RoundAudit`.
  - In memory: `Cycle._rounds` with a version counter and three memos (`cycle.py:134-150`). `close_round` rebuilds the round with `model_copy(update=…)` four times (`round.py:592-608`).
  - "The round advanced" is spelled 6 ways: `RoundEnding.CROWNED`, `RoundOutcome.improved`, `Election.selected`, verdict `elected/uncontested`, `RoundAdvance.ADVANCED`, `RunStanding.improved/advanced`.
  - Optimizer vocabulary inside it: `Measuring.block`, `n_priors`, `ending=moved` ("potter's `next_parent`"), `Reelection` ("a layer restating"), `origin_panel.reading.stall` ("L1 patience").

### 1.3 Run context
- **Owner:** `application/run/context.py`.
- **Is:** what a running launch holds, at four nested spans: `Launch` (the process), `CycleRun` (one bound cycle), `RoundContext`, `NodeContext`. Each is built once by one builder; each owns the mutable state of its own span.
- **Today:** 13 carriers.
  - `Session` + `LaunchState` + `ScorerSetup` + `LaunchClock` (`initialization/session.py`), `PreparedRun` (`runner/run_init.py:61`), `RunObservers` (`run_observers.py:132`), `RunScope` (`infrastructure/run_scope.py:71`), `Cycle` + `SliceMap` + `RoundContext` (`bench/cycle.py`), `NodeContext` (`bench/node_context.py:755`).
  - Launch side: `LaunchRequest`, `Opening`, `HeldBudget`, `OpenedCycle` (copied field by field at 3 sites).
  - How a run ends: `LoopEnd` (a 7-field tuple, `loop.py:42`), `_CycleOutcome` (`entry.py:270`), `end_run_on`, `StopLoop`, and `declare_run_stop` at 3 doors in `entry.py`.
  - The same handle is reachable three ways: `session.store`, `run_scope().account`, and a `stores: Stores` parameter (438 sites (g)).

### 1.4 Pass
- **Owner:** `application/scoring/pass_run.py` (driver) and `domain/pass_kinds.py` (the table of kinds, which exists).
- **Is:** one measurement of named individuals on named cells under one role, with its bracket on the ledger and its reading.
- **Today:** `PASSES` has 7 rows, and that part is one mechanism. Around it, per kind and by hand: the row choice (46 grep hits in 19 files), the ledger bracket (`_bracketed` in `passes.py:106` and by hand at `line.py:531-575`), the banked pass type, the reading, and the `PassGradedRecord` (built at 4 sites). Drivers sit in 5 files (see finding 3). Two names for the thing: "pass" and "walk", plus "query loop" and "phase".

### 1.5 Cell
- **Owner:** `domain/scoring.py` (facts and grade), `infrastructure/store/measurement_archive.py` (the stored row).
- **Is:** one sample measured on one configuration: its facts, and separately its grade.
- **Today:** 7 types name a cell (`MeasuredCell`, `GradedCell`, `LedgerCell`, `FiledAnswer`, `WalkedCell`, `CellRow`, `Cell`). The chain from backend reply to pass result is 12 carriers long; `QueryLoopResult`, `ScoredWalk`, `_Acquired` and the one-implementation `WalkRecorder` protocol are links with no content of their own. "Which row of a sample wins" has 4 rules. "Replayed or paid" is one property stamped at 3 sites and counted at 10.

### 1.6 Reading
- **Owner:** `domain/fitness_fold.py` (a level) and `domain/paired_reading.py` (a pair).
- **Is:** a level of one arm on a sheet, or the paired difference of two arms on the cells both scored, in any measurand.
- **Today:** one core each (`pooled_band`, `fitness_fold.py:280`; `_lift`, `scoring/paired.py:445`) and many shells: 8 fold wrappers, 3 hand-computed means, 5 sites that build a pair member, 5 that build an `UnreadPair`, 6 that compute a paired difference. `Scorer` and `FoldBasis` each declare the scorer's four identity fields.

### 1.7 Cycle state (the one fold)
- **Owner:** `infrastructure/store/ledger/cycle_state.py`.
- **Is:** everything a cycle's ledger says about its rounds, arms, standing, stop and limits, folded once and read by every surface.
- **Today:** the `Line` / `LedgerIndex` machinery (`read_model.py`) is the canonical read and it is good. Beside it stand 29 fold classes and 7 projections. The duplicates this produces:
  - "Latest standing" picked twice (`ledger_scan.py:388`, `live_dashboard.py:520-537`).
  - Displacement below a re-entered round at 3 sites.
  - "Which cycle ran round r" by 3 different rules (`audit_trail.py:138`, `served_dashboard.py:601`, `hierarchy_subtree.py:95`).
  - Standing budget folded twice (`StandingBudgetFold`, `RunLimitsProjection`).
  - Arm rows built at 3 sites; arms reach the dashboard through the projection and the tree through the read-time fold.
  - `dashboard.json` is written and read by no code; `fold_at` refolds for serving.

### 1.8 Line (the fork chain)
- **Owner:** `domain/cycle_line.py` (pure) with one store read in `infrastructure/store/ledger/`.
- **Is:** the ordered cycles a round's history runs through, and which cycle owns round r.
- **Today:** the parent or fork chain is walked at 7 sites and the supersede chain at 3. `_supersede_line` (`ledger_scan.py:768`) and `supersede_line` (`hierarchy_subtree.py:110`) share a name and return different things.

### 1.9 Budget and the bound chain
- **Owner:** `domain/spend.py` (the types), `application/spend/` (admission and shares), `infrastructure/llm/spend_book.py` (the per-send meter).
- **Is:** an ordered chain of bounds from the account's coupon down to one send, each level holding out of the one above it.
- **Today:** 12 carriers (`Budget`, `Remainder`, `HeldBudget`, `SpendCount`, `StandingBudgetRecord`, `CampaignCeilingRecord`, `CycleShareRecord.allotment`, `Hold.bound`, `Coupon`/`AccountWallet.headroom`, `SendBound`, `tier_ceiling`, `RunLimits`). `share` and `allotment` name one thing in 4 places. The declared budget is read at 7 sites and composed over the standing one two ways. The floor-at-zero remainder is written 5 times, the reserve rule 3 times, `sent_usd` 3 times. `admit_and_share` holds 8 `Budget` locals (`admission.py:402-457`). The chain already has its canonical form: `Hold` carries `parent` and `bound` (`records/money.py:50-70`), so a tree of holds on the charge book is the chain. The three bound records and `share` are second spellings of levels in that tree.

### 1.10 Launch
- **Owner:** `application/launch/` (today `application/jobs/launcher/` plus `jobs/mint.py`).
- **Is:** request, then one admission, then one open: three stages.
- **Today:** 5 request variants (which are real variants), 3 holder kinds (`Hosted`, `Inline`, `Asked`), and 6 stage types past admission (`Admitted`, `Launched`, `AdmittedHeadToHead`, `HeldLaunch`, `HeldRun`, `JobSpec`). `admitted` names six things. `admission.py` (715) holds 9 concerns, of which only two are admission.

### 1.11 Resolved run (config)
- **Owner:** `application/config/resolve.py` (today `pipeline_resolve.py`), with `domain/layer_fold.py` as the one fold engine.
- **Is:** the values one cycle runs with and which layer set each.
- **Today:** one fold engine (`fold_layers`, 15 call sites, 5 rules) and that is right. Around it: 8 wrappers, 8 merges that bypass it, 10 carriers between bytes and run values (`ConfigLayers`, `LaidConfig`, `FoldedParams`, `Resolution`, `RunResolution`, `DraftPipeline`, `DraftSource`, `ServedPipeline`, `PipelineRead`, `CycleConfig`), and four `PipelineSchema` values per run (`declared`, `schema`, `chain`, and `ran` recomputed at `pipeline_resolve.py:971`). A node's settable values have 7 names (params, config, knobs, levers, call settings, loci, axes). A partial document has 7 (overlay, layer, patch, edit, block, overrides, narrowing). 4 annotation walkers. Validation in 4 places.

### 1.12 Identity
- **Owner:** `domain/identity.py`.
- **Is:** the hashes that name a measurement, an individual, an origin, a treatment and a behaviour.
- **Today:** 18 hash functions in 11 modules. `config_key` (`measurement_archive.py:106`) and `PipelineSchema.sp_hash` (`pipeline_schema.py:1260`) hash the same chain at two lengths, one over keyed params and one over bare params. The behaviour key is computed in `optimizer_manifest.py:1062` and written by `scripts/offline_run.py:1872`.

### 1.13 Optimizer contract
- **Owner:** `application/optimizers/contract.py` and `application/optimizers/toolkit/`; the only application modules a member imports.
- **Is:** the role protocols, the carriers that pass between roles, the reads a member may ask of the cycle, and the helpers a member may build on.
- **Today:** `optimizer_contract.py` (856) holds protocols, knob types, round carriers, `BehaviourKeys` and two registries. The reads (`Lineage`, `Parent`, `Ruler`, `Bounds`, `Target`, `Calls`, `Children`, `Replay`, `Progress`, `RoundPanel`) are declared and implemented in `bench/node_context.py`, which members import. Coupling has 3 shapes (`MemberCoupling`, `Coupling`, `ConfigCoupling`). Potter vocabulary lives in `domain`: `Critique` in `optimizer_state.py`, 8 `ValidationReason.L1_*` in `failures.py`, `layer_parse_failure`, `NurseOwner.L1`.

### 1.14 Selected optimizer
- **Owner:** `application/optimizers/selected.py`, with `manifest.py` (find, read, switch) and `plan.py` (roles in walk order) beside it.
- **Is:** one manifest resolved for one launch.
- **Today:** `optimizer_manifest.py` (1148) with 14 concerns; `SelectedOptimizer` (347 lines) mixes panel sizing, knob and pool access, forwards to members, levers and identity hashing.

### 1.15 Command
- **Owner:** `domain/commands/` (a package by family).
- **Is:** one row per command kind holding every per-kind fact.
- **Today:** `command_kinds.py` (1536): 45 kinds, an enum row of 5 columns, and 12 more per-kind tables in 7 files (`PAYLOAD_MODEL_FOR_KIND`, 5 payload ClassVars, `_REPLAYS`, `keeps_answer`, `_rung`, `LOOP_KINDS`, `RUN_VERB_KIND`, `_NAMED`, a hand list in `admin_bot.py`).

### 1.16 Run status
- **Owner:** `domain/phases.py`.
- **Is:** three axes and no more: where the loop is (`CampaignPhase`), why it stopped (`StopReason` with its outcome), and what the producer is doing (`RunPhase`, derived).
- **Today:** 12 vocabularies describe a run or cycle (`RunPhase` 8, `StatusMark` 12, `StopOutcome` 4, `StopReason` 28, `PauseCause` 6, `StopCategory` 6, `ProducerState` 7, `DashboardState` 9, `CampaignPhase` 8, `LaunchState` 5, `JobStage` 3, `LifecycleStatus` 3). `paused` is a member of 3, `running` of 3, `held` of 5. `CampaignRefusal` is repeated value for value inside `PassRefusal`.

### 1.17 Campaign group (new name, existing mechanism)
- **Owner:** `domain/campaign_group.py`.
- **Is:** one declaration that several campaigns are minted under: what they share and what the group is for.
- **Today:** it exists for one purpose only, as the head-to-head: `HeadToHeadRecord`, `SharedPart`, `ArmSeat`/`Arm`, `Compare` (`domain/campaign.py:112-278`), `jobs/launcher/head_to_head.py` (256), the head-to-head half of `jobs/mint.py` (168 lines), `head_to_head_price.py`. Section 8 uses it for the retune.

---

## 2. End-state module tree

Sizes are lines; "today" sums the files that feed each row. End sizes are estimates.

```
domain/                                   23,537 -> ~21,500
  records/**                               1,566 -> ~1,350   one fact per kind
  round.py            (results.py 340 + records/rounds.py statement + RoundSummary)   ~520 -> ~380
  run_standing.py                            449 -> ~400
  phases.py                                  878 -> ~700     3 axes; DashboardState, StatusMark, JobStage go
  cycle_line.py       (new; line algebra from hierarchy_subtree 88-187, ledger_scan, audit_trail)   -> ~150
  spend.py                                   726 -> ~520     Bound / Spent; UI metadata out to views
  spend_rollup.py                            404 -> ~320
  scoring.py                                 948 -> ~750     criterion dial parser (112) to scoring/formula
  fitness_fold.py + cell_fold                398 -> ~300
  paired_reading.py                          732 -> ~650
  identity.py         (new; the hash functions)               -> ~200
  pipeline_schema.py                       1,347 -> ~650     model only
  pipeline_rows.py    (served rows, summaries; leaves the backbone for views)   -> ~330 moved
  output_contract.py  (from pipeline_schema 212-355)          -> ~144 moved
  pipeline_parsing.py                        529 -> ~520     all pipeline validation here
  campaign.py                                376 -> ~300
  campaign_group.py   (new; HeadToHeadRecord, SharedPart, Arm generalised)   -> ~160
  upgrade.py          (new; section 8)                        -> ~120
  commands/           (command_kinds.py 1,536 + command_events 200)   -> ~1,450 in 6 files
  hierarchy.py                               672 -> ~450     Subtree* twins go
  dashboard_rows.py                          536 -> ~380

infrastructure/store/                      9,188 -> ~8,300
  root.py             (new leaf: dirs and handles every store types against; from stores.py)   -> ~120
  stores.py                                  289 -> ~200
  ledger/
    log.py            (infrastructure/ledger.py 52 + the one append door)   -> ~110
    read_model.py                          1,012 -> ~850     file reads (80) out to io
    lines.py          (Line constants, money lines; from ledger_scan 740-953, 1065-1120)   -> ~270
    cycle_state.py    (_StandingFold + CycleFactsFold + what LiveDashboardProjection alone folds)   -> ~520
    small_folds.py    (ledger_scan 105-187, 571-738)          -> ~330
    wall_clock.py     (ledger_scan 956-1062, 1123-1158)       -> ~140
  campaign_store.py   (store.py 721)                         -> ~600
  measurement_archive.py                     644 -> ~620
  data_root.py                               832 -> ~830     stays; it is the survivor table
infrastructure/projections/                1,178 -> ~450
  checkouts.py        (dashboard.json, index.json writers as views of CycleState)   -> ~200
  event_stream.py, activity                  ~250
infrastructure/llm/spend_book.py             829 -> ~650
infrastructure/run_scope.py                  144 -> ~90      7 per-task fields

application/run/      (runner 3,704 + bench 4,556 + initialization 701 + run_observers 240
                       + run_phase_control 231 + run_stop 127 + origin 250 = 9,809)   -> ~7,600
  context.py          Launch, CycleRun, RoundContext, NodeContext         ~520
  cycle_rounds.py     the in-memory rounds, slices and outputs of one cycle   ~300
  launch.py           open and close a launch (entry.py 640 + run_init.py 392)   ~650
  end.py              RunEnd: one value for how a run ended               ~150
  loop.py             round loop and round 0's door                       ~300
  round.py            walk, measure, select, adapt, close                 ~520
  reads.py            the implementations of the contract's reads         ~480
  fork.py, resume.py, ab_replay.py   (resume_and_fork 756)                ~650
  inner/**            (1,053)                                             ~1,000
  llm_call.py 438, children.py 258, difficulty.py 287, task_context.py 268, control.py ~230, export.py 82
application/scoring/  (7,010 + runner/measure.py 730 + bench/line.py pass half ~450 = 8,190)   -> ~6,300
  pass_run.py         the one pass driver, bracket and banking            ~350
  walk.py             Walk and replay-or-measure (query_loop 135-717)     ~520
  schedule.py         turn scheduling and catch-ups (query_loop 776-1340) ~560
  cell_send.py        measure one cell (sample_measurement 453-622)       ~300
  round_pass.py       population and overlap (measure.py)                 ~480
  held_out.py         verify, noise floor, bench (passes.py 665 + line.py 493-720)   ~620
  bench_reads.py      curve, scopes, cycle bench (line.py 186-490, 830-946)   ~420
  pair.py             (paired.py 465)                                     ~380
  formula/**, judges/**, row_policy, race_protocol, arm_states, classification   unchanged
  (measurement_log.py 634 leaves to application/views/cells.py)
application/optimizers/
  contract.py         protocols, carriers, the reads' declarations (optimizer_contract.py 856 + read decls)   ~780
  toolkit/            race_protocol, row_policy, selection, replay, reply models   (re-homed, ~900)
  manifest.py, plan.py, selected.py, behaviour.py   (optimizer_manifest.py 1,148)   -> ~1,000 in 4 files
  potter/, capo/, gepa/, levi/, variation/, pools/   10,193 -> ~9,700
application/config/   (pipeline_resolve 1,282 + config_map 415 + path_patch 66 + datasets/authored)
  resolve.py          ResolvedRun and its one builder                     ~520
  layers.py           which layers exist and where each comes from        ~250
  knobs.py            knob registry and couplings (one shape)             ~260
  (pipeline responses 205+106, config-map response 151 leave to application/views/)
application/launch/   (jobs/launcher 2,041 + jobs/mint.py 811)   -> ~2,300
  request.py, admit.py, open.py, mint.py, resume_drift.py, group.py
application/spend/    (jobs/quota.py 779 + jobs/shares.py 298 + admission money half ~280)   -> ~1,050
  chain.py, coupon.py, rates.py, paid.py
```

The layering stays hexagonal. Three packages are renamed because their names stopped describing them: `bench/` (which also means the held-out set), `runner/` and `initialization/` become `run/`.

---

## 3. Deletions

Estimated lines removed, by root. "Moved" lines are not counted as removed.

| # | What goes | Est. lines |
|---|---|---|
| D1 | `Session`, `LaunchState`, `PreparedRun`, `RunObservers`, `OpenedCycle` as separate carriers; 7 `RunScope` fields that restate the context (`account`, `ledger`, `memory`, `judge_cache`, `spawn`, `determinism`, `abort_check`); `LoopEnd` and `_CycleOutcome`; two of the three stop-declaring doors in `entry.py` | 500 |
| D2 | `LiveDashboardProjection` as a fold (400 lines to a ~200-line view); `RoundAudits` as a fold; `RunLimitsProjection`; `_RayTail` beside `ActivityFeed`; `fold_at` / `run_limits_at` / `_start`; `CycleLedgerTail`'s re-parse | 520 |
| D3 | Round shapes 10 to 3 (`Round`, `RoundClosedRecord`, `ServedRound`); arm wrappers 6 to 2; `SubtreeCampaign` / `SubtreeCycle` / `SubtreeCampaignFacts` twins; the field-copy sites `served_dashboard.py:904`, `dashboard_rows.py:513`, `hierarchy_subtree.py:434,625` | 480 |
| D4 | Fork-chain walkers 7 to 1, supersede walkers 3 to 1, the three "who ran round r" rules to 1 | 150 |
| D5 | `QueryLoopResult`, `ScoredWalk`, `_Acquired`, `WalkRecorder`; `_OriginPanelPairs` and `ReferencePairs` (143 lines) as wrappers of `read_pair`; fold wrappers 8 to 2; 3 hand means; pair-member builders 5 to 1; `UnreadPair` builders 5 to 1; per-kind bracket and `PassGradedRecord` builders 4 to 1; `score_search_point` forward | 600 |
| D6 | `StandingBudgetRecord` + `CampaignCeilingRecord` + `CycleShareRecord` to one bound record; `share` / `allotment`; `Budget` used as amount; the 8 locals of `admit_and_share`; job-argument blocks x3; floor-at-zero x5; reserve rule x3; `sent_usd` x3; 15 pass-throughs in `quota.py`, `admission.py`, `shares.py`, `opening.py` | 450 |
| D7 | `LaidConfig`, `FoldedParams`, `Resolution`, `DraftPipeline` as classes; annotation walkers 4 to 1; the 8 merges that bypass `fold_layers`; `narrow()` written by hand beside its `Lay` annotations; `schema_as_run` x3; validation outside `pipeline_parsing`; `CampaignConfig.identity()` (0 callers) and `_TREATMENT_OWNED`; 11 pass-throughs | 450 |
| D8 | The restated half of `CycleResult`; `CycleIndex.stop_reason / finished_at / error`; `DashboardState`, `StatusMark`, `JobStage` and their tables; `CampaignRefusal` inside `PassRefusal`; the refusal ladder x2; tombstone twins; `SharedPart` / `RunSpec`, `ForkStart` / `CycleStart`, `RebaseRequest` / `ForkSpec` twins; `ArmName` defined twice | 450 |
| D9 | Coupling shapes 3 to 1; `SelectedOptimizer`'s forwards; sampler size computed twice x3; `PairedTRace` / `_GateRace` shared body; `round_facts` stat rows x8; `l1_wire_schema.py`'s hand-built schema beside the pydantic one; `PotterView`'s one-line forwards; `layout.py`'s 4 entry functions to 1; `bundle._REGISTRY` beside `injection_table()`; `dispatch/prompts.py` | 520 |
| D10 | Per-kind command tables 12 to 1 row; dataset-name validator x3; "dump only what was sent" x3 | 180 |
| | **Total** | **~4,300** |

Moved out of the backbone, not deleted: `scoring/measurement_log.py` (634, a read view that writes nothing), response assembly in `pipeline_resolve.py` (311), row serving in `pipeline_schema.py` (~330), the config-map response (151), cap-axis UI metadata in `domain/spend.py` (77). About 1,500 lines.

Kept on purpose: the narrowed cell facts on `sample_scored` beside the archive row. The contract says the ledger is the event log and the archive the database, and neither replaces the other. I name it a checkout and leave it.

---

## 4. Hubs (20 or more commits since 2026-11-14, renames followed)

| File (commits, lines) | Concerns it mixes | End state |
|---|---|---|
| `runner/measure.py` (3 on its own path; it was split out of `round.py`, whose history `--follow` attributes to it; 730) | origin-panel choice; overlap reading; two pair wrappers; population walk; arm open and conclude | Becomes `scoring/round_pass.py`. The two pair wrappers go (D5). It keeps the one thing that is a round's own: racing a population. Not a hub by its own count; listed because it carries half of what made `round.py` one. About 480. |
| `scoring/passes.py` (35, 665) | verify pick, reading and driver; saturation verify; noise floor; bench driver | Splits by what varies: `pass_run.py` owns driver, bracket and banking once; `held_out.py` keeps one row-chooser and one reading per kind. |
| `runner/round.py` (35, 643) | operator arms gate; owed outputs; the walk; adapt; close; controller act; 17 `append_record` calls | Stays central as `run/round.py`. The close stops rebuilding the round four times because `CycleRun` holds one writer for it. The arms gate moves to `run/control.py`. |
| `domain/records/run_records.py` (32, gone) | 47 record kinds in one file | Already split. D8 finishes it: one fact per kind. |
| `campaign_store/store.py` (31, 721) | campaign CRUD; cycle addressing; the keyed read; the append door; lifecycle and delete; budget reads; export; optimized surface; imports `projections` and `run_scope` (upward) | Splits three ways: `ledger/log.py` takes the append door, `ledger/` the reads, `campaign_store.py` keeps CRUD and lifecycle. The upward imports go with the projection (D2). |
| `bench/node_context.py` (31, 838) | 10 read classes; `NodeContext`; four measure helpers; three record builders | Dissolves. Read declarations go to the contract, implementations to `run/reads.py`, measure helpers to `scoring/pass_run.py`, record builders to `run/round.py`. |
| `bench/line.py` (29, 946) | bench pass driver; curve and scope reads; launch clocks; line outlay; campaign refusal | Dissolves. Driver to `scoring/held_out.py`, reads to `scoring/bench_reads.py`, clocks and outlay to `run/launch.py`. |
| `scoring/measurement_log.py` (28, 634) | a served read of cells in two scopes | Leaves the backbone for `views/cells.py`. It co-changes with `evidence/read.py` (11) because both are reads. |
| `optimizer_contract.py` (28, 856) | protocols; knob types; round carriers; `BehaviourKeys`; two registries | Stays central as `optimizers/contract.py` and gains the read declarations. `BehaviourKeys` moves to `behaviour.py`. |
| `jobs/launcher/admission.py` (28, 715) | backend probe; preflight; slot door; guards; money admission; arms; paid call; paid pass | Splits: probe and preflight to `preflight.py`, money to `spend/chain.py`, paid call and pass to `spend/paid.py`. `launch/admit.py` keeps the slot door and the one admission seam, about 250. |
| `projections/live_dashboard.py` (27, 925) | wire models; a 24-handler fold; checkout debounce; run limits | Dissolves into a view over `CycleState` plus a checkout writer. Its highest co-change partner, `served_dashboard.py` (13), then reads the same state instead of a copy. |
| `runner/inner/spawn.py` (27, 489) | inner campaign id; sandbox; seat opening; the wire adapter | Stays. It changes because every launch-shape change reaches it; after 1.3 and 1.10 it builds one `LaunchRequest` and is otherwise untouched. |
| `pipeline_resolve.py` (27, 1282) | 15 concerns: param fold, schema as run, draft source, identity layer, validation, config laying, three resolution carriers, mint facts, response assembly, a sidebar cache | Splits: `config/resolve.py` (the builder), `config/layers.py`, validation to `pipeline_parsing`, responses to views. |
| `scoring/query_loop.py` (25, 1385) | budget-stop translation; replay state; `Walk`; the archive recorder; turn scheduling; concurrency; catch-ups; flight gauge | Splits at its real seam: `walk.py` (one walk's rows) and `schedule.py` (N walks in turn). Stays large and central. |
| `runner/entry.py` (25, 640) | result building; cycle close; launch scope; the single-cycle run with two try blocks; rebase cut; the job entry | Becomes `run/launch.py` and `run/end.py`. The two try blocks and the 11-argument `close_cycle` collapse once the end of a run is one value. |
| `domain/command_kinds.py` (24, 1536) | 45 kinds; payloads; import-time gates; `ReadKind` | Splits by family under `domain/commands/`; one row per kind. Stays a hub because every new command lands there. |
| `bench/cycle.py` (23, 509) | the cycle's rounds and memos; slices; outputs; sibling failures; `RoundContext`; `ReadArm` | Splits: `run/cycle_rounds.py` (rounds, slices, outputs) and `run/context.py` (the contexts). |
| `runner/run_init.py` (22, 392) | launch open; controls arming; entering a cycle; moving onto a fork; INIT | Folds into `run/launch.py`: entering a cycle and moving onto a fork are both "build a `CycleRun`". |
| `optimizers/potter/members.py` (22, 786) | every potter member, plus the escalation wiring | Stays. Its churn follows the bench files it imports (round 10, node_context 8); the fenced surface ends that. |
| `store/data_root.py` (21, 832) | the table of everything under the data root; storage report; classify; load and prove | Stays central: it is the one table every on-disk change must amend. |
| `campaign_store/ledger_scan.py` (21, 1158) | 12 folds; 26 `Line` constants; wall-clock arithmetic; money lines | Splits into `ledger/{cycle_state, small_folds, lines, wall_clock}.py`. |
| `domain/spend.py` (20, 726) | token accounts; payer vocabulary; `Budget`; cap-axis UI metadata; payer spend | Keeps the types. UI metadata leaves. `Budget` splits into a bound and an amount. |
| `domain/hierarchy.py` (20, 672) | admission logic; facts models with `Subtree*` twins; the entry tree | Twins go (D3). Admission logic moves to `application/`. |
| `scoring/search_point_scorer.py` (20, 150) | the scoring gateway | Stays as the gateway, renamed into `pass_run.py`. It is 150 lines with 20 commits because every pass change passes through it; that is what a gateway is. |
| `optimizer_manifest.py` (20, 1148) | 14 concerns | Splits into `manifest.py`, `plan.py`, `selected.py`, `behaviour.py`. |
| `resume_and_fork/fork_siblings.py` (20, 355) | fork config; the cut; steered values; the operator's fork | Stays as `run/fork.py`. It sits in the launch import knot because `Opening` types against `ForkCut`; with three launch stages that edge goes. |

**Where the 51 type cycles knot** (my own count with the ledger's rule):
- Run context, 17 edges: 6 into `initialization/session.py`; `cycle`, `line`, `node_context`, `origin`, `difficulty`, `scoring_scope`, `search_point_scorer` among themselves.
- Store, 16 edges: 9 into `stores.py::Stores`, 5 into `campaign_store/store.py`, from `run_scope`, `spend_book`, `archive_queries`, `account_*`, `dataset_access`, `connectors/protocol`.
- Contract, 11 edges: `optimizer_contract` with `optimizer_manifest`, `config_map`, `bench/cycle`, `bench/node_context`, `pipeline_responses`; 2 inside potter's `dispatch`.
- Launch, 5 edges: `admission`, `mint_and_start`, `opening`, `mint`, `fork_siblings`. Only 2 of these guards break a real loop.
- Domain, 2 edges: `pipeline_params` and `search_point` back to `pipeline_schema`.

---

## 5. Moves

Ordered by dependency. Each is one root. "Wipe" means stored run data is reshaped. "Re-key" means built-in behaviour keys move.

### M1. Delete what has no reader
- **Root:** code and carriers with zero or one reader.
- **Files:** `domain/campaign_config.py` (`identity()`, `_TREATMENT_OWNED`); `scoring/query_loop.py` (`WalkRecorder`); `scoring/search_point_scorer.py` (`score_search_point` forward); `pipeline_resolve.py` (`draft_served`, `draft_config`, `draft_budget`, `_source_pipeline`, `mint_facts`, `campaign_connector`); `jobs/quota.py` and `jobs/launcher/admission.py` pass-throughs; `jobs/mint.py` (`mint_framed_cycle`); `domain/campaign.py` (`ArmName` duplicate); `domain/bench.py` (`CampaignRefusal`).
- **Predecessors:** none. **Wipe:** no. **Re-key:** no (no member file touched). **Size:** S, about -250.

### M2. Fence the member import surface
- **Root:** a member imports only `optimizers/contract`, `optimizers/toolkit` and `domain`; and a module rename stops re-keying.
- **What changes:**
  1. Read declarations move from `bench/node_context.py` to the contract as protocols; `NodeContext` hands each out by a named method gated on `sees`.
  2. `scoring/{race_protocol, row_policy, selection, row_diagnostics}`, the two replay types in `resume_and_fork/replayers.py`, the reply-model types in `bench/llm_call.py`, `ForkDecline` and `StopLoop` re-home under `optimizers/toolkit/` or the contract.
  3. Potter's vocabulary leaves `domain` where no bench file reads it: `ValidationReason.L1_*` and `layer_parse_failure`, after a grep per name. `Critique` stays: `runner/inner/transcript.py:12` imports `CritiqueReadout`, so moving it would make the bench import an optimizer.
  4. Members stop naming bench internals: `append_record` (4 uses in 2 member files), `run_scope` (1) and `inner_sandbox_store` (1) become methods of `NodeContext`. Members' imports of `intelligence` (6), `mask` (2), `infrastructure.llm` (4) and `config.prompt_blocks` (1) are not moved; the contract's docstring lists them as the allowed surface.
  5. `optimizer_manifest.py` splits into four files; `config_map.py`'s couplings join the contract in one shape.
  6. `scripts/offline_run.py::_unvouched` keys an import by the names it binds, not by its module path (fork F1).
- **Files:** `optimizer_contract.py`, `optimizer_manifest.py`, `config_map.py`, `pipeline_responses.py`, `bench/node_context.py`, `bench/cycle.py` (imports), the 6 `scoring` modules named, `resume_and_fork/replayers.py`, `bench/llm_call.py`, `domain/optimizer_state.py`, `domain/failures.py`, all 40 files under `optimizers/`, `scripts/offline_run.py`, `assets/behaviour_keys.json`.
- **Predecessors:** M1. **Wipe:** no (no record or state kind string changes). **Re-key:** yes, all five built-ins, once. **Size:** L. Closes 11 type cycles.
- **What F1 buys and what it does not.** With F1-A a module rename is free. A renamed class or function that a member binds in an import still re-keys. Names members bind today that later moves come near: `RoundResult` (36 uses in 10 member files), `RoundOutcome` (8), `RoundEnding` (11), `RoundStatement` (2), `StopReason` (9), `RoundPairs` (2), `PipelineSchema` (20) (g). M3–M9 keep every one of those names. M10 renames three.

### M3. One run context
- **Root:** D1. `Launch` and `CycleRun` replace `Session`, `LaunchState`, `ScorerSetup`, `PreparedRun`, `RunObservers` and `Cycle`'s context half; `RunScope` keeps its 7 per-task fields.
- **Files:** `initialization/{session, wiring, scoring_scope}.py`, `bench/cycle.py`, `bench/node_context.py`, `bench/line.py`, `bench/difficulty.py`, `origin.py`, `runner/{entry, run_init, loop, round, measure}.py`, `runner/inner/{spawn, tasks}.py`, `run_observers.py`, `run_phase_control.py`, `infrastructure/run_scope.py`, `scoring/{query_loop, sample_measurement, passes, closed_rounds, search_point_scorer}.py`, `jobs/mint.py`, `jobs/launcher/{held, head_to_head, mint_and_start}.py`, `embedded_run.py`, `mask/load.py`, `datasets/origin_readiness.py`. The package rename to `run/` rides this move.
- **Predecessors:** M2 (so the rename re-keys nothing). **Wipe:** no. **Re-key:** no, given F1-A; members bind none of the classes it removes (`Session` 0 uses, `Stores` 0) (g). **Size:** XL (116 parameter sites). Closes 17 type cycles.

### M4. The end of a run is one value
- **Root:** `LoopEnd`, `_CycleOutcome`, `end_run_on`, `StopLoop`'s payload and the three stop-declaring doors state one fact five ways.
- **Files:** `runner/{entry, loop, round}.py`, `run_stop.py`, `run_observers.py`, `run_phase_control.py`.
- **Predecessors:** M3. **Wipe:** no. **Re-key:** no. **Size:** M.

### M5. One store root and one append door
- **Root:** `Stores` is both the aggregate and the type every store beneath it needs, and a record reaches the ledger by two doors.
- **What changes:** a leaf `store/root.py` holds the directories and handles; stores type against it. `append_record` and `CampaignStore.append` become one `ledger/log.py::append`. `store.py` stops importing `projections` and `run_scope`.
- **Files:** `infrastructure/store/{stores, campaign_store/store, archive_queries, account_spend, account_trail, dataset_access, session_pointer}.py`, `infrastructure/{ledger, run_scope}.py`, `infrastructure/llm/{telemetry, spend_book, heartbeat}.py`, `connectors/protocol.py`, and the 15 files that call `append_record`.
- **Predecessors:** M3. **Wipe:** no. **Re-key:** no, given F1-A and M2's step 4 (members no longer name `append_record`). **Size:** L. Closes 16 type cycles.

### M6. One fold of a cycle
- **Root:** D2, D3, D4. `CycleState` is folded once; dashboard, index, tree, readout, round file and audit become views of it.
- **Files:** `campaign_store/ledger_scan.py`, `store/read_model.py`, `store/lineage_queries.py`, `store/family_ray_queries.py`, `projections/{live_dashboard, audit_trail, event_stream}.py`, `served_dashboard.py`, `hierarchy.py`, `hierarchy_subtree.py`, `domain/{hierarchy, dashboard_rows, cycle_index, run_standing}.py`, `scoring/closed_rounds.py`, `views/readout.py`, `cycle_reads.py`.
- **Predecessors:** M5. **Wipe:** no, under one constraint. `index.json`, `dashboard.json`, `rounds/round_NNNN.json` and `readout.log` are run-data rows of `data_root.py::CAMPAIGN_ROWS`, not caches, so reshaping them wipes. M6 therefore keeps every written document byte-compatible and changes only how it is derived. The shape half of D3 (round shapes and arm wrappers on disk and on the wire) lands in M10. Only the fold snapshots (`Fate.CACHE`) are dropped. **Re-key:** no. **Size:** XL. It can land in two steps: line algebra and the one fold first, the dashboard projection second.

### M7. One pass driver
- **Root:** D5.
- **Files:** `scoring/{passes, query_loop, search_point_scorer, paired, sample_measurement, measurement_log}.py`, `runner/measure.py`, `bench/line.py`, `bench/node_context.py` (measure helpers), `runner/loop.py` (origin and noise floor), `domain/{fitness_fold, scoring, verification}.py`, `evidence/read.py` and `hierarchy.py` (pair builders).
- **Predecessors:** M3. Independent of M5 and M6, so it can run beside them on disjoint files, except `bench/line.py` and `closed_rounds.py`, which M6 also touches: chain those two.
- **Wipe:** no. **Re-key:** no, given F1-A, provided request bytes and decisions do not move (the offline run checks that) and `RoundPairs` keeps its name. **Size:** L.

### M8. One resolved run
- **Root:** D7.
- **Files:** `pipeline_resolve.py`, `pipeline_responses.py`, `config_map.py` (what M2 left), `path_patch.py`, `datasets/{authored, draft_patch}.py`, `domain/{pipeline_schema, pipeline_parsing, pipeline_params, pipeline_overlay, pipeline_manifest, search_point, campaign_config, layer_fold}.py`, `preflight.py`.
- **Predecessors:** M3. **Wipe:** no in this move (the `sp_hash` unification waits for M10). **Re-key:** no, given F1-A; `PipelineSchema` keeps its name. **Size:** L. Closes 2 type cycles and 2 more into `pipeline_resolve`.

### M9. Three launch stages and one bound chain (code)
- **Root:** D6 less its record shapes, and section 1.10.
- **Files:** `jobs/launcher/{admission, mint_and_start, opening, launch, held, checkin, head_to_head}.py`, `jobs/{mint, quota, shares}.py`, `queued_launch.py`, `commands/launching.py`, `runner/entry.py` (`run_job`), `runner/inner/spawn.py`, `embedded_run.py`, `domain/spend.py`, `domain/spend_rollup.py`, `infrastructure/llm/spend_book.py`, `infrastructure/store/{account_spend, wallet_store}.py`, `presentation/cli/commands/launch.py`.
- **Predecessors:** M3, M5. **Wipe:** no. The job file (a survivor) holds no budget, and its `stage` is a subset of `RunPhase` values (`domain/job.py`), so it is untouched. `Hold` lines on the charge book keep their shape: the chain uses the `parent` and `bound` fields they already have. **Re-key:** no; members name no spend type (`Budget` 0 uses) (g). **Size:** L. Closes 5 type cycles.

### M10. One wipe: records, envelope, status, bound records, group record
- **Root:** every on-disk reshape the end state needs, landed together so run data is wiped once.
- **What changes:** D8; `Round` as one class (section 1.2); the three bound records become one; `sp_hash` joins `config_key`; `head_to_heads/{id}.json` becomes `groups/{id}.json` with a purpose (section 8); the 6 spellings of "advanced" become one.
- **Files:** `domain/records/**`, `domain/{results, cycle_result, cycle_index, phases, run_standing, arms, origin_panel, campaign, bench, fork, job}.py`, `domain/records/money.py`, `jobs/shares.py`, `infrastructure/store/{data_root, layout}.py`, `store/ledger/cycle_state.py` (the one reader, after M6), `maintenance/landing.py`.
- **Predecessors:** M6 (one reader to change), M7, M9.
- **Wipe:** yes, all run data, once (count first: `ls .promptpotter/projects/*/campaigns`). No survivor migrates: the job file stores `RunPhase` values, which do not change (dropping the `JobStage` alias changes no bytes), and no budget.
- **Re-key:** yes, the second and last. Merging the round classes and the spellings of "advanced" renames classes members bind (`RoundResult`, `RoundStatement`, `RoundEnding`). It lands on the wipe, so one landing carries both.
- **Size:** L.

### M11. One row per command
- **Root:** D10.
- **Files:** `domain/command_kinds.py`, `domain/command_events.py`, `commands/dispatcher.py`, `infrastructure/store/command_log.py`, `presentation/api/deps.py`, `presentation/admin_bot.py`, `presentation/cli/main.py`, `domain/hierarchy.py` (`DeleteKind`).
- **Predecessors:** none; placed late because it is the least central. It can run beside M3–M9. **Wipe:** no (kind strings stay). **Re-key:** no. **Size:** M.

### M12. Retune: group launch, upgrade, adoption
- **Root:** section 8. A feature, so it comes last.
- **Files:** `domain/{campaign_group, upgrade, export}.py`, `domain/bench.py` (columns), `launch/group.py` (from `launcher/head_to_head.py`), `spend/chain.py` (one more level), `connectors/protocol.py` and `connectors/n8n.py` (optional publish), `domain/commands/` (two kinds), `views/` (the event ledger), the five entry points.
- **Predecessors:** M9 (chain and stages), M10 (group record). **Wipe:** no beyond M10. **Re-key:** no. **Size:** L, about +700.

**What can run together.** After M3: M5 then M6 as a chain; M7, M8, M9 and M11 beside it on disjoint files (M7 chains behind M6 on two files; M9 waits for M5). M10 and M12 close the sequence.

---

## 6. Forks

**F1. What an import contributes to the behaviour key.**
- Today a member file's module-level import is keyed by module path and names. A rename of any imported module re-keys every built-in.
- Option A: key an import by the names it binds only. The docstring of `behaviour_key` already promises that "a rename on a line the world ran re-keys nothing".
- Option B: keep it, and batch every rename into two re-keys (M2 and M10).
- **Recommend A.** An import that ran is vouched by the trace like any other line that ran. It costs one re-key, taken inside M2.

**F2. How an adoption reaches the backend.** Backends are read-only today.
- Option A: export only. The operator applies the file.
- Option B: a connector may declare an optional `publish(leaves)`; `adopt` is a command on the command log, the write is recorded on the campaign, and a connector without it falls back to A.
- Option C: PromptPotter serves adopted values and the backend pulls them at call time.
- **Recommend B.** The backend stays read-only during a run; only an explicit operator command writes. C puts PromptPotter in the production path of someone else's pipeline.

**F3. What "Adopt selected" adopts.** A subset of the changes is an individual nobody measured.
- Option A: adopt it unmeasured, labelled as such.
- Option B: the subset is one operator-authored arm (`SubmittedArm`, `runner/round.py:126`, exists) read on the bench set before it is published, paid from the group's budget.
- **Recommend B.** The numbers shown beside the button then describe what is adopted.

**F4. How one budget is split across targets.**
- Option A: equal shares at mint.
- Option B: draw-down. Every target's origin is benched first; then each target holds out of what the group has left, by the rule `share_campaign_ceiling` already uses one level down (ceiling less metered less siblings' holds).
- **Recommend B.** A target with nothing to gain releases its share early, and no second rule is written.

**F5. Who notices a model release.**
- Option A: the engine watches provider catalogues and fires the retune.
- Option B: the engine owns only the `retune` command and the declaration; a scheduled caller (the box's routine, a webhook, a person) files it.
- **Recommend B.** It keeps a sixth I/O kind out of §0 and keeps provider catalogues out of the core.

---

## 8. The retune capability ("New model out? A tested upgrade waits.")

Written before section 7 on purpose: it changes moves, and section 7 checks moves.

**What the picture asks for.** Six targets across three connectors. One event (a model release) re-tunes each within one budget. The result is a dated ledger per event: per target, the change against the current config in right answers, cost and time on 200 examples; per step, what changed; and Adopt all, Adopt selected or Skip, or "no gain found".

**It is five existing concepts and two additions.**

| What the picture shows | Concept that carries it | What the code has today | What is missing |
|---|---|---|---|
| "3 pipelines, 2 skills, 1 pipeline" under one event | Campaign group (1.17) | `HeadToHeadRecord` + `SharedPart`: one declaration, N campaigns, one budget (`domain/campaign.py:163-278`); `launcher/head_to_head.py` mints and admits N campaigns as one (`AdmittedHeadToHead`) | A group whose members differ in dataset and connector and whose purpose is `Optimize`. Today a group exists only for `Compare`. |
| "Within a budget you set" | Bound chain (1.9) | `CampaignCeilingRecord`, `share_campaign_ceiling` (`jobs/shares.py:42`): a campaign's ceiling shared across its cycles | One level above the campaign: the group's hold, parent of each campaign's. `Hold.parent` already expresses it (`records/money.py:57`). |
| "Against the current config" | Origin | The origin is C0, the campaign root. Every round already reads the selection against the origin on the origin panel (`PromptExport.measurement.vs_origin`), and the bench grades origin and selection with one evaluator (`BenchScore.origin / selected`) | Nothing. The current config is the origin. |
| "+1 pt, -35 %, +6 %" | Reading (1.6) | `MeasuredLift` reads one measurand's lift (`paired_reading.py:553`); cells carry `cost_usd` (`domain/cells.py:226`) and `elapsed_s` (`domain/scoring.py:310`) | `BenchColumn` is `fitness | composite` only (`domain/bench.py:146`). Cost and time become columns read by the same pair. |
| "Step 1 · model B -> C" | Resolved run (1.11) and the individual | `ValueLeaf` is one settable value by path (`domain/value_tree.py:45`); `candidate_delta` diffs an individual against its parent (`candidate_diff.py:50`); `RunsWithParam(node, key, value, source)`; `PromptExport.tuned_params` | A diff of the selection against the **origin** by leaf, as data. This is `Upgrade`. |
| "No gain found · nothing to adopt" | Round, standing | A campaign that never selected past its origin exports round 0 (`build_prompt_export`'s docstring) | Nothing. |
| "New model out" | Layer (1.11) | The model menu and `model_floor` layer exist in `PipelineSchema` and `ParamLayer` | The retune's declaration carries one campaign layer that widens each target's model domain to include the new model. No new layer kind. |
| Adopt all / selected / Skip | Command (1.15), Record (1.1) | `SubmittedArm` lets an operator author an arm; `export.json` is written | An `adopt` command, an adoption record, and a connector's optional `publish`. |

**The two additions.**
- `domain/upgrade.py::Upgrade`: for one campaign, the leaf changes from origin to selection and the bench reading per column. `PromptExport` becomes "the upgrade with every leaf, plus prompts as scored"; the two share one builder.
- An adoption: a command naming a campaign and a set of leaves, one record of what was published and how, and `Connector.publish` where a connector offers it.

**The path through the backbone.** `retune` command, group declared (`groups/{id}.json`), N launch requests admitted as one under the group's bound, each campaign runs the ordinary loop from its origin, each closes with its bench pass, the event's ledger is a view over the group's campaigns' `Upgrade`s, `adopt` publishes. No step is new machinery; the loop, the pass, the fold and the chain are the ones every run uses.

**What it changes in the tree and the moves.**
- The group must be one concept before the wipe, so `HeadToHeadRecord` becomes `GroupRecord` in M10, not later. Otherwise M12 forces a second wipe.
- The bound chain in M9 must be the tree of holds, with no fixed set of levels. That is the shape section 1.9 already asks for.
- M7's one pass driver must take the measurand columns as data, so cost and time are columns and not a fourth reading function.
- M12 is then additive: `domain/upgrade.py`, `launch/group.py`, two command kinds, one view, one optional connector method.

**Forks it raises:** F2 (write-back), F3 (adopt selected), F4 (one budget, many targets), F5 (the trigger).

---

## 9. A later port to Rust (tie-breaker only)

Where two shapes were otherwise equal, the design above already prefers the portable one: explicit contexts over ambient state (1.3), read declarations as protocols (1.13), one stored form per fact (1.1, 1.2), one fold as a pure function of the ledger (1.7), pure line algebra in `domain` (1.8), one row per command as data (1.15).

Python-specific mechanisms the backbone leans on today, counted with `git grep` over `promptpotter/` less `assets/`:

| Mechanism | Count | Where it matters |
|---|---|---|
| Pydantic models as the record and wire format | 562 classes in 137 files; 77 `model_validator`, 23 `field_validator`, 17 `computed_field` | Every record. Validators carry rules that would become constructors. |
| `model_copy(update=…)` | 84 | The round is rebuilt this way 4 times per close. M3 and M10 remove most. |
| Models built at runtime (`create_model`, `_partial`) | 21 in 8 files | Draft layers, L1 reply models. |
| `dict[str, Any]` | 496 in 133 files | Overlays, declarations, payloads. The config area is the densest. |
| `getattr` by name | 132 in 63 files | Annotation walkers (4, to 1 in M8), the layout reflection in `store/layout.py` and `data_root.py`. |
| Registries filled by importing modules (`walked(`, `Registry(`, `__init_subclass__`, entry points) | 14 + 4 + 10 + 3 | Record kinds, state kinds, node members, manifests, injections. A port needs explicit tables. |
| One context variable, read ambiently | 1 `ContextVar`; 47 `run_scope()` reads, 28 `scoped(` | M3 cuts it to per-task fields, which map to a task-local. |
| Exceptions as control flow | `StopLoop` raised at 8 sites; `RUN_ENDS` / `RUN_STOPS` caught in 6 blocks | M4 makes the end a value. |
| `functools` caches | 90 in 33 files | Mostly file reads keyed by a stamp. |
| `asyncio.to_thread` | 81 in 34 files | Blocking store reads off the loop. |
| Pickled fold snapshots | 1 (`read_model.py:704-732`) | A cache; a port picks its own format. |
| Class docstring as a tool description; ClassVar tables | 331 `ClassVar` in 36 files | M11 turns the command ones into data. |
| Behaviour key from the Python AST and PEP 669 line tracing | `scripts/offline_run.py:1885-1935` | The hardest to port: identity is defined in terms of Python's `ast.dump`. A port would have to key by behaviour (decisions and request bytes) alone. |

Nothing in the design is grown for this.

---

## 7. Check against the debt doc (opened only after sections 1–6, 8 and 9 were written)

`docs/specs/code-debt-cleanup.md` at the tip holds 82 open rows in `## 01`–`## 10` (5, 9, 11, 14, 8, 11, 5, 8, 6, 5). 42 map onto a move. 40 are residual. Every row is listed.

### 7.1 Rows that map onto a move

| Area | Row (short title) | Move | Note |
|---|---|---|---|
| 01 | The layering is fictional at runtime (51 type cycles) | M2, M3, M5, M8, M9 | 11 + 17 + 16 + 2 + 5 edges. See amendment A2. |
| 01 | Names collide across layers | M6, M12 | The `hierarchy.py` pair in M6; the `head_to_head.py` pair in M12. `power.py` and `cells.py` stay residual. |
| 01 | A closed round banks its election twice | M10 | See amendment A1. |
| 01 | What a child moved over its parent is computed twice | M10 | The upgrade (section 8) needs the one diff, from `loci()`. See amendment A5. |
| 02 | `domain/sample.py`'s name no longer describes it | M7 | The archive row joins the cell concept (1.5). |
| 02 | A line's walks are a flat list every reader re-scans | M7 | With M6 on `closed_rounds.py`. |
| 02 | The `Stores` hub holds a cycle's stop machine | M5 | The ending moves to the one append door. |
| 03 | `BenchStatus.state` spans `read` | M7 | `scoring/bench_reads.py`. |
| 03 | A block race files an `ArmRecord` per arm per turn | M7 | The one driver files an arm's state where it moves. |
| 03 | A refused send's stop and a banked hole's stop share one field | M10 | Status tables (1.16). |
| 03 | A resume's divergence check and its cut are one off-loop function | M3 | `run/resume.py`. |
| 03 | An inner run's spawn is read off the ambient scope | M3 | `spawn` is one of the 7 fields that leave `RunScope`. |
| 03 | A cycle's export skips a lost bank in silence | M3 | One builder of `CycleRun` reads the drawn bank and refuses. |
| 03 | `declare_line_bench` re-derives its scorer | M3 | The scorer is the context's. |
| 03 | An unbound session's address is two empty strings | M3 | `Launch` has no hop; `CycleRun` always has one. |
| 03 | A line's spent rounds are spelled at three sites | M6 | Line algebra (1.8). |
| 04 | A walk ended outside `land` names nothing it bought | M7 | `scoring/schedule.py`. |
| 04 | A connector's two-valued term reaches no scorer | M8 | The resolved run compiles with its connector. |
| 04 | "Which benchmark a row measures" is spelled twice | M7 | |
| 04 | A hard-sample page resolves its own config | M7 | `measurement_log.py` leaves for views and is handed its config. |
| 04 | `SampleIndex`'s fold row carries a `degraded` nobody reads | M10 | A persisted reshape; rides the wipe. |
| 04 | An estimator spec every caller fixes (`ROUND_LIFT_SPEC`, 12 sites) | M7 | One pair builder owns its estimator. |
| 04 | `Election.deciding_pair` is typed wider than it is | M10 | The envelope. |
| 05 | A nested spend book is wired beside the cell that nests it | M9 | Nesting is `Hold.parent`. |
| 05 | A landed job's cycle is asked twice | M9 | |
| 05 | A provider hold is filed twice under a nested run | M5 | Filed once, on the sender, through the one door. |
| 05 | An outer run's overshoot may read its own ledger alone | M9 | The chain reads the enclosing fold. |
| 06 | An arm's admission is held twice | M9 | Three stages; the opening carries its grant. |
| 06 | A check-in's cycle plan selects beside its landing's refusal | M9 | Enabled by M3 (a context over a draft). |
| 06 | A queued launch's ending files where an unasked one does | M9 | |
| 06 | Child-process facts idle the parent ~8 s | M11 | Only the `command_kinds.py` import share (0.40–0.58 s). The rest is residual. |
| 06 | A fork's preview does not serve the refusal its launch meets | M9 | One preflight for every request variant. |
| 06 | A launch released refused reads as Detached | M9 | A state entered and not reported. |
| 06 | A check-in offers Propose on a draft it would refuse | M9 | The refusal half. The wording half is residual. |
| 07 | Params are not one fold | M8 | The 8 bypass merges. |
| 07 | A resolved dataset is resolved again past the seam | M8 | |
| 07 | A knob's provenance is diffed, not read off the fold | M8, M10 | The new mint field rides the wipe. See amendment A6. |
| 07 | The node document is typed two ways and the L2 steer is a side memory | M8, M10 | The typed document in M8; the steer as a layer on the re-key landing. See amendment A5. |
| 09 | A whole-candidate verdict books a placeholder axis | M10 | A wipe and a re-key, which M10 already is. |
| 09 | The plugin contract is spread over seven modules | M2 | See amendment A3. |
| 09 | "wound" survives in potter's dispatch hub | M10 | A rename that re-keys potter; rides the re-key landing. |
| 10 | An inner campaign's id is keyed by the instrument | M10 | Identity (1.12). |

### 7.2 Residual rows (40), and why

- **Measurement on the box or a paid run comes first (8):** 03 a resume's wait is unattributed; 03 a max-press resume replays on timing; 04 the first verify started 38 s late; 04 held re-tests need world legs; 05 the default host pin is a trial; 08 the box keeps 21 dead producers' locks; 09 `l1_critique` on minimax; 10 the paid L4 walk's stop.
- **A decision already taken to hold, or one the operator owes (4):** 02 the Windows replace; 02 `write_json` indent; 04 the constant paired side; 05 a landing refunds nobody (see amendment A4).
- **Another repo (4):** 08 the four TermNorm rows.
- **Statistics, not structure (2):** 04 the warm gate counts no se_δ; 04 an L4 panel averages θ over its cells' own rulers.
- **A cross-process wake that does not exist yet (3):** 04 a freed slot idles (two rows); 06 a command's settle and a retry's wait.
- **One defect in one place, off the backbone's shape (11):** 01 the data-root spec names dataclass fields; 02 the feed's version spans a whole store; 02 a landing waits on every live producer; 02 `list_campaigns` drops an unreadable manifest; 02 a sandbox whose owner file does not parse; 05 dbllmbench's cells send on the box's key; 06 a promoted launch's stderr; 07 an origin finding with an unknown row; 08 a remote backend's cells ask the bound account; 08 `Endpoint.setting`; 08 the docker host's claim.
- **Wording on a surface (2):** 05 a free account's refusal; 06 the run dock says a refusal as a send.
- **A feature or a measured prompt change (4):** 06 a fork cannot narrow a dead axis; 09 "slot" survives in LLM text; 10 the outer's edit menu; 10 the estimator has no golden corpus.
- **Tests (2):** 09 CAPO's settled race path; 10 no L4 fixture buys an outer cell ahead.

### 7.3 What this design finds that no row in 01–10 names

1. **The run context as one root.** Rows name four symptoms (the ambient spawn, the empty address, the re-derived scorer, the silent export), and area 03 lists `session.py`, `cycle.py`, `node_context.py`, `run_observers.py`, `round.py` and `line.py` as unexamined. No row says the six carriers are one concept.
2. **The eight folds of one cycle,** with six round shapes and six arm wrappers. Area 02's Target says "a line's fold is stated once"; no row counts how many times it is stated.
3. **One pass, five drivers, twelve carriers.** Area 04 lists `query_loop.py`, `search_point_scorer.py` and `sample_measurement.py` as unexamined.
4. **The hold tree is already the bound chain.** `Hold.parent` exists; `StandingBudgetRecord`, `CampaignCeilingRecord` and `CycleShareRecord` are second spellings. Area 05 lists `shares.py`, `quota.py`, `spend_book.py` and `domain/spend.py` as unexamined.
5. **The behaviour key hashes import paths,** so a module rename re-keys every built-in (F1). Area 10's Target says "a rename on a run line re-keys no cell"; an import is not treated as a run line.
6. **Two append doors,** one of them (`append_record`) in `infrastructure/llm/telemetry.py`, against "one ingress".
7. **The end of a run is stated five ways** (`LoopEnd`'s 7 fields, `_CycleOutcome`, `end_run_on`, `StopLoop`, three doors). Area 03's Target says "a stop is declared at one door".
8. **`cycle_final` restates six facts** other records hold; `CycleIndex` holds three of them twice more.
9. **Twelve status vocabularies** and six spellings of "the round advanced".
10. **Thirteen per-kind command tables** in seven files. Area 06 lists `command_kinds.py` as unexamined.
11. **Ten config carriers, four annotation walkers, seven words** for a settable value and seven for a partial document.
12. **`scoring/measurement_log.py` is a served read** living in the scoring package; **`admission.py` holds nine concerns,** two of them admission; **`optimizer_manifest.py` holds fourteen.**
13. **Four hub paths on the fact sheet no longer exist,** and `--follow` attributes `round.py`'s history to `measure.py`.
14. **The head-to-head is a campaign group with one purpose,** and the retune is the same group with another (section 8).

### 7.4 Amendments after the check (2026-11-28)

Sections 1–6 stand as written above. These are the changes the check forces. They are stated here and not edited in silently.

- **A1. The election is stored once.** Section 1.1 says both stay. The row is right that one fact sits on disk twice. `ElectionRecord` stays as the event the live surfaces read. In M10 the stored close stops carrying `election`, and the one fold joins it into the round. `RoundOutcome.election` remains in memory. Members bind `Election` nowhere (0 uses), so this adds no re-key.
- **A2. M2 must move the contexts' declarations with the reads.** The row records that moving `CycleRead` alone re-formed runtime cycles, because the reads and the member protocols name the bench's `NodeContext` and `RoundContext`. So the contract declares `NodeContext` as a protocol too (members name it 88 times), and `run/` implements it and imports the contract, never the reverse.
- **A3. The plugin surface: one module, or the contract plus `domain`.** The row asks that the example plugin import one module, and marks it undecided. M2 as written allows `domain` (members import 38 of its modules). A one-module surface means re-exporting domain types through the contract, which the repo's own ledger counts as a shim. My recommendation stays: the contract, the toolkit and `domain`. The operator settles this before M2. It is the scope of M2, not a sixth fork.
- **A4. Decide the refund rule before M10.** M10 wipes every account's run data on the box. The row "a landing that drops other accounts' run data refunds nobody" is an open operator choice, and M10 would repeat the 11-26 incident at full size. It is a predecessor of M10.
- **A5. Two more member-visible changes ride the re-key landing.** The L2 steer becoming a named layer (area 07) and `CandidateDelta` being built from `loci()` (area 01) both change what potter sends or imports. They belong to M10, not to M8 or M12, so the count stays at two re-keys.
- **A6. The knob-provenance stamp is a mint field.** It reshapes the minted record, so that half of the area 07 row rides M10's wipe.
- **A7. Two area Targets differ from this design in wording, not in invariant.** Area 03's Target keeps a frozen `Session` with one mutable `Session.state`. M3 keeps "a frozen context with one mutable state per span" and splits it at the cycle boundary. Area 05's Target is "one `Budget` at four positions". Section 1.9 keeps the four positions and separates a bound from an amount as types.

**Corrected totals.** One wipe (M10). Two re-keys (M2, M10). M10 has three predecessors that are decisions, not code: F1, the refund rule (A4) and the plugin surface (A3).
