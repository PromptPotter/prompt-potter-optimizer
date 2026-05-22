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
the key matches (`lib/useDatasetPreview.ts`). This is also stale-frame-free.

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
