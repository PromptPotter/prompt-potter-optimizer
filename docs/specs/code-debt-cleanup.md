# Code-Debt Cleanup — Backlog

**Only work that is blocked or multi-arc** — including every shape a pass noticed off its task (root
`CLAUDE.md` `<situational-awareness>`). Anything one edit closes is fixed in the pass that found it; never the root of a patch just shipped (root `CLAUDE.md` `<root-fix>`). An item ships by
being deleted. Each entry: `file::symbol` — the defect — the action — **Rides with:** the work that
reaches it — **Re-test:** the command or question that says it still stands. Run the re-test before
acting.

## URGENT — shapes that hide the backbone from a reshape

**Rides with:** any pass in the file named.

- **The per-principal read rate limit 429s the e2e `cold` tier** (`first-run.spec.ts` account modal,
  `pools-checkin.spec.ts` draft): one browser's boot polls trip it, so the walk reads 429 on
  `/hierarchy`, `/auth/*`, `/datasets`. Action: the limit admits one tab's own polling cadence.
  **Re-test:** `npx playwright test --project=cold` logs no 429.
- **Left by the check-in address (unstarted campaigns)**:
  - `components/chat/README.md` still describes one global `ThreadProvider` (another session's file).
  - **A promoted Start is unserved**: `application/hierarchy.py::queued_drafts` serves a draft `queued` only while
    its job is QUEUED; once promoted to STARTING (probe, `init_services`) and until the mint closes the
    draft, a reload serves it open and a Start is refused `draft_held`. Action: serve the job's stage
    (`queued` | `starting`) off the same `CampaignLaunch.draft_id`.
  - `useIngestFlow::readDraft` is a hand-rolled fetch in an effect (not `useRead`), its failure said
    into the thread as a string, not a `ReadFailure` on `FailureLine`.
  - `application/hierarchy.py::entries_version` reads the jobs dir on every listing poll where a draft exists, and
    `JobRegistry.list_queued` is a reconciling read: a GET can release an orphaned job.
  - The check-in thread is retold from the draft on a reload (`useIngestFlow::retell`): a warning or
    failure line said live is not draft state and is not retold.
  **Re-test:** `git grep -n "ThreadProvider" -- webapp/components/chat/README.md`;
  `git grep -n "queued_drafts" -- promptpotter` serves a stage.

## NEXT ARC

**Rides with:** the next arc's first builder wave.

### The webapp's vanilla pass (audit 2026-10-29)

- **Failures dropped to booleans before `FailureLine` can render them**: `useConnector` folds its read
  failure into `schema.status: "error"`, `useTimeRay` serves `failed`, ChannelMap's tree `failed`/`loaded`,
  `OriginChanges` flattens to `message`. `useCommand` `describe` tables (ArchiveCompactionControl,
  AccountUsageTab, AccountProfileTab) and `AuthCore::AUTH_ERROR_COPY[code] ?? FALLBACK` sit beside
  `operatorMessage`. The protected run card and `chat.css` (`.hsp-note`, `.chat-tools-err`,
  `.chat-decision-err`) are still on the old form. **Re-test:** every hook carries a failure object.
- **Wording authored in the browser**: label tables still client-side (`NODE_KINDS`,
  `lib/prompt-fields.ts::PROMPT_FIELD`, `lib/derivations/sample-trajectory.ts::MOVEMENT_CLASS`,
  `WINDOW_LABEL`, `TAB_TITLES`, …) and the tone vocabularies (`BadgeTone`, `MessageTone`, the time ray's
  served tone, `l4-eff-*`, `verdictTone`, HardSamplesPreview's, PipelineFlow's). Canonical: served label
  constants, one tone. **Re-test:** each table served or deleted.
- **Size literals and the seed-once latch**: chart `height: 140`
  (`git grep -n "height: 140" -- webapp/components`) beside `--chart-h`, which is a `clamp()`, so the
  two disagree on purpose or by accident; the seed-once latch hand-built at each
  `git grep -n -i "seeded" -- webapp/components webapp/lib` site; `STORAGE_KEYS` mixes hook and
  non-hook keys and `side-panel:*` has no `promptpotter.` prefix. Waiting on the run-card session's
  unlanded `lib/run-card-prefs.ts`: it keeps a second `BOOL` and a literal key.
  **Re-test:** one latch, one `BOOL`.
- **Modules still off the `lib/CLAUDE.md` § Where a module lives rule**, blocked by the run-card
  session's files and the ingest rebuild, which import them: to `state/` — `hard-samples`, `lineage`,
  `poll`, `workspace`, `ingest-flow`, `scoring-mask`, `chat/thread`, `SelectionContext` (as
  `selection.tsx`), `hooks/useConnector`'s provider; to `derivations/` — `cell-means`,
  `prompt-fields`, `origin-readiness`; to `prefs/` — `run-card-prefs`, once that unlanded file lands.
  Chrome still holding a surface: `shell/searchpoint/`, `shell/scoring/` (ingest imports it),
  `shell/node-surface/`, root `PairedLift` / `SummaryBlock`; a surface holding chrome:
  `dashboard/pipeline/` (`git grep -n "dashboard/pipeline" -- webapp/components/ingest`). Hard samples and compare each spread over
  several folders (`git ls-files webapp/components | grep -i -E "hard-?sample|compare"`).
  **Re-test:** `git ls-files ':(glob)webapp/lib/*.tsx'` lists none.
- **Adapters over a flat served shape**: `useCandidatesModel.ts` joins every source it imports;
  `.facts.` opened at `git grep -c "\.facts\." -- webapp/components webapp/lib`; liveness
  re-derived at each `git grep -n "phaseWalks(" -- webapp/components` site.
- **Several doors into one state**: candidate selection minted at each
  `git grep -n "selectedCandidateOf(" -- webapp` site, the sample set written through
  `setSelectionForSampleSet` from several surfaces, `TrendChart.onOpen` exists so a caller can
  `openView`, `FileTree::onSelect` drilled through its parents.

### Typed state — what the 2026-10-30 arc left (served words → enums + one exported table)

- **`domain/bench.py::BENCH_STATE_INFO` is a second table over `ReadingState`** beside
  `READING_STATE_INFO`. Action: one table with a `fault` column. Merging re-keys optimizer prompts
  (a digest table), so it asks the operator first. **Re-test:** `git grep -c "BENCH_STATE_INFO" -- promptpotter`.
- **`DISPLAY_METRIC_INFO` is hand-emitted three ways** (`DISPLAY_METRICS`, `_LABELS`, `_UNITS` in
  `scripts/build_ts_types.py`). Action: one `_WORD_TABLES` row. **Re-test:** grep the three names in the script.
- **One wording kept in two languages**: `selection_line` (Python `markdown.py` / `activity.py`, TS
  `hierarchy.ts::selectionLine`), and likewise the rounds (`primitives.py::rounds_readout` /
  `roundsText`) and lives (`LivesReading.label` / `lives.ts`) phrasings. The words are one table; the
  sentence frame is still written twice. Action: none until a third surface; then the frame is a table row.
- **`lib/derivations/time-ray.ts::rayHead` stitches `dash.run_phase` with the course's
  `state.status`** (two sources for one run). Action: one served head. **Rides with:** one owner per fact across `/hierarchy` and `/dashboard`.
- **A candidate's `caveat` restates the θ caveat** `THETA_CAVEAT_INFO` already words. Action: serve the enum.
- **`RunErrorBanner` splits `err.message` on `\n`** — prose read as structure. **Rides with:** warnings narrowed to self-heals.
- **`domain/cycle_index.py::RunStatus.of` builds `mark: str` and passes it through `model_validate`**,
  so the `StatusMark` literal is checked at runtime only. Action: map phase/outcome → `StatusMark` typed.
- **`application/config_map.py::ConfigKnob.source` is an untyped `str`**, and its only reader is
  `RowHoverCard`'s declared-knobs list. Action: a `KnobSource` literal. **Re-test:** `git grep -n "source: str" -- promptpotter/application/config_map.py`.
- **The pipeline renderer's styles live in `app/styles/domains/chat.css`** (`.wf-hero-*`,
  `.connector*`, `.pipeline-zoom*`), restated by `dashboard-pipeline-nodes.css` to win on order. The
  renderer is `dashboard/pipeline/PipelineFlow`'s. Action: move the block to its own domain file in
  one commit once the run-card session's `chat.css` hunk lands. **Re-test:** `git grep -c "wf-hero" -- webapp/app/styles/domains/chat.css`.
