# webapp — CLAUDE.md

Next.js + React + TypeScript, static export at `out/` mounted at the domain root by FastAPI: **the app owns `/`, the API is the carved-out `/api/v1` namespace.** A control-plane dashboard over served cycle state — it polls, it renders, it issues control verbs (pause/resume/fork/steer/set-budget/…) through `POST /commands/{kind}`; it never recomputes a score itself. Installed versions are in `package.json`.

## Load-bearing

The rules a change here breaks most often. Each names a section below; the section states it.

- Never compute a score, ordering or mask → § Scoring authority
- One source per data class → § Display-data sources
- One address, `viewedPath` → § Viewed identity — one address (CyclePath)
- Classify a failure, never bucket it → § Failure handling — classify, don't bucket
- No hand-rolled primitive → § Component conventions
- Barrel order IS the cascade → § Stylesheet organization (cascade order is load-bearing)
- No raw px for type or space, no new breakpoint → § Stylesheet organization (cascade order is load-bearing)
- A wire type is generated, never written → § A wire shape is GENERATED — never hand-declared

## Owned elsewhere

Each rule binds this layer but is stated by the file governing the artifact it constrains. Read the owner before changing anything it covers.

- **Surface behavior contract** — owned by [`../docs/specs/frontend-surface-contract.md`](../docs/specs/frontend-surface-contract.md); every user-facing PR here is measured against its `I*` invariants, and this file owns only the implementation side.
- **Scoring authority** — owned by [`../docs/architecture.md`](../docs/architecture.md) §0.5; here it lands as the three shapes below.
- **Visual identity and copy register** — owned by [`../BRAND.md`](../BRAND.md) + [`../VOICE.md`](../VOICE.md); never introduce a parallel design spec, tokens doc or theme-decision file, and read every value as `var(--…)`.
- **What `dashboard.json` guarantees its readers** — owned by [`../promptpotter/infrastructure/CLAUDE.md`](../promptpotter/infrastructure/CLAUDE.md) § Persistence; poll it and trust it, never reconstruct a served field from a second source.
- **Which model a new field lands on to reach a panel** — owned by [`../docs/developer/adding-a-surface.md`](../docs/developer/adding-a-surface.md) § 3; a panel reading a field no writer sets is the half-wiring that recipe exists to prevent.
- **The lineage tree — forks, crowns, θ** — owned by [`../promptpotter/infrastructure/CLAUDE.md`](../promptpotter/infrastructure/CLAUDE.md) § The lineage tree; this layer renders what `/tree` serves and derives no lineage of its own.
- **The check list this layer's gate runs** — owned by `scripts/gate.py`; run `python scripts/gate.py --web`.
- **Where a distributor's brand values come from** — owned by [`../deploy-linux/README.md`](../deploy-linux/README.md) § Running it under your own name; a new `lib/brand.ts` field is unreachable until `deploy-linux/brand-env.sh` exports its `NEXT_PUBLIC_*`.

## Scoring authority

Three shapes this layer must not introduce, each of which arrives looking reasonable:

