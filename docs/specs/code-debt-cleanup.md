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

**Do soon, not now** (surfaced by the 2026-07-10 drift pass; the six fields with *no* reader at all were already deleted):

- **`meta_champion/reducer.py::_finalize` pools cell-occurrences i.i.d.** — `anchor_effect` (the promotion ranking key) flattens every cell's every occurrence into one `paired_diff_posterior` call, so the SE uses n = total measurements (overstated confidence) and an over-represented cell outweighs uniform goodness; it also uses z=1.96 where the ~7-cell verdicts elsewhere use Student-t. Action: per-cell paired aggregation + `t_critical`, mirroring `domain/l4/verdict.py::compute_outer_verdict`. Fix before the next `champion` promotion — it is the table the promotion decision reads.
- **52 `export`s re-exported through `export *` barrels** — `lib/api`, `lib/types`, `lib/derivations`, `components/ui`, `components/workflow`. They look local-only to a naive grep; stripping `export` silently narrows each barrel's public surface. The 37 genuinely file-local ones already landed. Action: decide per barrel whether the symbol is meant to be public, then strip or keep — don't script it blind.
- **Post-flip copilot — deferred on purpose, not forgotten.** The `checkin` node consulting in run mode and raising `pause-cycle` / `change-spend-budget` / `fork-cycle` instead of draft patches. `RaisedCommand` (`datasets/origin_resolve.py`) is already general enough to carry it. Not debt and not now: it is a **new feature**, and the closing-phase directive is no new features until `promptpotter-self` is distributable. Lands with L4, so it belongs to [`l4-outer-loop.md`](l4-outer-loop.md) when it does.

## Blocked — named blocker

