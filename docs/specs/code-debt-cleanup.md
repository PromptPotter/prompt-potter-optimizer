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
shape was the old bloat source; readiness buckets replaced it (2026-06-19).

> **Verify before trusting an entry.** This doc decays — claims drift as the code
> moves under them. A 2026-06-19 audit found several long-standing entries were
> stale or outright wrong (a "dead" field that mlflow reads; an "always-False
> return slot" that's a live signal). Re-confirm call sites before acting; if an
> entry is wrong, fix or drop it as part of the work.

## Ready — no blocker, pick up cold

- **Campaign-from-origin Phase 2 — additive consumer layer** (backend mint seam shipped: `POST /commands/mint-campaign {origin_override}` already starts a fresh campaign from a chosen prior origin). Remaining: a `GET /origins` derived read over `list_campaigns()` (dedup by `Campaign.root_content_hash`; 3-hop to `session_state.origin_prompt_fields` for the payload) + the New-Campaign / `IngestPane` origin picker that POSTs it. (Forward-feature-ish — tracked in CHANGELOG 0.8.3 as "Origin-picker UI"; kept here until it lands.)
- `webapp` `forkReconcileDefaults` / `LimitReconcile` — freeze spend/round "remaining" via `useState(() => …)` at mount while the parent keeps polling, so a long edit session shows mount-time values. Latent staleness seam, intentional (avoids clobbering typed values) but undocumented. Add a one-line comment affirming the snapshot is deliberate, or recompute-on-reopen.
- **JSON-read sweep** — ~36 hand-rolled `json.loads(path.read_text())` across ~24 files (mostly `presentation/api/routers/*`) → route through the existing `read_json` / `read_json_tolerant` (`store/base.py`); drops redundant `.exists()` guards + try/except. Mechanical line-shaving (rides an existing channel, no new concept) — fold per-file in small commits.

## Blocked — named blocker

**Live L1 round (operator-gated):**
- **`*_override → *_updates` L1 delta-key rename** (+ webapp searchpoint-projection collapse). `prompt_fields_override` / `task_context_override` / `pipeline_params_override` / `pp_override` are merges, not replacements, but named "override." **Decision (settle first):** unify the pipeline delta to the glossary word **`pipeline_overlay`** everywhere (kills the short/long two-name tax); the prompt/context deltas become `*_updates`. Rename writer→reader in one commit (schema `dispatch/schemas.py::L1Variant` is the source of truth — the LLM contract auto-propagates), collapse the two webapp readers (`searchPoint.ts` + `candidateSearchPoint.ts`) into one `wireToCandidateSearchPoint(wire)` helper. Full site map: grep `*_override`. **Blocker:** invalidates on-disk cycles (round-file key + optimizer structured-output contract) — verify against a FRESH cycle that completes round 1, not a resume.

**Operator decision:**
- `application/optimization/dispatch/schemas.py::CheckinOutput.consultation` — write-only field, never read (only the rotted notebook references it). Blocker: `extra="forbid"` means the field + its auto-generated counterpart in `datasets/_optimizer/pipeline.json` must drop atomically; verify the schema regen + re-gate.
- `infrastructure/identity/provider_config.py::ProviderIdentity.email_verified` — always `True` on Google/GitHub tokens, reads as dead, but it's a security-adjacent claim. Delete only if confirmed no future OIDC provider sends `False`.
- `shared/composite.py:204::legend: str | None = None` — never overridden (zero non-`None` callers); the body does use it (appends a 4th line). Drop the param if the feature is dead, or add a caller.
- Per-provider `*_RPM` / `*_TPM` (8 `Settings` fields, all default-`None`, never set) — collapse to one rate-limit map rather than delete (the wiring is real for a free-tier operator). Small design call.
- `CampaignConfig.dataset_split` (display-only, one dataset sets it) + `origin_gate` (never varied off `"strict"`) — lower-confidence knob removals. Verify no operator reliance first.

**Security posture / migration:**
- **Benchmarks: dev-surface-only, hidden from the distributed end-user app.** Bundled benchmarks (`bbeh`, `aime_2025`, `gsm8k`, …) must stay on dev surfaces (CLI / `SKILL.md` / folder-UI / python entrypoint) but be invisible to the distributed webapp's default identity. The seam exists — `GET /datasets` tiers `yours`/`benchmark`/`demo`, the `benchmark` tier gated on `datasets.benchmarks.read`. Verify + lock: (1) the distributed-app default identity does NOT hold that capability (and `demo_mode_enabled=false`); (2) no webapp path leaks a benchmark past the API gate (esp. prefill-draft-from-benchmark, the IngestPane list); (3) CLI/dev surfaces stay unscoped; (4) add an R-15 leak check to `tests/test_security.py`. Blocker: operator confirms the default-identity capability set + whether `demo` also hides. Ties ADR-0002.
- **Backend-registration dedup** — `webapp/lib/hooks/useConnector.ts` client-side `distinct`/`seenEndpoints` collapse is a back-compat shim for per-dataset `BackendConnection` rows minted before the `wiring.py` one-row-per-`(base_url, backend_type)` fix. NOT a row-delete: it's a 3-step migration — (1) rewrite each campaign's `campaign.json::backend_id` to the canonical `local` (8 stale ids across 82 campaigns, all → the same `127.0.0.1:8000` endpoint); (2) collapse the duplicate rows (needs a new `BackendStore.remove`); (3) make every re-wire path reuse the canonical id. Then delete the loop. Also: `wiring.py` `not backend_id` reuse block should guard on `existing.base_url == backend_url` (mint a distinct id on mismatch). Blocker: write + operator-run the idempotent migration on their data first — the loop is load-bearing until then.

**Origin check-in soundness:**
- **Check-in "ready" ≠ "mintable" for prompt template-vars** — `origin_readiness(draft)` (`application/datasets/origin_readiness.py`) gates on columns / framing / answer-space but NOT on whether the committed prompt carries each prompt-bearing node's required `{{template vars}}`. That check lives only at mint (`application/config.py::configure_and_apply_pipeline`, now raising `pipeline_config_invalid` 422). So a resolver that authors a prompt missing a required placeholder passes the gate and fails at Start, not at check-in. Clean fix: factor config.py's per-node `missing_template_vars` loop into one pure helper `prompt_template_gaps(schema, prompt_by_node) -> list[FieldGap]`; call it from BOTH config.py (raise — the mint backstop) AND `origin_resolve.py::resolve_origin_turn` (fold into the resolution block's gaps so the ready panel shows it). One validator, two seams — NOT a second validator (that's the trap to avoid). **Blocker:** the check needs the live `GET /pipeline` schema (declared `template_variables` per node); `origin_readiness` is pure-over-draft by design and `resolve_origin_turn` has no schema today — needs a lightweight `client.fetch_pipeline()` in the resolve path (the backend is already required to mint). Lower urgency post-TermNorm-contract-fix (fresh uploads default to single-node `llm_only`, which now declares no vars; benchmarks ship authored prompts) — the 422 backstop is non-destructive (draft preserved, retry).

**Verify-behavior (not a blind change):**
- `application/optimization/validators/l2_output.py` dual-arm dict-fallback (`getattr(entry, …) or (entry.get(…) if isinstance(entry, dict) else None)`) on `L1SupplementalRule`/`L1SituationalExample` — dict-arm appears dead (`_parse_l2` always passes typed models). Blocker: trace ALL callers of `run_l2_output_validators` for a direct-dict invocation (safety-critical path) before dropping.
- `webapp/app/styles/index.css:34-40` — `foundation/reduced-motion.css` imports before five domain files, so their motion rules win over reduced-motion's suppression (a11y gap vs `webapp/CLAUDE.md § Stylesheet`'s "tail files win" rule). Move the five domain `@import`s above line 34. Blocker: cascade-behavior change — verify against the light/dark + reduced-motion harness.
- **Winner-artifact provenance cluster** — `runner/entry.py` pairs the BEST round's `pipeline_params` with `cycle.opt_sp`'s prompt fields, but `opt_sp` is overwritten to the LAST round by `absorb_round`, so when best≠last the operator-facing winner reports mismatched prompt+params; the resume best-round loop in `cycle.py` repeats the mismatch; and `entry.py`'s `final_block` never serializes `winner_prompt_fields`/`winner_pipeline_params`, so `output/writers.py` (log.md FinalWinnerView) renders empty and `pobb/elevation.py` skips every cycle. Fix: read both winner fields from `best_sp` (already populated from the best round by `to_job_search_point`); set per-round prompts before capturing `best_sp` on resume; add both keys to `final_block`. **Blocker:** Explore-agent-surfaced, NOT hand-verified this pass — re-confirm line-by-line vs HEAD before touching (it's a correctness path).

