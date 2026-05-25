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

- Seven `*Context` / `*State` classes (`TenantContext`, `ScorerSetup`, `ValidatorContext`, `QueryLoopState`, `ReplayContext`, `CycleSnapshot`) — uniform suffix masks distinct concepts; rename to semantic roles.
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
- **Trim stale `LiveStateProjection` row from `infrastructure/CLAUDE.md`** (audit-1.E) — DONE. Empty-description row deleted from the projections table; shipped shape is `LiveStateCore` dataclass + free helpers.

**Mid-size refactors (touches one signature or moves a file):**

- ~~Move `pobb/elevation.py` cross-cycle CLI workflow~~ (audit-2.A, *retracted 2026-05-24*) — the auditor flagged the file as "doesn't belong in `optimization/pobb/` because it's not called from L1/L2/L3", but `presentation/CLAUDE.md` explicitly forbids business logic in `cli/commands/` ("thin shells … business logic that creeps in here is drift — push it into `application/`"). `elevation.py` IS PoBB (multi-arm posterior + adaptive top-up), just at cross-cycle scope rather than within-round, and the workflow rides `score_search_point` + `archive_views` — domain work, not CLI. Current location stays; CLI thin shell in `presentation/cli/commands/compare.py` already imports + orchestrates correctly.
- **Decouple `AuditTrailView` from `LiveDashboardView` sticky-nodes** (audit-2.B) — `view.py::_persist()` calls `AuditTrailView.snapshot_nodes()` to populate `dashboard.json::current_round`. Backwards coupling: audit-trail acts as source for live-dashboard. Invert — `LiveDashboardView` owns `_l1_score`; deposits to audit-trail at ROUND:display via new `recorder.deposit_l1_score_for_round(block)`. Production-side analogue of the just-shipped webapp display-source unification.
- **Merge `l2_driver.py` + `l3_driver.py` into `escalation/firing/executor.py`** (audit-2.C) — drivers are pure `LayerStrategy` data (parse / apply / enter_payload / exit_payload tuples), never called directly; `executor.escalate_l2()` is the sole entry point. Inline as `L2_STRATEGY` / `L3_STRATEGY` module-level constants. *Hold if L4 outer-loop is imminent and will add a driver.*

**Tier-3-level arcs (mini-spec first):**

- ~~**Extract `Cycle.start()` bootstrap helpers**~~ (audit-3.A, DONE 2026-05-25) — extracted four private module-level helpers in `cycle.py` itself: `_build_initial_opt_sp`, `_assert_overlay_preserved`, `_inherit_sibling_runtime_failures`, `_load_archive_observations`. `Cycle.start()` collapsed from ~103 LOC to a 36-LOC orchestrator that names each step. Deviated from the "new `bootstrap/cycle_builders.py` module" shape in the original audit — keeping helpers local mirrors audit-1.C's live_dashboard inlines and avoids the high-blast new-dependency edges (no public API change, zero `cycle.*` access sites touched). mypy strict (235 files) + 212 pytest + ruff format/check green.
- **`view.py` three-concerns split** (audit-3.B) — after audit-1.C inlines and audit-2.B inverts, `view.py` becomes three cohesive sections (scalar tracking / round-state mutations / builders + persist). Visibly section the file or extract a private `_RoundState` class. **Prereq: audit-1.C + audit-2.B both landed.**

**Done log** (populated as items ship — format: `<commit-hash>` · `<audit-tag>` · one-line summary):

- `bf7907d1` · spec · Tier 6 post-split audit findings written into this spec.
- `24bc41c1` · audit-1.A · `catalogues.py` pipeline-param cache dropped (premature optimisation + apologetic docstring, one-entry global).
- *pending commit* · audit-1.C · four `live_dashboard/` helper submodules (`candidate_block`, `score`, `sample`, `pobb`) inlined into `view.py` as class methods + module-level helpers; submodule files deleted.
- *pending commit* · audit-1.B · four trivial accessor renderers (`_r_plan`, `_r_rendered_prompt`, `_r_l3_to_l2_note`, `_r_l1_overrides`) collapsed via a new `accessor_renderer(accessor, template, *, json_value=False)` factory in `bundle.py`. `_r_critique` + `_r_task_context` kept (real logic).
- *pending commit* · audit-2.B · `AuditTrailView.snapshot_nodes()` + `_sticky_nodes` + `rehydrate_sticky()` removed. `LiveDashboardView` now owns its own `_sticky_llm_calls` mirror, fed by overriding `_handle_llm_call` (sticky + in-flight clear) and seeded on resume via `read_most_recent_round_nodes(rounds_dir)`. Shared `build_node_block(record)` projects `LLMCallRecord → nodes[*]` for both subscribers. Production-side analogue of the read-side display-source unification.
- `abce3c03` · audit-3.A · `Cycle.start()` bootstrap collapsed from ~103 LOC to a 36-LOC orchestrator by lifting four private helpers into `cycle.py`: `_build_initial_opt_sp` (origin OSP seed with task_context + l1_overrides nested under `memory`), `_assert_overlay_preserved` (dataset-overlay invariant), `_load_archive_observations` (per-(backend, dataset) prior measurements), `_inherit_sibling_runtime_failures` (pull RuntimeFailures from sibling forks). Deviated from the audit's "new `bootstrap/cycle_builders.py` module" — keeping helpers local mirrors audit-1.C's inlines and avoids new dependency edges. Zero `cycle.*` access sites touched; closes the Tier 6 audit backlog.

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

## Pre-public-release polish arc (2026-05-25 forward)

**Context.** Three months of solo dev + AI-assisted iteration left
PromptPotter end-to-end wired but rough at the public-facing edges.
Today's Tier 6 audit (commit `f412a92c`) closed four tactical bloat
items inward. This arc continues the cleanup outward + lateral +
inward simultaneously, on the way to public release.

A three-agent parallel audit (facade + spec-tree + code-debt) produced
the items below. Five workstreams, ~27 items, each a self-contained
session unit an agent can pick up and finish without needing the
conversation context. Pick from the **Sequencing** list at the bottom
in priority order — that's the recommended pass order.

**No time-bucket framing on this arc** — with AI assistance running
items end-to-end, "Pass 1" lands in hours not days. Pick the next
TODO in sequence; commit; move on.

### How to read

Every item below has the same fields:

- **Status:** TODO · DONE · RETRACTED · HELD (with reason)
- **Workstream:** A–E
- **Confidence:** HIGH · MEDIUM · LOW
- **Blast radius:** Small · Medium · Large
- **Prereqs:** other item IDs or "None"
- **Files:** explicit paths the agent edits
- **Steps:** numbered, concrete
- **Verification:** how the agent knows it's done

Agent execution pattern: grep for `**Status:** TODO` in this section,
pick the lowest-numbered TODO per the Sequencing list, execute, then
either flip to `**Status:** DONE` with a commit hash or to
`**Status:** RETRACTED` with a reason. Append the done item to the
"Done log (polish arc)" section at the bottom of this arc as a
one-liner: `<commit-hash>` · `polish-X.Y` · summary.

---

### Workstream A — Construction-site scrub (operator-facing surfaces)

Strip internal jargon, milestone numbers, future-tense disclaimers,
and TODO-style scaffolding signs from anywhere a public operator can
see them. Internal docs and developer CLAUDE.md trees stay as-is —
they're for AI + maintainers.

