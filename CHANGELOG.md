# Changelog

All notable changes to this project will be documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- **Compare anything against anything.** Tick two campaigns and each gets a card on its own winner. Every card carries the dashboard's cladogram as a map: walk back through the rounds and click any searchpoint to move that card onto it, or add it beside the winner. Inner runs are channels too, at any depth.
- **What two searchpoints ARE, not just what they scored** — configurations lined up key by key, differing ones first, beside the parent chain that led to each.
- **A cell nobody measured is not a zero.** A coverage strip says which absence each blank is, and a channel on a different ruler or dataset is tagged rather than averaged in.
- **"What if we had scored it differently"** — put a branch on the chart twice, once as it ran and once under another formula or fewer samples, and read what changed: the winner, the round the two lineages part at, how much of the record survives.

### Changed

- **Picking a set of cells moves only the `overlap` bars.** Accuracy, θ and the composite stay on each candidate's own cells instead of vanishing, and a candidate that answered part of the set is blank rather than drawn short beside a full one.
- **The shared reading is `overlap` everywhere** — chart, terminal line, `log.md`, `review.md`, round document. It read "trajectory" on screen only, a word two other things already mean.

## [0.8.12] — 2026-08-24

> Start and pause the optimizer from the chat itself, read what every account on the install spent beside what it produced, and compare any campaigns across datasets. 77 commits since `v0.8.11`.

### Added

- **Optimize while you use it, from the composer** — the chat's Tools row carries the run: one switch firing the same start/pause verb as the dashboard's play/pause. Pause finishes the sample in flight so its datapoint persists, and says so rather than looking hung.
- **A Compare tab** — pick any campaigns from any datasets and read them together as grouped bars, stacked bars, a line each, or a forest, over the cells every selected campaign measured. Roster, comparability, variance and power load with the tab; ranking is a press. Reachable from any campaign, not only from inside one member of the set.
- **Exact statistics on a small panel** — the Hodges-Lehmann shift, an exact Wilcoxon p and an exact interval, with the smallest p your cell count can reach and the cells a corrected verdict would need shown beside the verdict. A six-cell panel says what six cells can and cannot establish.
- **Every θ names the ruler it was read on.** A score is comparable to another only when both were read on the same scale. A cold ruler says so, and an unknown scale never renders as comparable.
- **Layered limits per account, not one counter.** A launch clears a rate limiter, a concurrent-cycles ceiling and a daily campaign count (UTC midnight reset) before it reaches money — then a spend ceiling metered in dollars **and** tokens, because a dollar cap goes blind on an unpriced model. It is admitted whole or refused rather than clamped into a run that halts halfway, holds that ceiling as a reservation so two launches cannot spend one remainder twice, and its spend is banked before any delete, so a ceiling cannot be re-earned by deleting what it was spent on. `set-budget` then `resume` continues a campaign halted on its ceiling.
- **What every account on the install spent, and what it produced** — costliest first, on the operator-admin channel ([ADR-0004](docs/adr/0004-operator-admin-channels.md)). An account the report cannot read is named rather than dropped.
- **Prompt-cache reads are metered and priced** at the provider's cache tier, so a repeated prefix shows up as what it actually cost.
- **One address per view** — `#/c/<campaign>/<cycle>/<view>`. A reload, a shared link and the back button all land where you were, and `?at=` folds the whole page to one moment on the run's time ray.
- **A press that buys a group.** Where one round runs for hours, name how many campaigns launch together instead of arming a single round's worth; the arming is spent by exactly those.

### Changed

- **A round is won against the previous round's winner**, not the origin. Adoption is a strictly positive θ lift over that parent, and the elimination bar opens wide and tightens with depth, so a near-tie buys a few more cells before an arm is cut.
- **A sample id is re-derivable from the dataset's own name.** The row order that minted the ids is part of the dataset's identity, so an id points at the same question for the dataset's life, and a different ordering can exist only under a different name.

### Fixed

- **`reset` reaches the whole disk**, L4 sandboxes included. Each one banks its spend before its rows are taken, a sandbox that cannot be attributed to a tenant is kept and named, and `--dry-run` lists what it would take.
- **Absent is not zero**, from the sample tape to the heatmap: an errored row carries `ERR` and is withheld, never painted as a measured miss. `Best 0%` no longer stands in for a pass that has not scored yet.
- **Pause no longer takes the host down.** A shutdown announces itself on the admin channel and names any run it interrupted.
- **A check-in's own origin is measured** — an existing dataset no longer scores the first campaign's starting prompt under this campaign's identity.
- **Four findings from an audit of the published deployment** — the OIDC identity trust order and issuer pinning, stored XSS in the file viewer, a service that could rewrite its own checkout, and the admin bot sharing the app's secrets file.
- **The admin bot stops failing silently** — a gated message and a lost one no longer look the same, and a reply the transport refused leaves a trace.

