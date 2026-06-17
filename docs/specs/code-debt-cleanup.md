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
- `webapp/components/tree/RoundFileView.tsx:49-58` `isHit()` — full scorer reimplementation in TS (fallback chain: served `hit` → `fitness >= 0.5` → `score >= 0.5` → substring-match); drives per-sample HIT/MISS column and the `hits` rollup when `doc.hits` is absent (`?? results.filter(isHit).length`, line 74). Root-fix: ensure `AuditTrailView` always serves `hit` per-sample so the fallback chain never fires; then delete `isHit()` and drop the rollup fallback. **Blocker:** Lane C8 write-side (same as above). (found 2026-06-12)
- `webapp/components/eval/FreqChart.tsx:31-43` `bucketScores` fallback — same substring-match scorer triggered when `r.score` is absent; synthesizes a 1/0 score bucketed into the Score-Frequency histogram. Root-fix: same; bucket only the served `r.score`, skipping rows without one. **Blocker:** Lane C8 write-side. (found 2026-06-12)

**Tier 5 — medium, needs an operator call before action.**
- `connectors/protocol.py:158` `Connector.to_dict()` — zero call sites in-repo; **verify** no external/operator script uses it before deleting.
- `domain/pipeline_parsing.py:261` writes `"display_tag"` into `step_kwargs`, but `PipelineNode` has no such field → silently dropped on every parse of an LLM-generated pipeline (runtime tags come from `_build_display_tags`). On-disk datasets DO ship the key and the contract doc lists it optional, so the *write* is inert but the key isn't dead. **Drop the parse-path write; leave doc/datasets.**
- `application/intelligence/indexes/config.py` `ConfigIndex` — `ingest_run` populates `_configs_to_runs`/`_configs_to_node_configs` but exposes no query method and `measurements_for_config` does its own O(N) scan. Documented as a first-class derived view (glossary + developer README), so likely a half-built skip-scan, not oversight. **Confirm intent;** if abandoned, drop the class + `AxisIndex.config_index`.
- `application/optimization/validators/l1_behavior.py:78` `ValidatorContext.param_unlock_round` default 3 never overridden — possibly an intended future tunable. **Confirm before collapsing to a constant.**
- `application/scoring/evaluators.py:44` `compute_accuracy` ↔ `metrics.py:59` `_compute_accuracy` — the deprecated-filter + mean-fitness accuracy line is byte-identical across the two seams (registry evaluator vs. the `{hits,total,accuracy,…}` bundle). Marginal; have `_compute_accuracy` reuse `compute_accuracy` for the scalar if touched.

### Untracked-debt sweep (2026-06-13) — held items

**Tier 1 — dead domain-model fields (write-but-never-read-back; hold: on-disk format / wire changes).**
- `domain/results.py:207-208` `RoundMetadata.cumulative_total` / `cumulative_accuracy` — written at `runner/round.py:95-96` and `cycle.py:381-382`, serialized to `round_NNNN.json::metadata`, deserialized on resume via `model_validate`, but **never read back** from the model or the dict by any consumer (no presentation, infrastructure, webapp, or resume path reads these keys; resume recomputes from `rr.results`). **Action:** drop both fields + their two write sites. **Blocker:** on-disk format change — verify no external consumer reads these keys before deleting.
- `domain/results.py:305` `CycleResult.langfuse_trace_id` — no producer: both `CycleResult` construction sites (`runner/entry.py:346, 363`) never pass it; `bridge.py:322` computes the value locally but `end_campaign()` return is ignored at `entry.py:532`; the notebook guard at `notebook_run.py:129` always sees `None`. **Action:** drop the field OR wire `end_campaign()`'s return into `CycleResult` before `_finalize_run`. **Blocker:** operator decision on whether Langfuse integration is being actively built (if yes: wire; if no: drop).
- `domain/pipeline_schema.py:70-71` `NodePromptInfo.family` / `NodePromptInfo.description` — populated from the backend's `GET /pipeline resolved_prompts` JSON at `pipeline_parsing.py:132-134`; never accessed by any Python code after construction (only `.template_variables` is read, at `config.py:554`); `NodePromptInfo` is not serialized to any API, round file, or webapp wire boundary. **Action:** drop both fields + their population sites in `pipeline_parsing.py`. **Blocker:** confirm no external script/notebook reads them.

