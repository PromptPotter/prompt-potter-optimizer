# M11 Webapp — Next.js + Plain-CSS Port

**Status:** Shipped (Wave 1 + Wave 2/3 collapsed) 2026-05-07. Scaffold lives at `webapp-react/`; static export is deployed into `webapp/` (the vanilla `webapp/index.html` is gone — its preservation-list role is now historical). Run `npm run deploy` from `webapp-react/` to rebuild + redeploy.

**Implementation:**
- Stack: **Next.js 16 (App Router) + TypeScript + plain CSS + CSS Modules**. **No Tailwind** (per `m11-publication-benchmarks.md` line 84). React 19.
- Location: `webapp-react/` (the deploy target `webapp/` is the static export).
- Static deploy: Next.js `output: "export"` + `basePath: "/ui"` + `trailingSlash: true`, served by FastAPI's existing `/ui` mount.
- Build pipeline: `npm run deploy` (= `next build && node scripts/deploy-to-webapp.mjs`) writes the export over `webapp/`. During development: `next dev` on :3000 (visit `/ui`), FastAPI on :8001, Next config rewrites `/api/*` → `http://127.0.0.1:8001/api/*` (dev only — rewrites are stripped from the export).

**Depends on:** Vanilla a11y / tooltip / resilience pass (DONE — commit `f1dab82`). FastAPI read endpoints (DONE — `m11-webapp-minimal-preview.md`). Active session pointer (DONE).
**Blocks:** M11 monitoring slices (hard-sample dashboard, per-searchpoint score histogram, family-tree speciation, dataset preview on drop). M12 webapp Phase 2 (control plane, chat panel, wand toggle, multi-cycle).

---

## Goal

Port `webapp/index.html` to Next.js + React + plain CSS without losing the **9 patterns hardened in vanilla** (see § *Lift verbatim*). Land a clean foundation for M11 monitoring slices and M12's control plane. Visual rework that vanilla deferred (hero stat trio, h1 weight, sidebar slide-out, monospace stack) happens **in the port baseline**, not as follow-up.

Glassmorphism (5 sites) is **operator-vetoed off-limits** — preserved verbatim.

## Non-goals (this slice)

- No control plane, no SSE/WebSocket — those land with M12 Track 3.
- No multi-cycle picker, no campaign list — single-cycle preview from `active_session.json`.
- No write endpoints, no editing, no auth.
- No Tailwind, no styled-components, no CSS-in-JS runtime — plain CSS Modules over the existing design tokens.
- No SSR — `output: "export"` for static deploy under `/ui`.

---

## Stack decisions

| Decision | Choice | Reason |
|---|---|---|
| Framework | Next.js 15 (App Router) | Already named in `m11-publication-benchmarks.md`. App Router gives clean route-as-folder for Dashboard / Files / future panes. |
| Language | TypeScript | Pattern-rich UI (TERMS dict, dispatch on `dash.state`, role/aria states) is much easier to maintain typed. |
| Styling | Plain CSS + CSS Modules | Matches `m11-publication-benchmarks.md` line 84. Vanilla file's `:root` design tokens lift directly into a `globals.css`. |
| State | `useState` + `useContext` for cycle/active session | One-page-app scope. Adding Zustand / Redux is over-engineering for this slice. |
| Charts | Chart.js 4 + `react-chartjs-2` wrapper | Drop-in match for vanilla. Theme-aware `applyChartDefaults` pattern lifts. |
| Markdown | `marked` (existing CDN dep in vanilla) | Same library; one less migration vector. |
| Polling | Custom `useDashboardPoll(cycleId)` hook | `useEffect` + `AbortController` + `visibilitychange` — direct port of vanilla `startPolling/stopPolling`. |
| Routing | App Router: `/`, `/files`, future `/audit`, `/prompts`, `/datasets` | Matches sidebar nav. Stub routes 404 / show "Coming soon" until wired. |

---

## Directory layout

