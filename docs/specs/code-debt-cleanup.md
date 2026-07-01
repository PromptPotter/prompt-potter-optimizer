# Code-Debt Cleanup — Backlog

**Living backlog of open code debt only.** `git log` is the history layer; when
an item ships, delete it from here. Scope is literal: dead code, redundant
guards, single-caller indirections, premature optimizations that no longer earn
their keep, vibe-coded scaffolding. Default action on every entry is **delete**
(or inline / strip) — verify-first when the evidence isn't on disk.

**Not debt — goes elsewhere:** forward webapp perf/feature work →
[`roadmap.md`](roadmap.md); new milestones/specs → `docs/specs/`; architectural
decisions → [`../architecture.md`](../architecture.md).

**Entry format** (one line, enough to pick up cold): `file:symbol — why it's debt
— action — blocker`. New debt goes under **Ready** (no blocker) or **Blocked**
(name the blocker). Don't open a new dated section — the chronological sweep-log
shape was the old bloat source; readiness buckets replaced it.

> **Verify before trusting an entry.** This doc decays — claims drift as the code
> moves under them. Audits have found long-standing entries that were
> stale or outright wrong (a "dead" field that mlflow reads; an "always-False
> return slot" that's a live signal). Re-confirm call sites before acting; if an
> entry is wrong, fix or drop it as part of the work.

## Ready — no blocker, pick up cold

- **JSON-read sweep** — route hand-rolled `json.loads(path.read_text())` through the existing `read_json` / `read_json_tolerant` (`store/base.py`); drops redundant `.exists()` guards + try/except. The clean-win sites (full guard+try/except collapse) shipped this pass: `dispatcher.py`, `runner/entry.py`, `audit_trail.py` (×3), `origins.py` (×2). **Remaining = partial sites only** — each keeps a custom except arm, so the win is just the guard: `launcher/core.py:530` (raises `LaunchError`), `campaign_store/store.py:690` (returns a reason tuple), `event_stream/tail.py:97` (custom warming-up fallback). `shared/spend.py:95,157` is held separately — `shared/` importing `infrastructure/store/base` is a layer-direction question. Boundary-guard sites (`identity/allowlist.py`, `identity/provider_config.py`, `datasets/authored.py`, `csv_ingest.py`) are intentionally kept (`:104`). **Adopt-in-new-code for the coupon/BYO build:** the new `grant.json` / `api_keys.json` stores MUST ride `read_json_optional`/`write_json` (the `UserStore` template) from day one — don't add two more hand-rolled readers.

<!-- Verified subtractive candidates (hand-verified; each removes a named concept). -->
- `presentation/cli/commands/verify.py:125` `cmd_verify` (189 lines) — CLI command carrying application-layer logic (composite-fitness / archive-dedup / rescore, ~`:253-281`), violating `presentation/CLAUDE.md` ("no business logic in CLI commands"). Move the bootstrap→score→aggregate→record block behind an `application/` verify use-case; keep the CLI a thin arg-parse + format shell. Med-high confidence; one call site, no duplication. NOTE: this *adds* an application module — a layering fix, not a ledger-down subtraction (the surface-ledger "additive-but-safe" trap); land it as a feature-justified commit, not under a "refactor" label.

**Verify-behavior (poll revalidation — NOT a clean substitution):**
- `webapp lib/poll.tsx` local `revalCount`/`setRevalCount` (`:402,433,594`) vs the global `lib/revalidate.ts::useRevalidation()` bus — the dashboard poll uses the local counter, so it does **not** re-tick on a mutation's `bumpRevalidation()`. The filed "just swap to `useRevalidation()`" is WRONG: verified `usePoll`'s interval effect (`usePoll.ts:52-83`) deps `[intervalMs,pauseWhenHidden,tickOnFocus,enabled,runTick]` with `runTick` stable (`useCallback([])`), so on a unit switch (`enabled` unchanged) it does NOT restart/re-tick — the local `revalCount` bump (`:433`) is the ONLY immediate-tick trigger on campaign switch. Substituting the global bus would lose that. Real fix = feed BOTH signals (e.g. `revalidateOn: revalCount + globalReval`), which ADDS the mutation-tick behavior rather than removing a concept — a behavior change, deferred out of the subtractive batch.

## Blocked — named blocker

**Live L1 round (operator-gated):**
- **`*_override → *_updates` L1 delta-key rename** (+ webapp searchpoint-projection collapse). `prompt_fields_override` / `task_context_override` / `pipeline_params_override` / `pp_override` are merges, not replacements, but named "override." **Decision (settle first):** unify the pipeline delta to the glossary word **`pipeline_overlay`** everywhere (kills the short/long two-name tax); the prompt/context deltas become `*_updates`. Rename writer→reader in one commit (schema `dispatch/schemas.py::L1Variant` is the source of truth — the LLM contract auto-propagates), collapse the two webapp readers (`searchPoint.ts` + `candidateSearchPoint.ts`) into one `wireToCandidateSearchPoint(wire)` helper. Full site map: grep `*_override`. **Blocker:** invalidates on-disk cycles (round-file key + optimizer structured-output contract) — verify against a FRESH cycle that completes round 1, not a resume.

**Operator decision:**
- `application/optimization/dispatch/schemas.py::CheckinOutput.consultation` — write-only field, never read (only the rotted notebook references it). Blocker: `extra="forbid"` means the field + its auto-generated counterpart in `datasets/_optimizer/pipeline.json` must drop atomically; verify the schema regen + re-gate.
- `shared/composite.py:204::legend: str | None = None` — never overridden (zero non-`None` callers); the body does use it (appends a 4th line). Drop the param if the feature is dead, or add a caller.
- `CampaignConfig.dataset_split` — the **typed** field is read nowhere; the live consumer (`routers/datasets.py:531`) re-reads `campaign.json` as a raw dict and the webapp footer renders `split_train`/`split_test` from it. Load-bearing via the JSON wire, not the type → a clean removal needs a 3-part migration (drop the field + migrate `justlogic/campaign.json` so `extra="forbid"` doesn't reject it + the raw-dict reader + the `config_diff` path key). Low value pre-build. (`origin_gate` was paired here — **dropped**, verified live: the `strict`/`critical_only`/`off` literal is branched in `termination.py::origin_gate_tripped` and tested in `test_numerics.py`, not vestigial.)

**Security posture / migration:**
- **Benchmarks: dev-surface-only, hidden from the distributed end-user app.** Bundled benchmarks (`bbeh`, `aime_2025`, `gsm8k`, …) must stay on dev surfaces (CLI / `SKILL.md` / folder-UI / python entrypoint) but be invisible to the distributed webapp's default identity. The seam exists — `GET /datasets` tiers `yours`/`benchmark`/`demo`, the `benchmark` tier gated on `datasets.benchmarks.read`. Verify + lock: (1) the distributed-app default identity does NOT hold that capability (and `demo_mode_enabled=false`); (2) no webapp path leaks a benchmark past the API gate (esp. prefill-draft-from-benchmark, the IngestPane list); (3) CLI/dev surfaces stay unscoped; (4) add an R-15 leak check to `tests/test_security.py`. Blocker: operator confirms the default-identity capability set + whether `demo` also hides. Ties ADR-0002.
- **Backend-registration dedup** — `webapp/lib/hooks/useConnector.ts` client-side `distinct`/`seenEndpoints` collapse is a back-compat shim for per-dataset `BackendConnection` rows minted before the `wiring.py` one-row-per-`(base_url, backend_type)` fix. NOT a row-delete: it's a 3-step migration — (1) rewrite each campaign's `campaign.json::backend_id` to the canonical `local` (8 stale ids across 82 campaigns, all → the same `127.0.0.1:8000` endpoint); (2) collapse the duplicate rows (needs a new `BackendStore.remove`); (3) make every re-wire path reuse the canonical id. Then delete the loop. Also: `wiring.py` `not backend_id` reuse block should guard on `existing.base_url == backend_url` (mint a distinct id on mismatch). Blocker: write + operator-run the idempotent migration on their data first — the loop is load-bearing until then.

**Verify-behavior (not a blind change):**
- `application/optimization/validators/l2_output.py` dual-arm dict-fallback (`getattr(entry, …) or (entry.get(…) if isinstance(entry, dict) else None)`) on `L1SupplementalRule`/`L1SituationalExample` — dict-arm appears dead (`_parse_l2` always passes typed models). Blocker: trace ALL callers of `run_l2_output_validators` for a direct-dict invocation (safety-critical path) before dropping.
- **Collapse the three graded l2_output framing-staleness checks** — `validators/l2_output.py` carries `_check_task_context_verbatim_repeat` / `_check_duplicate_insert` / `_check_task_context_paraphrase_repeat`, three graded shades of "L2's `task_context` refinement is stale." Weigh folding them into one "framing-staleness" reason. Blocker: confirm each graded id isn't independently surfaced to L3 before collapsing. (The cross-registry predicate-sharing half of this item shipped — `TaskDecomposition.merge_changes_nothing` now backs both the live executor decision and the offline `l2_behavior` conformance check.)
- `webapp/app/styles/index.css:34-40` — `foundation/reduced-motion.css` imports before five domain files, so their motion rules win over reduced-motion's suppression (a11y gap vs `webapp/CLAUDE.md § Stylesheet`'s "tail files win" rule). Move the five domain `@import`s above line 34. Blocker: cascade-behavior change — verify against the light/dark + reduced-motion harness.
- **Winner-artifact provenance cluster** — `runner/entry.py` pairs the BEST round's `pipeline_params` with `cycle.opt_sp`'s prompt fields, but `opt_sp` is overwritten to the LAST round by `absorb_round`, so when best≠last the operator-facing winner reports mismatched prompt+params; the resume best-round loop in `cycle.py` repeats the mismatch; and `entry.py`'s `final_block` never serializes `winner_prompt_fields`/`winner_pipeline_params`, so `output/writers.py` (log.md FinalWinnerView) renders empty and `pobb/elevation.py` skips every cycle. Fix: read both winner fields from `best_sp` (already populated from the best round by `to_job_search_point`); set per-round prompts before capturing `best_sp` on resume; add both keys to `final_block`. **Blocker:** Explore-agent-surfaced, NOT hand-verified this pass — re-confirm line-by-line vs HEAD before touching (it's a correctness path).