- **`NodeKnobsPanel::POOL_NOUN` words a served closed set (`PoolRow.source`) locally.** Action: an
  exported word table. **Re-test:** `git grep -n "POOL_NOUN" -- webapp/components`.
- **`FreqChart` inks the low half of the bands one colour and the high half another**: colour that
  carries no meaning. Action: one ink. **Re-test:** read `FreqChart.tsx::colors`.
- **`search-point.ts::OBSERVE_LABELS` is a TS-only closed set** (`ObserveState`): a UI mode, not a
  served state — admitted until a surface beside the browser names it. **Re-test:** still TS-only.
- **`lib/format.ts::SIDE_TONE` / `verdictTone`** stay local: the file belongs to the run-card
  session. **Rides with:** that session landing.
- **A file's identity is tested twice, by two rules**: `store/read_model.py::_LedgerTail._holds_last`
  re-reads the last fed line, while `store/io.py::_swap` trusts `_FileMark.inode`. An inode
  outlives its file, so a delete and re-mint between `_read_marked` and the swap passes that test,
  and the rewrite keeps the new file's bytes past `mark.end` as a tail. Action: one identity check
  in `io.py`, read by both. **Re-test:** `git grep -n "st_ino" -- promptpotter/infrastructure/store`.
- **A landing waits on every live producer on another commit, whether or not the ledger shape
  moved** (`jobs/registry.py::JobRegistry.close_for_landing`), so a busy box may never land. Action:
  key it on a digest of the `CycleRecord` schema the code writes, not the commit. **Re-test:** a
  landing that moves no record shape refuses while a run is live.
- **`reset` still refuses when the spend bank's OWN record kinds reshape** (`TokenUsageRecord`,
  `SpendHoldRecord`, `SpendTombstoneRecord`): the bank cannot read the money it would bank, so it
  drops nothing, and the wipe is by hand. Action: decide whether such a reshape migrates the bank
  first or banks nothing on purpose. **Re-test:** a reset over a ledger with a reshaped `token_usage` line.
- **`reset` drops a campaign directory with no manifest unbanked, and guards no live producer**:
  `maintenance/reset.py::apply_reset` banks through `iter_campaign_dirs`, which skips such a
  directory, and never takes the guard `delete` takes (`_guard_and_release`). Action: bank by
  ledger, not manifest, and take the delete's guard. **Re-test:** a reset over a manifest-less
  campaign whose ledger is billed.
- **One act on an arm writes two records**: `runner/arm_gate.py` and `scoring/query_loop.py` append
  an `InterventionRecord` and a `LeftComparisonRecord` for one act. Action: one act record both
  reads derive from. **Re-test:** `git grep -n "LeftComparisonRecord(" -- promptpotter`.
- **A deleted fork takes its `LeftComparisonRecord` with it**, so its arm reads `controlled` again
  while the fork's spend stays banked. Action: the exit outlives the fork. **Re-test:** delete a
  fork off an arm, then read the arm's `evidence`.
- **A campaign's root is found two ways**: `ledger_scan.py::scan_campaign_ceiling` parses the
  directory name (`store/layout.py::root_cycle_id`) where its writer takes `Campaign.root_hop`. Action:
  the reader takes the root hop. **Re-test:** `git grep -n "root_cycle_id(" -- promptpotter`.
- **A fold that raises is logged and folded past** (`store/read_model.py::feed_fold`), so a read
  serves a value missing that line. Action: refuse as `ledger_record` does. **Re-test:**
  `git grep -n "logger.exception" -- promptpotter/infrastructure/store/read_model.py`.
- **An unparseable job file is logged and skipped** (`jobs/registry.py::JobRegistry.list_all`), so
  occupancy undercounts a live slot; a parseable one that fails validation raises
  `JobUnreadableError`. Action: one refusal for both. **Re-test:** `git grep -n "logger" -- promptpotter/application/jobs/registry.py`.

### The member backbone

- **The dev loop serializes where it need not:** sibling gates queue whole runs, `next-build` peaks
  near 4 GB, builders spend most tokens on whole-file reads, Vitest's worker start timeout fails a
  green suite on a loaded host. Action: attribute each, then cut what our code wastes. **Re-test:** five builders plus one full gate with no reap and no gate waiting
  longer than its own run.
- **`initialization/session.py::LaunchState.ledger` is `Optional` while a round needs it**:
  `run_observers.py::build_run_observers` sets it after the state is built, so `runner/round.py`
  asserts presence and five other readers branch on `None`. Action: the run's state is built with its
  ledger. **Re-test:** `git grep -n "state.ledger" -- promptpotter/application`.
- **`node_context.py::Lineage.rounds` copies every round on each access** (`model_copy` to seal
  `optimizer_state`), and a member reads it per step. Action: seal once at a round's close, or serve
  a sealed view. **Re-test:** read `Lineage.rounds`.
- **`run_records.py::RoundEnteredRecord.rewound` is a flag keeping two acts apart** — entering a round
  and displacing what stood past it — that `ledger_scan.py::_Standing.feed` splits again. Action:
  two records, or one whose displacement is its own field. **Rides with:** the `arc/records` arc.
  **Re-test:** `git grep -n "rewound" -- promptpotter/domain/run_records.py`.

## Open — multi-arc

- **One text in two packs is two library rows under one id** (`application/library.py`): the id is
  content-addressed, the row is per pack, so a drop keyed by id cannot say which pack's provenance it
  carries. Action: a snippet's identity is (pack, id), or packs dedupe on install. **Re-test:** two
  packs sharing a text serve distinct row keys.

- **The crowning bar tests nothing a result can quote.** `application/scoring/selection.py::elect_round_winner`
  admits with no interval and no multiplicity correction, and `RoundResult.improved` reads any selection
  past round 0 as a promotion; at panel width `exact_p_floor(6) = 0.031`, Holm across three arms 0.094, so
  no corrected exact verdict reaches α=0.05. Call any "this round improved" provisional. Action: re-read
  separability on a run that used the tenant ruler, then choose a bar — never tighten one before. **Rides
  with:** [`../../paper/README.md`](../../paper/README.md) § The sequence, stage 3. **Re-test:** a
  tenant-ruler run's `index.json::final.rounds_to_separable` beside its `rounds_to_improved`.