**Tier 1 medium — superseded escalation-via-signal mechanism.**
- `domain/escalation_signals.py:15-20` three dead `EscalationTarget` variants: `L2`, `L3`, `ABORT_CAMPAIGN` — zero construction sites across the codebase (only `ELIMINATE_CANDIDATE` + `LEADER_LOCKED` are produced; `RETRY` already removed via the 2026-06-12 sweep). Dependent dead surface: `EscalationSignal.routes_to_optimizer` + `.is_abort` properties (`escalation_signals.py:46,51`), their consuming branches at `runner/loop.py:178-181`, and `StopReason.ABORT` (`phases.py:38,142`). L2/L3 escalation flows through direct `escalate_or_stop()` calls and the `ForkProposal`/`REBASED` path, not these signal targets. **Action:** strip the 3 variants + 2 dead properties + `loop.py:178-181` branches + `StopReason.ABORT`. **Blocker:** operator confirmation that L2/L3/abort routing is fully covered by the direct path — multi-symbol cluster touching documented self-healing.

**Tier 3 — near-duplicate seams (multi-file; new helper needed before deleting copies).**
- `presentation/api/routers/campaigns/ledger.py:71` (and `files.py:104`, `ledger.py:116`, `datasets.py:259`) — identical `cycle_dir = cycle_dir_for(...); if not cycle_dir.exists(): raise NotFoundError(...)` repeated verbatim across 4 same-layer routers. `deps.py` already has `get_backend_or_404` / `read_text_or_404` as the seam. **Action:** add `get_cycle_dir_or_404(store, campaign_id, cycle_id)` to `deps.py`; replace all four sites. **Blocker:** none.
- `presentation/cli/commands/compare.py:30-32` (and `sweep/_common.py:133-135`, `new.py:545-547`, `resume.py:456-458`) — identical 3-line block `session.session_id = ctx.session_id; session.campaign_id = ctx.campaign_id; session.state.cycle_id = ctx.cycle_id` repeated across 4 CLI command modules. **Action:** extract `bind_session_identity(session, ctx)` in `commands/_shared.py`; replace all four sites. **Blocker:** none.
- `presentation/api/routers/active.py:104-118` + `campaigns/cycles.py:191-223` — both resolve the per-cycle dir, read `dashboard.json`, and on absence emit the same `warming_up` payload; only extras differ (runtime flags / `If-Modified-Since`). **Action:** extract shared read-or-warming-up dashboard helper. **Blocker:** medium; verify all callers after extraction.

**Tier 3 — webapp R-36 (backend projection needed; same blocker as existing Lane C8 items).**
- `webapp/components/dashboard/samples/columns.ts:129-145` `hit_rate` cell — computes `const hits = meas.reduce((k,m)=>k+(m.hit?1:0),0); const rate = hits/n` from raw `MeasurementDot[]`; the served `DatasetItem` carries `delta`/`p_hat`/`pick_score` but NOT a per-sample `hit_rate`, while the sibling `PerQueryRow` (types.generated.ts:444) already has one. **Action:** add `hit_rate` to `DatasetItem` projection; render the served value. **Blocker:** Lane C8 write-side scope (same as existing T3 items).
- `webapp/components/eval/TrendChart.tsx:33-37` — builds cumulative running-best fitness trajectory in TS (`runningBest = Math.max(runningBest, p.composite)`) — a best-so-far series the optimizer already owns (`dash.best`). **Action:** serve a `best_so_far` per-round series from the backend projection; delete the TS derivation. **Blocker:** Lane C8 write-side scope.

**Tier 5 — operator call before action.**
- `shared/composite.py:204` `legend: str | None = None` — never overridden by any caller (zero non-`None` call sites); the function body does use it (appends a 4th line when present). Low urgency. **Action:** drop the param if the 4th-line feature is confirmed dead; or add a caller. **Blocker:** operator decision.

### Untracked-debt sweep (2026-06-12) — holds after verification

Five-lens audit. What stayed:

**Tier 1 hold:**
- `application/optimization/dispatch/schemas.py:391` `CheckinOutput.consultation: str = ""` — write-only field; `load_or_build_task_context` / `_apply_findings` never read it; only reference is the rotted notebook at `notebooks/optimization_campaign.ipynb:178`. **Blocker:** `model_config = extra="forbid"` — the field and its counterpart in `datasets/_optimizer/pipeline.json` (auto-generated `checkin` output-schema block) must drop atomically; verify the schema regen and re-gate before committing.