### Technical Details

- **77 commits since `v0.8.11`** (2026-08-14 → 2026-08-24): 27 fixes, 19 features, 15 refactors, 9 docs, 5 chore, 2 perf.
- **BREAKING — start clean.** `composite_ci_*` is `mean_fitness_ci_*` and `matched_origin_*` is `matched_parent_*` across the served API, the generated TypeScript and the round document; existing round documents are not read back. The content-addressed measurement archive replays by `sample_id`, so nothing is re-paid.
- `pyproject.toml` → 0.8.12; `APP_VERSION` derives from it and `uv.lock` records it. **A release no longer voids a self-optimization corpus** — `APP_VERSION` left the L4 measurement identity, replaced by a digest over the four modules that actually decide a cell's number. All four changed this release, so banked `promptpotter-self` outer rows re-measure anyway; inner rows cache-serve.
- Backend unchanged since v0.8.10 — no new pairing.
- `python scripts/gate.py` runs every check CI runs in one invocation, on one declared parallelism budget, and names the slowest check in its verdict.

## [0.8.11] — 2026-08-14

> The release that makes `pip install promptpotter` real: the name is claimed, the wheel is less than half the size, a plain install is the engine rather than a web server, and publishing a GitHub Release is what puts it on the index. 42 commits since `v0.8.10`.

### Changed

- **The distribution is named `promptpotter`** — `promptpotter-optimizer` was never registered on any index. One word for the brand, the import, the CLI and the install.
- **The wheel is 1.6 MiB, not 3.5** — the dashboard's JavaScript source maps were 55% of every `pip install`, shipped because `stage_webapp` copied the deploy box's export wholesale. They exist so a live DevTools session on the deployed dashboard resolves a React error to a component and line, and a browser fetches one only when DevTools is open. The staging step strips them; the box reads `webapp/out` from a checkout and still has them.
- **The PyPI project page renders** — it is `README.md`, whose 2 logos and 50 doc links were relative and therefore 404 against `pypi.org`; they are absolute now. The sidebar gained Documentation / Changelog / Issues, and the classifiers say who this is for.
- **A telemetry event now has to reach a remote sink** — five mid-round events (candidate created / scored, round winner, L1 critique, layer applied) routed only to the local Langfuse-shape mirror, which nothing reads, restating what the ledger and `rounds/round_NNNN.json` already carried; each cost its writer a second emit at the same call site. They are gone, and the dispatch `match` they lived in is one routing table whose wholeness `ObservabilityBridge.__init__` now asserts — an `Event` with no row used to be dropped in silence.
- **`pip install promptpotter` is the engine, not a server** — the seven web/identity packages moved to a new `[api]` extra and `openpyxl` to `[excel]`, taking a plain install from 44 packages to 28. Reachability was measured, not argued: the CLI and `application/embedded_run.py` import to exactly the nine that remain. `[all]` folds both back in, so `.[all,dev]` and `deploy-linux/` are unchanged; serving the dashboard now means `promptpotter[api]`, and an `.xlsx` ingest without `[excel]` fails as an ingest error naming the extra.

### Added

- **PromptPotter is a DSPy optimizer** — `pip install promptpotter[dspy]`, then `PromptPotterOpt(...).compile(program, trainset=…)` returns a copy of the program with the winning prompt applied. An extra on the one distribution rather than a second package: after the Phase-A/B seams landed the adapter was ~300 lines, which does not carry its own CI and release matrix. Your metric is the scorer — its float rides an observation key the campaign formula reads, so no grading rule is restated on our side — and prompt *and* model settings evolve together, which is the half `with_instructions()` cannot reach. `acompile()` is the peer for a host that already has an event loop. [usage](docs/developer/dspy-optimizer.md) · [packaging boundary](docs/adr/0006-embeddable-core-and-extras.md)
- **A campaign now emits an artifact, not just a report** (`cycles/{id}/export.json`) — the winning prompt by field name plus the provenance that makes its fitness readable: the formula the number was computed under, n, the lift and its interval, θ, the rows' own hash, the optimizer manifest, and an `artifact_version` a reader refuses on. `domain/export.py::parse_prompt_export` reads it back to a `PromptTemplate`. Reader contract: [`docs/developer/stable-api.md`](docs/developer/stable-api.md) § 5c.
- **Publishing a GitHub Release publishes to PyPI** (`.github/workflows/publish.yml`) — dashboard build, full `build_release.py`, a tag/version guard, then a wheel smoke outside any checkout that demands the dashboard CI's `--no-webapp` build cannot. Uploads over Trusted Publishing, so no credential is stored.

### Technical Details