**Behavior change (needs explicit sign-off, not a blind swap):**
- **`dash.state` phase vocabulary hand-mirrored twice TS-side** (double-ownership rd-2 #5) — `run-phase.ts:50-59 PHASE_PAUSE_LABEL` + `components/workflow/layout.ts:96-106 activeNodeId` are two independent hand-copies of backend `live_dashboard/view.py:93-100 _PHASE_TO_STATE` (sole `dash.state` writer). Rename/extend a state backend-side → pause label falls back to generic + canvas node stops pulsing, silently (no compile error). **Deliberately NOT collapsed this pass:** the plan's preferred fix (backend serves resolved pause-label + active-node) is net-POSITIVE, `PHASE_PAUSE_LABEL` is UI copy that belongs frontend (VOICE.md), and `activeNodeId` fuses two live inputs (`inFlightNode` + `state`). Blocker: any clean fix is additive; the only subtractive option (emit the state union into `types.generated.ts` so both maps key off it, like `STOP_REASON_LABELS`) keeps both maps — purely additive safety. Low value; leave until the generated-union pass happens anyway.
- **Archive/delete guard = pointer, not liveness** (double-ownership rd-2 #6) — `campaign_store/store.py:396 _is_active_campaign` reads `active_session.json` (cleared only by CLI `reset`) to refuse archive; a FINISHED-but-still-pointed campaign refuses archive forever, while everything else derives liveness from `derive_run_phase`. Two derivations of "is this campaign live." Action: guard on `derive_run_phase`; keep the pointer a convenience pointer. Blocker: not a pure swap — archiving a finished-pointed campaign must also clear/repoint `active_session.json` (else stranded pointer), so the collapse ADDS a pointer-clear; behavior change to a destructive guard, needs sign-off.
- **Two DETACHED clocks** (double-ownership rd-2 #7) — backend `derive_run_phase` (file-mtime) vs frontend `run-phase.ts:71 resolveRunPhase(runPhase, connectionLive)` (SSE connection freshness) both answer "is this run alive." Documented split. Not cleanly collapsible: "is MY SSE connection live" is inherently a per-viewer frontend signal the backend can't own; the backend mtime rule and the viewer's connection-liveness are different facts that legitimately co-exist. Leave as-is unless a served freshness field proves it can subsume the viewer signal.
- `webapp lib/poll.tsx` local `revalCount`/`setRevalCount` (`:402,433,594`) vs the global `lib/revalidate.ts::useRevalidation()` bus — the dashboard poll uses the local counter, so it does **not** re-tick on a mutation's `bumpRevalidation()`. The filed "just swap to `useRevalidation()`" is WRONG: verified `usePoll`'s interval effect (`usePoll.ts:52-83`) deps `[intervalMs,pauseWhenHidden,tickOnFocus,enabled,runTick]` with `runTick` stable (`useCallback([])`), so on a unit switch (`enabled` unchanged) it does NOT restart/re-tick — the local `revalCount` bump (`:433`) is the ONLY immediate-tick trigger on campaign switch. Substituting the global bus would lose that. Real fix = feed BOTH signals (e.g. `revalidateOn: revalCount + globalReval`), which ADDS the mutation-tick behavior rather than removing a concept. Blocker: this is a behavior change (adds a trigger), not a subtractive cleanup — needs the light/dark + reduced-motion-style browser verification pass, not a blind edit.

**Behavior change (needs explicit sign-off, not a blind swap) — scoring:**
- **All-errored candidate scores `accuracy = 0.0`, not the honest `None`** — `compute_accuracy` (evaluators.py) returns 0.0 when no scoreable row exists; for all-deprecated that IS the verdict, but for all-errored it fabricates one (declared stage-1 tolerance in its docstring, 2026-07-13). The honest `None` must propagate: `ScoredCandidate.accuracy`/`RoundResult.accuracy` → `float | None`, `compute_composite_fitness` handling a missing `accuracy` term without `ScoringTermMissingError` in `_running_scores` (an "unscoreable candidate" state, the outer sibling of `InnerCycleUnscoreableError`), `display_fitness` double-None, dashboard + `types.generated.ts` + chart null handling, `best_round_by_cumulative_accuracy`/`_apply_best` null-safety. Blocker: wide Optional-propagation across the served surface for a state PoBB DegradationCheck usually eliminates mid-round anyway; needs its own pass.

**Live L1 round (operator-gated):**
- **`*_override → *_updates` L1 delta-key rename** (+ webapp searchpoint-projection collapse). `prompt_fields_override` / `task_context_override` / `pipeline_params_override` / `pp_override` are merges, not replacements, but named "override." **Decision (settle first):** unify the pipeline delta to the glossary word **`pipeline_overlay`** everywhere (kills the short/long two-name tax); the prompt/context deltas become `*_updates`. Rename writer→reader in one commit (schema `dispatch/schemas.py::L1Variant` is the source of truth — the LLM contract auto-propagates), collapse the two webapp readers (`searchPoint.ts` + `candidateSearchPoint.ts`) into one `wireToCandidateSearchPoint(wire)` helper. Full site map: grep `*_override`. **Blocker:** invalidates on-disk cycles (round-file key + optimizer structured-output contract) — verify against a FRESH cycle that completes round 1, not a resume.

**Operator decision:**
- (`shared/composite.py::legend` — **DONE**, deleted. Verified all three `render_composite_fitness_block` call sites (`views/render/markdown.py`, `views/live/display.py`, `views/render/text.py`); none passed it, so the param was always `None` and the `if legend:` branch was unreachable. Short-names mode already inlines each code's resolved value into the formula line, so the abbreviations are reconciled where they're used — the legend line had nothing left to do.)
- (`origin_gate` was paired here — **dropped**, verified live: the `strict`/`critical_only`/`off` literal is branched in `termination.py::origin_gate_tripped` and tested in `test_numerics.py`, not vestigial.)

**Security posture / migration:**
- **Benchmarks: dev-surface-only, hidden from the distributed end-user app.** Bundled benchmarks (`bbeh`, `aime_2025`, `gsm8k`, …) must stay on dev surfaces (CLI / `SKILL.md` / folder-UI / python entrypoint) but be invisible to the distributed webapp's default identity. The seam exists — `GET /datasets` tiers `yours`/`benchmark`/`demo`, the `benchmark` tier gated on `datasets.benchmarks.read`. Verify + lock: (1) the distributed-app default identity does NOT hold that capability (and `demo_mode_enabled=false`); (2) no webapp path leaks a benchmark past the API gate (esp. prefill-draft-from-benchmark, the IngestPane list); (3) CLI/dev surfaces stay unscoped; (4) add an R-15 leak check to `tests/test_security.py`. Blocker: operator confirms the default-identity capability set + whether `demo` also hides. Ties ADR-0002.
- **Backend-registration dedup** — `webapp/lib/hooks/useConnector.ts` client-side `distinct`/`seenEndpoints` collapse is a back-compat shim for per-dataset `BackendConnection` rows minted before the `wiring.py` one-row-per-`(base_url, backend_type)` fix. NOT a row-delete: it's a 3-step migration — (1) rewrite each campaign's `campaign.json::backend_id` to the canonical `local` (8 stale ids across 82 campaigns, all → the same `127.0.0.1:8000` endpoint); (2) collapse the duplicate rows (needs a new `BackendStore.remove`); (3) make every re-wire path reuse the canonical id. Then delete the loop. Also: `wiring.py` `not backend_id` reuse block should guard on `existing.base_url == backend_url` (mint a distinct id on mismatch). Blocker: write + operator-run the idempotent migration on their data first — the loop is load-bearing until then.

**Cross-repo (TermNorm sibling at `OfficeAddinApps/TermNorm-excel/backend-api`):**
- **TermNorm wire `model`** — backend `spend.backend.model` reports a provider slug (`"openrouter"`), not the upstream model, so backend $ can't be derived from `lookup_rate(model)×tokens`. Add `model` to the per-request response + a `/version` endpoint; this repo then bumps `termnorm.py::_EXPECTED_REVISION`. (The connector revision-pin already exists; the old `auth.py` back-fill is already gone.)
  **Downgraded 2026-07-10:** PromptPotter no longer reads that slug at all. `_compute_step_tokens` now stamps every step-token entry with the node's model — the backend's per-node `model` when it reports one, else the model the dataset overlay pinned (`pipeline.json::nodes.{n}.config.model`, which is mandatory for an LLM node). So per-node cost is derivable today, including for chars/4-estimated nodes, and the old `node_model or llm_provider` coalescing is gone. What remains genuinely owed on the TermNorm side is the **`/version` endpoint**; the per-request `model` is now a nicety (it wins over the overlay when present), not a blocker.
- **Backend fix isn't observable without clearing a cache** — PP's measurement cache + TermNorm's `match_database` both key on query/searchpoint, never on backend code/revision, so a co-owned backend fix replays stale results. Fold the connector revision-pin into the measurement-cache key (or add a `--fresh` flag); confirm the TermNorm `/matches` short-circuit only fires on `verified` aliases. Workaround: clear `archive/{measurements,dataset_runs}/`.

**Coupon + BYO build (Lane A2 — blocked on the build itself; ADR-0003 § Host coupon):**
- **Adopt-in-new-code for the coupon/BYO build:** the new `grant.json` / `api_keys.json` stores MUST ride `read_json_optional`/`write_json` (the `UserStore` template, `store/io.py`) from day one — don't add hand-rolled readers. `shared/spend.py:95,157` still hand-rolls `json.loads(path.read_text())`; held separately from the JSON-read sweep (SHIPPED elsewhere) because `shared/` importing `infrastructure/store/io` is an unresolved layer-direction question — resolve it before or alongside this build.
- **Two host-wallet mechanisms** — `application/jobs/quota.py::effective_spend_cap_usd` + `User.spend_budget_usd_daily` (recurring daily cap, mint-time snapshot) vs the new coupon (`grant.json`, ledger-derived, live). Two guards on one concern (host's wallet) = the no-redundant-mechanism rule. The snapshot is also concurrency-blind: two CLI mints while `daily_spent` is low each receive the full remainder and never re-aggregate mid-run, so combined spend can reach ~2× the daily cap (the capacity-1 `JobRegistry` slot doesn't bind — it's a per-process in-memory lock). Action: **delete the daily-cap path**; coupon-remaining becomes the single host ceiling, read by the per-cycle `BudgetGate` every tick (D1/D2 in ADR-0003), which also closes the concurrency hole. Blocker: lands *with* the coupon, not before — deleting first leaves the wallet unguarded.
- `domain/run_records.py::TokenUsageRecord` lacks `key_source` → `/auth/activity` `group_by=api_key` (`routers/auth.py`) fakes a *provider slug* as the key id. Once real `key_source: host|user` lands (declared on `TokenUsagePayload` in the asyncapi), replace the fake-slug derivation with the real dimension. Blocker: the coupon build adds the field.

## Standing — long-lived design holds

- **Optimizer model unreliable on heavy L2/L3 structured output** — `openrouter/gpt-oss-120b` (all optimizer nodes) is slow + schema-noncompliant on the large `L3PlanOutput`/`L2*` shapes, firing the repair retry and sometimes failing it. Swapping it is a per-node overlay edit (`datasets/_optimizer/pipeline.json::nodes.{l2_context,l3_plan}.config.model`), not service code — operator picks a faster/schema-reliable model, or shrink the schema. Needs a live cycle reaching L3 to measure repair-rate.
- **`RunPhase.STOPPING` thin window for non-paused stops** — declared only at the runner's cooperative checkpoints, so a running stop near a round boundary jumps `running → terminal(interrupted)` with no `stopping` frame. Have `_apply_stop_cycle` (the command applier that writes `stop.flag`) append a `control` `PhaseRecord(event="stopping")` so the projection fires at the instant of intent; the three in-runner `declare_run_phase(STOPPING)` then become redundant. Blocker: confirm the applier runs in-process with the runner's `LiveDashboardView` subscriber; verify the CLI Ctrl+C path keeps its no-`stopping` design.
- (`infrastructure/tracing/replay.py` `schema` param — **entry was WRONG, dropped.** Verified: `schema` IS threaded and read — `extract_pipeline_nodes` calls `schema.node_param_keys()` and iterates `schema.nodes`; `_replay_run` reads `schema.name` and passes it down. Nothing dead. A dedicated tracing-cleanup pass may still be worth it, but not for this reason.)

## Considered, not debt — don't re-open

- **`best_round` two bases (composite-argmax winner export vs cumulative-accuracy index/dashboard headline)** — settled 2026-07-03, operator-decided correct-by-design: composite is the optimizer's objective (winner export + L2/L3 stall comparator, which also compares θ), accuracy is the formula-independent headline. Documented in `_apply_best`'s docstring + `architecture.md` §0.5. Don't flip either basis to match the other.
- **Winner-artifact provenance (best≠last mismatch / empty `final` winner keys)** — filed 2026-07-02, hand-verified ALREADY FIXED at HEAD 2026-07-03: winner fields read from `best_sp` (`runner/entry.py:342-343`), resume overlays per-round prompts before capture (`cycle.py:520-527`), `final_block` carries both keys (`entry.py:751-752`) and `output/writers.py` renders them. The `pobb/elevation.py` leg is moot — module deleted; L4 proxy reads `origin_level`/`round_discovered_levels`.
- **reduced-motion.css barrel position was never an a11y gap** — the filed "five domain files import after it, so their motion rules win" claim was FALSE: `reduced-motion.css` is the only sheet with `!important` on motion properties, and important author declarations beat every normal declaration regardless of source order. The five domain `@import`s were still moved above the a11y tail (2026-07-03) so the barrel matches its own "tail LAST" comment — a zero-computed-style-change reorder, not a fix.
- **`l2_duplicate_insert` stays a distinct reason id** — verbatim+paraphrase repeat merged into `l2_task_context_stale_repeat` (2026-07-03), but duplicate-insert is deliberately NOT folded in: it's excluded from `firing.py`'s `SOFT_REJECT_IDS`, so a sole breach force-triggers L3 while a sole stale-repeat doesn't. Merging it would change escalation behavior.
- **`search_point_scorer.py::score_search_point` 3rd return slot** — not an "always-False" bool; it's a live `EscalationSignal | None` consumed by `l1/score/candidate.py` (drives candidate elimination). (Was a stale debt claim.)
- **`QueryNodeSpan.usage_details` (via `langfuse_sink`)** — read and forwarded to the Langfuse cloud observation. Not dead. (Stale claim.)
- **`webapp` `hit_rate` cell + `headline-stats.ts::fitnessTrend`** — fold already-served values (per-dot `hit` booleans; cumulative max over served `composite_fitness`); neither reimplements a scorer, so serving them would add wire coupling for identical behaviour (not R-36).
- **`presentation/views/live/phase.py` recall@k recompute** — the round-stats block recomputes top-1/top-5 recall via `find_rank`/`get_ranked_items` (terminal live readout only; `round_analysis._rank_analysis` owns the equivalent buckets app-side). Serving recall on `RoundResult` would add wire coupling for display-only behaviour — same call as the `hit_rate` entry above, so NOT a forced R-36 fix. The bare `except: pass` that wrapped it WAS the real smell and was fixed (now logs at warning).
- **`RunCallbacks` ↔ `emit_*`** — two writer APIs by design; the "which do I use" rule is in [`../developer/adding-a-surface.md`](../developer/adding-a-surface.md) §1.
- **`from_disk_log`** — not a roundtrip shim; foreign fork-siblings + historical cycles have no live ledger, so the on-disk `index.json` is the only source. (Its round twin `from_disk_round` had zero callers and was deleted.)
- **`measurement_archive.py` `.get(…, default)` at `save()`** — looks dead (the production writer always sets the keys) but `save()` has direct test-fixture callers with partial dicts; live boundary guards.
- **`writers.py` `_load_p_best_trajectory` / `_fork_summary_from_index` / `_load_sibling_indices`, `axis.py::_collect`** — single-caller, but the caller is in the SAME file in every case; intra-file `_private` decomposition is not inter-file indirection.
- **Leader-lock-in mechanism** (`leader_lock_in` / `pobb_lock_in` / `pobb_lock_in_n_min` knobs + `PoBBConfig.lock_in` + the `LEADER_LOCKED` `EscalationTarget`/`CandidateOutcome` + the `abort:lock_in_off` lineage-overlay lens) — the config knobs default off and no committed campaign sets them, so it LOOKS like a dead mechanism, but the `LEADER_LOCKED` path is structurally LIVE: a domain escalation target, a candidate outcome, the mask/lineage-overlay `abort:lock_in_off` what-if lens (`FamilyTree.tsx` "No lock-in"), and exercised by `tests/test_numerics.py`. Deleting it removes a shipped analysis feature, not dead code. Investigated + KEPT. (The unreachable significance-gate beside it WAS deleted — it had no live surface.)
- **Check-in "ready" ≠ "mintable" for prompt template-vars** — `origin_readiness(draft)` gates columns/framing/answer-space but not whether the committed prompt carries each node's required `{{template vars}}`; that check lives only at mint (`config.py::configure_and_apply_pipeline`, `pipeline_config_invalid` 422). Surfacing it earlier (at the resolve turn) needs the live `GET /pipeline` schema threaded into the deliberately-I/O-free resolve path (`origin_readiness` is pure-over-draft; `resolve_origin_turn`/`POST /resolve-origin` carry no backend client + the draft no base_url). **Operator decided: keep the 422 backstop, don't add pre-mint backend I/O.** It's non-destructive (draft preserved, retry) and names the exact missing vars + fix; a bad origin never runs. Revisit only if check-in UX timing becomes a felt pain.
- **MLflow + Langfuse sinks** — the observability-nexus *core capability*: PromptPotter drops into a team's EXISTING local MLflow / cloud Langfuse instance (flip a flag / add `.env` creds). Off-by-default ≠ dead. See `docs/architecture.md` §0.5 Tracing. Do not propose for deletion.

- **`EVIDENCE_GROUNDING_FIELDS` = renderable injections + the `stall_exploration` sentinel** — the phantom `parent_panel`/`sibling_yield` citations (names that never rendered into L1's prompt, inviting fabricated citations) were excised 2026-07-02; every citable panel is now a same-named DispatchHub `@signal`. `escalation_panel.exploration_budget` still gates the `stall_exploration` escape hatch (`validators/l1_behavior.py`).

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
`raise` vs `HTTPException` for the same failure class — M-sized standardization);
(**`events.jsonl` naming drift — DONE.** The per-cycle ledger was renamed to
`.runtime/ledger.jsonl`; `adr/0003-spend-and-tenancy.md` was the last holdout and is
fixed — five occurrences, not the three filed, found by verifying per-occurrence
rather than trusting the entry. The *workspace*-level ledger
`projects/{tenant}/.workspace/events.jsonl` is a genuinely different, correctly-named
file and was correctly left alone — `jobs/spend.py:34` states that split outright.)

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
