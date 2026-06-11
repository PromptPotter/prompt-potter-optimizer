# Code-Debt Cleanup — Backlog

**Status:** Reference — perpetual living backlog; `git log` is the history layer, this file holds only open debt. Active: TermNorm wire `model` (cross-repo) + the ingest/origin delta-key rename (items 5+7, live-gated) + a handful of standing entries. The M13+ intentional-UI-placeholder registry is permanent reference.

**Scope is literal: code debt only.** Dead code, redundant guards,
single-caller indirections, premature optimizations that no longer
earn their keep, vibe-coded scaffolding. The default action on every
entry is **delete** (or inline, or strip) — verify-first when the
evidence isn't on disk.

**Not debt — goes elsewhere:**
- Forward-looking webapp perf / feature work → [`roadmap.md` § Webapp Perf](roadmap.md)
- New milestones / specs → `docs/specs/`, indexed at [`CLAUDE.md`](CLAUDE.md)
- Architectural decisions → `docs/architecture.md`

This file is the dump location for new debt as it's found. Add a bullet under **Active backlog** with enough detail that a future session can pick it up cold:
- file + line range (or symbol)
- one sentence on *why* it's debt (not "what" — the code shows what)
- proposed action (delete / inline / extract / replace / verify)
- any blockers (needs telemetry, needs a mini-spec, depends on another item)

When an item ships, delete it from the file. The file is the live
backlog, not a history log — `git log` is the history layer.

## Active backlog

Lens: **vibe-coded remainder** — LLM-autopilot residue from
AI-assisted iteration the recent polish arc didn't catch. High
confidence after verification (call sites traced + bodies read), not
"I spotted a code smell." See § Audit guidance below for the
patterns.

### This week (execution slate)

0. **Campaign-from-origin Phase 2 — additive consumer layer only (seam refactor SHIPPED).** The seam unification landed: the origin *seed* (`CycleSeed`, formerly `OperatorForkOverride`) now threads through the fresh root mint (`jobs/mint.py::{resolve_cycle_plan,prepare_fresh_cycle}` accept `origin_override` → write `.overrides/seed.json` with `origin_source="campaign_origin"`; `mint_campaign_command` + the `mint-campaign` dispatcher + the `MintCampaignPayload` openapi schema all carry it). C0 lineage is data-driven (`origin.py::_SEED_ORIGIN_LINEAGE`). So `POST /commands/mint-campaign {origin_override}` already starts a fresh campaign from a chosen prior origin. **Remaining (additive, bundle — picker is live-round-gated):** a `GET /origins` derived read over `list_campaigns()` (dedup by `Campaign.root_content_hash`; 3-hop to `session_state.origin_prompt_fields` for the override payload) + the New Campaign / `IngestPane` origin picker that POSTs it. Optional: `origin_override` on `datasets/ingest.py::draft_from_dataset`.

1. **TermNorm wire `model`** — cross-repo edit at `C:\Users\dsacc\OfficeAddinApps\TermNorm-excel\backend-api`. With the connector revision-pin landed (`promptpotter/connectors/protocol.py::Connector.{expected_revision, version_check}`), the TermNorm-side PR adds `model` to the per-request response + a `/version` endpoint; this repo bumps `termnorm.py::_EXPECTED_REVISION` to the new SHA and deletes the `_synth_legacy_backend_record` back-fill in `presentation/api/routers/auth.py`.

### Ingest/origin model-alignment slate (2026-06-06)

Aligning optimizer/origin code to the §0 model — one shape per concept, fewer lines, AI-legible names. Items 1–4 + 6 shipped (git log). The two below stay, both gated on a live L1 round:

5. **L1 delta keys `*_override` → updates (on-disk contract — do last, live-verify)** — `prompt_fields_override` / `task_context_override` / `pp_override`(+`pipeline_params_override`) are merges, not replacements, but the name says "override." For the pipeline-delta pair use the glossary word **`pipeline_overlay`** (not a coined `pp_updates`); see the Decision below. Land together with item 7. **Blocker:** one live L1 round (operator-gated). Full execute-ready map below.