- **42 commits since `v0.8.10`** (2026-08-08 → 2026-08-14): 18 fixes, 9 features, 8 refactors, 7 docs.
- `APP_VERSION` + `pyproject.toml` → 0.8.11. It rides the L4 measurement identity hash, so banked `promptpotter-self` outer rows re-measure; inner rows cache-serve.

## [0.8.10] — 2026-08-08

> Continued 0.8.x beta-hardening toward the 0.9.0 broad launch. 123 commits since `v0.8.8` <!-- 0.8.9 was an in-flight version bump with no release of its own; consolidated here -->. No paired backend release this cycle. Zero released-between and zero long-lived on-disk data, so contract and on-disk changes ship without compatibility shims — **start clean** (see BREAKING).

### The build, in order of completion

One shape dominates: a number, a verdict or a prompt **composed from more than one thing and published as one**. Each arc collapses another instance to a single owner.

- **One accuracy basis** *(Jul 27 – Aug 1)* — a published accuracy was a union of rows measured by *different* configurations: the sidebar read 57%→78% beside a best-candidate bar of 0.679. One basis now, and `hit`/`hits` are deleted — an unreachable ceiling made every surface hanging off them read zero on a graded scorer.
- **L4 gets one measurand** *(Jul 28 – Aug 1)* — outer fitness is what the inner search *kept*, not the arms it discarded. Four multiplicative factors over eight proxies were measured on the first complete 39-cell panel, disqualified, and replaced by **one linear term** whose effect *is* the mean logit lift. New `rank-optimizer-prompts` ranks optimizer-prompt edits against the unedited original at zero LLM calls.
- **The wheel is the product** *(Jul 30 – Aug 1)* — PromptPotter is a package, not a checkout: `REPO_ROOT` had resolved to `site-packages/` once installed. Three named roots replace it, a dataset's shipped definition splits from its fetched rows, and a third party registers a connector through an entry-point group without forking us.
- **Verdicts stop grading on holes** *(Aug 2 – 7)* — an unscoreable cell raised the denominator and never the numerator, so a hole flattered a candidate; the verdict's subject could be an arm the round had refused to crown. `replicate_survivors` is out and **`verify`** is in — more samples, never the same cell twice.
- **Resume, fork and repair grow a memory** *(Aug 3 – 8)* — a round fingerprints its optimizer packages and stamps the optimizer that ran it, resolved model included; a correction cuts at a candidate, not a round. The escalation fold was dead across 89 real L2 fires, so every resume had been re-spending a spent budget.
- **Metering** *(Aug 5 – 6)* — OpenRouter DeepSeek priced **1.6×** off DeepSeek's own first-party key; a silent deadline retry and a mis-ordered cache write each lost a paid call with every on-disk surface clean. Metering now precedes storing, and reasoning tokens are recorded on the success path.

- **The prompt budget — bounded where it is produced** *(Aug 6 – 7)* — our own design notes were shipping as prompt text: a Pydantic class docstring rides the response schema on every optimizer call, **2088 of `l1_generate`'s 3513 schema characters and 63% of `l2_context`'s**. Net **`l2_context` −31.5% of the call, `l1_generate` −10.4%**. Candidate prompts had been shipping the parent's fields twice on 26% of candidates. §0 now carries the general rule.
- **The dead air is at process start** *(Aug 6)* — a round is ~99% LLM latency, so the cost sits at process boundaries, which supervision pays over and over. The sample index persists its delta cursor instead of re-scoring the whole slice on every start: **7.15 s → 0.58 s**.
- **Two samples in flight, on a button** *(Aug 7)* — arm the scoring walk to hold two, **~1.4×**. Absorbed in walk order with the overshoot discarded, so a candidate's rows are identical at either depth and no campaign becomes babysat for using it. Browser-only, host-admin, one round per arming; no CLI verb, by design.
- **The self-optimization surface** *(Aug 7 – 8)* — a candidate bar says whether it was measured or replayed; ranking is served instead of rebuilt by two panes that disagreed about the same rows; the running node is served, so the canvas no longer goes dark for a whole optimizer call; the run is the last item in the chat thread.
- **One gate** *(Aug 8)* — `scripts/gate.py` runs every check CI runs in one invocation, re-execing into the locked environment. 80 s against 299 s serial; its first run caught a real red.

### Bug Fixes

- **A measurement is filed under the asker that paid for it** — three provenance bugs had been re-running measurements already banked, and none of them raised: the ids were merely wrong.
- Two dataset-name rules disagreed, so an upload that succeeded 400'd at mint; a bare `reset` addressed the pre-sign-in directory; the daily quota was keyed to the local day.
- CI: a 3.12 wheel venv against a `>=3.13` requirement, a stale OpenAPI snapshot, and four endpoints declared camelCase where the app serves snake_case.