- **The free tier offers what it refuses.** `application/free_tier.py::with_free_tier_layer` narrows each
  model's params but never the model axis, so a free check-in offers models Start refuses. Action: the
  per-value lists name only values known to fail; the spend ceiling bounds the rest. An account
  holding a key of its own is still held to this menu (`quota.py::on_free_tier` reads no key), though
  its own key pays. **Rides with:** the next check-in pass. **Re-test:** a free account's model menu
  equals `free_tier.yaml::task_models`, and an own-key account is offered every model.
- **`application/optimizers/paper_templates.py::marked` refuses a `<prompt>` block with no closing tag**
  (`gpt-oss-20b`, ~1 in 6 LEVI replies), and the INVALID card names potter's `l1_generate`. Action: an
  unclosed block reads to the reply's end; the card names the node that proposed. **Rides with:** the
  h2h-3 re-run ([`roadmap.md`](roadmap.md) § Optimizer tuning — the standing board). **Re-test:**
  `marked("<prompt>x")` is `None`.
- **NEXT — the default host pin is a trial with no verdict.** `domain/pipeline_overlay.py::pin_default_routes`
  writes each model's default host (`infrastructure/llm/registry.py::_MODEL_PROFILES`) into every
  node config; `route_order: []` opts out. **Decided:** a model keeps its pin only where its realized
  cache share and effective $/M beat running uncached. **Rides with:** the next supervised campaign on
  a pinned model, beside its `route_order: []` twin. **Re-test:** `SpendRollup.by_node` in each twin's `review.md`.
- **The responsive walk takes no Lighthouse score and never sizes the candidates card.** Action: one
  Lighthouse run at 375 px, recorded here; the card swept once a spec has such a campaign. **Rides
  with:** any layout change with a browser open. **Re-test:** `grep -n candidate webapp/e2e/walk/responsive.spec.ts`.
- **Compare cards are laid out for few wide columns**, not five thin stripes on a 16:9 display
  (`components/compare/ChannelCard.tsx`). **Rides with:** the next compare-pane pass, through `/design`.
  **Re-test:** five candidates at 1920×1080 without horizontal scroll.
- **Optimizer-model repair rate on L2/L3 structured output is unmeasured.** **Rides with:** the next
  supervised campaign that escalates. **Re-test:** the share of its L2/L3 calls that needed a parse repair.
- **The dashboard carries Next.js only as a build tool** (`output: "export"`; FastAPI serves
  `webapp/out`), and `eslint-config-next` pulls `braces` (GHSA-vfj7-8cjw-p6xm), failing `gate.py --only npm-audit`.
  Action: build with Vite, keep the `out` directory; settle first whether the rebuilt ESLint set
  pulls `braces` back. **Rides with:** the next webapp dependency pass. **Re-test:**
  `git grep -n '"next"' webapp/package.json`; `npm ls braces` in `webapp/`.
- **`tests/test_scoring.py::_peer_cycle` stands a `Cycle` up by hand**, banking a C0 report
  `application/origin.py::establish_campaign_origin` cannot produce. Action: a backend whose answers
  produce the asserted origin, started through run init. **Rides with:** any change to how run init
  scores the origin. **Re-test:** `grep -n "_peer_cycle" tests/test_scoring.py`.
- **The never / partly / always spread contradicts the headline beside it.**
  `application/scoring/measurement_log.py::_cycle_cells` pools every candidate the cycle ran, so
  `domain/cells.py::hit_spread` reads a sample only C0 saw as "never". One decision: the spread
  describes the subject (the selected searchpoint's own cells), or stays a search-coverage statistic
  labelled as such, with samples only weak candidates measured counted as *unchallenged*. **Rides
  with:** any change to `hit_spread` or the run card. **Re-test:** ask the operator which.
- **A scoring cell beats as an LLM call.** `application/scoring/sample_measurement.py::_send_cell`
  and `search_point_scorer.py::_claim_cell` append `LLMCallProgressRecord`s named
  `node="backend_scoring"` (no view has it) with a `call_id` of `scoring:{position}`, so the
  dashboard fold cannot key a beat's `inner` hop to its open cell: with look-ahead above 1,
  `CurrentRound.inner` names the latest-beating cell. Action: a cell beat carries its `position`
  and no node. **Rides with:** the next change to the heartbeat record. **Re-test:**
  `git grep -n '"backend_scoring"' promptpotter`.
- **An optimizer's measurement node is derived twice**: `optimizer_manifest.py::RoundPlan.measurement`
  (the gateway `default` walks) and `pipeline_resolve.py::measurement_node(view)` (the first
  `measurement` node of the view), which serves `OptimizerPipelineResponse.measurement_node` as
  nullable though no admitted manifest lacks one. **Rides with:** the next change to that response.
  **Re-test:** `git grep -n "measurement_node(schema.view)" promptpotter`.
- **`domain/export.py` carries no optimization spend or tokens**, so a reader cannot compute amortized lifetime cost ([`../research/external-constraints.md`](../research/external-constraints.md) § Cost reporting — binds every run from now, not only the preprint). **Rides with:** the next export shape change. **Re-test:** `grep -n "spend\|token" promptpotter/domain/export.py`.

## Open — optimizer tuning

**Rides with:** "The optimizer climbs" in [`roadmap.md`](roadmap.md) § Sequence.

- **A held leader's evidence is discarded**: an arm that leads but misses the bar is never re-scored
  (`optimizers/potter/members.py::AdaptiveQueue.draw`). Action: carry it into the next round's draw.
  **Re-test:** the round after a leader missed the bar re-scores that arm.
- **`l1_generate` edits `answer_format`.** Action: the parsed directive stays locked, the rest free; the
  operator sees the diff first. **Re-test:** a round file whose `l1_generate` arm's `answer_format`
  differs from its parent's.
- **A declared bench is not the held-out count**: `CampaignConfig.dataset_split` `{bench: 100}` held out
  162 rows. Action: hold out the declared count. **Re-test:** a fresh `{bench: 100}` campaign holds out 100.
- **A paid pass does not stop on its command**: a stopped bench or verify pass acks `rejected`; a pass with
  no hop stops only on Ctrl+C; a standing pause stops a later bench pass with no result
  (`application/runner/grade_bench.py::grade_line_bench`). Action: every pass takes pause and stop at its
  cell boundary and banks what it graded. **Re-test:** pause a bench pass from a second terminal; it acks
  `accepted` and banks a result.
- **One refusal, one entry point**: `seed-screen` skips `application/preflight.py::refuse_below_reasoning_floor`,
  which `new` raises through `jobs/mint.py`; `presentation/cli/commands/new.py` parses `--set` with
  `yaml.safe_load`, so `off` reads `False`. Action: the refusal sits where every entry point passes; `--set`
  keeps `off` a string. **Re-test:** `seed-screen` below a reasoning floor refuses; `--set x=off` lands `"off"`.
- **A lost mirror loses its money**: `infrastructure/llm/spend_book.py::SpendBook.mirror` logs a failed
  append, yet the nested record already says `mirrored=True`, so no bill or bank counts that call. Action: a
  failed carry raises, or the nested record is written only after the carry lands. **Re-test:** a root
  append that raises leaves the call counted on the account.
- **A check-in can write framing no node takes**: decomposition fills `upstream_context` /
  `downstream_context` for any pipeline, and `domain/opt_search_point.py::scored_prompts` refuses at mint
  where no prompt node declares `problem_description` (`promptpotter-self` hit it; its `task.md` dropped the
  two). Action: decomposition writes the splice fields only where the schema has a splice target.
  **Re-test:** a check-in on a params-only backend commits no splice field and mints.
- **A fork can name one change only**: `ForkProposal.unlock_schema_field_rename` is the sole config a
  rebase applies, so once it is unlocked a layer's fork is admitted only as a backtrack the engine
  picks. L3's measured asks (stand on an arm read but never adopted, e.g. C3.3; narrow a dead param
  axis) have no channel: standing on a non-elected arm needs the fork's first round to elect it.
  Action: decide which fork-only changes exist and name each as a `ForkProposal` member mapped to one
  `ConfigOverrides`/seed edit. **Re-test:** `git grep -n "class ForkProposal" -A20 -- promptpotter`.
