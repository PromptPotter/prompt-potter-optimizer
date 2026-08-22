# application/ — orchestration layer

The use-case layer between `domain/` (pure types, frozen models) and
`infrastructure/` (I/O, persistence, LLM clients). One entry point —
`runner/` — coordinates everything; subpackages each own a coherent
slice of orchestration.

## Layer rule

**`application/intelligence/` MUST NOT import from `application/optimization/`.**
The optional sensitivity scan and the optimization loop both *consume*
intelligence; intelligence depends on neither. It carries no test because a
**module-level** back-import raises on the spot — `optimization/` imports
`intelligence/` at module level throughout, so the cycle closes at import time
([`../../tests/CLAUDE.md`](../../tests/CLAUDE.md) § Structural invariants). A
**function-local** import does not raise, and that is precisely what a circular-import
error tempts you into: moving an import inside a function to make the error go away
does not fix the cycle, it hides this rule.

## Subpackages

| Subpackage | Owns |
|---|---|
| `initialization/` | **Run init** — the ordered chain from a `new` / `resume` invocation to the first round: `init_services` (stores, LLM clients, connectors → `Session`) → `populate_session_scoring` → `init_cycle` (resume or create) → `init_optimization_loop` (preflight, observability, `INIT.exit`, hand off to the round loop). Pipeline-discovery view fetched at init time lives here. One member is deliberately NOT of that chain: `arm_diagnostic_scoring` is step 2 without the rest of it, for the verbs that score outside the loop (`verify` / `ab` / `noise-floor` / `seed-screen`) — they have no `ObservabilityBridge` to give, and that `obs=None` is what makes them one path rather than four copies. Sequence + pre/postconditions: [`../../docs/developer/run-initialization.md`](../../docs/developer/run-initialization.md). |
| `optimization/` | The L1/L2/L3 loop primitives: `Cycle` state, candidate generation, critique, validation, transitions, PoBB elimination, `dispatch/` injection routing. Curated subpackages `escalation/` (state + decide + rules + firing) and `resume_and_fork/` (decisions + replayers + fork siblings + resume entry). |
| `intelligence/` | Materialized views over the MeasurementArchive: `AxisIndex` (axis-keyed digest), `SampleIndex` (per-sample state), Rasch exploration, hard-sample sorter + archive. Shared by scan and loop. `indexes/` is **a cursor pattern, not a base class, deliberately** — both carry a `_seen_runs` set, but one consumes run *details* and the other *index entries* under its own cursor, so a shared base would have to abstract the archive call, the payload schema, cursor ownership and cache invalidation at once. |
| `scoring/` | The `score_search_point()` gateway plus formula compilation, evaluators, sample measurement, and three modules that were one bag named `metrics.py`: `metrics.py` (the composite-fitness COMPUTER — the gateway is not it), `selection.py` (which candidate wins / is cut: `elect_round_winner`, `elimination_p_best`, `paired_fitness`, `mean_fitness_ci`), `diagnostics.py` (what one measured row REPORTS: `find_rank`, `extract_sample_diagnostics`). Per CLAUDE.md: gateway is canonical; everything else is implementation detail. |
| `views/` | The **emit contract**: frozen typed View dataclasses (`view_models.py`), the live `PhaseEvent → View` builder (`ingress.py::from_phase_event` — needs same-layer `optimizer_model` + scoring formula evaluators), and markdown rendering (`render/` — `to_markdown` + heatmap + `render_sweep_summary`). Producing these views *is* an orchestration job, so they live here; `presentation/views` imports them upward for terminal (`to_text`) rendering. |
| `jobs/` | The launcher + job registry (capacity-1 machine slot), the spend cap, and the liveness reaper. `jobs/launcher/` is the shared mint/start seam CLI `new` and the web Start both funnel through. |
| `mask/` | The mask projection — record / divergence / invariant-vs-divergent, plus `backprop.py::select_rewind_round` (UCB1 over the lineage tree; the layer decides *whether* to rewind, this decides *where*). **The code SoT** for `docs/operations/mask-projection.md`. |

## Top-level modules