#### polish-A.1 — Scrub M-milestone numbers from operator-visible surfaces
**Status:** DONE (`c7794a11`) · **Workstream:** A · **Confidence:** HIGH · **Blast:** Small · **Prereqs:** None
**Files (known):** `webapp/components/dashboard/ChatPane.tsx` (line ~42), `webapp/lib/terms.ts` (lines ~50, 53), `promptpotter/presentation/api/routers/measurements.py` (line ~7); plus all hits from the grep below.
**Why:** Public operator should not see "lands in M12", "M13 Slice C", etc. Either feature-describe or move to an internal-only ROADMAP.md.
**Steps:**
1. `grep -rn "M1[0-3]" webapp/ promptpotter/presentation/ docs/manual/ docs/operations/ README.md` (run via Bash). Skip developer CLAUDE.md + `docs/developer/` + `docs/specs/`.
2. For each operator-visible hit, replace the milestone reference with a feature description ("planned for the next release", "currently read-only") or remove the line entirely.
3. If genuinely useful to keep a roadmap reference, create `ROADMAP.md` at repo root with terse "next: multi-connector + control plane" + link to `docs/specs/roadmap.md`. Don't link `ROADMAP.md` from operator docs — it's an internal pointer.
**Verification:** Re-grep returns zero hits in the named operator-visible paths. CI green.

#### polish-A.2 — Translate domain-internal vocabulary on operator surfaces
**Status:** DONE · **Workstream:** A · **Confidence:** HIGH · **Blast:** Small · **Prereqs:** None · **Note:** Four `phase_l*`/`node_l*` keys in `terms.ts` are tooltip-lookup keys keyed off the UI's phase short-names (`dash.state` literals) — their values translate the labels in plain English; renaming keys would require coordinating with the canonical phase short-names (out of scope here).
**Files:** `webapp/lib/terms.ts`, `webapp/components/dashboard/`, `promptpotter/presentation/cli/parsers.py`.
**Why:** A public operator should not need to know "L1/L2/L3", "PoBB", "OptSearchPoint" to use the product. Internal code keeps canonical names; user-visible text translates.
**Steps:**
1. Audit `webapp/lib/terms.ts` line-by-line. For each entry containing `L1`, `L2`, `L3`, `PoBB`, `OptSearchPoint`, `dispatch`, `injection`, rewrite to operator language: "L1/L2/L3" → "candidate generation / framing refinement / strategic replan"; "PoBB" → "statistical confidence"; "OptSearchPoint" → "candidate".
2. Grep `webapp/components/` for the same internal terms in JSX strings; rewrite.
3. Grep `promptpotter/presentation/cli/parsers.py` for argparse `help=` strings containing the same terms (note `stall_exploration`, `exploration_budget`, `l1_layout`). Rewrite to plain operator language.
**Verification:** `grep -r "PoBB\|OptSearchPoint\|l1_layout\|l1_generate\|l2_context\|l3_plan" webapp/lib/terms.ts webapp/components/ promptpotter/presentation/cli/parsers.py` returns zero hits. `npx tsc --noEmit && npm run lint && npm run build` in `webapp/`. CI green for Python.

#### polish-A.3 — Future-tense scrub on operator surfaces
**Status:** DONE (`54e1d9bf`) · **Workstream:** A · **Confidence:** HIGH · **Blast:** Small · **Prereqs:** None (recommended pair: polish-A.1)
**Files:** `webapp/lib/terms.ts` (line ~43 has the textbook offender: "Placeholder label. Not wired up yet; final feature shape may change."), full sweep across `webapp/` + `promptpotter/presentation/` + `README.md`.
**Why:** Visitor must never see "WIP", "not yet wired", "coming soon", "final feature shape may change", "hardcoded until" copy on a public surface.
**Steps:**
1. `grep -rn "will replace\|not yet\|coming soon\|WIP\|final feature shape\|hardcoded until\|future improvement\|TBD" webapp/ promptpotter/presentation/ README.md` (skip developer CLAUDE.md).
2. For each hit: either land the feature it describes, hide the UI/copy that depends on it, or rewrite to a present-tense statement. **Default action:** hide the UI element. Disabled-with-disclaimer is worse than not-shown.
**Verification:** Re-grep returns zero hits in operator-visible paths. Visual smoke: open `/ui/` — no scaffolding-language tooltips anywhere a hover lands.

#### polish-A.4 — Operator-visible TODO/FIXME sweep
**Status:** DONE · **Workstream:** A · **Confidence:** HIGH · **Blast:** Small · **Prereqs:** None · **Note:** No-op — grep verified zero hits across `webapp/lib/`, `webapp/components/`, `webapp/app/`, `promptpotter/presentation/`, `docs/manual/`, `docs/operations/`, `README.md`. The prior compaction arcs cleaned this up.
**Files:** Wherever the next greps surface them. Internal `# TODO` in implementation `.py` files are out of scope (those are developer notes).
**Why:** A `// TODO` rendered in the UI or visible in `--help` output is a credibility hit.
**Steps:**
1. `grep -rn "TODO\|FIXME\|XXX\|HACK" webapp/lib/ webapp/components/ webapp/app/ promptpotter/presentation/ docs/manual/ docs/operations/ README.md`. Skip developer CLAUDE.md, `docs/developer/`, and `tests/`.
2. For each operator-visible hit: either land the work or remove the TODO comment.
**Verification:** Re-grep zero hits in operator-visible paths.

#### polish-A.5 — ChatPane in-JSX scaffolding sweep
**Status:** DONE (`1d58c19a`) · **Workstream:** A · **Confidence:** HIGH · **Blast:** Small · **Prereqs:** None (recommended pair: polish-A.1)
**Files:** `webapp/components/dashboard/ChatPane.tsx` (lines 41, 205), `webapp/components/dashboard/ConfigMenu.tsx` (lines 4-17).
**Why:** Operator-surface audit found three scaffolding hits not covered by A.1/A.3: a JSX comment at line 41 referencing M12, a live tooltip "Adjust spend / finishing criteria — wired in M12" at line 205, and a block of M12 future-tense roadmap comments at the top of `ConfigMenu.tsx`.
**Steps:**
1. Read `ChatPane.tsx` end-to-end. For each milestone-referencing comment or live string, delete the comment and either land the feature it described or remove the affordance it annotated.
2. Same for `ConfigMenu.tsx` lines 4-17.
**Verification:** `grep -n "M1[0-3]" webapp/components/dashboard/ChatPane.tsx webapp/components/dashboard/ConfigMenu.tsx` returns zero.

#### polish-A.6 — `measurements.py` docstring scrub
**Status:** DONE (`1c3cc131`) · **Workstream:** A · **Confidence:** HIGH · **Blast:** Small · **Prereqs:** None
**Files:** `promptpotter/presentation/api/routers/measurements.py` (line ~3).
**Why:** Module docstring carries "Single endpoint backing the webapp's leverage panel (M13 Slice C)" — operator-visible via `/openapi.json` schema docs.
**Steps:** rewrite the module docstring to describe what the endpoint returns, not which release ships it.
**Verification:** `grep -rn "M1[0-3]\|Slice [A-Z]" promptpotter/presentation/api/routers/` returns zero.

---

### Workstream B — Webapp facade polish

A first-time `/ui/` visitor on a fresh install should never see
disabled-but-clickable controls, hardcoded one-dataset stubs, or
"the rest lands in M12" copy. Either the feature ships or the UI for
it is gone.

