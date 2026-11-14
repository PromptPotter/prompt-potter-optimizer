# webapp — CLAUDE.md

A React app built by Vite into `out/`, mounted at `/` by FastAPI beside `/api/v1`. It re-reads what the server's feed says moved, renders and issues control verbs through `POST /commands/{kind}`; **it never recomputes a score.** Visual identity and copy: `BRAND.md` in the site repo, every value read as `var(--…)`. `dashboard.json`'s guarantees: [`../promptpotter/infrastructure/CLAUDE.md`](../promptpotter/infrastructure/CLAUDE.md) § Persistence — one ingress, two projections.

## Scoring authority

- **No local re-sort, no recomputed mask.** An ordering is a score; masked values, the criterion's dials and anchors and the lens vocabulary are served. `lib/derivations/*` group, lay out and format, and introduce no number.
- **A criterion is ONE rendering** (`components/criterion/Criterion`); `lensOf` alone mints `?lens=` / `;lens=`. A dial is a weight only; a served lens re-opens in Expression mode, never decomposed into weights. Say **mask** and **lens**.
- **No number kept alive under a changed setup: render `?`**: an edited node parameter, model or prompt field invalidates every measurement at that searchpoint and its `lib/derivations/lineage-descendants.ts::descendantsOf` closure, the one client fold until the address serves it. **invalidated** ≠ **divergent** (server counterfactual) ≠ **retired** (what ran). `NodeSurface` emits the WHOLE running config: diff against the seed first.
- **A new served field lands on the record that owns it**: per arm `domain/arm_reading.py::ArmReading`, per round `domain/dashboard_rows.py::RoundSummary`. **A display field is not done until you have SEEN it render**: a field nothing writes passes every check.
- **Never `?? <default>` on an index miss** (`noUncheckedIndexedAccess` does not enforce it): handle the miss.

## Surface states and invariants

A data-backed surface is `anon` (public preview, demo content), `auth_empty` (nothing selected: no error styling), `warming`, `live`, `loading`, `error`, `offline` (reads stale) or `gone` (the server ANSWERED 404: terminal). Code comments cite the `I*` labels.

- **I1** `loading` resolves to `live`, empty or `error`; a 401 in `anon` is the anon empty state. **I2** Every failure maps to a typed state message, never `<status> <path>`. **I3** A control that looks operable IS operable. **I4** `anon` never shows authed chrome, authed never anon CTAs. **I5** `anon` fires no auth-gated request beyond the `/me` probe.
- **I6** "Is anything running?" has ONE served answer, `domain/phases.py::ProducerReading`; detached never renders as running. The browser holds no set of live phases, subtracts no clock, and never unmounts run controls on connection loss. Testing `=== "running"` drops a gate-held run to idle: read `RUN_PHASE_INFO` and the served `admission`; `isLive` is `producer.appending`, never attachment.
- **I7** Every error envelope carries an `error_id`. A GONE address stops re-asking and recovers to a live one; existence is the address's own read, never list membership. **I8** A rendered Δ names its floor: ORIGIN is C0 (`vs_origin`), PARENT the round's own (`vs_reference`).
- **I9** What config a node RUNS has ONE answer, `GET /campaigns/{id}/pipeline?at=`; every param carries its served `source`, `never_axis`, `pickable` and `role`, and a badge, fold or lock from a client-side diff is the violation.
- **I10** Every data panel rides the one source of its data class ([`lib/CLAUDE.md`](lib/CLAUDE.md) § Display-data sources; `dashboard.json` for in-flight `current_round` and `rounds[]`), re-read when the account's feed (`GET /feed`) says its store moved, + the SSE tail, except a topology read moved only by what the account filed (`/pipeline`) and `fork-preview`. **I11** Every optimizer gets the same surfaces where the method permits, and says so where not; features come from served facts.
- **I12** A view reads at a GLANCE (drawn once by the masthead), its PANE and its DETAIL; open views are columns, newest first.

`AccessGate` (dispatcher refusal) exits only by sign-out; `ConsentGate` (a `me.terms_version` mismatch) opens unticked (GDPR/FADP affirmative consent) and exits only by accept; `anon` sees neither.

## The served hierarchy

**Every surface that lists, switches, docks or heads a run renders ONE entry family**, the `domain/hierarchy.py::_HierarchyEntry` subclasses (`HierarchyCampaign`, `HierarchyCourse`; the deep read's `HierarchyEntry` union), minted by `application/hierarchy.py` and `application/hierarchy_subtree.py` alone; their `figures`, `lines` and a course's `climb` (the lead line: the selection's lift vs origin and vs parent, then its rates) render as served.

- **One tag exception, `mint`**, served typed as `facts.mint`. <!-- probe: git grep -cE "tagValue\(.*mint" -- webapp == 0 -->
- **One run-state derivation**: a campaign's `state` IS its line course's. <!-- probe: git grep -cE "HierarchyState\.of\(" -- promptpotter == 2 --> <!-- probe: git grep -cE "RunStatus\.of\(" -- promptpotter == 2 --> The listing serves `attached` and no clock, so it answers 304 between writes.
- **One listing read** over every lifecycle; the sidebar filter is layout over `facts.lifecycle`. <!-- probe: git grep -cE "hierarchyRead\(" -- webapp/lib/hooks/useRegistry.ts == 1 -->
- **The genealogy is the same entry, read deep** (`GET /hierarchy/{address}`), no second route and no client fold but `descendantsOf`. <!-- probe: git grep -cE "\}/tree" -- promptpotter/presentation == 0 --> A course's chronology is its STEPS (`GET /hierarchy/{course}/@`), never `children`.

## The chat thread — rendered, never authored

**Everything the Potter does is one generic `step`**; a new `ProjectionKind` reads into it, never a new item kind. Only message items are client-authored; an ENDED run freezes served values into a `run` item.

## State reset on prop change

When an identity prop changes, reset in the render phase (`if (key !== prevKey) { … }`), **never in a `useEffect`**, which paints one stale frame. View memory (`lib/state/view-memory.tsx`) holds ids, flags and UI keys, never a measurement or a mask; a display preference is one `storage-keys.ts::STORAGE_KEYS` row and one `lib/prefs/` hook.

## Testing posture

The gate is `python scripts/gate.py --web`; its typecheck makes `strict` real (`vite build` checks no types). **Vitest runs two projects** (`vitest.config.ts`): `logic` (node) over `{lib,components}/**/__tests__/*.test.ts`, reader-side derivations and `lib/read-cache.ts` / `useRead`, and `dom` (jsdom, RTL) over `components/**/__tests__/*.test.tsx`, a component's own render; both on fixtures of the SERVED shape (`lib/test-fixtures.ts`). **The browser walk (`npm run e2e`) owns "does it render against a served cycle"** ([`e2e/README.md`](e2e/README.md)). Smoke at `http://localhost:8001/`: as-is for **anon**, `PROMPTPOTTER_AUTH=off` for **authed + live** at zero spend, [`../dev/oidc-local/`](../dev/oidc-local/) for real OIDC.

## Build + run

**One build mode (`vite.config.ts`); no env switch adds a second.** A page's `index.html` holds only `#root` and its entry. **Visual work: `npm run dev`** on :3000, proxying `/api/*` to :8001.
