# Code-Debt Cleanup — Backlog

**Only work that is blocked or multi-arc** — including every shape a pass noticed off its task (root
`CLAUDE.md` `<situational-awareness>`). Anything one edit closes is fixed in the pass that found it; never the root of a patch just shipped (root `CLAUDE.md` `<root-fix>`). An item ships by
being deleted. Each entry: `file::symbol` — the defect — the action — **Rides with:** the work that
reaches it — **Re-test:** the command or question that says it still stands. Run the re-test before
acting.

## URGENT — shapes that hide the backbone from a reshape

**Rides with:** any pass in the file named.

- **The per-principal read rate limit 429s the e2e `cold` tier** (`first-run.spec.ts` account modal,
  `pools-checkin.spec.ts` draft): one browser's boot polls trip it, so the walk reads 429 on
  `/hierarchy`, `/auth/*`, `/datasets`. Action: the limit admits one tab's own polling cadence.
  **Re-test:** `npx playwright test --project=cold` logs no 429.
- **Left by the upload check-in's 422** (`25be69eac` cut rows on every draft read; fixed 2026-11-02 by
  gating `startability.py::draft_samples` on `origin_readiness.py::columns_confirmed`):
  - **"Run the resolver now" lives three times**: webapp `useIngestFlow` (`advance`, `land`,
    `submitContext`), CLI `new.py::_hold_checkin`, `DraftCampaign.resolution_pending`. Action: the served flag
    alone. **Rides with:** the host assistant ([`host-assistant.md`](host-assistant.md)).
  **Re-test:** `git grep -n "void runCheckin" -- webapp/lib/hooks/useIngestFlow.ts` is one line, under `resolution_pending`.
  - **A Start's check-in stops at `queued` / `starting`**: the mint closes the draft, so its read
    404s, and no served answer names the campaign a draft's Start minted, so no poll can follow it
    there. Action: one read of a Start off its job (`CampaignLaunch.draft_id`, its stage, then
    `Job.hop`). **Re-test:** Start a queued check-in; the view lands on the minted campaign unreloaded.
- **The phone draws the run's figures twice**: `shell/Views.tsx::ViewGlances` (any time more than one view
  is open; the Dashboard preset opens six) repeats the masthead `RunMasthead::RunFacts` `FigureRow`,
  both reading the same served `Said` figures. Action: delete the strip; a masthead figure some
  `FAMILIES[f].glance` names becomes that view's door (`open(family)` already moves an open view to
  newest and scrolls to it), lit while open; I12 and `components/CLAUDE.md` § "Where is this run" follow.
  **Re-test:** `git grep -n "ViewGlances" -- webapp/components`.
- **A backend wire skew is discovered as a 100% degraded origin, not refused at launch** — the box's
  TermNorm read `answer_field` while PromptPotter sent `answer_fields` (2026-10-31); the origin paid
  for every cell to learn it. Action: the connector's health/probe serves the wire contract it
  speaks, and `new` refuses a node config that contract rejects before any cell. **Rides with:**
  the connector probe. **Re-test:** a launch against a backend one wire rename behind fails at
  the probe, naming the key.
- **TermNorm words a warning's step into its message** (`research_pipeline.py` `f"{step_name} failed: {exc}"`
  over a step that raises `"llm_only: …"`), though `StepWarning` carries the step as a field: a served
  health sentence still reads `llm_only failed: llm_only: …`. Action: the message is the fault alone.
  **Rides with:** the next TermNorm change. **Re-test:** `git grep -n "failed: {" -- backend-api/api`
  in the TermNorm checkout.

## NEXT ARC

**Rides with:** the next arc's first builder wave.

### Config resolution and run scope (backbone hunt 2026-10-31, ranked by impact radius)

**Rides with:** one arc; 1 and 2 together remove most of 3 and the read-path fallbacks, 4 absorbs 7.

1. **Pipeline params resolve as one layered fold spelled five ways** — `application/pipeline_resolve.py`:
   `configure_and_apply_pipeline` (run), `_campaign_merge` + `resolve_pipeline_for_campaign`,
   `_draft_merge` (lays backend `base_config`, which the run never lays), `_runs_with` (no identity
   layer), `resolve_pipeline_for_dataset` each lay a different subset in a different order; the run's
   seed goes on after the floors without provenance, so a seeded model is never re-floored and a served
   "what this point ran" can differ from what was sent. `provenance` is an out-parameter threaded
   through six functions. Action: one `ParamLayers` value — an ordered declared list (backend, dataset,
   campaign, floor, identity, seed, evolved, recorded) each subject picks by data, provenance a product
   of the fold; the served response models move out of the module. **Re-test:**
   `git grep -c "apply_node_overlay(" -- promptpotter/application/pipeline_resolve.py` is 1.
2. **`CycleSeed` is a four-part delta each reader re-lays** — `read_cycle_seed(` at 9 sites;
   `domain/campaign_config.py::apply_cycle_seed` lays only `config_overrides` + `optimizer_narrowing`,
   `pipeline_overlay` is laid at 4 sites, `origin_prompts` again in `mint.py::resolve_cycle_plan`;
   `resolve_resume` resolves the plan twice to strip the seed off. Action: the seed folds whole into the
   cycle's effective config, read once by 1. **Re-test:** `git grep -c "read_cycle_seed(" -- promptpotter` is 1.
3. **The babysit verdict is computed at four sites against two narrowings** — `commands/launching.py::fork_cycle`,
   `pipeline_resolve.py::fork_preview`, CLI `resume_command.py::_fork_operator_steer` read the root's
   narrowing; `runner/run_prep.py::prepare_run` stamps after `apply_cycle_seed` (the seed's own
   `ForkSeed.node_narrowing`), so a fork widening the narrowing and steering in one seed can pass one and
   fail the other. The CLI also boots `init_services` only to type `--steer`. Action: the applier decides
   once and stamps the verdict on the seed record; CLI and preview read it. **Re-test:** a fork seed
   whose `node_narrowing` admits the steered model; read the cycle's interventions.
4. **Run scope is ~21 ambient `ContextVar`s bound piecemeal** — telemetry, send pacing, spend book,
   optimizer manifest, archive memory scope, coupon, measurement context, judges, spawn, harbor;
   `set_cycle_ledger` bound at 6 sites, `bind_memory_scope` sets without a reset token, one ledger with
   three doors (the var, `RunCallbacks.ledger`, `LaunchState.ledger`), an unbound memory scope defaulting
   to `OpenAt()` (the whole archive). Action: one frozen `RunScope` bound once at the run seam.
   **Re-test:** `git grep -c "ContextVar(" -- promptpotter`.
5. **`Session` / `LaunchState` is a staged builder modelled as a bag of Optionals** —
   `initialization/session.py`; ~62 guard or sentinel sites (`require_scorer()`, `launch_clock()`,
   `campaign_id=""`); `run_observers.declare_running` skips silently on a None ledger. Action: each init
   step returns the next typed stage. **Re-test:** `git grep -c "require_scorer\|require_partition" -- promptpotter`.
6. **Slot lifetime is a hand-rolled `except BaseException: release…` at ~20 sites** (admission,
   `run_job`, head-to-head, mint-and-start, check-in, embedded run). Action: the registry hands out a held
   slot as a context manager. **Re-test:** `git grep -c "release_slot(\|job_registry.release(" -- promptpotter`.
7. **Optimizer selection is ambient plus a fallback chain** — `optimizer_manifest.py` `_BOUND`,
   `_DETERMINISM`, `_OPTIMIZER_PROMPT_OVERRIDES`; `llm_node_manifest` tries the check-in manifest then
   `bound_optimizer()`; `resolve_optimizer(..., gate="off")` ×3; `_single_model` depends on node order.
   Action: selection rides `RunScope` (4), keyed by node, no fallback. **Re-test:** `git grep -n 'gate="off"' -- promptpotter`.
8. **A broad exception tuple turns programming errors into "did not resolve"** —
   `pipeline_resolve.py::_ROOT_UNRESOLVED` catches `ValueError, KeyError, TypeError, OSError`;
   `line_config` swallows silently, `application/hierarchy.py::_UNRESOLVED` serves it as data. It omits
   `LedgerUnreadableError`, which a root with no resolved run now raises, so one bad campaign fails the
   whole hierarchy listing instead of its own row. Action: catch typed `DatasetAccessError` /
   `StoredConfigInvalidError` / `LedgerUnreadableError` only, each served as its row's typed refusal.
   **Re-test:** read `_ROOT_UNRESOLVED`.
9. **Reads fall back from the recorded fact to today's files** — `_dataset_of` swallowing
   `DatasetAccessError`; `parse_cycle_seed` builds a whole served response to read schema rows. Action: a
   minted cycle's recorded declaration or a refusal; the write path reads 1's fold. **Re-test:** read `pipeline_resolve.py::_dataset_of`.
10. **Engine decisions narrated into `logger.warning`** — `bench/resume_and_fork/repair.py`, `origin_gate.py`,
    `termination.py`, `escalation/firing.py`. Action: each decision a field of its step's record.
    **Re-test:** `git grep -c "logger.warning" -- promptpotter/application/bench/resume_and_fork/repair.py`.
11. **The `evidence` CLI is a 731-line renderer in presentation** — `presentation/cli/commands/evidence.py`.
    Action: rows plus a label table in `application/views/evidence.py`. **Re-test:** `wc -l` of the file.
12. **`connectors/harbor.py` patches litellm module globals** (`_billed_acompletion`, `_refused_aresponses`,
    `_BLOCK`) and classifies failures by regex over stderr. Action: an injected client through the bench's
    admitted send; typed exit data. **Re-test:** `git grep -n "_billed_acompletion" -- promptpotter`.

### The engine swept by shape (debt hunt 2026-11-02)

Grouped by the arc each would ride; workaround comments and dead top-level names came back clean.

