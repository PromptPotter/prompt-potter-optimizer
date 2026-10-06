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

> **And every entry names the work that will REACH it: `Rides with:`.** The file, surface or kind
> of pass that lands on this anyway — written for a reader holding no context, so that a session
> already in there does the item ON THE WAY, inside the commit it came to make. This is the
> forward half of "fixed in the pass that found it": nothing here is large enough to earn a
> session of its own, which is what made it a backlog item rather than a task, so an entry that
> names no carrier is waiting for a pass that will never be scheduled. Name the carrier even when
> it is a whole surface ("any stylesheet change") — a vague one still fires, and none never does.
> Under § Blocked the blocker already IS the carrier: whatever lifts it is the pass that lands the
> item, so a second line there would only restate it.

**Not debt — goes elsewhere, and none of it comes back here:**

| Kind | Home |
|---|---|
| Forward feature work | [`roadmap.md`](roadmap.md) |
| Architectural decisions | [`../architecture.md`](../architecture.md) |
| **A refusal that protects code** | **Beside that code** — a docstring is re-read whenever someone touches the function; a backlog line never is. |
| How to HUNT debt, and what to skip on sight | [`../developer/conventions.md`](../developer/conventions.md) § Auditing for debt |

## Open — multi-arc, no blocker

A leading `NEXT` marks the one to take up cold when nothing else is in hand.

- **NEXT — the prompt cache is a cost lever only on a route that stays on ONE host, and nothing
  makes a route stay.** `infrastructure/llm/openai_compat.py` pins hosts only where a node config
  names `route_order` by hand, and so do `connectors/harbor.py` and `connectors/dbllmbench.py`. A
  backend node declares none by default, so a gateway spreads its calls over every host it lists
  and each one starts cold: the stored ledgers read 1.2% cache capture on the backend, which is
  72% of spend. Measured 2026-10-05 on `reactome-typeql-42`, where every call re-sends one 16k
  prefix: `xiaomi/mimo-v2.6-flash` billed $0.030 per M input tokens unpinned and $0.019 pinned to
  `xiaomi`, against a $0.14 list price and a $0.0028 cached one. So any design that counts on the
  cache — a cost term in fitness, a spend estimate, a search front on cached price — holds only
  under a condition no layer establishes or reports. Action: make stickiness the default rather
  than a per-node secret. OpenRouter keeps a route on the host that served a `session_id`
  (ten minutes idle), so send one per prompt prefix from the one request builder and from each
  connector that sends past it; keep `route_order` as the explicit override; and serve cache
  capture per node beside its spend, so a scattered route reads as one. **Rides with:** any change
  to the request builder in `openai_compat.py`, a connector's route handling, or the caching arc
  (`.scratch/caching-arc-state.md`). **Re-test:** `grep -n session_id
  promptpotter/infrastructure/llm/openai_compat.py` — no hit means still open; then run
  `.scratch/typeql-bench/screen_model.py xiaomi/mimo-v2.6-flash none 0,4,5,6,7,8` with and without
  a trailing `xiaomi` and compare billed dollars per input token — a gap means the default route
  still scatters.

- **Raising the in-flight depth takes effect only when a call LANDS.**
  `application/scoring/query_loop.py::run_walks` re-reads `_armed_cells` every step, then blocks
  on `asyncio.wait(calls, return_when=FIRST_COMPLETED)`, so a press that widens the window waits
  for whatever is already out — on Harbor a whole agent episode, minutes. Seen 2026-09-18: a
  Qwen run launched at depth 1 and set to 5 stayed at one cell until the first episode returned.
  Operator: a press applying at once is **the only behaviour that should exist**, not a UX nicety.
  Action: the wait also wakes on the depth rising (`.runtime/sample_lookahead.json` is written by
  another process, so a short poll alongside the calls, armed only while the depth is below
  `max_cells_in_flight`), and the fill that follows is the one already there. **Rides with:** any
  change to `run_walks`, the look-ahead flag, or the Harbor scoring path. **Re-test:** launch any
  harbor dataset, set the depth above 1 in the browser while the first cell is out, and count open
  `spend_hold` records in the cycle's `.runtime/ledger.jsonl` — one until that cell lands means
  this is still open.

