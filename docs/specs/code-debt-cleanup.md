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

- **Nine account-pane page loads put the server 45 SECONDS behind, and every other surface waits
  there.** Reproduced twice by running `e2e/walk/account.spec.ts` before `e2e/walk/dashboard.spec.ts`:
  the dashboard's `/cycles`, its poll and `/ray` all fire together and none of the three answers for
  **45.8s**, so the chronology — `TimeRay` renders null until the ray reports `loaded` — is simply
  absent for three quarters of a minute on a page that otherwise looks finished. **It is not one
  slow read**: measured against this workspace, `/ray` is 0.08s, the dashboard 0.01s, `/cycles`
  0.04s, `/workspace/storage` 0.50s, `/workspace/storage-by-dataset` 0.40s, `/auth/activity` 0.35s.
  **And it is not generic load**: `dashboard.spec.ts --repeat-each=3` is 21 page loads and stays
  green, while nine ACCOUNT page loads do it every time. What those panes add is whole-workspace
  directory walks (`routers/campaigns/storage.py` — both `def`, so each holds a threadpool thread
  for its whole walk) that keep running after the browser that asked for them has gone. Action:
  bound or cache the workspace walk, and decide whether a read nobody is waiting for should still be
  running at all. **Rides with:** any work on the account modal's panes or on `storage.py` — and any
  report that the dashboard "hangs" after a visit to Account, which is the operator-visible form of
  this. **Re-test:** `npx playwright test --project=walk e2e/walk/account.spec.ts
  e2e/walk/dashboard.spec.ts`, then read the chronology test's DURATION — it carries a 60s bound for
  exactly this reason, and anything near it means the backlog is still there.

- **Pointed out, NOT investigated — each needs a look before it is a claim.** Filed together
  because they were all passed while working on something else, and none has been measured.
  (1) **The winner's prompt duplicates itself.** By round 8 of `swiss-invoices-eval__b1b4f5` the
  winner's `problem_description` carried three literal copies of *"Raw invoice text is provided
  directly as the input column."* and two of another sentence — both are
  `upstream_context`/`downstream_context`, already injected, being re-absorbed by L1's rewrite one
  copy per round (0 repeats through round 5, 3 by round 8). Mechanical, not semantic, and a real
  part of that cycle's 3.2x token growth. (2) **`domain/results.py::is_floor_pinned` reads
  `objective`** — under a formula that SUBTRACTS cost rather than scaling,
  a correct-but-expensive arm could read as "0.0 on every cell", which is a caveat about a
  degenerate reading claiming the arm got everything wrong. Harmless under the house formula, which
  clamps at `fitness`.
  **Rides with:** (1) any work on L1's prompt composition or a token-growth reading — the repeats
  are visible in any winner's `problem_description` you already have open; (2) any edit to a
  scoring formula or to `domain/results.py`.
  **Re-test:** each is a fresh measurement; none carries a verdict yet, so do not act on one
  without re-deriving it.

- **`AccessGate` and `AllowanceSpent` render only for a NON-HOST account, and the browser walk has
  no way to be one.** Two of the four onboarding surfaces are now covered — `ConsentGate` because a
  throwaway `PROMPTPOTTER_HOME` is unaccepted by construction, `WelcomeLockoutModal` through the
  `?auth_error=` bounce-back, which is its only trigger that does not require `unauthed`. The other
  two are blocked by one shared fact rather than by a missing fixture, which is what the entry used
  to say: `PROMPTPOTTER_AUTH=off` resolves `registered_or_default_identity()`, whose `issuer` is
  `None`, so `quota.py::_is_host` answers YES and `lifetime_ceilings` exempts it from metering
  (`AllowanceSpent` can never see a ceiling), while `shared/identity.py::claim_access_state` stamps
  nothing and defaults to active (`AccessGate` can never see a block). Both are correct — metering
  bounds a stranger spending the host's key, and `_is_host`'s own docstring says merging its two
  detectors is the trap — so writing a `user.json` or a `blocklist.json` changes neither answer.
  What it would take is an OIDC session in the harness, which is the real cost and the reason this
  is filed. **Rides with:** any work that gives the walk a signed-in identity — a fake issuer for
  the cold tier, or the first spec that needs to be somebody other than the box's operator. One
  assertion per surface behind it then, never a suite. **Re-test:** `grep -rn "auth_error\|issuer"
  webapp/e2e/` — while nothing there mints a session, both surfaces are unreachable by
  construction rather than merely unwritten.

