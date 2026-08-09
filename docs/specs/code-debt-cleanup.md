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

**From the 2026-08-06 JustLogic model bake-off.** Each carries its measurement; the four
root fixes that arc DID land (reasoning-token share on the ledger, provider-aware pricing,
`answer_modal_share`, the `reasoning_only_response` arm) are in `git log`, not here.

- **`reasoning_effort` is not a lever on `deepseek-v4-flash`, and the optimizer owns a third
  of every L4 cell.** Measured across 292 optimizer calls: `l1_generate` (effort `medium`)
  53 s / 5352 output tokens median, `l1_critique` (effort **`low`**) 45 s / 4206, `l2_context`
  23 s / 2157. One `l1_critique` round-trip billed 4790 completion tokens for a 1044-character
  answer — **~94% reasoning, against a schema that caps the answer at ~1300 characters** — so a
  two-step effort change buys ~27% and the knob is not the lever. `_MODEL_PROFILES` already
  says this model "emits ~4k reasoning tokens before content"; what it advises (pair with
  `reasoning_effort=low`) is what `l1_critique` was already doing. The real options are a
  different optimizer model for the schema-bearing nodes or an explicit reasoning budget, and
  both are choices, not cleanups. **Blocker: read the share off a live run first** — it is on
  the ledger and the CLI call line as of this arc, and picking a model on the strength of a
  one-cell reading is the mistake the bake-off itself documented. Its cheaper twin is already
  the plan: `l1_generate` semantic widening at a net-SHORTER prompt.

- **No structured-output route probe before a run spends money.** `inclusionai/ling-3.0-flash`
  answered HTTP 405 `json_schema response format is not supported` (DeepInfra);
  `z-ai/glm-4.7-flash` returned empty content + `finish_reason=stop` + 5352 reasoning chars,
  burned a schema-repair re-prompt (~2x cost and latency), then ReadTimeout'd on both routes.
  Both are decidable in one call, and both were discovered by paying for a screen.
  `config.py::run_preflight_checks` grades budget/lives/couplings and
  `check_model_reasoning_floors` hard-blocks a too-low `max_tokens`, but nothing asks whether
  the route implements `response_format` — the failure the `&nitro_probe` anchor in
  `assets/optimizer/pipeline.yaml` warns about **in prose**, whose whole point is that it does
  not error. Same shape: swapping a model means hand-editing two lines of a dataset's
  `pipeline.yaml` (`nodes.*.config.model` + an `available_models` entry) and remembering to
  revert both, and a leaked pin silently mislabels the next run. **Blocker: a probe and a
  swap-verb are both new capabilities, and the closing directive opens no new features until
  the config is distributable.**

- **The bundled rate table decays silently, and nothing schedules a refresh.**
  `openrouter/openai/gpt-oss-20b` reads $0.02/$0.10 against a live $0.030/$0.130. `refresh_rates`
  exists and clears the `load_rates` cache; no caller runs it and no surface reports the
  table's age, so the floor `load_rates` falls back to is quietly whatever shipped. Now more
  visible, not less: since pricing became provider-aware, a model the table lacks under its
  real provider resolves to `None` rather than to another vendor's number — correct, and it
  makes `unpriced_tokens` the load-bearing signal. Pairs with the **already-filed** "daily cap
  can't see unpriced spend" entry below, which is what turns that signal into a gate the
  operator can trust.

- ⚠️ **`shared/identity.py` (the capability/tier authz vocabulary) collides with
  `domain/identity.py`** — flagged only, never acted on: the access model is the operator's call
  ([`../operations/access-model.md`](../operations/access-model.md)). The rest of the
  filler-name sweep is closed; see § Considered, not debt for the four names that keep theirs.

- **One unregistered hook script** — `.claude/hooks/detect_correction.py`, unregistered in all
  four settings files and instructing a write to `.claude/skills/potter-dev/rules.md`, a
  directory that no longer exists. Dead script, not a broken live hook. **Its filed twin
  `doc_drift_ratio.py` is LIVE** — registered as a `Stop` hook in `.claude/settings.json` and
  observed firing; the old entry claimed neither was registered, which would have invited
  deleting a working hook. ⚠️ `.claude/` is operator-curated: the deletion is theirs to make.