**A. The pipeline carrier is typed.** **Rides with:** § Config resolution and run scope #1 (`ParamLayers`).
- **`pipeline_params` is an untyped `dict[str, Any]`**: engine keys (`model`, `provider`,
  `route_order`, `max_tokens`, …) are string-read with mixed tolerance (`cfg["model"]`,
  `cfg.get("model")`, `str(cfg.get("model", ""))`); the reserved `steps` list shares the bag with the
  node dicts (`pipeline_overlay.py::node_config_items` skips it by `isinstance`, and `"steps"` is
  spelled again across connectors and domain, past `domain/CLAUDE.md`'s probe); the prompt is split
  off its config by `k != "prompt"` beside `search_point.py::strip_rendered_prompt`. Three merges
  (`campaign_config.py::merge_config_layers` / `merge_node_overlays`, `pipeline_overlay.py::apply_node_overlay`)
  fold one thing. Action: `PipelineParams(steps, nodes: {name: NodeConfig})` parsed once at the
  overlay seam, engine keys as fields, the backend's params one dict.
  **Re-test:** `git grep -c -E 'pipeline_params: (dict|Mapping)\[str, Any\]' -- promptpotter`;
  `git grep -n '"steps"\|k != "prompt"' -- promptpotter`.
- **`connectors/protocol.py::InProcessWorkload.experiment` is a `Mapping`** that
  `runner/inner/tasks.py::parse_resolved` re-parses at each reader (`dbllmbench.py` reads it with
  `.get("questions") or []`). Action: each connector declares its experiment model; the workload
  carries it parsed. **Re-test:** `git grep -n "parse_resolved(" -- promptpotter`.

**B. Run-data payloads are models.** **Rides with:** § Typed state "Run files read as dicts" and the
next run-data reshape.
- **`run_records.py::ResumeCheckpointRecord.inputs_ref` / `data` are `dict[str, Any]` on the ledger**,
  built by hand (`potter/race.py`, capo's `context`) and read back by string key with casts in each
  optimizer's replayer; a renamed key surfaces only at resume as `replay_error:`. Action: each
  `CheckpointKind` declares typed inputs/data. **Re-test:** `git grep -c -E 'inputs_ref\["' -- promptpotter`.
- **`RaceStandingRecord.paired_breakdown` is an untyped stat bag**: potter (`pobb/checks.py`) and capo
  write different keys, and `views/readout.py::on_race_standing` reads `.get("p_better", 1.0)` to sort
  and `.get("p_better", 0.0)` to print. Action: a typed per-prior model. **Re-test:** `git grep -n '"p_better"' -- promptpotter`.
- **`evidence/read.py` reads `campaign.json` as a tolerant dict** (`_dataset_of`, `_at`,
  `_resolve_head`), and `_dataset_name` accepts a `campaign_config.dataset_name` shape `Campaign` has no
  field for, while `subject_evidence` beside them loads `Campaign`. Action: `CampaignStore.load_campaign`.
  **Re-test:** `git grep -n "read_json_tolerant(CampaignLayout" -- promptpotter`.
- **The stale-data ladder's outcome has two owners**: `sample_measurement.py::execute_stale_data_protocol`
  returns a step string its one caller drops (`query_loop.py`, `recovered, _step =`), and stamps one of
  several exclusive bools on `MeasuredCell` that `render/sample.py` re-reads as an `elif` chain; the
  ladder is string-keyed. Action: one outcome enum on the cell, the ladder a `match`.
  **Re-test:** `git grep -n "recovered, _step" -- promptpotter`.

**C. Served reads fold the ledger.** **Rides with:** § What a cycle writes to disk (`FileSink`) and
§ One record shape for every scoring pass.
- **`hard_samples.json` is a checkout read back as served data**: `runner/output.py` writes it under
  `graceful`, and `scoring/measurement_log.py::_cycle_view` serves the leaderboard order from it, empty
  when absent (a failed write served as "no scale") and with no `at` (a replay page orders by today's
  file); the dataset scope derives its view instead (`_dataset_view`). Action: the cycle view derives
  from the fold. **Re-test:** `git grep -n "read_hard_samples(" -- promptpotter`.
- **`projections/racing_stream.py::RacingStreamProjection` copies `RaceStandingRecord` into per-round
  JSONL and swallows `OSError`**; its one reader (`output.py::_load_p_best_trajectory`) walks the copy
  as dicts. Action: the log folds the ledger records; delete the projection and its `data_root.py` row.
  **Re-test:** `git grep -n "RacingStreamProjection" -- promptpotter`.
- **A running verification is orphaned at two seams** (`live_dashboard/projection.py::_handle_run_wiring`,
  `served_dashboard.py::_serve`) and `orphaned=` computed a third way in `_serve`; `_round_axis` and
  `_walk` each re-decide whether `current_round` closed, by different predicates. Action: derive it
  once, from ledger plus producer lock. **Re-test:** `git grep -n 'm.state == "running"' -- promptpotter`.

**D. One LLM-call seam.** **Rides with:** § Config resolution and run scope #4 (`RunScope`) and #7.
- **The reuse-cache protocol is spelled twice**: `bench/llm_call.py::llm_call` + `_replay` and
  `scoring/judges/call.py::ask` + `_replay` each key by `hash_call`, read a load's `(OSError,
  ValueError)` as a re-sample (which re-spends), claim the price, emit `cached=True` usage, and save a
  non-empty reply; the cache arrives by two carriers (`LLMCallContext.cache`, `bind_cache`). Action:
  `LLMReuseCache` owns replay, meter and save around the send, behind one carrier.
  **Re-test:** `git grep -n "def _replay(" -- promptpotter/application/bench/llm_call.py promptpotter/application/scoring/judges/call.py`.
- **`llm_call` keeps defaults no caller uses**: `node=None` labels the call `"llm_call"`, `config=None`
  makes the callee read `llm_node_manifest`, `LLMCallContext.cache=None` silently re-spends, while every
  construction passes a cache. Action: `node`, `context`, `cache` required; `config` deleted.
  **Re-test:** `git grep -n "LLMCallContext(" -- promptpotter`.
- **`scoring/evaluators.py::Evaluator` is one class for two contracts** (`per_sample` async vs
  `per_round` sync, different kwargs) held apart by runtime guards (`_validate_evaluator`,
  `inspect.isawaitable`); its `Scope` collides with `campaign_config.Scope` and `data_root.Scope`.
  §0.5-bearing: state the case before acting. Action: two evaluator types.
  **Re-test:** `git grep -n "isawaitable\|iscoroutinefunction" -- promptpotter/application/scoring/evaluators.py`.

**E. Absent is not zero.** **Rides with:** any pass in the files named; the first item moves what the
loop decides, so it lands with an offline run.
- **Two bars for "is this θ lift real"**: `potter/escalation/state.py::_improved` reads
  `lift > (current.se or 0.0)` (one arm's se, absent as 0) while `potter/election.py::_lift` takes
  `hypot` of both arms' and says never either alone. Action: one lift test on `AbilityReading`, read by
  both. **Re-test:** `git grep -n "se or 0.0" -- promptpotter`.
- **`llm/pricing.py::_models_to_rates` prices a missing side at zero** (`float(in_c or 0.0)`). Action: a
  row missing either cost is unpriced. **Re-test:** read `_models_to_rates`.
- **`domain/spend.py::usd_text` is "THE money wording" beside inline `${x:.Nf}` spellings at varying
  precision** and a verbatim copy, `application/hierarchy.py::_usd`. `panels.py`'s spelling is
  optimizer-prompt text, so moving it re-keys the digest. Action: money text through `usd_text`.
  **Re-test:** `git grep -c -E '\$\{[^}]*:[,]?\.[0-9]f\}' -- promptpotter`.

**Singles.**
- **`identity/default_claim.py::maybe_claim_default` writes the claim marker after a failed rename** of
  `projects/default` (`renamed=False`, logged only), and never retries, so terminal and browser resolve
  different tenants. Survivor data; rides with § On the box (one email, two accounts). Action: the
  marker lands only with the rename. **Re-test:** `git grep -n -A3 "except OSError" -- promptpotter/infrastructure/identity/default_claim.py`.
- **`initialization/loop_start.py::arm_diagnostic_scoring` binds `unbounded_spend_book()` where no book
  is bound**: a scoring pass its caller forgot to admit runs with no ceiling instead of raising.
  Action: every diagnostic entry binds its admitted book (`paid_pass`), and an unbound one raises as
  a run's does (`telemetry.py::bound_ledger`). **Re-test:** `git grep -n "unbounded_spend_book()" -- promptpotter`.
- **`ledger_scan.py::StandingProposal.entered_at` has no writer and no reader.** Action: delete.
  **Re-test:** `git grep -n -w entered_at -- promptpotter`.
- **`held_out` (`InnerTask`, `domain/cells.py`) and `Sample.bench_only` name one concept**, translated in
  `ResolvedPanel.samples`. Rides with the "held-out" rename (§ One record shape for every scoring pass).

### The file layout (import-graph pass 2026-10-31)

**Rides with:** after the config/scope arc above; merges mostly cut duplicated import blocks (whole
tree ≈ −10% cross-file edges, ≈ −600 LOC), the LOC win is the root fixes inside them. Measure with an
import-graph script before and after; close each wave with `gate.py --only layering,help-imports,handler-tables,doc-claims,instruction-files`.

- **Wave A — presentation and infrastructure, no digest re-key:** CLI collapse
  (`cli/campaign_runner.py` + `parsers.py` + `commands/verbs.py` → `cli/main.py`; small read verbs →
  `commands/reads.py`, measure verbs → `commands/measure.py`; `handler-tables` names
  `campaign_runner._COMMANDS_PACKAGE`); `routers/datasets/` → one module; `infrastructure/identity/` 12 →
  3 (`oidc`, `grants`, `accounts`); `tracing/` 7 → 4 with `TracingProjection` building its own sinks
  (drops `Session.langfuse`, a second door into tracing); `projections/live_dashboard/` → one module;
  LLM wire types into `llm/base.py`.
- **Wave B — the application backbone:** `application/runner/` 18 → 7 files (loop, round, output,
  entry, bench, mint, inner/spawn) deleting the ledger-scan forwarders (`_spent`, `read_line_spend`,
  `fresh_campaign_id`); loose top-level modules regrouped (cycle reads, hierarchy, origin + `loop_start`);
  the optimizer registry (`optimizers/__init__.py`'s 146 lines), `knobs.py`, `run_callbacks.py` into
  `optimizers/nodes.py` — breaks the hidden 16-module cycle (`TYPE_CHECKING` / in-function imports hide 8 cycles).
- **Wave C — the optimizers, one re-key batch:** `potter/l1/` → `l1.py`, `validators/` 7 → 2, peer
  optimizers' `prompts` + `state` into `members.py`, `pobb/` and `escalation/` flattened, and
  `runner/inner/tasks.InnerCells` moved into `domain/l4` (optimizer code imports a runner internal).
- **Rejected:** a concept-first vertical re-cut (§0's "One rejected re-cut").

### Tooling seen while landing #59–#62

- **`scripts/offline_run.py` runs whatever package its interpreter imports**: run from a worktree with
  the main tree's venv, its child runs import the main tree and it reports UNMOVED. Action: refuse when
  `promptpotter.__file__` is not under the tree the script sits in. **Re-test:** run it from a worktree
  with the main venv; it refuses.
- **The pre-commit hook runs bare `python`**, so a commit in a worktree builds and syncs a `.venv` there.
  Action: the hook resolves the clone's venv like `_BORROWED` does. **Re-test:** a commit in a fresh
  worktree creates no `.venv`.
- **The gate measures whatever extras the venv happens to hold**: `gate.py::_PINNED_EXTRAS` syncs
  `--inexact`, so packages outside the pinned set stay installed and an optional import loads them. A
  venv built with `uv sync --all-extras` put the suite over `tests_owning.py::WIN32_CEILING_MB`;
  resynced to the pinned extras, the same tree passed. Action: the gate refuses a venv holding packages
  outside `_PINNED_EXTRAS`, naming the sync that makes it exact. **Re-test:** `uv sync --all-extras`,
  then `gate.py --only pytest` refuses before it measures.

### The webapp's vanilla pass (audit 2026-10-29)

- **Failures dropped to booleans before `FailureLine` can render them**: `useConnector` folds its read
  failure into `schema.status: "error"`, `useTimeRay` serves `failed`, ChannelMap's tree `failed`/`loaded`,
  `OriginChanges` flattens to `message`. `useCommand` `describe` tables (ArchiveCompactionControl,
  AccountUsageTab, AccountProfileTab) sit beside `operatorMessage`. **Re-test:** every hook carries a failure object.
- **Wording authored in the browser**: label tables still client-side (`lib/prompt-fields.ts::PROMPT_FIELD`, `lib/derivations/sample-trajectory.ts::MOVEMENT_CLASS`,
  `WINDOW_LABEL`, `AccountView::SECTIONS`, …) and the tone vocabularies off `domain/said.py::Tone` (`l4-eff-*`,
  `verdictTone`, PipelineFlow's). Canonical: served label constants, one tone. **Re-test:** each table
  served or deleted.
- **Size literals and the seed-once latch**: chart `height: 140`
  (`git grep -n "height: 140" -- webapp/components`) beside `--chart-h`, which is a `clamp()`, so the
  two disagree on purpose or by accident; the seed-once latch hand-built at each
  `git grep -n -i "seeded" -- webapp/components webapp/lib` site; `STORAGE_KEYS` mixes hook and
  non-hook keys and `side-panel:*` has no `promptpotter.` prefix.
  **Re-test:** one latch.
- **Modules still off the `lib/CLAUDE.md` § Where a module lives rule**, blocked by the run-card
  session's files and the ingest rebuild, which import them: to `state/` — `hard-samples`, `lineage`,
  `poll`, `workspace`, `ingest-flow`, `scoring-mask`, `chat/thread`, `SelectionContext` (as
  `selection.tsx`), `hooks/useConnector`'s provider; to `derivations/` — `cell-means`,
  `prompt-fields`.
  Chrome still holding a surface: `shell/searchpoint/`, `shell/scoring/` (ingest imports it),
  `shell/node-surface/`, root `PairedLift` / `SummaryBlock`; a surface holding chrome:
  `dashboard/pipeline/` (`git grep -n "dashboard/pipeline" -- webapp/components/ingest`). Hard samples and compare each spread over
  several folders (`git ls-files webapp/components | grep -i -E "hard-?sample|compare"`).
  **Re-test:** `git ls-files ':(glob)webapp/lib/*.tsx'` lists none.
- **Adapters over a flat served shape**: `useCandidatesModel.ts` joins every source it imports;
  `.facts.` opened at `git grep -c "\.facts\." -- webapp/components webapp/lib`; liveness
  re-derived at each `git grep -n "phaseWalks(" -- webapp/components` site.
- **A view family that is chrome**: `components/shell/families.tsx::FAMILIES` holds a family with a
  null pane (the phone's campaign list is the sidebar), so naming it on a wide screen opens a chip
  that draws nothing. **Re-test:** every family has a pane.
- **The `walk` tier rots unseen**: the gate runs only `cold`, so a walk assertion outlives the control
  it names. Action: the gate walks a world built by `scripts/offline_run.py`, which needs none of the
  operator's campaigns. **Re-test:** `gate.py` lists a walk check.
- **Several doors into one state**: candidate selection minted at each
  `git grep -n "selectedCandidateOf(" -- webapp` site, the sample set written through
  `setSelectionForSampleSet` from several surfaces, `FileTree::onSelect` drilled through its parents.
- **The wide masthead wraps to about 110px** at 1280px (setup line plus seven figures).

### The phone pass (2026-10-31)

The launch verdict (`quota.py::launch_refusal`) landed. The rest of the 390px pass is open: re-walk the phone screens and audit the surfaces against
`webapp/CLAUDE.md` and `lib/CLAUDE.md`. Shapes the first pass kept finding, each still present
somewhere:

- a client word table or literal beside a served enum, or a served sentence where an enum belongs;
- a second door or control into one state, and client-minted state beside a served one;
- a refusal spoken only after the tap, where a served verdict could gate the control;
- `??` / `||` defaults on facts the server always sends;
- a served field whose last reader was deleted;
- a whole-object replace where a per-key merge is meant;
- two placement or layout engines for one job, and fixed geometry beside wrapping text;
- chrome that stays pinned on a phone, and figure cells that truncate sentences;
- a hand-copied fixture.

The frame, the run card, origin pairing, pipeline CSS and money format wait on the run-card session's
files.

### The candidates surface (review 2026-11-01)

Seen while removing the canvas tooltip; the panel work is [`roadmap.md`](roadmap.md) § Arrangeable workspace.

- `useCandidatesModel` writes the module store during render (the per-cycle metric and overlap seeding).
- Two sources for one question: `wonOnTheta` reads `facts.elects_on`, `electedMetric` reads `dash.display_metric`.
- `FitnessChart::parentIdx` names the standing selection, not a parent.
- Three interval formatters: `format.ts::fmtInterval`, `VerifyReading::band`, `SearchpointDrillIn::band`.
- Two `cell_means` renderers: `SubjectSignals` and `channel-parts::CellMeans`.
- Three ways into Compare (the toolbar menu, `CompareOriginAction`, the forest).
- Teaching prose in `title=`: the `FitnessLegend` hint, the toolbar, the dendrogram `<title>`, `CompareOriginAction`, `RoundVerdict`.
- `TrajectorySeriesView::SeriesHoverPopup` is a hand-built popover beside `ui/Popover`.
- `CostStrip` sizes itself inline (`height: 140`); `.ov-forest` is styled in `compare-evidence.css` for `OuterSignalPanel`, and collides with `candidates/Forest`.
- `EvidenceCharts` and `CostStrip` lost their hover readouts with the tooltip; a per-cell or per-round value they need opens in the DOM.

### The webapp swept by shape (debt hunt 2026-11-02)

Grouped by the arc each would ride. Came back clean: dialogs, clipboard, `EventSource`, `<table>`,
component `fetch(`, `setInterval`, `useEffect` resets, client sorts, unfiled `Record<…, string>` tables,
fixtures.

**A. One arm shape, served.**
- **A live arm and a filed arm arrive in two shapes** (`LiveCandidate` flat; `RoundView.arms[key].individual`),
  so derivations come in pairs (`candidateSearchPoint` / `liveCandidateSearchPoint`, `…Verdicts`,
  `…ObserveConfig`), `ArmRow.source: "history" | "inflight"` branches in `MeasurementRun` and
  `SteerForkPanel`, and `round-candidates.ts::roundCandidates` stitches `dash.rounds` with
  `current_round.candidates`. "Is round N filed" is asked three ways (`useRound.ts::isRoundUnfiled` by
  membership in `dash.rounds`; `roundCandidates` and `search-point.ts::roundObserveTarget` by
  `round_axis.completed`). Action: one served arm shape carrying a filed/live state.
  **Re-test:** `git grep -nE "liveCandidate(SearchPoint|Verdicts|ObserveConfig)|round_axis.completed.includes" -- webapp/lib webapp/components`.

**B. Served words own every wording.**
- **The scoring-mask scenario sentence is built twice** from `SCENARIO_WORDS` pieces, with "under"/"of"
  typed at each site: `compare/ComparePane.tsx::Scenarios` and `cli/commands/evidence.py::_scenario_lines`.
  Action: `application/evidence/subjects.py` serves it as a `Said` row. **Re-test:** `git grep -n "SCENARIO_WORDS" -- promptpotter/presentation webapp/components`.
- **`lib/lineage.tsx::maskLabel` / `maskActive` word and decide the active re-read client-side**
  (the badge reads "Showing the Sample set mask"), beside the served `hierarchy_subtree.py::_read_under`
  line. Action: render `read_under`. **Re-test:** `git grep -n "maskLabel" -- webapp`.
- **Intervals typed into UI text beside their owners**: `TimeRay.tsx::Gap` "beats every 15 s"
  (`heartbeat.py::HEARTBEAT_INTERVAL_S` is the owner, and the gap comment disagrees with `RECENT_STEP_S`),
  `poll.tsx::OFFLINE.statusHint` "every 5 s", `TERMS.brand_live_preview` "every 2s". Action: interpolate
  the served or `cadence.ts` constant. **Re-test:** `git grep -nE "every (15|5|2) ?s" -- webapp/components webapp/lib`.
- **`MeasurementRun.tsx` words the served `ArmEnd`** through ``TERMS[`arm_${early}`]`` and
  `early.replace("_", " ")`, its tone picked locally. Action: an `ArmEnd` row in `_WORD_TABLES`.
  **Re-test:** `git grep -n 'arm_\${' -- webapp/components`.
- **`run-phase.ts::phasePauseLabel` covers blank served words** (`DASHBOARD_STATE_PAUSE_WORDS` maps
  `optimizer_step` and `stopped` to `""`) with a `||` and hand-typed "the current round". Action: the
  served table words every state. **Re-test:** `git grep -n "the current round" -- webapp/lib`.
- **Glyph tables over served enums**: `forest-layout.ts::KIND_GLYPH` / `TRIGGER_GLYPH` /
  `DIRECTION_GLYPH`, `Criterion.tsx::TERM_GLYPHS`, `FitnessRankSummary::MOVE_MARK`, a literal "⑂" in
  `ForestRows`. Low weight. Action: the glyph rides the served word rows.
  **Re-test:** `git grep -nE "_GLYPHS?\b|MOVE_MARK" -- webapp/lib webapp/components`.
- **`chat/HardSamplesPreview.tsx`**: `labelAt` invents `rank ${…at + 1}` on a lookup miss though
  `hard_sample_rank` is required on the wire; `rateOf` tests "unmeasured" three ways; bucket keys render
  raw. Action: handle the miss, one served unmeasured state, served bucket words.
  **Re-test:** `git grep -nE "at \+ 1|hit_spread === \"unmeasured\"" -- webapp/components/chat`.
- **"Measured in N/M rounds" is counted in the browser** (`sample-set.ts::roundsCoveringSample`, read by
  `SampleSetControl`), and `fullyCovered` counts rounds with any selection. Action: served per-sample
  coverage. **Re-test:** `git grep -n "roundsCoveringSample\|fullyCovered" -- webapp`.

**C. Lineage reads the served tree.**
- **A campaign's short label is parsed three ways**: `format.ts::shortId` (`{dataset}__{rand6}`),
  `ForestRows::shortOrigin` (`cycle_`), `ids.ts::shortFamilyTail`, used by `ComparePane::channelNames`,
  `EvidenceCharts::seriesLabel`, `SearchpointPanels`. Action: a served short label per subject and node.
  **Re-test:** `git grep -nE "shortId\(|shortOrigin\(|shortFamilyTail\(" -- webapp/components webapp/lib`.
- **`ids.ts::rootCycleId` regexes `_fork_` / `_diag_`** to give `useLineage::parentId` /
  `isInheritedSibling` what the served tree carries. Action: read parent and spawn off the tree.
  **Re-test:** `git grep -n "rootCycleId(" -- webapp/lib webapp/components`.
- **`SearchpointPanels.tsx::buildSpines` walks `parent_ids[0]`** beside the served `main_line`
  (`lineage-candidates.ts::mainLineOf` says never walk up). Action: spines from `main_line`.
  **Re-test:** `git grep -n "parent_ids\[0\]" -- webapp/components`.
- **`hooks/useLeafDatasetName.ts` reads a campaign pipeline for a `dataset_name`** the viewed subtree's
  entry already tags (`application/hierarchy.py::_campaign_tags`). Action: read the tag; delete the hook.
  **Re-test:** `git grep -n "useLeafDatasetName" -- webapp`.

**D. One selection and view store.** Extends § The webapp's vanilla pass "Several doors".
- **The picked candidate lives in two stores**, `workspace.tsx::viewedCandidateId` and
  `SelectionContext.candidate`: `useSelectNode.pick` writes both, `MeasurementRun.onSelectCandidate`,
  `ForestCard`'s ctx and `TimeRay` only the second. Action: one owner.
  **Re-test:** `git grep -nE "viewedCandidateId|setCandidate\(" -- webapp/lib webapp/components`.
- **`showForest` / `expandedLanes` sit in `state/candidates.ts` and `view-memory.tsx::CampaignView`**,
  written together by `hooks/useLineage.ts` and seeded during render by a latch; the hook's name
  collides with `lib/lineage.tsx`. Action: view memory alone owns them; rename the hook.
  **Re-test:** `git grep -nE "showForest|expandedLanes" -- webapp/lib`.
- **`workspace.tsx::WorkspaceState` exposes the viewed address four ways**: `viewedPath`, the root as
  unqualified `campaignId` / `cycleId`, `leafCampaignId` / `leafCycleId`, and callers running
  `pathRoot` / `pathLeaf(viewedPath)`. Action: one address value with named root and leaf.
  **Re-test:** `git grep -nE "leafCampaignId|leafCycleId|path(Root|Leaf)\(viewedPath" -- webapp/components webapp/lib`.

**E. Read discipline.**
- **Evidence invalidation is built twice with two liveness tests**: `ComparePane`'s
  `useEffect(() => invalidateReads("evidence"), [moved])` (`phaseWalks`) and `useOriginEvidence`
  (`producer.appending`), each invalidating every evidence read. Action: one invalidation on one served
  liveness fact. **Re-test:** `git grep -n 'invalidateReads("evidence")' -- webapp`.
- **`KnobClashes.tsx::useKnobClashes` and `KnobClashes` each open `configMapRead`**, and the hook reads a
  failure as `[]`, so `OptimizerCard` says "no clash" when the read failed. Action: one read; a failure
  stays a failure. **Re-test:** `git grep -n "configMapRead" -- webapp/components`.
- **`state/auth.tsx::AuthProvider` logs the user out on a transient failure** (any non-auth failure sets
  `unauthed`, `adopt(null)` clears the cache), against I7; `useAuthGate::onAuthError` tests
  `status === 401` beside `failureKind`. Action: branch on `failureKind`; transient keeps the session.
  **Re-test:** `git grep -n "status === 401" -- webapp/lib/state`.
- **`hooks/useCycleEvents.ts` drops an undecodable frame in a bare `catch`** and guards
  `env.cycle_id &&` on a required field. Action: report the frame; drop the guard.
  **Re-test:** `git grep -n "env.cycle_id &&" -- webapp/lib`.
- **`hooks/useNestedPipelines.ts` words a failed level into `truncated`** (`"<dataset> unavailable (<kind>)"`)
  instead of the unused `ReadFailure` member of `PipelineStatus`, and walks the nest client-side with its
  own `MAX_DEPTH`. Action: served nest depth; a failure stays a `ReadFailure`.
  **Re-test:** `git grep -nE "MAX_DEPTH|unavailable \(" -- webapp/lib/hooks`.
- **`poll.tsx::useRay::loadOlder` pages through a hand-built `readThrough`** whose failure only reaches
  `reportIncident`. Action: page through `useRead`. **Re-test:** `git grep -n "readThrough" -- webapp/lib`.
- **Defaults on an index miss**: `PipelineFlow.tsx`'s `colX` (`offsets[i] ?? 0`, `widths[i] ?? CELL_W`),
  `L1Variants.tsx` (`variants[idx] ?? variants[0]`). Action: handle the miss.
  **Re-test:** `git grep -nE "\[(i|idx)\] \?\? " -- webapp/components`.
- **`ForestRows.tsx` fakes `LOADED = { root: null, loaded: true }`** though `loaded` otherwise equals
  `root !== null`, and hand-builds loading and failure rows beside `ui/states::ReadState`. Action:
  delete `loaded`; render through `ReadState`. **Re-test:** `git grep -n "loaded: true" -- webapp/components`.
- `hooks/useVerify.ts` repeats `poll.tsx::useDashboardAt`'s streamed-path test with its own
  `dashboardRead`. **Re-test:** `git grep -n "dashboardRead(" -- webapp/lib/hooks`.

**F. The node editor reads served row facts.**
- **Key-name tests stand in for row facts** (I9): `NodeSurface::onAuthor`, `node-config.ts::effortLadder` /
  `pickedRoute` / `permittedModels`, `NodeConfigEditor::axisMenu`, `pipeline-nodes.ts` test
  `reasoning_effort`, `provider`, `model`, `response_format`, `schema_family`. Action: the param schema
  serves each row's role. **Re-test:** `git grep -nE '\.key (===|!==) "(reasoning_effort|provider|model|response_format|schema_family)"' -- webapp/lib webapp/components`.
- **Editability is decided client-side**: `SteerForkPanel::canBabysit` tests
  `me.capabilities.includes("campaign.babysit")`; `permittedModels` re-walks what `configRows` reads;
  `NodeConfigEditor`'s `schema` is a source prop two hosts fill from different reads. Action: served
  per-row editability for the account; one schema source.
  **Re-test:** `git grep -nE "campaign.babysit|permittedModels\(" -- webapp`.
- **`JobsDock::passMark` / `passLabel` derive running/starting from `started_at === null`** (so does
  `AccountUsageTab`). Action: a served pass state. **Re-test:** `git grep -n "started_at === null" -- webapp/components`.
- Hand-built controls beside the primitives: `Criterion::DialRow` commits on pointerup/keyup/blur beside
  `CommitInput`; raw `<select>` in several surfaces with no `ui/` select; `RunLimitsControl::capInput`
  and `LimitReconcile::parseCapIn` beside `run-limits.ts::parseCap`. Low weight.
  **Re-test:** `git grep -nE "<select|parseCapIn|capInput" -- webapp/components`.

**G. Dead and ratchets.**
- **No reader**: `run-phase.ts::phaseParked` (the last reader of `domain/phases.py::RunPhaseInfo.parked`,
  which goes with it), `terms.ts::targetNodeIds`, `TERMS.composite`; CSS `.avatar`, `.table-wrap`,
  `.family-cladogram-head`, `.l4-cell-subject`, `.limit-input-usd`, `.limit-usd-prefix`,
  `.subject-note`, `.unit-library-id`, `.tag-hit` / `.tag-miss` / `.tag-err`, `.btn-tight` (and the
  `:not(.btn-tight)` in `responsive.css`), `.hsp-note`, `.chat-tools-err`, `.chat-decision-err`.
  **Re-test:** `git grep -nE "phaseParked|targetNodeIds|TERMS\.composite" -- webapp`; each class name
  against `webapp/components` and `webapp/lib`.
- **Teaching prose in `title=` across many surfaces** beyond § The candidates surface's list. Action: a
  gate ratchet. **Re-test:** `git grep -cE 'title=("[^"]{60,}|\{\`[^\`]{60,})' -- webapp/components`.
- Minor: `CandidateRow` re-derives "retired" after `splitRetired`; `reads.ts::filesRead` builds the
  cycle URL beside `cyclePathUrl`; `useActivePointer` uses `|| null` on ids.

### What a cycle writes to disk (storage survey 2026-11-01)

- **`tracing/file_sink.py::FileSink`, the local Langfuse shadow, has no reader and runs on every
  cycle**, also with no Langfuse configured and in L4 inner cycles. It writes most of a run home's
  files: one file per panel row per cycle (`langfuse/datasets/ground_truth/*.json`, a copy of
  `banks/{hash}.json`), one per trace, one per observation (written at open and again at close), and
  a scores `.jsonl` + `.lock` per trace. Action: delete it and its `data_root.py::_cycle_rows`
  langfuse rows, keeping `state.json` (`LangfuseSink`'s id map). Langfuse and MLflow keep their
  native formats, and a local Langfuse-format copy is rendered from the ledger on demand.
  **Rides with:** the next on-disk reshape (§ One record shape for every scoring pass).
  **Re-test:** `git grep -n "FileSink(" -- promptpotter`; `find <offline home> -path '*langfuse*' -type f | wc -l`.
- **A bank mint keeps exact repeated rows**: `logic-proofwriter` (`banks/1d4ed70f….json`) repeats
  whole `(query, ground_truth)` rows, a query up to 3×. Each repeat has the same
  `domain/sample.py::sample_key`, so a panel can draw one sample twice. Action: the mint keeps one
  row per `sample_key`, so the content address names distinct rows. **Rides with:** the next
  dataset ingest. **Re-test:** count rows against distinct `(query, ground_truth)` pairs in that bank.
- **An archived answer's outputs are read off today's backend**: `scoring/measurement_log.py::open_cell`
  reads the live declaration to build `outputs_of`, because an `ArchiveEntry` records only
  `node_configs` and `PipelineData.wire()` is flat, with no per-node attribution. Action: a per-node
  output-key map filed with the answer; existing rows name no campaign to join, so a backfill reads
  today's declaration per dataset hash. Operator decides. **Rides with:** the next archive reshape.
  **Re-test:** `git grep -n "dataset_pipeline(" -- promptpotter/application/scoring/measurement_log.py`.

### On the box (2026-11-01)

- Unexplained: in `justlogic.d234__e85387` / `cycle_f1591f054ad0`, R1's three candidates and R2's two
  carry no score in `dashboard.json`. Read the cycle's ledger for their arms before calling it a bug.

- One email holds two accounts on the box: the claimed host (`identity/default_claimed.json`) and the
  Google sign-in that owns every webapp campaign. The host's coupon exemption and the CLI's identity
  read the first, the webapp the second (`default_claim.py::_is_host` compares a user id, never the
  declared `HOST_ADMIN_EMAIL`).
- No verb sets an account's coupon or starts it afresh: `grant.json` is hand-written, and a reset is
  moving `charges.jsonl` aside, which also drops the account's bill. A grant that counts charges from
  its own `granted_at` would make a re-grant the reset; today `wallet_store.py::CouponGrant.granted_at`
  is written and never read.
- **Left by the payer rule (2026-11-02)**:
  - `quota.py::sends_on` raises `NoApiKeyError` for a provider no key pays, so `change-run-limits`
    (`clamp_budget_change`) refuses a limit over a missing key. Action: `sends_on` is total (a no-key
    send's payer is `None`); `launch_refusal` refuses `NO_KEY`, and `coupon.py::keyed_client` is the one
    raise at send (today `provider_send` spells it again). **Re-test:** `git grep -n "NoApiKeyError(" -- promptpotter/application/jobs`.
  - `CouponStatus.state` / `.held` / `.left` and `LaunchRefusal.provider` are served with no reader.
    Action: delete. **Re-test:** grep each in `webapp/components webapp/lib` and `promptpotter/presentation`.
  - The in-run allowance refusal (`spend_book.py`'s `HostAllowanceExhaustedError`) names no provider
    though `admitted(…, provider=…)` carries one. Action: pass it.
  - **An in-process connector whose cells send through `keyed_client` declares a fixed payer**: the L4
    `promptpotter` connector keeps `Connector.cell_payer = "host"`, so an own-key L4 run is refused on a
    used-up allowance. Action: an in-process connector's cells are per-provider sends (the target
    nodes' providers, read through `pipeline_resolve.py::served_pipeline`), read by `sends_on`, the
    cell reservation and `application/free_tier.py::free_fence`. **Rides with:** the in-process `llm_only` connector
    (uploads that run without a backend, one node implementation shared with TermNorm's
    `_step_llm_only`), which needs the same shape and is what lets a BYOK account run an upload.

### One record shape for every scoring pass (2026-11-01, two independent reads agree)

**Rule: the ledger writes a pass the same way whatever verb sent it**; the verb and the row set are
fields. The per-cell records already hold it (`SampleScoredRecord` + `role`, for `RECORDED_ROLES`). The
records ABOUT a pass do not: origin, measure (`MeasureEnterView`), backfill (`RaceCatchUpRecord`),
overlap (`RoundCells.overlap`), repair (`RoundRestatedRecord`), verify (`VerifyEnterView`,
`VerifyGradedView`) and bench (`BenchEnterView`, `BenchGradedView`, `BenchScoredView`) each have their
own family, and `noise-floor` and `seed-screen` write nothing to the ledger.

**First arc: verify and the bench grade**, one act (an individual scored outside a round) built twice.
- Essential and kept: the row population (REPORT vs BENCH scope, never paired, `SCOPE_ROLES`), the
  subject (a candidate at a cycle vs the line), the comparator (fresh vs recorded vs origin-paired with
  `LiftCost`), the fold scope (own ledger vs the line), the bench's reserve and `reads_before`.
- Accidental and merged: `VerifyPass`/`BenchPass` (same facts, other names), the views, the phase, the
  fold (`ledger_scan.py::_Verify` vs `_Bench`), the renderers, the stop banking (only the bench banks a
  `PassStop`; a failed verify is inferred from a bare exit), the served can-run answer (only
  `BenchStatus.can_grade`), where `paid_pass` is taken, the spend bucket and source stamp, where the
  reading is joined, the chat bracket, the embedded entry (bench only).
- A skipped automatic verify is never reported: `diagnostics/verify.py::verify_on_saturation` logs its
  `DiagnosticError` at info and drops it, and re-opens the session from disk mid-loop. The served
  can-run status and a banked refusal on the one pass are where the skip lands.
- Target: one `VerificationPass` keyed by `VerificationKind`, one enter view and one graded view, one
  phase, one fold keyed `(kind, arm.key)`, a served can-run status for both. **Open (operator): one verb
  with a row choice, or two verbs over the one pass** — the comparator and subject differ, so the two
  readings stay either way.
- Names: `verify` reads as a correctness check; `bench` collides with `application/bench/` (the
  optimizer runtime); `bench` / `grade-bench` / "Grade on bench" disagree. "held-out" is the clear word.
- Ledger shape changes, so run data is wiped (after the 11-02 demo); archive and survivor rows untouched.

**Seen alongside:** `campaign_store/store.py::CampaignStore.bank_final` still swallows a failed export
write (`graceful`); the export is the run's handed-on prompt, so a lost one is never reported.

### Typed state — what the 2026-10-30 arc left (served words → enums + one exported table)

- **`domain/bench.py::BENCH_STATE_INFO` is a second table over `ReadingState`** beside
  `READING_STATE_INFO`. Action: one table with a `fault` column. Merging re-keys optimizer prompts
  (a digest table), so it asks the operator first. **Re-test:** `git grep -c "BENCH_STATE_INFO" -- promptpotter`.
- **`DISPLAY_METRIC_INFO` is hand-emitted three ways** (`DISPLAY_METRICS`, `_LABELS`, `_UNITS` in
  `scripts/build_ts_types.py`). Action: one `_WORD_TABLES` row. **Re-test:** grep the three names in the script.
- **`domain/cycle_index.py::RunStatus.of` builds `mark: str` and passes it through `model_validate`**,
  so the `StatusMark` literal is checked at runtime only. Action: map phase/outcome → `StatusMark` typed.
- **`application/config_map.py::ConfigKnob.source` is an untyped `str`**, and its only reader is
  `RowHoverCard`'s declared-knobs list. Action: a `KnobSource` literal. **Re-test:** `git grep -n "source: str" -- promptpotter/application/config_map.py`.
- **The pipeline renderer's styles live in `app/styles/domains/chat.css`** (`.wf-hero-*`,
  `.connector*`, `.pipeline-zoom*`), restated by `dashboard-pipeline-nodes.css` to win on order. The
  renderer is `dashboard/pipeline/PipelineFlow`'s. Action: move the block to its own domain file in
  one commit once the run-card session's `chat.css` hunk lands. **Re-test:** `git grep -c "wf-hero" -- webapp/app/styles/domains/chat.css`.
- **`NodeKnobsPanel::POOL_NOUN` words a served closed set (`PoolRow.source`) locally.** Action: an
  exported word table. **Re-test:** `git grep -n "POOL_NOUN" -- webapp/components`.
- **`search-point.ts::OBSERVE_LABELS` is a TS-only closed set** (`ObserveState`): a UI mode, not a
  served state — admitted until a surface beside the browser names it. **Re-test:** still TS-only.
- **`lib/format.ts::SIDE_TONE` / `verdictTone`** stay local: the file belongs to the run-card
  session. **Rides with:** that session landing.
- **A file's identity is tested twice, by two rules**: `store/read_model.py::_LedgerTail._holds_last`
  re-reads the last fed line, while `store/io.py::_swap` trusts `_FileMark.inode`. An inode
  outlives its file, so a delete and re-mint between `_read_marked` and the swap passes that test,
  and the rewrite keeps the new file's bytes past `mark.end` as a tail. Action: one identity check
  in `io.py`, read by both. **Re-test:** `git grep -n "st_ino" -- promptpotter/infrastructure/store`.
- **A landing waits on every live producer on another commit, whether or not the ledger shape
  moved** (`jobs/registry.py::JobRegistry.close_for_landing`), so a busy box may never land. Action:
  key it on a digest of the `CycleRecord` schema the code writes, not the commit. **Re-test:** a
  landing that moves no record shape refuses while a run is live.
- **Run files read as dicts have no shape row**: `data_root.py::KINDS` proves `draft.json`,
  `resolution.json`, `active_session.json`, `rounds/round_*.json`, `dashboard.json`, `index.json`,
  `diagnostics/`, `banks/cuts.jsonl`, `rates.json`, the langfuse mirror and the account's
  `model_capabilities.yaml` by presence alone, because their readers walk `dict`s
  (`read_json_optional`, `read_json_tolerant`, `read_yaml_optional`, `fold_jsonl`). A reshape there
  passes `land-data` and is read around. Action: a model per file, its reader's, then its row's
  shape. **Re-test:** `git grep -n "Fate.RUN),$" -- promptpotter/infrastructure/store/data_root.py`.
- **`iter_jsonl` reads past a line it cannot decode** (`store/read_model.py`), and `fold_jsonl`,
  the bank labels and cuts with it, so a torn or reshaped line is silently absent. Action: the
  complete-line rule and a refusal, as `ledger_record`. **Re-test:** `git grep -n "except json.JSONDecodeError" -- promptpotter/infrastructure/store/read_model.py`.
- **`CycleEventLog.__init__` counts an unterminated last line as an offset** (`infrastructure/ledger.py`)
  where `iter` and every tail skip it, so a torn tail shifts the next append's offset by one against
  a refold. Action: count through `io.py::complete_lines` and decide what an append does past a torn
  tail. **Re-test:** `git grep -n "for line in fh" -- promptpotter/infrastructure/ledger.py`.
- **`_Billed` is `_Cost` with a flag flipped** (`store/account_spend.py::_Cost.counts_mirrored`): a
  flag keeping a bank's spend and a campaign's cost apart over one fold of the same lines. Action:
  one fold whose value carries both sums, read by `billed_spend` and `ledger_cost`. **Re-test:** `git grep -n "counts_mirrored" -- promptpotter`.
- **Two `ValidationError` formatters remain beside `shared/errors.py::validation_reason`**:
  `application/datasets/draft_patch.py` (prefixes the field) and `PayloadInvalidError.refusing`
  (carries `ctx`). Action: both read `validation_reason`, the prefix and the context as its data.
  **Re-test:** `git grep -n "exc.errors()" -- promptpotter`.
- **A ledger subscriber that raises after `Admission._write` lands a charge leaves it in `_unlanded`**
  (`llm/spend_book.py`), so the book counts that part twice until the run ends — conservative, never
  under. Action: the record lands before any subscriber runs. **Re-test:** the outlives journey in
  `tests/test_spend.py` with a raising projection.
- **`CampaignOwner` reads `campaign.json` beside `Campaign`** (`campaign_store/store.py::campaign_owner`),
  a second, tolerant model of one file. Action: one model. **Re-test:** `git grep -n "CampaignOwner" -- promptpotter`.
- **Writers with no reader**: `dashboard.json` and `index.json` (checkouts nothing reads back), and
  `identity/blocklist_audit.jsonl` and `grants_audit.jsonl`. Action: decide whether each earns a
  reader or goes. **Re-test:** `git grep -n "blocklist_audit" -- promptpotter`.
- **One act on an arm writes two records**: `runner/arm_gate.py` and `scoring/query_loop.py` append
  an `InterventionRecord` and a `LeftComparisonRecord` for one act. Action: one act record both
  reads derive from. **Re-test:** `git grep -n "LeftComparisonRecord(" -- promptpotter`.
- **A deleted fork takes its `LeftComparisonRecord` with it**, so its arm reads `controlled` again
  while the fork's spend stays banked. Action: the exit outlives the fork. **Re-test:** delete a
  fork off an arm, then read the arm's `evidence`.
- **A campaign's root is found two ways**: `ledger_scan.py::scan_campaign_ceiling` parses the
  directory name (`store/layout.py::root_cycle_id`) where its writer takes `Campaign.root_hop`. Action:
  the reader takes the root hop. **Re-test:** `git grep -n "root_cycle_id(" -- promptpotter`.
- **A fold that raises is logged and folded past** (`store/read_model.py::feed_fold`), so a read
  serves a value missing that line. Action: refuse as `ledger_record` does. **Re-test:**
  `git grep -n "logger.exception" -- promptpotter/infrastructure/store/read_model.py`.
- **An unparseable job file is logged and skipped** (`jobs/registry.py::JobRegistry.list_all`), so
  occupancy undercounts a live slot; a parseable one that fails validation raises
  `JobUnreadableError`. Action: one refusal for both. **Re-test:** `git grep -n "logger" -- promptpotter/application/jobs/registry.py`.

### The member backbone

- **The dev loop serializes where it need not:** sibling gates queue whole runs, `next-build` peaks
  near 4 GB, builders spend most tokens on whole-file reads, Vitest's worker start timeout fails a
  green suite on a loaded host. Action: attribute each, then cut what our code wastes. **Re-test:** five builders plus one full gate with no reap and no gate waiting
  longer than its own run.
- **`initialization/session.py::LaunchState.ledger` is `Optional` while a round needs it**:
  `run_observers.py::build_run_observers` sets it after the state is built, so `runner/round.py`
  asserts presence and five other readers branch on `None`. Action: the run's state is built with its
  ledger. **Re-test:** `git grep -n "state.ledger" -- promptpotter/application`.
- **`node_context.py::Lineage.rounds` copies every round on each access** (`model_copy` to seal
  `optimizer_state`), and a member reads it per step. Action: seal once at a round's close, or serve
  a sealed view. **Re-test:** read `Lineage.rounds`.
- **`run_records.py::RoundEnteredRecord.rewound` is a flag keeping two acts apart** — entering a round
  and displacing what stood past it — that `ledger_scan.py::_Standing.feed` splits again. Action:
  two records, or one whose displacement is its own field. **Rides with:** the `arc/records` arc.
  **Re-test:** `git grep -n "rewound" -- promptpotter/domain/run_records.py`.

## Open — multi-arc

- **One text in two packs is two library rows under one id** (`application/library.py`): the id is
  content-addressed, the row is per pack, so a drop keyed by id cannot say which pack's provenance it
  carries. Action: a snippet's identity is (pack, id), or packs dedupe on install. **Re-test:** two
  packs sharing a text serve distinct row keys.

- **The crowning bar tests nothing a result can quote.** `application/scoring/selection.py::elect_round_winner`
  admits with no interval and no multiplicity correction, and `RoundResult.improved` reads any selection
  past round 0 as a promotion; at panel width `exact_p_floor(6) = 0.031`, Holm across three arms 0.094, so
  no corrected exact verdict reaches α=0.05. Call any "this round improved" provisional. Action: re-read
  separability on a run that used the tenant ruler, then choose a bar — never tighten one before. **Rides
  with:** [`../../paper/README.md`](../../paper/README.md) § The sequence, stage 3. **Re-test:** a
  tenant-ruler run's `index.json::final.rounds_to_separable` beside its `rounds_to_improved`.
- **The free tier offers what it refuses.** `application/free_tier.py::with_free_tier_layer` narrows each
  model's params but never the model axis, so a free check-in offers models Start refuses. Action: the
  per-value lists name only values known to fail; the spend ceiling bounds the rest. **Rides with:**
  the next check-in pass. **Re-test:** a free account's model menu equals
  `free_tier.yaml::task_models`.
- **`application/optimizers/paper_templates.py::marked` refuses a `<prompt>` block with no closing tag**
  (`gpt-oss-20b`, ~1 in 6 LEVI replies), and the INVALID card names potter's `l1_generate`. Action: an
  unclosed block reads to the reply's end; the card names the node that proposed. **Rides with:** the
  h2h-3 re-run ([`roadmap.md`](roadmap.md) § Optimizer tuning — the standing board). **Re-test:**
  `marked("<prompt>x")` is `None`.
- **NEXT — the default host pin is a trial with no verdict.** `domain/pipeline_overlay.py::pin_default_routes`
  writes each model's default host (`infrastructure/llm/registry.py::_MODEL_PROFILES`) into every
  node config; `route_order: []` opts out. **Decided:** a model keeps its pin only where its realized
  cache share and effective $/M beat running uncached. **Rides with:** the next supervised campaign on
  a pinned model, beside its `route_order: []` twin. **Re-test:** `SpendRollup.by_node` in each twin's `review.md`.
- **The responsive walk takes no Lighthouse score and never sizes the candidates card.** Action: one
  Lighthouse run at 375 px, recorded here; the card swept once a spec has such a campaign. **Rides
  with:** any layout change with a browser open. **Re-test:** `grep -n candidate webapp/e2e/walk/responsive.spec.ts`.
- **Compare cards are laid out for few wide columns**, not five thin stripes on a 16:9 display
  (`components/compare/ChannelCard.tsx`). **Rides with:** the next compare-pane pass, through `/design`.
  **Re-test:** five candidates at 1920×1080 without horizontal scroll.
- **Optimizer-model repair rate on L2/L3 structured output is unmeasured.** **Rides with:** the next
  supervised campaign that escalates. **Re-test:** the share of its L2/L3 calls that needed a parse repair.
- **The dashboard carries Next.js only as a build tool** (`output: "export"`; FastAPI serves
  `webapp/out`), and `eslint-config-next` pulls `braces` (GHSA-vfj7-8cjw-p6xm), failing `gate.py --only npm-audit`.
  Action: build with Vite, keep the `out` directory; settle first whether the rebuilt ESLint set
  pulls `braces` back. **Rides with:** the next webapp dependency pass. **Re-test:**
  `git grep -n '"next"' webapp/package.json`; `npm ls braces` in `webapp/`.
- **`tests/test_scoring.py::_peer_cycle` stands a `Cycle` up by hand**, banking a C0 report
  `application/origin.py::establish_campaign_origin` cannot produce. Action: a backend whose answers
  produce the asserted origin, started through run init. **Rides with:** any change to how run init
  scores the origin. **Re-test:** `grep -n "_peer_cycle" tests/test_scoring.py`.
- **The never / partly / always spread contradicts the headline beside it.**
  `application/scoring/measurement_log.py::_cycle_cells` pools every candidate the cycle ran, so
  `domain/cells.py::hit_spread` reads a sample only C0 saw as "never". One decision: the spread
  describes the subject (the selected searchpoint's own cells), or stays a search-coverage statistic
  labelled as such, with samples only weak candidates measured counted as *unchallenged*. **Rides
  with:** any change to `hit_spread` or the run card. **Re-test:** ask the operator which.
- **A scoring cell beats as an LLM call.** `application/scoring/sample_measurement.py::_send_cell`
  and `search_point_scorer.py::_claim_cell` append `LLMCallProgressRecord`s named
  `node="backend_scoring"` (no view has it) with a `call_id` of `scoring:{position}`, so the
  dashboard fold cannot key a beat's `inner` hop to its open cell: with look-ahead above 1,
  `CurrentRound.inner` names the latest-beating cell. Action: a cell beat carries its `position`
  and no node. **Rides with:** the next change to the heartbeat record. **Re-test:**
  `git grep -n '"backend_scoring"' promptpotter`.
- **An optimizer's measurement node is derived twice**: `optimizer_manifest.py::RoundPlan.measurement`
  (the gateway `default` walks) and `pipeline_resolve.py::measurement_node(view)` (the first
  `measurement` node of the view), which serves `OptimizerPipelineResponse.measurement_node` as
  nullable though no admitted manifest lacks one. **Rides with:** the next change to that response.
  **Re-test:** `git grep -n "measurement_node(schema.view)" promptpotter`.
- **`domain/export.py` carries no optimization spend or tokens**, so a reader cannot compute amortized lifetime cost ([`../research/external-constraints.md`](../research/external-constraints.md) § Cost reporting — binds every run from now, not only the preprint). **Rides with:** the next export shape change. **Re-test:** `grep -n "spend\|token" promptpotter/domain/export.py`.

## Open — optimizer tuning

**Rides with:** "The optimizer climbs" in [`roadmap.md`](roadmap.md) § Sequence.

- **PoBB locks in a leader the election then refuses (high-accuracy regime)**: seen on tenant
  `inbox-requires-action-v6` round 1 (parent 92.5% of 40). The round order puts the parent's misses first
  (`build_round_order`, "win-opportunities first"), so after 6 cells a candidate that fixes them reads
  `P(c>p)=93.8%` and `pobb` locks it in at `theta_election.admission: 0.85` (C1.2 locked at q6, C1.4 at
  q13, both 100%). The election then refuses both: `θ lift +0.400 logits, se 1.165`, `collapsed_band`
  caveat. The round's cells went to the three losers (38, 35, 27 cells) and the two leaders got 6 and 13,
  so the round held at the price of a full round. Two bars spelled twice: the lock-in reads P(best) on
  the cells drawn so far (front-loaded with the parent's losses), the election reads θ with its se over
  the whole ruler. Action: the lock-in asks the election's own question (would `theta_election` admit
  this arm now?), or a locked leader keeps buying cells until it does or the round's budget ends; a lock
  at q6 against a 92.5% parent needs at least the cells that could show one parent-level error.
  **Rides with:** any change to `early_stop` / `Selector.admission`. **Re-test:** rerun round 1 of
  `inbox-requires-action-v6__e1bda1`; an arm that is locked in is either elected or not locked.
- **L1 spends a round on zero candidates**: tenant `crm-extract-names__29f63f` round 2 (parent 97.5% of 40, fields
  graded with `fields_match`): `l1_generate_r2` returned 5 variants, all no-ops ("every variant is a no-op — zero
  candidates"), and the round went on to MEASURE 0 candidates. One call bought nothing, and the stall counter is the only
  thing that moves. Action: a no-op batch is retried once with the no-op named in the prompt, or the round escalates at
  once instead of closing empty. **Rides with:** any change to `potter/l1/candidate_source.py`. **Re-test:** resume that
  campaign; round 2 measures at least one candidate or names why it escalated.
- **A run reads every campaign's ledger, so one stale campaign crashes another**: `crm-extract-names__29f63f`
  crashed entering round 1 on `LedgerUnreadableError` for `inbox-requires-action-v6__e1bda1`'s ledger. The bill
  no longer reads a ledger and `land-data` refuses a tree holding one that does not load, but a desk tree is
  never landed, and `intelligence/earned_blocks.py` and `initialization/loop_start.py::_banked_instruments`
  still read every campaign's ledger after origin scoring is paid. Action: those readers refuse at mint,
  before origin. **Re-test:** with one pre-shape-change campaign on a desk tree, `new` refuses before spending.
- **A resumed walk re-walks a later proposer's arms**: `runner/round.py::bank_walk` banks the walk
  whole, and the opener's replay (`potter/l1/candidate_source.py::_generate_or_load`) returns it whole,
  so `mating` adds its children again and `locus_crossover` re-crosses the banked ones on a shifted rng;
  L1's replayed readout counts them as its yield. Inert at `mating.offspring: 0`. Action: a replayed
  walk returns its bank and runs no proposer, its readout banked beside it. **Rides with:** any change
  to `bank_walk`. **Re-test:** resume a potter round with `offspring: 1` between bank and close; the
  same arms walk.
- **A re-read arm has two names**: `runner/measurement.py::ReferencePairs.of_round` names an individual
  by the slot an EARLIER round walked it in, so a population member walked again (CAPO's
  `population_rejoin`, potter's held leader) reads `C2.1` on its pair and `C3.1` on the round card. **Rides with:** any change to
  `arm_name`. **Re-test:** a held leader's round-3 pair names its round-3 slot.
- **An operator-held prompt field is refused by potter alone**: `bench/children.py::carried_writes`
  writes any declared prompt field, so GEPA, CAPO, LEVI and an authored arm write a field
  `NodeSearchNarrowing.prompt_fields_held` holds; only `potter/validators/l1_strict.py::l1_prompt_fields_open`
  refuses it. Action: the child door refuses a held field. Open: may an operator's own authored arm
  write a field they held? **Rides with:** any change to `carried_writes`. **Re-test:** a GEPA child
  writing a held field carries no failure.
- **A declared bench is not the held-out count**: `CampaignConfig.dataset_split` `{bench: 100}` held out
  162 rows on the box's `hotpotqa-closedbook`. No shipped recipe adds it a `bench_only` tail and the
  loader dedups rows, so the 62 are in the box's tenant definition; the one code path holding out past
  `bench` is `domain/bench.py::partition_bank` holding declared `bench_only` rows beside the ranked
  ones, which L4 inner cells (`bench: 0`) and `justlogic/d234-held` are built on. Open: read the
  definition (`/potter-box`), then decide whether `bench` becomes the total. **Re-test:** a fresh
  `{bench: 100}` campaign on that definition holds out 100.
- **A pass with no cycle has no address**: `seed-screen` and `probe-reasoning` hold a job
  (`jobs/launcher/admission.py::paid_pass`, `hop=None`) but no ledger a `pause-cycle` reaches, so only
  Ctrl+C in their own terminal stops them; `noise-floor` lets its pause escape as `PassStoppedError`,
  a band over fewer than `k` rescores being no band. Open: a job-addressed stop at all five entry
  points, or Ctrl+C named as a CLI-only verb's stop. **Rides with:** any change to `paid_pass`.
  **Re-test:** stop a `seed-screen` from a second terminal.
- **A locked-in arm's catch-up cells are read by its race, never its round**: `potter/race.py`
  measures a prior on each later challenger's cells (`RaceCatchUpRecord`), but
  `runner/measurement.py::_walk_population` folds only the arm's own walk into `rows[key]`, so the
  election and scoreboard read the leader on the cells it stopped at. capo-pobb (CAPO ranks on shared
  cells) elects an arm PoBB eliminated. Action: a caught-up cell is a row of the prior's round reading.
  **Rides with:** any change to `CatchUpPool`. **Re-test:** a locked-in leader's round reading counts
  its caught-up cells.
- **The origin's `changes_description` embeds the absolute dataset path**
  (`application/origin.py::establish_campaign_origin`), so a decision record depends on where the
  checkout sits; `offline_run.py --against` another checkout reads gepa and levi as MOVED. Action:
  name the dataset, not its path. **Rides with:** any change to the origin record. **Re-test:**
  `offline_run.py --against` a base run from another directory reads gepa UNMOVED.
- **A lost mirror loses its money**: `infrastructure/llm/spend_book.py::SpendBook.mirror` logs a failed
  append, yet the nested record already says `mirrored=True`, so no bill or bank counts that call. Action: a
  failed carry raises, or the nested record is written only after the carry lands. **Re-test:** a root
  append that raises leaves the call counted on the account.
- **A check-in can write framing no node takes**: decomposition fills `upstream_context` /
  `downstream_context` for any pipeline, and `domain/opt_search_point.py::scored_prompts` refuses at mint
  where no prompt node declares `problem_description` (`promptpotter-self` hit it; its `task.md` dropped the
  two). Action: decomposition writes the splice fields only where the schema has a splice target.
  **Re-test:** a check-in on a params-only backend commits no splice field and mints.
- **A fork can name one change only**: `ForkProposal.unlock_schema_field_rename` is the sole config a
  rebase applies, so once it is unlocked a layer's fork is admitted only as a backtrack the engine
  picks. L3's measured asks (stand on an arm read but never adopted, e.g. C3.3; narrow a dead param
  axis) have no channel: standing on a non-elected arm needs the fork's first round to elect it.
  Action: decide which fork-only changes exist and name each as a `ForkProposal` member mapped to one
  `ConfigOverrides`/seed edit. **Re-test:** `git grep -n "class ForkProposal" -A20 -- promptpotter`.
- **The L4 estimator digest does not see the line series**: `CycleResult.bench_round_levels` now spans
  the line (`runner/bench.py::line_bench_series`), which `domain/l4/proxies.py` reads unchanged, so a
  rebased cell banked under the per-cycle series and one under the line series share an estimator
  digest yet differ in what their mean averages. Action: move the line's series into the hashed
  estimator, or re-key on the wipe. **Re-test:** `python scripts/offline_run.py --digests` before and
  after a line-series edit.
- **`AxisIndex.digest` computes what no panel renders**: `panels.py::_r_axis_memory` prints only
  `_AXIS_MEMORY_LABEL_ORDER`, yet `digest` also builds `dead_queries`, `discriminating`, `bottleneck`,
  `failure_group_insights`, `volatile` and `improvement_attribution` each round. Action: delete each key
  no reader takes, or render it. **Re-test:** `git grep -n "digest.get\|\.digest(" -- promptpotter`.
- **A critique cluster on unmarked rows is dropped into a log** (`l1/critique.py::spanning_clusters`), never
  healed. Action: reject at the parse boundary with the marked roster in reach, so the repair rung heals it.
  **Re-test:** a critique naming an unmarked row is re-asked, not thinned.
- **The fastest hosts refuse the `l1_generate` schema**: Cerebras and Groq (`openai/gpt-oss-*`) answer 400 to
  `L1GenerateOutput`'s inlined JSON schema — Cerebras wants `properties` on every object, Groq strict mode wants
  every property `required` — so the cheapest fast silicon cannot run an optimizer node. This was found by
  probing `L1GenerateOutput.model_json_schema()`, not yet the wire schema
  (`dispatch/l1_wire_schema.py::build_l1_response_schema`). Action: emit the wire schema in the strict-portable
  subset (closed objects, all-required with nullable optionals). **Re-test:** one `l1_generate` call pinned to
  Cerebras parses.

## Open — from the head-to-head arc

A leading **INVESTIGATE** means nobody has shown the item is real; answer that first.

- **`dashboard.json::run_limits` goes stale on `change-run-limits`.** It is copied from the cycle's
  last `run_wiring` record, while a mid-run `set-limits` writes `standing_budget` + an ack. Seen on
  h2h-7 `capo-b5` (`__530ab2`): `set-limits --max-rounds 10` armed 10 (ack `effect.armed`), the
  cycle stopped `rounds_budget` at 10 clean rounds, and the dashboard still served `rounds: 50` —
  while `cycles` and `evidence` read 10. One served fact, two sources. **Re-test:** after
  `set-limits --max-rounds N` on a live cycle, `dashboard.json::run_limits.budget.rounds == N`.
- **The head-to-head guard reads identical budgets as `differs`.** `head_to_heads/<id>.json` keeps
  `shared.budget: {}` when each arm's ceiling came by `--max-usd` (a standing budget, not the
  declaration); `evidence/head_to_head.py::pair_guard`'s `off_record` then compares every arm's
  `next_launch_budget` against `over_the_default({})`. h2h-7's three arms print the same
  `usd=0.2, rounds=50` under "budget DIFFERS", so no per-arm-ceiling head-to-head reads
  `controlled`. One budget, two homes. Sent to `arc/n-arm-mint-v2` (#58), which owns the record.
  **Re-test:** `evidence` on h2h-7 reads `controlled`.
- **A head-to-head's arms may diverge on budget mid-run, and only `evidence` notices.** `set-limits`
  on one arm moves it alone (by design), and `evidence` then marks every pair `x` (`Differs on:
  budget`) — but nothing at the `set-limits` door says the arm is leaving the head-to-head's
  declared budget. Open: refuse it on an arm, or serve the divergence where it is made.
- **A reserved cell's bill is on no ledger** (`Connector.holds_own_sends` writes no hold), so
  `infrastructure/llm/spend_book.py::unreported_on` can re-teach a book its sends but never the cell.
  Action: a hold record per reserved cell. **Rides with:** any change to `spend_book.py::reserved`.
  **Re-test:** `grep -n "emit_spend_hold" promptpotter/infrastructure/llm/spend_book.py`.
- **The bench pass runs at look-ahead depth 1**, because depth is a press a round spends and
  `runner/bench.py` runs outside any round. **Decided:** the pass takes a depth of its own. **Rides
  with:** any change to `runner/bench.py`'s walk. **Re-test:** time one bench pass against its cell count.
- **A resume from a pause at the origin boundary runs round 0 again** (a second round-0 election,
  a second `l1_critique`). Open: whether the re-send should read the banked `optimizer_state`.
  **Rides with:** `DashboardCheckout.over` or the origin round in `runner/run_prep.py`. **Re-test:**
  `scripts/offline_run.py --rounds 1 --rows 60` — `decisions MOVED` on the resume line.
- **INVESTIGATE — does an errored cell bias a head-to-head?** `gpt-oss-20b:nitro` can end
  `finish_reason=error` then pass one schema repair; unknown whether the row banks the answer or a
  miss. **Rides with:** the next head-to-head read. **Re-test:** per arm, the hit rate of error rows
  against the arm's other rows.
- **INVESTIGATE — CAPO's survivors are ranked on another reading than the reference ranks them on.**
  `population` keeps μ best-first by mean objective on the cells all survivors measured; promptolution's
  CAPO sorts survivors on raw score over every cell each has been evaluated on, cached across rounds,
  so an incumbent's deeper blocks from earlier races count. After a one-block race (the paper's rule,
  `candidate-elimination.md` § CAPO's race) the two can crown differently. Open: which reading the
  bench compares. **Rides with:** any change to `capo/members.py::_rank_survivors`. **Re-test:** on a CAPO
  run with offspring < μ, rank one round's survivors both ways.
- **"Calls held equal" has no axis to hold.** `domain/spend.py::Budget` meters usd, tokens, rounds and
  wall clock; the roadmap and `external-constraints.md` § 4 hold calls equal, `paper/README.md` holds
  spent tokens, and `paper/plans/` holds USD under the arm's `search_incurred` meter, so two arms of one
  point make the same calls only at one cell price and one optimizer cost. Action: one matched axis,
  decided; if calls, a cells axis on `Budget` the search meter binds. **Rides with:** any change to
  `Budget`'s axes. **Re-test:** `price-head-to-head paper/plans/comparability.yaml` — do one point's
  `search_cells` differ by arm?
- **A pinned `determinism.seed` makes every replicate one draw.** `PINNED_LOOP_KEYS` pins
  `optimization.determinism` across a head-to-head, and `bench/cycle.py::Cycle.rng` seeds every
  member off the pin instead of the campaign id, so N replicates of an arm on a pinning dataset make
  the same draws and read as a tight interval. Action: a replicate's identity enters the seed under a
  pin. **Rides with:** any change to `Cycle.rng` or `PINNED_LOOP_KEYS`. **Re-test:** two replicates under a
  pinned seed send the same first `l1_generate` request.
- **The $0 price opens a backend session** (`application/head_to_head_price.py::price_head_to_head`
  through `jobs/launcher/head_to_head.py::dataset_arms`) only to resolve the node models and the
  partition, so a stopped backend refuses a read that makes no call. **Rides with:** any change to
  `dataset_arms`. **Re-test:** price a plan with the backend down.
- **An optimizer's round price is its name's, not its treatment's**
  (`application/head_to_head_price.py::optimizer_round_price`): the three `escalation_ladder` arms read one potter
  price, though an `l1` round sends fewer optimizer calls than a `full` one. Action: key it on the
  treatment once enough campaigns of each have run. **Re-test:** the ablation's optimizer line names
  one price for three ladders.
- **INVESTIGATE — a CAPO arm is admitted on its round CEILING** (`preflight.py::check_cap_funds_round`
  over `round_cells_ceiling`: 4,230 cells on 310 search rows), which its race rarely spends, so a
  budget point must fund the worst case to mint CAPO at all. **Re-test:** a finished CAPO round's
  cells against its ceiling.
- **`notebooks/bbeh_potter.ipynb` states another protocol than `shared_config.py`**: its Methodology
  cell and Cell 2 say the test set is the ~4,060 non-mini rows and the default target mistral-small;
  `load_and_split` tests on the second 10 mini rows per task. Action: the notebook states what
  `shared_config.py` runs. **Re-test:** `grep -n "non-mini" notebooks/bbeh_potter.ipynb`.
- **A selection is still made in three places outside a launch.** `config_map`, `draft_patch` and
  `checkin` each call `select_optimizer` themselves. The launch path now resolves one selection and
  hands it down.
- **`CommandAckRecord.effect` is typed, but nothing reads it.** A writer with no reader is not a state:
  serve it or delete it.
- **`embedded_run.run_campaign` steers one branch with two flags:** a `None` `declaration` plus
  `assert resumed is not None`.
- **A moved capability or backend revision alone passes a resume.** It is named as a drift cause only
  when the origin hash also mismatches.
- **`round_cells` has two meanings for one draw:** potter's unclamped value, read by L4, verify and the
  overlap, and the backbone's ceiling panel, clamped to the search split. Also, `RoundPlan.walk` and
  `round_walks` each decide which walk counts as a round of its own.
- **`gate.py --only` queues behind other worktrees' gates** (`queue_behind_siblings`), so one
  regeneration waits minutes. `scripts/build_ts_types.py` cannot run under `-m`, because it imports
  `processes` from `scripts/`. A bare `mypy` run shows errors in `evidence/subjects.py` and
  `tests/test_scoring.py` that the gate's mypy passes.
- **The library's duplicate-text check is written twice**: `application/library.py::bank_snippet` (a
  no-op) and `infrastructure/store/account_packs.py::bank_into_account_pack` (under the lock).
  `SnippetLibrary`'s empty copy still says "installed".
- **A cross's arity is declared nowhere**: `crossable` unpacks exactly two parents. `donated_loci` is
  derived by verbatim equality with the donor, not recorded on the lineage, so an LLM edit that lands on
  the donor's value reads as donated. The capo offline bench turns on one seed: lift +0.700 or none
  after a draw-order change, so the fixture cannot tell a mating-law change from a reshuffle.
- **`config/prompt_blocks.py` is hashed whole into every optimizer's `treatment.source`**
  (`shapes_optimizer_prompt(__name__)`): the per-pack refusal, a shelf read with no prompt text,
  re-keyed every treatment. Action: mark only the units a prompt reads, as `optimizer_manifest.py`
  does. **Re-test:** edit `PackShelf.refusals`; potter's treatment key stays.
- **Two packs holding one snippet text raise a bare `ValueError`** (`config/prompt_blocks.py::PackShelf._held`)
  where every other shelf failure is a `PackRefusal` or a typed refusal of the knob: a `packs` knob
  naming both reaches the operator as an error, not a sentence. Action: `BlockPoolInitializer.refusal`
  names the repeated entry. **Re-test:** two account packs with one text, both named; the draft serves.
- **A detached arm's job is the spawner's until its process adopts it**
  (`jobs/launcher/run_job.py::spawn_job`, `jobs/registry.py::_orphaned`): a `head-to-head`
  terminal killed between the spawn and the adoption leaves jobs the sweep releases, so the
  child's `adopt` refuses and the arm's minted cycle never runs. Action: the child holds the producer
  lock before the spawner returns. **Re-test:** kill the terminal right after its `arm …` lines; each
  arm's cycle runs or reads released with its reason.
- **Two acts on one fresh head-to-head id race its record**: `jobs/mint.py::arm_plans` reads the
  standing record outside any lock, so two concurrent acts each declare their own `SharedPart`.
  Action: declare the record under the workspace lock and plan the second off it. **Re-test:** two
  `mint-head-to-head` on one fresh id at once — one is refused off the record.
- **A refused launch still spends its rate tokens** (`jobs/quota.py::check_launch_quotas`): the
  bucket is debited before the slot checks that can still refuse, so a head-to-head refused
  `machine_busy` costs all its arms' tokens. Action: debit on admission. **Re-test:** refuse N
  launches on a full box; the next is not `rate_limited`.
- **Two launch doors**: `jobs/launcher/launch.py::launch` (one run, may queue) and
  `jobs/launcher/head_to_head.py::admit_head_to_head` (N arms, never queued) each request slots and
  admit. Action: one door admitting N, queueing only when N is 1. **Re-test:** `git grep -c
  "check_launch_quotas(" -- promptpotter/application/jobs/launcher/*.py` is 1.
- **The origin's `changes_description` embeds the checkout's absolute path** (`Origin from
  /…/origin.yaml`): a served individual names the host's filesystem, and two checkouts of one commit
  read `decisions MOVED` in `scripts/offline_run.py --against`. Action: name the dataset-relative
  path. **Re-test:** `grep -c '"Origin from /' <home>/<optimizer>/decisions.json` is 0.
- **The controlled offline leg's arm-memory count moves between identical runs** (`arm potter: memory
  holds 380` then `375` own answers on one tree): a cell coalesced with a twin in flight is answered
  from memory, not sent. A printed reading, not a decision. Action: count filed answers, not distinct
  ones. **Re-test:** two `--controlled` runs of one tree print one count.

## Open — renames the operator picked (2026-10-29, low priority)

**Rides with:** a pass touching the name, or the post-2026-10-30 wipe for on-disk keys. Names are root
`CLAUDE.md`'s rule; each rename lands with its doc and `CLAUDE.md` row in one commit. **Re-test:** the old
word's `git grep -c` for that sense is 0.

- **`candidate_library` → `answer_terms`** (the target's answer-term list, beside
  `DraftCampaign.answer_space`), and `PoolSource.LIBRARY` → `PACKS`: the route
  `/datasets/draft/candidate-library`, `EditDraftPatch` and the dataset file move, and
  `shared/hashing.py::content_hash`'s key re-keys root ids.
- **"course" → "cycle" in the served and doc layer** (`HierarchyCourse`, `level: "course"`,
  `/hierarchy/{course}`, `ids.ts::courseAddressOf`); `CycleHop` stays an element of a `CyclePath`.
- **"line" in its live-cycle sense → `live_cycle`** (`command_kinds.py::LinePayload`,
  `BenchLine.on_line`, `domain/bench.py::LineRun`); "selection line" is never bare "line". A command
  payload kind, so ledger shape.
- **`nodes.py::Panel` / served `ArmPanel` → `RoundSamples` / `ArmSamples`**, the L4 roster → `inner_panel`;
  "panel" stays the evidence section an LLM reads.
- **"slot"**: prompt slot → prompt field, layout slot → region, `domain/arms.py::ArmSlot` →
  `ArmPosition` (ledger field), bank `slot_of` → `position_of`. The layout key is optimizer-prompt text.
- **`CycleSeed*` / `ForkSeed` → `CycleStart*` / `ForkStart`**, so "seed" is the RNG seed alone. A
  ledger record.
- **"member" for an individual → "individual"** (`run_standing.py`, `origin_panel.py`); "member" is the
  optimizer node; "candidate", an individual at a round address, is owed to the Vocabulary row.
- **The served hierarchy discriminator `level` → `entry_kind`**
  (`git grep -c -w level -- webapp/components webapp/lib`).
- **The READING node → reader node** in `resolved_schemas.json`'s layout prose: prompt text, so a
  re-measure.
- **draft vs check-in, by rule, no rename**: "check-in" is the resolver-and-gates process, "draft" the
  object (`DraftCampaign`); owed to root `CLAUDE.md`'s Vocabulary row.

## Open — from the structure pass

**Rides with:** any change to the file named. **Re-test:** the `file::symbol` still holding it.

- **Seen in the `lib/` regroup**: `lib/state/registry.tsx` and `lib/workspace.tsx` import `derivations/hierarchy` past the barrel
  to dodge a cycle (barrel → search-point → poll → workspace); `lib/theme.ts` mixes chart setup with
  the theme preference.

- **The origin gate holds forever** (seen on the box, 2026-11-01): a justlogic cycle launched from the
  webapp failed every origin cell (`llm_only`: `output_schema` set, `answer_field` not) and then held at
  `origin_gate_r0` for 20 h, alive after every window closed, appending a "still waiting" line every
  10 s (ledger 1.7 MB), and blocking `update.sh`. An origin with no scored cell has nothing to gate.
  `campaign_config.py::origin_gate` says it HALTS and a plain `resume` overrides; `runner/origin_gate.py`
  HOLDS a live process instead, and its three answers repeat run verbs (`abort` ≈ leave it ended,
  `proceed` ≈ `resume`, `rescore` ≈ `resume --from 0` force-fresh). Decide: halt, or keep the hold.
- **Two "waiting" predicates** — `useRemoteModel.ts::waitNote` and `lib/derivations/time-ray.ts`.
  **Decided:** a call is open AND its wait target is known; serve `dash.wait_line` once.
- **Four seams owed a re-read:** `webapp/lib/poll.tsx::CycleStreamState` fields nothing reads;
  `webapp/lib/hooks/useCompareWithOrigin.ts` picks the best pointer; `webapp/lib/derivations/spend.ts::readSpend`
  sums spend; `presentation/cli/commands/resume_command.py::_steer_overlay` builds its own overlay.
- **`domain/phases.py` is two fifths stop-reason table**; a split was measured and declined, a
  rename re-points ~46 files. **Rides with:** a pass that moves the table.
- **A cold server folds every ledger before the first page answers**: the first
  `GET /hierarchy?depth=1` waits on a full `LEDGER_FOLDS` pass over every `projects/` ledger, and
  the browser shows a sidebar stuck on "loading…". On 2026-10-30, with 91 MB / 129k lines in
  `projects/`, it took 33 s cold beside the boot sweep and 1.1 s warm (`.scratch/cold_real.py`).
  `SpendRollup`'s private-attr factories and per-record `setdefault(k, Model())` cost a further
  70–90 s before they were cut. Two shapes remain:
  - The `run_state_stamps` ETag is the first opener. To learn the launch claim, its "stats only"
    stamp folds the whole roster.
  - `jobs/reaper.py::periodic_sweep` runs at boot, beside the first page. Over 58 ledgers it folds
    23 s, about 10 s of it on L4 inner-sandbox ledgers that no page reads.

  The fold itself is the root: pydantic validates each screened line on every cold start. A fold
  snapshot keyed by `file_sig` would make a restart pay only the tail.
- **Seen in the parsimony pass (2026-11-01)**:
  - **One append rebuilds every campaign's entry**: `application/hierarchy.py::entries_version` and
    `hierarchy_subtree.py::subtree_version` are store-wide. The shape is a per-campaign version and an
    entry memo; what a shared input (the ruler, an account's spend) invalidates is the decision.
  - **Activity rows are `Said` rows under other names**: `domain/activity.py::ActivityItem` and
    `DecisionFact` carry label, tone and detail, with kind glyphs (★ ◆ ↻) beside the CSS tone glyphs,
    and `chat/LiveSegment`'s `<dl>` honours only `danger`. One `Said` row reaches every constructor,
    `LiveSegment`, `TimeRay` and the event contract.
  - **Off-screen view columns stay mounted and poll**: `shell/Views.tsx` mounts every open column, so a
    phone on the Dashboard preset runs every pane's reads at once. React's `<Activity mode="hidden">`
    per column, on one IntersectionObserver, stops them and keeps their state. The fork: a column
    swiped into is blank until its reads land.
  - **Run data past `MAX_PATH` is read only where `store/io.py` reads it**: `io.py` writes through
    `_long_path`, but `store/read_model.py::file_sig`, `_complete_lines` and `_LedgerTail._holds_last`
    (among many: `grep -rnE "\.stat\(\)|[^_.a-z]open\(" promptpotter`) read bare, so a ledger past 260
    characters reads as absent and a deep L4 store lists no course. This desk has long paths off. Root:
    spell the data root once in its long form (`config/paths.py`) so every derived path carries it;
    the cost is every comparison with a short-spelled path (`tmp_path`, `REPO`). The Linux box is unaffected.
  - Unverified, for the next change to each file: `shell/CriticalAlertBanner` builds `Said` rows in the
    browser and re-sorts `TONES` for `pinned`; `ViewTabs.show` is many `open`/`close` calls, not one
    workspace act; `cycle_files.list_cycle_files` walks all of `.runtime/` and filters after
    (`iter_files(skip=)` exists); `domain/activity.py::ActivityFeed` falls back to `"run error"` where
    `run_said` has none; `iter_family_courses` reads each index twice; `list_hierarchy` re-calls
    `line_config` per non-holder course; `FileTree` mounts a 2 s `useActivePointer()` poll for one
    failure line; a `/hierarchy` 200 replaces every entry object in `lib/state/registry.tsx`.
- **The collapse gate cuts a hedging arm on noise**: `pobb/checks.py::check` →
  `domain/scoring.py::is_answer_collapsed` reads one repeated label over `n_min` answered cells as
  "no measurement". The panel walk runs hardest-first, so its first cells can hold one or two truth
  labels. A task model that answers `Uncertain` on about 75% of cells then trips the gate by chance in
  about 18% of arms (0.75⁶).
  - Seen 2026-10-30 in L4 inner `justlogic.d234__731eda`: C1.2 was cut at 0/6, all `Uncertain` and
    every one parsed. The round's own n_min top-up read it at 0.393 against C0's 0.5 over the 28-cell
    origin panel.
  - The bar and the sidebar lead with the 6-cell 0%; the 39% shows only as the overlap series.
  - Collapse must be judged against the truth labels the cells hold, and against the parent's answers on
    the same cells. An engine decision: prove it with an offline run.
- **The Candidates card shows empty axes while its lineage read is pending**: `CandidatesCard` gates on
  `dash` only. On a cold first page `viewedNode` is undefined, so the chart draws no bars and no
  `ReadState`, and reads as broken. Gate on the lineage read's own state.
- **The server's shutdown notice counts runs it does not host**: `main.py::_telegram_shutdown_notice`
  reads `JobRegistry.list_running()`, which holds the machine's CLI runs too. A server stopped beside a
  CLI campaign reports it "stopped … in flight" and says "Resume from the campaign once it is back up",
  but the run continues. The notice must name only the jobs this process owns. Seen 2026-10-30 against
  `promptpotter-self.panel__606cc9`; the same stop logged `ASGI callable returned without completing
  response` for its open `events:subscribe` stream.
- **One node's knob gates four nodes' pool access**: `potter/dispatch/facade.py` declares `BlockPool` and
  `DemoPool` draws on `L1GenerateKnobs` for every hub member, so the check-in shows `l1_critique`,
  `l2_context` and `l3_plan` "open · l1_generate.block_access". Either each reader owns its access or
  the pool is read once by the hub; the served row must say which.
- **The check-in's optimizer graph ignores the draft's overlay**: `resolve_pipeline_for_optimizer`
  resolves the manifest with `{}`.
- **Rename candidates, not yet picked** (one word, two senses each):
  `evidence/head_to_head.py::HeadToHeadRow.swapped_in` beside the manifest `swap`;
  `application/library.py::library` beside `candidate_library` (rides the `candidate_library` →
  `answer_terms` rename); `domain/pool.py::PoolOffer` / `offered` beside
  `datasets/startability.py::offered`.
- **`Scope` says nothing on members**: every member knob is `Scope.POLICY`, and a knob's `NodeConfigParam` serves neither
  scope nor estimand.
- **Seen building the setup screen**:
  - `OptimizerSetupInner` reads the optimizer from the draft and from `useConnector().optimizer`, which
    are one value through two doors.
  - An edited optimizer-prompt field cannot be reset: `OptimizerNodePrompt` serves no template text and
    `campaign_config._merge_dicts` keeps a null as a value.
  - `LibrarySnippet` serves no `presumes`, so the library cannot show that a snippet the chain refuses
    never enters a pool.
  - Smaller: `title=` used as teaching prose in `NodeDetail` and `OptimizerCard`;
    `CheckinLoadingWindow` keeps its own `setInterval` outside `lib/cadence`; `.setup-step` is styled in
    `chat.css`.
- **Seen in the phone-first setup pass**:
  - A changed knob value names no source: the resolver keeps none for `optimization.nodes` layers
    (dataset block, draft override, cycle seed), so the row says only "Changed from default".
  - `locus_crossover` is listed as a maker with nothing upstream making two-parent children; an
    arm-editing maker's on/off belongs on `nodes.Proposer` as a member-declared rule.
  - The "adds no arms ⇒ off" rule sits in `pipeline_resolve.py`, not on `SelectedOptimizer`.
  - `HoverCard` opens on tap only through focus (fragile on iOS); the check-in picklist button's
    accessible name runs slug and meta together; two open check-ins share one slug.
- **Seen drawing the pools in place**:
  - A named pack that leaves the shelf makes `BlockPoolInitializer.held` raise `block_pack_not_found`,
    so the draft read itself fails instead of offering the pack to unname.
- **Seen splitting the manifest hash**: the scan now hashes every top-level definition the marked units
  reach (`source_scan.py::optimizer_prompt_shapers`), so most marks are redundant roots kept from when
  each reached name had to be marked by hand; only a unit read through an instance or a registry (as
  `SelectedOptimizer`) needs one. The `application/` modules that still carry
  `shapes_optimizer_prompt(__name__)` hash whole where the reached units would do. **Re-test:**
  `git grep -n "shapes_optimizer_prompt(__name__)" -- promptpotter/application`.
- **Seen building the prompt workspace**:
  - An override cannot be removed, only overwritten: knob Reset writes the declared value and a cleared
    prompt field writes `""`, so the draft stays "edited". The overlay merge needs a delete.
  - `PipelineSetupSection` picks a default node in a `useEffect` with a ref latch: a second door into the
    shared node selection.
  - The packs copy each snippet's arXiv id and method into its `tags`, so Tags repeats Source and Method.
  - `LibrarySource` fakes "authored" as a cited work with no author or year.
  - `ui/MenuCheck` takes a `title=` prop; filter buttons are ~28px, under the 44px touch target.
- **Seen framing snippets in a prompt**:
  - `.prompt-snippet-input` re-declares the field skin's border and background inside the frame.
  - `config/prompt_blocks.py` says "render order" for `PROMPT_FIELDS` order (a hashed module).
  - `vitest.config.ts` loads as CommonJS (Vite warns on every run).
- **A side worktree cannot pass the full gate**: Turbopack refuses the `node_modules` junction
  ("points out of the filesystem root"), so `next-build` and `playwright` never run there, and pytest's
  `compileall` breaks on long Temp paths. A rebuilt commit series is therefore gated without them.
  **Re-test:** the full gate green in a worktree.
- **`p_served`'s ceiling is slack** (175 measured under 176 since 3dfce02f0); the gate allows slack, so
  the ratchet never tightened. **Re-test:** BASELINE equals the measured count.
- **The installed webapp packages drift from `package.json`**: `npm ls` reports `next@16.3.8` against
  `16.4.1` and `vitest@4.1.11` against `^4.1.12`, so the gate builds with versions the lock does not
  name. **Re-test:** `npm ls --depth=0` clean.
- **`scripts/offline_run.py` imports the gate for `RamBudget`**: the script the gate runs loads
  `gate.py` (and through it `tests_owning`, `yaml`) in every child, while `OFFLINE_RUN_READS` names
  neither file. Fan-out sizing sits beside `processes.py::free_mb`. **Re-test:**
  `git grep -n "from gate import" -- scripts`.
- **A killed push leaves live links into the main tree.** `gate.py::_tip_checkout` links `.venv` and
  `webapp/node_modules` into `%TEMP%\pp-push-*`. A hard kill skips the unlink, so the temp dir keeps
  junctions that a later recursive temp delete could follow. Action: the next `--push` sweeps stale
  `pp-push-*` (unlink, then `git worktree prune`). **Re-test:** kill a push mid-run, then run
  `--push` again and list `%TEMP%\pp-push-*`.
- **`scripts/offline_run.py --controlled` crashes on main** (fd51d06 and after): the potter arm's
  `_variant_edit` asserts `no framing to cite`, since the controlled leg's prompt holds no
  `task_context`, so its arm-exit check (`run_controlled`: a fork takes its arm out) never runs.
  Action: the leg declares the framing its fake proposer cites. **Re-test:**
  `PROMPTPOTTER_HOME=<dir> python scripts/offline_run.py --controlled` exits 0.
- **Seen bounding the perimeter** (2026-10-29):
  - Two doors into account state: `PATCH /auth/user-settings` and `POST /auth/accept-terms` (also
    `/datasets/ingest` and the check-in draft opens) write outside `CommandDispatcher`, against
    access-model's "all writes flow through one dispatcher". **Re-test:**
    `git grep -n "@router.\(post\|patch\|delete\)" -- promptpotter/presentation/api/routers`.
  - `/docs` serves Scalar's HTML, which loads its script from a CDN, while `ResponseHeadersMiddleware`
    gives every non-API path `_WEBAPP_CSP` (`script-src 'self'`): the page likely renders blank.
    **Re-test:** open `/docs` in a browser and read the console.
  - `auth.py::callback` warns "HOST_ADMIN_EMAIL is unset" also when it is set and that admin has not
    signed in yet. **Re-test:** set it, sign in as someone else, read the log line.
  - `POST /campaigns/{id}/fork-preview` is a read on a write verb, so it spends the write bucket
    (`quota.py::refuse_write_rate`), not the read one. **Re-test:** `git grep -n "fork-preview" -- promptpotter/presentation`.
  - `quota.py::refuse_read_rate` takes a principal id and meters the host, while the launch, verb and
    write buckets take the identity and exempt it. **Re-test:** `git grep -n "def refuse_.*_rate" -- promptpotter`.
- **Seen building the host coupon** (2026-10-29):
  - Every connector declares its cells the box's to pay (`Connector.cell_payer`): none can send on the
    account's key, so an own-key account past its coupon is refused any run with a backend. Action: a
    connector that takes the account's key per call declares `"user"`. **Re-test:** an own-key account
    at a used-up coupon runs one TermNorm cell.
  - `api_keys.json` holds account keys in plaintext at rest, beside `user.json`. Action: encrypt under a
    box secret, or name the file in access-model's at-rest table. **Re-test:** `cat` one.
  - `registry.get_llm_client` caches up to 256 clients by key, so a removed key's secret stays in
    memory until evicted; resolution re-reads `api_keys.json` each send, so it is never SENT. **Re-test:**
    remove a key, then read `get_llm_client.cache_info()`.
  - A resumed cycle's prior metering counts as the box's in its book's coupon reserve
    (`SpendBook.take_up` reads only unreported own-key holds), so a resumed own-key run reserves more of
    the coupon than it draws. Conservative, not a leak. **Re-test:** resume an own-key cycle and read its
    `off_coupon_usd`.
  - `TokenUsageRecord.cached` and `key_source is None` are two spellings of one fact (a replay no
    wallet paid). Action: one field. **Re-test:** `git grep -n "key_source=None" -- promptpotter`.
  - `coupon.py::keyed_client` folds the account's charges on each send the box's key pays (it reads
    the remainder fresh). Action: the run's book carries the remainder it was admitted against.
    **Re-test:** time one send on an account with 100 cycles.
  - Restamp drops `User.spend_budget_usd_total` / `token_budget_total`: a hand-raised account must be
    re-granted as `grant.json`. **Re-test:** `ls .promptpotter/projects/*/grant.json` after a restamp.
  - The refusals in `coupon.py` / `errors.py` name a webapp pane ("Account → API keys"): renaming the
    pane means editing Python strings. **Re-test:** `git grep -n "API keys" -- promptpotter`.
  - `LifetimeSpend` carries no token count, so the coupon's token side shows left / face, never used.
    **Re-test:** the Host allowance section with a token-bound grant.
  - `CommitInput` forces `autoComplete="off"` and, after a failed commit, will not resend the same value
    on Enter. **Re-test:** fail a key PUT, press Enter again.
  - `AccountSpend` refreshes the coupon both on its poll and from an effect watching the sidebar's
    `billed_usd`. **Re-test:** `git grep -n "couponRead" -- webapp/components`.
  - The onboarding dialogs use raw `<button className="login-button">`, not `ui/Button`; and
    `AccountUsageTab::LimitControl`'s note ("runs on their provider key") predates own keys.
    **Re-test:** `git grep -n "login-button" -- webapp/components/onboarding`.
- **The offline legs rest on fake-data draws keyed by the whole node config**
  (`offline_world.py::FakeBackend`): an unrelated config value reshuffles which rows saturate, so a
  leg's premise must be pinned (`EVERY_L2`, `REBASE_HARDNESS`), never inherited.
- **Seen removing the migrations** (2026-10-30):
  - `LLMCallRecord.payload_kind == "synthesized"` and `usage is None` state one fact twice.
  - `jobs/logs/{job_id}.log` (`launcher/run_job.py`) outlives its job: nothing deletes it.
  - `family_ray_queries.py::_RayTail` gets a line no activity arm accepts as a `feed_fold` log, not a
    `LedgerUnreadableError`: `LineIndex` validates JSON only. Both tails skip an unknown
    `record_type` silently.

## Bypasses — held for one holistic pass

**A send made on our behalf re-derives what `_admitted_send` decides for our own.** Patched singly,
each fix is one more copy; the remedy is the upstream redesign.

- `infrastructure/llm/send_pacing.py::SendBudget` stops at the relay's door: TermNorm's
  `_MAX_ATTEMPTS` and terminus-2 × litellm retry on their own budgets. **Re-test:**
  `grep -n "_MAX_ATTEMPTS = " <termnorm>/backend-api/core/llm_providers.py`.

## Blocked

- **TermNorm's live datasets (`llm_only`, `entity_profiling`, `llm_ranking`) are refused at parse**:
  TermNorm's `_enrich_with_registries` must carry `meta["fields"]`, its prompt metadata declare
  `fields`, and `direct_prompt` gain a `prompt_info`. **Blocker:** the operator's go to edit the
  TermNorm checkout. **Re-test:** `python -m promptpotter new llm_only` parses its pipeline.
- **Two readings wait on the operator:** a declared slot before `rewrites` + `field` fold into one
  knob; which ruler `ab_replay` reads.
- **`infrastructure/llm/anthropic.py::AnthropicClient` cannot run a node on a current Claude model**
  (no effort mapping, sends `temperature`, never sends the schema, stale `max_tokens`). **Blocker:** no
  Anthropic credential. **Re-test:** `settings.ANTHROPIC_API_KEY` non-empty, then `probe-reasoning`.
- **A head-to-head's arms do not share one optimizer model.** **Decided:** every arm on one model,
  each peer's swap a declared deviation. **Rides with:** the next head-to-head mint. **Re-test:**
  `grep -n "model:" promptpotter/assets/optimizers/*/pipeline.yaml`.
- **Archive hygiene — build before the next bulk delete.** **Re-test:** `compact-archive inventory`.
  (1) **Reclaim**: a dataset-scoped destructive `delete` counterpart, dry-run by default, refusing
  while a producer can append, naming what it would strand. (2) **Attribution**: an L4 outer cell
  stamps the runs its inner campaign produced when it spawns them; never re-file a backfill. (3)
  **The reach map**: the campaign family left, the archive partitions it reaches right, the
  load-bearing column what is shared outside the selection.
- **Cross-repo (TermNorm `backend-api`):** a `/version` endpoint, then bump
  `connectors/termnorm.py::_EXPECTED_REVISION` (re-test: it is `None`). A co-owned backend fix replays
  stale results because neither cache keys on backend revision: fold the revision pin into the
  measurement-cache key (re-test: grep for `--fresh` or a revision term on the key).
- **No inbound REST credential** — owned by [`../developer/stable-api.md`](../developer/stable-api.md)
  § 8; owed here: the credential and its `submit → poll → fetch` examples. **Re-test:** a bearer or
  API-key reader in `promptpotter/presentation/api/`.
- **Swapping a model means hand-editing two `pipeline.yaml` lines.** **Blocker:** a swap verb.
  **Re-test:** one in `presentation/cli/commands/`.
- **`infrastructure/llm/json_parse.py::try_groq_json_validate_repair` banks a missing count as an
  empty account.** Never estimate from content length. **Blocker:** `TokenUsageRecord` has no
  unknown-count dimension. **Re-test:** a `provider: groq` in any dataset pipeline.
- **`_rebank_on_branch`'s re-bank has never been observed.** **Re-test:** repair a fork; each
  corrected round carries its own `round_restated` record.
- **The `seed` provenance layer has never been stamped by real data.** A `resume --steer NODE.PARAM=VALUE`
  fork exercises it. **Re-test:** from the checkout root, `grep -rh cycle_seed
  .promptpotter/projects/*/campaigns/*/cycles/*/.runtime/ledger.jsonl | grep -c '"pipeline_overlay": *{'`.
