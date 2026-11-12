# application/ — orchestration layer

## Layer rule

- **`intelligence/` imports neither `bench/` nor an optimizer**; both import it at module level. A back-import moved into a function hides the cycle, it does not fix it. Nothing outside `optimizers/` imports an optimizer package (`scripts/gate.py::_IMPORTS_OPTIMIZER`).
- **Init writes `Session`, frozen; the runner writes only `Session.state`.** <!-- probe: git grep -cE "^\s*session\.[a-z_]+ = " -- promptpotter/application/runner == 0 --> State that survives `new` / `resume` rides the ledger.

## Registries complete at a declared step

**A registry whose members import back up the stack completes in `wiring.py::complete_registries`, never at import**; every process calls it once at entry, and a CLI verb that parses no ledger, banked round or member declares `Verb.needs_registries=False`.

## Conventions

- **A check-in's config is `pipeline_resolve.py::DraftPipeline`'s `config`, an optimizer resolves through `optimizer_manifest.py::select_optimizer`**: never derive beside them.
- Optimizer LLM calls go through `bench/llm_call.py::llm_call`, never `chat()`. <!-- probe: git grep -cE "\.chat\(" -- promptpotter/application/optimizers/*.py == 0 --> A stop rule's verdict flows by return value (`QueryLoopResult.stop_signal`), never an exception.
- **An await that can outlast `RUN_FRESH_S` and writes nothing heartbeats** (`infrastructure/llm/heartbeat.py`), or it is served `wedged`.
- **A run's ambient state is ONE frozen `shared/run_scope.py::RunScope`**, bound once; a verb scoring outside a run takes it through `scoring_scope.py::arm_diagnostic_scoring`, and a move inside a run goes through `rescope`, never a second ContextVar.
- **A served read is a `ReadKind` row plus its `read_params.py::ReadParams` model**, a GET unless the model names `method="POST"`; `api/routers/reads.py` mounts it, so no route is written. Address it by the id of the entity it is a fact ABOUT, resolve it here, never in the router, serve the provenance a client would otherwise diff, and give it its row in [`webapp/lib/CLAUDE.md`](../../webapp/lib/CLAUDE.md) § Display-data sources.
- **`commands/dispatcher.py` is the sole filer of a `CommandRecord`** <!-- probe: git grep -cE "CommandRecord\(" -- promptpotter/application/*.py == 1 --> **and imports no handler** <!-- probe: git grep -cE "^from promptpotter\.application\.commands\." -- promptpotter/application/commands/dispatcher.py == 0 -->: a `CommandKind` row names its `module:handler`, imported at dispatch.
- **Every ingress edits an origin through an `EditDraftPatch`, applied in `datasets/draft_patch.py`**, never in `api/routers/`. A projection reading the draft's connector or criterion lives in `datasets/draft_campaign.py`, never in `jobs/launcher/` (an import cycle through `pipeline_resolve`). A draft is kept only once its response is built.
- **A schemaless pipeline (no `backend_type`, no nodes) is a typed 422**, never a 500, which reads as `transient` and the webapp retries.

## The bench

- **An optimizer's state rides `Cycle` as typed `slices` (across rounds) and `outputs` (one round), reached through what a member declares on `NodeContext`**; a member's step never gets the `Cycle`.
- **`checkin` is the bench's one llm node: two modes (decomposition, origin resolution), one output schema.** <!-- probe: git grep -cE "class \w*Checkin\w*Output\(" -- promptpotter/*.py <= 1 --> Resolution bills the draft's cycle ledger, decomposition the run's.

## L4's machinery (`runner/inner/`)

L4's law is `domain/l4/`; this package runs it, and the `promptpotter` connector's `in_process_run` delegates to `spawn.py::run_inner_cycle`.
- **The inner campaign runs in its own `asyncio.Task`**, so the child's context copy carries the outer's pause inward. **The outer L1's mutations are laid into the cell's `optimization.nodes` at spawn**, reaching the inner optimizer only; a mutation no render can apply is banked as the cell's fault (`CellUnscoreableError`), never dropped.
- **Sandboxes are a FLAT per-cycle registry** (`<workspace>/.inner/<key>/`), never nested, so the recursion stays re-entrant. The sandbox owner is the last of `inside`, the asker the last of `enclosing`; a fork moves only the asker.

## Run admission (`jobs/`)

- **Every slot is asked at `jobs/launcher/admission.py::request_jobs`** (a launch, the one ask that may queue; a head-to-head's arms, all or none; a scoring pass), from `commands/launching.py`. <!-- probe: git grep -cE "promptpotter\.application\.commands" -- promptpotter/application/jobs/*.py == 0 --> A path reserving its own slot is the bug: the machine cannot report occupancy it was not told about. `embedded_run.py` and an inner run are the declared exemption (`slotless_share`: no slot, a ceiling share and a reservation).
- **An entry point hands DATA, never a callable**: a `LaunchRequest` arm, a `RunMode`, `Inline` where a person watches.
- **A paid verb outside a run is admitted inside the application function**: a scoring pass through `paid_pass` (a slot, refused `machine_busy`, never queued), one LLM call through `jobs/quota.py::paid_call`; both refuse a taken cycle.
- **A server-launched run executes in its own process** (`jobs/launcher/run_job.py`); a queued job's intent never leaves the process that authenticated it, so a restart drops the queue.
- **A job file is written by `JobRegistry._stamp` under the admission gate**; a second writer overwrites a lowered reserve. <!-- probe: git grep -cE "_persist\(" -- promptpotter/application/jobs/registry.py == 4 -->

## Backend overlay

- This layer merges the overlay, never authors one ([`datasets/CLAUDE.md`](../assets/datasets/CLAUDE.md) § Sole route).
- **The backend's pipeline is read live through `pipeline_resolve.py::served_pipeline` and stored on no draft** <!-- probe: git grep -cE "fetch_pipeline\(" -- promptpotter/*.py == 2 -->; an unreachable backend raises `BackendUnreachableError` (a 503), never a stand-in schema.
- **Pipeline params are ONE fold, `pipeline_resolve.py::ParamLayers`**, in `ParamLayer`'s order; each subject picks its layers by the data it lays. <!-- probe: git grep -cE "apply_node_overlay\(" -- promptpotter/application/*.py == 1 -->