7. **Webapp duplicate searchpoint projection** (do *with* item 5, same commit) — `searchPoint.ts::liveInFlightSearchPoint` and `candidateSearchPoint.ts` both map wire `prompt_fields`/`pp_override` → `{origin_prompt_fields, pipeline_overlay}`. **Action:** one `wireToCandidateSearchPoint(wire)` helper both call — collapses item 5's reader change to a single site. **Blocker:** none on its own, but bundle with 5 so the reader key rename lands once.

#### Execution scope for 5 + 7 (next session — rip-to-green in one pass)

These are one arc: item 5's wire-key rename is exactly what item 7's helper consolidates, so do them in **one commit**. Verified site map (greps traced 2026-06-06).

**Rename table** — `*_override → *_updates`, only the three L1-delta keys (NOT `LimitOverrides`, fork/campaign/`resume_from_round_override`, or `forbidden_axes` — those are genuine replacements, leave them):

| Old | New | Surfaces |
|---|---|---|
| `prompt_fields_override` | `prompt_fields_updates` | L1Variant schema field + parser + meta-prompt prose |
| `task_context_override` | `task_context_updates` | L1Variant schema field + parser + meta-prompt prose |
| `pipeline_params_override` | `pipeline_params_updates` | L1Variant schema field + `round_NNNN.json::candidate_scores[]` key + validators + CLI display + verify |
| `pp_override` | `pp_updates` | the SHORT dashboard/wire alias: `dashboard.json` candidate entry + CLI display + **webapp readers** |

**Decision to settle first (one axis):** `pp_override` (dashboard wire) and `pipeline_params_override` (round-file + schema) are two names for one delta. Either (a) rename both to the `*_updates` pair above (keeps the short/long split), or (b) **unify to one `pipeline_overlay`** everywhere (glossary's sanctioned word; kills the two-name tax — preferred, matches the arc's "one shape per concept"). Pick before starting; the table assumes (a), option (b) drops `pipeline_params_updates`/`pp_updates` in favor of `pipeline_overlay` at every site. (The prompt/context deltas stay `*_updates` either way — only the pipeline delta has a glossary word.)

**Python sites (writer + reader together — no shims):**
- Schema (source of truth): `dispatch/schemas.py::L1Variant` (3 fields + their docstrings) → JSON schema the LLM sees is built from these field names by `validators/l1_strict.py::build_l1_output_schema` (reads `variant_props["pipeline_params_override"]`), so the rename auto-propagates to the LLM contract.
- Parser: `l1/generate.py` (reads the variant dict keys).
- Population/score: `l1/population.py`, `l1/score/{loop,candidate,winner}.py`, `l1/stats.py`, `domain/results.py` (`ScoredCandidate` field), `validators/l1_behavior.py` (`_touches_param_scope`/`_touched_forbidden_keys`).
- Writers (dashboard/round file): `run_observers.py::seed_candidate` (param `pp_override` + `"pp_override"` key), `infrastructure/projections/live_dashboard/view.py` (`"pp_override"` at the candidate entry, l. ~207/599/929).
- CLI display: `presentation/views/live/{display,candidate,__init__}.py` (`fmt_pp_override` + param + `scores.get("pipeline_params_override")`).
- Verify path: `presentation/cli/commands/verify.py` (`proposal.get("pipeline_params_override")`), `cli/commands/sweep/_common.py`, `application/review.py`.
- Meta-prompt prose: `datasets/_optimizer/variants/l1_current.json` (active `l1_generate` text names the keys) + `dispatch/hub/injections/catalogues.py:24` (docstring). **Decision:** also rename the historical snapshots (`l1_v2..v6.json`, `l1_60pct_winner.json`) for grep-cleanliness, or leave them as archival meta-campaign history — they're inactive; recommend renaming so a future grep for `*_override` is clean.
- Docs: `docs/developer/self-healing-internals.md`, `l1-candidate-analysis-checklist.md` reference the keys.

