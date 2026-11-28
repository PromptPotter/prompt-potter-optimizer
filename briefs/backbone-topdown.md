# Backbone end state, top-down (tip ff429d376)

Sources: `CLAUDE.md`, `docs/architecture.md` §0/§0.5, `docs/decisions.md`, the layer `CLAUDE.md` files, MAP Target
lines (MAP's Deviations are stale against this tip and were not used), DECISIONS.md, the seven memory rules.

How the code was read: `git grep` outlines of top-level defs, per-file import lists, every `from promptpotter.`
line run through the rank map of §0, and full reads of `domain/results.py`, `domain/records/rounds.py`,
`runner/round.py` (330–643), `initialization/session.py`, `read_model.py` (756–855) and the head of
`optimizer_contract.py`. A claim marked **(verify)** comes from the contract and an outline, not from a read of
the body. §1–6, §8 and §9 were written before the debt doc was opened. §7 is the check; after it, the ladder was
measured against the tip and §0, §5 corrected where the measurement contradicted the first draft (said in §7).

## 0. The thesis

Every backbone hub on the fact sheet is one of three shapes. The end state removes the shape, not the file.

1. **A file on two ranks.** `application/` states no import order, so packages import each other both ways.
   A file on such a cycle is edited by every chain on either side of it.
2. **A file that holds every concept's fold.** `ledger_scan.py` (1,158 lines, about 20 `Line` declarations for
   rounds, money, bench, verify, index, clocks, timelines) is touched by any new ledger fact.
3. **One concept in several shapes.** A round (`RoundOutcome`, `RoundStatement`, `RoundResult`,
   `RoundClosedRecord`, `RoundSummary`, `StandingRound`, `IndexRound`); a resolution (`Resolution`, `RunResolution`,
   `DraftPipeline`, `LaidConfig`, `LineConfig`, `RootRunsWith`, two `ResolvedRun`s, `resolve_pipeline_for_dataset`);
   a hold (campaign ceiling, cycle share, run hold, the head-to-head's budget, L4's root filing, `_ENCLOSED`);
   a session (three lifecycle stages as `None` fields); a held-out pass (two executors).

### The import ladder (the one new rule; flat packages stay, per the settled decision)

Each rank imports only ranks to its left. A module sits on one rank.

```
shared < domain < config
  < infrastructure/store < infrastructure/llm < infrastructure/projections, infrastructure/backend.py
  < G  grading: scoring/formula/, scoring/judges/, scoring/evaluators.py, scoring/paired.py,
       scoring/race_protocol.py                              (what a grade and a race are; no run needed)
  < A0 optimizer_contract.py, optimizer_knobs.py            (types the bench and a plugin trade)
  < A1 pipeline_resolve.py, optimizer_manifest.py, optimizer_identity.py, config_map.py,
       the dataset READERS (see below)                       (which values, which optimizer, which bank)
  < A2 jobs/ (registry, gate, holds, quota, the asks, launcher/opening.py), run_stop.py,
       run_phase_control.py, free_tier.py, account.py        (may it start; how it stops)
  < A3 session.py                                            (what a run carries)
  < A4 scoring/ (gateway, walk, cell), intelligence/, mask/  (measuring)
  < A5 bench/                                                (Cycle, NodeContext, llm_call, held-out pass, resume/fork)
  < A6 initialization/, origin.py, preflight.py              (INIT: mint, open, wire)
  < A7 runner/{loop,round,measure,entry}.py                  (the loop)
  < A8 doors: commands/, embedded_run.py, queued_launch.py, inner/ (L4), optimizers/ (members, loaded by
       registry), maintenance/
```

Outside the backbone (`views/`, `evidence/`, `served_dashboard.py`, `hierarchy*.py`, `pipeline_responses.py`,
the dataset EDITORS, `presentation/`) sits above A7 and is never imported by G–A7. A projection the backbone
must drain but cannot import (`views/readout.py`) joins the `Projecting` roster by module in
`maintenance/landing.py::complete_registries`, the mechanism `application/CLAUDE.md` § Registries already names.

**Measured against the tip.** With this rank map the ladder is broken by the edges below and no others (doors
importing "outside" are not counted). Each has a cut.

| Rightward edge at the tip | Cut by |
|---|---|
| `optimizer_contract.py` → `run_stop` (`StopLoop`) | M7: `StopLoop` is a type in `domain/stop.py` |
| `pipeline_resolve.py` → `initialization/session` (`laid_session`, `run_resolution`, `bind_resolution`) | M8: the session-binding functions are INIT's (`initialization/wiring.py`) |
| `pipeline_resolve.py` → `jobs/quota`, `free_tier` (`draft_budget`, `draft_sends`) | M8: pricing a draft is `jobs/holds.py` reading a `Resolution` |
| `pipeline_resolve.py` → `pipeline_responses` | M8: served shapes are built from a `Resolution`, never inside it |
| `jobs/mint.py`, `launcher/mint_and_start.py`, `launcher/checkin.py`, `launcher/head_to_head.py` → `initialization/*`, `run_observers`, each other | M9: rank-A6 code in a rank-A2 package; it moves to `initialization/` |
| `jobs/launcher/launch.py` → `checkin`, `mint_and_start` (types) | M9: `LaunchRequest` and its arms are `domain/job.py`'s |
| `admission.py`, `held.py`, `bench/…/fork_siblings.py` → `launcher/opening` | none if `opening.py` imports nothing above A2 **(verify)**; it stays in `jobs/` |
| `scoring/passes.py` → `bench/{difficulty,export,line}`, `initialization/scoring_scope`, `views/readout` | M10: a held-out pass is rank A5; the verb that opens a diagnostic session is a door |
| `scoring/measurement_log.py` → `bench/difficulty` | M12 |
| `bench/node_context.py`, `initialization/scoring_scope.py`, `runner/run_init.py` → `runner/inner/*` | M14 |
| `run_observers.py`, `runner/entry.py`, `runner/run_init.py`, `bench/…/fork_siblings.py` → `served_dashboard`, `views/readout` | M9: the roster is completed by registration |
| `store/*` → `llm/money_fold` (4 files); `store.py` → `projections/live_dashboard` | M3 |
| `store/data_root.py` → `llm/base`, `llm/pricing`; `store/llm_reuse_cache.py` → `llm/*`; `run_state.py` → `llm/heartbeat` | M3 re-homes: `data_root.py` ranks last in infrastructure (it names every reader), the reuse cache is `llm/`'s, `heartbeat.py` is infrastructure's |
| `store/dataset_access.py`, `backend.py` → `connectors/protocol`, which imports `Stores` | **not cut here**: part of the store-hub knot (§7) |

**`application/datasets/` sits on two ranks and this design does not finish it.** The backbone imports eight of
its modules (`loaders`, `authored`, `prompts`, `tabular_ingest`, `draft_campaign`, `startability`, `draft_build`,
`origin_readiness`), while `datasets/` imports `scoring` (6 edges), `jobs` (4) and `pipeline_resolve` (5). At the
end state those eight are A1 readers importing nothing above A1 and the rest are editors outside the backbone.
Which function lands on which side is the config subsystem's stage, bound by this rule.

## 1. Concepts

Columns: owner · §0 bucket · what it is · replaces today · reported by · Rust (as is / reshape).

### Records and the round envelope

