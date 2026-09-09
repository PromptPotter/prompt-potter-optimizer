# Code-Debt Cleanup — Backlog

**Nothing smaller than a multi-arc or blocked item goes here.** A fix you could make in the pass that found it is made there, not filed, and an adjacent finding is part of the topic you are already on. An item ships by being DELETED from this file.

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

- **Nothing in CI runs the app — no server is ever started and no browser is ever opened**, so a
  whole class of first-user breakage ships green. `scripts/smoke_wheel.py` reaches the API through
  `app.openapi()` and never issues an HTTP request, so a 500 inside a router or a static mount
  resolving to nothing passes both wheel jobs; there is no Playwright suite anywhere, and
  `webapp/components/onboarding/{AccessGate,ConsentGate,AllowanceSpent,WelcomeLockoutModal}.tsx` —
  the four surfaces a brand-new account meets before it sees anything else — have no test of any
  kind. Vitest is jsdom units of primitives and pure derivations; nothing renders `app/page.tsx` or
  navigates a route. Three arcs, and the first is worth more than the other two: (1) start uvicorn
  from the built wheel and fetch `/` plus one API read; (2) a scripted browser walk of the
  zero-campaign path, asserting console-clean; (3) coverage on the four onboarding components.
  **Re-test:** `grep -rn "openapi()\|uvicorn" scripts/smoke_wheel.py` — if it still never starts a
  server, arc 1 is open; `ls webapp/**/*.spec.ts webapp/e2e 2>/dev/null` empty means arcs 2–3 are.

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