**Tier 2 holds — dead defensiveness (verified writer, but save() has direct-call test paths):**
- `application/optimization/validators/l2_output.py:237-239,279-280,289-291,325-330` — dual-arm dict-fallback (`getattr(entry, "rule_id", None) or (entry.get("rule_id") if isinstance(entry, dict) else None)`) on `L1SupplementalRule`/`L1SituationalExample` entries; the dict-arm appears dead because `_parse_l2` always passes typed model objects. **Blocker:** trace ALL callers of `run_l2_output_validators` to confirm no direct-dict invocation exists (safety-critical validator path).
- `application/optimization/dispatch/hub/facade.py:118` `getattr(template, slot) or ""` — dead `or ""`; `PromptTemplate` slot fields are `str = ""`, never None. Action: drop ` or ""`. (medium, cosmetic)
- `application/scoring/evaluators.py:185` `node.current_config or {}` — dead `or {}`; `current_config: dict = Field(default_factory=dict)`, never None. Action: `node.current_config`. (medium, cosmetic)
- `application/optimization/dispatch/hub/injections/catalogues.py:40-41` `node.param_descriptions or {}` / `node.param_allowed_values or {}` — same `default_factory=dict` guarantee. (medium, cosmetic)
- `presentation/views/view_ingress.py:325` `l2_prompt=d.get("l2_prompt", "") or ""` — double-default; key is always set by `executor.py:197` `_l2_exit()`. Action: `d["l2_prompt"]`. (medium)

**Tier 5 structural holds — single-caller indirections, no tests, inline candidates:**
- `presentation/writers.py:206` `_load_p_best_trajectory`, `:339` `_fork_summary_from_index`, `:420` `_load_sibling_indices` — each called only by `from_disk_log`; no dedicated test. Inline into caller loop. Blocker: none.
- `application/intelligence/indexes/axis.py:74` `_collect()` (called only by `axis.digest()`) and `application/intelligence/indexes/sample.py:287` `_dominant_failure_mode()` (called only by `records()`) — 1–3 line helpers, no dedicated tests. Inline. Blocker: none.
- `application/jobs/launcher.py:779` `_claim_email()` — byte-identical implementation duplicated at `presentation/api/routers/auth.py:539`. Extract to `shared/` or a common identity utility. Blocker: none.
- `application/jobs/launcher.py:302-304 + 373-375` — identical 3-line origin-readiness guard at two call sites (`commit_draft_to_dataset` / `mint_campaign_from_draft_command`). Extract to `_assert_origin_ready(draft)`. Blocker: none.
- `application/jobs/launcher.py:755` `_repo_root()` — called 3× in same module (lines 163, 606, 643); 1-line `Path(__file__).resolve().parents[3]`. Convert to module-level constant. Blocker: none.

### Benchmarks: dev-surface-only, hidden from the distributed end-user app (2026-06-11)

**Product intent (operator):** the distributed webapp must be learnable in ~1h by a non-technical layperson (the Swiss "Ralf"); bundled benchmarks (`bbeh`, `aime_2025`, `gsm8k`, `hotpotqa`, `justlogic`, `lca-termnorm`, `promptpotter*`) confuse that audience and are not for distribution. They must stay fully accessible to **dev surfaces only**: GitHub clone, `SKILL.md` (`/potter-run`), CLI (`new <name>`), folder-UI, and the python entrypoint. The webapp end-user sees only **`yours`** (their own ingested Origins) + optionally **`demo`**.

**The seam already exists — this is a posture/wiring task, not build-from-scratch.** `GET /datasets` (`presentation/api/routers/datasets.py:66-92`) already tiers `yours`/`benchmark`/`demo`, and the `benchmark` tier is gated on the `datasets.benchmarks.read` capability (`infrastructure/store/dataset_access.py`; `demo` rides `User.demo_mode_enabled`, `user_store.py:40`). So benchmarks are already invisible to any identity lacking that capability.

