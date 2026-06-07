# application/ — orchestration layer

The use-case layer between `domain/` (pure types, frozen models) and
`infrastructure/` (I/O, persistence, LLM clients). One entry point —
`runner/` — coordinates everything; subpackages each own a coherent
slice of orchestration.

## Layer rule (enforced by `tests/test_structure.py::test_runtime_layer_imports`)

`application/intelligence/` MUST NOT import from `application/optimization/`.
The optional sensitivity scan and the optimization loop both *consume*
intelligence; intelligence does not depend on either.

## Subpackages

| Subpackage | Owns |
|---|---|
| `bootstrap/` | `init_services` + `init_optimization_loop` — wiring stores, LLM clients, connectors → `Session`; preflight; cycle bootstrap; observability + scoring setup. Pipeline-discovery view fetched at bootstrap time lives here. |
| `optimization/` | The L1/L2/L3 loop primitives: `Cycle` state, candidate generation, critique, validation, transitions, PoBB elimination, `dispatch_hub` injection routing. Curated subpackages `escalation/` (state + decide + rules + firing) and `resume_and_fork/` (decisions + replayers + fork siblings + resume entry). |
| `intelligence/` | Materialized views over the MeasurementArchive: `AxisIndex` (axis-keyed digest), `SampleIndex` (per-sample state), `ConfigIndex`, Rasch exploration, hard-sample sorter + archive. Shared by scan and loop. |
| `scoring/` | The `score_search_point()` gateway plus formula compilation, evaluators, sample measurement, composite-fitness metrics. Per CLAUDE.md: gateway is canonical; everything else is implementation detail. |
| `sweep/` | Sweep-mode siblings — cheap A/B for L1 candidates ahead of full promotion. |

## Top-level modules

- `runner/` — master orchestrator; the optimize-loop entry point (`identity`, `round`, `sweep`, `loop`, `entry`).
- `config.py` — `CampaignConfig` model + LLM factory.
- `origin.py` — campaign origin scoring + dataset loading. `resolve_origin_opt_search_point` resolves the origin OSP by priority **fork-seed → experiment prompts → dataset prompts → empty**: an operator-steered fork's `.overrides/seed.json::origin_prompt_fields` *is* the origin (lineage `source="fork_seed"`).
- `review.py` — per-cycle markdown renderer (post-cycle log).
- `datasets/` — `loaders.py` (dataset loaders + registry + `build_dataset_run_data`), `prompts.py` (per-dataset prompt store + node overlay), `traces.py` (potter-trace loader).
- `run_observers.py` — `RunCallbacks` typed event constructor over `CycleEventLog.append`.

## Conventions

- Optimizer LLM calls go through `llm_call()` (`optimization/dispatch/llm_call/call.py`),
  never `chat()`.
- Escalation flows via return value (`QueryLoopResult.escalation_signal`),
  not exception.
- New optimizer state MUST flow through `OptSearchPoint` — no sidecar state.
- Backend tunables ride the per-dataset overlay
  (`datasets/{name}/pipeline.json::nodes.{name}.config`) merged by
  `configure_and_apply_pipeline()` (`config.py:342`). See **Backend overlay** below for the merge contract.
- **Per-call telemetry from deep async chains uses the `emit_*` shape**, not
  `RunCallbacks`. Canonical template (set by `TokenUsageRecord`):
  define the `*Record` in `domain/run_records.py`, add the `*Record` arm to
  the `CycleRecord` discriminated union + a `_handle_*` no-op default to
  `DerivedView` (`infrastructure/projections/base.py`), write a kwargs-only
  `emit_*` helper that reads the active ledger from the `_CYCLE_LEDGER`
  ContextVar (`infrastructure/llm/models.py`) and appends, register the
  projection subscriber. No process-global sink, no wrapper dataclass — the
  call site goes from kwargs to ledger in one hop. `RunCallbacks` stays the
  shape for high-frequency snapshot/phase events the runner already drives;
  emit_*-style is the shape for per-call telemetry buried inside dispatch.

## Backend overlay

`nodes.{name}.config` in the dataset's `pipeline.json` is a sparse overlay merged onto each wire payload by `load_dataset_node_overlay` → `configure_and_apply_pipeline()` (`config.py:342`). It's the sole route for changing a backend tunable — model, provider, temperature, anything in the node's `optimizer.param_keys`. AIME's overlay pins OpenRouter+Mistral; absent an overlay the node uses the backend's `llm_defaults` snapshot (OpenRouter+gpt-oss-20b today) — keep that snapshot accurate, don't repurpose it.

**Resolution is tenant-first.** "The dataset's `pipeline.json`" means the file under the dir `resolve_dataset_config_dir` chose — a tenant upload at `projects/{tenant}/datasets/{slug}/` before a repo benchmark at `datasets/{name}/`. The loaders (`load_dataset_node_overlay`, `load_node_prompt`, …) take that resolved dir (carried on `Session.dataset_config_dir`), never a bare name — so an ingested dataset's overlay + starting prompts load identically to a benchmark's.

**Never edit the backend repo to achieve a switch** (even co-owned ones like the sibling TermNorm backend); if a needed key isn't in the dataset's `param_keys`, extend the snapshot, not the backend. Pipeline-agnostic is a §0 commitment.

**Fork-seed overlay (operator-steered forks only).** A fork's `.overrides/seed.json::pipeline_overlay` is layered ON TOP of the resolved `session.pipeline_params` (which already holds dataset-overlay + campaign-overrides), so the effective precedence is **seed > campaign-override > dataset > backend default** — for that fork only, the dataset file stays immutable. The merge is read-once and applied at the single runner seam (`runner/entry.py::run_optimization` via `_read_fork_seed`), keyed by the known fork `cycle_id` — not threaded through each launcher or `configure_and_apply_pipeline` caller.
