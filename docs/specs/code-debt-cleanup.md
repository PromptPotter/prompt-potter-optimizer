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

### Tier 6 — post-split audit (2026-05-24)

Post-compaction follow-on. Tiers 1 / 3 of this spec called for "split the god-files into subpackages" — the compaction campaign (commits `6dfebb3d` / `25f9c8d9` / `7c56191b` / `eb63d8dc`) shipped that work, and the `l1/score.py 1013` + `injections.py 1147` rows of the hotspots table above are now subpackages. This audit walked the post-split shape for the next-generation bloat: twin surfaces, speculative extractions that produced no seam, and cross-cutting cleanups analogous to the just-shipped webapp display-source unification (commit `7b500aac`, archived spec [`archive/webapp-display-source-unification.md`](archive/webapp-display-source-unification.md)). Methodology: four parallel `Explore` subagents over `dispatch/`, `l1/score/` + `pobb/`, `projections/`, `escalation/` + `resume_and_fork/`.

**Bite-size cleanups (single file, no API change):**

- **Drop `catalogues.py` global pipeline-param cache** (audit-1.A) — `dispatch/hub/injections/catalogues.py:27-83` keeps a one-entry `id(schema)`-keyed cache for a sub-millisecond render. Premature optimisation with apologetic docstring; delete cache + module-global var.
- **Inline four `live_dashboard/` helper submodules into `view.py`** (audit-1.C) — `candidate_block.py` (175L) + `score.py` (90L) + `sample.py` (66L) + `pobb.py` (42L) each have exactly one caller, no separate tests. Pure-helper extractions that produced no seam. Inline as `LiveDashboardView` private methods. Keep `factory.py` (resume-state healing) and `round_summary.py` (the just-shipped `dash.rounds[]` shape transform).
- ~~Delete `LiveStateView` wrapper~~ (audit-1.D, *retracted 2026-05-24*) — the auditor's "wrapper class" doesn't exist on disk. `projections/live_state.py` is already the clean shape: `LiveStateCore` dataclass + free helper functions, no "View" indirection. The stale `LiveStateProjection` row in `infrastructure/CLAUDE.md` (empty description column) is a doc-only artefact; trim during a future docs sweep.
- **Collapse one-line accessor renderers in `layer_state.py` + `panels.py`** (audit-1.B) — six 2–3-line wrappers around `OptSearchPoint` field reads (`_r_plan`, `_r_rendered_prompt`, `_r_l3_to_l2_note`, `_r_task_context`, `_r_critique`, `_r_l1_overrides`). Replace with a `_make_accessor_renderer` factory + direct `INJECTIONS` dict entries. Net ~150–200 LOC removed.

**Mid-size refactors (touches one signature or moves a file):**

- ~~Move `pobb/elevation.py` cross-cycle CLI workflow~~ (audit-2.A, *retracted 2026-05-24*) — the auditor flagged the file as "doesn't belong in `optimization/pobb/` because it's not called from L1/L2/L3", but `presentation/CLAUDE.md` explicitly forbids business logic in `cli/commands/` ("thin shells … business logic that creeps in here is drift — push it into `application/`"). `elevation.py` IS PoBB (multi-arm posterior + adaptive top-up), just at cross-cycle scope rather than within-round, and the workflow rides `score_search_point` + `archive_views` — domain work, not CLI. Current location stays; CLI thin shell in `presentation/cli/commands/compare.py` already imports + orchestrates correctly.
- **Decouple `AuditTrailView` from `LiveDashboardView` sticky-nodes** (audit-2.B) — `view.py::_persist()` calls `AuditTrailView.snapshot_nodes()` to populate `dashboard.json::current_round`. Backwards coupling: audit-trail acts as source for live-dashboard. Invert — `LiveDashboardView` owns `_l1_score`; deposits to audit-trail at ROUND:display via new `recorder.deposit_l1_score_for_round(block)`. Production-side analogue of the just-shipped webapp display-source unification.
- **Merge `l2_driver.py` + `l3_driver.py` into `escalation/firing/executor.py`** (audit-2.C) — drivers are pure `LayerStrategy` data (parse / apply / enter_payload / exit_payload tuples), never called directly; `executor.escalate_l2()` is the sole entry point. Inline as `L2_STRATEGY` / `L3_STRATEGY` module-level constants. *Hold if L4 outer-loop is imminent and will add a driver.*

**Tier-3-level arcs (mini-spec first):**

- **Extract `Cycle.start()` bootstrap helpers** (audit-3.A) — `application/optimization/cycle.py` (~516L) carries 18 fields and a 200+ LOC factory that inlines archive-observation discovery + sibling-failure inheritance + pipeline-param validation. Extract `_inherit_sibling_runtime_failures()` + `_load_archive_observations()` to a new `bootstrap/cycle_builders.py`. High blast — runner, resume path, fork minting, intelligence-layer init all touch.
- **`view.py` three-concerns split** (audit-3.B) — after audit-1.C inlines and audit-2.B inverts, `view.py` becomes three cohesive sections (scalar tracking / round-state mutations / builders + persist). Visibly section the file or extract a private `_RoundState` class. **Prereq: audit-1.C + audit-2.B both landed.**

