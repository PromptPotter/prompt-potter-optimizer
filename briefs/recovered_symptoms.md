# Recovered symptom lists from deleted chain reports (unreachable commits, 2026-11-27)

Each block is a report's Symptoms / Rows-to-file sections as written; re-test before trusting.

## 2026-11-27 · report: w8-tests3 (a7f0a41bf:report.md)

## Rows to file
None new. Cuts, each under its existing row:
- **Child-process facts idle the parent ~8 s**: the root is import time in `promptpotter/`, outside Owns.
- **A knob layer's tiers have no world example**: the door is `tests/world.py::World.campaign(knobs=)`, outside Owns.
- **An operator's act is atomic in the offline world**: stepping a command needs production step events.
- **test_security's stdio-host test is 78 lines**: the test is 39 lines. One test spawns a host, so a module-scoped host saves nothing. Close it as moot or keep it, the operator's choice.
- **One guard is tested once per door**: the row has no re-test or named guard. Many facts already table their doors (`..._at_every_door`).
## Symptoms
- `scripts/offline_run.py::decided`: the canonical labels of a bench reading move on a resume into the end pass (C0 reads as `parent@R1`). Suspected root: `Canon` labels ids by first ledger appearance, which the resume's init records reorder.

## 2026-11-27 · report: w7-rows5 (df4266a0d:report.md)

## Rows to file
## Symptoms

## 2026-11-27 · report: w7-rows2 (a0ad08733:report.md)

## Rows to file
- **The masthead's candidate count** stays open: the masthead's Candidates figure is the hierarchy's `RunStanding.candidates` (sum of `candidates_scored`), a second count beside `ServedDashboard.line`. Serving one on the dashboard means deleting or re-deriving the hierarchy figure, and `run_standing.py` uses it for lift per candidate. It needs a decision on which count is served.
- **`cycle_reads.py` copies `current_round.candidates`** stays open: `LineArm` lacks `prompts`, `resolved_params`, `validation_failures` and `evidence_grounding`, which `ServedArm` (the round view) carries. The line must carry `ServedArm` first.

## 2026-11-27 · report(w7-open): chain report (e4ebf04e0:report.md)

## Rows to file
- A fork's cut is opened beside its admission (`commands/launching.py::fork_cycle`), not through a request's `open`.
- The embedded fresh door probes its backend after its session opens.
## Symptoms
- walk-world failed twice in the full gate with `FileNotFoundError` under `.gate_cache/walk-world/<key>` while ts-types regenerated `built_key.py` mid-run. It passed once the key was committed.

## 2026-11-27 · docs: w9-fields report (fd4d6b168:report.md)

## Rows to file
None. Note: two ledger rows (about lines 549, 559) say "Rides with: root C2's chain", a root now closed.
## Symptoms

## 2026-11-27 · docs: w7-web2 report (675c02632:report.md)

## Rows to file
- **Read params are not generated**: `ActivityGroupBy` can only be typed off the response echo `ActivityResponse.group_by`. Generating each `ReadParams` model's query into `types.generated.ts` lets the echo go. **Re-test:** `git grep -n 'ActivityResponse\["group_by"\]' -- webapp/lib/api/types.ts` is empty.
- **Adapters over a flat served shape**, **The browser recomputes engine facts** and **Served fields the browser never reads** stay open (`p_served` 201). Most of the 201 are request payloads (`descend` ×10) and campaign-config echoes. The rest are banked fields: `RoundAudit`, `DatasetItem` and the fields `live_dashboard.py` writes, which belong to w7-rows2.
## Symptoms
- `webapp/lib/state/poll.tsx:91`: `isLive` re-derives `producer.appending` beside the served `round_axis.live`/`walk`. `HardSamplesPreview.tsx:26` gates `dash.walk`, which `_walk` already gates. Suspected root: the dashboard does not serve one liveness that covers a moment replay.

## 2026-11-27 · docs: w7-rows3 report (30cf0c11c:report.md)

## Rows to file
- **One test over the 2 s budget** (w7-rows3): the row's premise is false at base. `promptpotter.presentation.mcp.server` imports neither `promptpotter.main` nor fastapi (checked in `sys.modules`). Its ~1.25 s import is the `mcp` SDK (~0.42 s: `mcp.types`, `mcp.client`) plus the host door's own application closure (~0.7 s: `host_surface` → `read_params`, `commands.dispatcher`, `bench.task_context` → `bench.llm_call` at 0.45 s). `python -X importtime -m promptpotter mcp --help` stops at `cli.main` (0.47 s) and never reaches the server. The stdio test measured 2.05–2.44 s. Root: a lighter host-door closure (or a stated budget); lazy imports would be a bridge, so none was added. Re-test unchanged.
## Symptoms
- `promptpotter/presentation/mcp/server.py:50`: imports `CommandResult` from `cli.main`, which loads the CLI's whole verb table into the host door. Suspected root: `CommandResult` belongs below presentation.
- `scripts/gate.py` `--changed`: on a branch based on an unlanded `land/*`, the clean-tree base is `origin/main`, so it lists every wave commit, not only the last link.

## 2026-11-27 · docs: w7-rows report (7cdf1058e:report.md)

## Rows to file
## Symptoms
- `promptpotter/application/commands/operator_acts.py`: `set-coupon` takes `usd` and `tokens` as strings (`-` = unbounded) and parses them in the handler; a typed unbounded value in the payload would be the root.

## 2026-11-27 · docs: w7-gate2 report (357c73caa:report.md)

## Appendix: rows to file
- **Vitest's start meets load from outside the gate lock**: cut in the ledger to its Windows re-test.
- **One test over the 2 s budget**: rewritten in the ledger. The stdio MCP host test is 2.05 s; its child `promptpotter mcp` spends ~1.8 s importing. Door: the stdio server starts without the API's import closure (`promptpotter/`, outside Owns). Re-test: `pytest --durations=10` lists none over 2 s.
## Appendix: symptoms
- `tests/test_security.py` stdio host: child boot ~1.8 s; suspect `promptpotter mcp` imports `promptpotter.main`.

## 2026-11-27 · docs(report): w9-payer report (1acf4333b:report.md)

## Rows to file
- **dbllmbench's cells send on the box's key read off `settings`, not `sending_key`** (w9-payer): `connectors/dbllmbench.py:182,205` hand the container `settings.<KEY>`, so an account's own key never pays a dbllmbench cell, and `holds_own_sends=False` is what holds its payer at the box. No open row's root covers a connector reading a key outside the wallet door. Owner root: the wallet's payer rule (money). Door: the container takes `sending_key(provider)`'s secret, it keys its own sends, and `cells_paid_by` reads it as a library's. **Re-test:** `git grep -n "api_key_attr).get_secret_value\|ANTHROPIC_API_KEY.get_secret_value" -- promptpotter/connectors` is empty.
- **A campaign names its connector, and five readers look it up again** (w9-payer): `Campaign.connector` / `Environment.connector` are `str`, so `wiring.py:258`, `admission.py::campaign_sends`, `mint_and_start.py:123`, `mask/load.py:206` and `pipeline_resolve.py:1043` each call `CONNECTORS.get`. No open row covers it: the closed name-lookup row covered sites that already held the `Connector`. Owner root: the campaign domain (`domain/campaign.py`). **Re-test:** `git grep -c "CONNECTORS.get(.*campaign.connector)" -- promptpotter` is 1.
## Symptoms
- `infrastructure/backend.py::BackendClient.cells_paid_by`: it asks for the bound account even for a remote backend, which never reads the keys. It is harmless while every entry that sends binds one.

## 2026-11-27 · docs(report): w8-web2 report (043666b55:report.md)

## Appendix: Rows to file
Filed 0 / closed 0. No line is added under root E.
## Appendix: Symptoms
- `webapp/lib/derivations/candidate-search-point.ts::nodeConfigs` filters `steps` out of `resolved_params`.
  - The server could serve the arm's node overlay apart.
  - This goes under root E's **Adapters over a flat served shape**.
- `webapp/lib/derivations/node-config.ts::knobGroups` joins `OptimizerSetup.knobs` to `node_config_schema` by key. This goes under root E.
- `scripts/complexity_ledger.py` surface-ledger measures against origin/main, so a release/next base starts red. This repeats w8-web's finding.

## 2026-11-27 · docs(report): w8-web report (90f3020b9:report.md)

## Appendix: Rows to file
Filed 0 / closed 1.
One line under root E's existing row: "`cycle_reads.py` copies the live round's candidates: the closed line holds `DashboardCandidate`, so the line cannot carry `ServedArm` without the dashboard banking prompts; the door is the round read serving its unfiled arms from the fold's own method."
## Appendix: Symptoms
- `tests/test_security.py::test_a_task_held_at_its_arms_gate_asks_its_host_and_its_answer_acts_once` failed under the gate's parallel load in 2 of 3 runs and passed alone.
  - The failure: `queue/jobs/<id>.json … Invalid JSON: EOF`, a job file read while it is being written.
  - Suspected root: `JobRegistry._stamp`, a non-atomic write or an unlocked read. Under the store's `io.py` row.
- `gate.py --changed` reports `specs` red when its generator rewrites `docs/specs/openapi.generated.json` in the same run. It is green on rerun, which the gate itself calls a defect.
- `scripts/complexity_ledger.py` surface-ledger measures against origin/main. On a release/next base every chain starts red.

## 2026-11-27 · docs(report): w8-wallet report (d1ea42042:report.md)