- `runner/` — master orchestrator; the optimize-loop entry point (`campaign_ids`, `round`, `sweep`, `loop`, `entry`). `runner/inner/` is the L4 recursion: `tasks.py` (the panel a dataset declares in `inner_tasks.yaml` — the type is the validator, `extra="forbid"`) + `spawn.py` (spawn context, the sandboxed re-entrant task, the narrative). The law that SCORES the result is one layer down, in `domain/l4/` — keep it there.
- `campaign_config.py` — the `CampaignConfig` / `OptimizationConfig` schema and the knob vocabulary it is annotated with. **Renaming a `CampaignConfig` / `Campaign` field is a data migration, not a code change.** `extra="forbid"`, and the config rides **two** on-disk surfaces: the minted manifest `campaigns/{id}/campaign.json::config` and the dataset template `datasets/{slug}/campaign.yaml::campaign_config`. A rename makes `load_campaign_config` raise `extra_forbidden` on every file still naming it — `resume`/`ab`/`verify`/L4 die at load, the dataset reads 500. It has fired three times. Read the rule narrowly, because reading it wider is the usual error: **we may break our own code freely; we may not silently break measurements a paying tenant owns** — so on an empty dev store it costs nothing (count first, per root § STOP). Both surfaces already persist only the **delta from defaults** (`freeze_campaign_config`), so renaming a knob nobody set is free; one the operator *did* set still breaks, which the fixtures pin through the real reader. Never `extra="allow"`, an alias, or a migration shim. **Every knob declares itself on its own field** — `Annotated[T, Knob(scope, *estimands)]` says what it shapes (`Scope.POLICY` = resume keeps the data trace; `Scope.DATA` = resume runs divergence detection) and which `Estimand`(s) it moves. Adding a knob without one fails at import. **Imports nothing from `application/`** — that is what keeps `knobs` → `campaign_config` a plain edge.
- `preflight.py` — the pre-run validator over that schema: `run_preflight_checks` (pure, returns `PreflightWarning`s) and `check_model_reasoning_floors`, the one HARD block.
- `pipeline_resolve.py` — the resolver that turns schema + overlays into `session.pipeline_params`: `apply_node_overlay`, `resolve_pipeline_config_params`, starting prompts, model-ownership validation, `configure_and_apply_pipeline`. See **Backend overlay** below for the merge contract.
- `output.py` — operator-facing artifact WRITERS (`write_log_md`, `write_review_md`, `write_hard_samples_artifacts`) + disk-side view reconstruction (`from_disk_log`). Every function here is session-scoped and touches disk; computing artifacts and writing them is orchestration, so it lives here — not in `presentation/`. Renders through `application/views` (`to_markdown` + typed view models).
- `review_md.py` — the PURE `review.md` renderer (`render_review_md`) + its behaviour-check evaluation. No `Session`, no `Cycle`, no disk, which is what lets `scripts/render_review.py` re-render a finished cycle off what is already on disk. It shared `output.py` with the writers until that file's *second* `__all__` was found silently shadowing the first, leaving its one externally-called function unexported.
- `knobs.py` — `KNOBS`, walked off those declarations: the ONE config-leaf taxonomy. Two facets read it — `classify_config_diff` (resume: does this edit fork the data trace?) and `COUPLINGS`/`resolve_knob_states` (which knobs collide, what overwrites what → preflight + the webapp config-map panel). One-way import: `knobs` → `campaign_config`, never the reverse.
- `evidence.py` — what ANY SET of campaigns jointly says, recomputed from disk per read (`GET /evidence?campaign=…&metric=&ranking=`, CLI `evidence`). **No L4 gate and no dataset scope** — an ordinary campaign takes the same path as a `promptpotter-self` one, and a selection spanning datasets is answered rather than refused. Two halves: the **origin half answers at round 0** and auto-loads (roster, `Comparability`, arm replicates, the cell/arm/residual split beside the null scatter an arm mean shows anyway, resolving power, run-order confound); the **ranking half** is the only walk past round 0, so it alone is opt-in behind `ranking=`. **Read-only** — it names a leader, and graduating one into `promptpotter/assets/optimizer/pipeline.yaml` is a hand-edit. **No noise term beside `EditSpread`:** the inner instrument is content-addressed, so a second reading of one (state, cell) replays and its spread is zero by construction. A replicate ARM is the honest noise reading; depth on one candidate is `verify`'s job. **WHICH measurand is the request's** (`evidence_metrics.py`, `?metric=`): a catalogue key or `expr:<formula>`, evaluated PER CELL and only then collapsed — a campaign-level scalar cannot cancel cell difficulty, so collapsing first would cost the paired test. `cell_channels` is the ONE row-to-number path. **Every channel is a fact about the CELL** — on the recursion a cell IS a whole inner campaign, so its lift, origin, cost and round count are that seed's own (`InnerCellFacts`, emitted at `runner/inner/spawn.py`); nothing opens a second document for what the cell already carries. A channel a row cannot answer is ABSENT (`latency` reads `step_timings`, never `total_time`, which a cached replay banks as 0.0); one it answers with **0.0 is a MEASUREMENT**, so `x or y` between two sources reports a free cell as an unpriced one. `MetricUnit` says what a number IS — only a `delta` earns a leading `+`, a `composed` expression's units are unknowable — and every per-unit table is completeness-guarded at import (Python) or by a total `Record` (TS). **The catalogue is per-SELECTION and INTERSECTED**: `measurand` resolves to the seed's lift where the cells carry one and the cell's own fitness where they do not, and `fitness` is offered beside it only where it is a different number. `expr:` deliberately reaches WIDER than the served namespace — naming a hidden channel must reach *unscorable*, never a refusal. Every reading rides that metric, so no table answers in units the picker does not name. **One row per campaign** (`CampaignReading`), read from the **root cycle the manifest names** — a fork lives beside its parent under one campaign id, so a directory walk served the fork's origin under the parent's name. Its merged value brackets **that campaign's OWN cells**, not the shared axis the charts plot, so every surface showing it shows the count. An id that answered nothing rides `unread_campaigns` rather than thinning the roster in silence, and a selection where NOTHING answered raises about the SELECTION, not the metric. **Pairwise** is Student-t on the cells both scored, Holm-corrected across PAIRS — never across metrics, which the surface has to say out loud.
- `sweep.py` — sweep-batch orchestration: one fork per `OperatorSweepFile` via the shared `_mint_fork`. Reached from CLI `new --sweep-batch`; the second, hand-rolled `sweep` verb that did the same job is gone.
- `origin.py` — campaign origin scoring + dataset loading. `resolve_origin_opt_search_point` resolves the origin OSP by priority **seed → experiment prompts → dataset prompts → empty**: a `CycleSeed`'s `origin_prompt_fields` (read from the cycle's `CycleSeedRecord` on the ledger) *is* the origin (operator-steered fork or campaign-from-origin; lineage stamped from `seed.origin_source`).
- `embedded_run.py` — the **embedded launch entry**: `open_session` → `mint_and_score_origin` → `run_campaign`, for a host Python program driving one campaign in its own event loop (the notebooks, the BBEH harness, `scripts/smoke_campaign.py`, a packaged adapter). Peer of `jobs/launcher/mint_and_start.py`, which detaches onto a background task and holds the capacity-1 slot; this one blocks in the caller's loop and holds none. Three steps rather than one because every caller does its own work between them. It takes a `LiveDisplay` and an `on_status` callback but renders neither — the read-out of what it returns is `presentation/views/completion.py`.
- `datasets/` — `loaders.py` (dataset loaders + registry + `build_dataset_run_data`), `prompts.py` (per-dataset prompt store + node overlay), `draft_patch.py` (`EditDraftPatch` + the rules that apply one — the ONE origin-edit vocabulary every ingress shares. It lives here rather than in `api/routers/`, which no CLI verb can import without FastAPI: a rule an adapter cannot reach is one it writes a narrower copy of, so the layer boundary is the bug and the duplication only the symptom).
- `run_observers.py` — `RunCallbacks` typed event constructor over `CycleEventLog.append`. Also binds the `_CYCLE_LEDGER` ContextVar (`build_run_observers`) — anything reaching an LLM *before* this runs must bind its own ledger or its spend goes unrecorded. **And it owns `build_campaign_emitter`**, the `LiveDashboardView` constructor: it sat in `origin.py`, which builds no projections and whose importers had to reach past their own concern to find it.
- `verify.py` (the `verify` verb), `noise_floor.py` (the fenced `noise-floor` diagnostic — never wired into the loop), `run_phase_control.py`.

