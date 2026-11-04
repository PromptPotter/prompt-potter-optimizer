# webapp — CLAUDE.md

Next.js static export at `out/`, mounted at `/` by FastAPI; the API is the carved-out `/api/v1`. It polls, renders and issues control verbs through `POST /commands/{kind}`; **it never recomputes a score.**

## Owned elsewhere

- **Surface behavior** — [`../docs/specs/frontend-surface-contract.md`](../docs/specs/frontend-surface-contract.md) `I*` invariants; this file owns the implementation side.
- **Visual identity and copy** — `BRAND.md` in the site repo; every value read as `var(--…)`.
- **`dashboard.json` guarantees** — [`../promptpotter/infrastructure/CLAUDE.md`](../promptpotter/infrastructure/CLAUDE.md) § Persistence — one ingress, two projections.
- **Which model a new field lands on** — [`../docs/developer/adding-a-surface.md`](../docs/developer/adding-a-surface.md) § 3. A dashboard / view field; the lineage tree is rendered, never derived here.

## Scoring authority

- **No local re-sort, no recomputed mask.** An ordering is a score (`/datasets/{name}/cells` is a served ranking); masked values, the criterion's dials and anchors (`composite_fitness_weights`, `composite_fitness_anchors`) and the lens vocabulary (`CELL_TERM_META`) are served. `lib/derivations/*` group, lay out and format, and introduce no number.
- **A criterion is ONE rendering** — `components/shell/scoring/Criterion`; `lensOf` alone mints `?lens=` / `;lens=`. A dial is a weight only (`dials:<term=weight,…>`); a fork carries the served `lens_criterion`; a served lens re-opens in Expression mode, never decomposed into weights. Say **mask** and **lens**.
- **No number kept alive under a changed setup: render `?`** — an edited node parameter, model or prompt field invalidates every measurement at that searchpoint and its `descendantsOf` closure. **invalidated** ≠ **divergent** (server counterfactual) ≠ **retired** (what ran). `NodeSurface` emits the WHOLE running config: diff against the seed first.

- **Never `?? <default>` on an index miss** (`noUncheckedIndexedAccess` does not enforce it): handle the miss; `!` only where the line above proves presence.

## The chat thread — rendered, never authored

Positioning is [`../docs/specs/host-assistant.md`](../docs/specs/host-assistant.md)'s.

- **Everything the Potter does is one generic `step`**; a new `ProjectionKind` reads into it, never a new item kind. `candidate` and `round` are the deletable optimizer layer.
- **Only message items are client-authored.** One crossing: an ENDED run freezes served values into a `run` item.
- **`sample_order` yields no item** — it lands as `declared_sample_order` and drives the run card's "next in line" (`dash.walk`).

## State reset on prop change

When an identity prop changes, reset in the render phase (`if (key !== prevKey) { setPrevKey(key); setDerived(EMPTY); }`), **never in a `useEffect`** — that paints one stale frame. A single-state hook may instead stamp data with its fetch key and return `EMPTY` until it matches.

A reset may seed from `lib/state/view-memory.tsx` (per-campaign ids, flags and UI keys, never a measurement or a mask). **A display preference is not view memory**: `useLocalStorage` under one `STORAGE_KEYS` entry (`lib/prefs/`), never a `UserSettings` field.

## Testing posture

The gate is `python scripts/gate.py --web`. **Vitest covers reader-side derivations and `lib/read-cache.ts` / `useRead`** only, on fixtures of the SERVED shape (`lib/test-fixtures.ts`), never a cast `dashboard.json`. **The browser walk (`npm run e2e`) owns "does it render against a served cycle"**; the gate runs its `cold` tier ([`e2e/README.md`](e2e/README.md)). A component-render test earns its place only for a class no other tier catches. Smoke by hand at `http://localhost:8001/`: as-is for **anon**, `PROMPTPOTTER_AUTH=off` for **authed + live** at zero spend; [`../dev/oidc-local/`](../dev/oidc-local/) for the real OIDC round-trip.

## Build + run

**One build mode (`next.config.ts`); no env switch adds a second.** Source maps ship to the box only (`scripts/build_release.py::stage_webapp`). **Visual work: `npm run dev`** on :3000, proxying `/api/*` to :8001; a real OIDC login needs the built app on :8001.

<!-- BEGIN:nextjs-agent-rules -->

# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` (resolved from this file's directory; in monorepos the `next` package may not be visible from the repo root) before writing any code. Heed deprecation notices.

This block is written and re-added by `next dev` — verify at `node_modules/next/dist/server/lib/generate-agent-files.js`. Removing it from a diff only re-creates the uncommitted change; committing it with your work keeps the tree clean.

<!-- END:nextjs-agent-rules -->
