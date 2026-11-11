# webapp — CLAUDE.md

A React app built by Vite into `out/`, mounted at `/` by FastAPI beside `/api/v1`. It polls, renders and issues control verbs through `POST /commands/{kind}`; **it never recomputes a score.** Visual identity and copy: `BRAND.md` in the site repo, every value read as `var(--…)`. `dashboard.json`'s guarantees: [`../promptpotter/infrastructure/CLAUDE.md`](../promptpotter/infrastructure/CLAUDE.md) § Persistence — one ingress, two projections.

## Scoring authority

- **No local re-sort, no recomputed mask.** An ordering is a score; masked values, the criterion's dials and anchors and the lens vocabulary are served. `lib/derivations/*` group, lay out and format, and introduce no number.
- **A criterion is ONE rendering** (`components/shell/scoring/Criterion`); `lensOf` alone mints `?lens=` / `;lens=`. A dial is a weight only; a served lens re-opens in Expression mode, never decomposed into weights. Say **mask** and **lens**.
- **No number kept alive under a changed setup: render `?`**: an edited node parameter, model or prompt field invalidates every measurement at that searchpoint and its `descendantsOf` closure. **invalidated** ≠ **divergent** (server counterfactual) ≠ **retired** (what ran).
- **A new served field lands on the record that owns it**: per arm `domain/arm_reading.py::ArmReading`, per round `domain/dashboard_rows.py::RoundSummary`. **A display field is not done until you have SEEN it render**: a field nothing writes passes every check.
- **Never `?? <default>` on an index miss** (`noUncheckedIndexedAccess` does not enforce it): handle the miss.

## Surface states and invariants

A data-backed surface is `anon` (public preview, demo content), `auth_empty` (nothing selected: no error styling), `warming`, `live`, `loading`, `error`, `offline` (poll stale) or `gone` (the server ANSWERED 404: terminal). Code comments cite the `I*` labels.

- **I1** `loading` resolves to `live`, empty or `error`; a 401 in `anon` is the anon empty state. **I2** Every failure maps to a typed state message, never `<status> <path>`. **I3** A control that looks operable IS operable. **I4** `anon` never shows authed chrome, authed never anon CTAs. **I5** `anon` fires no auth-gated request beyond the `/me` probe.
- **I6** "Is anything running?" has ONE served answer, `domain/phases.py::ProducerReading`; detached never renders as running. The browser holds no set of live phases, subtracts no clock, and never unmounts run controls on connection loss.
- **I7** Every error envelope carries an `error_id`; callers branch on `failureKind`. A GONE address stops polling and recovers to a live one; existence is the address's own read, never list membership. **I8** A rendered Δ names its floor: ORIGIN is C0 (`vs_origin`), PARENT the round's own (`vs_reference`).
- **I9** What config a node RUNS has ONE answer, `GET /campaigns/{id}/pipeline?at=`; every param carries its served `source`, `never_axis`, `editable` and `role`, and a badge, fold or lock from a client-side diff is the violation.
- **I10** Every data panel rides `dashboard.json` + the SSE tail, except a one-shot topology read (`/pipeline`) and `fork-preview`. **I11** Every optimizer gets the same surfaces where the method permits, and says so where not; features come from served facts.
- **I12** A view reads at a GLANCE (drawn once by the masthead), its PANE and its DETAIL; open views are columns, newest first.

`AccessGate` (dispatcher refusal) exits only by sign-out; `ConsentGate` (a `me.terms_version` mismatch) only by accept; `anon` sees neither.

## The served hierarchy

**Every surface that lists, switches, docks or heads a run renders ONE entry shape**, `domain/hierarchy.py::HierarchyEntry`, minted by `application/hierarchy.py` alone; its `figures`, `lines` and `climb` render as served.

- **One tag exception, `mint`**, served typed as `facts.mint`. <!-- probe: git grep -cE "tagValue\(.*mint" -- webapp == 0 -->
- **One run-state derivation**: a campaign's `state` IS its line course's. <!-- probe: git grep -cE "HierarchyState\.of\(" -- promptpotter == 2 --> <!-- probe: git grep -cE "RunStatus\.of\(" -- promptpotter == 2 --> The listing serves `attached` and no clock, so it answers 304 between writes.
- **One poll** over every lifecycle; the sidebar filter is layout over `facts.lifecycle`. <!-- probe: git grep -cE "hierarchyRead\(" -- webapp/lib/hooks/useRegistry.ts == 1 -->
- **The genealogy is the same entry, read deep** (`GET /hierarchy/{address}`), no second route, no client fold. <!-- probe: git grep -cE "\}/tree" -- promptpotter/presentation == 0 --> A course's chronology is its STEPS (`GET /hierarchy/{course}/@`), never `children`.

## The chat thread — rendered, never authored

**Everything the Potter does is one generic `step`**; a new `ProjectionKind` reads into it, never a new item kind. Only message items are client-authored; an ENDED run freezes served values into a `run` item.

## State reset on prop change

When an identity prop changes, reset in the render phase (`if (key !== prevKey) { … }`), **never in a `useEffect`**, which paints one stale frame. View memory (`lib/state/view-memory.tsx`) holds ids, flags and UI keys, never a measurement or a mask; a display preference is one `STORAGE_KEYS` entry (`lib/prefs/`).

## Testing posture

The gate is `python scripts/gate.py --web`; its typecheck makes `strict` real (`vite build` checks no types). **Vitest covers reader-side derivations and `lib/read-cache.ts` / `useRead` only**, on fixtures of the SERVED shape (`lib/test-fixtures.ts`). **The browser walk (`npm run e2e`) owns "does it render against a served cycle"** ([`e2e/README.md`](e2e/README.md)). Smoke at `http://localhost:8001/`: as-is for **anon**, `PROMPTPOTTER_AUTH=off` for **authed + live** at zero spend, [`../dev/oidc-local/`](../dev/oidc-local/) for real OIDC.

## Build + run

**One build mode (`vite.config.ts`); no env switch adds a second.** A page's `index.html` holds only `#root` and its entry. **Visual work: `npm run dev`** on :3000, proxying `/api/*` to :8001.
