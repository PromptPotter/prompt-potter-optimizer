# webapp — CLAUDE.md

Next.js 16.2.7 + React 19.2.4 + TypeScript, static export at `out/` mounted at the domain root by FastAPI (the app owns `/`; the API is the carved-out `/api/v1` namespace). Read-only dashboard: polls `dashboard.json` every 2 s, lazy-fetches `round_NNNN.json` on drill-in.

## Surface behavior contract

What each user-facing control **must do**, per auth/data state, lives in
[`../docs/specs/frontend-surface-contract.md`](../docs/specs/frontend-surface-contract.md).
This file owns *implementation* invariants; that one owns *behavior* — read it
before changing any control's states. Its **six** invariants — `I1_state_complete`,
`I2_no_raw_transport`, `I3_affordance_honest`, `I4_auth_coherent`,
`I5_no_anon_noise` (anon fires no auth-gated request — don't fire it, not merely
"keep the console clean"), `I6_run_state_server_owned` (`run_phase` has ONE
server-owned answer; `IN_FLIGHT_PHASES` in `lib/run-phase.ts`) — are the bar for
user-facing PRs. Drive the surface against it with the
two-harness recipe in § Testing posture below (anon = `:8001`; authed+live =
`PROMPTPOTTER_AUTH=off`).

## Design — single source of truth

**Visual identity (palette, theme framing) lives in [`../BRAND.md`](../BRAND.md); copy register lives in [`../VOICE.md`](../VOICE.md).** Read them before touching styles, brand assets, or any user-visible copy. They are the spec; `app/styles/foundation/tokens.css` + `app/styles/foundation/themes.css` are the canonical token implementation (dark `:root` defaults + the `[data-theme="light"]` block); every component reads `var(--…)`. Do not introduce a parallel design spec, design-tokens file, or theme-decision doc — extend `BRAND.md`/`VOICE.md` in place if direction changes.

The central register is **light / editorial-cobalt** (cobalt `#090C9B` accent, oxblood `#55251D` depth, taupe `#C5AFA4` tint, olive `#696047` muted, warm-bone `#F5F1EA` paper — no orange). The webapp loads in light by default (`app/layout.tsx` pre-paint script, `var t = s || 'light'`). Dark is **DOOM/lava**, opt-in for deep operator work — a distinct register, not a recolor; orange lives only there. Theme change swaps palette + density + framing together via `[data-theme="…"]` on `<html>`.

## Stylesheet organization (cascade order is load-bearing)

All CSS lives under `app/styles/`, imported by the ordered barrel `app/styles/index.css` (the only stylesheet `app/layout.tsx` imports). Lightning CSS inlines the `@import`s at build into one sheet **in barrel order** — so the barrel order *is* the cascade. There is no `globals.css`; do not reintroduce one.

- **`foundation/`** — the portable, whitelabel-safe skeleton, imported first: `tokens.css` (all `--color-*` / `--font-*` / `--bp-*` / radius / touch / safe-area vars), `themes.css` (dark `:root` + `[data-theme="light"]`), `base.css` (reset, `box-sizing`, `:focus-visible`, body type). Plus two cross-cutting tail files imported last so their overrides win: `reduced-motion.css` and `responsive.css` (the `≤640`/`≤380` blocks, sidebar drawer, `pointer:coarse` 44px floor, rotate prompt).
- **`domains/`** — one file per feature (`shell`, `dashboard`, `chat`, `workflow`, `hard-samples`, `account`, …), each carrying its own co-located `@media`. `glass.css` is the operator-vetoed glassmorphism — preserved verbatim, never restyled.

**Editing rules.** Component-specific rules belong in their domain file (or, once a component is refactored, its co-located `*.module.css` — that is the migration endgame). Adding a new `@media` breakpoint trips `lib/__tests__/css-breakpoints.test.ts` unless the value is canonical — reuse a `--bp-*` token or update the allowlist deliberately. When you move rules between files, the cascade only stays correct if you preserve their relative order in the barrel.

State-class composition uses `cx()` (`lib/cx.ts`), not template strings: `cx("hs-cell", folded && "folded")`, never `` `hs-cell${folded ? " folded" : ""}` ``.

## Component conventions

