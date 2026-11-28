# Backbone end state, merged (tip ff429d376, worktree `wt-land-w7`)

Inputs: `endstate_topdown.md` (TD), `endstate_bottomup.md` (BU), `factsheet.md`, the carousel page, five memory
rules. Paths are under `promptpotter/` unless they start with `scripts/` or `docs/`. Every number marked (g) is my
own `git grep` at the tip; every size marked ≈ is an estimate, not a measurement. Move ids here are **N0–N14** and
**W**; "TD M7" or "BU M5" always names a move of an input design.

**Decided by the operator (2026-11-28), treated as settled, not as forks:**
1. An optimizer's behaviour key keys a member's import by the names it binds, never by module path (BU F1-A).
2. Adoption is an optional connector `publish` behind an `adopt` command; "Adopt selected" is measured as its
   own arm first; export is the fallback for a connector without `publish`.
3. One budget for many targets is a draw-down under one group hold, origins first.
4. A round closes at its election and ADAPT opens the next round (TD F3). It rides the single wipe landing.
5. The engine ships only a `retune` command. No watcher.

**Settled here from the code or a recorded decision (not forks):**
- **Flat packages stay.** `.scratch/map/DECISIONS.md:50`: "`domain/` and `application/` stay flat, since §0 rules
  out a concept-first re-hierarchy". BU's `application/{run,config,launch,spend}/` and `domain/commands/` are
  dropped; its concept merges are kept in today's packages. BU's point that `bench/` names two things stands as
  a naming smell and is not acted on.
- **L4 is a door** (TD F5). Four modules outside `runner/inner/` import it: `bench/node_context.py:36`,
  `initialization/scoring_scope.py:21`, `runner/run_init.py:31`, `maintenance/landing.py:39` (g). BU's "stays"
  leaves bench and INIT importing the runner.
- **The held-out pass has two homes, one per rank.** `scoring/passes.py` imports six names from `bench/line.py`,
  three from `bench/difficulty.py`, `bench/export.py`, `initialization/scoring_scope.py`, `jobs/launcher/admission`
  and `views/readout` (g), so it was never rank-A4 code (TD is right). But `bench/line.py:548` drives the gateway
  with its own bracket too (BU is right that the driver is spelled several times). So: the generic driver
  (bracket, gateway, grade, record) is `scoring/pass_run.py`; the kinds that read a line are `bench/held_out.py`.
- **`scoring/measurement_log.py` has no backbone half.** Its only importers are `read_params.py` and
  `views/readout.py` (g). The whole file leaves for `views/` (BU); TD's "≈150 lines to `row_policy.py`" is refuted.
- **The stop's writer is the store's append door, not `application/`.** Debt row 02 "`Stores` hub holds a cycle's
  stop machine" names the root as "the ending belongs to the campaign store's append door". TD M7 moved the
  declaring function up into `run_stop.py`; that is the wrong direction. `run_stop.py` decides WHICH stop only.
- **A pure fold lives in `domain`, and there is one of them per cycle.** TD only relocates about 20 folds; BU
  collapses them but keeps the result in the store. Merged: BU's content in TD's home (§2).

---

## 1. The one root

The backbone states each of its concepts once at the core and then again around it: a second carrier, a second
fold, a second driver, a second spelling on disk. **No rule makes the second spelling expensive or the merge
cheap**: `application/` has no import order, and a member's identity was keyed by module paths, so a merge
either crossed an area boundary (a chain cut the root in half) or re-keyed every optimizer (it was deferred).
The end state adds the two rules (an import ladder the gate counts; identity by bound names) and then removes
the second spellings in one pass per concept, with every on-disk reshape on one landing.

---

## 2. The concept table

Bucket = the `docs/architecture.md` §0 / §0.5 heading. "Agreed" = both designs name the same end state;
"chosen" = they differ and the reason follows. Counts are (g) unless attributed. Where a cell names more than
one module, **the first is the owner**; the others are where it is executed, stored or tabled, named because a
move touches them.