- **The L4 estimator digest does not see the line series**: `CycleResult.bench_round_levels` now spans
  the line (`runner/bench.py::line_bench_series`), which `domain/l4/proxies.py` reads unchanged, so a
  rebased cell banked under the per-cycle series and one under the line series share an estimator
  digest yet differ in what their mean averages. Action: move the line's series into the hashed
  estimator, or re-key on the wipe. **Re-test:** `python scripts/offline_run.py --digests` before and
  after a line-series edit.
- **`AxisIndex.digest` computes what no panel renders**: `panels.py::_r_axis_memory` prints only
  `_AXIS_MEMORY_LABEL_ORDER`, yet `digest` also builds `dead_queries`, `discriminating`, `bottleneck`,
  `failure_group_insights`, `volatile` and `improvement_attribution` each round. Action: delete each key
  no reader takes, or render it. **Re-test:** `git grep -n "digest.get\|\.digest(" -- promptpotter`.
- **A critique cluster on unmarked rows is dropped into a log** (`l1/critique.py::spanning_clusters`), never
  healed. Action: reject at the parse boundary with the marked roster in reach, so the repair rung heals it.
  **Re-test:** a critique naming an unmarked row is re-asked, not thinned.
- **The fastest hosts refuse the `l1_generate` schema**: Cerebras and Groq (`openai/gpt-oss-*`) answer 400 to
  `L1GenerateOutput`'s inlined JSON schema — Cerebras wants `properties` on every object, Groq strict mode wants
  every property `required` — so the cheapest fast silicon cannot run an optimizer node. This was found by
  probing `L1GenerateOutput.model_json_schema()`, not yet the wire schema
  (`dispatch/l1_wire_schema.py::build_l1_response_schema`). Action: emit the wire schema in the strict-portable
  subset (closed objects, all-required with nullable optionals). **Re-test:** one `l1_generate` call pinned to
  Cerebras parses.

## Open — from the head-to-head arc

A leading **INVESTIGATE** means nobody has shown the item is real; answer that first.

- **`dashboard.json::run_limits` goes stale on `change-run-limits`.** It is copied from the cycle's
  last `run_wiring` record, while a mid-run `set-limits` writes `standing_budget` + an ack. Seen on
  h2h-7 `capo-b5` (`__530ab2`): `set-limits --max-rounds 10` armed 10 (ack `effect.armed`), the
  cycle stopped `rounds_budget` at 10 clean rounds, and the dashboard still served `rounds: 50` —
  while `cycles` and `evidence` read 10. One served fact, two sources. **Re-test:** after
  `set-limits --max-rounds N` on a live cycle, `dashboard.json::run_limits.budget.rounds == N`.
- **The head-to-head guard reads identical budgets as `differs`.** `head_to_heads/<id>.json` keeps
  `shared.budget: {}` when each arm's ceiling came by `--max-usd` (a standing budget, not the
  declaration); `evidence/head_to_head.py::pair_guard`'s `off_record` then compares every arm's
  `next_launch_budget` against `over_the_default({})`. h2h-7's three arms print the same
  `usd=0.2, rounds=50` under "budget DIFFERS", so no per-arm-ceiling head-to-head reads
  `controlled`. One budget, two homes. Sent to `arc/n-arm-mint-v2` (#58), which owns the record.
  **Re-test:** `evidence` on h2h-7 reads `controlled`.
- **A head-to-head's arms may diverge on budget mid-run, and only `evidence` notices.** `set-limits`
  on one arm moves it alone (by design), and `evidence` then marks every pair `x` (`Differs on:
  budget`) — but nothing at the `set-limits` door says the arm is leaving the head-to-head's
  declared budget. Open: refuse it on an arm, or serve the divergence where it is made.
- **A reserved cell's bill is on no ledger** (`Connector.holds_own_sends` writes no hold), so
  `infrastructure/llm/spend_book.py::unreported_on` can re-teach a book its sends but never the cell.
  Action: a hold record per reserved cell. **Rides with:** any change to `spend_book.py::reserved`.
  **Re-test:** `grep -n "emit_spend_hold" promptpotter/infrastructure/llm/spend_book.py`.
- **The bench pass runs at look-ahead depth 1**, because depth is a press a round spends and
  `runner/bench.py` runs outside any round. **Decided:** the pass takes a depth of its own. **Rides
  with:** any change to `runner/bench.py`'s walk. **Re-test:** time one bench pass against its cell count.
- **A resume from a pause at the origin boundary runs round 0 again** (a second round-0 election,
  a second `l1_critique`). Open: whether the re-send should read the banked `optimizer_state`.
  **Rides with:** `DashboardCheckout.over` or the origin round in `runner/run_prep.py`. **Re-test:**
  `scripts/offline_run.py --rounds 1 --rows 60` — `decisions MOVED` on the resume line.
- **INVESTIGATE — does an errored cell bias a head-to-head?** `gpt-oss-20b:nitro` can end
  `finish_reason=error` then pass one schema repair; unknown whether the row banks the answer or a
  miss. **Rides with:** the next head-to-head read. **Re-test:** per arm, the hit rate of error rows
  against the arm's other rows.
- **INVESTIGATE — CAPO's survivors are ranked on another reading than the reference ranks them on.**
  `population` keeps μ best-first by mean objective on the cells all survivors measured; promptolution's
  CAPO sorts survivors on raw score over every cell each has been evaluated on, cached across rounds,
  so an incumbent's deeper blocks from earlier races count. After a one-block race (the paper's rule,
  `candidate-elimination.md` § CAPO's race) the two can crown differently. Open: which reading the
  bench compares. **Rides with:** any change to `capo/members.py::_rank_survivors`. **Re-test:** on a CAPO
  run with offspring < μ, rank one round's survivors both ways.
- **"Calls held equal" has no axis to hold.** `domain/spend.py::Budget` meters usd, tokens, rounds and
  wall clock; the roadmap and `external-constraints.md` § 4 hold calls equal, `paper/README.md` holds
  spent tokens, and `paper/plans/` holds USD under the arm's `search_incurred` meter, so two arms of one
  point make the same calls only at one cell price and one optimizer cost. Action: one matched axis,
  decided; if calls, a cells axis on `Budget` the search meter binds. **Rides with:** any change to
  `Budget`'s axes. **Re-test:** `price-head-to-head paper/plans/comparability.yaml` — do one point's
  `search_cells` differ by arm?
- **A pinned `determinism.seed` makes every replicate one draw.** `PINNED_LOOP_KEYS` pins
  `optimization.determinism` across a head-to-head, and `bench/node_context.py::_rng` seeds every
  member off the pin instead of the campaign id, so N replicates of an arm on a pinning dataset make
  the same draws and read as a tight interval. Action: a replicate's identity enters the seed under a
  pin. **Rides with:** any change to `_rng` or `PINNED_LOOP_KEYS`. **Re-test:** two replicates under a
  pinned seed send the same first `l1_generate` request.
- **The $0 price opens a backend session** (`application/head_to_head_price.py::price_head_to_head`
  through `jobs/launcher/head_to_head.py::dataset_arms`) only to resolve the node models and the
  partition, so a stopped backend refuses a read that makes no call. **Rides with:** any change to
  `dataset_arms`. **Re-test:** price a plan with the backend down.
- **An optimizer's round price is its name's, not its treatment's**
  (`application/head_to_head_price.py::optimizer_round_price`): the three `escalation_ladder` arms read one potter
  price, though an `l1` round sends fewer optimizer calls than a `full` one. Action: key it on the
  treatment once enough campaigns of each have run. **Re-test:** the ablation's optimizer line names
  one price for three ladders.
- **INVESTIGATE — a CAPO arm is admitted on its round CEILING** (`preflight.py::check_cap_funds_round`
  over `round_cells_ceiling`: 4,230 cells on 310 search rows), which its race rarely spends, so a
  budget point must fund the worst case to mint CAPO at all. **Re-test:** a finished CAPO round's
  cells against its ceiling.
- **`notebooks/bbeh_potter.ipynb` states another protocol than `shared_config.py`**: its Methodology
  cell and Cell 2 say the test set is the ~4,060 non-mini rows and the default target mistral-small;
  `load_and_split` tests on the second 10 mini rows per task. Action: the notebook states what
  `shared_config.py` runs. **Re-test:** `grep -n "non-mini" notebooks/bbeh_potter.ipynb`.
- **An unread backend schema serves "lands nowhere"**: with `schema_source: unreachable` no prompt
  node declares a part, so `application/pipeline_resolve.py::_pool_fills` serves `PoolRow.fills` empty, the state
  its description reserves for "nothing in play", and the check-in draws the pool nowhere. Also
  `application/datasets/ingest.py::fetch_backend_nodes` returns `{}` for an `in_process` connector, never its
  `pipeline_declaration`. Action: an unknown fill is served unknown. **Re-test:** a draft on an
  unreachable backend serves its fills unknown, not empty.
- **A selection is still made in three places outside a launch.** `config_map`, `draft_patch` and
  `checkin` each call `select_optimizer` themselves. The launch path now resolves one selection and
  hands it down.
- **`CommandAckRecord.effect` is typed, but nothing reads it.** A writer with no reader is not a state:
  serve it or delete it.
- **`embedded_run.run_campaign` steers one branch with two flags:** a `None` `declaration` plus
  `assert resumed is not None`.
- **A moved capability or backend revision alone passes a resume.** It is named as a drift cause only
  when the origin hash also mismatches.
- **`round_cells` has two meanings for one draw:** potter's unclamped value, read by L4, verify and the
  overlap, and the backbone's ceiling panel, clamped to the search split. Also, `RoundPlan.walk` and
  `round_walks` each decide which walk counts as a round of its own.
- **`gate.py --only` queues behind other worktrees' gates** (`queue_behind_siblings`), so one
  regeneration waits minutes. `scripts/build_ts_types.py` cannot run under `-m`, because it imports
  `processes` from `scripts/`. A bare `mypy` run shows errors in `evidence/subjects.py` and
  `tests/test_scoring.py` that the gate's mypy passes.
- **The library's duplicate-text check is written twice**: `application/library.py::bank_snippet` (a
  no-op) and `infrastructure/store/account_packs.py::bank_into_account_pack` (under the lock).
  `SnippetLibrary`'s empty copy still says "installed".