- **Layout is three tiers, decided top-down.** Every file answers exactly one of: is it a **primitive** (`ui/`, `forms/` — kind-named by design), a piece of cross-surface **chrome** (`shell/` — Topbar, Sidebar, Console-adjacent, `AppShell`, `CriticalAlertBanner`, `CyclePicker`: anything that renders on more than one tab), or part of **one surface** (`chat/`, `dashboard/`, `verify/`, `tree/`=Files, `ingest/`)? A file that answers two — a dashboard view that's also the app root, a banner filed under `dashboard/` but shown on every tab — is mis-filed; that overload is what this layer was untangled from. Shared app state is a context in `lib/` (`SelectionContext`, `auth-context`, `workspace`, `poll`), never a component. `app/page.tsx` mounts `components/shell/AppShell.tsx` (the composition root — owns the `Tab` type and mounts every pane).
- **Inside a surface, organize by domain region — one axis, never by widget kind.** `dashboard/` is `samples/` · `scoring/` · `pipeline/` · `control/` · `layout/` — the §0 primitives the operator observes, so the names stay stable as milestones add views (M11 viz → `scoring/`, M12 multi-connector → `pipeline/`). Do **not** add kind-buckets (`charts/`, `detail/`); they collide with the region axis and rot. Cross-surface domain widgets that aren't owned by one pane stay as their own feature folders (`candidates/`, `eval/`, `workflow/`) — `candidates/` is the one card the Dashboard and the Chat job dropdown both mount, which is why it is not filed under `dashboard/`.
- **Anatomy: fetch lives in a hook, not a component.** A component that fetches *and* renders *and* owns selection/scroll state is the prototype smell this layer is moving off of. Put data access in a `lib/hooks/use*.ts` hook (peers: `useRoundFile`, `useDatasetPreview`, `useDashboard`); keep components presentational where possible. Reuse the pure `lib/derivations/*` for data→data shaping rather than re-deriving inline.
- **Primitive-first.** Do not hand-roll another modal / popover / dropdown / button / **toggle-chip / segmented control / toolbar**. Reach for `components/ui/*`; if the primitive doesn't exist yet, add it there (with a `*.module.css` and an RTL test) so the next caller inherits it. The toggle shapes are named explicitly because they were the ones that got missed: three surfaces each hand-rolled the same segmented control and had silently drifted apart on radius, divider weight and the active fill before `SegmentedControl` / `Chip` existed. A one-off toggle is how a design system rots.
- **A card header is ONE row.** `Toolbar` + `ToolbarSep` + `ToolbarSpacer`, with icon-sized controls (`Chip icon`, `SegmentedControl` with icon labels) named by `title` + `ariaLabel`. Controls are `flex: 0 0 auto` — a toolbar that shrinks its buttons to fit is lying about how much room it has. Only what's read on every glance stays in the header; **rare controls fold into the `Menu` primitive behind a `⋯`** (light the trigger while any of them is active, so nothing runs silently), and **a control that drives one region belongs beside that region, not in the header** — the forest toggle sits with the dendrogram, not in the toolbar. The labelled-button version of that header ran to four rows, taller than the chart it drove.
- **Two surfaces that share no axis do not share a box.** The candidates card's whole geometry exists to hold the dendrogram on its bars, so anything nested in it is bound to the chart's width. The lineage forest is a cladogram of *cycles* — no shared axis — so it is its own card (`ForestCard`), opened by a toggle rather than swapped in. Nesting it stretched the card from 363→946px and dragged the bars with it.
- **Accessibility is positioning, not compliance** (`BRAND.md`). Every interactive element — including SVG `<g>`/nodes — needs keyboard operability + `role`; dialogs trap focus + restore on close + close on ESC; state pairs color with a label or icon (HIT/MISS, pass/fail, live/stale), never color alone; focus rings stay visible (the `:focus-visible` accent outline); honor `prefers-reduced-motion` on the 2 s poll. Operator data (IDs, hashes, payloads) stays selectable — never inject hidden characters.

## Brand identity / "About this unit"

