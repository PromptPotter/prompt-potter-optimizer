# application/ — orchestration layer

The use-case layer between `domain/` (pure types, frozen models) and
`infrastructure/` (I/O, persistence, LLM clients). One entry point —
`runner/` — coordinates everything.

## Layer rule

**`application/intelligence/` MUST NOT import from `application/bench/` or an optimizer.**
The optional sensitivity scan and the optimization loop both *consume*
intelligence; intelligence depends on neither. It carries no test because a
**module-level** back-import raises on the spot — `bench/` and `optimizers/potter/` import
`intelligence/` at module level, so the cycle closes at import time
([`../../tests/CLAUDE.md`](../../tests/CLAUDE.md) § Structural invariants). A
**function-local** import does not raise — moving an import inside a function to silence a
circular-import error does not fix the cycle, it hides this rule. `intelligence/indexes/` is
**a cursor pattern, not a base class, deliberately**: one folds each configuration's *population*,
the other its *index entry*, so a shared base would abstract four things at once.

**The bench never imports an optimizer; an optimizer may import the bench.** Nothing outside
`optimizers/` imports potter's modules: the bench asks the selected optimizer
through the registry — the members its manifest names and its `OptimizerRuntime`, both addressed by
the Protocols in `optimizers/nodes.py` — so a peer's manifest runs through the same bench.
`scripts/gate.py::_IMPORTS_POTTER` holds the line and names its exceptions.

## Init writes `Session`; the runner reads it

`initialization/` writes to `Session`. The runner reads `Session` and the `CycleEventLog`, and never
writes back outside the per-round bundle (`session.state`). State that must survive a `new` / `resume`
goes through the ledger or its projections, **never `Session`**; per-process per-cycle state lives on
`Session.state` or `Cycle`. No test guards it — state that skips the ledger does not survive a restart,
which is visible immediately ([`../../tests/CLAUDE.md`](../../tests/CLAUDE.md)).

## Subpackages

| Subpackage | Owns | CLAUDE.md |
|---|---|---|
| `initialization/` | Run init: the ordered chain from `new` / `resume` to the first round, and where the registries complete. | [`initialization/CLAUDE.md`](initialization/CLAUDE.md) |
| `bench/` | The harness every optimizer runs inside, which imports none. | [`bench/CLAUDE.md`](bench/CLAUDE.md) |
| `views/` | The emit contract: typed views, renderers, the readout. | [`views/CLAUDE.md`](views/CLAUDE.md) |
| `jobs/` | Run admission: the one launch, machine slots and their queue, the spend cap, the reaper. | [`jobs/CLAUDE.md`](jobs/CLAUDE.md) |
| `commands/` | The control-plane write path: `CommandDispatcher`. | [`commands/CLAUDE.md`](commands/CLAUDE.md) |
| `runner/` | The master orchestrator and optimize-loop entry point; `inner/` is the L4 recursion's machinery. | [`runner/CLAUDE.md`](runner/CLAUDE.md) |
| `evidence/` | What ANY SET of SUBJECTS jointly says, reduced per read (`GET /evidence`, CLI `evidence --subject`). | [`evidence/CLAUDE.md`](evidence/CLAUDE.md) |
| `datasets/` | Dataset loaders, the per-dataset prompt store, the check-in's draft. | [`datasets/CLAUDE.md`](datasets/CLAUDE.md) |

## Top-level modules

