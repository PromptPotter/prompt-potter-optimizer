# webapp — CLAUDE.md

Next.js 16.2.7 + React 19.2.4 + TypeScript, static export at `out/` mounted at the domain root by FastAPI (the app owns `/`; the API is the carved-out `/api/v1` namespace). Read-only dashboard: polls `dashboard.json` every 2 s, lazy-fetches `round_NNNN.json` on drill-in.

## Load-bearing

The rules a change here breaks most often. Each names a section below; the section states it.

- Never compute a score, ordering or mask → § Scoring authority
- One source per data class → § Display-data sources
- One address, `viewedPath` → § Viewed identity — one address (CyclePath)
- `dashboard.json` IS the dashboard → § Folder-UI file contract (load-bearing — do not regress)
- Classify a failure, never bucket it → § Failure handling — classify, don't bucket
- No hand-rolled primitive → § Component conventions
- Barrel order IS the cascade → § Stylesheet organization (cascade order is load-bearing)

## Surface behavior contract

What each user-facing control **must do**, per auth/data state, lives in
[`../docs/specs/frontend-surface-contract.md`](../docs/specs/frontend-surface-contract.md).
This file owns *implementation* invariants; that one owns *behavior* — read it
before changing any control's states. Its invariants — `I1_state_complete`,
`I2_no_raw_transport`, `I3_affordance_honest`, `I4_auth_coherent`,
`I5_no_anon_noise` (anon fires no auth-gated request — don't fire it, not merely
"keep the console clean"), `I6_run_state_server_owned` (`run_phase` has ONE
server-owned answer; `IN_FLIGHT` in `lib/run-phase.ts`),
`I7_failure_traceable` (every failure identified, classified and traceable — see
§ Failure handling below) — are the bar for user-facing PRs. Drive the surface against it with the
two-harness recipe in § Testing posture below (anon = `:8001`; authed+live =
`PROMPTPOTTER_AUTH=off`).

## Scoring authority

**Every number this app renders was computed by the backend. The webapp never computes, re-derives, re-sorts, or defaults one.** Fitness is formula-relative (active / what-if / lens / replay) and mode-relative (`measured` subset vs `all`), so a locally-computed figure is not a stale version of the served one — it answers a *different question* in the same slot, with nothing on screen to tell the operator which. That asymmetry is why this is absolute rather than a performance preference. Served, and served only: `composite_fitness`, `accuracy`, `theta` / `cumulative_theta`, the hard-sample ordering, every lens / what-if / sample-set value, and the `is_winner` crown. Resolution chain: [`../docs/architecture.md`](../docs/architecture.md) §0.5.

Three shapes it bans, each of which arrives looking reasonable:

- **A local re-sort.** An ordering *is* a score. `hard_samples.json` is a served ranking, not a list to `.sort()` on whichever field is in hand.
- **A recomputed mask.** A lens / what-if / sample-set value comes down as a served overlay; deriving one client-side re-answers the question under the client's guess at the formula.
- **A fabricated default.** `noUncheckedIndexedAccess` is ON, so an index access (`arr[i]`, `rec[k]`, `match[n]`) types as possibly-`undefined` — **never `?? <default>` to silence it.** `edits[key] ?? r.value` is not `edits[key] !== undefined ? … : …` the moment an operator clears a field to `""`. Handle the miss (skip / early-return); `!` only where the line above proves presence. Without the flag a *correct* guard read as dead code (`sample-line.ts` guards regex group 3, which the pattern makes optional) while a *missing* one read as fine — the type system lied in both directions at once.

The pure helpers in `lib/derivations/*` are not an exception: they group, lay out and format served data, and introduce no number that was not already on the wire.

## Design — single source of truth

**Visual identity lives in [`../BRAND.md`](../BRAND.md), copy register in [`../VOICE.md`](../VOICE.md) — never introduce a parallel design spec, tokens doc or theme-decision file.** Extend those two in place if direction changes. They are the spec; `app/styles/foundation/{tokens,themes}.css` is the implementation, and every component reads `var(--…)`.

**Theme is audience, not a recolor.** Light / editorial-cobalt is the central register and the default; dark is DOOM/lava, opt-in for deep operator work, and swaps palette *and* density *and* framing together via `[data-theme="…"]` on `<html>`.