| Concept | Owner | §0 bucket | One line | Replaces today | Reported by | Rust |
|---|---|---|---|---|---|---|
| Individual | `domain/opt_search_point.py` | Purpose · two-layer searchpoints | `{locus: value}` with a DAG lineage; id is content | stays | round file, lineage read | as is |
| Target spec | `domain/search_point.py` | same | the frozen spec a cell measures | stays | archive row address | as is |
| Arm | `domain/arms.py` | Bench and optimizer · neutral envelope | one individual's state in one round | stays | `ArmRecord` | as is (tagged enum) |
| Cell | `domain/scoring.py` (`MeasuredCell`, `GradedCell`, `CellSheet`, `Scorer`) | Measurement archive | one measured fact and its grade under one scorer | the file's other half (`ScoringSpec`, `Dial`, `anchored_criterion*`, `ConstantAnswer`) moves to `domain/scoring_spec.py` | archive row; round file | as is |
| Bank | `domain/bank.py` | Four entities · Dataset | the samples a dataset's content is, and their keys | `domain/sample.py`'s first half | `PartitionRecord` | as is |
| Archive row | `domain/archive_row.py` | Measurement archive | what is filed under `(config_key, sample_key)` | `domain/sample.py`'s second half (`ArchiveConfig`, `ArchiveEntry`, `Grading`, `FiledAnswer`) | `measurements/` | as is |
| Round | `domain/round.py` | neutral envelope | ONE model, `Round`: arms, cell addresses, election, origin panel, optimizer state and facts. A round with rows is `RoundRows(round, cells, panel_rows)`, by composition, never stored | `RoundOutcome` + `RoundStatement` + `RoundResult` across `domain/results.py` and `records/rounds.py`; `run_standing`, `panel_precision`, `health` and `summary()` leave it | `RoundClosedRecord`; round file | reshape today (three-level inheritance); as is after |
| Pass | `domain/pass_kinds.py` + `domain/records/passes.py` | Central loop · sole gateway | a measuring walk with an arm and a kind (race, panel, verify, bench, noise floor): opened, cell scored, graded | `records/sample_passes.py` + `records/held_out.py`; `RaceCatchUpRecord` and every `CatchUp*` (23 hits / 6 files) | the pass records | as is |
| Ledger record | `domain/records/<family>.py`, kinds by `domain/record_kinds.py` | State + persistence · Persistence | one fact, one module per family, **with the folds that read it** | the fold classes of `ledger_scan.py` | the ledger itself | as is, once the import-walk registry is an explicit enum |
| Line fold | `domain/line_fold.py` (`Line`, a `Scope` NAME enum) | Persistence | a pure `(scope name, fold, then)` declaration; `read_line` resolves the name to ledgers and executes it | `Line` in `read_model.py`. `LineScope` holds a function over paths that reads ledgers, so it stays in the store as the name's resolver | n/a (mechanism) | as is |
| Optimizer state | `domain/optimizer_state.py` | two-layer searchpoints | typed parts riding the round | stays | `Round.optimizer_state` | reshape (`StatePart(kind=…)` class-kwarg registry → enum) |
| Run standing | `domain/run_standing.py` | §0.5 · a cycle's best has two bases | ONE fold of a line's rounds plus its bench readings | the copy stored on every statement (`run_standing`: 35 hits / 24 files) | a `Line` read; `index.json` | as is |
| Stop | `domain/stop.py` + `application/run_stop.py` | Display · run state is derived | `StopReason`, `StopLoop`, the reading; which stop a fault or a budget is; the one function that declares it | the stop half of `domain/phases.py`. At the tip the one WRITER is already `Stores.declare_stop` (callers: `run_observers.py::declare_run_stop`, `launcher/opening.py`, itself). What is spread is who decides WHICH stop (`run_observers.py::end_run_on`, `run_phase_control.py::budget_stop`, `query_loop.py::_budget_stop`, `run_stop.py`), and that the store owns the writer | `RunPhaseRecord` | as is |
| Run phase / state | `domain/phases.py` | Display | the phase vocabulary and the derived `RunState` | keeps what `stop.py` does not take | `RunPhaseRecord`, `derive_run_state` | as is |

### Store, ledger, projections

| Concept | Owner | §0 bucket | One line | Replaces today | Reported by | Rust |
|---|---|---|---|---|---|---|
| Ledger ingress | `infrastructure/ledger.py::CycleEventLog.append` | Persistence | the sole writer | stays | — | as is |
| Ledger index | `infrastructure/store/read_model.py` (`LedgerIndex`, `read_line`, the scope resolvers, `Moment`) | Persistence | the incremental tail that feeds every registered fold, and the one keyed read | `read_model.py` less its row-file readers (→ `io.py`), less `Projection`/`Projecting` (→ `projections/projection.py`), less `Line` | — | as is |
| Campaign store | `infrastructure/store/campaign_store.py` | Four entities · Campaign, Cycle | the door of a named cycle: mint its directory, append, read, destroy | `campaign_store/store.py`; the package of two flattens once `ledger_scan.py` is gone; `CycleIndexProjection` leaves for `projections/` | — | as is |
| Stores | `infrastructure/store/stores.py` | Four entities · Workspace | the tenant's datastore handle | loses `declare_stop` | — | as is |
| Measurement archive | `infrastructure/store/measurement_archive.py` | Measurement archive | the only gateway to `measurements/` | stays | — | as is |
| Data-root table | `infrastructure/data_root.py::KINDS` | decisions § 0011 | one row per file kind; survivor or run data | `store/data_root.py`, re-homed because it names llm's readers too | `reset`, the landing | as is |
| Projection | `infrastructure/projections/{projection,live_dashboard,cycle_index,audit_trail,event_stream}.py`; a projection that grades stays in `scoring/closed_rounds.py` | Display | a checkout of a fold nothing reads back | `Projection` in `read_model.py`; `CycleIndexProjection` in `store.py`; `run_observers.py`'s roster | its file; `derived_file_stale` | as is |
| Landing | `application/maintenance/landing.py` | decisions § 0011 | proves the root, wipes run data, migrates survivors, completes registries | stays | landing key | reshape (registries completed by import) |

### Scoring and fitness

| Concept | Owner | §0 bucket | One line | Replaces today | Reported by | Rust |
|---|---|---|---|---|---|---|
| Scoring gateway | `scoring/search_point_scorer.py` | Central loop | the one function that scores individuals on rows | stays | pass records | as is |
| Walk | `scoring/walk.py` | Central loop | the ordered cells of arms under a race protocol, stoppable | `query_loop.py` (1,385) less `CatchUps` and the recorder | pass records | as is |
| Cell measurement | `scoring/sample_measurement.py` | Central loop · no retry | one `(sample, individual)` replayed by content or bought once | stays | `SampleScoredRecord`, archive | as is |
| Race protocol | `scoring/race_protocol.py` | The bench score is not the selection | what an eliminator decides over a walk's view | stays (rank G) | `RaceStandingRecord` | as is (trait) |
| Held-out pass | `bench/held_out.py` | The bench score is not an optimizer's selection | run ONE pass of a declared pick on held-out rows: admit, walk, grade, record | `scoring/passes.py` (it imports bench and INIT, so it was never rank A4) + the executing half of `bench/line.py` (`score_on_bench`, `reserve_selection_pass`, `declare_line_bench`) | `PassGradedRecord` | as is |
| Bench line | `bench/line.py` | §0.5 two bases | the line's held-out readings: which pick, the curve, the outlay | the reading half of `bench/line.py` | `Line` reads | as is |
| Evaluator / formula | `scoring/evaluators.py`, `scoring/formula/`, `domain/fitness_fold.py` | §0.5 fitness is formula-relative | terms, formula, fold | stays (rank G) | served score | as is |
| δ ruler | `domain/ruler.py`, `store/ruler_store.py`, `bench/difficulty.py` | §0.5 one tenant δ ruler | the fixed scale every θ is read on | stays | `ruler.jsonl`, `LevelSeriesRecord` | as is |
| Degradation check | `scoring/classification.py` | Errors heal upward | the bench's per-arm failure halt | stays | `Cut.kind`, `StopReason` | as is |
| Hard-sample order | `scoring/row_policy.py` | §0.5 hard-sample sorter | the round's row order | the backbone half of `scoring/measurement_log.py`; its served cells view leaves for `views/` **(verify)** | `RoundProposedRecord.reads` | as is |

