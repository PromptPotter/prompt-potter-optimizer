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