## Stylesheet organization (cascade order is load-bearing)

All CSS lives under `app/styles/`, imported by the ordered barrel `app/styles/index.css` — the only stylesheet `app/layout.tsx` imports. Lightning CSS inlines those `@import`s into one sheet **in barrel order**, so **the barrel order IS the cascade**: moving rules between files only stays correct if you preserve their relative order in it. There is no `globals.css`; do not reintroduce one. `foundation/` (whitelabel-safe skeleton) imports first, `domains/` (one file per feature, each with its co-located `@media`) after, and the two cross-cutting tail files last so their overrides win.

Component-specific rules belong in their domain file, or a co-located `*.module.css` once the component is refactored — that is the migration endgame. A new `@media` breakpoint trips `lib/__tests__/css-breakpoints.test.ts` unless the value is canonical: reuse a `--bp-*` token or update the allowlist deliberately. `glass.css` is operator-vetoed glassmorphism, preserved verbatim and never restyled.

State-class composition uses `cx()` (`lib/cx.ts`), not template strings: `cx("hs-cell", folded && "folded")`, never `` `hs-cell${folded ? " folded" : ""}` ``.

## Component conventions

- **Layout is three tiers, decided top-down.** Every file is exactly one of: a **primitive** (`ui/`, `forms/`), cross-surface **chrome** (`shell/` — anything rendering on more than one tab), or part of **one surface** (`chat/`, `dashboard/`, `verify/`, `tree/`=Files, `ingest/`). A file answering two is mis-filed; that overload is what this layer was untangled from. The worked example is `shell/node-surface/` — `NodeSurface` renders on **dashboard, ingest, chat and workflow**, and it sat under `dashboard/pipeline/` with its editors under `dashboard/control/`, so three surfaces reached into a fourth surface's folder to render their own node detail. The three files it composes (`NodeConfigEditor`, `PromptFieldsEditor`, `NodeOutputSchemaView`) are chrome by the same test, since they render wherever it does. Shared app state is a context in `lib/`, never a component; `app/page.tsx` mounts `shell/AppShell.tsx`, the composition root.
- **Inside a surface, organize by domain region — one axis, never by widget kind.** `dashboard/` is `samples/` · `scoring/` · `pipeline/` · `control/` · `layout/`: the §0 primitives the operator observes, so the names survive new views. Kind-buckets (`charts/`, `detail/`) collide with that axis and rot. A domain widget no single pane owns gets its own folder (`candidates/`, `eval/`, `workflow/`).
- **Never hand-roll a second modal / popover / dropdown / toggle-chip / segmented control / toolbar.** Reach for `components/ui/*`, or add it there with a `*.module.css` and an RTL test. Three surfaces each hand-rolled the same segmented control and had silently drifted on radius, divider weight and active fill before `SegmentedControl` existed.
- **A card header is ONE row** — `Toolbar` + `ToolbarSep` + `ToolbarSpacer`, controls `flex: 0 0 auto`, because a toolbar that shrinks its buttons to fit is lying about how much room it has. Rare controls fold into `Menu` behind a `⋯`, lit while any is active; **a control driving one region belongs beside that region**, not in the header. The labelled-button version ran four rows, taller than the chart it drove.
- **Two surfaces sharing no axis do not share a box.** The candidates card's geometry exists to hold the dendrogram on its bars; the lineage forest is a cladogram of *cycles* with no shared axis, so it is its own `ForestCard`. Nesting it stretched the card 363→946px and dragged the bars with it.
- **Operator data (IDs, hashes, payloads) stays selectable — never inject hidden characters**, and pair every state with a label or icon rather than color alone (HIT/MISS, pass/fail, live/stale). Accessibility here is positioning, not compliance (`BRAND.md`), so SVG `<g>`/nodes earn keyboard operability and `role` like any other control, and the 2 s poll honors `prefers-reduced-motion`.

## Brand identity / "About this unit"