```
webapp-react/
├── app/
│   ├── layout.tsx                 # <html lang="en"> + <head> + <Providers>
│   ├── page.tsx                   # Dashboard pane (default)
│   ├── files/page.tsx             # File-tree pane
│   ├── (chat)/                    # Future: chat-mode shell
│   └── api/                       # empty — FastAPI is the API
├── components/
│   ├── shell/
│   │   ├── Sidebar.tsx            # <nav aria-label="Primary">
│   │   ├── Topbar.tsx             # <header>, ThemeToggle, Tabs (role=tablist)
│   │   └── ThemeToggle.tsx        # no-reload swap (destroy + re-render charts)
│   ├── status/
│   │   ├── StatusBar.tsx          # role=status aria-live=polite, dot shape variation
│   │   └── statusBuckets.ts       # ageS → 'live'|'idle'|'snapshot'|'offline'
│   ├── workflow/
│   │   ├── WorkflowCanvas.tsx     # SVG edges + HTML node boxes
│   │   ├── WorkflowNode.tsx       # role=button + aria-pressed
│   │   ├── layout.ts              # LAYOUT + EDGES constants (lift verbatim)
│   │   └── WizardMascot.tsx       # CSS-only ::after, prefers-reduced-motion-aware
│   ├── whatif/
│   │   ├── WhatIfPanel.tsx
│   │   ├── EvaluatorTile.tsx      # role=checkbox aria-checked
│   │   └── whatif.ts              # whatifIdentifiersInFormula, etc.
│   ├── tree/
│   │   └── FileTree.tsx           # role=button + aria-expanded on dir headers
│   ├── chat/                      # M12 prep — keep the airy shape
│   └── ui/
│       ├── Tooltip.tsx            # Wraps native title for now; popover later
│       └── KeyboardActivate.tsx   # Shared role=button div + Enter/Space handler
├── lib/
│   ├── terms.ts                   # TERMS dict — LIFTED VERBATIM from vanilla
│   ├── api.ts                     # fetchActive, fetchCycleFile, fetchFiles
│   ├── poll.ts                    # useDashboardPoll hook
│   ├── theme.ts                   # applyChartDefaults, getCss helpers
│   └── pipeline-state.ts          # phaseToNodeId, etc.
├── styles/
│   ├── globals.css                # :root + [data-theme="light"] tokens (lifted)
│   ├── reset.css                  # reduced-motion media query, focus-visible
│   └── *.module.css               # per-component
├── public/
│   ├── wizard-64x64.png           # lifted
│   └── promptpotter-wordmark.svg
├── next.config.ts                 # output: "export", rewrites for /api proxy
├── package.json
└── tsconfig.json
```

---

## Lift verbatim from vanilla `webapp/index.html`

These 9 patterns are *production-tested* in vanilla as of `f1dab82`. The port copies the shape, not the syntax. Naming cosmetics aside, the contracts must match — that's how `useDashboardPoll` and `<StatusBar>` consume the same API responses without a parallel data layer.

| # | Pattern | Vanilla location | React target | Lift mode |
|---|---|---|---|---|
| 1 | `TERMS` dict (~30 entries, namespaced `status_*` / `phase_*` / `node_*` / `col_*` / `whatif_*`) | top of `<script>` block | `lib/terms.ts` | **Verbatim**. Same keys. |
| 2 | Status banner age buckets (live <30s / idle 30s–5m / snapshot >5m / offline) | `setStatus` call sites in `refreshDashboard` | `lib/poll.ts::ageBucket(ageS)` | Same thresholds, same kind strings. |
| 3 | Status dot shape variation (filled / hollow ring / ×) via `::before` `::after` | `.status-bar.{live,stale,offline} .status-dot` CSS | `StatusBar.module.css` | CSS lifts directly. |
| 4 | Delegated keydown on `[role="button|tab|checkbox"]` activates Enter/Space with preventDefault | `document.addEventListener('keydown', …)` | `<KeyboardActivate>` HOC + `useKeyboardActivate` hook | Pattern, not code — React idiom is per-element handler or a context provider. |
| 5 | `applyChartDefaults()` re-callable on theme switch | inline JS | `lib/theme.ts::applyChartDefaults(getCssVar)` | Same `Chart.defaults` shape; the destroy + re-render dance lifts into `<ThemeToggle>` callback. |
| 6 | Polling lifecycle: `startPolling` / `stopPolling` + `visibilitychange` + `AbortController` | inline JS | `lib/poll.ts::useDashboardPoll(cycleId)` | Idiom flips to `useEffect` cleanup. Threshold and abort semantics identical. |
| 7 | Semantic landmarks: `<nav aria-label="Primary">`, `<main>`, `<header>` (topbar), `<h2 class="card-title">` | hardened in vanilla | `Sidebar`, `Topbar`, `Card.tsx` | Same elements + same ARIA attrs. |
| 8 | `hideDecorativeSvgs()` boot pass — every `<svg>` without aria-label/labelledby/role=img gets `aria-hidden="true"` | inline JS | shared `<Icon>` component default | React idiom: every Icon component sets `aria-hidden="true"` unless `label` prop is given. |
| 9 | `prefers-reduced-motion` media query stops animations + transitions | bottom of `<style>` block | `styles/reset.css` | CSS lifts directly. |

