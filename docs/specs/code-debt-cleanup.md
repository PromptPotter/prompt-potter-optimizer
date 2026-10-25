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

## URGENT — the fog that hid the backbone from the core-reshape arc

Filed at the operator's ask, ahead of everything below: each entry is a shape that kept a refactor
from being SEEN, so leaving it blinds the next plan the same way (`<next-change>`). Solve as soon
as possible. **Rides with:** the shrink arc's stage named in each entry, or any pass in that file.

- **Most doc invariants are still prose no gate checks.** `gate.py --only doc-claims` resolves every
  cited `file::symbol` and runs every probe, and `scripts/gate.py::_KNOWN_FALSE` is empty. Action: every
  other one-X / no-second-Y / nothing-reads-Z claim gains a probe or is
  deleted. **Re-test:** `gate.py --only doc-claims`.
- **Three "the one reader" mechanisms, each owned by a different layer file** —
  `infrastructure/projections/base.py::Projection`, `infrastructure/store/read_model.py::LedgerFold` + `infrastructure/store/campaign_store/ledger_scan.py::scan_*`,
  `domain/activity.py::ActivityFeed` — with no roster across layers, and `adding-a-surface.md` §1
  writing the per-record cost as a recipe. Action: one concept → mechanism → owner table in
  `promptpotter/CLAUDE.md`; every reader a `LedgerFold`, projections checkouts of folds;
  `CampaignStore.ledger(hop)` the one ledger-path derivation. **Re-test:** `rg -c
  "CycleLayout\(stores.campaigns.cycle_dir" promptpotter` → 0; computed `p_record` ≤ 4.
- **`application/bench/node_context.py::NodeContext` grows a method per member need** (52 → 88
  public in one arc). Action: declared typed reads refused at load, ~11 methods left, a member base
  in `optimizers/nodes.py`. **Re-test:** `p_member` ≤ 12.
- **Run state mirrored by test namespaces**, so a reshape looks costly and stays off the brief.
  Action: a `seat` in `tests/factories.py` the one seat. **Re-test:** renaming a `Cycle` attribute
  changes one file under `tests/`.

## NEXT ARC — seeded by the shrink arc

Closed by the operator after its review-repair wave; what it could not reach starts the next one.
The arc's method is the session protocol in memory (`feedback_arc_of_arcs_loop`), not this file.
**Rides with:** the next arc's first builder wave, after its cold recon and premise hunt.

- **The cladogram re-derives fork lineage the server already resolves.**
  `webapp/components/candidates/forest-layout.ts::extentKeys` walks spawn cuts and from-candidates
  on the client (a second `resolve_line`), while each `HierarchyCandidate` serves its `main_line`.
  The cut is also by PROPOSAL round, not by line position: Compare on a pick that later rounds held
  hides those rounds whole. Root: an extent is the served line (`main_line`, through the round the
  line last stood on the anchor), the client walk dies, and every channel and `clip` reads it.
- **The dev loop serializes where it need not.** Sibling gates queue whole runs (a `gate --py` waited
  9 min behind two others before its own 5); `next-build` peaks near 4 GB; a builder spends ~100k
  tokens of tool output, most of it whole-file reads and whole-tree content greps; Turbopack
  refuses a junctioned `webapp/node_modules`, so web checks run only in the integration tree; at
  five builders plus a full gate the host reaped the gate's shell. Action: attribute each (scout
  first, read-only), then cut what OUR code wastes — gate lanes that overlap, a lighter build
  check, briefs that read by section. Vitest's fixed worker START_TIMEOUT counts jsdom setup, so a
  loaded host fails a green suite (happy-dom is the candidate; a new dependency). **Re-test:** five
  builders and one full gate run with no reap and no gate waiting longer than its own run.
- **`application/campaign_config.py` belongs in `domain/`** — the payload table completes only once
  it is imported, and 71 modules import it. Action: move it, re-point the importers. **Re-test:** the
  payload table is whole at `import promptpotter.domain`.
