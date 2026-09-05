# Code-Debt Cleanup — Backlog

**Only what cannot be picked up now, and only what ASKS FOR WORK.** An item earns a line by being
**blocked** or **multi-arc**. Everything else — anything adjacent to work already in hand, anything
one edit closes — is **fixed in the pass that found it**, never filed. Enough to pick up cold:
`file::symbol — why — action — blocker`. `git log` is the history layer; when an item ships, delete
it.

> **Every entry names its RE-TEST: the command, the landing, or the person and the question they
> must answer.** This is the whole discipline. A blocker without one is a claim about the world on
> the day it was written, and nothing ever forces a re-read, so a blocker that cleared months ago
> goes on reading as current. **An entry with no re-test is not blocked, it is unverified.** Write
> the re-test or do not file the entry; where an entry carries one, RUN IT before acting. Audits
> keep finding entries that were stale or outright wrong — a "dead" field mlflow reads, an
> "always-False return slot" that is a live signal, a directory deleted by the same commit that
> filed it. If an entry is wrong, fix or drop it as part of the work.

**Not debt — goes elsewhere, and none of it comes back here:**

| Kind | Home |
|---|---|
| Forward feature work | [`roadmap.md`](roadmap.md) |
| Architectural decisions | [`../architecture.md`](../architecture.md) |
| **A refusal that protects code** | **Beside that code** — a docstring is re-read whenever someone touches the function; a backlog line never is. |
| How to HUNT debt, and what to skip on sight | [`../developer/conventions.md`](../developer/conventions.md) § Auditing for debt |

## Open — multi-arc, no blocker

- **The mobile pass was verified at 375/1440 on chat/dashboard/files/verify only.** Unswept: 393,
  412, 768 and landscape; login, onboarding, l4, account modal, candidates, lineage. No Lighthouse
  number was recorded, so there is no before/after. Action: sweep + record one pass.

- **THREE numbers are computed in the browser, against § Scoring authority** — each verified by
  tracing, not suspected, and **every re-check has moved a target, so fix the aim before the code**:
  the `cached_samples / n` division now lives in `candidates/series.ts` (it was in
  `FitnessChart.tsx`, and before that was filed against `CandidatesCard.tsx`); the `θ/$` chip is
  minted in `shell/RemoteControl.tsx`, NOT `chat/ChatPane.tsx`. **A fourth was struck, not fixed:**
  the searchpoint drill-in never subtracted anything — it renders served `matchedParentAccuracy` and
  served `matchedParentLift` with its interval. The rest: `HardSamplesHeatmap.tsx` folds per-sample
  measurements into a mean and thresholds at **`>= 0.5`**, matching neither
  `lib/fitness.ts::HIT_THRESHOLD = 1.0` nor `sample-walk.ts::sampleBucket`'s 0/1 boundaries — so the
  mini heat strip and the table row beneath it can colour one sample differently on a graded scorer,
  while served `series.mean_fitness` is already read by two sibling files and `archivePerSample` is
  already a prop; `dashboard/scoring/OuterSignalPanel.tsx::leadingArm` falls back to a browser-side
  argmax over `composite_fitness` where the engine elects on **θ**, disagreeing with
  `forest-layout.ts::pickWinner` (deliberately no-fallback, its comment says why) exactly on HELD
  rounds — so it can draw a lift interval attributed to an arm the round never crowned; and
  `shell/RemoteControl.tsx` mints `abilityDelta / usedUsd` as a headline `θ/$` KPI chip. All three
  need **serving**, not deleting, so each wants a backend field first.

- **Holistic reframes — larger chunks, noted so they aren't mistaken for done; don't slip one into a
  release.** (1) **Tooltip/overlay consolidation:** ~86 of the webapp's ~170 DOM `title=` attributes
  are teaching prose the browser renders as an unstyled, unselectable blob that dies on touch.
  Migrate **by string source, not by file** — `lib/terms.ts::TERMS` first, then the `VerifyPane` /
  `RoundFileView` header glossaries; leave the `title={same truncated string}` sites, where
  HoverCard is strictly worse. (2) **Never examined, and the one with real reach:**
  `application/optimization/CLAUDE.md` asserts L2/L3/L4 are one family, yet each is built from
  scratch. Whether they should share machinery has never been asked, only asserted — and the L2↔L4
  hunt found one real collision underneath it.