### BREAKING Changes

- **Many, and none shimmed** — there is nothing released to be compatible with. The install layout, a dozen config keys, `hit`/`hits` and five verbs moved or went; `restamp --apply` migrates what can migrate.
- **Start clean.** Measurement identity now also hashes the response schemas, the renderers' own source and the inner benchmark's node configs, so banked self-optimization outer rows re-measure; inner rows cache-serve.
- **Same backend as 0.8.8** — runs against TermNorm v1.2.0; no new pairing.

### Technical Details

- **123 commits since `v0.8.8`** (2026-07-19 → 2026-08-08): 46 fixes, 28 refactors, 28 features, 14 docs, 3 chore, 2 perf, 1 test (+ one squashed sweep PR and merges).
- `APP_VERSION` + `pyproject.toml` → 0.8.10. 0.8.9 was an in-flight bump with no release of its own.
- See individual sections above for the full change set.

## [0.8.8] — 2026-07-19

> Continued 0.8.x beta-hardening toward the 0.9.0 broad launch. 292 commits since `v0.8.2` <!-- last published release was v0.8.2; 0.8.3–0.8.7 were internal version bumps with no separate notes, consolidated here -->. This is a **paired release** with the TermNorm backend (see BREAKING → Paired backend). Zero released-between and zero long-lived on-disk data, so on-disk and contract changes ship without compatibility shims — **start clean** (see BREAKING).

### The build, in order of completion

This release is a five-week arc, and the order the work landed *is* the story — each capability sat on the one before it. What follows walks that order rather than grouping by theme, so the shape of the release is legible: honest measurement first, then the loop that trusts it, then the recursion that optimizes the loop itself.

**Search hygiene + honest round verdicts** *(Jun 11–14)*
- **Search-space narrowing, per-node parameter locks, shape-aware prediction, origin identity** — the optimizer stops wandering dimensions a node can't move, holds a locked knob across resume/fork, and predicts in the shape the pipeline actually returns.
- **Round-health verdicts** — a source-stamped warning kind + context-aware round health; the webapp surfaces the **degraded-round verdict** and danger-tints the Stop button, so a round that got *worse* can't read as progress.

**Origin becomes a first-class halt-and-decide gate** *(Jun 16)*
- The **origin is a real baseline, gated.** Before round 1, the loop stops on the origin as a checkpoint: an interactive **rescore / proceed / abort** decision, offered identically across CLI, webapp, and chat. A broken floor **halts the run** instead of manufacturing illusory improvement off a bad reference. This is the change that made every later score trustworthy — you cannot compare candidates to an origin you never actually measured.
- **`candidate_library`** — the pipeline gains a node-typed *fourth* input alongside the prompt, params, and dataset; soft, so it informs the search without blocking a mint.

**Pluggable mechanisms + the evidence-starvation cascade** *(Jun 17–18)*
- **Pluggable campaign mechanism toggles** (sorting + early-abort), a `/lineage` conditional-`304` fast-path, a copy-to-clipboard icon on dashboard boxes.
- **Evidence-starvation detection (R-48).** A round that can't gather enough signal to decide anything is now *detected*, routed to L2 for a strategy change, and — if still starved — terminated with a backend-supplied reason rather than emitting a confident-looking non-result. Round-1 critique and `exploration_budget` are seeded so the very first round's escalation signals are real, not placeholders.

**The chat-first front door + one-source lineage** *(Jun 19–20)*
- **Chat is the front door.** The webapp root is now a live **activity stream** with in-thread loop control — skip a searchpoint, watch babysat provenance land, drive a remote run from the bar — fed by **cross-process SSE via a ledger tail** so a run launched in one process streams live into a browser attached to another, with a running composite updating in place. Contract: `docs/specs/chat-foundation.md`.
- **Live lineage as one source, two slices** — the tree and the data view stop diverging; plus three M10 levers (trace link, ingest auto-skip, origin reuse).

**Durable check-in + security hardening** *(Jun 21)*
- **Disk-backed check-in survives restart.** The origin check-in is persisted, so a killed server resumes mid-resolution instead of re-asking. A clean check-in **auto-mints** — the review panel appears *only* when a real gap or resolver question remains — and resolver degradation is surfaced to the operator rather than swallowed. A consent gate plus path/session hardening closes the perimeter around it.

**Parameter locks, live focus, MECE storage** *(Jun 22–23)*
- **Per-node param locks persist across resume/fork**; a live **Focus chain** with comparable samples and mint timing; a slimmed per-cycle ledger writer.
- **MECE storage taxonomy.** Measurements relocate under a mutually-exclusive, collectively-exhaustive on-disk layout with a recycle-bin delete; the webapp renders storage as that one hierarchy with a Files-tab placement. *(The recycle bin was later dropped — an archived campaign stays in `campaigns/` behind `lifecycle_status`, so a campaign has one home; see `docs/operations/persistence-and-state.md` § Why this shape.)*

