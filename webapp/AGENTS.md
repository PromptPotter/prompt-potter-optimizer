<!-- BEGIN:nextjs-agent-rules -->
# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` before writing any code. Heed deprecation notices.
<!-- END:nextjs-agent-rules -->

## State reset on prop change

When a component or context must drop derived state because an identity prop
changed (the viewed `(campaignId, cycleId)` switched, etc.), use the
**render-phase guarded reset** — React's sanctioned "adjusting state when a
prop changes" recipe:

```tsx
const [prevKey, setPrevKey] = useState(key);
if (key !== prevKey) {
  setPrevKey(key);
  setDerived(EMPTY); // ...clear every key-scoped field
}
```

It runs **during render**, so the reset and the re-render commit together —
no stale frame. A `useEffect` reset runs after paint and flashes one frame of
the prior unit's data; do not use it for this.

Canonical sites: `lib/poll.tsx` (`unitKeyRef`, the `useRef` variant),
`components/dashboard/SelectionContext.tsx`, `components/console/ConsolePane.tsx`.

A hook that owns a single state object may instead derive freshness purely —
stamp the loaded data with the key it was fetched for and return `EMPTY` until
the key matches (`lib/useDatasetPreview.ts`, `lib/useRoundFile.ts`). This is
also stale-frame-free.

## Display-data sources

Two on-disk surfaces back the dashboard. Read from the right one:

- **`dashboard.json`** (polled every 2 s by `lib/poll.tsx` → `useCycleStream()`)
  — origin, in-flight `current_round`, and the `rounds[]` array of completed-
  round summaries. **Sole source** for the FitnessChart, TrendChart, TopStrip
  sparkline, LineageTree. Don't stitch in `round_NNNN.json` for chart data.
- **`round_NNNN.json`** (lazy, fetched via `lib/useRoundFile.ts`) — deep
  audit per round: full LLM I/O, per-sample results, scoreboard with
  `per_sample`. Reach for it only when the operator drills into a specific
  round (FreqChart distribution, ScoringInspector composite/hits,
  OptimizerNodeDetail node-by-node inspection).

If you find yourself adding a "merge in-flight with historical" or "fall
back to round-file when dashboard hasn't written X yet" branch, you're
re-introducing the stitch pattern the unification spec
(`docs/specs/webapp-display-source-unification.md`) collapsed. Pick one
source per data class.

## Testing posture

The webapp has no unit-test harness, by deliberate choice. It is a read-only
dashboard that polls `dashboard.json` — display code, which the project's test
charter (`tests/CLAUDE.md`) says earns no test. The gate is **compile-time +
smoke**, enforced by CI (`.github/workflows/ci.yml`, `webapp` job):

- `npm run lint` — ESLint.
- `npx tsc --noEmit` — full strict typecheck (`next build` alone does not
  hard-fail on every type error, so this line is what makes `strict` real).
- `npm run build` — the static export must succeed.
- Manual smoke at `http://localhost:8001/ui/` after a behavioural change.

A `vitest` harness scoped to `lib/` (the polling + render-phase state-reset
logic — genuine non-display code) is the right move once the M12 control
plane adds webapp write paths (launch / stop / resume / fork). Until then,
a test harness for a read-only view is not worth its upkeep.