- **A local re-sort.** An ordering *is* a score. `hard_samples.json` is a served ranking, not a list to `.sort()` on whichever field is in hand.
- **A recomputed mask.** A scoring or sample-set masked value comes down as a served overlay, as do the realized criterion's per-evaluator weights (`composite_fitness_weights`); deriving either here re-answers the question under the client's guess at the formula. Judging whether a served value is READABLE is not recomputing it — `subsetExactFor` asks the served registry which criteria survive a sample-set mask whole and suppresses only the rest. **The SCORING MASK is ONE value and ONE form** (`components/shell/mask/`): a discriminated union over the two ways to say a criterion, with `lensOf` the single place either `?lens=`/`;lens=` spelling is minted and `criterionOf` the only place the `score:` namespace is stripped off. Say **mask** (the alternative criterion) and **lens** (the selector), never "what-if". A served lens is a string the wire already collapsed, so it re-opens in Expression mode — decomposing one back into weights is the formula parse this rule forbids. Ownership does not collapse with the form: the dashboard's mask is module state, a Compare channel's rides its own address.
- **A number kept alive under a changed setup.** The two masks re-PROJECT the record, both re-read from rows that exist. A node parameter, a model or a prompt field is not: nothing ever ran at the edited value, so every measurement at that searchpoint *and everything descending from it* describes a search that did not happen. The honest render is `?`, and the closure is `descendantsOf` over the served `parent_id` — which crosses forks, because a fork's candidates hang off the point it left. Say **invalidated** (no reading exists), distinct from **divergent** (a counterfactual the server computed) and **retired** (the record of what ran); each wears its own class and its own words in the node's `<title>`. Diff a values-editor emission against its seed before reading it as an edit — `NodeSurface` emits the point's WHOLE running config, so taking it wholesale marks every parameter changed at once.
- **A fabricated default.** `noUncheckedIndexedAccess` is ON, so an index access (`arr[i]`, `rec[k]`, `match[n]`) types as possibly-`undefined` — **never `?? <default>` to silence it.** `edits[key] ?? r.value` is not `edits[key] !== undefined ? … : …` the moment an operator clears a field to `""`. Handle the miss (skip / early-return); `!` only where the line above proves presence.

The pure helpers in `lib/derivations/*` are not an exception: they group, lay out and format served data, and introduce no number that was not already on the wire.

## Stylesheet organization (cascade order is load-bearing)

All CSS lives under `app/styles/`, imported by the ordered barrel `app/styles/index.css` — the only stylesheet `app/layout.tsx` imports. Lightning CSS inlines those `@import`s into one sheet **in barrel order**, so **the barrel order IS the cascade**: moving rules between files only stays correct if you preserve their relative order in it. There is no `globals.css`; do not reintroduce one. `foundation/` (whitelabel-safe skeleton) imports first, `domains/` (one file per feature, each with its co-located `@media`) after, and the two cross-cutting tail files last so their overrides win.

Component-specific rules belong in their domain file, or a co-located `*.module.css` once the component is refactored — that is the migration endgame. A new `@media` breakpoint reuses a `--bp-*` token; a novel value is a deliberate act, read in the diff. **Type, space and COLOUR come from tokens, never a literal** — `--text-*`, `--space-*` and the hues in `foundation/tokens.css`, the space scale fluid above its smallest steps so a phone contracts without anyone writing a media query. Greys are exempt because a wash has no hue to name. **Nothing enforces this half automatically**: the three `css-*` scans that read the stylesheets off disk were cut for breaking on every file move and never on a wrong colour ([`../tests/CLAUDE.md`](../tests/CLAUDE.md), axis 1). Stylelint is the admissible replacement if the class returns. A categorical chart takes its Nth ink from `theme.ts` — `seriesColor` for a `<canvas>`, which has no cascade to read, and `seriesVar` everywhere else — never a local array. **Only a `<canvas>` needs `getCss` to resolve a token** — inline SVG and `style={{}}` read `var()` off the cascade and repaint on a theme flip with no read, no subscription and no re-render, so a theme-tick beside one is apparatus for a problem it does not have. The rule reaches inline `style={{fontSize}}` in TSX too, which is the half no stylesheet lint can see. Three deliberate exceptions: the `16px` on a touch text input, because iOS focus-zoom is a constraint and a token merely equal to 16 today would take the suppressor with it; sub-4px hairlines, which are tile-grid geometry rather than rhythm; and `fontSize: 0`, which hides text rather than sizing it.

