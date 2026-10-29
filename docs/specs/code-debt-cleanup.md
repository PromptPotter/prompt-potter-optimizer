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
  - **INIT selects the optimizer apart from the launch's binding**: `jobs/mint.py::refuse_unrunnable`'s
    pool refusals and INIT's preflight call `select_optimizer` on the mtime-keyed manifest cache, so a
    `pipeline.yaml` edit during INIT is read by them and not by `bound_optimizer()`.
  - **A running job's allotment share is read live** from the job registry; only the armed ceiling
    lands, on `change-run-limits`'s ack effect.
  - **`ResolvedRunRecord.model_capabilities` / `backend_revision` have no served reader** beyond
    `refuse_drifted_resume`'s capability cause.
  - **An arm's fork, `submit_arms` and skip are refused** (`refused_on_an_arm`) while a rewind,
    `fork_on_divergence` and `no_divergence_check` are admitted as a `LeftComparisonRecord`: one door
    shape per act is `decisions.md` § 0009's open proposal.
  - **The new `ResolvedRunRecord` fields default so today's ledgers read** (data kept through
    2026-10-30): make them required with the wipe.
  - **`CommandAckRecord.effect` is an untyped `dict[str, Any]`**, now carrying the armed ceiling.
  - **`campaign.standing_budget`'s money lives in `campaign.json`**, beside the ledger.
  **Re-test:** each input either hashed into the declaration or written as a record.

- **Three probed invariants the tree breaks** (`gate.py::_KNOWN_FALSE`): θ differenced without
  `comparable_to` (`escalation/state.py::_improved`, `views/render/phase.py`); hand-rolled `<table>`
  beside `ui/DataTable` in `components/compare/`, `shell/SpendBuckets`, `ingest/DatasetPreview`; a
  hand-rolled dialog in `shell/sidebar/SidebarFilterPopover`. Action: route each through its owner.
  **Re-test:** `gate.py --only doc-claims`.
- **The chat thread has no subject.** `webapp/lib/chat/thread.ts::ThreadProvider` is one in-memory list for
  the whole app and `lib/ingest-flow.tsx::IngestFlowProvider` one check-in beside whatever is viewed;
  `components/chat/ChatPane.tsx` concatenates both with the viewed cycle's live feed and run card, held
  apart only by a `composing` flag that the modal door (`ingest/IngestPane.tsx`) never sets. A run ending
  while a check-in is open files its ending into the check-in (seen 2026-10-29). Action: the viewed
  address is the conversation — a draft is an address beside a cycle path, `+ New campaign` navigates to
  one, the pane renders that subject's thread; `composing`, the modal door and the global list go.
  **Re-test:** open a check-in while another run ends; the check-in shows nothing of that run.

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
  several folders (`git ls-files webapp/components | grep -i -E "hard-?sample|compare"`); hand
  `<table>`s at `git grep -l "<table" -- webapp/components` (tracked `_KNOWN_FALSE`).
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
  round and restates onto the branch hop. It moves the repair path's package fingerprints, which decide
  EQUIVALENT vs SUPERSEDE, a path the offline run does not walk, so its proof is a repair fork first.
  **Re-test:** `git grep -c "RoundContext(" -- promptpotter/application/optimizers` is 0.
- **A round role is ordered twice**: `optimizer_manifest.py::_walk` and `runner/round.py::execute_round`
  each place the sampler, eliminator, selector, adapters and controller. **Re-test:** one of the two
  orders deleted.

- **A sampler's cells ceiling states other members' reads.** `Sampler.round_cells_ceiling` overrides
  in `Blocks`, `ProxyCss` and `AdaptiveQueue` hold facts the backbone should sum. The formula
  `arms × round_cells + parent_cells` misses gepa's proposer parent read (`Frontier.measure_parent`)
  and levi's seeding draw, which under-prices
  `preflight._check_cap_funds_round`. Action: the sampler states its draw per walk; the parent read is the
  union of the selector's `parent_cells` and every proposer's `measure_parent`, both bounded by the
  panel; then delete the overrides. **Rides with:** recombination's pools stage. **Re-test:** the
  derived ceiling equals each `round_cells_ceiling` override's for every optimizer at search sizes
  40, 200 and 1000.

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
- **No check holds `§ Name` to a heading.** `docs/CLAUDE.md` requires each `§` reference to match
  exactly one heading in the linked file; `variation-and-pools.md` pointed at a section of
  `domain/CLAUDE.md` that never existed, and every gate was green. Action: a `doc-claims` pass
  resolving each `[...](path) § X`. **Re-test:** a dangling `§` fails the gate.

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

- **A ledger field removal broke the operator's campaigns silently**: 5b023674e dropped
  `llm_call_start.injection_silent`, leaving 6 of 8 local campaigns unreadable until `restamp --apply`
  (2026-10-29). Nothing runs `restamp`'s report after a record-shape change. **Re-test:** a gate or
  commit check that a removed ledger field is prunable, or a removing commit's body naming the restamp.

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
- **The check-in lists a demo pool as raw sample-key hashes** (chips like `a9123c5c…· 1`): an id with
  no content preview gives the operator nothing to pick by.
- **Seen building the setup screen**:
  - One app-wide `SelectionContext.node` serves the target and the optimizer graphs, and
    `PipelineSetupInner`'s auto-select races the optimizer graph for it: a global standing in for a keyed
    subject.
  - `OptimizerSetupInner` reads the optimizer from the draft and from `useConnector().optimizer`, which
    are one value through two doors.
  - `NodeDetail` shows an optimizer prompt read-only through `NodeSurface`, whose `PromptFieldsEditor`
    is labelled `scope="target"` (`NodeSurface.tsx`), while the setup step edits it. Make `NodeDetail`
    the one editor.
  - An edited optimizer-prompt field cannot be reset: `OptimizerNodePrompt` serves no template text and
    `campaign_config._merge_dicts` keeps a null as a value.
  - `LibrarySnippet` serves no `presumes`, so the library cannot show that a snippet the chain refuses
    never enters a pool.
  - Smaller: `title=` used as teaching prose in `NodeDetail` and `OptimizerCard`;
    `CheckinLoadingWindow` keeps its own `setInterval` outside `lib/cadence`; `.setup-step` is styled in
    `chat.css`.
- **No gate check runs `scripts/offline_run.py`**, so a commit that breaks every offline leg passes
  the gate (3e3b1bb62 did). Its legs also rest on fake-data draws keyed by the whole node config
  (`offline_world.py::FakeBackend`): an unrelated config value reshuffles which rows saturate, so a
  leg's premise must be pinned (`EVERY_L2`, `REBASE_HARDNESS`), never inherited. **Re-test:** a gate
  check that names `offline_run`.

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
