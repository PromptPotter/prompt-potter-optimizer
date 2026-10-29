# Code-Debt Cleanup — Backlog

**Only work that is blocked or multi-arc** — including every shape a pass noticed off its task (root
`CLAUDE.md` `<situational-awareness>`). Anything one edit closes is fixed in the pass that found it; never the root of a patch just shipped (root `CLAUDE.md` `<root-fix>`). An item ships by
being deleted. Each entry: `file::symbol` — the defect — the action — **Rides with:** the work that
reaches it — **Re-test:** the command or question that says it still stands. Run the re-test before
acting.

## URGENT — shapes that hide the backbone from a reshape

**Rides with:** any pass in the file named.

- **Inputs that change a run but leave no event** (audit 2026-10-29, `decisions.md` § 0009's last
  bullets). Left:
  - **An arm's fork, `submit_arms` and skip are refused** (`refused_on_an_arm`) while a rewind,
    `fork_on_divergence` and `no_divergence_check` are admitted as a `LeftComparisonRecord`: one door
    shape per act is `decisions.md` § 0009's open proposal.
  - **Today's ledgers are read by tolerance until the 2026-10-30 wipe**: the new `ResolvedRunRecord`
    fields default, and `CommandEffect`'s arms are told apart by their fields, not a tag. Make the
    fields required and tag the union with the wipe.
  - **`campaign.standing_budget`'s money lives in `campaign.json`**, beside the ledger.
  - **A cycle's share has two stores**: the job file arbitrates siblings and feeds the book live, and
    `CycleShareRecord` records each move. Action: the book and the sibling sums read the record.
  - **A backend revision unlike the connector's expected one is a log warning**
    (`initialization/wiring.py::_verify_connector_revision`); only a resume whose origin moved names it.
  **Re-test:** each input either hashed into the declaration or written as a record.

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
  `shell/node-surface/`, root `PairedLift` / `SummaryBlock`. Hard samples and compare each spread over
  several folders (`git ls-files webapp/components | grep -i -E "hard-?sample|compare"`).
  **Re-test:** `git ls-files ':(glob)webapp/lib/*.tsx'` lists none.
- **Adapters over a flat served shape**: `useCandidatesModel.ts` joins every source it imports;
  `.facts.` opened at `git grep -c "\.facts\." -- webapp/components webapp/lib`; liveness
  re-derived at each `git grep -n "phaseWalks(" -- webapp/components` site.
- **Several doors into one state**: candidate selection minted at each
  `git grep -n "selectedCandidateOf(" -- webapp` site, the sample set written through
  `setSelectionForSampleSet` from several surfaces, `TrendChart.onOpen` exists so a caller can
  `openView`, `FileTree::onSelect` drilled through its parents.

### The member backbone

- **A repair reads the cycle past `sees`.** `round_packages` and `rederive` are handed the `Cycle` and a
  `CampaignStore`: `potter/resume.py::_seat_on` builds its own `RoundContext` off the session ledger, and
  `rederive_critiques` restates the round itself. Action: the bench seats one `NodeContext` per repaired
  round and restates onto the branch hop: a member answers `round_packages(ctx)` and `rederive(ctx)`
  for that one round, and gepa's and levi's `rows_read_packages` read `Lineage.rounds`. It moves the
  repair path's package fingerprints, which decide EQUIVALENT vs SUPERSEDE, a path the offline run
  does not walk and no test reaches, so its proof is a repair-fork test first.
  **Re-test:** `git grep -c "RoundContext(" -- promptpotter/application/optimizers` is 0.
- **A round role is ordered three times**: `optimizer_manifest.py::_round_plan` admits each role by
  its position against the gateway and selector, `::_walk` places every role for the closure check,
  and `runner/round.py::execute_round` calls them. A static order cannot derive from calls, so one
  order needs the executor to walk the plan's. **Re-test:** two of the three orders deleted.

- **The cladogram re-derives fork lineage the server resolves.**
  `webapp/components/candidates/forest-layout.ts::extentKeys` walks spawn cuts client-side while each
  `HierarchyCandidate` serves `main_line`; the cut is by proposal round, not line position. Action:
  an extent is the served line; the client walk dies.
- **A round's reference is recorded at SELECT**, so a measuring round serves `RoundFacts.reference`
  null. Action: record it at round entry. Blocker: a repair fork's restated rounds must write one first.
- **A round-0 fork whose parent's round 0 elected nobody is refused** (`ConflictError`), for both a
  resume-divergence fork and a diag sibling, where the origin is the only candidate to hang on.
- **The dev loop serializes where it need not:** sibling gates queue whole runs, `next-build` peaks
  near 4 GB, builders spend most tokens on whole-file reads, Vitest's worker start timeout fails a
  green suite on a loaded host. Action: attribute each, then cut what our code wastes. **Re-test:**
  five builders plus one full gate with no reap and no gate waiting longer than its own run.
- **Ledger shapes left standing:** `round_restated` carries the whole round. **Re-test:** the
  symbol deleted, read, or tested.

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
  per-value lists name only values known to fail; the spend ceiling bounds the rest. **Rides with:** the
  host coupon ([`roadmap.md`](roadmap.md) § Host coupon + BYO keys). **Re-test:** a free account's model
  menu equals `free_tier.yaml::task_models`.
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

## Open — from the head-to-head arc