**The θ ruler — one comparable scoring axis** *(Jun 23–24)*
- **Every decision now reads Rasch ability θ, not subset accuracy.** This is the release's measurement backbone. A per-subset accuracy never compared across rounds — an easy sample set flattered a weak candidate. Round-winner election, promotion/elimination gates, and cross-cycle elevation all now compare on a **fixed-delta θ primitive**: the cross-round comparability ruler. Along the way: a **measurement-provenance grade** de-biases the archive (a cached score and a fresh one aren't weighed the same), θ is threaded onto the candidate read-models and shown **wherever a candidate is inspected**, a **θ-exact stall replay** unblocks `per_round_resubset`, and a fork seed can A/B a selection-policy knob, not just run limits. **Coherent θ measurement** ties it together — one δ ruler, resubset ON, an A/B engine — and the ruler **graduates 1PL→2PL where it wins on held-out data**. A config coupling/provenance map documents what each knob moves and what it clashes with.

**L4 — the recursion goes live** *(Jun 24 – Jul 1)*
- **PromptPotter optimizes its own optimizer prompts.** An **in-process execution seam** plus the **`llm_only` connector** let a cycle spawn inner cycles with no second server, so the outer loop runs PromptPotter *as its own target* and searches the L1/L2/L3 optimizer prompts that drive every campaign. **Inner-cycle recursion is live-validated end-to-end.** The outer optimizer prompts reach the inner cycle (slice 3b); a **read-only bridge** lets you follow inner loops from the webapp (with sidebar filter/resize); and a per-node prompt layout lets L4 **edit the inner nodes' information flow and rewards lean layouts** — the optimizer learns to hand its inner self a shorter, denser prompt.

**Outer-loop screening + the reaper** *(Jul 2–3)*
- Practical-equivalence elimination gate; an **evidence-driven outer loop** with measurement-identity coherence; screening geometry with a NO-OP noise-floor probe; an un-starved evidence channel + pre-mint baseline batch; a heartbeat-verified **staleness reaper** with one terminal-stamp seam; and an honest connector/pipeline/samples UI for L4 self-optimization.

**One paired-margin gate + the champion lifecycle** *(Jul 4)*
- **Selection collapses to one gate.** A single **paired-margin gate** replaces the PoBB dominance+equivalence pair, and **one deterministic shared round order** replaces the online CAT picker (deleted). On top of it: a **champion registry** (`champion refresh`) selects the winning optimizer prompt set, `champion apply` **graduates it into the distributable `_optimizer` config**, and a capability-gated **L4 Lab** (resource matrix, per-cell inner panel, Champion Console, blocked paired-outer verdict) lets a dev follow the recursion read-only. Outer fitness is **composed and delta-led** — an optimizer prompt is scored on the *lift* it produces on a fixed inner benchmark, not the inner run's raw accuracy.

**Bounded and cheap by default** *(Jul 5)*
- A `noise-floor` diagnostic verb with **composite-CI whiskers**, opt-in **successive-halving replication**, an opt-in **lives/hearts round budget**, and a deterministic inner optimizer with composite-CI on C0 — so a self-optimization run stays inside a small, predictable spend.

**The schema is the second prompt** *(Jul 8–9)*
- **A structured-output schema teaches the model as much as the prompt does.** The `llm_only` output schema is now treated as a *second prompt*: the field **description axis** is made reachable, honest, and taught, and both `schema_field_rename` and the output-schema description become **unlockable optimizer axes** on `l1_generate` (L2/L3 unlock them by forking; nested params became a declared mechanism). `evidence_grounding` is hoisted so a variant can't cite what it wasn't shown. The webapp serves `backend_type` so the sidebar stops guessing L4 from a name, hearts gain a denominator, and webapp commits are gated on `tsc` + `eslint`.

**Resolver proposals, archive perf, one candidates surface** *(Jul 10–13)*
- The resolver **hands the operator its proposals** instead of applying them silently; the measurement index **appends instead of rewriting itself** and the archive stops re-folding on every read (two perf passes); the MCTS loop is closed and the block library nothing was reading is reattached; the unusable fastest route is fixed by **changing the model, not the route**; and the what-if grid and lineage forest **fold into one candidates surface**.

**Three-tier access model + delegated capabilities (ADR-0005)** *(Jul 15)*
- **Team-ready access control.** A **three-tier access model** (host-admin kept first-class) with perimeter hardening; **ADR-0005 delegated principals + capability scoping** so a steer can be handed out narrowly and attenuated; an **origin allow-list** that gates who can fork the human model-steer, with an allow-list-aware steer warning and a canonical node-config render; the allow-list is authored from one source (setup checklist + live edit).