**`overflow:hidden` on a wrapper whose content can outgrow it DELETES that content** — no scrollbar, no ellipsis, nothing on screen to say so, and it reads as a clean layout in every screenshot. Wrappers that can overflow take `overflow-x:auto`; a fixed width takes `min(…,100%)`, since `max-width:100%` cannot beat a larger `min-width`. **A `viewBox`'d SVG at `width:100%` fails the same way and is harder to see** — it never overflows, it *scales*, so a strip built for N nodes silently compresses until the labels collide and no overflow check anywhere fires. Give it its intrinsic width and let a wrapper scroll — and where it must genuinely fit a narrower box, shrink the GEOMETRY and stop printing the text (`forest-layout::Density`, `ROOMY` vs `DENSE`), because a drawing's width is set by the labels on it. Never the scale. All three halves — width, clipping, scaling — are on you at review; none is caught statically. `glass.css` is operator-vetoed glassmorphism, preserved verbatim and never restyled.

State-class composition uses `cx()` (`lib/cx.ts`), not template strings: `cx("hs-cell", folded && "folded")`, never `` `hs-cell${folded ? " folded" : ""}` ``.

## Component conventions

- **Layout is three tiers, decided top-down.** Every file is exactly one of: a **primitive** (`ui/`, `forms/`), cross-surface **chrome** (`shell/` — anything rendering on more than one tab), or part of **one surface** (`chat/`, `dashboard/`, `verify/`, `tree/`=Files, `ingest/`). A file answering two is mis-filed. `components/shell/node-surface/` and `components/shell/searchpoint/` are the worked examples: each renders on several tabs, so filing either under one surface makes the others reach into that folder. **Each host owns only what its own surface can answer** — which cycle is streaming, which round is in flight, which dataset's schema applies — and the reading itself takes props. Shared app state is a context in `lib/`, never a component; `app/page.tsx` mounts `shell/AppShell.tsx`.
- **Inside a surface, organize by domain region — one axis, never by widget kind.** `dashboard/` is `samples/` · `scoring/` · `pipeline/` · `control/` · `layout/`: the §0 primitives the operator observes, so the names survive new views. Kind-buckets (`charts/`, `detail/`) collide with that axis and rot. A domain widget no single pane owns gets its own folder (`candidates/`, `eval/`, `workflow/`).
- **Never hand-roll a second modal / popover / dropdown / toggle-chip / segmented control / toolbar / commit-on-blur input.** Reach for `components/ui/*`, or add it there with an RTL test and a `*.module.css` where the thing is presentational at all (`CommitInput` is behaviour, so it carries none).
- **Nor a second pipeline GRAPH or a second LINEAGE TREE.** Every graph is `dashboard/pipeline/PipelineFlow` over the served `view` + its `tier`/`rank`; a hand-placed geometry beside it lasted exactly as long as it took to disagree — three edges the wire never declared, the loop and both escalations dropped. Every cladogram is `candidates/Forest`, taking a `CladogramCtx` so a surface injects which lane is its own, what a searchpoint click means, which channels it carries and where it is CUT. **A channel's extent is the chain that PRODUCED its point**: its own course up to its own round, each course above it cut where the next hangs off, plus the seed runs that measured the anchor, whole. `extentKeys` decides it and `layout(tree, expanded, keep)` draws exactly that set, so what a card draws and what it inks cannot disagree; extents nest, and the narrowest one holding a node owns its ink. **Never a round-COLUMN test** — a seed is drawn one column RIGHT of the point it measures, so that reading loses every seed of a campaign read at its own origin.
- **A card header is ONE row** — `Toolbar` + `ToolbarSep` + `ToolbarSpacer`, controls `flex: 0 0 auto`, because a toolbar that shrinks its buttons to fit is lying about how much room it has. Rare controls fold into `Menu` behind a `⋯`, lit while any is active; **a control driving one region belongs beside that region**, not in the header.
- **Two surfaces sharing no axis do not share a box.** The candidates card's geometry exists to hold the dendrogram on its bars; the lineage forest is a cladogram of *cycles* with no shared axis, so it is its own `ForestCard`.
- **A text or numeric control commits on Enter or blur, NEVER per keystroke** — `ui/CommitInput` owns the discipline; do not re-implement the draft. Each half-typed value is a *valid but wrong* request: a keystroke commit on the look-ahead field arms `"1"` — a disarm — on the way to `"12"`, and a keystroke-driven metric key 400s on every half-formula and blanks the card under the cursor still typing. On error keep the last good read on screen (`failureKind → invalid` is the operator's input, not a dead read), which is exactly why the primitive latches what it SENT rather than comparing against the prop: a rejected value stays on screen deliberately, so the blur after the Enter would re-fire it.
- **A lit chip wears the ink of what it put ON SCREEN**, never the ink of the thing it names. A chip lit in the series colour with no series drawn reads as data still loading when nothing is pending. Lit state comes from the served value, never a local boolean — which would stay lit after the round consumed it.
- **A sidebar collapsed on a desktop must not become an empty list screen.** Below `--bp-md` the sidebar at full width IS the list screen and carries its own header and footer, so it needs no bar of its own and drops the view nav and the collapse chevron; above it, the same component is chrome.
- **A painted surface is a control too.** SVG `<g>` elements and tree nodes earn keyboard operability and a `role`; a `<canvas>` earns an `aria-label` naming what it plots, because a chart is otherwise nothing at all to a screen reader — reading this rule as SVG-only is what let every Chart.js canvas ship unnamed. The accessibility floor is `BRAND.md`, and it does not stop at HTML elements.

## Display-data sources

Four surfaces back the dashboard. **Read from the right one, and pick one source per data class** — each file's own header states what it holds.

| Surface | Reached by | Holds |
|---|---|---|
| `dashboard.json` | `lib/poll.tsx` → `useCycleStream()` | in-flight `current_round` + `rounds[]` completed summaries. **Sole source** for FitnessChart, TrendChart, TopStrip sparkline |
| `/tree` | `lib/lineage.tsx` | the served genealogy, `course → candidate → course` at any depth |
| `rounds/round_NNNN.json` | `lib/hooks/useRoundFile.ts`, lazy | the round document — per-sample results, scoreboard, closing OSP |
| the AUDIT TWIN `.runtime/cache/rounds/…` | `useRoundAudit`, lazy | the per-node LLM I/O, which lives ONLY here |

The rules, none of them derivable from the surfaces themselves:

- **A "merge in-flight with historical" or "fall back to the round file when the dashboard hasn't written X yet" branch is the stitch pattern this section exists to prevent.** Where two genuinely are ONE series — the hard-samples dots, whose round-file walk cannot see the round being measured — the SERVER merges them and the client still reads one source.
- **Take a WHOLE candidate row from whichever half has it, and choose the half ONCE.** Never fill one in from the other per field, which puts a bar and its whisker on two different polls.
- **ONE band per CHANNEL, and no scale field.** A per-BAR scale is the thing that must not come back — a band rescaled onto a bar that did not produce it is how the confidence band twice went silently undrawn. Both bands are 95%, because two whiskers drawn alike have to mean alike.
- **A bar-chart channel is declared ONCE**, in `components/candidates/series.ts`. Never a bare string literal joined back into a plugin.
- **Join on `label`, never on a live `candidate_id`.** A candidate has no lineage id until it is scored, so joining on one resolves every closed round and no live one.
- **`id` alone is NOT a key — the address is `(path, id)`.** A supersede leaves the retired side on the timeline and a repair re-measures without re-minting, so both sides can share a `candidate_id`. Key rows on `nodeKeyOf`, and split with `splitRetired` into one collapsed row per branch; left flat, a round of three reads as a round of six.
- **`is_winner: false` says nothing on its own** — a round that HELD reads exactly like one still scoring, so ask `election_held`. `pickWinner` has no first-candidate fallback and must not grow one.
- **The SIDEBAR's campaign row reads `/cycles`, not the tree**, following the served successor POINTER `index.json::superseded_by`. A pointer rather than a status, because a parent that had already stopped for its own reason cannot also say "rebased" without destroying its real `stop_reason`.
- **Two axes, kept apart.** NAVIGATION (`workspace::viewedPath` + `viewedCandidateId`) is written ONLY by the sidebar; INSPECTION (`SelectionContext.candidate`) by a bar click. **A bar click never navigates** — one slot for both made the chart its own input, re-plotting under the cursor that clicked it. A selection is MINTED by `selectedCandidateOf` and nowhere else.
- **The audit twin selects on `round === current_round.round`, not on "has this round closed"**: the twin is flushed AT close, so the escalation calls that fire next land only in the live block. `useRoundNodes.ts` is the single resolver, and splitting that switch is what lets two surfaces disagree about which round they show.

## A wire shape is GENERATED — never hand-declared

**Every response type comes from `lib/api/types.generated.ts`**, emitted by `scripts/build_ts_types.py` off the Pydantic model. Adding a field server-side reaches the browser by regeneration; hand-writing the interface instead is how a type drifts fields behind its model with every gate green. To add a shape: register the model in that script's `EXPORTED_MODELS`, regenerate, re-export it from `lib/api/types.ts`.

**Three allowed escapes, all narrow.** A route with no `response_model` has nothing to generate from, so its shape stays hand-written *and says so* (`reads.ts::HealthResponse`; `workflow/types.ts::PipelineDoc`, the `/optimizer-pipeline` envelope — its `view` half IS generated, and three hand-mirrors of it beside a "keep them in sync" note were the drift that proved the rule; and `LifecycleFilter`, a QUERY-param set with no response model behind it — it must match `manifests.py::_LIFECYCLE_FILTERS` member for member, and under an earlier claim that the two sets differed it silently lost `checkin`). And a narrow alias is **derived**, never re-declared — `export type ActivityWindow = ActivityResponse["window"]` reads the closed set back off the generated interface, so a member added in Python arrives here. Re-typing the members is the thing this rule forbids.

**A closed set belongs on the server.** If you find a union declared only in TypeScript, that is the bug — the only named version of it lives here and nothing can catch a rename.

**`lib/api` is write modules plus one read module, not one file.** `errors.ts` (`throwApiError`, `mintIdempotencyKey`, `IngestApiError` — it lands first; the others throw through it) · `commands.ts` (the closed-set `/commands/{kind}` highway) · `ingest.ts` (the writes that CREATE what a command later addresses) · `draft-types.ts` (pure wire types, and the single owner of `lib/`'s one import from `components/`) · `account.ts` (per-user identity, which is why it is not a command). `reads.ts` stays whole: it is one job.

## Viewed identity — one address (CyclePath)

**"What am I looking at?" has ONE answer — `viewedPath`, and no surface may keep a second.** It is a **`CyclePath`** (`lib/ids.ts`): the chain of `(campaign, cycle)` hops from top-level root to leaf, mirroring the engine's re-entrant `.inner/` sandbox — one hop for a top-level cycle, one more per inner loop. `lib/workspace.tsx` owns it as `pinnedPath` + `following`, with `viewedPath` derived. Drilling in is `drillInto(campaignId, cycleId)` (the workspace appends the hop; a cell bar, an L4 panel row and the hard-samples pointer all just name a run), backing out `backToOuter()`.

**Everything that DISPLAYS re-roots to the LEAF hop; only conversation identity stays on the ROOT.** Leaf: the dashboard stream, connector/pipeline hero, hard-samples panes, chat activity feed, and the selection axes (`SelectionProvider` keyed on `leafCycleId`). Root: `sessionId`, ingest/compose-new-campaign, and Files — so drilling in never mints a thread or moves ingest off the outer conversation. Leaf ids derive at the consumer (`pathLeaf`); `leafIsL4` is likewise the workspace's single answer rather than a per-surface lookup.

**Deep-link carries the WHOLE address, and it lives in the HASH** — `lib/address.ts` owns the syntax (`#/c/<campaign>/<cycle>/<tab>/k/<candidate>`, `#/account/<pane>`), `lib/workspace.tsx` is its sole reader and writer. The hash rather than the path because the app is `output: "export"` behind `StaticFiles(html=True)`: there is no SPA fallback, so a real route would 404 on reload. Three parts, not one, and each was learned: the PATH; the CANDIDATE, because encoding only the path drops the parked node, which for a FORK names nothing at all and leaves the bars blank (same reason DESELECTING returns to the course whose TIMELINE it renders on, never to its own path with a null candidate); and the VIEW, without which every reload dropped the operator back on Chat. Malformed → the view is left alone, never reset. The cycle's universal `cycle_` prefix is stripped there and nowhere else — `ids.ts::encodeCyclePath` is also the server's `?descend=` wire format and stays byte-exact.

## Polling shape

The conditional-request mechanics live in `lib/poll.tsx`'s own header. Four rules bind here:

- **ONE tree per campaign, fetched ONCE and ADDRESSED into — never a second genealogy read.** The server's recursion already reaches every fork and inner run, so a leaf surface addresses into it. Two objections do not survive: a masked body is a strict **superset**, and labels DO join, on `course_label` — the minting course's position, carried through the timeline renumber for exactly this. Only the tree can do that renumber. Ask the tree.
- **`/ray` is the CHRONOLOGY, not a second tree.** The tree answers *what descends from what*, the ray *what happened when* — a fork's records interleave with its parent's, the only way to see it ran concurrently. Server-side they share one family walk, so they cannot disagree about which cycles a campaign holds.
- **A `RayItem.payload` is a SUBSET of the live envelope's, declared server-side** (`domain/projection_envelope.py::RAY_PAYLOAD_FIELDS`). **Reading a new field off a ray item requires declaring it there first** — otherwise it arrives on the live envelope and is simply missing from every ray item, with nothing anywhere to say so.
- **Exactly one live channel (`useCycleEvents`) and one history channel (`/ray`).** The ray gets no SSE join and the tail no `since=`; either would be a redundant mechanism, and the ledger mtime bumps on every append, so the conditional poll surfaces a new event within one tick anyway.

## The chat thread — rendered, never authored

Three rules a plausible edit undoes. Positioning and the open conversational endpoint are [`../docs/specs/chat-foundation.md`](../docs/specs/chat-foundation.md)'s; these are the implementation half.

- **The imprint is the generic `step`.** Everything the Potter *does* — an optimizer LLM call, a web search, a code execution, an MCP call, a backend match — is one `step`: icon + label + status + optional duration/cost. A new `ProjectionKind` maps into that same family; **no new item kind, no translator reshape.** `candidate` and `round` are the optimizer specialization layered on top, and they are what a reusing team deletes (`components/chat/README.md`).
- **Activity and decision items are rendered, never authored by the client** — message items are the only ones the operator and assistant write, and that line is what keeps the thread honest. **One deliberate crossing:** a run that has ENDED is neither, so the client freezes a snapshot of *served values* into the list as a `run` item. Values rather than a pointer, because `resume` re-animates `dashboard.json` and `resume --from N` rewrites the round files beneath it — anything holding a pointer would silently restate itself as the next run.
- **A non-item is not the same as discarded.** `sample_order_preview` yields no item — nothing *happened*, it is the order the scorer is about to walk — but it is read as STATE beside the feed and drives the run card's "next in line". It fires **once per candidate**, so the stream alone left a reader that joined mid-candidate with no forward view at all; `LiveDashboardView` absorbs it to `declared_sample_order` and `sampleWalk` falls back to that. Stream first because it lands sooner, never because it is the only copy. A *declared* order, never a promise: PoBB can stop a candidate before its tail is reached, so no surface may word it as "will".

## Failure handling — classify, don't bucket

**A bare `catch` is the bug.** Every read-path failure is classified once at the transport seam by `failureKind(err)` (`lib/api/client.ts`) into `transient | auth | gone | denied | invalid`, and callers branch on that, never on a status literal. `transient` is the safe default — 5xx, network and parse errors all land there — because the directions are asymmetric: mistaking transient for `gone` destroys the operator's view, the reverse costs one retry.

**`useFetch`'s `survive` needs something to survive ON.** It keeps the last good read under an `invalid`, so the caller must check `data !== null` before rendering the failure as the operator's own input — with nothing kept, `invalid` is a dead read like any other and the message usually renders inside a branch that the missing data has already switched off. Compare rested forever on "Reading 1 channel(s)…" that way, because `/evidence` also 400s on a selection with no scored rows, which is what ticking a campaign whose origin has not run produces. One status, two meanings: the kind alone cannot tell them apart.

**`gone` (404) is terminal and must stop the poll.** One owner acts — `workspace.tsx::reportAddressGone` unpins and resumes following — and **only the address's own authoritative read may report it**, because an L4 inner hop is absent from `/cycles` and an archived campaign from the `active` filter, so list membership would kill two live addresses.

The detector is the dashboard poll, the one read that speaks for the address: its route answers `warming_up` at 200 while a cycle exists without a dashboard, so a 404 there means the cycle dir itself is gone. It confirms over `GONE_CONFIRM_LIMIT` consecutive misses (a single 404 is a mint race). Everyone else reacts locally without voting — notably `useCycleEvents`, whose `EventSource` **cannot see a status** and would auto-reconnect a 404 forever, so it subscribes only while the address is live.

**Every failure is reportable, and carries ids only.** `lib/diagnostics.ts` records each with the `error_id` the API stamps on its envelope, localStorage-backed so it survives the reload the bug provokes; each `error_id` greps the server log. **Never** measurements or prompt text — the same rule `view-memory.tsx` states.

## State reset on prop change

When a component or context must drop derived state because an identity prop changed (the viewed `(campaignId, cycleId)` switched, etc.), use the **render-phase guarded reset** — React's sanctioned "adjusting state when a prop changes" recipe:

```tsx
const [prevKey, setPrevKey] = useState(key);
if (key !== prevKey) {
  setPrevKey(key);
  setDerived(EMPTY); // ...clear every key-scoped field
}
```

It runs **during render**, so the reset and the re-render commit together — no stale frame. **A `useEffect` reset runs after paint and flashes one frame of the prior unit's data; never use it for this.** A hook owning a single state object may instead derive freshness purely: stamp the loaded data with the key it was fetched for and return `EMPTY` until the key matches. Also stale-frame-free.

**A reset may SEED from view memory instead of clearing** — `lib/view-memory.tsx`, one localStorage record per campaign with TTL and LRU applied in the codec so no caller can forget them. It is `useSyncExternalStore`-backed, so the record is readable *during* render and a restore rides the same render-phase reset rather than a post-paint effect.

**Nothing in that record is a measurement, and that is a hard rule** — ids, flags and UI keys only. It is why the INSPECTION axis (`SelectionContext.candidate`) is NOT remembered: it carries `accuracy` and `is_winner`, so a restored value would claim a candidate won a round it may since have lost. The NAVIGATION axis (`viewedPath` + `viewedCandidateId`) is remembered instead — every field in it is an id, and it merely re-parks the tree. A scoring or sample-set mask is excluded for the same reason: restoring one silently means the operator reads masked numbers as the record.

## Render-cost guards (do not regress)

Per-poll re-renders cascade through the chart tree by default. **Any chart consuming `dash` is `React.memo`-wrapped with its `useMemo`s keyed on the narrowest stable derivation** (`dash?.rounds`), never on `dash` itself. Two consequences worth stating:

- **Geometry keys on STRUCTURE, live values ride outside it.** The forest and dendrogram layouts memo on the served `tree` + `expanded` set, not `dash` identity — the tree only changes identity when a refetch lands, so unrelated `dash` mutations cannot re-flow it. The per-candidate overlays (`valueByKey` / `thetaByKey`) sit deliberately outside that memo, so a per-sample tick repaints node text without re-flowing geometry. Don't fold a live value into the structure.
- **Anything riding the chart's `options` memo must be a stable identity.** `onSelect` / `onGeometry` are `useCallback`s: an inline arrow there defeats the memo *and* forces a `chart.update()` on every poll tick.

## Brand identity / "About this unit"

`lib/brand.ts` is the single source of brand identity, each field `NEXT_PUBLIC_*`-overridable for whitelabel, feeding the Web App Manifest, the schema.org JSON-LD, and the Account → "About this unit" pane. Version is not duplicated here: it is server-owned (`APP_VERSION`), read live from `/api/v1/health`. **Never render a "verified" state while `BRAND.verification` says `self-declared`.**

## Testing posture

The gate is `python scripts/gate.py --web`, which is what CI's `webapp` job runs. Two things about it are this layer's: **the standalone typecheck is what makes `strict` real**, because `next build` alone does not hard-fail on every type error; and **Vitest is scoped to reader-side derivations** — pure data → data helpers — with display components left to smoke. Cycle fixtures live at `tests/fixtures/cycles/` ([`../tests/CLAUDE.md`](../tests/CLAUDE.md) § Frozen cycle fixtures); the one vitest loads is `l2_terminal/`, via `loadCycleFixture()`. Reach for a component-render test only for a regression class compile + smoke + the derivation tests cannot catch.

Smoke-test manually at `http://localhost:8001/` after a behavioural change. **Two states, two harnesses:** open `:8001` as-is for **anon**, or relaunch with `PROMPTPOTTER_AUTH=off` for **authed + live**, where `deps.py::resolve_identity` short-circuits to the CLI's resolver so every auth-gated read resolves to your real on-disk campaigns at zero spend. That is the cheap way to exercise the surface contract's `live` / `warming` clauses; reserve the Dex harness ([`../dev/oidc-local/`](../dev/oidc-local/)) for the one thing it cannot reach, the real Google OIDC round-trip.

## Build + run

**Two build modes (`next.config.ts`).** `npm run build` is the fast rebuild→reload preview: compile only. The React Compiler pass and full-bundle source maps sit behind `DEPLOY_BUILD=1` (the gate sets it; `npm run build:deploy` sets it for a shell), and **the maps serve the deployed box only** — `build_release.py::stage_webapp` strips them. Type-check and lint run inside neither, being separate gate checks.

**For visual work run `npm run dev`, not a rebuild per change.** It serves :3000 with Turbopack HMR and proxies `/api/*` to :8001 via `next.config.ts::rewrites`, which exists for this and nothing else; `output: "export"` means every `npm run build` prerenders every route instead. **The port picks your harness:** :3000 covers the anon and `PROMPTPOTTER_AUTH=off` surfaces, while the OIDC redirect is bound to :8001, so a real login round-trip needs the built app.

`out/` is what FastAPI mounts at `/`, so that is what a built change reaches. **A rebuild swaps every chunk hash under every tab already open on `:8001`**, surfacing only when one asks for a lazy route and 404s; `ui/ErrorBoundary` matches that and reloads once, so running the gate during a demo costs a flicker rather than a dead screen. The "once" is a `sessionStorage` stamp, and where storage is blocked the boundary reloads **nothing** and asks instead — an unbounded auto-reload is a reload LOOP, since the chunk is still missing next pass.

## Stack drift

Check `package.json` for what is installed rather than assuming a version. The block below is written and re-added by `next dev` — it says the rest.

<!-- BEGIN:nextjs-agent-rules -->

# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` (resolved from this file's directory; in monorepos the `next` package may not be visible from the repo root) before writing any code. Heed deprecation notices.

This block is written and re-added by `next dev` — verify at `node_modules/next/dist/server/lib/generate-agent-files.js`. Removing it from a diff only re-creates the uncommitted change; committing it with your work keeps the tree clean.

<!-- END:nextjs-agent-rules -->