#### polish-B.1 — Empty-state authoring
**Status:** DONE (`a5801a13`) · **Workstream:** B · **Confidence:** HIGH · **Blast:** Small · **Prereqs:** None
**Files:** `webapp/components/` (campaign list sidebar + dashboard landing).
**Why:** Today the sidebar shows raw "no campaigns" when nothing has been run. Public visitor sees that and has no idea what to do.
**Steps:**
1. Locate the no-campaigns empty-state component (likely in the sidebar or a `CampaignList` component under `webapp/components/`).
2. Replace the empty-state text with an onboarding card: a short headline + the command to run: `python -m promptpotter new <dataset>` (rendered as inline code) + a one-liner pointer to the docs/manual quickstart.
3. Same logic for per-campaign empty round states if any are still raw "no rounds yet" copy.
**Verification:** Open `/ui/` in a workspace with zero campaigns; the empty state reads as guidance, not an error. `npm run build` green.

#### polish-B.2 — Hide non-functional controls
**Status:** DONE (`763c29eb`) · **Workstream:** B · **Confidence:** HIGH · **Blast:** Small · **Prereqs:** None
**Files:** `webapp/components/shell/Topbar.tsx` (line ~29 — disabled search input), `webapp/components/dashboard/ChatPane.tsx` (non-functional New Job + input + toggles), Topbar tabs that gate (Compare, Leverage, Verify).
**Why:** Disabled-looking-enabled controls signal broken, not unfinished. Either wire the feature or remove the affordance.
**Steps:**
1. For each disabled control listed above, **default action: remove from the DOM**. Don't ship it as `disabled`. A control absent from the UI is honest; a disabled control with a tooltip "lands with M12" is a credibility hit.
2. Where a tab routes to half-built content (Compare / Leverage / Verify if their inner panes are stubs), check the inner pane: if it's a stub, remove the tab from the topbar. If it works, keep it.
**Verification:** Visual `/ui/` walk: no greyed-out controls with disabled handlers. Every visible affordance does something.

#### polish-B.3 — Hardcoded config-menu removal
**Status:** DONE · **Workstream:** B · **Confidence:** HIGH · **Blast:** Small · **Prereqs:** None · **Note:** Wholesale removal — `ConfigMenu.tsx` deleted, import + render dropped from `ChatPane.tsx`, surrounding flex header simplified to single-row status badge. Also moots polish-B.6.
**Files:** `webapp/components/dashboard/ConfigMenu.tsx` (lines ~30-42 — `FROZEN_BY_DATASET` hardcoded for `aime_2025`).
**Why:** A config-menu that secretly only works for one dataset is worse than no menu. The audit-cited comment is literally "Hardcoded until the API ships."
**Steps:** Default action: **remove the ConfigMenu component + the geartooth button that opens it**. The config-edit surface depends on a not-yet-shipped API endpoint and is itself M12 control-plane work — don't ship half of it. If preferred, replace with a read-only "view config" surface that reads from `dashboard.json::current_round`.
**Verification:** Geartooth button absent from dashboard topbar (or routes to read-only view). `npm run build` green.

#### polish-B.4 — Copy audit pass on `webapp/lib/terms.ts`
**Status:** DONE · **Workstream:** B · **Confidence:** HIGH · **Blast:** Small · **Prereqs:** polish-A.2 (jargon translation) · **Note:** Two future-tense leftovers fixed (`newjob_bar_budget` "Currently always uncapped" → "no cap enforced"; `newjob_bar_eta` "until spend tracking is wired" → "when the budget is uncapped or spend is unknown"). One internal-jargon `task-context` collapsed to plain "framing" in `phase_l2_refining`. Remainder already conformant after A.2 + A.3.
**Files:** `webapp/lib/terms.ts` (the canonical glossary backing tooltips everywhere).
**Why:** This file is the single source of truth for tooltip copy. Currently mixes polished entries with scaffolding language.
**Steps:**
1. Read the full file. For every entry, rewrite the description to: (a) present tense, (b) operator language (no PoBB / OptSearchPoint / L1-L3), (c) no future references, (d) one sentence max where possible.
2. Where an entry exists only because a UI element references it and the UI element will be removed by polish-B.2 / polish-B.3, delete the entry too.
**Verification:** Every entry passes the four criteria above. `npm run lint && npx tsc --noEmit && npm run build` green.

#### polish-B.5 — ChatPane disabled-affordance purge
**Status:** DONE (`4cbeee54`) · **Workstream:** B · **Confidence:** HIGH · **Blast:** Small · **Prereqs:** None (pair with polish-B.2)
**Files:** `webapp/components/dashboard/ChatPane.tsx` (lines 273-279, 286-322).
**Why:** Audit caught six disabled-but-visible controls not enumerated under B.2: attach + chat textarea + send button (lines 273-279), and three toggles (Extended thinking, Web search, Code execution, lines 286-322) with `toggle locked` class and no tooltip. Renders as broken UI on first visit.
**Steps:** remove the disabled `<input>` / `<button>` affordances and the three locked toggles from the DOM. If the surrounding layout collapses, restructure the container so what remains looks intentional. No "disabled with tooltip" workaround.
**Verification:** visual `/ui/` walk — no greyed-out controls in the ChatPane footer/toolbar.

#### polish-B.6 — ConfigMenu footer placeholder removal
**Status:** DONE-by-prereq · **Workstream:** B · **Confidence:** HIGH · **Blast:** Small · **Prereqs:** polish-B.3 (decides whether ConfigMenu survives at all) · **Note:** Moot — polish-B.3 elected wholesale removal of ConfigMenu, so the footer line was deleted with the rest of the component.
**Files:** `webapp/components/dashboard/ConfigMenu.tsx` (line ~139).
**Why:** Footer line "More config knobs land here as the menu grows." is pure scaffolding language. Moot if B.3 elects wholesale removal; if B.3 kept a read-only surface, this line still has to go.
**Steps:** if B.3 wholesale-removed ConfigMenu, mark this DONE-by-prereq. Otherwise delete the footer line + the conditional that renders it.
**Verification:** ConfigMenu either absent entirely (B.3 path) or rendered without future-tense footer copy.

---

### Workstream C — Spec tree maturity triage

`docs/specs/` mixes shipped / aspirational / internal at the same
level. Triage so the tree presents a polished public roadmap +
clearly-labeled internal scaffolding.

#### polish-C.1 — Archive shipped specs
**Status:** DONE (`dd22307b`) · **Workstream:** C · **Confidence:** HIGH · **Blast:** Small · **Prereqs:** polish-C.5
**Files (move target):** `docs/specs/archive/`
**Files (to move):**
- `docs/specs/hard-sample-sorter.md` → archive. Phase 1 shipped; phases 2–3 belong in `m12-plus-backlog.md`. Add a one-liner to backlog spec referencing the deferred phases.
- `docs/specs/code-debt-cleanup.md` → **DO NOT ARCHIVE.** This is the live active arc — Tier 6 just shipped, this polish arc lives here. The spec-audit recommendation was wrong on this one (it's a backlog spreadsheet, but it's the active backlog, not historical).
- `docs/specs/security-audit.md` → archive after embedding the three deferred items into `m12-control-plane.md` and the M11 security hardening section.
**Steps:**
1. Move `hard-sample-sorter.md` to `archive/`. Add the deferred phases-2/3 line to `m12-plus-backlog.md`.
2. For `security-audit.md`: read the deferred-items list; embed each as a prereq line in `m12-control-plane.md`. Then `git mv` to `archive/`.
3. Update `docs/specs/CLAUDE.md` index to reflect the moves (or this gets folded into polish-C.4).
**Verification:** `ls docs/specs/` no longer lists the moved files; `ls docs/specs/archive/` does; `docs/specs/CLAUDE.md` index agrees.

