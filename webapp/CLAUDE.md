# webapp — CLAUDE.md

Next.js 16.2.7 + React 19.2.4 + TypeScript, static export at `out/` mounted at the domain root by FastAPI (the app owns `/`; the API is the carved-out `/api/v1` namespace). Read-only dashboard: polls `dashboard.json` every 2 s, lazy-fetches `round_NNNN.json` on drill-in.

## Surface behavior contract

What each user-facing control **must do**, per auth/data state, lives in
[`../docs/specs/frontend-surface-contract.md`](../docs/specs/frontend-surface-contract.md).
This file owns *implementation* invariants; that one owns *behavior* — read it
before changing any control's states. Its five invariants (state-completeness,
no-raw-transport-errors, affordance-honesty, auth-coherence, console-hygiene)
are the bar for user-facing PRs. Drive the surface against it with the
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
- **Inside a surface, organize by domain region — one axis, never by widget kind.** `dashboard/` is `lineage/` · `samples/` · `scoring/` · `pipeline/` · `control/` · `layout/` — the §0 primitives the operator observes, so the names stay stable as milestones add views (M11 viz → `scoring/`, M12 multi-connector → `pipeline/`). Do **not** add kind-buckets (`charts/`, `detail/`); they collide with the region axis and rot. Cross-surface domain widgets that aren't owned by one pane stay as their own feature folders (`eval/`, `whatif/`, `workflow/`).
- **Anatomy: fetch lives in a hook, not a component.** A component that fetches *and* renders *and* owns selection/scroll state is the prototype smell this layer is moving off of. Put data access in a `lib/hooks/use*.ts` hook (peers: `useRoundFile`, `useDatasetPreview`, `useDashboard`); keep components presentational where possible. Reuse the pure `lib/derivations/*` for data→data shaping rather than re-deriving inline.
- **Primitive-first.** Do not hand-roll another modal / popover / dropdown / button. Reach for `components/ui/*`; if the primitive doesn't exist yet, add it there (with a `*.module.css` and an RTL test) so the next caller inherits it.
- **Accessibility is positioning, not compliance** (`BRAND.md`). Every interactive element — including SVG `<g>`/nodes — needs keyboard operability + `role`; dialogs trap focus + restore on close + close on ESC; state pairs color with a label or icon (HIT/MISS, pass/fail, live/stale), never color alone; focus rings stay visible (the `:focus-visible` accent outline); honor `prefers-reduced-motion` on the 2 s poll. Operator data (IDs, hashes, payloads) stays selectable — never inject hidden characters.

## Brand identity / "About this unit"