`lib/brand.ts` is the single source of brand identity, each field `NEXT_PUBLIC_*`-overridable for whitelabel, feeding the Web App Manifest, the schema.org JSON-LD, and the Account → "About this unit" pane. **`publisher`** = the distributing host (overridable); **`provider`** = PromptPotter (fixed — it is the provenance fact). Version is not duplicated here: it is server-owned (`APP_VERSION`), read live from `/api/v1/health`. **Never render a "verified" state while `BRAND.verification` says `self-declared`.** **Where a distributor's values come from** — owned by [`../docs/developer/whitelabel.md`](../docs/developer/whitelabel.md); a new field here is unreachable until `deploy-linux/brand-env.sh` exports its `NEXT_PUBLIC_*`.

## Stack-drift warning

Next.js 16 has breaking changes from prior versions — APIs, file conventions, and config differ from earlier training data. Before touching framework-level code (config, routing, build, async-component shapes), read the relevant section of `node_modules/next/dist/docs/` and heed deprecation notices in the CLI output. React 19 likewise: `use()`, `Actions`, ref-as-prop, removed `forwardRef` boilerplate.

## Folder-UI file contract (load-bearing — do not regress)

`dashboard.json` and `round_NNNN.json` are not a cache, not a perf optimization, not "also written for debugging" — **they ARE the dashboard.** An operator opens them in a text editor mid-run and sees the current state; the browser is one consumer, the file tree an equal one. Three writer-side guarantees:

1. **Always on disk, always atomic-swap** (tmp + rename — never a partial write or torn read). `dashboard.json` exists after any ledger event in a cycle; sole writer `LiveDashboardView`. Sole writer of `rounds/round_NNNN.json` is `CampaignStore.save_round_file`, persisting `RoundResult.model_dump()` — the model **is** the round document. Its *audit twin* under `.runtime/cache/rounds/` shares the basename, is written by `AuditTrailView`, and the webapp does not fetch it.
2. **Settles within `_DASHBOARD_DEBOUNCE_S` of the last event.** The writer coalesces high-frequency bursts (sample-scored, token-usage, LLM-call progress) but converges behind real-time by no more than that constant. Plumbing: `view.py::_schedule_persist`.
3. **Immediate — no debounce — at round boundaries.** `PhaseRecord("round"|"origin", "complete"|"exit")` and `mark_stopped` flush synchronously via `view.py::_flush_pending_persist`, so a round's file is current before the next begins. **Do not remove these flushes, relax atomic-swap, or add a path that lets `dashboard.json` lag past a completed round.**

Before deferring or skipping any write, answer: *can an operator who alt-tabs to the file tree right now still see the truth?* If no, the change is wrong.

