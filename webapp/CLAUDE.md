# webapp — CLAUDE.md

Next.js static export at `out/`, mounted at `/` by FastAPI; **the API is the carved-out `/api/v1`.** It polls, renders and issues control verbs through `POST /commands/{kind}`; **it never recomputes a score.**

## Owned elsewhere

- **Surface behavior** — [`../docs/specs/frontend-surface-contract.md`](../docs/specs/frontend-surface-contract.md) `I*` invariants; this file owns only the implementation side.
- **Visual identity and copy** — [`BRAND.md`](../../promptpotter-web/BRAND.md) in the site repo; no parallel design spec, every value read as `var(--…)`.
- **`dashboard.json` guarantees** — [`../promptpotter/infrastructure/CLAUDE.md`](../promptpotter/infrastructure/CLAUDE.md) § Persistence; never reconstruct a served field from a second source.
- **Which model a new field lands on** — [`../docs/developer/adding-a-surface.md`](../docs/developer/adding-a-surface.md) § 3; the lineage tree is [`../promptpotter/infrastructure/store/CLAUDE.md`](../promptpotter/infrastructure/store/CLAUDE.md) § The lineage tree, rendered, never derived here.

## Scoring authority

- **No local re-sort.** An ordering is a score; `/datasets/{name}/cells` is a served ranking.
- **No recomputed mask.** Masked values, the criterion's dials and anchors (`composite_fitness_weights`, `composite_fitness_anchors`) and the lens vocabulary (`CELL_TERM_META`) are served.
- **A criterion is ONE rendering** — `components/shell/scoring/Criterion`, at a rung (line, dials, formula); a new scoring surface hosts it. `lensOf` alone mints `?lens=` / `;lens=`.
- **A dial is a weight only**: the browser sends `dials:<term=weight,…>`; a fork carries the served `lens_criterion`. A served lens re-opens in Expression mode, never decomposed into weights. Say **mask** and **lens**, never "what-if".
- **No number kept alive under a changed setup: render `?`** — an edited node parameter, model or prompt field invalidates every measurement at that searchpoint and its `descendantsOf` closure (crosses forks). **invalidated** ≠ **divergent** (server counterfactual) ≠ **retired** (what ran). `NodeSurface` emits the WHOLE running config: diff against the seed first.
- **Never `?? <default>` on an index miss** (`noUncheckedIndexedAccess`): handle the miss; `!` only where the line above proves presence.
- `lib/derivations/*` group, lay out and format served data, and introduce no number.

## The chat thread — rendered, never authored

Positioning is [`../docs/specs/chat-foundation.md`](../docs/specs/chat-foundation.md)'s.

- **Everything the Potter does is one generic `step`** (icon, label, status, optional duration/cost); a new `ProjectionKind` reads into it, never a new item kind. `candidate` and `round` are the deletable optimizer layer.
- **Only message items are client-authored.** One crossing: an ENDED run freezes served values into a `run` item, since `resume` rewrites what a pointer would name.
- **`sample_order` yields no item** — it lands as `declared_sample_order` and drives the run card's "next in line" (`dash.walk`). Declared, never "will".

## State reset on prop change

When an identity prop changes, use the **render-phase guarded reset**:

```tsx
const [prevKey, setPrevKey] = useState(key);
if (key !== prevKey) {
  setPrevKey(key);
  setDerived(EMPTY); // ...clear every key-scoped field
}
```

**Never a `useEffect` reset** — it paints one stale frame. A single-state hook may instead stamp data with its fetch key and return `EMPTY` until it matches.

**A reset may seed from `lib/state/view-memory.tsx`** (per-campaign, TTL + LRU in the codec, `useSyncExternalStore`). **A display preference is not view memory** — per device, permanent, `useLocalStorage` under one `STORAGE_KEYS` entry (`lib/hooks/useLocalStorage.ts`; `lib/prefs/`), never a `UserSettings` field. **View memory holds ids, flags and UI keys, never a measurement or a mask.**

## Testing posture

The gate is `python scripts/gate.py --web`. **The standalone typecheck is what makes `strict` real** (`next build` does not hard-fail on every type error). **Vitest covers reader-side derivations and `lib/read-cache.ts` / `useRead`** only; a fixture is the SERVED shape, built by `lib/test-fixtures.ts`, never a cast `dashboard.json`.

**The browser walk (`npm run e2e`) owns "does it render against a served cycle"** — a component-render test earns its place only for a class no other tier catches. The gate runs the `cold` tier; runbook: [`e2e/README.md`](e2e/README.md).

Smoke by hand at `http://localhost:8001/`: as-is for **anon**, `PROMPTPOTTER_AUTH=off` for **authed + live** at zero spend; [`../dev/oidc-local/`](../dev/oidc-local/) only for the real OIDC round-trip.

## Build + run

**One build mode (`next.config.ts`); no env switch adds a second** — a second would evict Turbopack's one-configuration cache. Source maps ship to the box only (`scripts/build_release.py::stage_webapp` strips them).

**Visual work: `npm run dev`** on :3000, proxying `/api/*` to :8001 (`nextConfig.rewrites`). A real OIDC login needs the built app on :8001.

**A rebuild swaps chunk hashes under open tabs**; `ui/ErrorBoundary` reloads once (a `sessionStorage` stamp) and, with storage blocked, asks instead of looping.

<!-- BEGIN:nextjs-agent-rules -->

# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` (resolved from this file's directory; in monorepos the `next` package may not be visible from the repo root) before writing any code. Heed deprecation notices.

This block is written and re-added by `next dev` — verify at `node_modules/next/dist/server/lib/generate-agent-files.js`. Removing it from a diff only re-creates the uncommitted change; committing it with your work keeps the tree clean.

<!-- END:nextjs-agent-rules -->