`lib/brand.ts` is the single source of brand identity (name, publisher vs. provider, URLs), each `NEXT_PUBLIC_*`-overridable for whitelabel. It feeds three real surfaces — the Web App Manifest (`app/manifest.ts`), the schema.org `SoftwareApplication` JSON-LD in `<head>` (`app/layout.tsx`), and the Account → "About this unit" pane (`components/account/AboutUnit.tsx`). **`publisher`** = the distributing host (overridable); **`provider`** = PromptPotter (fixed — it's the provenance fact). The app **version** is not duplicated here: it's server-owned (`APP_VERSION`) and read live from `/api/v1/health`. Provenance is `self-declared` until a signed credential lands — never render a "verified" state while `BRAND.verification` says otherwise.

## Stack-drift warning

Next.js 16 has breaking changes from prior versions — APIs, file conventions, and config differ from earlier training data. Before touching framework-level code (config, routing, build, async-component shapes), read the relevant section of `node_modules/next/dist/docs/` and heed deprecation notices in the CLI output. React 19 likewise: `use()`, `Actions`, ref-as-prop, removed `forwardRef` boilerplate.

## Folder-UI file contract (load-bearing — do not regress)

`dashboard.json` and `round_NNNN.json` are the canonical UI surface of PromptPotter. They are not a cache, not a perf optimization, not "also written for debugging" — **they ARE the dashboard.** An operator can open `.promptpotter/campaigns/*/cycles/*/dashboard.json` and `rounds/round_NNNN.json` in any text editor at any moment and see the current state of the run. The browser UI is one consumer; the file tree is another, equal consumer.

Three guarantees the writer side MUST hold:

1. **Always on disk.** `dashboard.json` exists after any ledger event in a cycle. Sole writer: `LiveDashboardView` (`promptpotter/infrastructure/projections/live_dashboard/view.py`). Sole writer of `rounds/round_NNNN.json`: `CampaignStore.save_round_file`, which persists `RoundResult.model_dump()` — the model **is** the round document. (The `.runtime/cache/rounds/round_NNNN.json` *audit twin* is a different file with the same basename, written by `AuditTrailView`; the webapp does not fetch it.) All use atomic-swap (tmp + rename) — never partial-write, never torn read.
2. **Settles within `_DASHBOARD_DEBOUNCE_S` (0.25 s) of the last event.** The writer debounces high-frequency events (sample-scored, token-usage, LLM-call progress) to coalesce bursts, but converges to current state at most 250 ms behind real-time. Constant `_DASHBOARD_DEBOUNCE_S`; flush plumbing at `view.py::_schedule_persist`.
3. **Immediate (no debounce) at round boundaries.** `PhaseRecord("round"|"origin", "complete"|"exit")` and `mark_stopped` flush synchronously via `view.py::_flush_pending_persist`. When a round ends, its file is current before the next round begins. **Do not remove these flushes.** Do not relax atomic-swap. Do not introduce a path that lets `dashboard.json` lag past a completed round.

If a future change wants to defer or skip a write, the question to answer first is: *can an operator who alt-tabs to the file tree right now still see the truth?* If no, the change is wrong.

**The rule above is the writer side; the recipe is [`../docs/developer/adding-a-surface.md`](../docs/developer/adding-a-surface.md) § 3** (a dashboard / view field: `view_models.py` → `ingress.py` → renderers). A field on the ROUND document is nearly free — declare it on `RoundResult` and it reaches disk and every reader of the round file — but the webapp reads `dashboard.json`, not round files, so reaching a panel also means mirroring it onto `RoundSummary` + one line in `projections/live_dashboard/round_summary.py`. Surfacing a new value in the webapp starts there, not in a component — a panel reading a field no writer sets is the half-wiring this contract exists to prevent.

## Display-data sources

Two on-disk surfaces back the dashboard. Read from the right one:

- **`dashboard.json`** (polled every 2 s by `lib/poll.tsx` → `useCycleStream()`) — in-flight `current_round` and the `rounds[]` array of completed-round summaries (**round 0 = origin**, a one-candidate round labelled "C0"; there is no separate origin block). **Sole source** for the FitnessChart, TrendChart, TopStrip sparkline. Don't stitch in `round_NNNN.json` for chart data.
- **The `ForestCard` and the sidebar are NOT `dashboard.json` — they render `/tree`, THE served genealogy.** One recursive shape, `course → candidate → course`, alternating at any depth: an L4 inner run is a course hanging off the candidate it measured, so L5+ needs no new tier. Rooted at a COURSE, not a campaign. Owner: `store/lineage_views.py`; read by `lib/lineage-overlay.tsx` (the viewed campaign, carrying the what-if / lens / sample-set masks) and `lib/hooks/useCampaignTree.ts` (the sidebar, deliberately mask-free — it must show what a run did, not what a mask says it would have done); both walk the one `derivations/lineage-candidates.ts`. **The webapp derives no lineage.** Identity (`id`, `label`) is minted on the ledger and served; the round-CLOSE facts (`is_winner`, `theta`, `cumulative_accuracy`) come from `dashboard.json::rounds[]`, which is the only place they exist. `round_closed` separates a HELD round from one that never finished — collapsing them into one `is_winner: false` promotes a never-closed round's lone candidate to a fake winner.
  - **A fork is not a node.** Its candidates sit on the parent's ONE timeline, renumbered there (`C{round}.{n}` is a course's private counter — every course mints a `C1.1`), wearing the ⑂ stamp and the fork's own `path`. Its `C0` is a replay and merges into the candidate it was cut from.
  - **The address is `(path, candidateId)` — `nodeAt`, read off the node.** Never a bare cycle_id (inner ids collide across sandboxes), never a label. Because a fork-contributed candidate carries the fork's `path`, selecting it re-roots the dashboard / samples / inspector onto that fork for free.
  - **Two axes, and keeping them apart is load-bearing:** NAVIGATION is `workspace::viewedPath` + `viewedCandidateId` ("whose children do the bars plot"), written ONLY by the sidebar; INSPECTION is `SelectionContext.candidate` ("which bar is lit"), written by a bar click. One slot for both made the chart its own input — it re-plotted under the cursor that clicked it. **A bar click never navigates.**
  - **The bars are the children of the viewed node**, straight off the tree. `dash` keeps exactly one job here: the candidate being scored right now (the ledger mints before it measures, so a mid-scoring bar is not in the tree). One source per data class.
  - **Separate cards on purpose:** everything in the candidates card is bound to the chart's box (that's what keeps the dendrogram on its bars); the forest shares no axis with it.