**Done log** (populated as items ship — format: `<commit-hash>` · `<audit-tag>` · one-line summary):

- `bf7907d1` · spec · Tier 6 post-split audit findings written into this spec.
- `24bc41c1` · audit-1.A · `catalogues.py` pipeline-param cache dropped (premature optimisation + apologetic docstring, one-entry global).
- *pending commit* · audit-1.C · four `live_dashboard/` helper submodules (`candidate_block`, `score`, `sample`, `pobb`) inlined into `view.py` as class methods + module-level helpers; submodule files deleted.
- *pending commit* · audit-1.B · four trivial accessor renderers (`_r_plan`, `_r_rendered_prompt`, `_r_l3_to_l2_note`, `_r_l1_overrides`) collapsed via a new `accessor_renderer(accessor, template, *, json_value=False)` factory in `bundle.py`. `_r_critique` + `_r_task_context` kept (real logic).
- *pending commit* · audit-2.B · `AuditTrailView.snapshot_nodes()` + `_sticky_nodes` + `rehydrate_sticky()` removed. `LiveDashboardView` now owns its own `_sticky_llm_calls` mirror, fed by overriding `_handle_llm_call` (sticky + in-flight clear) and seeded on resume via `read_most_recent_round_nodes(rounds_dir)`. Shared `build_node_block(record)` projects `LLMCallRecord → nodes[*]` for both subscribers. Production-side analogue of the read-side display-source unification.

**Notes from this session (2026-05-24):**

- Two audit items retracted, not shipped: audit-1.D (no `LiveStateView` wrapper exists on disk — the auditor invented one; file is already the clean `LiveStateCore` + free-function shape) and audit-2.A (proposed destination `presentation/cli/commands/compare.py` violates the layer rule against business logic in `cli/`; current location in `optimization/pobb/` is defensible — the workflow IS PoBB at cross-cycle scope).
- The stale `LiveStateProjection` row in `infrastructure/CLAUDE.md` (empty description column) is doc-only drift; trim during a future docs sweep.
- Test count unchanged through the arc: 212 tests, mypy strict + ruff format/check + pytest all green at every step.

**Cleared (defensible, no action):**

- **Escalation:** `escalation/state.py` (309L, cohesive FSM), `escalation/firing/fork_siblings.py` (263L, tight dispatch + local handlers), all four validators (`l1_strict`, `l1_behavior`, `l2_behavior`, `l2_l3` — zero overlap; strict ≠ behavior, L1 ≠ L2).
- **DispatchHub:** `dispatch/hub/builder.py` (88L, single-purpose), `dispatch/hub/auto_rules.py` (124L, every entry consumed), `dispatch/hub/injections/wounds.py` "four wound channels" (genuinely four distinct concerns: validation / runtime / L2 guard / L3 guard — not collapsible).
- **L1 score post-compaction:** `l1/score/candidate.py` (239L), `winner.py` (285L), `signal_effect.py` (206L), `classification.py` (221L), `population.py` (212L) — each defensible after the P3 split.
- **Projections:** `base.py` (60L, load-bearing dispatch routing), `pobb_stream.py` (109L, focused JSONL appender), `live_dashboard/factory.py` (70L, resume-state healing), `live_dashboard/round_summary.py` (56L, the just-shipped `dash.rounds[]` shape transform).
- **Resume/fork:** `resume_and_fork/decisions.py` (104L), `replayers.py` (290L), `resume.py` (157L) — clean.
- **Application:** `transitions.py`, `round_analysis.py`, `task_context.py` — appropriately sized.

### Investigate first

`webapp/lib/poll.tsx` (381) was flagged in the initial line-count diagnostic as "tangled state" but not opened. Per `webapp/AGENTS.md` it's the canonical render-phase guarded reset site, so likely fine — but a read should confirm before a cleanup tier is sized.

Two items from the Tier 6 post-split audit also land here — confidence too low to act without evidence:

- **`facade.py::_apply_budget` shed-rate** (audit-2.D) — 60-LOC tiered shed allocator at `dispatch/hub/facade.py:88-151`, fires when composed prompt > 10k chars. Audit estimated rare-fire from a ~4.7k MANDATORY-injection back-of-envelope; memory note ([[feedback-optimizer-prompt-size]]) says the budget is the *enforcement* mechanism for a real ≤10k-char constraint, not an emergency valve. Instrument shed-rate over a few campaigns before deciding to drop / relax.
- **`PoBBCheck.check()` separability gate** (audit-2.E) — audit cited `pobb/elimination/checks.py:395-404` as a hard-coded `alpha=0.05` separability gate. Memory note ([[project-pobb-separability-floor]]) says the floor was dropped in commit `39369a5c`. The cited lines may already be gone or may refer to a different statistical guard. Read-only verify needed before any action.

## Out of scope

Not a correctness fix · not an architecture change · not a single milestone — each tier is independent.