- **A phase VIEW is on-disk shape, and nothing treats it as one.** Resume folds the seed cycle's
  ledger through `projections/live_dashboard/projection.py::resolve_resume_state`, so every key
  `_apply_phase` reads off `payload.view` is a persisted contract — while a view is declared in
  `application/views/view_models.py` as a display dataclass and changed as freely as one. Adding
  `BenchEnterView` made every cycle paused before it unresumable (`KeyError: 'subject'` at init),
  and `maintenance/restamp.py::compact_cycle_ledgers` cannot repair it: the old record never held
  the fact. `scripts/offline_run.py` has no pause-and-resume leg, so the gate stayed green. Owed is
  a design answer, not a tolerant read: either the fold reads only declared record fields and views
  stay display-only, or a view change is priced as a persistence change at the writer. **Rides
  with:** any new or changed phase view. **Re-test:**
  `grep -n -i resume scripts/offline_run.py` — empty means no check resumes a cycle, so the next
  view change strands paused cycles the same way.

- **The responsive walk records no Lighthouse score, and sizes the candidates card at no width.**
  The six-width sweep (`e2e/walk/responsive.spec.ts`) catches content DELETED by an
  `overflow:hidden` wrapper or a `viewBox`'d SVG that scaled instead of overflowing — the
  outer-signal panel and the lineage forest are now swept there too, at every width. The
  candidates card is not: it needs a campaign of a shape the walk cannot count on finding. The
  sweep still says nothing about whether a phone layout is USABLE, which stays a human pass, and
  the original mobile pass took no Lighthouse number. Action: one Lighthouse run on the dashboard
  at 375, written down here. **Rides with:** any stylesheet or layout change that already has a
  browser open — the number is one run once you are there — and, for the card, the next spec with
  a campaign of the right shape to hand (the spend tier mints one). **Re-test:** `grep -n candidate
  webapp/e2e/walk/responsive.spec.ts` — empty means the card is still unswept; the Lighthouse half
  has nothing on disk to grep for a number that was never taken.

- **Optimizer model repair-rate on heavy L2/L3 structured output — unmeasured.** What is owed is the
  measurement: a live cycle reaching L3, read under the model
  `promptpotter/assets/optimizers/potter/pipeline.yaml` currently pins — read it off that file, never off
  this entry. **Rides with:** the next supervised campaign that escalates. The run is the expensive
  part and someone is already paying for it; this is a read of what it wrote. **Re-test:** whoever
  supervises that run, asked what share of its L2/L3 optimizer calls needed a parse repair — a
  number closes this; no number leaves it standing.

- **The `prompt_info` trap has no GUARD, and the obvious one is wrong.** A node that omits it scores
  every variant identically as no-skill and raises nothing — stated at the decision point,
  [`../developer/adding-a-surface.md`](../developer/adding-a-surface.md) § 5. Requiring
  `prompt_info` whenever `optimizer.param_keys` names a prompt field would trip on every L4 run,
  since `promptpotter-self` deliberately declares the first without the second, so what is owed is a
  design answer rather than a check. **Rides with:** the next connector added, or any edit to where
  a node's `param_keys` are validated. **Re-test:** grep `promptpotter/` for a raise naming
  `prompt_info`; while none exists, the no-skill shape still passes silently.

- **DSPy cells cannot run under a spend ceiling.** It serves no `spend_bound`, so
  `scoring/sample_measurement.py::cell_bound` leaves every cell unbounded and the spend book refuses
  it — loudly, before it is sent. Action: declare `Connector.sent_spend_bound` from the student
  node's own config, as harbor does, and check the input against it before the call. **Rides
  with:** the next change to `connectors/dspy_module.py`. **Re-test:** a dspy campaign under a USD
  ceiling — every cell refused with "no rate bounds what it may cost" means this is still open.

- **No gate stops a router importing `promptpotter.infrastructure.store`**, so a route that picks WHICH rows or in WHAT ORDER stays unreachable from every other entry point (`presentation/CLAUDE.md` § Out-of-bounds). Action: move each router's composition into `application/` (template: `routers/datasets/leaderboard.py` → `application/scoring/cells.py::measurement_log`), then add the import ban to the gate. **Rides with:** any change to one of those routers — each takes its own module off the list. **Re-test:** `grep -rl --include=*.py promptpotter.infrastructure.store promptpotter/presentation/api/routers` — a non-empty list means the gate cannot land yet.