### Runner, round, bench

| Concept | Owner | §0 bucket | One line | Replaces today | Reported by | Rust |
|---|---|---|---|---|---|---|
| Session | `application/session.py` | Layering · init writes Session | frozen, always bound: store, backend client, campaign, cycle, resolution, bank, partition | `initialization/session.py`; the `None` stages (`campaign`, `resolution`, `drawn`, `cycle_id ""`) become a separate `Services` that `wiring.py` returns before a bind | `RunWiringRecord`, `ResolvedRunRecord` | as is |
| Run scope | `infrastructure/run_scope.py` | Layering | the one ambient value | stays | — | reshape (ContextVar → explicit or task-local) |
| Cycle | `bench/cycle.py` | Four entities · Cycle | one running cycle in memory | stays | every field is a fold of its ledger | as is |
| Node context | `bench/node_context.py` | Dispatch hub · every optimizer is a manifest | the implementation of what a member may reach | stays; the face a member types against moves to the contract; its `runner.inner.transcript` and `domain.l4.proxies` imports leave | — | reshape (capability by declaration → traits) |
| Round walk | `runner/round.py` | Central loop | PROPOSE, SELECT, ADAPT, close, one function each | stays; round 0's own path joins it | phase records, close | as is |
| Measure | `runner/measure.py` | Central loop | MEASURE under the eliminator's race | stays | pass records | as is |
| Loop | `runner/loop.py` | Central loop | rounds until a stop | loses `open_origin_round`, `walk_round_zero`, `bench_line_origin` (round 0 is a round) **(verify)** | `RunPhaseRecord` | as is |
| Run entry | `runner/entry.py` | Layering · entry points | open → loop → close for one admitted job, moving its job's stage | stays where db31dcb5c put it ("the run leaves jobs/"): `runner → jobs` is leftward and legal. Loses only its `served_dashboard` and `maintenance.landing` imports | job file, cycle stop | as is |
| Run control | `application/run_phase_control.py` | Control-local · pause is the single interrupt | pause, flight gauge, producer hold | loses `budget_stop` to `run_stop.py` | `RunPhaseRecord`, `FlightRecord` | as is |
| INIT | `initialization/{mint,open_cycle,wiring,scoring_scope,group}.py` | Layering; Four entities · one `declare` | mint a campaign or cycle, open it, wire a `Session` | `jobs/mint.py`, `jobs/launcher/mint_and_start.py`, `jobs/launcher/checkin.py`, `jobs/launcher/head_to_head.py`, `runner/run_init.py` | `CycleMintedRecord`, `PartitionRecord`, `RunWiringRecord` | as is |
| Resume / fork | `bench/resume.py`, `bench/fork.py` | §0.5 resume and fork-on-divergence | replay by digest; `cut_fork` the one writer | `bench/resume_and_fork/` (5 modules; `decisions.py` is 13 lines; `ab_replay.py` is an evidence verb and leaves) | `DecisionRecord` | as is |
| Check-in | `bench/task_context.py` | Central loop · origin and check-in | the bench's one LLM node | stays; takes `CheckinManifest` from `optimizer_manifest.py` | draft file, draft ledger | as is |
| Optimizer call | `bench/llm_call.py` | Dispatch hub | the one door from a member to a model | stays | `LLMCallRecord` | as is |

### Optimizer contract and members