**Token theme** — also lift verbatim:
- `:root` block with `--color-*`, `--font-*`, `--border-radius-*`. Both dark + `[data-theme="light"]` palettes are user-rationalized; preserve the comments explaining mid-tone surface choices.
- The `--*-rgb` triplet trick that lets `rgba()` overlays follow the theme.
- Theme persistence: `localStorage.getItem('promptpotter.theme')` key stays the same so a vanilla session and a React session share the same preference.

**Workflow topology** — `LAYOUT` and `EDGES` constants in vanilla (`webapp/index.html`) hardcode positions. Pipeline shape "never changes" per `m11-webapp-minimal-preview.md`. Lift these constants verbatim into `components/workflow/layout.ts`. The SVG marker definitions also lift unchanged.

---

## Redesign in the port (visual deferred from vanilla)

Per the operator's 2026-05-07 directive: vanilla's hero stat trio, h1 weight, sidebar slide-out, and monospace stack are deferred to the React port baseline so vanilla stays a stable preservation list. The port should ship with these *already redone*, not as follow-up.

| Item | Vanilla state | Port baseline |
|---|---|---|
| Hero stat trio (BEST / QUERIES / LAST QUERY) | 3 centered cards under the page title — generic AI-dashboard opener | One large primary stat (likely BEST + sparkline) + 2 inline secondary stats. Or: kill the trio, let workflow canvas anchor the page. Decide in implementation; do not replicate the centered-3-card pattern. |
| h1 weight | 500 (reads as subhead) | 600 + larger leading. The page title should clearly anchor. |
| Sidebar transition | `display: none` cut on chat-mode entry | Slide-out transform (respects `prefers-reduced-motion`). |
| Monospace stack | `"SF Mono", Menlo, Consolas, monospace` (thin on Windows ClearType) | Lead with `Cascadia Mono` for Windows, then `SF Mono`. Bump body 12 → 13 px. |
| Status dot animation | None | Slow pulse on `live` (1.6s breathe). Off under `prefers-reduced-motion`. |

Glassmorphism (chat user message, LLM hero node, what-if active tile, wand row, hero KB pill): **5 sites, all preserved**. Operator veto.

---

## API integration

No new endpoints. The port consumes the existing FastAPI surface from `m11-webapp-minimal-preview.md`:
- `GET /api/v1/active`
- `GET /api/v1/optimizer/pipeline`
- `GET /api/v1/campaigns/{cycle_id}/files`
- `GET /api/v1/campaigns/{cycle_id}/file?scope=…&path=…`

Dev-mode rewrite in `next.config.ts`:
```ts
async rewrites() {
  return [
    { source: '/api/:path*', destination: 'http://127.0.0.1:8001/api/:path*' },
  ];
}
```

Static-export deploy: `output: "export"` produces `out/`. A small Python step (in CI or a make target) copies `out/*` over `webapp/`. FastAPI's existing `app.mount("/ui", StaticFiles(...))` serves it unchanged. The vanilla `webapp/index.html` is replaced; `webapp/assets/wizard-64x64.png` is preserved.

CORS: same-origin under `/ui` in production. Dev-mode the rewrite avoids preflight too.

---

## Implementation waves

**Wave 1 — Scaffold + Dashboard parity (one PR)**
1. `npx create-next-app@latest webapp-react --typescript --no-tailwind --no-src-dir --app`. Add `react-chartjs-2`, `chart.js`, `marked`. Configure `output: "export"` and the `/api` rewrite.
2. Lift `:root` design tokens + reduced-motion media query into `app/globals.css`. Lift `TERMS` dict verbatim into `lib/terms.ts`.
3. Build `<Shell>` (sidebar + topbar + content slot). Sidebar nav items use the placeholder TERMS for stubs. Topbar tabs as `role="tablist"`.
4. Implement `useDashboardPoll(cycleId)` hook with AbortController + visibility pause. Implement `useActiveSession` for `/api/v1/active`.
5. Build `<StatusBar>`, `<WorkflowCanvas>`, `<WhatIfPanel>`, `<PassRateCard>`, `<FreqChart>`, `<TrendChart>`, `<EvalTable>` — direct ports of vanilla render functions. Use `react-chartjs-2`.
6. Theme toggle: no-reload swap, destroys + re-creates Chart.js refs.
7. **Visual baseline**: redo hero stat trio, h1 weight, monospace stack, sidebar slide-out per § *Redesign*.
8. Tests: one Playwright spec per critical path (load Dashboard, click File pane, click workflow node, toggle what-if tile, switch theme without reload). No DOM-rendering unit tests for chart internals — Chart.js is third-party.