#### polish-C.2 — Reclassify aspirational specs
**Status:** DONE · **Workstream:** C · **Confidence:** HIGH · **Blast:** Small · **Prereqs:** None · **Note:** All three specs now open with a blockquote status banner. `grep` for "we'll | will replace | in the next release" across the three already returned zero hits.
**Files:** `docs/specs/m11-spend-tracking.md`, `docs/specs/m12-control-plane.md`, `docs/specs/m12-multi-connector.md`.
**Why:** All three read like promises without delivery (future-tense "will / we'll"). Public reader stumbling in sees infrastructure-in-progress.
**Steps:**
1. Each of the three specs: add a top-level header `> **Status:** Forward direction — spec only, no shipped code.` (or for m12-multi-connector which has partial shipping: `> **Status:** Partial — Track 1 (connector boundary + TermNorm) shipped; remainder forward direction.`).
2. In each spec, rewrite prescriptive "we'll", "will replace" sentences to descriptive "this design proposes…" or "when this lands, …".
3. Do NOT delete content — just reframe. Memory says "augment in place, don't rewrite".
**Verification:** Each spec opens with a clear status banner. `grep -n "we'll\|will replace\|in the next release" docs/specs/m1{1,2}-{spend-tracking,control-plane,multi-connector}.md` returns minimal hits.

#### polish-C.3 — Public-facing roadmap front door
**Status:** DONE · **Workstream:** C · **Confidence:** HIGH · **Blast:** Small · **Prereqs:** polish-C.1, polish-C.2 · **Note:** `docs/roadmap.md` minted (Shipped / Next / Direction split); root README gets a new `# Roadmap` section linking it + `docs/specs/roadmap.md`.
**Files (new):** `docs/roadmap.md` (NOT inside `docs/specs/`).
**Why:** Public readers should see a lightweight roadmap first, not the developer-dense `docs/specs/roadmap.md`. Two audiences, two pages.
**Steps:**
1. Create `docs/roadmap.md`. One short page: "Public release covers: prompt + pipeline optimization, statistical early-stopping, cross-run memory. Next: multi-connector + control plane. Full development plan: [`specs/roadmap.md`](specs/roadmap.md)."
2. Link `docs/roadmap.md` from the root `README.md` if a roadmap section exists; don't link from `docs/manual/` (manual is operator-facing).
3. Do NOT merge with `docs/specs/roadmap.md` — different audiences.
**Verification:** File exists; root README links it; visual scan reads as marketing-clean, not developer-dense.