`lib/brand.ts` is the single source of brand identity (name, publisher vs. provider, URLs), each `NEXT_PUBLIC_*`-overridable for whitelabel. It feeds three real surfaces — the Web App Manifest (`app/manifest.ts`), the schema.org `SoftwareApplication` JSON-LD in `<head>` (`app/layout.tsx`), and the Account → "About this unit" pane (`components/account/AboutUnit.tsx`). **`publisher`** = the distributing host (overridable); **`provider`** = PromptPotter (fixed — it's the provenance fact). The app **version** is not duplicated here: it's server-owned (`APP_VERSION`) and read live from `/api/v1/health`. Provenance is `self-declared` until a signed credential lands — never render a "verified" state while `BRAND.verification` says otherwise.

## Stack-drift warning

Next.js 16 has breaking changes from prior versions — APIs, file conventions, and config differ from earlier training data. Before touching framework-level code (config, routing, build, async-component shapes), read the relevant section of `node_modules/next/dist/docs/` and heed deprecation notices in the CLI output. React 19 likewise: `use()`, `Actions`, ref-as-prop, removed `forwardRef` boilerplate.

## Folder-UI file contract (load-bearing — do not regress)

`dashboard.json` and `round_NNNN.json` are the canonical UI surface of PromptPotter. They are not a cache, not a perf optimization, not "also written for debugging" — **they ARE the dashboard.** An operator can open `.promptpotter/campaigns/*/cycles/*/dashboard.json` and `rounds/round_NNNN.json` in any text editor at any moment and see the current state of the run. The browser UI is one consumer; the file tree is another, equal consumer.

Three guarantees the writer side MUST hold:

1. **Always on disk.** `dashboard.json` exists after any ledger event in a cycle. Sole writer: `LiveDashboardView` (`promptpotter/infrastructure/projections/live_dashboard/view.py`). Sole writer of `round_NNNN.json`: `AuditTrailView` (`promptpotter/infrastructure/projections/audit_trail.py`). Both use atomic-swap (tmp + rename) — never partial-write, never torn read.
2. **Settles within `_DASHBOARD_DEBOUNCE_S` (0.25 s) of the last event.** The writer debounces high-frequency events (sample-scored, token-usage, LLM-call progress) to coalesce bursts, but converges to current state at most 250 ms behind real-time. Constant at `view.py:88`; flush plumbing at `_schedule_persist()` (`view.py:448`).
3. **Immediate (no debounce) at round boundaries.** `PhaseRecord("round"|"origin", "complete"|"exit")` and `mark_stopped` flush synchronously via `_flush_pending_persist()` (`view.py:472`). When a round ends, its file is current before the next round begins. **Do not remove these flushes.** Do not relax atomic-swap. Do not introduce a path that lets `dashboard.json` lag past a completed round.

If a future change wants to defer or skip a write, the question to answer first is: *can an operator who alt-tabs to the file tree right now still see the truth?* If no, the change is wrong.

## Display-data sources

Two on-disk surfaces back the dashboard. Read from the right one:

- **`dashboard.json`** (polled every 2 s by `lib/poll.tsx` → `useCycleStream()`) — in-flight `current_round` and the `rounds[]` array of completed-round summaries (**round 0 = origin**, a one-candidate round labelled "C0"; there is no separate origin block). **Sole source** for the FitnessChart, TrendChart, TopStrip sparkline, LineageTree. Don't stitch in `round_NNNN.json` for chart data.
- **`round_NNNN.json`** (lazy, fetched via `lib/hooks/useRoundFile.ts`) — deep audit per round: full LLM I/O, per-sample results, scoreboard with `per_sample`. Reach for it only when the operator drills into a specific round (FreqChart distribution, ScoringInspector composite/hits, OptimizerNodeDetail node-by-node inspection).

If you find yourself adding a "merge in-flight with historical" or "fall back to round-file when dashboard hasn't written X yet" branch, you're re-introducing the stitch pattern the unification spec (`docs/specs/webapp-display-source-unification.md`) collapsed. Pick one source per data class.

## Viewed identity — one address (CyclePath)

"What am I looking at?" has ONE answer: `viewedPath`, a **`CyclePath`** (`lib/ids.ts`) — the chain of `(campaign, cycle)` hops from the top-level root to the leaf. A top-level cycle is a 1-hop path; an L4 inner loop is a 2-hop path `[outer, inner]`; L5+ nests deeper (this mirrors the engine's re-entrant `.inner/<cycle_id>` sandbox). `lib/workspace.tsx` owns it: state is `pinnedPath` + `following`; `viewedPath` is derived (`following ? [active 1-hop] : pinnedPath`). Everything that DISPLAYS what you're looking at re-roots to the **LEAF hop** — the dashboard stream, the connector/pipeline hero, the hard-samples panes, AND the chat live activity feed (`useCycleEvents` + its gate-decision control, which is derived from the leaf `dash`). Drilling into an inner loop shows THAT inner cycle's telemetry everywhere, coherently. Only the operator's **conversation identity** stays on the **ROOT hop** — the `sessionId`, the ingest/compose-new-campaign flow, and Files (the `campaignId`/`cycleId` exports remain the root) — so drilling in never mints a new thread or moves ingest off the outer conversation. The leaf ids are derived from `viewedPath` at the consumer (`pathLeaf`), never a second identity variable. The connector/samples read the leaf's dataset (and the ETA chip the leaf's start time) via `useLeafCycleIndex`; the hard-samples slice + the feed both ride `?descend=` into the inner sandbox. There is no separate inner-focus axis; drilling in = `selectCyclePath([outer, inner])`, backing out = `backToOuter()`. Deep-link is one `?path=` param (encoded CyclePath; malformed → falls back to following). Don't reintroduce a second identity variable or a per-surface "unit" — every surface derives from `viewedPath`.

## Polling shape

- `GET /api/campaigns/{id}/cycles/{cid}/dashboard` supports `If-Modified-Since` → `304 Not Modified`. Client (`lib/poll.tsx`, `lastModifiedRef`) tracks the latest `Last-Modified` per `unitKey` (the encoded `CyclePath`) and skips `setState` on 304. One fetch (`fetchDashboardByPath`) serves any depth: an inner descendant rides a `?descend=<hops>` query the server walks into each hop's `.inner/` sandbox; at depth 1 the URL is byte-identical to a plain per-cycle read, so 304 semantics are unchanged.
- When `dashboard.json` does not yet exist (fresh campaign before origin completes), the route returns `{ warming_up: true, campaign_id, cycle_id, phase_hint: "origin" }` with HTTP 200 and `Last-Modified` from the session dir mtime. Client recognises `warming_up === true` and renders a friendly placeholder ("Origin running") rather than treating the cycle as offline.
- `unitKey` change (any hop of the path) resets `lastModifiedRef` in the render-phase guard. Required — without it, switching campaigns (or drilling into/out of an inner loop) leaves stale `If-Modified-Since` on the wire.

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

- `React.memo` on `FitnessChart` (`components/whatif/FitnessChart.tsx`), `TrendChart` (`components/eval/TrendChart.tsx`), `TopStrip` (`components/dashboard/layout/TopStrip.tsx`), `FamilyTree` (`components/dashboard/lineage/FamilyTree.tsx` — the unified lineage card).
- The lineage geometry (`components/dashboard/lineage/layout.ts`) runs inside `Forest`'s `useMemo`, keyed on the content-stabilized `detailByCycle` (via `useStableContent` in `useLineage` — the co-located hook that owns all lineage fetch + state; `FamilyTree` is presentational) + the `expanded` set, **not** on `dash` identity. Re-renders triggered by unrelated `dash` mutations don't recompute the tree.
- Chart `useMemo`s key on narrow stable derivations (e.g. `dash?.rounds`), not on `dash`.

Any new chart that consumes `dash` follows the same pattern: `React.memo` wrap, `useMemo` keyed on the narrowest stable input.

## Testing posture

The webapp gate is **compile-time + smoke + a small Vitest scope**, enforced by CI (`.github/workflows/ci.yml`, `webapp` job):

- `npm run lint` — ESLint.
- `npx tsc --noEmit` — full strict typecheck (`next build` alone does not hard-fail on every type error, so this line is what makes `strict` real).
- `npm run test` — Vitest, scoped to `lib/**/__tests__/` + `components/**/__tests__/` per `webapp/vitest.config.ts`. Reader-side derivations only (pure data → data helpers); display components stay covered by smoke. Cycle fixtures live at `tests/fixtures/cycles/` — recipe at [`docs/developer/cycle-fixtures.md`](../docs/developer/cycle-fixtures.md).
- `npm run build` — the static export must succeed.
- Manual smoke at `http://localhost:8001/` after a behavioural change. Two states, two harnesses:
  - **anon** — open `:8001` as-is (no flag); drives the public-preview surface.
  - **authed + live** — relaunch the server with `PROMPTPOTTER_AUTH=off`: `deps.py::resolve_identity` short-circuits to `registered_or_default_identity` (the CLI's resolver), so `/auth/me` returns 200 and every auth-gated read resolves to your **real on-disk campaigns** (zero spend, pure reads). This is the cheap way to exercise the contract's `live`/`warming` clauses — no Docker, no fixtures. **Add `PROMPTPOTTER_ADMIN=1` to faithfully reproduce the operator's production view**: install benchmarks (repo `datasets/{name}/` — every CLI `new <benchmark>` campaign: justlogic, aime_2025, musr, …) resolve only for an identity holding `BENCHMARKS_READ_CAP`, which the registered operator carries in production (pinned at the OIDC seam, `oidc.py`) but the bare auth-off identity does **not**. Without the flag, those campaigns' `/datasets/{name}/preview` + `/measurement-series` 404 in the console — a harness gap, not a product bug (a real operator has the cap; regular users never own benchmark campaigns). Reserve the Dex harness ([`dev/oidc-local/`](../dev/oidc-local/), [`docs/developer/local-oidc.md`](../docs/developer/local-oidc.md)) for the one thing the flag can't reach: the real Google OIDC login round-trip.

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