**Cross-repo (TermNorm sibling at `OfficeAddinApps/TermNorm-excel/backend-api`):**
- **TermNorm wire `model`** — backend `spend.backend.model` reports a provider slug (`"openrouter"`), not the upstream model, so backend $ can't be derived from `lookup_rate(model)×tokens`. Add `model` to the per-request response + a `/version` endpoint; this repo then bumps `termnorm.py::_EXPECTED_REVISION`. (The connector revision-pin already exists; the old `auth.py` back-fill is already gone.)
- **Backend fix isn't observable without clearing a cache** — PP's measurement cache + TermNorm's `match_database` both key on query/searchpoint, never on backend code/revision, so a co-owned backend fix replays stale results. Fold the connector revision-pin into the measurement-cache key (or add a `--fresh` flag); confirm the TermNorm `/matches` short-circuit only fires on `verified` aliases. Workaround: clear `archive/{measurements,dataset_runs}/`.

**Coupon + BYO build (Lane A2 — blocked on the build itself; ADR-0003 § Host coupon):**
- **Two host-wallet mechanisms** — `application/jobs/quota.py::effective_spend_cap_usd` + `User.spend_budget_usd_daily` (recurring daily cap, mint-time snapshot) vs the new coupon (`grant.json`, ledger-derived, live). Two guards on one concern (host's wallet) = the no-redundant-mechanism rule. Action: **delete the daily-cap path**; coupon-remaining becomes the single host ceiling, read by the per-cycle `BudgetGate` every tick (D1/D2 in ADR-0003). Blocker: lands *with* the coupon, not before — deleting first leaves the wallet unguarded.
- `domain/run_records.py::TokenUsageRecord` lacks `key_source` → `/auth/activity` `group_by=api_key` (`routers/auth.py`) fakes a *provider slug* as the key id. Once real `key_source: host|user` lands (declared on `TokenUsagePayload` in the asyncapi), replace the fake-slug derivation with the real dimension. Blocker: the coupon build adds the field.

## Standing — long-lived design holds

- **HITL notebook (`notebooks/optimization_campaign.ipynb`) rotted** against the orchestration API (un-gated, drifted across the ingest/origin unify): wrong tuple arity from `prepare_origin_notebook`, imports a deleted module, treats `CampaignOrigin` as a round list. Rewrite the three cells against the current `notebook_run.py` contract, or retire the notebook (operator's call). Retiring also clears `application/origin.py::DatasetSummary.splits` + `domain/search_point.py::TaskDecomposition.FIELDS` (read only by the notebook). Blocker: needs a live TermNorm backend to verify — dedicated session, not a blind edit.
- **Optimizer model unreliable on heavy L2/L3 structured output** — `openrouter/gpt-oss-120b` (all optimizer nodes) is slow + schema-noncompliant on the large `L3PlanOutput`/`L2*` shapes, firing the repair retry and sometimes failing it. Swapping it is a per-node overlay edit (`datasets/_optimizer/pipeline.json::nodes.{l2_context,l3_plan}.config.model`), not service code — operator picks a faster/schema-reliable model, or shrink the schema. Needs a live cycle reaching L3 to measure repair-rate.
- **`RunPhase.STOPPING` thin window for non-paused stops** — declared only at the runner's cooperative checkpoints, so a running stop near a round boundary jumps `running → terminal(interrupted)` with no `stopping` frame. Have `_apply_stop_cycle` (the command applier that writes `stop.flag`) append a `control` `PhaseRecord(event="stopping")` so the projection fires at the instant of intent; the three in-runner `declare_run_phase(STOPPING)` then become redundant. Blocker: confirm the applier runs in-process with the runner's `LiveDashboardView` subscriber; verify the CLI Ctrl+C path keeps its no-`stopping` design.
- `infrastructure/tracing/replay.py` — `schema` param accepted but not threaded through; dead branches inside. File for a dedicated tracing-cleanup pass.

## Considered, not debt — don't re-open

- **`search_point_scorer.py::score_search_point` 3rd return slot** — not an "always-False" bool; it's a live `EscalationSignal | None` consumed by `l1/score/candidate.py` (drives candidate elimination). (Was a stale debt claim.)
- **`measurement_archive.py::register_alias(*hashes)`** — the variadic isn't dead; the sole caller passes both hashes and the function needs ≥2. (Stale claim.)
- **`QueryNodeSpan.usage_details` (via `langfuse_sink`)** — read and forwarded to the Langfuse cloud observation. Not dead. (Stale claim.)
- **`webapp` `hit_rate` cell + `headline-stats.ts::fitnessTrend`** — fold already-served values (per-dot `hit` booleans; cumulative max over served `composite_fitness`); neither reimplements a scorer, so serving them would add wire coupling for identical behaviour (not R-36).
- **`presentation/views/live/phase.py` recall@k recompute** — the round-stats block recomputes top-1/top-5 recall via `find_rank`/`get_ranked_items` (terminal live readout only; `round_analysis._rank_analysis` owns the equivalent buckets app-side). Serving recall on `RoundResult` would add wire coupling for display-only behaviour — same call as the `hit_rate` entry above, so NOT a forced R-36 fix. The bare `except: pass` that wrapped it WAS the real smell and was fixed (now logs at warning).
- **`RunCallbacks` ↔ `emit_*`** — two writer APIs by design; the "which do I use" rule is in [`../developer/adding-a-surface.md`](../developer/adding-a-surface.md) §1.
- **`from_disk_round` / `from_disk_log`** — not a roundtrip shim; foreign fork-siblings + historical cycles have no live ledger, so on-disk `round_NNNN.json` is the only source.
- **`measurement_archive.py` `.get(…, default)` at `save()`** — looks dead (the production writer always sets the keys) but `save()` has direct test-fixture callers with partial dicts; live boundary guards.
- **`writers.py` `_load_p_best_trajectory` / `_fork_summary_from_index` / `_load_sibling_indices`, `axis.py::_collect`** — single-caller, but the caller is in the SAME file in every case; intra-file `_private` decomposition is not inter-file indirection.- **Leader-lock-in mechanism** (`leader_lock_in` / `pobb_lock_in` / `pobb_lock_in_n_min` knobs + `PoBBConfig.lock_in` + the `LEADER_LOCKED` `EscalationTarget`/`CandidateOutcome` + the `abort:lock_in_off` lineage-overlay lens) — the config knobs default off and no committed campaign sets them, so it LOOKS like a dead mechanism, but the `LEADER_LOCKED` path is structurally LIVE: a domain escalation target, a candidate outcome, the mask/lineage-overlay `abort:lock_in_off` what-if lens (`FamilyTree.tsx` "No lock-in"), and exercised by `tests/test_numerics.py`. Deleting it removes a shipped analysis feature, not dead code. Investigated + KEPT. (The unreachable significance-gate beside it WAS deleted — it had no live surface.)
- **Check-in "ready" ≠ "mintable" for prompt template-vars** — `origin_readiness(draft)` gates columns/framing/answer-space but not whether the committed prompt carries each node's required `{{template vars}}`; that check lives only at mint (`config.py::configure_and_apply_pipeline`, `pipeline_config_invalid` 422). Surfacing it earlier (at the resolve turn) needs the live `GET /pipeline` schema threaded into the deliberately-I/O-free resolve path (`origin_readiness` is pure-over-draft; `resolve_origin_turn`/`POST /resolve-origin` carry no backend client + the draft no base_url). **Operator decided: keep the 422 backstop, don't add pre-mint backend I/O.** It's non-destructive (draft preserved, retry) and names the exact missing vars + fix; a bad origin never runs. Revisit only if check-in UX timing becomes a felt pain.
- **MLflow + Langfuse sinks** — the observability-nexus *core capability*: PromptPotter drops into a team's EXISTING local MLflow / cloud Langfuse instance (flip a flag / add `.env` creds). Off-by-default ≠ dead. See `docs/architecture.md` §0.5 Tracing. Do not propose for deletion.

## Audit guidance — what to hunt for

Bar for an entry: **high confidence after verification** (call sites traced +
bodies read), not "I spotted a smell." Productive patterns:

- **Premature optimization with apologetic docstring** — protects a scenario that can't happen / fires never. Verify by reading call sites + measuring fire-rate.
- **Redundant double-protection** — two guards on one condition where one subsumes the other. Verify by writing the decision boundaries.
- **Single-caller indirection without architectural reason** — module/helper consumed by one caller, no own test, no layer-boundary justification. Skip splits across a load-bearing layer or with a dedicated test.
- **Dead exception paths / enum variants** — handler arms left after the raising path was deleted. Grep every variant for a construction site.
- **Speculative API surface** — params never read, `X | None` always non-None, default kwargs no caller overrides, fields declared/written never read.
- **Vibe-coded scaffolding** — `NotImplementedError` branches, comments referring to work the project doesn't plan. Verify the "future" really isn't on the roadmap first.

**Anti-patterns — NOT debt, skip on sight:** intentional UI placeholders
(below); per-injection `char_cap`; domain vocabulary policed elsewhere (`origin`
not `baseline`); the `application/intelligence/ ↮ application/optimization/`
layer-invariant split; ABC `@abstractmethod` / `Protocol` `...` bodies; `from
__future__ import annotations`; boundary guards at external-input sites (file
I/O, JSON ingest); validators on `extra='forbid'` user-config models; `_*`
private helpers used by one caller **in the same file**.

**Next-round angles:** `dict[str, Any]` payload soup in hot paths
(`RoundResult`/`CandidateResult`/`PipelineParams`); test-charter violations
(substring assertions, stub-forest tests — suite cap ≤200, currently ~199);
drifted `Field(description=…)` on LLM-facing schemas; INFO/WARN logging nobody
surfaces; error-raising style diverging by layer (generic `Exception` vs bare
`raise` vs `HTTPException` for the same failure class — M-sized standardization).

## Intentional UI placeholders

UI affordances the product *intentionally* ships disabled today — they preview
the chat-first front door (Lane **C1**, [`chat-foundation.md`](chat-foundation.md)) + the
config-edit + analytics-search surfaces. **Not** scaffolding; not in scope for any "hide
non-functional controls" sweep.

| Placeholder | File | Future surface |
|---|---|---|
| Topbar search input (disabled) | `webapp/components/shell/Topbar.tsx` | analytics search (C4-adjacent) |
| ChatPane attach + textarea + send (disabled) | `webapp/components/chat/ChatPane.tsx` | **C1** chat-first front door ([`chat-foundation.md`](chat-foundation.md)) |
| ChatPane thinking / web-search / code-exec toggles (locked) | `webapp/components/chat/ChatPane.tsx` | assistant tool-use — deferred past **C1** (asyncapi-first; [`chat-foundation.md`](chat-foundation.md) §7) |
| AccountModal "Update profile" (disabled) | `webapp/components/account/AccountModal.tsx` | profile editing |
| AccountModal "Remove account" (disabled) | `webapp/components/account/AccountModal.tsx` | multi-provider account mgmt |
| AccountModal "+ Connect account" (alerts, no-ops) | `webapp/components/account/AccountModal.tsx` | multi-provider account linking |

**Rule:** cleanup touching these must distinguish *intentional placeholder* from
*scaffolding*. Milestone-reference text inside them is OK (exempt from the "no
M-milestone references on operator surfaces" grep gate); other operator surfaces
must not leak milestone numbers.

Closed items are not tracked here — `git log` is the history layer.