- **`rounds/round_NNNN.json`** (lazy, fetched via `lib/hooks/useRoundFile.ts`) — the round document, i.e. a serialized `RoundResult`: per-sample `results`, `all_candidate_results`, `candidate_scores`, the derived `scoreboard`, and the closing `opt_search_point` / `health`. Reach for it only when the operator drills into a specific round (FreqChart distribution, ScoringInspector composite/hits).
- **The AUDIT TWIN, `.runtime/cache/rounds/round_NNNN.json`** (lazy, via `useRoundAudit`) — same basename, different tree, written by `AuditTrailView`. The per-node LLM I/O lives ONLY here; the round document carries no `nodes` block at all. `lib/hooks/useRoundNodes.ts` is the single resolver that picks between it and the live `dashboard.json::current_round.nodes`, and both the optimizer canvas and its node detail read through it — splitting that switch is what let them disagree about which round they were showing.

If you find yourself adding a "merge in-flight with historical" or "fall back to round-file when dashboard hasn't written X yet" branch, you're re-introducing the stitch pattern the display-source unification collapsed. Pick one source per data class.

## Viewed identity — one address (CyclePath)

"What am I looking at?" has ONE answer: `viewedPath`, a **`CyclePath`** (`lib/ids.ts`) — the chain of `(campaign, cycle)` hops from the top-level root to the leaf. A top-level cycle is a 1-hop path; an L4 inner loop is a 2-hop path `[outer, inner]`; L5+ nests deeper (this mirrors the engine's re-entrant `.inner/<cycle_id>` sandbox). `lib/workspace.tsx` owns it: state is `pinnedPath` + `following`; `viewedPath` is derived (`following ? [active 1-hop] : pinnedPath`). Everything that DISPLAYS what you're looking at re-roots to the **LEAF hop** — the dashboard stream, the connector/pipeline hero, the hard-samples panes, the chat live activity feed (`useCycleEvents` + its gate-decision control, which is derived from the leaf `dash`), AND the selection axes (`SelectionProvider` is keyed on `leafCycleId`; it scopes the inspector / samples / `round_NNNN.json`, which all read the leaf). Drilling into an inner loop shows THAT inner cycle's telemetry everywhere, coherently. Only the operator's **conversation identity** stays on the **ROOT hop** — the `sessionId`, the ingest/compose-new-campaign flow, and Files (the `campaignId`/`cycleId` exports remain the root) — so drilling in never mints a new thread or moves ingest off the outer conversation. The leaf ids are derived from `viewedPath` at the consumer (`pathLeaf`), never a second identity variable. The connector/samples read the leaf's dataset (and the ETA chip the leaf's start time) via `useLeafCycleIndex`; the hard-samples slice + the feed both ride `?descend=` into the inner sandbox. There is no separate inner-focus axis; drilling in = `drillInto(campaignId, cycleId)` (the workspace appends the hop to `viewedPath` — a cell bar, an L4 panel row and the hard-samples pointer all name a run and mean "descend into it"; none of them builds the address), backing out = `backToOuter()`. `leafIsL4` is likewise the workspace's — the leaf campaign's `backend_type` decides whether a course's samples are inner campaigns, and the surfaces branch on that one answer rather than each re-running the lookup. Deep-link is one `?path=` param (encoded CyclePath; malformed → falls back to following). Don't reintroduce a second identity variable or a per-surface "unit" — every surface derives from `viewedPath`.