A leading **INVESTIGATE** means nobody has shown the item is real; answer that first.

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
- **An arm refused at run init keeps its key, and its config can no longer be changed.**
  `preflight.py::refuse_arm_below_round` advises "size the round down with the optimizer's own
  knobs", but a node knob is fixed at mint. `new --arm <h2h>:<key>` on that key is refused ("is
  campaign … already"), so the operator mints under a second key and the head-to-head keeps a dead
  arm that never searched. Seen on h2h-7: capo `__bca431` was refused at its $0.03 cap; `capo-b5`
  `__530ab2` was minted with `max_blocks=5`. Also seen there: `set-limits --max-usd 0.20` on
  `__bca431` did not arm before init read $0.03. Open: should an arm that never closed a round be
  re-mintable under its own key, or should the refusal run before the mint so no key is taken?
  **Re-test:** `promptpotter evidence` on h2h-7 lists `capo` beside `capo-b5`.
- **CAPO's race ends after its first block whenever offspring equal the cut.** The fixed blocks follow
  the paper (App. C.4), but with 4 offspring and μ 10 every h2h-7 round cut 4 on block 1 and stopped.
  The population was then judged only on those 30 cells: the crown held C1.7 (36.7% there, 27.5% on
  the origin panel), and the selection benched 0.110 against the origin's 0.140. `max_blocks` was inert.
  Open: is this the paper's behaviour at these sizes, or does our eliminator stop where the paper races
  on? **Re-test:** the per-round "Blocks raced" fact on a CAPO run with offspring < μ.
- **One bad pack fails the whole library.** `config/prompt_blocks.py::PackShelf.read` raises on a
  malformed `pack.json`, and a name both roots hold raises `block_pack_collision`. Either one is a 500
  or 409 for every `GET /library` and every draft edit, not just for that pack. Seen: the e2e fixture
  kept the old `Authored` shape. Action: refuse the pack, serve the rest.
- **A pack's on-disk shape moved with nothing to migrate it.** `Authored.version: int` and a required
  `supersedes` landed with the packs commit. The account's `starter` pack still held git-sha versions,
  so `GET /library` returned 500 until it was migrated by hand (2026-10-29). `gate.py`'s `restamp` check
  guards ledger records only. Action: packs ride the same shape-moved check, or `restamp` migrates them.
  The live box holds packs too. **Re-test:** change a field of `Authored` and see the gate name the packs.
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
  - `pipeline_resolve.py::_pack_offers` reads the block pool's `packs` by its key off a dumped knob
    model: the initializer should serve its packs as it serves `active`, but `optimizers/pools/members.py`
    is hashed whole, so the method rides the next accepted treatment re-key.
  - `potter/dispatch/facade.py::_by_field` spells a block's field as `entry.group[0]` beside
    `domain/pool.py::BlockPool.part`; the facade is hashed whole, so it rides the same re-key.
  - A named pack that leaves the shelf makes `BlockPoolInitializer.held` raise `block_pack_not_found`,
    so the draft read itself fails instead of offering the pack to unname.
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
  - `config/prompt_blocks.py` says "render order" for `PROMPT_FIELDS` order (a hashed module);
    `examples/optimizer-plugin/README.md` names `nodes.Measured` for `MeasuredRound`.
  - `vitest.config.ts` loads as CommonJS (Vite warns on every run).
- **A worktree that ran the offline gate cannot be removed by git**: `.gate_cache/offline-run` nests
  campaign paths past Windows' 260-char limit (`git worktree remove` fails "Filename too long"), and a
  worktree's `webapp/node_modules` junction makes any forced recursive delete reach the main tree's.
  Action: the offline home lives outside the tree (or under a short root). **Re-test:** run the
  offline gate in a worktree; `git worktree remove` succeeds.
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
- **A killed push leaves live links into the main tree.** `gate.py::_tip_checkout` links `.venv`,
  `webapp/node_modules` and `.gate_cache` into `%TEMP%\pp-push-*`. A hard kill skips the unlink, so the
  temp dir keeps junctions that a later recursive temp delete could follow. Action: the next `--push`
  sweeps stale `pp-push-*` (unlink, then `git worktree prune`). Also: the green store keeps one key per
  check name, so a tip's offline-run green overwrites the working tree's own, and the two take turns
  rerunning. **Re-test:** kill a push mid-run, then run `--push` again and list `%TEMP%\pp-push-*`.
- **Seen hardening the endpoints** (P0, 2026-10-29):
  - `main.py` CORS keeps `allow_methods=["*"]` / `allow_headers=["*"]` beside the pinned origins;
    pin them to what the routes serve.
  - Only launches and paid calls meter a write (`quota.py::refuse_verb_rate`, `paid_call`): a free
    verb (`set-campaign-label`, `bank-snippet`) has no rate (C6).
  - Every refusal logs one WARNING (`main.py::_error_response`), so a 429 flood is a log flood.
  - `/docs` and `/openapi.json` sit outside `main.py::API_ROUTERS`, public by a comment, not by the
    table; the leaks journey matches public routes by path, so a gated method sharing a public path is
    skipped.
  - `auth.py::_require_provider_client` and `callback` dispatch on the provider string with two
    `type: ignore[union-attr]`: string-keyed call dispatch.
  - `app.openapi()` warns `UnsupportedFieldAttributeWarning` (alias `body` / `payload` on a union-typed
    body param): a request alias that has no effect.
- **The offline legs rest on fake-data draws keyed by the whole node config**
  (`offline_world.py::FakeBackend`): an unrelated config value reshuffles which rows saturate, so a
  leg's premise must be pinned (`EVERY_L2`, `REBASE_HARDNESS`), never inherited.

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