- **The same seam, the other direction: a browser predicate whose server twin never returns its
  verdict — and it has been closed once already, wrongly.**
  `webapp/lib/derivations/nodeConfig.ts::overlaySetsModelOutsideAllowed` mirrors
  `domain/pipeline_overlay.py::overlay_sets_model_outside_allowed` rule for rule (a provider edit
  always taints; a model must sit in the node's permitted set; an absent node sanctions nothing) and
  drives `SteerForkPanel`'s pre-confirm warning. It was struck as fixed when the predicate's INPUT
  became server-authored — the served per-node `permitted` set — but the ask was the VERDICT, and the
  server reaches it only inside `fork-cycle` dispatch, where it 404s rather than answers. So deleting
  the client copy costs the operator the warning entirely; what is owed is a dry-run on the fork
  preview. **Re-test:** grep the served surface for a `steers_disallowed_model` field — while none is
  served, the browser copy is load-bearing and must not be struck again.

- **Holistic reframes — larger chunks, noted so they aren't mistaken for done; don't slip one into a
  release.** (1) **Tooltip/overlay consolidation:** most of the webapp's DOM `title=` attributes are
  teaching prose the browser renders as an unstyled, unselectable blob that dies on touch. Migrate
  **by string source, not by file** — `lib/terms.ts::TERMS` first, then the `VerifyPane` /
  `RoundFileView` header glossaries; leave the `title={same truncated string}` sites, where
  HoverCard is strictly worse. **Re-test:** `grep -rn "title={TERMS\[" webapp --include=*.tsx | wc -l`
  — while it reads 0, nothing has migrated. (2) **Whether L4 should reach the escalation machinery.**
  Not "each is built from scratch" — L2 and L3 already share `dispatch/`, `escalation/`, `cycle.py`
  and `OPTIMIZER_RESPONSE_MODELS`, and `application/optimization/CLAUDE.md` already splits the
  conceptual family from the structural one, which leaves only L4 outside, at the connector seam.
  So the question is not whether three strangers should converge; it is whether the recursion
  belongs inside the ladder it recurses on. That one has never been asked.

- **FIVE node kinds spell one concept — "runs a model".** `domain/pipeline_schema.py::NodeKind`
  closed the vocabulary and named the families, which is what makes the redundancy countable rather
  than merely suspected: `llm` (1 site, written by `presentation/teleprompter.py` and actually a
  VIEW kind spelled into a manifest), `generation` (20), `llm/optimizer` (4), `optimizer_prompt` (9),
  `agent` (1). `THINKING_KINDS` is the predicate that keeps the split from spreading, but it is a
  containment, not a fix — the five stay declarable and a sixth is one connector away. Not a rename:
  `runs_llm` reads `GENERATION` *specifically* while `_derive_node_kind` treats all five alike, so
  collapsing them decides which nodes newly carry the model axis, and every `datasets/*/pipeline.yaml`
  is operator-curated on-disk config (same standing as the `sp_budget_ttest` entry below — the
  operator's call, not a sweep's). Action: settle whether the survivor is `generation` or `llm`,
  then rewrite writer→reader in one commit. **Re-test:** `grep -rh "^    type: " datasets/*/pipeline.yaml
  promptpotter/assets/*/*/pipeline.yaml | sort | uniq -c` — fewer than five thinking spellings means
  someone started, and `NodeKind` names what is left.

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
- **All-errored candidate scores `accuracy = 0.0`, not the honest `None`** —
  `application/scoring/evaluators.py::compute_accuracy` already returns `None` for an EMPTY result
  set, so the type is `float | None` and only the ARM is wrong: it still returns 0.0 when the set is
  non-empty and nothing in it is scoreable. For all-deprecated that IS the verdict, but for
  all-errored it fabricates one. The honest `None` must propagate:
  `ScoredCandidate.accuracy` / `RoundResult.accuracy` → `float | None`, `compute_composite_fitness`
  handling a missing `accuracy` term without `ScoringTermMissingError` in `_running_scores` (an
  "unscoreable candidate" state, the outer sibling of `InnerCycleUnscoreableError`),
  `display_fitness` double-None, dashboard + `types.generated.ts` + chart null handling,
  `best_round_on_shared_cells` / `_apply_best` null-safety. **Smaller than filed:** its sibling
  `rescore_results` stamps errored rows `fitness = 0.0` citing `compute_accuracy`, which actually
  EXCLUDES them, and `_mean_fitness_by_cell` reads an absent key identically — so no cited reader
  depends on the stamp. Audit every unguarded `r["fitness"]` subscript; note the stamp makes a row's
  shape depend on replay (a freshly measured error row has no `fitness` key at all).
  **The producer is one line and the propagation is not — attempted and reverted rather than
  half-landed.** Splitting the arm (`0.0` only where a row was DEPRECATED, `None` where every
  non-scoreable row errored) is three lines and the suite stays GREEN, because nothing exercises an
  all-errored candidate — so landing it alone turns a fabricated 0.0 into a `ValidationError` at
  `l1/population.py`'s single `ScoredCandidate` construction, on the L4 path where all-errored cells
  actually happen. Typing that one field then names ten files' worth of seams, and they are
  DECISIONS rather than casts: `display_rank_key` (where an unscoreable candidate ranks),
  `mask/load.py::_mask_candidate`, `views/ingress.py::ScoreEntry`, `CycleRoundState`'s pair below,
  `resume_and_fork/repair.py`, `runner/inner/spawn.py` (a `None - float` on the L4 lift), and the
  three that take an ORIGIN accuracy — `ObservabilityBridge.start_campaign`, `build_run_observers`,
  `LiveDisplay.set_origin` — which ask what a campaign whose origin never scored even IS.
  `RoundResult.accuracy` is a second pass on top of that, then the wire and the charts.
  **Re-test:** make the producer edit and run `mypy promptpotter` — the list it prints IS the arc's
  width, and it is measured rather than estimated.
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

**Needs a capability M13 does not open** — the no-new-features clause is retired, so the bar is no
longer "is a feature allowed" but "does the preprint need it", and these do not:
- **The REST API has no inbound credential, so it cannot yet be the external integration surface the
  roadmap calls it.** `presentation/api/deps.py::resolve_identity` 401s unless
  `request.state.identity_ctx` is set, and `middleware/oidc.py` sets that from a browser **session
  cookie** and nothing else — no bearer token, no API key anywhere on the inbound path. A
  third-party caller reaches it only by running the server with `PROMPTPOTTER_AUTH=off`, i.e. with no
  auth at all. (The one bearer token the repo has,
  [`backend-integration.md`](../operations/backend-integration.md) § Connection security, runs
  PP→TermNorm — outbound, the other direction — so this gap is unowned.) What is TRUE is now said out
  loud — [`developer/stable-api.md`](../developer/stable-api.md) § 8 names the surface rather than
  leaving it implicitly internal. What remains is the credential itself, and the worked
  `submit → poll → fetch` examples and per-endpoint guarantees that wait on it. Blocker: that
  capability.
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

**Needs a live run, not a decision:**
- **`_rebank_on_branch`'s re-bank has never been observed** — fixed to take each corrected round
  through the whole ingress, but the cycle it was measured on went with a store wipe, so the fix is
  reasoned, not seen. Repair a fork; confirm each corrected round carries its own `round:complete` on
  the branch.
- **The `evolved` and `seed` provenance layers have never been stamped by real data.**
  `pipeline_resolve.py::_evolved_overlay` reads a candidate's sparse `pipeline_params_override`
  and the seed layer reads the cycle seed's `pipeline_overlay`; every candidate on this workspace
  is prompt-only, so both feeds are dead here and only the merge primitive beneath them is
  covered (`tests/test_pipeline_resolve.py`). A campaign that actually MOVES a node param
  exercises both, and the trap they guard is documented at `_evolved_overlay`: reading
  `resolved_pipeline_params` instead would stamp every param `evolved` at once.
  **Re-test:** `grep -rho '"pipeline_params_override": [^,}]*'
  .promptpotter/projects/*/campaigns/*/cycles/*/rounds/*.json | sort -u` — while the only
  distinct value is `null`, no live row has reached either layer.

Closed items are not tracked here — `git log` is the history layer.
