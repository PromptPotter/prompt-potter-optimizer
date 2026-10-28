# application/ — orchestration layer

## Layer rule

- **`intelligence/` imports neither `bench/` nor an optimizer.** Both import it at module level, so a back-import raises at import; moving it into a function hides the cycle, it does not fix it.
- **Nothing outside `optimizers/` imports an optimizer package** (`scripts/gate.py::_IMPORTS_OPTIMIZER`); the bench reaches a member through `optimizers/nodes.py`.
- **Init writes `Session`, frozen; the runner writes only `Session.state`.** <!-- probe: git grep -cE "^\s*session\.[a-z_]+ = " -- promptpotter/application/runner == 0 --> State that survives `new` / `resume` rides the ledger.

## Top-level modules

- **Renaming a `CampaignConfig` / `Campaign` field is a data migration** — owned by [`domain/CLAUDE.md`](../domain/CLAUDE.md) § Tolerance; it covers `campaign.json::declaration` and `dataset.yaml` `campaign`.
- **A check-in's config is `pipeline_resolve.py::draft_config`'s**, and an optimizer resolves through `optimizer_manifest.py::select_optimizer` — take them, never derive beside them.
- **`embedded_run.py` holds no machine slot, but a ceiling share and a reservation** (`jobs/launcher/admission.py::embedded_share`) — a declared exemption from `request_job`.
- **`run_observers.py::build_run_observers` binds every projection for every caller; none is optional**, and an unbound paid call raises (`infrastructure/llm/telemetry.py::bound_ledger`).

## Conventions

- Optimizer LLM calls go through `llm_call()` (`bench/llm_call.py`), never `chat()`. <!-- probe: git grep -cE "\.chat\(" -- promptpotter/application/optimizers/*.py == 0 -->
- A stop rule's verdict flows by return value (`QueryLoopResult.stop_signal`), never an exception.
- **An optimizer's working state is typed `StatePart`s** (`domain/optimizer_state.py`) banked as `optimizer_state` — never a field of the individual, never a sidecar; an unregistered kind raises.
- **An await that can outlast `RUN_FRESH_S` and writes nothing heartbeats** (`infrastructure/llm/heartbeat.py`), or it is served `wedged`. Create the task unconditionally: it also carries `on_suspend`.
- **Per-call telemetry from deep async chains uses `emit_*`, not `RunCallbacks`** — the recipe is [`adding-a-surface.md`](../../docs/developer/adding-a-surface.md) §1.

## Backend overlay

- **This layer merges the overlay, never authors one** — owned by [`datasets/CLAUDE.md`](../assets/datasets/CLAUDE.md) § Sole route.
- **Two moments read the backend schema**: run init (`initialization/wiring.py`) and the check-in (`promptpotter/application/datasets/ingest.py::fetch_backend_nodes`, stored on `DraftCampaign.backend_nodes`), since only the backend declares which params are search axes. An unreachable one is served as `schema_source: unreachable`.
- **Cycle-seed precedence is seed > campaign override > dataset > backend default**, laid last by `configure_and_apply_pipeline`.
