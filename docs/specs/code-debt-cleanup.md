# Code-Debt Cleanup — Backlog

**Only what cannot be picked up now, and only what ASKS FOR WORK.** An item earns a line by being
**blocked** or **multi-arc**. Everything else — anything adjacent to work already in hand, anything
one edit closes — is **fixed in the pass that found it**, never filed. Enough to pick up cold:
`file::symbol — why — action — blocker`. An item ships by being DELETED from this file; `git log`
is the history layer. **Never the root of a patch just shipped** — that root ships instead
(root `CLAUDE.md` `<root-fix>`); a bridge left standing with its cure filed here is refused.

> **Every entry names its RE-TEST: the command, the landing, or the person and the question they
> must answer.** A blocker without one is a claim about the world on the day it was written, and
> nothing ever forces a re-read. **An entry with no re-test is not blocked, it is unverified.**
> Write the re-test or do not file the entry; where an entry carries one, RUN IT before acting,
> and fix or drop a wrong entry as part of the work.

> **And every entry names the work that will REACH it: `Rides with:`.** The file, surface or kind
> of pass that lands on this anyway — written for a reader holding no context, so that a session
> already in there does the item ON THE WAY, inside the commit it came to make. Nothing here earns
> a session of its own, so an entry naming no carrier waits for a pass that will never be
> scheduled. Name the carrier even when it is a whole surface ("any stylesheet change") — a vague
> one still fires. Under § Blocked the blocker already IS the carrier.

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
  makes a route stay.** `infrastructure/llm/openai_compat.py`, `connectors/harbor.py` and
  `connectors/dbllmbench.py` pin hosts only where a node config names `route_order` by hand. An
  OpenRouter `session_id` was tried and dropped: measured 2026-10-08 on `xiaomi/mimo-v2.6-flash`
  over a ~15k-token head, it hopped hosts within three calls and billed $0.055–0.077 per M input
  against $0.026 pinned to `xiaomi`. So any design that counts on the cache — a cost term in
  fitness, a spend estimate, a search front on cached price — holds only under a pin. Action: a
  default host pin per model that no node has to author, or a refusal to price on the cache
  without one. **Rides with:** any change to the request builder in `openai_compat.py`, a
  connector's route handling, or the caching arc (`.scratch/caching-arc-state.md`). **Re-test:**
  the per-node table in an unpinned campaign's `review.md` (`domain/spend.py::SpendRollup.by_node`)
  beside a pinned one on the same model — a prefix-cache share well below the pinned one means the
  default route still scatters.

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
  `webapp/e2e/serve.mjs`, `scripts/build_release.py::_WEBAPP_SRC`, the build step in `publish.yml`,
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

**Spend**

- **A reserved cell's bill is on no ledger, so a re-armed book holds its first such cell whole.**
  A cell that reserves (`Connector.holds_own_sends`) writes no hold of its own, so
  `spend_book.py::unreported_on` can re-teach a book its sends and never the cell they made up.
  **Blocker:** a per-cell record is stored shape — it rides the stored-shape batch below.
  **Re-test:** `grep -n "emit_spend_hold" promptpotter/infrastructure/llm/spend_book.py` — none
  inside `reserved` means a resumed harbor run still opens one cell deep under a tight ceiling.
- **The bench pass runs at look-ahead depth 1 because the depth is a press a ROUND spends.** Both
  passes go through `run_walks` and read `_armed_cells` like a round, but `runner/bench.py` runs
  them outside any round — before the loop and after it — where no press stands unless the
  operator makes one during the pass, or `campaign.lookahead` is `auto`. **Blocker:** the
  operator's call on whether a pass takes a depth of its own; look-ahead is browser-only, so no
  config key may carry it. **Re-test:** time one bench pass against its cell count.

**Run state and the tree**

- **A resume from a pause at the origin's boundary runs round 0 again.** The cells replay, so the
  round file comes back with every cell `cached` and no token counts, the ledger holds a
  second round-0 election, and potter sends `l1_critique` a second time. A pause one round later
  resumes with every decision equal. Unknown whether the second round 0 is the intended warm
  restamp or a boundary the resume fold misses. **Rides with:** any change to
  `projection.py::resolve_resume_state` or the origin round in `runner/entry.py`. **Re-test:**
  `scripts/offline_run.py --rounds 1 --rows 60` — `decisions MOVED` on the resume line means it
  still does.
- **INVESTIGATE — two items filed with their re-tests in `.scratch/debug-arc-seed.md`**: errored
  cells (`finish_reason=error` then a schema repair) and whether their rate differs by arm; and
  potter's round-5 L2 layout refusal.

## Bypasses — one defect class, held for ONE holistic pass

**A path that goes around the mechanism the rest of the code rides, and re-derives the answer
itself.** The entries below are filed
TOGETHER rather than patched one by one on purpose: read side by side they sort into two shapes,
and each shape names an upstream redesign that makes the class hard to write at all. Patched
singly, each fix is one more local copy of the rule it restores. **Rides with:** that redesign.
A pass already rewriting one of these symbols may take its entry, but takes the shape's remedy,
never a local patch. **Re-test:** each entry's command; a hit means it still stands.