**Wave 2 — Files pane + chat scaffold (separate PR)**
- Port file-tree component with collapsible `role="button" aria-expanded` headers.
- Port file viewer (JSON pretty / `marked` for `.md` / `<pre>` for `.log`).
- Bring over the chat pane shell as inert UI (no API yet) — M12 will wire it.

**Wave 3 — Static export + cutover (separate PR)**
- Add `npm run build && cp -r out/* ../webapp/` make target.
- Update `promptpotter/main.py` static mount comment to point at the export source.
- Delete `webapp/index.html` (the vanilla file). Keep `webapp/assets/`.
- Update `m11-webapp-minimal-preview.md` status: "Slice 1 archived; React port active in webapp-react/."
- Update CLAUDE.md: drop "ugly read-only webapp preview" wording; replace with "Next.js read-only operator dashboard at /ui."

---

## What stays in vanilla (do not touch until cutover)

The vanilla file is the reference implementation until Wave 3 lands. **Do not iterate on it** — bug fixes only, no feature work, no a11y rework beyond what already shipped in `f1dab82`. Operator may run vanilla and React side-by-side during Wave 1 → Wave 2.

If a bug is found in vanilla that *also* matters for the port, fix it in the port branch first, then back-port to vanilla minimally.

---

## Verification

- **Functional parity**: same dashboard data renders; same theme toggle behavior; same polling cadence; same files-pane navigation; same workflow-node selection. Every panel that worked in vanilla works in port.
- **A11y parity (must improve)**: vanilla scored ~16-17/20 post-`/harden`. Port should hit 19-20/20 — touch targets ≥ 44px (charter previously deferred mobile; the port baseline includes it), keyboard navigation already-equivalent via the lifted patterns, focus-visible already styled.
- **Bundle size**: `out/` total < 500 KB gzipped excluding wizard PNG. Chart.js is ~70 KB gzipped; marked is ~15 KB. Plain CSS keeps the rest small.
- **No regressions**: `pytest tests/test_api.py` still green (FastAPI surface unchanged). `tests/test_invariants.py` still green (no new campaign artifacts written).
- **Operator smoke**: `npm run dev` on :3000, `uvicorn promptpotter.main:app --port 8001`, run `python -m promptpotter optimize` in a third terminal — dashboard live-updates, file tree works, theme toggle does not reload, keyboard tab order reaches every interactive element.

---

## Pre-reading for the implementer

- `webapp/index.html` (vanilla — the source of truth for behavior post-`f1dab82`).
- `docs/specs/m11-webapp-minimal-preview.md` (charter for the read-only surface; API contracts).
- `docs/specs/m11-publication-benchmarks.md` line 84 (stack constraint: plain CSS + CSS Modules, no Tailwind).
- `docs/specs/m12-multi-connector.md` § Track 3 (what M12 layers on top of this baseline).
- `promptpotter/presentation/api.py` (the API surface — read end-to-end; the port is a pure consumer).
- `promptpotter/main.py` static mount block.
- Active pointer + path helpers — read-only consumers, no changes.

---

## Open questions (decide during Wave 1)

1. **Hero replacement shape** — kill the trio entirely, or one-big-stat + sparkline? Decide based on workflow canvas's standalone strength as a page anchor.
2. **Chat pane in baseline or Wave 2** — currently Wave 2. Could pull into Wave 1 as inert UI to reduce churn before M12 wires it.
3. **Wand toggle on /ui in Wave 1** — purely visual in vanilla; could ship in Wave 1 baseline or stay deferred to M12 alongside the chat pane wiring. Default: defer (don't ship the wand outside the chat surface).
4. **Mobile breakpoint commitment** — vanilla deferred mobile entirely. Port baseline should commit to ≥ 768 px tablet at minimum; full mobile (< 600 px) is M12 alongside whitelabel work. Confirm with operator before Wave 1 lands.
