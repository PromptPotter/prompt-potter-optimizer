# Code-Debt Cleanup — Known Bloat Hotspots

Tech-debt backlog, unscheduled. Each tier ships independently; order is by leverage, not dependency.

## What audited clean

- Layer boundaries hold (`domain → application → infrastructure → presentation`); `application/intelligence/` does not import `application/optimization/`.
- `score_search_point()` is the single scoring gateway.
- No sidecar optimizer state — everything rides `OptSearchPoint`.
- **Zero banned-pattern violations** — no shims, no fallback chains, no `legacy`/breadcrumb comments, no future-tense docstrings. The no-backward-compat discipline has held.
- **Cross-language drift bounded by codegen** — the dashboard/router Pydantic shapes flow through `scripts/build_ts_types.py` → `webapp/lib/api/types.generated.ts`. Hand-mirrored TS interfaces for those shapes are gone (re-exported with old-name aliases).

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
| `webapp/lib/poll.tsx` | 381 | polling primitive flagged "tangled state" in initial diagnostic; not yet opened — needs a read before a cleanup tier is sized |

**Root cause under the parameter soup:** untyped nested dicts (`dict[str, dict[str, Any]]`) are the lingua franca for `pipeline_params`, `round_data`, `candidate_results`. With no types, callers bundle loose params and guard with `.get()` ladders (115 in `presentation/views/view_ingress.py` alone).

## Tiered backlog

### Tier 0 — shipped

- **2026-05-21:** `PARAM_SCOPE_KEYS` collapse (duplicate frozenset) · `GSM8K_ANSWER_RE` consolidation · `POBB_DEFAULT_EPSILON` named constant (6 sites). ruff / mypy / pytest green.
- **2026-05-24:** `routers/datasets.py` de-knot (639→438) — `marginal_hit_probability` extracted to `adaptive_picker`, `cycle/campaign_measurement_series` into new `application/intelligence/measurement_series.py`, `is_error_result` wired to 3 duplicate predicate sites, `validate_dataset_name` to `store/paths`, `strip_lone_surrogates` to `domain/pipeline_parsing` (parse-time). `LiveDisplay._handle_snapshot` explicit `sample_started` no-op closes a silent display asymmetry. `LiveDashboardState` Pydantic model + `BackfillLogEntry` document the `dashboard.json` shape. `scripts/build_ts_types.py` ships a Pydantic→TS emitter (no new deps); `EXPORTED_MODELS` walks **35 models** across `domain/results`, the live-dashboard state, and six routers (`datasets`, `active`, `campaigns/registry`, `campaigns/lineage`, `measurements`, `verify`); `webapp/lib/api/types.ts` re-exports with old-name aliases. Renamed `shared/errors.is_degraded → has_pipeline_warnings` (near-name clash with `is_error_result`, different concepts). Split `validators/l2_l3.py (535)` → `l2_output.py` + `l3_output.py` (matches the L1 strict/behavior split; `l2_behavior.py` already existed separately). 212 tests + mypy strict + webapp `tsc` green.

### Tier 1 — split `l1/score.py`

Highest leverage; worst file; actively churned. Bundle candidate-independent params of `l1_score` and `decode_signal_effect` into dataclasses (`L1ScoringInput`, signal-decode context). Extract round-winner selection out of `l1_score`. Lift `score_population`'s nested closure (`_pobb_backfill`, picker-refit body) to module-level functions taking explicit context. Target: no function over ~120 lines; natural split into `score.py` (orchestration) + `signal_decode.py` + picker-loop module.

### Tier 2 — mechanical splits + low-risk wins

Independent, follow established patterns, no architecture change:

- **Remaining `EXPORTED_MODELS` candidates** — the emitter walks 35 models today. Still hand-maintained in `types.ts`: `FileEntry` / `FileResponse` / `FilesListing` (`backends`/`campaigns/files` routers — name mismatch with Pydantic side) and `CampaignDetail` / `CampaignRoundSummary` (read straight from `index.json`, not a router response). Wiring each is one entry in `EXPORTED_MODELS` plus a re-export.

### Tier 3 — structural

- **Split `injections.py`** into a `dispatchers/` subpackage (one module per injection category: wounds / diagnostics / context / examples / rules). `INJECTIONS` registry stays the single seam.
- **Bundle `llm_call.py` params** — `LLMCallContext` dataclass for `(ledger, round_num, candidate_idx, cache)`; keep prompt/template args separate.
- **Typed models for the nested dicts** (L, invasive) — `RoundData` / `CandidateResult` / `PipelineParams`. Dissolves the parameter soup and `.get()` ladders at source.

### Tier 4 — god-class splits (lower urgency, low-churn files)

`CampaignStore` (37 methods) → focused sub-stores composed under existing class · `LiveDashboardView` (37 methods) → per-event-kind handlers; state machine + dispatch table · `Cycle` (15+ fields) → run-config / loop-local / inter-round-bridge / cache sub-objects.

### Tier 5 — cosmetic

- Seven `*Context` / `*State` classes (`TenantContext`, `ScoringContext`, `CheckContext`, `_LoopContext`, `ReplayContext`, `CycleState`) — uniform suffix masks distinct concepts; rename to semantic roles.
- `presentation/views/view_models.py` has 20+ view dataclasses re-declaring `timestamp` / `round_num` / `cycle_id` — wants a base.
- `task_context: dict[str, Any]` / `l1_layout: dict[str, list[str]]` at L2 output boundary works but key contract is implicit; typed sub-schema would harden it.
- **Collapse TS↔Pydantic naming aliases.** Six aliases in `webapp/lib/api/types.ts` today (`ActiveSession → ActiveSessionResponse`, `Campaign → CampaignSummary`, `DatasetPreview → DatasetPreviewResponse`, plus three `Dashboard*` ones from the prior cutover). Two-convention drift between webapp's `Dashboard*` / unprefixed names and the server's domain / `*Response` names. Pick one (suggest: drop the prefix, use Pydantic names verbatim) and update the webapp consumers so aliases go away.

### Investigate first

`webapp/lib/poll.tsx` (381) was flagged in the initial line-count diagnostic as "tangled state" but not opened. Per `webapp/AGENTS.md` it's the canonical render-phase guarded reset site, so likely fine — but a read should confirm before a cleanup tier is sized.

## Out of scope

Not a correctness fix · not an architecture change · not a single milestone — each tier is independent.