**Shape 1 — a send made ON OUR BEHALF re-derives what `_admitted_send` decides for our own.**
The classifier and the settle rule are one (`infrastructure/llm/spend_book.py::SendOutcome`,
`Admission.close`); what stands is the half no edit on this side reaches: a relay's own retry loop
runs on its own budget and reports its attempts as a count.
- TermNorm reports `attempts` per node and ONE usage sum, so an answered repair turn and a 5xx it
  retried read the same, and `scoring/sample_measurement.py::_uncounted_attempts` holds both as
  unreported. Owed by `backend-api/core/llm_providers.py::llm_call`: per node, how many attempts
  left and reported no usage (today only a timeout says so, by voiding the whole cell). **Re-test:**
  `grep -n "unreported" backend-api/core/pipeline_context.py` in the TermNorm checkout — a per-node
  count beside `attempts` closes it, and `_uncounted_attempts` then reads that count instead.
- A unit of work's one budget (`send_pacing.py::SendBudget`) stops at the relay's door: TermNorm's
  `_MAX_ATTEMPTS` and terminus-2 × litellm each still count under one of our attempts, and a
  remote cell waits out `Connector.cell_wait_s` rather than the budget's clock. **Blocker:** each
  loop is the relay's own code, and handing a remote cell the budget's clock turns a timed-out
  cell from an unreported send into a `HALTED` cut — a decision change that wants the operator.
  **Re-test:** `grep -n "_MAX_ATTEMPTS = " <termnorm>/backend-api/core/llm_providers.py` — a
  literal means that budget is still its own; and ask the operator whether a remote cell ends on
  the budget's clock.

**Shape 2 — an act or a reading lives in ONE adapter, so the entry points disagree.** The
canonical mechanisms are ones an adapter may call, not the only path an act can take. Remedy:
every operator act runs one application pipeline (validate → admit → apply → record) whose
exemptions are parameters rather than skipped calls; every reading an operator acts on is one
served field behind one read facade; `presentation/` imports facades only, and no route returns
an untyped dict.
- The CLI's own compositions still import `promptpotter.infrastructure` past its composition root
  (`build_stores`): `cli/campaign_runner.py` (the first-run check), `cli/commands/resume_command.py`
  (`is_checkin`), `reset.py` and `cli/parsers.py` (`SHARED_CACHE_DIRS`),
  `new.py` (the dataset gateway), `launch.py` (the latest-readout pointer), `verify.py` (its own
  `descend_store`, where `pipeline_resolve.py::resolve_pipeline_at` descends for its caller), and
  beside the CLI `teleprompter.py`, `terminal/completion.py` and `admin_bot.py`. Each moves into
  the application function its verb already calls, as the routers did; the gate then takes
  `presentation/cli` under the rule `gate.py::_layering` holds the routers to.
  `grep -rnE "^(from|import) promptpotter\.infrastructure" promptpotter/presentation --include=*.py | grep -vE "api/|store\.stores import (Stores|build_stores|Stores, build_stores)$"`.

## Blocked — named blocker

- **The stored-shape batch — every item below changes what a round file, ledger record or cache
  holds, and lands as ONE pass with the workspace wipe.** **Blocker:** the workspace must stay
  loadable for the demo. **Re-test:** `ls .promptpotter/projects/*/campaigns` is empty.
  - *Authorship.* The author's display name is stored beside `issued_by`, so "Steered by" stops
    serving the issuer id; the authoring act and issuer ride the candidate's ledger record
    (`authorship_of` reads every C0 as `origin`).
  - *Round file.* `RoundResult.health` is required (`generation_only.py` writes `None`, and
    `dispatch/facade.py` tolerates it); `RoundResult.subset_mode`; `RuntimeFailure.node`;
    `ability.unlinked` / `pinned_share`; a ledger discriminator for restamped rounds.
  - *Served rows that re-derive.* The crown on `DashboardCandidate` / `ScoreboardRow`;
    the headline pick on the
    stored `BenchScore`; `electable_count` + `overlap` on
    `ElectionRecord`; `verdict` on `PanelPrecision`.
  - *Race lineage.* The race parent id `R{n}_winner` becomes the lineage id (the ELIMINATION_CUT /
    LEADER_LOCK_IN records, `race_catch_up`, `elimination_context`).
  - *Cell identity.* Harbor's `_REASONING_CHANNEL` still spells `openrouter:` and is hashed into
    every harbor cell; `.cache/model_capabilities.json` stores a catalogue `pricing` nothing reads.
  - *Seams.* `spawn.py`'s sixth site + `prepare_fresh_cycle(arm=)`; the identity follow-up seam;
    the webapp's poll, fork-dialog and compare seams.
  The per-item write-ups are `.scratch/root-hunt/*.md`.