- **Optimizer model repair-rate on heavy L2/L3 structured output — unmeasured.** What is owed is the
  measurement: a live cycle reaching L3, read under the model
  `promptpotter/assets/optimizer/pipeline.yaml` currently pins — read it off that file, never off
  this entry.

- **Three mechanisms key on ground-truth LABELS and go silently inert on a verifier-graded
  backend.** One subject, three sites, each needing a design answer rather than a guard — which is
  why they are filed together and not fixed in the pass that found them. The wrong-number half of
  this class (fabricated recall, fabricated rank statistics, `exact_match("", "")` scoring 1.0) was
  fixed; these three are the half that *reports nothing* rather than something false. (1)
  `pobb/checks.py::is_answer_collapsed` — `enumerable_truth_labels` returns `None` with no labels,
  so the COLLAPSED elimination gate is permanently `False` and an L4 candidate driving every inner
  cell to one lift is invisible to it; what collapse MEANS without labels is the open question. (2)
  `intelligence/earned_blocks.py::answer_space_signature` — an empty label set returns
  `OPEN_ANSWER_SPACE`, the same key a free-text labelled task gets, so Harbor's mined Agent-Skill
  blocks pool with L4's optimizer-prompt blocks and with any open-answer benchmark's; separating
  them needs an answer to what actually makes framing blocks transferable, not just a second
  constant. (3) The `prompt_info` trap — stated at the decision point in
  [`../developer/adding-a-surface.md`](../developer/adding-a-surface.md) § 5 — has no GUARD,
  and the obvious one is wrong: prompt fields in `optimizer.param_keys` ⇒ `prompt_info` required
  would trip on every L4 run, since `promptpotter-self` deliberately declares the first without
  the second. **Re-test:** `.venv/Scripts/python.exe -m promptpotter
  new spreadsheetbench-s10` past round 1 with `prompt_block_catalogue` on, then read the round file
  for a COLLAPSED verdict and `earned_blocks` under `OPEN` — if either now discriminates, the entry
  is stale.

- **`InProcessRun` has no arming context, so both in-process connectors that need per-run state
  invented the same ContextVar.** `InProcessRun = Callable[[str, dict], Awaitable[dict]]` — query
  and payload, no session, no dataset, no resolved experiment. `dspy_module.py` holds `_PROGRAM`;
  `harbor.py` holds `_PANEL`, armed as a **side effect of `extract_experiment`**, which neither
  that function's name nor its protocol docstring mentions. Two connectors reaching the same
  workaround independently is a missing NAME, and the author of connector #3 can only discover
  the channel by debugging. The fix is a seam widening that DELETES both ContextVars — give
  `in_process_run` the arming context (the resolved experiment doc, or the session handle). Both
  are currently CORRECT, so this is cost-of-authoring, not a bug: campaigns are
  `asyncio.create_task` siblings, so each copies the context and two concurrent Harbor campaigns
  cannot clobber each other. **Re-test:** author a throwaway connector needing per-run state
  without reading `harbor.py`; if it reaches for a ContextVar, the entry stands.

- **`harbor-tbench-regex-log` and `spreadsheetbench-s10` duplicate 23 `pipeline.yaml` lines over
  30 chars** — node type, `prompt_info`, `param_keys`, the `env_reward` mapping and every comment
  explaining why; they differ only in model, description and `max_turns`.
  `Connector.default_pipeline` / `default_node_config` exist for exactly this and none of the
  three harbor datasets uses them. The predicted trigger has now half-fired: the third
  (`sealqa-longseal-12`) is NOT the copy-paste this entry expected — it shares 10 lines with the
  first and 11 with the second, 29 of its 39 being its own — so the drift surface is the original
  PAIR, not the count. **Re-test:** intersect those two files' lines over 30 chars; under 23 means
  someone split them. A fourth dataset says nothing this one did not already answer.