## Polling shape

- `GET /api/campaigns/{id}/cycles/{cid}/dashboard` supports `If-Modified-Since` → `304 Not Modified`. Client (`lib/poll.tsx`, `lastModifiedRef`) tracks the latest `Last-Modified` per `unitKey` (the encoded `CyclePath`) and skips `setState` on 304. One fetch (`fetchDashboardByPath`) serves any depth: an inner descendant rides a `?descend=<hops>` query the server walks into each hop's `.inner/` sandbox; at depth 1 the URL is byte-identical to a plain per-cycle read, so 304 semantics are unchanged.
- When `dashboard.json` does not yet exist (fresh campaign before origin completes), the route returns `{ warming_up: true, campaign_id, cycle_id, phase_hint: "origin" }` with HTTP 200 and `Last-Modified` from the session dir mtime. Client recognises `warming_up === true` and renders a friendly placeholder ("Origin running") rather than treating the cycle as offline.
- `unitKey` change (any hop of the path) resets `lastModifiedRef` in the render-phase guard. Required — without it, switching campaigns (or drilling into/out of an inner loop) leaves stale `If-Modified-Since` on the wire.
- **A course's subtree is `/tree`, fetched at the viewed path** (`lib/hooks/useCampaignTree.ts`), `If-Modified-Since` per query key. There is no second genealogy read: the sandbox's `/campaigns`+`/cycles` pair used to answer "which inner run measured this cell" beside the tree, and the two disagreed — a fork's runs are stamped with the fork's private counter (`C1.1`) while the timeline renumbers them (`C1.4`), which only the tree can do. Ask the tree.
- **Root-scoped vs leaf-scoped is load-bearing.** `useLineageOverlay` fetches at `rootCycleId` (the whole campaign, carrying the masks); `useCampaignTree` fetches at the path it is given. A surface whose rows come from the LEAF's `dashboard.json` (the samples panel) must read the leaf's tree — the root's tree renumbers a fork's candidates, so the labels would not join.

## State reset on prop change

When a component or context must drop derived state because an identity prop changed (the viewed `(campaignId, cycleId)` switched, etc.), use the **render-phase guarded reset** — React's sanctioned "adjusting state when a prop changes" recipe:

```tsx
const [prevKey, setPrevKey] = useState(key);
if (key !== prevKey) {
  setPrevKey(key);
  setDerived(EMPTY); // ...clear every key-scoped field
}
```

It runs **during render**, so the reset and the re-render commit together — no stale frame. A `useEffect` reset runs after paint and flashes one frame of the prior unit's data; do not use it for this.

Canonical sites: `lib/poll.tsx` (`unitKeyRef`, the `useRef` variant), `lib/SelectionContext.tsx`.

A hook that owns a single state object may instead derive freshness purely — stamp the loaded data with the key it was fetched for and return `EMPTY` until the key matches (`lib/hooks/useDatasetPreview.ts`, `lib/hooks/useRoundFile.ts`). Also stale-frame-free.

## Render-cost guards (do not regress)

Per-poll re-renders cascade through the chart tree by default. The following guards exist to stop that and must stay:

- `React.memo` on `FitnessChart` + `DendrogramStrip` (`components/candidates/`), `TrendChart` (`components/eval/TrendChart.tsx`), `TopStrip` (`components/dashboard/layout/TopStrip.tsx`).
- The forest geometry (`components/candidates/forest-layout.ts`) runs inside `Forest`'s `useMemo`, keyed on the served `tree` + the `expanded` set, **not** on `dash` identity. The tree changes identity only when a refetch lands (the overlay provider fetches on the dashboard change-signal, and a 304 keeps the prior object), so re-renders from unrelated `dash` mutations don't recompute it. **The per-candidate value overlays (`valueByKey` / `thetaByKey`) ride OUTSIDE that memo on purpose** — a per-sample value tick repaints node text without re-flowing the geometry. Don't fold a live value into the structure. The dendrogram geometry (`components/candidates/dendrogram.ts`) follows the same rule, keyed on the stabilized structural rows + the published `centers`.
- Chart `useMemo`s key on narrow stable derivations (e.g. `dash?.rounds`), not on `dash`.
- **Anything riding the chart's `options` memo must be a stable identity.** `onSelect` / `onGeometry` are `useCallback`s: an inline arrow there defeats `FitnessChart`'s memo *and* forces a `chart.update()` on every 2 s poll tick — which the `xBridge` geometry publisher now rides.

