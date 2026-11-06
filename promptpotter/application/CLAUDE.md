# application/ — orchestration layer

## Layer rule

- **`intelligence/` imports neither `bench/` nor an optimizer**; both import it at module level, and moving a back-import into a function hides the cycle, it does not fix it.
- Nothing outside `optimizers/` imports an optimizer package (`scripts/gate.py::_IMPORTS_OPTIMIZER`); the bench reaches a member through `optimizers/nodes.py`.
- **Init writes `Session`, frozen; the runner writes only `Session.state`.** <!-- probe: git grep -cE "^\s*session\.[a-z_]+ = " -- promptpotter/application/runner == 0 --> State that survives `new` / `resume` rides the ledger.

## Conventions

- **A check-in's config is `pipeline_resolve.py::draft_config`'s, an optimizer resolves through `optimizer_manifest.py::select_optimizer`** — take them, never derive beside them.
- Optimizer LLM calls go through `bench/llm_call.py::llm_call`, never `chat()`. <!-- probe: git grep -cE "\.chat\(" -- promptpotter/application/optimizers/*.py == 0 -->
- A stop rule's verdict flows by return value (`QueryLoopResult.stop_signal`), never an exception.
- **An await that can outlast `RUN_FRESH_S` and writes nothing heartbeats** (`infrastructure/llm/heartbeat.py`), or it is served `wedged`; create the task unconditionally, it also carries `on_suspend`.
- **A run's ambient state is ONE frozen `shared/run_scope.py::RunScope`**: the ledger, the selected optimizer and its clamp, the judge cache, the memory scope and the inner spawn. It is bound once at `runner/entry.py::run_optimization`, and a diagnostic binds it through `open_diagnostic`. A move inside a run goes through `rescope`, never a second ContextVar.
- Per-call telemetry from deep async chains uses `emit_*`, not `RunCallbacks` — [`adding-a-surface.md`](../../docs/developer/adding-a-surface.md) §1.
- **`commands/dispatcher.py` is the sole writer of `CommandRecord`** <!-- probe: git grep -cE "emit_command\(" -- promptpotter/application/*.py <= 2 --> **and imports no handler** <!-- probe: git grep -cE "^from promptpotter\.application\.commands\." -- promptpotter/application/commands/dispatcher.py == 0 -->: a `CommandKind` row names its `module:handler`, imported at dispatch.

- **Every ingress edits an origin through an `EditDraftPatch`, and the rules applying it live in `datasets/draft_patch.py`**, never in `api/routers/`. **A projection off the draft alone is a `domain/checkin_draft.py::DraftFile` method; one that reads its connector or criterion lives in `datasets/draft_campaign.py`** (`draft_build.py` the served ones, `origin_readiness.py` the gate and write-back): `pipeline_resolve` reads a draft and `jobs/launcher/` calls `pipeline_resolve`, so a projection parked in the launcher closes an import cycle.
- **A draft is kept only once its response is built** (`datasets/ingest.py::_kept`, the `edit-draft-campaign` and `resolve-origin` handlers): a build that raises keeps nothing.

## Backend overlay

- This layer merges the overlay, never authors one — owned by [`datasets/CLAUDE.md`](../assets/datasets/CLAUDE.md) § Sole route.
- **The backend's pipeline is read live through `pipeline_resolve.py::served_pipeline` and stored on no draft** <!-- probe: git grep -cE "fetch_pipeline\(" -- promptpotter/*.py == 2 -->; each reader (run init, `dataset_pipeline`, a check-in's `draft_served`) lays its own definition on it. An unreachable backend raises `BackendUnreachableError`, never a stand-in schema.
- **Pipeline params are ONE fold, `pipeline_resolve.py::ParamLayers`**, in `ParamLayer`'s declared order; each subject (run, draft, campaign, candidate) picks its layers by the data it lays. <!-- probe: git grep -cE "apply_node_overlay\(" -- promptpotter/application/*.py == 1 -->
- **`embedded_run.py` holds no machine slot, but a ceiling share and a reservation** (`jobs/launcher/admission.py::embedded_share`) — a declared exemption from `request_jobs`.