**One lineage tree + strict-by-default gates** *(Jul 16–17)*
- **Lineage is served as one tree** — `course → candidate → (course | sample)` — and a candidate is **named when it is minted**, not when its round closes (the separate `/lineage` route is retired; a fork is a candidate, not a course). New CI gates **ratchet `Any` params and guard NUL bytes**, and `StrictModel` is **`forbid`-by-default** with any lax model written down and counted.

**Final tuning for a distributable `promptpotter-self`** *(Jul 19)*
- **Dispatch shaping** feeds the outer generator inner-run narratives and earned reasoning blocks; a **model reasoning-token floor** is enforced at preflight; an **origin eval-budget** and a whole-inner-spec identity fingerprint land; the **`justlogic-d234`** inner instrument replaces the retired cuts. The θ-implied accuracy CI is served on the candidate bar, and the sidebar consolidates onto one hover-card primitive.

### Bug Fixes

- **`0` is a value, not an absence** — four read surfaces that silently dropped a real zero now carry it; symmetrically, an **absent** verdict/measurement is *excluded* (and bounds the run), never floored to `0`.
- **L1 grounding** — a variant could cite a panel it was never shown and still pass; the response schema never reached the provider. Both closed.
- **L4 inner instrument measured nothing while four surfaces reported it fine** — the instrument-degeneracy class is fixed; an optimizer prompt parse failure is now charged to the round, not to nobody.
- **The round document loses twelve fields** — the round file now carries a typed model; five operator-read numbers the server never actually said, and five engine decisions taken from the wrong field, corrected.
- **The dashboard stops overstating** — "best so far" no longer inflates past the evidence, and a single-source dashboard kills the two-state flicker between the live and persisted views.
- Web check-in crash; SSE shutdown + framing via `sse-starlette`; mint pipeline-config setup errors surface as `422`, not a raw `500`; connection loss stops impersonating a run phase and the navbar indicator stops vanishing; `dashboard.json` rewind parity.
- CI: deps pinned via `uv.lock`; `setup-uv` pinned to `v8.2.0`; prod installs the pinned lock graph.

### BREAKING Changes

- **Start clean — pre-0.8.8 measurements are not comparable.** Scoring decisions now read Rasch ability θ (not subset accuracy) and the on-disk cycle layout changed (session tier removed, `CycleLayout` owns layout, measurements relocated under the MECE taxonomy). By policy there are no compatibility shims: re-measure origins on a fresh workspace.
- **`CampaignConfig` field renames remain data migrations.** Both on-disk surfaces now persist the **delta from defaults** (a knob nobody set is absent, so renaming it is free); a knob the operator *did* set is still written down. `deploy-linux/update.sh` runs `promptpotter restamp --apply` on every deploy.
- **`CycleResult.n_rounds → n_l1_rounds`** (origin-exclusive count).
- **Elimination config** — PoBB `pobb_epsilon` + dominance/equivalence are replaced by one `margin_elimination` paired-gate knob. *(Never shipped under that name; `pobb_epsilon` is still the live knob, now beside `pobb_epsilon_floor`.)*
- **Paired backend.** 0.8.8 pairs with the TermNorm backend on the web-search `strategy` axis + the structured-output seam (`llm_only` returns `content=""` with a `content_empty:` advisory rather than substituting the reasoning trace). Paired with **TermNorm v1.2.0**.

### Technical Details

- **292 commits since `v0.8.2`** (2026-06-11 → 2026-07-19): 89 features, 82 fixes, 76 refactors, 23 docs, 5 chore, 2 perf, 1 build (+ merges).
- Sustained complexity-ledger reduction (`567`→`510`-range across the cycle): 34 re-export shims collapsed, four one-thing packages folded, a store `__init__` that manufactured import cycles removed, and repeated double-ownership sweeps (one owner per fact).
- `StrictModel` is `forbid`-by-default with `models_lax` counted; new CI gates ratchet `Any` params and guard NUL bytes; webapp commits gated on `tsc` + `eslint`.
- `APP_VERSION` + `pyproject.toml` → 0.8.8.
- See individual sections above for the full change set.

## [0.8.2] — 2026-06-11

> Continued 0.8.x beta-hardening toward the 0.9.0 broad launch. 24 commits since `v0.8.1`; no breaking changes. Deferred to later: BYO per-user keys, full state-sync, the Lane-C8 mask write-side, the live-gated `*_override → *_updates` rename, the cross-repo TermNorm `model` wire.

### Added