## Conventions

- Optimizer LLM calls go through `llm_call()` (`optimization/dispatch/llm_call/call.py`),
  never `chat()`.
- Escalation flows via return value (`QueryLoopResult.escalation_signal`),
  not exception.
- New optimizer state MUST flow through `OptSearchPoint` — no sidecar state.
- **An await that can outlast `RUN_FRESH_S` and writes nothing MUST heartbeat**
  (`optimization/dispatch/llm_call/heartbeat.py`) — silence is how this package
  says "dead", so a long quiet await reads as a vanished producer and gets reaped
  out from under itself. Obligates every long await here, not just the LLM calls.
  That one loop also carries `on_suspend` (the machine-sleep signal a wall-clock
  deadline needs), so **create the task unconditionally** — its `ledger` is
  optional precisely so a missing telemetry sink can never disarm a guard that
  has nothing to do with telemetry.
- Backend tunables ride the per-dataset overlay
  (`datasets/{name}/pipeline.yaml::nodes.{name}.config`) merged by
  `configure_and_apply_pipeline()` (`pipeline_resolve.py`). See **Backend overlay** below for the merge contract.
- **Per-call telemetry from deep async chains uses the `emit_*` shape**, not
  `RunCallbacks`. Canonical template (set by `TokenUsageRecord`):
  define the `*Record` in `domain/run_records.py`, add the `*Record` arm to
  the `CycleRecord` discriminated union + a `_handle_*` no-op default to
  `DerivedView` (`infrastructure/projections/base.py`), write a kwargs-only
  `emit_*` helper that reads the active ledger from the `_CYCLE_LEDGER`
  ContextVar (`infrastructure/llm/telemetry.py`) and appends, register the
  projection subscriber. No process-global sink, no wrapper dataclass — the
  call site goes from kwargs to ledger in one hop. `RunCallbacks` stays the
  shape for high-frequency snapshot/phase events the runner already drives;
  emit_*-style is the shape for per-call telemetry buried inside dispatch.