- **A cross's arity is declared nowhere**: `crossable` unpacks exactly two parents. `donated_loci` is
  derived by verbatim equality with the donor, not recorded on the lineage, so an LLM edit that lands on
  the donor's value reads as donated. The capo offline bench turns on one seed: lift +0.700 or none
  after a draw-order change, so the fixture cannot tell a mating-law change from a reshuffle.
- **`config/prompt_blocks.py` is hashed whole into every optimizer's `treatment.source`**
  (`shapes_optimizer_prompt(__name__)`): the per-pack refusal, a shelf read with no prompt text,
  re-keyed every treatment. Action: mark only the units a prompt reads, as `optimizer_manifest.py`
  does. **Re-test:** edit `PackShelf.refusals`; potter's treatment key stays.
- **Two packs holding one snippet text raise a bare `ValueError`** (`config/prompt_blocks.py::PackShelf._held`)
  where every other shelf failure is a `PackRefusal` or a typed refusal of the knob: a `packs` knob
  naming both reaches the operator as an error, not a sentence. Action: `BlockPoolInitializer.refusal`
  names the repeated entry. **Re-test:** two account packs with one text, both named; the draft serves.
- **A detached arm's job is the spawner's until its process adopts it**
  (`jobs/launcher/run_job.py::spawn_job`, `jobs/registry.py::_reap_if_orphaned`): a `head-to-head`
  terminal killed between the spawn and the adoption leaves jobs a reconciling read releases, so the
  child's `adopt` refuses and the arm's minted cycle never runs. Action: the child holds the producer
  lock before the spawner returns. **Re-test:** kill the terminal right after its `arm …` lines; each
  arm's cycle runs or reads released with its reason.
- **Two acts on one fresh head-to-head id race its record**: `jobs/mint.py::arm_plans` reads the
  standing record outside any lock, so two concurrent acts each declare their own `SharedPart`.
  Action: declare the record under the workspace lock and plan the second off it. **Re-test:** two
  `mint-head-to-head` on one fresh id at once — one is refused off the record.
- **A refused launch still spends its rate tokens** (`jobs/quota.py::check_launch_quotas`): the
  bucket is debited before the slot checks that can still refuse, so a head-to-head refused
  `machine_busy` costs all its arms' tokens. Action: debit on admission. **Re-test:** refuse N
  launches on a full box; the next is not `rate_limited`.
- **Two launch doors**: `jobs/launcher/launch.py::launch` (one run, may queue) and
  `jobs/launcher/head_to_head.py::admit_head_to_head` (N arms, never queued) each request slots and
  admit. Action: one door admitting N, queueing only when N is 1. **Re-test:** `git grep -c
  "check_launch_quotas(" -- promptpotter/application/jobs/launcher/*.py` is 1.
- **The origin's `changes_description` embeds the checkout's absolute path** (`Origin from
  /…/origin.yaml`): a served individual names the host's filesystem, and two checkouts of one commit
  read `decisions MOVED` in `scripts/offline_run.py --against`. Action: name the dataset-relative
  path. **Re-test:** `grep -c '"Origin from /' <home>/<optimizer>/decisions.json` is 0.