| # | Concept | One owning module | §0 bucket | Replaces today (counts) | Rust-port note | Status |
|---|---|---|---|---|---|---|
| 1 | Import ladder | `scripts/gate.py::_LAYERING` + one `ladder_edges` count in `scripts/complexity_ledger.py::BASELINE` | Layering | no stated order inside `application/`; 51 type cycles (`BASELINE["type_cycles"]`) | a crate graph is this ladder made mandatory | **chosen: TD.** BU has no rule that keeps the merges merged |
| 2 | Behaviour key | `application/optimizer_identity.py` + `scripts/offline_run.py::_unvouched` | §0.5 behaviour key | an import keyed by `ast.dump` with its module path (`offline_run.py:1910-1935`); `BehaviourKeys` in the contract | hardest to port: identity is defined on Python's AST. Names-bound keying is one step toward "key by behaviour alone" | **decided (1).** TD did not see it and marked six moves "possible re-key" |
| 3 | Plugin contract | `application/optimizer_contract.py` (Protocols for members, `NodeContext`, `RoundContext`, the reads; round carriers; two registries) | Every optimizer is a manifest | member imports of `bench.node_context` (13), `resume_and_fork.replayers` (5), `bench.llm_call` (3), `run_stop` (1), `run_scope` (1), `llm.telemetry` (2), `store.io` (1); the contract's 4 `TYPE_CHECKING` imports upward | traits; as is | agreed. Scope of the allowed surface is fork F2 |
| 4 | Knobs and couplings | `application/optimizer_knobs.py` | an optimizer's knobs are its nodes' `config` | knob half of the contract; 3 coupling shapes (BU) | as is | agreed (TD names the file) |
| 5 | Manifest and selected optimizer | `application/optimizer_manifest.py` (find, parse, select into a plan) | Every optimizer is a manifest | 1,148 lines, 14 concerns (BU); identity and the check-in manifest leave | registry by import → explicit table | agreed. Flat files, not BU's `optimizers/{manifest,plan,selected,behaviour}.py` |
| 6 | Record | `domain/records/<family>.py`, kinds in `domain/record_kinds.py` | Persistence | 18 files; facts stated twice: `cycle_final` restates 6 (BU), `CycleIndex` 3, the election 2 | import-walk registry → enum | agreed |
| 7 | Round (the envelope) | `domain/round.py` | neutral envelope | `RoundOutcome` + `RoundResult` + `RoundStatement` (3 write classes) and `StandingRound`, `IndexRound`, `RoundSummary`, `ServedRound`, `HierarchyRound`, `RoundAudit` (6 read shapes, BU); `run_standing` stored on every statement (35 hits / 24 files) | three-level inheritance → one struct; `RoundRows` by composition | agreed. `health` STAYS stamped (§5 V5) |
| 8 | Round boundary | `runner/round.py` | Central loop | `ElectionRecord` beside the close, `settle_owed_outputs`, `_adapt_beside_overlap`, `restated` (32 hits) | as is | **decided (4).** BU's A1 (keep the event, drop the copy) is superseded |
| 9 | Cycle state (the one fold) | `domain/cycle_state.py`, pure `(state, record) → state`; executed by `infrastructure/store/read_model.py` | Persistence; Display · run state is derived | `_StandingFold`, `CycleFactsFold`, `LiveDashboardProjection`'s fold, `RoundAudits`, `RunLimitsProjection` + `StandingBudgetFold`, the readout's and round file's own round rebuild | pure fold in the domain crate | **chosen: BU's one fold, TD's home.** A fold of anything but rounds, arms, standing, stop and limits sits beside its record family (TD's rule) |
| 10 | Line and scope | `domain/line_fold.py` (`Line`, a scope NAME) + `domain/cycle_line.py` (which cycle owns round r) | Persistence | `ledger_scan.py`'s 26 `Line` constants; the fork chain walked at 7 sites, supersede at 3, "who ran round r" by 3 rules (BU) | a scope as a name, not a closure | agreed in substance; both files kept |
| 11 | Money fold | `domain/money_fold.py` | §0.5 spent is a fold | the pure part of `infrastructure/llm/money_fold.py` (`Money`, `MoneyFold`, `bill_paid`, `still_held`: lines 43–377 of 434), the spend `Line`s, `domain/spend_rollup.py`. Its four file readers (`filed`, `cycle_money`, `money_on`, `charge_book_fold`, ≈60 lines) take a `Path` and stay in infrastructure, re-homed to `store/read_model.py` | as is | **chosen: TD**, corrected: BU left it in `llm/`, which is why `store/` imports `llm/` from below; TD moved the whole file though part of it reads files |
| 12 | Store root and append door | `infrastructure/store/root.py` (leaf) + `infrastructure/ledger.py` (the door) | Four entities · Workspace; Persistence | `Stores` as both aggregate and the type its parts import (16 type cycles, BU); `append_record` in `llm/telemetry.py` (58 calls / 15 files) beside `CampaignStore.append` | as is | **chosen: BU.** TD says so itself: "not cut here" |
| 13 | Run context | `application/session.py`: `Launch` (process span) and `Session` (one bound cycle, always bound) | Layering · init writes Session | `Session` + `LaunchState` + `ScorerSetup` + `LaunchClock` + `PreparedRun` + `RunObservers` + `OpenedCycle`; 7 `RunScope` fields; `session: Session` at 116 sites / 25 files, `session.state.` 112 / 21 | explicit context over a task-local | agreed in substance. **Name chosen:** `Session` keeps its name (116 sites unrenamed); BU's `CycleRun` and TD's `Services` are dropped ("service" is a banned word for a node, and near enough to avoid) |
| 14 | Run end and stop | `domain/stop.py` (`StopReason`, `StopLoop`, `RunEnd`) + `application/run_stop.py` (which stop) | Display · run state is derived | `LoopEnd` (7 fields), `_CycleOutcome`, `end_run_on`, 3 declaring doors in `entry.py`, `budget_stop` in two files; `raise StopLoop` at 8 sites / 3 files | exception as control flow → a value | agreed (TD M7 + BU M4 are one move) |
| 15 | Pass | `scoring/pass_run.py` (driver) + `bench/held_out.py` (kinds) + `domain/pass_kinds.py` (table) + `domain/records/passes.py` | Central loop · sole gateway | the gateway driven from 4 files (`passes.py` ×2, `line.py`, `node_context.py`, `measure.py`); `records/sample_passes.py` + `held_out.py`; catch-up (23 hits / 6 files) | as is | chosen: both, split by rank (above) |
| 16 | Walk | `scoring/walk.py` + `scoring/turns.py` | Central loop | `query_loop.py` (1,385); `QueryLoopResult`, `ScoredWalk`, `_Acquired`, `WalkRecorder` (1 implementation) | as is | agreed; BU's seam (one walk / N walks in turn) |
| 17 | Cell, reading | `domain/scoring.py`, `domain/fitness_fold.py`, `domain/paired_reading.py` | Measurement archive; §0.5 fitness | 7 cell types, 8 fold wrappers, 5 pair-member builders (BU, unverified) | as is | agreed. Measurand columns (rate, usd, seconds) are data |
| 18 | Resolution | `application/pipeline_resolve.py`: ONE `Resolution` from one `resolve(subject)` | Two resolution seams | `Resolution`, `RunResolution`, `DraftPipeline`, `LaidConfig`, `LineConfig`, `RootRunsWith`, 2 `ResolvedRun`s (TD: 8); 10 carriers and 8 bypass merges (BU) | `dict[str, Any]` is densest here | agreed |
| 19 | Launch | `domain/job.py` (`LaunchRequest`) + `jobs/launcher/` (admit) + `initialization/` (mint, open, wire) | Layering · one admission seam | `jobs/mint.py` (811), `launcher/{mint_and_start,checkin}.py`, `runner/run_init.py` (392); 6 stage types past admission (BU) | as is | agreed. TD's homes; BU's three stages |
| 20 | Hold tree | `domain/records/money.py::Hold` + `application/jobs/holds.py` | decisions § 0003; §0.5 a budget is never one value | `CampaignCeilingRecord`, `CycleShareRecord` as kinds apart; `shares.py`; budget half of `quota.py`; `_ENCLOSED` / `enclosed_as` (10 hits); 12 bound carriers | as is | agreed. `Hold` already has `parent` and `bound` (`money.py:60,63`). **Chosen: TD** on `StandingBudgetRecord`: it is the operator's edit of the declared budget, not a hold, and stays |
| 21 | Group | `domain/campaign.py::Group`, `groups/{id}.json` | Four entities (a fifth: see §10) | `HeadToHeadRecord`, `SharedPart`, `launcher/head_to_head.py` (`AdmittedHeadToHead`) | as is | agreed; **decided (3)** for its budget |
| 22 | Run status | `domain/phases.py`: `CampaignPhase`, `StopReason`, derived `RunPhase` | Display | 12 vocabularies (BU); `DashboardState`, `StatusMark`, `JobStage` go | as is | **chosen: BU.** TD only split the file |
| 23 | Command | `domain/command_kinds.py`: one row per kind holds every per-kind fact | Layering · entry points | 12 per-kind tables in 7 files (BU) | ClassVar tables → data | chosen: BU's merge, in a flat file |
| 24 | L4 door | `application/inner/` | L4 (kept layer) | `runner/inner/` imported by bench, INIT, the landing | as is | settled above (TD) |
| 25 | Upgrade and adoption | `domain/upgrade.py`, `domain/records/adoption.py`, `commands/adopting.py`, `connectors/protocol.py::publish` | neutral envelope; Backend overlay | new; `candidate_delta` generalised to "selection against origin by leaf" | as is | **decided (2, 5)** |

| 26 | Identity (the hashes) | `domain/identity.py` | Measurement archive; §0.5 behaviour key | 18 hash functions in 11 modules (BU, not re-counted); `config_key` (`measurement_archive.py:106`) and `sp_hash` (`pipeline_schema.py:1260`, `search_point.py:73`) hash one chain at two lengths | as is | BU only; kept. `config_key`'s bytes are frozen (it keys paid measurements); `sp_hash` moves on W |

**Concepts both designs leave as they are** (owner unchanged, no move touches their shape): the individual
(`domain/opt_search_point.py`), target spec (`domain/search_point.py`), arm (`domain/arms.py`), measurement
archive (`store/measurement_archive.py`), scoring gateway (`scoring/search_point_scorer.py`), cell measurement
(`scoring/sample_measurement.py`), race protocol, evaluator and formula, δ ruler, degradation check, optimizer
state, check-in (`bench/task_context.py`), optimizer call (`bench/llm_call.py`), dispatch hub
(`optimizers/potter/dispatch/`), declaration (`domain/campaign.py::RunDeclaration`), payer
(`llm/wallet.py::sending_key`), dataset-file seam (`store/dataset_access.py::resolve_dataset`), param fold
(`domain/layer_fold.py`), landing (`maintenance/landing.py`), data-root table. Run scope stays one ambient value,
cut from 14 fields to 7.

No state without a reporter: the merged design removes three (a `Session` bound to no cycle; a hold whose kind
is guessed; a round between election and close with `optimizer_state=None`) and adds none, provided the group
hold's draw-down is a `Hold` line like every other.

---

## 3. The end-state module tree

Lines today are (g) where I counted them, else the fact sheet's. End sizes are estimates.

