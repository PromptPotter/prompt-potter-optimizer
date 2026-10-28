# application/ — orchestration layer

## Layer rule

- **`intelligence/` imports neither `bench/` nor an optimizer.** Both import it at module level, so a back-import raises at import; moving it into a function hides the cycle, it does not fix it.
- **The bench never imports an optimizer** — enforced by `scripts/gate.py::_IMPORTS_POTTER`.
- **Init writes `Session`, frozen; the runner writes only `Session.state`.** <!-- probe: git grep -cE "^\s*session\.[a-z_]+ = " -- promptpotter/application/runner == 0 --> State that survives `new` / `resume` rides the ledger.

## Top-level modules

- **Renaming a `CampaignConfig` / `Campaign` field is a data migration**: `extra="forbid"` over `campaign.json::declaration` and `dataset.yaml` `campaign`. Never `extra="allow"`, an alias or a shim.
- **`pipeline_resolve.py::draft_config` is a check-in's one config derivation** — every read before Start, admission and the declaration take it.
- **`optimizer_manifest.py::select_optimizer` is the one optimizer resolution** a run, draft edit, fork, mint and menu share.
- **`embedded_run.py` holds no machine slot, but a ceiling share and a reservation** (`jobs/launcher/admission.py::embedded_share`) — a declared exemption from `request_job`.
- **`run_observers.py::build_run_observers` binds every projection for every caller; none is optional.** An unbound paid call raises (`infrastructure/llm/telemetry.py::bound_ledger`).

## Conventions

- Optimizer LLM calls go through `llm_call()` (`bench/llm_call.py`), never `chat()`.
- A stop rule's verdict flows by return value (`QueryLoopResult.stop_signal`), never an exception.
- **An optimizer's working state is typed `StatePart`s** (`domain/optimizer_state.py`) banked as `optimizer_state` — never a field of the individual, never a sidecar.
- **An await that can outlast `RUN_FRESH_S` and writes nothing heartbeats** (`infrastructure/llm/heartbeat.py`), or it is served `wedged`. Create the task unconditionally: it also carries `on_suspend`.
- **Per-call telemetry from deep async chains uses `emit_*`, not `RunCallbacks`**: a `*Record` in `domain/run_records.py`, its `CycleRecord` arm, a `_handle_*` no-op on `Projection` (`infrastructure/projections/base.py`), a kwargs-only `emit_*` in `infrastructure/llm/telemetry.py`, the subscriber.

## Backend overlay

- **This layer merges the overlay, never authors one.** A node with no `config.model` raises in `configure_and_apply_pipeline`.
- **Two moments read the backend schema**: run init (`initialization/wiring.py`) and the check-in (`promptpotter/application/datasets/ingest.py::fetch_backend_nodes`, stored on `DraftCampaign.backend_nodes`), since only the backend declares which params are search axes. An unreachable one is served as `schema_source: unreachable`.
- **Resolution is tenant-first, once**: every read takes the `ResolvedDataset` on `Session.dataset`, never a re-resolved name.
- **Cycle-seed precedence is seed > campaign override > dataset > backend default**, laid last by `configure_and_apply_pipeline`.