## Blocked — named blocker

**Archive hygiene — the corpus it was sized against is gone again:**
- **Re-test: `ls .promptpotter/projects/*/measurements`, plus `compact-archive compact --dataset
  <name>` for the per-dataset split.** Operator-confirmed 2026-09-02: most of the measurement data
  was deleted, so the four pieces below have nothing to be built or verified against — the same
  state that stranded them the first time. **Build them BEFORE the next bulk delete, never after:**
  that is the one moment both halves exist at once, something to measure and a delete about to
  strand it. Order is fixed by the pieces themselves — inventory sizes the reclaim, and the map
  needs both.
- **Reclaim** — the destructive counterpart of `delete`, dataset-scoped, dry-run by default,
  refusing while a producer can append, and NAMING what it would strand for a dataset whose rows
  another dataset's inner runs may share. Nothing does this today: `delete` leaves the shared
  content-addressed rows standing (correctly — a sibling may replay them), `compact-archive` reaches
  only the fields a row does not read, and `reindex`'s GC is positive-identification-only, so every
  orphan is kept.
- **Attribution** — `LineageNode.sp_hash` is stamped forward-only, which is right and is enough for
  everything measured from here on. The DESIGN question outlives the data: an L4 outer cell should
  stamp the runs its inner campaign produced at the moment it spawns them, the only attribution that
  survives the sandbox being reclaimed. Decide it before the next L4 run banks rows nothing can name.
  ⚠️ **Do not re-file a backfill** — refused once on the merits (the schema a hash covers is
  persisted nowhere), and there is nothing left to backfill from.
- **Inventory, then the map** — run counts, byte split and replay rate by dataset / label / age off
  `MeasurementArchive`; then the selector, whose shape is settled and is a REACH MAP rather than a
  tree of checkboxes: the campaign family on the LEFT (`candidates/Forest` over
  `iter_family_courses`, which already descends `.inner/`), the archive partitions that selection
  REACHES on the RIGHT, load-bearing column = what is SHARED with campaigns outside the selection,
  because an `sp_hash` is not owned by a campaign.

**Behavior change (needs explicit sign-off, not a blind swap) — absent-vs-zero in the scoring spine:**
- **All-errored candidate scores `accuracy = 0.0`, not the honest `None`** — `compute_accuracy`
  (evaluators.py) returns 0.0 when no scoreable row exists; for all-deprecated that IS the verdict,
  but for all-errored it fabricates one. The honest `None` must propagate:
  `ScoredCandidate.accuracy` / `RoundResult.accuracy` → `float | None`, `compute_composite_fitness`
  handling a missing `accuracy` term without `ScoringTermMissingError` in `_running_scores` (an
  "unscoreable candidate" state, the outer sibling of `InnerCycleUnscoreableError`),
  `display_fitness` double-None, dashboard + `types.generated.ts` + chart null handling,
  `best_round_on_shared_cells` / `_apply_best` null-safety. **Smaller than filed:** its sibling
  `rescore_results` stamps errored rows `fitness = 0.0` citing `compute_accuracy`, which actually
  EXCLUDES them, and `_mean_fitness_by_cell` reads an absent key identically — so no cited reader
  depends on the stamp. Start from the audit that implies: every unguarded `r["fitness"]` subscript.
  Note the stamp makes a row's shape depend on replay (a freshly measured error row has no `fitness`
  key at all).