- **Scoring-divergence "mask" overlay (read-side).** A backend projection over stored measurements surfaces where an alternative scoring criterion would diverge — fold + verdict + serve + lineage-tree overlay, an abort verdict, What-If / fitness integration, and a row-derivable evaluators + "Lens" API. Read-side only; the write-side is deferred (Lane C8).
- **Campaign-from-origin.** Start a fresh campaign from a chosen prior origin: `origin_override` threads through the fresh-mint seam (`jobs/mint.py`), `OperatorForkOverride` collapsed into the one typed `CycleSeed`, and C0 lineage is data-driven from `origin_source`.
- **Machine-busy gate + capacity-1 run-admission seam** — atomic reserve, `409 machine_busy`, `/machine-status` banner.
- **New-Campaign origin reuse + a PoBB lock-in knob** in the webapp.
- **Sample-order trajectory ("Steps" view)** + per-candidate fixed-sample-set fitness recompute.
- **Two dev skills** — `potter-dev` (self-improving dev/investigation playbook) and `potter-debt-sweep` (daily five-lens code-hygiene sweep that ends in a PR; wired to a 05:30 CET cloud routine).

### Changed

- **Origin is a first-class round 0** — emitted through the standard `close_round` path like any round (label C0); the separate origin block is gone.
- **Optimizer LLM config is install-global, per-node** (`datasets/_optimizer/pipeline.json::nodes.*.config`), resolved through the normal overlay merge; the `OptimizerLLMConfig` / `campaign.json::optimizer_llm` knot is removed.
- **Webapp** — lineage made vertical + human-owned (view/data split); `LiveDashboardView` split; build modes split with pane code-splitting; emoji icon + card refresh.
- Model-selection knot gutted + dataset materialization unified; round/candidate payloads + resume-rewind errors typed.
- **List A polish** — state-sync, L3 deadline, login + value-prop copy.
- Docs — fixed `stable-api.md` Connector/SessionProtocol drift + stale CLAUDE.md line-cites + broken cross-refs; operator-facing `queries` → `samples` vocab.

### Fixed

- React #185 post-login render loop (de-referenced the unstable-dep cascade).

### Internal

- Code-hygiene sweep — removed dead code (`OptimizerAction` dup, `summarize_archive_runs`, `_refresh_identity`, `Sample` proxies, dead params) and hidden defaults (`sp_budget_ttest or 15`, `max_rounds or 999`, dead `or {}` guards); backlog reconciled.
- Test suites charter-named + import-time invariant guards.
- `APP_VERSION` + `pyproject.toml` → 0.8.2.

## [0.8.1] — 2026-06-06

> Beta-hardening release. `0.8.0` was an interim version bump with no changelog entry; this section covers everything since (29 commits: 8 feat, 14 refactor, 5 fix, docs).

### Highlights

- **Chat-first ingest, end to end.** Drop a file into chat → multi-format parse → dataset-bridge (name-collision UX + version-and-repoint Replace) → one-LLM-call origin check-in (provenance gate `unset|proposed|confirmed`, no hidden defaults, no literal-column requirement) → mint. CLI `new <file>` shares the same `ingest → commit` path.
- **Operator-steered fork (HITL).** Stop a run, pick a searchpoint, edit its full node config + prompt, reconcile spend/round limits, and fork-continue. Rides the existing `fork-cycle` verb (mint-then-launch); config-on-node consolidated the old ConfigMenu into `BackendNodeDetail`.
- **One error envelope, one mint seam.** Every API error serializes to the flat `{error, message, details?}` the OpenAPI spec declares (typed `PotterError` taxonomy; ~92 `raise HTTPException` removed; one `@app.exception_handler`). Fresh-mint logic collapsed to a single `application/jobs/mint.py` seam shared by CLI + web.
- **`ScoredCandidate` is the round-file shape.** A frozen Pydantic model whose `model_dump`/`model_validate` *are* the wire format (the hand-rolled `to_dict` is gone); `ci_lo`/`ci_hi` are computed fields, collapsing three Wilson-CI sites to one.

### Added

- Operator-steered fork: backend + read-side plumbing, webapp steer flow, live connection monitoring.
- Unified lineage cladogram — fork tree + intra-loop candidate tree in one expand/collapse view; a no-edit fork inherits the branch-point accuracy as C0 (skips a nondeterministic re-score).
- Connector `execution` mode declaration (`remote_http | in_process`) — the L4 self-recursion seam.
- Project-agnostic Linux deploy kit + one-command update (`deploy-linux/`).

### Changed

- Webapp reshaped to a claude.ai-style surface served at the domain root: RESTful API paths, de-underscored routers, 3-tier component layout (surfaces / chrome / dashboard regions), mobile polish, frontend-hardening alpha gate + auth-aware surface.
- Run-state is owned, typed live-state on `dashboard.json::run_phase`; quotas surface `429`.
- Clock + I/O writes routed through enforced seams; the typed-View persistence roundtrip collapsed (producer emits the view, Pydantic serializes it — nothing to reconstruct); backend + webapp de-duplicated.
- Docs: forward specs consolidated into one `roadmap.md` (per-milestone specs + the `archive/` dir removed — git log is the history); `code-debt-cleanup.md` trimmed to open items only.