## Backend overlay

**This layer MERGES the overlay; it never authors one.** `nodes.{name}.config` in the dataset's `pipeline.yaml` is a sparse overlay laid onto each wire payload by `load_dataset_node_overlay` → `configure_and_apply_pipeline()` (`pipeline_resolve.py`). A node arriving with no `config.model` is a loud setup error raised right there — never a silent fall-through to the backend's hidden `GET /pipeline` default, because a silent one attributes a measurement to a model nobody chose.

**Resolution is tenant-first.** "The dataset's `pipeline.yaml`" means the file under the dir `readable_dataset_dir` chose — a tenant upload at `projects/{tenant}/datasets/{slug}/` before install content at `datasets/{name}/`. The loaders (`load_dataset_node_overlay`, `load_node_prompt`, …) take that resolved dir (carried on `Session.dataset_config_dir`), never a bare name — so an ingested dataset's overlay + starting prompts load identically to a benchmark's.

**Sole route for a tunable change** — owned by [`../../datasets/CLAUDE.md`](../../datasets/CLAUDE.md) § Sole route. This layer only merges what that route produced; it never reads a tunable from anywhere else. **Where a change belongs when the cause is in TermNorm's own code** — owned by [`../connectors/CLAUDE.md`](../connectors/CLAUDE.md) § TermNorm is not a third party.

**Cycle-seed overlay (seeded cycles — steered forks + campaign-from-origin).** A seed's `pipeline_overlay` (read from the cycle's `CycleSeedRecord` on the ledger) is layered ON TOP of the resolved `session.pipeline_params` (which already holds dataset-overlay + campaign-overrides), so the effective precedence is **seed > campaign-override > dataset > backend default** — for that cycle only, the dataset file stays immutable. The merge is read-once and applied at the single runner seam (`runner/entry.py::run_optimization` via `_read_cycle_seed`), keyed by the known `cycle_id` — not threaded through each launcher or `configure_and_apply_pipeline` caller.