- **No offline leg rebases mid-run.** `scripts/offline_run.py` strips every L2/L3 `fork_proposal`,
  so `runner/entry.py::_run_line`'s later passes are reached only by a test patching privates
  (`test_private_reach` 9). Action: a rebasing leg; the test drops its patches. **Re-test:**
  `test_private_reach` back to 6.
- **Ledger and readout shapes the review left standing:** the operator's seat is not a `Member`; the rebase clock has no test; `LiveCandidate.samples`
  has no reader; `round_restated` carries the whole round body; `RunStanding.after` recomputes what
  the fold holds; the readout filters on `resumed_from_round`; `InnerCellFacts` keeps no unread
  rounds, so the sample readout cannot name them. Action: each to its root, one builder per seam.
  **Re-test:** each symbol named here is deleted, read, or tested.

## Open — multi-arc, no blocker

A leading `NEXT` marks the one to take up cold when nothing else is in hand.

- **NEXT — the default host pin is a trial with no verdict.** A model's default host
  (`infrastructure/llm/registry.py::_MODEL_PROFILES`, `default_route`) is written into every node config before it is
  keyed (`domain/pipeline_overlay.py::pin_default_routes`, optimizer calls through
  `bench/llm_call.py`); a node naming `route_order`, `[]` included, opts out. Unpinned, the cache
  is no lever: an OpenRouter `session_id` hopped hosts within three calls and billed $0.055–0.077
  per M input against $0.026 pinned (2026-10-08, `xiaomi/mimo-v2.6-flash`, ~15k-token head).
  **Decided:** a model keeps its pin only where its realized cache share and effective $/M beat
  running uncached; otherwise it drops the pin and the caching. Action: read both per model and
  host off a pinned campaign and its twin at `route_order: []`. **Rides with:** the next supervised
  campaign on a pinned model. **Re-test:**
  the per-node table in each twin's `review.md` (`domain/spend.py::SpendRollup.by_node`); no pair
  read per pinned model means it stands.

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

- **The compare view's candidate cards are laid out for a few wide columns, not many narrow ones.**
  Each `components/compare/ChannelCard.tsx` card stretches across the page. Its config rows put the
  label and the value side by side, and its prompt rows do the same for field and text. The layout
  should suit thin vertical stripes instead: five cards side by side on a 16:9 display, each one
  readable at that width. Operator-raised 2026-10-20, for later. **Rides with:** the next pass on
  the compare pane, through `/design`. **Re-test:** open compare with five candidates at 1920×1080
  and see whether all five fit without horizontal scroll.

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
  everything keyed to Next's commands and output: the `next-build` check and `playwright`'s
  ordering after it in `scripts/gate.py`, the `next typegen` in `npm run typecheck`, the `.next`
  includes and the `.next/dev` exclude in `tsconfig.json`, `webapp/e2e/serve.mjs`,
  `scripts/build_release.py::_WEBAPP_SRC`, the build step in `publish.yml`, the eslint cache step
  in `ci.yml`, the `/serve` skill and `webapp/CLAUDE.md`. Keep the output
  directory named `out` and most of that list falls away. Unverified: whether the rebuilt ESLint
  set brings `braces` back through its own `micromatch` — settle that FIRST, since it decides
  whether the migration closes the audit or only shrinks the lock. **Rides with:** the next
  webapp dependency pass — a `next` advisory, a Dependabot bump of `webapp/package-lock.json`, or
  the release audit going red on `braces`. **Re-test:** `git grep -n '"next"' webapp/package.json`
  — a hit means Next is still the build; `npm ls braces` in `webapp/` names what still pulls it;
  and `git grep -hoE 'from "next(/[a-z/-]+)?"' -- webapp | sort | uniq -c` recounts the imports,
  which is the size of the job.

- **`tests/test_scoring.py::_peer_cycle` stands a `Cycle` up by hand, origin included.** It banks
  a C0 report the origin path (`application/origin.py::establish_campaign_origin`) cannot produce:
  the bench journey needs accuracy 0 beside a composite of 0.9, which the formula it declares cannot
  reach, and the lives journey's report reads accuracy 0 over rows that all hit. Action: rebuild
  those journeys on a backend whose answers produce the origin they assert, then start the cycle
  through run init. **Rides with:** any change to how run init scores the origin.
  **Re-test:** `grep -n "_peer_cycle" tests/test_scoring.py` — a hit is a journey still standing
  the cycle itself.

