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
  belongs inside the ladder it recurses on. **Asked and DEFERRED by the operator**, on the ground
  that it is structural while what M13 still needs is empirical — so this is held by decision, not
  by nobody having looked. **Rides with nothing, deliberately** — it is the one item here that must
  not be picked up on the way. **Re-test:** the preprint ships (`.scratch/m13-preprint.md` carries
  the stage state); until then, do not open it and do not re-file it as unasked.

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

## Blocked — named blocker

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