| Concept | Owner | §0 bucket | One line | Replaces today | Reported by | Rust |
|---|---|---|---|---|---|---|
| Plugin contract | `application/optimizer_contract.py` | Every optimizer is a manifest | the member Protocols, the round types they trade, the member's face of `NodeContext`/`RoundContext` as Protocols, the two registries; imports `domain` and rank G only; the one module a plugin imports | today's file less knobs, couplings, `BehaviourKeys`; its four `TYPE_CHECKING` imports of bench, config_map and manifest go | — | as is (traits) |
| Knob types | `application/optimizer_knobs.py` | an optimizer's knobs are its nodes' `config` | shared knob mixins and `MemberCoupling` | the knob half of `optimizer_contract.py` | `config_map` read | as is |
| Manifest | `application/optimizer_manifest.py` | Every optimizer is a manifest | parse a manifest, select it into a `RoundPlan` | today's file less identity and the check-in manifest | `ResolvedRunRecord` | as is |
| Behaviour key | `application/optimizer_identity.py` + `assets/behaviour_keys.json` | §0.5 behaviour key | an optimizer's identity for L4 and banked origins | `behaviour_key`, `BehaviourKeys`, `Treatment` wiring | the generated file; the gate | as is |
| Members | `optimizers/<name>/members.py`; shared `pools/`, `variation/` | decisions § 0007 | one module per optimizer stating its rule once | stays | optimizer facts, step records | as is |
| Dispatch hub | `optimizers/potter/dispatch/` | Dispatch hub | `injection_table()`; where a prompt is shaped | stays (potter's, a kept layer) | `LLMCallRecord` breakdown | as is |

### Config and resolution

| Concept | Owner | §0 bucket | One line | Replaces today | Reported by | Rust |
|---|---|---|---|---|---|---|
| Dataset-file seam | `infrastructure/store/dataset_access.py::resolve_dataset` | Two resolution seams | which bytes | stays | `ResolvedDataset` | as is |
| Effective-config seam | `application/pipeline_resolve.py` | Two resolution seams | ONE `Resolution`, built by one `resolve(subject)`; the subject is a dataset, a draft, a campaign root or a cycle at an offset | `Resolution` + `RunResolution` + `DraftPipeline` + `LaidConfig` + `LineConfig` + `RootRunsWith` + `resolve_pipeline_for_dataset` (4 hits / 2 files) + `ledger_scan.py`'s two `ResolvedRun`s | `ResolvedRunRecord` | as is |
| Param fold | `pipeline_resolve.py::ParamLayers` + `domain/layer_fold.py` | Backend overlay | one fold, in `ParamLayer`'s order | stays | `knob_layers` on the resolved-run record | as is |
| Declaration | `domain/campaign.py::RunDeclaration` | Four entities · Campaign; controlled comparison | what a campaign is and what it is for | stays | campaign file | as is |

### Spend and admission

| Concept | Owner | §0 bucket | One line | Replaces today | Reported by | Rust |
|---|---|---|---|---|---|---|
| Budget | `domain/spend.py` | §0.5 a budget is never one value | one value at four positions | stays; the money words (`usd_text`, `bill_text`, `money_text`, `metered_line`, `replay_text`) and the account shapes leave for `domain/money_text.py` | — | as is |
| Money fold | `domain/money_fold.py` | §0.5 spent is a fold | the ONE fold of money records | `infrastructure/llm/money_fold.py`, the spend `Line`s of `ledger_scan.py`, `domain/spend_rollup.py` | every money read | as is |
| Hold | `domain/records/money.py::Hold` + `application/jobs/holds.py` | decisions § 0003 | ONE nest: account → group → campaign → cycle → run → call; each hold names its parent | `jobs/shares.py`; the budget half of `quota.py`; `CampaignCeilingRecord` / `CycleShareRecord` as kinds apart; `spend_book.py::_ENCLOSED` (5 hits); the head-to-head's own budget; L4's root filing | hold records, money fold | as is |
| Spend book | `infrastructure/llm/spend_book.py` | §0.5 only the spend book halts | the run's admission of each send against its hold | stays; `SpendMeter` goes (a count is a `read_line` of the money fold) **(verify)** | `TokenUsageRecord` | as is |
| Payer | `infrastructure/llm/wallet.py::sending_key` | decisions § 0003 | who pays a send | stays | charge | as is |
| Quota | `application/jobs/quota.py` | Layering · one admission seam | launch refusals: rate, daily count, coupon, key | keeps refusals only | refusal | as is |
| Admission | `application/jobs/launcher/admission.py` | Layering · one admission seam | the asks: `request_jobs`, `paid_pass`, `paid_call`, `slotless_opening` | loses the probes and campaign refusals (→ `preflight.py`) and `destroying`/`deletable_stubs` (→ `commands/cycle_cleanup.py`) | job file, machine status | as is |
| Job | `domain/job.py` + `application/jobs/registry.py` | Layering | a launch and its moves; `LAUNCH_MOVES` the only writer; `LaunchRequest` and its arms live beside `Job` | `LaunchRequest`, `FreshMint`, `ExistingCycle`, `ForkLaunch`, `InnerSeat` move in from `mint_and_start.py` | job file, live-jobs index | as is |
| Preflight | `application/preflight.py` | Layering | every "may this launch" refusal, one home | plus `admission.py`'s `probe_backend`, `refuse_unreachable`, `campaign_refusal`, `launch_preflight`, `refuse_campaign` | refusal | as is |

No state without a reporter. The design removes two states nothing reports: a `Session` bound to no campaign,
and a hold whose kind is guessed (`_ENCLOSED`). A third, a round between its election and its close holding
`optimizer_state=None`, goes only if fork F3 is taken.

## 2. End-state module tree (lines today → end; ≈ are estimates; the owning move in brackets)

```
domain/
  round.py                    new ≈300   [M4]  (results.py 340 + RoundStatement)
  results.py                  340 → 0    [M4]
  records/rounds.py           218 → ≈260 [M3 folds, M4 shape]
  records/passes.py           new ≈200   [M5]  (sample_passes.py 83 + held_out.py 45 + folds)
  records/money.py            257 → ≈300 [M6]
  records/lineage.py          204 → ≈320 [M3]  (+ index, arm-act, launch-clock, timeline folds)
  records/run_wiring.py       132 → ≈200 [M3]  (+ resolved-run fold)
  line_fold.py                new ≈90    [M3]
  money_fold.py               new ≈450   [M3]  (llm/money_fold.py + spend Lines + spend_rollup.py 404)
  spend.py                    726 → ≈430 [M3b]
  money_text.py               new ≈300   [M3b] (served words; outside the backbone)
  phases.py                   878 → ≈450 [M7]
  stop.py                     new ≈440   [M7]
  scoring.py                  948 → ≈600 [M3b]
  scoring_spec.py             new ≈350   [M3b]
  sample.py                   244 → 0    [M3b] → bank.py ≈120, archive_row.py ≈130
  cells.py                    369 → 0    [M3b] → cell_rows.py (served rows) and L4's Seat/PinnedCell to domain/l4/
  job.py                      242 → ≈360 [M9]
  campaign.py                 376 → ≈390 [M15]

infrastructure/
  data_root.py                832 (from store/)            [M3]
  heartbeat.py                (from llm/)                  [M3]
  llm/reuse_cache.py          148 (from store/)            [M3]
  store/read_model.py        1012 → ≈650 [M3]
  store/io.py                1031 → ≈1100 [M3]
  store/campaign_store.py     new ≈560   [M3]  (campaign_store/store.py 721)
  store/campaign_store/ledger_scan.py 1158 → 0 [M3] (≈1,000 lines of folds move to domain; ≈150 of scope resolvers to read_model.py)
  store/stores.py             289 → ≈250 [M7]
  projections/projection.py   new ≈70    [M3]
  projections/cycle_index.py  new ≈60    [M3]
  projections/live_dashboard.py 925 → ≈850 [M4, M5]
  llm/spend_book.py           829 → ≈650 [M6]

application/
  optimizer_contract.py       856 → ≈620 [M2]
  optimizer_knobs.py          new ≈260   [M2]
  optimizer_manifest.py      1148 → ≈760 [M13]
  optimizer_identity.py       new ≈220   [M13]
  pipeline_resolve.py        1282 → ≈650 [M8]
  pipeline_responses.py       448 → ≈850 [M8]  (served; outside)
  session.py                  new ≈200   [M2 move, M9 always-bound]
  run_stop.py                 127 → ≈260 [M7]
  run_observers.py            240 → 0    [M9]
  run_phase_control.py        231 → ≈200 [M7]
  preflight.py                340 → ≈560 [M9]
  jobs/holds.py               new ≈480   [M6]  (shares.py 298 + quota's budget half)
  jobs/quota.py               779 → ≈380 [M6]
  jobs/launcher/admission.py  715 → ≈400 [M9]
  jobs/launcher/{launch,held,opening}.py   561 → ≈540
  jobs/launcher/{mint_and_start,checkin,head_to_head}.py  765 → 0 [M9, M15]
  jobs/mint.py                811 → 0    [M9]
  initialization/mint.py      new ≈850   [M9]
  initialization/open_cycle.py new ≈480  [M9]  (runner/run_init.py 392 + checkin.py 179, de-duplicated)
  initialization/group.py     new ≈260   [M15]
  initialization/wiring.py    268 → ≈400 [M8, M9]
  scoring/walk.py             new ≈1000  [M12] (query_loop.py 1385)
  scoring/passes.py           665 → 0    [M10]
  scoring/measurement_log.py  634 → 0    [M12] (≈150 to row_policy.py, the rest to views/)
  bench/held_out.py           new ≈750   [M10]
  bench/line.py               946 → ≈450 [M10]
  bench/node_context.py       838 → ≈760 [M2, M14]
  bench/resume.py, fork.py    new ≈540   [M9]  (resume_and_fork/ 756 less ab_replay 205)
  runner/loop.py              246 → ≈150 [M11]
  runner/round.py             643 → ≈600 [M4, M11]
  runner/measure.py           730 → ≈650 [M5]
  runner/entry.py             640 → ≈600 [M9]
  runner/run_init.py          392 → 0    [M9]
  inner/{spawn,tasks,transcript}.py  1053 → ≈850 [M14]
```

Reading the numbers: about 19 files go and about 22 appear; the count is no target. Of the lines the moves take
out of backbone files, roughly 2,000–2,500 are deleted (catch-up, the origin's own walk, duplicate resolutions,
`SpendMeter`, the session's unbound stage, `run_observers`, the hold kinds) and roughly 1,500 only leave the
backbone for served modules (`pipeline_responses.py`, `money_text.py`, `cell_rows.py`, `views/`, `evidence/`).
The end state is checked by one number: the ladder's rightward edges are 0.

## 3. Deletions

Modules: `store/campaign_store/ledger_scan.py` and the `campaign_store/` package; `application/run_observers.py`;
`jobs/mint.py`, `jobs/shares.py`, `jobs/launcher/mint_and_start.py`, `launcher/checkin.py`,
`launcher/head_to_head.py`; `runner/run_init.py`; `scoring/query_loop.py` (renamed, smaller), `scoring/passes.py`,
`scoring/measurement_log.py`; `bench/resume_and_fork/` as a package, with its `decisions.py`;
`domain/results.py`, `domain/sample.py`, `domain/cells.py`, `domain/spend_rollup.py`,
`domain/records/sample_passes.py`, `records/held_out.py`; `infrastructure/llm/money_fold.py` (moved).

Classes and fields: `RoundOutcome` and `RoundResult` as an inheritance chain; `RoundStatement.run_standing`,
`.panel_precision`, `.summary()`, and `.health` **(verify: it is "graded against the rounds before it", so it is
a line fold; keep it only if a resume must read it before the line is folded)**; `RaceCatchUpRecord`, `CatchUps`,
`CatchUpLogEntry`; `CampaignCeilingRecord` and `CycleShareRecord` as kinds apart from `Hold`; `HeadToHeadRecord`
(a `Group`, fork F2); `DraftPipeline`, `LaidConfig`, `LineConfig`, `RootRunsWith`, `ledger_scan.py::ResolvedRun`
and `_ResolvedRun`; `SpendMeter`; `Session.bound_campaign`, `.bound_resolution` and every `None` stage;
`RunObservers`.

Mechanisms: `spend_book.py::_ENCLOSED` and `enclosed_as`; `Stores.declare_stop`; the origin round's own walk
(`open_origin_round`, `walk_round_zero`, `bench_line_origin`, `emit_origin_round`); `resolve_pipeline_for_dataset`;
the four `TYPE_CHECKING` imports in `optimizer_contract.py`; every rightward edge in §0's table. Only if fork F3
is taken: `ElectionRecord`, `settle_owed_outputs`, `_adapt_beside_overlap`, `RoundStatement.restated`.

Second spellings: "a pass" (three record families → one); "a round" (seven shapes → `Round`, `RoundRows`, and
the served row); "a resolution" (eight → one); "a hold" (six → one nest); "a held-out pass" (two executors →
one); "cell" (`domain/cells.py` → `cell_rows.py`, so the word names the fact).

## 4. Hubs

Backbone files only; a file outside it gets one line on whether the backbone still imports it.

| File (commits since 11-14) | Kind | End state |
|---|---|---|
| `assets/behaviour_keys.json` (43) | generated | stays generated. It moves at every re-key; it falls because re-keys are batched into two landings (§5). |
| `maintenance/built_key.py` (39) | generated | already gone at this tip (0663bd745: the key is derived in `landing_key.py`). Nothing to do. |
| `scoring/passes.py` (35) | two ranks | imports bench, INIT, views and jobs. Its work moves to rank A5 as `bench/held_out.py`; the file goes. |
| `runner/round.py` (35) | legitimately central | already 643 lines. Loses close-time derivations to folds and gains round 0. Stays, quieter. |
| `domain/records/run_records.py` (32) | all records in one file | already split into `records/*` (dd4c0c085). The successors stop being co-edited with `ledger_scan.py` once each family holds its own folds. |
| `store/campaign_store/store.py` (31) | multi-concept | the cycle door, the index projection, money banking. Keeps the door. |
| `bench/node_context.py` (31) | legitimately central | the one member surface. Stays; edited only when a capability is added. |
| `scoring/measurement_log.py` (28) | backbone + served read | split; the file goes. |
| `jobs/launcher/admission.py` (28) | multi-concept | asks, probes, campaign refusals, destroy helpers. Keeps the asks. |
| `runner/inner/spawn.py` (27) | L4 on the wrong rank | builds a launch by hand; bench and INIT import its package. Becomes a `LaunchRequest(InnerSeat)` through INIT (fork F5). |
| `pipeline_resolve.py` (27) | one concept in eight shapes, on a cycle with `jobs` and `datasets` | one `Resolution`; session binding, draft pricing and served shapes leave. |
| `jobs/mint.py` (27) | two ranks | rank-A6 code in a rank-A2 package. Moves to `initialization/mint.py`. |
| `projections/live_dashboard.py` (26) | legitimately central for Display | stays the one live fold; no longer edited for catch-up or for row shapes built in a record. |
| `runner/bench.py` (26) | successor `bench/line.py` | splits into the pass (`held_out.py`) and the line's readings. |
| `run_observers.py` (26) | three concepts | which stop, the wiring declaration, the projection roster. Deleted; each goes to its owner. |
| `scoring/query_loop.py` (25) | multi-concept | walk engine + catch-up + recorder + a budget-stop classifier. |
| `runner/entry.py` (25) | legitimately central | the run's entry, by db31dcb5c. Stays; stops importing a served read and the stop classifier. |
| `domain/command_kinds.py` (24) | registry by contract | stays: one row per command kind is the design. |
| `optimizers/nodes.py` (24) | successor `optimizer_contract.py` | splits by reader: what the bench reads, what config reads. No upward import. |
| `maintenance/landing.py` (23) | registry by contract | stays: every reshape names its wipe here. Churn falls as wipes batch. |
| `bench/cycle.py` (23) | legitimately central | the cycle in memory. Stays. |
| `optimizers/potter/members.py` (22) | potter's own | one module per optimizer is the rule. Stays. |
| `store/data_root.py` (21) | registry by contract | stays, re-homed. |
| `store/campaign_store/ledger_scan.py` (21) | every concept's fold | deleted. |
| `domain/spend.py` (20) | multi-concept | budget types + served money words + account shapes. Keeps the budget. |
| `scoring/search_point_scorer.py` (20) | legitimately central | the sole gateway, 150 lines. Stays. |
| `optimizer_manifest.py` (20) | multi-concept | parse/select + identity + check-in manifest. Keeps parse/select. |
| `bench/resume_and_fork/fork_siblings.py` (20) | load-bearing | `cut_fork`, the one fork writer. Stays as `bench/fork.py`, without its `served_dashboard` import. |
| `store/read_model.py` (18) | multi-concept | index + row readers + projection base + line types. Keeps the index. |
| `served_contract.py`, `served_dashboard.py`, `hierarchy.py`, `hierarchy_subtree.py`, `views/readout.py`, `evidence/read.py`, `evidence/head_to_head.py`, `domain/hierarchy.py`, CLI `launch.py` / `main.py`, `types.generated.ts`, `test-fixtures.ts`, `scripts/*` | outside | the backbone stops importing all of them (today `runner/entry.py`, `run_observers.py`, `runner/run_init.py`, `fork_siblings.py`, `scoring/passes.py` import two). They still churn with served shapes, but a served row no longer drags a backbone file. |

## 5. Moves

Identity columns: **run data** (any `CycleRecord` or model under `Round`, `CampaignConfig`, `Campaign`),
**measurement key** (caches), **behaviour key** (banked origins, L4). `optimizer_manifest.py::behaviour_key` keys a
built-in by its fingerprint on the offline world. Architecture §0.5 says a rename on a run line re-keys nothing
and an edit to an unrun line re-keys by its AST and the names it calls. So a pure module move may re-key through
unrun lines; `scripts/offline_run.py` is the judge, and re-keys are batched into the two landings named below.

The moves are a chain. Parallel pairs are named; any two others share a file.

| # | Move (one root) | File set | After | Run data | Meas. key | Behaviour key | Size (+/−) |
|---|---|---|---|---|---|---|---|
| M1 | **The ladder is a gate check.** `gate.py::_LAYERING` gains the rank order; today's rightward edges are held as ONE count in `complexity_ledger.py::BASELINE` (`ladder_edges`, as `type_cycles` is), which no move may raise. | `scripts/gate.py`, `scripts/complexity_ledger.py`, `application/CLAUDE.md` | — | no | no | no | +120 / −20 |
| M2 | **`Session` and the contract move down.** `Session` type to `application/session.py`; the whole face a member types against (`NodeContext`, `RoundContext`, `CycleRead` as Protocols) into `optimizer_contract.py`; knobs to `optimizer_knobs.py`; the contract's four `TYPE_CHECKING` imports go. | `initialization/session.py` → `session.py`; `optimizer_contract.py`, `optimizer_knobs.py`; `bench/cycle.py`, `bench/node_context.py`, `config_map.py`; import lines in the 14 outside importers of the contract and every importer of `Session` | M1 | no | no | possible | +350 / −320 |
| M3 | **A fold lives beside its record.** `Line` and a scope-name enum to `domain/line_fold.py`; each fold class of `ledger_scan.py` to its `domain/records/<family>.py`; the money fold to `domain/money_fold.py`; the scope resolvers to `read_model.py`; `ledger_scan.py` and `llm/money_fold.py` deleted; `campaign_store/` flattened; `Projection` to `projections/`; `data_root.py`, the reuse cache and `heartbeat.py` re-homed. **(verify that every fold class is pure; a helper that takes a `cycle_dir` is a scope resolver and stays in the store)** | `store/campaign_store/*`, `store/read_model.py`, `store/io.py`, `store/archive_queries.py`, `store/account_spend.py`, `store/data_root.py`, `store/llm_reuse_cache.py`, `llm/money_fold.py`, `llm/heartbeat.py`, `run_state.py`, `projections/*`, `domain/records/*`, `domain/spend_rollup.py`; import lines in `runner/entry.py`, `scoring/passes.py`, `bench/line.py`, `jobs/mint.py`, `pipeline_resolve.py`, `run_observers.py`, `stores.py` | M2 | no (shapes untouched) | no | no | +2,000 / −2,500 (mostly moved) |
| M3b | **A domain file names one concept.** Pure splits: `sample.py` → `bank.py` + `archive_row.py`; `scoring.py` → + `scoring_spec.py`; `cells.py` → `cell_rows.py` + L4's types to `domain/l4/`; `spend.py` → + `money_text.py`. | those four files and their import lines | M3 | no | no | possible | +100 / −100 |
| M4 | **One round.** `Round` replaces three classes; `RoundRows` by composition; `run_standing`, `panel_precision`, `health`, `summary()` leave the statement; the election is stated once (F3 decides how). | `domain/results.py` → `round.py`, `records/rounds.py`, `run_standing.py`, `l4/proxies.py`, `dashboard_rows.py`, `export.py`; `projections/live_dashboard.py`; `scoring/closed_rounds.py`; `runner/round.py`; `bench/cycle.py`, `difficulty.py`; `views/readout.py`; the 24 readers of `run_standing`; `maintenance/landing.py` | M3b | **wipes** round lines (landing W1) | no | re-keys only if F3 is taken | +400 / −700 |
| M5 | **One pass.** One record family with a kind; catch-up deleted; an arm's state is filed at the decision that moves it. | `records/sample_passes.py` + `held_out.py` → `passes.py`, `pass_kinds.py`, `scoring/query_loop.py`, `runner/measure.py`, `projections/live_dashboard.py`, `views/readout.py` | M4 | **wipes** (W1) | no | **re-keys** (W1) | +250 / −550 |
| M6 | **One hold nest.** `Hold` names its parent and absorbs ceiling and share; the spawning pass names a nested hold's kind (decision 2), so `_ENCLOSED` goes; a fact is filed once on its sender and read through the fold. | `records/money.py`, `llm/spend_book.py`, `llm/send_pacing.py`, `jobs/shares.py` → `holds.py`, `jobs/quota.py`, `head_to_head_price.py`, `domain/money_fold.py`, `runner/entry.py` (hold/release calls) | M5 | **wipes** money records (W1) | no | no | +350 / −550 |
| M7 | **Which stop, and one declarer.** `domain/stop.py` holds `StopReason`, `StopLoop` and the reading; `run_stop.py` owns `end_run_on`, `budget_stop` and the declaring function; `Stores.declare_stop` goes; a self-pause carries its own words. | `domain/phases.py`, `domain/stop.py`, `run_stop.py`, `run_observers.py`, `run_phase_control.py`, `store/stores.py`, `jobs/launcher/opening.py`, `runner/entry.py`, `runner/loop.py`, `scoring/query_loop.py`, `optimizer_contract.py` | M6 | no (record shape kept) | no | no | +300 / −350 |
| M8 | **One resolution.** `resolve(subject)`; session binding to `initialization/wiring.py`; draft pricing to `jobs/holds.py`; served shapes to `pipeline_responses.py`; the eight dataset readers bound to rank A1. | `pipeline_resolve.py`, `pipeline_responses.py`, `config_map.py`, `records/run_wiring.py`, `jobs/holds.py`, `initialization/wiring.py`, `session.py`, `bench/line.py` (`Resolution(config…)`), `datasets/{authored,draft_campaign,startability,prompts,loaders}.py`, `store/dataset_access.py` | M7 | a field added to or dropped from `ResolvedRunRecord` rides W1: decide before W1 lands | no | no | +550 / −950 |
| M9 | **INIT is one package; a `Session` is always bound.** Mint, check-in's opening and the cycle's opening under `initialization/`; `LaunchRequest` to `domain/job.py`; probes and campaign refusals to `preflight.py`; `run_observers.py` deleted, its roster completed in `complete_registries`; `resume_and_fork/` flattened, the divergence check on the loop and the cut off it. | `jobs/mint.py`, `jobs/launcher/{mint_and_start,checkin,admission,launch}.py`, `runner/run_init.py`, `runner/entry.py`, `initialization/*`, `session.py`, `preflight.py`, `origin.py`, `domain/job.py`, `run_observers.py`, `bench/resume_and_fork/*`, `bench/export.py`, `maintenance/landing.py`, `commands/{launching,cycle_cleanup}.py`, `embedded_run.py`, `queued_launch.py`, `projections/projection.py` | M8 | no | no | possible | +1,500 / −2,200 |
| M10 | **One held-out pass.** Verify and bench are kinds of one pass in `bench/held_out.py`; `bench/line.py` keeps readings (one function of a line's spans); the verb that opens a diagnostic session is `commands/measuring.py`'s. | `scoring/passes.py`, `bench/line.py`, new `bench/held_out.py`, `domain/bench.py`, `runner/round.py` (`post_round`), `runner/entry.py`, `commands/measuring.py`, `inner/spawn.py` (`span.own`) | M9 | no (rides W1's pass records) | no | possible | +800 / −1,250 |
| M11 | **Round 0 is a round.** The origin is the population `initial_population` yields; the origin gate is a `hold_for_operator` act; the origin's bench pass is the pass every pick gets. | `runner/loop.py`, `runner/round.py`, `origin.py`, `bench/cycle.py`, `optimizer_manifest.py` (the plan for an empty population) | M10 | no if the records it writes are unchanged **(verify)**; else it joins W1 | no | **re-keys** (the origin's decisions move) | +80 / −250 |
| M12 | **The walk.** `query_loop.py` → `scoring/walk.py`; `measurement_log.py` split into the row order and a served view. | `scoring/query_loop.py`, `scoring/measurement_log.py`, `scoring/row_policy.py`, `runner/measure.py`, `views/` | M10 (∥ M11: disjoint files) | no | no | possible | +200 / −800 |
| M13 | **Manifest, identity and check-in manifest apart; the contract is the one module a plugin imports.** | `optimizer_manifest.py`, `optimizer_identity.py`, `optimizer_contract.py`, `bench/task_context.py`, `domain/treatment.py`, `examples/optimizer-plugin` | M11 | no | no | no (the key's inputs are unchanged) | +280 / −300 |
| M14 | **L4 is a door.** `runner/inner/` → `application/inner/`; `spawn.py` builds a `LaunchRequest(InnerSeat)` and takes the same INIT; the spawn is a field the run hands its session, not an ambient read; the transcript capability reads inner ledgers through the store. | `runner/inner/*`, `bench/node_context.py`, `initialization/{scoring_scope,mint,open_cycle}.py`, `connectors/promptpotter.py`, `infrastructure/run_scope.py` | M9, M13; fork F5 | no | no (`inner_campaign_id`'s inputs must not change, or name old → new) | possible | +150 / −350 |
| M15 | **A group of campaigns.** `Group` replaces `HeadToHeadRecord`; a head-to-head is a group whose members declare `Compare`; the group is a hold; its admission is held once. (§8) | `domain/campaign.py`, `jobs/launcher/head_to_head.py` → `initialization/group.py`, `jobs/holds.py`, `head_to_head_price.py`, `initialization/mint.py` (`mint_arms`), `commands/launching.py`, `domain/command_kinds.py`, `store/layout.py`, `data_root.py`, `evidence/head_to_head.py` | M9, M6; fork F2 | wipes `head_to_heads/` (landing W2) | no | no | +350 / −300 |
| M16 | **Adoption.** `AdoptionRecord`; an `adopt` command; `Connector.adopt` as a declared capability; `CandidateDelta` built from `loci()`. (§8) | `domain/records/adoption.py`, `domain/command_kinds.py`, `commands/adopting.py`, `connectors/protocol.py`, one connector, `domain/candidate_diff.py`, `domain/opt_search_point.py`, `bench/held_out.py` (the subset's pass) | M15, M10; forks F1, F4 | no (a new record) | no | no | +350 / −80 |

Landings that move identity: **W1** carries M4, M5 and M6 (built as three moves, landed together), plus any
record field M8 or M11 needs. **W2** carries M15. Re-keys fold into W1 and M11. L4 banks no paid result before
W1 and M11 land.

## 6. Forks

1. **F1 · What "Adopt" writes.** §0 says backends are read-only and the per-call overlay is the sole route for a
   tunable. Adoption needs the chosen values to reach what production calls. Either the loop stays read-only and
   adoption is an export the backend's owner applies, or a connector may declare an `adopt` capability and the
   command acks only after `served_pipeline` reads the new values back (decisions § 0001: `applied` means the
   effect happened). *Recommend the capability*, with export as what a connector without it gets; §0's
   "read-only" becomes "read-only except a declared adoption". Inside the same question: "Adopt selected" names a
   subset no pass measured. *Recommend* it is measured first, as an operator-authored arm through one held-out
   pass (free where the archive holds its cells), so "a tested upgrade" stays true. That pass spends on a click.
2. **F2 · Something above the campaign.** §0 names four entities and says a head-to-head is two campaigns.
   Decisions § 0003 says the money axes are the campaign's and a run is admitted whole or refused, never clamped.
   One budget over many targets needs a holder above the campaign. *Recommend `Group`*: today's
   `head_to_heads/{id}.json` widened, so a head-to-head is a group whose members compare and an upgrade event a
   group whose members optimize. To keep § 0003 whole, the group's budget is split into member ceilings when it
   is declared (equal by default, weights allowed), each member is admitted whole at its ceiling or reads "not
   run", and what a finished member left returns to the group for members not yet started. No member is clamped.
3. **F3 · When a round closes.** Today SELECT elects, then ADAPT runs, then the round closes. So the election is
   on the ledger twice (`ElectionRecord` and the close), `optimizer_state` is `None` in between, and an adapter
   that fails is re-run by the next round (`settle_owed_outputs`). The alternative closes the round at its
   election and makes ADAPT the first step of the next round's walk, which is already the recovery path; the
   last round runs no adapter today. *Recommend closing at the election*, in W1. The loop stays generate → score
   → critique; only the ledger boundary moves, and potter re-keys. If you say no, the close holds no election
   copy and the standing-rounds fold pairs each close with its round's last `ElectionRecord`.
4. **F4 · Where a new model becomes known.** The capability and price tables are code
   (`infrastructure/llm/registry.py`, `capabilities.py`, `pricing.py`), so "model C is out" is a release today.
   *Recommend* a host-edited catalogue file (a `KINDS` row, a survivor) laid over the code table, and the
   trigger a plain command any host sends (decisions § 0012). The engine ships no watcher.
5. **F5 · L4's machinery above the runner.** L4 is a kept layer; this reshapes inside it. `runner/inner/` mints
   and runs a campaign, and bench and INIT import it, so it sits on three ranks. *Recommend* `application/inner/`,
   the inner run entering as a `LaunchRequest(InnerSeat)`, the same path `embedded_run.py` takes.

## 7. Check against `docs/specs/code-debt-cleanup.md` (opened only after §1–6, §8, §9 were saved)

Rows counted at this tip by their `- **` leads: 01: 5 · 02: 9 · 03: 11 · 04: 14 · 05: 8 · 06: 11 · 07: 5 ·
08: 8 · 09: 6 · 10: 5 = **82**. Mapped to a move: **34**. Residual: **48** (three of them share a file, landing or
re-key with a move without being closed by it). 34 + 48 = 82.

A row counts as mapped only where its named file is in the move's file set and the move's end state passes the
row's Re-test. † marks a row placed from its title and lead line; its body was not read (02, 04, 06, 08, 10 and
a few others), so a † mapping is a claim to confirm when the move is briefed.

| § | Row (short) | Move or residual |
|---|---|---|
| 01 | data-root spec names dataclass fields | residual: a generator fix |
| 01 | the layering is fictional at runtime | M1 holds it; M2 cuts bench ↔ contract; M3 cuts store ↔ llm. **Not closed:** `stores` ↔ its parts, `pipeline_params` ↔ `pipeline_schema` |
| 01 | names collide across layers | M3 (`CycleFacts`), M3b (`cells.py`), M15 (the launcher's `head_to_head.py`); `hierarchy.py`, `power.py` stay residual |
| 01 | a closed round banks its election twice | M4, by either answer to F3 |
| 01 | what a child moved is computed twice | M16 |
| 02 † | the feed's version spans a whole store | residual: served-feed versioning |
| 02 † | Windows replace refused under a reader | residual: held by decision 8 |
| 02 † | a landing waits on every live producer | residual: a deploy step; W1 makes it rarer |
| 02 † | `domain/sample.py`'s name | M3b |
| 02 † | `write_json` indent | residual: decision 12, price first |
| 02 † | `list_campaigns` drops a manifest into a log | residual; shares M3's file |
| 02 † | a line's walks re-scanned per individual | M5 |
| 02 † | a sandbox's unparsed owner file | residual: local to the store |
| 02 † | the `Stores` hub holds a cycle's stop machine | M7 |
| 03 † | a resume's wait is unattributed | residual: needs the box timed |
| 03 † | a max-press resume replays on timing | residual: unreproduced |
| 03 | `BenchStatus.state` spans `read` | M10 |
| 03 | a block race files an `ArmRecord` per turn | M5 |
| 03 | a refused send's stop and a hole's stop share a field | M7 |
| 03 | a resume's check and cut are one off-loop function | M9 |
| 03 | an inner run's spawn is read off the ambient scope | M14 |
| 03 | a cycle's export skips a lost bank | M9 |
| 03 | `declare_line_bench` re-derives its scorer | M10, with M8 |
| 03 | an unbound session's address is two empty strings | M9 |
| 03 | a line's spent rounds at three sites | M10 |
| 04 † | a walk ended outside `land` names nothing it bought | M12 |
| 04 † | a freed slot idles `HELD_POLL_S` | residual: needs a wake channel this design does not add |
| 04 † | the first verify after a pause 38 s late | residual: needs a live verify |
| 04 † | the warm gate counts no se_δ | residual: a statistics method |
| 04 † | a connector's two-valued term reaches no scorer | M8 |
| 04 † | a constant paired side reads the `1/(4n)` floor | residual: decided method, unbuilt |
| 04 † | "which benchmark a row measures" twice | residual: held |
| 04 † | a hard-sample page resolves its own config | M12, with M8 |
| 04 † | an L4 panel averages θ over its cells' rulers | residual: L4 statistics |
| 04 † | `SampleIndex`'s `degraded` nobody reads | residual: `intelligence/` |
| 04 † | an estimator spec every caller fixes | residual: local to `paired_reading.py` |
| 04 † | held re-tests need world legs | residual: tests |
| 04 † | a freed slot waits on a poll | residual: as above |
| 04 † | `Election.deciding_pair` typed wider | M4 |
| 05 † | the default host pin is a trial | residual: paid twins |
| 05 | a nested spend book is wired beside its cell | M6 (`_ENCLOSED`), then M9 (`RunObservers.arm_spend_book`) |
| 05 | a landing's wipe refunds nobody | residual: operator's choice pending |
| 05 | dbllmbench's cells send on the box's key | residual: connector and the wallet's providers |
| 05 | a landed job's cycle is asked twice | M6 |
| 05 | a free account's refusal is a raw sentence | residual: words |
| 05 | a provider hold is filed twice under a nested run | M6 |
| 05 | an outer's overshoot may read its own ledger | M6 |
| 06 † | an arm's admission is held twice | M15 |
| 06 † | a check-in's cycle plan selects beside its refusal | M9 |
| 06 † | a fork cannot narrow a dead param axis | residual: decided 11, a capability |
| 06 † | a command's settle re-reads on a clock | residual: a poll |
| 06 † | a promoted launch's stderr is discarded | residual: local to `jobs/registry.py`, which no move reshapes |
| 06 † | a queued launch's ending files where an unasked one does | residual: same |
| 06 † | child-process facts idle the parent | residual: tests and CLI start |
| 06 † | a fork's preview does not serve its launch's refusal | M8, with M9 (`preflight.py` the one home) |
| 06 † | a refused launch reads Detached | M9 |
| 06 † | the run dock says a refusal as a send | residual: words |
| 06 † | a check-in offers Propose it would refuse | M9 |
| 07 | params are not one fold | M8 |
| 07 | a resolved dataset is resolved again | M8 |
| 07 | a knob's provenance is diffed | M8; its new mint field rides W1 |
| 07 | an origin finding dropped unserved | residual: `datasets/` |
| 07 † | the node document is typed two ways | residual: the config subsystem's own design, bound by M8 |
| 08 † | all eight rows | residual: connectors, TermNorm's branch, the box |
| 09 | a whole-candidate verdict books a placeholder axis | residual; its reshape shares W1 |
| 09 | the plugin contract is spread over seven modules | M2, finished by M13 |
| 09 | "wound" survives | residual: a rename; its re-key shares M11's |
| 09 † | CAPO's settled race path runs in no leg | residual: tests |
| 09 † | "slot" in LLM text | residual: paid comparison |
| 09 † | `l1_critique` on minimax | residual: paid proof, a prompt |
| 10 † | the paid L4 walk's stop | residual: paid |
| 10 † | the outer's edit menu | residual: decided 13, L4's own subsystem |
| 10 † | no L4 fixture buys ahead | residual: tests |
| 10 † | the estimator has no golden corpus | residual: tests |
| 10 † | an inner campaign's id is keyed by the instrument | M14 |

Residuals by reason: a paid, box, live or unreproduced measurement (8); a statistics method (3); connectors and
TermNorm (9); words on a surface (2); tests and the offline world (5); a wait on a poll (3); held or a pending
operator decision (6); a local root or another subsystem's own design (12). Sum 48.

**What the check changed in §0–§5, said here and not hidden.** After §7's first pass the ladder was measured
against the tip, and three first-draft claims fell:
- The first draft put the job wrapper back into `jobs/`, reversing db31dcb5c. `runner → jobs` is leftward, so the
  entry stays.
- It said a stop has four doors. The tip has one writer; the move is about who decides which stop, and smaller.
- It called M3 a pure move with `LineScope` in `domain`. `LineScope` reads ledgers, so the scope becomes a name.
It also added rank G, moved the held-out pass to rank A5, and split the one wipe into three moves on one landing.

**What the debt doc taught, not folded into the design:**
- The layering row records that moving `CycleRead` alone re-formed runtime cycles. M2 therefore moves the whole
  face a member types against.
- The same row names a knot this design does not cut: `stores` ↔ `session_pointer`, `account_spend`,
  `account_trail`, `archive_queries`, `dataset_access`, `connectors/protocol`. The ladder puts them on one rank,
  so it cannot see it. Each part needs a narrow protocol of what it reads, as the row says.

**Shape gaps this design closes that no row names:**
1. `application/` has no import order. Rows count type cycles; none states the ranks.
2. Folds live far from their records (`ledger_scan.py`).
3. A round has seven shapes; every statement stores `run_standing`; `panel_precision` puts an L4 proxy in the
   neutral envelope; `RoundStatement.summary()` builds a served row inside a record.
4. `jobs/` sits on two ranks: admission is low, `mint.py` and three launcher modules are high.
5. `pipeline_resolve.py` imports `jobs.quota`, the `Session` type and a served module; eight resolution shapes.
6. `scoring/passes.py` is ranked with scoring and imports bench, INIT and views; two executors of one pass.
7. `Session` has three lifecycle stages as `None` fields (one row names only the empty strings).
8. Round 0 has its own walk, against the origin-differs-in-values rule.
9. Six kinds of hold; nothing above a campaign can hold a budget.
10. `run_observers.py` is three concepts; `domain/phases.py`, `domain/spend.py`, `domain/scoring.py`,
    `optimizer_manifest.py` two or three each.
11. Five backbone files import `served_dashboard.py` or `views/readout.py`.
12. `application/datasets/` is imported by the backbone and imports it back.
13. `store/data_root.py`, `store/llm_reuse_cache.py` and `run_state.py` import `llm/` from below it.

## 8. The upgrade flow ("New model out? A tested upgrade waits.")

It is a path through the concepts above plus two additions, `Group` and adoption. It adds no second loop.

| What the page shows | Carried by |
|---|---|
| n8n 3 pipelines, Hermes 2 skills, your API 1 pipeline | each target is a **Dataset** definition bound to a connector endpoint and pipeline; a new backend is a connector file (§0 pipeline-agnostic). No backbone change. |
| the current config | the campaign's **origin**, read live through `pipeline_resolve.py::served_pipeline`. |
| "model C out" | one **param layer** the group lays on every member (`ParamLayers`): model C added to `param_allowed_values["model"]`. A model is already an axis opened per node. |
| re-tunes each pipeline | one **campaign** per target, `RunDeclaration.purpose = Optimize`, minted by INIT through the one admission seam. |
| within a budget you set | the **group hold** in the hold nest; each campaign's ceiling is a child hold. Only a run's spend book halts it. |
| Ledger · 12 Nov · 200 examples | the **Group** record (cause, date, members); 200 is the bench set each member's held-out pass grades. |
| +1 pt, −35 %, +6 % against the current config | **Run standing**: the declared pick against the origin on the bench set, one `MeasuredLift` per measurand. `Measurand` already carries the units `rate`, `usd` and `seconds`. |
| step 1 model B → C, step 4 threshold 0.82 → 0.78 | the **Individual**'s loci: the pick's `loci()` against the origin's. |
| no gain found · nothing to adopt | run standing whose pick is the origin. |
| Adopt all / Adopt selected / Skip | an `adopt` **command** through `CommandDispatcher`. Its effect is an `AdoptionRecord` on the member's ledger and, where the connector declares it, `Connector.adopt`. Pending is the absence of the record, so no state goes unreported. |

Changes to the tree: `domain/campaign.py::Group`; `domain/records/adoption.py`; `initialization/group.py`;
`jobs/holds.py`'s group level; `commands/adopting.py`; `connectors/protocol.py` (the capability). The served
ledger page is a `ReadKind` row folding the members' run standing, loci and adoption records; it is outside the
backbone. Moves: M15 and M16. Forks: F1, F2, F4.

Why the order matters for this flow. Without the hold nest (M6) the group budget would be a seventh kind of
hold. Without one resolution (M8) the group's layer would be laid in three resolvers. Without one held-out pass
(M10) "Adopt selected" would be a third executor.

## 9. Rust tie-breaker

Where two shapes were equal it chose: folds as pure `(state, record) → state` in `domain`; a scope as a name,
not a function; `RoundRows` by composition over a three-level inheritance chain; Protocols with no upward import
for the contract; one serialised shape per record; an explicit ladder. Concepts that would need reshaping are
marked in §1: the import-walk record registry, `StatePart`'s class-keyword registry, `RunScope`'s ContextVar,
capability-by-declaration on `NodeContext`, and registries completed by import in the landing. The design is not
grown for any of them.