- **The controlled offline leg's arm-memory count moves between identical runs** (`arm potter: memory
  holds 380` then `375` own answers on one tree): a cell coalesced with a twin in flight is answered
  from memory, not sent. A printed reading, not a decision. Action: count filed answers, not distinct
  ones. **Re-test:** two `--controlled` runs of one tree print one count.

## Open — renames the operator picked (2026-10-29, low priority)

**Rides with:** a pass touching the name, or the post-2026-10-30 wipe for on-disk keys. Names are root
`CLAUDE.md`'s rule; each rename lands with its doc and `CLAUDE.md` row in one commit. **Re-test:** the old
word's `git grep -c` for that sense is 0.

- **`candidate_library` → `answer_terms`** (the target's answer-term list, beside
  `DraftCampaign.answer_space`), and `PoolSource.LIBRARY` → `PACKS`: the route
  `/datasets/draft/candidate-library`, `EditDraftPatch` and the dataset file move, and
  `shared/hashing.py::content_hash`'s key re-keys root ids.
- **"course" → "cycle" in the served and doc layer** (`HierarchyCourse`, `level: "course"`,
  `/hierarchy/{course}`, `ids.ts::courseAddressOf`); `CycleHop` stays an element of a `CyclePath`.
- **"line" in its live-cycle sense → `live_cycle`** (`command_kinds.py::LinePayload`,
  `BenchLine.on_line`, `domain/bench.py::LineRun`); "selection line" is never bare "line". A command
  payload kind, so ledger shape.
- **`nodes.py::Panel` / served `ArmPanel` → `RoundSamples` / `ArmSamples`**, the L4 roster → `inner_panel`;
  "panel" stays the evidence section an LLM reads.
- **"slot"**: prompt slot → prompt field, layout slot → region, `domain/arms.py::ArmSlot` →
  `ArmPosition` (ledger field), bank `slot_of` → `position_of`. The layout key is optimizer-prompt text.
- **`CycleSeed*` / `ForkSeed` → `CycleStart*` / `ForkStart`**, so "seed" is the RNG seed alone. A
  ledger record.
- **"member" for an individual → "individual"** (`run_standing.py`, `origin_panel.py`); "member" is the
  optimizer node; "candidate", an individual at a round address, is owed to the Vocabulary row.
- **The served hierarchy discriminator `level` → `entry_kind`**
  (`git grep -c -w level -- webapp/components webapp/lib`).
- **The READING node → reader node** in `resolved_schemas.json`'s layout prose: prompt text, so a
  re-measure.
- **draft vs check-in, by rule, no rename**: "check-in" is the resolver-and-gates process, "draft" the
  object (`DraftCampaign`); owed to root `CLAUDE.md`'s Vocabulary row.

## Open — from the structure pass

**Rides with:** any change to the file named. **Re-test:** the `file::symbol` still holding it.

- **Seen in the `lib/` regroup**: `lib/state/registry.tsx` and `lib/workspace.tsx` import `derivations/hierarchy` past the barrel
  to dodge a cycle (barrel → search-point → poll → workspace); `shell/Modal.tsx` beside the `ui/` modal
  rule; `lib/theme.ts` mixes chart setup with the theme preference; `RemotePanel` keeps a "not available
  now" fallback beside the served `lookahead.auto_refusal`.

- **Two "waiting" predicates** — `useRemoteModel.ts::waitNote` and `lib/derivations/time-ray.ts`.
  **Decided:** a call is open AND its wait target is known; serve `dash.wait_line` once.
- **Four seams owed a re-read:** `webapp/lib/poll.tsx::CycleStreamState` fields nothing reads;
  `webapp/lib/hooks/useCompareWithOrigin.ts` picks the best pointer; `webapp/lib/derivations/spend.ts::readSpend`
  sums spend; `presentation/cli/commands/resume_command.py::_steer_overlay` builds its own overlay.
- **`domain/phases.py` is two fifths stop-reason table**; a split was measured and declined, a
  rename re-points ~46 files. **Rides with:** a pass that moves the table.
- **A cold server folds every ledger before the first page answers**: the first
  `GET /hierarchy?depth=1` waits on a full `LEDGER_FOLDS` pass over every `projects/` ledger, and
  the browser shows a sidebar stuck on "loading…". On 2026-10-30, with 91 MB / 129k lines in
  `projects/`, it took 33 s cold beside the boot sweep and 1.1 s warm (`.scratch/cold_real.py`).
  `SpendRollup`'s private-attr factories and per-record `setdefault(k, Model())` cost a further
  70–90 s before they were cut. Two shapes remain:
  - The `run_state_stamps` ETag is the first opener. To learn the launch claim, its "stats only"
    stamp folds the whole roster.
  - `jobs/reaper.py::periodic_sweep` runs at boot, beside the first page. Over 58 ledgers it folds
    23 s, about 10 s of it on L4 inner-sandbox ledgers that no page reads.

  The fold itself is the root: pydantic validates each screened line on every cold start. A fold
  snapshot keyed by `file_sig` would make a restart pay only the tail.
- **The collapse gate cuts a hedging arm on noise**: `pobb/checks.py::check` →
  `domain/scoring.py::is_answer_collapsed` reads one repeated label over `n_min` answered cells as
  "no measurement". The panel walk runs hardest-first, so its first cells can hold one or two truth
  labels. A task model that answers `Uncertain` on about 75% of cells then trips the gate by chance in
  about 18% of arms (0.75⁶).
  - Seen 2026-10-30 in L4 inner `justlogic.d234__731eda`: C1.2 was cut at 0/6, all `Uncertain` and
    every one parsed. The round's own n_min top-up read it at 0.393 against C0's 0.5 over the 28-cell
    origin panel.
  - The bar and the sidebar lead with the 6-cell 0%; the 39% shows only as the overlap series.
  - Collapse must be judged against the truth labels the cells hold, and against the parent's answers on
    the same cells. An engine decision: prove it with an offline run.
- **The Candidates card shows empty axes while its lineage read is pending**: `CandidatesCard` gates on
  `dash` only. On a cold first page `viewedNode` is undefined, so the chart draws no bars and no
  `ReadState`, and reads as broken. Gate on the lineage read's own state.
- **The server's shutdown notice counts runs it does not host**: `main.py::_telegram_shutdown_notice`
  reads `JobRegistry.list_running()`, which holds the machine's CLI runs too. A server stopped beside a
  CLI campaign reports it "stopped … in flight" and says "Resume from the campaign once it is back up",
  but the run continues. The notice must name only the jobs this process owns. Seen 2026-10-30 against
  `promptpotter-self.panel__606cc9`; the same stop logged `ASGI callable returned without completing
  response` for its open `events:subscribe` stream.
- **One node's knob gates four nodes' pool access**: `potter/dispatch/facade.py` declares `BlockPool` and
  `DemoPool` draws on `L1GenerateKnobs` for every hub member, so the check-in shows `l1_critique`,
  `l2_context` and `l3_plan` "open · l1_generate.block_access". Either each reader owns its access or
  the pool is read once by the hub; the served row must say which.
- **The check-in's optimizer graph ignores the draft's overlay**: `resolve_pipeline_for_optimizer`
  resolves the manifest with `{}`.
- **Rename candidates, not yet picked** (one word, two senses each):
  `evidence/head_to_head.py::HeadToHead.swapped` / `swapped_in` beside the manifest `swap`;
  `application/library.py::library` beside `candidate_library` (rides the `candidate_library` →
  `answer_terms` rename); `domain/pool.py::PoolOffer` / `offered` beside
  `datasets/startability.py::offered`.
- **`Scope` says nothing on members**: every member knob is `Scope.POLICY`, and `KnobRow` serves neither
  scope nor estimand.
- **Seen building the setup screen**:
  - One app-wide `SelectionContext.node` serves the target and the optimizer graphs, and
    `PipelineSetupInner`'s auto-select races the optimizer graph for it: a global standing in for a keyed
    subject.
  - `OptimizerSetupInner` reads the optimizer from the draft and from `useConnector().optimizer`, which
    are one value through two doors.
  - An edited optimizer-prompt field cannot be reset: `OptimizerNodePrompt` serves no template text and
    `campaign_config._merge_dicts` keeps a null as a value.
  - `LibrarySnippet` serves no `presumes`, so the library cannot show that a snippet the chain refuses
    never enters a pool.
  - Smaller: `title=` used as teaching prose in `NodeDetail` and `OptimizerCard`;
    `CheckinLoadingWindow` keeps its own `setInterval` outside `lib/cadence`; `.setup-step` is styled in
    `chat.css`.
- **Seen in the phone-first setup pass**:
  - A changed knob value names no source: the resolver keeps none for `optimization.nodes` layers
    (dataset block, draft override, cycle seed), so the row says only "Changed from default".
  - `locus_crossover` is listed as a maker with nothing upstream making two-parent children; an
    arm-editing maker's on/off belongs on `nodes.Proposer` as a member-declared rule.
  - The "adds no arms ⇒ off" rule sits in `pipeline_resolve.py`, not on `SelectedOptimizer`.
  - `HoverCard` opens on tap only through focus (fragile on iOS); the check-in picklist button's
    accessible name runs slug and meta together; two open check-ins share one slug.
- **Seen drawing the pools in place**:
  - A named pack that leaves the shelf makes `BlockPoolInitializer.held` raise `block_pack_not_found`,
    so the draft read itself fails instead of offering the pack to unname.
- **Seen splitting the manifest hash**: the scan now hashes every top-level definition the marked units
  reach (`source_scan.py::optimizer_prompt_shapers`), so most marks are redundant roots kept from when
  each reached name had to be marked by hand; only a unit read through an instance or a registry (as
  `SelectedOptimizer`) needs one. The `application/` modules that still carry
  `shapes_optimizer_prompt(__name__)` hash whole where the reached units would do. **Re-test:**
  `git grep -n "shapes_optimizer_prompt(__name__)" -- promptpotter/application`.
- **Seen building the prompt workspace**:
  - An override cannot be removed, only overwritten: knob Reset writes the declared value and a cleared
    prompt field writes `""`, so the draft stays "edited". The overlay merge needs a delete.
  - `PipelineSetupSection` picks a default node in a `useEffect` with a ref latch: a second door into the
    shared node selection.
  - The packs copy each snippet's arXiv id and method into its `tags`, so Tags repeats Source and Method.
  - `LibrarySource` fakes "authored" as a cited work with no author or year.
  - `ui/MenuCheck` takes a `title=` prop; filter buttons are ~28px, under the 44px touch target.
- **Seen framing snippets in a prompt**:
  - `.prompt-snippet-input` re-declares the field skin's border and background inside the frame.
  - `config/prompt_blocks.py` says "render order" for `PROMPT_FIELDS` order (a hashed module).
  - `vitest.config.ts` loads as CommonJS (Vite warns on every run).
- **A worktree's `webapp/node_modules` junction makes a forced recursive delete reach the main
  tree's**, so a worktree is removed by unlinking its junctions first. **Re-test:**
  `git worktree remove` on a worktree with its junctions in place leaves main's `node_modules`.
- **A side worktree cannot pass the full gate**: Turbopack refuses the `node_modules` junction
  ("points out of the filesystem root"), so `next-build` and `playwright` never run there, and pytest's
  `compileall` breaks on long Temp paths. A rebuilt commit series is therefore gated without them.
  **Re-test:** the full gate green in a worktree.
- **`p_served`'s ceiling is slack** (175 measured under 176 since 3dfce02f0); the gate allows slack, so
  the ratchet never tightened. **Re-test:** BASELINE equals the measured count.
- **The installed webapp packages drift from `package.json`**: `npm ls` reports `next@16.3.8` against
  `16.4.1` and `vitest@4.1.11` against `^4.1.12`, so the gate builds with versions the lock does not
  name. **Re-test:** `npm ls --depth=0` clean.
- **`scripts/offline_run.py` imports the gate for `RamBudget`**: the script the gate runs loads
  `gate.py` (and through it `tests_owning`, `yaml`) in every child, while `OFFLINE_RUN_READS` names
  neither file. Fan-out sizing sits beside `processes.py::free_mb`. **Re-test:**
  `git grep -n "from gate import" -- scripts`.
- **A killed push leaves live links into the main tree.** `gate.py::_tip_checkout` links `.venv` and
  `webapp/node_modules` into `%TEMP%\pp-push-*`. A hard kill skips the unlink, so the temp dir keeps
  junctions that a later recursive temp delete could follow. Action: the next `--push` sweeps stale
  `pp-push-*` (unlink, then `git worktree prune`). **Re-test:** kill a push mid-run, then run
  `--push` again and list `%TEMP%\pp-push-*`.
- **`scripts/offline_run.py --controlled` crashes on main** (fd51d06 and after): the potter arm's
  `_variant_edit` asserts `no framing to cite`, since the controlled leg's prompt holds no
  `task_context`, so its arm-exit check (`run_controlled`: a fork takes its arm out) never runs.
  Action: the leg declares the framing its fake proposer cites. **Re-test:**
  `PROMPTPOTTER_HOME=<dir> python scripts/offline_run.py --controlled` exits 0.
- **Seen bounding the perimeter** (2026-10-29):
  - Two doors into account state: `PATCH /auth/user-settings` and `POST /auth/accept-terms` (also
    `/datasets/ingest` and the check-in draft opens) write outside `CommandDispatcher`, against
    access-model's "all writes flow through one dispatcher". **Re-test:**
    `git grep -n "@router.\(post\|patch\|delete\)" -- promptpotter/presentation/api/routers`.
  - `/docs` serves Scalar's HTML, which loads its script from a CDN, while `ResponseHeadersMiddleware`
    gives every non-API path `_WEBAPP_CSP` (`script-src 'self'`): the page likely renders blank.
    **Re-test:** open `/docs` in a browser and read the console.
  - `auth.py::callback` warns "HOST_ADMIN_EMAIL is unset" also when it is set and that admin has not
    signed in yet. **Re-test:** set it, sign in as someone else, read the log line.
  - The sign-in redirect codes are a closed set declared only in TypeScript
    (`AuthCore.tsx::AUTH_ERROR_COPY`); the server mints them as bare strings
    (`auth.py::_redirect_with_error(code: str)`). **Re-test:** `git grep -n "_redirect_with_error(\"" -- promptpotter`.
  - `POST /campaigns/{id}/fork-preview` is a read on a write verb, so it spends the write bucket
    (`quota.py::refuse_write_rate`), not the read one. **Re-test:** `git grep -n "fork-preview" -- promptpotter/presentation`.
  - `quota.py::refuse_read_rate` takes a principal id and meters the host, while the launch, verb and
    write buckets take the identity and exempt it. **Re-test:** `git grep -n "def refuse_.*_rate" -- promptpotter`.
- **Seen building the host coupon** (2026-10-29):
  - A backend cell is always the box's to pay (`spend_book.py::_CELL_PAYER`): a connector sends on its
    own keys, so an own-key account past its coupon stops at its first backend cell, as the reserve
    binds it. Action: a cell's payer is the connector's key, declared on the connector. **Re-test:** an
    own-key account at a used-up coupon runs one TermNorm cell.
  - `api_keys.json` holds account keys in plaintext at rest, beside `user.json`. Action: encrypt under a
    box secret, or name the file in access-model's at-rest table. **Re-test:** `cat` one.
  - `registry.get_llm_client` caches up to 256 clients by key, so a removed key's secret stays in
    memory until evicted; resolution re-reads `api_keys.json` each send, so it is never SENT. **Re-test:**
    remove a key, then read `get_llm_client.cache_info()`.
  - A resumed cycle's prior metering counts as the box's in its book's coupon reserve
    (`SpendBook.take_up` reads only unreported own-key holds), so a resumed own-key run reserves more of
    the coupon than it draws. Conservative, not a leak. **Re-test:** resume an own-key cycle and read its
    `off_coupon_usd`.
  - `TokenUsageRecord.cached` and `key_source is None` are two spellings of one fact (a replay no
    wallet paid). Action: one field. **Re-test:** `git grep -n "key_source=None" -- promptpotter`.
  - `coupon.py::resolve_api_key` folds every account ledger on each send of a non-host account (it reads
    the remainder fresh). Action: the run's book carries the remainder it was admitted against.
    **Re-test:** time one send on an account with 100 cycles.
  - Restamp drops `User.spend_budget_usd_total` / `token_budget_total`: a hand-raised account must be
    re-granted as `grant.json`. **Re-test:** `ls .promptpotter/projects/*/grant.json` after a restamp.
  - `CouponStatus.exhausted` is true both used up and `void`, so each reader guards `void` first (three
    webapp sites). **Re-test:** `git grep -n "\.void" -- webapp/components`.
  - The refusals in `coupon.py` / `errors.py` name a webapp pane ("Account → API keys"): renaming the
    pane means editing Python strings. **Re-test:** `git grep -n "API keys" -- promptpotter`.
  - `LifetimeSpend` carries no token count, so the coupon's token side shows left / face, never used.
    **Re-test:** the Host allowance section with a token-bound grant.
  - `CommitInput` forces `autoComplete="off"` and, after a failed commit, will not resend the same value
    on Enter. **Re-test:** fail a key PUT, press Enter again.
  - `AccountSpend` refreshes the coupon both on its poll and from an effect watching the sidebar's
    `billed_usd`. **Re-test:** `git grep -n "couponRead" -- webapp/components`.
  - The onboarding dialogs use raw `<button className="login-button">`, not `ui/Button`; and
    `AccountUsageTab::LimitControl`'s note ("runs on their provider key") predates own keys.
    **Re-test:** `git grep -n "login-button" -- webapp/components/onboarding`.
- **The offline legs rest on fake-data draws keyed by the whole node config**
  (`offline_world.py::FakeBackend`): an unrelated config value reshuffles which rows saturate, so a
  leg's premise must be pinned (`EVERY_L2`, `REBASE_HARDNESS`), never inherited.
- **Seen removing the migrations** (2026-10-30):
  - `LLMCallRecord.payload_kind == "synthesized"` and `usage is None` state one fact twice.
  - `identity/default_claim.py::_rewrite_campaign_ownership` writes `campaign.json` beside
    `CampaignStore.update_campaign`, and logs an unreadable manifest instead of refusing.
  - `jobs/logs/{job_id}.log` (`launcher/run_job.py`) outlives its job: nothing deletes it.
  - `family_ray_queries.py::_RayTail` gets a line no activity arm accepts as a `feed_fold` log, not a
    `LedgerUnreadableError`: `LineIndex` validates JSON only. Both tails skip an unknown
    `record_type` silently.

## Bypasses — held for one holistic pass

**A send made on our behalf re-derives what `_admitted_send` decides for our own.** Patched singly,
each fix is one more copy; the remedy is the upstream redesign.

- `infrastructure/llm/send_pacing.py::SendBudget` stops at the relay's door: TermNorm's
  `_MAX_ATTEMPTS` and terminus-2 × litellm retry on their own budgets. **Re-test:**
  `grep -n "_MAX_ATTEMPTS = " <termnorm>/backend-api/core/llm_providers.py`.

## Blocked

- **TermNorm's live datasets (`llm_only`, `entity_profiling`, `llm_ranking`) are refused at parse**:
  TermNorm's `_enrich_with_registries` must carry `meta["fields"]`, its prompt metadata declare
  `fields`, and `direct_prompt` gain a `prompt_info`. **Blocker:** the operator's go to edit the
  TermNorm checkout. **Re-test:** `python -m promptpotter new llm_only` parses its pipeline.
- **Two readings wait on the operator:** a declared slot before `rewrites` + `field` fold into one
  knob; which ruler `ab_replay` reads.
- **`infrastructure/llm/anthropic.py::AnthropicClient` cannot run a node on a current Claude model**
  (no effort mapping, sends `temperature`, never sends the schema, stale `max_tokens`). **Blocker:** no
  Anthropic credential. **Re-test:** `settings.ANTHROPIC_API_KEY` non-empty, then `probe-reasoning`.
- **A head-to-head's arms do not share one optimizer model.** **Decided:** every arm on one model,
  each peer's swap a declared deviation. **Rides with:** the next head-to-head mint. **Re-test:**
  `grep -n "model:" promptpotter/assets/optimizers/*/pipeline.yaml`.
- **Archive hygiene — build before the next bulk delete.** **Re-test:** `compact-archive inventory`.
  (1) **Reclaim**: a dataset-scoped destructive `delete` counterpart, dry-run by default, refusing
  while a producer can append, naming what it would strand. (2) **Attribution**: an L4 outer cell
  stamps the runs its inner campaign produced when it spawns them; never re-file a backfill. (3)
  **The reach map**: the campaign family left, the archive partitions it reaches right, the
  load-bearing column what is shared outside the selection.
- **Cross-repo (TermNorm `backend-api`):** a `/version` endpoint, then bump
  `connectors/termnorm.py::_EXPECTED_REVISION` (re-test: it is `None`). A co-owned backend fix replays
  stale results because neither cache keys on backend revision: fold the revision pin into the
  measurement-cache key (re-test: grep for `--fresh` or a revision term on the key).
- **No inbound REST credential** — owned by [`../developer/stable-api.md`](../developer/stable-api.md)
  § 8; owed here: the credential and its `submit → poll → fetch` examples. **Re-test:** a bearer or
  API-key reader in `promptpotter/presentation/api/`.
- **Swapping a model means hand-editing two `pipeline.yaml` lines.** **Blocker:** a swap verb.
  **Re-test:** one in `presentation/cli/commands/`.
- **`infrastructure/llm/json_parse.py::try_groq_json_validate_repair` banks a missing count as an
  empty account.** Never estimate from content length. **Blocker:** `TokenUsageRecord` has no
  unknown-count dimension. **Re-test:** a `provider: groq` in any dataset pipeline.
- **`_rebank_on_branch`'s re-bank has never been observed.** **Re-test:** repair a fork; each
  corrected round carries its own `round_restated` record.
- **The `seed` provenance layer has never been stamped by real data.** A `resume --steer NODE.PARAM=VALUE`
  fork exercises it. **Re-test:** from the checkout root, `grep -rh cycle_seed
  .promptpotter/projects/*/campaigns/*/cycles/*/.runtime/ledger.jsonl | grep -c '"pipeline_overlay": *{'`.