- **`infrastructure/llm/anthropic.py::AnthropicClient` cannot run an optimizer node on a current
  Claude model.** It refuses a set `reasoning_effort` (no mapping onto `output_config.effort`),
  sends `temperature` on every call (current Claude models reject sampling parameters), never
  sends the response schema (`output_config.format`), and sizes `max_tokens` from a stale 8192.
  The manifests offer `provider: anthropic` all the same. **Blocker:** no Anthropic credential on
  the dev machine, so none of the four can be measured, and which models reject sampling is a
  per-model fact `registry.py::_MODEL_PROFILES` admits only as a measurement.
  **Re-test:** `settings.ANTHROPIC_API_KEY` is empty; once it holds a key, `probe-reasoning` a
  Claude model and this is one pass.

- **Concurrent sibling cycles of one campaign each spend up to the whole ceiling.**
  `optimization.ceiling` binds a CYCLE (`runner/entry.py::_arm_spend_book` seeds its book off that
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
- **Re-test: `compact-archive inventory`**, which is what sizes the three below: configurations,
  answers and bytes by dataset / role family / age, plus the index rows no answer stands behind,
  which is what makes every other count an upper bound. Run it before concluding a piece has
  nothing to be built against. **Build them BEFORE the next bulk delete, never after:** that is the one moment both
  halves exist at once, something to measure and a delete about to strand it.
- **Reclaim** — the destructive counterpart of `delete`, dataset-scoped, dry-run by default,
  refusing while a producer can append, and NAMING what it would strand for a dataset whose rows
  another dataset's inner runs may share. Nothing does this today: `delete` leaves the shared
  content-addressed rows standing (correctly — a sibling may replay them), `compact-archive` reaches
  only the fields a row does not read, and `reindex`'s GC is positive-identification-only, so every
  orphan is kept.
- **Attribution** — `ArmNode.reading.sp_hash` is stamped forward-only, which is right and is enough for
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

**Cross-repo (the TermNorm sibling's `backend-api`):**
- **The TermNorm `/version` endpoint** is what remains owed on that side; this repo then
  bumps `termnorm.py::_EXPECTED_REVISION`. **Re-test:** `termnorm.py::_EXPECTED_REVISION`
  is `None`; the moment it holds a string the endpoint landed and this entry goes.
- **A backend fix isn't observable without clearing a cache** — PP's measurement cache and
  TermNorm's `match_database` both key on query/searchpoint, never on backend code/revision, so a
  co-owned backend fix replays stale results. Fold the connector revision-pin into the
  measurement-cache key (or add a `--fresh` flag); confirm the TermNorm `/matches` short-circuit
  fires only on `verified` aliases. Workaround: clear `measurements/`. **Re-test:** grep the package
  for `--fresh` and for any revision term on the measurement-cache key; while both miss, this stands.

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
  covers), and the per-run **reservation** (`Job.reserve`) — without it two concurrent
  launches are each admitted against the same remainder and the pair spends ~2× the ceiling.
- **`domain/run_records.py::TokenUsageRecord` lacks `key_source`** → `/auth/activity`
  `group_by=api_key` (`application/jobs/account_activity.py`) groups by the recorded `provider` —
  who billed, not whose key. Once real `key_source: host|user` lands (declared on
  `TokenUsagePayload` in the asyncapi), the axis gains that dimension. Blocker: the coupon build
  adds the field.

**Needs a capability neither the bench nor the preprint opens** — the bar is "does M13 or M14 need
it", and these do not. The bench does not rescue the first one in particular: a third party ships an optimizer through
an entry point, in-process, so it never touches the inbound credential.
- **The REST API has no inbound credential** — owned by
  [`../developer/stable-api.md`](../developer/stable-api.md) § 8. What is NOT stable. Owed HERE: the
  credential itself, plus the worked `submit → poll → fetch` examples and per-endpoint guarantees
  that wait on it. Blocker: that capability. **Re-test:** grep `promptpotter/presentation/api/` for
  a bearer or API-key reader on the inbound path; while the session cookie is the only one, this
  stands.
- **Swapping a model means hand-editing two `pipeline.yaml` lines and remembering to revert both**,
  and a leaked pin mislabels the next run. Blocker: the
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
  `round_closed` record on the branch; nothing under `tests/` asserts it.
- **The `seed` provenance layer has never been stamped by real data.** `pipeline_resolve.py`
  reads the CYCLE SEED's `pipeline_overlay` for it — one field name, three carriers, distinguished
  by the `source` each layer stamps (`campaign` / `seed` / `evolved`). Candidates move node
  params, so the `evolved` layer has live rows; no cycle seed on this workspace carries an overlay,
  so the seed feed is still dead here. A fork steered with `resume --steer NODE.PARAM=VALUE` exercises it.
  **Re-test**, from the checkout root where `.promptpotter/` lives and never from a worktree:
  `grep -rh cycle_seed .promptpotter/projects/*/campaigns/*/cycles/*/.runtime/ledger.jsonl | grep -c '"pipeline_overlay": *{'`
  — `0` means no live seed has reached the layer.

Closed items are not tracked here — `git log` is the history layer.
