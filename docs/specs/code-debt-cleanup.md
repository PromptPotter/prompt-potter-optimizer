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

- **A dead L4 inner cycle leaves its spend unrolled.** *The earlier entry here claimed inner spend
  was invisible to `sum_user_spend` entirely. That was wrong, and acting on it double-counts:*
  `inner/spawn.py` returns the finished cycle's `SpendRollup` as `step_tokens`, and
  `scoring/sample_measurement.py::emit_step_token_usage` writes it to the OUTER cycle's ledger as
  one `backend` `TokenUsageRecord`, so the account tier and `BudgetGate` both already see it.
  Measured on the dev box: 27 inner cycles reaching a normal terminal stop (`max_rounds`,
  `lives_exhausted`) accounted for $1.6187 / 8,195,851 tokens on the sandbox ledgers and
  $1.6187 / 8,195,851 in outer rollup rows — exact in both units. What does NOT roll up is the
  cell that dies: 5 cycles (`producer_vanished` ×2, `crashed`, no stop stamped ×2) held
  $0.2215 / 1,112,746 tokens with no outer row, because `step_tokens` rides the SUCCESS return and
  a failed cell raises `InnerCycleUnscoreableError` before `_compute_step_tokens`.
  Exposure is bounded while L4 runs are the operator's, who is unmetered.

  **The filed action — "two fixes, one per cause" — is refuted in both halves, and taking it
  would trade a bounded under-count for an unbounded over-count.** (1) `resolve_resume_state`
  (`projections/live_dashboard/factory.py`) seeds the projection from the prior `dashboard.json`
  WHOLE — its `model_copy(update=…)` rewrites `rounds` / `round` / `best` and never `spend` — so
  `CycleResult.spend` on a CONTINUED cycle is cumulative across attempts, and continuation is not
  an edge case here: `_open_inner_campaign` continues every non-live terminal class, and
  `_banked_inner_rounds` re-budgets the wall clock around it. A deadline-cancelled cell is the
  main PRODUCER of continuable partial cycles, so emitting from the `result is None` arm bills its
  spend once there and again inside the cumulative rollup when the cell later completes. (2) The
  reaper half has the same shape from the other side: `delete_campaign` already declares its
  `inner_sandbox_root` cascade **delete-only** precisely because the success rollup has banked
  that money on the outer ledger, and a sandbox that survives a delete (the argument is
  `None`-defaulted, so a caller may omit it) reaches `reclaim_orphan_sandboxes` with its spend
  already inside the owner's tombstone.

  **What keeps the one live emitter honest is not the emitter.** `inner_campaign_id` is
  content-addressed, so a repeated (optimizer-prompt state, cell) is served from the
  content-addressed `measurements/` archive and never re-enters `run_inner_cycle` at all — that
  replay, not any guard at the emit site, is why the single cumulative emission fires at most once
  per inner campaign id today. It is load-bearing and nothing states it.

  **Root, and the corrected action.** Inner spend reaches the outer ledger through a channel that
  fires ONCE, ON SUCCESS, carrying a CUMULATIVE total — so it is neither complete (a cell that
  dies never emits) nor idempotent (a cell that emits twice bills its whole history twice). Fix
  the channel, not the call sites: emit the DELTA since this inner cycle's last emission, at cell
  teardown, on every terminal outcome. That needs a persisted per-inner-cycle high-water mark, and
  it is what would let both destroyers stop special-casing sandboxes. Blocker: none, but it is a
  spend-path change with a persisted mark, so it wants its own PR and its own mutation pass.

**From the 2026-08-06 JustLogic model bake-off.** Each carries its measurement; the four
root fixes that arc DID land (reasoning-token share on the ledger, provider-aware pricing,
`answer_modal_share`, the `reasoning_only_response` arm) are in `git log`, not here.

- **`reasoning_effort` is not a lever on `deepseek-v4-flash`, and the optimizer owns a third
  of every L4 cell.** Measured across 292 optimizer calls: `l1_generate` (effort `medium`)
  53 s / 5352 output tokens median, `l1_critique` (effort **`low`**) 45 s / 4206, `l2_context`
  23 s / 2157. One `l1_critique` round-trip billed 4790 completion tokens for a 1044-character
  answer — **~94% reasoning, against a schema that caps the answer at ~1300 characters** — so a
  two-step effort change buys ~27% and the knob is not the lever. (`l1_critique` has since been
  moved to `medium` deliberately — `low` was inherited from a profile note about the token FLOOR,
  never measured as the better DISTILLER, so the comparison arm simply did not exist. Its
  `max_tokens` went to 16000 with it: the tail already ran 11,370 against 12000.) The real options are a
  different optimizer model for the schema-bearing nodes or an explicit reasoning budget, and
  both are choices, not cleanups. Its cheaper twin is already the plan: `l1_generate` semantic
  widening at a net-SHORTER prompt.

  **Blocker CLEARED — and the headline was high.** Read off 46 banked ledgers joining
  `llm_call_start.prompt_chars` to `token_usage`: the optimizer is **~20% of LLM seconds and
  ~21% of cost**, not a third (`l1_generate` 8.7% / `l1_critique` 7.6% / `l2_context` 4.0% /
  `l3_plan` 0.3%); backend scoring is the other 80%. The reasoning share holds — `l1_critique`
  spends 4,724 reasoning tokens on a 932-char answer AT effort `low` — so the conclusion stands
  and only its size moved. What the same read DID surface as a lever is input, not effort:
  `l1_generate` split into thirds by prompt size runs 11.7k chars → 3,831 reasoning tok / 26.7s
  against 23.2k → 5,408 / 42.1s, and 6.4% of every character ever sent to that node was a
  verbatim second copy of a panel (fixed: `l1_layout_dups_across_slots`).

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