- **`optimization/cycle.py::CycleRoundState`'s accuracy/composite pair belongs to THIS entry, not to
  a bounded `or 0.0` sweep** — re-scoped 2026-09-02 by counting: `current_accuracy` /
  `current_composite_fitness` / `best_composite_fitness` reach 55 sites, including the escalation
  FSM's PERSISTED `l2_/l3_best_composite_fitness_at_entry` counters, so nulling them is a
  stall-ladder behaviour change and an on-disk shape change at once. `live_dashboard/state.py` moved
  its own copy of the pair to `| None`, which is the fixed twin — it does not make the tracker
  bounded. Blocker: the same sign-off as above; land them together or not at all.

  *(The bounded half of `or 0.0` SHIPPED — `views/ingress.py`, `review_md.py` →
  `l1/stats.py::_top_lifts`, `CycleResult` / `PromptExport.origin_composite_fitness`, and
  `output.py`'s two digest views. `mask/record.py::MaskCandidate.accuracy` was STRUCK: its 0.0 is a
  placeholder on a row every reader skips on the empty-`evaluators` guard beside it, so nulling it
  would type-infect `display_rank_key` for no reachable gain.)*

**Live L1 round (operator-gated):**
- **`*_override → *_updates` L1 delta-key rename.** `prompt_fields_override` /
  `task_context_override` / `pipeline_params_override` / `pp_override` are merges, not replacements,
  but named "override". **Decision (settle first):** unify the pipeline delta to
  **`pipeline_overlay`** everywhere (kills the short/long two-name tax); the prompt/context deltas
  become `*_updates`. Rename writer→reader in one commit (`dispatch/schemas.py::L1Variant` is the
  source of truth — the LLM contract auto-propagates). Full site map: grep `*_override`. **Blocker:**
  invalidates on-disk cycles (round-file key + optimizer structured-output contract) — verify against
  a FRESH cycle that completes round 1, not a resume.

**Security posture / migration:**
- **Backend-registration dedup** — `webapp/lib/hooks/useConnector.ts`'s client-side `distinct` /
  `seenEndpoints` collapse is a back-compat shim for per-dataset `BackendConnection` rows minted
  before the `wiring.py` one-row-per-`(base_url, backend_type)` fix. NOT a row-delete: a 3-step
  migration — (1) rewrite each campaign's `campaign.yaml::backend_id` to the canonical `local` (8
  stale ids across 82 campaigns, all → the same `127.0.0.1:8000` endpoint); (2) collapse the
  duplicate rows (needs a new `BackendStore.remove`); (3) make every re-wire path reuse the canonical
  id. Then delete the loop. Also: `wiring.py`'s `not backend_id` reuse block should guard on
  `existing.base_url == backend_url` (mint a distinct id on mismatch). Blocker: write + operator-run
  the idempotent migration on their data first — the loop is load-bearing until then.

**Cross-repo (TermNorm sibling at `OfficeAddinApps/TermNorm-excel/backend-api`):**
- **The TermNorm `/version` endpoint** is what remains genuinely owed on that side; this repo then
  bumps `termnorm.py::_EXPECTED_REVISION`. The per-request `model` beside it is now a nicety, not a
  blocker: `_compute_step_tokens` stamps every step-token entry with the node's model — the backend's
  per-node `model` when it reports one, else the model the dataset overlay pinned
  (`pipeline.yaml::nodes.{n}.config.model`, mandatory for an LLM node) — so per-node cost is
  derivable today, including for chars/4-estimated nodes.
- **A backend fix isn't observable without clearing a cache** — PP's measurement cache and
  TermNorm's `match_database` both key on query/searchpoint, never on backend code/revision, so a
  co-owned backend fix replays stale results. Fold the connector revision-pin into the
  measurement-cache key (or add a `--fresh` flag); confirm the TermNorm `/matches` short-circuit
  fires only on `verified` aliases. Workaround: clear `measurements/`.

**Coupon + BYO build (Lane A2 — blocked on the build itself; ADR-0003 § Host coupon):**
- **Adopt-in-new-code:** the new `grant.json` / `api_keys.json` stores MUST ride
  `read_json_optional` / `write_json` (the `UserStore` template, `store/io.py`) from day one — no
  hand-rolled readers. `shared/pricing.py` still hand-rolls `json.loads(...)` at three sites (one of
  them decoding a fetched payload); held separately because `shared/` importing
  `infrastructure/store/io` is an unresolved layer-DIRECTION question — resolve it before or
  alongside this build. (No longer an import-cycle question: the eager `store/__init__` is gone.)