#### polish-C.4 — `docs/specs/CLAUDE.md` index refresh
**Status:** DONE · **Workstream:** C · **Confidence:** HIGH · **Blast:** Small · **Prereqs:** polish-C.1, polish-C.2 · **Note:** Reference table collapsed to live entries only; explicit Archive section lists hard-sample-sorter + security-audit moves with pointers to the specs that absorbed their deferred items.
**Files:** `docs/specs/CLAUDE.md`.
**Why:** After C.1 archives + C.2 reclassifies, the spec index drifts. Bring it back in sync.
**Steps:**
1. Walk the index entries. Mark each as: SHIPPED / FORWARD / REFERENCE / ARCHIVED.
2. Move archived entries to an explicit "Archived" section near the bottom.
3. Keep the existing TODO §0-promises list at the top (it's load-bearing for AI readers).
**Verification:** Every line of the index matches the actual state of `docs/specs/` + `docs/specs/archive/`.

#### polish-C.5 — Embed deferred security-audit items into target specs
**Status:** DONE (`7824fd32`) · **Workstream:** C · **Confidence:** HIGH · **Blast:** Small · **Prereqs:** None (prereq FOR polish-C.1's `security-audit.md` archive step)
**Files:** `docs/specs/security-audit.md` (read source), `docs/specs/m12-control-plane.md` + `docs/specs/m11-publication-benchmarks.md` (embed targets).
**Why:** polish-C.1 says "archive `security-audit.md` AFTER embedding the three deferred items into `m12-control-plane.md` and the M11 security hardening section." That embedding step needs to be its own item — otherwise C.1 stalls on it.
**Steps:**
1. Read `security-audit.md` deferred-items list.
2. For each item, add a prereq line to the appropriate target spec (`m12-control-plane.md` for control-plane-related items, `m11-publication-benchmarks.md`'s security section for publication-related items).
3. Sequence completes BEFORE the `git mv` step in polish-C.1.
**Verification:** every deferred item from `security-audit.md` has a home in a live spec; polish-C.1's `git mv` then proceeds cleanly.

#### polish-C.6 — Boundary front-matter on `docs/concepts/`
**Status:** DONE · **Workstream:** C · **Confidence:** HIGH · **Blast:** Small · **Prereqs:** None (pair with polish-C.4) · **Note:** Audience banner prepended to all 7 files: README + the-loop + paired-sample-pobb + state-record + campaign-tree + optimizer-of-the-optimizer + (scoring-and-memory had no jargon hits beyond canonical anchor names).
**Files:** `docs/concepts/the-loop.md`, `docs/concepts/paired-sample-pobb.md`, `docs/concepts/scoring-and-memory.md`, plus any other `docs/concepts/*.md` containing dispatch / OSP / PoBB / L1-L3 / OptSearchPoint references.
**Why:** Audit found these files are linked from `docs/manual/` but contain developer-density jargon. Operators following a link land in unexplained internal vocabulary.
**Steps:** for each affected file, prepend a one-line front-matter banner immediately under the H1: `> **Audience:** Developer reference. Operators see [`../manual/`](../manual/) for usage docs.` Do not rewrite content — augment in place per [[feedback-dont-trim-unprompted]].
**Verification:** every `docs/concepts/*.md` containing the listed jargon patterns starts with the banner; `grep` patterns cross-referenced against banner presence.

#### polish-C.7 — Scrub `docs/operations/observability.md` jargon
**Status:** DONE · **Workstream:** C · **Confidence:** HIGH · **Blast:** Small · **Prereqs:** None (pair with polish-A.2) · **Note:** Table rows at lines 15-17 reworded to drop `L1GenerateSurface` / `DispatchHub.fill_fixed` / `L2Surface` / `l1_generate_field_catalogue` / `TransitionResult`. `persistence-and-state.md`'s `OptSearchPoint` / `l1_layout` references kept — they document the on-disk artifact shape (round file format), not a jargon leak.
**Files:** `docs/operations/observability.md` (line ~16 + any other internal-vocabulary hits).
**Why:** This file lives in operator-facing `docs/operations/` but references `DispatchHub.fill_fixed()`, `L2Surface`, `l1_generate_field_catalogue` — internal implementation names that have no business in an ops doc.
**Steps:**
1. Rewrite the offending paragraph(s) using operator language ("the optimizer's prompt-rendering layer", "L2 framing-refinement output", "the L1 field catalogue used by the optimizer").
2. If the internal-name detail is useful to developers, move it into `docs/developer/dispatch-hub.md` instead.
**Verification:** `grep -rn "DispatchHub\|L2Surface\|fill_fixed\|fill_l1\|l1_generate_field_catalogue\|OptSearchPoint" docs/operations/` returns zero hits.

---

### Workstream D — Architecture / model simplifications (continuing inward arc)

Continues the architectural simplification arc the Tier 6 audit started.
Each item is one PR + standard CI gate. D.5 needs its own mini-spec
before any PR — high blast radius.

#### polish-D.1 — Type the `view_ingress` dict boundary
**Status:** DONE · **Workstream:** D · **Confidence:** HIGH · **Blast:** Medium · **Prereqs:** None · **Note:** `ViewContext` dataclass with 12 typed fields added to `view_models.py`; all builders + `from_phase_event` take `ViewContext` instead of `dict[str, Any]`; `RunCallbacks._phase_ctx` typed accordingly; wire boundary uses `asdict()`. `display.py` stays dict-based (reads JSON-deserialized wire). `.get()` count on `view_ingress.py` dropped 115→99; the remainder are `d.get(...)` reads against `PhaseEvent.data` (the genuine external boundary the spec noted). mypy strict + pytest + ruff format/check green.
**Files:** `promptpotter/presentation/views/view_ingress.py` (529L, 115 `.get()` ladder sites); secondary touch on `view_models.py`, `display.py`, `RunCallbacks` ingress.
**Why:** Biggest leverage on the "parameter soup" root cause already noted in this spec's Tier 3. The 115-`.get()` count is the symptom; the cause is untyped `dict[str, Any]` passed through the `PhaseEvent → View → wire_dict` pipeline.
**Steps:**
1. Define `ViewContext` dataclass in `view_models.py` with explicit fields: `round_num: int`, `cycle_id: str`, `timestamp: str`, `node_name: str | None`, `dataset_name: str`, `schema: PipelineSchema | None`.
2. Refactor `view_ingress.py` callers so the `ViewContext` is built once per `PhaseEvent` and passed explicitly into each view-constructor instead of unpacking via `.get()` ladders.
3. Replace as many of the 115 `.get()` sites as cleanly fit; remaining ones are at genuine external boundaries (LLM output dicts, ledger payloads) and stay.
**Verification:** `grep -c "\.get(" promptpotter/presentation/views/view_ingress.py` drops materially (target ~< 40). `mypy --strict promptpotter/` clean. `pytest -q` green.

#### polish-D.2 — Bundle `llm_call` context
**Status:** DONE · **Workstream:** D · **Confidence:** HIGH · **Blast:** Small · **Prereqs:** None · **Note:** `LLMCallContext(ledger, round_num, candidate_idx, cache)` frozen dataclass added to `call.py` + re-exported from `dispatch/llm_call/__init__.py`. `llm_call()` + `run_optimizer_node()` signatures collapsed: four loose kwargs → one `context` arg. Three call sites updated (`l1/generate.py`, `l1/critique.py`, `escalation/firing/executor.py`); `task_context.py` checkin call drops to the default `context=None`. mypy strict + 212 pytest + ruff format/check green.
**Files:** `promptpotter/application/optimization/dispatch/llm_call/call.py` (458L); call sites in `dispatch/hub/facade.py` + escalation firing.
**Why:** `llm_call()` takes 11 kwargs; `run_optimizer_node()` 15 params. Tier 3 of this spec already names this. Mirrors today's audit-2.B inversion in spirit.
**Steps:**
1. Define `LLMCallContext` dataclass: `ledger`, `round_num`, `candidate_idx`, `cache`, `node_name`, `task_context`. Place near `llm_call`.
2. Update `llm_call()` + `run_optimizer_node()` signatures to take `context: LLMCallContext` instead of the loose kwargs.
3. Update call sites (facade.py fill_l1 / fill_fixed paths, escalation drivers).
**Verification:** `llm_call()` signature down to `(llm, prompt, context, …)` plus genuinely-distinct prompt/template args. mypy strict + pytest green.

#### polish-D.3 — Extract `L2L3Memory` from `OptSearchPoint`
**Status:** DONE · **Workstream:** D · **Confidence:** HIGH · **Blast:** Medium · **Note:** Decided full ship after rejecting the memory-note PromptDecomposition heuristic (only 1 iteration site here vs. 15+ for prompt fields; 90 individual reads are mechanical). `L2L3Memory(BaseModel)` defined in `domain/opt_search_point.py` carrying all six L2/L3-authored fields + the `_coerce_task_context` validator. OSP shape: 8 prompt fields (PromptTemplate base) + `lineage` + `memory: L2L3Memory`. `copy_memory_to` collapsed to `target.memory = self.memory.model_copy(deep=True)`; `mutate()` preserves the propagation asymmetry (task_context + l1_overrides inherit; wounds/l1_layout/l1_supplemental_rules/l1_situational_examples reset to defaults). Mechanical sweep of 90 reads across 22 files; `Cycle.start` model_copy updated to nest under `memory`. On-disk migration script `scripts/migrate_to_l2l3_memory.py` walked 151 OSPs across 151 files in active campaigns — idempotent, dry-run flag. Developer docs (l2-internals.md, README.md, l1-generate-surface.md, self-healing-internals.md) + per-layer CLAUDE.md (promptpotter/CLAUDE.md, domain/CLAUDE.md) updated. mypy strict + 212 pytest + ruff format/check green.
**Files:** `promptpotter/domain/opt_search_point.py` (383L, 38 fields).
**Why:** OptSearchPoint inherits from PromptTemplate (8 fields) + adds lineage + wounds + l1_layout + l1_overrides + l1_supplemental_rules + l1_situational_examples + task_context = 38 fields. Mental model is muddied.
**Steps:**
1. Read [[architecture-opt-search-point-design]] memory — a prior PromptDecomposition extraction was rejected (15+ getattr sites). Verify this proposed cut is different (it bundles L2/L3-authored memory fields, not prompt-decomposition fields).
2. Define `L2L3Memory` dataclass bundling: `wounds`, `l1_layout`, `l1_overrides`, `l1_supplemental_rules`, `l1_situational_examples`, `task_context`.
3. OptSearchPoint becomes ~10 fields: PromptTemplate base + lineage + `memory: L2L3Memory`.
4. Update `copy_memory_to`, `mutate()`, serialization (pydantic round-trip), validator outputs that read wounds, and dispatch-hub injection renderers that read `b.opt_sp.wounds.X`.
**Verification:** Domain tests pass; serialization round-trip on a real campaign's `OptSearchPoint` matches pre-refactor; dispatch-hub renderer tests green; full pytest green.

#### polish-D.4 — Three-concern split of `live_dashboard/view.py`
**Status:** DONE · **Workstream:** D · **Confidence:** MEDIUM · **Blast:** Medium · **Prereqs:** Today's audit-1.C + audit-2.B (both shipped in `f412a92c`) — **prereqs met** · **Note:** Spec offered two options (three private inner classes OR namespaced method groups); took the surgical valid path. Extracted `_RoundBuffer` `@dataclass` (the cleanest extractable concern — the round-local candidate buffer feeding `dashboard.json::current_round.nodes.l1_score`) with the 5 round-state mutators (`slot`, `seed_candidate`, `append_sample`, `set_candidate_scores`, `update_p_best`). Added class-level docstring on `LiveDashboardView` naming all three concerns + their section dividers. Block builders + scalar mutations stay on the view (read across multiple state sources; pulling them into inner classes would introduce ~75 LOC of back-ref plumbing for marginal clarity gain — same wire shape either way). 775 → 814L (+39, within "line count not materially different"). mypy strict + 212 pytest + ruff format/check green.
**Files:** `promptpotter/infrastructure/projections/live_dashboard/view.py` (~775L after today's audit-1.C inlines).
**Why:** Post-inline, view.py still has 37 methods spanning state tracking + sample/candidate mutations + block builders. Three cohesive concerns visibly mixed.
**Steps:**
1. Within the same file (not a new module), section into three private inner classes or namespaced method groups: `_RoundTracking` (per-sample tally, per-candidate scores, current_accuracy / best_sp), `_RoundMutations` (the `_handle_*` event dispatch methods), `_RoundBuilders` (`_build_l1_score_block`, `_build_pobb_block`, `_fmt_sample_line`).
2. LiveDashboardView composes them as fields, delegating `on_record()` dispatch to the right inner.
3. Public API of LiveDashboardView is unchanged (constructor + `for_session` + `mark_stopped` + `log_fork` + base-class ledger subscription).
**Verification:** view.py line count not materially different (this is a clarity refactor, not LOC reduction). mypy strict + pytest green. Manual smoke at `/ui/` over a quick campaign — `dashboard.json` shape unchanged.

#### polish-D.5 — Extract `CycleRunConfig` from `cycle.py` (mini-spec first)
**Status:** RETRACTED (2026-05-25) — `Cycle` carries 14 fields, not 18 (`CycleRoundState` was already extracted as part of the post-compaction shape). With the trajectory already grouped, the remaining flat fields are single-purpose slots that don't suffer from confusion. Extracting `CycleRunConfig` would force `.run_config.session` / `.round_state.opt_sp` indirection across 121 `cycle.*` access sites in 20 files for marginal mental-model gain — crosses the "no abstractions beyond what the task requires" line. The genuine local complexity in this file (200+ LOC `Cycle.start()` factory) was the right cleanup target and was lifted into private helpers as audit-3.A.
**Files:** `promptpotter/application/optimization/cycle.py` (~404L, 18 fields) + the runner, resume path, fork bootstrap, intelligence-layer init.
**Why:** `Cycle` carries 18 fields bridging three concerns: (a) immutable run config (session, config, axes), (b) round-mutable state (tracking, opt_sp, rounds), (c) cache (archive_observations, last_rasch_posterior, pending_decisions).
**Steps when unblocked:**
1. Write `docs/specs/cycle-state-split.md` mini-spec covering the proposed cut.
2. Extract `CycleRunConfig` (session, config, axes, escalation, state_version). Rename `CycleRoundState` → `CycleRoundState`. Cycle composes the two + `archive_cache`.
3. Update runner, resume path, fork minting, intelligence-layer init — anything that reads `cycle.*` directly.
**Verification (when run):** mypy strict + pytest green; smoke pass through fresh `new` + 3 rounds + `resume` + `--fork-on-divergence`.

#### polish-D.6 — Typed `task_context` schema
**Status:** DONE · **Workstream:** D · **Confidence:** HIGH · **Blast:** Small · **Note:** Collapsed to docs-only — `task_context` is *already* typed via the `TaskDecomposition` dataclass (`domain/search_point.py`, 8 explicit string fields + `FIELDS` ClassVar + typed `merge` / `from_dict` / `to_dict`). The "dict-like at the L2 output boundary" is intentional and documented inline on `L2ContextOutput`'s task_context field (`dispatch/schemas.py:231-237`) — `TaskDecomposition.merge` owns the key set and merges in. The remaining gap was per-field documentation, which I added directly to the dataclass docstring (operator/LLM-facing semantics for each of the 8 fields, plus `_field_value` splice behaviour for upstream/downstream_context). No Pydantic conversion — the dataclass is doing its job; converting just for `Field(description=)` would be over-engineering.
**Files:** Wherever `task_context: dict[str, Any]` is consumed — at minimum `domain/`, `dispatch/hub/injections/layer_state.py::_r_task_context`, L2 output parsing.
**Why:** `task_context` is dict-like at the L2 output boundary; key contract is implicit. Typed sub-schema would harden it.
**Steps:**
1. Create `TaskContextSchema(BaseModel)` with explicit fields + per-field docstrings: `key_challenges`, `raw_description`, `upstream_context`, `downstream_context`, plus whatever L2 may currently write.
2. Replace `task_context.to_dict()` / `from_dict` with Pydantic round-tripping.
3. Update L2 output parser to validate into the schema.
**Verification:** Removes `.get()` calls in render paths. mypy strict + pytest green. Existing L2 outputs from on-disk round files round-trip cleanly.

#### polish-D.7 — `*Context` / `*State` naming arc
**Status:** DONE · **Workstream:** D · **Confidence:** HIGH · **Blast:** Small (cosmetic, no logic change) · **Note:** All six suggested mappings applied repo-wide via mechanical regex sweep: `ScoringContext → ScorerSetup`, `CycleState → CycleSnapshot`, `CheckContext → ValidatorContext`, `_LoopContext → QueryLoopState`, `TrackingState → CycleRoundState`, `EscalationState → EscalationFSM`. 25 files touched (source + tests + docs). `TenantContext` + `ReplayContext` kept as-is — already precise. mypy strict + ruff format/check + 212 pytest green.
**Files:** Repo-wide rename: `TenantContext`, `ScorerSetup`, `ValidatorContext`, `QueryLoopState`, `ReplayContext`, `CycleSnapshot`, `CycleRoundState`, `EscalationFSM`.
**Why:** Uniform suffixes mask distinct concepts. Rename to semantic roles.
**Suggested mappings:** `ScoringContext → ScorerSetup`, `CycleState → CycleSnapshot`, `CheckContext → ValidatorContext`, `_LoopContext → QueryLoopState`, `TrackingState → CycleRoundState`, `EscalationState → EscalationFSM`. `TenantContext` and `ReplayContext` may already be precise; keep if so.
**Steps:**
1. For each rename, run repo-wide grep → confirm rename target is clear → execute (ruff-aware sed or IDE rename).
2. Update imports.
3. mypy strict + ruff format + pytest green per rename.
**Verification:** No collisions; each new name describes the concept without needing to open the file.

---

### Workstream E — First-run smoothness

A fresh-clone operator should reach their first campaign without
manual sidecar steps. Today's path requires cloning TermNorm
separately, running a `.bat` in their own terminal, then coming back
to PromptPotter — three rough edges in the first ten minutes.

#### polish-E.1 — TermNorm reachability check at CLI startup
**Status:** DONE · **Workstream:** E · **Confidence:** HIGH · **Blast:** Small · **Prereqs:** None · **Note:** `check_status()` already runs preflight in `new.py` and `resume.py`; just enriched the unreachable message to name the `.bat` (`TermNorm-excel\backend-api\start-server-py-LLMs.bat`) and link the install guide. Auto-spawn deferred to when Claude Code can pop terminals.
**Files:** CLI entry point (likely `promptpotter/__main__.py` or `promptpotter/presentation/cli/session.py::init_services_cli`); `.claude/skills/potter-run/SKILL.md`.
**Why:** Fresh-clone failure mode today: `python -m promptpotter new bbeh` → "Backend `/status` unreachable" → operator must clone TermNorm sibling, run `start-server-py-LLMs.bat`, come back. Three steps without guidance.
**Steps:**
1. Before any campaign verb that requires the backend, probe TermNorm's `/status` endpoint (URL is in dataset config or env).
2. On failure, print a single, actionable message naming the `.bat` to launch + the expected URL + a link to the install guide. Don't crash — let the operator launch it and re-run.
3. When Claude Code can pop terminals (per memory [[project-potter-run-terminal-spawn]]), upgrade `polish-E.1` to auto-spawn. Until then, the check is the win.
**Verification:** Stop TermNorm; run `python -m promptpotter new <dataset>`; see a single clean actionable message. Restart TermNorm; same command succeeds.

#### polish-E.2 — `.env` first-run prompt
**Status:** DONE · **Workstream:** E · **Confidence:** HIGH · **Blast:** Small · **Prereqs:** None · **Note:** `_ensure_api_key()` runs before `new`/`resume`/`sweep`. If no `.env` and no provider env var set, prompts for a Groq key and writes `.env` to CWD; skipping is graceful.
**Files:** CLI entry + a new `.env.example` if not present at repo root.
**Why:** Today's first run fails with "no API key set" if `.env` is missing. Three options: (a) ship a `.env.example` the CLI detects, (b) prompt for the key interactively on first run, (c) both.
**Steps:** Recommend (b) — prompt path. On `python -m promptpotter new <dataset>`, if no `.env` and no `$GROQ_API_KEY` (or other provider) is set, prompt: "No API key found. Paste your Groq key (or set $GROQ_API_KEY): ". Write to `.env` if pasted.
**Verification:** Fresh clone with no `.env` → `python -m promptpotter new <dataset>` → operator sees a one-line prompt, pastes a key, campaign starts.

#### polish-E.3 — Root `README.md` quality pass
**Status:** DONE (`5b78b154`) · **Workstream:** E · **Confidence:** HIGH · **Blast:** Small · **Prereqs:** None (could come early since touches public-first impression)
**Files:** `README.md` at repo root.
**Why:** Audit found the existing README is good in tone, but doesn't mention TermNorm backend setup — a critical missing piece for fresh-clone operators.
**Steps:**
1. Read current README.
2. Add a "System requirements" section listing: Python 3.13+, `.env` with one of the supported providers, TermNorm backend (link to its repo or install instructions), VS Code / Claude Code recommended.
3. Add a "First run" subsection with the three-command path: `pip install -e ".[all]"` → `python -m promptpotter new <dataset>` → open `http://localhost:8001/ui/`.
4. Preserve all existing positioning / tone (audit said it's "excellent" — don't rewrite, augment).
**Verification:** A reader who's never seen the project can clone + install + run a first campaign by following only the README.

#### polish-E.4 — `python -m promptpotter` no-args friendly landing
**Status:** DONE · **Workstream:** E · **Confidence:** HIGH · **Blast:** Small · **Prereqs:** polish-A.1 (CLI scrub) recommended first · **Note:** Bare invocation already auto-defaults to `resume` (`campaign_runner:main` line 52). Added first-run guard: when there's no `active_session.json`, print a friendly five-verb landing card and exit cleanly rather than letting `resume` fail.
**Files:** CLI entry (`__main__.py` or top-level parser dispatch).
**Why:** Today bare `python -m promptpotter` likely errors with "no verb given". Public visitor's first move; should be a friendly help.
**Steps:**
1. If no verb is passed, print a short landing message: "Welcome to PromptPotter. Pick a verb: `new`, `resume`, `verify`, `sweep`, `compare`. Run `python -m promptpotter new --help` to start." Exit 0.
**Verification:** `python -m promptpotter` exits cleanly with the message. CI green.

#### polish-E.5 — Root `SECURITY.md` + `CODE_OF_CONDUCT.md` stubs
**Status:** RETRACTED (2026-05-25) — operator dropped both files + their README links during the polish-arc squash. Solo-dev / pre-public-distribution moment doesn't need release-grade governance stubs yet; revisit when going truly public.

#### polish-E.6 — `[project.scripts]` console entry-point in `pyproject.toml`
**Status:** DONE · **Workstream:** E · **Confidence:** HIGH · **Blast:** Small · **Prereqs:** polish-E.4 (the no-args landing message is what the bare command shows first) · **Note:** Added `[project.scripts]` block pointing `promptpotter = "promptpotter.presentation.cli.campaign_runner:main"` — bare `promptpotter` works after `pip install`.
**Files:** `pyproject.toml`, possibly `promptpotter/__main__.py` (to expose a `main()` callable if not already).
**Why:** Audit found `pyproject.toml` lacks `[project.scripts]`. After `pip install promptpotter`, the bare `promptpotter` command doesn't work — users must use `python -m promptpotter`. Public-release ergonomics gap.
**Steps:**
1. Read `promptpotter/__main__.py`. If a callable `main()` exists, use it; otherwise wrap the existing module-level dispatch in a `def main() -> None:` and call `main()` from the `if __name__ == "__main__":` guard.
2. Add `[project.scripts]` block to `pyproject.toml`: `promptpotter = "promptpotter.__main__:main"`.
3. Smoke-test in a clean venv: `pip install -e .` → `promptpotter` (no args) prints the polish-E.4 landing → `promptpotter new --help` works.
**Verification:** bare `promptpotter --help` succeeds in a fresh venv after `pip install -e .`. Pre-existing `python -m promptpotter` still works identically.

#### polish-E.7 — Remove hardcoded developer path from `CLAUDE.md`
**Status:** DONE (`cb94af98`) · **Workstream:** E · **Confidence:** HIGH · **Blast:** Small · **Prereqs:** None
**Files:** `CLAUDE.md` Known Issues section.
**Why:** Known Issues section references `C:\Users\dsacc\OfficeAddinApps\TermNorm-excel\backend-api` — not a public artifact.
**Steps:** rewrite the line to "TermNorm backend lives in a sibling repo; clone alongside PromptPotter." If TermNorm has a public GitHub URL, link it instead of the absolute path.
**Verification:** `grep -rn "C:\\\\Users\\\\dsacc\\|OfficeAddinApps" CLAUDE.md` returns zero hits.

---

### Strategic sequencing

Priority passes (an agent picks the next TODO from the top of Pass 1
first, then Pass 2, then Pass 3):

**Pass 1 — Visible polish (highest first-impression leverage):**
1. polish-A.1 (M-milestone scrub)
2. polish-A.3 (future-tense scrub) — pair with A.1
3. polish-A.5 (ChatPane in-JSX scaffolding) — pair with A.1/A.3
4. polish-A.6 (`measurements.py` docstring) — pair with A.1
5. polish-B.1 (empty-state authoring)
6. polish-B.2 (hide non-functional controls)
7. polish-B.5 (ChatPane disabled-affordance purge) — pair with B.2
8. polish-C.5 (embed security deferred items) — prereq for C.1's security-audit move
9. polish-C.1 (archive shipped specs)
10. polish-E.3 (README quality pass)
11. polish-E.7 (`CLAUDE.md` hardcoded path removal) — trivial; do early

**Pass 2 — Coherence + first-run smoothness:**
13. polish-A.2 (jargon translation)
14. polish-B.4 (`terms.ts` copy audit) — runs naturally after A.2
15. polish-B.3 (hardcoded config-menu removal)
16. polish-B.6 (ConfigMenu footer placeholder) — moot if B.3 wholesale
17. polish-C.2 (reclassify aspirational specs)
18. polish-C.3 (public roadmap front door)
19. polish-C.4 (specs index refresh)
20. polish-C.6 (concepts boundary front-matter) — pair with C.4
21. polish-C.7 (`observability.md` jargon scrub) — pair with A.2
22. polish-E.2 (`.env` first-run prompt)
23. polish-E.4 (no-args friendly help)
24. polish-E.6 (`[project.scripts]` console entry) — pair with E.4
25. polish-A.4 (operator-visible TODO sweep)
26. polish-E.1 (TermNorm reachability check)
27. audit-1.E (`LiveStateProjection` row removal) — trivial doc edit

**Pass 3 — Architecture iteration (inward):**
28. polish-D.1 (`view_ingress` typing)
29. polish-D.2 (`LLMCallContext` bundle)
30. polish-D.4 (`view.py` three-concern split — prereqs met)
31. polish-D.3 (`L2L3Memory` extraction)
32. polish-D.6 (`task_context` typed schema) — pairs with D.3
33. polish-D.7 (`*Context` / `*State` naming arc) — cosmetic clean-up at the end

**Held (need evidence or mini-spec):**
- audit-2.D (`_apply_budget` shed-rate — instrument first, carry from Tier 6 above)
- audit-2.E (PoBB separability gate — verify lines still exist, carry from Tier 6 above)

**Retracted on inspection:**
- polish-D.5 (`CycleRunConfig` extraction) — see RETRACTED note on the item above; abstraction over `Cycle`'s 14 single-purpose fields would force `.run_config.X` / `.round_state.Y` indirection at 121 access sites for marginal mental-model gain. The local complexity it was meant to address (the 200+ LOC `Cycle.start()` factory) shipped under audit-3.A instead.

### Pre-public-release end-of-arc gate (7 checks for the 98% bar)

When the full arc (Pass 1 → 2 → 3) has landed, a final integration check:

1. **Visual webapp smoke at `http://localhost:8001/ui/`:** no visible disabled controls, no M-milestone copy in any tooltip, no "PoBB" / "OptSearchPoint" / "L1/L2/L3" leaking into tooltips or labels, no "WIP" / "phase N" / "not yet wired" anywhere a hover lands.
2. **Fresh-clone first-run timing:** `git clone …; cd …; pip install -e ".[all]"; python -m promptpotter new <small-dataset>` → first campaign running in under 10 minutes including TermNorm + `.env` setup, with no Stack Overflow detour.
3. **Spec tree audit:** `ls docs/specs/` reads as polished roadmap + reference; archived items are in `archive/`; `docs/roadmap.md` exists at the public-facing layer.
4. **CI gates:** `ruff check . && ruff format --check . && mypy promptpotter/ && pytest -q` green; `cd webapp && npm run lint && npx tsc --noEmit && npm run build` green.
5. **Bare `pip install` smoke:** fresh venv → `pip install -e ".[all]"` → `promptpotter --help` (no `python -m`) prints help. Confirms polish-E.6 landed.
6. **Final-grep zero-set.** All of the following return zero hits across operator-visible paths (`webapp/`, `promptpotter/presentation/`, `docs/manual/`, `docs/operations/`, `README.md`):
   - Milestones: `M1[0-3]`, `Slice [A-Z]`
   - Internal jargon: `PoBB`, `OptSearchPoint`, `OSP`, `DispatchHub`, `l1_generate`, `l2_context`, `l3_plan`
   - Scaffolding language: `coming soon`, `not yet`, `will replace`, `final feature shape`, `hardcoded until`, `WIP`, `TBD`
   - Developer paths: `C:\\Users\\dsacc`, `OfficeAddinApps`

All 6 green = codebase is at the 98% public-release bar.

### Done log — polish arc

Format: `<commit-hash>` · `polish-X.Y` · one-line summary.

**Pass 3 — Architecture iteration (in progress):**
- `41b846da` · polish-D.1 + D.2 + D.4 (squashed) · ViewContext dataclass for view_ingress boundary + LLMCallContext bundle for `llm_call` (four kwargs → one context arg) + `live_dashboard/view.py` three-concern visibility (extract `_RoundBuffer` @dataclass + class-level concerns docstring).
- `415f8ba2` · polish-D.3 · extract `L2L3Memory` from `OptSearchPoint` (6 L2/L3-authored fields bundled under `memory`; 90 read sites swept across 22 files; on-disk migration script for 151 OSPs in active campaigns; developer docs + CLAUDE.md updated).
- `06342a3a` · polish-D.6 + D.7 · per-field docstrings added to `TaskDecomposition` (D.6 collapsed to docs-only per resume plan) + six `*Context`/`*State` renames via repo-wide mechanical sweep (D.7).

**Pass 2 — Coherence + first-run smoothness (complete, squashed into `6e3cfd87`):**
- A.2 · jargon translation across terms.ts + workflow/layout.ts + parsers.py.
- A.4 · operator-visible TODO sweep (no-op; prior compaction cleared it).
- B.3 · ConfigMenu wholesale removal + ChatPane simplification.
- B.4 · terms.ts copy audit pass (future-tense + jargon leftovers).
- B.6 · DONE-by-prereq (collapsed into B.3).
- C.2 · status banners on m11-spend-tracking + m12-control-plane + m12-multi-connector.
- C.3 · docs/roadmap.md public front door + root README link.
- C.4 · docs/specs/CLAUDE.md index refresh (Live / Forward / Reference / Archive).
- C.6 · audience banner prepended to all 7 docs/concepts/*.md files.
- C.7 · observability.md jargon scrub (DispatchHub / L2Surface / fill_fixed dropped).
- E.1 · enriched TermNorm-unreachable message with .bat path + install-guide link.
- E.2 · `.env` first-run prompt via promptpotter/config/env_bootstrap.py.
- E.4 · friendly five-verb landing card on bare `python -m promptpotter` with no active session.
- E.6 · `[project.scripts]` console entry point in pyproject.toml.
- audit-1.E · stale LiveStateProjection row removed from infrastructure/CLAUDE.md.

**Pass 1 — Visible polish (complete):**
- `c7794a11` · polish-A.1 · scrub M-milestone refs from operator surfaces (webapp, ops docs, README, manual, API docstring).
- `54e1d9bf` · polish-A.3 · drop scaffolding `placeholder` TERMS entry; tighten `brand_live_preview`.
- `1d58c19a` · polish-A.5 · scrub ChatPane + ConfigMenu in-JSX scaffolding; remove `job-footer` M12 affordance.
- `1c3cc131` · polish-A.6 · scrub M13 ref from `measurements.py` docstring.
- `a5801a13` · polish-B.1 · proper onboarding empty-states (Sidebar card, CyclePicker label, FileTree hint).
- `763c29eb` · polish-B.2 · remove disabled search input from Topbar; audit tabs (all wired).
- `4cbeee54` · polish-B.5 · purge ChatPane fake-chat + locked-toggle mockup (-78 LOC).
- `7824fd32` · polish-C.5 · embed security-audit deferred items into m11 + m12 specs.
- `dd22307b` · polish-C.1 · archive `hard-sample-sorter.md` + `security-audit.md`; add Phase 2/3 row to m12-plus-backlog.
- `5b78b154` · polish-E.3 · README quality pass — first-run + system requirements.
- `cb94af98` · polish-E.7 · scrub hardcoded developer path from `CLAUDE.md` tree.

---

## Out of scope

Not a correctness fix · not an architecture change · not a single milestone — each tier is independent.