```
domain/
  round.py                       new  ≈380   (results.py 340 + the statement half of records/rounds.py 218)   [W]
  results.py                     340  → 0                                                                      [W]
  records/rounds.py              218  → ≈150                                                                   [W]
  records/passes.py              new  ≈150   (sample_passes.py + held_out.py)                                  [W]
  records/money.py               257  → ≈230 (ceiling and share become Hold lines)                             [W]
  records/adoption.py            new  ≈60                                                                      [W shape, N13 use]
  cycle_state.py                 new  ≈520   (the one fold)                                                    [N7]
  cycle_line.py                  new  ≈150                                                                     [N7]
  line_fold.py                   new  ≈90                                                                      [N7]
  money_fold.py                  new  ≈430   (llm/money_fold.py 434 + spend Lines; spend_rollup.py folds in)   [N7]
  stop.py                        new  ≈300                                                                     [N5]
  phases.py                      878  → ≈450                                                                   [N5, W]
  spend.py                       726  → ≈430 (+ money_text.py ≈300, served words)                              [N14]
  scoring.py                     948  → ≈600 (+ scoring_spec.py ≈350)                                          [N14]
  sample.py                      244  → 0    (bank.py ≈120, archive_row.py ≈130)                               [N14]
  job.py                         242  → ≈360                                                                   [N10]
  campaign.py                    376  → ≈390 (Group)                                                           [W]
  upgrade.py                     new  ≈120                                                                     [N13]
  command_kinds.py              1536  → ≈1,400                                                                 [N12]

infrastructure/
  ledger.py                       52  → ≈110 (the one append door)                                             [N6]
  data_root.py                   832         (from store/; it names llm's readers too)                         [N6]
  store/root.py                  new  ≈120                                                                     [N6]
  store/stores.py                289  → ≈200                                                                   [N6]
  store/read_model.py           1012  → ≈700 (index, read_line, scope resolvers)                               [N7]
  store/campaign_store.py        new  ≈560   (campaign_store/store.py 721; the package flattens)               [N7]
  store/campaign_store/ledger_scan.py  1158 → 0                                                                [N7]
  projections/live_dashboard.py  925  → ≈450 (a view of CycleState + the checkout writer)                      [N7]
  projections/projection.py      new  ≈70                                                                      [N7]
  llm/spend_book.py              829  → ≈650                                                                   [N10]
  llm/money_fold.py              434  → 0    (≈370 pure lines to domain; ≈60 of file readers to store/)        [N7]
  run_scope.py                   143  → ≈90                                                                    [N4]

application/
  optimizer_contract.py          856  → ≈780 (gains the face; loses knobs and BehaviourKeys)                   [N3]
  optimizer_knobs.py             new  ≈260                                                                     [N3]
  optimizer_identity.py          new  ≈220                                                                     [N3]
  optimizer_manifest.py         1148  → ≈760                                                                   [N3]
  session.py                     new  ≈300   (initialization/session.py 234; Launch + Session)                 [N4]
  run_observers.py               240  → 0                                                                      [N4]
  run_stop.py                    127  → ≈260                                                                   [N5]
  pipeline_resolve.py           1282  → ≈650                                                                   [N9]
  preflight.py                   340  → ≈560                                                                   [N10]
  jobs/holds.py                  new  ≈480   (shares.py 298 + quota's budget half)                             [N10]
  jobs/quota.py                  779  → ≈380                                                                   [N10]
  jobs/launcher/admission.py     715  → ≈400                                                                   [N10]
  jobs/mint.py                   811  → 0                                                                      [N10]
  jobs/launcher/{mint_and_start,checkin,head_to_head}.py  765 → 0                                              [N10]
  initialization/{mint,open_cycle,group,wiring}.py   new/268 → ≈1,900                                          [N10]
  scoring/pass_run.py            new  ≈350                                                                     [N8]
  scoring/walk.py, turns.py      new  ≈1,050 (query_loop.py 1385)                                              [N8]
  scoring/passes.py              665  → 0                                                                      [N8]
  scoring/measurement_log.py     634  → 0    (whole file to views/cells.py)                                    [N8]
  bench/held_out.py              new  ≈620                                                                     [N8]
  bench/line.py                  946  → ≈450 (readings only)                                                   [N8]
  bench/node_context.py          838  → ≈650 (implementations of the contract's face)                          [N3, N8]
  bench/cycle.py                 509  → ≈450                                                                   [N4]
  bench/resume.py, fork.py       new  ≈540   (resume_and_fork/ 756)                                            [N10]
  runner/round.py                643  → ≈520                                                                   [N5, W]
  runner/loop.py                 246  → ≈170                                                                   [W]
  runner/measure.py              730  → ≈520                                                                   [N8]
  runner/entry.py                640  → ≈520                                                                   [N4, N5]
  runner/run_init.py             392  → 0                                                                      [N10]
  inner/{spawn,tasks,transcript}.py  1053 → ≈850  (from runner/inner/)                                         [N11]
```

Totals are in §4.

---

## 4. The merged move sequence

Identity columns: **wipe** = run data reshaped; **re-key** = built-in behaviour keys move; measurement keys
(`config_key`) move in NO move (constraint on W). Size: S < 300 changed lines, M < 1,000, L < 2,500, XL above.