**Action (verify + lock, then confirm with operator):**
1. Confirm the **distributed-app default identity does NOT hold `datasets.benchmarks.read`** (and `demo_mode_enabled=false`), while the CLI/headless/dev identity DOES. Find where the capability is granted (grep `datasets.benchmarks.read`) and check the default-user/anonymous grant path. This is the load-bearing line.
2. Audit the webapp for any path that surfaces a benchmark to a non-capable user despite the API gate — esp. the "prefill a draft from a benchmark/Origin" feature (`datasets.py:190`), the IngestPane dataset list, and any hardcoded benchmark name in `webapp/`.
3. CLI / folder-UI / python entrypoint must NOT route through the capability check (they're the dev surfaces that keep benchmarks) — confirm `new <name>` and direct file-tree access stay unscoped.
4. Add a leak check to [`tests/test_security.py`](../../tests/test_security.py) so a benchmark can't leak to a no-capability identity (R-15) — a leak is the one silent-harm class that earns a standing test (see [`../../tests/CLAUDE.md`](../../tests/CLAUDE.md)).

**Blockers:** operator confirmation on (a) whether `demo` tier stays visible to end-users or also hides, and (b) the exact default-identity capability set for the distributed app vs. the dev clone. Ties to identity ADR-0002.

### Backend-registration dedup (2026-06-11)

Both surface from the `wiring.py` root fix that made one `BackendConnection` per
`(base_url, backend_type)` instead of one per dataset.

- **`webapp/lib/hooks/useConnector.ts` (the `distinct`/`seenEndpoints` loop, ~l.227-238)** —
  client-side collapse of duplicate `BackendConnection` rows is a **back-compat shim**
  for per-dataset rows already minted on disk *before* the `wiring.py` fix (the loop's
  own comment admits it's "a no-op" once data is clean). Violates no-shim / zero-stale-data.
  **Action (planned 2026-06-12 — NOT just a row delete):** investigation 2026-06-11 found the
  dedup is a genuine three-step migration, not a cleanup:
    1. **Rewrite the stored `backend_id` on every existing campaign → one canonical id (`local` =
       `DEFAULT_BACKEND_ID`).** There are 8 distinct per-dataset ids on disk, all pointing at the
       SAME endpoint (`termnorm` @ `127.0.0.1:8000`), referenced by **82 campaigns**
       (`local`×33, `email-tagging`×23, `customer-tickets-eval`×16, `email-tagging-hard-eval`×5,
       `justlogic`×3, + 1 each for `data_justlogic_deductive_reasoning` / `email-tagging-eval` /
       `lca-bom-termnorm`). A bare row-delete does NOT stick — the per-dataset id lives in
       `campaign.json::backend_id`, so any re-wire path that reads it re-references (and the
       register-if-missing block in `wiring.py` re-mints) the row. Canonicalize the references first.
    2. **Collapse the duplicate rows** under `.promptpotter/projects/{tenant}/archive/backends/`
       to one per `(base_url, backend_type)` — needs a new `BackendStore.remove(backend_id)`
       (the store currently has no delete verb).
    3. **Make every re-wire path reuse the canonical id** so it never re-diverges: CLI already
       passes `--backend-id local`; the web/ingest path (empty `backend_id`) hits the
       endpoint-reuse block — confirm it resolves to the canonical row, not "first alphabetical".
  Then **delete the loop** — restore `others: active ? backends.filter(b => b !== active) : backends`.
  Ship as a one-shot idempotent migration script the operator runs on their own data (destructive +
  hard to fully reverse — do NOT run blind from a clean clone).
  **Blocker:** the migration above — write + operator-run it before deleting the loop. The loop is
  **load-bearing until that runs** (the operator currently has 8 stale per-dataset rows + 82
  campaigns referencing them).

- **`promptpotter/application/bootstrap/wiring.py` (the `not backend_id` reuse block, ~l.312-323)** —
  when a *second distinct* endpoint is wired with no `--backend-id` and `DEFAULT_BACKEND_ID`
  already exists for a *different* endpoint, the `if not store.backends.get(backend_id)`
  guard skips registration and the dataset's backend *record* keeps the old endpoint's
  `base_url` (calls still go to the right place — `client` uses `backend_url` directly —
  only the registration metadata lies). **Action:** when falling back to `DEFAULT_BACKEND_ID`,
  guard the reuse on `existing.base_url == backend_url`; mint a deterministic distinct id on
  mismatch (so re-wiring the same endpoint stays stable). **Blocker:** none functionally —
  only bites once a 2nd endpoint/connector exists; defer to the multi-connector lane, but
  recorded so it isn't rediscovered live.

### Untracked-debt sweep (2026-06-14) — holds from today's run

Dead-field / single-caller-indirection sweep. Code fixes **shipped** in `debt-sweep/2026-06-14` (see PR). Items below held — each has a reason verified before holding:

**Security-adjacent / operator-decision holds:**
- `infrastructure/identity/provider_config.py::ProviderIdentity.email_verified` — field is always `True` on Google/GitHub tokens (the provider guarantees it), so it reads as dead. But `.email_verified` is a security-adjacent claim; before deleting, confirm no future OIDC provider (e.g. a hypothetical SAML bridge) might set it `False`. Operator decision. **Action:** delete if confirmed no provider sends `False`.

**Cascade holds (dropping would require wider refactor):**
- `application/optimization/escalation/state.py::EscalationEvent.reason` — set in every `EscalationEvent` constructor, never read by any consumer (only `.next_action` / `.stop_reason` are read). **Cascade:** removing it requires also removing `EscalationRule.format_reason(inputs)` (called only to populate this field in `decide.py:52`) and `EscalationRule.reason` (the callable/string that `format_reason` delegates to). Straightforward cascade but touches 3 files; file together. **Action:** drop `EscalationEvent.reason`, `EscalationRule.format_reason`, and `EscalationRule.reason` in one pass.
- `decide_escalation(rules=...)` default kwarg — the `rules` param carries a DI seam (test override + `__all__` note); no test currently exercises a non-default ruleset beyond the two tests that use `DEFAULT_ESCALATION_RULES` directly. **Confirm** no test passes a custom `rules=` list; if so, collapse the kwarg to a positional-required or an import-time constant. Needs a live test read.
- `domain/round_diagnostics.py::RoundDiagnostics.cache_share: float = 0.0` — always 0.0, only populated as the explicit `cache_share=0.0` default we stripped from `round_analysis.py` this sweep. The field itself (`cache_share`) is still in `RoundDiagnostics`; its render block in `panels.py` was deleted. **Blocker:** round diagnostics serialize to `round_NNNN.json`; confirm no existing on-disk round files carry a non-zero `cache_share` (safe to delete the field since default is 0.0 — old files will deserialize cleanly when the field is missing). **Action:** `grep cache_share .promptpotter/ -r` on operator's data, then delete the field.

**Needs-verify / operator-script holds:**
- `application/datasets/loaders.py::load_dataset()` — exported from `application/datasets/__init__.py` but zero callers in-repo. Public export might mean an operator notebook or external script uses it. **Action:** verify no external use, then delete.
- `infrastructure/store/entity_store.py` CRUD base class — `save`/`load`/`update`/`_entity_path`/`_entity_dir`/`_subdir` never called through the base class (all subclasses call the store-layer directly). **Action:** confirm via grep; if confirmed, delete the base-class methods or collapse the hierarchy.

**Tracing layer — medium (multiple files, safe but tedious):**
- `domain/run_records.py::RoundEnd.temperature` — populated but `temperature` is never read by any projection or display. Confirm and drop.
- `infrastructure/tracing/replay.py` — `schema` param on the replay entry point accepted but not threaded through; dead branches inside. File for a dedicated tracing cleanup pass.
- `infrastructure/tracing/event_stream/view.py::cycle_dir` property — computed but never accessed via the property (callers use the constructor arg directly). Confirm and drop.
- `infrastructure/tracing/langfuse_sink.py::LangfuseObservation.usage_details` — populated (maybe) but never read. Confirm and drop.

**Numeric / always-false slot:**
- `application/scoring/search_point_scorer.py:308` — the 3rd return slot of an internal function is always `False`; the caller destructures it but doesn't use the third value. Confirm the always-False invariant and collapse to a 2-tuple return.

**Variadic hold:**
- `infrastructure/store/measurement_archive.py::register_alias(...)` — variadic `*args` or keyword-only params that are never passed by the sole caller. Verify and drop the dead params.

### Untracked-debt sweep (2026-06-15) — holds after verification

Five-lens audit. The tree came back near-clean (recent sweeps drained the
dead-symbol backlog); the safe set that shipped this run — `_truncate`/`_truncate_raw`
collapse into `shared/text.py::truncate_ellipsis` + dead `vanilla webapp/index.html:NNNN`
breadcrumb-comment strips — is in git log. One genuine new hold:

**Tier 5 — webapp CSS cascade-order drift (operator call: reordering changes the cascade).**
- `webapp/app/styles/index.css:34-40` — `foundation/reduced-motion.css` is imported at line 34,
  **before** five domain files (`login`/`onboarding`/`backend-node`/`account`/`auth`, lines 35-39),
  yet `webapp/CLAUDE.md § Stylesheet` declares reduced-motion + responsive the "two cross-cutting
  tail files imported last so their overrides win." So any motion rule in those five domains
  currently wins over `reduced-motion`'s suppression — a latent `prefers-reduced-motion` a11y gap.
  **Action:** move the five domain `@import`s above line 34 so the cascade tail is just
  `reduced-motion` then `responsive`. **Blocker:** it's a cascade-behavior change — verify against
  the light/dark + reduced-motion harness before landing, not a blind reorder.

**Verified NOT new debt (checked, left alone):** the three `M12 control-plane … write half`
comments (`ChatPane.tsx:71`, `ConnectorInspector.tsx:12`, `webapp/CLAUDE.md`) — M12 is genuinely
in-flight per root CLAUDE.md + the milestone reorg, so they describe *pending* work accurately,
not stale drift. The `.get`/`except` defensiveness at `scoring_context.py:70` / `authored.py:69`
/ `l1_layout.py:120` / `backend.py:59` / `connectors/termnorm.py:259` are legit external-input /
JSON-parse boundary guards (or already tracked in the 2026-06-12 holds), not contract-key fallbacks.

### A backend fix isn't observable without clearing a cache (2026-06-17)

Surfaced landing the two TermNorm fixes (cp1252→utf-8 file I/O; `token_matcher.py` length-bias —
git log). A co-owned backend change is NOT re-exercised by a fresh PP run, because two caches both
key on the *query/searchpoint*, never on backend code/revision:

- **PP measurement cache is backend-version-blind.** Origin/round scoring replays cached per-sample
  measurements keyed on the PP-side searchpoint only (`application/scoring/` `score_search_point` →
  `archive/{measurements,dataset_runs}/`). A backend fix replays the stale (buggy) result — every
  sample shows `📖`/`0.0s` (`presentation/views/live/display.py:169`); observed live as origin C0
  scoring 15/20 cached after the matcher fix. **Action:** fold the connector revision-pin
  (`connectors/protocol.py::Connector.{expected_revision,version_check}`, already exists) into the
  measurement cache key, OR a `--fresh` flag on `new`/`resume`. **Blocker:** key-vs-flag decision.
  Workaround: clear the dataset's `archive/{measurements,dataset_runs}/` before re-running.

- **TermNorm `match_database` feedback-loop** (`…/backend-api/services/match_database.py`) — rebuilt
  from past langfuse traces, so it learns prior (incl. buggy) predictions; `cache_lookup`/`fuzzy_matching`
  (both `short_circuit: true`) can replay a learned target before `token_matching` runs. **Action:**
  confirm the live `/matches` path short-circuits only on `verified` aliases. **Blocker:** cross-repo;
  trace the short-circuit in `api/research_pipeline.py`.

### Considered, not debt (don't re-open)

- **`RunCallbacks` ↔ `emit_*`** — two writer APIs, but `RunCallbacks._phase_ctx: ViewContext` is owned write-then-read cross-event state; folding it into an ambient ContextVar is a downgrade. The "which do I use" rule is in [`developer/adding-a-surface.md`](../developer/adding-a-surface.md) §1.
- **`from_disk_round` / `from_disk_log`** — looks like a roundtrip shim, but it's a genuine separate source: foreign fork-sibling + historical cycles have no live ledger, so on-disk `round_NNNN.json` is the only source. `test_round_complete_view_roundtrip` keeps both factories honest against one View.
- **`measurement_archive.py:115,122,125` `.get("name", run_id)` / `.get("rendered_prompt_hash", "")` / `.get("source", "")` — looked dead (production writer `loaders.py` always sets them), but `save()` is called directly from test fixtures with partial dicts, so the defaults ARE live boundary guards at the `save()` entry point. Not debt.**

### Standing entries

- ~~**Pure dataset → effective-pipeline-params resolver (follow-up to config-aware identity)**~~
  **SHIPPED 2026-06-11.** Extracted `resolve_pipeline_config_params(active, pipeline_overrides,
  dataset_dir) -> dict` (no Session) in `application/config.py`; both
  `configure_and_apply_pipeline` and `origins.py::_dataset_origin_id` now call it, so the
  prospective-origin id can never silently diverge from what a real run stamps. (Signature took
  `active` + overrides rather than the proposed `(dataset_dir, campaign_config, schema)` — the
  shared core is just the node-config merge; `to_pipeline_params()` is only `{"steps": …}`, so
  the resolver needs neither the schema nor the full config.)

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
