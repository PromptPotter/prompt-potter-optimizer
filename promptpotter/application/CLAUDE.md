# application/ — orchestration layer

The use-case layer between `domain/` (pure types, frozen models) and
`infrastructure/` (I/O, persistence, LLM clients). One entry point —
`runner.py` — coordinates everything; subpackages each own a coherent
slice of orchestration.

## Layer rule (enforced by `tests/test_invariants.py::test_no_unexpected_runtime_layer_violations`)

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
| `datasets/` | Dataset loaders + sample materialization. |

## Top-level modules

- `runner.py` — master orchestrator; the optimize-loop entry point.
- `config.py` — `CampaignConfig` model + LLM factory.
- `origin.py` — campaign origin scoring + dataset loading.
- `resume.py` — campaign config diffing + resume logic.
- `review.py` — per-cycle markdown renderer (post-cycle log).

## Conventions

- Optimizer LLM calls go through `llm_call()` (`optimization/llm_call.py`),
  never `chat()`.
- Escalation flows via return value (`QueryLoopResult.escalation_signal`),
  not exception.
- New optimizer state MUST flow through `OptSearchPoint` — no sidecar state.
- Backend tunables ride the per-dataset overlay
  (`datasets/{name}/pipeline.json::nodes.{name}.config`) merged by
  `configure_pipeline()` (`config.py:301`). Never reconfigure a backend
  repo to switch model/provider — see root `CLAUDE.md`.
