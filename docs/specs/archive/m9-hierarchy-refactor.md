# M9 Track 2: Hierarchy Refactor (Hexagonal Layout)

**Version:** 0.1.0
**Date:** 2026-04-12
**Status:** Shipped (hexagonal layout in place: `domain/`, `application/`, `infrastructure/`, `presentation/`)
**Parent:** M9 ([`m9-stable-config-and-scaffolding.md`](m9-stable-config-and-scaffolding.md))

---

## Context

`promptpotter/` has drifted flat. `services/campaign/` holds 12 files mixing orchestration with persistence/IO. `services/search/` holds 8 files mixing scan, smart-search, and memory. `ui/campaign/` holds 8 files with three display modules >25KB each. Tracing, LLM client, store, and backend client live as siblings under `services/` rather than as an infrastructure adapter layer. The largest single files are 37KB, 34KB, 34KB, 32KB, 31KB — and they mix layers.

Three looming requirements make this painful:

1. **Multi-tenant / whitelabel distribution** — tenant context must thread cleanly through every read/write path.
2. **Next.js webapp** — adds a fourth entry point. The current "services is the core" story only half holds: orchestration and I/O are mixed, so the webapp would either re-import CLI-flavored modules or duplicate them.
3. **Notebook ↔ CLI parity gap** — caused by UI wrappers leaking persistence concerns.

Industry standard for CLI + FastAPI + future webapp + multi-tenant is hexagonal / ports-and-adapters: `domain` (pure) → `application` (use cases) → `infrastructure` (adapters) → `presentation` (entry points), plus leaf `shared/` and `config/`.

## Target Hierarchy

```
promptpotter/
├── domain/                 # pure models, no I/O, no logging
│   ├── search_point.py     # JobSearchPoint, PromptTemplate, OptSearchPoint
│   ├── pipeline.py         # PipelineSchema, PipelineNode, NodeOutputSchema
│   ├── scoring.py          # ScoringEnv, RoundResult, composite formulas
│   ├── campaign.py         # LoopState, RunConfig, RunListener
│   └── tenant.py           # TenantContext (new — the multi-tenant seam)
│
├── application/            # use cases / orchestration — no direct disk or network
│   ├── campaign/           # lifecycle, runner, round_execution, setup
│   ├── optimization/       # L1/L2/L3 pipeline, l1_critique, escalation, layer_transitions
│   ├── search/             # search_memory (and dormant recon producers)
│   └── scoring/            # search_point_scorer, sample_measurement, metrics
│
├── infrastructure/         # adapters — all I/O lives here
│   ├── store/              # Stores composite + build_stores() + focused leaf stores
│   ├── backend/            # BackendClient, pipeline parsing
│   ├── llm/                # _OpenAICompatibleClient, providers
│   ├── tracing/            # obs_logger, langfuse_client, langfuse_push
│   └── persistence/        # session_emitter, round_recorder, control surfaces
│
├── presentation/           # entry points — thin, one per surface
│   ├── cli/                # click/typer commands → application
│   ├── api/                # FastAPI routers → application
│   └── ui/                 # notebook + webapp display adapters → application
│       ├── campaign/       # (replaces current ui/campaign/)
│       └── formatters/     # display, phase_display, reporting helpers
│
├── shared/                 # leaf utilities — no domain or application deps
│   ├── errors.py           # graceful(), PauseForReviewError
│   ├── constants.py        # PROMPT_STRING_FIELDS
│   └── scoring.py          # compile_scorer
│
└── config/                 # settings, APP_VERSION, logging setup
```

## Scope

- **Move-only.** This spec does not split any fat file. Each offender is flagged for a follow-up spec.
- **Tenant seam shaped, not enforced.** Add `domain/tenant.py` with a frozen `TenantContext` dataclass and an optional `tenant: TenantContext | None = None` field on `SessionEnv`. No store path changes. No auth middleware.
- **No backward-compat shims.** Imports update in one pass.
- **No behavior change.** If a test's behavior changes, something went wrong.

## Mapping from Current Layout

| Current | Target |
|---|---|
| `services/campaign/{lifecycle,runner,round_execution,setup,reporting,campaign_setup}.py` | `application/campaign/` |
| `services/campaign/{state,control,session_emitter,round_recorder}.py` | `infrastructure/persistence/` |
| `services/campaign/nodes/` | `application/optimization/nodes/` |
| `services/optimizer/` | `application/optimization/` |
| `services/search/` | `application/search/` |
| `services/scoring/` + `services/metrics.py` | `application/scoring/` |
| `services/dataset_builder.py` | `application/datasets/builder.py` |
| `services/store/` | `infrastructure/store/` |
| `services/backend_client.py` | `infrastructure/backend/client.py` |
| `services/llm_client.py` | `infrastructure/llm/client.py` |
| `services/tracing/` | `infrastructure/tracing/` |
| `models/` | `domain/` |
| `cli/` | `presentation/cli/` |
| `routers/` | `presentation/api/` |
| `ui/campaign/` | `presentation/ui/campaign/` |
| `shared/`, `config/` | unchanged |