- **No surface reports the rate table's age.** `refresh_rates` writes `fetched_at` and only
  `_cache_fresh` reads it, so a stale table shows up indirectly as `unpriced_tokens` on a model it
  lacks. The refresh gap itself is closed (it now fires from `runner/entry.py::run_optimization`,
  the seam every launch path funnels through). Remaining action: decide whether age belongs on
  `/health` or nowhere — age is now bounded by the TTL plus time-since-last-run, which may make the
  surface not worth its wire.

- ⚠️ **`shared/identity.py` (the capability/tier authz vocabulary) collides with
  `domain/identity.py`** — flagged only, never acted on: the access model is the operator's call
  ([`../operations/access-model.md`](../operations/access-model.md)). The rest of the
  filler-name sweep is closed; see § Considered, not debt for the four names that keep theirs.

- **`datasets/bbeh/sweep/*.yaml` parse but would mint 12 identical no-op forks.** None of the 12
  carries `l1_layout` — the only lever `OperatorSweepFile` still has — so `--sweep-batch` over them
  pays full measurement for zero contrast. The dead keys they did carry are gone for structural
  reasons, not drift: `l1_section_overrides` named sections that are no longer panel names, and
  `l1_section_overrides_text` wrote `task_context`, now frozen (`TaskDecomposition.merge` refuses
  it). **Action before anyone runs the verb: author a real `l1_layout` per arm, or the batch
  measures nothing.** Mechanism itself is sound (exercised 12/12 through the real reader); the
  payloads' `reason` fields still carry the measurement narratives worth keeping.

