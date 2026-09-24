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

- **NEXT — raising the in-flight depth takes effect only when a call LANDS.**
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

- **Holistic reframes — larger chunks, noted so they aren't mistaken for done; don't slip one into a
  release.** **Whether L4 should reach the escalation machinery.**
  Not "each is built from scratch" — L2 and L3 already share `dispatch/`, `escalation/`, `cycle.py`
  and `OPTIMIZER_RESPONSE_MODELS`, and `application/optimization/CLAUDE.md` already splits the
  conceptual family from the structural one, which leaves only L4 outside, at the connector seam.
  So the question is not whether three strangers should converge; it is whether the recursion
  belongs inside the ladder it recurses on. **Asked and DEFERRED by the operator** — held by
  decision, not by nobody having looked. **The ground it was deferred on is gone**: it was
  "structural while what the milestone needs is empirical", and the new M13 is itself structural.
  What replaces it is stronger — [`roadmap.md`](roadmap.md) § The optimizer plug point answers the
  same question in the same direction, since a plugged-in proposer takes L1's *seat* and nesting
  stays re-entrancy at a seam rather than a new rung. **Rides with nothing, deliberately.**
  **Re-test:** read that section; if it settles the question for the operator too, this item is
  deletable rather than deferred, and deleting it is how it ships.

- **Optimizer model repair-rate on heavy L2/L3 structured output — unmeasured.** What is owed is the
  measurement: a live cycle reaching L3, read under the model
  `promptpotter/assets/optimizer/pipeline.yaml` currently pins — read it off that file, never off
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

- **`noise-floor` and `seed-screen` spend with no record.** Both admit every call against a book
  (`initialization/loop_start.py::arm_diagnostic_scoring`) but bind no ledger, so what they pay is
  on no ledger any account sum reads; on a `promptpotter-self` cycle the inner spend is banked only
  when the sandbox is reaped. Action: file both on a ledger the account walk reaches — `noise-floor`
  has its cycle, as `verify` does (`diagnostics/verify.py::_diagnostic_trace`); `seed-screen` has
  none and needs one named. **Rides with:** the next change to either verb. **Re-test:** run
  `noise-floor -k 1` on a cycle and grep its `.runtime/ledger.jsonl` for a `token_usage` line
  stamped `diagnostic`; none means this is open.

- **No gate stops a router importing `promptpotter.infrastructure.store`**, so a route that picks WHICH rows or in WHAT ORDER stays unreachable from every other entry point (`presentation/CLAUDE.md` § Out-of-bounds). Action: move each router's composition into `application/` (template: `routers/datasets/leaderboard.py` → `application/scoring/cells.py::measurement_log`), then add the import ban to the gate. **Rides with:** any change to one of those routers — each takes its own module off the list. **Re-test:** `grep -rl --include=*.py promptpotter.infrastructure.store promptpotter/presentation/api/routers` — a non-empty list means the gate cannot land yet.