- **Two host-wallet mechanisms** — `application/jobs/quota.py::admit_launch` plus the
  `User.spend_budget_usd_total` / `token_budget_total` lifetime ceilings, vs the new coupon
  (`grant.json`, ledger-derived, live). Two guards on one concern = the no-redundant-mechanism rule.
  Action: **delete the free-tier path**; coupon-remaining becomes the single host ceiling, read by
  the per-cycle `BudgetGate` every tick (D1/D2 in ADR-0003). Blocker: lands *with* the coupon —
  deleting first leaves the wallet unguarded. ⚠️ Two things the replacement must carry or it is a
  regression: BOTH units (an all-USD coupon re-opens the unpriced-model blindness D1's token arm
  covers), and the per-run **reservation** (`Job.cap_usd` / `cap_tokens`) — without it two concurrent
  launches are each admitted against the same remainder and the pair spends ~2× the ceiling.
- **`domain/run_records.py::TokenUsageRecord` lacks `key_source`** → `/auth/activity`
  `group_by=api_key` (`routers/auth.py`) fakes a *provider slug* as the key id. Once real
  `key_source: host|user` lands (declared on `TokenUsagePayload` in the asyncapi), replace the
  fake-slug derivation with the real dimension. Blocker: the coupon build adds the field.

**Needs a capability the closing directive does not open:**
- **The REST API has no inbound credential, so it cannot yet be the external integration surface the
  roadmap calls it.** `presentation/api/deps.py::resolve_identity` 401s unless
  `request.state.identity_ctx` is set, and `middleware/oidc.py` sets that from a browser **session
  cookie** and nothing else — no bearer token, no API key anywhere on the inbound path. A
  third-party caller reaches it only by running the server with `PROMPTPOTTER_AUTH=off`, i.e. with no
  auth at all. (The one bearer token the repo has,
  [`backend-integration.md`](../operations/backend-integration.md) § Connection security, runs
  PP→TermNorm — outbound, the other direction — so this gap is unowned.) Action: **document what is
  true before promising anything** — the surface is same-origin browser-session plus a local auth-off
  mode, and `developer/stable-api.md` lists it in neither §1–§7 nor §8, so it is implicitly internal
  today. Worked `submit → poll → fetch` examples and per-endpoint stability guarantees wait on the
  credential, which is a new capability. Blocker: that capability.
- **Nothing probes whether a route implements `response_format` before a run spends money** — an
  unsupporting model is discovered by paying for it (HTTP 405 outright, or empty content plus a
  burned schema-repair re-prompt). Same shape: swapping a model means hand-editing two
  `pipeline.yaml` lines and remembering to revert both, and a leaked pin mislabels the next run.
  Blocker: a probe and a swap-verb are both new capabilities.
- **`infrastructure/llm/json_parse.py::try_groq_json_validate_repair` meters a fabricated ZERO** — it
  rebuilds `LLMResponse` with `usage` hardcoded to zeros after a `json_validate_failed` 400 that was
  already billed. The 400 body carries no `usage`, so the count is unrecoverable, and
  `unpriced_tokens` is the wrong home: it means price unknown, not count unknown. Never estimate from
  content length. Dormant — Groq-only, every configured provider is `openrouter`. Blocker:
  `TokenUsageRecord` has no unknown-count dimension, and the account gate leans on a count always
  being knowable.

**A name that stopped describing what it names:**
- **`sp_budget_ttest` names a t-test nothing has run since 2026-05-01.** Paired t-test → Wilcoxon
  (`689d5dec`) → Bayesian PoBB (`3fbaf215`); the knob survived all three. Bigger than it reads: 81
  sites, and it is a *served wire field* and a declared request-schema key, not only a config knob.
  Live across `datasets/*/campaign.yaml`, `scripts/smoke_campaign.py`,
  `.claude/skills/potter-run/SKILL.md`, `test_numerics.py`, `test_integrity.py`. Action: rename
  writer→reader in one commit. Blocker: it is an on-disk config key under operator-curated
  `datasets/` — the rename is the operator's call, not a sweep's.