- `campaign_config.py` — the `CampaignConfig` / `OptimizationConfig` schema and the knob vocabulary it is annotated with. **It holds only the bench's knobs**, plus `optimizer` (which manifest runs) and `nodes` (the campaign's overlay on it): an optimizer's knobs are its nodes' `config`, typed by each node member's `knobs` model — never a field here. **Renaming a `CampaignConfig` / `Campaign` field is a data migration, not a code change.** `extra="forbid"`, and the config rides **two** on-disk surfaces: the minted manifest `campaigns/{id}/campaign.json::declaration` and the dataset template `dataset.yaml::campaign`. A rename makes `CampaignConfig.model_validate` raise `extra_forbidden` on every file still naming it — `resume`/`ab`/`verify`/L4 die at load, the dataset reads 500. Read it narrowly: **we may break our own code freely; we may not silently break measurements a paying tenant owns** — so on an empty dev store it costs nothing (count first, per root § STOP). **Every door's config becomes ONE `RunDeclaration`** (`declare_run`, reached through `jobs/mint.py::declare`): only the keys a layer SET, split into dataset hash, run seed, loop, budget, environment and purpose (`Optimize | Compare(arm)`), so a moved code default never rewrites a manifest; `effective_config` is the ONE derivation of what a declaration runs (today's defaults under it), and the defaults it is a delta over — its `bench_defaults_pin` (`knobs.py::bench_defaults_pin`, which every arm shares) and its campaign's whole `Treatment` (the optimizer's manifest with member knob defaults in, its source and its prompts) — name what moved when resume finds the origin drifted (`pipeline_resolve.py::defaults_drift`). The criterion its dials lock on the origin is a ledger record read through `pipeline_resolve.py::resolve_campaign_config` — never the declaration raw where scoring matters. The dataset file is only a seed for the NEXT campaign; the template keeps only its delta from defaults. Never `extra="allow"`, an alias, or a migration shim. **Every knob declares itself on its own field** — `Annotated[T, Knob(scope, *estimands)]` says what it shapes (a `Scope`: [`mask-projection.md`](../../docs/operations/mask-projection.md) says what resume does under each) and which `Estimand`(s) it moves. Adding a knob without one fails at import. **Imports nothing from `application/`** — that is what keeps `knobs` → `campaign_config` a plain edge.
- `preflight.py` — the pre-run validator over that schema: `run_preflight_checks` (pure, returns `PreflightWarning`s) and the three HARD blocks, asked as ONE function of what a cycle runs (`jobs/mint.py::refuse_unrunnable`, reached through `resolve_cycle_plan` — a mint's plan, or given a hop the plan that cycle runs, its root's origin under its own seed, which every resume compares by hash in `refuse_drifted_resume` — and asked by an upload's Start of the draft before its dataset lands): `check_search_split_holds_round`, `refuse_below_reasoning_floor`, `refuse_unfit_pipeline` (a member's declared `prompt_arity` or rewritten `field` the chain's prompt nodes cannot hold) and the selected optimizer's `pool_refusals`. A fourth is run init's alone, since it needs the archive's cell price: `refuse_arm_below_round` turns the `spend_cap_below_round` warning into a block for an arm of a head-to-head. The fifth block before the origin is scored is not here: a cell no rate bounds cannot run under a dollar ceiling, and that rule is the spend book's (`SpendBook.refuse_unpriced`), which the runner asks of the origin's cell and every send asks again — one stop (`no_rate`), one sentence.
- `pipeline_resolve.py` — the resolver that turns schema + overlays into `session.pipeline_params`: `resolve_pipeline_config_params`, starting prompts, model-ownership validation, `configure_and_apply_pipeline`. The campaign list's `campaign_runs_with` shares the per-campaign read's two merges (`_campaign_merge` / `_draft_merge`), never a resolver of its own. **A check-in's config has ONE derivation**, `draft_config`: every read before Start, admission and the declaration take it, so none can show a ceiling the run does not hold. The optimizer's served reads (`resolve_pipeline_for_optimizer`, `optimizer_knobs`, `optimizer_roster`) sit here and never in `optimizer_manifest.py`, whose whole source is hashed into every `Treatment`.
- `knobs.py` — `KNOBS`, walked off those declarations, and the selected optimizer's node knobs walked the same way: the ONE config-leaf taxonomy. Two facets read it — `bench_defaults_pin` (which bench defaults a declaration is a delta over, walked live) and the couplings/`resolve_knob_states` (which knobs collide, what overwrites what → preflight + `config_map.py`, the one config-map every surface reads); a member declares its own couplings. One-way import: `knobs` → `campaign_config`, never the reverse.
- `optimizer_manifest.py` — which optimizer a campaign runs: `select_optimizer` resolves the manifest `optimization.optimizer` names with the `nodes` overlay laid on and every member's knobs validated, the ONE resolution a run, a draft edit, a fork, a mint and a served menu share, refusing as the caller's input (`NotFoundError` / `PayloadInvalidError`); a launch's sparse override lays on through it (`promptpotter/application/datasets/authored.py::with_optimization`). `bind_optimizer` holds it per task for the calls deep in a round; the bench's check-in node is `checkin_manifest`, apart from every optimizer.
- `origin.py` — the campaign's origin, resolved then scored; `origin_listing.py` lists them. `resolve_origin_opt_search_point` resolves the origin OSP by priority **seed → dataset prompts → empty**, for every prompt node the roster declares: a `CycleSeed`'s `origin_prompts` (read off the cycle's `CycleSeedRecord` on the ledger) *is* the origin, its lineage stamped from `seed.origin_source`. It resolves the origin BEFORE it is measured; once round 0 banks C0, `verify`, `ab` and `noise-floor` read it as any measured point, through `domain/results.py::Concluded.searchpoint` — a second recovery is a second origin.
- `embedded_run.py` — the **embedded launch entry**: `open_session` → `run_campaign`, plus `submit_gate_decision` (the gate has no TTY here and `origin_gate` defaults `strict`, so a host that cannot answer blocks at round 0 forever) and `submit_arms`, its answer to an arm gate. It **deliberately holds no machine slot** — the host program owns its own scheduling, and a slot taken from a shared pool would make a notebook wait on a browser. That is a declared exemption from `request_job`, not a missing call. **It still holds a share of its campaign's ceiling and an account reservation**, admitted as a launch's are (`jobs/launcher/admission.py::embedded_share`): a share and a slot are two holds, and the first never takes the second. It prints nothing unless the host passes `readout_sink`; the read-out of what it returns is `presentation/terminal/completion.py`.
- `run_callbacks.py` — `RunCallbacks`, the typed event constructor over `CycleEventLog.append`. `run_observers.py` binds the `_CYCLE_LEDGER` ContextVar (`build_run_observers`) — a paid call or any `emit_*` where no ledger is bound raises (`telemetry.py::bound_ledger`; a bound `None` is the declared mute), so anything reaching an LLM *before* this runs binds its own — and the same projections for every caller (`_bind_observers` is the roster) — a launch path that could omit one leaves a cycle without that artifact, so none is optional and the only per-caller argument is `readout_sink`. Beside them `declare_running`, `declare_gate` and `declare_run_stop`: the runner is sole declarer of `running` / `gate` and of its own stop, and a `paused` one carries what caused it.
- `run_phase_control.py` — `RunControl`, the one object every checkpoint polls for pause, skip, look-ahead, a gate decision, submitted arms and the spend ceiling (`Session.control`, armed per cycle by `runner/spend_arming.py::arm_run_controls`). **It reads the cycle's own ledger and nothing else**, and acks `applied` what it takes.

## Conventions

- Optimizer LLM calls go through `llm_call()` (`bench/llm_call.py`),
  never `chat()`.
- A stop rule's verdict flows via return value (`QueryLoopResult.stop_signal`),
  not exception.
- An optimizer's working state is typed parts (`domain/optimizer_state.py::StatePart`, each registered under its `kind` by the package declaring it) that a member declares it `reads` / `writes` — a `StateSlice` carried across rounds, a `RoundOutput` one round produced — and every round banks as `optimizer_state`; a resume or fork restores from there — never a field of the individual, never a sidecar. The carried set is the roster's declarations, and a roster leaving one open is refused at load.
- **An await that can outlast `RUN_FRESH_S` and writes nothing MUST heartbeat**
  (`infrastructure/llm/heartbeat.py`) — the cycle's lock is what says a producer is
  attached and no silence reaps it, but a quiet one is served `wedged` to an operator
  deciding whether to kill it. Obligates every long await here, not just the LLM calls.
  That loop also carries `on_suspend` (the machine-sleep signal), so **create the
  task unconditionally** — its `ledger` is optional so a missing telemetry sink
  can never disarm it.
- Backend tunables ride the per-dataset overlay
  (`dataset.yaml::pipeline.nodes.{name}.config`) — the merge contract is **Backend overlay** below.
- **Per-call telemetry from deep async chains uses the `emit_*` shape**, not
  `RunCallbacks`. Template (`TokenUsageRecord`): a `*Record` in
  `domain/run_records.py`, its arm in the `CycleRecord` union + a `_handle_*`
  no-op on `Projection` (`infrastructure/projections/base.py`), a kwargs-only
  `emit_*` helper in `infrastructure/llm/telemetry.py`, the projection
  subscriber. A walk's sample records are the scoring gateway's own, written this
  way off the `MeasuredPass` a caller opens it with — never a callable handed in.
  `RunCallbacks` stays the shape for arm and phase records the runner drives.

## Backend overlay

**This layer MERGES the overlay; it never authors one.** `nodes.{name}.config` in the dataset's `dataset.yaml::pipeline` is a sparse overlay laid onto each wire payload by `load_dataset_node_overlay` → `configure_and_apply_pipeline()` (`pipeline_resolve.py`). A node with no `config.model` is a loud setup error raised right there: a silent fall-through to the backend's `GET /pipeline` default attributes a measurement to a model nobody chose. `domain/pipeline_overlay.py::apply_node_overlay` merges to the depth the declaration states (`param_types: object`), never a sniffed type, and sits on the identity path.

**Two moments read that schema, not one.** Run init reads it live (`initialization/wiring.py`); the CHECK-IN reads it too (`promptpotter/application/datasets/ingest.py::fetch_backend_nodes`, stored on `DraftCampaign.backend_nodes`) because `optimizer.param_keys` — which params are search AXES — is declared by the backend and by nothing else; a setup surface built from the connector seed alone draws locks the run does not enforce. Store it on the draft rather than fetching per response: it is a material fact about that check-in, and an unreachable backend must surface as `schema_source: unreachable` rather than as an axis nobody opened.

**Resolution is tenant-first, and it happens once.** "The dataset's `dataset.yaml`" means the file under the `ResolvedDataset` that `infrastructure/store/dataset_access.py::resolve_dataset` returned — a tenant upload at `projects/{tenant}/datasets/{slug}/` before install content at `promptpotter/assets/datasets/{name}/`. Every read takes that value (carried on `Session.dataset`), never a bare name re-resolved — so an ingested dataset's overlay + starting prompts load identically to a benchmark's.

**Sole route for a tunable change** — owned by [`../assets/datasets/CLAUDE.md`](../assets/datasets/CLAUDE.md) § Sole route. This layer only merges what that route produced; it never reads a tunable from anywhere else. **Where a change belongs when the cause is in TermNorm's own code** — owned by [`../connectors/CLAUDE.md`](../connectors/CLAUDE.md) § TermNorm is not a third party.

**Cycle-seed overlay (seeded cycles — steered forks + campaign-from-origin).** A seed's `pipeline_overlay` (read from the cycle's `CycleSeedRecord` on the ledger) is layered ON TOP of the resolved `session.pipeline_params` (which already holds dataset-overlay + campaign-overrides), so the effective precedence is **seed > campaign-override > dataset > backend default** — for that cycle only, the dataset file stays immutable. The merge is read-once at the single runner seam (`runner/run_prep.py::prepare_run`), never threaded through each launcher or `configure_and_apply_pipeline` caller.
