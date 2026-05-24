# Code-Debt Cleanup — Known Bloat Hotspots

Tech-debt backlog, unscheduled. Each tier ships independently; order is by leverage, not dependency.

## What audited clean

- Layer boundaries hold (`domain → application → infrastructure → presentation`); `application/intelligence/` does not import `application/optimization/`.
- `score_search_point()` is the single scoring gateway.
- No sidecar optimizer state — everything rides `OptSearchPoint`.
- **Zero banned-pattern violations** — no shims, no fallback chains, no `legacy`/breadcrumb comments, no future-tense docstrings. The no-backward-compat discipline has held.

Debt is concentrated bloat in the optimization hot-path.

## Bloat hotspots

| File | Lines | Issue |
|---|---|---|
| `application/optimization/dispatch/hub/injections.py` | 1147 | 21 `_r_*` renderer functions in one flat module |
| `application/optimization/l1/score.py` | 1013 | four functions over 140 lines — `decode_signal_effect` (~143, 9 kwargs), `score_one_candidate` (~186), `score_population` (~307, 12-var closure), `l1_score` (~211, ~16 params). Worst file; live picker work churns it |
| `presentation/api/routers/campaigns.py` | 972 | one router, many endpoints + inline shaping |
| `infrastructure/projections/live_dashboard/view.py` | 799 | `LiveDashboardView` — 37 methods, every event kind |
| `infrastructure/store/campaign_store/store.py` | 789 | `CampaignStore` — 37 methods, 4 unrelated concerns |
| `application/optimization/dispatch/llm_call.py` | 686 | `llm_call()` ~240 lines / 11 kwargs; `run_optimizer_node()` 15 params |
| `application/scoring/search_point_scorer.py` | 646 | 8 classes, unclear loop-lifecycle orchestration |
| `application/optimization/cycle.py` | 512 | `Cycle` — 15+ ungrouped fields, mixed lifecycles |

**Root cause under the parameter soup:** untyped nested dicts (`dict[str, dict[str, Any]]`) are the lingua franca for `pipeline_params`, `round_data`, `candidate_results`. With no types, callers bundle loose params and guard with `.get()` ladders (115 in `presentation/views/view_ingress.py` alone).

## Tiered backlog

### Tier 0 — shipped 2026-05-21

`PARAM_SCOPE_KEYS` collapse (duplicate frozenset) · `GSM8K_ANSWER_RE` consolidation · `POBB_DEFAULT_EPSILON` named constant (6 sites). ruff / mypy / pytest green.

### Tier 1 — split `l1/score.py`

Highest leverage; worst file; actively churned. Bundle candidate-independent params of `l1_score` and `decode_signal_effect` into dataclasses (`L1ScoringInput`, signal-decode context). Extract round-winner selection out of `l1_score`. Lift `score_population`'s nested closure (`_pobb_backfill`, picker-refit body) to module-level functions taking explicit context. Target: no function over ~120 lines; natural split into `score.py` (orchestration) + `signal_decode.py` + picker-loop module.

### Tier 2 — structural

- **Split `injections.py`** into a `dispatchers/` subpackage (one module per injection category: wounds / diagnostics / context / examples / rules). `INJECTIONS` registry stays the single seam.
- **Bundle `llm_call.py` params** — `LLMCallContext` dataclass for `(ledger, round_num, candidate_idx, cache)`; keep prompt/template args separate.
- **Typed models for the nested dicts** (L, invasive) — `RoundData` / `CandidateResult` / `PipelineParams`. Dissolves the parameter soup and `.get()` ladders at source.

### Tier 3 — god-class splits (lower urgency, low-churn files)

`CampaignStore` (37 methods) → focused sub-stores composed under existing class · `LiveDashboardView` (37 methods) → per-event-kind handlers; state machine + dispatch table · `Cycle` (15+ fields) → run-config / loop-local / inter-round-bridge / cache sub-objects.

### Tier 4 — cosmetic

Seven `*Context` / `*State` classes (`TenantContext`, `ScoringContext`, `CheckContext`, `_LoopContext`, `ReplayContext`, `CycleState`) — uniform suffix masks distinct concepts; rename to semantic roles. `presentation/views/view_models.py` has 20+ view dataclasses re-declaring `timestamp` / `round_num` / `cycle_id` — wants a base. `task_context: dict[str, Any]` / `l1_layout: dict[str, list[str]]` at L2 output boundary works but key contract is implicit; typed sub-schema would harden it.

## Out of scope

Not a correctness fix · not an architecture change · not a single milestone — each tier is independent.
