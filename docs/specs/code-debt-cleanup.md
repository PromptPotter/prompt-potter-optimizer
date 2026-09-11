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

- **Pointed out, NOT investigated — each needs a look before it is a claim.** Filed together
  because they were all passed while working on something else, and none has been measured.
  (1) **The winner's prompt duplicates itself.** By round 8 of `swiss-invoices-eval__b1b4f5` the
  winner's `problem_description` carried three literal copies of *"Raw invoice text is provided
  directly as the input column."* and two of another sentence — both are
  `upstream_context`/`downstream_context`, already injected, being re-absorbed by L1's rewrite one
  copy per round (0 repeats through round 5, 3 by round 8). Mechanical, not semantic, and a real
  part of that cycle's 3.2x token growth. (2) **`domain/results.py::RoundResult.scoreboard` is a
  display projection living on the data model** — a `@computed_field` returning `ScoreboardRow`s,
  which is what made `results` reach into `rendering` at all. (3) **`domain/results.py:523`
  (floor-pinned) reads `objective`** — under a formula that SUBTRACTS cost rather than scaling,
  a correct-but-expensive arm could read as "0.0 on every cell", which is a caveat about a
  degenerate reading claiming the arm got everything wrong. Harmless under the house formula, which
  clamps at `fitness`. (4) **`halt_at_accuracy` is threaded through ~14 call sites** as a
  pass-through parameter across CLI, REST, launcher and runner.
  **Re-test:** each is a fresh measurement; none carries a verdict yet, so do not act on one
  without re-deriving it.

- **No BROWSER is ever opened in CI**, so a whole class of first-user breakage ships green.
  `scripts/smoke_wheel.py` now serves the wheel over a real socket, but nothing navigates a route:
  there is no Playwright suite anywhere, and
  `webapp/components/onboarding/{AccessGate,ConsentGate,AllowanceSpent,WelcomeLockoutModal}.tsx` —
  the four surfaces a brand-new account meets before it sees anything else — have no test of any
  kind. Vitest is jsdom units of primitives and pure derivations; nothing renders `app/page.tsx`.
  Two arcs: (1) a scripted browser walk of the zero-campaign path, asserting console-clean; (2)
  coverage on the four onboarding components.
  **Re-test:** `ls webapp/**/*.spec.ts webapp/e2e 2>/dev/null` — empty means both are open.

- **The mobile pass was verified at 375/1440 on chat/dashboard/files/verify only.** Unswept: 393,
  412, 768 and landscape; login, onboarding, l4, account modal, candidates, lineage. No Lighthouse
  number was recorded, so there is no before/after. Action: sweep + record one pass.

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
  belongs inside the ladder it recurses on. **Asked and DEFERRED by the operator**, on the ground
  that it is structural while what M13 still needs is empirical — so this is held by decision, not
  by nobody having looked. **Re-test:** the preprint ships (`.scratch/m13-preprint.md` carries the
  stage state); until then, do not open it and do not re-file it as unasked.

- **FIVE node kinds spell one concept — "runs a model".** `domain/pipeline_schema.py::NodeKind`
  closed the vocabulary and named the families, which is what makes the redundancy countable rather
  than merely suspected: `llm` (1 site, written by `presentation/teleprompter.py` and actually a
  VIEW kind spelled into a manifest), `generation` (20), `llm/optimizer` (4), `optimizer_prompt` (9),
  `agent` (1). `THINKING_KINDS` is the predicate that keeps the split from spreading, but it is a
  containment, not a fix — the five stay declarable and a sixth is one connector away. Not a rename:
  `runs_llm` reads `GENERATION` *specifically* while `_derive_node_kind` treats all five alike, so
  collapsing them decides which nodes newly carry the model axis, and every `datasets/*/pipeline.yaml`
  is operator-curated on-disk config, so the survivor is the operator's call and not a sweep's.
  Action: settle whether the survivor is `generation` or `llm`,
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