- **The cycle-path codec agrees with Python only in prose.** `lib/ids.ts` re-implements `encode_cycle_path`'s separators and id charset, and nothing compares the TS literals against Python's output — `lib/__tests__/cyclepath.test.ts` locks the TS side against itself. **The filed "fold the camelCase hop into the generated `CycleHop`" was refused on inspection and must not be re-filed:** the generated type is the wire element of `LineageNode.path` / `RayItem.path`, while `ids.ts`'s hop is the BROWSER's address — it encodes into `?path=` URLs and view-memory keys, so binding it to a wire shape lets a server-side field rename invalidate persisted addresses. They were never one concept, only one name; the hop is now `PathHop` and the collision is gone. The one real divergence it hid (no TS mirror for `validate_path_component`'s all-dots traversal guard) is fixed and locked. Action for the remainder: generate the codec from `openapi.generated.json`, or accept the duplication and say so. Blocker: none; low priority — the browser only reads and the server re-validates every hop.

**Ray / lineage follow-ups** (named during the 2026-07-26 time-ray landing; none blocks the feature):

- **`lib/hooks/usePoll.ts` — head-of-line blocking.** One in-flight tick skips the whole next tick, and the lineage tick `Promise.all`s every subscribed key — one slow campaign's fetch delays every other key. Action: per-key in-flight accounting (a `usePoll` design change, not a caller patch).
- **`/ray` payload size.** A `limit`-sized window carries full `llm_call` payloads — multi-MB on a chatty course. Action: per-kind field allowlist in `store/family_ray_views.py`, coordinated with what `lib/chat/activity.ts::projectionToActivity` actually reads (an uncoordinated elision silently blanks activity lines).
- **`/ray` non-304 cost while a run is live.** Every append moves the family validator, so each 5 s tick re-parses the whole merged window. Fine at current sizes; revisit with a per-ledger byte cursor if it shows in profiles.
- **`/tree` always serves the whole subtree** (recursion to every fork + inner run) though collapsed sidebar rows render one tier. Action: only if tree size shows up — a `depth=` param is contract surface, don't add speculatively.
- **View-memory hydration order.** Today's restore sites read the store during render (`useSyncExternalStore`), which is safe; a future consumer restoring from an effect could seed from the pre-hydration empty store and record it back. Guard belongs in `lib/view-memory.tsx` if a second effect-time reader ever appears.
- **Weak-ETag identity folding.** `_conditional.py::weak_etag` folds the lens/samples mask (request identity) into the validator (resource state). Correct today because nothing caches by URL alone; a URL-keyed cache layer would cross-serve variants. Split validator vs `Vary`-style identity if one ever appears.
- **`useNodeToggle`'s defaults-aware resolution has no Vitest** — the registry got its test (`lineage-registry.test.ts`). Not the "codec": `viewMemoryCodec` IS tested (`view-memory.test.ts` round-trips the persisted record), and calling it that pointed at the one piece with coverage. What is untested is `sidebar/grouping.ts::isNodeOpen`, where membership means *deviation from the per-kind default* — the four-way `(present/absent) × (default open/closed)` matrix. Cheapest shape is a pure `.test.ts` on `isNodeOpen`/`nodeKey`: testing the hook itself would need a jsdom `.test.tsx` under `lib/**/__tests__/`, a shape that exists nowhere today.

**Do soon, not now** (surfaced by the 2026-07-10 drift pass; the six fields with *no* reader at all were already deleted):

- **`export`s re-exported through `export *` barrels** — `lib/api`, `lib/types`, `lib/derivations`, `components/ui`, `components/workflow` (all five still live; the 37 genuinely file-local ones already landed). They look local-only to a naive grep; stripping `export` silently narrows each barrel's public surface. Action: decide per barrel whether the symbol is meant to be public, then strip or keep — don't script it blind. **The old "52" figure is retired (2026-07-16): every barrel has been touched by feature work since it was counted, and the barrels are load-bearing (85+/34+/32+ importers). Recount before acting; don't re-cite a headcount as current.**
- **A salvaged Groq response reports ZERO tokens.** `infrastructure/llm/json_parse.py::try_groq_json_validate_repair` rebuilds an `LLMResponse` after re-parsing `failed_generation` out of a `json_validate_failed` 400, hardcoding `usage={"prompt_tokens": 0, "completion_tokens": 0}`. That call **already reached the wire and was billed** — the model burned tokens producing the malformed JSON. It IS metered (every returning optimizer round-trip passes `call.py`'s single exit), so this is not a seam that forgot; it is a seam that meters a **fabricated zero**, which is worse, because a zero is what a replayed call legitimately reports. **Why it is not a one-line fix:** Groq's 400 body carries no `usage`, so the true counts are unrecoverable, and `unpriced_tokens` is the WRONG home — it means *billed tokens with no USD rate* (count known, price unknown), whereas here the **count itself** is unknown. There is no "tokens unknown" representation on `TokenUsageRecord`, and adding one is the whole add-a-surface recipe ([`../developer/adding-a-surface.md`](../developer/adding-a-surface.md)) — record field, projection, and every reader that divides by a count. Note what it would NOT ride: the account gate now leans on the token arm precisely because a count is always knowable, and a row whose count is fabricated is the one thing that breaks that assumption. Do NOT estimate from content length — a fabricated number rendered as a measurement is the one thing this must never do. **Currently dormant** (`json_validate_failed` is Groq-only; every configured provider is `openrouter`), so it is a correctness landmine, not a live leak — it fires the day anyone repoints a node at Groq. Blocker: needs the unknown-count dimension.
- **Post-flip copilot — deferred on purpose, not forgotten.** The `checkin` node consulting in run mode and raising `pause-cycle` / `change-spend-budget` / `fork-cycle` instead of draft patches. `RaisedCommand` (`datasets/origin_resolve.py`) is already general enough to carry it. Not debt and not now: it is a **new feature**, and the closing-phase directive is no new features until `promptpotter-self` is distributable. Lands with L4, so it belongs to [`l4-outer-loop.md`](l4-outer-loop.md) when it does.
- **`RoundResult.results` drops the parent's panel on every round that promotes — but the filed ACTION would not deliver what it was filed for.** Re-verified 2026-08-15 across 142 banked round documents. The mechanism holds exactly: `l1/score/winner.py` seeds `best_results` from `parent.results` and overwrites it with `all_candidate_results[winner_id]` when a winner is elected — **69/69** rounds with a winner are byte-identical to that candidate's rows, **39/39** held rounds match no candidate. So the parent's per-sample panel on that round's own subset is never persisted on any promoting round, and that IS a real gap in the record.

  **Three corrections to the entry as filed.** (1) It is *not* the cause of the flips line's round-0 pinning: `sampleFlips` reads `all_candidate_results[candidate_id]` on BOTH sides (`lib/derivations/round-samples.ts`) and the round-0 reference is hardcoded in `RunCard.tsx`, so swapping this field's subject leaves that join with nothing new to match. (2) It is not a "duplicate": on the 39 held rounds it is the ONLY copy of the retained incumbent's rows, so inverting it does not remove a duplicate — it moves which of two subjects goes unpersisted. (3) "One assignment plus every reader" is under-scoped: `resume_and_fork/repair.py` holds a second and third WRITER that re-assert the invariant, and the frontier merges in `optimization/cycle.py` + `mask/load.py` feed `cumulative_theta` — the L4 outer fitness signal — so a subject swap would lag the frontier a round permanently. `winner.py` also computes the round's `accuracy` and `p_value` off the same local, and `RoundResult` is `extra="ignore"`, so all 142 existing documents would load under the new meaning with no version marker and no complaint.

  Corrected action: **additive** — key the parent's rows into `all_candidate_results` under a reserved id, or give them their own field, and leave `results` as the headline's subject that ~18 readers already depend on. Blocker: none, but it is a persisted-document change, so it wants its own PR.
- **`matched_origin_*` is named for the origin and computed from the parent.** `matched_origin_stats(origin_results=…)` is called with `RoundParent.results`, which is the origin only at round 0 and the prior winner after it — so these are the PARENT's rate on the candidate's rows at every round ≥ 2. They coincide at round 1, which is why the name survived. Action: rename to `matched_parent_*`. **Scope, re-measured 2026-08-15:** five names, not two-plus-twins (`_lift`, `_lift_ci_lo`, `_lift_ci_hi` too), plus `matched_origin_stats`' own `origin_results` parameter and `scoring/selection.py::matched_origin_lift`; `RoundSummaryCandidate` inherits from `DashboardCandidate`, so renaming the base moves both; the openapi files carry **zero** occurrences (the round document is served as a raw file body by a route with no `response_model`), so the generated surface is `webapp/lib/api/types.generated.ts` plus the camelCase twins. The webapp *copy* already says "parent" — only the identifiers are stale.

  **The blocker is not "it touches a persisted document" — it is that `RoundResult` and `ScoredCandidate` are both `extra="ignore"`.** A hard rename does not raise on the 142 banked round files; it loads them with `matched_parent_* = None`, silently voiding every historical floor, lift and CI. Worse than a crash: the migration must restamp them or state the loss.

- **A repair's re-bank onto the branch has never been observed.** `_rebank_on_branch` was fixed
  2026-08-11 to take each corrected round through the whole ingress — mint, measurement, election,
  close — so the branch no longer shows a round document nothing on the ledger backs. The cycle it
  was measured on went with that day's store wipe, so the fix is reasoned, not seen. Action: repair
  a fork and confirm each corrected round carries its own `round:complete` on the branch. Blocker:
  none — it needs a run, not a decision.

**From the 2026-08-11 mobile pass — what it left half-done.**

- **Mobile pass verified at 375/1440 on chat/dashboard/files/verify only.** Unswept: 393, 412, 768,
  landscape; login, onboarding, l4, account modal, candidates, lineage. No Lighthouse number recorded,
  so there is no before/after. Action: sweep + record one pass. Blocker: none.

## Blocked — named blocker

**Behavior change (needs explicit sign-off, not a blind swap) — scoring:**
- **All-errored candidate scores `accuracy = 0.0`, not the honest `None`** — `compute_accuracy` (evaluators.py) returns 0.0 when no scoreable row exists; for all-deprecated that IS the verdict, but for all-errored it fabricates one (declared stage-1 tolerance in its docstring, 2026-07-13). The honest `None` must propagate: `ScoredCandidate.accuracy`/`RoundResult.accuracy` → `float | None`, `compute_composite_fitness` handling a missing `accuracy` term without `ScoringTermMissingError` in `_running_scores` (an "unscoreable candidate" state, the outer sibling of `InnerCycleUnscoreableError`), `display_fitness` double-None, dashboard + `types.generated.ts` + chart null handling, `best_round_by_measured_accuracy`/`_apply_best` null-safety. Blocker: wide Optional-propagation across the served surface for a state PoBB DegradationCheck usually eliminates mid-round anyway; needs its own pass. **Smaller than filed:** its sibling `rescore_results` stamps errored rows `fitness = 0.0` citing `compute_accuracy`, which actually EXCLUDES them, and `_mean_fitness_by_cell` reads an absent key identically — so no cited reader depends on the stamp. Start from the audit that implies: every unguarded `r["fitness"]` subscript. Note the stamp makes a row's shape depend on replay (a freshly measured error row has no `fitness` key at all).

**Live L1 round (operator-gated):**
- **`*_override → *_updates` L1 delta-key rename.** `prompt_fields_override` / `task_context_override` / `pipeline_params_override` / `pp_override` are merges, not replacements, but named "override." **Decision (settle first):** unify the pipeline delta to **`pipeline_overlay`** everywhere (kills the short/long two-name tax); the prompt/context deltas become `*_updates`. Rename writer→reader in one commit (schema `dispatch/schemas.py::L1Variant` is the source of truth — the LLM contract auto-propagates). Full site map: grep `*_override`. **The old rider "collapse `searchPoint.ts` + `candidateSearchPoint.ts` into one `wireToCandidateSearchPoint`" is DROPPED (verified 2026-07-16):** they answer different questions over one served field — `ObserveConfig` is a read-only view (keeps `steps`, carries a label), `CandidateSearchPoint` is an editable fork seed (drops `steps`). `lib/derivations/searchPoint.ts`'s module comment says so in-code. Merging would fuse a read projection with a write seed; the only real overlap is two lines of `candidate_scores.find`. **Blocker:** invalidates on-disk cycles (round-file key + optimizer structured-output contract) — verify against a FRESH cycle that completes round 1, not a resume.

**Security posture / migration:**
- **Backend-registration dedup** — `webapp/lib/hooks/useConnector.ts` client-side `distinct`/`seenEndpoints` collapse is a back-compat shim for per-dataset `BackendConnection` rows minted before the `wiring.py` one-row-per-`(base_url, backend_type)` fix. NOT a row-delete: it's a 3-step migration — (1) rewrite each campaign's `campaign.yaml::backend_id` to the canonical `local` (8 stale ids across 82 campaigns, all → the same `127.0.0.1:8000` endpoint); (2) collapse the duplicate rows (needs a new `BackendStore.remove`); (3) make every re-wire path reuse the canonical id. Then delete the loop. Also: `wiring.py` `not backend_id` reuse block should guard on `existing.base_url == backend_url` (mint a distinct id on mismatch). Blocker: write + operator-run the idempotent migration on their data first — the loop is load-bearing until then.

**Cross-repo (TermNorm sibling at `OfficeAddinApps/TermNorm-excel/backend-api`):**
- **TermNorm wire `model`** — backend `spend.backend.model` reports a provider slug (`"openrouter"`), not the upstream model, so backend $ can't be derived from `lookup_rate(model)×tokens`. Add `model` to the per-request response + a `/version` endpoint; this repo then bumps `termnorm.py::_EXPECTED_REVISION`. (The connector revision-pin already exists; the old `auth.py` back-fill is already gone.)
  PromptPotter does not read that slug at all. `_compute_step_tokens` now stamps every step-token entry with the node's model — the backend's per-node `model` when it reports one, else the model the dataset overlay pinned (`pipeline.yaml::nodes.{n}.config.model`, which is mandatory for an LLM node). So per-node cost is derivable today, including for chars/4-estimated nodes, and the old `node_model or llm_provider` coalescing is gone. What remains genuinely owed on the TermNorm side is the **`/version` endpoint**; the per-request `model` is now a nicety (it wins over the overlay when present), not a blocker.
- **Backend fix isn't observable without clearing a cache** — PP's measurement cache + TermNorm's `match_database` both key on query/searchpoint, never on backend code/revision, so a co-owned backend fix replays stale results. Fold the connector revision-pin into the measurement-cache key (or add a `--fresh` flag); confirm the TermNorm `/matches` short-circuit only fires on `verified` aliases. Workaround: clear `measurements/`.

**Coupon + BYO build (Lane A2 — blocked on the build itself; ADR-0003 § Host coupon):**
- **Adopt-in-new-code for the coupon/BYO build:** the new `grant.json` / `api_keys.json` stores MUST ride `read_json_optional`/`write_json` (the `UserStore` template, `store/io.py`) from day one — don't add hand-rolled readers. `shared/pricing.py` still hand-rolls `json.loads(...)` at three sites (one of them decoding a fetched payload — same pattern, not previously filed); held separately from the JSON-read sweep (SHIPPED elsewhere) because `shared/` importing `infrastructure/store/io` is an unresolved layer-direction question — resolve it before or alongside this build. (`store/io.py` is now safe to import from anywhere: the eager `store/__init__` that made any leaf import drag in `CampaignStore` is gone, so this is a layer-*direction* question only, no longer an import-cycle one.)
- **Two host-wallet mechanisms** — `application/jobs/quota.py::admit_launch` + the `User.spend_budget_usd_total` / `token_budget_total` ceilings (lifetime, mint-time snapshot) vs the new coupon (`grant.json`, ledger-derived, live). Two guards on one concern (host's wallet) = the no-redundant-mechanism rule. Action: **delete the free-tier path**; coupon-remaining becomes the single host ceiling, read by the per-cycle `BudgetGate` every tick (D1/D2 in ADR-0003). Blocker: lands *with* the coupon, not before — deleting first leaves the wallet unguarded. ⚠️ Two things the replacement must carry or it is a regression: BOTH units (an all-USD coupon re-opens the unpriced-model blindness D1's token arm exists to cover), and the per-run **reservation** (`Job.cap_usd` / `cap_tokens`) — without it two concurrent launches are each admitted against the same remainder and the pair spends ~2× the ceiling.
- `domain/run_records.py::TokenUsageRecord` lacks `key_source` → `/auth/activity` `group_by=api_key` (`routers/auth.py`) fakes a *provider slug* as the key id. Once real `key_source: host|user` lands (declared on `TokenUsagePayload` in the asyncapi), replace the fake-slug derivation with the real dimension. Blocker: the coupon build adds the field.

## Standing — long-lived design holds

- **Every persisted `StrictModel` owes a row in `application/restamp.py::_SURFACES` — adding one without its row IS the bug.** It has already cost an outage: `5a69ca67` dropped `BackendConnection.last_synced_at`, `init_services` raised `extra_forbidden`, and every `new`/`resume` died at load. What the table deliberately excludes and why each absence is a decision — and why round documents are reported on but never rewritten — is stated in that module's own docstring; the tolerance rule and the reporting-vs-scoring boundary it stops at, by [`../../promptpotter/domain/CLAUDE.md`](../../promptpotter/domain/CLAUDE.md) § Tolerance is scoped by what a payload is FOR. Read the table as covering the typed documents, not everything the verb touches. **Outside the table is not outside all obligation, and reading it that way already cost the record once** — `extra="ignore"` forgives an extra key, not a missing one, and it does not reach the `extra="forbid"` models nested inside, where a stale key is fatal in the other direction.

- **Tier 3c — the web-launch process split — is DEFERRED, and the trigger is what to re-read, not the deferral.** A web-launched run executes in-process in the API worker, so it shares that process, its env and every provider key ([`../operations/access-model.md`](../operations/access-model.md) § Tier 3). The reason to wait is not cost: **the requirement is undefined until we know what is being isolated.** Audited 2026-08-15 across every tenant-controlled path into that worker — scoring formula (AST-allowlisted, no attribute/subscript), YAML (`safe_load` throughout), dataset slugs (regex-validated), provider + `base_url` (closed three-entry registry, never tenant-set), and no URL fetched from tenant input. Nobody can supply executable code, so a boundary built now is built against a guess, and the guess decides the shape: a custom pipeline node wants a subprocess, a plugin connector a different trust model, arbitrary Python a container. **It also fights L4 directly** — `runner/inner/spawn.py` spawns each inner campaign as an `asyncio.create_task` in this process and depends on it (per-task ContextVar copies for ledger / round / abort, the flat `.inner/` sandbox, `set_optimizer_prompt_overrides` bound in the child), so the split would mean rebuilding the recursion seam at every depth while L4 is the closing focus. **Waiting costs nothing because the launch seam is already single** (`application/embedded_run.py`, `jobs/launcher/mint_and_start.py`) — keep it that way and a later move is contained; let run-launch logic spread across call sites and this becomes expensive. **THE TRIGGER: the first time a tenant can supply anything executable** — a custom node, a plugin connector, arbitrary Python. That is a product decision, and when it is made 3c stops being deferred and becomes a prerequisite of shipping it. Until then, what bounds the blast radius is 3a's kernel wall plus `DATA_DIR` (`deploy-linux/install-service.sh`), which takes away the service's ability to rewrite its own source and `.env`.

- **Holistic reframes — larger chunks, noted so they aren't mistaken for done; don't slip one into a release.** (1) The `ui/HoverCard` primitive rides ONE hover, while the native `title=` tooltips spread across the webapp are the same job — consolidate incrementally, alongside the three bespoke popovers vs `ui/Popover`. (2) Candidate-CI resolution is ONE seam and one estimator now (`scoring/selection.py::composite_ci`, stamped at `candidate_scored`); a second band beside it is what made the whisker appear and vanish by election gating, so do not add one back. (3) **Never examined, and the one with real reach:** `promptpotter/application/optimization/CLAUDE.md` asserts L2/L3/L4 are "the same family — each mutates a slower-changing surface of the level below", yet each is built from scratch (L2/L3 are escalation strategies, L4 a connector recursion). Whether the family should share machinery has never been asked, only asserted. The L2↔L4 hunt found one real collision underneath it (`NodeLayoutSpec.editor` claimed two owners of `l1_generate`'s layout, fixed `d1d792b0`), so the assertion is load-bearing enough to be wrong in places.

- **Two numbers ARE computed in the browser, against § Scoring authority** — verified while tracing the whisker, not suspected: `CandidatesCard.tsx` derives `cached_samples / n` and plots it as a chart series (its own tooltip admits "the height is geometry, the counts are the measurement"), and `ScoringInspector.tsx` subtracts `matchedOriginAccuracy` from `accuracy` for the "vs origin" delta. Both need serving, not deleting. Beside them, `composite_ci`'s NAME is wrong and it is the root of a bug already paid for: it runs `mean_ci` over per-cell mean FITNESS, and accuracy IS mean fitness, so the band brackets accuracy whatever the composite formula says. Believing the name is what put a `composite == accuracy` guard on the θ override and made a reader think the whisker sat on the wrong bar. Renaming it (and its `composite_ci_lo/hi` fields) is a migration over ~200 round documents, so it is filed, not done.

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
- **`l2_duplicate_insert` / `l2_task_context_stale_repeat` are GONE — don't re-file either** — both were `task_context` checks, and that framing is frozen for the run (`TaskDecomposition.merge` refuses a rewrite), so neither breach is representable. Owner: [`../developer/dispatch-hub.md`](../developer/dispatch-hub.md) § Wound 4.
- **Benchmarks are NOT gated from the distributed app** — settled the other way by `20d17ea8`: repo `datasets/` is *install content* (tracked in git, readable by anyone who has the install), so the `datasets.benchmarks.read` capability + `PROMPTPOTTER_ADMIN=1` gate were deleted and the tier is now `yours`/`install`. The gate existed to hide one gitignored scratch cut, and its cost was a blank pipeline hero + hard-sample leaderboard on every benchmark campaign. A private cut belongs in the tenant, where path isolation already protects it. Don't re-file the old "hide benchmarks from the default identity" entry.
- **Display-only recomputes do NOT breach scoring authority** — `headline-stats.ts::fitnessTrend` folds already-served values, and `presentation/views/live/phase.py` recomputes recall@k for the terminal readout. Serving either would add wire coupling for identical behaviour. (The bare `except: pass` around the recall block WAS the real smell, and was fixed.) **`hit_rate` sat on this list and should not have:** a fold is display-only only where its result is REACHABLE, and `is_hit` is `fitness >= 1.0` — unreachable on a graded scorer, so the column printed `0/N` on every row of a healthy campaign while this entry vouched for it. Now served (`SampleSeries.n_hits` / `mean_fitness`) and renamed `mean_fitness`. Check a proposed exemption at its threshold before granting one.
- **Sample look-ahead is LIVE, and every part of it looks removable** — it defaults off, no committed campaign enables it, `ADMIN_CAPABILITIES` has exactly one member so the tier reads like scaffolding, and `_sample_lookahead_depth` returns 1 on `promptpotter-self` (the campaign most often read), so a reader concludes the branch never fires. It fires on every remote-HTTP dataset the moment the operator presses the button. Four pieces that must move together or not at all: the `.runtime/sample_lookahead.flag` write/poll/consume triple, the acquire/absorb split in `query_loop.py`, `dashboard.json::sample_lookahead` + `sample_lookahead_discards`, and the `scoring.lookahead` cap. **Never "recover" the discarded acquisition** — recording it makes the run's rows depend on in-flight depth, which forces a `human_intervened` stamp and devalues the campaign; that discard is the design, not an oversight. Why it is browser-only with no CLI verb: [`../operations/access-model.md`](../operations/access-model.md) § Tier 1a.
- **`session.py` ×3, `state.py` ×2, `base.py` ×2 keep their names — the package path already carries the concept.** Filed as a filler-name collision; the verification the entry asked for says no. Checked two ways and both come back clean: the three `session.py` are the run `Session` (`initialization/`), the CLI's accessor over that same noun (`cli/`), and the cookie→JSON auth store (`infrastructure/identity/`) — and **no module in the tree imports the auth one alongside either other**, so the only real ambiguity is never experienced; there is no disambiguating `import … as` anywhere, which is the friction a genuine clash produces. `escalation/state.py` vs `live_dashboard/state.py` and `llm/base.py` vs `projections/base.py` are each one concept per package, and `base.py`-holds-this-package's-ABC is a language-wide convention, not a filler name. Renaming any of them buys a longer import line and costs every citation that names it. Re-open only for a name whose *own package* cannot resolve it.
- **The unreset ContextVars are NOT a leak class — the one real defect among them shipped, and "one `@contextmanager` for all" would have caused two.** Filed as seven vars with five leaking; it is eight, four have no reset path, and each was traced 2026-08-15. `_MODE` is set once per fresh inner task that then dies, and a NARROW scope is actively harmful: the mode must cover finalize or the archive reads, tracing sink, earned-blocks fence and optimizer clamp de-hermeticize mid-measurement. `_CURRENT_ROUND` is a per-round running marker with no enclosing block — `run_observers.py::set_round`'s first-token-only trick is a deliberate outer restore. `_INNER_SPAWN`'s stale-context path needs a second campaign in one task reaching `run_optimization` with no `cycle_id` **and** then spawning an inner cycle; every in-repo caller mints the cycle first, and spawning requires the cycle that would have prevented it. And **not** clearing `_OPTIMIZER_PROMPT_OVERRIDES` is load-bearing, not an oversight: `spawn.py::_run_inner_campaign` sets the inner mutations and *then* calls `run_optimization`, so an unconditional set there would wipe them — the eight test sites hand-calling `set_optimizer_prompt_overrides(None)` are test isolation, not a production symptom. The genuine defect the original entry never named — `_ABORT_CHECK` chaining one predicate per rebase, keeping every retired fork's `pause.flag` live — is fixed; `git log` has it. **Do not re-file a sweep here, and above all do not fuse them into one settings object:** `_ABORT_CHECK` is *composed* at the runner seam so an outer pause reaches a nested L4 cycle, and `session.pause_check` holds a second copy of that composition, so a context manager restoring only the ContextVar desyncs the checkpoint poll from the rate-limit poll.
- **θ and `matched_origin_*` do NOT belong on `ElectionRecord`** — refused on evidence when the crown moved there (2026-08-11), and the shape invites re-proposing both. θ is RESTAMPED when the ruler warms, which is what round 0's second close exists to deliver (`runner/loop.py`), so it stays on `round:complete`, which every close re-reads; only the crown never moves, and only the crown belongs to a record that does not replay. `matched_origin_*` is not merely unservable there but *unwanted*: nothing plots a floor on a bar (the sole renderer is `ScoringInspector`, off the row it selected), so serving it on the tree ships a writer with no reader — and by this package's own test (`infrastructure/CLAUDE.md`) a value the round document already carries per candidate earns no ledger payload at all.
- **The `score:` lens cannot be ranked BY the election, at any price worth paying** — refused 2026-08-11; the tempting entry reads as a one-line consistency fix and is not. θ under another formula must be re-fit from per-sample grades against a re-calibrated δ ruler, which is `ab_replay`'s substrate (`with_replay=True` plus an archive read). The lens and `ab` are **one mechanism at two prices**, not two rankings to reconcile, and the cheap one is polled by the tree route — so adopting the exact one puts a campaign-wide refit behind a 5 s poll. What was wrong was the claim, and it is fixed: `display_rank_key` (ex-`round_winner_key`) names the candidate a formula ranks first, nothing more.
- **Typing `index.json` — measured 2026-08-05, answered "not yet".** The only `dict[str, Any]` in the read path a static model could name (every other is the node-keyed overlay whose keys the backend invents at runtime). **43 files, 24 top-level keys, 0 unreadable**, against ~120–160 model lines / ~25 files / ~65 read sites — **net +60 to +100 LOC**. Refused because the ledger scores it **zero** (`any_params` excludes container values, `models_lax` moves ≤1) and `extra="forbid"` breaks the deliberately tolerant reads in `enumerate_cycles` / the lineage surveys. Re-verify 43/24/0 before re-opening; `domain_any_maps` is the dimension that would make it decidable.
- **`RunCallbacks` ↔ `emit_*`** — two writer APIs by design; the "which do I use" rule is in [`../developer/adding-a-surface.md`](../developer/adding-a-surface.md) §1.
- **`from_disk_log`** — not a roundtrip shim; foreign fork-siblings + historical cycles have no live ledger, so the on-disk `index.json` is the only source. (Its round twin `from_disk_round` had zero callers and was deleted.)
- **`measurement_archive.py` `.get(…, default)` at `save()`** — looks dead (the production writer always sets the keys) but `save()` has direct test-fixture callers with partial dicts; live boundary guards.
- **`writers.py` `_load_p_best_trajectory` / `_fork_summary_from_index` / `_load_sibling_indices`, `axis.py::_collect`** — single-caller, but the caller is in the SAME file in every case; intra-file `_private` decomposition is not inter-file indirection.
- **Leader-lock-in mechanism** (`leader_lock_in` / `pobb_lock_in` / `pobb_lock_in_n_min` knobs + `PoBBConfig.lock_in` + the `LEADER_LOCKED` `EscalationTarget`/`CandidateOutcome` + the `abort:lock_in_off` lineage-overlay lens) — the config knobs default off and no committed campaign sets them, so it LOOKS like a dead mechanism, but the `LEADER_LOCKED` path is structurally LIVE: a domain escalation target, a candidate outcome, the mask/lineage-overlay `abort:lock_in_off` what-if lens (the candidates card's Lens select, "No lock-in"), and exercised by `tests/test_numerics.py`. Deleting it removes a shipped analysis feature, not dead code. Investigated + KEPT. (The unreachable significance-gate beside it WAS deleted — it had no live surface.)
- **Check-in "ready" ≠ "mintable" for prompt template-vars** — `origin_readiness(draft)` gates columns/framing/node-models but not whether the committed prompt carries each node's required `{{template vars}}`; that check lives only at mint (`config.py::configure_and_apply_pipeline`, `pipeline_config_invalid` 422). Surfacing it earlier (at the resolve turn) needs the live `GET /pipeline` schema threaded into the deliberately-I/O-free resolve path (`origin_readiness` is pure-over-draft; `resolve_origin_turn`/`POST /resolve-origin` carry no backend client + the draft no base_url). **Operator decided: keep the 422 backstop, don't add pre-mint backend I/O.** It's non-destructive (draft preserved, retry) and names the exact missing vars + fix; a bad origin never runs. Revisit only if check-in UX timing becomes a felt pain.
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