**Declared, no reader — each blocked on a served-surface decision, not on finding out:**
- **`manifests.py::ConfigCoupling.estimand` and `.knobs`** ride `openapi.generated.json` +
  `types.generated.ts`, and the one consumer (`ConfigMapPanel.tsx`) renders
  `severity`/`labels`/`relation`/`consequence`/`active`/`name` and neither of these; no CLI reader
  either (`check_couplings` takes `Coupling` objects direct). Action: drop from the response model.
  Blocker: deleting a served field is a wire decision.
- **`domain/results.py::CycleResult.origin_level_se`** — the L4 reader its own field comment names
  explicitly refuses it (`l4/proxies.py::mean_parent_level_se`: *"`origin_level` is deliberately
  absent … folding it into each side counts it twice"*). Blocker, and it is thinner than filed: it
  rides `cycle_result_command`'s `model_dump()` into CLI `--json`, but it is in neither
  `openapi.generated.json` nor `types.generated.ts` and no doc enumerates that key set — so this is a
  policy call about undocumented `--json` keys, not a documented consumer.
- **`dashboard.json::in_flight` (`live_dashboard/state.py::InFlightCall`)** is served on every poll
  and read by nothing — the whole webapp names it only in `types.generated.ts` and a test fixture
  (`max_cells_in_flight` is a different field, and is read). Its L4 twin is the other half of one
  decision: `live_dashboard/view.py::_handle_llm_call_progress` discards the record whole, so the
  inner-campaign `detail` that `runner/inner/spawn.py::_inner_detail` mints ("inner rX/Y · Δθ…")
  reaches the chat/ray `inner-progress` chip and never the outer dashboard — and an inner campaign
  sets no `in_flight` at all, since `spawn.py` fires the heartbeat directly rather than through
  `llm_call`. So serving `detail` here would still render nothing. Action: decide whether the outer
  dashboard shows inner progress at all — if yes, one served field and one reader; if no,
  `in_flight` goes. Blocker: adding or deleting a served field is a wire decision, and the chat feed
  already answers the operator's question.
- **`scoring/formula/matchers.py::SCORING_FUNCTIONS["relu"]`, `["smoothstep"]` and `["sigmoid"]`** —
  three, not two. No `campaign.yaml`, fixture or test uses any of them, and no doc names
  `SCORING_FUNCTIONS` or tabulates the DSL vocabulary, so an operator cannot discover them. Their
  neighbours all land. Action: document the DSL or drop the three. Blocker: it is an operator-facing
  DSL — reach is a product call.

**One rule, two languages, kept in sync by hand:**
- **`webapp/lib/derivations/allowedModels.ts::overlaySetsModelOutsideAllowed` re-implements
  `domain/pipeline_overlay.py`'s predicate line-for-line**, node-config walk included, with a Vitest
  suite locking the TS side against itself. Action: serve the predicate's verdict beside the fork
  affordance rather than re-deriving it. Blocker: that is a new served field. Re-test: does
  `AllowedModels` read a served verdict, or still compute one? (The `PROMPT_STRING_FIELDS` half of
  this entry SHIPPED — the set is generated by `build_ts_types.py::_emit_prompt_string_fields` and
  `prompt-fields.ts` re-exports it, keeping only the labels.)

**Needs a live run, not a decision:**
- **`_rebank_on_branch`'s re-bank has never been observed** — fixed to take each corrected round
  through the whole ingress, but the cycle it was measured on went with a store wipe, so the fix is
  reasoned, not seen. Repair a fork; confirm each corrected round carries its own `round:complete` on
  the branch.

Closed items are not tracked here — `git log` is the history layer.