## Open — surfaced by the head-to-head arc

Filed at the operator's ask rather than under the multi-arc bar above. A leading **INVESTIGATE**
means nobody has established that the item is real, solvable or worth its cost: answer that first,
and delete the entry if the answer is no.

**Spend**

- **A reserved cell's bill is on no ledger, so a re-armed book holds its first such cell whole.**
  A cell that reserves (`Connector.holds_own_sends`) writes no hold of its own, so
  `infrastructure/llm/spend_book.py::unreported_on` can re-teach a book its sends and never the cell they made up.
  Action: a hold record per reserved cell. **Rides with:** any change to `infrastructure/llm/spend_book.py::reserved`
  or to how a harbor cell is admitted.
  **Re-test:** `grep -n "emit_spend_hold" promptpotter/infrastructure/llm/spend_book.py` — none
  inside `reserved` means a resumed harbor run still opens one cell deep under a tight ceiling.
- **The bench pass runs at look-ahead depth 1 because the depth is a press a ROUND spends.** Both
  passes go through `run_walks` and read `_armed_cells` like a round, but `runner/bench.py` runs
  them outside any round — before the loop and after it — where no press stands unless the
  operator makes one during the pass, or `campaign.lookahead` is `auto`. **Decided: the pass
  takes a depth of its own** — look-ahead is browser-only, so no config key
  carries it; the pass's cell set is fixed and known before it starts. **Rides with:** any change to
  `runner/bench.py`'s walk. **Re-test:** time one bench pass against its cell count.

**Run state and the tree**

- **A resume from a pause at the origin's boundary runs round 0 again.** The cells replay, so the
  round file comes back with every cell `cached` and no token counts, the ledger holds a
  second round-0 election, and potter sends `l1_critique` a second time. A pause one round later
  resumes with every decision equal. The re-close itself is declared (`application/runner/loop.py::run_round_loop`:
  until an L1 round closes, every launch re-closes round 0, since a stopped run's file passed no
  gate); open is whether the critique re-send should read the banked `optimizer_state` instead. **Rides with:** any change to
  `infrastructure/projections/live_dashboard/projection.py::DashboardCheckout.over` or the origin round in `runner/run_prep.py`. **Re-test:**
  `scripts/offline_run.py --rounds 1 --rows 60` — `decisions MOVED` on the resume line means it
  still does.
- **INVESTIGATE — does an errored cell bias a head-to-head?** On `gpt-oss-20b:nitro` a target call
  can end `finish_reason=error` and then pass one schema repair; it was seen in every arm of a
  four-arm head-to-head, most on long-reasoning prompts. Unknown: whether the row banks the repaired
  answer or a miss, so whether an arm whose prompts reason longer is graded by the provider's error
  rate. **Rides with:** the next head-to-head read. **Re-test:** count the rows carrying the error
  per arm off the archive and compare their hit rate with the arm's other rows — an arm-dependent
  rate is a bias, an arm-independent one is noise on every arm alike.

## Open — left by the structure pass

Filed at the operator's ask as the hand-off of that pass, not under the multi-arc bar. Each was
found, sized and deliberately not taken; none is the root of a patch that shipped.

**The browser still derives**

Each needs a served field declared on its response model first, then the browser code deleted.
**Rides with:** any change to the served model named, or to the component. **Re-test:** the
`file::symbol` still holding the arithmetic.