- **The responsive walk proves only that no page scrolls sideways.** Six widths (375/393/412/768,
  landscape, 1440) run every pass against the shell, all five tabs, the account modal and login
  (`e2e/walk/responsive.spec.ts`). That catches content DELETED by an `overflow:hidden` wrapper,
  or a `viewBox`'d SVG that scaled instead of overflowing; it says nothing about whether a phone
  layout is USABLE, which stays a human pass. Two gaps sit behind it: the L4 panel, the candidates
  card and the lineage forest are swept at NO width, each needing a campaign of a shape the walk
  cannot count on finding; and the original mobile pass recorded no Lighthouse score, so a later
  one has no before to beat. Action: one Lighthouse run on the dashboard at 375, written down here.
  **Rides with:** any stylesheet or layout change that already has a browser open — the
  Lighthouse number is one run once you are there, and the three panels get their widths the
  next time a spec has a campaign of the right shape to hand (the spend tier mints one).
  **Re-test:** `grep -n "l4\|lineage" webapp/e2e/walk/responsive.spec.ts` — empty means those
  three are still unswept at every width.

- **The same seam, the other direction: a browser predicate whose server twin never returns its
  verdict — and it has been closed once already, wrongly.**
  `webapp/lib/derivations/nodeConfig.ts::overlaySetsModelOutsideAllowed` mirrors
  `domain/pipeline_overlay.py::overlay_sets_model_outside_allowed` rule for rule (a provider edit
  always taints; a model must sit in the node's permitted set; an absent node sanctions nothing) and
  drives `SteerForkPanel`'s pre-confirm warning. It was struck as fixed when the predicate's INPUT
  became server-authored — the served per-node `permitted` set — but the ask was the VERDICT, and the
  server reaches it only inside `fork-cycle` dispatch, where it 404s rather than answers. So deleting
  the client copy costs the operator the warning entirely; what is owed is a dry-run on the fork
  preview. **Rides with:** the next change to fork or steer — `fork-cycle` dispatch, `SteerForkPanel`,
  or anything adding a field to the fork preview response. **Re-test:** grep the served surface for a
  `steers_disallowed_model` field — while none is served, the browser copy is load-bearing and must
  not be struck again.

- **Holistic reframes — larger chunks, noted so they aren't mistaken for done; don't slip one into a
  release.** (1) **Tooltip/overlay consolidation:** most of the webapp's DOM `title=` attributes are
  teaching prose the browser renders as an unstyled, unselectable blob that dies on touch. Migrate
  **by string source, not by file** — `lib/terms.ts::TERMS` first, then the `VerifyPane` /
  `RoundFileView` header glossaries; leave the `title={same truncated string}` sites, where
  HoverCard is strictly worse. **Rides with:** any edit to `lib/terms.ts` or to a header glossary —
  migrate the strings that file already made you read. **Re-test:**
  `grep -rn "title={TERMS\[" webapp --include=*.tsx | wc -l` — while it reads 0, nothing has
  migrated. (2) **Whether L4 should reach the escalation machinery.**
  Not "each is built from scratch" — L2 and L3 already share `dispatch/`, `escalation/`, `cycle.py`
  and `OPTIMIZER_RESPONSE_MODELS`, and `application/optimization/CLAUDE.md` already splits the
  conceptual family from the structural one, which leaves only L4 outside, at the connector seam.
  So the question is not whether three strangers should converge; it is whether the recursion
  belongs inside the ladder it recurses on. **Asked and DEFERRED by the operator**, on the ground
  that it is structural while what M13 still needs is empirical — so this is held by decision, not
  by nobody having looked. **Rides with nothing, deliberately** — it is the one item here that must
  not be picked up on the way. **Re-test:** the preprint ships (`.scratch/m13-preprint.md` carries
  the stage state); until then, do not open it and do not re-file it as unasked.

- **Optimizer model repair-rate on heavy L2/L3 structured output — unmeasured.** What is owed is the
  measurement: a live cycle reaching L3, read under the model
  `promptpotter/assets/optimizer/pipeline.yaml` currently pins — read it off that file, never off
  this entry. **Rides with:** the next supervised campaign that escalates. The run is the expensive
  part and someone is already paying for it; this is a read of what it wrote.

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
  the second. **Rides with:** the next verifier-graded run (Harbor, spreadsheetbench), or any edit
  to `pobb/checks.py` or `intelligence/earned_blocks.py` — each site is inert exactly where someone
  working there would otherwise assume it fires. **Re-test:** `.venv/Scripts/python.exe -m
  promptpotter new spreadsheetbench-s10` past round 1 with `prompt_block_catalogue` on, then read
  the round file for a COLLAPSED verdict and `earned_blocks` under `OPEN` — if either now
  discriminates, the entry is stale.

- **A Harbor run's roster never lands on disk.** `connectors/harbor.py::_registry_tasks` memoizes per
  `(dataset, version)` for reads outside a run, but a Harbor version names an EDITABLE registry entry:
  a long-lived process keeps the first roster it read, and a served campaign's identity is recomputed
  from the current fetch rather than from the roster its run used. The run itself is consistent — its
  `InProcessWorkload` carries one resolution. Action: land the resolved roster beside
  `pipeline.resolved.yaml` when a cycle starts, and read it for that campaign; the memo is the
  operator's chosen interim. **Rides with:** the next Harbor campaign, or any edit to
  `connectors/harbor.py`. **Re-test:** `ls .promptpotter/projects/*/campaigns/harbor-*/cycles/*/`
  — no roster file beside `pipeline.resolved.yaml` means open.

- **The BROWSER still cannot bound a check-in run's spend; every other ingress now can.** The wire
  half is closed — `StartCheckinPayload` inherits `LaunchLimits`, `api-openapi.yaml` declares the
  three ceilings on it, and `start_checkin_campaign` admits under what was asked rather than under a
  bare `LaunchLimits()`. What is left is the SURFACE: the Start button in
  `webapp/components/ingest/IngestConversation.tsx` posts `campaign_id` alone
  (`lib/api/ingest.ts::postStartCheckin`), so a web operator's only ceiling is the account's own, and
  `e2e/spend/run.spec.ts` still clamps with `change-spend-budget` after the run is already live.
  It is filed rather than done because WHERE three money fields belong in a chat-shaped check-in is a
  design call, not a threading one. **Rides with:** any edit to the ingest Start surface — the
  check-in panel, `useIngestFlow`, or the draft's own override controls, which already render
  operator-set knobs beside this button. **Re-test:** `grep -n spend_budget_usd
  webapp/lib/api/ingest.ts` — while it is absent, the browser sends no ceiling.

- **NEXT — L4 re-reads `inner_tasks.yaml` from disk during a run.** `application/runner/inner/ruler.py` and
  `spawn_context.py` each call `tasks.py::load_inner_tasks`, so an edit mid-run splits one run's cells
  across two panels — the per-run-state shape `InProcessWorkload` closed for in-process connectors.
  Action: the outer run resolves the panel once and hands it down through the spawn context.
  **Rides with:** any edit under `runner/inner/` — both call sites are in that one directory.
  **Re-test:** `grep -rn "load_inner_tasks(" promptpotter/application/runner/inner/` — more than one
  call site means open.

- **The CLI's verb table defers every handler import, for startup speed.**
  `presentation/cli/campaign_runner.py::COMMANDS` resolves each handler through
  `importlib.import_module` at call time — the deferral [`../developer/conventions.md`](../developer/conventions.md)
  refuses, counted in `complexity_ledger`'s `deferred_imports`. Action: time `python -m promptpotter
  --help` cold with the handlers imported eagerly, then hoist them or state the exemption beside the
  table. **Rides with:** adding or renaming a CLI verb — you are in `COMMANDS` already, and the
  timing is one cold `--help`. **Re-test:** the operator answers whether that measured startup cost
  justifies the deferral; until then `grep -n import_module
  promptpotter/presentation/cli/campaign_runner.py` hits.


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
  hand-rolled readers.
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
- **Swapping a model means hand-editing two `pipeline.yaml` lines and remembering to revert both**,
  and a leaked pin mislabels the next run. The half of this that was about `response_format` is
  closed: the OpenRouter catalogue's `supported_parameters` already answers whether a route takes
  the key, and `PipelineSchema._refused` spends that answer on the search space rather than on a
  badge — so an unsupporting model no longer has to be discovered by paying for it. Blocker: the
  swap-verb, which is a new capability.
- **`infrastructure/llm/json_parse.py::try_groq_json_validate_repair` meters a fabricated ZERO** — it
  rebuilds `LLMResponse` with `usage` hardcoded to zeros after a `json_validate_failed` 400 that was
  already billed. The 400 body carries no `usage`, so the count is unrecoverable, and
  `unpriced_tokens` is the wrong home: it means price unknown, not count unknown. Never estimate from
  content length. Dormant — Groq-only, every configured provider is `openrouter`. Blocker:
  `TokenUsageRecord` has no unknown-count dimension, and the account gate leans on a count always
  being knowable.

**Needs a live run, not a decision:**
- **`_rebank_on_branch`'s re-bank has never been observed** — fixed to take each corrected round
  through the whole ingress, but the cycle it was measured on went with a store wipe, so the fix is
  reasoned, not seen. Repair a fork; confirm each corrected round carries its own `round:complete` on
  the branch.
- **The `evolved` and `seed` provenance layers have never been stamped by real data.**
  `pipeline_resolve.py::_evolved_overlay` reads the CANDIDATE's `pipeline_overlay` and the seed
  layer the CYCLE SEED's — one field name, three carriers, distinguished by the `source` each
  layer stamps (`campaign` / `seed` / `evolved`); every candidate on this workspace
  is prompt-only, so both feeds are dead here and only the merge primitive beneath them is
  covered (`tests/test_pipeline_resolve.py`). A campaign that actually MOVES a node param
  exercises both, and the trap they guard is documented at `_evolved_overlay`: reading
  `resolved_pipeline_params` instead would stamp every param `evolved` at once.
  **Re-test:** `grep -rho '"pipeline_overlay": [^,}]*'
  .promptpotter/projects/*/campaigns/*/cycles/*/rounds/*.json | sort -u` — while the only
  distinct value is `null`, no live row has reached either layer.

Closed items are not tracked here — `git log` is the history layer.

## verified 2026-09-12

- **Pointed out, NOT investigated** — VALID. Code-verifiable sub-claims (2), (3), (4) all hold. `domain/results.py::RoundResult.scoreboard` is still a `@computed_field` at line 845 returning `list[ScoreboardRow]`; `results.py` imports `display_rank_key` from `rendering` at line 18 — the coupling the entry describes. `is_floor_pinned` (domain/results.py:530) still reads `r["objective"]` at line 544; line number drifted from the filed 523 but the claim holds. `halt_at_accuracy` has 28 occurrences across `promptpotter/` — the "~14 call sites" figure is an undercount, but the pass-through shape across CLI, REST, launcher and runner stands. Sub-claim (1) is empirical and cannot be verified from code.
- **No BROWSER is ever opened in CI** — VALID. All four onboarding components confirmed present: `webapp/components/onboarding/{AccessGate,ConsentGate,AllowanceSpent,WelcomeLockoutModal}.tsx`. No `webapp/e2e` directory. No `*.spec.ts` files in webapp/ (only `*.test.ts` for lib/derivations and candidates components; none cover onboarding or `app/page.tsx`). `scripts/smoke_wheel.py` contains no browser navigation calls. Re-test condition (empty result) confirmed.
- **FIVE node kinds spell one concept** — VALID. All five `NodeKind` members still declared at `domain/pipeline_schema.py:139–143` (`LLM`, `GENERATION`, `LLM_OPTIMIZER`, `OPTIMIZER_PROMPT`, `AGENT`). `THINKING_KINDS` frozenset still carries all five (lines 158–165). `presentation/teleprompter.py:236` still writes `"type": "llm"`. `llm/optimizer` (4 occurrences) and `agent` (1) still appear in `promptpotter/assets/` pipeline.yaml files alongside `generation` and `optimizer_prompt`. No consolidation has occurred.

## verified 2026-09-13

- **The same seam, the other direction** — VALID. Re-test ("grep the served surface for a `steers_disallowed_model` field") run: the name appears as a LOCAL variable in `presentation/api/middleware/command_dispatcher.py:869` — the server now ENFORCES the check inside `fork-cycle` dispatch, 404ing disallowed steers (exactly as the entry says, "it 404s rather than answers"). It is not a served response field; no fork-preview endpoint returns it. `webapp/lib/derivations/nodeConfig.ts::overlaySetsModelOutsideAllowed` is therefore still the only source of the pre-confirm warning in `SteerForkPanel`, and deleting it would still cost the operator that warning.
- **Holistic reframes** (tooltip consolidation half) — VALID. `grep -rn "title={TERMS\[" webapp --include=*.tsx | wc -l` = 0, confirmed. No `lib/terms.ts::TERMS` string has migrated to HoverCard.
- **InProcessRun has no arming context** — VALID. `_PROGRAM: ContextVar[DspyProgram | None]` still at `connectors/dspy_module.py:58`; `_PANEL: ContextVar[dict[str, Any] | None]` still at `connectors/harbor.py:123`. Both in-process connectors still reach independently for a ContextVar to carry per-run state, as described.