- **`datasets/bbeh/sweep/*.yaml` now PARSE but would mint 12 identical no-op forks.** The
  filed entry said the blocker was a stale `brief` key; that was one of three, and fixing it
  does not revive the verb. All 12 also carried `l1_section_overrides` / `_text`, and **none
  of the 12 carries `l1_layout`** — the only lever `OperatorSweepFile` still has. So a
  `--sweep-batch` run over them mints twelve forks identical to their parent and pays full
  measurement for zero contrast. The three dead keys were stripped (operator-approved) so the
  reader no longer raises; the payloads' `reason` fields are untouched and still carry the
  measurement narratives that make them worth keeping (D001's +50pp persona, D002's +21pp
  answer_format). Two of the removed levers are gone for structural reasons, not drift:
  `l1_section_overrides` named sections (`axes_l1`) that are no longer panel names — the
  modern lever IS `l1_layout` — and `l1_section_overrides_text` wrote `task_context`, which
  is now frozen (`TaskDecomposition.merge` refuses it). **Action before anyone runs the verb:
  author a real `l1_layout` per arm, or the batch measures nothing.** The mechanism itself is
  sound and was exercised through the real reader (12/12).

- **`index.json` is the only payload a model could reach — measured 2026-08-05, and the answer is "not yet".** Every other `dict[str, Any]` in the read path is the node-keyed overlay whose keys the BACKEND invents at runtime, so no static model can name them; this one is PP-owned and statically knowable. **43 files, 24 top-level keys, 0 unreadable** — against ~120–160 lines of model, ~25 files, ~65 read sites (**net +60 to +100 LOC**) to close the misspelled-key class on 3 `update()` sites plus `create()`. Two reasons it is not a refactor: the ledger scores it **zero** (`any_params` excludes container values, `models_lax` moves ≤1), and `extra="forbid"` breaks the deliberately tolerant reads in `enumerate_cycles` / the lineage surveys. Action: **none — the numbers are the deliverable**; the `domain_any_maps` dimension is what will make it decidable. Re-verify 43/24/0 first. Blocker: none, it is a recorded decision.
- **Two `CycleHop` types in the webapp, and the path codec is prose-enforced.** `lib/api/types.generated.ts` carries the generated one (`campaign_id`/`cycle_id`, emitted from `domain/cycle_paths.py` by `scripts/build_ts_types.py`); `lib/ids.ts` declares a second, local, camelCase one behind its own `CyclePath`. They are not assignable, which reads as a bug the first time someone tries. `encodeCyclePath`/`decodeCyclePath` likewise re-implement `encode_cycle_path` with no generated counterpart — the Python docstring asserts "the same codec" and nothing checks it. Verified 2026-08-05: separators (`::`, `~`) and the id regex still agree, so **no live drift** — this is where `<entry-point-parity>` rests on a comment, not a break. Action: fold the camelCase hop into the generated type, then consider generating the codec (`openapi.generated.json` already ships). Not now — the webapp is stable. Blocker: none, but it is a webapp-wide rename.

**Ray / lineage follow-ups** (named during the 2026-07-26 time-ray landing; none blocks the feature):

- **`lib/hooks/usePoll.ts` — head-of-line blocking.** One in-flight tick skips the whole next tick, and the lineage tick `Promise.all`s every subscribed key — one slow campaign's fetch delays every other key. Action: per-key in-flight accounting (a `usePoll` design change, not a caller patch).
- **`/ray` payload size.** A `limit`-sized window carries full `llm_call` payloads — multi-MB on a chatty course. Action: per-kind field allowlist in `store/family_ray_views.py`, coordinated with what `lib/chat/activity.ts::projectionToActivity` actually reads (an uncoordinated elision silently blanks activity lines).
- **`/ray` non-304 cost while a run is live.** Every append moves the family validator, so each 5 s tick re-parses the whole merged window. Fine at current sizes; revisit with a per-ledger byte cursor if it shows in profiles.
- **`/tree` always serves the whole subtree** (recursion to every fork + inner run) though collapsed sidebar rows render one tier. Action: only if tree size shows up — a `depth=` param is contract surface, don't add speculatively.
- **View-memory hydration order.** Today's restore sites read the store during render (`useSyncExternalStore`), which is safe; a future consumer restoring from an effect could seed from the pre-hydration empty store and record it back. Guard belongs in `lib/view-memory.tsx` if a second effect-time reader ever appears.
- **Weak-ETag identity folding.** `_conditional.py::weak_etag` folds the lens/samples mask (request identity) into the validator (resource state). Correct today because nothing caches by URL alone; a URL-keyed cache layer would cross-serve variants. Split validator vs `Vary`-style identity if one ever appears.
- **`useNodeToggle` has no Vitest** — the registry got its test (`lineage-registry.test.ts`); the toggle codec's defaults-aware `isOpen`/`toggle` roundtrip (toggled = deviation from per-kind default) still rides smoke only.

**Do soon, not now** (surfaced by the 2026-07-10 drift pass; the six fields with *no* reader at all were already deleted):