| Move | Root | Whole file set | After | Wipe / re-key | Size (≈ deleted) | Shares files with |
|---|---|---|---|---|---|---|
| **N0** | The ladder is a gate count that no move may raise (TD M1). | `scripts/gate.py`, `scripts/complexity_ledger.py`, `application/CLAUDE.md` | — | no / no | S (+120) | every move that lowers the count edits one `BASELINE` line |
| **N1** | Delete what has no reader (BU M1), list re-derived by grep at the brief (§5 V13). | `domain/campaign_config.py` (`identity()`: 0 `.identity()` callers; `_TREATMENT_OWNED`), `scoring/query_loop.py` (`WalkRecorder`), `pipeline_resolve.py`, `jobs/quota.py`, `jobs/launcher/admission.py`, `jobs/mint.py` | — | no / no | S (−100 to −250) | N8, N9, N10 (one function each; lands first) |
| **N2** | An import is keyed by the names it binds (decision 1). | `scripts/offline_run.py` (`_unvouched`), `assets/behaviour_keys.json`, `application/optimizer_manifest.py` (docstring), `docs/architecture.md` §0.5 | — | no / **re-key 1** | S | N3 (the generated file) |
| **N3** | A member imports the contract, rank-G grading, and `domain`; nothing of the bench (BU M2 + TD M2 + TD M13). The face a member types against (`NodeContext`, `RoundContext`, the reads) is Protocols in the contract; `StopLoop` is a `domain` type; `append_record`, `run_scope`, `inner_sandbox_store` become methods of the face; knobs, identity and the check-in manifest leave their files. | `optimizer_contract.py`, `optimizer_knobs.py`, `optimizer_identity.py`, `optimizer_manifest.py`, `config_map.py`, `pipeline_responses.py`, `bench/node_context.py`, `bench/cycle.py` (imports), `bench/task_context.py`, `bench/llm_call.py`, `bench/resume_and_fork/replayers.py`, `domain/{stop,treatment,failures}.py`, all 40 files under `optimizers/`, `examples/optimizer-plugin`, `assets/behaviour_keys.json` | N2; fork F2 | no / lands WITH N2 as re-key 1 (potter's member files drop bound names) | L (≈ −150) | N4, N8, N11 (`node_context.py`, `cycle.py`); N5 (`domain/stop.py`, `optimizer_contract.py`) |
| **N4** | One run context at two spans: `Launch` and an always-bound `Session` (BU M3 + TD M9's "always bound"). The split is real, not a rename: `session.state` holds launch-span fields (`clock`, `control`, `flight`, `held`, `observers`) beside cycle-span ones (`scoring`, `filed_basis`, `resumed_from_round`, `human_intervened`), and a fork is a new `Session` that inherits the first set (`session.py:66-84`, `run_init.py:359`). `RunObservers`' three concepts go to their owners; the projection roster is completed by registration. | `initialization/{session,wiring,scoring_scope}.py` → `session.py`, `bench/{cycle,node_context,line,difficulty,export}.py`, `origin.py`, `runner/{entry,run_init,loop,round,measure}.py`, `runner/inner/{spawn,tasks}.py`, `run_observers.py`, `run_phase_control.py`, `infrastructure/run_scope.py`, `scoring/{query_loop,sample_measurement,passes,closed_rounds,search_point_scorer}.py`, `jobs/mint.py`, `jobs/launcher/{held,head_to_head,mint_and_start}.py`, `embedded_run.py`, `mask/load.py`, `datasets/origin_readiness.py`, `maintenance/landing.py` (`complete_registries`) | N3 | no / no | XL (≈ −400), in two commits: types down, then carriers out | nearly every later move: it is the trunk; only N12 and N14 run beside it |
| **N5** | The end of a run is one value; which stop is decided in one place (BU M4 + TD M7). | `domain/{stop,phases}.py`, `run_stop.py`, `run_phase_control.py`, `runner/{entry,loop,round}.py`, `scoring/query_loop.py` (`_budget_stop`), `jobs/launcher/opening.py`, `optimizer_contract.py` | N4 | no (the record's shape is kept; the split of its shared field rides W) / no | M (≈ −100) | N8 (`query_loop.py`, `loop.py`, `round.py`): chain N5 → N8. **N10** (`runner/entry.py`, `jobs/launcher/opening.py`): N5 lands first |
| **N6** | One store root and one append door (BU M5 + TD M3's re-homes). `append_record` keeps its NAME and moves to `infrastructure/ledger.py`, so its 58 call sites change an import line, not a call; `Stores.declare_ended` and its three siblings move to the campaign store's door; `data_root.py`, the reuse cache and `heartbeat.py` are re-homed. | `infrastructure/store/{stores,root,campaign_store/store,archive_queries,account_spend,account_trail,dataset_access,session_pointer,data_root,llm_reuse_cache}.py`, `infrastructure/{ledger,run_scope,run_state}.py`, `infrastructure/llm/{telemetry,spend_book,heartbeat}.py`, `connectors/protocol.py`, import lines in the 13 application files that call `append_record` | N4 (N3 for the two member files) | no / no | L (≈ −100) | N5, N8 by import lines only (`append_record` is a 5-line function over the ambient ledger, `telemetry.py:50-54`, so its home moves and no call changes); N7 (`store.py`, `archive_queries.py`, `account_spend.py`): chain N6 → N7; N9 (`dataset_access.py`); N10 (`spend_book.py`, `account_spend.py`); N11 (`run_scope.py`): N6 lands first on all four |
| **N7** | One fold of a cycle, pure, in `domain`; everything else is a view (BU M6 + TD M3). Step a: `Line` with a scope name, the line algebra and every pure fold move to `domain`; `ledger_scan.py` and `llm/money_fold.py` go; `campaign_store/` flattens. Step b: the round-rebuilding folds collapse into `CycleState`; dashboard, index, readout, audit and round file become views. Every written file stays byte-identical (§10). | `store/campaign_store/*`, `store/{read_model,io,lineage_queries,family_ray_queries,archive_queries,account_spend}.py`, `llm/money_fold.py`, `projections/*`, `domain/{cycle_state,cycle_line,line_fold,money_fold,spend_rollup,hierarchy,dashboard_rows,cycle_index,run_standing}.py`, `domain/records/*` (small folds), `scoring/closed_rounds.py`, `views/readout.py`, `served_dashboard.py`, `hierarchy.py`, `hierarchy_subtree.py`, `cycle_reads.py` | N6 | no (drops the fold-snapshot CACHE only) / no | XL (≈ −670) | N8 (`closed_rounds.py`, `hierarchy.py`, `bench/line.py` import lines): N7 first; N10 (`served_dashboard.py`, `account_spend.py`); N12 (`domain/hierarchy.py`); W (its one reader) |
| **N8** | One pass driver; the walk; measurands as columns (BU M7 + TD M10 + TD M12). Catch-up's records are NOT touched here (W). | `scoring/{passes,pass_run,query_loop,walk,turns,search_point_scorer,paired,sample_measurement,measurement_log}.py`, `bench/{line,held_out,node_context}.py` (measure helpers), `runner/{measure,loop,round}.py` (`post_round`, origin and noise floor), `commands/measuring.py`, `domain/{fitness_fold,scoring,verification,bench}.py`, `views/cells.py`, `read_params.py`, `evidence/read.py` and `hierarchy.py` (pair builders) | N4, N5 | no / no, if the offline run shows requests and decisions unmoved | L (≈ −600) | N7 (`closed_rounds.py`, `hierarchy.py`); N9 (`bench/line.py`, one site); N11 (`bench/node_context.py`): N8 first; N14 (`domain/scoring.py`) |
| **N9** | One resolved run (BU M8 + TD M8). Session binding to `initialization/wiring.py`; served shapes out; validation in `pipeline_parsing`. | `pipeline_resolve.py`, `pipeline_responses.py`, `config_map.py`, `path_patch.py`, `initialization/wiring.py`, `datasets/{authored,draft_patch,draft_campaign,startability,prompts,loaders}.py`, `store/dataset_access.py`, `domain/{pipeline_schema,pipeline_parsing,pipeline_params,pipeline_overlay,pipeline_manifest,search_point,campaign_config,layer_fold}.py`, `domain/records/run_wiring.py` (no field change) | N4 | no (field changes ride W) / no | L (≈ −450) | N10 (`preflight.py`, draft pricing): chain N9 → N10. Disjoint from N5–N8 except one line in `bench/line.py` |
| **N10** | A launch is request, admission, opening; INIT is one package; the bound chain is the hold tree (BU M9 + TD M9 + TD M6's code). `_ENCLOSED` does NOT go here: it is what a `Hold` line with no `resource` under a nested run means (`spend_book.py:254-262`), so naming the kind on the line changes how existing charge-book lines read, and that rides W. | `jobs/launcher/{admission,mint_and_start,opening,launch,held,checkin,head_to_head}.py`, `jobs/{mint,quota,shares,holds}.py`, `initialization/{mint,open_cycle,group}.py`, `runner/{run_init,entry}.py` (`run_job`), `runner/inner/{spawn,tasks}.py`, `preflight.py`, `origin.py`, `queued_launch.py`, `embedded_run.py`, `commands/{launching,opening,cycle_cleanup}.py`, `bench/resume_and_fork/*` → `bench/{resume,fork}.py`, `domain/{job,spend}.py`, `infrastructure/llm/{spend_book,send_pacing}.py`, `infrastructure/store/{account_spend,wallet_store}.py`, `head_to_head_price.py`, `served_dashboard.py` (`fork_remainder`, `spend_counts` move below it), `presentation/cli/commands/launch.py` | N4, N6, N9 | no, **under a constraint**: `job` and `queued launch` are SURVIVOR rows (`data_root.py:320,324`), so their bytes stay or the move ships a one-shot migration / no | XL (≈ −650) | N11 (`inner/spawn.py`, `inner/tasks.py`, `initialization/open_cycle.py`): chain; N5 (`entry.py`, `opening.py`), N6 (`spend_book.py`), N7 (`served_dashboard.py`, `account_spend.py`): N10 lands after all three; N14 (`domain/spend.py`) |
| **N11** | L4 is a door (TD M14). `runner/inner/` → `application/inner/`; the spawn is a field of the context; the transcript read goes through the store. `inner_campaign_id`'s inputs do not change here. | `runner/inner/*`, `bench/node_context.py`, `initialization/{scoring_scope,open_cycle}.py`, `connectors/promptpotter.py`, `infrastructure/run_scope.py`, `maintenance/landing.py` | N10 | no / no | M (≈ −200) | N8 (`node_context.py`), N6 (`run_scope.py`), N10; W (`inner/spawn.py`, `landing.py`) |
| **N12** | One row per command (BU M11). | `domain/{command_kinds,command_events,hierarchy}.py`, `commands/dispatcher.py`, `infrastructure/store/command_log.py`, `presentation/api/deps.py`, `presentation/admin_bot.py`, `presentation/cli/main.py` | N1 | no (kind strings stay) / no | M (≈ −180) | N7 (`domain/hierarchy.py`, the `DeleteKind` table only); N13. Otherwise runs beside everything |
| **N14** | A domain file names one concept (TD M3b): pure splits. | `domain/{sample,bank,archive_row,scoring,scoring_spec,cells,cell_rows,spend,money_text}.py` and their import lines | N2 (else the 38 domain modules members import re-key on a split) | no / no | S–M (≈ 0, moved) | N8 (`scoring.py`), N10 (`spend.py`): land between them |
| **W** | **The one wipe and the last re-key.** Every on-disk reshape and every change to the decision trace, built as four chains on one branch and landed together: (a) rounds: one `Round`; the close at the election; round 0 through the round walk; `run_standing`, `panel_precision`, `summary()` off the statement. (b) passes: one pass record family; catch-up deleted; an arm's state filed where it moves. (c) money and group: ceiling and share as `Hold` lines; a nested hold names its kind, so `_ENCLOSED` and `enclosed_as` go; `groups/{id}.json` with a purpose; the `AdoptionRecord` shape. (d) status: `cycle_final` and `CycleIndex` restate nothing; 12 vocabularies → 3 axes; the refused-send field split. Riders: `sp_hash` derived from the `config_key` chain with `config_key`'s bytes unchanged; `ValidationFailure.axis` optional; "wound" renamed; the L2 steer as a layer; `CandidateDelta` from `loci()`; the knob-provenance mint field; `inner_campaign_id`; `SampleFoldRow.cells`; `Election.deciding_pair`. | `domain/records/**`, `domain/{results,round,cycle_result,cycle_index,phases,run_standing,arms,origin_panel,campaign,bench,fork,candidate_diff,failures,dashboard_rows,export,l4/proxies}.py`, `runner/{round,loop,measure}.py`, `origin.py`, `bench/cycle.py`, `scoring/{walk,turns,closed_rounds}.py`, `jobs/holds.py`, `infrastructure/store/{data_root,layout}.py`, `domain/cycle_state.py` (the one reader), `projections/live_dashboard.py`, `views/readout.py`, `optimizers/potter/**` (wound, steer, validators), `intelligence/indexes/sample.py`, `inner/spawn.py`, `maintenance/landing.py`, `assets/behaviour_keys.json`; **every reader of the four round classes**: 39 more code files outside `optimizers/` and 10 member files (`git grep -lwE "RoundResult\|RoundStatement\|RoundOutcome\|RoundEnding"`), among them `optimizer_contract.py`, `optimizer_manifest.py` (round 0's plan), `bench/{line,node_context,difficulty,round_analysis}.py`, `bench/resume.py`, `cycle_reads.py`, `mask/{load,record}.py`, `intelligence/{earned_blocks,rasch,indexes/axis}.py`, `views/render/*`, `domain/{narration,results_health}.py`; the served readers of a round (`served_dashboard.py`, `hierarchy_subtree.py`, `domain/hierarchy.py`); the status vocabularies' holders (`domain/{cycle_index,job,phases}.py`, `webapp/lib/api/types.generated.ts`); `infrastructure/llm/spend_book.py`, `inner/tasks.py` (`enclosed_as`); `docs/architecture.md` §0 (the group; the round boundary) | N5, N7, N8, N10, N11; fork F1 decided | **wipe 1 of 1 / re-key 2 of 2** | XL (≈ −1,400) | everything; nothing else lands with it |
| **N13** | Retune (BU M12 + TD M15/M16): group launch drawing down one group hold, origins first; `retune`; `Upgrade`; `adopt` with `publish` or export; "Adopt selected" as a submitted arm through one held-out pass. | `domain/{upgrade,export,bench}.py`, `initialization/group.py`, `jobs/holds.py`, `bench/held_out.py`, `commands/{adopting,launching}.py`, `domain/command_kinds.py`, `connectors/protocol.py`, one connector, `views/` (the event ledger), the five entry points | W, N8, N10, N12 | no (its record shapes landed in W) / no | L (+700 to +900) | — |

**Order and parallel chains.**

```
N0 ─┐
N1 ─┼─ N2+N3 ── N4 ──┬── N5 ── N8 ─────────────┐
N12 ┘ (free)         ├── N6 ── N7 ──────────────┼── W ── N13
                     └── N9 ── N10 ── N11 ──────┘
                 N14 anywhere after N2, between N8 and N10 on its two shared files
```

- Before N4: N0, N1, N12 are mutually disjoint and can run at once. N2 is a one-file change; N3 follows it.
- After N4 there are **three chains: runner/scoring (N5 → N8), store (N6 → N7), config/launch (N9 → N10 → N11).
  They are NOT file-disjoint.** Intersecting the file sets above, eleven files cross chains, and each has a fixed
  landing order:

  | Shared file | Chains | Lands first |
  |---|---|---|
  | `runner/entry.py`, `jobs/launcher/opening.py` | N5 ∩ N10 | N5 |
  | `bench/node_context.py` | N8 ∩ N11 | N8 |
  | `scoring/closed_rounds.py`, `application/hierarchy.py` | N7 ∩ N8 | N7 |
  | `infrastructure/llm/spend_book.py`, `store/account_spend.py` | N6 ∩ N10 | N6 |
  | `infrastructure/run_scope.py` | N6 ∩ N11 | N6 |
  | `store/dataset_access.py` | N6 ∩ N9 | N6 |
  | `served_dashboard.py`, `store/account_spend.py` | N7 ∩ N10 | N7 |
  | `domain/hierarchy.py` | N7 ∩ N12 | either (disjoint symbols) |

  So the first link of each chain (N5, N6, N9) runs in parallel on disjoint files except `dataset_access.py`;
  the second links (N8, N7, N10) each rebase on a named file. That is three parallel builds with ordered
  landings, not three independent chains.
- BU's claim that M5 (store root) runs beside M4 and M7 on disjoint files is **false as BU wrote it**: 13 of the
  15 `append_record` files are runner, bench and scoring files (g). Keeping the function's name is what makes
  the overlap an import line.
- TD's chain is strictly linear ("any two others share a file"). That is a property of its file-by-file
  relocation, not of the roots.

**Totals.** Wipes: **1** (W). Re-keys: **2** (N2+N3 as one landing; W). If N3 slips and N2 lands alone, 3.
Measurement-key moves: **0, as a constraint on W, not a finding**: `config_key`'s bytes are frozen and only
`sp_hash` moves; under `infrastructure/` the one `sp_hash` use is a comparison in `live_dashboard.py:638`, so no
cache was found keyed on it, but `intelligence/` and the reuse cache were not searched. Survivor migrations:
**0 planned, 2 at risk**: N10's `job` and `queued launch` files, and the charge book on W (`data_root.py:235`
treats `CHARGES_FILE` beside the survivors; `Hold` lines live on it).
Lines deleted, net of moves: **≈ 5,050**, the sum of the per-move figures above (150 + 150 + 400 + 100 + 100 +
670 + 600 + 450 + 650 + 200 + 180 + 1,400), honest range 3,500–5,500. The figures rest on BU's D1–D8 and D10
(≈3,780; six sub-readers' inventories, not re-counted; D1's 500 is split across N4 and N5), plus what only TD's
roots remove (the election machinery, round 0's own walk, catch-up, the launcher's duplicate mint paths). BU's
D9 (≈520, inside `optimizers/`) is Stage 2 and not counted. TD's 2,000–2,500 is a subset of the same roots and
is not added. About 1,500–2,000 more lines leave the backbone for served modules. Retune adds ≈ 800. Type cycles:
51 → a target of ≤ 5, held by the gate; the per-knot split (17 / 16 / 11 / 5 / 2) is BU's count and unverified.
The ladder's rightward edges → 0, except inside `application/datasets/` (Stage 2).

---

## 4b. What each design got wrong (details in §5)

**Top-down.** (1) It never read how the behaviour key is computed, so it marks six moves "possible" re-key and
M13 "no"; under the tip's rule every one of its module moves that a member imports re-keys. (2) It relocates
folds without collapsing them (M3: "+2,000 / −2,500, mostly moved"). (3) Two wipe landings (W1, W2) where one
suffices. (4) A strictly serial chain. (5) It moves the stop's writer up into `application/`, against the debt
row's own root. (6) It splits `measurement_log.py` on a backbone half that has no importer. (7) It does not cut
the `Stores` knot and says so.

**Bottom-up.** (1) Its package renames contradict a recorded decision. (2) M3 is XL and also carried a package
rename. (3) Its M1 "zero or one reader" list is partly not that: `draft_served` is named on 15 lines in 6
files, `campaign_connector` on 13 in 6, `CampaignRefusal` on 14 in 4 (g, whole-word). (4) "Five pass drivers"
holds only by its own wide definition (§5 V10). (5) It puts `sp_hash` joining `config_key` on the wipe without
saying that `config_key` is the measurement archive's key: moving it would void paid measurements. (6) It
checked the `job` file as a survivor but not `queued launch`. (7) Its parallel claim for M5 fails on
`append_record`'s call sites. (8) It leaves round 0's own walk in place, against "origin differs in values, not
layers". (9) Its `CycleState` sits in the store though it is pure.

**Both agree, and the code disagrees or is silent.** Both keep "`data_root.py` stays central" (true: all
campaign rows are `Fate.RUN`, `data_root.py:540`). Both say `Hold` already carries the chain (true:
`money.py:60,63`). Both assume the eight round-rebuilding folds compute the same rounds; nobody has diffed
their outputs (§10).

---

## 5. Verification

| # | Claim | Verdict | Command (run in `wt-land-w7`) and result |
|---|---|---|---|
| V1 | TD: `launcher/opening.py` imports nothing above rank A2 | **verified at runtime, with one type edge** | `git grep -nE "^\s*(from|import) " -- promptpotter/application/jobs/launcher/opening.py`: `jobs.registry`, `jobs.shares`, `domain.*`, `llm.spend_book`, `store.stores`; plus `TYPE_CHECKING` → `bench.resume_and_fork.fork_siblings.ForkCut` (line 26), which N10 must cut |
| V2 | TD: `measurement_log.py` has a backbone half for `row_policy.py` | **refuted** | `git grep -lE "scoring\.measurement_log" -- promptpotter` → `read_params.py`, `views/readout.py` only |
| V3 | TD: `loop.py` loses `open_origin_round`, `walk_round_zero`, `bench_line_origin` for −250 | **the functions exist; the size is unproven** | `git grep -nE "def (open_origin_round|walk_round_zero|bench_line_origin|emit_origin_round)"` → `loop.py:136,189,219`, `round.py:564`. The door also holds the origin gate, the noise floor, the bench set-aside and hole repair (`loop.py:136-236`), which a round must then carry |
| V4 | TD: `SpendMeter` goes because "a count is a `read_line` of the money fold" | **refuted as stated** | `git grep -n SpendMeter -- promptpotter`: it reads the account's charge book (`money_on(self.charges.path)`, `spend_book.py:243`), not a cycle line; it is `SpendBook`'s base and `served_dashboard.py:722`'s reader. It can shrink to a function of the money fold once `_ENCLOSED` goes (N10) |
| V5 | TD: `RoundStatement.health` is a line fold and may leave | **refuted as a deletion** | `results.py:159-160` ("Graded against the rounds before it, so it is stamped, never re-derived"); the origin gate reads `cycle.origin_round.health` at `loop.py:161,176`, on a resume too. It stays on the round |
| V6 | TD M3: every fold class of `ledger_scan.py` is pure | **partly verified** | `git grep -nE "^(from|import) " -- …/ledger_scan.py`: beyond `domain` it imports `llm.money_fold`, `store.layout.CycleLayout`, `store.read_model`. The fold classes take records only; three combiners read a span's path for its cycle id (`:764`, `:873`) and `line_walks` (`:911`) calls `read_line`. So: folds move once a span carries its cycle id as a name; `_supersede_line`, `_own_record`, `line_walks` stay in the store |
| V7 | TD M11: round 0 as a round wipes nothing if its records are unchanged | **refuted; it rides W** | `round.py:572` writes `ElectionRecord(round=0)`, which decision 4 removes; the gate today is `StopReason.ORIGIN_GATE` plus a `decision(OriginGatePass)` (`loop.py:178,241`) |
| V8 | BU: six run-context carriers; `session: Session` at 116 sites / 25 files; `session.state.` 112 | **verified** | `git grep -c "session: Session\b" -- promptpotter` → 116 / 25; `git grep -c "session\.state\."` → 112 / 21; `git grep -nE "^class (Session|LaunchState|PreparedRun|RunObservers|RunScope|Cycle)\b"` → all six |
| V9 | BU: about eight folds of one cycle; `RoundEnteredRecord` handled in five | **existence verified; equivalence not** | the eight classes exist (`git grep -nE "^class \w*(Fold|Projection|Audits|Tail|Feed)\b"`); `RoundEnteredRecord` is handled in six places (`closed_rounds.py:144`, `readout.py:468`, `narration.py:198`, `audit_trail.py:86`, `live_dashboard.py:497`, `ledger_scan.py:466`). That each rebuilds the SAME rounds was not checked |
| V10 | BU: one pass, five drivers | **verified by BU's definition** (a file that picks a pass's rows and brackets or seats its result by hand) | `git grep -nE "score_search_points?\(" -- promptpotter` → four files call the gateway: `line.py:548`, `node_context.py:214`, `measure.py:393`, `passes.py:369,503`. The fifth, `loop.py`, does not call it but `walk_round_zero` (`:189-216`) picks round 0's cells, repairs holes and seats the read |
| V11 | BU: twelve spend-bound carriers | **verified (all twelve exist)** | `git grep -nE "^class (Budget|Remainder|HeldBudget|SpendCount|StandingBudgetRecord|CampaignCeilingRecord|CycleShareRecord|Hold|Coupon|AccountWallet|SendBound|RunLimits)\b"` + `tier_ceiling` (`free_tier.py:40`) |
| V12 | BU: the behaviour key hashes a member's import statements by module path | **verified** | `scripts/offline_run.py:1910-1935`: every statement outside a function body is keyed by `ast.dump`, and an `ImportFrom` dumps its `module`. Member import counts: `git grep -hoE "^from promptpotter\.(application|infrastructure|config|shared)[.a-z_0-9]* import" -- promptpotter/application/optimizers \| sort \| uniq -c` → `bench.node_context` 13, `replayers` 5, `bench.llm_call` 3, 38 distinct `domain` modules |
| V13 | BU M1: its listed names have zero or one reader | **partly refuted; the list is re-derived at the brief** | whole-word line counts, `git grep -nw NAME -- promptpotter \| wc -l` (lines naming it, definition included, not a caller count): `draft_served` 15 in 6 files, `campaign_connector` 13 in 6, `CampaignRefusal` 14 in 4 → several readers each. Fit the claim: `.identity()` 0 callers, `WalkRecorder` 2 lines in 1 file, `draft_config` 2 in 1, `_source_pipeline` 4 in 1. Unclear: `mint_facts` 5 in 2, `mint_framed_cycle` 3 in 2, `draft_budget` 3 in 2. `ArmName` IS defined twice, as BU says: `domain/campaign.py:109` (an annotated `str`) and `domain/paired_reading.py:245` (a class) |
| V14 | Fact sheet: four hub paths are stale | **verified** | `git ls-files` returns nothing for `runner/bench.py`, `optimizers/nodes.py`, `domain/records/run_records.py`, `maintenance/built_key.py` |
| V21 | Merged: `llm/money_fold.py` is pure and moves whole to `domain` | **refuted; split** | `git grep -nE "^(from\|import) \|^(class\|def) " -- …/llm/money_fold.py`: `Money`, `MoneyFold` and the bill helpers import `domain` only; `filed`, `cycle_money`, `money_on`, `charge_book_fold` take a `Path` or a `LedgerSpan` and import `store.io` and `store.read_model` |
| V22 | Merged: re-homing `append_record` changes only import lines | **verified** | `telemetry.py:50-54`: `ledger = bound_ledger(...); return None if ledger is None else ledger.append(*records)` |
| V15 | Both: 51 type cycles | verified as the baseline, not re-run | `git grep -n '"type_cycles"' -- scripts/complexity_ledger.py` → 51 |
| V16 | Both: 82 open rows in `## 01`–`## 10` | **verified** | row leads per section: 5, 9, 11, 14, 8, 11, 5, 8, 6, 5 |
| V17 | TD: five backbone files import a served module | verified, six import lines | `git grep -nE "^from promptpotter\.application\.(views|served_dashboard)" -- …/runner …/bench …/scoring …/run_observers.py`: `fork_siblings.py:21`, `run_observers.py:16,17`, `entry.py:48`, `run_init.py:34`, `passes.py:41` |
| V18 | TD: catch-up 23 hits / 6 files; `run_standing` 35 / 24 | **verified** | `git grep -ciE CatchUp`, `git grep -c run_standing` |
| V19 | BU: `index.json`, `dashboard.json`, the round file and the readout are run data, not caches | **verified** | `data_root.py:540`: every campaign row is built `Fate.RUN` |
| V20 | BU: no survivor migrates in the launch move | **not shown** | `data_root.py:320,324`: `job` AND `queued launch` are `_SURVIVOR`; BU checked only the first |

---

## 6. Forks for the operator (three)

**F1. The refund rule, before W.** W wipes every account's run data on the box; the row "a landing that drops
other accounts' run data refunds nobody" (area 05) is the 11-26 incident at full size.
- TD: lists the row residual ("operator's choice pending") and does not tie it to its wipe.
- BU: makes it a predecessor of the wipe (A4), no recommendation.
- **Recommend option (a) of the row**: the landing releases each dropped account's charges for the dropped
  campaigns and posts the notice. Option (b) makes run data a survivor, which is the end of the wipe rule and
  not this plan's to take.

**F2. What a plugin may import.** The row (area 09) asks that `examples/optimizer-plugin` import one module.
- TD: one module, `optimizer_contract.py`, "the one module a plugin imports".
- BU: the contract, a toolkit and `domain`; a one-module surface means re-exporting `domain` types through the
  contract, which the repo's own ledger counts as a shim.
- **Recommend: the contract plus `domain` plus the rank-G grading modules, with no re-exports**, and reword the
  row's Re-test to "the example imports nothing under `bench/`, `runner/`, `initialization/` or a built-in's
  package". Members import 38 `domain` modules today (g); one module would be a façade over them. This sets
  N3's scope, so it is asked before N3.

**F3. Where a new model's capabilities and prices come from.**
- TD (F4): a host-edited catalogue file (a `KINDS` row, a survivor) laid over the code tables, so "model C is
  out" needs no release.
- BU (F5): only the trigger is decided; the tables stay code.
- **Recommend TD's file**, built as a Stage-2 item of `infrastructure/llm` (it is outside the backbone and
  blocks only N13's usefulness, not its build). Without it the carousel's promise waits on a PyPI release per
  model.

---

## 7. Hubs

Every fact-sheet file with 20 or more commits. Backbone files first; files outside the backbone get one line.

| File (commits) | In the backbone? | What makes it stop being a hub, or why it stays |
|---|---|---|
| `assets/behaviour_keys.json` (43) | yes, generated | N2: a module move no longer touches it. It then moves twice (N2+N3, W) and afterwards only when behaviour changes on purpose |
| `maintenance/built_key.py` (39) | gone at the tip | nothing to do |
| `scoring/passes.py` (35) | yes | N8: deleted; driver to `pass_run.py`, kinds to `bench/held_out.py`, the diagnostic verb to `commands/measuring.py` |
| `runner/round.py` (35) | yes | **stays central**: it is the round walk. N4 ends its four `model_copy` rebuilds per close; W removes the election machinery. Edited afterwards only when the walk changes |
| `domain/records/run_records.py` (32) | gone (split into `records/*`) | W finishes it: one fact per kind, so a new reader does not co-edit two families |
| `campaign_store/store.py` (31) | yes | N6 takes the append door and the stop ending; N7 takes the index projection and flattens the package. Keeps campaign CRUD |
| `bench/node_context.py` (31) | yes | N3: the declarations move to the contract, so a new member capability edits the contract and one implementation. **Stays the implementation**; no longer imported by members |
| `scoring/measurement_log.py` (28) | no (a served read in a backbone package) | N8: leaves whole for `views/cells.py` |
| `jobs/launcher/admission.py` (28) | yes | N10: keeps the asks; probes to `preflight.py`, money to `jobs/holds.py`, destroy helpers to `commands/cycle_cleanup.py` |
| `runner/inner/spawn.py` (27) | yes (L4) | N10 + N11: it builds one `LaunchRequest(InnerSeat)` and is touched no more by launch-shape changes |
| `pipeline_resolve.py` (27) | yes | N9: one `Resolution`; binding, pricing and served shapes leave |
| `jobs/mint.py` (27) | yes | N10: deleted; rank-A6 code moves to `initialization/mint.py` |
| `projections/live_dashboard.py` (26) | yes | N7: a view of `CycleState` plus a checkout writer; a new ledger fact edits the fold, not this file |
| `runner/bench.py` → `bench/line.py` (26) | yes | N8: readings only |
| `run_observers.py` (26) | yes | N4: deleted |
| `scoring/query_loop.py` (25) | yes | N5 takes the budget-stop classifier; N8 splits it at one walk / N walks. **`walk.py` stays central** to measuring |
| `runner/entry.py` (25) | yes | **stays central** as the run's entry (db31dcb5c). N4, N5 and N10 remove its served import, its three stop doors and `run_job`'s launch code |
| `domain/command_kinds.py` (24) | yes, a registry by contract | **stays a hub by design**: one row per command. N12 makes that the only table, so a new command is one edit, not up to thirteen |
| `optimizers/nodes.py` → `optimizer_contract.py` (24) | yes | N3. **Stays central**: it is the plugin boundary |
| `maintenance/landing.py` (23) | yes, a registry by contract | **stays**: every reshape names its wipe here. One wipe instead of six per 28 landings is what quiets it |
| `bench/cycle.py` (23) | yes | **stays central**: the cycle in memory. N4 moves the contexts' declarations out |
| `optimizers/potter/members.py` (22) | yes (a member) | **stays**: one module per optimizer. N3 ends the churn it inherits from bench files |
| `store/data_root.py` (21) | yes, a registry by contract | **stays**: the one table every on-disk change amends; re-homed in N6 |
| `campaign_store/ledger_scan.py` (21) | yes | N7: deleted |
| `domain/spend.py` (20) | yes | N14 + N10: keeps the budget types |
| `scoring/search_point_scorer.py` (20) | yes | **stays**: the sole gateway, 150 lines. N8 leaves one caller chain above it |
| `optimizer_manifest.py` (20) | yes | N3: keeps find, parse and select |
| `resume_and_fork/fork_siblings.py` (20) | yes | N10: `bench/fork.py`, without its `served_dashboard` import and without `Opening`'s type edge (V1) |
| `scripts/complexity_ledger.py` (144), `scripts/gate.py` (38) | outside | N0 adds one count. They stay hubs while every landing edits a baseline; fewer landings is the only cure |
| `scripts/offline_run.py` (54), `scripts/offline_world.py` (23) | outside (the offline world) | N2 edits one function. Stage 2 |
| `webapp/lib/api/types.generated.ts` (97), `webapp/lib/test-fixtures.ts` (38) | outside, generated / fixtures | move with served shapes; W moves them once |
| `served_contract.py` (34), `served_dashboard.py` (33), `hierarchy.py` (32), `views/readout.py` (30), `evidence/read.py` (24), `hierarchy_subtree.py` (22), `evidence/head_to_head.py` (21), `domain/hierarchy.py` (20) | outside | N7 makes them views of one fold; N4, N8 and N10 cut the six backbone imports of them (V17). Their own shape is Stage 2 |
| `presentation/cli/commands/launch.py` (23), `presentation/cli/main.py` (20) | outside | N10 and N12 touch them once |

---

## 8. Residual rows

82 open rows in `## 01`–`## 10` (V16). **41 close with a move, 41 stay residual.** A † marks a mapping made
from the row's lead and both designs' notes without my reading its body.

**Closed by a move**

| Area | Row | Move |
|---|---|---|
| 01 | The layering is fictional at runtime | N0 holds the count; N3, N4, N6, N9, N10 cut the knots |
| 01 | Names collide across layers | N7 (`hierarchy.py` pair), N10 (`head_to_head.py` pair), N14 (`cells.py`); `power.py` is left |
| 01 | A closed round banks its election twice | W (decision 4) |
| 01 | What a child moved over its parent is computed twice | W (`CandidateDelta` from `loci()`) |
| 02 | `domain/sample.py`'s name | N14 |
| 02 | A line's walks are re-scanned per individual | N7 (`line_walks`), with N8 † |
| 02 | The `Stores` hub holds a cycle's stop machine | N6 (row body read) |
| 03 | `BenchStatus.state` spans `read` | N8 † |
| 03 | A block race files an `ArmRecord` per arm per turn | N8; rides W if the offline run shows the trace moves † |
| 03 | A refused send's stop and a banked hole's stop share one field | N5 (code), W (the field) † |
| 03 | A resume's divergence check and its cut are one function | N10 (`bench/{resume,fork}.py`) † |
| 03 | An inner run's spawn is read off the ambient scope | N4 (the field), N11 (the imports) |
| 03 | A cycle's export skips a lost bank | N4 † |
| 03 | `declare_line_bench` re-derives its scorer | N8, on N4's context † |
| 03 | An unbound session's address is two empty strings | N4 |
| 03 | A line's spent rounds at three sites | N7 † |
| 04 | A walk ended outside `land` names nothing it bought | N8 † |
| 04 | A connector's two-valued term reaches no scorer | N9 † |
| 04 | "Which benchmark a row measures" is spelled twice | N8 (its hold, "l4-runs owns `bench/line.py`", lapses when N8 owns the file; body read) |
| 04 | A hard-sample page resolves its own config | N8, with N9 (body read) |
| 04 | `SampleIndex`'s fold row carries a `degraded` nobody reads | W rider: it wipes a persisted fold (body read) |
| 04 | An estimator spec every caller fixes | N8 (body read) |
| 04 | `Election.deciding_pair` is typed wider | W † |
| 05 | A nested spend book is wired beside its cell | N10 (`_ENCLOSED`); N4 (`arm_spend_book`) |
| 05 | A landed job's cycle is asked twice | N10 † |
| 05 | A provider hold is filed twice under a nested run | N10 † |
| 05 | An outer run's overshoot may read its own ledger alone | N10 † |
| 06 | An arm's admission is held twice | N10 † |
| 06 | A check-in's cycle plan selects beside its refusal | N10 † |
| 06 | A queued launch's ending files where an unasked one does | N10, whose file set gains `commands/opening.py` for it (body read) |
| 06 | A fork's preview does not serve its launch's refusal | N10, with N9 † |
| 06 | A launch released refused reads as Detached | N10 † |
| 06 | A check-in offers Propose it would refuse | N10 (the refusal half; the wording stays) † |
| 07 | Params are not one fold | N9 |
| 07 | A resolved dataset is resolved again | N9 † |
| 07 | A knob's provenance is diffed | N9; its mint field on W |
| 07 | The node document is typed two ways; the L2 steer | N9; the steer as a layer on W † |
| 09 | A whole-candidate verdict books a placeholder axis | W rider (body read: "run data wiped, L2's rendered failure JSON moves") |
| 09 | The plugin contract is spread over seven modules | N3, per fork F2 |
| 09 | "wound" survives | W rider (body read: "re-keys potter") |
| 10 | An inner campaign's id is keyed by the instrument | built in N11, lands on W † |

**Residual (41), and why**

- **A measurement on the box, a paid run or a live session comes first (8):** 03 a resume's wait; 03 a
  max-press resume; 04 the first verify 38 s late; 04 held re-tests need world legs; 05 the default host pin;
  08 the box's 21 dead locks; 09 `l1_critique` on minimax; 10 the paid L4 walk's stop.
- **Held, or an operator decision (5):** 02 the Windows replace; 02 `write_json` indent; 04 the constant paired
  side; 05 a landing refunds nobody (fork F1, a predecessor of W); 06 a fork cannot narrow a dead axis.
- **Statistics, not structure (2):** 04 the warm gate's se_δ; 04 an L4 panel's θ over mixed rulers.
- **A cross-process wake that does not exist (3):** 04 a freed slot idles (two rows); 06 a command's settle.
- **Connectors, TermNorm, the box (7 more of area 08):** every area-08 row is outside the backbone.
- **Words on a surface (2):** 05 a free account's refusal; 06 the run dock's refusal.
- **Tests and the offline world (4):** 06 child-process facts; 09 CAPO's settled race path; 10 no L4 fixture
  buys ahead; 10 the estimator's golden corpus.
- **One defect in one place, off the backbone's shape (8):** 01 the data-root spec; 02 the feed's version;
  02 a landing waits on every producer; 02 `list_campaigns`; 02 a sandbox's owner file; 05 dbllmbench's key;
  06 a promoted launch's stderr; 07 an origin finding dropped.
- **A prompt or a feature (2):** 09 "slot" in LLM text; 10 the outer's edit menu.

Where the two designs' checks disagreed, I took the move that owns the row's named file: BU placed nine area-03
and area-04 rows on its run-context and pass moves, TD spread them over INIT and the held-out pass; both land
on N4, N8 or N10 here. TD's 34 mapped and BU's 42 differ mainly on rows TD marked † and left residual.

---

## 9. Stage 2: what stays outside the backbone, and what the backbone asks of it

| Subsystem | Stays outside | Constraint the backbone places on its design |
|---|---|---|
| Served reads and views (`views/`, `evidence/`, `served_dashboard.py`, `hierarchy*.py`, `pipeline_responses.py`, `read_params.py`) | yes | Read `CycleState` and `read_line`; never fold the ledger again. Never imported by ranks G–A7; a projection the run must drain joins by registration. Served shapes are built from `Round`, `Resolution`, the money fold; none is built inside a record |
| Surfaces (API, CLI, webapp, admin bot) | yes | One row per command is the only per-kind table. A surface may not hold a status vocabulary: three axes, derived. `types.generated.ts` moves once, at W |
| Identity, accounts, access | yes | Survivor rows migrate, never wipe. The store root is a leaf; an account store types against it, not `Stores` |
| Connectors | yes | Read-only during a run; the one write is `publish`, declared, behind `adopt`, acked only after a read-back. `connectors/protocol.py` types against the store root |
| Config and dataset editors (`application/datasets/`) | the editors, yes; eight readers are rank A1 | A reader imports nothing above rank A1. Which function is a reader and which an editor is this subsystem's stage; the ladder count tells it when it is done |
| `infrastructure/llm` | partly | The model catalogue (fork F3). `append_record` is not its to own. The spend book admits each send against one `Hold`; it never guesses a kind |
| Optimizer internals (potter's dispatch, CAPO, GEPA, LEVI) | yes | Import the contract, rank G and `domain` only. BU's D9 (≈ 520 lines inside `optimizers/`) is this stage's, not the backbone's. Any change to what a member sends is a re-key and waits for a re-key landing |
| Tests and the offline world | yes | `scripts/offline_run.py` is the judge of every "no re-key" claim above; it must be able to diff decisions, requests and (new) the bytes of derived files |

---

## 10. Where the merged design is weakest, and what to measure

1. **N7's premise is unmeasured.** That the round-rebuilding folds compute the same rounds, and that every
   derived file can stay byte-identical when derived from one fold, is asserted by BU and checked by nobody.
   *Measure:* on the offline world's cycles, dump each fold's rounds and diff them; then have `offline_run.py`
   hash `index.json`, `dashboard.json`, the round files and `readout.log` before and after N7a. If they differ,
   N7b moves onto W.
2. **The deleted-line total is an estimate built on estimates.** BU's 4,300 came from six sub-readers'
   inventories; one of its lists (M1) was mostly wrong when checked (V13). *Measure:* the surface ledger's
   package-line row after each landing, against the per-move figure in §4.
3. **W is very large.** Four chains, one landing, a wipe and a re-key, plus decision 4, which changes when ADAPT
   runs. *Measure:* on a branch, the offline run's decision trace per built-in before and after chain (a); a
   paid potter round is the only proof that closing at the election changes no selection. If W cannot be gated
   whole, the fallback is two wipes (rounds + passes; money + group + status), which the operator has not
   approved.
4. **N4 touches 116 parameter sites and nothing can run beside it.** *Measure:* a dry run of its first commit
   (types down only) and the type-cycle count after it; BU's "17 cycles close" is its own count.
5. **The survivor rows.** N10's three launch stages may reshape `job` or `queued launch` files (V20), and W
   changes how a `Hold` line on the charge book reads. Neither design checked both.
   *Measure:* `land-data --dry-run` on a root holding one queued launch, one live job and a charge book with a
   nested run's holds, before and after each.
6. **A group is a fifth entity.** §0 names four. The design adds it as a record and a hold, but
   `docs/architecture.md` §0 must say so in the same landing as W, or the contract and the code part.
7. **Names-bound keying has a known hole.** Two modules exporting one name with different behaviour are equal
   under the key on lines the offline world never runs. *Measure:* the share of member lines the world runs
   (`ran_lines`) per built-in; a low share makes the hole wide.
8. **The ladder does not finish `application/datasets/`** (TD says so), and the type-cycle target of ≤ 5 is a
   target, not a count.