- **`webapp/lib/derivations/round-samples.ts` re-walks the sample mark with three arms** (ERR / HIT / MISS) over raw round-file rows, where `domain/dashboard_rows.py::sample_status` has four — so a historical UNSC row reads as a wrong answer. Action: serve the mark on the row the client reads (the round file's `all_candidate_results`, or route the reader through `/cells`, which already serves `CellRow.status`), then delete the client ladder. **Rides with:** any change to `round-samples.ts` or the round-file result row. **Re-test:** `grep -n '"UNSC"' webapp/lib/derivations/round-samples.ts` — empty while the client ladder still has three arms.

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
  at $0 (`StepTokenUsage` drops TermNorm's `attempts`), and `infrastructure/backend.py::run_query`
  re-sends the 503 TermNorm uses for "my provider timed out" — a possibly-billed send the ceiling
  never sees, up to five times. Silent. `grep -n '"attempts"' promptpotter/domain/spend.py` (empty).
- `infrastructure/llm/pricing.py::_fetch_route_ceiling` returns `None` for a FAILED catalogue fetch
  as for an unpriceable route, so an OpenRouter outage stops a capped run as `SPEND_BUDGET` with
  advice that cannot help, and re-fetches on every send. Loud, wrong reason. `grep -n -A3 "except
  (urllib.error.URLError, OSError, TimeoutError, json.JSONDecodeError)" promptpotter/infrastructure/llm/pricing.py`.
- `sample_measurement.py::_classify_http_error` files a 4xx by bare status, skipping
  `shared/errors.py::is_provider_credit_refusal`, so an empty account relayed by TermNorm aborts
  every walk as a CLIENT config error instead of halting as PROVIDER_CREDIT. `grep -n
  is_provider_credit_refusal promptpotter/application/scoring/sample_measurement.py` (empty).
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
  promptpotter/application/optimization/resume_and_fork/ab_replay.py promptpotter/application/diagnostics/noise_floor.py`.
- `infrastructure/store/measurement_archive.py::load_reusable_results` tests `predicted == "ERROR"`
  (a display token) instead of `is_error_result`, and `domain/sample.py::Measurement` has no
  `error_category`, so a formula-failure row replays as a live answer and verify reads archived
  holes as measured. `grep -n 'predicted") == "ERROR"' promptpotter/infrastructure/store/measurement_archive.py`.
- `application/runner/inner/spawn.py::_open_inner_campaign` continues the ROOT inner cycle and never
  follows `superseded_by`, so deepening a rebased inner cell reopens the retired root and measures
  a trajectory other than the one banked. `grep -c superseded_by promptpotter/application/runner/inner/spawn.py` (0).
- `presentation/terminal/completion.py::render_completion` takes the max raw subset accuracy as
  "Best", a third derivation beside `best_round_on_shared_cells` and the composite high-water that
  `CycleResult.best_round` names under `index.json`'s field name. `grep -n "r.accuracy), default=None"
  promptpotter/presentation/terminal/completion.py`.
- `connectors/promptpotter.py` reads `inner_tasks.yaml` raw (no `resolve_experiment`) beside the
  typed `runner/inner/tasks.py::InnerTasks`, so an `axes:` panel reaches the run as zero tasks and
  every axes grid hashes as one instrument; `connectors/CLAUDE.md` and `promptpotter/CLAUDE.md`
  both describe it wrongly. `grep -c resolve_experiment promptpotter/connectors/promptpotter.py` (0).
- The inner benchmark's directory is resolved three ways — `connectors/promptpotter.py::_identity_config`
  by sibling path, the spawn through a sandbox store with an empty tenant tier, the ruler through
  `readable_dataset_dir` — so a tenant-tier copy splits identity, run and ruler. `grep -n
  "dataset_dir.parent / str(benchmark)" promptpotter/connectors/promptpotter.py`.

**Shape 3 — an act or a reading lives in ONE adapter, so the entry points disagree.** The
canonical mechanisms are ones an adapter may call, not the only path an act can take. Remedy:
every operator act runs one application pipeline (validate → admit → apply → record) whose
exemptions are parameters rather than skipped calls; every reading an operator acts on is one
served field behind one read facade; `presentation/` imports facades only, and no route returns
an untyped dict.
- The config-drift gate on resume (`presentation/cli/commands/resume_command.py`, `root_content_hash`
  against the recomputed cycle id) is CLI-only: web Resume, `step-cycle` and a fork's launch
  continue a cycle under an edited `pipeline.yaml`, starting prompt or framing. Silent. `grep -rn
  root_content_hash promptpotter/application/jobs promptpotter/application/runner promptpotter/application/optimization/resume_and_fork` (empty).
- `presentation/terminal/live/phase.py::render_progress_table` differences θ across rounds and
  advises a "Plateau" stop without `AbilityReading.comparable_to` or the served caveat, where the
  browser filters by ruler. `grep -n "theta - prev\|comparable_to" promptpotter/presentation/terminal/live/phase.py`.
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
- `presentation/cli/commands/new.py::_commit_task_framing` mints a check-in skeleton outside
  `launcher/checkin.py::create_checkin_campaign` and writes dataset framing from `presentation/`,
  leaving an unstartable campaign per `--task-file`. `grep -n mint_checkin_skeleton promptpotter/presentation/cli/commands/new.py`.
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
- **The `evolved` and `seed` provenance layers have never been stamped by real data.**
  `pipeline_resolve.py::_evolved_overlay` reads the CANDIDATE's `pipeline_overlay` and the seed
  layer the CYCLE SEED's — one field name, three carriers, distinguished by the `source` each
  layer stamps (`campaign` / `seed` / `evolved`); every candidate on this workspace
  is prompt-only, so both feeds are dead here and only the merge primitive beneath them is
  covered (`tests/test_integrity.py`). A campaign that actually MOVES a node param
  exercises both, and the trap they guard is documented at `_evolved_overlay`: reading
  `resolved_pipeline_params` instead would stamp every param `evolved` at once.
  **Re-test**, from the checkout root where `.promptpotter/` lives and never from a worktree:
  `grep -rho '"pipeline_overlay": [^,}]*'
  .promptpotter/projects/*/campaigns/*/cycles/*/rounds/*.json | sort -u` — while the only
  distinct value is `null`, no live row has reached either layer.

Closed items are not tracked here — `git log` is the history layer.