Any new chart that consumes `dash` follows the same pattern: `React.memo` wrap, `useMemo` keyed on the narrowest stable input.

## Testing posture

The webapp gate is **compile-time + smoke + a small Vitest scope**, enforced by CI (`.github/workflows/ci.yml`, `webapp` job):

- `npm run lint` — ESLint.
- `npx tsc --noEmit` — full strict typecheck (`next build` alone does not hard-fail on every type error, so this line is what makes `strict` real). **`noUncheckedIndexedAccess` is ON**: an index access (`arr[i]`, `rec[k]`, `match[n]`) can miss, so it types as possibly-`undefined`. Without it a *correct* guard reads as dead code (`sample-line.ts` guards regex group 3, which the pattern makes optional) while a *missing* one reads as fine — the type system lied in both directions at once, and "is this `??` redundant?" had no answer. Handle the miss (skip / early-return); `!` only where the line above proves presence; **never `?? <default>` to silence it** — a fabricated number rendered as a measurement is the one thing this app must never do, and `edits[key] ?? r.value` is not `edits[key] !== undefined ? … : …` when the operator clears a field to `""`.
- `npm run test` — Vitest, scoped to `lib/**/__tests__/` + `components/**/__tests__/` per `webapp/vitest.config.ts`. Reader-side derivations only (pure data → data helpers); display components stay covered by smoke. Cycle fixtures live at `tests/fixtures/cycles/` — recipe at [`docs/developer/cycle-fixtures.md`](../docs/developer/cycle-fixtures.md).
- `npm run build` — the static export must succeed.
- Manual smoke at `http://localhost:8001/` after a behavioural change. Two states, two harnesses:
  - **anon** — open `:8001` as-is (no flag); drives the public-preview surface.
  - **authed + live** — relaunch the server with `PROMPTPOTTER_AUTH=off`: `deps.py::resolve_identity` short-circuits to `registered_or_default_identity` (the CLI's resolver), so `/auth/me` returns 200 and every auth-gated read resolves to your **real on-disk campaigns** (zero spend, pure reads). This is the cheap way to exercise the contract's `live`/`warming` clauses — no Docker, no fixtures, and no second flag: repo `datasets/{name}/` is **install content** (tracked in git, so it ships with every clone) and needs no capability to read. Reserve the Dex harness ([`dev/oidc-local/`](../dev/oidc-local/), [`docs/developer/local-oidc.md`](../docs/developer/local-oidc.md)) for the one thing this can't reach: the real Google OIDC login round-trip.

When to reach for a component-render test (`@testing-library/react`): pick a regression class that compile + smoke + the derivation tests can't catch — today's bug classes are reader-side and ride the existing Vitest scope.

## Build + run

```bash
cd webapp
npm install                          # one-time
npm run build                        # FAST local-preview export → webapp/out/, served at the root by FastAPI
npm run lint
npx tsc --noEmit
```

**Two build modes — `next build` has two audiences (`next.config.ts`).** `npm run build` is the operator's fast rebuild→reload preview loop: it compiles only. The React Compiler pass and full-bundle source maps are deploy-artifact concerns gated behind `DEPLOY_BUILD=1`, exposed as **`npm run build:deploy`** — the artifact CI validates and the deploy box (`deploy-linux/{bootstrap,update}.sh`) ships. Type-check and lint never run inside *either* build: they're owned by the dedicated CI gates above (`npx tsc --noEmit`, `npm run lint`), so re-running them in `next build` is duplication. The `build:deploy` script uses bash inline-env, so it runs in CI and on the deploy box; to preview compiler-on behaviour from Windows, set the var in-shell instead: `$env:DEPLOY_BUILD=1; npm run build`.

`out/` is the route mounted by FastAPI (`StaticFiles(html=True)` at `/`). After any source change, rebuild and hard-reload the browser. Dev mode (`npm run dev`) proxies `/api/*` to `http://127.0.0.1:8001` via `next.config.ts::rewrites` — production has no proxy (same FastAPI origin).