- **`webapp/lib/derivations/round-samples.ts` re-walks the sample mark with three arms** (ERR / HIT / MISS) over raw round-file rows, where `domain/dashboard_rows.py::sample_status` has four — so a historical UNSC row reads as a wrong answer. Action: serve the mark on the row the client reads (the round file's `all_candidate_results`, or route the reader through `/cells`, which already serves `CellRow.status`), then delete the client ladder. **Rides with:** any change to `round-samples.ts` or the round-file result row. **Re-test:** `grep -n '"UNSC"' webapp/lib/derivations/round-samples.ts` — empty while the client ladder still has three arms.

- **The dashboard is a single-page React app that carries Next.js as its build tool.**
  `webapp/next.config.ts` sets `output: "export"`, so no Next server runs anywhere: FastAPI serves
  `webapp/out`. The source reaches the framework through `next/dynamic` in
  `components/shell/AppShell.tsx` and through type imports in `app/layout.tsx` and
  `app/manifest.ts`; the route tree is `/` and `/login` under one layout, with no API route, no
  server action, no middleware, and neither `next/link`, `next/navigation` nor `next/image`. What
  Next supplies is Turbopack, the React Compiler switch, the dev-mode `/api` rewrite and the ESLint
  preset — and for that the lock carries `next`, which draws advisories of its own (a critical one
  was patched 2026-10-06), and `eslint-config-next`, whose
  `@next/eslint-plugin-next → fast-glob → micromatch → braces` chain holds a high advisory with no
  patched release (GHSA-vfj7-8cjw-p6xm), which fails `gate.py --only npm-audit` and so the audit
  step in `publish.yml`. Action: build with Vite, which Vitest already runs on. That is a new
  build config with the `/api` dev proxy and the React Compiler plugin, an `index.html` shell and
  client entry in place of `app/layout.tsx` and `app/manifest.ts`, `React.lazy` for `next/dynamic`,
  two routes (the export's trailing-slash layout is what FastAPI's `StaticFiles(html=True)`
  resolves, so keep it), and the ESLint config rebuilt from `typescript-eslint` and the React,
  hooks and a11y plugins while keeping the barrel-import rule in `webapp/eslint.config.mjs`. Then
  everything keyed to Next's commands and output: the `next-build` check and its `tsc` /
  `playwright` ordering in `scripts/gate.py`, the `.next/types` include in `tsconfig.json`,
  `webapp/e2e/serve.mjs`, `scripts/build_release.py::_WEBAPP_SRC`, `build:deploy` in `publish.yml`,
  the eslint cache step in `ci.yml`, the `/serve` skill and `webapp/CLAUDE.md`. Keep the output
  directory named `out` and most of that list falls away. Unverified: whether the rebuilt ESLint
  set brings `braces` back through its own `micromatch` — settle that FIRST, since it decides
  whether the migration closes the audit or only shrinks the lock. **Rides with:** the next
  webapp dependency pass — a `next` advisory, a Dependabot bump of `webapp/package-lock.json`, or
  the release audit going red on `braces`. **Re-test:** `git grep -n '"next"' webapp/package.json`
  — a hit means Next is still the build; `npm ls braces` in `webapp/` names what still pulls it;
  and `git grep -hoE 'from "next(/[a-z/-]+)?"' -- webapp | sort | uniq -c` recounts the imports,
  which is the size of the job.

## Open — surfaced by the head-to-head arc

Filed at the operator's ask rather than under the multi-arc bar above. A leading **INVESTIGATE**
means nobody has established that the item is real, solvable or worth its cost: answer that first,
and delete the entry if the answer is no.

**The ruler and the peers**

- **INVESTIGATE — GEPA's round ceiling doubled.** `optimizers/gepa/members.py::GepaRuntime.
  round_cells_ceiling` now prices the parent's cells beside the child's, so
  `preflight.py::check_search_pool_holds_round` may warn on, or refuse, an arm whose cap held a
  round before. **Rides with:** the next head-to-head mint. **Re-test:** mint a GEPA arm at the
  budget the last head-to-head used and read the preflight line.
- **INVESTIGATE — ruler linking is one pass per round.** `domain/ruler.py::extend_ruler` links a
  cell only through an arm already on the ruler, so a cell reachable through an arm linked in the
  same pass stays off it until the next round. Unknown whether any cell is ever left that way.
  **Rides with:** any change to `extend_ruler`. **Re-test:** count `unmeasured_delta` caveats per
  round in a potter cycle's `rounds/*.json`; a caveat that clears one round later is this.
- **`candidate_scored` fires before the per-arm caveat is stamped.** `runner/measurement.py::
  measure_population` stamps `ThetaCaveat.UNMEASURED_DELTA` after `_walk_population` has already
  emitted each report, so the live event and the closed round disagree. Action: stamp where the
  report is built, or re-emit. **Rides with:** any change to `measure_population`. **Re-test:**
  compare one arm's `theta_caveat` in the ledger's `candidate_scored` record and in its round file.
- **INVESTIGATE — the webapp's per-arm text for `unmeasured_delta` is unverified**, and so are the
  docs and tests the ruler-linking change touched: neither was read after it landed. **Rides
  with:** any webapp caveat surface. **Re-test:** grep `unmeasured_delta` under `webapp/lib`, and
  `git log -p` that change's `docs/` and `tests/` hunks.

**Spend**

- **Spend by measurement role is not built.** Overlap and catch-up spend are attributed by matching
  token triples. Action: one optional role on `domain/run_records.py::TokenUsageRecord`, stamped
  in `infrastructure/llm/telemetry.py::emit_token_usage` from `shared/instrument.py::
  measured_candidate()` as `round` is. **Rides with:** any change to `TokenUsageRecord` or the
  spend book. **Re-test:** grep `TokenUsageRecord` for a role field.
- **Hold bounds leave three gaps.** `_billed_most` is not seeded on resume; `reserved()` does not
  hold `held_at(...)`, so a walk can plan a cell its reservation refuses; and `scoring/
  query_loop.py::Walk.end` drops a paid look-ahead cell that crossed a block decision. **Rides
  with:** any change to `spend_book.py`, `quota.py` or `run_walks`. **Re-test:** resume an arm and
  read its first `spend_hold` against the ledger's billed maximum for that label.
- **A paused run prints no spend line.** The run-end line carries billed and incurred; a pause
  writes neither to the readout. **Rides with:** any change to the readout's stop lines.
  **Re-test:** `pause` a running cycle and grep its `readout.log` for the spend line.
- **INVESTIGATE — `runner/campaign_result.py` and a dataset with `split.bench == 0`.** Whether the
  bench pass, the `bench_origin` reserve and the headline all degrade to a stated reason rather
  than a zero is unread. **Rides with:** any change to `campaign_result.py`. **Re-test:** run
  `scripts/offline_run.py` on a split with no bench rows.
- **INVESTIGATE — the bench pass runs serial at look-ahead depth 1**, which is clock, not dollars.
  Whether the held-out pass should ride the depth the search uses is undecided. **Rides with:**
  any change to `runner/bench.py`. **Re-test:** time one bench pass against its cell count.

**Run state and the tree**

- **`scripts/offline_run.py` has no pause-and-resume leg** — the re-test of the phase-view entry
  above, and what would have caught it. **Rides with:** that entry.
- **INVESTIGATE — `presentation/cli/commands/reset.py` does not recognise `.cache`**, so `reset`
  reports it as unknown rather than preserving or clearing it by rule. Changing the preserve list
  was refused by the permission layer once; it needs the operator's explicit say. **Rides with:**
  any change to `reset.py`. **Re-test:** run `reset` dry and read what it says about `.cache`.
- **INVESTIGATE — `dashboard.json` carries each candidate's rows twice**, as `samples` and as
  `sample_lines`, and they are most of the file: on a wide panel one cycle's dashboard passes half
  a megabyte. The file is indented and reads fine; the question is whether both shapes have a
  reader. **Rides with:** any change to `projections/live_dashboard/blocks.py`. **Re-test:** grep
  `sample_lines` under `webapp/lib` and `webapp/components`.
- **INVESTIGATE — three items filed with their re-tests in `.scratch/debug-arc-seed.md`**: errored
  cells (`finish_reason=error` then a schema repair) and whether their rate differs by arm; the
  potter `plan` panel one char over its runaway backstop; and potter's round-5 L2 layout refusal.

**Webapp — the control-plane session's files**

- **Four stale reads of a run's ending.** `components/compare/ChannelCards.tsx` keeps a
  `?? row.stop_reason` default the typed field made dead; `e2e/spend/l4.spec.ts` matches a stop
  by a regex over its name where `STOP_REASON_OUTCOMES` answers; the `RunMasthead.tsx` headline
  does not read the stop table; and `docs/specs/roadmap.md` still describes LEVI's proxy as drawn
  on the pool. **Rides with:** that session's next webapp pass. **Re-test:** grep `stop_reason`
  under `webapp/components/compare` and `webapp/e2e/spend`.

## Bypasses — one defect class, held for ONE holistic pass

**A path that goes around the mechanism the rest of the code rides, and re-derives the answer
itself.** The model case, root-fixed 2026-09-19: Harbor's agent called its provider through
litellm, outside our client, so one 429 had three outcomes depending on which path met it. The
entries below are the same class, found by a three-way audit that day, and they are filed
TOGETHER rather than patched one by one on purpose: read side by side they sort into three shapes,
and each shape names an upstream redesign that makes the class hard to write at all. Patched
singly, each fix is one more local copy of the rule it restores. **Rides with:** that redesign.
A pass already rewriting one of these symbols may take its entry, but takes the shape's remedy,
never a local patch. **Re-test:** each entry's command; a hit means it still stands.

**Shape 1 — a send made ON OUR BEHALF re-derives what `_admitted_send` decides for our own.**
TermNorm over HTTP, terminus-2 and DSPy through litellm reach the spend book as a status code and
bare token counts, so each path invents its own worst case, failure category and settlement.
Remedy: one typed per-attempt outcome (sent · may-have-billed · usage-or-unknown · failure
category · retry-after) that our clients emit and every relay must produce, read by ONE classifier
and ONE settle rule (unknown ⇒ the whole bound); one retry budget and one time budget passed down
through every nested loop.
- `application/scoring/sample_measurement.py::cell_billing` settles a relayed timed-out attempt
  at $0 (`StepTokenUsage` drops TermNorm's `attempts`) — a possibly-billed send the ceiling never
  sees. Silent. `grep -n '"attempts"' promptpotter/domain/spend.py` (empty).
- `infrastructure/llm/pricing.py::_fetch_route_ceiling` returns `None` for a FAILED catalogue fetch
  as for an unpriceable route, so an OpenRouter outage stops a capped run as `SPEND_BUDGET` with
  advice that cannot help, and re-fetches on every send. Loud, wrong reason. `grep -n -A3 "except
  (urllib.error.URLError, OSError, TimeoutError, json.JSONDecodeError)" promptpotter/infrastructure/llm/pricing.py`.
- `connectors/dspy_module.py::_in_process_run` calls its student through litellm with none of the
  typed cell errors harbor raises, so a 429 or an empty account becomes UNKNOWN rows (the spend
  half is the DSPy entry above). `grep -c "CellThrottledError\|CellSendRefusedError"
  promptpotter/connectors/dspy_module.py` (0).
- Smaller, same shape: `infrastructure/llm/base.py::_admitted_send` runs `acquire_reservation`
  inside `admitted` but before its `try`, so a `RequestTooLargeError` or a cancel while queued
  charges an unsent request its whole bound (only with `RATE_LIMITS` set);
  `diagnostics/probe_reasoning.py` reads every exception as "refuses this effort";
  `tracing/langfuse_client.py` runs a second private 429 loop beside `decide_429_wait`.

**Shape 2 — a fact REBUILT from its inputs where the producer already resolved and stamped it.**
Each rebuild drops one layer — the seed, the framing, an ancestor's delta, the typed error, the
successor, the tenant tier. Remedy: resolution writes a persisted, typed, addressable artifact
(per cycle: config after seed and overrides, dataset tier, validated panel; per measured point:
opt_sp, resolved params, `sp_hash`), archive rows carry a required `error_category` behind
predicates, and later readers accept nothing else.
- `application/diagnostics/verify.py::verify_candidate` (and `noise_floor.py`) rebuild the config
  from `campaign.json` plus the proposal's sparse delta, dropping every adopted ancestor's move and
  a fork seed's overlay, then spend on cells of a config that never ran. Silent. `grep -n
  "validate_campaign_config(campaign.config)" promptpotter/application/diagnostics/verify.py promptpotter/application/diagnostics/noise_floor.py`.
- `resume_and_fork/ab_replay.py` and `diagnostics/noise_floor.py` rebuild C0 with
  `OptSearchPoint.from_prompt_fields(round0)` — a second origin recovery beside
  `origin.py::resolve_origin_opt_search_point`, without the framing; `ab` then splits the origin
  into two arms on its δ ruler. `grep -n "from_prompt_fields(origin.prompt_fields)\|from_prompt_fields(round_file.prompt_fields)"
  promptpotter/application/bench/resume_and_fork/ab_replay.py promptpotter/application/diagnostics/noise_floor.py`.

**Shape 3 — an act or a reading lives in ONE adapter, so the entry points disagree.** The
canonical mechanisms are ones an adapter may call, not the only path an act can take. Remedy:
every operator act runs one application pipeline (validate → admit → apply → record) whose
exemptions are parameters rather than skipped calls; every reading an operator acts on is one
served field behind one read facade; `presentation/` imports facades only, and no route returns
an untyped dict.
- The config-drift gate on resume (`presentation/cli/commands/resume_command.py`, `root_content_hash`
  against the recomputed cycle id) is CLI-only: web Resume, `step-cycle` and a fork's launch
  continue a cycle under an edited `pipeline.yaml`, starting prompt or framing. Silent. `grep -rn
  root_content_hash promptpotter/application/jobs promptpotter/application/runner promptpotter/application/bench/resume_and_fork` (empty).
- `application/views/render/phase.py::render_progress_table` differences θ across rounds and
  advises a "Plateau" stop without `AbilityReading.comparable_to` or the served caveat, where the
  browser filters by ruler. `grep -n "theta - prev\|comparable_to" promptpotter/application/views/render/phase.py`.
- A fork's run limits are reconciled in the browser (`webapp/lib/derivations/forkReconcile.ts`: the
  parent's remaining rounds and dollars) and not by `fork_siblings.py::mint_operator_fork`, so a
  browser steer and `resume --steer` mint different ceilings. `grep -rln forkReconcileDefaults webapp/lib webapp/components`.
- The check-in wire is hand-declared in `webapp/lib/api/draft-types.ts` (`DraftCampaignWire`,
  `DraftPatch`, the `ProvenanceTag` union) because the routes return `dict[str, Any]`; a field or
  `Provenance` member added in Python compiles green and renders blank. `grep -n "export type
  ProvenanceTag" webapp/lib/api/draft-types.ts`.
- "Is the backend up?" has three answers: launch admission asks `connector.preflight`, the health
  route (`presentation/api/routers/backends.py`) a bare `GET /status` (a URL no in-process connector
  serves), the embedded launch nothing. `grep -n check_status promptpotter/presentation/api/routers/backends.py`.
- The `/potter-run` skill reads `run_phase` off `dashboard.json`, where it is `exclude=True` and
  never written, and no CLI verb serves `derive_run_phase` or the machine queue `cancel-queued`
  needs. `grep -n run_phase .claude/skills/potter-run/SKILL.md`.
- `webapp/components/verify/VerifyPane.tsx` computes "N cached" by its own formula, which
  `verify_candidate` computes differently and never persists. `grep -n "const cacheReplays"
  webapp/components/verify/VerifyPane.tsx`.

## Blocked — named blocker

- **Concurrent sibling cycles of one campaign each spend up to the whole ceiling.**
  `spend_budget_usd` binds a CYCLE (`runner/entry.py::_build_budget_gate` seeds its book off that
  cycle's folded history), so two forks launched side by side are each admitted the full ceiling
  and the campaign can spend twice it. **Blocker:** a decision only the operator can make — whether
  the ceiling is the campaign's or the cycle's. The campaign's answer is one book per campaign,
  shared by its live cycles through the job registry the account wallet already reads.
  **Re-test:** ask the operator which the ceiling is; while unanswered, this stands.

- **A head-to-head's arms do not share one optimizer model**, so "the only difference is the
  optimizer" is false at the proposer: each preset under `promptpotter/assets/optimizers/` pins its
  own, and an arm's lift and its dollars both carry the model as well as the method. **Blocker:** a
  decision only the operator can make — each peer on its paper's model class (faithful, confounded)
  or every arm on one model (controlled, a declared deviation per peer). **Re-test:**
  `grep -n "model:" promptpotter/assets/optimizers/*/pipeline.yaml` — more than one distinct model
  across presets means the arms still differ, and ask the operator which reading the bench reports.


**Archive hygiene — the reclaim, its attribution, and the map over both:**
- **Re-test: `compact-archive inventory`**, which is what sizes the three below: runs, cells, bytes
  and replay rate by dataset / label family / age, plus the index rows carrying no detail file,
  which is what makes every other count an upper bound. It supersedes the 2026-09-02 reading that
  most of the measurement data was gone — run it before concluding a piece has nothing to be built
  against. **Build them BEFORE the next bulk delete, never after:** that is the one moment both
  halves exist at once, something to measure and a delete about to strand it.
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
- **The reach map** — the selector, whose shape is settled and is a REACH MAP rather than a tree of
  checkboxes: the campaign family on the LEFT (`candidates/Forest` over `iter_family_courses`,
  which already descends `.inner/`), the archive partitions that selection REACHES on the RIGHT,
  load-bearing column = what is SHARED with campaigns outside the selection, because an `sp_hash`
  is not owned by a campaign. The partitions are now countable; which of them a given family
  reaches is what nothing answers, and it is the join `sp_hash` → `prompt_fields_id` would buy.

**Cross-repo (TermNorm sibling at `OfficeAddinApps/TermNorm-excel/backend-api`):**
- **The TermNorm `/version` endpoint** is what remains genuinely owed on that side; this repo then
  bumps `termnorm.py::_EXPECTED_REVISION`. The per-request `model` beside it is now a nicety, not a
  blocker: `_compute_step_tokens` stamps every step-token entry with the node's model — the backend's
  per-node `model` when it reports one, else the model the dataset overlay pinned
  (`pipeline.yaml::nodes.{n}.config.model`, mandatory for an LLM node) — so per-node cost is
  derivable today, including for chars/4-estimated nodes. **Re-test:** `termnorm.py::_EXPECTED_REVISION`
  is `None`; the moment it holds a string the endpoint landed and this entry goes.
- **A backend fix isn't observable without clearing a cache** — PP's measurement cache and
  TermNorm's `match_database` both key on query/searchpoint, never on backend code/revision, so a
  co-owned backend fix replays stale results. Fold the connector revision-pin into the
  measurement-cache key (or add a `--fresh` flag); confirm the TermNorm `/matches` short-circuit
  fires only on `verified` aliases. Workaround: clear `measurements/`. **Re-test:** grep the package
  for `--fresh` and for any revision term on the measurement-cache key; while both miss, this stands.

**A second containerized connector:**
- **`package_cache` is honoured by harbor alone, and nothing refuses a dataset whose connector
  ignores it.** The dataset key is already backend-neutral; what is harbor-only is the reader
  (`harbor.py::PACKAGE_CACHE_SCOPES`). Action: a `Connector.package_cache_scopes` declaration,
  empty by default, that run init checks the declared scope against — with one connector it would
  have one reader and guard nothing. **Re-test:** a second `connectors/*.py` that runs cells in a
  container; while harbor is the only one, this waits.

**Coupon + BYO build (Lane A2 — blocked on the build itself; ADR-0003 § Host coupon + BYO keys):**
- **Re-test for all three below: grep the package for `grant.json`.** It is prose-only today, so
  while that grep reaches no code the build has not started and every premise here stands.
- **Adopt-in-new-code:** the new `grant.json` / `api_keys.json` stores MUST ride
  `read_json_optional` / `write_json` (the `UserStore` template, `store/io.py`) from day one — no
  hand-rolled readers.
- **Two host-wallet mechanisms** — `application/jobs/quota.py::admit_launch` plus the
  `User.spend_budget_usd_total` / `token_budget_total` lifetime ceilings, vs the new coupon
  (`grant.json`, ledger-derived, live). Two guards on one concern = the no-redundant-mechanism rule.
  Action: **delete the free-tier path**; coupon-remaining becomes the single host ceiling, read by
  the run's spend book at every admission (D1/D2 in ADR-0003). Blocker: lands *with* the coupon —
  deleting first leaves the wallet unguarded. ⚠️ Two things the replacement must carry or it is a
  regression: BOTH units (an all-USD coupon re-opens the unpriced-model blindness D1's token arm
  covers), and the per-run **reservation** (`Job.cap_usd` / `cap_tokens`) — without it two concurrent
  launches are each admitted against the same remainder and the pair spends ~2× the ceiling.
- **`domain/run_records.py::TokenUsageRecord` lacks `key_source`** → `/auth/activity`
  `group_by=api_key` (`routers/auth.py`) fakes a *provider slug* as the key id. Once real
  `key_source: host|user` lands (declared on `TokenUsagePayload` in the asyncapi), replace the
  fake-slug derivation with the real dimension. Blocker: the coupon build adds the field.

**Needs a capability neither the bench nor the preprint opens** — the no-new-features clause is
retired, so the bar is no longer "is a feature allowed" but "does M13 or M14 need it", and these do
not. The bench does not rescue the first one in particular: a third party ships an optimizer through
an entry point, in-process, so it never touches the inbound credential.
- **The REST API has no inbound credential** — owned by
  [`../developer/stable-api.md`](../developer/stable-api.md) § 8. What is NOT stable. Owed HERE: the
  credential itself, plus the worked `submit → poll → fetch` examples and per-endpoint guarantees
  that wait on it. Blocker: that capability. **Re-test:** grep `promptpotter/presentation/api/` for
  a bearer or API-key reader on the inbound path; while the session cookie is the only one, this
  stands.
- **Swapping a model means hand-editing two `pipeline.yaml` lines and remembering to revert both**,
  and a leaked pin mislabels the next run. The half of this that was about `response_format` is
  closed: the OpenRouter catalogue's `supported_parameters` already answers whether a route takes
  the key, and `PipelineSchema._refused` spends that answer on the search space rather than on a
  badge — so an unsupporting model no longer has to be discovered by paying for it. Blocker: the
  swap-verb, which is a new capability. **Re-test:** a swap verb in the `presentation/cli/commands/`
  listing; while it has none, this stands.
- **`infrastructure/llm/json_parse.py::try_groq_json_validate_repair` banks a MISSING count as an
  empty account** — it rebuilds `LLMResponse` without `usage` after a `json_validate_failed` 400 that
  was already billed, so the model's own default is what every reader downstream sees. The 400 body
  carries no `usage`, so the count is unrecoverable, and `unpriced_tokens` is the wrong home: it
  means price unknown, not count unknown. Never estimate from content length. Dormant — Groq-only,
  every configured provider is `openrouter`. Blocker: `TokenUsageRecord` has no unknown-count
  dimension, and the account gate leans on a count always being knowable. **Re-test:** a
  `provider: groq` in any `datasets/*/pipeline.yaml`; while none pins one the path cannot fire.

**Needs a live run, not a decision:**
- **`_rebank_on_branch`'s re-bank has never been observed** — fixed to take each corrected round
  through the whole ingress, but the cycle it was measured on went with a store wipe, so the fix is
  reasoned, not seen. **Re-test:** repair a fork, then confirm each corrected round carries its own
  `round:complete` on the branch; nothing under `tests/` asserts it.
- **The `seed` provenance layer has never been stamped by real data.** `pipeline_resolve.py`
  reads the CYCLE SEED's `pipeline_overlay` for it — one field name, three carriers, distinguished
  by the `source` each layer stamps (`campaign` / `seed` / `evolved`). Candidates move node
  params, so the `evolved` layer has live rows; no cycle seed on this workspace carries an overlay,
  so the seed feed is still dead here and only the merge primitive beneath it is covered
  (`tests/test_integrity.py`). A fork steered with `resume --steer NODE.PARAM=VALUE` exercises it.
  **Re-test**, from the checkout root where `.promptpotter/` lives and never from a worktree:
  `grep -rh cycle_seed .promptpotter/projects/*/campaigns/*/cycles/*/.runtime/ledger.jsonl | grep -c '"pipeline_overlay": *{'`
  — `0` means no live seed has reached the layer.

Closed items are not tracked here — `git log` is the history layer.
