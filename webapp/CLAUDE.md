# webapp — CLAUDE.md

Next.js 16.2.5 + React 19.2.4 + TypeScript, static export at `out/` mounted at `/ui` by FastAPI. Read-only dashboard: polls `dashboard.json` every 2 s, lazy-fetches `round_NNNN.json` on drill-in.

## Stack-drift warning

Next.js 16 has breaking changes from prior versions — APIs, file conventions, and config differ from earlier training data. Before touching framework-level code (config, routing, build, async-component shapes), read the relevant section of `node_modules/next/dist/docs/` and heed deprecation notices in the CLI output. React 19 likewise: `use()`, `Actions`, ref-as-prop, removed `forwardRef` boilerplate.

## Folder-UI file contract (load-bearing — do not regress)

`dashboard.json` and `round_NNNN.json` are the canonical UI surface of PromptPotter. They are not a cache, not a perf optimization, not "also written for debugging" — **they ARE the dashboard.** An operator can open `.promptpotter/campaigns/*/cycles/*/dashboard.json` and `rounds/round_NNNN.json` in any text editor at any moment and see the current state of the run. The browser UI is one consumer; the file tree is another, equal consumer.

Three guarantees the writer side MUST hold:

1. **Always on disk.** `dashboard.json` exists after any ledger event in a cycle. Sole writer: `LiveDashboardView` (`promptpotter/infrastructure/projections/live_dashboard/view.py`). Sole writer of `round_NNNN.json`: `AuditTrailView` (`promptpotter/infrastructure/projections/audit_trail.py`). Both use atomic-swap (tmp + rename) — never partial-write, never torn read.
2. **Settles within `_DASHBOARD_DEBOUNCE_S` (0.25 s) of the last event.** The writer debounces high-frequency events (sample-scored, token-usage, LLM-call progress) to coalesce bursts, but converges to current state at most 250 ms behind real-time. Constant at `view.py:78`; flush plumbing at `_schedule_persist()` (`view.py:429`).
3. **Immediate (no debounce) at round boundaries.** `PhaseRecord("round"|"origin", "complete"|"exit")` and `mark_stopped` flush synchronously via `_flush_pending_persist()` (`view.py:453`). When a round ends, its file is current before the next round begins. **Do not remove these flushes.** Do not relax atomic-swap. Do not introduce a path that lets `dashboard.json` lag past a completed round.

If a future change wants to defer or skip a write, the question to answer first is: *can an operator who alt-tabs to the file tree right now still see the truth?* If no, the change is wrong.

## Display-data sources

Two on-disk surfaces back the dashboard. Read from the right one:

- **`dashboard.json`** (polled every 2 s by `lib/poll.tsx` → `useCycleStream()`) — origin, in-flight `current_round`, and the `rounds[]` array of completed-round summaries. **Sole source** for the FitnessChart, TrendChart, TopStrip sparkline, LineageTree. Don't stitch in `round_NNNN.json` for chart data.
- **`round_NNNN.json`** (lazy, fetched via `lib/useRoundFile.ts`) — deep audit per round: full LLM I/O, per-sample results, scoreboard with `per_sample`. Reach for it only when the operator drills into a specific round (FreqChart distribution, ScoringInspector composite/hits, OptimizerNodeDetail node-by-node inspection).

If you find yourself adding a "merge in-flight with historical" or "fall back to round-file when dashboard hasn't written X yet" branch, you're re-introducing the stitch pattern the unification spec (`docs/specs/webapp-display-source-unification.md`) collapsed. Pick one source per data class.

## Polling shape

- `GET /api/campaigns/{id}/cycles/{cid}/dashboard` supports `If-Modified-Since` → `304 Not Modified`. Client (`lib/poll.tsx`, `lastModifiedRef` at line 259) tracks the latest `Last-Modified` per `unitKey` and skips `setState` on 304.
- When `dashboard.json` does not yet exist (fresh campaign before origin completes), the route returns `{ warming_up: true, campaign_id, cycle_id, phase_hint: "origin" }` with HTTP 200 and `Last-Modified` from the session dir mtime. Client recognises `warming_up === true` and renders a friendly placeholder ("Origin running") rather than treating the cycle as offline.
- `unitKey` change resets `lastModifiedRef` in the render-phase guard at `poll.tsx:273`. Required — without it, switching campaigns leaves stale `If-Modified-Since` on the wire.

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

Canonical sites: `lib/poll.tsx` (`unitKeyRef`, the `useRef` variant), `components/dashboard/SelectionContext.tsx`, `components/console/ConsolePane.tsx`.

A hook that owns a single state object may instead derive freshness purely — stamp the loaded data with the key it was fetched for and return `EMPTY` until the key matches (`lib/useDatasetPreview.ts`, `lib/useRoundFile.ts`). Also stale-frame-free.

## Render-cost guards (do not regress)

Per-poll re-renders cascade through the chart tree by default. The following guards exist to stop that and must stay:

- `React.memo` on `FitnessChart` (`components/whatif/FitnessChart.tsx`), `TrendChart` (`components/eval/TrendChart.tsx`), `TopStrip` (`components/dashboard/TopStrip.tsx`), `LineageTree` (`components/dashboard/LineageTree.tsx`).
- `LineageTree` keys its layout `useMemo` on a structural fingerprint (`l1RoundsKey` at line 73, dep at line 143), **not** on `dash` identity. Re-renders triggered by unrelated `dash` mutations don't recompute the tree.
- Chart `useMemo`s key on narrow stable derivations (e.g. `dash?.rounds`), not on `dash`.

Any new chart that consumes `dash` follows the same pattern: `React.memo` wrap, `useMemo` keyed on the narrowest stable input.

## Testing posture

The webapp gate is **compile-time + smoke + a small Vitest scope**, enforced by CI (`.github/workflows/ci.yml`, `webapp` job):

- `npm run lint` — ESLint.
- `npx tsc --noEmit` — full strict typecheck (`next build` alone does not hard-fail on every type error, so this line is what makes `strict` real).
- `npm run test` — Vitest, scoped to `lib/**/__tests__/` + `components/**/__tests__/` per `webapp/vitest.config.ts`. Reader-side derivations only (pure data → data helpers); display components stay covered by smoke. Cycle fixtures live at `tests/fixtures/cycles/` — recipe at [`docs/developer/cycle-fixtures.md`](../docs/developer/cycle-fixtures.md).
- `npm run build` — the static export must succeed.
- Manual smoke at `http://localhost:8001/ui/` after a behavioural change. Auth-on smoke uses the local Dex harness at [`dev/oidc-local/`](../dev/oidc-local/) — see [`docs/developer/local-oidc.md`](../docs/developer/local-oidc.md).

When to reach for a component-render test (`@testing-library/react`): pick a regression class that compile + smoke + the derivation tests can't catch — today's bug classes are reader-side and ride the existing Vitest scope.

## Build + run

```bash
cd webapp
npm install                          # one-time
npm run build                        # static export → webapp/out/, served at /ui by FastAPI
npm run lint
npx tsc --noEmit
```

`out/` is the route mounted by FastAPI (`StaticFiles(html=True)` at `/ui`). After any source change, rebuild and hard-reload the browser. Dev mode (`npm run dev`) proxies `/api/*` to `http://127.0.0.1:8001` via `next.config.ts::rewrites` — production has no proxy (same FastAPI origin).