## Rows to file
- **A launch on a library connector reserves the coupon whatever keys the account holds** (w8-box2, w8-wallet). New row, owner root: money. No open row covers who pays a cell.
  - `connectors/dspy_module.py` and `connectors/harbor.py` declare `Connector.cell_payer="host"`. Since w8-box2, their cells send on `wallet.py::sending_key`'s key, the account's own first.
  - Every reader of `cell_payer` still plans and holds a cell as the box's:
    - `jobs/quota.py::sends_on`: the launch's reservation and the tier cap.
    - `wallet.py::AccountWallet.refused`: "whatever keys this account holds".
    - `free_tier.py::free_fence`.
    - In-run: the cell hold in `backend.py::BackendClient._in_process_cell`, the set-aside in `runner/bench.py` and `runner/loop.py`, `runner/run_init.py`, and `fits`/`refuse_unless_room` in `scoring/query_loop.py`.
  - Fixing the launch alone regresses: a run that reserved nothing plans its cells against a zero reserve and halts `host_allowance` at its first cell.
  - Door:
    - The connector declares that its cells send on the provider their node names (`cell_payer: None`).
    - One resolver (`paying_key` over the nodes' `priced_as` providers) fills a session payer, and every in-run reader re-points to it.
    - `sends_on` is handed the target nodes' providers by its callers, read from the served declaration.
  - **Rides with:** money.
  - **Re-test:** an account holding its own openrouter key launches a DSPy cell (`max_calls` set) with no coupon reserved, and the cell runs.
## Symptoms
- `infrastructure/llm/wallet.py:245`: the `BackendCells` refusal says the cells go "on the box's keys whatever keys this account holds". That is false for dspy and harbor. Suspected root: the row above.

## 2026-11-27 · docs(report): w8-tests2 report (8e4f8d2c7:report.md)

## Rows to file
none new. The `offline_run.py` knobs belong under the existing "23 parked facts" and walk rows (owner: the offline_run.py chain).

## 2026-11-27 · docs(report): w8-store2 report, body under 400 words (81cb2e7bc:report.md)

## Rows to file
## Symptoms
- `promptpotter/infrastructure/store/measurement_archive.py:250,350`: `_CellFile.refresh` and `_live_rows` still key on `(mtime, size)`. A same-size rewrite in one tick goes unseen. Suspected root: the same as link 2, with `file_sig` as the validator. These lines were outside Owns (`cell_signatures` only), so they ride under the store `io.py` root.
- `promptpotter/infrastructure/store/io.py`: `long_form` is still applied at every open, but it is an identity for a path derived from the root. Paths that went through `resolve()` lose the prefix on Windows (`reset.py:87`, `producer_lock.py`). Suspected root: no `resolve()` of a data-root path. This rides under the `io.py` root.

## 2026-11-27 · docs(report): w8-store chain report (182299b22:report.md)

## Rows to file
## Symptoms
- `promptpotter/infrastructure/store/io.py::_STAMP_COMPACTS_AT`: the epoch still grows 2 bytes per 2048 writes (~1 KB per 1M writes). Suspected root: the epoch has no compaction of its own. Bounded in practice.
- `promptpotter/application/optimizer_manifest.py:95` and `promptpotter/infrastructure/store/measurement_archive.py::cell_signatures`: both read `st_mtime_ns` as a version. Two writes in one tick on Windows share it. Suspected root: a per-file version that only a writer moves (the stamp's channel).
- `promptpotter/infrastructure/store/io.py::_from_root` (lexical) and `io.py::_place` (realpath) are two ways to place a path under the data root.
- Windows only, not exercised here: the compaction's `msvcrt` lock on `.stamp-epoch` (reset to position 0 before the unlock), and `os.truncate` of `.stamp` while writers hold it open. Re-test on the desk with the hammer.

## 2026-11-27 · docs(report): w8-spine4 report within 400 words (d165a698b:report.md)

## Rows to file
None. The **Fields written, never read** row now reads 13 left and lists its members.
## Symptoms
- The brief's OWNS path `infrastructure/projections/ledger_scan.py` is dead. The file lives at `infrastructure/store/campaign_store/ledger_scan.py`.
- `domain/results.py::Election.deciding_pair` is typed `PairedReading | None`, but it is always a read pair. It could be `ReadPair | None`, which would let `scoring/selection.py` drop its assert. (w8-round's file.)
- `domain/bench.py::BenchStatus.state` spans `read`, so `runner/bench.py`'s unread branch has to assert `read` away.

## 2026-11-27 · docs(report): w8-spine3 report (8d4f7e5c4:report.md)

## Rows to file
- Under root C2's existing row (owner root: the records spine, C2): **`QueryLoopState.running` re-folds what the recorder already holds**. Its readers (`Narration.fold`, `ledger_scan.py::StandingArm.fold`, `live_dashboard.py:683`) need `metrics.py::compute_composite_fitness`, which reads `evaluators.py::materialize_round_values` (application). Root: a domain-side cell fold the readers can call, or the fold moved to where the readers are built. Re-test: `git grep -c "running=" -- promptpotter/application/scoring/query_loop.py` is 0.
## Symptoms
- `tests/test_security.py::test_a_task_held_at_its_arms_gate_asks_its_host_and_its_answer_acts_once` failed once under a gate run with `queue/jobs/<id>.json … Invalid JSON: EOF`, then passed 3/3 alone. Suspected root: a job file is read while `JobRegistry._stamp` writes it (a write that is not atomic, or a read outside the admission gate). The row goes under the job-file root.
- `**PairedReading` is a tagged union** and **Fields written, never read** were not taken. The first is held on `domain/paired_reading.py` (outside Owns). The second has two rows in Owns: `VerifySaturated.already_measured` is read by its sentence template (a false positive in `_unread_fields`), and `ErrorRecord.traceback` is kept for the operator.

## 2026-11-27 · docs(report): w8-spine2 chain report (565019f9f:report.md)

## Rows to file
- **Overlap and repair keep their own pass families**: `overlap_results`/`RoundRestatedRecord` are a second door beside the arm's recorded passes. Action: an overlap pass is a recorded pass of its arm. Blocked on `domain/run_standing.py` (w8-web) and the served `OverlapReading` contract. Re-test: `git grep -c overlap_results -- promptpotter` is 0.
- **`QueryLoopState.running` re-folds what the recorder already holds**: its readers sit in domain and infrastructure projections that hold no scorer. Re-test: `git grep -c "running=" -- promptpotter/application/scoring/query_loop.py` is 0.
## Symptoms
- `application/optimizers/potter/pobb.py:70` `race_sheet` builds sheets with `scorer_id=''`, so each reader re-grades them (`scoring/catch_up.py:75`). Suspected root: a race's priors should be filed under the session scorer.
- `potter/l4` reported requests UNMOVED in an earlier link-2 run, but MOVED (4 of 5) at both `6a28de2` and the trimmed tip. Suspected root: that earlier run predates a regeneration.

## 2026-11-27 · docs(report): w8-spine report (813476aa8:report.md)

## Appendix: rows to file
None. One line was added under C1's open half: "a catch-up walk records as `BACKFILL`, no arm".
## Appendix: symptoms
- `application/scoring/query_loop.py::caught_up`: a catch-up cell now reaches the ledger twice, as a `backfill` SampleScored line and in `RaceCatchUpRecord.cells`. Suspected root: C1's caught-up half. The walk should continue the prior's arm. It is blocked because the parent prior's arm is announced only after `measure_as_parent`.
- `bench/cycle.py::ArmWalk.continued`: the precedence row's root moves decisions (see link 4).

## 2026-11-27 · docs(report): w8-round report (07c1db6e6:report.md)

## Rows to file
None new. One cut (w8-fork's `entry.py`), already in the ledger as the open half of **"Rebased to fork" names no fork**: no stop names the fork the run continues on. The layer's rebase is cut after its stop is declared (`runner/entry.py::_run_single_cycle`), and `runner/entry.py::open_line`'s divergence stop passes no detail. Door: the cut comes before the stop, and the stop's detail names the child cycle.
## Symptoms
- `scripts/offline_run.py::ROUND_KEYS`: the trace picks a round's keys by hand, so a new row set was invisible to equals-main until it was added. Suspected root: the trace reads `RoundResult` fields off the model.
- `potter/election.py::_verdict_reason`: a held round's words are the match's fall-through, because `RoundDecision.ending` is typed `RoundEnding`, which admits `adopted`. Suspected root: a selector's ending type is the three it can decide.

## 2026-11-27 · docs(report): w8-resolve3 (fa56793fe:report.md)

## Rows to file
- **A connector's two-valued term reaches no scorer**: cut, and it stays in the ledger as written. Harbor's reward is two-valued only per task, so its shape is a panel's declaration. That puts it on `connectors/harbor.py::HarborPanel`, which is outside Owns ("the protocol" only), and no panel in the tree declares it. A protocol field with no producer would be a field no one writes. Owner root: the connector protocol plus harbor's panel, built together.
- The carrier row's open half was re-written in the ledger (filed 1): `measurement_log.py::_span` reads the archive address `node_configs` (`measurement_archive.py`, w8-store's), and `wounds.py` reads `RuntimeFailure.observed_config`, which `scoring/arm_states.py` writes (w8-spine2's). Re-test: `git grep -cE "cfg\.get\(\"(model|provider)\"" -- promptpotter` is 0.
## Symptoms
- `statistics.py::_normal_posterior`: the brief names it as a reach of the two-valued term, but it takes no declaration. That is the open row "The paired SE floor is its own rule", which needs an offline attribution.

## 2026-11-27 · docs(report): w8-resolve2 report (5fd0f1fe5:report.md)

## Rows to file (cuts; each row stays in the ledger as written)
- **A connector's name is looked up again where its `Connector` is held**: cut. `jobs/launcher/admission.py`, `checkin.py`, `head_to_head.py`, `mint_and_start.py` and `jobs/mint.py` are w8-launch2's. Owner root: after w8-launch2 lands.
- **A check-in's cycle plan selects beside its landing's refusal**: cut. `jobs/launcher/checkin.py::_land_draft` and `jobs/mint.py::resolve_cycle_plan` are w8-launch2's.
- **The node document is typed two ways and the L2 steer is a side memory**: cut. `CycleStart`/`ForkStart` live in `domain/records/` (w8-spine2's), and the row spans ~11 modules outside Owns.
- **The pipeline carrier is typed only at its door**: cut. `domain/pipeline_parsing.py`, `pipeline_schema.py`, `free_tier.py` and `wounds.py` are outside Owns.
## Symptoms
- `scripts/complexity_ledger.py` / gate `surface-ledger`: `release/next` is measured against main's merge-base, so every w8 chain inherits the +90 red. Suspected root: the gate should measure against the chain's base.
- Link 2's compile needs the campaign's owners (connector terms, report keys, observation keys), but `pipeline_resolve.py::Resolution` holds only a `CampaignConfig`. Since `FiledAnswer.grading` now feeds `open_cell`'s compile, the owners must ride the grading or the refusal must happen before filing. Root: decide where the owners live before building row 2a.

## 2026-11-27 · docs(report): w8-resolve chain report (d0b2d5a8a:report.md)

## Rows to file
- **The bank cannot carry its dataset's scoring** (w8-resolve census; amends **`open_cell` resolves a whole dataset to compile its scorer**): a bank is keyed by its rows alone. `tenant_dataset_store.py::put_bank` keeps the first write of equal rows, and `relabel` / `cuts.jsonl` share one bank across names. A scoring block on `domain/sample.py::Bank` would grade a cell under its bank's first writer, neither as filed nor as today. Its persist is `tenant_dataset_store.py` (outside w8-resolve's Owns). Compiling also needs `judges` beside `scoring`. Root: the answer carries the scoring it was graded under (`FiledAnswer`), or the read names its dataset. Run data: a wipe of the archive or banks. **Re-test:** `open_cell` resolves no dataset.
- **A per-cell criterion naming a term no cell carries compiles**: cut. Its root is `scoring/formula/compiler.py::compile_scorer` plus the connector's owners on `Resolution`, outside Owns, and it moves digests.
- **A connector's two-valued term reaches no scorer**: cut. It needs the connector protocol to declare its observation shapes (`connectors/`) and `matchers.py::fitness_scale_of`, outside Owns.
## Symptoms
- `read_params.py::DatasetCellParams`: once `open_cell` needs no dataset, the route's `{name}` goes unread. Suspected root: the browser root (re-address the read as `/answers/{answer}`).

## 2026-11-27 · docs(report): w8-launch4 chain report (c70eeb107:report.md)

## Rows to file
- `quota.py::sends_on` takes a connector name where the caller holds the `Connector`. This is w8-wallet's file.
- Row 2: a check-in's plan beside its landing's refusal. It needs the wiring `_session` owner.
- Row 3: a queued launch on disk.
## Symptoms
- A mint's job now names its hop before its cycle exists. If a sibling admitted on that campaign during the mint window runs `shares.py::cycle_share`, it raises "no share landed there". This is unreachable today, since a fresh campaign id is unknown before its manifest, but nothing enforces it.
filed 3 / closed 1

## 2026-11-27 · docs(report): w8-launch3 report (9fdcf7f8f:report.md)

## Appendix: rows to file (each row stands in the ledger as written)
1. **A connector's name is looked up again where its `Connector` is held** (cut). `free_tier.py::free_fence`, `pipeline_resolve.py::laid_config` and `UploadBasis.connector` are w8-resolve3's. If only the jobs half is built, `.name` comes back at `FreshMint.campaign_config`'s `laid_config` call and at `sends_on`. Owner root: after w8-resolve3 lands.
2. **A check-in's cycle plan selects beside its landing's refusal** (cut). The Held precondition, a `Session` over a draft-held `ResolvedDataset`, needs `loaders.py::resolve_bank`/`build_panel`, `dataset_access.py::dataset_experiment` and `tabular_ingest.py::read_answer_terms_file` to read a definition that is not on disk. All of these are outside Owns.
3. **A fork's share lands at its landing** (cut). The in-run cuts are `runner/entry.py::_cut_rebase_fork` and `bench/resume_and_fork/resume.py`'s divergence cut, through `fork_siblings.py::cut_fork` (w8-resolve3's). For the share to ride the cut, those must cut under the gate, as `ForkLaunch.stand` does. The census found no wrong number: `_rebase_held` reads the reshared parent share under the gate. A leg should prove the window before anyone builds this.
4. **A resume's wait is unattributed** (box). Offline, a detached resume opens the full `_session` twice: `ExistingCycle.open` runs in the admitting process (`launch.py::hold`) and again in `run_job.py::run_job`. So every network edge (`served_pipeline`, `_read_backend_revision`, `ensure_model_capabilities`, `session_bank`) is paid twice. Dropping the admitting open moves a drift refusal into the child, where the job cannot report it until w8-job's "a job carries how its run ended" lands. Measure on the box first.
5. **A mint takes no claim until `opening.py::claim_cycle`** stays open. Its landing half (`landing.py:365`) is w8-job's. The mint half needs `LaunchRequest.open` to take the `Opening` (the job id), across every kind and `run_job.py`.
## Appendix: symptoms
- `promptpotter/application/jobs/launcher/mint_and_start.py:212` `ExistingCycle.open` binds the cycle once per process; see row 4.

## 2026-11-27 · docs(report): w8-l4 chain report (2dc0726fa:report.md)

## Appendix: rows to file
None new. One line for the existing row **A pressed L4 outer run does not repeat**: Root: `scripts/offline_run.py::_asker` names an inner campaign by `determinism.seed` plus overlay, not the seat's `run_seed`, so seats on one arm share a turn counter. Under a press, call numbering follows arrival. Keying by `run_seed` moves `estimator`. Re-test: two pressed runs of a ≥2-search-seat L4 leg read the same inner requests.
## Appendix: symptoms
- `tests/test_security.py::test_a_task_held_at_its_arms_gate_asks_its_host_and_its_answer_acts_once`: red once in a `--changed` run, green on the rerun. Suspected root: a poll-timed wait (the open row on poll constants).
- `scripts/complexity_ledger.py::BASELINE` `package_lines` = 113788, the base's exact count. Any chain growing by one line is red until another chain deletes.

## 2026-11-27 · docs(report): w8-job (d9814f471:report.md)

## Rows to file (one line each under existing rows)
- Under **A queued launch dies with the asking process**: the refusal of a launch released before any cycle is filed at `jobs/refused/` (w8-job) because no command id reaches `JobRegistry.release`. Once the queued intent carries its command, it files on the command log and the `launch refusal` Kind goes.
- Under **Run data is read bare outside `store/io.py`**: w8-job cut this row. The long form moves every derived path's spelling on Windows, and there was no Windows run to prove it.
## Symptoms
- `domain/cycle_result.py:58`: `CycleResult.stepped` has no reader in code now (the stop record's `STEP` cause holds it). It is still served on the events schema. Suspected root: a field kept for a reader that moved.

## 2026-11-27 · docs(report): w8-gate chain report (fcf0a540c:report.md)

## Rows to file
## Symptoms
- `promptpotter/application/embedded_run.py:65` imports the landing inside a function, against `conventions.md`'s module-scope rule. The suspected root is the landing's import cost; that file belongs to w8-launch.
- `scripts/gate.py::_ts_types` and `_proven_in` both stage the key: one in the index's own tree, one copying back from its checkout. That split follows `--staged`'s two modes. If they were one mode, it would be one stage.

## 2026-11-27 · docs(report): w8-fork report (9aac8dce7:report.md)

## Rows to file
- **The default host pin is a trial with no verdict** (stays open; cut for w8-fork). Its holders sit outside `pipeline_overlay.py`: `infrastructure/llm/registry.py::default_routes`, `bench/llm_call.py::_merged_config` and the `pipeline_resolve.py` injection. One line under the existing row: "No model has a verdict yet, so under the decision none keeps its pin. Closing it deletes `pin_default_routes`, `pinned_route`, `PipelineSchema.default_routes` and the profiles' `default_route`. That re-keys every routed cell's archive address (a wipe) and needs the twin readouts first."
- **A fork cannot narrow a dead param axis** (stays open): one line under it: "Decided (w8-fork): fork-only. The member moves potter's behaviour key, so it lands with a re-key."
## Symptoms
- `run_init.py:376`: `resume_with_divergence_check` runs whole off the loop, only because its cut takes the gate. Suspected root: the check and the cut are one function. The run-init owner could split the decision (on the loop) from the cut (off it).

## 2026-11-27 · docs(report): w8-doors (7748abe3a:report.md)

## Rows to file
None new. **No door yields a recorded capability menu or a dataset with a panel** stays open and now names its door: the offline OpenRouter catalogue moved between a world campaign and its resume, with the snapshot refreshed; and a dbllmbench dataset on `world.opened(connector=…)`.
## Symptoms
- `promptpotter/domain/command_kinds.py:999`: `BankSnippetPayload.experiment` has the pattern `^[A-Za-z0-9_-]+$`, so it refuses every campaign minted from a family dataset (`justlogic.d234__…` holds a dot). An edit banked in such a campaign cannot name it. Suspected root: the payload re-spells an id pattern instead of reading the campaign id's own grammar. The file is w8-launch's hub, so this goes as one line under that chain's command-kinds row.
- **Smells the test-shape split left** is still open: the budget-cut table's call takes 2.41 s, over the 2 s budget. Its fix is in the run's write path, outside the test world.

## 2026-11-27 · docs(report): w8-box2 (c6337a0e3:report.md)

## Rows to file
- **A launch on a library connector reserves the coupon whatever keys the account holds** (owner root: `application/jobs/quota.py::sends_on`). `sends_on` declares a dspy or harbor launch's cells as `BackendCells(connector, cell_payer="host")`, and `wallet.py::AccountWallet.refused` words them as "go out on the box's keys whatever keys this account holds". Since w8-box2, the cells' LLM sends go out on `wallet.py::sending_key`'s key: the account's own key where it holds one. So a launch whose account holds its own key is still held against, or refused on, the coupon. Root: a connector whose cells send on a node's provider (`holds_own_sends`) declares its sends as that provider's `ProviderSend`, never a constant `cell_payer`. No open row covers launch-time payer declaration. Re-test: an account holding its own openai key launches a dspy campaign past a used-up coupon.
## Symptoms
- `promptpotter/infrastructure/llm/wallet.py:249`: the `BackendCells` refusal sentence is stale for dspy and harbor (root above).

## 2026-11-27 · docs(report): w8-box (6e621e102:report.md)

## Rows to file
None. Cut: **A resume's wait is unattributed** stays in the ledger as written. Its re-test needs the box, and its suspected holders (`runner/run_init.py`, `initialization/wiring.py`) are outside these Owns.
## Symptoms
- `deploy-linux/update.sh` `as_unit` and `install-service.sh` each build their own environment lines (`PROMPTPOTTER_HOME`, now `OPENBLAS_NUM_THREADS` only on the unit). The unit's environment has no shared block the way `hardening.sh::unit_hardening` shares hardening, so the two can drift. Suspected root: `hardening.sh` should own the units' environment too.
- A third-party library that reads keys from `os.environ` (harbor's agents, litellm) loses whatever only the env file held now that no unit exports it. promptpotter's own reads all go through `Settings`. Check `harbor` cells on the box after the env move.

## 2026-11-27 · docs(report): w7-web (cc41aa629:report.md)

## Rows to file
- **A hand-kept mirror of the served shapes** stays open. Generating the fixtures needs `scripts/offline_world.py`, which belongs to tests-w, not this chain, so it is cut here.
- **`MeteredSpend` is banked, so its words cannot be served**: `run_records.py`'s stop record holds `StopSpend.spent: MeteredSpend`, so `spend.ts::spendHeadline`, `meteredBucketsLine`, `replayShareLine` and `ratePricedText` stay browser composers. Root: the stop record banks a served shape; it should bank the `SpendRollup` facts and serve `MeteredSpend` per read. **Run data:** a wipe. **Re-test:** `git grep -n "export function" -- webapp/lib/derivations/spend.ts` names no composer.
- **Adapters over a flat served shape**, **The browser recomputes engine facts** and **Served fields the browser never reads** stay open. `surface-ledger`'s `p_served` already counts the served properties no browser or CLI reads, so the last row's gate check comes down to driving that baseline to 0.
## Symptoms
- `promptpotter/main.py:313`: no door reads `HealthResponse.service`; only `deploy-linux/deploy.config.example:106` names it.
- `promptpotter/application/served_dashboard.py:94`: no reader reads the discriminator `WarmingDashboard.warming_up` by name.

## 2026-11-27 · docs(report): w7-ver2 (339ad0ca2:report.md)

## Rows to file
- **A reset leaves an emptied sandbox standing on its stamp** (suspected from reading, not run; `reset.py` is outside Owns). `maintenance/reset.py:197-202` prunes the empty directories, then `root.rmdir()` under `suppress(OSError)`. The sandbox root still holds its `.stamp` (MACHINE, never dropped), so the rmdir fails in silence and `stores.py::inner_sandbox_store` still finds the sandbox. Door: a sandbox reset drops the stamps under its root with it. **Re-test:** after a reset of a tenant with a sandbox, `.inner/<key>` is gone.
## Symptoms
- `scripts/gate.py:1963`: `(REPO / path.format(key=key)).touch()` raised `FileNotFoundError` for `.gate_cache/walk-world/<key>/potter/data` on a first full run. The cache directory is not made before the touch; the check was green on rerun.
- `scripts/gate.py --changed` with a clean tree runs 0 checks, so it proves nothing for a committed link.

## 2026-11-27 · docs(report): w7-ver (0d2872139:report.md)

## Rows to file
- **An authored dataset definition has no writer**: an install or research definition edit moves no stamp, so reads that depend on it stand until the store's next write or a restart. Action: the definition loader stamps its tier root. **Re-test:** edit an install definition; the cells ETag moves.
Still open, already in the ledger: **The account feed never re-asks an exhibit's reads when the host's store moves** (the feed carries no host subject); the ETag half of **A follower reads with the host's whole `Stores`**; **The lineage subtree sorts every candidate per candidate**.
## Symptoms
- `uv` 0.11.32 is installed but the repo pins 0.11.21, so the gate refused to run; I installed 0.11.21 under `/tmp/uvpin`.
- The first gate run left `openapi.generated.json` stale; it was regenerated and is green on rerun.

## 2026-11-27 · docs(report): w7-tests3 report within 400 words (e0a3f36e0:report.md)

## Rows to file
## Symptoms
- `research/datasets/promptpotter-self/family.yaml:152`, symptom: `panel.inner_campaign.optimization.nodes` lays potter's nodes over every seat, so a CAPO seat cannot sit in any `promptpotter-self` panel. Suspected root: a panel's geometry has no per-optimizer layer. Unverified whether this refuses or misreads.
- `config/paths.py::research_datasets_root` returns `Path | None`, so `l4_panel`'s facts run only in a checkout.

## 2026-11-27 · docs(report): w7-tests3 final report (a03e07d2d:report.md)

## Rows to file
## Symptoms
- `research/datasets/promptpotter-self/family.yaml:152`, symptom: `panel.inner_campaign.optimization.nodes` lays potter's nodes over every seat. So a CAPO seat cannot sit in any `promptpotter-self` panel, and a mixed panel had to drop those nodes. Suspected root: a panel's geometry has no per-optimizer layer. Unverified whether it refuses or misreads.
- `promptpotter/config/paths.py::research_datasets_root` returns `Path | None`. `l4_panel` asserts the checkout's tier. A wheel has none, so the facts are checkout-only by construction.

## 2026-11-27 · docs(report): w7-tests2 (93dfda81a:report.md)

## Rows to file
None beyond the open half written into the row.
## Symptoms
- `promptpotter/infrastructure/store/io.py:313`: each send's charge `fsync`s `charges.jsonl` (504 fsyncs, 0.31 s of the table's 3 s). Suspected root: a durable append per send, where it could be one per hold.
- `tests/test_spend.py` (a hub): this chain only removed `offline` parameters from facts there.

## 2026-11-27 · docs(report): w7-tests chain report (e90761c09:report.md)

## Rows to file
- (filed in the replay row) `seat`'s three sites have no offline door. `_gepa_seat` needs two prompt nodes, and the offline TermNorm serves only `llm_only`. `_view` and `_proposed` need an L4 outer whose run init walks whole inner campaigns. Action: the offline world serves a declared node graph, and an L4 outer opens with its inner cells stood in.
- **Smells the test-shape split left** is still open on both halves: a spend book binds an account without the offline world, and the bench facts share one module-scoped round.
## Symptoms
- `tests/test_scoring.py:4590`: the budget-cut table opens one campaign per case (3.4 s). Suspected root: a shared module-scoped opened round, which is the Smells row.

## 2026-11-27 · docs(report): w7-spend report (2da4993d7:report.md)

## Rows to file
## Symptoms
- `scripts/gate.py` `walk-world`: in the first full-gate run it failed with `FileNotFoundError` on `.gate_cache/walk-world/<key>/potter/data`, in the same run where `ts-types` regenerated a stale `built_key.py`. It was green alone on rerun. Suspected root: `walk-world`'s cache key and `ts-types`' rewrite of an engine source race inside one gate run.

## 2026-11-27 · docs(report): w7-rows6 chain report (e4a5030f9:report.md)

## Rows to file
- **A dataset read is a third resolution** (w7-res3; confirmed by w7-rows6, design pick open): `pipeline_resolve.py::resolve_pipeline_for_dataset` repeats `RunResolution`'s parse (`parse_pipeline_response` of a served declaration, as `PipelineRead.declared_schema` does) and its narrowing (`_resolve_active_schema` with the config's `exclude_nodes` and `optimizer_narrowing`, no menu). Root: it is a `RunResolution` with no read and no cycle. **Design pick:** `RunResolution` requires `recorded`, `cycle` and `verifier_graded`, and its `schema` is computed through `layers`, which reads `read`. Three choices are open. Either a schema-only base (config, declared, menu) that `RunResolution` extends, or optional fields. Second, what `verifier_graded` means for a read with no bank. Third, whether the dataset read keeps the dataset file's own config (`load_dataset_campaign_config`) or the laid one that `merge_outside_run` uses. **Re-test:** one schema parse in `pipeline_resolve.py`.
## Symptoms

## 2026-11-27 · docs(report): w7-rows4 (677acd99a:report.md)

## Rows to file
- `gate.py --changed` prints its "tree clean" line twice per run (the selection is built twice). Closing it means finding the second `Selection.build` caller.
- A subject root is now a path shape: a jobs dir not named `jobs` stamps nothing, silently (a test names its jobs dir `feed_jobs` and no assertion caught it). The root would be a typed jobs-dir argument that refuses a wrong name.
## Symptoms
- `tests/test_security.py` `test_clearing_the_active_pointer_moves_the_accounts_feed_version`: its `JobRegistry(tmp_path / "feed_jobs")` is outside the jobs-dir shape; the test passes without the machine stamp.

## 2026-11-27 · docs(report): w7-res3 chain report (2c15785b3:report.md)

## Rows to file
None new. **`open_cell` resolves a whole dataset to compile its scorer** stays open, blocked on the three choices above.
## Symptoms
- `pipeline_resolve.py::resolve_pipeline_for_dataset`: it parses and narrows a schema of its own beside `RunResolution`. Suspected root: a dataset read is a third resolution (no read, no cycle).
- `pipeline_resolve.py::_cycle_mint`, `evidence/head_to_head.py:570`, `evidence/read.py:824`, `runner/bench.py:880`, `scoring/closed_rounds.py:53`, `scoring/passes.py:244`: each reads `last(hop, PartitionRecord)` and tolerates `None` beside `drawn_partition`. Suspected root: a tolerant draw read is undeclared.
- `tests/factories.py::loop_session`: it hand-seats a `Session` and its resolution (against the tests charter's no-hand-seating rule). Its five callers want `opened_round`.

## 2026-11-27 · docs(report): w7-res2 report (29df45c60:report.md)

## Appendix: Rows to file
- (cut, 1 of 3) **`open_cell` resolves a whole dataset to compile its scorer** stays in the ledger unchanged. Its root, an archive that carries its dataset's config, is in `measurement_archive.py` / the tenant dataset store. Neither is in my Owns (the rest of `measurement_log.py` is w7-ver2's).
## Appendix: Symptoms
- `initialization/session.py::Session.pipeline_schema`: it holds the read's parse before `bind_resolution` and the chain as run after. Suspected root: a `Session` holds its read, and the resolution owns the schema.
- `bench/resume_and_fork/fork_siblings.py:193`: a third reader of a cycle's draw raises its own `ConflictError`. Suspected root: it should call `drawn_partition`.
- `pipeline_resolve.py::_Merge.optimizer`, `RunResolution.optimizer` and `DraftPipeline.optimizer` each spell `select_optimizer(self.config.optimization)`. Suspected root: a draft's merge is no `RunResolution`.

## 2026-11-27 · docs(report): w7-res chain report (be73f74c5:report.md)

## Appendix: Rows to file
None cut. The open half of root B stays in the ledger, re-tested by `git grep -c "def configure_and_apply_pipeline" -- promptpotter` reaching 0.
## Appendix: Symptoms
- `pipeline_resolve.py::resolved` raises `FileUnreadableError` and `session.py::Session.bound` raises `ConflictError` for the same state: a cycle with no `PartitionRecord`. Suspected root: one reader of a cycle's draw.
- `measurement_log.py::open_cell` resolves the dataset only to compile its scorer and to refuse an unknown name. Suspected root: the archive's own dataset config.
- `startability.py:86`, `preflight.py:109` and `draft_patch.py` each spell `all_verifier_graded(s.ground_truth for s in samples)` over a sample list, beside `Bank.verifier_graded`. Suspected root: a draft's rows are not a `Bank`.

## 2026-11-27 · docs(report): w7-open3 chain report (15c0a475d:report.md)

## Appendix: Rows to file
## Appendix: Symptoms
- `promptpotter/application/bench/resume_and_fork/fork_siblings.py:171` — `ForkCut.withdraw` swallows every exception, so a stub that fails to delete is reported nowhere. Suspected root: the withdraw has no channel to report the stub it kept.
- `promptpotter/application/jobs/launcher/opening.py` `__exit__` — on a raised fork exit it stamps a stop on the child, then withdraws (deletes) the child: the write is wasted. Kept as the base did, so that a withdraw that fails still leaves a stamped stub.

## 2026-11-27 · docs(report): w7-open2 (8dd58f956:report.md)

## Rows to file
## Symptoms
- `jobs/launcher/mint_and_start.py::ForkLaunch.open`: a failure after the hold's open but before the fork's run takes the cut (`opening.land`) is not withdrawn there. `launching.py::_held_fork` withdraws only failures after `held()`. Suspected root: the cut's withdrawal belongs to the opening's exit once its job is released.
- An admitted fork that has not been cut yet holds no share on any cycle, so a sibling admitted in that window reads the campaign without it, as a mint's does.
- `gate.py` walk-world vs ts-types regenerating `built_key.py` mid-run: the same as w7-open's report.

## 2026-11-27 · docs(report): w7-gate chain report (3b29b3b97:report.md)

## Rows to file
- **Vitest's start still meets load from outside the gate lock** (cut): the start is in `webapp/vitest.config.ts` / `webapp/package.json` (`"test": "vitest run"` takes no gate lock), outside w7-gate's Owns, and its re-test is the Windows desk. Root: a builder's `npm test` queues behind the clone's lock as the gate does. **Re-test:** five looping builders plus one gate on the Windows desk reap none.
- **The gate meters no check's CPU**: `processes.py::close_job` reads only the peak, so `Check.slots` is measured by hand (`time gate.py --only NAME`). Root: the job (Windows `JobObjectBasicAccountingInformation`) and `os.wait4` (POSIX) report CPU-s beside the peak, printed per check. **Re-test:** the gate's table prints each check's CPU-s ÷ wall.
- **`walk-world` runs in CI's `check` job, where nothing reads it**: it is a `py` check (`gate.py::_PINNED_KINDS` needs one for `--web` to re-exec), so `--py` runs it beside the `webapp` job's copy. Root: whether a check re-execs is whether it runs the engine, not its kind, so `walk-world` runs only where playwright does. **Re-test:** `gate.py --py` lists no `walk-world`.
- **Tests over `tests/CLAUDE.md` § Budget per test (2 s)**, from `pytest --durations=40` at the base:
  - `test_security.py::test_the_api_starts_under_the_loop_guard_with_its_held_producer_lock`: 2.64 s. It starts the real lifespan in a child process. No other test walks it.
  - `test_security.py::test_an_edit_to_code_a_load_runs_lands_the_root_again`: 2.56 s. It re-derives `derived_landing_key()` over the whole loaded code, as `ts-types` already does once per gate.
  - `test_security.py::test_a_host_over_stdio_acts_in_the_terminals_account_as_the_in_process_door_does`: 2.36 s. It runs a stdio MCP host process. No other test walks it.
  - These fixture setups are over 2 s but already shared: `conftest.py::dspy_child` 3.42 s (2 facts), `test_scoring.py::head_to_head` 3.40 s (3), `test_identity.py::steered_line` 3.12 s (7), `test_archive.py::line` 2.49 s (20).
## Symptoms
- `scripts/gate.py::_schedule`: vitest is timed with `started=lambda: False`, so it always runs alone at the end (about 10 s idle on 3 cores). Suspected root: Vitest's 90 s worker start (the row above).

## 2026-11-27 · docs(report): trim to 400 words (a6bf3017c:report.md)

## Rows to file
- `quota.py::sends_on` takes a connector name where the caller holds the `Connector`. This is w8-wallet's file.
- Row 2: a check-in's plan beside its landing's refusal. It needs the wiring `_session` owner.
- Row 3: a queued launch on disk.
## Symptoms
- A mint's job now names its hop before its cycle exists. If a sibling admitted on that campaign during the mint window runs `shares.py::cycle_share`, it raises "no share landed there". Unreachable today (a fresh id is unknown before its manifest), but unenforced.
filed 3 / closed 1

## 2026-11-27 · docs(report): fix a stray backtick (502283368:report.md)

## Rows to file
- Under root C2's existing row (owner root: the records spine, C2): **`QueryLoopState.running` re-folds what the recorder already holds**. Its readers (`Narration.fold`, `ledger_scan.py::StandingArm.fold`, `live_dashboard.py:683`) need `metrics.py::compute_composite_fitness`, which reads `evaluators.py::materialize_round_values` (application). Root: a domain-side cell fold the readers can call, or the fold moved to where the readers are built. Re-test: `git grep -c "running=" -- promptpotter/application/scoring/query_loop.py` is 0.
## Symptoms
- `tests/test_security.py::test_a_task_held_at_its_arms_gate_asks_its_host_and_its_answer_acts_once` failed once under a gate run with `queue/jobs/<id>.json … Invalid JSON: EOF`, then passed 3/3 alone. Suspected root: a job file is read while `JobRegistry._stamp` writes it (a write that is not atomic, or a read outside the admission gate). The row goes under the job-file root.
- **`PairedReading` is a tagged union** and **Fields written, never read** were not taken. The first is held on `domain/paired_reading.py` (outside Owns). The second has two rows in Owns: `VerifySaturated.already_measured` is read by its sentence template (a false positive in `_unread_fields`), and `ErrorRecord.traceback` is kept for the operator.

## 2026-11-26 · report: tw-c (9dfbb9066:report.md)

## Rows to file
- test_scoring `# Journeys awaiting a leg`: 128, 101, 96, 82 and 72 lines. These need an offline-run leg; no pytest stand-in.
- test_spend `test_a_resumed_arm_rereads…`, 111 lines, belongs to tw-j2.
- test_archive charged-parent test, 78 lines: one journey, no door seats a hand-minted parent. `by_its_place` (62) keeps a hand mint.
- test_security `refused_poll`, 62 lines.
- test_security stdio-host: three facts now spawn three hosts at ~4 s each. Door lacked: one spawned host shared across facts.
- `tests/factories.py`: builders with zero callers are left for the owning chain to delete; count not tallied.
## Symptoms
- A cold first world run costs ~4 s, which exceeds the 2 s budget per fact.
- The Bash hook refuses shell reads of tests/*.py, which slowed scripted edits.

## 2026-11-26 · docs: w7-small report (cc61858ea:report.md)

## Rows to file
- **A wiped campaign answers two failure kinds**: not reproduced at base (404 on every shape tried: no `at`, `campaign:`, `cycle:`, `candidate:`, `;in=` hops). If the 400 recurs, capture the exact `at` the stale tab sent.
- **A dead producer's lock vs its docker containers**: `docker_host.reap_dead_producers` finds a dead producer's containers by its lock; the sweep (and `registry.reap_orphans` already) now unlinks the lock first, so containers of a producer that died while the server lives are never reaped. Root: containers labelled with a token should be reaped by whoever removes the token.
## Symptoms
- `tests/test_security.py` imports `settings` twice at module level (line ~166 and ~502).
- Running `gate.py` rewrites `maintenance/built_key.py` on every code change; a dirty file after a gate run.

## 2026-11-26 · docs(report): w7-text chain report (12e9e3643:report.md)

## Rows to file
**`ReviewReading.feedback` has no reader.** `potter/l1.py::review_reading` fills it, but the critique already reaches the readout as the round block's Critique note. Deleting the field and its l1.py line moves capo-pobb's behaviour key (an edit to a line its straight run never ran), so the deletion waits for a planned re-key. **Re-test:** `git grep -n "feedback=\[" -- promptpotter/application/optimizers/potter/l1.py` is empty.
## Symptoms
- `infrastructure/store/layout.py:192` `optimized_surface` (optimized.md) is a markdown file the decision did not name. Suspected root: the same question as root G.
- `application/views/readout.py` `_reviewed` and `cycle_hard_samples` each fold `closed_rounds` at a stop, and the review folds again at every close. Suspected root: the projection has no round fold of its own to keep.

## 2026-11-26 · docs(report): w7-line chain report (8596ba5c4:report.md)

## Rows to file
- **The masthead's candidate count names no count**: `components/shell/masthead` (outside Owns) still recounts. It should read the served line and say which count it shows: arms scored over the line vs C0, or C0 plus every candidate. Cut: this needs a served count beside `ServedDashboard.line`.
- **A Candidates card draws an empty chart over a served lineage** (kept): the bars now come off `dash.line` keyed by address. This was not re-tested against a real campaign; an address the subtree does not list still draws nothing.
## Symptoms
- `promptpotter/application/cycle_reads.py:136` copies `current_round.candidates` whole into a second shape (outside Owns).
- `promptpotter/domain/phases.py:291`: `start_refusal` and `ServedDashboard.successor` word the same fact twice. The root is a `Said` on `RunState`.

## 2026-11-26 · docs(report): w7-host, the host's bulletins (f2f028bd7:report.md)

## Appendix: Rows to file (code-debt-cleanup.md)
- `infrastructure/identity/accounts.py::is_host` is a one-line wrapper over `identity.host`. Deleting it is blocked because `docs/specs/roadmap.md` cites it, and that file is off-limits to a chain.
- No code deletes an account, so the rule "an account's deletion drops its dismissals in the same write" has no writer to ride. It lands with the account deleter: `bulletin_board.py` drops `dismissed[account_id]`.
- `identity/accounts.py::claim_default` renames the terminal's `default` account to the host id. Dismissals filed as `default` before the claim are left orphaned, and the host sees those bulletins again.
## Appendix: Symptoms
- The CLI `bulletins` read does not mark which bulletins pop up; only the webapp shows that.
- The bot's other admin commands call `application.account` directly, outside the dispatcher (§ 0004). `/bulletin` is the first that dispatches a `CommandKind`.

## 2026-11-26 · docs(report): tw-x, the world's doors and every hand-seat onto them (7e1f2ac1a:report.md)

## Rows to file (code-debt-cleanup.md)
1. **offline_world: OpenRouter lists no Groq host.** Every candidate record pins `route_order ('Groq',)`, so a steered fork in the offline world is unpriced under a dollar cap. Tests work around it either by clearing `route_order` in the fork's `pipeline_overlay` or by listing a "Groq" endpoint. Root: `scripts/offline_world.py` serves the host the world's LLM names.
2. **offline USAGE carries no cost.** The world prices every cell at 0, so spend ratios, capped arms and the positive θ quote (test_scoring) have no world door. `evidence/_ratios` and `_read_alike` are private.
3. **No in-process connector world door.** test_spend's ungraded-cell fact stays on `loop_session`.
4. **`hierarchy_subtree` resolution rules are not exposed.** A candidate's line, retirement and placement could be tables in place of the family-tree world run (~11 s). The tree shapes the world never produces are untested: inner hangs (re-read, OVERLAP, unrun, live), supersede-of-supersede, a restated round, tied rival cuts.
5. **`fork_siblings.py:131-139`, the never-closed-origin edge of `fork_edge`, has no door.** `cut_operator_fork` refuses an unmeasured origin. This also leaves test_identity's sibling stop-record on `mint_cycle`.
6. **`apply_correction` is unreachable by any live walk, and `_earlier` is not exposed.** The archive repair and child-hang tests stay hand-seated.
7. **No door yields a non-empty recorded capability menu or a world dataset with a panel.** Two `write_resolved_run` seats stay in test_identity.
8. **A replay that moves no file is reached only by a race.** It needs a `run_one` knob for two walks on one uncached cell.
9. **A module-scoped shared run's stores live under the shared home.** `inner_sandbox_store` refuses a fact's own `PROMPTPOTTER_HOME`, so the family fixture re-points the env itself. `World` could hand out a reader scoped to a `Shared`.
10. **The knob-layer fact reads only "campaign"-level layers.** The dataset vs free_tier distinction has no world example and needs a `campaign(knobs=)` layer per tier.
## Symptoms
- **`line.made` changed shape** to `(hop, scorer)` partway through. Three merged facts read it as a hop; this was fixed at merge.
- **`scripts/gate.py` buffers all output until it exits**, so a long run cannot be watched.
---

## 2026-11-26 · docs(report): tw-w report (2658dde6f:report.md)

## Rows to file
- Capture of a valid answer records only content; an invalid one now records the body minus usage: the capture shape differs by case (`offline_run.py` `shown`).
- Pytest wall/RSS "before" not cleanly measured at base; successor should run `gate.py --only pytest` at `/tmp/base`.
## Symptoms

## 2026-11-26 · docs(report): tw-v — the suite's wall, link by link, and its gap (ebd4a1301:report.md)

## Appendix: Rows to file
- **Child-process facts idle the parent ~8 s** (tw-v): `conftest.py::dspy_child` (2.9 s), `test_security.py::test_the_api_starts_under_the_loop_guard_with_its_held_producer_lock` (2.7 s) and the stdio host (2.4 s) run serially while the parent waits. Each child peaks at 160–165 MB, so overlapping them breaks pytest's 393 MB job cap. Most of each child's cost is importing `promptpotter.main` or the CLI (~1.6 s). Door: a lighter import graph for the API and CLI boot. **Re-test:** the child walls fall under 1.5 s each.
- **Archive's world runs cost ~7.5 s once a session** (tw-v): `line` 2.5, `origin_forks` 1.6, `family_tree` 1.6, `displaced` 1.6 and `gated` 0.2 s, against 3.3 s for the whole file hand-seated at `7dce00a96`. No shape merges, because each run's forks are distinct. Door: a cheaper run, not fewer runs. **Re-test:** archive's file wall is under 8 s.
- **`import_closure` re-normalizes 329 modules per landing key** (tw-v): `shared/import_walk.py::import_closure` calls `normalized_tree` on its own parse rather than `hashing.py::normalized_module`'s memo, so it costs 2.8 s for the one fact (`test_an_edit_to_code_a_load_runs_lands_the_root_again`) and ~3.4 s for every `build_ts_types.py` run. **Re-test:** a second `derived_landing_key()` in one process takes under 0.2 s.
- **fsync is ~2.5 s of real time a suite** (tw-v): 4235 atomic writes (`io.py::_synced`); `live_dashboard.py::_write` rewrites `dashboard.json` 1048 times. Durability is kept; whether the dashboard checkout needs fsync on every write is a decision to price. **Re-test:** a decision with its reason.
## Appendix: Symptoms
- `tests/test_security.py:3189`: on a never-run tree, a first landing leaves `jobs/.live-jobs` unwritten and a second landing stages it, so the `moved` fixture keeps `campaign(rounds=0)` rather than a mint. Suspected root: the landing's live-jobs index row is written only once a run has started.
- `tests/test_security.py:508` (BASE-V): the API-start child fetched upstream's real price list over the network ("refreshed 4961 model rates"), because `refused_network` does not reach a child process. Fixed in `d2fce21`: the child installs the offline world and asserts nothing went unrouted. Suspected root: no child-process door for the offline world beyond a copied preamble.
- `tw-j2` row: the per-fact adopt costs ~25 ms (census C4), not ~0.5 s. The per-fact time sits in the doors (fork, resume, campaign) and in production frames.

## 2026-11-26 · docs(report): tw-r3 final report (b3a58c1ab:report.md)

## Rows to file
- **A walk ended outside `land` names nothing it bought**: `query_loop.py::_ScoringPhase.discard` (a cancel) and a crash write no `UntakenBoughtRecord`. A pause is not affected: the resume takes every cell it dropped (tw-r3 probe). Root, if a red comes: name the cell at its filing in `query_loop.py::_acquire`. **Re-test:** a cancelled pressed walk's untaken cells are left out of its campaign's `SampleIndex`. It needs a pytest; tw-r3 owns no tests.
## Symptoms
- `scripts/offline_run.py::moved_against`: a leg the base home lacks reads `no base`, not compared.

## 2026-11-26 · docs(report): tw-q report body within 400 words (9a038cc16:report.md)

## Rows to file
- **A round close re-resolves the config it already holds:** `RunArtifacts._render` → `cycle_hard_samples` re-runs `cycle_config` and `campaign_scorer` per close, about 0.4 s across the suite. The fix is to pass the resolved config down from the round.
- **`to_job_search_point` re-resolves a resolved config:** `PipelineParams.with_nodes` takes 2.29 s over 8609 calls across the suite. The fix is one resolution per point, at its owner.
## Symptoms
- `promptpotter/infrastructure/store/io.py` (`write_json`): `indent=2` forces the pure-Python encoder on every dashboard write. Suspected root: the format, not the writer.
- `tests/test_archive.py`: 23.5 s against a 2 s-per-fact budget's intent. Suspected root: five shared runs where fewer shapes would carry its facts (tw-w owns `tests/world.py`).

## 2026-11-26 · docs(report): tw-p final report (367bae135:report.md)

## Rows to file
- **`bench/round_analysis.py::mended_round`, `records/run_records.py::ForkGradedRecord` and `measurement_context.py::MeasurementRole.REPAIR` have no producer**.
  - The defect: their one writer was `resume_and_fork/repair.py::apply_correction`, deleted as dead by tw-p link 1. A 400 cuts the arm and a 500 heals or ends the run, so no closed round holds a repairable hole on a leader-eligible arm.
  - Action: delete them with their readers (`ledger_scan.py`'s `ForkGradedRecord` case, `pass_kinds.py`'s REPAIR row), or give a hole a real producer.
  - **Rides with:** none yet. **Held:** scoring and records belong to tw-j.
  - **Re-test:** `git grep -n "mended_round(\|ForkGradedRecord(" -- promptpotter` names only definitions.
- **A home's rates are held past its home**.
  - The defect: `infrastructure/llm/pricing.py::load_rates` keys `read_model.py::derived(("rates", home))` per home and holds one `RateTable` per home whose listed rows are non-empty, even when they equal the bundled floor. `read_model.py::_DERIVED` (an LRU of `DERIVED_MAX = 4096`) never evicts entries whose path is gone, whereas `_registered` ledger indexes are evicted. In the suite this is ~10 MB, plus the pydantic models it pins (39 MB), across test_archive's homes.
  - Action: an entry keyed by a path is evicted when that path is gone, as `_registered` already does. A home's rates equal to the floor read as the floor.
  - **Rides with:** none. **Held:** `infrastructure/` is outside tw-p's Owns.
  - **Re-test:** `tracemalloc` over test_archive still lists `pricing.py:165/200` among its top growth.
## Symptoms
- `domain/cell_fold.py:52` `repairable_holes` counts every errored non-HALTED cell, including a charged error that a re-walk pays again. Suspected root: `ErrorCategory` does not say which errors are fillable.
- `infrastructure/store/read_model.py:857` `derived` keeps entries for deleted paths. Root: the eviction above.

## 2026-11-26 · docs(report): tw-j2 final report (2f8c68451:report.md)

## Rows to file
- **A resumed walk re-buys a charged errored cell**: `infrastructure/store/measurement_archive.py::ReplayFeed.advance` skips `cell.errored`, and a pass stopped before a pause is filed twice on resume. Action: an errored cell is replayed as the error it was. **Re-test:** `scripts/offline_run.py --charged` crossed-resumed `harness.backend_cells` equals crossed's.
- **No leg's L2 carries a steer**: `potter/records.py::L2Memory.steered_onto` (`potter/members.py:442`) runs in no leg and no test. Door: the world's L2 reply names a `steer`. **Re-test:** offline `--coverage` reaches `records.py:80`.
- **Doors the parked facts lack.** Each fact is kept until its door exists:
  | fact | door it lacks |
  |---|---|
  | identity I2 | a two-prompt-node dataset |
  | I3, I4 | a parent on fewer rows than its frontier |
  | I6 | an L4 origin past 16k chars |
  | mixed ×2 | a mixed potter + capo L4 leg |
  | scoring S3, S6, S13 | a leg binding a budget (backend cells cost $0) |
  | S8, S14, S15 | a bench-pass pause |
  | S9–S12 | a leg setting `origin_replicates` |
  | S16–S18 | a gate leg running `every_adoption` |
  | S19 | an `arm_gate=author` leg |
  | S20 | a GEPA round proposing more than one arm |
  | S25 | a world LLM naming `stand_on` |
  | spend: skip journey | a `run_one` act dispatching `SkipSearchpoint` mid-walk, then a resumed twin |
  | spend: inner-stop | an L4 leg whose inner run rebases and whose provider refuses credit |
  | 1222 catch-up | a pause while a prior's catch-up is out past the sure rows |
  | 1324 + 1349 priced replays | a Compare leg paused mid-walk and resumed (`search_incurred`) |
- **Hand-seats with no world door.** The world has no mint-only door, and `world.opened(rows)` takes no connector.
  | file | seats |
  |---|---|
  | test_security | `minted_campaign` ×6, `stored_campaign` ×3, `loop_session` ×1 |
  | test_scoring | `loop_session` ×2 (lines 1047 and 4242), `minted_campaign` ×2 (lines 1825 and 1986) |
  | test_spend | `loop_session` ×2: line 768 is tw-x row 3; line 930 is the kept skip journey |
- **The stdio-host test is 78 lines.** Door: a host spawned once per module.
- **The cold first run costs 3.27 s at the base.** It falls on `line`'s setup, which takes ~2.3 s warm. Door: a session warm-up pays the cold start, not a fact.
**Factories builders left with zero callers:** none.
## Symptoms
- `b11c492` and `3279e33`: the subject has no blank line after it, so the bullets run into it. They were not rewritten, because force-push is not allowed. Root: the message was passed with `-m` without `\n\n`.
- `3279e33` and `e50b6c0` are standalone fix commits, against "one commit per link". Root: each link's `--changed` gate ran after the push instead of before it.
- `tests/test_archive.py`: about 0.5 s a fact in adopt and the doors, against 4.1 s for the whole file at 7dce00a96. Suspected root: tw-q's landing-key build and the world's per-adopt opening (tw-q § Symptoms).

## 2026-11-26 · docs(report): tw-j's links, proofs at the tip and rows left (d04d56f40:report.md)

## Rows to file
- **archive J1** (resumed informative race): no leg pauses before an informative choice is made. Under the mutation (a per-process call counter shifting θ in `send_rows`), every potter leg stayed green. A pause-depth dimension on `run_one` would close it.
- **archive J2** (charged parent error replay): needs a world edge where the parent's cell errors.
- **identity**: 8 journeys. They read hand-seated views; each needs a leg assertion over captured requests.
- **scoring**: 26 journeys, plus `_peer_cycle`, `_bench_cycle`, `_closed_origin` and `_pause_mid_pass`.
- **spend**: 1365 (spend ceiling), 1409 (skip holds), 1448 (inner stop; patches `execute_round`), sp:1567 (needs a priced world). 1374 is a rule and stays.
## Symptoms
- **Pressed L4 outer runs don't repeat.** Two pressed runs differ in the outer `L1Critique/Generate_0,_1` requests and in the inner decisions, while unpressed runs repeat. Concurrent inner campaigns depend on timing.
- **Bank filing is dead code wherever a claim files the cell.** With `Walk.bank`'s `recorder.bank` disabled (mutation M1), every leg and all of pytest stay green.
- **Test 1365 guards a path I did not find.** It stays green with `affordable` set to maxsize, and also with the hold's refusal disabled.
- **A rename on a run line re-keys potter.** Renaming `_guard_evidence` to a public name re-keyed potter and capo-pobb. potter/CLAUDE.md says a rename on a run line re-keys nothing. I reverted the rename, and the test imports the private name.

## 2026-11-25 · docs(tw-s): census report for test_spend (921ab30d3:report.md)

## Rows to file
1. **The real opening lays no `ScorerSetup` before `open_launch`.** Because of that, the measure_sample edges sp:704, sp:2549 and sp:2588 stay on `loop_session`. The world needs a "cell measured through the door" hook (tw-x).
2. **One world campaign per fact.** In-suite, test_spend went from 8.0 s to 17.7 s, at about 0.5 s per world run. A module-scoped world fixture (tw-x's `conftest.py`) would share it.
3. **Two dspy children.** `test_spend::dspy_billed` and `test_connectors::dspy_cells` build the same student and engine. Fold them into one child in the dspy connector's file (a cross-chain edit, so not done here).

## 2026-11-25 · docs(tw-3): report for the test_identity rebuild (89dc13a17:report.md)

## Appendix: Rows to file
- **tests: shipped-data scans in test_identity.** `test_…shipped_tiers_disjoint` (:1474) and the framing-budget scan (:4306) are repo-wide scans. Each should become a check at import in the registry that owns it (`application/CLAUDE.md` § Registries complete at import), and then the test is deleted.
- **tests: hand-seated campaigns left in test_identity.** These have no production door to fork or to mint inside a cycle that a test can reach:
  - the `tiered` fixture and :1351 (`minted_campaign` + `stored_campaign`)
  - the `steered` fixture (`mint_cycle` + `write_resolved_run`)
  - :2371 (`ScoringLockedRecord` appends)
  - :2598 (knob layers)
  - :3347's inner door (`stored_campaign`)
  The fix is tw-0's world module exposing a fork door and an inner-mint door.
- **tests: `factories.loop_session` builds a Session the real opening refuses.** An LLM node with no owned model passes there, but `init_services` refuses it ("dataset owns its task model"). The builder hides that refusal from every caller. Move the callers to the real opening, then delete it.
## Appendix: Symptoms
- `read_model.derived` held one 2.2k-row rate table per data root, bounded only by `DERIVED_MAX` = 4096. Fixed in dd072fe.
- The first `init_services` in a process costs about 2.5 s of landing warm-up. Whichever test opens first pays it, which makes per-test times move between files.

## 2026-11-25 · docs(tw-1): report for the test_scoring census (1067fc080:report.md)

## Rows to file
- `code-debt-cleanup.md`: "`bench/node_context.py::closed_round` takes a whole `Cycle` but reads only `difficulty`, `config.optimization.lives` and `session.bank.position_of`; narrow it to those three so the lives stop is replayable without a cycle."
- `code-debt-cleanup.md`: "`tests/test_scoring.py::_evidence_campaign` hand-seats a ledger through `close_round` + `seat` for the three head-to-head doors; re-point it onto tw-0's world or a controlled leg, then delete `seat`/`close_round`."
## Symptoms
- `_evidence_campaign` was a clean-seam stop: it needs tw-0's world module, which I do not own.
- `wip/tw-1` carries a malformed commit subject (see Links).

## 2026-11-25 · docs(report): web-tail report and before/after pane shots (24bc42329:report.md)

## Rows to file
- **A build stamp names HEAD while the build reads the worktree** (web-tail's report): `webapp/vite.config.ts` stamps `git rev-parse HEAD:./` and `application/health.py::webapp_bundle` compares `HEAD:webapp`, so a dirty-tree build is stamped as HEAD's and a commit after a build turns a byte-identical page "out of date". web-tail keyed `vite-build` on `HEAD:webapp` (`scripts/gate.py::Check.stamps_head`). Root: the stamp names the bytes it built (a worktree tree hash) on both sides. **Re-test:** `stamps_head` is deleted and a build-then-commit walk stays green.
- **A focus ring is re-skinned outside `base.css`** (web-tail's report): `shell.css:64` `.sidebar-filter-search:focus-visible{outline:none;border-color:…}` and `compare.css`'s `.cmp-expr-bad{border-color}` state a field's look beside the skin. Root: the focus and invalid states of a field skin in `base.css` (`:focus-visible`, `[aria-invalid=true]`). **Re-test:** no `outline` or `border-color` on a field class outside `foundation/base.css`.
## Symptoms
- `tests/conftest.py:83`, `tests/test_security.py:4399`, `webapp/e2e/serve_app.py`: three spell `Router(FakeLLM(lambda _body: "ok"), FakeBackend([]))`; suspected root: `offline_world.py` owns the idle world's constructor.
- `webapp/e2e/fake_issuer.ts`: the auth-on world's server is spawned with `--backend=offline` and now runs fully in-process too; nothing changed for it, noted for its owner.

## 2026-11-25 · docs(report): web-land report and shots (fd272dc96:report.md)

## Rows to file
- **A pane's data read in flight reports nothing**: `components/chat/HardSamplesPreview.tsx` (and every
  `useRead` consumer but `AccountSpend` / `PipelineFlow`) draws its rows when the read lands but sets no
  `aria-busy` while it is in flight. The walk had to count fetches itself (`e2e/harness.ts::drawn`).
  Root: the read's `loading` state marks its pane busy where `useRead` is consumed, one rule in `lib/`.
  **Re-test:** `drawn` keeps only its `aria-busy` check and `landscape-844 › every view` stays red 3/3 at base.
## Symptoms
- `webapp/app/styles/domains/shell.css` (landscape block): at `.main` scrollTop 0 the chat composer sits
  below the bars. A drag inside the pane scrolls the pane first and reaches the masthead only at the pane's
  end (nested scrollers). Suspected root: the masthead lives outside the pane's scroller, so no CSS-only
  layout gives it a single scroll.
- Playwright fires no `requestfinished` / `requestfailed` for a lazy chunk served twice, or for a fetch the
  old document sent after `reload` began (`e2e/harness.ts::open`'s goto + reload). That is why `drawn` counts in the page.

## 2026-11-25 · docs(report): w1-rebase, under 400 words (7911931a5:report.md)

## Rows to file
## Symptoms
- `scripts/complexity_ledger.py` `config_leaf_fields`: measures 100 against a 98 ceiling at both
  `3a1226a51` and `ab49e6559` (100/100 at main and the tip); the land/w1 gate measured it against
  its own merge-base, so the stale ceiling there never showed.

## 2026-11-25 · docs(report): tw-r2 final report (53edd6711:report.md)

## Rows to file
- **A walk ended outside `land` names nothing it bought**: `query_loop.py::_ScoringPhase.discard` (the cancel path) and a crash write no `UntakenBoughtRecord`, so those cells stay the running campaign's memory. Root: the record is written where every ending lands. **Re-test:** a cancelled pressed walk's untaken cells are left out of its campaign's `SampleIndex`.
## Symptoms
- `query_loop.py::Walk.bank` → `_ArchiveRecorder.bank`. A claimed fresh cell, already filed at arrival, is filed a second time under a new ref when a pause banks it. Suspected root: `bank` re-files what `take` already filed.

## 2026-11-25 · docs(report): tw-r report (913152184:report.md)

## Rows to file
- `_walked` lists every campaign's spans on each population read. Cost is unmeasured on a large tenant.
- The pause act (`pausing_in_walk`) perturbs arrival timing.
## Symptoms
- Base: mypy-clean, but surface-ledger sat at its ceiling.
- Link 3 shipped a mypy red (non-hashable `sig`), closed in link 4.

## 2026-11-25 · docs(report): tw-m final report (502f9a575:report.md)

## Rows to file
- **landing_key is recomputed by every process at entry.** `maintenance/landing.py::landing_key` costs +20.5 MB committed (3 MB live) and 2.6 s on each process that lands, an API's lifespan among them.
- **Queue and task-result waits re-read on a clock.** These are `application/jobs/registry.py::_QUEUE_POLL_S` and `host_surface.py::_SETTLE_POLL_S`. Each wait ends on a write another process makes, and no signal in this process carries it. Tests patch both to 0 (`test_security.py::_held_at_arms_gate` and the reaped-queued test). Root: a cross-process wake on the jobs dir or ledger write (a lock handoff or a file watch).
- **test_identity misses an import.** At `tests/test_identity.py:4533`, `resolved_key` is undefined, which turns ruff, mypy and pytest red on `land/test-world`.

## 2026-11-25 · docs(report): tw-2 test_security three-tier rebuild report (efbbe5e02:report.md)

## Rows to file
- **test_security tests tw-0's gate.** `test_a_webapp_gate_runs_the_engine_checks…`, `test_the_walk_world_check_under_no_key…` and `test_a_declared_write_is_what_orders_two_gate_checks` (tests/test_security.py:4565–4588) test `scripts/gate.py`. They move to the file owning the gate.
- **App lifespan state outlives shutdown.** `promptpotter/main.py:126` `lifespan` leaves about 23 MB held in-process after `TestClient(app)` exits. Release what lifespan opened, or own it per app instance.
- **Launcher detached tasks are a process global.** `promptpotter/application/jobs/launcher/launch.py:27` `_DETACHED_TASKS`: tests cancel it by hand (tests/test_security.py:4308). Key it to the app or loop that launched it.
- **Poll intervals are module constants patched to 0.** `registry.py:53` `_QUEUE_POLL_S` and `host_surface.py:553` `_SETTLE_POLL_S`. Wake on the event that ends the wait.
## Symptoms
- tests/test_security.py:530: the API lifespan child takes 5.4 s (it was 1.0 s in-process). Suspected root: importing everything cold; see the lifespan row.
- tests/test_security.py:855: the first in-process `TestClient(app)` costs +24.5 MB. Suspected root: app import and route mounting.
- tests/test_security.py:4871: the MCP door over HTTP costs +7.1 MB. Suspected root: SDK session managers.

## 2026-11-25 · docs(report): trim the l4-key-b report (fcb4c5dba:report.md)

## Rows to file
## Symptoms
- Fallout outside Owns: `node_context.py`, `read_params.py`, `search_point_scorer.py`, CLI `writes.py`/`main.py`, the webapp archive control, `domain/cells.py`, `measurement_context.py`, `embedded_run.py`, `offline_run.py`.
- `axes:` panels: untested, unused.

## 2026-11-25 · docs(report): spend-tomb report (26de16b0e:report.md)

## Rows to file
- **A sandbox delete names its owner twice** (spend-tomb's report): `jobs/reaper.py::reclaim_orphan_sandboxes` (outside Owns; one call line edited, as fallout of the signature) and `campaign_store/store.py::delete_campaign` each read `read_sandbox_owner` and build the owner hop before calling `delete_inner_sandbox`. Root: `delete_inner_sandbox` reads its own owner file. **Re-test:** one `read_sandbox_owner` per sandbox delete.
- **A cycle's running job is found two ways** (spend-tomb's report): `served_dashboard.py::spend_counts` takes this cycle's `hold_id` from its caller (`run_scope().book` in `run_observers.py`, `holder(hop).run` in `armed_caps`) and reads the siblings' through `_job_holding`. Root: one reader of a cycle's live job. **Re-test:** `spend_counts` takes no `hold_id`.
- **The composite step has no band beside the fitness difference** (spend-tomb's report): `VerifyReading.composite_increment` is a bare float, while `fitness_difference` is an `Estimate`. **Re-test:** one shape for both columns' fresh − recorded.
## Symptoms
- `promptpotter/application/served_dashboard.py` `spend_counts`: builds a full `SpendBook` per cycle just to call `count()`. Suspected root: `SpendBook` has no count-only constructor; quota.py and admission.py build similar zero-budget books.

## 2026-11-25 · docs(report): potter-w1 report (6cbfd6222:report.md)

## Rows to file
- **`l1.py::rejoining` reads a population that is always the parent alone**: since potter-w1 a held round hands on its parent only, so `rejoining` is always empty. It is read by `members.py`'s `opening` (`+ len(rejoining(...))`) and by `L1Generate.arms`'s `+1` ("the leader a held round keeps"). Action: delete all three. members.py is ml-5's. **Re-test:** `git grep -c rejoining -- promptpotter` is 0.
- **`PoBBStop` carries rivals by value that its record names by key**: `pobb.py::PoBBStop.priors` holds `CellSheet`s, and `race.py::PoBBRace.judge` reduces them to keys. Root: `PoBBCheck.check` (ml-5's) builds `cells: dict[ArmKey, list[str]]` directly. **Re-test:** `PoBBStop` has no `priors`.
## Symptoms
- `docs/developer/node-standard.md:73`: names Selection's `population`, but the field is `nodes.py::Selection.ranked`. Suspected root: a doc left behind by a rename.
- `promptpotter/application/optimizers/potter/members.py:257`: the comment cites the keep that link 1 deleted. Root: the first row above.

## 2026-11-25 · docs(report): ml-fork report (2d5d81ea5:report.md)

## Rows to file
- **A series caveat unions every round's cells.** `bench/difficulty.py::level_series` stamps `LevelSeries.caveat` from `theta_caveat` over every cell the line read. A withholding state in one round's cells (say `collapsed_band`) therefore withholds every level of the line, and `SeriesReading` falls back to it whenever a level's own caveat is null.
- **A fork proposed with `rebase_capability` off is dropped unsaid.** The reply schemas carry `fork_proposal` whatever the bit says (`dispatch/schemas.py:386`). `_run_transition` asks no `_rebase_request` when the bit is off, so the proposal leaves no record.
## Symptoms
- `scripts/offline_run.py:1036` (ml-6): closed by link 3.
- `promptpotter/application/bench/difficulty.py:282`: one series caveat over all rounds. Suspected root: the first row above.

## 2026-11-25 · docs(report): ml-8, trimmed to the word limit (bf3dd7479:report.md)

## Rows to file
None new. **A max-press resume replays on timing** stays open: 0 of 6 base runs moved on a 4-core desk, so no replayed record could be named. It needs a repro where ml-7 saw it.
## Symptoms
- The 6 `se=None` sites left are not t-based: `scoring/passes.py:144` (Welch/MOVER), `scoring/selection.py:33` (bounded interval), `passes.py:547` and `domain/arm_reading.py:331` (no band), plus 2 null-by-state sites.
- ml-7's capo 1-SIGINT leg exits 130 here at press 16. It did not reproduce.

## 2026-11-25 · docs(report): ml-8 (2f8220450:report.md)

## Rows to file
None new. **A max-press resume replays on timing** stays open: 0 of 6 base runs moved on this 4-core desk, so no record could be named. It needs a repro on the desk where ml-7 saw it.
## Symptoms
- `scripts/offline_run.py` (ml-7's capo SIGINT leg): it exits 130 on this desk at press 16. It did not reproduce.

## 2026-11-25 · docs(report): ml-6 report within its word limit (f537becc3:report.md)

## Rows to file
- **A fork under `informative` rows is declined in silence.** `mask/load.py::load_lineage_spine` reads every level of a line whose rows lie off the banked ruler as `unmeasured_delta`. Then `backprop.py::select_branch_round` returns `None`, and `escalation.py::_rebase_request` drops the L2's `fork_proposal` with no record or readout line.
- **The served walk is the last-listed arm's, not `measuring_arm`'s.** In `served_dashboard.py::_walk`, under `per_round` or `rolling` the last arm can hold no cells: 11 of 18 walk keys served nothing in the replay.
- **A switch inherits every knob it does not pin.** A potter `pobb` knob added later reaches capo-pobb with nothing saying so.
## Symptoms
- `scripts/offline_run.py:1036`: the rebase leg can't run potter's default rows or vote. Suspected root: the silent fork decline above.
- `promptpotter/application/served_dashboard.py:333`: the walk follows `rows[-1]`. Suspected root: the walk has no arm of its own.

## 2026-11-25 · docs(report): ml-5 rolling dispatch report (901de4a92:report.md)

## Rows to file
- `infrastructure/store/campaign_store/ledger_scan.py` is outside Owns, and I edited it rather than cut it. `StandingRounds.reads` is the fold that `arrival`'s resume reads. The orchestrator may count this as a cut.
## Symptoms
- `application/served_dashboard.py:342`: under rolling, the axis is the walk's declared row order. A walk now re-files it at every decision that moves its rows (`runner/round.py::on_turn`). The axis is still a declared order, not the rows taken. Suspected root: the axis should derive from the cells taken.
- `application/optimizers/potter/race.py::_settles`: the warrant is first-order, one preposterior sd. Its calibration is unmeasured on a paid run.

## 2026-11-25 · docs(report): ml-4 report (cbb5cc5a7:report.md)

## Rows to file
- **A parent's θ is bought by paired catch-up.** Under either shape the parent answers each row a rival took: 50 of the 300 calls on the offline run. Root: seat the parent's θ on the anchored ruler alone (`race.py::PoBBRace`, `catch_up.py`). Re-test: parent calls per round on `offline_run.py --optimizer potter --straight` → 0, with no decision move.
- **`qN/M` reads past its budget.** The offline readout prints `q25/20` (6 lines at the base too): a walk's top-ups count against `n_samples`. Root: name the budget a top-up extends. Re-test: no `qN/M` with N>M in `readout.log`.
## Symptoms
- `promptpotter/application/optimizers/potter/pobb.py`: eliminated lines read `q25/20`. Suspected root: top-ups count against `n_samples`.
- `promptpotter/application/maintenance/landing.py:121`: `check_every_treatment` refuses a potter-only `row_choice: informative` because capo-pobb inherits it while voting `draw`. Suspected root: a treatment's PoBB knobs are not its own.

## 2026-11-25 · docs(report): ml-3 report (5f2e662d2:report.md)

## Rows to file
- **`SampleStartedRecord.sample_idx` is read by no one and goes stale on a re-seat.** `domain/records/run_records.py::SampleStartedRecord` gets it from `Walk._started` as the row's index at launch; once a block's open re-seats the cell, its row sits at another index. No projection, view or webapp reads it. Action: delete the field (a ledger reshape: wipe run data), and drop `idx` from `query_loop.py::LaunchEvent`. This is outside ml-3's Owns.
## Symptoms
- `query_loop.py::Walk.flight`: `allowed` reads the rule horizon only. The press bounds what is past a close, and `flight` doesn't know the press. Suspected root: `Flight` carries no press.

## 2026-11-25 · docs(report): ml-2 report (3ba8b0265:report.md)

## Rows to file
- **A refusing coupling reaches only the mint.** `refuse_couplings` runs in `refuse_unrunnable`. An overlay that a resume or steer lays later is not re-asked, so a resume onto `informative` with `vote: draw` would run. Root: couplings are evaluated against `CampaignConfig` at preflight, not at `optimizer_manifest.py::_select`, where every selection passes.
- **Outside Owns (cut)**: `config_map.py`, `preflight.py`, `jobs/mint.py`, `types.generated.ts` (regenerated), `scripts/complexity_ledger.py`. Brief Owns named no refusal path. That is one cut, all for the refusal.
## Symptoms
- `round.py::_open_arm`: under `random` every walk now opens on the whole filtered pool (the brief's decision), so `_resolve_prior_cache` reads the archive for every pool row of every arm, every round. That is I/O that does not move any decision. Suspected root: the walk's replay state is built eagerly at open, never lazily per row taken.
- `webapp e2e responsive.spec.ts:32` (landscape-844) is flaky.
- `test_archive.py` new tests patch `DifficultyView.ruler`: no factory seats a warm ruler in a cycle.
- The resume test proves the race re-derives its choice. The walk-level "no new record" rides slice 1's resume test (`test_spend.py`, ml-3's).

## 2026-11-25 · docs(report): l4-key-c report (817cc4633:report.md)

## Rows to file
- **`catalogue_for` keys L4 on a term no connector declares**: `application/evidence/metric_catalogue.py::catalogue_for` branches on `"lift" in available` ("Lift over origin", θ, "the number the outer loop scores"). That was L4's θ term, which this link removed, so the branch is dead and its words were already false: the outer loop scores `held_out_lift`. Action: delete the branch. L4 evidence then reads `fitness`, a linear re-anchor of `held_out_lift`. **Re-test:** `git grep -c seed_lift -- promptpotter` (today 8, target 0).
## Symptoms
- `scoring/paired.py:423`, `scoring/selection.py:33`, `scoring/passes.py:529,538`, `evidence/metric_catalogue.py:94`, `domain/arm_reading.py:331`: every t-based `Estimate` sets `se=None`, although the statistics helper took one. Suspected root: `shared/statistics.py::paired_mean_t` returns `n` where it could return the SE.

## 2026-11-25 · docs(report): l4-key-b report within 400 words (367aa042f:report.md)

## Rows to file
## Symptoms
- Fallout outside Owns:
  - `node_context.py`, `read_params.py`, `search_point_scorer.py`
  - CLI `writes.py` and `main.py`
  - the webapp archive control
  - `domain/cells.py`, `measurement_context.py`, `embedded_run.py`, `offline_run.py`
- `axes:` panels are untested and no yaml uses them.

## 2026-11-25 · docs(report): l4-key-a2 report within its word limit (0b1ad7c90:report.md)

## Rows to file
- **The connector hook's signature reached outside Owns.** `connectors/protocol.py`, `harbor.py` and `dbllmbench.py` were edited so `extract_experiment` takes `(stores, panel)` and is awaited. This is signature fallout; the cut is one census line.
- **Edits outside Owns for staleness.**
  - The `domain/cells.py:42` docstring now cites `l4/cells.py::cell_key`.
  - `research/datasets/promptpotter-self/family.yaml:25` (potter-w1's file) still cites `InnerTaskSpec`. Action: cite `domain/l4/cells.py::cell_key`.
- **A cell's pin over-keys** (`domain/cells.py::PinnedCell`). `Seat.id` is still a label inside the pin. Action: the pin drops the seat id. **Re-test:** renaming a seat moves no cell key.
- **The inner depth check may be dead** (`spawn.py::run_inner_cycle`). Resolve now refuses a self-optimization seat, so the depth check may be unreachable. Action: prove it or delete it.
- **`embedded_run.py` imports the landing at module scope** (lines 38 and 77), like the one link 3 removed. **Re-test:** `git grep -n "maintenance.landing" -- promptpotter/application/embedded_run.py` is empty.
## Symptoms
- `promptpotter/application/datasets/loaders.py::held_bank`: an owned panel with no mint lists `n_samples` null. Suspected root: listings cannot await the keyed build.
- `scripts/offline_run.py:1530`: a comment restates both campaign-id widths. Suspected root: it should cite `persistence-and-state.md`.

## 2026-11-25 · docs(report): l4-key-a chain report (b130ae6fa:report.md)

## Rows to file
- **An L4 outer's bank has synchronous builders outside the session** (cut from link 1, **An L4 cell's key holds its config, not what its session loads**, which stays open):
  - Three readers build an L4 outer's pinned bank from `tasks.py::ResolvedInnerPanel.read`'s synchronous resolve, beside `wiring.py::session_bank`. They are `datasets/loaders.py::held_bank` (through `dataset_access.py::dataset_panel_rows`, read by `datasets/startability.py` and `scoring/measurement_log.py`) and `head_to_head_price.py` (~:147-167).
  - A pin keyed at the outer's async init would give those readers a second bank hash. That key is the seat's `CyclePlan.cycle_id`, i.e. `origin_hash` + `measured_address` under an `Optimize` purpose, since the bank is built before a purpose is bound.
  - Action: one async bank build for an owned panel, awaited by every reader, which then lays the seat keys. The pins must stay purpose-free.
  - **Rides with:** bkeys-2, with `loaders.py`, `startability.py`, `measurement_log.py` and `head_to_head_price.py` in its Owns.
  - **Re-test:** the bkeys-2 row's.
- **The launch probe reads a connector name, never a dataset** (cut from **An L4 launch never probes the connectors its inner cells name**, still open):
  - `admission.py::refuse_unreachable(name, endpoint)` is asked at `admit_and_hold`, `jobs/launcher/head_to_head.py:83,:94` and `embedded_run.py::_dataset_session`.
  - Each passes a connector and endpoint, so no door can reach the panel's seat datasets.
  - Action: the probe takes the stores and the dataset, and probes each seat's `runnable_definition` too.
  - **Rides with:** xconn 3, with `head_to_head.py` and `embedded_run.py` in its Owns.
  - **Re-test:** the xconn row's.
## Symptoms
- `jobs/launcher/run_job.py:23` imports `maintenance/landing.py` at module top. So no module that `landing.py::complete_registries` completes can import `run_job`, which is why `held_job.py` exists. Suspected root: the detached entry's `land_at_entry` belongs in `run_job`'s `__main__` path, not its module scope.
- `docs/operations/persistence-and-state.md:13` names a campaign id `{dataset}__{6 hex}`, a root's alone. An inner campaign's 12 hex is written nowhere outside `spawn.py`.

## 2026-11-19 · docs: webd-2 report (b1d535d2f:report.md)

## Rows to file
- **A campaign's own address opens nothing**: `#<campaign id>`, the hierarchy's address for a campaign, is refused as malformed by `address.ts::parseAddress`, which reads only a cycle hop (`campaign::cycle`). Action: decide whether a deep link may name a campaign (it would open its served `facts.live_cycle`), or say nothing mints such a link. Re-test: `#<campaign id>` opens that campaign's line with no banner.
## Symptoms
- `webapp/lib/address.ts:172`: the "bare campaign link" row's root is false. The banner IS `openHash`'s refusal. The campaign "opened" because the followed active run was that campaign. Suspected root: the grammar (row above).
- `promptpotter/domain/projection_envelope.py:33`: the docstring says the receiver treats an unknown field as drift. No client does. Suspected root: a claim no code keeps.
- `webapp/components/searchpoint/__tests__/press-subject.test.tsx:36`: `as never` for a partial `BenchScore`, because `test-fixtures.ts` has no builder for it. Suspected root: the hand-kept fixtures row.
- `webapp/lib/live-stream.ts:9`: the guard checks only the stamp. A matching contract stands for the rest of the shape.

## 2026-11-19 · docs: send-2 report (f1a8beeff:report.md)

## Rows to file
## Symptoms
- `promptpotter/connectors/harbor.py:1330` (`Trial.create`) and the docker steps: an infrastructure fault that is not a `CellInfrastructureError` escapes raw and now reads UNKNOWN (charged). Suspected root: the connector should declare it with `cell_failure(…, CONNECTION)` rather than core inferring it.

## 2026-11-19 · docs: readings-4 report (0a9f827df:report.md)

## Rows to file
- **Binariness has two sources**: `paired.py:86` reads `column.binary or key in scorer.two_valued`, and `GradeColumn.binary` now means only `hit`. Root: the declaration covers `hit` too (it is two-valued under every scorer), and `GradeColumn.binary` goes. **Re-test:** `git grep -c "column.binary" -- promptpotter` is 0.
- **A merge drops a sheet's declaration**: `merge_known_outcomes` returns `list[GradedCell]`, so `Parent.own_rows`, `cycle.py::pooled_theta` and `measurement_log.py` re-wrap through the session's scorer. Root: the merge takes and returns sheets. **Re-test:** `git grep -c "scorer.graded(" -- promptpotter` drops by 3.
## Symptoms
- `promptpotter/application/optimizers/potter/pobb.py:67`: `race_sheet` carries no scorer and no declaration, so no θ may ever be fit on it. Suspected root: the race holds the arm's search-point sheets instead of re-wrapping cells.
- `promptpotter/application/bench/difficulty.py:129`: `restamped` re-fits off `stamp.scope.scorer`, an id alone. Suspected root: as `CellSheet` had, a stamp names a scorer and none of its declarations.

## 2026-11-19 · docs: live-3f report (7945b13b3:report.md)

## Rows to file
None. No holder had to be cut.
## Symptoms
- `scoring/passes.py:266`, `domain/run_standing.py:444`, `store/lineage_queries.py:184,203`: each still spells `role != MeasurementRole.PARENT`. That is a second spelling of `is_proposal`. These files are outside Owns.
- `NodeConfigEditor.tsx:71`: rows reset on `base` identity, so a steer edit on a round still in flight resets whenever a new answer lands. Suspected root: the base should key on content, not identity.
- `useRound.ts:35`: the hook peeks `readResult` to choose its descriptor. A `final(body)` field on `ReadDescriptor` would own this in `read-cache.ts`.

## 2026-11-19 · docs: live-3 report (802846e97:report.md)

## Rows to file
## Symptoms
- `webapp/lib/derivations/round-candidates.ts:5` (`rowAt`, `ObservePoint.row`): this still stitches `dash.rounds[]` and `current_round` on the client. Both halves come from `dashboard.json`, so it is not a cross-source stitch, but the round read now serves the same series. Suspected root: `ObservePoint.row` could read `useRound`.
- `webapp/vitest.config.ts:11` (`isolate: false`): a `vi.mock` of `@/lib/state/auth` was ignored once another file in the run had imported the real module. Suspected root: mocks are unreliable in a shared module graph, so tests should mount real providers.

## 2026-11-19 · docs: live-2 report (fcd0323a7:report.md)

## Appendix: Rows to file
- **Lineage cleanup's ack survives a moved campaign** (live-2's grep, outside its Owns): `useCampaignForest.ts:121`'s `cleanupAcked` is set by the cleanup answer and never reset when `campaignId` changes, so another campaign shows the cleanup as done. Action: key it by `campaignId` the way `VerifyAction`'s press state is keyed. **Rides with:** webd. **Re-test:** a `dom`/`logic` fact: an ack on A reads false on B.
## Appendix: Symptoms
- `webapp/components/searchpoint/VerifyAction.tsx` (by design): going back to A while A's verify is still running shows no Stop, because the Stop belongs to the subject that was pressed. The served pass still shows "Measuring…".
- `webapp/lib/state/auth.tsx:74`: while the first `/me` probe keeps failing with a passing failure, no feed opens until the probe succeeds. That is intended under I5, but a slow probe means no live moves before then.

## 2026-11-19 · docs(store-3): chain report (64c32743a:report.md)

## Rows to file (cuts: three, so the chain stops here, with every link built)
1. (ledger, cut in place) **A cycle read still takes (campaign, hop)**: `pipeline_resolve.py::permitted_narrowing` (an optional `hop`, `None` reading the campaign's subject) takes the pair `CampaignCycle` carries. Its callers sit in `bench/resume_and_fork/fork_siblings.py` and `commands/launching.py`, both outside Owns. Action: it takes `at: CampaignCycle`, with the campaign-subject callers named. **Re-test:** `git grep -cE "campaign: Campaign, hop: CycleHop" -- promptpotter` == 0.
2. **The offline world reads a fault in its own policy as a refused call** (still open, re-tested): a `KeyError` injected at `offline_world.py::FakeLLM.reply`'s third send ran `offline_run.py --optimizer potter --against` to rc=1 with `MOVED decisions.ledger.optimizer_calls, round_warnings` and no traceback. Suspected root: `infrastructure/llm/openai_compat.py:438`'s `except Exception` → `failed_send`, which classifies a non-wire fault as a failed send. Holders: `infrastructure/llm/**`, `scripts/offline_world.py`, both outside Owns. **Re-test:** unchanged.
3. **A fact's account is built beside its data root** (rides with tests-x's "A test home is built beside `factories.workspace`"): `test_spend.py::_web_account(tmp_path, …)` builds stores outside `PROMPTPOTTER_HOME`, so `test_a_set_limits_raced_by_a_delete_answers_not_found_and_leaves_no_campaign_dir` reads no anchor now that io places a campaign from the data root. It is red at this tip. Action: the account mints at the fact's data root (`factories.workspace` at `user_data_root()`, or `own_home` set to the root the factories use). **Re-test:** that fact green.
## Symptoms
- `promptpotter/application/cycle_addressing.py:132`: `"campaigns"` is the refusal's noun, not the dir. The row's literal re-test can never reach 0 while `io.py` defines the constant.
- `promptpotter/infrastructure/store/draft_store.py::write_draft`: a keep makes the draft dir. It is safe only because an edit holds the claim (`dispatcher.py:528`) and a discard takes it. Nothing types that ordering.
- `tests/conftest.py::own_home` vs `tests/factories.py::workspace`: two data roots per fact (home beside `tmp_path`, stores in it). This is the root of cut 3.
- `scripts/offline_run.py:1219`: it builds `projects_root` from `long_path(...)`, so io has to strip the `\\?\` prefix before matching from the root (`io.py::_plain`).

## 2026-11-19 · docs(small-10): report (134e15066:report.md)

## Rows to file
- **A payload refusal says no expected form**: `shared/errors.py::PayloadInvalidError.refusing` lists pydantic's per-branch union errors (`cells.constrained-int: …; cells.literal['max']: …`), and REST's 422 and every CLI verb read it. Action: `validating` folds each field's errors into one entry naming its expected form from `model.model_json_schema()` (e.g. "cells: a count ≥ 1 or max"), for every door. **Re-test:** `set-sample-lookahead bogus --campaign c --cycle y` in a fresh home.
- (ledger hygiene) **The cap halts on money its served reading omits**: drop its "Left open … Action: a budget stop travels as its `StopLoop`" sentence, which `c1c6c81` shipped. The row keeps the `refuse_unless_room` / `exhausted()` half.
## Symptoms
- `runner/inner/spawn.py:149`: the writer builds from `ctx.inner_sandbox_root`, which `tasks.py::inner_spawn_of` derives from `shared_root` + tenant + `inside[-1]`; a test can pass any root (`tests/test_identity.py:2876`). Suspected root: `InnerSpawnContext` carries derivable fields instead of the spawner's `Stores`.

## 2026-11-19 · docs(report): webd-4 (a076aaf07:report.md)

## Rows to file
- **A followed exhibit still offers its candidate acts**: fork (`ApplyScenarioPanel`, `SteerForkPanel`), verify (`VerifyAction`) and held-out (`BenchGradeAction`) do not read `useWorkspace().exhibit`, so a follower may press an act the server refuses (I3). Action: each act's surface reads the flag, or the server serves the acts it admits per follower. **Re-test:** follow the exhibit as a non-host; no act control renders.
- **`size-tokens` reads no TypeScript**: a canvas font literal (`font: { size: N }`, `` ctx.font = `Npx` ``) that a token names passes the gate. **Re-test:** the check refuses `size: 11` in a `components/**/*.tsx` chart.
## Symptoms
- `webapp/e2e/walk/responsive.spec.ts:32` (`landscape-844 › every view fits its viewport`, touch project) failed once in 740 runs. The screenshot was overwritten by later runs. Suspected root: a layout read before the views settle (`noSidewaysScroll`/`everyTapLands` racing a late mount). It is not this diff, which changes no computed value.

## 2026-11-19 · docs(report): webd-3's chain report (61ca94e5b:report.md)

## Appendix: Rows to file
- **Delete's words are API prose** (webd-3's report): `DeleteCampaignPayload` ("…cache-hit for siblings") and `DiscardDraftPayload` docstrings are now the dialog's sentence (`hierarchy.py::DeleteAct.of`). Action: word each for a person: what goes, what stays (the measurement cache, the spend on the account). `command_kinds.py` was outside webd-3's Owns.
## Appendix: Symptoms
- `domain/hierarchy.py::SubtreeCampaign` inherits `delete` from `HierarchyCampaign`. So an inner campaign in the deep read serves a `delete-campaign` act that no door can press for it. Suspected root: the subtree entry subclasses the listed entry.
- `projection_envelope.py::ProjectionEnvelope.payload` is `dict[str, Any]`, outside the contract's shape, and `useCycleEvents.ts` reads only `cycle_id` and `activity`. Suspected root: a served body that no client reads, a case for the "Served fields the browser never reads" row.

## 2026-11-19 · docs(report): webd-1 report (9585164ac:report.md)

## Rows to file
The ledger row was cut in link 4 to read:
- **The served contract has two hand copies** (✓): SSE frames are cast with `as` (`live-stream.ts::openLiveStream`) and the build handshake (`X-Served-Contract`) covers fetches, not streams, which `EventSource` cannot read a header of: each frame carries the stamp, a server change in `application/cycle_reads.py::feed_frames` and the cycle tail; and the client decides what Delete files (`CampaignMenu.tsx:44-63` picks `discard-draft` vs `delete-campaign` and words what goes, vs `workspace_edits.py:64-78`): the listing entry (`domain/hierarchy.py`) serves it. **Rides with:** webd.
Cuts (outside Owns): `application/cycle_reads.py` (stream stamp); `domain/hierarchy.py` + `application/hierarchy.py` (Delete's served act).
## Symptoms
- `webapp/vitest.config.ts:11` — `isolate: false` makes `vi.mock` of a module another file already loaded silently miss. Suspected root: mocks are not a safe test tool in the shared module graph; the gate could refuse `vi.mock` of `@/lib/hooks/*`.
- `webapp/components/shell/sidebar/CampaignMenu.tsx:175` — Delete's dialog words what the server removes ("the shared measurement cache survives"), a third hand copy of `delete_campaign`'s behaviour, and it ignores `keep_results`. Suspected root: the same as the filed Delete row.
- A deep link `#promptpotter-self.e2e__329a79` (a campaign id alone) shows "This link opens nothing" yet opens the campaign. Suspected root: the masthead banner and `openHash` disagree on a bare campaign address.

## 2026-11-19 · docs(report): webapp-1 (40a725d30:report.md)

## Rows to file
- **A pane's phone layout keys on the screen, not its column** (webapp-1's report): `measurements.css`, `scoring.css`, `dashboard-pipeline.css`, `remote.css` and `ui/IntervalRow.module.css` switch at `@media (max-width:640px)` though they draw inside a `.view` that is 413px wide on a 1440 screen. Root: the `column` container the fold now reads. **Re-test:** `git grep -c "@media (max-width: *640px)" -- webapp/components webapp/app/styles/domains` reaches the screen-only rules alone.
- **A short landscape screen leaves its pane a 30px strip** (webapp-1's report): at 844×390 on touch, the masthead (about 200px), nav (56px) and remote bar (about 60px) are fixed. Nothing is under the bar any more, but a pane shows less than one row. Root: the chrome stays fixed on a short viewport; the masthead could scroll away with the pane. **Re-test:** at landscape-844 touch the samples pane shows ≥ 3 rows.
- **An open panel lags its trigger by a frame** (webapp-1's report): `ui/overlay.ts::useAnchored` re-places a popover from `requestAnimationFrame`, and it follows a trigger scrolled out of its pane over the masthead. Root: CSS anchor positioning for the panel, with no JS follow. **Re-test:** `git grep -c requestAnimationFrame -- webapp/components/ui/overlay.ts` == 0.
## Symptoms
- `promptpotter/main.py:328`: a walk run against a stale `webapp/out` shows "This page is out of date", which changes the layout under test. A bare `npx playwright test` does not refuse a stale build (the gate rebuilds), so a desk run reads a different page. Suspected root: the walk server does not refuse a stale bundle.

## 2026-11-19 · docs(report): usage-2 report (1aa89a55b:report.md)

## Rows to file
## Symptoms
- `promptpotter/domain/spend.py:38`: `StepUsage` re-declares `TokenAccount`'s parts flat beside the billing fields. The hand list that dropped `cache_write` is gone, but the class still carries a second copy of the field names. Suspected root: `step_tokens`' flat wire shape.
- `scripts/offline_world.py:61`: the world reports no `cached_tokens`, so every offline prefix reads "unreported" where OpenRouter would report a cold 0. Suspected root: the same gap row 3 of this brief closed for the other two parts.

## 2026-11-19 · docs(report): usage-1 chain report (e05487592:report.md)

## Rows to file
- **`_peer_cycle` seats an origin reading the archive does not hold**: `tests/test_scoring.py::_peer_cycle`'s `measured_origin` builds round 0's seated sheet from the bare `_measure`, never `score_search_point`. The seated reading names rows no cell file backs, and no round 0 closes. A fact reading round 0 must walk it itself (`_closed_origin`, `open_origin_round`). Root: seat the origin through the walk that files it. **Rides with:** the chain owning `tests/factories.py::seat`.
## Symptoms
- `pyproject.toml:54-66`: the `api` extra pins `mcp`, and `promptpotter/main.py:64` imports `presentation/mcp/server.py` unconditionally. So no venv the project can build lacks `mcp` yet has fastapi. `tests/test_security.py` imports fastapi and `promptpotter.main` at module level (lines 330-359). In a no-`mcp` venv it cannot collect because the `api` extra is missing, not because of MCP. Skipping its MCP facts by name would not make it collect. The row's half 1 is a claim about a venv that does not exist. I suspect the root is that the row should be restated or dropped, unless `mcp` is to leave `api`.
- `tests/test_scoring.py:1357`: `measured_origin` (see Rows to file).

## 2026-11-19 · docs(report): trim store-2 report under 400 words (2e915698d:report.md)

## Rows to file
- **An install write leans on the landing's mint** (store-2's link 1, cut):
  - `infrastructure/llm/pricing.py` writes `rates.json` and `route_ceilings/*.json` into dirs that only `data_root.py::mint_install` makes, and that module was outside store-2's Owns.
  - `mint_install` therefore makes every fixed prefix `INSTALL_KINDS` names, including dirs that no first write of this build needs.
  - Action: pricing's writes name themselves through `io.py::with_parents`, and `mint_install` keeps only what a reader or lock needs before any write.
  - **Re-test:** `git grep -c "with_parents" -- promptpotter/infrastructure/llm/pricing.py` ≥ 1, and the suite stays green with `mint_install` cut to `jobs/`.
## Symptoms
- `scripts/offline_run.py:389` (at base): the stale `cycle_config` call raised inside the fake LLM policy. The run then halted at `origin_gate` with 0 inner campaigns instead of crashing. Suspected root: the offline world turns a fault in its own policy into a refused call, so a harness bug looks like a decision.
- `application/maintenance/migrations/live_jobs_index.py:19`: it detected the pre-index shape by the existence of `jobs/`, which any lock taken there also creates. Suspected root: a migration keyed on a dir rather than on the files it migrates.
- `tests/test_spend.py` and `tests/test_security.py`: several facts built ledgers, locks and homes under paths that nothing minted, and passed only because io ran `makedirs`. Suspected root: test homes built beside `factories.workspace`, not through it.

## 2026-11-19 · docs(report): trim load-2 report (bec06dd7e:report.md)

## Appendix: Rows to file
- **A launch's slot door re-reads the campaign its door already holds** (load-2's report): `admission.py::request_jobs` loads the campaign under `admission_gate` while `ExistingCycle.campaign` holds it. The re-read is the archive-race guard, so it can go only once archive refuses a cycle with an asked job. Action: archive refuses under the gate, and `request_jobs` reads the held campaign. **Re-test:** CLI resume launch-path loads 2 → 1.
## Appendix: Symptoms
- `admission.py::request_jobs`: it re-loads a campaign the door holds. Suspected root: archive is not refused against an asked job under the gate.
- `bench.py::declare_line_bench`: it keeps a `campaign is None` arm for an unbound session. Suspected root: a stopped-before-init launch declares a bench line with no cycle.

## 2026-11-19 · docs(report): the feed-version-fix pass, with feed-version's report below (9bc6660d5:report.md)

### Rows to file
### Symptoms
- `run_state.py:201`: `time.time_ns()` under a claim moves `store:` every tick; idle walk re-asks 6 reads per ~7 s (base and tip). Root: a clock as version.
- `e2e/walk/responsive.spec.ts:32,55`: tap checks are flaky, failing in 5 of 8 tip runs and 1 of 6 base runs. Landscape-844 leaves ~20px of content under `button.chat-attach`. Root: layout. The tip's share is unproven.
---
## Rows to file
- **A pool or pack chip pressed twice inside one write's ack builds its second list from the first's pre-write read** (feed-version's report): `NodeKnobsPanel.tsx::PoolPicker` and the pack chips compute the whole next `active` / `packs` from the served value at press time. `useIngestFlow.ts::mutateDraft` queues the second send, but its patch is already fixed, so it drops the first toggle. Action: a draft write names its change (a toggle) and the server folds it onto the draft it holds, so no press builds a whole list from a read. **Re-test:** two presses in one ack window both land.
## Symptoms
- `promptpotter/application/cycle_reads.py::_COUNTED`: entries for deleted sandboxes and accounts are never dropped (bounded by subjects seen per process). The suspected root is the same as the "version spans a whole store" row: a writer-stamped version would own its lifetime.
- `application/health.py:203`: a bundle built before the commit reads "BROKEN" (stamp vs `git` tree) during a local e2e run, while it carries the change.

## 2026-11-19 · docs(report): tests-x report (09757be0e:report.md)

## Rows to file
- **A store under the data root off its workspace shapes** (tests-x's report): `build_stores` refuses only a root outside `user_data_root()`. A root under it but matching no `io.py::WORKSPACE_SHAPES` (say `<home>/projects/x/projects`) still builds, and io's anchor never binds there. The one holder is `scripts/offline_run.py::interrupted`, which reads a child leg's data root from the parent process. Root: the parent reads a leg through the leg's own process (or its own home), then `build_stores` matches the shapes. **Re-test:** `build_stores` on `<home>/projects/x/projects` raises.
- **io's path spelling is private** (tests-x's report): `stores.py::build_stores` imports `io.py::_plain`, the one spelling of a path for the anchor's compare. Root: io names it publicly. **Re-test:** `git grep -c "import.*_plain" -- promptpotter` is 0 outside io.
## Symptoms
- `scripts/offline_run.py:1219`: the parent builds `Stores` over another process's data root; the stricter refusal raises there. Suspected root: a cross-root read is done in-process.
- `tests/test_spend.py::_running`: it hard-codes `user_id="sub-9"` and `workspace=WorkspaceDir(registry.jobs_dir)`, a jobs dir standing in for a workspace. Suspected root: the helper predates `stores.identity`.
- `tests/test_security.py` (`bot-box`), `test_identity.py` (`packs-home`): second homes inside a fact. They still hold, and they are named here in case one is not needed.

## 2026-11-19 · docs(report): store-5a report (c087c36b4:report.md)

## Rows to file
- **The cycle index reads its chain beside the store's** (hubs; the row stays as is). Built and
  reverted in-run: `cycle_index.py::fold_cycle_index(hop, chain)` takes the chain's last span as the
  cycle's own; `CampaignStore.load(hop, moment)` = `fold_cycle_index(hop, self.chain(hop, moment=moment))`;
  `CycleIndexProjection(campaigns, hop)` feeds through `load`; `evidence/read.py` and
  `hierarchy.py` call `stores.campaigns.load`. Blocked by `scripts/offline_run.py::_fork_labels` and
  `:1737`, which call `read_cycle_index(<bare dir>)`; that file is bkeys-3's. **Rides with:** the
  next chain owning `scripts/offline_run.py`. **Re-test:** unchanged.
- **A machine's cell ceiling is counted twice** (Held; the row stays). The two counts have different
  subjects. `backend.py::_SENDING` counts this process's calls out, by `(connector, base_url)`, and
  `query_loop.py`'s `room()`/`freed()` schedule from it. `MachineSlots` counts containers across
  processes with file locks, by `connector.name` only. One count reaching both needs `room()` to read
  the pool's machine-wide occupancy. That means probing the slot locks, and POSIX `fcntl` locks are
  per-process (a probe can't see this process's own slots) while Windows `msvcrt` differs.
  **Held (design fork):** (a) a machine-wide occupancy file the pool writes under its turn lock, read
  by `room()`; or (b) the in-process count gives way for machine-held connectors, and arming waits on
  the pool. **Re-test:** unchanged.
## Symptoms
- `scripts/gate.py` `playwright`: red on the first full run and green on the rerun, with no web
  change. That run's WebServer logged "model capability refresh failed ... 403 Forbidden (agent
  proxy)". Suspected root: an e2e path depends on a network capability refresh, or on timing.
- `infrastructure/backend.py:118`: `MachineSlots` keys its pool by `connector.name`, while `_SENDING`
  keys by `(name, base_url)`. Suspected root: the machine has no single identity.
- `infrastructure/store/measurement_archive.py:601`: `_ChainReader` spells the prefix-chain file
  keys beside `file_keys(entry)`, without its `config_key` fallback. Suspected root: the file-key
  chain is a function of `node_configs`, not of the entry.
- `infrastructure/store/archive_queries.py::list_populations`: its ArmOf branch loads every
  population to drop the empty ones; no production caller now runs under ArmOf.

## 2026-11-19 · docs(report): store-4 report (e4fda63a9:report.md)

## Rows to file
## Symptoms
- The tests-x row-2 re-test (`git grep -c "import.*_plain"`) cannot see a parenthesized multi-line
  import, so it read closed at the base. Suspected root: a probe shaped as one line for a fact
  that spans lines. A symbol's absence (`git grep -c "_plain\b" -- promptpotter/infrastructure`) is
  the sturdier probe.

## 2026-11-19 · docs(report): store-3b (0ca2e232c:report.md)

## Rows to file
- **The data root has two sources**: io reads `paths.py::user_data_root()` on every miss while a store
  carries `Stores.projects_root`; where they disagree every door fails open silently, and in the suite
  they always do (`conftest.py::own_home` sets `<tmp>-home`, `built_stores` is `workspace(tmp_path)`), so
  the anchor is off in every fact but the `homed_stores` ones. Root: one binding source (`build_stores`
  refuses a root outside the data root) with test homes built under it. **Rides with:** tests-x's "A test
  home is built beside `factories.workspace`".
- **An 8.3 short path still fails open** (`io.py::_plain`): `abspath` makes a path absolute but expands
  no Windows short name, so `C:\PROGRA~1\…` against a long-named data root matches no anchor and re-makes
  a deleted unit. Root: the data root and the path compared in one canonical spelling
  (`os.path.realpath` on both, the `\\?\` prefix stripped after). **Re-test:** on Windows, a write under
  the 8.3 spelling of a deleted campaign raises `NotFoundError`.
## Symptoms
- `promptpotter/infrastructure/store/io.py:283` — "gone" was read off the path's shape, not the unit's
  presence; suspected root: `_created` decided by place alone.
- `promptpotter/infrastructure/store/io.py:119` — `_plain` absolute but not canonical (8.3, symlinks);
  root: two spellings of one dir.
- `tests/conftest.py:57` vs `:69` — `own_home` and `built_stores` name different roots, so the anchor
  is off in most facts; root: the data root's two sources (row above).

## 2026-11-19 · docs(report): store-2g report (e7a01a2d9:report.md)

## Rows to file
- **A data root nested at `…/projects/<x>/campaigns/<D>` reads as a campaign at its own first write.** `io.py::_ANCHORS` matches from the right. Close it by having the store hand its data root to the io doors, or by a check of the data root.
## Symptoms
- `read_model.py:519`, `Projecting.shielded`: it swallows every `Exception` from a projection, a vanished cycle's `FileNotFoundError` included, and then appends into that same deleted cycle. Suspected root: a projection over a deleted cycle should be skipped, not first turned into a warning.
- `io.py:300`, `write_bytes`: a replace whose temporary vanished raises a raw `FileNotFoundError`, while a create in the same spot answers `NotFoundError`. Two ways of saying "the unit is gone".

## 2026-11-19 · docs(report): store-2f chain report (69ada8c83:report.md)

## Appendix: Rows to file
- **A cycle read still takes (campaign, hop)**: `cycle_reads.py::_closed` and `pipeline_resolve.py::permitted_narrowing` (an optional `hop`) take the pair that `CampaignCycle` carries. Action: they take `at: CampaignCycle`. **Re-test:** `git grep -cE "campaign: Campaign, hop: CycleHop" -- promptpotter` == 0.
- **The campaigns dir is spelled beside its constant**: `landing.py:326`, `data_root.py:651` and `storage_report.py:141` spell `"campaigns"` where `io.py::CAMPAIGNS_DIR` names it. **Re-test:** `git grep -cE "\"campaigns\"" -- promptpotter` == 0.
## Appendix: Symptoms
- `draft_store.py::discard`: a draft is not an anchor, so a held draft-ledger handle appending after a discard re-makes `drafts/<id>/`. Suspected root: drafts are a third deleted unit.
- `presentation/cli/commands/reads.py:173-174`: the export writes through raw `mkdir` and `write_text`, not `io.py`. Suspected root: a CLI file write outside the store seam.
- `jobs/mint.py:695`: `draft_dir(...).mkdir(parents=True)` is a raw exclusive mint outside `io.py`. Suspected root: the same.
- `surface-ledger` said `origin/main is stale` (origin holds `64cb267`). The base was kept as briefed.

## 2026-11-19 · docs(report): small-13 report (0f4beb57f:report.md)

## Rows to file
- **A reported zero of reasoning reads as unreported** stays open. Its build is `wip/small-13`. **Held:** landing it re-keys every optimizer, because every offline fold's `tokens.cache_write` reads `None` (unreported) where it read 0. You decide whether that re-key is paid now, or whether the offline world reports the parts first.
- **A step's usage drops `cache_write`**: `domain/spend.py::StepUsage` has no `cache_write`, so `StepUsage.from_account` drops it, despite its docstring ("the counts ride whole, so none drops a part"). Every fold's `cache_write` is therefore unreported. Root: `StepUsage` carries every `TokenAccount` part. **Re-test:** a reply reporting `cache_write_tokens: 5` reaches a fold's `tokens.cache_write` as 5.
## Symptoms
- `promptpotter/domain/spend.py:61`: `from_account` drops `cache_write`. Suspected root: `StepUsage` lacks the field.
- `promptpotter/domain/records/run_records.py:559-564`: `TokenUsageRecord` keeps "0 also means no breakdown" for reasoning, cache_read and cache_write. Suspected root: the ledger's flat raw-JSON sum, which can't hold `None`.
- `promptpotter/domain/results_health.py:105`: `usage.reasoning or 0` folds an unreported count into 0 for the terminal-shape check.
- `promptpotter/application/bench/cycle.py:554`: `ArmWalk.continued` keeps the EARLIER row (`where(_unheld)`), while `CellSheet.merged` lets a later scored row win. That is two precedence rules for joining rows.

## 2026-11-19 · docs(report): small-12b report (7efcd23b5:report.md)

## Rows to file
- **A fit keyed by `str` beside arms keyed by `ArmKey`**: `intelligence/rasch.py::candidate_abilities` takes `Mapping[str, CellSheet]`, while every caller holds `ArmKey` keys. Mapping keys are invariant, so a caller holding a dict must rebuild it as a comprehension (`potter/election.py::RoundWinner.replay`, `tests/test_scoring.py::_elect`). Action: key the fit by `ArmKey`. **Held:** none claims it. **Re-test:** `git grep -n "for k in self.arms}, raced" -- promptpotter`. This corrects small-12's re-test pattern, which matched nothing.
## Symptoms
- The `potter/l4` label `promptpotter-self/e2e` moves from e930e406… at the base to 043ee9e2… at the tip, with decisions unmoved. Suspected root: the self-bank's rows carry potter's code fingerprint, so any potter code edit re-cuts it. Unverified.
- `potter/election.py:101` `RoundWinner.replay` fits again what the round measured, because `RoundResult` banks per-arm `ArmFit`s but not `RoundAbilities`. This is a derivation from banked inputs, not a second state.

## 2026-11-19 · docs(report): small-12 report (c1cec41b3:report.md)

## Rows to file
- **A fit keyed by `str` beside arms keyed by `ArmKey`**: `intelligence/rasch.py::candidate_abilities` takes `Mapping[str, CellSheet]`, while every caller holds `ArmKey` keys. Mapping keys are invariant, so a caller that holds a dict must rebuild it as a comprehension (`potter/election.py::RoundWinner.replay`, `tests/test_scoring.py::_elect`). Action: key the fit by `ArmKey`. **Held:** none claims it. **Re-test:** `git grep -n "for key in self.arms}, raced" -- promptpotter`.
## Symptoms
- `potter/l4` account label `promptpotter-self/e2e`: hash eba95b07… at base, 6dde97b2… at tip, with decisions unmoved. Suspected root: the self-bank's rows carry potter's code fingerprint, so any potter code edit re-cuts it. Unverified.
- `optimizers/potter/election.py::RoundWinner.replay`: a replay fits again what the round already measured, because `RoundResult` banks per-arm `ArmFit`s but not `RoundAbilities`. Suspected root: none. The fit's inputs are banked, so it is a derivation, not a second state.

## 2026-11-19 · docs(report): small-11 report (78ec7c3f1:report.md)

## Rows to file
## Symptoms
- `promptpotter/domain/spend.py:63`: `from_account` writes `tokens.reasoning or None`, so a reported `reasoning=0` becomes "not reported". `TokenAccount.reasoning` is an int with no `None`, so the reported/unreported split the brief asks for exists for `cache_read` only. Suspected root: `TokenAccount.reasoning` should be `int | None`, as `cache_read` is.
- `promptpotter/infrastructure/llm/openai_compat.py:126,128`: `reasoning` / `cache_write` still read `or 0`, which is the same root as the item above.
- `briefs/small-11.md`: links 1 and 3 were built from a ledger older than the base (`918e116` had closed both).

## 2026-11-19 · docs(report): rk-dec-3 report (81bcc463f:report.md)

## Rows to file
## Symptoms
- `HardSamplesPreview`'s rate text ("x of n tries") reads pooled cells, while the bucket colour beside it now reads only the selection's.

## 2026-11-19 · docs(report): refuse-1 report (d8216a9e4:report.md)

## Appendix: Rows to file
- **`test_spend.py` asserts the stop that ignored calls out** (refuse-1's report): `tests/test_spend.py::test_every_level_holds_a_sends_whole_bound_whatever_such_sends_billed_before` bills 4 holds before `exhausted()` may fire. Since refuse-1, `exhausted()` reads the remainder net of the calls out, so it fires as soon as the fifth hold is refused. Action (load-2 owns the file): replace the `while` loop with `assert own.exhausted() is ErrorCategory.SPEND_CEILING` right after the refused hold, then bill the 4 holds and assert it again. **Re-test:** `gate.py --only pytest` is green.
## Appendix: Symptoms
- `presentation/cli/commands/launch.py:285`: `--set` words a closed set itself ("is none of …") before the model refuses. This is a second renderer of an expected form. Suspected root: `_laid` should pass raw through to `validating`, which now names the form.
- `promptpotter/main.py:256`: the REST 422 finds its model through `request.scope["route"].body_field`, because a `RequestValidationError` carries no model. Suspected root: the command routes take the body untyped and run `validating`, keeping the schema through `openapi_extra`.
- The row's served half was not checked: `dashboard` / `set-limits` serving "$1.09 spent" while the book counts unreported and set-aside. The row's Action covered only `exhausted()`.

## 2026-11-19 · docs(report): readings-6 report (d0c2723f1:report.md)

## Rows to file
- **`bench_curve` reads `mean_lift`'s pooled default** (readings-6's report): `tests/factories.py::bench_curve` calls `paired.py::mean_lift` without `pooled`, so the keyword keeps a default only for that caller. Root: `bench_curve` passes `pooled=len(levels) > 1` and the default goes. **Re-test:** `git grep -c "pooled: bool = True" -- promptpotter` == 0.
- **The verify's `held` description names Welch alone** (readings-6's report): `VerifyReading.held`'s field description in `domain/verification.py` (and the two generated specs) says "else Welch's". A constant graded side now reads MOVER over its support band (`statistics.py::difference_interval`). Root: the description names the rule. **Re-test:** the generated specs name `_side_band`'s rule.
- **The paired SE floor is its own rule** (readings-6's report): `statistics.py::_normal_posterior` floors the SE at `1/(4n)`, beside `bounded_mean_interval`'s support bound. Folding it would move CAPO's paired t race and `read_arms`' SEs, so it needs an offline-run attribution of every moved pick first. **Re-test:** one support rule in `statistics.py`.
## Symptoms
- `scoring/paired.py::mean_lift`: a defaulted flag that keeps the old (t) read for an unchanged caller. Suspected root: the test factory sits outside this chain's files.
- `bench/difficulty.py:70` `frontier` iterates every cell of its sheet, not `standing()`. A merged-but-unfolded sheet would fit θ on duplicate rows. Suspected root: the `CellSheet` handed in is not guaranteed to be standing.

## 2026-11-19 · docs(report): readings-5 report (f4702223f:report.md)

## Rows to file
- **A connector's two-valued term reaches no scorer**: `authored.py::scorer_of` compiles from `CampaignConfig` alone, and that config names no connector. So a `CellTerm` or observation declaration cannot reach `fitness_scale_of`. Harbor's `env_reward` is `float(reward)` of a panel's `reward_key`, which is two-valued only per task, so `max(0.0, min(1.0, env_reward))` still fits φ. Root: a connector declares the shape of its observation keys (per panel where it varies), and the scorer is compiled with the campaign's connector. **Re-test:** the clamp under harbor reads two-valued in `Scorer.two_valued`.
- **The re-wraps through a separately fetched scorer**: `node_context.py`, `cycle.py`, `measurement_log.py` and `read.py` re-wrap through the session's scorer. The root is `cell_fold.py::merge_known_outcomes`, which returns `list[GradedCell]` and so drops a sheet's declaration. Root fix: the merge takes and returns sheets. **Re-test:** `git grep -c "scorer.graded(" -- promptpotter` drops by 4.
- **`read_arms` forces the t at one replicate per side**: `paired.py::read_arms` goes through `mean_lift`, which forces `binary=False`. At one replicate the means are 0/1 and the declaration calls for McNemar. The docstring "no row is 0 or 1" is false there. Root: `mean_lift` reads the declaration whenever the replicate means are single cells. **Re-test:** a 1-replicate arm pair under `label_match` reads `flips`.
## Symptoms
- `promptpotter/shared/statistics.py::difference_interval`: when both graded sides have zero variance, the band is the point (d, d). A constant fresh read that is below a constant recorded read then fails with certainty. Suspected root: the Welch arm has no support-bounded variance the way `bounded_mean_interval` has.
- `promptpotter/application/scoring/passes.py::verify_reading`: the levels (`own_level`) and `_held` read the fitness column separately, so there are two readers of one comparison. Suspected root: `VerifyReading` should carry the difference band as its own `Estimate`.

## 2026-11-19 · docs(report): readings-3 report (ed90790d4:report.md)

## Rows to file
- **A level's band reads a column's binariness, never its scorer's**: `selection.py::level_band` hands `GradeColumn.binary` to `bounded_mean_interval`, so a two-valued scorer's fitness band is the graded t, not Clopper-Pearson, though `Scorer.two_valued` declares it. Root: `level_band` reads the scorer's declaration; its callers (`runner/bench.py`, `scoring/metrics.py`) hold the scorer. **Re-test:** a 20/20 fitness band under `label_match` reads [0.832, 1].
- Replace **A verify compares against a point**: the record already keeps the level's `Estimate` (`OwnLevel`), and `scoring/passes.py::_held` reads `recorded.value` against the fresh band's top. Root: `_held` compares the two bands. **Rides with:** a chain owning `passes.py`. **Re-test:** a verify whose recorded level has a wide band is never "held" on a point comparison.
## Symptoms
- `promptpotter/domain/scoring.py:556`: `CellSheet` carries `scorer_id` alone, so every reader past the scorer (θ, `metric_measurand`) can recover none of its declarations. Suspected root: a sheet carries its scorer's declarations, not an id.
- `promptpotter/application/intelligence/rasch.py:271`: `_dispersion` still infers φ = 1 from the values.
- `promptpotter/application/scoring/paired.py:271`: `read_arms` takes a `spec` its one caller always passes as `ROUND_LIFT_SPEC`.
- `promptpotter/application/scoring/selection.py:32`: see the `level_band` row.

## 2026-11-19 · docs(report): rates-6 report (282f70dbd:report.md)

## Rows to file
## Symptoms
- `webapp/e2e/walk/responsive.spec.ts:32`: `landscape-844 › every view fits its viewport` fails at
  base `1df1293af575` ("a tap lands off the control drawn there": "Error" under
  `div.remote-control`); suspected root: a remote-control overlay drawn over a control at 844px landscape.
- `webapp/e2e/walk/responsive.spec.ts:32,40`: desktop-1440 and phone-393/412 cases flake between
  runs on the same tree; suspected root: the layout check samples before the view settles.

## 2026-11-19 · docs(report): rates-5 report (df1221a6f:report.md)

## Rows to file
- **An orphan reclaim is observed by its count** (rates-5's report): `reaper.py::reclaim_orphan_sandboxes`
  returns a reclaimed count that `reaper.py::sweep` discards, read only by tests
  (`tests/test_security.py`, `tests/test_spend.py`), and swallows an `OSError` on a sandbox's delete, so a
  sandbox that cannot be deleted is reported nowhere. Root: it returns its faults to `_recorded` like the
  sweep, and the facts read the sandbox dir. **Re-test:** `git grep -c "except OSError:" --
  promptpotter/application/jobs/reaper.py` == 0.
## Symptoms
- `promptpotter/application/jobs/reaper.py:54`: `except OSError: continue` on an orphan delete; error swallowed, never in `machine-status`; root as the row above.

## 2026-11-19 · docs(report): rates-4 report (2b3d62eae:report.md)

## Appendix: Rows to file
## Appendix: Symptoms
- `promptpotter/application/jobs/reaper.py::sweep_dead_cycles`: it returns a reaped count that no production code reads; only tests read it. Suspected root: the tests observe the reap through its count instead of the cycles' `stop_reason`. Once the tests read the cycles, the function can return its faults directly and the `nonlocal` closure goes away.
- `promptpotter/infrastructure/llm/pricing.py::_ROUTE_OUTAGE`: a process-global memo keyed by path. Only the process that met an outage remembers it. Another process re-fetches within `_OUTAGE_MEMO_S`.

## 2026-11-19 · docs(report): rates-3 report (7caff0998:report.md)

## Appendix: Rows to file
- **A route answer left stale is said nowhere** (rates-3's report): `pricing.py::refresh_route_answers` logs an `OSError` per row and returns `None`. The sweep's `route-answers` step (`reaper.py::_refreshed`) records that as a clean pass, so a row the sweep cannot refresh or drop stands silently. Root: the refresh answers the rows it left, as `RatesFile.unfetched` does for rates. **Re-test:** a route answer whose refresh raises `OSError` reads in `machine_status(...).unswept` under `route-answers`.
## Appendix: Symptoms
- `promptpotter/infrastructure/llm/pricing.py:350`: an error swallowed into a log (route answers). Suspected root: the step answers no fault (row above).
- `promptpotter/application/jobs/reaper.py:85` (`sweep_dead_cycles`) and the `periodic_sweep` except-branch: two writers of one step's record, the step itself and the loop on a raise. Suspected root: a step returns its faults and one place records them. It is held apart because launches call `sweep_dead_cycles` outside the loop.

## 2026-11-19 · docs(report): rates-2 (2f045e52a:report.md)

## Rows to file
## Symptoms
- `promptpotter/infrastructure/llm/pricing.py:122` (`refresh_rates`): a failed fetch is only logged. Neither the launch nor the sweep's `rates` step records it (it returns `False` and the pass reads clean), so `machine-status` cannot report that upstream is dead. Suspected root: `refresh_rates` returns a bool instead of the fault it hit. Its four `assert pricing.refresh_rates(force=True)` callers in `tests/test_spend.py` belong to readings-5.
- `scripts/offline_world.py:583`: the world has no switch for "upstream unreachable" and no count of fetches. The new fact has to patch `pricing.UPSTREAM_URL` and read `Router.unrouted`, which is a set.

## 2026-11-19 · docs(report): rates-1 chain report (f6d155a50:report.md)

## Rows to file
- **The API process prices off rates only a run refreshes** (rates-1's report): `pricing.py::refresh_rates_once` runs at a launch; an API box that launches nothing for over a day prices `paid_call`/`paid_pass` and capability reads on a stale `rates.json`. Root: whether the API's `reaper.py::periodic_sweep` (the route answers' one refresher) owns the rates too. **Re-test:** an API process with no launch refreshes a day-old `rates.json`.
## Symptoms
- `tests/test_security.py:3276`: patches `pricing.cache_path` because a mint fetches into the landing's home. Whether a fact fetches now depends on whether an earlier fact in the process already did. Suspected root: per-process state behind a test seam.
- `tests/test_spend.py:1927`: a policy raises a raw `ssl.SSLError`, a wire fault in the policy's own voice. The world lets `OSError` through for it.
- The open half of "A fact's home outlives it" (pytest `tmp_path` retention) needs `pyproject.toml`, outside these Owns.

## 2026-11-19 · docs(report): load-3 chain report (4845f19bf:report.md)

## Rows to file
Already written into the ledger by link 3, as the open half:
- **A test seat binds a campaign disk does not hold** (load-2's review, cut by load-3): `factories.py::loop_session` seats a synthetic `minted_campaign(hop)`. Reading the stored one breaks 19 facts whose fixtures seat a hop (`test_scoring.py::_PEER_HOP`) and only then store its campaign, across `test_scoring.py` and `test_security.py`. Action: each seat stores its campaign first, and `loop_session` reads it. **Re-test:** `git grep -c "minted_campaign(hop)" -- tests/factories.py` drops by the seat's one.
## Symptoms
- `infrastructure/store/campaign_store/store.py` (`armed_budget`, `run_limits`) and `pipeline_resolve.py::cycle_config` / `admission.py::campaign_sends` still take (campaign, hop). That is the same doubled campaign id one layer down, where the infrastructure cannot import `CampaignCycle`. Suspected root: the carrier belongs in `domain/` beside `Campaign`.
- `admission.py:391` re-reads the campaign that `at.campaign` already holds, before `refuse_campaign`. Either it is a deliberate fresh read under the gate or it is a second load; unclear which.
- The row said "nothing calls `bank_campaign_result` now", but `runner/entry.py:205` and `scoring/passes.py:637` both call it. The claim was stale.
- `tests/test_archive.py:1655` fakes a `HeldRun` with a `SimpleNamespace(session=…)`, against the tests charter.

## 2026-11-19 · docs(report): live-1 report (3664145a1:report.md)

## Rows to file
## Symptoms
- `webapp/lib/hooks/useCommand.ts`, `then` throwing: the error becomes a `failure` on the slot. Kept as before, but this is a programmer bug surfacing as an operator message (suspected root: `then` should not live inside the send's try).
- `webapp/lib/diagnostics.ts:82`, `IncidentContext` is not exported, so `live-stream.ts` spells it as `Parameters<typeof reportIncident>[1]`.

## 2026-11-19 · docs(report): inner-1 report (8c2bd2afd:report.md)

## Rows to file
None (no cuts).
## Symptoms
- `promptpotter/application/run_observers.py:117`: `RunObservers.arm_spend_book` reads none of `self`. Suspected root: the nesting wiring belongs to the book or to `enter_inner_cell`, not the observers. So the test fact (`tests/test_spend.py`) has to repeat its one line instead of calling it.
- `tests/factories.py:440`: `spend_book(enclosing=…)` sets `book.enclosing` directly, a second way to nest a book next to `arm_spend_book`. Suspected root: same as above.
- `promptpotter/infrastructure/run_scope.py:113`: `RunScope.depth` / `InstrumentMode.depth` say the same thing as `instrument is not None` while `MAX_INSTRUMENT_DEPTH = 1`. Suspected root: depth is generalised past what L4 allows.
- `promptpotter/infrastructure/llm/spend_book.py:225`: `_ENCLOSED = "backend"` is the default a nested book falls back to when the spawner's pass names no kind (`mode.enclosed_as or book.enclosed_as`). That is a fallback for a kind no spawner declares. Suspected root: every scoring pass that spawns should name its kind.

## 2026-11-19 · docs(report): feed-version (0acc876d8:report.md)

## Rows to file
- **A pool or pack chip pressed twice inside one write's ack builds its second list from the first's pre-write read** (feed-version's report): `NodeKnobsPanel.tsx::PoolPicker` and the pack chips compute the whole next `active` / `packs` from the served value at press time. `useIngestFlow.ts::mutateDraft` queues the second send, but its patch is already fixed, so it drops the first toggle. Action: a draft write names its change (a toggle) and the server folds it onto the draft it holds, so no press builds a whole list from a read. **Re-test:** two presses in one ack window both land.
## Symptoms
- `promptpotter/application/cycle_reads.py::_COUNTED`: entries for deleted sandboxes and accounts are never dropped (bounded by subjects seen per process). The suspected root is the same as the "version spans a whole store" row: a writer-stamped version would own its lifetime.
- `application/health.py:203`: a bundle built before the commit reads "BROKEN" (stamp vs `git` tree) during a local e2e run, while it carries the change.

## 2026-11-19 · docs(report): exhibit-acts-fix report (4e943763c:report.md)

## Rows to file
- None. Every finding was built.
## Symptoms
- `webapp/lib/state/auth.tsx:57`: adopting an identity calls `clearReadCache`, which remounts every read, so `/auth/me` is probed twice at sign-in. A `useFiles` test has to wait for the second probe. Suspected root: the anon→authed transition clears the cache it has just filled; the cache could be keyed by identity instead.
- `webapp/components/shell/remote/RunLimitsControl.tsx` (from the original report): its locked note is still written in the client ("not yours to set"). It now matches the served `READ_ONLY` voice, but it is not that row. Suspected root: the pane reads `useFiles`, not a served refusal, because no read offers limits.

## 2026-11-19 · docs(report): exhibit-acts (828d16d00:report.md)

## Rows to file
- **A pane's acts gate on the dashboard read, not the entry they act on**: `PromptFieldsEditor.tsx` picks where to bank a snippet by order (the viewed campaign, else the check-in), now limited to campaigns whose `filable` holds `bank-snippet`. Action: the editor's host names the subject an edit is written in. **Re-test:** follow the exhibit, open a check-in, edit a snippet; it banks into the check-in.
- **`RunAdmission.filable` is repeated on every listed entry**: about 36 kind strings per entry. Action: if listing size matters, serve it once per response. **Re-test:** the `/hierarchy` body size for a 50-campaign account.
## Symptoms
- `webapp/components/shell/remote/RunLimitsControl.tsx:182`: the read-only note "its owner's to set" is written in the client. Suspected root: the read-only words belong on the served `_READ_ONLY` refusal, which this pane doesn't read.
- `webapp/components/shell/sidebar/CampaignMenu.tsx:93`: archive, rename and delete don't read `filable`. The sidebar lists only the caller's own stores, so it's harmless for the exhibit, but a delegate granted only some capabilities would still be offered them.

## 2026-11-19 · docs(report): err-1 chain report (98fe04d02:report.md)

## Rows to file
- **dspy's send raises its raw failure** (cut: `connectors/dspy_module.py` is outside Owns): `dspy_module.py` closes its admission with `failed_send(failure)` and then re-raises the litellm exception. The cell then classifies it again in `sample_measurement.py::_failed_row` through `failed_send`. Root: raise `send_failure.py::FailedSendError` carrying the closed outcome. **Re-test:** `git grep -c "failed_send" -- promptpotter/application` == 0.
## Symptoms
- `promptpotter/infrastructure/llm/openai_compat.py:168` `FaultedReplyError` subclasses `httpx.HTTPStatusError` only so that `failed_send` can read its `status_code`. It is an in-send carrier that a typed close could replace.

## 2026-11-19 · docs(report): cmd-2 report (266a5c60c:report.md)

## Rows to file
## Symptoms
- `webapp/components/searchpoint/VerifyAction.tsx:48` holds `acting` (the in-flight command id) and `saturated` across a changed `path`; they reset only on `candidate.arm`. Stop and the saturated note can paint on another cycle. Suspected root: subject-bound view state that is not keyed by address. This is the same shape link 1 closed in `useRunControl`.
- `webapp/components/candidates/ApplyScenarioPanel.tsx:21`: `done` ("Forked at round N") survives a changed `path` unless the host remounts it. Same suspected root.
- `docs/specs/code-debt-cleanup.md` § webd: the row "The webapp's live state…" still says "(reported unless ✓)", but its only ✓ clause is gone.

## 2026-11-19 · docs(report): cfg-5 report (e0a01d3e4:report.md)

## Rows to file
## Symptoms
None new.

## 2026-11-19 · docs(report): cfg-4 report (6b4fd0d80:report.md)

## Rows to file
- **A run declaration lays today's defaults by a second merge beside the one fold** (cfg-4's report): `datasets/authored.py::effective_config` lays `defaults_config()` under a declaration's parts by a shallow `**` merge. `declare_run` diffs whole top-level values against the defaults dump. A declaration is therefore a replace-per-key document, while `layer_fold.py::fold_layers` lays a mapping key by key (and lists by `SUBSET`/`UNION`). The merge is right today only because `declare_run` dumps complete values; swapping in `CampaignConfig.overlaid` would quietly keep a default's mapping keys that a declaration dropped. Root: a `RunDeclaration`'s `loop` / `environment.target` is a fold patch (`CLEAR` for a dropped key) laid over `defaults.yaml` by `fold_layers`. That reshapes the measured address every root id hashes, which wipes run data and moves root ids. **Re-test:** `git grep -cE "\*\*defaults\." -- promptpotter/application/datasets/authored.py` == 0 (now 2).
## Symptoms
- `webapp/e2e/walk/responsive.spec.ts:32`: the [touch] "every view fits its viewport" fails at the base and the tip, on a different viewport each run ("a tap lands off the control drawn there", `"Error" is under div.remote-control`). Suspected root: a remote-control overlay drawn over a view's controls at some viewports. It is flaky, which hides it.

## 2026-11-19 · docs(report): cfg-3 report (019dbefe2:report.md)

## Rows to file
- **The bench-defaults pin walks code defaults that no longer exist**: `config_map.py::bench_defaults_pin` hashes `_leaf_defaults(CampaignConfig)` beside `defaults.yaml`. Since cfg-3, every value that walk sees is an absence, so it is a constant re-keying nothing. `config_map.py::_models_in` is a second model-tree walker beside the schema check in `campaign_config.py`. Action: the pin is `defaults.yaml`'s hash alone, and the walk goes. **Re-test:** `git grep -c "_leaf_defaults" -- promptpotter` == 0. (Not built: `config_map.py` was held by another session.)
## Symptoms
- `domain/draft_edit.py::CampaignLayer` validates nested models whole. A leaf layer cannot restate one axis of a family's split (for example `{bench: 100}` over the family's three), and that layer is refused at `definition_layer`. The family-level fold lays keys, but each layer's read refuses a partial instance. Suspected root: a layer's nested model should be a partial of it.
- `campaign_config.py::OptimizerChoice.arm_gate = "off"` is a code default on a bare selection outside the `CampaignConfig` tree, which `OptimizationConfig` overrides.

## 2026-11-19 · docs(report): cfg-3 after cfg-3-fix (750409e4c:report.md)

## Rows to file
- **The bench-defaults pin walks code defaults that no longer exist.** `config_map.py::bench_defaults_pin` hashes `_leaf_defaults(CampaignConfig)` beside `defaults.yaml`. Every value that walk sees is now an absence, and `_models_in` is a second model-tree walker. Action: the pin is `defaults.yaml`'s hash alone. **Re-test:** `git grep -c "_leaf_defaults" -- promptpotter` == 0.
- **A null split survives in `config_map.py`.** `config_map.py:274` still tests `config.dataset_split is None`, which no config can hold since cfg-3 (mypy `redundant-expr`). Action: delete the operand. **Re-test:** `git grep -c "dataset_split is None" -- promptpotter` == 0.
## Symptoms
- `campaign_config.py::OptimizerChoice.arm_gate = "off"` is a code default on a bare selection outside the `CampaignConfig` tree, which `OptimizationConfig` overrides.
- `application/datasets/authored.py::declare_run` / `effective_config` lay defaults by a shallow `**` merge. A nested instance is right only because the config dumped is complete. The suspected root is that the one fold (`fold_layers`) should lay them.

## 2026-11-19 · docs(report): bkeys-4 fix pass report (c50576029:report.md)

## Appendix: Rows to file
- **The landing key over-keys by module**: `landing.py::loading_modules` keys the whole import closure (325 of 421 modules), so a prose-free edit to any module a load could import re-lands every root, and the key costs ~1.4s more at every process entry. Over-keying costs one walk and no wipe; a missed key reads a stale landing. Action: key the functions a load calls, not their modules, once a call-graph walk is cheaper than the hash.
- **A served metric spec says `lift: bool` where an estimate says `form`**: `application/evidence/metric_catalogue.py::MetricSpec.lift` beside `Estimate.form` (`metric_catalogue.py:93` converts one to the other), so the browser picks `fmtLift`/`fmtUnit` by a flag (`EvidenceCharts.tsx`). Action: `MetricSpec` serves `form: EstimateForm`, and `fmtMetric` words it as it words an `Estimate`.
## Appendix: Symptoms
- `webapp/e2e/walk/responsive.spec.ts:55` (`harness.ts:404`): at phone-412, 14 taps on the opened campaign list landed under `div._card_883q4_34.rowhover-shell` / `.summary-block` (the sidebar row hover card drawn over the rows); red once, green on rerun. Suspected root: the hover card opens on the touch press that expands the list and is still open when taps are probed (a timing race, webd-4 lane).
- `scripts/gate.py --changed` does not run the offline-run check (it is landing tier), so a script break in the trace (link 5's first `RoundResult.model_validate_json`, a `CellSheet` that cannot parse from JSON) showed only at equals-main. Suspected root: nothing forces a `--changed` pass to run the offline run when `scripts/offline_run.py` itself changes.
- `promptpotter/domain/results.py::RoundResult` held `extra="ignore"` so that it "must read back", yet nothing reads a round file back as `RoundResult` (it is a write-only projection, `closed_rounds.py::RoundFileProjection`).

## 2026-11-19 · docs(report): bkeys-4 fix pass (F1-F9) report (f710e3010:report.md)

### Rows to file
- **The landing key over-keys by module**: `landing.py::loading_modules` keys the import closure, 325 modules, at 1.85 s per entry. Fix 2's walk cannot close it: that walk keys by a trace, and a landing has no trace. Action: key the functions a load calls once a call-graph walk is cheaper than parsing the closure.
### Symptoms
- `webapp/e2e/walk/responsive.spec.ts:55`: phone-375, a tap lands off its row. Red in the first full gate, green in the second. Same root as bkeys-4's phone-412 entry.
- `gate.py --web`, first run: one check was red, and it was green on rerun. Its name was lost to `tail`.
- `optimizer_manifest.py::behaviour_key` takes `members: Iterable` and iterates it three times. It should be a `Sequence`.
---
## Appendix: Rows to file
- **The landing key over-keys by module**: `landing.py::loading_modules` keys the whole import closure (325 of 421 modules), so a prose-free edit to any module a load could import re-lands every root, and the key costs ~1.4s more at every process entry. Over-keying costs one walk and no wipe; a missed key reads a stale landing. Action: key the functions a load calls, not their modules, once a call-graph walk is cheaper than the hash.
- **A served metric spec says `lift: bool` where an estimate says `form`**: `application/evidence/metric_catalogue.py::MetricSpec.lift` beside `Estimate.form` (`metric_catalogue.py:93` converts one to the other), so the browser picks `fmtLift`/`fmtUnit` by a flag (`EvidenceCharts.tsx`). Action: `MetricSpec` serves `form: EstimateForm`, and `fmtMetric` words it as it words an `Estimate`.
## Appendix: Symptoms
- `webapp/e2e/walk/responsive.spec.ts:55` (`harness.ts:404`): at phone-412, 14 taps on the opened campaign list landed under `div._card_883q4_34.rowhover-shell` / `.summary-block` (the sidebar row hover card drawn over the rows); red once, green on rerun. Suspected root: the hover card opens on the touch press that expands the list and is still open when taps are probed (a timing race, webd-4 lane).
- `scripts/gate.py --changed` does not run the offline-run check (it is landing tier), so a script break in the trace (link 5's first `RoundResult.model_validate_json`, a `CellSheet` that cannot parse from JSON) showed only at equals-main. Suspected root: nothing forces a `--changed` pass to run the offline run when `scripts/offline_run.py` itself changes.
- `promptpotter/domain/results.py::RoundResult` held `extra="ignore"` so that it "must read back", yet nothing reads a round file back as `RoundResult` (it is a write-only projection, `closed_rounds.py::RoundFileProjection`).

## 2026-11-19 · docs(report): bkeys-3 (4969831df:report.md)

## Rows to file
- **A round file stores a served word**: `arm_reading.py::ArmReading.vs_reference_name` is a `@computed_field` dumped into each round file's `scoreboard` and `headline` (`results.py::RoundResult`'s computed fields, read back on `extra="ignore"`). Root: the round file holds facts; the round read words them. **Re-test:** a `RoundResult` dump holds no `vs_reference_name`.
- **The landing key over-keys by module**: `landing.py::loading_modules` names 86 modules, so any code edit in one of them re-walks every root at entry (cost: one walk, no wipe). Root: key the functions a load reaches, not their modules. **Re-test:** an edit to an unreached function in `domain/paired_reading.py` leaves `landing_key()` unchanged.
## Symptoms
- `scripts/offline_world.py:602-613`: `unslept` replaces only the backend's `held_wait`. `openai_compat.py:483` still sleeps on the wall clock in the offline world. Suspected root of link 3.
- `infrastructure/store/read_model.py:518-528`: a `derived_file_stale` warning on lock contention (`LOCK_TIMEOUT`) depends on load. Another suspect for link 3.
- `webapp/lib/format.ts:113,129`: these comments cite `arm_reading.py::level_text` and `lift_text`, which live in `paired_reading.py`.

## 2026-11-19 · docs(report): addr-4 report within 400 words (9c011f00b:report.md)

## Rows to file
- **`instrument_of` takes params it reads only identity from**: `domain/paired_reading.py::instrument_of(dataset_hash, params)` reads only the inner origin. Since link 2 that is cell identity, never sent params, so its callers lay `PipelineParams.filed(cell_identity)` to call it. Action: it takes the cell identity. **Rides with:** bkeys (owns the file). **Re-test:** `git grep -c "filed(" -- promptpotter/application/runner/mint.py` == 0.
## Symptoms
- `webapp/e2e/walk/responsive.spec.ts:55` (phone-412): "a tap lands off the control drawn there", because a `rowhover-shell` card covered the rows. It passed on rerun, and 375/393 passed in the same run. Suspected root: the previous test's hover card does not close before the tap probe.

## 2026-11-19 · docs(report): addr-4 fix pass report (0af4776f4:report.md)

## Rows to file
## Symptoms
- The per-link gate runs before the R4 commit skipped `offline-run`, so R4's re-key of `behaviour_keys.json` landed as its own commit (`a205b3d`).

## 2026-11-19 · docs(addr-3): the chain's report (6f494806f:report.md)

## Rows to file
- **An identity re-key moves offline decisions: its holders sit outside `pipeline_resolve.py`** (cut from addr-3). An arm's id is `opt_search_point.py::OptSearchPoint.id`, which hashes `pipeline_params`. Those params carry the identity layer (`pipeline_resolve.py::ParamLayers._identity`), and the wire strips it again (`scoring/sample_measurement.py::_without_identity`, by `Session.identity_keys`). Root: a point's params hold what it sends. The identity is laid only into the archive key (`scoring/search_point_scorer.py`'s `node_configs`, `scoring/passes.py`, `datasets/loaders.py`), and `ParamLayers.recorded` keeps the identity a measured point ran under. Holders: `sample_measurement.py`, `initialization/session.py`, `search_point_scorer.py`, `passes.py`, `loaders.py`. **Rides with:** xconn 3. **Re-test:** `wip/xconn`'s run against its base moves only `backend_revision` leaves.
## Symptoms
- `assets/datasets/defaults.yaml`: a default campaign still raises the `inert` coupling `display_subset_relative_under_resubset`. The suspected root is the display defaults.
- `domain/layer_fold.py::patch_between`: a SUBSET list that `defaults.yaml` might one day hold cannot be widened by a declaration. The suspected root is that SUBSET has no replace.

## 2026-11-18 · docs: owner-1 report (1ecbe1bae:report.md)

## Appendix: Rows to file
- **`ServedPipeline` carries its connector's name, not its owner**: `pipeline_resolve.py::ServedPipeline.connector` is a `str` built from a `Connector` that `served_pipeline` already holds. Because of that, `_response` (`:892`), `campaign_terms` (`compiler.py:297`), `laid_config` and `datasets/draft_patch.py:121` all look the name up again, and `origin.py:67` hands `campaign_terms` the `.name` of a `Connector` it already holds. Action: `ServedPipeline` and `campaign_terms` take the `Connector`. **Re-test:** `git grep -n "CONNECTORS.get(" -- promptpotter/application/pipeline_resolve.py promptpotter/application/scoring` leaves only the campaign-name sites.
## Appendix: Symptoms
- `optimizer_manifest.py:922`: `fired_on`'s key order follows `_reach`, and it differed between two resolves of the same panel in one base process (it also varies with the hash seed). Suspected root: `_reach` is built in set order. It is harmless while every reader compares or hashes with sorted keys.
- `tests/test_identity.py:1092` and `:4199` pin `identity_config`'s three arguments and the single-argument `Connector.pinned`, which is why a pinned panel resolves its cells lazily (`ResolvedInnerPanel.cells`).

## 2026-11-18 · docs(ws-3): report and walk shots (f930ae838:report.md)

## Rows to file
## Symptoms
- `application/evidence/read.py` (candidate branch): a candidate with no cells in the addressed round is now served with 0 cells. It no longer falls back to an older round. Suspected root: none; this is the intended semantic, flagged for readers of `measuring`.
- `domain/cycle_paths.py::Subject.parse`: a malformed candidate subject now raises the hierarchy address's "Malformed hierarchy address" message. Suspected root: the two grammars share one tail parser but not one error vocabulary.
- `components/candidates/lineage-tree.ts::ROOMY.rightPad` (80): a long lane name on the rightmost column can run past the SVG edge. Suspected root: `forestWidth` reserves no width for the lane label.

## 2026-11-18 · docs(report): tests-1 report (130d802dd:report.md)

## Rows to file
## Symptoms
- `tests/test_security.py::_SERVE_MCP_OFFLINE` installs the world inside a subprocess it spawns.
  The conftest refusal covers only the test process, so a fact that spawns a child without that
  preamble could still send for real. Suspected root: no child-process world. Unmeasured.

## 2026-11-18 · docs(report): spend-served-fix report (5942c1e3d:report.md)

## Rows to file
- **A campaign's cap count joins every cycle's bills to one cycle's other parts**: `served_dashboard.py::campaign_count` sets `metered_spend` (every cycle) beside the unreported, set-aside and calls out of *this* cycle's book, so another cycle's lapsed cells drop out of the campaign line the cap is said against. Action: the campaign's unreported folds every cycle's ledger (`Money.unreported_holds` filed by the campaign's ledgers). **Re-test:** a lapsed cell on a sibling cycle reads in this cycle's campaign cap line.
- **The door test reads `campaign_count`, not a door**: `tests/test_spend.py::test_a_door_reads_a_live_runs_bench_set_aside_as_its_book_stops_on_it` calls the helper, because `current_caps` serves parts only while a producer appends. Action: drive it through `current_caps` under a held, appending cycle (`test_security.py`'s `held_cycle` setup, moved into `factories.py`). **Re-test:** the test fails when `armed_caps` drops `count=`.
## Symptoms
- `scripts/complexity_ledger.py:803` (`_counted_at` of the merge-base): with an unfetched `origin/main`, surface-ledger measures an older merge-base (111004) and reads red for growth main itself made, naming no stale ref. Suspected root: the gate does not fetch or name the main it measures.

## 2026-11-18 · docs(report): spend-served chain report (f3bffffd1:report.md)

## Rows to file
- **A cycle's sidebar spend reads its ledger alone, so a cut send reads $0.000**:
  `hierarchy.py:302-308` builds the cycle row from `scan_ledger_spend` and passes `None` as
  unreported (`_spend_row("spend", …, None)`). A send a pause cuts is already filed at the cut as a
  `Lapse` (`spend_book.py::admitted`), but on the charge book, and for a nested run under the ROOT's
  ledger (`Admission`: `held_on((root.ledger or ledger).path)`), so neither the inner row nor the
  inner ledger can see it. `MeteredSpend` carries no `sends_unreported` (the paused-inner row's text
  says it does). Action: the cycle row reads its unreported off the hold tree
  (`Money.unreported_holds` under the cycle's jobs), not by ledger; and decide whether a cut send's
  late usage is collected at all (the cancelled request never returns). **Rides with:** hierarchy.py
  and the inner chain. **Re-test:** that row's own.
  (Replaces "A send a pause cuts is never filed…", whose filing half is already true at base.)
## Symptoms
- `domain/records/run_records.py:497` `Hold.node` says "None on a job's", but a set-aside is a
  second kind of hold with no node. Suspected root: a hold's role (job, cell, call, set-aside) is
  implied by which fields are null rather than stated.
- A cell reservation left open under a released job stays in `Money.open` forever, which keeps its
  job hold in `_holds`. Suspected root: a Release does not close the open holds beneath it.

## 2026-11-18 · docs(report): spend-4's chain report (98e209d44:report.md)

## Rows to file
- **A cycle's spend line is the campaign's count relabelled** (spend-4's report): `domain/spend.py::StopSpend.meter_lines` (`cycle = self.campaign.beside(...)`) and `served_dashboard.py::served_dashboard` (`metered_line(count=…caps count….beside(state.spend.metered(meter)))`) build "this cycle" from the campaign count's non-billed parts. So "A campaign's cap count joins every cycle's bills to one cycle's other parts" cannot close in `campaign_count` alone: folding the siblings' unreported there moves them into the cycle line. Action: `StopSpend` carries `cycle: SpendCount` beside `campaign` (a run-data wipe: the RunPhaseRecord stop shape), `campaign_count` folds every cycle's unreported, and `SpendCount.beside` goes. **Rides with:** a chain owning `domain/spend.py`, `run_observers.py`, `served_dashboard.py`. **Re-test:** a sibling's lapsed cell reads in the campaign cap line and not in this cycle's line.
- **A test that omits `offline` sends for real** (spend-4's report): nothing refuses a real `urlopen`/`httpx` send outside the `offline` fixture, so a fact missing it spends a real fetch. Three did, until spend-4. Action: the conftest world refuses unrouted network by default. **Re-test:** a fact resolving an OpenRouter plan without `offline` raises.
## Symptoms
- `promptpotter/domain/spend.py:1003`: the cycle line is `campaign.beside(cycle bills)`. Suspected root: one `SpendCount` stands for two books.
- `promptpotter/application/served_dashboard.py:805`: the same relabel, on the served metered line.

## 2026-11-18 · docs(report): small-9 (29277770b:report.md)

## Rows to file
## Symptoms
- `application/*` holds about 15 direct `connectors.CONNECTORS.get(definition.connector)` lookups (for example `wiring.py:174`, `pipeline_resolve.py:413`, `head_to_head_price.py:147`). They are outside Owns. Each could read `dataset_access.py::connector_of` (or `dataset_connector`) instead. The suspected root is that a definition doesn't carry its owner.

## 2026-11-18 · docs(report): small-8 report (0cbec47b6:report.md)

## Rows to file
- **The round-result factory keys arms by position** (small-7's report; cut by small-8): `tests/factories.py::round_result` keys arms `c{i}` and the reference `PARENT = ArmKey("parent")`, but `Parent.own_rows` reads `ArmKey(individual.id)`. So a seated round's cells never reach the parent's rows unless a test re-keys them (`tests/test_security.py`'s fence test does). Root: the factory keys each arm, and `PARENT`, by its individual's id. Holder outside small-8's Owns: `tests/test_scoring.py` (literal `ArmKey("c0")`/`"c1"` against factory rounds). Three tests fail under the factory fix: `test_a_survey_rounds_starts_and_reference_are_arms_and_its_matrix_their_cells`, `test_a_cell_a_declared_bound_cut_is_settled_and_never_re_measured` and `test_a_layer_forking_on_an_arm_its_round_left_unelected_re_elects_it`. Run it after the `l4-words-fix` / `spend-served-fix` chains release test_scoring.py. **Re-test:** the fence test passes without its re-key.
## Symptoms
- `tests/test_identity.py:3693,3813`: an arm keyed `PARENT` carries a different individual (`population[0]`, `origin`). The same position-keying shape the factory has.

## 2026-11-18 · docs(report): small-7 (e6c6940f5:report.md)

## Rows to file
## Symptoms
- `tests/factories.py::round_result`: it keys arms `c{i}` while `Parent.own_rows` reads `ArmKey(individual.id)`, so a seated round's cells never reach the parent's rows unless a test re-keys them (link 2 had to). Suspected root: the factory's arm key is not the individual's id.
- `application/scoring/passes.py:513`: `reading=` repeats `unread`'s condition, because mypy narrows `band` only through the inline test.
- The new render was not seen in a browser: no served stand-up in this cloud lane beyond the gate's playwright walk, which doesn't open the account's archive control.

## 2026-11-18 · docs(report): small-6 report (5a43c5a9b:report.md)

## Appendix: Rows to file
## Appendix: Symptoms
- **`promptpotter/domain/results_health.py:166` (`content_filtered` → fatal):** a provider content filter can trip on one row's input text. That would make it row-deterministic, which breaks the law in the `classify_result` docstring the same way `empty_response` did. Suspected root: the filter fires on the cell's content, not the config. It needs a measured case before it moves.
- **`tests/factories.py::arm_state` (`cut="broken"`):** this builds `Broken(degradation={})`, which `render/candidate.py::individual_summary` cannot render (KeyError `fatal`). The re-test had to rebuild the reason itself.

## 2026-11-18 · docs(report): small-5 report (0db884252:report.md)

## Rows to file
## Symptoms
- `paper/notes/ep04-findings.md:168` describes `schema_repair_errors` as strings. These are historical notes and were left alone.

## 2026-11-18 · docs(report): served-1 chain report (0a819fe6f:report.md)

## Rows to file
- **A connector's name is looked up again where its `Connector` is held** (served-1's report): `free_tier.py::free_fence`, `pipeline_resolve.py::laid_config` and `jobs/launcher/admission.py::refuse_unreachable` take a `str` name. Every caller in `pipeline_resolve.py`, `datasets/draft_patch.py` and `jobs/launcher/` holds the `Connector` and passes `.name`, and `free_fence` then calls `CONNECTORS.get` on it. Action: they take the `Connector`, and the campaign-name sites (`pipeline_resolve.py` `_campaign_merge`, `jobs/mint.py:131`) look it up once. **Rides with:** a chain owning `free_tier.py` and `jobs/launcher/`. **Re-test:** `git grep -n "connector\.name" -- promptpotter/application/pipeline_resolve.py promptpotter/application/datasets promptpotter/application/jobs` → 0.
## Symptoms
- `pipeline_resolve.py:390`: `UploadBasis.connector` is a `str` that every read resolves again. Suspected root: the draft basis stores a name, not a validated `Connector`.

## 2026-11-18 · docs(report): scope-2 report (c7cac46a9:report.md)

## Appendix: Rows to file
The ledger now holds this row (batch/26 · money):
- **A resume loads its campaign 4×**: the door, `CampaignStore.ceiling_meter_of` (from
  `launcher/admission.py::admit_and_share`), `CampaignStore.armed_budget` (from
  `quota.py::declare_run_budget`) and `runner/mint.py::open_launch`. No launch carries the door's
  `Campaign` past admission: `admit_slotless` and `admit_and_share` take a hop, and neither
  `Session` (its `purpose` only) nor `CycleConfig` holds the campaign it was laid over. Action: the
  door's `Campaign` rides the launch (the session, or `CycleConfig` beside its `config`), and the
  store's ceiling reads take it. **Rides with:** a chain owning `store.py`, `quota.py` and
  `initialization/session.py`. **Re-test:** one `load_campaign` per inline resume, counted with a
  patched loader around `embedded_run.py::run_campaign`.
Cuts: 1 (the holders above, outside Owns).
## Appendix: Symptoms
- `promptpotter/application/jobs/launcher/admission.py:390`: a queued launch loads its campaign
  again after the wait (`refuse_campaign`). That is a fifth load, but it is deliberate (a
  definition can move while the job waits), so it belongs to the row above, not a fix here.
- `tests/test_identity.py::held` (fixture) mints `decl` with no bank or `source:`. A resume of it
  stops at `PayloadInvalidError` before `open_launch`, so no test drives a whole inline resume to
  count its loads.

## 2026-11-18 · docs(report): panel-rule-2 (2b94a2e85:report.md)

## Rows to file
- **A test outside Owns encodes the connectorless panel fold** (panel-rule-2's report): `tests/test_identity.py::test_a_members_panel_lays_over_its_familys_key_by_key_at_every_depth` declares a panel with no connector, which `read_dataset_definition` now reads as none. Action: its family layer names `pipeline.connector: promptpotter`. **Re-test:** `gate.py --only pytest` green.
## Symptoms
- `promptpotter/application/runner/inner/tasks.py:214`: `parse_inner_panel` is a second name for `InnerTasks.model_validate`. Its one caller, `spawn.py::_resolve_inner_panel`, sits outside this chain's Owns.
- `promptpotter/connectors/__init__.py:30`: `_validate` does not refuse a `declared_panel_model` on a connector that owns no panel.

## 2026-11-18 · docs(report): panel-rule chain report (6ca3c7974:report.md)

## Appendix: Rows to file
The ledger row now reads, in full:
- **A panel's `inner_campaign` folds family→member by key, not by `CAMPAIGN_RULE`** (cite-fold's report): `store/dataset_access.py::read_dataset_definition` lays the panel by `layer_fold.py::untyped_rule`, so a member that changes a node's `model` or names another `optimizer` keeps the family's `route_order` or `nodes`, which a campaign block's fold drops or clears. The connector declares only the model its panel *resolves* to (`experiment_model`), which for `promptpotter` (`ResolvedInnerPanel`) is not the declared panel's shape (`InnerTasks`). Action: `connectors/protocol.py::Connector` declares the model its declared panel folds by, `spawn.py`'s `CONNECTOR` names `InnerTasks`, whose `inner_campaign` lays by `CAMPAIGN_RULE`. **Re-test:** a member changing `l1_generate`'s `model` under a family that sets `route_order` resolves without that `route_order`.
Owns for a successor: `connectors/protocol.py`, `runner/inner/spawn.py` (the `CONNECTOR` binding), `runner/inner/tasks.py::InnerTasks`, `dataset_access.py`. `rules_of` also needs a way to let a `dict` field fold by a model's rule: either `inner_campaign` annotated with CampaignConfig's rule as metadata, or `inner_campaign` typed as a partial model.
## Appendix: Symptoms
- `dataset_access.py:430` `_declared_panel`: a definition's `panel` under a connector that owns no panel is folded and then ignored without a word. Suspected root: `DatasetDefinition.panel` is read without its connector.

## 2026-11-18 · docs(report): load-1 stops at census, three cuts (cd1f60dca:report.md)

## Appendix: Rows to file
- **The campaign's carrier needs owner-1 and spend-4 files**: reland **A resume loads its campaign 4×** as a chain that owns `initialization/wiring.py`, `application/jobs/mint.py`, `pipeline_resolve.py`, `served_dashboard.py`, `tests/test_spend.py`, `tests/factories.py` and `tests/test_identity.py` beside this brief's Owns. The suggested shape: `Session.bound(hop, *, campaign)` replaces `purpose` (`purpose` derived), `open_launch` reads `session.campaign`, and admission and `armed_budget`/`ceiling_meter_of` take the `Campaign`. **Re-test:** the counts above.
- **A terminal/REST resume reloads its campaign at every hop of the dispatch**: `cycle_addressing.py::load_session`, `dispatcher.py::_on_cycle`, `admission.py::request_jobs`, `admission.py::campaign_sends`, `store.py::run_limits`→`read_campaign_ceiling`. Action: `ExistingCycle.campaign` (already held) feeds each of them. **Re-test:** CLI resume launch-path loads 8 → 1.
- **A run's projections re-read the manifest per round**: `output.py::_render_*`, `measurement_log.py::cycle_hard_samples`, `bench.py::_line/line_passes/line_outlay/declare_line_bench`, `earned_blocks` `list_campaigns`: 16 loads per 1-round resume. **Re-test:** whole-run loads 20 → ≤ 5.
## Appendix: Symptoms
- `infrastructure/store/campaign_store/store.py:686`: `read_campaign_ceiling(hop)` loads the manifest only for `root_hop`. Suspected root: the cycle ceiling is addressed by hop, not by campaign.

## 2026-11-18 · docs(report): limits-ux chain report (7ccdcc376:report.md)

## Appendix: Rows to file
- **Two wordings of the campaign's spend against its cap.** `domain/spend.py::campaign_cap_lines` (served as `ServedDashboard.cap_lines`, read by `shell/SpendBuckets.tsx` and `searchpoint/LimitReconcile.tsx`, and by `StopSpend.meter_lines`) says the same fact as `ArmedCaps`' money rows. Action: render the money rows of `caps` there and delete `campaign_cap_lines` and `cap_lines`. Outside this chain's Owns.
- **`metered_line` reads a cycle's spend against its share of the campaign cap** ("this cycle $X of its $Y share"), the scope pairing this chain removed from the panel. It is still served and rendered by `SpendBuckets`. Action: decide whether the share belongs on any surface; if not, delete it with `_Present.allotment`.
## Appendix: Symptoms
- `.claude/skills/design/SKILL.md`, named by the brief, does not exist at the base.
- `webapp/e2e/walk`: no spec opens the run panel, so its render rides only this chain's RTL test.

## 2026-11-18 · docs(report): land-order chain report (e44a281de:report.md)

## Rows to file
- **A reader's helper body is not keyed**: `landing.py::landing_key` hashes each reader's and migration's own source. A helper a reader calls (for example the record validators behind `ledger_line`), or a pydantic validator body on a row's model, can change what loads without moving the key. Root: a shape's loading behaviour has no digest beyond its JSON schema. **Rides with:** rel. **Re-test:** edit a validator a survivor's model runs; `landing_key()` moves.
## Symptoms
- `landing.py::last_landing` reads a stamp it cannot validate as `None`. A future reshape of `LandedStamp` would therefore make older code read a newer stamp as none, land again, and bypass link 1's refusal. Suspected root: the stamp's version should be read apart from its strict model.
- `docs/specs/code-debt-cleanup.md`, row "A landing waits on every live producer…": its "beside the stamp row above" now points at a deleted row. I left the row whole, as the brief says.

## 2026-11-18 · docs(report): l4-words-fix (247230038:report.md)

## Rows to file
- **A process-global outage memo leaks across tests**: `infrastructure/llm/pricing.py::_ROUTE_OUTAGE` is a module dict. In the full suite, `test_security.py::test_a_provider_error_on_a_live_flow_files_one_refusal_in_fixed_words` leaves it holding a real-proxy fetch failure. A later fact that prices an OpenRouter route then refuses with "Price list unreachable … Tunnel connection failed: 403". Root: a memo held by the process, not the store or the run scope; something sent through the real `urlopen` past the `offline` world. Action: key the memo where a test's world resets it, and find the send that escapes `offline`. **Re-test:** that fact, then `test_an_inner_runs_stop_reaches_its_outer_cell_off_the_cycle_it_ended_on` without its `_ROUTE_OUTAGE` monkeypatch, passes.
## Symptoms
- `pricing.py:286` `_ROUTE_OUTAGE`: the leak above. Suspected root: a background route refresh (`reaper.py:115` `refresh_route_answers`) outliving the `offline` fixture of an app-lifespan test.

## 2026-11-18 · docs(report): l4-words chain report (b58bab25e:report.md)

## Appendix: Rows to file
- **A paused stop is declared without its words** (l4-words' report): `run_observers.py::declare_run_stop` takes `detail` only for a stop that is not a pause. On a PAUSED stop it takes `take_pause() or (interrupted_by, "")`, and `runner/entry.py::_finalize_run` passes no detail there. So a self-pause (`ADAPTER_OWED`) can never say why. Action: a self-pause's raiser's detail reaches its `Paused` record. **Re-test:** an owed adapter's pause reads its raiser's words.
## Appendix: Symptoms
- `runner/termination.py::panel_gate_tripped`: SERVER→`BACKEND_HOLE` is spelled here, beside `phases.py::ERROR_CATEGORY_INFO.stops`, which says `None` for SERVER. Suspected root: `stops` carries two meanings, a refused send's stop and a banked hole's stop.
- `phases.py`: `PANEL_HOLE.grades_selection` is False, but `BACKEND_HOLE`'s is True. A halted backend hole still benches its pick, even though the outer reads no evidence from a halted inner run.

## 2026-11-18 · docs(report): l4-valid chain report (9edb7a5d0:report.md)

## Appendix: Rows to file
- **An L4 outer's errored cell carries no backend words**: `CycleResult` holds no stop detail, so `runner/inner/spawn.py::run_inner_cycle` grades a second `backend_hole` as "stopped on backend_hole", and the backend's status and message stay on the inner ledger. Action: the outer cell's error reads the inner stop's detail. **Re-test:** two injected 504s in one inner cell put the backend's words in the outer cell's error.
## Appendix: Symptoms
- `runner/termination.py::panel_gate_tripped`, its last line: a hole that is neither `SERVER` nor `HALTED` still stops as a bare `PAUSED` with no words. Suspected root: the gate has no stop for a charged-category hole.
- `runner/inner/tasks.py::_round_zero_ruler`: it scans standing rounds for every scope `inner_spawn_of` lays, L4 or not. Suspected root: the spawn context is built for every cycle (`spawn.py::claim_sandbox`'s own note).

## 2026-11-18 · docs(report): hubs-2 chain report (2a1d6009e:report.md)

## Rows to file
Already written into the ledger as the open half of link 3:
- **The cycle index reads its chain beside the store's**: `projections/cycle_index.py::read_cycle_index` takes a bare cycle dir and spells `ledger_chain(CycleDir(...))` itself. The store imports it (`CampaignStore.load`), so it cannot read `CampaignStore.chain`. Action: hand the index fold its hop's chain, with `CycleIndexProjection` keyed by hop. **Rides with:** hubs. **Re-test:** `git grep -c "ledger_chain(CycleDir(" -- promptpotter` sums to at most 1.
## Symptoms
- `infrastructure/store/campaign_store/store.py:393`: `list_campaigns` logs a manifest it cannot read (`logger.warning`) and drops it from the survey. The account holds a campaign that no surface reports. Suspected root: the survey has no typed "unreadable" row for that campaign.
- `application/runner/entry.py::build_cycle_result` now scans the wall clock on a pause too (it used to scan only at a final). This costs one ledger read per pause, and the readers ignore the result there.

## 2026-11-18 · docs(report): gate-speed chain report (808a69047:report.md)

## Rows to file
- **`Check.slots` is a guess beside `mb`'s measured peak**: Playwright claimed 3 and used 0.5. offline-run claims `min(8, cores)`, which is the whole box on any desk with 8 or fewer cores. Root: `mb` is a measured job peak plus a quarter, while `slots` is written by hand. **Re-test:** each `slots` within a quarter of the check's CPU-s ÷ wall, measured the way `mb` is.
## Symptoms
- `scripts/gate.py::_schedule`: `started()` probes run while the scheduler holds its Condition, up to 1 s per port, which delays a finishing check's bookkeeping. Suspected root: the probe belongs on the beat outside the lock.

## 2026-11-18 · docs(report): gate-hold report within its word limit (1280c635c:report.md)

## Rows to file
- **`l4-valid`**: an inner cell that died on `ServiceUnavailableError` (`admission_gate_stuck`) was banked as an errored sample of its arm. Whether that classifies as infra and gets re-measured belongs to the `l4-valid` chain. Reported, not edited.
- **The wallet read under the gate grows with the account's charges** (`quota.py::read_account_wallet` → `money_on(stores.charges.path)`, `campaign_spend`). It is admission's other scan; it grows with charges, not job files. Measure it before deciding.
## Symptoms
- `promptpotter/application/jobs/launcher/launch.py:136`: before this change a sweep's own `reap_orphans` raised `admission_gate_stuck` into the launch that ran it (`launch.py:136`). The tight-timeout base re-test lost 7 of its 8 launches that way. Suspected root: the scan was under the gate. Fixed here.

## 2026-11-18 · docs(report): flight-share chain report (153805388:report.md)

## Rows to file
- **The bench's set-aside lives only on the run's book, so no door serves it**: `SpendBook.set_aside_usd` is set in memory by `runner/loop.py`, `runner/bench.py::reserve_selection_pass` and `runner/entry.py`. `exhausted()` counts it, while `spend_book.py::cycle_count` serves it as 0. Action: land the set-aside where the book reads it back (a ledger fact or a hold on the charge book). **Re-test:** a fold during a run with a bench reserve serves the figure `exhausted()` reads.
- **A kept cut and a round boundary halt with no words**: `run_walks(keep_cut=True)` returns a bare `StopReason`, `runner/round.py` raises `StopLoop(cut)`, and `runner/loop.py::LoopEnd` carries a reason only. So only a walk's own stop names the parts. Action: a budget stop travels as its `StopLoop`, detail from `SpendBook.counted_words()`. **Re-test:** a round-boundary halt names the book's parts in its `run_phase` detail.
- **The stop record's campaign spend reads the bills alone**: `run_observers.py::declare_run_stop` fills `StopSpend.campaign` from `metered_spend`, so its `campaign_cap_lines` omit unreported money. Action: `StopSpend` carries the book's `SpendCount`. **Re-test:** a halted run's stop record names its lapsed cells.
## Symptoms
- `promptpotter/infrastructure/backend.py:110` `MachineSlots`: a second count of the machine's ceiling (a file-lock pool across processes, only for `cells_hold_the_machine`) beside the in-process count. Suspected root: one machine fact with two reaches; a single cross-process count could serve both.
- `promptpotter/application/scoring/query_loop.py` `_ScoringPhase.freed`: a run waiting on another run's landing polls at `HELD_POLL_S`, so a freed slot can sit idle up to 0.25 s. Suspected root: no wake signal crosses phases.

## 2026-11-18 · docs(report): factory-keys chain report (5b84b69c8:report.md)

## Appendix: Rows to file
## Appendix: Symptoms
None found.

## 2026-11-18 · docs(report): draw-strata chain report (01ef3397e:report.md)

## Rows to file
- **"Which benchmark a row measures" is spelled twice**: Shape: `potter/members.py::_strata` reads `measured_dataset or ""` where every other reader takes `Sample.measured_dataset` raw (None = the filed one), and `BenchScope` partitions by benchmark under the word `RulerScope` owns. Action: `_strata` keys on the field raw (`out.setdefault(s.measured_dataset, [])`, with `dict[str | None, …]`); `BenchScope` takes a benchmark name. **Held:** l4-outer owns `potter/`, l4-runs `runner/bench.py`. **Re-test:** `git grep -n "measured_dataset or\|BenchScope" -- promptpotter`. (Already in the ledger at the tip in this form.)
- **No offline leg draws a mixed-benchmark round**: Shape: `scripts/offline_run.py`'s L4 leg (`L4_DATASET = "promptpotter-self/e2e"`) seats two `justlogic/d234` cells, so `build_round_order`'s interleave and `origin_panel`'s per-benchmark seats are proved by pytest alone, and a regression there moves no leg. Action: seat the e2e L4 panel on two benchmarks. **Held:** l4-outer owns the L4 leg and `behaviour_keys.json`. **Re-test:** `potter/l4` moves against a base that draws by δ alone.
## Symptoms
- `promptpotter/application/optimizers/potter/members.py:136` — `_strata` keeps a second grouping of rows by benchmark beside the order's own interleave. Suspected root: the stratified share (`stratum_shares`) and the order should read one benchmark partition.

## 2026-11-18 · docs(report): cite-fold chain report (7a83ad565:report.md)

## Rows to file
- **A panel's `inner_campaign` folds family→member by key, not by `CAMPAIGN_RULE`**: `store/dataset_access.py::read_dataset_definition` lays the panel with an untyped every-mapping-by-key rule. A member that changes a node's `model`, or names another `optimizer`, keeps the family's `route_order` or `nodes`, where a campaign block's fold drops or clears them. Action: `InnerTasks` types `inner_campaign` so the connector declares its panel's rule, which `read_dataset_definition` folds by. **Re-test:** a member that changes `l1_generate`'s `model` under a family that sets `route_order` resolves without that `route_order`.
## Symptoms
- `store/dataset_access.py:230`: `source |= …` is the same one-level merge the panel had. Suspected root: `source` also wants `fold_layers`.
- `datasets/prompts.py:88` and `dataset_access.py`: the same untyped rule is spelled as two lambdas. Suspected root: `layer_fold.py` owes a named one.
- `domain/paired_reading.py::ArmName`: no slot is served, so a reply variant cannot name its arm except through its quote or its display label.

## 2026-11-18 · docs(report): admit-3 chain report (12eff178a:report.md)

## Rows to file
(Rows 1 and 5 stay in the ledger unchanged. Each gains the same unblock: whoever next owns `tests/test_spend.py`.)
- L1 needs: in `tests/test_spend.py::test_a_backend_cell_is_never_sent_again_while_it_may_still_bill`, build `remote` inside `remote_cell` (one client per `aclosing`); in `tests/test_security.py:4264-4267`, drop `_ = client.http` and assert `client.http.is_closed`. Backend diff: `http` becomes an attribute built in `__init__`, and `aclose` is `await self.http.aclose()`.
- L5 needs: `"dataset_split": {"bench": 10}` in the `campaign` block at `tests/test_spend.py:4914`; a split on the draft in `test_security.py::test_a_head_to_head_start_withdrawn_unrun_closes_the_client_its_arms_opened`; the two `test_identity.py` arm tests rewired onto `dataset_split.bench: 5` (the replicate test on 35 rows, since capo needs a 30-row search).
## Symptoms
- `embedded_run.py::enter` now sweeps on every call, and `teleprompter.py::acompile` enters twice, so it sweeps twice. This joins the open "`acompile` lands twice" row and closes with it.

## 2026-11-18 · docs(l4-runs): report the arc's links, proof and rows to file (c4b14eb77:report.md)

## Rows to file
- **An inner run spawned by a bench pass files all its own calls as `bench`.** The spawner's `run_scope().filed_as` leaks into the inner scope: inner cycle_8a2bda4c4c97 reads `{bench: 12}`, at both base and tip. Fix: `enter_inner_cell` carries the spawner's kind on `InstrumentMode` and clears `filed_as`, and `arm_spend_book` then reads `enclosed_as` from the mode. Owner: arc/l4-outer.
- **The noise-floor pass sets `refusal` on a pass that may hold cells**, which the `PassGradedRecord._refused_reads_nothing` validator would reject. Unverified.
## Symptoms
- `promptpotter/application/runner/inner/tasks.py:412` — inner calls are filed as `bench`. Suspected root: `filed_as` is inherited across `enter_instrument_mode`.
- `promptpotter/application/scoring/passes.py:538` — a refusal is set beside cells. Suspected root: the noise floor writes its refusal before it knows the pass read nothing.

## 2026-11-18 · docs(flight-ux): report, with panel shots before and after (60bc0846b:report.md)

## Rows to file
- **Inner runs inherit no look-ahead.** `promptpotter/domain/command_kinds.py` `SetSampleLookaheadPayload.inner_refusal` says "Throughput is what an inner run answers for itself: the outer's depth is never inherited." An "inner runs too" toggle would have to carry the outer's depth into each L4 inner campaign's walk at spawn (`application/runner/inner/spawn.py::run_inner_cycle`). That code is owned by l4-outer.
## Symptoms
- `promptpotter/application/views/readout.py:352`: an every-round depth is narrated "(armed — expires when this round finishes scoring)". The record carries no `auto`, so the readout can't tell the two apart.
- `.claude/skills/design/SKILL.md`, which the brief names, does not exist at base.
- The `settled` word "not available now" gives no reason. The fix belongs in `LOOKAHEAD_WORDS` (limits-ux / owner of the wording).

## 2026-11-15 · docs(tests-w): chain report under 400 words (1701af80b:report.md)

## Rows to file
- **`seat`, `_peer_cycle` and `close_round` replay production by hand**: loop_session's connector holds its own sends, so a remote cell has no bound, and the world cannot measure it. Action: loop_session builds the real bound. **Rides with:** tests-w's successor.
- **One guard is tested once per door**: not built this chain.
- **Two wounded legs move under load**: kept open; not reproduced on 4 cores.
## Symptoms
- `mint.py` starts a background rates refresh per fact. A daemon thread can outlive its fact's home.
- `tmp_path` retention keeps every fact's home on disk.

## 2026-11-15 · docs(small-4): report within its word limit (0931702f1:report.md)

### Rows to file
### Symptoms
- `measurement_archive.py::rewrite_cell_file`: a reclaim that empties a cell file leaves a 0-byte file, which `keys(CELLS)` still lists. Suspected root: `rewrite_pair` has no "delete when empty" outcome.
- `webapp/components/account/ArchiveCompactionControl.tsx`: the dataset is free text because no served read lists the account's datasets. Suspected root: there is no dataset listing `ReadKind`.

## 2026-11-15 · docs(small-4): chain report (498720a4f:report.md)

### Rows to file
### Symptoms
- `measurement_archive.py::rewrite_cell_file`: a reclaim that empties a cell file leaves a 0-byte file, which `keys(CELLS)` still lists. Suspected root: `rewrite_pair` has no "delete when empty" outcome.
- `webapp/components/account/ArchiveCompactionControl.tsx`: the dataset is a free-text field because no served read lists the account's datasets. Suspected root: there is no dataset listing `ReadKind`.

## 2026-11-15 · docs(rk-rest): final report (d50c5e311:report.md)

## Rows to file
- Cut (d4): **A per-cell criterion naming a term no cell carries compiles.** The row stays in the ledger. `compile_scorer` would need the campaign's owners (connector terms, report keys, observations), but none of the `scorer_of` readers outside Owns holds them: `runner/bench.py:763,827`, `scoring/closed_rounds.py:37`, `mask/load.py:204,230`, `evidence/head_to_head.py:410`, `initialization/scoring_scope.py:57`. An optional owners argument would be a flag. The owners must ride the scoring spec first.
- Cut (d3): **A check-in's cycle plan selects beside its landing's refusal.** The row stays. Planning before landing needs a `Session` over an unlanded definition. `wiring.py::init_services` → `dataset_access.py::resolve_dataset`, `session_bank` and `dataset_experiment` all read the landed definition from disk. The prerequisite is a session over a draft-held `ResolvedDataset`.
## Symptoms
- `scripts/gate.py` `specs`: red once under `--changed`, green on rerun. Suspected: an order or cache dependency in the check.
- `surface-ledger` measures `package_lines` against the merge-base (110053), while BASELINE read 110058. Suspected: a baseline left behind by an earlier raise.

## 2026-11-15 · docs(report): ws-2 report (ad371645e:report.md)

## Rows to file
- **A `candidate:` subject names no round, so one arm key at two rounds of a cycle reads the latest** (cut: `tests/` spells the grammar): `cycle_paths.py::Subject.key` spells `candidate:<campaign>/<cycle>/<arm>`, and `evidence/read.py::_candidate_point` resolves the newest scored round. In the walk world, "Compare C1.1 with C0" opens a channel that reads C2.1 · round 2 (same arm `4fe6d83270e5bef5`). The cells filter (`candidate_id`) and `ScenarioEdits` keys share the gap, and `mask/tree_lens.py::_Overlay.readings` keys on (cycle, arm). Action: the subject carries the round, keyed as the hierarchy address. **Re-test:** compare a same-key r1 arm; its channel reads round 1.
## Symptoms
- An inner campaign in Compare asks `GET /hierarchy/<inner campaign>` and `/campaigns/<inner>/pipeline` with no chain: 404, so the walk's problem check goes red. Suspected root: the compare channel resolves its run by a bare `rootCampaignId` (`ChannelCards.tsx::campaignAt`).
- The freed tree's labels collide around an inner lane (`R2 justlogic/d234` over `C2.1`; the `θ` labels overlap). Seen in `shots/2-same-key-freed-tree-1280x800.png`.
- Once the tree is freed, the Candidates card sits at half width beside empty space (`dashboard-layout.css:8` auto-fit tracks).

## 2026-11-15 · docs(report): tv-2 chain report and the 390 px stage shot (b49c911a4:report.md)

## Rows to file
- The main workspace follows the exhibit too, and its run controls (pause etc.) 404 there, an I3 break. Gate them on `said.key`.
- The account feed does not re-ask exhibit reads when the host's store moves.
## Symptoms
- The door sweep reads the SDK-private `_server_instances`. A session that expires idle leaves only at the door's next request.
- `ActiveSessionResponse` carries both `hop` and `said` only in the exhibit case.

## 2026-11-15 · docs(report): small-3 report (91b558be5:report.md)

## Rows to file
- **A round opens several `RoundContext`s** (small-3): `runner/round.py::walk_round` builds one for the draw and one after it, `execute_round` and `post_round` build a `seat` for the controller, and `settle_owed_outputs` builds `at_prior`. Each has its own `walks`, so a read through one is not seen by the others. Root: one context per round, opened with its draw. **Re-test:** `git grep -c "RoundContext(" -- promptpotter/application/runner` (7 today).
The tune-1 line for "panel" in prompt text was added to the "slot" row in link 2.
## Symptoms
- `pipeline_resolve.py::laid_config` and `campaign_config.py::CycleConfig.laid` both filter `optimization.nodes` stamps with `path[:2] == ("optimization", "nodes") and len(path) == 5`. One predicate is spelled twice; suspected root: no named accessor for "a node-config stamp".

## 2026-11-15 · docs(money-2): chain report (3bcaf45cb:report.md)

## Rows to file
- **A run's ambient state rides three carriers, not two**: `infrastructure/run_scope.py::RunScope` names no plan or sender. It carries `round`, and the node context carries `round` as well. `shared/measurement_context.py` holds two more ContextVars (`_MODE`, `_MEASURED`), against application `CLAUDE.md`'s "never a second ContextVar". Root: they ride `RunScope`, and the "Two carriers" row is rewritten to what is true. **Rides with:** a run-scope chain. **Re-test:** `git grep -c "ContextVar(" -- promptpotter` is 1.
## Symptoms
- `scoring/measurement_log.py:464`: `campaign_scorer` returns `None` for an absent campaign, and this caller refuses it with its own `NotFoundError`. Suspected root: `campaign_scorer` should take a `Campaign`. Its callers include `cycle_reads.py`, which addr owns.

## 2026-11-15 · docs(mkey-fix): the fix pass's report (0b213ec5d:report.md)

## Rows to file
## Symptoms
- `tokens.py::_tokens`: one row that fails validation blocks every bearer on the install, because the whole file is refused. The likely root is a per-row load.
- `promptpotter/application/account.py::mint_token` and `::account_tokens` each spell the host's refusal (`identity.host`). These could be one predicate.

## 2026-11-15 · docs(mkey): the chain's report and the account-tokens shot (b7caf17db:report.md)

## Rows to file
They are already filed under `### batch/29 · mkey`:
- **An account's own act has no rung, so a blocked account and a delegate press it**: `set-user-settings` and `accept-terms` carry `capability=None` (`command_kinds.py::CommandKind`), and the dispatcher's one check (`dispatcher.py::CommandDispatcher.open`) skips a `None`, so a blocked account (empty set) sets its preferences and a delegate accepts the Terms for its delegator: a consent another person gave. Root: the account's own act is a capability of the owner set (`shared/identity.py::OWNER_COMMAND_CAPABILITIES`), never `None`, so the one check covers it. Sub-threshold until a delegate is granted on the box. **Re-test:** `sum(k.capability is None for k in CommandKind)` is 0 (2 at mkey's tip).
- **A command's target resolves before its capability is checked**: `CommandDispatcher.open` runs `addressed` (a campaign prefix's resolution, a 404 of its own) before `require_capability`, so a caller without the rung learns which of its own prefixes resolve. Root: the capability check first, as nothing it reads depends on the target. Sub-threshold: it crosses no tenant. **Re-test:** a caller lacking `campaign.lifecycle` gets one refusal for a resolving and an unresolved prefix.
## Symptoms
- **Delegate cancel.** The one follow rule widens reads: a delegate who reads the account can now follow, and cancel by command id, the owner's queued launch. That is the intent of "whoever may reopen may follow", but it is a widening.
- **Possible one-edit fix.** The second filed row (capability before target) looks like a one-edit swap in `CommandDispatcher.open`. It was left filed, per "fix one only if one edit"; the orchestrator can take it.
- **`useCommand` silently drops a send made inside `then`.** That is how 3″ arose. Another surface chaining a relist this way would fail the same way.
- **The name stays in the field after a mint.** The "New token name" field keeps the name after the mint commits (`CommitInput value=""`). This is cosmetic.

## 2026-11-15 · docs(mcp): the chain's report (742c756bb:report.md)

## Rows to file
- **A held task on a 2026-07-28 wire is never asked for its input**: `presentation/mcp/server.py::host_server`'s `tasks/result` elicits only on a handshake-era wire. On a modern one, server→client requests give way to `InputRequiredResult` and the tasks extension's `tasks/update`, which nothing here answers. Root: the hold's `HostHold` rides the modern task's input requests. **Rides with:** the next mcp link. **Re-test:** a 2026-07-28 client holding a task at its arms gate receives the hold's input request and answers it with `tasks/update`.
## Symptoms
- `presentation/mcp/server.py::HttpDoor._managers`: a manager per authority is kept until shutdown, so memory grows with the number of distinct tokens. Suspected root: no idle sweep of managers whose sessions all ended.

## 2026-11-15 · docs(faults-2b): the report, under 400 words (d8075170a:report.md)

## Rows to file
- **A line file's kind is passed by its caller:** `data_root` imports `read_model` (a cycle), and `KINDS` holds no `FileKind`. Move the kind table below both modules.
- **`SampleFoldRow.unscoreable` is read by nothing:** its reader was the skip log. Serve it, or delete it with a sample-fold wipe.
## Symptoms
- An inline launch queued on a full machine prints nothing.
- machine-status `unloadable` has no CLI line.

## 2026-11-15 · docs(faults-2b): the chain's report (8f3529ec6:report.md)

## Appendix: Rows to file
- **A line file's kind is passed by its caller** (faults-2a, kept): `data_root` imports `read_model`, which is a cycle, and `KINDS` holds no `FileKind`. Fix: move the kind table below both modules.
- **`SampleFoldRow.unscoreable` is read by nothing**: its one reader was the skip log. Serve the count where the sample index is read, or delete the field with a sample-fold wipe.
## Appendix: Symptoms
- An inline CLI launch waiting on a full machine prints nothing.
- machine-status `unloadable` has no CLI line.

## 2026-11-15 · docs(faults-2a): the arc's report (1c0c436ac:report.md)

## Rows to file
- **A loop refusal carries no code** — `dispatcher.py::RejectedError` is no `PotterError`, so a refused loop command serves no code or acts. Root: a `PotterError` subclass.
- **A refusal path reads banks** — `cycle_addressing` and `no_dataset_hint` call `list_datasets`, which reads every bank, to word a refusal. Root: a listing that holds no rows.
- **`ActValue` widens twice in TS** — the generator emits `string | number | number | boolean`. Root: the generator dedupes a union.
- **Harbor's other refusals are `ValueError`s** — only the pin-roster one is a refusal with acts.
- **`new` spells its dataset twice** — `--dataset-name` beside the positional.
- **A line file's kind is passed by its caller** — `LineIndex.of(..., kind)` defaults to `LEDGER`. Root: the kind read off the path's `data_root.py::KINDS` row.
## Symptoms
- `offline_run.py` in a base worktree needs `PYTHONPATH=<worktree>`.

## 2026-11-15 · docs(addr-2): final report (19670d8e1:report.md)

## Rows to file
- **The freed tree and the channel map still key a candidate on `facts.individual`.** `Forest.tsx`'s `altIds` keys on `n.candidateId`, and `ChannelMap.tsx::ArmedActions` builds its subject from `armed.facts.individual`. One arm key re-proposed on two branches (seen: `c8c1b433…` at r2 of the root and at r3 of fork ·c56947) lights both. Key both on `address`, the way link 3 keys spines.
## Symptoms
| `file:line` | Symptom | Suspected root |
|---|---|---|
| `webapp/components/candidates/Forest.tsx:431` | `alt` matches by individual id, so one reused arm key lights both branches | the row above |
| `webapp/components/compare/ChannelMap.tsx:125` | After arming C3.1 of fork ·c56947 in channel 1's map, the card's "Picked" fold says "reads at C3.2" while its main line walks C3.1's chain | the subject is built from the individual; the fold's "reads at" follows the channel, not the pick |

## 2026-11-15 · docs(addr): chain report (6bc02cbc5:report.md)

## Rows to file
- **A cycle's family root is still parsed from its id**: `infrastructure/store/layout.py::root_cycle_id` regexes `_fork_`, read at `campaign_store/store.py` `_stub_deletion_blocked` / `stub_forks` and `fork_siblings.py::_sibling_id`. A campaign's root is `Campaign.root_cycle_id`, and "is a root" is the mint's `ForkSpec` (`CycleIndex.minted_as`). Action: the stub checks read the index they already load, and the sibling id takes the campaign's root. **Re-test:** `git grep -n "_SIBLING_SEP_RE\|root_cycle_id(" -- promptpotter/infrastructure` is empty.
- **The reaper swallows an unreadable cycle**: `jobs/reaper.py::_reap` has `except FileUnreadableError: pass`, so a cycle the sweep cannot read is neither reaped nor reported. Action: serve it as a fault of the sweep. **Re-test:** `git grep -n "pass$" -- promptpotter/application/jobs/reaper.py` is empty.
## Symptoms
- `jobs/reaper.py:65`: an error is swallowed into nothing; the suspected root is the sweep having no fault channel.
- `webapp/components/candidates/lineage-tree.ts:84,248`: crown and winner are read off `election.selected`, which only the tree's copy clears for a retired arm (row 6).

## 2026-11-14 · docs(xconn): chain report (aeada6120:report.md)

## Rows to file
- **An identity re-key moves offline decisions**: an arm's content id carries identity-only keys (`pipeline_resolve.py::identity_layer`), so a pure re-key re-draws the cells raced. Action: derive an arm's id from what it sends, never from its identity keys. **Re-test:** the `f70980f` run against base moves only `backend_revision` leaves.
## Symptoms
- `connector.base_url` was a global standing in for a value keyed by dataset.
- Link 3's ceiling raises are stand-ins. The real fix is the row above.

## 2026-11-14 · docs(ws): report and shots (7c71073ca:report.md)

## Rows to file
## Symptoms
- `webapp/components/compare/EvidenceCharts.tsx:83` and `webapp/components/dashboard/scoring/CostStrip.tsx:50` paint canvases with no keyboard path, against `components/CLAUDE.md:9`'s rule that "a painted surface is a control". Suspected root: `ui/ChartReading`'s hook could take arrow keys over the columns.
- The walk world's spend is all `$0.0000` (offline backend), so the CostStrip reading can only be seen working on zeros there.

## 2026-11-14 · docs(webb-5a): report and pipeline-stack shots (8136c7778:report.md)

## Rows to file
## Symptoms
- `scripts/gate.py:1800`: `_editable_target` calls `Path.from_uri` (3.13+). Yet `.githooks/pinned-python` tells a desk to "build it with `python3 scripts/gate.py`", which fails on a 3.11 `python3` once `.venv` exists. Suspected root: the bootstrap path assumes the pinned interpreter's version.
- `webapp/playwright.config.ts`: two names for one value. The config hands `PP_E2E_WALK_WORLD` to `serve.mjs` as `PP_E2E_WORLD`, and the cold server gets `""` so it does not inherit one. Suspected root: `serve.mjs` takes its world from the environment, not as an argument the way it takes `--auth` and `--backend`.

## 2026-11-14 · docs(valid): chain report — stopped after link 1 (a676362b7:report.md)

## Appendix: Rows to file
## Appendix: Symptoms
- **`tests/test_archive.py:1515`**
  - Symptom: a test inserted under another test's `usefixtures` decorator left that test unscoped. Fixed in `f923f17`.
  - Suspected root: a fact's scope rides a decorator instead of the fact itself.
- **`promptpotter/infrastructure/store/campaign_store/store.py::mint_fork_cycle`**
  - Symptom: the rewound `RoundEnteredRecord` is still a second append.
  - Suspected root: `_Standing`, activity, the audit trail and the round buffer each read entry off `RoundEnteredRecord`, never off the mint.

## 2026-11-14 · docs(rn-conn): run report (da1a2cf55:report.md)

## Rows to file
The NodeCall row now reads:
> **The pipeline carrier is typed only at its door**: both connectors now read `NodeCall`'s fields, but `PipelineNode.current_config` is a `dict` (`domain/pipeline_parsing.py:381` builds it from the validated call), so `pipeline_schema.py` (7 reads), `pipeline_resolve.py:297` and `free_tier.py:137` read the model by string; `measurement_log.py::_span` and `wounds.py:184` read a laid dump the same way. The re-test also counts carriers that are no `NodeCall` (an OpenAI request body in `openai_compat.py`, a record payload in `audit_trail.py`, a model menu, dspy's `LM.kwargs`), which the action leaves alone. Action: type `current_config` as `NodeCall` and read its fields. **Re-test:** `git grep -cE "current_config\.get\(" -- promptpotter` is 0.
## Symptoms
| Where | Symptom | Suspected root |
|---|---|---|
| `runner/inner/tasks.py:319` | Any rename in the module re-keys every banked L4 cell. | The estimator hashes whole module source, not what decides a number. |
| `connectors/harbor.py:229` | `skill_delivery` defaults to `agent_skill` in code. | Same §0.5 gap: the default belongs in the dataset's file. |
| `connectors/dbllmbench.py:90` | `_DB_DEFAULT_URLS` maps a db to a URL in code. | The address is operator config (`DBLLMBENCH_DB_URL`). |

## 2026-11-14 · docs(report): webb-5b report and shots (a54ee2f7d:report.md)

## Rows to file
## Symptoms
- `promptpotter/application/runner/output.py:468`: the markdown scoreboard still names its lift column from the first arm's `cell_set`, which is the same first-arm shape `RoundFileView` had. Suspected root: that table could read each arm's served `vs_reference_name`.

## 2026-11-14 · docs(report): webb-4 report within 400 words (fa72529e6:report.md)

## Rows to file
- **A tap on a sidebar row opens its card**: `.unit-library-item` is no control to `ownPress`, so the tap that leaves the list screen also opens the row's card and its `/storage` read. **Re-test:** the touch walk's `hideSidebar` asks no `/storage`.
## Symptoms
- Compare at 375: the remote bar floats above the tab bar (present at base).
- A ChannelMap deselect snaps back to the seed.

## 2026-11-14 · docs(report): webb-4 report trimmed (45cfb9819:report.md)

## Rows to file
- **A tap on a sidebar row opens its card**: `.unit-library-item` is no control to `ownPress`, so leaving the list screen opens the row's card. **Re-test:** the touch walk's `hideSidebar` asks no `/storage`.
## Symptoms
- Compare at 375: the remote bar floats above the tab bar (base too).
- A ChannelMap deselect snaps back to the seed.

## 2026-11-14 · docs(report): webb-4 chain report and phone shots (6189e20c2:report.md)

## Rows to file
- **A tap on a sidebar row opens its card**: `ui/HoverCard` opens on a touch press of the row itself (`.unit-library-item` is not a control), so the tap that leaves the list screen also mounts the row's card and its `/storage` read. Root: a row whose press navigates is a control to `ownPress`. **Re-test:** the touch walk's `hideSidebar` asks no `/storage`.
## Symptoms
- Compare at 375: the remote bar floats about 120px above the tab bar. Present at base.
- After a ChannelMap deselect, the channel shows its seed again (`picked ?? seed`), never an empty pick.

## 2026-11-14 · docs(report): valid-3 report (5bda00d4e:report.md)

## Appendix: Rows to file (filed)
- One fact, two append loops
- `HeldRun.of`'s second config read
- A fork's share lands at its landing
## Appendix: Symptoms
- `NodeCall`'s plain serializer ignores `include=`, so `call_settings` returns prompt fields too. It is left alone because a fix re-keys calls.
- `prompt_edits` served call keys as `edited` until link 4.

## 2026-11-14 · docs(report): valid-2 stopped at link 1 on the shrink rule (e94b062bd:report.md)

## Appendix: Rows to file (filed in d75eb7e)
- One fact, two append loops (`append_jsonl` vs `CycleEventLog.append`)
- `HeldRun.of`'s second config read
- A fork's share lands at its landing (a `set-limits` filed in the window re-shares without it)
## Appendix: Symptoms
- An import cycle (`fork_siblings → mint_and_start → mint → resume → fork_siblings`) forced `OperatorFork` into `mint_and_start.py`.
- Moving the unlanded-fork check into `_admitting_before` broke the wallet test, so it stays only in `_refuse_unshared`.
- `/tmp/base` imports the main tree (editable install). The base run predates all edits, so its result stands.

## 2026-11-14 · docs(report): small-2 chain report (2e364e4c3:report.md)

## Rows to file
## Symptoms
None found.

## 2026-11-14 · docs(report): small-1 chain report (c1f1bb9bd:report.md)

## Appendix: Rows to file
## Appendix: Symptoms
- `promptpotter/infrastructure/llm/spend_book.py:351`: a third remainder shape, a signed `(usd, tokens)` tuple next to `Budget.less`, clamped again by `_at_least_nothing` (`:602`). Suspected root: an overdrawn state needs a typed form (`Budget` cannot be negative).
- `paper/bbeh-comparison/bbeh_potter_runner.py:1`: the docstring pins `promptpotter==0.8.20`, but the ledger fixes the file against the current API (`init_services`). Suspected root: no rule for which tree the paper harness tracks.
- `promptpotter/application/cycle_files.py:116`: before this chain, a directory path reached `read_bytes_optional` (now refused as not found). Suspected root: `stat_optional` answers for any inode kind.

## 2026-11-14 · docs(report): model-lines chain report (f86474287:report.md)

## Rows to file
## Symptoms
- `docs/methods/candidate-elimination.md:130,132,164`: the LEVI and GEPA rows still say "the bench's one model". That is still true, but they could link `optimization.call_target`. This is a doc nicety, not debt.

## 2026-11-14 · docs(report): bkeys report under 400 words (40079d49a:report.md)

## Rows to file
- **An L4 cell's instrument hashes a hand-picked subset, not what its session loads** (✓)
  - The pin's `InnerTaskSpec.instrument` (`runner/inner/tasks.py::InnerTasks`: node config, campaign identity, estimator) skips what the inner session loads live (`wiring.py:228-307`: `origin.yaml`, the `source:` cut and recipe, `task.md` framing, the pipelines walk, the backend revision).
  - Pins freeze at outer run init while the session loads at each spawn.
  - Over-keying: `Seat.id`, a label, sits inside the pin; so do axes key order and mixed-manifest chain order.
  - Root: the instrument hashes the seat's `MintPlan.origin_hash` plus its `measured_address`, resolved at the outer run's init (one async resolve and one backend read per seat), which `dataset_access.py::dataset_experiment`'s synchronous resolve cannot host.
  - **Re-test:** an edit to an inner `task.md` moves the pin of every seat on that dataset. (Already in the ledger.)
- **A seat's `n_rounds` takes `ge=1` under a panel floor of 2**
  - `domain/cells.py::Seat.n_rounds` overrides `max_inner_rounds` (`ge=2`) per seat, so one seat can still run a single inner round.
  - Action: `ge=2`.
  - **Re-test:** a seat declaring `n_rounds: 1` is refused.
## Symptoms
- `research/datasets/promptpotter-self/e2e/dataset.yaml:30`: the browser walk's `usd: 0.05` stop was sized for one inner round. Suspected root: depth 2 doubles inner spend, so the paid `webapp/e2e/spend/l4.spec.ts` may halt on its ceiling. It was not run (paid).
- `promptpotter/application/runner/inner/spawn.py:217-250`: a continued inner campaign resumes its stored declaration. Suspected root: raising `max_inner_rounds` keeps the inner id (by design) but may not extend the stored round budget, so "its campaign continued" may stop at the old depth.

## 2026-11-14 · docs(ren): chain report (2cf5593a3:report.md)

## Rows to file
- **A behaviour key moves on a rename**: `nodes.py::behaviour_key` and `tasks.py::estimator` hash source text. Action: hash what decides, not the spelling. **Rides with:** digest owner.
- **`propose` hands samples only gepa reads**: twelve of thirteen ignore `_panel`. Action: gepa reads it through `NodeContext`, and the parameter goes.
- **"slot" survives in LLM-facing text**: prose, `{{slot}}` tokens, `not_a_slot`/`stable_slot`. Action: rename as a measured prompt change.
- **"panel" overloaded**: `rr.panel` and the origin panel. Action: each named for its holder.
- **Fold `seed=`** in read_model/live_dashboard. Action: `start`.
## Symptoms
- `docs/specs/roadmap.md:529`: `CycleSeed`. Suspected root: no probe covers roadmap.

## 2026-11-14 · docs(rel): chain report (8f006b21f:report.md)

## Rows to file
- **The dev loop still serializes on Vitest** (left in the ledger, re-tested): `gate.py::_VITEST_SLOTS = 4` (`--maxWorkers`, the comment at `:1139`) caps the gate's own pool. It does not cap load from outside the gate lock, which can still push a worker past Vitest's 90 s start. **Re-test (desk only):** five builders plus one full gate, no reap.
Not this chain's: the three landing rows ("The landing stamp is unordered", "A landing waits on every live producer…", "`landing_key` misses a body change") ride with `rel`, not rel 1/2. "Run data is stat'ed bare" is local and last. rel 3 is unverified. All are untouched.
## Symptoms
- `webapp/e2e/walk/cells.spec.ts:13` (base): a seat-cell row's accessible name starts with its vendor mark's `img` alt ("OpenAI justlogic/d234 …"). Every `^label` row locator misses it. Suspected root: the mark is not `aria-hidden` in the sidebar row.

## 2026-11-14 · docs(rec): chain report (ad1a39670:report.md)

## Rows to file
- **Generated `last` record classes live in module globals so a snapshot can pickle them**: `store/campaign_store/ledger_scan.py::_last` builds a class at runtime, and `globals()[made.__name__] = made` registers it. Action: the last-wins records become declared classes, and the registration goes. **Re-test:** `git grep -c "globals()\[" -- promptpotter/infrastructure/store/campaign_store/ledger_scan.py` is 0.
## Symptoms
- `domain/round_diagnostics.py::RoundDiagnostics.error_rate`:
  - Symptom: it stays a `float`, so an empty round reads 0.0.
  - Suspected root: the rate is not `None` when there are no cells.
- `MemberAddress.pass_role`:
  - Symptom: no reader, but it rides the round-file readings hashed into the trace, so deleting it moves behaviour keys.
  - Suspected root: the trace hashes a display field.

## 2026-11-14 · docs(readings-r): report (4b6ecc2b7:report.md)

## Rows to file
All three stand at the tip and are already in the ledger (link 4 wrote them, `code-debt-cleanup.md:693-709`): **A lift is read two ways** (`ruler.py::theta_lift` still returns a bare `Estimate`), **φ's binariness is inferred from the values** (`rasch.py::_dispersion`), **The warm gate counts no se_δ**.
## Symptoms
- `promptpotter/application/evidence/comparison.py:71`: `binary=False` hard-coded. Still true.
- `promptpotter/domain/crowning_bar.py:104` `CrowningBar.separates`: never separates graded. Still true.
- `promptpotter/application/optimizers/potter/pobb.py:30` `EliminationGate`: a one-member enum. Still true.
- `validators.py:395`: one bar. Closed.
- `VerifyReading.held` treats a recorded level as known. Still true.
- New: `scripts/complexity_ledger.py::_without_types` is a TS heuristic. Any Python `type X =` line ends the strip at the next `;`, so any ledger row that applies it to Python reads garbage. Fixed for `_unread_fields` in link 1; no other row is affected.
- New: the original report's "requests 7/9, healed 10/14" is the whole arc vs base, not link 3 alone. Link 3 alone is 4/9 and 7/14.

## 2026-11-14 · docs(readings): the readings chain report (17cf811d6:report.md)

## Rows to file
- **A lift is read two ways**: `paired_reading.py::Estimate` is the one level and lift every surface renders, and `paired.py::_lift`'s test is the measurand's (exact sign where it is declared binary, paired t where graded), but a θ lift is a bare `Estimate` (`ruler.py::theta_lift`), never a `MeasuredLift`, so rk-dec's bar and rk-opt's lift test read two shapes. Action: θ returns `MeasuredLift` beside the paired kinds; θ and paired t stay distinct measurands; CAPO, GEPA and LEVI keep their papers' bars. No `EstimatorSpec.kind`: the measurand's declaration already picks the paired test. **Rides with:** rk-dec. **Re-test:** the chain's census lists the estimate readings; one remains.
- **φ's binariness is inferred from the values** (`rasch.py::_dispersion`) against the DECLARED rule (`Measurand.binary`; one 0.999 for 1.0 cuts θ SE 0.546 → 0.324). But the response θ reads (`objective`) is declared graded on every scorer (`scoring.py::ROW_GRADES`), so reading that declaration fits φ on every binary dataset, giving φ = 0.10 (θ_se 0.28 where the information gives 0.89). Root: the scorer declares whether its objective is two-valued, and `fit_theta` takes that declaration. **Re-test:** a 0.999 for a 1.0 moves θ_se by less than 1%.
- **The warm gate counts no se_δ**: the young-ruler joint MAP still shrinks δ̂ inward (slope 0.30 at 3 individuals per cell), and the warm gate counts rows alone. Root: δ by MML, or the warm gate reads se_δ.
## Symptoms
- `promptpotter/application/evidence/comparison.py:71`: `metric_measurand` hard-codes `binary=False`. Root: `MetricSpec` declares no binariness, and a replicate cell mean is not binary.
- `promptpotter/domain/crowning_bar.py` (`CrowningBar.separates`): never separates a graded measurand. Root: the same missing binary declaration.
- `promptpotter/application/optimizers/potter/pobb.py:30` `EliminationGate` (one-member enum, wired through lens and `mask/record.py:31`): a flag standing in for a concept with one value. Root: the enum is unneeded and should be deleted along with its wiring.
- `validators.py:397-398`, always true: **closed** in link 3. `lost_ideas` now reads one bar (`validators.py:395`, headline `side == "below"`).
- `VerifyReading.held` treats a recorded level as known. Root: the recorded level carries no band, so the verify compares against a point.

## 2026-11-14 · docs(narr-3): report (c000b9002:report.md)

## Rows to file
- **A phase control still appends around the scope.** `application/run_phase_control.py` keeps one `campaigns.append(`, outside the brief's grep. Route it through `declare_on_cycle` or name why a pause/resume control writes outside a run scope.
- **Muted and unbound read the same.** `llm/telemetry.py::active_cycle_ledger` returns None for both a muted scope and UNBOUND, so `verb_ledger` under a muted scope opens a fresh ledger. The scope should report the three states distinctly.
## Symptoms
- `promptpotter/application/run_phase_control.py`: a direct `campaigns.append(`. Suspected root: the one-door rule is unenforced outside runner/.
- `promptpotter/infrastructure/llm/telemetry.py::active_cycle_ledger`: mute is indistinguishable from unbound. Suspected root: `None` doubles as the mute sentinel.

## 2026-11-14 · docs(narr-1): report within 400 words (b5cc207af:report.md)

## Appendix: Rows to file
- **The live dashboard keeps a third hold tracker**: `live_dashboard/projection.py::_holds` keeps the last `ProviderHoldRecord` per sender. Unlike `narration.py::Narration`, it is never cleared at a run-phase declaration. Action: the dashboard reads the narration's holds. **Re-test:** `git grep -c "_holds" -- promptpotter/infrastructure/projections/live_dashboard` is 0.
- **`_Standing.value` still builds a `StandingArm` per arm of every round per call**: closed rounds' cells are shared now, but the tuples and the outer dicts are O(R·A) per poll. Action: freeze a closed round's `StandingArm`s at its close. **Re-test:** a value at round R builds tuples for the live round only.
- The wire half (`served_rounds`, `dash.rounds`) stays in the retitled row, as the brief orders.
## Appendix: Symptoms
- `promptpotter/infrastructure/projections/live_dashboard/round_buffer.py:28`: `RoundBuffer` wraps `Narration`'s arm API (`announce`, `take`, `enter`) one call deep. Suspected root: the buffer is a view of the narration's live round, not a holder of its own.
- `promptpotter/application/views/readout.py`: the `[ARM GATE]` block and `activity.py::_arms_gate` word one hold twice. Suspected root: the gate is no narration row (narr 2's typed act).

## 2026-11-14 · docs(narr): trim narr-2 report under 400 words (198139ea9:report.md)

## Rows to file
- `RunPhaseInfo.no_action` and `parked`: two refusal words per phase; faults-1's `Refusal` could absorb both.
- Live dashboard keeps `_race` / `_race_standings` beside the narration.
## Symptoms
- The admission re-test campaign lacked a definition (`ds1`), so its "before" was already refused.

## 2026-11-14 · docs(narr): narr-2 chain report (7184dc17a:report.md)

## Rows to file
- `RunPhaseInfo.no_action` and `parked`: two refusal words per phase; faults-1's `Refusal` could absorb both.
- Live dashboard keeps `_race` / `_race_standings` beside the narration.
## Symptoms
- The admission re-test campaign lacked a definition (`ds1`), so its "before" was already refused; the unjoined `RunAdmission` shows base's `resume`.

## 2026-11-14 · docs(lw): chain report and phone shots (127db2457:report.md)

## Rows to file
- **The theme key is spelled twice**: `webapp/public/theme-init.js:4` reads `"promptpotter.theme"` beside `lib/storage-keys.ts::STORAGE_KEYS.theme`. The pre-paint script cannot import the table. Action: the build injects the key, or the script is generated from the table. **Re-test:** `git grep -c '"promptpotter.theme"' -- webapp ':!webapp/lib/storage-keys.ts'` == 0.
- **A stale roadmap citation**: `docs/specs/roadmap.md:522` cites `lib/workspace.tsx`, which is now `lib/state/workspace.tsx`. The roadmap is not mine to edit.
## Symptoms
- `webapp/lib/address.ts:6` imports `./derivations/search-point` past the barrel (type-only). The suspected root is the same cycle reasoning the hierarchy row closed.
- The `export` on these types is dead (each is used only inside its own file): `chat-thread.ts::StoredItem`, `poll.tsx::FailureOf`, `lineage.tsx::CampaignTree`, `compare-selection.ts::ChannelView`/`Fold`, `spend.ts::SpendLine`, `round-timeline.ts::StepOrder`, `sample-trajectory.ts::SampleMovement`, `node-config.ts::parseNested`, `useConnector.ts::BackendReading`.

## 2026-11-14 · docs(lite): chain report (9dc8ecb7f:report.md)

## Appendix: Rows to file
- **The capability override's fate is RUN, though it is authored** (`store/data_root.py`, row "model capability override"): the landing drops an override no model types, while its reader refuses it as operator config (`FileKind.STORED_CONFIG`, "Fix the file"). Action: decide whether an authored workspace file is a SURVIVOR (migrated) or stays RUN. **Re-test:** the row's `Fate`.
## Appendix: Symptoms
- `llm/pricing.py::_fetch_route_ceiling` and `::_host_tiers`: still walk OpenRouter's endpoints payload with `.get` and `_as_tokens`. It is no data-root file, but it is the same shape of bug — suspected root: the endpoints payload has no model.

## 2026-11-14 · docs(layers): trim the report under 400 words (951fb2d76:report.md)

## Rows to file
- **Store hub ↔ part knots:** `stores` ↔ `session_pointer`, `account_spend`, `account_trail`, `archive_queries`, `dataset_access`, `spend_book`, `run_scope`, `connectors/protocol`. A narrower parameter cannot close these type cycles; each part needs a protocol module.
- **Domain knots:** `spend` ↔ `run_records`, `sample`, `pipeline_schema`, `pass_kinds`. Moving the scoring fold re-keys the estimator.
- **Bench ↔ nodes protocol cycle:** bench still reads `CycleRead`, the member protocols and `NODE_MEMBERS`.
- **Playwright touch flake:** the OFFLINE note overlays Compare.
## Symptoms
- `promptpotter/application/optimizers/nodes.py`: bench imports optimizers. Suspected root: the plugin contract and the round types share a module.
- `webapp/e2e/walk/records.spec.ts:4`: the click is intercepted. Suspected root: the OFFLINE card is not pointer-transparent.

## 2026-11-14 · docs(layers): run report for the layers chain (29676152c:report.md)

## Rows to file
- **Store hub ↔ part knots:** `campaign_store`/`stores` ↔ `session_pointer`, `account_spend`, `account_trail`, `archive_queries`, `dataset_access`, `spend_book`, `run_scope`, `connectors/protocol`. They form type-only cycles that a narrower parameter cannot close; each part needs its own protocol module.
- **Domain knots:** `spend` ↔ `run_records`/scoring, `sample`, `pipeline_schema`, `pass_kinds`. Moving the scoring fold re-keys the estimator through `metrics.py`.
- **Bench ↔ nodes protocol cycle:** bench still reads `CycleRead`, the member protocols and `NODE_MEMBERS` from the plugin contract.
- **Playwright touch-click flake:** `records.spec.ts:4:3`, where the OFFLINE note overlays Compare.
## Symptoms
- `promptpotter/application/optimizers/nodes.py`: bench imports `optimizers` at runtime. Suspected root: the plugin contract and bench round types share one module.
- `webapp/e2e/walk/records.spec.ts:4`: the touch click is intercepted. Suspected root: the OFFLINE card is not pointer-transparent.

## 2026-11-14 · docs(la): final report (a811da955:report.md)

## Rows to file
- **`run_state.py` still reaches `campaign_store/` transitively**: `infrastructure/holders.py:15` imports `campaign_store/ledger_scan.py`, so the run-state leg imports neither directly but still loads it. Root: what holders reads off the ledger belongs in `store/read_model.py`. **Re-test:** importing `run_state` alone loads no `campaign_store` module.
- **The CLI prompts for an API key under `--json`**: a verb run with no provider key asks on the terminal before it answers, so a `--json` caller gets an interactive prompt rather than a Refusal. Root: under `--json`, a missing key is a typed refusal. **Re-test:** `resume --json` with no key set exits 1 with Refusal JSON on stdout and nothing read from stdin.
## Symptoms
- `promptpotter/infrastructure/holders.py:15`: a run-state reader imports campaign_store; suspected root: holders' ledger fold sits in campaign_store.
- `docs/specs/roadmap.md:241`: stale cite `default_claim.py::is_host`; root: link 2's merge into `identity/accounts.py` (roadmap is not this chain's to edit).
- The CLI's API-key prompt under `--json`; suspected root: the key check runs before the `--json` refusal path.

## 2026-11-14 · docs(hubs): chain report (15d780223:report.md)

## Rows to file
- **`write_resolved_run` warns policy into a log**: `campaign_store/store.py::write_resolved_run` logs a moved panel as a `logger.warning` and appends anyway. That is a state the run enters but reports nowhere. Action: the moved panel lands as a field of `ResolvedRunRecord`, read by the readout. **Re-test:** no `logger.warning` in `write_resolved_run`.
- **An 11-arm `case: pass`**: `domain/activity.py`'s fold matches 11 record kinds only to ignore them, beside `assert_never`. Action: the fold declares the kinds it reads (`records`) and is fed nothing else. **Re-test:** no `pass` arm in it.
- **`ledger_chain(CycleDir(...), moment=None)` is spelled 16 times.** Action: one store read of a hop's chain (`CampaignStore.chain`). **Re-test:** that spelling is counted at most once.
- **`p_record_drift` counts handler tables as mirrors.** Action: the count skips a `_handle_*` table and a fold's `records`. **Re-test:** drift is 0 at this tip.
## Symptoms
- A gate pytest hung at 2% CPU for 20 minutes on a failing connector test instead of failing.

## 2026-11-14 · docs(faults): faults-1 report (23ef3738b:report.md)

## Rows to file
- **An origin gate's last error reaches a log alone**: `DegradationHealth.last_error` is read only by `runner/loop.py`'s `logger.error` at the origin-gate halt. The readout and the browser never say why round 0 was graded unhealthy. Root: the stop's served detail (`Ended.detail`, as `OPTIMIZER_ABORT` now carries) holds it. **Rides with:** faults 2. **Re-test:** an `ORIGIN_GATE` readout names the origin's last error, and the log goes.
- **An act's CLI spelling is derived, not the verb's own**: `views/render/phase.py::_spelled` makes `--{leaf}` from a field, so `mode.from_round` would spell `--from-round` while the flag is `--from` (the CLI's `unspelled` table holds the truth). Root: the spelling reads the verb's flag table. **Re-test:** an act on `mode.from_round` spells `--from`.
## Symptoms
- `infrastructure/store/campaign_store/store.py` (`mint_cycle`, `cycle_minted_otherwise`): a 409 naming no next act. Suspected root: as above, the act table.
- `application/optimizers/potter/CLAUDE.md:26`: says L2's one stop is `OPTIMIZER_EXHAUSTED`, beside a terminate that stops `optimizer_abort`. Suspected root: the line predates `terminate_proposal`.
- `infrastructure/maintenance` `LANDING_REMEDY` and harbor's pin-roster `ValueError` name `land-data` / a terminal verb. They are outside the refusal-call scan (no command kind exists), so they were left.

## 2026-11-14 · docs(coupon): chain report (1d376af0f:report.md)

## Appendix: Rows to file
## Appendix: Symptoms
- `quota.py::_contended`: the retryable refusal has no `next_acts`, unlike every other refusal faults-1 typed. Suspected root: `RateLimitedError` takes no acts.
- `quota.py`: `any(draws_coupon(send.payer) for send in sends)` is spelled at four sites (`launch_bound`, `admit_launch`, `_tier_bound`, `_clamped`). Suspected root: whether a launch draws the coupon is a fact of `sends` that has no name.

## 2026-11-13 · docs(report): rk-opt-10 report (77075ebca:report.md)

## Rows to file
None. The open halves are already in the ledger: the locked-in leader's recorded caught-up cells, `resolve_cells` once per init, and `Layer`/`TransitionResult`.
## Symptoms
- `docs/specs/code-debt-cleanup.md`, the population_kept re-test: `-k replays_its_decisions` matches no test. The suspected root is a test renamed without its ledger re-test.
- The `e43ba83` commit body says "no digest" moved, but the estimator digest did move (`difficulty.py` is in `measurement_modules`). The commit text is wrong; the code is not.

## 2026-11-13 · docs(report): rk-dec-2 — trim the report under 400 words (6de2805d9:report.md)

## Rows to file
- `selection.py::prior_read`/`PriorRead` are uncalled. Re-point roadmap.md:345 to `pobb.py::field_p_best`, then delete them.
- The spread row stays open (rk-dec).
- `hold_up` is withheld on a flat ruler, so the offline world never shows it.
- CLI parity for `lift_prior`/`hold_up` is unverified.
## Symptoms
- The playwright forest-viewport check is flaky (`cand-card` +583px).
- uvicorn refused the offline home's data root (`ConflictError`).
- `uv` on PATH is 0.11.32; the gate needs 0.11.21.

## 2026-11-13 · docs(report): rk-dec-2 — family-wise race and noise-walk readout (b832a9598:report.md)

## Rows to file
- `selection.py::prior_read`/`PriorRead` are no longer called and stay only for roadmap.md:345. Re-point the citation to `pobb.py::field_p_best` and delete them.
- The spread row stays open (from rk-dec).
- `hold_up` is withheld on a flat ruler, so the offline world never shows it.
- The CLI readout may not render `lift_prior`/`hold_up`; parity is unverified.
## Symptoms
- The playwright forest-fits-viewport check is flaky: red once, green on rerun.
- uvicorn refused the offline home's data root (`ConflictError` on landing).
- `uv` on PATH is 0.11.32; the gate needs the pinned 0.11.21 (`/tmp/uvpin`).

## 2026-11-13 · docs(report): rk-dec stopped at link 5 on the planted-arm proof (fd15f5067:report.md)

## Rows to file
- **PoBB's cut depends on its slot, and a slot-symmetric cut raises multiplicity**: `pobb.py::PoBBCheck.check` reads each arm against the parent plus the siblings decided before it, so the cut share rises by slot (0.25 → 0.63). A block race against every standing arm is flat, but it cuts a planted better arm 11.8% of the time, against 7.1% today (`wip/rk-dec`). Action: an operator decision on the multiplicity (a family-wise p_best, or rivals limited to the leader), then rk-dec 5 is rebuilt. The top-up race and the walk-order panel ride with it. **Re-test:** the planted-arm Monte Carlo over `PoBBCheck`.
- **`StopReason.CONVERGED` has no producer**: `convergence_patience` went in rk-dec 4, and `tests/test_security.py` still holds the reason (main's hunk). Action: delete the reason with those tests. **Re-test:** `git grep -n CONVERGED -- promptpotter`.
- **The spread row is open**: `measurement_log.py::_cycle_cells` / `cells.py::hit_spread`. Action: name which headline's cells. **Rides with:** readings.
## Symptoms
- A block race with `block_size=1` appends an `ArmRecord` per arm per cell (`round.py::_walk_population`'s `on_turn`).
- A top-up cut that never reaches the arm's state is re-bought by `measure_overlap`'s trigger monitor. `wip/rk-dec` lands the cut as `Cut(Eliminated)`.

## 2026-11-13 · docs(report): recipes chain report (d92d40fa1:report.md)

## Rows to file
## Symptoms
- `promptpotter/application/datasets/loaders.py::recipe_code`: the template assets a panel recipe reads are not hashed into the recipe digest. This is harmless today, because a panel is rebuilt on every fetch and never keyed by `Source.identity`. If a panel is ever cut by identity, the assets must join the digest.

## 2026-11-13 · docs(report): payer-2 chain report (39dc0bac1:report.md)

## Appendix: Rows to file
## Appendix: Symptoms
- `application/jobs/quota.py:627`: `_Left` repeats the "nothing left on a bounded axis" test that `Budget.run_out` now owns. Suspected root: `_Left` is a second shape of a budget's remainder beside `Budget`.

## 2026-11-13 · docs(report): life-2b final report (dec06573a:report.md)

## Rows to file
- **The inner layers carry life-2's +285 net**: see the LEAN line above. Root: an offload-once helper at the async doors would replace the `_open_pass`/`_close_pass`/`_land`/`_unmint` thread shims. **Rides with:** a lean chain. **Re-test:** `git diff --shortstat 805feff HEAD -- promptpotter/{domain,application,infrastructure}` ≤ 0 net.
## Symptoms
- `paper/bbeh-comparison/bbeh_potter_runner.py:~150`: the test-eval loop calls `session.backend_client.run_query` after `run_campaign` returned, and `run_campaign` closes that client on every exit. Suspected root: the runner reuses the run's client instead of opening its own for the verify pass.
- `docs/specs/code-debt-cleanup.md:111` (`acompile` lands twice): its re-test `git grep -c "enter()"` read 0 only because of the `to_thread(enter)` spelling. It reads 1 now, and the double landing is still there.

## 2026-11-13 · docs(report): life-2 stopped at link 1; equals-main unmoved (9dd5cb480:report.md)

## Rows to file
- **An async door still takes a waiting lock on the loop**: `main.py::lifespan` calls `this_producer` (a waiting `take_lock`) on the loop, so the API refuses to start under link 1's guard, and pytest never runs the real lifespan. Root: the lifespan's sync preamble runs in one `asyncio.to_thread`, and an e2e-free probe starts the lifespan. **Rides with:** life-2 (build 3). **Re-test:** `gate.py --only playwright` is green.
- **Origin readiness is asserted twice on a head-to-head Start**: `launching.py::start_checkin._admit` and `launcher/checkin.py::open_checkin` both call `assert_origin_ready`, and `CheckinStart.open` fetches `draft_served` a second time. Root: one check, at the admission. **Rides with:** life-2.
## Symptoms
- `promptpotter/main.py:119`: the API exits at startup (`RuntimeError: waiting on …lock blocks the event loop`). Suspected root: a sync preamble on the loop.
- `promptpotter/application/embedded_run.py:79`: `enter()` lands on the caller's thread, so a host calling it inside a running loop is refused. Suspected root: the embedded door is sync-only.
- `promptpotter/application/commands/launching.py`: the internal `CommandCall`s name no door and default to HOST. Suspected root: the door is a default, not required.
- `paper/bbeh-comparison/bbeh_potter_runner.py:238`: a second `aclose` after the opener's, under `suppress(Exception)`. Suspected root: the client is closed outside its opener.

## 2026-11-13 · docs(report): holders-2 report under its word cap (3c2520ec9:report.md)

## Rows to file
- **A job file that does not load is logged, not reported**: `registry.py::list_all` skips it after one `logger.error`, and only the landing names it. Root: a served read reports it. **Rides with:** life.
- **`reindex` has no dry run**: it now refuses beside a writer, but it still cannot be previewed. Root: one plan/apply pair, like `compact`.
- **The blocklist and grants rewrites take no write lock**: they are now durable but still last-writer-wins. Root: `write_lock`, as `wallet_store.py::_rewrite` does.
- **An atomic write's temporary is unlocked between its close and its replace**: a reset in that gap makes the replace raise. On Windows, a probe of an empty locked temporary may read it as unheld. Root: hold the lock through the replace on POSIX.
- **`gate.py --changed` records a narrowed pytest under the full key**: the next full gate then keeps pytest unrun (`gate.py:1906`). Root: a narrowed run records no green.
## Symptoms
- The full gate reported pytest "unchanged since green" after only `--changed` runs. I ran the whole suite by hand, and it is green.
- The base offline run refused until it was given `PYTHONPATH=/tmp/base`.

## 2026-11-13 · docs(report): cfg-2 report within 400 words (2a50ce045:report.md)

## Rows to file
Both rows are already in the ledger's `### cfg-2` section. Their full text:
- **The node document is typed two ways and the L2 steer is a side memory**: every door now parses a
  patch through `layer_fold.py::wire_patch`, but L2's steer is read through `L2Memory.steered` /
  `steered_layout` beside the fold, not as a named layer of `l1_generate`'s config, and about 58 of
  the engine's `dict[str, Any]` sites are node-config bags (`dict[str, NodeCall]` on `CycleSeed`,
  `dict[str, dict[str, Any]]` on `ForkSeed`, which carries a null clear and text a `NodeCall` refuses
  before the schema types it). Action: one typed node document with knobs a view on it; the steer a
  named layer. It closes ren 8's node-config census and the pipeline-switch row. **Rides with:** cfg.
- **A sub-model's knobs still default in code**: every `CampaignConfig` / `OptimizationConfig` knob's
  default lives in `assets/datasets/defaults.yaml` (`authored.py::defaults_config`), but a knob inside
  a nested model keeps a code default the file never shows: `LivesConfig` start 2 / cap 4,
  `JudgeSpec.temperature` 0.0, and `campaign_config.py::over_the_default`'s budget (usd 0.025, rounds
  50) laid under every budget a layer names. Action: each into `defaults.yaml`, its field required.
  **Rides with:** cfg. **Re-test:** no field of a model `CampaignConfig` nests carries a valued default.
## Symptoms
- `promptpotter/application/optimizers/potter/members.py:227`
  - Symptom: every potter campaign launched from the defaults now shows the inert badge `l1_generate.shots_without_demo_pool`. `defaults.yaml` holds out no demo pool, while `l1_generate.k_max` is above 0.
  - Suspected root: a demo-shot count whose default draws from a pool the default campaign never holds out. Either the count's floor is 0 or `defaults.yaml` declares a demo split.
- `scripts/gate.py:1894` (`last_green.py:87`)
  - Symptom: `python scripts/gate.py --changed` run under the system Python 3.11, rather than the venv, dies with a `SyntaxError` on the `type` statement in `bare_imports`, instead of refusing as the CLAUDE.md says a bare python does.
  - Suspected root: the gate parses sources before its own interpreter check.

## 2026-11-13 · docs(life): arc report (81aa5927a:report.md)

## Rows to file
- **A withdrawn head-to-head leaks its source's client**: `head_to_head.py` `DatasetArms`/`CheckinArms.closing` closes only in `launched()`; `withdraw` is sync and never acloses. Action: an async withdraw closes the source stack. **Re-test:** withdraw a head-to-head and read its client closed.
- **The ledger names `runtime_flags.py`**: `code-debt-cleanup.md` rows say `runtime_flags.py::run_owed`/`producer_attached`; they should say `run_state.py`. **Re-test:** `gate.py --only doc-claims` is green.
- **Unbuilt life rows still owed**: "A blocking lock runs on the event loop", "The CLI composes answers the server should own", "The job registry globs every job file per question", and the kept-upload re-read on the loop.
## Symptoms
- `promptpotter/application/runner/entry.py:167,305` walks `observers.dashboard.live.state.spend`. Suspected root: the projection's state is read through, not asked.
- `tests/factories.py:422` builds `BackendClient`s nobody closes. Suspected root: the test session is built beside the opener.