### Fixed

- Security: CORS default closed, upload stream-cap, dependency CVE floors (serving path CVE-clean).
- Webapp: closed rounds route to the historical source (kills the in-flight 404 + degraded inspector); derived-origin drafts mint a canonical dataset instead of cloning per-slug.
- `llm_ranking` re-enabled now that the backend validates structured output.

### Internal

- `APP_VERSION` + `pyproject.toml` → 0.8.1.

## [0.7.0] — 2026-05-26

> Note: existing entries below predate M9. Headline M10 beta-hosting (OIDC + lifecycle + quotas + browser start surface), Stage-1 identity foundation, M12 control-plane (ADR-0001/0002/0003), webapp Next.js port, and the mypy-strict-default migration are not enumerated here — see the v0.7.0 GitHub release notes for the headline summary.

### Added — Routed Dispatch arc
- Typed `dispatch_hub.SIGNALS` (`dict[str, _Signal]` with `name`/`kind`/`render`/`doc`); load-time `validate_template` raises on unknown `{{slot}}` names. *(Now `INJECTIONS: dict[str, _Injection]`; the `doc` field is gone — five fields, per `dispatch/bundle.py`.)*
- New `axis_memory` signal — `cycle.axes.digest()` flows into L1, L2, L3 prompts.
- Cadence rules engine (`application/optimization/cadence/{rules,evaluator}.py`); `EscalationState.observe_round` delegates to `evaluate_round(SignalInputs)` over `DEFAULT_ROUND_RULES`. Opt-in `l2_axis_yield_drought` rule via `campaign.json::optimization.escalate_on_yield_drought`.
- `domain/decision_trace.py` — frozen Pydantic `DecisionTrace` (extra-forbid, JSON-roundtrip-stable). PoBB writes traces at promote/eliminate decision points → `RoundResult.decision_traces`; surfaced to `l1_critique` via the new `decision_trace_summary` signal.
- New `SignalsProjection` (`infrastructure/projections/signals.py`) appends `cadence/rule_fired` PhaseRecords to `.runtime/signals.jsonl`; `LiveDashboardProjection` mirrors firings into `dashboard.json::recent_rules` (rolling 8) + `current_signals` (latest per layer); webapp gains `SignalsPanel.tsx` (chronological readout) + `StuckDiagnosis.tsx` (per-layer verdict from latest `signal_inputs`). *(This whole surface was later retired — there is no `signals.jsonl`, `recent_rules` or `current_signals`. Escalation firings ride the ledger as `PhaseRecord(phase="escalation", event="rule_fired")`.)*

### Changed
- Replaced Wilcoxon+Holm sequential elimination with Bayesian Posterior-of-Being-Best (PoBB)
  population-aware stopping. New `OptimizationConfig.pobb_epsilon` (default 0.05) replaces
  `elimination_alpha`. PoBB uses joint Normal-CLT posterior over candidate accuracy means;
  per-query Monte Carlo argmax computes each candidate's `P(round-best)`; stop when below ε.
- Consolidated `ScoringSetConfig` + `HardSampleSorterConfig` into one `ExplorationConfig`.
- Trimmed redundant Rasch refit: `hard_sample_sorter` now reuses the round-end posterior
  cached on `Cycle.last_rasch_posterior` instead of refitting at finalize.
- Per-query P(best) snapshot stream: new `streams/round_NNNN_p_best.jsonl` (append-only),
  surfaced on `dashboard.json::current_round.candidates[].p_best` + `current_round.p_best_top`,
  in CLI/notebook live display, and as ASCII sparklines in `log.md` round digests.
- New `PoBBStreamProjection` (subscribes to the per-cycle ledger, writes JSONL).
- Modernized all type hints to PEP 604 (`X | None`, `list[str]`, `dict[K, V]`) across 12 files
- Replaced `print()` with `logger.warning()` in evaluators
- Fixed all 12 ruff lint errors (E501 line length, E402 import order)
- Added project metadata to `pyproject.toml` (license, authors, keywords, classifiers, URLs)
- Standardized `api/services/stores/` facade pattern in `ProjectStore`
- Refactored grid search and API router conventions

## Pre-0.7.0

0.6.0 and below described the pre-M9 architecture — `api/services/`, `ProjectStore`,
`PromptState`, `_campaign_lib.py` — none of which survives, and 0.7.0's note above already
sent readers past them. `git log v0.1.0..v0.7.0` has them; the GitHub release notes have the
headline summary.