- **The "waiting" line has two predicates** — `components/shell/remote/useRemoteModel.ts` and
  `lib/derivations/time-ray.ts`. Serve `dash.wait_line` once. **Decided: a call is open
  AND its wait target is known** (`webapp/components/shell/remote/useRemoteModel.ts::waitNote`'s rule); the
  recency rule goes. **Re-test:** both predicates still present means it stands.

**Unbound context that still does not raise**

`judges/call.py::ask` returns the unbound-ledger raise as an absent grade, by its never-raises
contract. **Rides with:** any change to the judge call's failure contract. **Re-test:**
`grep -n "except" promptpotter/judges/call.py` — a catch wide enough to take a `RuntimeError`
means it stands.

**Structure**

- `judges/` and `application/` import each other, and the connector, judge and optimizer
  registries are three hand-written loaders over `shared/plugin_registry.py`. **Rides with:** any
  change to plugin loading. **Re-test:** `grep -rn "from promptpotter.application"
  promptpotter/judges` — a hit means the pair stands; `gate.py --only layering` holds no rule for
  it, so it passes either way.
- `domain/results.py::RoundOutcome.health` is still optional and `potter/dispatch/facade.py`
  tolerates `None`. **Rides with:** any change to `RoundOutcome`. **Re-test:**
  `grep -n "health: DegradationHealth | None" promptpotter/domain/results.py` — a hit means it
  stands.
- Four seams a pre-reshape hunt named are owed a re-read against the code: the poll store carries
  fields nothing reads (`webapp/lib/poll.tsx::CycleStreamState`); the browser picks the best pointer
  (`webapp/lib/hooks/useCompareWithOrigin.ts`);
  spend is summed and zero-filled in the browser (`webapp/lib/derivations/spend.ts::readSpend`); a
  terminal fork builds its own overlay rather than dispatching `fork-cycle`
  (`presentation/cli/commands/resume_command.py::_steer_overlay`). Action: re-read each, then take
  it or delete it. **Rides with:** any change to a file named. **Re-test:** the `file::symbol`
  still holding the logic.
- `domain/phases.py` holds the stop-reason table and the producer reading for about two fifths of
  its length; `phases` no longer names them. A split adds modules and removes no cycle — measured,
  declined; a rename re-points some forty-six files. **Rides with:** a pass that already moves the
  stop-reason table. **Re-test:** none needed; it is a name.
- Every CLI verb completes the registries at entry (`presentation/cli/campaign_runner.py::main`), about a second
  a verb, and that call is the package's one deferred import of `application`. **Rides with:** any
  change to `application/initialization/wiring.py::complete_registries`. **Re-test:** time `python -m promptpotter cycles`.

## Bypasses — one defect class, held for ONE holistic pass

**A path that goes around the mechanism the rest of the code rides, and re-derives the answer
itself.** The entries below are filed
TOGETHER rather than patched one by one on purpose: read side by side they share one shape,
and that shape names an upstream redesign that makes the class hard to write at all. Patched
singly, each fix is one more local copy of the rule it restores. **Rides with:** that redesign.
A pass already rewriting one of these symbols may take its entry, but takes the shape's remedy,
never a local patch. **Re-test:** each entry's command; a hit means it still stands.

**The shape — a send made ON OUR BEHALF re-derives what `_admitted_send` decides for our own.**
The classifier and the settle rule are one (`infrastructure/llm/spend_book.py::SendOutcome`,
`Admission.close`); what stands is the half no edit on this side reaches: a relay's own retry loop
runs on its own budget and reports its attempts as a count.
- TermNorm reports `attempts` per node and ONE usage sum, so an answered repair turn and a 5xx it
  retried read the same, and `application/scoring/sample_measurement.py::_uncounted_attempts` holds both as
  unreported. Owed by TermNorm's `llm_call` (`backend-api/core/llm_providers.py`): per node, how many attempts
  left and reported no usage (today only a timeout says so, by voiding the whole cell). **Re-test:**
  `grep -n "unreported" backend-api/core/pipeline_context.py` in the TermNorm checkout — a per-node
  count beside `attempts` closes it, and `_uncounted_attempts` then reads that count instead.
- A unit of work's one budget (`infrastructure/llm/send_pacing.py::SendBudget`) stops at the relay's door: TermNorm's
  `_MAX_ATTEMPTS` and terminus-2 × litellm each still count under one of our attempts — retry
  loops in the relay's own code. **Re-test:** `grep -n "_MAX_ATTEMPTS = " <termnorm>/backend-api/core/llm_providers.py`
  — a literal means that budget is still its own.

## Blocked — named blocker

- **TermNorm's live datasets (`llm_only`, `entity_profiling`, `llm_ranking`) are refused at parse:
  a prompt node now declares its own fields and TermNorm's side does not.** Owed there:
  TermNorm's `_enrich_with_registries` (its `backend-api/api/pipeline.py`) carries `meta["fields"]`, both
  `logs/prompts/*/1/metadata.json` declare `fields`, `config/pipeline.json` gives `llm_only` its
  fields, and `direct_prompt` gains a `prompt_info`. **Blocker:** the operator's go to edit the
  TermNorm checkout, whose tree holds uncommitted work; it also blocks landing the shrink arc on
  main. **Re-test:** `python -m promptpotter new llm_only` parses its pipeline.
- **Three readings wait on the operator.** (1) A one-text prompt mode needs a declared slot before
  `rewrites` + `field` fold into one knob — today the slot moves the scored prompt. (2) `ab_replay`
  re-fits the ruler under the replaying scorer, `restamped` under the stamp's scope: which ruler
  a replay reads. (3) `SearchpointDrillIn.tsx` prints rates at 1dp beside `webapp/lib/format.ts::fmtLevel`
  at 0dp: one precision. **Blocker:** the operator's answer to each. **Re-test:** ask the operator.

- **`infrastructure/llm/anthropic.py::AnthropicClient` cannot run an optimizer node on a current
  Claude model.** It refuses a set `reasoning_effort` (no mapping onto `output_config.effort`),
  sends `temperature` on every call (current Claude models reject sampling parameters), never
  sends the response schema (`output_config.format`), and sizes `max_tokens` from a stale 8192.
  The manifests offer `provider: anthropic` all the same. **Blocker:** no Anthropic credential on
  the dev machine, so none of the four can be measured, and which models reject sampling is a
  per-model fact `infrastructure/llm/registry.py::_MODEL_PROFILES` admits only as a measurement.
  **Re-test:** `settings.ANTHROPIC_API_KEY` is empty; once it holds a key, `probe-reasoning` a
  Claude model and this is one pass.

- **A head-to-head's arms do not share one optimizer model**, so "the only difference is the
  optimizer" is false at the proposer: each preset under `promptpotter/assets/optimizers/` pins its
  own, and an arm's lift and its dollars both carry the model as well as the method. **Decided: every
  arm on one model** — the controlled reading is the headline, each
  peer's model swap a declared deviation; its paper's model class is a side reading only.
  **Rides with:** the next head-to-head mint. **Re-test:**
  `grep -n "model:" promptpotter/assets/optimizers/*/pipeline.yaml` — more than one distinct model
  across presets, with nothing in the head-to-head pinning one, means the arms still differ.


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
- **Attribution** — `CandidateFacts.reading.sp_hash` is stamped forward-only, which is right and is enough for
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
  bumps `connectors/termnorm.py::_EXPECTED_REVISION`. **Re-test:** `connectors/termnorm.py::_EXPECTED_REVISION`
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
  who billed, not whose key. Once `key_source: host|user` lands (undeclared on
  every model today), the axis gains that dimension. Blocker: the coupon build
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
- **`_rebank_on_branch`'s re-bank has never been observed** — each corrected round is restated
  as one record, but the cycle it was measured on went with a store wipe, so the fix is
  reasoned, not seen. **Re-test:** repair a fork, then confirm each corrected round carries its own
  `round_restated` record on the branch; nothing under `tests/` asserts it.
- **The `seed` provenance layer has never been stamped by real data.** `pipeline_resolve.py`
  reads the CYCLE SEED's `pipeline_overlay` for it — one field name, three carriers, distinguished
  by the `source` each layer stamps (`campaign` / `seed` / `evolved`). Candidates move node
  params, so the `evolved` layer has live rows; no cycle seed on this workspace carries an overlay,
  so the seed feed is still dead here. A fork steered with `resume --steer NODE.PARAM=VALUE` exercises it.
  **Re-test**, from the checkout root where `.promptpotter/` lives and never from a worktree:
  `grep -rh cycle_seed .promptpotter/projects/*/campaigns/*/cycles/*/.runtime/ledger.jsonl | grep -c '"pipeline_overlay": *{'`
  — `0` means no live seed has reached the layer.

Closed items are not tracked here — `git log` is the history layer.