**Cross-repo (TermNorm sibling at `OfficeAddinApps/TermNorm-excel/backend-api`):**
- **TermNorm wire `model`** — backend `spend.backend.model` reports a provider slug (`"openrouter"`), not the upstream model, so backend $ can't be derived from `lookup_rate(model)×tokens`. Add `model` to the per-request response + a `/version` endpoint; this repo then bumps `termnorm.py::_EXPECTED_REVISION` and deletes the `_synth_legacy_backend_record` back-fill in `routers/auth.py`. (The connector revision-pin already exists.)
- **Backend fix isn't observable without clearing a cache** — PP's measurement cache + TermNorm's `match_database` both key on query/searchpoint, never on backend code/revision, so a co-owned backend fix replays stale results. Fold the connector revision-pin into the measurement-cache key (or add a `--fresh` flag); confirm the TermNorm `/matches` short-circuit only fires on `verified` aliases. Workaround: clear `archive/{measurements,dataset_runs}/`.

## Standing — long-lived design holds

- **HITL notebook (`notebooks/optimization_campaign.ipynb`) rotted** against the orchestration API (un-gated, drifted across the ingest/origin unify): wrong tuple arity from `prepare_origin_notebook`, imports a deleted module, treats `CampaignOrigin` as a round list. Rewrite the three cells against the current `notebook_run.py` contract, or retire the notebook (operator's call). Retiring also clears `application/origin.py::DatasetSummary.splits` + `domain/search_point.py::TaskDecomposition.FIELDS` (read only by the notebook). Blocker: needs a live TermNorm backend to verify — dedicated session, not a blind edit.
- **Optimizer model unreliable on heavy L2/L3 structured output** — `openrouter/gpt-oss-120b` (all optimizer nodes) is slow + schema-noncompliant on the large `L3PlanOutput`/`L2*` shapes, firing the repair retry and sometimes failing it. Swapping it is a per-node overlay edit (`datasets/_optimizer/pipeline.json::nodes.{l2_context,l3_plan}.config.model`), not service code — operator picks a faster/schema-reliable model, or shrink the schema. Needs a live cycle reaching L3 to measure repair-rate.
- **`RunPhase.STOPPING` thin window for non-paused stops** — declared only at the runner's cooperative checkpoints, so a running stop near a round boundary jumps `running → terminal(interrupted)` with no `stopping` frame. Have `_apply_stop_cycle` (the command applier that writes `stop.flag`) append a `control` `PhaseRecord(event="stopping")` so the projection fires at the instant of intent; the three in-runner `declare_run_phase(STOPPING)` then become redundant. Blocker: confirm the applier runs in-process with the runner's `LiveDashboardView` subscriber; verify the CLI Ctrl+C path keeps its no-`stopping` design.
- `infrastructure/tracing/replay.py` — `schema` param accepted but not threaded through; dead branches inside. File for a dedicated tracing-cleanup pass.

## Considered, not debt — don't re-open

- **`search_point_scorer.py::score_search_point` 3rd return slot** — not an "always-False" bool; it's a live `EscalationSignal | None` consumed by `l1/score/candidate.py` (drives candidate elimination). (Was a stale debt claim; verified 2026-06-19.)
- **`measurement_archive.py::register_alias(*hashes)`** — the variadic isn't dead; the sole caller passes both hashes and the function needs ≥2. (Stale claim; verified 2026-06-19.)
- **`QueryNodeSpan.usage_details` (via `langfuse_sink`)** — read and forwarded to the Langfuse cloud observation. Not dead. (Stale claim; verified 2026-06-19.)
- **`webapp` `hit_rate` cell + `headline-stats.ts::fitnessTrend`** — fold already-served values (per-dot `hit` booleans; cumulative max over served `composite_fitness`); neither reimplements a scorer, so serving them would add wire coupling for identical behaviour (not R-36).
- **`RunCallbacks` ↔ `emit_*`** — two writer APIs by design; the "which do I use" rule is in [`../developer/adding-a-surface.md`](../developer/adding-a-surface.md) §1.
- **`from_disk_round` / `from_disk_log`** — not a roundtrip shim; foreign fork-siblings + historical cycles have no live ledger, so on-disk `round_NNNN.json` is the only source.
- **`measurement_archive.py` `.get(…, default)` at `save()`** — looks dead (the production writer always sets the keys) but `save()` has direct test-fixture callers with partial dicts; live boundary guards.
- **`writers.py` `_load_p_best_trajectory` / `_fork_summary_from_index` / `_load_sibling_indices`, `axis.py::_collect`** — single-caller, but the caller is in the SAME file in every case; intra-file `_private` decomposition is not inter-file indirection. (Verified 2026-06-19.)
- **Leader-lock-in mechanism** (`leader_lock_in` / `pobb_lock_in` / `pobb_lock_in_n_min` knobs + `PoBBConfig.lock_in` + the `LEADER_LOCKED` `EscalationTarget`/`CandidateOutcome` + the `abort:lock_in_off` lineage-overlay lens) — the config knobs default off and no committed campaign sets them, so it LOOKS like a dead mechanism, but the `LEADER_LOCKED` path is structurally LIVE: a domain escalation target, a candidate outcome, the mask/lineage-overlay `abort:lock_in_off` what-if lens (`FamilyTree.tsx` "No lock-in"), and exercised by `tests/test_numerics.py`. Deleting it removes a shipped analysis feature, not dead code. Investigated + KEPT 2026-06-19. (The unreachable significance-gate beside it WAS deleted — it had no live surface.)
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

**Anti-patterns — NOT debt, skip on sight:** M13+ intentional UI placeholders
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

## History

All prior tiers + the pre-public-release polish arc (Tiers 0–6 + polish A–E +
audits 1–3) closed by 2026-05-25; the chronological per-sweep "holds" sections
(2026-06-11 … 06-17) were folded into the readiness buckets above on 2026-06-19.
Recover any pruned detail via `git log`.