**The rule above is the writer side; the recipe is [`../docs/developer/adding-a-surface.md`](../docs/developer/adding-a-surface.md) § 3** (a dashboard / view field: `view_models.py` → `ingress.py` → renderers). A field on the ROUND document is nearly free — declare it on `RoundResult` and it reaches disk and every reader of the round file — but the webapp reads `dashboard.json`, not round files, so reaching a panel means mirroring it too. **Per-candidate facts go on `RoundSummaryCandidate` and cost nothing further** (the projection's include-set is derived from `model_fields`); only a genuinely per-ROUND fact needs `RoundSummary` + a hand-written line in `round_summary.py`. Surfacing a new value in the webapp starts there, not in a component — a panel reading a field no writer sets is the half-wiring this contract exists to prevent, and its mirror image is just as real: `RoundSummary.improved` / `electable_count` are served today and rendered by nothing.

## Display-data sources

Two on-disk surfaces back the dashboard. Read from the right one:

- **`dashboard.json`** (polled every 2 s by `lib/poll.tsx` → `useCycleStream()`) — in-flight `current_round` and the `rounds[]` array of completed-round summaries (**round 0 = origin**, a one-candidate round labelled "C0"; there is no separate origin block). **Sole source** for the FitnessChart, TrendChart, TopStrip sparkline. Don't stitch in `round_NNNN.json` for chart data.
- **The `ForestCard` and the sidebar are NOT `dashboard.json` — they render `/tree`, THE served genealogy, and the webapp derives no lineage.** One recursive shape, `course → candidate → course`, alternating at any depth, rooted at a COURSE: an L4 inner run is a course hanging off the candidate it measured, so L5+ needs no new tier. Owner `store/lineage_views.py`, read through **one** client seam (`lib/lineage.tsx`) over one keyed store. Identity (`id`, `label`) and the round-CLOSE facts (`is_winner`, `theta`, `cumulative_theta`) both fold from the cycle's own LEDGER — never read back out of `dashboard.json`, which is a projection. A round crowns nobody when it never closed OR when a correction retired its winner, so `pickWinner` has no first-candidate fallback and must not grow one. A HELD round and one that never closed both read `is_winner: false` and are **not** distinguished today; that distinction returns WITH the surface that draws it.
  - **A fork is not a node.** Its candidates sit on the parent's ONE timeline, renumbered there (`C{round}.{n}` is a course's private counter — every course mints a `C1.1`), wearing the ⑂ stamp and the fork's own `path`. Its `C0` is a replay and merges into the candidate it was cut from. Reach a node with `nodeAt` / `candidatesAtPath`, never a bare cycle_id (inner ids collide across sandboxes) and never a label.
  - **A cut that moved the line means the BRANCH answers for the campaign** — the course row's `run_phase` / `state` / `best_accuracy` are served from it, because the parent's own row describes a retirement (`rebased_to_fork`, stamped at the cut) while the cycle that IS running owns no row of its own. `supersede` AND `equivalent` both move it; only an `offshoot` leaves the parent running. That is the whole reason the row can show a green ● at all: a fork is not a node, so without this nothing in the sidebar carries `running`.
  - **A supersede leaves the retired side on the timeline, wearing `superseded_by`.** Split with `splitRetired` into one collapsed row per branch (`ForestRows.tsx::RetiredGroupRow`) — left flat, a round of three reads as a round of six. Both sides keep their labels, so **one label appears twice in a round, at most once live**, and after a REPAIR the two sides also share a `candidate_id`: a repair re-measures, it does not re-mint. **So `id` alone is NOT a key — the address is `(path, id)`, and their paths differ.** Key rows on `nodeKeyOf`, never `node.id`. The RUNS are served on the live node only (they measured the individual, not either measurement of it), so an id-keyed lookup must skip `superseded_by` rather than rely on iteration order.
  - **⑂ marks an OFFSHOOT only.** A supersede's contributions arrive with `course_kind` / `fork_direction` cleared, because the branch IS the line — stamping it would tag the whole live timeline as a detour. If you see ⑂ on a row, it hangs off a line that is still running somewhere else.
  - **Two axes, kept apart:** NAVIGATION is `workspace::viewedPath` + `viewedCandidateId` ("whose children do the bars plot"), written ONLY by the sidebar; INSPECTION is `SelectionContext.candidate` ("which bar is lit"), written by a bar click. **A bar click never navigates** — one slot for both made the chart its own input, re-plotting under the cursor that clicked it.
  - **The bars are the children of the viewed node**, straight off the tree. `dash` keeps exactly one job here: the candidate being scored right now (the ledger mints before it measures, so a mid-scoring bar is not yet in the tree).
- **`rounds/round_NNNN.json`** (lazy, fetched via `lib/hooks/useRoundFile.ts`) — the round document, i.e. a serialized `RoundResult`: per-sample `results`, `all_candidate_results`, `candidate_scores`, the derived `scoreboard`, and the closing `opt_search_point` / `health`. Reach for it only when the operator drills into a specific round (FreqChart distribution, ScoringInspector composite/hits).
- **The AUDIT TWIN, `.runtime/cache/rounds/round_NNNN.json`** (lazy, via `useRoundAudit`) — same basename, different tree, written by `AuditTrailView`. The per-node LLM I/O lives ONLY here; the round document carries no `nodes` block at all. `lib/hooks/useRoundNodes.ts` is the single resolver that picks between it and the live `dashboard.json::current_round.nodes`, and both the optimizer canvas and its node detail read through it — splitting that switch is what let them disagree about which round they were showing.

If you find yourself adding a "merge in-flight with historical" or "fall back to round-file when dashboard hasn't written X yet" branch, you're re-introducing the stitch pattern the display-source unification collapsed. Pick one source per data class.

## A wire shape is GENERATED — never hand-declared