**Webapp sites (item 7 — the readers intentionally left on old keys):**
- `lib/poll.tsx::LiveInputCandidate` (the `pp_override?` field), `lib/derivations/searchPoint.ts::liveInFlightSearchPoint` (`latest.pp_override`), `lib/derivations/candidateSearchPoint.ts` (`entry.pp_override`, reads `round_NNNN.json::candidate_scores[]`) + their `__tests__/`.
- **Item 7 collapse:** extract one `wireToCandidateSearchPoint(wire)` in `lib/derivations/` that both `searchPoint.ts` and `candidateSearchPoint.ts` call — maps `{prompt_fields, pp_updates}` → `{origin_prompt_fields, pipeline_overlay}`. The reader key rename then lands at exactly one site. `prompt_fields` (the candidate's full evolved prompt) is NOT renamed — only the delta key is.

**Order:** (1) settle the two decisions above; (2) Python rename writer→reader (schema first, then parser/validators/writers/display/verify/meta-prompt) + run the Python gate; (3) webapp: build the `wireToCandidateSearchPoint` helper, rename the reader key, point both projections at it + run the webapp gate; (4) **live-verify** (below); (5) one commit (`refactor(l1): override→updates delta keys + collapse webapp searchpoint projection`).

**Live-verify protocol (the actual blocker):** this changes the optimizer LLM's structured-output contract and the `round_NNNN.json` key, so it invalidates on-disk cycles — old round files won't read in the webapp. Verify against a FRESH cycle, not a resumed one: `python -m promptpotter new <small dataset>`, let **round 1 (one L1 round) complete**, then confirm (a) the round parsed — no `l1_zero_candidates` `RoundWarningRecord`, variants populated in `round_0001.json::candidate_scores[]` under the new keys; (b) the dashboard candidate cards render the pipeline delta (webapp reads `pp_updates`); (c) the steer panel seeds from a candidate (exercises `candidateSearchPoint`). Then land.

**Gates:** Python — `ruff check`/`ruff format --check`/`mypy`/`pytest` (`invariants.py`, `numerics.py`, `contracts.py` touch these keys). Webapp — `npm run lint`/`tsc --noEmit`/`npm run test` (`searchPoint.test.ts`, `candidateSearchPoint.test.ts`)/`npm run build`.

### Operator-steered-fork drift (v0.8.1 — found 2026-06-03)

Knots 1–4 shipped in the v0.8.1 panel-fix arc (git log). Remaining:

1. **Reconcile defaults snapshot `dash` at mount while the parent keeps polling.** `forkReconcileDefaults`/`LimitReconcile` freeze spend/round "remaining" via `useState(() => …)`; a long edit session shows mount-time remaining, not current. *Why debt:* latent staleness seam — intentional (avoids clobbering the operator's typed values) but undocumented, so a future reader may "fix" it into a clobber. **Action:** one-line comment affirming the snapshot is deliberate, or recompute-on-reopen. **Blocker:** none.

### Untracked-debt sweep (2026-06-11) — remaining after rollover

Five-lens verification audit. Tiers 1–2 dead-code/hidden-default deletes + all Tier-4 doc-drift fixes **shipped** 2026-06-11 (full Python gate green; see `git log`). What stayed — held because verification showed it isn't a clean delete:

**Tier 1 holds — verification reclassified these as NOT standalone-dead.**
- `application/origin.py::DatasetSummary.splits` + `domain/search_point.py::TaskDecomposition.FIELDS` — both are read only by the rotted HITL notebook (`notebooks/optimization_campaign.ipynb` l.118-120 / l.185). Their removal rides the **HITL-notebook rewrite/retire decision** (Standing entries below), not a standalone delete — dropping them now just deepens the notebook break.
- **`__all__`-only export hygiene** — cosmetic public-surface trim, near-zero functional value, and several entries (`ProviderSpec`, `VersionCheck`/`PreflightFn`, the `try_parse_json` re-export in `infrastructure/llm/__init__.py`) are *documented* protocol/registry surface that legitimately belongs in `__all__`. The one with teeth: `domain/scoring.py:67` `EMPTY_SCORER_ID` is fully unreferenced (zero refs incl. internal) — a delete-vs-adopt-the-constant call ("none" literals are used directly). Dedicated cosmetic pass, not a bulk sweep. (Verified NOT debt, dropped from the list: `FewShotExample.explanation` — optional LLM-facing schema field that round-trips via `FewShotExample(**ex)`.)

**Tier 3 — webapp client-side scoring recompute (R-36), all Lane C8 served-projection write-side.**
- `webapp/components/whatif/fitness-bars.ts:51-88` (`accuracyOverSampleSet`, `correctedFromEvaluators`) + `whatif/FitnessRankSummary.tsx:5-23` (`ranks()`/`pickWinner()`) — recompute what-if fitness, fixed-sample-set accuracy, and alternative ordering/winner-flip in TS. The **deferred mask WRITE-SIDE** (read-side shipped 2026-06-10; `lib/lineage-overlay.tsx` already proves the served-projection pattern R-36-clean).
- `webapp/lib/derivations/round-candidates.ts:82` `computeAccuracyFromSamples(c.samples)` — NOT a clean delete (initial read was wrong). Root cause: `live_dashboard/render.py:113` serves all-or-nothing `scores.get("accuracy")`, which is **null until a candidate fully scores** (`view.py:410` "pending node, null accuracy"), so the TS recompute fills *partial* mid-scoring accuracy the projection doesn't serve. **Root-fix (R-08):** have the projection serve partial accuracy over scored-so-far samples, then delete the TS recompute. Belongs with the Lane C8 served-projection work above. **Blocker:** Lane C8 write-side scope.

**Tier 5 — medium, needs an operator call before action.**
- `connectors/protocol.py:158` `Connector.to_dict()` — zero call sites in-repo; **verify** no external/operator script uses it before deleting.
- `domain/pipeline_parsing.py:261` writes `"display_tag"` into `step_kwargs`, but `PipelineNode` has no such field → silently dropped on every parse of an LLM-generated pipeline (runtime tags come from `_build_display_tags`). On-disk datasets DO ship the key and the contract doc lists it optional, so the *write* is inert but the key isn't dead. **Drop the parse-path write; leave doc/datasets.**
- `application/intelligence/indexes/config.py` `ConfigIndex` — `ingest_run` populates `_configs_to_runs`/`_configs_to_node_configs` but exposes no query method and `measurements_for_config` does its own O(N) scan. Documented as a first-class derived view (glossary + developer README), so likely a half-built skip-scan, not oversight. **Confirm intent;** if abandoned, drop the class + `AxisIndex.config_index`.
- `application/optimization/validators/l1_behavior.py:78` `ValidatorContext.param_unlock_round` default 3 never overridden — possibly an intended future tunable. **Confirm before collapsing to a constant.**
- `application/scoring/evaluators.py:44` `compute_accuracy` ↔ `metrics.py:59` `_compute_accuracy` — the deprecated-filter + mean-fitness accuracy line is byte-identical across the two seams (registry evaluator vs. the `{hits,total,accuracy,…}` bundle). Marginal; have `_compute_accuracy` reuse `compute_accuracy` for the scalar if touched.

### Considered, not debt (don't re-open)

- **`RunCallbacks` ↔ `emit_*`** — two writer APIs, but `RunCallbacks._phase_ctx: ViewContext` is owned write-then-read cross-event state; folding it into an ambient ContextVar is a downgrade. The "which do I use" rule is in [`developer/adding-a-surface.md`](../developer/adding-a-surface.md) §1.
- **`from_disk_round` / `from_disk_log`** — looks like a roundtrip shim, but it's a genuine separate source: foreign fork-sibling + historical cycles have no live ledger, so on-disk `round_NNNN.json` is the only source. `test_round_complete_view_roundtrip` keeps both factories honest against one View.

### Standing entries

- **Optimizer model unreliable on heavy l2/l3 structured output (operator's model call)** —
  the L3-plan-timeout *false-halt* is fixed (the wall budget now covers the
  schema-repair round-trip — `OPTIMIZER_CALL_DEADLINE_S` is per-round-trip,
  `_MAX_ROUND_TRIPS_PER_CALL` budgets initial+repair in
  `dispatch/llm_call/call.py`). The *deeper* root remains: `openrouter/gpt-oss-120b`
  (all optimizer nodes) is both slow (~150s on a heavy `l3_plan` prompt) and
  schema-noncompliant on the large `L3PlanOutput`/`L2*` shapes, so it routinely
  fires the ~2× repair retry and sometimes fails it (`l1_zero_candidates` /
  `OPTIMIZER_TIMEOUT`). *Why it's here, not a code fix:* swapping the optimizer
  model is a cost/quality decision and a per-node overlay edit
  (`datasets/_optimizer/pipeline.json::nodes.{l2_context,l3_plan}.config.model`),
  not a service-code change (R-13). **Action (needs operator pick):** evaluate a
  faster/more-schema-reliable model for `l2_context` + `l3_plan` specifically
  (they carry the largest schemas), or shrink the `L3PlanOutput` schema surface.
  **Blocker:** operator chooses the model; needs a live cycle that reaches L3 to
  measure repair-rate before/after.

- **HITL notebook (`notebooks/optimization_campaign.ipynb`) has rotted against
  the orchestration API** — not CI-gated, so it drifted unnoticed across the
  ingest/origin unify arc. Three confirmed breaks: (1) the `data-setup` cell
  unpacks **four** values from `prepare_origin_notebook`, which now returns a
  **3-tuple** (`RunObservers, list[Sample], CampaignOrigin`); (2) it imports
  `decompose_task_context` from `promptpotter.application.optimization.pipeline`
  — that module **no longer exists** (the seam is now
  `task_context.py::load_or_build_task_context` /
  `decompose_prompt_fields`); (3) downstream cells treat `campaign_rounds` as a
  list of round dicts, but the runner seam now hands back a `CampaignOrigin`.
  **Action:** rewrite the three cells against the current `notebook_run.py`
  contract (`prepare_origin_notebook` → `(observers, dataset, origin)`;
  `run_optimization_notebook(observers, dataset, origin, …)`) — or retire the
  notebook if the CLI/web paths have fully superseded the HITL flow (operator's
  call). **Blocker:** can't be verified without a live TermNorm backend at
  `:8000`; treat as a dedicated notebook session, not a blind edit. **Pattern:**
  un-gated surface drifting behind a renamed orchestration seam.

- **`RunPhase.STOPPING` has a thin window for non-paused stops** —
  the runner declares `stopping` (`application/run_phase_control.py`)
  at its own cooperative checkpoints: the pause-barrier stop-check
  (`runner/loop.py`) and the two scoring stop-checks
  (`scoring/query_loop.py`, `scoring/sample_measurement.py`). For a
  *running* (non-paused) stop, the operator's `stop.flag` is only
  observed at the next scoring checkpoint, so a stop landing near a
  round boundary can jump `running → terminal(interrupted)` without a
  visible `stopping` frame. The honest single source for "stop
  requested, not yet exited" is the moment the flag is written — the
  `stop-cycle` command applier (`presentation/api/middleware/command_dispatcher.py::_apply_stop_cycle`),
  which already has ledger access (it writes the `CommandRecord`).
  **Action:** have `_apply_stop_cycle` append a `control`
  `PhaseRecord(event="stopping")` to the target cycle ledger alongside
  writing the flag, so `LiveDashboardView` projects `stopping` the
  instant the operator clicks — independent of where the runner is in
  the round. Then the three in-runner `declare_run_phase(…, STOPPING)`
  calls become redundant and can be dropped (the flag-write is the
  single declaration point).
  **Load-bearing check:** confirm the dispatcher runs in-process with
  the runner's `LiveDashboardView` subscriber (cycle-targeted commands
  are applied by `RunnerCommandSubscriber` in the runner process) so
  the appended record actually fires the projection; if the applier
  runs in a context without the live subscriber, the declaration won't
  surface until the runner next drains. Also verify the CLI Ctrl+C
  path (no command) still goes straight to `terminal(interrupted)` —
  it has no `stopping` frame by design.
  **Pattern:** control-state declared at the actor's checkpoints
  instead of at the point of intent; >2 days because the cross-process
  in-vs-out-of-runner verification is the real work.

- **TermNorm backend reports a provider slug, not a model** — backend
  `dashboard.json::spend.backend.model = "openrouter"` is the provider,
  not the actual upstream model (e.g. `mistralai/mistral-7b-instruct`).
  Without the real model on the wire, $ for backend usage cannot be
  derived from `shared.spend.lookup_rate(model)` × tokens; the
  Account modal's Activity pane back-fills $ from
  `dashboard.json::spend.backend.total_usd` instead. **Action:** wire
  TermNorm's per-request response to carry the upstream `model` string
  (cross-repo edit at the sibling backend
  `C:\Users\dsacc\OfficeAddinApps\TermNorm-excel\backend-api`). Once
  the wire carries `model`, drop the `_synth_legacy_backend_record`
  back-fill in `presentation/api/routers/auth.py`.
  **Pattern:** missing telemetry field at the wire boundary.

<!-- round_summary.py + factory.py revisit (2026-05-26): both KEEP.
  round_summary.py = named Python→Pydantic adapter
  (RoundResult → RoundSummary); inlining would push raw
  RoundSummaryCandidate(...) constructor calls into _handle_phase
  (wrong abstraction layer in a 920-line projection class).
  factory.py = resume-time disk-reconciliation; for_session docstring
  explicitly commits the classmethod to "thin assembly", and
  resolve_resume_state's stale-pointer healing (_max_round_on_disk +
  prior-state merge) is a named concern that earns its own file. -->

## Audit guidance — what to hunt for

The bar for entries here is **high confidence after verification**,
not "I spotted a code smell." Generic-smell audits flood the backlog
with debatable items. These six patterns merit deletion, each with a
precedent from the closed arc.

### Pattern: premature optimization with apologetic docstring
Code that protects against a scenario that doesn't actually occur,
often hedged by a comment ("for perf", "cached because", "in case
the schema changes"). Verify by reading call sites + measuring
hit-rate / fire-rate. If the protected scenario provably can't
happen, or fires never/rarely on real campaigns, it's debt.
**Precedents (deleted):** `_apply_budget` shed allocator (fired
only when composed prompts exceeded 10k chars; real composed
prompts capped at ~4.7k mandatory + ~3k static = under 8k);
`catalogues.py` global pipeline-param cache (one-entry, sub-ms
render).

### Pattern: redundant double-protection
Two guards on the same condition where one strictly subsumes the
other. Verify by writing the decision boundaries (e.g. two-sided
95% CI: z=1.96 vs one-sided ε=0.05: z=1.645) and confirm one
swallows the other's legitimate cases. **Precedent (deleted):**
PoBB separability floor sitting on top of the Bayesian gate
(strictly stricter; swallowed every mid-budget abort the gate
wanted to fire).

### Pattern: single-caller indirection without architectural reason
Modules / helpers / classes consumed by exactly one caller, with no
test of their own + no layer-boundary justification. Skip splits
that cross a load-bearing layer
(`application/intelligence/ ↮ application/optimization/` per the
invariant) or have their own dedicated test in `tests/`.
**Precedents (inlined):** `l2_driver.py` + `l3_driver.py` →
`executor.py`; audit-1.C `candidate_block` + `score` + `sample` +
`pobb` → `view.py`.

### Pattern: dead exception paths / dead enum variants
Enum members + their handler arms left behind after the code path
that raised them was deleted. Verify by `grep` for every variant —
if the only references are the enum definition + handler arms with
no `raise` / construction site, the variant is debt. **Precedent
(deleted):** `StopReason.PROMPT_BUDGET` after `_apply_budget`
removal.

### Pattern: speculative API surface
Parameters accepted but never read; optional return types `X | None`
where every return is non-None; default kwargs no caller overrides;
Pydantic / dataclass fields declared but never populated. Verify by
tracing call sites + reading the body. **Precedent (deleted):**
`L1Variant.target_axis` + `.reasoning` — the docstring claimed
"persisted in the audit trail but doesn't read them at runtime,"
but l1_behavior validators substring-matched them as
peaked-axis / rebut signals. Resolved by routing both signals
through `pipeline_params_override` keys + `changes_description` +
the citation string, then deleting the fields.

### Pattern: bug blocked on operator-local context
Bug repro requires an environment, fixture, or sibling repo not in the
tree (auth-on tunnel deploy, a specific cycle dir on the maintainer's
laptop, a co-owned backend repo). Default action: **promote the
unblocker before the fix.** Build a local mock harness, check a frozen
fixture into `tests/fixtures/`, or pin the cross-repo dependency — so
the bug becomes reproducible from a clean `git clone` by any
collaborator. Then ship the fix on top.
**Precedents (this arc, 2026-05-28):**
- L2/L3-terminal hang → checked-in `tests/fixtures/cycles/l2_terminal/`
  + Vitest harness at `webapp/lib/derivations/__tests__/`; the
  empty-historical fix landed against the fixture, not against the
  operator's laptop.
- TermNorm wire `model` → `Connector.expected_revision` +
  `version_check` (still pending the actual cross-repo edit, but the
  drift detector is in place so the next mismatch is caught at session
  start instead of weeks later in spend accounting).

### Pattern: vibe-coded scaffolding
Half-finished branches behind `raise NotImplementedError`, enum
variants promising dynamism the system never delivers, comments
referring to future work the project doesn't plan to build. The
root `CLAUDE.md` is explicit: "Document current state, not
half-done plans." **Verify the "future" actually isn't on the
roadmap before flagging** — `ForkTrigger.L2_REBASE` / `L3_REBASE` /
`OPERATOR_REWIND` looked like vibe-coded scaffolding behind a
`NotImplementedError` branch, but `roadmap.md`
explicitly schedules them for wiring. They're now active backlog
("Wire rebase emission") instead of a delete candidate.

### Anti-patterns to skip
These are NOT debt — skip on sight:
- Intentional UI placeholders for M13+ (see § below)
- Per-injection `char_cap` (LLM-overrun truncation; real boundary
  guard)
- Domain vocabulary policed elsewhere (`origin` not `baseline`,
  `sample` not `query`)
- Layer-invariant splits (`application/intelligence/` ↮
  `application/optimization/`)
- ABC `@abstractmethod` / `Protocol` `...` bodies
- `from __future__ import annotations` (standard PEP 563)
- Boundary guards at external-input sites (file I/O, JSON ingest)
- Validators on user-config Pydantic models with `extra='forbid'`
- `_*` private helpers used by exactly one caller in the same file
  (intra-file decomposition isn't inter-file indirection)

### Next-round audit angles
The closed arc + the current backlog drained the obvious vibe-coded
classes. Remaining productive angles for future re-audits:
1. **`dict[str, Any]` parameter soup in hot paths** (polish-D.1
   typed `view_ingress`, but `RoundResult` / `CandidateResult` /
   `PipelineParams` payloads remain). M-sized refactor, own arc.
2. **Test charter violations** — substring assertions on rendered
   text, stub-forest regression tests, tests for trivial wrappers.
   The charter caps the suite at ≤200 collected tests; currently
   199.
3. **Stale `Field(description=...)` strings on LLM-facing schemas** —
   load-bearing per [[feedback-field-description-load-bearing]] but
   some may have drifted from current behavior.
4. **INFO/WARN-level logging for events nobody actually surfaces** —
   log noise audit.
5. **Error-raising style diverges by layer** — generic `Exception` catch in
   `application/optimization/dispatch/hub/facade.py`, bare `raise` + asserts in
   `infrastructure/store/campaign_store/cycles.py`, `HTTPException` in
   `presentation/cli/commands/new.py` for the same class of validation failure.
   An agent can't predict which to raise. M-sized standardization arc (domain
   exception + one HTTP-mapping seam), not a single fix.

## M13+ intentional UI placeholders

UI affordances the product *intentionally* ships disabled today — they
preview the M13+ chat-first UX + config-edit surface + analytics-search
surface. They are **not** scaffolding, not credibility hits, and not in
scope for any "hide non-functional controls" sweep.

| Placeholder | File | Future surface |
|---|---|---|
| Topbar search input (disabled) | `webapp/components/shell/Topbar.tsx:29` | M13+ analytics search |
| ChatPane attach + textarea + send button (disabled) | `webapp/components/chat/ChatPane.tsx:273-279` | M13+ chat-first operator UX |
| ChatPane Extended-thinking / Web-search / Code-execution toggles (`toggle locked`) | `webapp/components/chat/ChatPane.tsx:286-322` | M13+ chat-first feature toggles |
| AccountModal "Update profile" button (disabled) | `webapp/components/account/AccountModal.tsx:193-200` | M13+ profile-editing surface |
| AccountModal "Remove account" menu item (disabled) | `webapp/components/account/AccountModal.tsx:251-258` | M13+ multi-provider account management |
| AccountModal "+ Connect account" button (alerts then no-ops) | `webapp/components/account/AccountModal.tsx:267-278` | M13+ multi-provider account linking |

**Rule:** any future cleanup that touches these surfaces must
distinguish *intentional placeholder* from *scaffolding text/comment*.
Milestone-reference text inside these placeholders is OK (and exempts
them from a "no M-milestone references on operator surfaces" final-grep
gate); other operator surfaces still must not leak milestone numbers.

## History

All prior tiers + the pre-public-release polish arc (Tiers 0–6 + polish
A–E + audits 1–3) closed by 2026-05-25. Done-log entries lived here and
were pruned with the arc; recover via `git log` if needed.