- **The daily cap can't see unpriced spend** — `jobs/spend.py::record_cost_usd` resolves an unpriced model (no wire `cost_usd`, no rate-table entry) to `0.0`, so `effective_spend_cap_usd` under-counts and the gate fails open — the same shape as the `except OSError: continue` fail-open that function's docstring already records. The dashboard handles this case by counting `unpriced_tokens` and arming a "USD cap inactive" warning (`live_dashboard/view.py`); the quota path has no channel to say so. Action: carry the unpriced-token residue out of `sum_user_spend` and surface it on `QuotaStatus`, mirroring the dashboard's split. Blocker: none — it needs a `/auth/quota-status` field + the Account pane reading it, so it's an entry-point-parity slice, not a pure fix.

- **`export`s re-exported through `export *` barrels** — `lib/api`, `lib/types`, `lib/derivations`, `components/ui`, `components/workflow` (all five still live; the 37 genuinely file-local ones already landed). They look local-only to a naive grep; stripping `export` silently narrows each barrel's public surface. Action: decide per barrel whether the symbol is meant to be public, then strip or keep — don't script it blind. **The old "52" figure is retired (2026-07-16): every barrel has been touched by feature work since it was counted, and the barrels are load-bearing (85+/34+/32+ importers). Recount before acting; don't re-cite a headcount as current.**
- **A salvaged Groq response reports ZERO tokens.** `infrastructure/llm/json_parse.py::try_groq_json_validate_repair` rebuilds an `LLMResponse` after re-parsing `failed_generation` out of a `json_validate_failed` 400, hardcoding `usage={"prompt_tokens": 0, "completion_tokens": 0}`. That call **already reached the wire and was billed** — the model burned tokens producing the malformed JSON. It IS metered (every returning optimizer round-trip passes `call.py`'s single exit), so this is not a seam that forgot; it is a seam that meters a **fabricated zero**, which is worse, because a zero is what a replayed call legitimately reports. **Why it is not a one-line fix:** Groq's 400 body carries no `usage`, so the true counts are unrecoverable, and `unpriced_tokens` is the WRONG home — it means *billed tokens with no USD rate* (count known, price unknown), whereas here the **count itself** is unknown. There is no "tokens unknown" representation on `TokenUsageRecord`, and adding one is the four-shape edit the spend-accounting entry above describes. Do NOT estimate from content length — a fabricated number rendered as a measurement is the one thing this must never do. **Currently dormant** (`json_validate_failed` is Groq-only; every configured provider is `openrouter`), so it is a correctness landmine, not a live leak — it fires the day anyone repoints a node at Groq. Blocker: needs the unknown-count dimension.
- **Post-flip copilot — deferred on purpose, not forgotten.** The `checkin` node consulting in run mode and raising `pause-cycle` / `change-spend-budget` / `fork-cycle` instead of draft patches. `RaisedCommand` (`datasets/origin_resolve.py`) is already general enough to carry it. Not debt and not now: it is a **new feature**, and the closing-phase directive is no new features until `promptpotter-self` is distributable. Lands with L4, so it belongs to [`l4-outer-loop.md`](l4-outer-loop.md) when it does.
- **`RoundResult.results` holds two different subjects, and the one it drops is the only copy.** When a round elects a winner, `l1/score/winner.py` overwrites `best_results` with that candidate's rows — verifiable on disk: in a round with a winner, `results` is byte-identical to `all_candidate_results[winner_id]`, and in a round with none it matches no candidate because it is the parent's re-score on that round's own subset. So the field is a **duplicate** of a per-candidate list in the good case, and in every case where a winner IS elected the **parent's per-sample panel on that round's subset is never persisted** — the panel `matched_origin_stats` computed the served `matched_origin_accuracy` from. Consequence downstream: the webapp's flipped-rows line can only join against round 0's C0 panel, so under `per_round_resubset` it goes silent on exactly the campaigns it is for, and the run card ends up naming a different reference than its own percent pair (`frontend-surface-contract.md` § run_card.flips states the labelling that holds until this lands). Action: keep `results` the PARENT's rows unconditionally and let the winner be read from `all_candidate_results`, which already holds it. Blocker: none in principle — it is one assignment plus every reader that assumed "the round's rows" meant the winner's, and it changes a persisted document, so it wants its own PR.
- **`matched_origin_*` is named for the origin and computed from the parent.** `matched_origin_stats(origin_results=…)` is called with `RoundParent.results`, and `RoundParent` is "the origin only at round 0; the prior winner after it" — so `matched_origin_accuracy` / `matched_origin_composite` / the round-level twins are the PARENT's rate on the candidate's rows at every round ≥ 2. The two coincide at round 1, which is why the name survived. A name that stopped describing its contents; the repo already owns the right word (`parent`, per root `CLAUDE.md`). Action: rename to `matched_parent_*` through the domain model, the round document, `RoundSummaryCandidate`, the served OpenAPI surface and the webapp field — one sweep, no compat alias. Blocker: it touches a persisted document and a generated client surface, so it lands with the entry above rather than beside it.
- **The hard-sample ORDER knob has no browser control yet.** `CampaignConfig.hard_sample_order` is honoured by `/preview` + `/measurement-series` (`?order=` overrides per read), by the `log.md` leaderboard, and per-dataset in `campaign.yaml` — but the hard-samples pane has no select, so from the browser the operator can only read the order, never pick one. An `<entry-point-parity>` gap, not a wrong number: every surface agrees on what it shows. Action: thread `order` beside `hardSamplesScope` (AppShell owns it → `ChatPane` → **both** consumers, `HardSamplesHeatmap` and the run card's `HardSamplesPreview`) and add it to `useDatasetPreview`'s slice key, which is a third dimension on `(unit, scope)`. **Blocker: none.** The concurrent arc it was held back for has landed — `useDatasetPreview` now fetches one `(unit, scope)` slice at a time, so the third dimension slots into an existing key rather than a rewrite, and the second consumer is the reason to thread it through `ChatPane` rather than into one pane.

## Blocked — named blocker

**Behavior change (needs explicit sign-off, not a blind swap):**
- **~~`dash.state` phase vocabulary hand-mirrored twice TS-side~~ — the `activeNodeId` half LANDED 2026-08-08.** The backend serves `current_round.active_node`, resolved over a `_STATE_TO_NODE` map that is TOTAL over `DashboardState` with an import-time exhaustiveness raise; `activeNodeId` and its five call sites are deleted, along with `TargetPipelineHero`'s `BACKEND_ACTIVE_PHASES`, a sixth spelling of the same predicate. `PHASE_PAUSE_LABEL` stays frontend — it is UI copy (VOICE.md) and genuinely belongs there.
  **This entry's own judgment cost the most.** It predicted the symptom exactly — *"canvas node stops pulsing, silently (no compile error)"* — then priced the fix as "purely additive safety. Low value." It was not latent (the operator hit it as a recurring fade at phase interfaces and kept patching the symptom site) and it was not additive (four client-side joins came out). **The lesson is the `<root-fix>` one: "additive" was measured on the seam being touched rather than on the deletions it unlocks, and a "not debt / low value" verdict is precisely why nobody looked again.**
- `webapp lib/poll.tsx` local `revalCount`/`setRevalCount` vs the global `lib/revalidate.ts::useRevalidation()` bus — the dashboard poll uses the local counter, so it does **not** re-tick on a mutation's `bumpRevalidation()`. The filed "just swap to `useRevalidation()`" is WRONG: verified `usePoll`'s interval effect (`lib/hooks/usePoll.ts`) deps `[intervalMs,pauseWhenHidden,tickOnFocus,enabled,runTick]` with `runTick` stable (`useCallback([])`), so on a unit switch (`enabled` unchanged) it does NOT restart/re-tick — the local `revalCount` bump is the ONLY immediate-tick trigger on campaign switch. Substituting the global bus would lose that. Real fix = feed BOTH signals (e.g. `revalidateOn: revalCount + globalReval`), which ADDS the mutation-tick behavior rather than removing a concept. Blocker: this is a behavior change (adds a trigger), not a subtractive cleanup — needs the light/dark + reduced-motion-style browser verification pass, not a blind edit.

**Behavior change (needs explicit sign-off, not a blind swap) — scoring:**
- **All-errored candidate scores `accuracy = 0.0`, not the honest `None`** — `compute_accuracy` (evaluators.py) returns 0.0 when no scoreable row exists; for all-deprecated that IS the verdict, but for all-errored it fabricates one (declared stage-1 tolerance in its docstring, 2026-07-13). The honest `None` must propagate: `ScoredCandidate.accuracy`/`RoundResult.accuracy` → `float | None`, `compute_composite_fitness` handling a missing `accuracy` term without `ScoringTermMissingError` in `_running_scores` (an "unscoreable candidate" state, the outer sibling of `InnerCycleUnscoreableError`), `display_fitness` double-None, dashboard + `types.generated.ts` + chart null handling, `best_round_by_measured_accuracy`/`_apply_best` null-safety. Blocker: wide Optional-propagation across the served surface for a state PoBB DegradationCheck usually eliminates mid-round anyway; needs its own pass. **Smaller than filed:** its sibling `rescore_results` stamps errored rows `fitness = 0.0` citing `compute_accuracy`, which actually EXCLUDES them, and `_mean_fitness_by_cell` reads an absent key identically — so no cited reader depends on the stamp. Start from the audit that implies: every unguarded `r["fitness"]` subscript. Note the stamp makes a row's shape depend on replay (a freshly measured error row has no `fitness` key at all).

**Live L1 round (operator-gated):**
- **`*_override → *_updates` L1 delta-key rename.** `prompt_fields_override` / `task_context_override` / `pipeline_params_override` / `pp_override` are merges, not replacements, but named "override." **Decision (settle first):** unify the pipeline delta to the glossary word **`pipeline_overlay`** everywhere (kills the short/long two-name tax); the prompt/context deltas become `*_updates`. Rename writer→reader in one commit (schema `dispatch/schemas.py::L1Variant` is the source of truth — the LLM contract auto-propagates). Full site map: grep `*_override`. **The old rider "collapse `searchPoint.ts` + `candidateSearchPoint.ts` into one `wireToCandidateSearchPoint`" is DROPPED (verified 2026-07-16):** they answer different questions over one served field — `ObserveConfig` is a read-only view (keeps `steps`, carries a label), `CandidateSearchPoint` is an editable fork seed (drops `steps`). `lib/derivations/searchPoint.ts`'s module comment says so in-code. Merging would fuse a read projection with a write seed; the only real overlap is two lines of `candidate_scores.find`. **Blocker:** invalidates on-disk cycles (round-file key + optimizer structured-output contract) — verify against a FRESH cycle that completes round 1, not a resume.

**Security posture / migration:**
- **The forbid-by-default flip obliges every on-disk model — now discharged by a table, and the table is what to check.** `application/restamp.py::_SURFACES` is the coverage contract: `CampaignConfig` (twice — the minted snapshot's config and the dataset template), `Campaign`, `BackendConnection`, `User`, `DiagnosticRunRecord`. **Adding a persisted `StrictModel` without adding its row is the bug**, and it has already cost one outage (`5a69ca67` dropped `BackendConnection.last_synced_at`; `init_services` raised `extra_forbidden` and every `new`/`resume` died at load). Two kinds are outside it *by design*, stated in the module docstring so the absence reads as a decision: `RoundResult` is the one `extra="ignore"` on-disk model (a stale key must never make a paid measurement unreadable), and `LLMResponse` under `archive/optimizer_calls/` is a content-addressed cache — evictable, not migratable. A third is outside it for a different reason: the per-cycle **ledger** is migrated by `compact_cycle_ledgers`, a sibling function on the same verb rather than a row, because a row prunes a `StrictModel` by `model_fields` and a ledger payload is `dict[str, Any]`. Read the table as covering the typed documents, not everything the verb touches. **Open, and the operator's call:** 3 of the 9 public round files fail `RoundResult.model_validate`, all three in `promptpotter-self__af6252/cycle_277352830c98`, and on TWO causes rather than the one filed (re-measured 2026-08-06). *Extra* `health.ci_lo`/`ci_hi` a re-stamp could drop; and a *missing* `diagnostics.samples[].fitness`, which it cannot — a dropped field leaves no default behind, so no repair reconstructs it. What that costs today: `verify`, `resume` and the `ab` replay all read that cycle through the sole typed round loader and raise; a `score:`/`abort:` lens reads the summary fields and still serves it. Blocker: none for the table; the 3 files are a keep-or-drop decision, not a fix.
- **Backend-registration dedup** — `webapp/lib/hooks/useConnector.ts` client-side `distinct`/`seenEndpoints` collapse is a back-compat shim for per-dataset `BackendConnection` rows minted before the `wiring.py` one-row-per-`(base_url, backend_type)` fix. NOT a row-delete: it's a 3-step migration — (1) rewrite each campaign's `campaign.yaml::backend_id` to the canonical `local` (8 stale ids across 82 campaigns, all → the same `127.0.0.1:8000` endpoint); (2) collapse the duplicate rows (needs a new `BackendStore.remove`); (3) make every re-wire path reuse the canonical id. Then delete the loop. Also: `wiring.py` `not backend_id` reuse block should guard on `existing.base_url == backend_url` (mint a distinct id on mismatch). Blocker: write + operator-run the idempotent migration on their data first — the loop is load-bearing until then.

**Cross-repo (TermNorm sibling at `OfficeAddinApps/TermNorm-excel/backend-api`):**
- **TermNorm wire `model`** — backend `spend.backend.model` reports a provider slug (`"openrouter"`), not the upstream model, so backend $ can't be derived from `lookup_rate(model)×tokens`. Add `model` to the per-request response + a `/version` endpoint; this repo then bumps `termnorm.py::_EXPECTED_REVISION`. (The connector revision-pin already exists; the old `auth.py` back-fill is already gone.)
  PromptPotter does not read that slug at all. `_compute_step_tokens` now stamps every step-token entry with the node's model — the backend's per-node `model` when it reports one, else the model the dataset overlay pinned (`pipeline.yaml::nodes.{n}.config.model`, which is mandatory for an LLM node). So per-node cost is derivable today, including for chars/4-estimated nodes, and the old `node_model or llm_provider` coalescing is gone. What remains genuinely owed on the TermNorm side is the **`/version` endpoint**; the per-request `model` is now a nicety (it wins over the overlay when present), not a blocker.
- **Backend fix isn't observable without clearing a cache** — PP's measurement cache + TermNorm's `match_database` both key on query/searchpoint, never on backend code/revision, so a co-owned backend fix replays stale results. Fold the connector revision-pin into the measurement-cache key (or add a `--fresh` flag); confirm the TermNorm `/matches` short-circuit only fires on `verified` aliases. Workaround: clear `archive/{measurements,dataset_runs}/`.

**Coupon + BYO build (Lane A2 — blocked on the build itself; ADR-0003 § Host coupon):**
- **Adopt-in-new-code for the coupon/BYO build:** the new `grant.json` / `api_keys.json` stores MUST ride `read_json_optional`/`write_json` (the `UserStore` template, `store/io.py`) from day one — don't add hand-rolled readers. `shared/spend.py` still hand-rolls `json.loads(...)` at three sites (one of them decoding a fetched payload — same pattern, not previously filed); held separately from the JSON-read sweep (SHIPPED elsewhere) because `shared/` importing `infrastructure/store/io` is an unresolved layer-direction question — resolve it before or alongside this build. (`store/io.py` is now safe to import from anywhere: the eager `store/__init__` that made any leaf import drag in `CampaignStore` is gone, so this is a layer-*direction* question only, no longer an import-cycle one.)
- **Two host-wallet mechanisms** — `application/jobs/quota.py::effective_spend_cap_usd` + `User.spend_budget_usd_daily` (recurring daily cap, mint-time snapshot) vs the new coupon (`grant.json`, ledger-derived, live). Two guards on one concern (host's wallet) = the no-redundant-mechanism rule. The snapshot is also concurrency-blind: two CLI mints while `daily_spent` is low each receive the full remainder and never re-aggregate mid-run, so combined spend can reach ~2× the daily cap (the capacity-1 `JobRegistry` slot doesn't bind — it's a per-process in-memory lock). Action: **delete the daily-cap path**; coupon-remaining becomes the single host ceiling, read by the per-cycle `BudgetGate` every tick (D1/D2 in ADR-0003), which also closes the concurrency hole. Blocker: lands *with* the coupon, not before — deleting first leaves the wallet unguarded.
- `domain/run_records.py::TokenUsageRecord` lacks `key_source` → `/auth/activity` `group_by=api_key` (`routers/auth.py`) fakes a *provider slug* as the key id. Once real `key_source: host|user` lands (declared on `TokenUsagePayload` in the asyncapi), replace the fake-slug derivation with the real dimension. Blocker: the coupon build adds the field.

## Standing — long-lived design holds

- **Holistic reframes — larger chunks, noted so they aren't mistaken for done; don't slip one into a release.** (1) The `ui/HoverCard` primitive rides ONE hover, while the native `title=` tooltips spread across the webapp are the same job — consolidate incrementally, alongside the three bespoke popovers vs `ui/Popover`. (2) Keep candidate-CI resolution one seam if a third whisker source ever appears (CLT default vs θ-band override). (3) **Never examined, and the one with real reach:** `promptpotter/application/optimization/CLAUDE.md` asserts L2/L3/L4 are "the same family — each mutates a slower-changing surface of the level below", yet each is built from scratch (L2/L3 are escalation strategies, L4 a connector recursion). Whether the family should share machinery has never been asked, only asserted. The L2↔L4 hunt found one real collision underneath it (`NodeLayoutSpec.editor` claimed two owners of `l1_generate`'s layout, fixed `d1d792b0`), so the assertion is load-bearing enough to be wrong in places.

- **`idea_fingerprint` cannot see a SEMANTIC re-proposal, and the gate built on it is the only
  cross-round one.** `domain/candidate_diff.py::idea_fingerprint` matches content-word overlap
  between mutated VALUES, so it catches a re-proposal only when the wording survives. Measured on
  the banked corpus: **0 of 15 real re-proposal pairs caught** — including the `justlogic-d234`
  case the gate was written for, one idea ("exhaust modus tollens before answering Uncertain")
  walking `instruction` → `thinking_style` → `output_schema_descriptions.reasoning` →
  `task_intent` across 8 rounds. So `repeat_variant` rejects a copy-paste and nothing else, and
  the generator can restate one hypothesis indefinitely while `l1_n_repeat` reads 0 — which is
  worse than no counter, because a 0 reads as hygiene. The docstring now says this (`d1d792b0`);
  the mechanism does not. **HELD, not deferred by accident:** every fix is a new mechanism
  (embeddings, or an LLM judge per pair), and the closing phase does not open new features. Its
  cheaper twin is already the plan — make candidates differ MORE (`l1_generate` semantic
  widening), which attacks the same failure from the generator side and is owed anyway for the
  panel's arm-resolution problem. Revisit only if widening lands and repeats persist.

- **Optimizer model repair-rate on heavy L2/L3 structured output — unmeasured.** Every optimizer node runs whatever `promptpotter/assets/optimizer/pipeline.yaml` pins, chosen for speed + schema obedience together. What is owed is the measurement: a live cycle reaching L3 to read the repair-retry rate under the *current* model — read the model off that file, never off this entry.

## Considered, not debt — don't re-open

- **`best_round` two bases (composite-argmax winner export vs cumulative-accuracy index/dashboard headline)** — operator-decided correct-by-design: composite is the optimizer's objective (winner export + L2/L3 stall comparator, which also compares θ), accuracy is the formula-independent headline. Documented in `_apply_best`'s docstring + `architecture.md` §0.5. Don't flip either basis to match the other.
- **`l2_duplicate_insert` / `l2_task_context_stale_repeat` are GONE — don't re-file either** — both were `task_context` checks, and that framing is frozen for the run (`TaskDecomposition.merge` refuses a rewrite), so neither breach is representable. Owner: [`../developer/l2-internals.md`](../developer/l2-internals.md) § Wound 4.
- **Benchmarks are NOT gated from the distributed app** — settled the other way by `20d17ea8`: repo `datasets/` is *install content* (tracked in git, readable by anyone who has the install), so the `datasets.benchmarks.read` capability + `PROMPTPOTTER_ADMIN=1` gate were deleted and the tier is now `yours`/`install`. The gate existed to hide one gitignored scratch cut, and its cost was a blank pipeline hero + hard-sample leaderboard on every benchmark campaign. A private cut belongs in the tenant, where path isolation already protects it. Don't re-file the old "hide benchmarks from the default identity" entry.
- **Display-only recomputes do NOT breach scoring authority** — `headline-stats.ts::fitnessTrend` folds already-served values, and `presentation/views/live/phase.py` recomputes recall@k for the terminal readout. Serving either would add wire coupling for identical behaviour. (The bare `except: pass` around the recall block WAS the real smell, and was fixed.) **`hit_rate` sat on this list and should not have:** a fold is display-only only where its result is REACHABLE, and `is_hit` is `fitness >= 1.0` — unreachable on a graded scorer, so the column printed `0/N` on every row of a healthy campaign while this entry vouched for it. Now served (`SampleSeries.n_hits` / `mean_fitness`) and renamed `mean_fitness`. Check a proposed exemption at its threshold before granting one.
- **Sample look-ahead is LIVE, and every part of it looks removable** — it defaults off, no committed campaign enables it, `ADMIN_CAPABILITIES` has exactly one member so the tier reads like scaffolding, and `_sample_lookahead_depth` returns 1 on `promptpotter-self` (the campaign most often read), so a reader concludes the branch never fires. It fires on every remote-HTTP dataset the moment the operator presses the button. Four pieces that must move together or not at all: the `.runtime/sample_lookahead.flag` write/poll/consume triple, the acquire/absorb split in `query_loop.py`, `dashboard.json::sample_lookahead` + `sample_lookahead_discards`, and the `scoring.lookahead` cap. **Never "recover" the discarded acquisition** — recording it makes the run's rows depend on in-flight depth, which forces a `human_intervened` stamp and devalues the campaign; that discard is the design, not an oversight. Why it is browser-only with no CLI verb: [`../operations/access-model.md`](../operations/access-model.md) § Tier 1a.
- **`session.py` ×3, `state.py` ×2, `base.py` ×2 keep their names — the package path already carries the concept.** Filed as a filler-name collision; the verification the entry asked for says no. Checked two ways and both come back clean: the three `session.py` are the run `Session` (`initialization/`), the CLI's accessor over that same noun (`cli/`), and the cookie→JSON auth store (`infrastructure/identity/`) — and **no module in the tree imports the auth one alongside either other**, so the only real ambiguity is never experienced; there is no disambiguating `import … as` anywhere, which is the friction a genuine clash produces. `escalation/state.py` vs `live_dashboard/state.py` and `llm/base.py` vs `projections/base.py` are each one concept per package, and `base.py`-holds-this-package's-ABC is a language-wide convention, not a filler name. Renaming any of them buys a longer import line and costs every citation that names it. Re-open only for a name whose *own package* cannot resolve it.
- **`RunCallbacks` ↔ `emit_*`** — two writer APIs by design; the "which do I use" rule is in [`../developer/adding-a-surface.md`](../developer/adding-a-surface.md) §1.
- **`from_disk_log`** — not a roundtrip shim; foreign fork-siblings + historical cycles have no live ledger, so the on-disk `index.json` is the only source. (Its round twin `from_disk_round` had zero callers and was deleted.)
- **`measurement_archive.py` `.get(…, default)` at `save()`** — looks dead (the production writer always sets the keys) but `save()` has direct test-fixture callers with partial dicts; live boundary guards.
- **`writers.py` `_load_p_best_trajectory` / `_fork_summary_from_index` / `_load_sibling_indices`, `axis.py::_collect`** — single-caller, but the caller is in the SAME file in every case; intra-file `_private` decomposition is not inter-file indirection.
- **Leader-lock-in mechanism** (`leader_lock_in` / `pobb_lock_in` / `pobb_lock_in_n_min` knobs + `PoBBConfig.lock_in` + the `LEADER_LOCKED` `EscalationTarget`/`CandidateOutcome` + the `abort:lock_in_off` lineage-overlay lens) — the config knobs default off and no committed campaign sets them, so it LOOKS like a dead mechanism, but the `LEADER_LOCKED` path is structurally LIVE: a domain escalation target, a candidate outcome, the mask/lineage-overlay `abort:lock_in_off` what-if lens (the candidates card's Lens select, "No lock-in"), and exercised by `tests/test_numerics.py`. Deleting it removes a shipped analysis feature, not dead code. Investigated + KEPT. (The unreachable significance-gate beside it WAS deleted — it had no live surface.)
- **Check-in "ready" ≠ "mintable" for prompt template-vars** — `origin_readiness(draft)` gates columns/framing/answer-space but not whether the committed prompt carries each node's required `{{template vars}}`; that check lives only at mint (`config.py::configure_and_apply_pipeline`, `pipeline_config_invalid` 422). Surfacing it earlier (at the resolve turn) needs the live `GET /pipeline` schema threaded into the deliberately-I/O-free resolve path (`origin_readiness` is pure-over-draft; `resolve_origin_turn`/`POST /resolve-origin` carry no backend client + the draft no base_url). **Operator decided: keep the 422 backstop, don't add pre-mint backend I/O.** It's non-destructive (draft preserved, retry) and names the exact missing vars + fix; a bad origin never runs. Revisit only if check-in UX timing becomes a felt pain.
- **`LLMResponse.reasoning` has no code reader, and that is the design — never file it as write-only surface.** It is the model's own thinking channel (`message.reasoning` on the OpenAI-compat wire). It looks exactly like the "fields declared/written never read" pattern in the hunt list below, and it has already been surfaced that way once (2026-07-26, by the audit that deleted the `llm_only` connector — whose `resp.reasoning[:4000]` had been its only reader). **A model with nowhere to put its internal process answers without one** — give it a bare classification slot and it emits the label with no reasoning behind it, measurably worse. So the slot is part of the ask, capturing what lands there is part of the contract, and the value of the field is not "who reads it in Python." It now rides the ledger payload → `nodes[*].output.reasoning` (audit twin + live dashboard) → the operator's node-detail "Thinking" pane. **Hard invariant: analytical only** — it must never reach a gate, metric, validator, scorer, escalation signal or cache key, because scoring narration teaches the loop to narrate. Full rationale is the field note in `infrastructure/llm/response.py`; the principle is `docs/concepts/structured-output.md` § A place to think is part of the ask. Same call applies to a `reasoning` slot in any node's `output_schema`.
- **MLflow + Langfuse sinks** — the observability-nexus *core capability*: PromptPotter drops into a team's EXISTING local MLflow / cloud Langfuse instance (flip a flag / add `.env` creds). Off-by-default ≠ dead. See `docs/architecture.md` §0.5 Tracing. Do not propose for deletion.
- **L2/L3's shared `fork_proposal` + `terminate_proposal` is ONE seam with two entry points** — L3's layout already teaches it both. Don't re-file as duplication. The fact worth carrying: **`l3_plan` has never fired in a banked ledger**, so every L3-side claim in this repo is design intent, not measurement ([`l4-outer-loop.md`](l4-outer-loop.md)).

- **Citability is DERIVED — never re-introduce a citable-panel list.** `EVIDENCE_GROUNDING_FIELDS` was a hand-maintained frozenset the validator checked *set membership* against, so a variant could cite a panel the prompt never rendered and pass clean. It had already drifted twice: the phantom `parent_panel`/`sibling_yield` names were excised 2026-07-02, and by 2026-07-13 four of its nine names (`sample_transcripts`, `axis_memory`, `archive_top_runs`, `rare_hit_samples`) rendered nothing on `l1_generate`'s floor while two rendered panels (`l1_wounds`, `origin_strengths`) were uncitable. Deleted: `@signal(citable=…)` declares evidence-vs-menu at each renderer, and `citable_fields(layout, exploration_budget)` intersects that with the node's **live layout** — one derivation feeding the prompt's `{{citable_fields}}` menu, the wire-schema enum, and `evidence_grounding_present`. **A citable panel that never renders invites a fabricated citation; the only defence that holds is deriving one from the other.**

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
(substring assertions, stub-forest tests — suite cap ≤200; count with
`pytest --collect-only -q` before treating the cap as binding);
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