**Every response type comes from `lib/api/types.generated.ts`**, emitted by `scripts/build_ts_types.py` off the Pydantic model. Adding a field server-side reaches the browser by regeneration; hand-writing the interface instead is how a type drifts fields behind its model with every gate green. To add a shape: register the model in that script's `EXPORTED_MODELS`, regenerate, re-export it from `lib/api/types.ts`.

**Two allowed escapes, both narrow.** A route with no `response_model` has nothing to generate from, so its shape stays hand-written *and says so* (`reads.ts::HealthResponse`, and `LifecycleFilter`, whose server-side member set genuinely differs). And a narrow alias is **derived**, never re-declared — `export type ActivityWindow = ActivityResponse["window"]` reads the closed set back off the generated interface, so a member added in Python arrives here. Re-typing the members is the thing this rule forbids.

**A closed set belongs on the server.** `ActivityResponse.window` and `BackendHealthResponse.status` were bare `str` in Python while the browser narrowed them to unions — the only named version of each lived here and nothing could catch a rename. They are `Literal`s server-side now; if you find a union declared only in TypeScript, that is the bug.

**`lib/api` is five write modules, not one.** `errors.ts` (`throwApiError`, `mintIdempotencyKey`, `IngestApiError` — it lands first; the other two throw through it) · `commands.ts` (the closed-set `/commands/{kind}` highway) · `ingest.ts` (the writes that CREATE what a command later addresses) · `draft-types.ts` (pure wire types, and the single owner of `lib/`'s one import from `components/`) · `account.ts` (per-user identity, which is why it is not a command). `reads.ts` stays whole: it is one job.

## Viewed identity — one address (CyclePath)

**"What am I looking at?" has ONE answer — `viewedPath`, and no surface may keep a second.** It is a **`CyclePath`** (`lib/ids.ts`): the chain of `(campaign, cycle)` hops from top-level root to leaf, mirroring the engine's re-entrant `.inner/` sandbox — 1 hop for a top-level cycle, 2 for an L4 inner loop, deeper for L5+. `lib/workspace.tsx` owns it as `pinnedPath` + `following`, with `viewedPath` derived. Drilling in is `drillInto(campaignId, cycleId)` (the workspace appends the hop; a cell bar, an L4 panel row and the hard-samples pointer all just name a run), backing out `backToOuter()`.

**Everything that DISPLAYS re-roots to the LEAF hop; only conversation identity stays on the ROOT.** Leaf: the dashboard stream, connector/pipeline hero, hard-samples panes, chat activity feed, and the selection axes (`SelectionProvider` keyed on `leafCycleId`). Root: `sessionId`, ingest/compose-new-campaign, and Files — so drilling in never mints a thread or moves ingest off the outer conversation. Leaf ids derive at the consumer (`pathLeaf`); `leafIsL4` is likewise the workspace's single answer rather than a per-surface lookup.

**Deep-link carries the WHOLE address — `?path=` plus `?cand=`** (malformed → falls back to following). Both halves, because the address is `(path, candidateId)`: encoding only the path made a reload drop the parked node, which for a FORK names nothing at all (a fork is not a node) and left the bars blank. Same reason DESELECTING a candidate returns to the course whose TIMELINE it renders on, never to its own path with a null candidate.

## Polling shape

- `GET /api/campaigns/{id}/cycles/{cid}/dashboard` supports `If-Modified-Since` → `304 Not Modified`. Client (`lib/poll.tsx`, `lastModifiedRef`) tracks the latest `Last-Modified` per `unitKey` (the encoded `CyclePath`) and skips `setState` on 304. One fetch (`fetchDashboardByPath`) serves any depth: an inner descendant rides a `?descend=<hops>` query the server walks into each hop's `.inner/` sandbox; at depth 1 the URL is byte-identical to a plain per-cycle read, so 304 semantics are unchanged.
- When `dashboard.json` does not yet exist (fresh campaign before origin completes), the route returns `{ warming_up: true, campaign_id, cycle_id, phase_hint: "origin" }` with HTTP 200 and `Last-Modified` from the session dir mtime. Client recognises `warming_up === true` and renders a friendly placeholder ("Origin running") rather than treating the cycle as offline.
- `unitKey` change (any hop of the path) resets `lastModifiedRef` in the render-phase guard. Required — without it, switching campaigns (or drilling into/out of an inner loop) leaves stale `If-Modified-Since` on the wire.
- **ONE tree per campaign, fetched ONCE and ADDRESSED into — never a second genealogy read.** `/tree` (`lib/lineage.tsx`) is conditional on a weak **ETag** whose validator covers the `lens`/`samples` mask as well as subtree mtime, which is what lets a MASKED read 304 (`Last-Modified` cannot express query-dependence, so masked reads used to be rebuilt on every poll). The server's recursion already reaches every fork and inner run, so a leaf surface uses `nodeAt` / `candidatesAtPath` rather than fetching its own. Two objections used to force a second read and neither survives: a masked body is a strict **superset** (the overlay writes only `divergence` / `divergent` / `lens_value` / `sample_set_*` and never touches what the sidebar reads), and labels DO join — on **`course_label`**, the minting course's position, which is carried through the timeline renumber for exactly this. The sandbox's old `/campaigns`+`/cycles` pair disagreed with the tree about a fork's runs (`C1.1` vs the renumbered `C1.4`); only the tree can do that renumber. Ask the tree.
- **`/ray` is the CHRONOLOGY, not a second tree** (`lib/hooks/useTimeRay.ts`, ETag). The tree answers *what descends from what*; the ray answers *what happened when* — a fork's records interleave with its parent's, the only way to see it ran concurrently. Server-side they share one family walk, so they cannot disagree about which cycles a campaign holds. The HEAD window is polled, an OLDER one fetched once and revalidated like any other; the server does not claim deep windows immutable.
- **Exactly one live channel (`useCycleEvents`) and one history channel (`/ray`).** So the ray gets no SSE join and the tail no `since=` — either would be a redundant mechanism, and the ledger mtime bumps on every append, so the conditional poll surfaces a new event within one tick anyway.
- **`llm_call_progress` rides the ray on purpose.** The client uses a bare heartbeat to prove the process was alive across a silent stretch and then drops it from the rendered steps (`projectionToActivity` already returns null for one). Stop sending them and every heartbeated 120 s backend query grows a spurious gap marker. The coupling is commented at `format.ts::fmtGap`, `derivations/time-ray.ts` and `store/family_ray_views.py`.

## Failure handling — classify, don't bucket

**A bare `catch` is the bug.** Every read-path failure is classified once at the transport
seam by `failureKind(err)` (`lib/api/client.ts`) into `transient | auth | gone | denied |
invalid`, and callers branch on that, never on a status literal. `transient` is the safe
default — 5xx, network and parse errors all land there — because the directions are
asymmetric: mistaking transient for `gone` destroys the operator's view, the reverse costs
one retry.

**`gone` (404) is terminal and must stop the poll**, and it is ordinary rather than exotic:
`delete-campaign` removes the campaign you are looking at, the reaper deletes an `.inner/`
sandbox with no user action at all, a store reset invalidates every bookmark. One owner
acts — `workspace.tsx::reportAddressGone` unpins and resumes following — and **only the
address's own authoritative read may report it**, because an L4 inner hop is absent from
`/cycles` and an archived campaign from the `active` filter, so list membership would kill
two live addresses. **Archived is not gone.**

The detector is the dashboard poll, the one read that speaks for the address: its route
answers `warming_up` at 200 while a cycle exists without a dashboard, so a 404 there means
the cycle dir itself is gone. It confirms over `GONE_CONFIRM_LIMIT` consecutive misses (a
single 404 is a mint race). Everyone else reacts locally without voting — notably
`useCycleEvents`, whose `EventSource` **cannot see a status** and would auto-reconnect a
404 forever, so it subscribes only while the address is live.

**Every failure is reportable, and carries ids only.** `lib/diagnostics.ts` records each
with the `error_id` the API stamps on its envelope, localStorage-backed so it survives the
reload the bug provokes; each `error_id` greps the server log. **Never** measurements or
prompt text — the same rule `view-memory.tsx` states.

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

**Nothing in that record is a measurement, and that is a hard rule** — ids, flags and UI keys only. It is why the INSPECTION axis (`SelectionContext.candidate`) is NOT remembered: it carries `accuracy` and `is_winner`, so a restored value would claim a candidate won a round it may since have lost. The NAVIGATION axis (`viewedPath` + `viewedCandidateId`) is remembered instead — every field in it is an id, and it merely re-parks the tree. A lens / what-if / sample-set mask is excluded for the same reason: restoring one silently means the operator reads masked numbers as the record.

## Render-cost guards (do not regress)

Per-poll re-renders cascade through the chart tree by default. **Any chart consuming `dash` is `React.memo`-wrapped with its `useMemo`s keyed on the narrowest stable derivation** (`dash?.rounds`), never on `dash` itself. Two consequences worth stating:

- **Geometry keys on STRUCTURE, live values ride outside it.** The forest and dendrogram layouts memo on the served `tree` + `expanded` set, not `dash` identity — the tree only changes identity when a refetch lands, so unrelated `dash` mutations cannot re-flow it. The per-candidate overlays (`valueByKey` / `thetaByKey`) sit deliberately outside that memo, so a per-sample tick repaints node text without re-flowing geometry. Don't fold a live value into the structure.
- **Anything riding the chart's `options` memo must be a stable identity.** `onSelect` / `onGeometry` are `useCallback`s: an inline arrow there defeats the memo *and* forces a `chart.update()` on every poll tick.

## Testing posture

The webapp gate is **compile-time + smoke + a small Vitest scope**. Run it with `python scripts/gate.py --web`, which is also what CI's `webapp` job runs — the check list lives there and nowhere else, so what you run locally is what reds `main`:

- `npm run lint` — ESLint.
- `npx tsc --noEmit` — full strict typecheck (`next build` alone does not hard-fail on every type error, so this line is what makes `strict` real). `noUncheckedIndexedAccess` is ON; what you may do with the possibly-`undefined` it surfaces is § Scoring authority.
- `npm run test` — Vitest, scoped to `lib/**/__tests__/` + `components/**/__tests__/` per `webapp/vitest.config.ts`. Reader-side derivations only (pure data → data helpers); display components stay covered by smoke. Cycle fixtures live at `tests/fixtures/cycles/` — recipe at [`docs/developer/cycle-fixtures.md`](../docs/developer/cycle-fixtures.md).
- `npm run build` — the static export must succeed.
- Manual smoke at `http://localhost:8001/` after a behavioural change. Two states, two harnesses:
  - **anon** — open `:8001` as-is (no flag); drives the public-preview surface.
  - **authed + live** — relaunch with `PROMPTPOTTER_AUTH=off`: `deps.py::resolve_identity` short-circuits to the CLI's resolver, so `/auth/me` returns 200 and every auth-gated read resolves to your **real on-disk campaigns** (zero spend, pure reads). The cheap way to exercise the contract's `live`/`warming` clauses — no Docker, no fixtures. Reserve the Dex harness ([`dev/oidc-local/`](../dev/oidc-local/), [`docs/developer/local-oidc.md`](../docs/developer/local-oidc.md)) for the one thing it cannot reach: the real Google OIDC login round-trip.

Reach for a component-render test only for a regression class compile + smoke + the derivation tests cannot catch; today's bug classes are reader-side and ride the existing Vitest scope.

## Build + run

```bash
cd webapp
npm install                          # one-time
npm run build                        # FAST local-preview export → webapp/out/, served at the root by FastAPI
npm run lint
npx tsc --noEmit
```

**Two build modes (`next.config.ts`).** `npm run build` is the operator's fast rebuild→reload preview: compile only. The React Compiler pass and full-bundle source maps are deploy-artifact concerns behind `DEPLOY_BUILD=1`, exposed as **`npm run build:deploy`** — what CI validates and the deploy box ships. Type-check and lint run inside *neither*; they are the dedicated CI gates above, so repeating them in `next build` is duplication. `build:deploy` uses bash inline-env; to preview compiler-on behaviour from Windows use `$env:DEPLOY_BUILD=1; npm run build`.

`out/` is the route mounted by FastAPI (`StaticFiles(html=True)` at `/`). After any source change, rebuild and hard-reload the browser. Dev mode (`npm run dev`) proxies `/api/*` to `http://127.0.0.1:8001` via `next.config.ts::rewrites` — production has no proxy (same FastAPI origin).
