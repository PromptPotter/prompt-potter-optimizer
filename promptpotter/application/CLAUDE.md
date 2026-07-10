# application/ — orchestration layer

The use-case layer between `domain/` (pure types, frozen models) and
`infrastructure/` (I/O, persistence, LLM clients). One entry point —
`runner/` — coordinates everything; subpackages each own a coherent
slice of orchestration.

## Layer rule (fails loud at import — no standing test; see [`../../tests/CLAUDE.md`](../../tests/CLAUDE.md))

`application/intelligence/` MUST NOT import from `application/optimization/`.
The optional sensitivity scan and the optimization loop both *consume*
intelligence; intelligence does not depend on either.

## Subpackages

| Subpackage | Owns |
|---|---|
| `bootstrap/` | `init_services` + `init_optimization_loop` — wiring stores, LLM clients, connectors → `Session`; preflight; cycle bootstrap; observability + scoring setup. Pipeline-discovery view fetched at bootstrap time lives here. |
| `optimization/` | The L1/L2/L3 loop primitives: `Cycle` state, candidate generation, critique, validation, transitions, PoBB elimination, `dispatch_hub` injection routing. Curated subpackages `escalation/` (state + decide + rules + firing) and `resume_and_fork/` (decisions + replayers + fork siblings + resume entry). |
| `intelligence/` | Materialized views over the MeasurementArchive: `AxisIndex` (axis-keyed digest), `SampleIndex` (per-sample state), Rasch exploration, hard-sample sorter + archive. Shared by scan and loop. |
| `scoring/` | The `score_search_point()` gateway plus formula compilation, evaluators, sample measurement, composite-fitness metrics. Per CLAUDE.md: gateway is canonical; everything else is implementation detail. |
| `sweep/` | Sweep-mode siblings — cheap A/B for L1 candidates ahead of full promotion. |
| `output/` | Operator-facing artifact writers (`write_log_md`, `write_review_md`, `write_hard_samples_artifacts`) + disk-side view reconstruction (`from_disk_round`, `from_disk_log`). Computes artifacts and writes disk (orchestration), so it lives here — not in `presentation/`. Renders through `application/views` (`to_markdown` + typed view models). |
| `views/` | The **emit contract**: frozen typed View dataclasses (`view_models.py`), the live `PhaseEvent → View` builder (`ingress.py::from_phase_event` — needs same-layer `optimizer_model` + scoring formula evaluators), and markdown rendering (`render/` — `to_markdown` + heatmap + `render_sweep_summary`). Producing these views *is* an orchestration job, so they live here; `presentation/views` imports them upward for terminal (`to_text`) rendering. |

## Top-level modules

- `runner/` — master orchestrator; the optimize-loop entry point (`identity`, `round`, `sweep`, `loop`, `entry`).
- `config.py` — `CampaignConfig` model + LLM factory.
- `origin.py` — campaign origin scoring + dataset loading. `resolve_origin_opt_search_point` resolves the origin OSP by priority **seed → experiment prompts → dataset prompts → empty**: a `CycleSeed`'s `.overrides/seed.json::origin_prompt_fields` *is* the origin (operator-steered fork or campaign-from-origin; lineage stamped from `seed.origin_source`).
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
  `configure_and_apply_pipeline()` (`config.py:839`). See **Backend overlay** below for the merge contract.
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

`nodes.{name}.config` in the dataset's `pipeline.json` is a sparse overlay merged onto each wire payload by `load_dataset_node_overlay` → `configure_and_apply_pipeline()` (`config.py`). It's the sole route for changing a backend tunable — model, provider, temperature, anything in the node's `optimizer.param_keys`. **The dataset OWNS its task model**: every LLM node must carry `config.model` (benchmarks pin theirs, e.g. AIME → OpenRouter+Mistral; a fresh drop inherits the connector's `default_node_config` seed, written into its own committed file). A missing model is a loud setup error in `configure_and_apply_pipeline`, never a silent fall-through to the backend's hidden `GET /pipeline` default. The `llm_defaults` block is a non-authoritative display snapshot — never read for resolution.

**Resolution is tenant-first.** "The dataset's `pipeline.json`" means the file under the dir `resolve_dataset_config_dir` chose — a tenant upload at `projects/{tenant}/datasets/{slug}/` before a repo benchmark at `datasets/{name}/`. The loaders (`load_dataset_node_overlay`, `load_node_prompt`, …) take that resolved dir (carried on `Session.dataset_config_dir`), never a bare name — so an ingested dataset's overlay + starting prompts load identically to a benchmark's.

**Per-dataset tunable switches ride the overlay, never a backend edit.** A model/provider/temperature change for one dataset belongs in `nodes.{name}.config` (extend the overlay, not the backend) — pipeline-agnostic is a §0 commitment, and a truly third-party/read-only backend you can't edit anyway. **TermNorm is the exception to "read-only backend," not to this rule:** it's co-owned/same-project, so a genuine *structural* root cause living in TermNorm's code should be fixed there (coordinate explicitly), keeping both sides as simple as possible — don't patch PromptPotter to paper over a TermNorm-root bug. The line: per-dataset config → overlay; backend behaviour/bug → TermNorm root-fix.

**Cycle-seed overlay (seeded cycles — steered forks + campaign-from-origin).** A seed's `.overrides/seed.json::pipeline_overlay` is layered ON TOP of the resolved `session.pipeline_params` (which already holds dataset-overlay + campaign-overrides), so the effective precedence is **seed > campaign-override > dataset > backend default** — for that cycle only, the dataset file stays immutable. The merge is read-once and applied at the single runner seam (`runner/entry.py::run_optimization` via `_read_cycle_seed`), keyed by the known `cycle_id` — not threaded through each launcher or `configure_and_apply_pipeline` caller.