## Fat Files (Deferred to Follow-Up Specs)

Moved intact in M9; each gets its own splitting spec later.

1. `services/tracing/observability_logger.py` (37KB) → `infrastructure/tracing/observability_logger.py`
2. `cli/campaign_runner.py` (34KB) → `presentation/cli/campaign_runner.py`
3. `services/search/recon_advisor.py` (34KB) → `application/search/recon_advisor.py`
4. `ui/campaign/{search,optimize,phase_display,display}.py` (26–32KB) → `presentation/ui/campaign/`
5. `services/campaign/nodes/formatting.py` (31KB) → `application/optimization/nodes/formatting.py`
6. `services/search/adaptive_recon.py` (28KB) → `application/search/adaptive_recon.py`

## Invariants Preserved

- **Three-layer I/O architecture** (persistence / display / control): persistence → `infrastructure/persistence/`, display → `presentation/ui/formatters/`, control → `infrastructure/persistence/control/`. The `CAMPAIGN_ARTIFACTS` + `SESSION_ARTIFACTS` parity tests move with the persistence code.
- **`score_search_point()` as single scoring gateway.** Lives at `application/scoring/search_point_scorer.py`.
- **`llm_call()` as single optimizer LLM primitive.** Lives at `application/optimization/pipeline.py`, backed by `infrastructure/llm/`.
- **`shared/` rule** (leaf-only, no domain deps). Unchanged.
- **No backward compatibility.** No shims, no re-export modules.

## Multi-Tenant Seam

`domain/tenant.py`:

```python
from dataclasses import dataclass, field

@dataclass(frozen=True)
class TenantContext:
    tenant_id: str
    user_id: str | None = None
    capabilities: frozenset[str] = field(default_factory=frozenset)
```

`SessionEnv` gains `tenant: TenantContext | None = None`. Nothing downstream reads it in M9. Future specs populate and enforce.

## Execution Order (sketch)

Each step is a single commit; tree keeps compiling and tests keep passing between steps.

1. Create empty target tree (`domain/`, `application/`, `infrastructure/`, `presentation/`) with `__init__.py` files.
2. Move `models/` → `domain/`. Add `domain/tenant.py`. Add optional `tenant` field to `SessionEnv`.
3. Move infrastructure adapters: `store`, `backend`, `llm`, `tracing`.
4. Move `services/campaign/` with orchestration/persistence split; move `nodes/` to `application/optimization/nodes/`.
5. Move remaining application code: `optimizer`, `search`, `scoring`, `metrics`, `dataset_builder`.
6. Move presentation: `cli`, `routers`, `ui`.
7. Delete empty `services/`. Update `CLAUDE.md` Architecture section and `docs/architecture/overview.md`.

How and when this sequence runs inside M9 is open. The target shape is the contract.

## Critical Files to Modify

- `pyproject.toml` — package discovery and entry points
- `promptpotter/__init__.py`
- `promptpotter/main.py` — FastAPI app import paths
- `tests/test_artifact_parity.py` — new module paths for persistence artifacts
- `CLAUDE.md` — rewrite Architecture section
- `docs/architecture/overview.md` — disk layout + mental model diagrams
- `notebooks/optimization_campaign.ipynb` — imports from `promptpotter.presentation.ui.campaign`

## Verification

1. `python -m ruff check promptpotter/ tests/ -q && python -m ruff format --check promptpotter/ tests/ -q && python -m mypy promptpotter/ --no-error-summary && python -m pytest tests/ --tb=no -q -p no:warnings` — all green.
2. `tests/test_artifact_parity.py` passes under new module paths.
3. CLI smoke: `python -m promptpotter init … && python -m promptpotter show-status`.
4. API smoke: `uvicorn promptpotter.main:app --port 8001` + hit `/api/v1/backends`.
5. Notebook smoke: top cell import of `optimization_campaign.ipynb` succeeds.
6. `grep -r "from promptpotter.services" promptpotter/ tests/ notebooks/` returns zero.
7. `TenantContext` importable from `promptpotter.domain.tenant`; `SessionEnv.tenant` field exists.

## Out of Scope

- Splitting any fat file (follow-up specs, one per offender).
- Actual tenant enforcement, store path changes, auth middleware.
- Webapp scaffolding (lives in M9 Track 4 and M10/M11 webapp slices).
- Behavior changes of any kind.
